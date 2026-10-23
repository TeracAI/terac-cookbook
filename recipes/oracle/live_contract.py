"""Strict local binding and checkpoint rules for Oracle's read-only live seam."""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from adapters.terac_study_contract import PROVIDER_STATUSES, ReviewedStudyBinding, StudyReadError
from runtime.json_utils import unique_json_object
from runtime.runner import RunState

LIVE_BINDING_FORMAT_VERSION = 1
ORACLE_TEMPLATE_VERSION = 1
_BINDING_FIELDS = {
    "format_version",
    "template_version",
    "read_only",
    "organization_id",
    "project_id",
    "opportunity_id",
    "brief_sha256",
    "definition_sha256",
}
_OBSERVATION_FIELDS = {
    "status",
    "num_participants",
    "total_cost_cents",
    "cost_per_participant_cents",
    "currency",
}


@dataclass(frozen=True)
class OracleLiveBinding:
    organization_id: str
    project_id: str
    opportunity_id: str
    brief_sha256: str
    definition_sha256: str
    format_version: int = LIVE_BINDING_FORMAT_VERSION
    template_version: int = ORACLE_TEMPLATE_VERSION
    read_only: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.format_version) is not int
            or self.format_version != LIVE_BINDING_FORMAT_VERSION
            or type(self.template_version) is not int
            or self.template_version != ORACLE_TEMPLATE_VERSION
            or self.read_only is not True
        ):
            raise StudyReadError("invalid_binding")
        self.provider_binding()

    def provider_binding(self) -> ReviewedStudyBinding:
        return ReviewedStudyBinding(
            organization_id=self.organization_id,
            project_id=self.project_id,
            opportunity_id=self.opportunity_id,
            brief_sha256=self.brief_sha256,
            definition_sha256=self.definition_sha256,
        )

    def checkpoint_value(self) -> dict:
        return asdict(self)


def load_oracle_live_binding(path: Path) -> OracleLiveBinding:
    """Load one reviewed study binding without retaining credentials or raw IDs in errors."""
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique_json_object
        )
        if not isinstance(value, dict) or set(value) != _BINDING_FIELDS:
            raise ValueError("Invalid binding fields")
        if type(value["read_only"]) is not bool:
            raise ValueError("Invalid read-only flag")
        return OracleLiveBinding(**value)
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        raise StudyReadError("invalid_binding") from None


def oracle_phase_for_provider_status(status: str) -> str:
    if status not in PROVIDER_STATUSES:
        raise ValueError("Provider status is not recognized")
    return "awaiting_launch" if status == "draft" else "provider_status_available"


def is_safe_provider_observation(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _OBSERVATION_FIELDS:
        return False
    if not isinstance(value["status"], str) or value["status"] not in PROVIDER_STATUSES:
        return False
    if type(value["num_participants"]) is not int or value["num_participants"] < 1:
        return False
    costs = (value["total_cost_cents"], value["cost_per_participant_cents"])
    if costs == (None, None) and value["currency"] is None:
        return True
    return value["currency"] == "usd" and all(type(cost) is int and cost >= 0 for cost in costs)


def validate_live_progress(state: RunState) -> None:
    checkpoint = state.adapter_payload
    if not isinstance(checkpoint, dict) or set(checkpoint) != {"binding", "observation"}:
        raise ValueError("Live Oracle checkpoint is incomplete")
    binding = checkpoint["binding"]
    if not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS:
        raise ValueError("Live Oracle binding is incompatible")
    try:
        parsed = OracleLiveBinding(**binding)
    except (TypeError, StudyReadError) as exc:
        raise ValueError("Live Oracle binding is incompatible") from exc

    recipe = state.recipe_payload
    brief = recipe.get("brief") if isinstance(recipe, dict) else None
    brief_digest = (
        hashlib.sha256(brief.encode("utf-8")).hexdigest() if isinstance(brief, str) else None
    )
    if brief_digest != parsed.brief_sha256 or parsed.brief_sha256 != state.recipe_payload.get(
        "artifact_id"
    ):
        raise ValueError("Live Oracle brief binding is incompatible")

    observation = checkpoint["observation"]
    expected_phase = "awaiting_provider_refresh"
    if observation is not None:
        if not is_safe_provider_observation(observation):
            raise ValueError("Live Oracle observation is incompatible")
        expected_phase = oracle_phase_for_provider_status(observation["status"])
    if (
        state.mode != "live"
        or state.status != "working"
        or state.phase != expected_phase
        or state.question is not None
        or state.recipe_payload.get("feedback") is not None
        or state.error is not None
    ):
        raise ValueError("Live Oracle lifecycle is incompatible")
