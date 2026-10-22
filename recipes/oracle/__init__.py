"""Oracle's public caller interface for synthetic GTM expert review."""

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from adapters.replay import ReplayAdapter
from runtime.runner import (
    RECIPE_CHECKPOINT_VERSION,
    CheckpointIncompatibleError,
    Runner,
    RunState,
)

REVIEW_SCOPE = ("buyer_pain", "willingness_to_pay", "channel_fit")
RECIPE_CONTRACT_VERSION = 1
PACKAGED_CASE_IDS = {"pass_with_comments", "fail", "inconclusive"}
_PHASE_BY_STATUS = {
    "working": "waiting_for_expert",
    "needs_input": "awaiting_clarification",
    "succeeded": "feedback_available",
    "failed": "invalid_event",
}


class InvalidReplayCaseError(ValueError):
    code = "invalid_replay_case"

    def __init__(self):
        super().__init__("Replay case is not in the packaged catalog")


class ReplayCaseUnavailableError(ValueError):
    code = "replay_case_unavailable"

    def __init__(self):
        super().__init__("Replay case is unavailable for this adapter")


@dataclass(frozen=True)
class _ReplayCase:
    case_id: str
    label: str
    summary: str
    recipe_contract_version: int
    fixture_version: int
    digest: str
    adapter: ReplayAdapter


@dataclass(frozen=True)
class OracleRunView:
    """Oracle's typed view of current recipe and adapter progress payloads."""

    run_id: str
    parent_id: str | None
    intent: str
    mode: str
    recipe_contract_version: int
    artifact_id: str
    brief: str
    scope: tuple[str, ...]
    status: str
    phase: str
    question: str | None
    feedback: dict | None
    error: str | None
    answer: str | None
    cursor: int | None
    replay_case: str | None
    replay_fixture_version: int | None
    replay_fixture_digest: str | None


def _reject_nonfinite(value: str):
    raise ValueError(f"Non-finite JSON number: {value}")


def _load_packaged_cases() -> tuple[str, dict[str, _ReplayCase]]:
    manifest = json.loads(
        files(__package__).joinpath("replay.json").read_text(encoding="utf-8"),
        parse_constant=_reject_nonfinite,
    )
    if not isinstance(manifest, dict):
        raise ValueError("Replay manifest must be an object")
    recipe_version = manifest.get("recipe_contract_version")
    fixture_version = manifest.get("fixture_version")
    default_case = manifest.get("default_case")
    cases = manifest.get("cases")
    if (
        type(recipe_version) is not int
        or recipe_version != RECIPE_CONTRACT_VERSION
        or type(fixture_version) is not int
        or fixture_version != 1
        or not isinstance(default_case, str)
        or default_case != "inconclusive"
        or not isinstance(cases, dict)
        or set(cases) != PACKAGED_CASE_IDS
        or default_case not in cases
    ):
        raise ValueError("Replay manifest has an unsupported contract")

    loaded = {}
    for case_id, definition in cases.items():
        if not isinstance(definition, dict):
            raise ValueError("Replay case definition must be an object")
        label = definition.get("label")
        summary = definition.get("summary")
        events = definition.get("events")
        if (
            not isinstance(label, str)
            or not label.strip()
            or not isinstance(summary, str)
            or not summary.strip()
            or not isinstance(events, list)
        ):
            raise ValueError("Replay case definition is incomplete")
        adapter = ReplayAdapter(events)
        loaded[case_id] = _ReplayCase(
            case_id=case_id,
            label=label,
            summary=summary,
            recipe_contract_version=recipe_version,
            fixture_version=fixture_version,
            digest=adapter.fixture_digest(
                case_id=case_id,
                recipe_contract_version=recipe_version,
                fixture_version=fixture_version,
            ),
            adapter=adapter,
        )
    return default_case, loaded


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    scope: tuple[str, ...]
    recommendation: Literal["pass", "fail", "inconclusive"]
    rationale: str = Field(min_length=1)
    findings: tuple[str, ...]
    comments: tuple[str, ...]
    limitations: tuple[str, ...]
    next_steps: tuple[str, ...]
    provenance: Literal["synthetic"]

    @model_validator(mode="after")
    def validate_recommendation(self):
        if not self.rationale.strip():
            raise ValueError("Review rationale must be nonempty")
        if self.recommendation == "pass" and self.findings:
            raise ValueError("A material finding cannot accompany pass")
        return self


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    parent_id: str | None
    intent: Literal["review", "follow_up", "new_query"]
    artifact_id: str
    brief: str
    scope: tuple[str, ...]
    mode: Literal["replay"]
    replay_case: str | None = None
    status: Literal["working", "needs_input", "succeeded", "failed"]
    phase: str
    question: str | None
    feedback: Review | None
    error: str | None


class Oracle:
    def __init__(
        self,
        state_dir: Path,
        *,
        replay: ReplayAdapter | None = None,
    ):
        self._custom_replay = replay is not None
        if replay is None:
            self._default_case, self._cases = _load_packaged_cases()
        else:
            self._default_case = "custom"
            self._cases = {"custom": self._custom_case(replay)}
        self._runner = Runner(
            state_dir,
            self._transport_for_state,
            self._transition_for_event,
            self._validate_run,
            recipe_id="oracle",
            respond_for_state=self._respond_for_runner,
            observation_error_for_state=self._observation_error_for_recipe_state,
        )

    @staticmethod
    def _custom_case(replay: ReplayAdapter) -> _ReplayCase:
        recipe_version = RECIPE_CONTRACT_VERSION
        fixture_version = 1
        return _ReplayCase(
            case_id="custom",
            label="Custom injected replay",
            summary="A deterministic schedule supplied by the caller.",
            recipe_contract_version=recipe_version,
            fixture_version=fixture_version,
            digest=replay.fixture_digest(
                case_id="custom",
                recipe_contract_version=recipe_version,
                fixture_version=fixture_version,
            ),
            adapter=replay,
        )

    @property
    def replay_metadata(self) -> dict:
        """Packaged discovery data; injected fixtures are not catalog entries."""
        if self._custom_replay:
            _, packaged = _load_packaged_cases()
        else:
            packaged = self._cases
        return {
            "default_replay_case": "inconclusive",
            "replay_cases": [
                {"id": case_id, "label": case.label, "summary": case.summary}
                for case_id, case in packaged.items()
            ],
        }

    def _select_case(self, replay_case: str | None) -> _ReplayCase:
        if replay_case is None:
            replay_case = self._default_case
        elif not isinstance(replay_case, str):
            raise InvalidReplayCaseError()

        case = self._cases.get(replay_case)
        if case is not None:
            return case
        if replay_case == "custom" or (self._custom_replay and replay_case in PACKAGED_CASE_IDS):
            raise ReplayCaseUnavailableError()
        raise InvalidReplayCaseError()

    @staticmethod
    def _view(state: RunState) -> OracleRunView:
        recipe = state.recipe_payload
        adapter = state.adapter_payload
        if (
            state.checkpoint_version != RECIPE_CHECKPOINT_VERSION
            or state.recipe_id != "oracle"
            or state.recipe_contract_version != RECIPE_CONTRACT_VERSION
            or not isinstance(recipe, dict)
            or set(recipe) != {"brief", "scope", "artifact_id", "feedback", "answer"}
            or not isinstance(recipe["brief"], str)
            or not isinstance(recipe["scope"], list)
            or any(not isinstance(item, str) for item in recipe["scope"])
            or not isinstance(recipe["artifact_id"], str)
            or len(recipe["artifact_id"]) != 64
            or (recipe["feedback"] is not None and not isinstance(recipe["feedback"], dict))
            or (recipe["answer"] is not None and not isinstance(recipe["answer"], str))
            or not isinstance(adapter, dict)
            or set(adapter)
            != {"cursor", "replay_case", "replay_fixture_version", "replay_fixture_digest"}
            or type(adapter["cursor"]) is not int
            or adapter["cursor"] < 0
            or not isinstance(adapter["replay_case"], str)
            or not adapter["replay_case"]
            or type(adapter["replay_fixture_version"]) is not int
            or adapter["replay_fixture_version"] < 1
            or not isinstance(adapter["replay_fixture_digest"], str)
            or len(adapter["replay_fixture_digest"]) != 64
            or state.mode != "replay"
        ):
            raise CheckpointIncompatibleError()
        return OracleRunView(
            run_id=state.run_id,
            parent_id=state.parent_id,
            intent=state.intent,
            mode=state.mode,
            recipe_contract_version=state.recipe_contract_version,
            artifact_id=recipe["artifact_id"],
            brief=recipe["brief"],
            scope=tuple(recipe["scope"]),
            status=state.status,
            phase=state.phase,
            question=state.question,
            feedback=recipe["feedback"],
            error=state.error,
            answer=recipe["answer"],
            cursor=adapter["cursor"],
            replay_case=adapter["replay_case"],
            replay_fixture_version=adapter["replay_fixture_version"],
            replay_fixture_digest=adapter["replay_fixture_digest"],
        )

    def _adapter_for_state(self, state: RunState) -> ReplayAdapter:
        view = self._view(state)
        case = self._cases.get(view.replay_case)
        if (
            case is None
            or state.recipe_contract_version != case.recipe_contract_version
            or view.replay_fixture_version != case.fixture_version
            or view.replay_fixture_digest != case.digest
        ):
            raise CheckpointIncompatibleError()
        return case.adapter

    def _transport_for_state(self, state: RunState):
        return _OracleTransport(self, self._adapter_for_state(state))

    @staticmethod
    def _respond_for_runner(state: RunState, answer: object) -> dict:
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("A nonempty clarification answer is required")
        Oracle._view(state)
        recipe = dict(state.recipe_payload)
        recipe["answer"] = answer
        return {
            "recipe_payload": recipe,
            "question": None,
            "status": "working",
            "phase": "waiting_for_expert",
        }

    @staticmethod
    def _observation_error_for_recipe_state(error: Exception, state: RunState) -> dict:
        return {
            "status": "failed",
            "phase": "invalid_event",
            "error": "invalid_provider_event",
        }

    def start(
        self,
        brief: str,
        *,
        mode: str = "replay",
        parent_id: str | None = None,
        intent: str = "review",
        replay_case: str | None = None,
    ) -> Snapshot:
        if mode == "live":
            raise ValueError("Live integration is unavailable")
        if mode != "replay":
            raise ValueError("Unsupported Oracle mode")
        if not isinstance(brief, str):
            raise ValueError("A GTM brief must be text")
        self._validate_intent(intent, parent_id)
        recipe_payload = {
            "brief": brief,
            "scope": list(REVIEW_SCOPE),
            "artifact_id": hashlib.sha256(brief.encode()).hexdigest(),
            "feedback": None,
            "answer": None,
        }
        case = self._select_case(replay_case)
        return self._snapshot(
            self._runner.start(
                recipe_payload=recipe_payload,
                adapter_payload={
                    "cursor": 0,
                    "replay_case": case.case_id,
                    "replay_fixture_version": case.fixture_version,
                    "replay_fixture_digest": case.digest,
                },
                mode="replay",
                intent=intent,
                parent_id=parent_id,
                recipe_contract_version=case.recipe_contract_version,
                phase="waiting_for_expert",
            )
        )

    def inspect(self, run_id: str) -> Snapshot:
        return self._snapshot(self._runner.inspect(run_id))

    def resume(self, run_id: str) -> Snapshot:
        return self._snapshot(self._runner.resume(run_id))

    def respond(self, run_id: str, answer: str) -> Snapshot:
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("A nonempty clarification answer is required")
        return self._snapshot(self._runner.respond(run_id, answer))

    @staticmethod
    def _validate_review(feedback: dict, state: OracleRunView) -> dict:
        review = Review.model_validate(feedback)
        if review.artifact_id != state.artifact_id or review.scope != state.scope:
            raise ValueError("Review must bind to the exact brief and scope")
        return review.model_dump(mode="json")

    def _transition_for_event(self, event: dict, state: RunState) -> dict:
        view = self._view(state)
        recipe = dict(state.recipe_payload)
        adapter = dict(state.adapter_payload)
        if not isinstance(event, dict):
            raise ValueError("Replay event must be an object")
        if (
            event.get("kind") == "clarification"
            and isinstance(event.get("question"), str)
            and event["question"].strip()
        ):
            return {
                "status": "needs_input",
                "phase": "awaiting_clarification",
                "question": event["question"],
                "recipe_payload": {**recipe, "answer": None},
                "adapter_payload": {**adapter, "cursor": view.cursor + 1},
            }
        if event.get("kind") == "review":
            recipe["feedback"] = self._validate_review(event.get("feedback", {}), view)
            return {
                "status": "succeeded",
                "phase": "feedback_available",
                "question": None,
                "recipe_payload": recipe,
                "adapter_payload": {**adapter, "cursor": view.cursor + 1},
            }
        raise ValueError("Invalid replay event")

    @staticmethod
    def _validate_intent(intent: str, parent_id: str | None) -> None:
        if not isinstance(intent, str) or intent not in {"review", "follow_up", "new_query"}:
            raise ValueError("Unsupported Oracle intent")
        if (intent == "review") != (parent_id is None):
            raise ValueError("Linked start requires a parent and follow_up/new_query intent")

    @staticmethod
    def _validate_run(state: RunState) -> None:
        view = Oracle._view(state)
        if (
            not view.brief.strip()
            or view.scope != REVIEW_SCOPE
            or view.artifact_id != hashlib.sha256(view.brief.encode()).hexdigest()
        ):
            raise ValueError("Oracle artifact or GTM scope is incompatible")
        Oracle._validate_intent(view.intent, view.parent_id)
        if view.phase != _PHASE_BY_STATUS[view.status] or (view.status == "succeeded") != (
            view.feedback is not None
        ):
            raise ValueError("Oracle lifecycle does not match its phase and feedback")
        if view.feedback is not None:
            Oracle._validate_review(view.feedback, view)

    @staticmethod
    def _snapshot(state: RunState) -> Snapshot:
        view = Oracle._view(state)
        return Snapshot(
            run_id=view.run_id,
            parent_id=view.parent_id,
            intent=view.intent,
            artifact_id=view.artifact_id,
            brief=view.brief,
            scope=view.scope,
            mode=view.mode,
            replay_case=view.replay_case,
            status=view.status,
            phase=view.phase,
            question=view.question,
            feedback=view.feedback,
            error=view.error,
        )


class _OracleTransport:
    """Adapts replay events to the shared Runner seam."""

    def __init__(self, oracle: Oracle, adapter: ReplayAdapter):
        self._oracle = oracle
        self._adapter = adapter

    def observe(self, state: RunState):
        view = self._oracle._view(state)
        return self._adapter.advance(view.cursor, view.artifact_id, view.scope)

    def validate_progress(self, state: RunState) -> None:
        view = self._oracle._view(state)
        self._adapter.validate_progress(view.cursor, view.status, view.question, view.answer)
