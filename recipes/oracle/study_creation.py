"""Oracle-owned study request validation and checkpoint shape."""

import hashlib
import json
from collections.abc import Mapping

from adapters.terac_study_contract import (
    CUSTOMER_SCREENING_REVIEW_MODES,
    FEASIBILITY_REQUEST_STATUSES,
    PROVIDER_STATUSES,
    TERAC_COUNTRY_FILTER_IDS,
    StudyReadError,
    is_valid_https_url,
    normalize_feasibility_request,
    normalize_opportunity_definition,
    validate_provider_id,
)
from runtime.runner import CheckpointIncompatibleError, RunState

WORKFLOW = "oracle_study_creation_v1"
_REQUEST_FIELDS = {
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
    "quote",
    "maximum_total_cost_cents",
}
_FEASIBILITY_FIELDS = {
    "task_description",
    "panel_description",
    "submission_count",
    "timeline_hours",
    "pay_rate_frequency",
    "loi_minutes",
}
_PHASES = {
    "study_prepared",
    "requesting_feasibility",
    "pricing_pending",
    "quote_ready",
    "creating_draft",
    "draft_needs_readback",
    "refreshing_draft",
    "awaiting_launch",
    "launching",
    "launched",
    "unknown_write_outcome",
    "launch_outcome_unknown",
    "write_rejected",
    "launch_blocked",
    "pricing_stopped",
}
_CHECKPOINT_FIELDS = {
    "workflow",
    "scope_sha256",
    "request",
    "feasibility_request_id",
    "feasibility",
    "draft",
    "write_intent",
    "last_error",
}
_LAUNCH_CONFIRMED_STATUSES = {"pre_recruiting", "active"}
_QUOTE_BOUND_PHASES = {
    "quote_ready",
    "creating_draft",
    "draft_needs_readback",
    "refreshing_draft",
    "awaiting_launch",
    "launching",
    "launched",
    "launch_blocked",
    "launch_outcome_unknown",
}


def _copy_json_object(value: object, message: str) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(message)
    try:
        copied = json.loads(json.dumps(dict(value), allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        raise ValueError(message) from None
    if not isinstance(copied, dict):
        raise ValueError(message)
    return copied


def _scope_digest(brief: str, request: dict) -> str:
    content = json.dumps(
        {"brief": brief, "study_request": request},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _has_explicit_country_filter(filters: list) -> bool:
    found_country = False
    for item in filters:
        if "multi_select--country" not in item:
            continue
        if found_country:
            return False
        found_country = True
        country = item["multi_select--country"]
        if not isinstance(country, Mapping) or len(country) != 1:
            return False
        if "$eq" in country:
            value = country["$eq"]
            valid = (
                isinstance(value, str) and bool(value.strip()) and value in TERAC_COUNTRY_FILTER_IDS
            )
        elif "$in" in country:
            values = country["$in"]
            valid = (
                isinstance(values, list)
                and bool(values)
                and all(
                    isinstance(value, str) and value.strip() and value in TERAC_COUNTRY_FILTER_IDS
                    for value in values
                )
            )
        else:
            valid = False
        if not valid:
            return False
    return found_country


def normalize_study_request(brief: str, value: object) -> dict:
    """Validate the explicit Oracle and provider scope before a checkpoint is saved."""
    if not isinstance(brief, str) or not brief.strip():
        raise ValueError("A nonempty Oracle brief is required")
    request = _copy_json_object(value, "invalid Oracle study request")
    if set(request) - _REQUEST_FIELDS:
        raise ValueError("invalid Oracle study request")
    if "quote" not in request or "maximum_total_cost_cents" not in request:
        raise ValueError("Oracle study request needs quote scope and a cost cap")
    cap = request["maximum_total_cost_cents"]
    if type(cap) is not int or cap <= 0:
        raise ValueError("invalid Oracle study cost cap")

    quote = _copy_json_object(request["quote"], "invalid Oracle quote scope")
    if set(quote) - _FEASIBILITY_FIELDS:
        raise ValueError("invalid Oracle quote scope")
    definition = {key: request[key] for key in _REQUEST_FIELDS if key in request and key != "quote"}
    definition.pop("maximum_total_cost_cents")
    normalized_definition = normalize_opportunity_definition(definition)
    if any(
        task["task_type"] == "file_upload" and task["review_type"] != "manual_review"
        for task in normalized_definition["tasks"]
    ):
        raise ValueError("Oracle file uploads require manual_review")
    if normalized_definition["num_participants"] < 1:
        raise ValueError("invalid Oracle study participant count")
    if not normalized_definition["filters"]:
        raise ValueError("Oracle study requires explicit audience filters")
    if not _has_explicit_country_filter(normalized_definition["filters"]):
        raise ValueError("Oracle study requires an explicit country filter")

    if "feasibility_request_id" in normalized_definition:
        validate_provider_id(normalized_definition["feasibility_request_id"])

    if "submission_count" in quote:
        if (
            type(quote["submission_count"]) is not int
            or quote["submission_count"] != normalized_definition["num_participants"]
        ):
            raise ValueError("quote participant count must match the opportunity")
    feasibility_request = {
        "taskDescription": quote.get("task_description"),
        "panelDescription": quote.get("panel_description"),
        "submissionCount": normalized_definition["num_participants"],
    }
    for source, target in (
        ("timeline_hours", "timelineHours"),
        ("pay_rate_frequency", "payRateFrequency"),
        ("loi_minutes", "loiMinutes"),
    ):
        if source in quote:
            feasibility_request[target] = quote[source]
    try:
        checked_quote = normalize_feasibility_request(feasibility_request)
    except (TypeError, ValueError):
        raise ValueError("invalid Oracle quote scope") from None
    normalized_quote = {
        "task_description": checked_quote["taskDescription"],
        "panel_description": checked_quote["panelDescription"],
        "submission_count": checked_quote["submissionCount"],
        **{
            source: checked_quote[target]
            for source, target in (
                ("timeline_hours", "timelineHours"),
                ("pay_rate_frequency", "payRateFrequency"),
                ("loi_minutes", "loiMinutes"),
            )
            if target in checked_quote
        },
    }
    # Keep the quote count explicit in the caller shape while using the one checked count.
    normalized_request = {
        **normalized_definition,
        "quote": normalized_quote,
        "maximum_total_cost_cents": cap,
    }
    if "feasibility_request_id" in request:
        normalized_request["feasibility_request_id"] = request["feasibility_request_id"]
    try:
        return json.loads(json.dumps(normalized_request, allow_nan=False))
    except (TypeError, ValueError, RecursionError):
        raise ValueError("invalid Oracle study request") from None


def feasibility_payload(request: dict) -> dict:
    quote = request["quote"]
    payload = {
        "taskDescription": quote["task_description"],
        "panelDescription": quote["panel_description"],
        "submissionCount": request["num_participants"],
    }
    for source, target in (
        ("timeline_hours", "timelineHours"),
        ("pay_rate_frequency", "payRateFrequency"),
        ("loi_minutes", "loiMinutes"),
    ):
        if source in quote:
            payload[target] = quote[source]
    return normalize_feasibility_request(payload)


def opportunity_payload(request: dict, feasibility_request_id: str) -> dict:
    payload = {
        key: request[key]
        for key in _REQUEST_FIELDS - {"quote", "maximum_total_cost_cents", "feasibility_request_id"}
        if key in request
    }
    payload["feasibility_request_id"] = validate_provider_id(feasibility_request_id)
    return normalize_opportunity_definition(payload)


def create_study_checkpoint(brief: str, request: object) -> dict:
    normalized = normalize_study_request(brief, request)
    return {
        "workflow": WORKFLOW,
        "scope_sha256": _scope_digest(brief, normalized),
        "request": normalized,
        "feasibility_request_id": normalized.get("feasibility_request_id"),
        "feasibility": None,
        "draft": None,
        "write_intent": None,
        "last_error": None,
    }


def is_study_creation_payload(payload: dict) -> bool:
    return payload.get("workflow") == WORKFLOW


def validate_study_checkpoint(state: RunState) -> None:
    payload = state.adapter_payload
    if (
        state.mode != "live"
        or not is_study_creation_payload(payload)
        or set(payload) != _CHECKPOINT_FIELDS
        or state.phase not in _PHASES
        or state.status not in {"working", "failed"}
        or state.question is not None
    ):
        raise CheckpointIncompatibleError()
    try:
        request = normalize_study_request(state.recipe_payload["brief"], payload["request"])
        requested_id = request.get("feasibility_request_id")
        attached_id = payload["feasibility_request_id"]
        if (
            not _same_json(payload["request"], request)
            or payload["scope_sha256"] != _scope_digest(state.recipe_payload["brief"], request)
            or (requested_id is not None and attached_id != requested_id)
            or (attached_id is not None and not isinstance(attached_id, str))
            or (payload["feasibility"] is not None and not isinstance(payload["feasibility"], dict))
            or (payload["draft"] is not None and not isinstance(payload["draft"], dict))
            or not _valid_feasibility_record(payload["feasibility"], attached_id)
            or (
                _phase_requires_confirmed_quote(state, payload)
                and not _has_confirmed_quote(request, payload["feasibility"])
            )
            or not _valid_draft_record(payload["draft"], attached_id)
            or not _valid_write_intent(payload["write_intent"])
            or (
                payload["last_error"] is not None
                and (
                    not isinstance(payload["last_error"], str)
                    or not payload["last_error"].replace("_", "").isalnum()
                    or len(payload["last_error"]) > 64
                )
            )
            or (state.status == "failed") != (state.error is not None)
            or not _phase_matches_checkpoint(state, payload)
        ):
            raise ValueError("invalid study checkpoint")
    except (KeyError, TypeError, ValueError, RecursionError):
        raise CheckpointIncompatibleError() from None


def _valid_feasibility_record(value: object, request_id: str | None) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    return (
        set(value)
        == {
            "id",
            "status",
            "task_description",
            "panel_description",
            "submission_count",
            "timeline_hours",
            "pay_rate_frequency",
            "loi_minutes",
            "cost_per_participant",
            "dashboard_url",
        }
        and value["id"] == request_id
        and value["status"] in FEASIBILITY_REQUEST_STATUSES
        and isinstance(value["task_description"], str)
        and isinstance(value["panel_description"], str)
        and type(value["submission_count"]) is int
        and value["submission_count"] > 0
        and (value["timeline_hours"] is None or type(value["timeline_hours"]) is int)
        and (value["pay_rate_frequency"] is None or isinstance(value["pay_rate_frequency"], str))
        and (value["loi_minutes"] is None or type(value["loi_minutes"]) is int)
        and (
            value["cost_per_participant"] is None or isinstance(value["cost_per_participant"], str)
        )
        and (value["dashboard_url"] is None or is_valid_https_url(value["dashboard_url"]))
    )


def _has_confirmed_quote(request: dict, value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("status") == "RESPONDED"
        and isinstance(value.get("cost_per_participant"), str)
        and bool(value["cost_per_participant"].strip())
        and is_valid_https_url(value.get("dashboard_url"))
        and _matches_feasibility(request, value)
    )


def _phase_requires_confirmed_quote(state: RunState, payload: dict) -> bool:
    if state.phase in _QUOTE_BOUND_PHASES:
        return True
    intent = payload["write_intent"]
    operation = intent.get("operation") if isinstance(intent, dict) else None
    return state.phase in {"unknown_write_outcome", "write_rejected"} and operation in {
        "create_opportunity",
        "launch_opportunity",
    }


def _valid_draft_record(value: object, feasibility_id: str | None) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict):
        return False
    price = value.get("pricing")
    screening_review = value.get("customer_screening_review")
    definition = {
        key: value.get(key)
        for key in (
            "title",
            "project_id",
            "num_participants",
            "business_type",
            "tasks",
            "filters",
            "device_types",
        )
    }
    for key in (
        "description",
        "customer_screening_review",
        "expected_days_to_complete",
        "feasibility_request_id",
    ):
        if value.get(key) is not None:
            definition[key] = value[key]
    try:
        normalized_definition = normalize_opportunity_definition(definition)
    except (TypeError, ValueError, RecursionError):
        return False
    return (
        set(value)
        == {
            "id",
            "title",
            "description",
            "status",
            "project_id",
            "num_participants",
            "business_type",
            "customer_screening_review",
            "device_types",
            "expected_days_to_complete",
            "filters",
            "tasks",
            "feasibility_request_id",
            "pricing",
            "draft_editor_url",
        }
        and isinstance(value["id"], str)
        and isinstance(value["title"], str)
        and (value["description"] is None or isinstance(value["description"], str))
        and value["status"] in PROVIDER_STATUSES
        and isinstance(value["project_id"], str)
        and type(value["num_participants"]) is int
        and value["num_participants"] > 0
        and isinstance(value["business_type"], str)
        and (screening_review is None or _valid_screening_review(screening_review))
        and isinstance(value["device_types"], list)
        and (
            value["expected_days_to_complete"] is None
            or type(value["expected_days_to_complete"]) is int
        )
        and isinstance(value["filters"], list)
        and isinstance(value["tasks"], list)
        and _same_json(definition, normalized_definition)
        and value["feasibility_request_id"] == feasibility_id
        and isinstance(price, dict)
        and set(price) == {"cost_per_participant_cents", "total_cost_cents", "currency"}
        and (
            price["cost_per_participant_cents"] is None
            or (
                type(price["cost_per_participant_cents"]) is int
                and price["cost_per_participant_cents"] >= 0
            )
        )
        and (
            price["total_cost_cents"] is None
            or (type(price["total_cost_cents"]) is int and price["total_cost_cents"] >= 0)
        )
        and (price["currency"] is None or isinstance(price["currency"], str))
        and (value["draft_editor_url"] is None or is_valid_https_url(value["draft_editor_url"]))
    )


def _valid_screening_review(value: object) -> bool:
    return isinstance(value, str) and value in CUSTOMER_SCREENING_REVIEW_MODES


def _valid_write_intent(value: object) -> bool:
    if value is None:
        return True
    return (
        isinstance(value, dict)
        and set(value) == {"operation", "outcome"}
        and value["operation"]
        in {"request_feasibility", "create_opportunity", "launch_opportunity"}
        and value["outcome"] in {"pending", "succeeded", "not_sent", "rejected", "unknown"}
    )


def _phase_matches_checkpoint(state: RunState, payload: dict) -> bool:
    intent = payload["write_intent"]
    operation = intent.get("operation") if isinstance(intent, dict) else None
    outcome = intent.get("outcome") if isinstance(intent, dict) else None
    if state.status == "failed":
        return (
            state.phase == "write_rejected"
            and state.error == "provider_rejected"
            and outcome == "rejected"
        )
    if state.phase == "write_rejected":
        return False
    if state.phase == "requesting_feasibility":
        return operation == "request_feasibility" and outcome == "pending"
    if state.phase == "creating_draft":
        return operation == "create_opportunity" and outcome == "pending"
    if state.phase == "launching":
        return (
            operation == "launch_opportunity"
            and outcome == "pending"
            and isinstance(payload["draft"], dict)
            and payload["draft"]["status"] == "draft"
        )
    if state.phase == "unknown_write_outcome":
        return outcome == "unknown" and operation != "launch_opportunity"
    if state.phase == "launch_outcome_unknown":
        return (
            operation == "launch_opportunity"
            and outcome in {"pending", "unknown"}
            and isinstance(payload["draft"], dict)
        )
    if state.phase == "launched":
        return (
            operation in {"create_opportunity", "launch_opportunity"}
            and outcome == "succeeded"
            and isinstance(payload["draft"], dict)
            and payload["draft"]["status"] != "draft"
        )
    if state.phase in {"pricing_pending", "quote_ready", "pricing_stopped"}:
        if payload["feasibility"] is None:
            return state.phase == "pricing_stopped" and payload["last_error"] is not None
        if state.phase == "quote_ready":
            return _has_confirmed_quote(payload["request"], payload["feasibility"])
        if state.phase == "pricing_pending":
            return payload["feasibility"]["status"] == "RECEIVED"
        return payload["feasibility"]["status"] in {"WON", "LOST", "NOT_PURSUED"} or (
            payload["last_error"] is not None
        )
    if state.phase in {
        "draft_needs_readback",
        "refreshing_draft",
        "awaiting_launch",
        "launch_blocked",
    }:
        return payload["draft"] is not None and (
            state.phase != "awaiting_launch"
            or is_valid_https_url(payload["draft"]["draft_editor_url"])
        )
    return True


def study_observation(state: RunState) -> dict:
    validate_study_checkpoint(state)
    payload = state.adapter_payload
    observation = {
        "workflow": "oracle_study_creation",
        "phase": state.phase,
        "scope_sha256": payload["scope_sha256"],
        "request": payload["request"],
        "maximum_total_cost_cents": payload["request"]["maximum_total_cost_cents"],
        "feasibility_request_id": payload["feasibility_request_id"],
        "feasibility": payload["feasibility"],
        "draft": payload["draft"],
        "write_intent": payload["write_intent"],
        "last_error": payload["last_error"],
    }
    if (
        state.phase == "awaiting_launch"
        and payload["draft"] is not None
        and _valid_screening_review(payload["draft"]["customer_screening_review"])
    ):
        observation["confirmation_phrase"] = launch_confirmation_phrase(
            state.run_id,
            payload["scope_sha256"],
            payload["feasibility"]["id"],
            payload["feasibility"]["cost_per_participant"],
            payload["draft"]["id"],
            payload["draft"]["draft_editor_url"],
            payload["draft"]["pricing"]["total_cost_cents"],
            payload["request"]["maximum_total_cost_cents"],
            payload["draft"]["customer_screening_review"],
        )
    return observation


def launch_confirmation_phrase(
    run_id: str,
    scope_sha256: str,
    feasibility_id: str,
    feasibility_quote: str,
    draft_id: str,
    draft_editor_url: str,
    total_cost_cents: int,
    maximum_total_cost_cents: int,
    screening_review: str,
) -> str:
    quote_digest = hashlib.sha256(feasibility_quote.encode("utf-8")).hexdigest()
    return (
        f"LAUNCH {run_id} {scope_sha256} FEASIBILITY {feasibility_id} "
        f"CPI_SHA256 {quote_digest} DRAFT {draft_id} "
        f"{hashlib.sha256(draft_editor_url.encode('utf-8')).hexdigest()} "
        f"SCREENING {screening_review} "
        f"USD {total_cost_cents} CENTS "
        f"CAP {maximum_total_cost_cents} CENTS"
    )


class StudyCheckpointAdapter:
    """Validate study state without observing or advancing provider state."""

    def observe(self, _state: RunState):
        return None

    def validate_progress(self, state: RunState) -> None:
        validate_study_checkpoint(state)


def _feasibility_dict(observation) -> dict:
    from adapters.terac_studies import FeasibilityObservation

    if not isinstance(observation, FeasibilityObservation):
        raise ValueError("invalid feasibility observation")
    return {
        "id": observation.request_id,
        "status": observation.status,
        "task_description": observation.task_description,
        "panel_description": observation.panel_description,
        "submission_count": observation.submission_count,
        "timeline_hours": observation.timeline_hours,
        "pay_rate_frequency": observation.pay_rate_frequency,
        "loi_minutes": observation.loi_minutes,
        "cost_per_participant": observation.cost_per_participant,
        "dashboard_url": (
            observation.dashboard_url if is_valid_https_url(observation.dashboard_url) else None
        ),
    }


def _opportunity_dict(observation) -> dict:
    from adapters.terac_studies import OpportunityObservation

    if not isinstance(observation, OpportunityObservation):
        raise ValueError("invalid opportunity observation")
    return {
        "id": observation.opportunity_id,
        "title": observation.title,
        "description": observation.description,
        "status": observation.status,
        "project_id": observation.project_id,
        "num_participants": observation.num_participants,
        "business_type": observation.business_type,
        "customer_screening_review": observation.customer_screening_review,
        "device_types": list(observation.device_types),
        "expected_days_to_complete": observation.expected_days_to_complete,
        "filters": list(observation.filters),
        "tasks": list(observation.tasks),
        "feasibility_request_id": observation.feasibility_request_id,
        "pricing": {
            "cost_per_participant_cents": observation.pricing.cost_per_participant_cents,
            "total_cost_cents": observation.pricing.total_cost_cents,
            "currency": observation.pricing.currency,
        },
        "draft_editor_url": (
            observation.draft_editor_url
            if is_valid_https_url(observation.draft_editor_url)
            else None
        ),
    }


def _draft_record(
    request: dict,
    feasibility_id: str,
    observed: dict,
    *,
    accept_provider_screening: bool = True,
) -> dict:
    """Keep caller-owned definition fields and accept only provider lifecycle facts."""
    definition = opportunity_payload(request, feasibility_id)
    return {
        "id": observed["id"],
        "title": definition["title"],
        "description": definition.get("description"),
        "status": observed["status"],
        "project_id": definition["project_id"],
        "num_participants": definition["num_participants"],
        "business_type": definition["business_type"],
        "customer_screening_review": (
            observed["customer_screening_review"]
            if accept_provider_screening
            and _valid_screening_review(observed["customer_screening_review"])
            else definition.get("customer_screening_review")
        ),
        "device_types": list(definition["device_types"]),
        "expected_days_to_complete": definition.get("expected_days_to_complete"),
        "filters": definition["filters"],
        "tasks": definition["tasks"],
        "feasibility_request_id": feasibility_id,
        "pricing": observed["pricing"],
        "draft_editor_url": observed["draft_editor_url"],
    }


def _same_json(left: object, right: object) -> bool:
    try:
        return json.dumps(
            left, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ) == json.dumps(right, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return False


def _matches_feasibility(request: dict, observed: dict) -> bool:
    quote = request["quote"]
    if (
        observed["task_description"] != quote["task_description"]
        or observed["panel_description"] != quote["panel_description"]
        or observed["submission_count"] != request["num_participants"]
    ):
        return False
    for field in ("timeline_hours", "pay_rate_frequency", "loi_minutes"):
        if field in quote and observed[field] != quote[field]:
            return False
    return True


def _matches_opportunity(
    request: dict,
    feasibility_id: str,
    observed: dict,
    *,
    expected_screening_review: str | None = None,
) -> bool:
    expected = opportunity_payload(request, feasibility_id)
    if "customer_screening_review" not in expected and expected_screening_review is not None:
        expected["customer_screening_review"] = expected_screening_review
    return all(_same_json(observed.get(field), expected.get(field)) for field in expected)


def _new_state(state: RunState, *, phase: str, payload: dict, status=None, error=None) -> RunState:
    return state.model_copy(
        update={
            "phase": phase,
            "adapter_payload": payload,
            "status": status or state.status,
            "error": error,
            "question": None,
        }
    )


def _save_state(state: RunState, payload: dict, phase: str, save, *, last_error=None):
    payload["last_error"] = last_error
    candidate = _new_state(state, phase=phase, payload=payload)
    save(candidate)
    return candidate


def _write_state(state: RunState, payload: dict, operation: str, phase: str, save) -> RunState:
    payload["write_intent"] = {"operation": operation, "outcome": "pending"}
    candidate = _new_state(state, phase=phase, payload=payload)
    save(candidate)
    return candidate


def _complete_write(state: RunState, payload: dict, phase: str, save, *, last_error=None):
    intent = payload["write_intent"]
    payload["write_intent"] = {"operation": intent["operation"], "outcome": "succeeded"}
    return _save_state(state, payload, phase, save, last_error=last_error)


def _handle_write_error(state: RunState, payload: dict, error, previous_phase: str, save):
    from adapters.terac_study_contract import StudyWriteError

    if isinstance(error, StudyWriteError):
        code = error.code
        outcome = error.outcome
    else:
        code = "unknown_write_outcome"
        outcome = "unknown"
    operation = payload["write_intent"]["operation"]
    payload["write_intent"] = {"operation": operation, "outcome": outcome}
    if outcome == "not_sent":
        return _save_state(state, payload, previous_phase, save, last_error=code)
    if outcome == "rejected":
        payload["last_error"] = code
        candidate = _new_state(
            state,
            phase="write_rejected",
            payload=payload,
            status="failed",
            error="provider_rejected",
        )
        save(candidate)
        return candidate
    phase = (
        "launch_outcome_unknown" if operation == "launch_opportunity" else "unknown_write_outcome"
    )
    return _save_state(state, payload, phase, save, last_error=code)


def _read_feasibility(state: RunState, payload: dict, studies, save, *, create=False):
    request = payload["request"]
    if create:
        state = _write_state(state, payload, "request_feasibility", "requesting_feasibility", save)
        try:
            observation = studies.request_feasibility(feasibility_payload(request))
        except Exception as error:
            return _handle_write_error(
                state, dict(state.adapter_payload), error, "study_prepared", save
            )
    else:
        observation = studies.get_feasibility(payload["feasibility_request_id"])
    observed = _feasibility_dict(observation)
    if not create and observed["id"] != payload["feasibility_request_id"]:
        return _save_state(
            state,
            dict(state.adapter_payload),
            "pricing_stopped",
            save,
            last_error="feasibility_identity_mismatch",
        )
    payload = dict(state.adapter_payload)
    payload["feasibility_request_id"] = observed["id"]
    payload["feasibility"] = observed
    matching = _matches_feasibility(request, observed)
    if not matching:
        phase = "pricing_stopped"
        last_error = "feasibility_scope_mismatch"
    elif observed["status"] == "RECEIVED":
        phase = "pricing_pending"
        last_error = None
    elif (
        observed["status"] == "RESPONDED"
        and isinstance(observed["cost_per_participant"], str)
        and observed["cost_per_participant"].strip()
    ):
        if is_valid_https_url(observed["dashboard_url"]):
            phase = "quote_ready"
            last_error = None
        else:
            phase = "pricing_stopped"
            last_error = "feasibility_review_unavailable"
    else:
        phase = "pricing_stopped"
        last_error = "feasibility_not_priceable"
    if create:
        return _complete_write(state, payload, phase, save, last_error=last_error)
    return _save_state(state, payload, phase, save, last_error=last_error)


def _read_opportunity(state: RunState, payload: dict, studies, save):
    payload = dict(payload)
    draft = payload["draft"]
    request = payload["request"]
    prior_screening_review = draft["customer_screening_review"]
    if (
        "customer_screening_review" not in request
        and not _valid_screening_review(prior_screening_review)
        and state.phase not in {"draft_needs_readback", "launched"}
    ):
        return _save_state(
            state, payload, "launch_blocked", save, last_error="draft_review_unavailable"
        )
    prior_price = draft["pricing"]["total_cost_cents"]
    prior_currency = draft["pricing"]["currency"]
    prior_link = draft["draft_editor_url"]
    try:
        observation = studies.get_opportunity(draft["id"])
    except StudyReadError as error:
        if error.code != "invalid_response":
            raise
        return _save_state(
            state, payload, "launch_blocked", save, last_error="draft_review_unavailable"
        )
    observed = _opportunity_dict(observation)
    if observed["id"] != draft["id"]:
        return _save_state(
            state,
            dict(state.adapter_payload),
            "launch_blocked",
            save,
            last_error="draft_identity_mismatch",
        )
    if not _matches_opportunity(
        request,
        payload["feasibility_request_id"],
        observed,
        expected_screening_review=(
            prior_screening_review if _valid_screening_review(prior_screening_review) else None
        ),
    ):
        return _save_state(
            state, payload, "launch_blocked", save, last_error="draft_scope_mismatch"
        )
    if not _valid_screening_review(observed["customer_screening_review"]):
        return _save_state(
            state, payload, "launch_blocked", save, last_error="draft_review_unavailable"
        )
    payload["draft"] = _draft_record(request, payload["feasibility_request_id"], observed)
    if state.phase == "launched" and observed["status"] == "draft":
        phase = "launch_blocked"
        last_error = "launch_status_unconfirmed"
    elif state.phase == "launched":
        phase = "launched"
        last_error = None
    elif observed["status"] in _LAUNCH_CONFIRMED_STATUSES:
        phase = "launched"
        last_error = None
    elif observed["status"] != "draft":
        phase = "launch_blocked"
        last_error = "draft_not_launchable"
    elif (
        observed["pricing"]["currency"] != "usd"
        or observed["pricing"]["total_cost_cents"] is None
        or observed["draft_editor_url"] is None
    ):
        phase = "launch_blocked"
        last_error = "draft_review_unavailable"
    elif observed["pricing"]["total_cost_cents"] > request["maximum_total_cost_cents"]:
        phase = "launch_blocked"
        last_error = "price_over_cap"
    elif (
        observed["pricing"]["total_cost_cents"] != prior_price
        or observed["pricing"]["currency"] != prior_currency
    ):
        phase = "awaiting_launch"
        last_error = "price_changed_review_required"
    elif observed["draft_editor_url"] != prior_link:
        phase = "awaiting_launch"
        last_error = "draft_url_changed_review_required"
    else:
        phase = "awaiting_launch"
        last_error = None
    return _save_state(state, payload, phase, save, last_error=last_error)


def _reconcile_interrupted_launch(state: RunState, payload: dict, studies, save):
    payload = dict(payload)
    draft = payload["draft"]
    if not isinstance(draft, dict) or not isinstance(draft.get("id"), str):
        return _save_state(
            state, payload, "unknown_write_outcome", save, last_error="launch_identity_missing"
        )
    if "customer_screening_review" not in payload["request"] and not _valid_screening_review(
        draft.get("customer_screening_review")
    ):
        return _save_state(
            state,
            payload,
            "launch_outcome_unknown",
            save,
            last_error="draft_review_unavailable",
        )
    try:
        observation = studies.get_opportunity(draft["id"])
    except StudyReadError as error:
        if error.code != "invalid_response":
            raise
        return _save_state(
            state,
            payload,
            "launch_outcome_unknown",
            save,
            last_error="draft_review_unavailable",
        )
    observed = _opportunity_dict(observation)
    if observed["id"] != draft["id"]:
        return _save_state(
            state, payload, "launch_outcome_unknown", save, last_error="draft_identity_mismatch"
        )
    if not _matches_opportunity(
        payload["request"],
        payload["feasibility_request_id"],
        observed,
        expected_screening_review=draft.get("customer_screening_review"),
    ):
        return _save_state(
            state, payload, "launch_outcome_unknown", save, last_error="scope_mismatch"
        )
    payload["draft"] = _draft_record(
        payload["request"], payload["feasibility_request_id"], observed
    )
    if observed["status"] not in _LAUNCH_CONFIRMED_STATUSES:
        error = (
            "launch_not_reconciled"
            if observed["status"] == "draft"
            else "launch_status_unconfirmed"
        )
        return _save_state(state, payload, "launch_outcome_unknown", save, last_error=error)
    payload["write_intent"] = {"operation": "launch_opportunity", "outcome": "succeeded"}
    return _save_state(state, payload, "launched", save, last_error=None)


def launch_study(
    state: RunState,
    studies,
    save,
    *,
    expected_total_cost_cents: int,
    maximum_total_cost_cents: int,
    scope_sha256: str,
    confirmation: str,
) -> RunState:
    """Preflight the current draft, then submit one separately confirmed launch."""
    validate_study_checkpoint(state)
    if state.phase != "awaiting_launch" or state.status != "working":
        raise ValueError("Oracle run has no launchable Terac draft")
    payload = json.loads(json.dumps(state.adapter_payload, allow_nan=False))
    request = payload["request"]
    draft = payload["draft"]
    if (
        type(expected_total_cost_cents) is not int
        or type(maximum_total_cost_cents) is not int
        or maximum_total_cost_cents != request["maximum_total_cost_cents"]
        or expected_total_cost_cents != draft["pricing"]["total_cost_cents"]
        or expected_total_cost_cents > maximum_total_cost_cents
        or scope_sha256 != payload["scope_sha256"]
        or not _valid_screening_review(draft["customer_screening_review"])
        or confirmation
        != launch_confirmation_phrase(
            state.run_id,
            payload["scope_sha256"],
            payload["feasibility"]["id"],
            payload["feasibility"]["cost_per_participant"],
            draft["id"],
            draft["draft_editor_url"],
            draft["pricing"]["total_cost_cents"],
            request["maximum_total_cost_cents"],
            draft["customer_screening_review"],
        )
        or draft["status"] != "draft"
        or draft["pricing"]["currency"] != "usd"
        or not draft["draft_editor_url"]
    ):
        raise ValueError("Oracle launch confirmation does not match the reviewed draft")

    try:
        observed = _opportunity_dict(studies.get_opportunity(draft["id"]))
    except StudyReadError as error:
        if error.code != "invalid_response":
            raise
        return _save_state(
            state, payload, "launch_blocked", save, last_error="draft_review_unavailable"
        )
    if observed["id"] != draft["id"]:
        return _save_state(
            state,
            payload,
            "launch_blocked",
            save,
            last_error="draft_identity_mismatch",
        )
    if not _matches_opportunity(
        request,
        payload["feasibility_request_id"],
        observed,
        expected_screening_review=draft["customer_screening_review"],
    ):
        return _save_state(
            state, payload, "launch_blocked", save, last_error="draft_scope_mismatch"
        )
    if observed["status"] in _LAUNCH_CONFIRMED_STATUSES:
        payload["draft"] = _draft_record(request, payload["feasibility_request_id"], observed)
        return _save_state(state, payload, "launched", save, last_error="launch_seen_before_action")
    if observed["status"] != "draft":
        payload["draft"] = _draft_record(request, payload["feasibility_request_id"], observed)
        return _save_state(
            state, payload, "launch_blocked", save, last_error="draft_not_launchable"
        )
    current_price = observed["pricing"]["total_cost_cents"]
    if (
        observed["id"] != draft["id"]
        or observed["pricing"]["currency"] != "usd"
        or type(current_price) is not int
    ):
        payload["draft"] = _draft_record(request, payload["feasibility_request_id"], observed)
        return _save_state(
            state, payload, "launch_blocked", save, last_error="draft_review_unavailable"
        )
    if current_price != expected_total_cost_cents:
        payload["draft"] = _draft_record(request, payload["feasibility_request_id"], observed)
        if current_price > maximum_total_cost_cents:
            return _save_state(state, payload, "launch_blocked", save, last_error="price_over_cap")
        return _save_state(
            state,
            payload,
            "awaiting_launch",
            save,
            last_error="price_changed_review_required",
        )
    if observed["draft_editor_url"] != draft["draft_editor_url"]:
        payload["draft"] = _draft_record(request, payload["feasibility_request_id"], observed)
        return _save_state(
            state,
            payload,
            "launch_blocked",
            save,
            last_error="draft_url_changed_review_required",
        )

    payload["draft"] = _draft_record(request, payload["feasibility_request_id"], observed)
    state = _write_state(state, payload, "launch_opportunity", "launching", save)
    try:
        response = studies.launch_opportunity(observed["id"])
    except Exception as error:
        return _handle_write_error(
            state, dict(state.adapter_payload), error, "awaiting_launch", save
        )
    result = _opportunity_dict(response)
    payload = dict(state.adapter_payload)
    response_identity_matches = result["id"] == observed["id"]
    same_result = (
        response_identity_matches
        and _matches_opportunity(
            request,
            payload["feasibility_request_id"],
            result,
            expected_screening_review=draft["customer_screening_review"],
        )
        and result["id"] == observed["id"]
        and result["draft_editor_url"] == observed["draft_editor_url"]
        and result["pricing"] == observed["pricing"]
        and result["status"] in _LAUNCH_CONFIRMED_STATUSES
    )
    if not same_result:
        payload["write_intent"] = {"operation": "launch_opportunity", "outcome": "unknown"}
        return _save_state(
            state, payload, "launch_outcome_unknown", save, last_error="launch_response_unconfirmed"
        )
    payload["draft"] = _draft_record(request, payload["feasibility_request_id"], result)
    payload["write_intent"] = {"operation": "launch_opportunity", "outcome": "succeeded"}
    return _save_state(state, payload, "launched", save, last_error=None)


def advance_study_step(state: RunState, studies, save) -> RunState:
    """Perform one explicit provider step, persisting any write intent first."""
    validate_study_checkpoint(state)
    payload = json.loads(json.dumps(state.adapter_payload, allow_nan=False))
    intent = payload["write_intent"]
    if isinstance(intent, dict) and intent["outcome"] == "pending":
        if intent["operation"] == "launch_opportunity":
            return _reconcile_interrupted_launch(state, payload, studies, save)
        payload["write_intent"] = {
            "operation": intent["operation"],
            "outcome": "unknown",
        }
        return _save_state(
            state, payload, "unknown_write_outcome", save, last_error="write_interrupted"
        )
    if state.status != "working":
        return state
    if state.phase == "study_prepared":
        if payload["feasibility_request_id"] is None:
            return _read_feasibility(state, payload, studies, save, create=True)
        return _read_feasibility(state, payload, studies, save)
    if state.phase == "pricing_pending":
        return _read_feasibility(state, payload, studies, save)
    if state.phase == "quote_ready":
        request = payload["request"]
        feasibility_id = payload["feasibility_request_id"]
        if not feasibility_id:
            return _save_state(
                state, payload, "pricing_stopped", save, last_error="feasibility_identity_missing"
            )
        previous_phase = "quote_ready"
        state = _write_state(state, payload, "create_opportunity", "creating_draft", save)
        try:
            observation = studies.create_opportunity(opportunity_payload(request, feasibility_id))
        except Exception as error:
            return _handle_write_error(
                state, dict(state.adapter_payload), error, previous_phase, save
            )
        observed = _opportunity_dict(observation)
        payload = dict(state.adapter_payload)
        matching = _matches_opportunity(request, feasibility_id, observed)
        payload["draft"] = _draft_record(
            request,
            feasibility_id,
            observed,
            accept_provider_screening=False,
        )
        if not matching:
            phase, last_error = "launch_blocked", "draft_scope_mismatch"
        elif observed["status"] != "draft":
            phase, last_error = "launch_blocked", "draft_not_reviewable"
        else:
            phase, last_error = "draft_needs_readback", None
        return _complete_write(state, payload, phase, save, last_error=last_error)
    if state.phase in {"draft_needs_readback", "awaiting_launch", "launched", "launch_blocked"}:
        return _read_opportunity(state, payload, studies, save)
    if state.phase == "launch_outcome_unknown":
        return _reconcile_interrupted_launch(state, payload, studies, save)
    return state
