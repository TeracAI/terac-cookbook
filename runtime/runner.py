"""Shared versioned run lifecycle with recipe-owned opaque progress."""

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from runtime.json_utils import unique_json_object

RECIPE_CHECKPOINT_VERSION = 3


class CheckpointIncompatibleError(ValueError):
    """A saved run cannot be interpreted by the installed recipe contract."""

    code = "checkpoint_incompatible"

    def __init__(self):
        super().__init__("Checkpoint is incompatible with the installed recipe contract")


class RunState(BaseModel):
    """Common identity and lifecycle around opaque recipe and adapter payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    parent_id: str | None = None
    intent: str = Field(strict=True, min_length=1)
    mode: str = Field(strict=True, min_length=1)
    checkpoint_version: Literal[3] = RECIPE_CHECKPOINT_VERSION
    recipe_contract_version: int = Field(strict=True, ge=1)
    recipe_id: str = Field(strict=True, min_length=1)
    status: Literal["working", "needs_input", "succeeded", "failed"] = "working"
    phase: str = Field(strict=True, min_length=1)
    question: str | None = Field(default=None, strict=True, min_length=1)
    error: str | None = Field(default=None, strict=True, min_length=1)
    recipe_payload: dict
    adapter_payload: dict

    @model_validator(mode="after")
    def validate_common_lifecycle(self):
        if self.question is not None and not self.question.strip():
            raise ValueError("Checkpoint clarification cannot be empty")
        if (self.status == "needs_input") != (self.question is not None):
            raise ValueError("Checkpoint clarification does not match its lifecycle")
        if self.error is not None and not self.error.strip():
            raise ValueError("Checkpoint error cannot be empty")
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("Checkpoint error does not match its lifecycle")
        return self


class RunAdapter(Protocol):
    def observe(self, state: RunState) -> object | None: ...

    def validate_progress(self, state: RunState) -> None: ...


class Runner:
    def __init__(
        self,
        state_dir: Path,
        adapter_for_state: Callable[[RunState], RunAdapter],
        transition_for_event: Callable[[object, RunState], dict],
        validate_run: Callable[[RunState], None],
        *,
        recipe_id: str,
        respond_for_state: Callable[[RunState, object], dict] | None = None,
        observation_error_for_state: Callable[[Exception, RunState], dict] | None = None,
    ):
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            raise ValueError("Runner requires a nonempty recipe_id")
        self.state_dir = Path(state_dir)
        self.adapter_for_state = adapter_for_state
        self.transition_for_event = transition_for_event
        self.validate_run = validate_run
        self.recipe_id = recipe_id
        self.respond_for_state = respond_for_state
        self.observation_error_for_state = observation_error_for_state

    def start(
        self,
        *,
        mode: str,
        intent: str,
        parent_id: str | None = None,
        recipe_contract_version: int,
        recipe_payload: dict,
        adapter_payload: dict,
        phase: str,
    ) -> RunState:
        if not isinstance(recipe_payload, dict) or not isinstance(adapter_payload, dict):
            raise ValueError("Recipe start requires opaque payload objects")
        state = RunState(
            run_id=str(uuid4()),
            parent_id=parent_id,
            intent=intent,
            mode=mode,
            checkpoint_version=RECIPE_CHECKPOINT_VERSION,
            recipe_contract_version=recipe_contract_version,
            recipe_id=self.recipe_id,
            phase=phase,
            recipe_payload=recipe_payload,
            adapter_payload=adapter_payload,
        )
        self.save(state)
        return state

    def resume(self, run_id: str) -> RunState:
        state = self.inspect(run_id)
        if state.status != "working":
            return state
        try:
            adapter = self.adapter_for_state(state)
            event = adapter.observe(state)
            if event is None:
                return state
            state = self._apply_update(state, self.transition_for_event(event, state))
        except CheckpointIncompatibleError:
            raise
        except (ValidationError, ValueError) as exc:
            if self.observation_error_for_state is None:
                raise
            state = self._apply_update(state, self.observation_error_for_state(exc, state))
        self.save(state)
        return state

    def respond(self, run_id: str, answer: str) -> RunState:
        state = self.inspect(run_id)
        if state.status != "needs_input":
            raise ValueError("Run has no pending clarification")
        if self.respond_for_state is None:
            raise ValueError("Recipe does not accept a clarification response")
        state = self._apply_update(state, self.respond_for_state(state, answer))
        self.save(state)
        return state

    @staticmethod
    def _apply_update(state: RunState, update: object) -> RunState:
        if not isinstance(update, dict):
            raise ValueError("Run transition must be an object")
        candidate = RunState.model_validate(state.model_copy(update=update).model_dump())
        immutable_fields = (
            "run_id",
            "parent_id",
            "intent",
            "mode",
            "checkpoint_version",
            "recipe_contract_version",
            "recipe_id",
        )
        if any(getattr(candidate, name) != getattr(state, name) for name in immutable_fields):
            raise CheckpointIncompatibleError()
        return candidate

    def inspect(self, run_id: str) -> RunState:
        current_id = run_id
        seen: set[str] = set()
        requested = None
        requested_owner: str | None = None
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
                payload = json.loads(
                    checkpoint_path.read_text(encoding="utf-8"),
                    object_pairs_hook=unique_json_object,
                )
                if (
                    not isinstance(payload, dict)
                    or type(payload.get("checkpoint_version")) is not int
                    or payload["checkpoint_version"] != RECIPE_CHECKPOINT_VERSION
                ):
                    raise CheckpointIncompatibleError()
                state = RunState.model_validate(payload)
            except FileNotFoundError as exc:
                if requested is None:
                    raise
                raise CheckpointIncompatibleError() from exc
            except CheckpointIncompatibleError:
                raise
            except (ValidationError, ValueError, UnicodeDecodeError) as exc:
                raise CheckpointIncompatibleError() from exc
            try:
                if state.run_id != current_id:
                    raise CheckpointIncompatibleError()
                owner = self._owner_for_state(state)
                if requested is not None and owner != requested_owner:
                    raise CheckpointIncompatibleError()
                self.validate_run(state)
                self.validate_adapter_progress(state)
            except CheckpointIncompatibleError:
                raise
            except (ValidationError, ValueError) as exc:
                raise CheckpointIncompatibleError() from exc
            if requested is None:
                requested = state
                requested_owner = owner
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
        if self._owner_for_state(state) != self.recipe_id:
            raise CheckpointIncompatibleError()
        if state.parent_id is not None:
            self.inspect(state.parent_id)
        self.validate_run(state)
        self.validate_adapter_progress(state)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        destination = self.path(state.run_id)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.state_dir, delete=False
        ) as temporary:
            temporary.write(json.dumps(state.model_dump(mode="json"), indent=2))
        try:
            os.replace(temporary.name, destination)
        finally:
            Path(temporary.name).unlink(missing_ok=True)

    def _owner_for_state(self, state: RunState) -> str:
        if state.recipe_id != self.recipe_id:
            raise CheckpointIncompatibleError()
        return state.recipe_id

    def validate_adapter_progress(self, state: RunState) -> None:
        self.adapter_for_state(state).validate_progress(state)
