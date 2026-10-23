"""Dependency-free study types shared by the recipe and Terac reader."""

import re
from dataclasses import dataclass

PROVIDER_STATUSES = frozenset(
    {
        "draft",
        "pre_recruiting",
        "active",
        "fulfilled",
        "paused",
        "stopped",
        "completed",
        "expired",
        "cancelled",
        "failed",
    }
)


class StudyReadError(RuntimeError):
    """A bounded failure without credential, provider body or SDK exception text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Terac study read failed: {code}")


@dataclass(frozen=True)
class ReviewedStudyBinding:
    organization_id: str
    project_id: str
    opportunity_id: str
    brief_sha256: str
    definition_sha256: str

    def __post_init__(self):
        for value in (self.organization_id, self.project_id, self.opportunity_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                raise StudyReadError("invalid_binding")
        for value in (self.brief_sha256, self.definition_sha256):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise StudyReadError("invalid_binding")


@dataclass(frozen=True)
class StudyObservation:
    opportunity_id: str
    project_id: str
    status: str
    definition_sha256: str
    brief_sha256: str
    num_participants: int
    total_cost_cents: int | None
    cost_per_participant_cents: int | None
    currency: str | None
