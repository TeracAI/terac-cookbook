"""Dependency-free study types shared by the recipe and Terac reader."""

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

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
CUSTOMER_SCREENING_REVIEW_MODES = frozenset({"auto_invite", "manual_review"})
FEASIBILITY_REQUEST_STATUSES = frozenset({"RECEIVED", "RESPONDED", "WON", "LOST", "NOT_PURSUED"})
# Captured 2026-10-23 via terac_get_filter_options for multi_select--country.
# SHA-256 of sorted, newline-delimited IDs:
# b89860570553322b00fee8cebeb3ea578ff03ee02be9c7abcf594a39bc2c4804.
TERAC_COUNTRY_FILTER_IDS = frozenset(
    """
    AF AX AL DZ AS AD AO AI AQ AG AR AM AW AU AT AZ BS BH BD BB BE BZ BJ BM
    BT BO BQ BA BW BV BR IO VG BN BG BF BI CV KH CM CA KY CF TD CL CN CX CC
    CO KM CK CR CI HR CU CW CY CZ CD DK DJ DM DO EC EG SV GQ ER EE SZ ET FK FO
    FJ FI FR GF PF TF GA GM GE DE GH GI GR GL GD GP GU GT GG GN GW GY HM HN HK HU
    IS IN ID IR IE IM IL IT JM JP JE JO KZ KE KI XK KW KG LA LV LS LR LY LI LT
    LU MO MG MW MY MV ML MT MH MQ MR MU YT MX FM MD MC MN ME MS MA MZ NA NR NP NL
    NC NZ NI NE NG NU NF KP MK MP NO OM PK PW PA PG PY PE PH PN PL PT PR QA CG
    RE RO RW BL SH KN LC MF PM VC WS SM ST SA SN RS SC SL SG SX SK SI SB SO ZA
    GS KR SS ES LK SD SR SJ SE CH SY TW TJ TZ TH TL TG TK TO TT TN TR TM TC TV
    VI UG UA AE GB US UM UY UZ VU VA VN WF EH YE ZM ZW
    """.split()
)
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_FILTER_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*--[A-Za-z][A-Za-z0-9_-]{0,127}\Z")
_FILTER_OPERATOR_PATTERN = re.compile(r"\$[a-z_]{1,16}\Z")
PAY_RATE_FREQUENCIES = frozenset({"ONE_TIME", "HOURLY", "PER_TASK"})
DEVICE_TYPES = frozenset(
    {"desktop", "mobile_ios", "mobile_android", "tablet_ios", "tablet_android", "other"}
)
_MIME_TYPE = re.compile(r"(?:[A-Za-z0-9!#$&^_.+-]+|\*)/(?:[A-Za-z0-9!#$&^_.+-]+|\*)\Z")


def validate_provider_id(value: object) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid provider identity")
    return value


def is_valid_https_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or any(
            character.isspace() or ord(character) < 33 or character == "\\" for character in value
        )
    ):
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
    )


def normalize_feasibility_request(value: object) -> dict:
    allowed = {
        "taskDescription",
        "panelDescription",
        "submissionCount",
        "timelineHours",
        "payRateFrequency",
        "loiMinutes",
    }
    required = {"taskDescription", "panelDescription", "submissionCount"}
    if not isinstance(value, Mapping) or set(value) - allowed or required - set(value):
        raise ValueError("invalid feasibility request")
    task = value["taskDescription"]
    panel = value["panelDescription"]
    count = value["submissionCount"]
    if (
        not isinstance(task, str)
        or not task.strip()
        or len(task) > 10_000
        or not isinstance(panel, str)
        or not panel.strip()
        or len(panel) > 10_000
        or type(count) is not int
        or not 1 <= count <= 1000
    ):
        raise ValueError("invalid feasibility request")
    timeline = value.get("timelineHours")
    loi = value.get("loiMinutes")
    pay_rate = value.get("payRateFrequency")
    if timeline is not None and (type(timeline) is not int or timeline < 1):
        raise ValueError("invalid feasibility request")
    if loi is not None and (type(loi) is not int or loi < 1):
        raise ValueError("invalid feasibility request")
    if pay_rate is not None and (
        not isinstance(pay_rate, str) or pay_rate not in PAY_RATE_FREQUENCIES
    ):
        raise ValueError("invalid feasibility request")
    payload = dict(value)
    json.dumps(payload, allow_nan=False)
    return payload


def normalize_task(value: object) -> dict:
    allowed = {
        "sequence",
        "task_type",
        "review_type",
        "task_url",
        "title",
        "description",
        "duration_minutes",
        "instructions",
        "accepted_mime_types",
        "min_files",
        "max_files",
        "max_file_size_bytes",
    }
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise ValueError("invalid task")
    if (
        type(value.get("sequence")) is not int
        or value["sequence"] < 1
        or not isinstance(value.get("task_type"), str)
        or value["task_type"] not in {"interview", "file_upload", "activity"}
        or not isinstance(value.get("review_type"), str)
        or value["review_type"] not in {"auto_approve", "manual_review", "self_report"}
    ):
        raise ValueError("invalid task")
    for field in ("title", "description", "instructions"):
        if field in value and (not isinstance(value[field], str) or not value[field].strip()):
            raise ValueError("invalid task")
    if "duration_minutes" in value and (
        type(value["duration_minutes"]) is not int or value["duration_minutes"] < 1
    ):
        raise ValueError("invalid task")
    task_type = value["task_type"]
    if task_type == "interview":
        task_url = value.get("task_url")
        if not is_valid_https_url(task_url):
            raise ValueError("invalid task")
        if "duration_minutes" not in value:
            raise ValueError("invalid task")
    elif task_type == "file_upload":
        mime_types = value.get("accepted_mime_types")
        if (
            not isinstance(value.get("instructions"), str)
            or not value["instructions"].strip()
            or not isinstance(mime_types, list)
            or not mime_types
            or any(
                not isinstance(item, str) or _MIME_TYPE.fullmatch(item) is None
                for item in mime_types
            )
            or "task_url" in value
            or type(value.get("min_files")) is not int
            or not 1 <= value["min_files"] <= 20
            or type(value.get("max_files")) is not int
            or not value["min_files"] <= value["max_files"] <= 20
            or type(value.get("max_file_size_bytes")) is not int
            or not 1 <= value["max_file_size_bytes"] <= 25 * 1024 * 1024
        ):
            raise ValueError("invalid task")
    elif "duration_minutes" not in value:
        raise ValueError("invalid task")
    if value["review_type"] == "auto_approve" and not is_valid_https_url(value.get("task_url")):
        raise ValueError("automatic approval requires an HTTPS completion URL")
    return dict(value)


def _valid_filter_scalar(value: object) -> bool:
    if isinstance(value, str):
        return len(value) <= 10_000
    if type(value) in {bool, int}:
        return True
    return type(value) is float and math.isfinite(value)


def normalize_audience_filters(value: object) -> list[dict]:
    """Copy only the flat, declared filter shape used by study definitions."""
    if not isinstance(value, list) or not value:
        raise ValueError("invalid audience filters")
    normalized = []
    for item in value:
        if not isinstance(item, Mapping) or len(item) != 1:
            raise ValueError("invalid audience filters")
        name, condition = next(iter(item.items()))
        if (
            not isinstance(name, str)
            or _FILTER_NAME_PATTERN.fullmatch(name) is None
            or not isinstance(condition, Mapping)
            or len(condition) != 1
        ):
            raise ValueError("invalid audience filters")
        operator, operand = next(iter(condition.items()))
        if not isinstance(operator, str) or _FILTER_OPERATOR_PATTERN.fullmatch(operator) is None:
            raise ValueError("invalid audience filters")
        if isinstance(operand, list):
            if not operand or any(not _valid_filter_scalar(value) for value in operand):
                raise ValueError("invalid audience filters")
            normalized_operand = list(operand)
        elif _valid_filter_scalar(operand):
            normalized_operand = operand
        else:
            raise ValueError("invalid audience filters")
        normalized.append({name: {operator: normalized_operand}})
    return normalized


def normalize_opportunity_definition(value: object) -> dict:
    allowed = {
        "title",
        "description",
        "project_id",
        "num_participants",
        "business_type",
        "customer_screening_review",
        "tasks",
        "filters",
        "device_types",
        "expected_days_to_complete",
        "feasibility_request_id",
    }
    required = {
        "title",
        "project_id",
        "num_participants",
        "business_type",
        "tasks",
        "filters",
        "device_types",
    }
    if not isinstance(value, Mapping) or set(value) - allowed or required - set(value):
        raise ValueError("invalid opportunity definition")
    if (
        not isinstance(value["title"], str)
        or not value["title"].strip()
        or len(value["title"]) > 200
    ):
        raise ValueError("invalid opportunity definition")
    if (
        not isinstance(value["project_id"], str)
        or _ID_PATTERN.fullmatch(value["project_id"]) is None
    ):
        raise ValueError("invalid opportunity definition")
    if (
        type(value["num_participants"]) is not int
        or not 1 <= value["num_participants"] <= 1000
        or not isinstance(value["business_type"], str)
        or value["business_type"] not in {"b2b", "b2c"}
        or not isinstance(value["filters"], list)
        or not value["filters"]
        or any(not isinstance(item, Mapping) or not item for item in value["filters"])
        or not isinstance(value["tasks"], list)
        or not value["tasks"]
        or (
            "customer_screening_review" in value
            and (
                not isinstance(value["customer_screening_review"], str)
                or value["customer_screening_review"] not in CUSTOMER_SCREENING_REVIEW_MODES
            )
        )
        or not isinstance(value["device_types"], list)
        or not value["device_types"]
        or any(
            not isinstance(item, str) or item not in DEVICE_TYPES for item in value["device_types"]
        )
        or len(set(value["device_types"])) != len(value["device_types"])
    ):
        raise ValueError("invalid opportunity definition")
    if "description" in value and (
        not isinstance(value["description"], str) or len(value["description"]) > 280
    ):
        raise ValueError("invalid participant description")
    if "feasibility_request_id" in value:
        validate_provider_id(value["feasibility_request_id"])
    filters = normalize_audience_filters(value["filters"])
    tasks = [normalize_task(task) for task in value["tasks"]]
    sequences = [task["sequence"] for task in tasks]
    if sequences != list(range(1, len(sequences) + 1)):
        raise ValueError("tasks must have contiguous sequence numbers")
    if "expected_days_to_complete" in value and (
        type(value["expected_days_to_complete"]) is not int
        or value["expected_days_to_complete"] < 5
    ):
        raise ValueError("invalid expected completion window")
    payload = dict(value)
    payload["filters"] = filters
    payload["tasks"] = tasks
    json.dumps(payload, allow_nan=False)
    return payload


class StudyReadError(RuntimeError):
    """A bounded failure without credential, provider body or SDK exception text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Terac study read failed: {code}")


class StudyWriteError(RuntimeError):
    """A sanitized write failure with explicit effect certainty."""

    def __init__(self, code: str, outcome: str):
        if outcome not in {"not_sent", "rejected", "unknown"}:
            raise ValueError("Invalid write outcome")
        self.code = code
        self.outcome = outcome
        super().__init__(f"Terac study write {outcome}: {code}")


@dataclass(frozen=True)
class ReviewedStudyBinding:
    organization_id: str
    project_id: str
    opportunity_id: str
    brief_sha256: str
    definition_sha256: str

    def __post_init__(self):
        for value in (self.organization_id, self.project_id, self.opportunity_id):
            try:
                validate_provider_id(value)
            except ValueError:
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
