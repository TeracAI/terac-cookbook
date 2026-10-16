"""Local run identity and checkpoints for the first Cookbook recipe."""

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from adapters.replay import ReplayAdapter

CHECKPOINT_VERSION = 1


class CheckpointIncompatibleError(ValueError):
    """A saved run cannot be interpreted by the installed recipe contract."""

    code = "checkpoint_incompatible"

    def __init__(self):
        super().__init__("Checkpoint is incompatible with the installed replay contract")


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    parent_id: str | None = None
    intent: str = Field(strict=True, min_length=1)
    artifact_id: str
    brief: str
    scope: tuple[str, ...] = ()
    mode: Literal["replay"] = "replay"
    checkpoint_version: int = Field(strict=True, ge=1)
    recipe_contract_version: int = Field(strict=True, ge=1)
    replay_case: str = Field(min_length=1)
    replay_fixture_version: int = Field(strict=True, ge=1)
    replay_fixture_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["working", "needs_input", "succeeded", "failed"] = "working"
    phase: str = "waiting_for_expert"
    question: str | None = None
    feedback: dict | None = None
    error: str | None = None
    cursor: int = Field(ge=0, strict=True)
    answer: str | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self):
        phases = {
            "working": "waiting_for_expert",
            "needs_input": "awaiting_clarification",
            "succeeded": "feedback_available",
            "failed": "invalid_event",
        }
        if self.phase != phases[self.status]:
            raise ValueError("Checkpoint phase does not match its lifecycle")
        if (self.status == "needs_input") != bool(self.question):
            raise ValueError("Checkpoint clarification does not match its lifecycle")
        if (self.status == "succeeded") != (self.feedback is not None):
            raise ValueError("Checkpoint delivery does not match its lifecycle")
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("Checkpoint error does not match its lifecycle")
        return self


class Runner:
    def __init__(
        self,
        state_dir: Path,
        adapter_for_state: Callable[[RunState], ReplayAdapter],
        transition_for_event: Callable[[dict, RunState], dict],
        validate_run: Callable[[RunState], None],
    ):
        self.state_dir = Path(state_dir)
        self.adapter_for_state = adapter_for_state
        self.transition_for_event = transition_for_event
        self.validate_run = validate_run

    def start(
        self,
        brief: str,
        scope: tuple[str, ...],
        *,
        intent: str,
        parent_id: str | None = None,
        replay_case: str,
        recipe_contract_version: int,
        replay_fixture_version: int,
        replay_fixture_digest: str,
    ) -> RunState:
        state = RunState(
            run_id=str(uuid4()),
            parent_id=parent_id,
            intent=intent,
            artifact_id=hashlib.sha256(brief.encode()).hexdigest(),
            brief=brief,
            scope=scope,
            checkpoint_version=CHECKPOINT_VERSION,
            recipe_contract_version=recipe_contract_version,
            replay_case=replay_case,
            replay_fixture_version=replay_fixture_version,
            replay_fixture_digest=replay_fixture_digest,
            cursor=0,
        )
        if parent_id is not None:
            self.inspect(parent_id)
        self.save(state)
        return state

    def resume(self, run_id: str) -> RunState:
        state = self.inspect(run_id)
        if state.status != "working":
            return state
        try:
            event = self.adapter_for_state(state).advance(
                state.cursor, state.artifact_id, state.scope
            )
            update = self.transition_for_event(event, state)
            if not isinstance(update, dict):
                raise ValueError("Event transition must be an object")
            state = RunState.model_validate(state.model_copy(update=update).model_dump())
        except ValueError:
            state = state.model_copy(
                update={
                    "status": "failed",
                    "phase": "invalid_event",
                    "error": "invalid_provider_event",
                }
            )
        self.save(state)
        return state

    def respond(self, run_id: str, answer: str) -> RunState:
        state = self.inspect(run_id)
        if state.status != "needs_input":
            raise ValueError("Run has no pending clarification")
        state = state.model_copy(
            update={
                "answer": answer,
                "question": None,
                "status": "working",
                "phase": "waiting_for_expert",
            }
        )
        self.save(state)
        return state

    def inspect(self, run_id: str) -> RunState:
        current_id = run_id
        seen: set[str] = set()
        requested = None
        while True:
            if current_id in seen:
                raise CheckpointIncompatibleError()
            seen.add(current_id)
            try:
                checkpoint_path = self.path(current_id)
            except ValueError as exc:
                if requested is None:
                    raise
                raise CheckpointIncompatibleError() from exc
            try:
                state = RunState.model_validate_json(checkpoint_path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                if requested is None:
                    raise
                raise CheckpointIncompatibleError() from exc
            except (ValidationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise CheckpointIncompatibleError() from exc
            try:
                if (
                    state.run_id != current_id
                    or state.artifact_id != hashlib.sha256(state.brief.encode()).hexdigest()
                ):
                    raise CheckpointIncompatibleError()
                self.validate_run(state)
                adapter = self.adapter_for_state(state)
                adapter.validate_progress(state.cursor, state.status, state.question, state.answer)
            except CheckpointIncompatibleError:
                raise
            except (ValidationError, ValueError) as exc:
                raise CheckpointIncompatibleError() from exc
            if requested is None:
                requested = state
            if state.parent_id is None:
                return requested
            current_id = state.parent_id

    def path(self, run_id: str) -> Path:
        if not isinstance(run_id, str):
            raise ValueError("Expected a canonical run identity")
        if str(UUID(run_id)) != run_id:
            raise ValueError("Expected a canonical run identity")
        return self.state_dir / f"{run_id}.json"

    def save(self, state: RunState) -> None:
        state = RunState.model_validate(state.model_dump())
        self.validate_run(state)
        self.adapter_for_state(state).validate_progress(
            state.cursor, state.status, state.question, state.answer
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        destination = self.path(state.run_id)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.state_dir, delete=False
        ) as temporary:
            temporary.write(state.model_dump_json(indent=2))
        try:
            os.replace(temporary.name, destination)
        finally:
            Path(temporary.name).unlink(missing_ok=True)
