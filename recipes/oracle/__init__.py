"""Oracle's public caller interface for synthetic GTM expert review."""

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from adapters.replay import ReplayAdapter
from runtime.runner import CHECKPOINT_VERSION, CheckpointIncompatibleError, Runner, RunState

REVIEW_SCOPE = ("buyer_pain", "willingness_to_pay", "channel_fit")
RECIPE_CONTRACT_VERSION = 1
PACKAGED_CASE_IDS = {"pass_with_comments", "fail", "inconclusive"}


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
    replay_case: str
    status: Literal["working", "needs_input", "succeeded", "failed"]
    phase: str
    question: str | None
    feedback: Review | None
    error: str | None


class Oracle:
    def __init__(self, state_dir: Path, *, replay: ReplayAdapter | None = None):
        self._custom_replay = replay is not None
        if replay is None:
            self._default_case, self._cases = _load_packaged_cases()
        else:
            self._default_case = "custom"
            self._cases = {
                "custom": self._custom_case(replay),
            }
        self._runner = Runner(
            state_dir,
            self._adapter_for_state,
            self._transition_for_event,
            self._validate_run,
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

    def _adapter_for_state(self, state: RunState) -> ReplayAdapter:
        case = self._cases.get(state.replay_case)
        if case is None:
            raise CheckpointIncompatibleError()
        if (
            state.checkpoint_version != CHECKPOINT_VERSION
            or state.recipe_contract_version != case.recipe_contract_version
            or state.replay_fixture_version != case.fixture_version
            or state.replay_fixture_digest != case.digest
        ):
            raise CheckpointIncompatibleError()
        return case.adapter

    def start(
        self,
        brief: str,
        *,
        mode: str = "replay",
        parent_id: str | None = None,
        intent: str = "review",
        replay_case: str | None = None,
    ) -> Snapshot:
        if mode != "replay":
            raise ValueError("Live integration is unavailable")
        if not isinstance(brief, str):
            raise ValueError("A GTM brief must be text")
        self._validate_intent(intent, parent_id)
        case = self._select_case(replay_case)
        return self._snapshot(
            self._runner.start(
                brief,
                REVIEW_SCOPE,
                intent=intent,
                parent_id=parent_id,
                replay_case=case.case_id,
                recipe_contract_version=case.recipe_contract_version,
                replay_fixture_version=case.fixture_version,
                replay_fixture_digest=case.digest,
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
    def _validate_review(feedback: dict, state: RunState) -> dict:
        review = Review.model_validate(feedback)
        if review.artifact_id != state.artifact_id or review.scope != state.scope:
            raise ValueError("Review must bind to the exact brief and scope")
        return review.model_dump(mode="json")

    @staticmethod
    def _transition_for_event(event: dict, state: RunState) -> dict:
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
                "answer": None,
                "cursor": state.cursor + 1,
            }
        if event.get("kind") == "review":
            feedback = Oracle._validate_review(event.get("feedback", {}), state)
            return {
                "status": "succeeded",
                "phase": "feedback_available",
                "feedback": feedback,
                "question": None,
                "cursor": state.cursor + 1,
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
        if not state.brief.strip() or state.scope != REVIEW_SCOPE:
            raise ValueError("Oracle requires a nonempty brief and its exact GTM scope")
        Oracle._validate_intent(state.intent, state.parent_id)
        if state.recipe_contract_version != RECIPE_CONTRACT_VERSION:
            raise CheckpointIncompatibleError()
        if state.feedback is not None:
            Oracle._validate_review(state.feedback, state)

    @staticmethod
    def _snapshot(state: RunState) -> Snapshot:
        return Snapshot.model_validate(
            state.model_dump(
                exclude={
                    "cursor",
                    "answer",
                    "checkpoint_version",
                    "recipe_contract_version",
                    "replay_fixture_version",
                    "replay_fixture_digest",
                }
            )
        )
