"""Bounded local observations for an explicitly configured simulation run."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

MAX_OBSERVATION_BYTES = 65_536
SIMULATION_CHECKPOINT_VERSION = 2


class SimulationUnavailableError(ValueError):
    """A simulation run was requested without local mailbox configuration."""

    code = "simulation_unavailable"

    def __init__(self):
        super().__init__("Local simulation is not configured")


class SimulationObservationError(ValueError):
    """The next local simulation observation is malformed or mismatched."""

    code = "simulation_observation_invalid"

    def __init__(self):
        super().__init__("Local simulation observation is invalid")


class SimulationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    run_id: str = Field(min_length=1)
    reference: str = Field(min_length=1)
    artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: tuple[str, ...]
    revision: int = Field(ge=1)
    kind: Literal["waiting", "clarification", "review"]
    question: str | None = None
    feedback: dict | None = None

    @field_validator("scope", mode="before")
    @classmethod
    def accept_json_array(cls, value):
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_kind_payload(self):
        base_fields = {"run_id", "reference", "artifact_id", "scope", "revision", "kind"}
        payload_fields = {
            "waiting": set(),
            "clarification": {"question"},
            "review": {"feedback"},
        }[self.kind]
        if self.model_fields_set != base_fields | payload_fields:
            raise ValueError("Observation fields do not match its kind")
        if self.kind == "clarification" and (
            not isinstance(self.question, str) or not self.question.strip()
        ):
            raise ValueError("Clarification observation requires a nonempty question")
        if self.kind == "review" and self.feedback is None:
            raise ValueError("Review observation requires feedback")
        return self


@dataclass(frozen=True)
class MailboxRead:
    observation: SimulationObservation
    digest: str


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value: str):
    raise ValueError(f"Non-finite JSON number: {value}")


def _validate_unicode(value):
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_unicode(key)
            _validate_unicode(item)
    elif isinstance(value, list):
        for item in value:
            _validate_unicode(item)


class SimulationMailbox:
    def __init__(self, simulation_dir: Path | None):
        self.simulation_dir = Path(simulation_dir) if simulation_dir is not None else None

    def validate_progress(self, state) -> None:
        if (
            state.mode != "simulation"
            or state.checkpoint_version != SIMULATION_CHECKPOINT_VERSION
            or state.replay_case is not None
            or state.replay_fixture_version is not None
            or state.replay_fixture_digest is not None
            or state.cursor is not None
        ):
            raise ValueError("Simulation checkpoint contract is incompatible")
        self._reference_id(state.simulation_ref)
        if type(state.simulation_revision) is not int or state.simulation_revision < 0:
            raise ValueError("Simulation revision is invalid")
        digest = state.simulation_observation_digest
        if state.simulation_revision == 0:
            if digest is not None:
                raise ValueError("Initial simulation checkpoint cannot have an observation digest")
            if state.status != "working" or state.answer is not None:
                raise ValueError("Initial simulation checkpoint has released work")
        elif (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("Simulation observation digest is invalid")

    def observe(self, state) -> MailboxRead | None:
        if self.simulation_dir is None:
            raise SimulationUnavailableError()
        reference_id = self._reference_id(state.simulation_ref)
        next_revision = state.simulation_revision + 1
        observation_path = (
            self.simulation_dir
            / "runs"
            / reference_id
            / "observations"
            / f"{next_revision:020d}.json"
        )
        try:
            with observation_path.open("rb") as source:
                raw = source.read(MAX_OBSERVATION_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise SimulationObservationError() from exc
        if len(raw) > MAX_OBSERVATION_BYTES:
            raise SimulationObservationError()
        try:
            parsed = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_nonfinite,
            )
            _validate_unicode(parsed)
            observation = SimulationObservation.model_validate(parsed)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
            RecursionError,
        ) as exc:
            raise SimulationObservationError() from exc
        if observation.revision != next_revision:
            raise SimulationObservationError()
        return MailboxRead(observation, hashlib.sha256(raw).hexdigest())

    @staticmethod
    def _reference_id(reference: str | None) -> str:
        if not isinstance(reference, str) or not reference.startswith("sim:"):
            raise ValueError("Simulation reference is invalid")
        reference_id = reference[4:]
        try:
            if str(UUID(reference_id)) != reference_id:
                raise ValueError("Simulation reference is invalid")
        except (ValueError, AttributeError) as exc:
            raise ValueError("Simulation reference is invalid") from exc
        return reference_id
