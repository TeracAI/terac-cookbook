"""Bounded REST operations for Terac pricing, draft creation and launch."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from adapters.terac_study_contract import (
    CUSTOMER_SCREENING_REVIEW_MODES,
    DEVICE_TYPES,
    FEASIBILITY_REQUEST_STATUSES,
    PAY_RATE_FREQUENCIES,
    PROVIDER_STATUSES,
    StudyReadError,
    StudyWriteError,
    is_valid_https_url,
    normalize_audience_filters,
    normalize_feasibility_request,
    normalize_opportunity_definition,
    normalize_task,
    validate_provider_id,
)
from runtime.json_utils import unique_json_object

TERAC_API_BASE = "https://terac.com/api/external/v2"
REQUEST_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class FeasibilityObservation:
    request_id: str
    status: str
    task_description: str
    panel_description: str
    submission_count: int | None
    timeline_hours: int | None
    pay_rate_frequency: str | None
    loi_minutes: int | None
    cost_per_participant: str | None
    dashboard_url: str | None


@dataclass(frozen=True, slots=True)
class OpportunityPrice:
    cost_per_participant_cents: int | None
    total_cost_cents: int | None
    currency: str | None


@dataclass(frozen=True, slots=True)
class OpportunityObservation:
    opportunity_id: str
    title: str
    description: str | None
    status: str
    project_id: str
    num_participants: int
    business_type: str
    customer_screening_review: str | None
    device_types: tuple[str, ...]
    expected_days_to_complete: int | None
    filters: tuple[dict, ...]
    tasks: tuple[dict, ...]
    feasibility_request_id: str | None
    pricing: OpportunityPrice
    draft_editor_url: str | None


def _invalid_constant(_value: str):
    raise ValueError("Non-finite JSON value")


def _validate_token(token: object) -> str:
    if (
        not isinstance(token, str)
        or not token
        or token.strip() != token
        or any(not 33 <= ord(character) <= 126 for character in token)
    ):
        raise ValueError("credentials unavailable")
    return token


_validate_id = validate_provider_id


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid provider field")
    return value


def _optional_https_url(value: object) -> str | None:
    url = _optional_string(value)
    if url is not None and not is_valid_https_url(url):
        raise ValueError("invalid provider link")
    return url


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise ValueError("invalid provider field")
    return value


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("invalid provider field")
    return value


def _nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("invalid provider price")
    return value


def _feasibility_observation(value: object) -> FeasibilityObservation:
    if not isinstance(value, dict):
        raise ValueError("invalid feasibility response")
    status = value.get("status")
    if status not in FEASIBILITY_REQUEST_STATUSES:
        raise ValueError("invalid feasibility response")
    task = _required_string(value.get("taskDescription"))
    panel = _required_string(value.get("panelDescription"))
    cost = _optional_string(value.get("costPerParticipant"))
    url = _optional_https_url(value.get("dashboard_url"))
    pay_rate = _optional_string(value.get("payRateFrequency"))
    if pay_rate is not None and pay_rate not in PAY_RATE_FREQUENCIES:
        raise ValueError("invalid feasibility response")
    return FeasibilityObservation(
        request_id=_validate_id(value.get("id")),
        status=status,
        task_description=task,
        panel_description=panel,
        submission_count=_optional_positive_int(value.get("submissionCount")),
        timeline_hours=_optional_positive_int(value.get("timelineHours")),
        pay_rate_frequency=pay_rate,
        loi_minutes=_optional_positive_int(value.get("loiMinutes")),
        cost_per_participant=cost,
        dashboard_url=url,
    )


def _opportunity_observation(value: object) -> OpportunityObservation:
    if not isinstance(value, dict):
        raise ValueError("invalid opportunity response")
    status = value.get("status")
    if status not in PROVIDER_STATUSES:
        raise ValueError("invalid opportunity response")
    filters = value.get("filters")
    tasks = value.get("tasks")
    if not isinstance(filters, list) or not isinstance(tasks, list):
        raise ValueError("invalid opportunity response")
    filters = normalize_audience_filters(filters)
    tasks = [normalize_task(item) for item in tasks]
    if type(value.get("num_participants")) is not int or value["num_participants"] < 1:
        raise ValueError("invalid opportunity response")
    feasibility_id = _optional_string(value.get("feasibility_request_id"))
    if feasibility_id is not None:
        _validate_id(feasibility_id)
    pricing = value.get("pricing")
    if pricing is None:
        price = OpportunityPrice(None, None, None)
    elif isinstance(pricing, dict):
        currency = _optional_string(pricing.get("currency"))
        price = OpportunityPrice(
            cost_per_participant_cents=_nonnegative_int(pricing.get("cost_per_participant_cents")),
            total_cost_cents=_nonnegative_int(pricing.get("total_cost_cents")),
            currency=currency,
        )
    else:
        raise ValueError("invalid opportunity price")
    links = value.get("links")
    dashboard = links.get("dashboard") if isinstance(links, dict) else None
    draft_url = _optional_https_url(
        dashboard.get("draft_editor") if isinstance(dashboard, dict) else None
    )
    device_types = value.get("device_types")
    if not isinstance(device_types, list) or any(
        not isinstance(item, str) or item not in DEVICE_TYPES for item in device_types
    ):
        raise ValueError("invalid opportunity device types")
    customer_screening_review = value.get("customer_screening_review")
    if customer_screening_review is not None and (
        not isinstance(customer_screening_review, str)
        or customer_screening_review not in CUSTOMER_SCREENING_REVIEW_MODES
    ):
        raise ValueError("invalid opportunity screening review")
    return OpportunityObservation(
        opportunity_id=_validate_id(value.get("id")),
        title=_required_string(value.get("title")),
        description=_optional_string(value.get("description")),
        status=status,
        project_id=_required_string(value.get("project_id")),
        num_participants=value["num_participants"],
        business_type=_required_string(value.get("business_type")),
        customer_screening_review=customer_screening_review,
        device_types=tuple(device_types),
        expected_days_to_complete=_optional_positive_int(value.get("expected_days_to_complete")),
        filters=tuple(filters),
        tasks=tuple(tasks),
        feasibility_request_id=feasibility_id,
        pricing=price,
        draft_editor_url=draft_url,
    )


class TeracStudies:
    """Use fixed Terac REST routes for one bounded study operation at a time."""

    def __init__(
        self,
        credential_provider: Callable[[], str | None],
        *,
        transport_factory: Callable[[], httpx.BaseTransport] | None = None,
    ):
        if not callable(credential_provider) or (
            transport_factory is not None and not callable(transport_factory)
        ):
            raise ValueError("invalid Terac study adapter")
        self._credential_provider = credential_provider
        self._transport_factory = transport_factory

    def request_feasibility(self, request: Mapping) -> FeasibilityObservation:
        try:
            payload = normalize_feasibility_request(request)
        except (TypeError, ValueError):
            raise StudyWriteError("invalid_request", "not_sent") from None
        return self._parse_write(
            lambda: _feasibility_observation(
                self._request("POST", "/feasibility/requests", payload=payload, write=True)
            )
        )

    def get_feasibility(self, request_id: str) -> FeasibilityObservation:
        try:
            request_id = _validate_id(request_id)
        except ValueError:
            raise StudyReadError("invalid_binding") from None
        value = self._request(
            "GET", f"/feasibility/requests/{quote(request_id, safe='')}", write=False
        )
        try:
            return _feasibility_observation(value)
        except (TypeError, ValueError):
            raise StudyReadError("invalid_response") from None

    def create_opportunity(self, definition: Mapping) -> OpportunityObservation:
        try:
            payload = normalize_opportunity_definition(definition)
        except (TypeError, ValueError):
            raise StudyWriteError("invalid_request", "not_sent") from None
        return self._parse_write(
            lambda: _opportunity_observation(
                self._request("POST", "/opportunities", payload=payload, write=True)
            )
        )

    def get_opportunity(self, opportunity_id: str) -> OpportunityObservation:
        try:
            opportunity_id = _validate_id(opportunity_id)
        except ValueError:
            raise StudyReadError("invalid_binding") from None
        value = self._request(
            "GET", f"/opportunities/{quote(opportunity_id, safe='')}", write=False
        )
        try:
            return _opportunity_observation(value)
        except (TypeError, ValueError):
            raise StudyReadError("invalid_response") from None

    def launch_opportunity(self, opportunity_id: str) -> OpportunityObservation:
        try:
            opportunity_id = _validate_id(opportunity_id)
        except ValueError:
            raise StudyWriteError("invalid_request", "not_sent") from None
        return self._parse_write(
            lambda: _opportunity_observation(
                self._request(
                    "POST",
                    f"/opportunities/{quote(opportunity_id, safe='')}/launch",
                    write=True,
                )
            )
        )

    @staticmethod
    def _parse_write(parser):
        try:
            return parser()
        except StudyWriteError:
            raise
        except Exception:
            raise StudyWriteError("unknown_write_outcome", "unknown") from None

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping | None = None,
        write: bool,
    ) -> dict:
        try:
            token = _validate_token(self._credential_provider())
        except Exception:
            error = StudyWriteError if write else StudyReadError
            if write:
                raise error("credential_unavailable", "not_sent") from None
            raise error("credential_unavailable") from None

        try:
            with httpx.Client(
                transport=self._transport_factory() if self._transport_factory else None,
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                with client.stream(
                    method,
                    f"{TERAC_API_BASE}{path}",
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Authorization": f"Bearer {token}",
                    },
                    json=dict(payload) if payload is not None else None,
                ) as response:
                    status = response.status_code
                    if write and 400 <= status < 500:
                        raise StudyWriteError("provider_rejected", "rejected")
                    if status != 200:
                        if write:
                            raise StudyWriteError("unknown_write_outcome", "unknown")
                        raise StudyReadError("provider_error")
                    body = self._read_response(response)
        except (StudyReadError, StudyWriteError):
            raise
        except httpx.TimeoutException:
            if write:
                raise StudyWriteError("unknown_write_outcome", "unknown") from None
            raise StudyReadError("timeout") from None
        except httpx.TransportError:
            if write:
                raise StudyWriteError("unknown_write_outcome", "unknown") from None
            raise StudyReadError("transport_error") from None
        except httpx.HTTPError:
            if write:
                raise StudyWriteError("unknown_write_outcome", "unknown") from None
            raise StudyReadError("transport_error") from None
        except Exception:
            if write:
                raise StudyWriteError("unknown_write_outcome", "unknown") from None
            raise StudyReadError("transport_error") from None
        try:
            value = json.loads(
                body,
                object_pairs_hook=unique_json_object,
                parse_constant=_invalid_constant,
            )
            if not isinstance(value, dict):
                raise ValueError("invalid response")
            return value
        except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError):
            if write:
                raise StudyWriteError("unknown_write_outcome", "unknown") from None
            raise StudyReadError("invalid_response") from None

    @staticmethod
    def _read_response(response: httpx.Response) -> bytes:
        media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        content_encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
        content_length = response.headers.get("Content-Length")
        if media_type != "application/json" or content_encoding not in {"", "identity"}:
            raise ValueError("invalid response")
        if content_length is not None:
            if not content_length.isdigit() or int(content_length) > MAX_RESPONSE_BYTES:
                raise ValueError("invalid response size")
        chunks = []
        size = 0
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            chunks.append(chunk)
        return b"".join(chunks)


__all__ = [
    "MAX_RESPONSE_BYTES",
    "REQUEST_TIMEOUT_SECONDS",
    "TERAC_API_BASE",
    "FeasibilityObservation",
    "OpportunityObservation",
    "OpportunityPrice",
    "TeracStudies",
    "normalize_feasibility_request",
    "normalize_opportunity_definition",
]
