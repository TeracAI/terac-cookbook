"""Owner verification: synthetic replay of a reviewer's verdict on a redacted owner call."""

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from runtime.runner import Runner, RunState

RECIPE_ID = "owner_verification"
RECIPE_CONTRACT_VERSION = 1
FIXTURE_VERSION = 1
PACKAGED_CASE_IDS = {"verified", "concerns", "fail"}
MAX_SUMMARY_CHARS = 8_000
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{8,}\d")


class OwnerVerificationError(ValueError):
    code = "invalid_request_or_checkpoint"


class InvalidReplayCaseError(OwnerVerificationError):
    code = "invalid_replay_case"

    def __init__(self):
        super().__init__("Replay case is not in the packaged catalog")


class ConsentRequiredError(OwnerVerificationError):
    code = "consent_required"

    def __init__(self):
        super().__init__("The owner's recorded consent is required")


class UnredactedInputError(OwnerVerificationError):
    code = "unredacted_input"

    def __init__(self):
        super().__init__("Summary appears to contain an email address or phone number")


class LiveUnavailableError(OwnerVerificationError):
    code = "live_unavailable"

    def __init__(self):
        super().__init__("Live owner verification is unavailable")


class NoClarificationError(OwnerVerificationError):
    code = "no_clarification"

    def __init__(self):
        super().__init__("Owner verification has no clarification step")


class Answers(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    credible_owner: str = Field(min_length=1)
    business_operating: str = Field(min_length=1)
    genuine_intent: str = Field(min_length=1)


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    verdict: Literal["verified", "concerns", "fail"]
    reasons: tuple[str, ...] = Field(min_length=1)
    red_flags: tuple[str, ...]
    answers: Answers
    limitations: tuple[str, ...]
    provenance: Literal["synthetic"]

    @model_validator(mode="after")
    def validate_verdict(self):
        if any(not reason.strip() for reason in (*self.reasons, *self.red_flags)):
            raise ValueError("Reasons and red flags must be nonempty")
        if self.verdict == "verified" and self.red_flags:
            raise ValueError("A red flag cannot accompany verified")
        if self.verdict == "fail" and not self.red_flags:
            raise ValueError("Fail requires at least one red flag")
        return self


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    mode: Literal["replay"]
    replay_case: str
    artifact_id: str
    summary: str
    status: Literal["working", "succeeded", "failed"]
    phase: str
    result: Verdict | None
    error: str | None


@dataclass(frozen=True)
class _Case:
    case_id: str
    label: str
    summary: str
    digest: str
    events: tuple[dict, ...]


def _load_cases() -> tuple[str, dict[str, _Case]]:
    manifest = json.loads(files(__package__).joinpath("replay.json").read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("recipe_contract_version") != RECIPE_CONTRACT_VERSION
        or manifest.get("fixture_version") != FIXTURE_VERSION
        or not isinstance(manifest.get("cases"), dict)
        or set(manifest["cases"]) != PACKAGED_CASE_IDS
        or manifest.get("default_case") not in PACKAGED_CASE_IDS
    ):
        raise ValueError("Replay manifest has an unsupported contract")
    cases = {}
    for case_id, definition in manifest["cases"].items():
        events = definition.get("events")
        if not isinstance(events, list) or len(events) != 1 or events[0].get("kind") != "verdict":
            raise ValueError("Replay case must contain exactly one verdict event")
        canonical = json.dumps(
            {"case_id": case_id, "fixture_version": FIXTURE_VERSION, "events": events},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        cases[case_id] = _Case(
            case_id=case_id,
            label=definition["label"],
            summary=definition["summary"],
            digest=hashlib.sha256(canonical).hexdigest(),
            events=tuple(events),
        )
    return manifest["default_case"], cases


def artifact_id_for(summary: str) -> str:
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()


class _ReplayProgress:
    """Releases one synthetic verdict; inspection never advances it."""

    def __init__(self, cases: dict[str, _Case]):
        self._cases = cases

    def _case(self, state: RunState) -> _Case:
        case = self._cases.get(state.recipe_payload.get("replay_case"))
        if case is None or state.adapter_payload.get("fixture_digest") != case.digest:
            raise ValueError("Replay fixture is incompatible with this checkpoint")
        return case

    def observe(self, state: RunState) -> dict | None:
        if state.status != "working":
            return None
        event = deepcopy(self._case(state).events[0])
        if event["result"].get("artifact_id") == "$artifact_id":
            event["result"]["artifact_id"] = state.recipe_payload["artifact_id"]
        return event

    def validate_progress(self, state: RunState) -> None:
        self._case(state)
        expected = 0 if state.status == "working" else 1
        if state.status != "failed" and state.adapter_payload.get("cursor") != expected:
            raise ValueError("Replay cursor is incompatible")


class OwnerVerification:
    def __init__(self, state_dir: Path):
        self.default_case, self._cases = _load_cases()
        self._progress = _ReplayProgress(self._cases)
        self._runner = Runner(
            state_dir,
            recipe_id=RECIPE_ID,
            adapter_for_state=lambda state: self._progress,
            transition_for_event=self._transition,
            validate_run=self._validate,
            observation_error_for_state=lambda exc, state: {
                "status": "failed",
                "phase": "invalid_event",
                "error": "Replay verdict failed validation",
            },
        )

    @property
    def replay_metadata(self) -> dict[str, Any]:
        return {
            "default_replay_case": self.default_case,
            "replay_cases": [
                {"id": c.case_id, "label": c.label, "summary": c.summary}
                for c in self._cases.values()
            ],
        }

    def start(
        self,
        summary: str,
        *,
        consent_recorded: bool,
        mode: str = "replay",
        replay_case: str | None = None,
    ) -> Snapshot:
        if mode != "replay":
            raise LiveUnavailableError()
        if consent_recorded is not True:
            raise ConsentRequiredError()
        if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY_CHARS:
            raise OwnerVerificationError("Summary must be nonempty and at most 8,000 characters")
        if _EMAIL.search(summary) or _PHONE.search(summary):
            raise UnredactedInputError()
        case_id = self.default_case if replay_case is None else replay_case
        if case_id not in self._cases:
            raise InvalidReplayCaseError()
        state = self._runner.start(
            mode="replay",
            intent="verify",
            recipe_contract_version=RECIPE_CONTRACT_VERSION,
            recipe_payload={
                "summary": summary,
                "artifact_id": artifact_id_for(summary),
                "replay_case": case_id,
                "result": None,
            },
            adapter_payload={"cursor": 0, "fixture_digest": self._cases[case_id].digest},
            phase="waiting_for_reviewer",
        )
        return self._snapshot(state)

    def inspect(self, run_id: str) -> Snapshot:
        return self._snapshot(self._runner.inspect(run_id))

    def resume(self, run_id: str) -> Snapshot:
        return self._snapshot(self._runner.resume(run_id))

    def respond(self, run_id: str, response: object) -> Snapshot:
        self._runner.inspect(run_id)
        raise NoClarificationError()

    @staticmethod
    def _transition(event: object, state: RunState) -> dict:
        if not isinstance(event, dict) or event.get("kind") != "verdict":
            raise ValueError("Unsupported replay event")
        result = Verdict.model_validate(event.get("result"))
        if result.artifact_id != state.recipe_payload["artifact_id"]:
            raise ValueError("Verdict is bound to a different summary")
        return {
            "status": "succeeded",
            "phase": "verdict_available",
            "recipe_payload": {**state.recipe_payload, "result": result.model_dump(mode="json")},
            "adapter_payload": {**state.adapter_payload, "cursor": 1},
        }

    @staticmethod
    def _validate(state: RunState) -> None:
        payload = state.recipe_payload
        if set(payload) != {"summary", "artifact_id", "replay_case", "result"}:
            raise ValueError("Unsupported owner verification checkpoint")
        if state.mode != "replay" or state.recipe_contract_version != RECIPE_CONTRACT_VERSION:
            raise ValueError("Unsupported owner verification checkpoint")
        if payload["artifact_id"] != artifact_id_for(payload["summary"]):
            raise ValueError("Summary binding is incompatible")
        expected_phase = {
            "working": "waiting_for_reviewer",
            "succeeded": "verdict_available",
            "failed": "invalid_event",
        }.get(state.status)
        if state.phase != expected_phase:
            raise ValueError("Phase does not match status")
        if state.status == "succeeded":
            result = Verdict.model_validate(payload["result"])
            if result.artifact_id != payload["artifact_id"]:
                raise ValueError("Verdict is bound to a different summary")
        elif payload["result"] is not None:
            raise ValueError("Undelivered run cannot hold a verdict")

    @staticmethod
    def _snapshot(state: RunState) -> Snapshot:
        payload = state.recipe_payload
        return Snapshot(
            run_id=state.run_id,
            mode="replay",
            replay_case=payload["replay_case"],
            artifact_id=payload["artifact_id"],
            summary=payload["summary"],
            status=state.status,
            phase=state.phase,
            result=payload["result"],
            error=state.error,
        )
