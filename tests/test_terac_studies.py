import json

import httpx
import pytest

from adapters.terac_studies import TeracStudies
from adapters.terac_study_contract import StudyReadError, StudyWriteError

FEASIBILITY_ID = "feasibility-123"
OPPORTUNITY_ID = "opportunity-123"
TOKEN = "private-test-token"


def feasibility_response(*, status="RECEIVED", cost=None):
    return {
        "id": FEASIBILITY_ID,
        "status": status,
        "source": "API",
        "taskDescription": "One 15-minute written GTM review, delivered as a file.",
        "panelDescription": "One English-speaking small-business SaaS operator worldwide.",
        "submissionCount": 1,
        "timelineHours": 120,
        "payRateFrequency": "ONE_TIME",
        "loiMinutes": 15,
        "costPerParticipant": cost,
        "respondedAt": None,
        "createdAt": "2026-10-23T13:00:00Z",
        "dashboard_url": "https://terac.com/org/feasibility",
    }


def opportunity_response(*, status="draft", total_cost_cents=5250):
    return {
        "id": OPPORTUNITY_ID,
        "title": "SaaS GTM review",
        "description": "A bounded fictional GTM review.",
        "status": status,
        "num_participants": 1,
        "filters": [
            {"multi_select--country": {"$in": ["US", "CA"]}},
            {"multi_select--language": {"$eq": "en-US"}},
        ],
        "tasks": [
            {
                "sequence": 1,
                "task_type": "file_upload",
                "review_type": "manual_review",
                "duration_minutes": 15,
                "instructions": "Upload a short report.",
                "accepted_mime_types": ["text/markdown"],
                "min_files": 1,
                "max_files": 1,
                "max_file_size_bytes": 2 * 1024 * 1024,
            }
        ],
        "business_type": "b2b",
        "customer_screening_review": "auto_invite",
        "device_types": ["desktop"],
        "feasibility_request_id": FEASIBILITY_ID,
        "project_id": "project-123",
        "pricing": {
            "cost_per_participant_cents": total_cost_cents,
            "total_cost_cents": total_cost_cents,
            "currency": "usd",
        },
        "links": {
            "dashboard": {
                "draft_editor": "https://terac.com/org/opportunities/opportunity-123/edit",
            }
        },
    }


def study_definition():
    return {
        "title": "SaaS GTM review",
        "project_id": "project-123",
        "num_participants": 1,
        "business_type": "b2b",
        "customer_screening_review": "auto_invite",
        "device_types": ["desktop"],
        "filters": [
            {"multi_select--country": {"$in": ["US", "CA"]}},
            {"multi_select--language": {"$eq": "en-US"}},
        ],
        "tasks": [
            {
                "sequence": 1,
                "task_type": "file_upload",
                "review_type": "manual_review",
                "duration_minutes": 15,
                "instructions": "Upload a short report.",
                "accepted_mime_types": ["text/markdown"],
                "min_files": 1,
                "max_files": 1,
                "max_file_size_bytes": 2 * 1024 * 1024,
            }
        ],
        "feasibility_request_id": FEASIBILITY_ID,
    }


def test_request_feasibility_uses_current_rest_fields_and_keeps_pending_quote_opaque():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=feasibility_response())

    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(handle),
    )
    result = provider.request_feasibility(
        {
            "taskDescription": "One 15-minute written GTM review, delivered as a file.",
            "panelDescription": "One English-speaking small-business SaaS operator worldwide.",
            "submissionCount": 1,
            "timelineHours": 120,
            "payRateFrequency": "ONE_TIME",
            "loiMinutes": 15,
        }
    )

    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert request.url == "https://terac.com/api/external/v2/feasibility/requests"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == {
        "taskDescription": "One 15-minute written GTM review, delivered as a file.",
        "panelDescription": "One English-speaking small-business SaaS operator worldwide.",
        "submissionCount": 1,
        "timelineHours": 120,
        "payRateFrequency": "ONE_TIME",
        "loiMinutes": 15,
    }
    assert result.request_id == FEASIBILITY_ID
    assert result.status == "RECEIVED"
    assert result.cost_per_participant is None
    assert result.dashboard_url == "https://terac.com/org/feasibility"


def test_read_feasibility_preserves_provider_price_string_and_uses_exact_id():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json=feasibility_response(status="RESPONDED", cost="52.50"))

    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(handle),
    )
    result = provider.get_feasibility(FEASIBILITY_ID)

    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert str(calls[0].url) == (
        "https://terac.com/api/external/v2/feasibility/requests/feasibility-123"
    )
    assert result.status == "RESPONDED"
    assert result.cost_per_participant == "52.50"


@pytest.mark.parametrize("operation", ["request_feasibility", "create_opportunity"])
def test_write_response_with_malformed_provider_link_is_an_unknown_outcome(operation):
    def handle(_request):
        if operation == "request_feasibility":
            body = feasibility_response(status="RESPONDED", cost="52.50")
            body["dashboard_url"] = "https://exa mple.com/quote"
        else:
            body = opportunity_response()
            body["links"]["dashboard"]["draft_editor"] = "https://terac.com:99999/draft"
        return httpx.Response(200, json=body)

    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(handle),
    )

    with pytest.raises(StudyWriteError) as error:
        if operation == "request_feasibility":
            provider.request_feasibility(
                {
                    "taskDescription": "task",
                    "panelDescription": "audience",
                    "submissionCount": 1,
                }
            )
        else:
            provider.create_opportunity(study_definition())

    assert error.value.code == "unknown_write_outcome"
    assert error.value.outcome == "unknown"


@pytest.mark.parametrize("operation", ["get_feasibility", "get_opportunity"])
def test_read_response_with_malformed_provider_link_is_rejected(operation):
    def handle(_request):
        if operation == "get_feasibility":
            body = feasibility_response(status="RESPONDED", cost="52.50")
            body["dashboard_url"] = "https://exa mple.com/quote"
        else:
            body = opportunity_response()
            body["links"]["dashboard"]["draft_editor"] = "https://terac.com:99999/draft"
        return httpx.Response(200, json=body)

    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(handle),
    )

    with pytest.raises(StudyReadError) as error:
        if operation == "get_feasibility":
            provider.get_feasibility(FEASIBILITY_ID)
        else:
            provider.get_opportunity(OPPORTUNITY_ID)

    assert error.value.code == "invalid_response"


def test_create_and_launch_use_distinct_rest_effects_and_exact_provider_links():
    calls = []

    def handle(request):
        calls.append(request)
        if request.method == "POST" and request.url.path.endswith("/opportunities"):
            return httpx.Response(200, json=opportunity_response())
        if request.method == "GET":
            return httpx.Response(200, json=opportunity_response())
        return httpx.Response(200, json=opportunity_response(status="active"))

    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(handle),
    )
    created = provider.create_opportunity(study_definition())
    read_back = provider.get_opportunity(OPPORTUNITY_ID)
    launched = provider.launch_opportunity(OPPORTUNITY_ID)

    assert [(call.method, call.url.path) for call in calls] == [
        ("POST", "/api/external/v2/opportunities"),
        ("GET", "/api/external/v2/opportunities/opportunity-123"),
        ("POST", "/api/external/v2/opportunities/opportunity-123/launch"),
    ]
    assert json.loads(calls[0].content) == study_definition()
    assert calls[2].content in (b"", b"{}")
    assert created.status == "draft"
    assert created.pricing.total_cost_cents == 5250
    assert read_back.draft_editor_url == (
        "https://terac.com/org/opportunities/opportunity-123/edit"
    )
    assert launched.status == "active"


def test_create_opportunity_accepts_omitted_optional_screening_review():
    calls = []

    def handle(request):
        calls.append(request)
        body = opportunity_response()
        body.pop("customer_screening_review")
        return httpx.Response(200, json=body)

    definition = study_definition()
    definition.pop("customer_screening_review")
    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(handle),
    )

    created = provider.create_opportunity(definition)

    assert len(calls) == 1
    assert "customer_screening_review" not in json.loads(calls[0].content)
    assert created.customer_screening_review is None


@pytest.mark.parametrize("screening_mode", ["unsupported", 7])
def test_opportunity_read_rejects_invalid_screening_resolution(screening_mode):
    def handle(_request):
        body = opportunity_response()
        body["customer_screening_review"] = screening_mode
        return httpx.Response(200, json=body)

    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(handle),
    )

    with pytest.raises(StudyReadError) as error:
        provider.get_opportunity(OPPORTUNITY_ID)

    assert error.value.code == "invalid_response"
    assert str(screening_mode) not in str(error.value)


def test_opportunity_read_rejects_duplicate_json_properties():
    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b'{"id":"opportunity-123","id":"other"}',
                headers={"content-type": "application/json"},
            )
        ),
    )

    with pytest.raises(StudyReadError) as error:
        provider.get_opportunity(OPPORTUNITY_ID)

    assert error.value.code == "invalid_response"


@pytest.mark.parametrize("nested_field", ["tasks", "filters"])
def test_read_opportunity_rejects_unexpected_nested_provider_fields(nested_field):
    marker = "PRIVATE_PROVIDER_MARKER_8a19"

    def handle(_request):
        body = opportunity_response()
        if nested_field == "tasks":
            body["tasks"][0]["report_payload"] = {"confidential_text": marker}
        else:
            body["filters"][0]["multi_select--country"]["report_payload"] = marker
        return httpx.Response(200, json=body)

    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(handle),
    )

    with pytest.raises(StudyReadError) as error:
        provider.get_opportunity(OPPORTUNITY_ID)

    assert error.value.code == "invalid_response"
    assert marker not in str(error.value)


def test_ambiguous_write_is_sanitized_and_never_retried():
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout("private response body")

    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(handle),
    )
    with pytest.raises(StudyWriteError) as error:
        provider.request_feasibility(
            {
                "taskDescription": "task",
                "panelDescription": "audience",
                "submissionCount": 1,
            }
        )

    assert len(calls) == 1
    assert error.value.code == "unknown_write_outcome"
    assert error.value.outcome == "unknown"
    assert "private response body" not in str(error.value)


@pytest.mark.parametrize("duration", [0, -1, True, "15", None])
def test_invalid_optional_file_upload_duration_is_rejected_before_transport(duration):
    calls = []
    provider = TeracStudies(
        lambda: TOKEN,
        transport_factory=lambda: httpx.MockTransport(
            lambda request: (
                calls.append(request) or httpx.Response(200, json=opportunity_response())
            )
        ),
    )
    definition = study_definition()
    definition["tasks"][0]["duration_minutes"] = duration

    with pytest.raises(StudyWriteError) as error:
        provider.create_opportunity(definition)

    assert error.value.outcome == "not_sent"
    assert calls == []


@pytest.mark.parametrize(
    "token,study_request",
    [
        (
            None,
            {"taskDescription": "task", "panelDescription": "audience", "submissionCount": 1},
        ),
        (
            TOKEN,
            {
                "taskDescription": "task",
                "panelDescription": "audience",
                "submissionCount": 1,
                "unknown": True,
            },
        ),
    ],
)
def test_invalid_credentials_or_fields_stop_before_transport(token, study_request):
    calls = []
    provider = TeracStudies(
        lambda: token,
        transport_factory=lambda: httpx.MockTransport(
            lambda request: (
                calls.append(request) or httpx.Response(200, json=feasibility_response())
            )
        ),
    )

    with pytest.raises(StudyWriteError):
        provider.request_feasibility(study_request)

    assert calls == []
