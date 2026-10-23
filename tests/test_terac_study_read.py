import hashlib
import json
import logging
from dataclasses import asdict

import anyio
import httpx
import pytest

from adapters import terac_study_read as reader
from adapters.terac_study_read import ReviewedStudyBinding, StudyReadError, read_study


@pytest.fixture
def anyio_backend():
    return "asyncio"


def study():
    return {
        "id": "synthetic-study",
        "project_id": "synthetic-project",
        "title": "Synthetic review",
        "internal_title": None,
        "description": "Fictional material",
        "num_participants": 1,
        "estimated_duration_minutes": 15,
        "filters": [],
        "screening_questions": [],
        "quotas": [],
        "cross_quotas": [],
        "tasks": [
            {"sequence": 1, "task_type": "file_upload", "description": "Exact fictional task"}
        ],
        "device_types": ["desktop"],
        "business_type": "b2b",
        "candidate_profile": None,
        "required_accounts": [],
        "video_required": "off",
        "screen_share_required": "off",
        "customer_screening_review": "auto_invite",
        "pay_frequency": "one_off",
        "screening_option_randomization": "off",
        "forwarding_email_enabled": "off",
        "max_responses_per_expert": None,
        "feasibility_request_id": None,
    }


def binding():
    # Independently materialized reviewed definition; do not call the production digest helper.
    definition = json.dumps(study(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ReviewedStudyBinding(
        organization_id="synthetic-org",
        project_id="synthetic-project",
        opportunity_id="synthetic-study",
        brief_sha256=hashlib.sha256(b"Exact fictional task").hexdigest(),
        definition_sha256=hashlib.sha256(definition.encode()).hexdigest(),
    )


class FakeTerac(httpx.AsyncBaseTransport):
    def __init__(self):
        self.requests = []
        self.context = {"organizationId": "synthetic-org"}
        self.opportunity = {
            **study(),
            "status": "draft",
            "pricing": {
                "cost_per_participant_cents": 1800,
                "total_cost_cents": 1800,
                "currency": "usd",
            },
        }

    async def handle_async_request(self, request):
        self.requests.append(request)
        assert str(request.url) == "https://terac.com/api/mcp"
        assert request.headers["x-api-key"] == "synthetic-fixture-token"
        message = json.loads(request.content)
        method = message["method"]
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "synthetic-provider", "version": "1"},
            }
        elif message["params"]["name"] == "terac_get_context":
            assert message["params"]["arguments"] == {}
            result = {"content": [], "structuredContent": self.context}
        else:
            assert message["params"] == {
                "name": "terac_get_opportunity",
                "arguments": {"opportunityId": "synthetic-study"},
            }
            result = {"content": [], "structuredContent": self.opportunity}
        result = self.transform_result(message, result)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    def transform_result(self, message, result):
        return result


class DuplicateStructuredFieldTerac(FakeTerac):
    def __init__(
        self,
        tool_name,
        field,
        conflicting_value,
        media_type="application/json",
        literal_unicode=False,
        nonfinite_value=None,
    ):
        super().__init__()
        self.tool_name = tool_name
        self.field = field
        self.conflicting_value = conflicting_value
        self.media_type = media_type
        self.literal_unicode = literal_unicode
        self.nonfinite_value = nonfinite_value

    async def handle_async_request(self, request):
        response = await super().handle_async_request(request)
        message = json.loads(request.content)
        if message["method"] != "tools/call" or message["params"]["name"] != self.tool_name:
            return response
        raw = response.content
        encoded_field = json.dumps(self.field).encode()
        encoded_value = json.dumps(
            self.context[self.field]
            if self.field == "organizationId"
            else self.opportunity[self.field]
        ).encode()
        duplicate = (
            encoded_field
            + b":"
            + json.dumps(self.conflicting_value, ensure_ascii=not self.literal_unicode).encode()
        )
        duplicate += b"," + encoded_field + b":" + encoded_value
        if self.nonfinite_value is not None:
            duplicate = b'"ignored":' + self.nonfinite_value.encode() + b"," + duplicate
        needle = encoded_field + b":" + encoded_value
        assert raw.count(needle) == 1
        headers = dict(response.headers)
        headers.pop("content-length", None)
        if self.media_type == "text/event-stream":
            headers["content-type"] = self.media_type
            raw = b"data:" + raw + b"\n\n"
        return httpx.Response(
            response.status_code,
            headers=headers,
            content=raw.replace(needle, duplicate, 1),
        )


@pytest.mark.anyio
async def test_sdk_reads_only_bound_context_and_study_without_catalog_or_effects():
    transport = FakeTerac()
    observation = await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert observation.status == "draft"
    assert observation.opportunity_id == "synthetic-study"
    assert observation.total_cost_cents == 1800
    assert observation.num_participants == 1
    assert len(transport.requests) == 4
    assert [json.loads(request.content)["method"] for request in transport.requests] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
        "tools/call",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("scenario", "code"),
    [
        ("redirect", "redirect"),
        ("unauthorized", "unauthorized"),
        ("forbidden", "forbidden"),
        ("encoded", "unsupported_encoding"),
        ("oversized", "response_too_large"),
    ],
)
async def test_unsafe_response_stops_before_another_provider_request(scenario, code, caplog):
    class UnsafeTerac(FakeTerac):
        async def handle_async_request(self, request):
            self.requests.append(request)
            if scenario == "redirect":
                return httpx.Response(307, headers={"Location": "https://terac.com/api/mcp"})
            if scenario == "unauthorized":
                return httpx.Response(401, text="synthetic-provider-sensitive-canary")
            if scenario == "forbidden":
                return httpx.Response(403, text="synthetic-provider-sensitive-canary")
            if scenario == "encoded":
                return httpx.Response(
                    200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"bad")
                )
            return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))

    transport = UnsafeTerac()
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert failure.value.code == code
    assert len(transport.requests) == 1
    assert "synthetic-fixture-token" not in str(failure.value) + caplog.text
    assert "synthetic-provider-sensitive-canary" not in str(failure.value) + caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("title", None),
        ("estimated_duration_minutes", "15"),
        ("filters", {}),
        ("video_required", False),
        ("required_accounts", "none"),
        ("candidate_profile", []),
        ("max_responses_per_expert", True),
    ],
)
async def test_even_reviewed_hash_cannot_bless_wrong_provider_field_types(field, invalid):
    from dataclasses import replace

    transport = FakeTerac()
    projected = study()
    projected[field] = invalid
    transport.opportunity[field] = invalid
    expected_digest = hashlib.sha256(
        json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    reviewed = replace(binding(), definition_sha256=expected_digest)
    with pytest.raises(StudyReadError) as failure:
        await read_study(reviewed, "synthetic-fixture-token", transport=transport)
    assert failure.value.code == "invalid_response"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("id", "different-study", "identity_mismatch"),
        ("project_id", "different-project", "identity_mismatch"),
        ("title", "Changed task framing", "binding_mismatch"),
        ("status", "Draft", "unrecognized_status"),
        ("status", "draft ", "unrecognized_status"),
        ("status", "new_unknown_state", "unrecognized_status"),
        (
            "pricing",
            {"currency": "usd", "total_cost_cents": True, "cost_per_participant_cents": 1},
            "invalid_response",
        ),
    ],
)
async def test_wrong_binding_and_unsupported_observations_are_not_delivered(field, value, code):
    transport = FakeTerac()
    transport.opportunity[field] = value
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert failure.value.code == code
    assert len(transport.requests) == 4


@pytest.mark.anyio
@pytest.mark.parametrize(
    "extra_field",
    [
        {"expert_report": "private-feedback-canary"},
        {"launch_authority": True},
        {
            "unreviewed_definition": {
                "expert_report": "private-feedback-canary",
                "launch_authority": True,
            }
        },
    ],
)
async def test_unrecognized_opportunity_fields_are_rejected_without_exposure(extra_field):
    transport = FakeTerac()
    transport.opportunity.update(extra_field)

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)

    assert failure.value.code == "invalid_response"
    assert "private-feedback-canary" not in str(failure.value)
    assert len(transport.requests) == 4


@pytest.mark.anyio
async def test_documented_opportunity_metadata_is_ignored_from_the_public_observation():
    transport = FakeTerac()
    transport.opportunity.update(
        {
            "created_at": "2026-10-23T00:00:00Z",
            "funding": {"affordable": True, "balance_cents": 100000},
            "links": {"dashboard": {"study": "https://example.test/synthetic"}},
            "submission_stats": {"total": 0},
        }
    )

    observation = await read_study(binding(), "synthetic-fixture-token", transport=transport)
    observation_data = asdict(observation)

    assert observation.status == "draft"
    assert set(observation_data) == {
        "opportunity_id",
        "project_id",
        "status",
        "definition_sha256",
        "brief_sha256",
        "num_participants",
        "total_cost_cents",
        "cost_per_participant_cents",
        "currency",
    }
    assert "https://example.test/synthetic" not in str(observation_data)


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["draft", "DRAFT", "completed", "COMPLETED", "cancelled"])
async def test_known_statuses_are_observations_without_feedback_or_local_success(status):
    transport = FakeTerac()
    transport.opportunity["status"] = status
    result = await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert result.status == status.lower()
    assert not {"feedback", "recommendation", "succeeded", "approval"} & asdict(result).keys()


@pytest.mark.anyio
async def test_wrong_organization_stops_before_study_read():
    transport = FakeTerac()
    transport.context.update(
        {
            "organizationId": "different-org",
            "organizationName": "Synthetic organization",
            "organizationSlug": "synthetic-organization",
            "balanceDollars": 12.5,
            "dashboard": {
                "home": "/home",
                "opportunities": "/opportunities",
                "drafts": "/drafts",
                "feasibility": "/feasibility",
                "finance": "/finance",
                "api_keys": "/api-keys",
            },
            "markdown": "Synthetic organization context.",
        }
    )
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert failure.value.code == "organization_mismatch"
    assert len(transport.requests) == 3


@pytest.mark.anyio
async def test_documented_context_metadata_is_ignored_and_study_read_continues():
    class DocumentedContextTerac(FakeTerac):
        def __init__(self):
            super().__init__()
            self.context.update(
                {
                    "organizationName": "Synthetic organization",
                    "organizationSlug": "synthetic-organization",
                    "balanceDollars": 12.5,
                    "dashboard": {
                        "home": "/home",
                        "opportunities": "/opportunities",
                        "drafts": "/drafts",
                        "feasibility": "/feasibility",
                        "finance": "/finance",
                        "api_keys": "/api-keys",
                    },
                    "markdown": "Synthetic organization context.",
                }
            )

        def transform_result(self, message, result):
            if (
                message["method"] == "tools/call"
                and message["params"]["name"] == "terac_get_context"
            ):
                result["content"] = [{"type": "text", "text": self.context["markdown"]}]
            return result

    transport = DocumentedContextTerac()
    observation = await read_study(binding(), "synthetic-fixture-token", transport=transport)

    assert observation.status == "draft"
    assert [
        json.loads(request.content).get("params", {}).get("name")
        for request in transport.requests
        if json.loads(request.content)["method"] == "tools/call"
    ] == ["terac_get_context", "terac_get_opportunity"]


@pytest.mark.anyio
async def test_missing_pricing_is_not_invented_as_a_null_quote():
    transport = FakeTerac()
    del transport.opportunity["pricing"]
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert failure.value.code == "invalid_response"


@pytest.mark.anyio
@pytest.mark.parametrize("is_error", [True, "false", None, 0])
async def test_error_or_malformed_error_flag_never_exposes_provider_body(is_error, caplog):
    class ErrorTerac(FakeTerac):
        def transform_result(self, message, result):
            if message["method"] == "tools/call":
                result["isError"] = is_error
                result["content"] = [{"type": "text", "text": "synthetic-private-provider-canary"}]
            return result

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=ErrorTerac())
    assert failure.value.code in {"provider_tool_error", "invalid_response"}
    assert "synthetic-private-provider-canary" not in str(failure.value) + caplog.text
    assert "synthetic-fixture-token" not in str(failure.value) + caplog.text


@pytest.mark.anyio
async def test_untrusted_content_type_is_not_logged(monkeypatch, caplog):
    monkeypatch.setattr(reader, "OPERATION_TIMEOUT_SECONDS", 0.05)

    class HeaderTerac(FakeTerac):
        async def handle_async_request(self, request):
            self.requests.append(request)
            return httpx.Response(200, headers={"content-type": "synthetic-sensitive-header"})

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=HeaderTerac())
    assert failure.value.code == "invalid_response"
    assert "synthetic-sensitive-header" not in caplog.text


@pytest.mark.anyio
async def test_whole_operation_timeout_is_sanitized_and_not_retried(monkeypatch):
    # Leave time for the MCP/HTTP clients to reach the fake transport before
    # the operation deadline starts testing cancellation.
    monkeypatch.setattr(reader, "OPERATION_TIMEOUT_SECONDS", 1)

    class SlowTerac(FakeTerac):
        def __init__(self):
            super().__init__()
            self.cancelled = False

        async def handle_async_request(self, request):
            self.requests.append(request)
            try:
                await anyio.sleep(2)
            except BaseException:
                self.cancelled = True
                raise
            raise AssertionError("operation deadline must cancel the request")

    transport = SlowTerac()
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert failure.value.code == "timeout"
    assert len(transport.requests) == 1
    assert transport.cancelled


@pytest.mark.anyio
async def test_request_budget_rejects_before_network_and_no_nested_sync_loop(monkeypatch):
    monkeypatch.setattr(reader, "MAX_REQUEST_BYTES", 16)
    transport = FakeTerac()
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert failure.value.code == "request_too_large"
    assert transport.requests == []
    with pytest.raises(StudyReadError) as nested:
        reader.read_study_sync(binding(), "synthetic-fixture-token", transport=transport)
    assert nested.value.code == "async_context_requires_async_reader"


@pytest.mark.anyio
async def test_session_get_is_bounded_and_delete_is_never_sent():
    class SessionTerac(FakeTerac):
        async def handle_async_request(self, request):
            if request.method == "GET":
                self.requests.append(request)
                return httpx.Response(405)
            response = await super().handle_async_request(request)
            if json.loads(request.content)["method"] == "initialize":
                response.headers["mcp-session-id"] = "synthetic-session"
            return response

    transport = SessionTerac()
    observation = await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert observation.status == "draft"
    assert [request.method for request in transport.requests].count("POST") == 4
    assert [request.method for request in transport.requests].count("GET") <= 1
    assert not {"DELETE", "PUT", "PATCH"} & {request.method for request in transport.requests}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        for start in range(0, len(self.body), 71):
            yield self.body[start : start + 71]


@pytest.mark.anyio
@pytest.mark.parametrize("extra", [0, 1])
async def test_streamed_response_limit_is_inclusive_without_content_length(extra, monkeypatch):
    monkeypatch.setattr(reader, "MAX_RESPONSE_BYTES", 2048)
    monkeypatch.setattr(reader, "OPERATION_TIMEOUT_SECONDS", 0.1)

    class StreamTerac(FakeTerac):
        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            if json.loads(request.content)["method"] == "initialize":
                body = response.content + b" " * (2048 + extra - len(response.content))
                return httpx.Response(
                    200, headers={"content-type": "application/json"}, stream=Chunks(body)
                )
            return response

    if extra:
        with pytest.raises(StudyReadError) as failure:
            await read_study(binding(), "synthetic-fixture-token", transport=StreamTerac())
        assert failure.value.code == "response_too_large"
    else:
        assert (
            await read_study(binding(), "synthetic-fixture-token", transport=StreamTerac())
        ).status == "draft"


@pytest.mark.anyio
async def test_aggregate_response_limit_counts_multiple_responses(monkeypatch):
    monkeypatch.setattr(reader, "MAX_TOTAL_RESPONSE_BYTES", 300)
    monkeypatch.setattr(reader, "OPERATION_TIMEOUT_SECONDS", 0.1)
    transport = FakeTerac()
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert failure.value.code == "response_too_large"
    assert len(transport.requests) <= 4


@pytest.mark.anyio
async def test_documented_null_quote_stays_unknown():
    transport = FakeTerac()
    transport.opportunity["pricing"] = None
    observation = await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert observation.total_cost_cents is None
    assert observation.cost_per_participant_cents is None
    assert observation.currency is None


@pytest.mark.anyio
async def test_malformed_sdk_message_does_not_log_body_or_suppress_other_tasks(caplog):
    logger = logging.getLogger("mcp.client.streamable_http")
    caplog.set_level(logging.DEBUG, logger=logger.name)
    arrived = anyio.Event()

    async def unrelated_client():
        await arrived.wait()
        logger.warning("unrelated-client-visible")

    class MalformedTerac(FakeTerac):
        async def handle_async_request(self, request):
            self.requests.append(request)
            arrived.set()
            await anyio.sleep(0)
            message = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"private": "synthetic-malformed-sensitive-canary"},
                },
            )

    async with anyio.create_task_group() as group:
        group.start_soon(unrelated_client)
        with pytest.raises(StudyReadError):
            await read_study(binding(), "synthetic-fixture-token", transport=MalformedTerac())
    logger.warning("after-read-visible")
    assert "synthetic-malformed-sensitive-canary" not in caplog.text
    assert "synthetic-fixture-token" not in caplog.text
    assert "unrelated-client-visible" in caplog.text
    assert "after-read-visible" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("framing", ["json", "sse"])
async def test_malformed_framing_does_not_leak_to_logs_or_stderr(
    framing, caplog, capsys, monkeypatch
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(reader, "OPERATION_TIMEOUT_SECONDS", 0.05)

    class FramingTerac(FakeTerac):
        async def handle_async_request(self, request):
            self.requests.append(request)
            body = b'{"jsonrpc":"2.0","private":"synthetic-framing-sensitive-canary"}'
            if framing == "sse":
                body = b"event: message\ndata: " + body + b"\n\n"
            return httpx.Response(
                200,
                content=body,
                headers={
                    "content-type": "application/json"
                    if framing == "json"
                    else "text/event-stream",
                    "mcp-session-id": "synthetic-sensitive-session-id",
                },
            )

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=FramingTerac())
    assert failure.value.code in {"timeout", "transport_or_protocol", "invalid_response"}
    captured = capsys.readouterr()
    combined = caplog.text + captured.out + captured.err + str(failure.value)
    for secret in (
        "synthetic-framing-sensitive-canary",
        "synthetic-sensitive-session-id",
        "synthetic-fixture-token",
    ):
        assert secret not in combined


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_log_containment_restores_existing_filters_on_every_exit(outcome, caplog):
    names = (
        "mcp.client.streamable_http",
        "client",
        "httpx",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "",
    )
    loggers = [logging.getLogger(name) for name in names]
    before = [tuple(logger.filters) for logger in loggers]

    class SlowTerac(FakeTerac):
        async def handle_async_request(self, request):
            await anyio.sleep(1)

    if outcome == "success":
        await read_study(binding(), "synthetic-fixture-token", transport=FakeTerac())
    elif outcome == "error":
        transport = FakeTerac()
        transport.context["organizationId"] = "wrong-org"
        with pytest.raises(StudyReadError):
            await read_study(binding(), "synthetic-fixture-token", transport=transport)
    else:
        with anyio.move_on_after(0.02) as scope:
            await read_study(binding(), "synthetic-fixture-token", transport=SlowTerac())
        assert scope.cancelled_caught
    assert [tuple(logger.filters) for logger in loggers] == before
    loggers[0].warning("after-exit-visible")
    assert "after-exit-visible" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("failure_kind", ["redirect", "oversize"])
async def test_optional_get_guard_failure_is_terminal_even_when_sdk_swallows_it(failure_kind):
    class BackgroundFailureTerac(FakeTerac):
        async def handle_async_request(self, request):
            if request.method == "GET":
                self.requests.append(request)
                if failure_kind == "redirect":
                    return httpx.Response(307, headers={"location": reader.MCP_URL})
                return httpx.Response(
                    200,
                    headers={
                        "content-type": "text/event-stream",
                        "content-length": str(1024 * 1024 + 1),
                    },
                )
            response = await super().handle_async_request(request)
            if json.loads(request.content)["method"] == "initialize":
                response.headers["mcp-session-id"] = "synthetic-session"
            return response

    transport = BackgroundFailureTerac()
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert failure.value.code == (
        "redirect" if failure_kind == "redirect" else "response_too_large"
    )
    get_position = next(i for i, item in enumerate(transport.requests) if item.method == "GET")
    assert all(item.method != "POST" for item in transport.requests[get_position + 1 :])


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method,failure_kind", [("GET", "redirect"), ("GET", "oversize"), ("POST", "redirect")]
)
async def test_rejected_response_close_failure_cannot_erase_guard_failure(method, failure_kind):
    class BrokenCloseStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b""

        async def aclose(self):
            raise RuntimeError("synthetic-close-failure")

    class BrokenCleanupTerac(FakeTerac):
        async def handle_async_request(self, request):
            if request.method == method:
                self.requests.append(request)
                headers = (
                    {"location": reader.MCP_URL}
                    if failure_kind == "redirect"
                    else {
                        "content-type": "text/event-stream",
                        "content-length": str(1024 * 1024 + 1),
                    }
                )
                return httpx.Response(
                    307 if failure_kind == "redirect" else 200,
                    headers=headers,
                    stream=BrokenCloseStream(),
                )
            response = await super().handle_async_request(request)
            if request.method == "POST" and json.loads(request.content)["method"] == "initialize":
                response.headers["mcp-session-id"] = "synthetic-session"
            return response

    transport = BrokenCleanupTerac()
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)
    assert failure.value.code == (
        "redirect" if failure_kind == "redirect" else "response_too_large"
    )
    rejected = next(i for i, request in enumerate(transport.requests) if request.method == method)
    assert len(transport.requests) == rejected + 1


@pytest.mark.anyio
@pytest.mark.parametrize("conflicting", [False, True])
async def test_json_text_and_structured_facts_must_agree(conflicting):
    class RepresentedTerac(FakeTerac):
        def transform_result(self, message, result):
            if message["method"] == "tools/call":
                value = dict(result["structuredContent"])
                if conflicting and message["params"]["name"] == "terac_get_opportunity":
                    value.update(id="different-study", status="completed")
                result["content"] = [{"type": "text", "text": json.dumps(value)}]
            return result

    if conflicting:
        with pytest.raises(StudyReadError) as failure:
            await read_study(binding(), "synthetic-fixture-token", transport=RepresentedTerac())
        assert failure.value.code == "conflicting_content"
    else:
        observation = await read_study(
            binding(), "synthetic-fixture-token", transport=RepresentedTerac()
        )
        assert observation.status == "draft"


@pytest.mark.anyio
@pytest.mark.parametrize(
    (
        "tool_name",
        "field",
        "conflicting_value",
        "request_count",
        "media_type",
        "literal_unicode",
    ),
    [
        ("terac_get_context", "organizationId", "different-org", 3, "application/json", False),
        ("terac_get_opportunity", "id", "different-study", 4, "application/json", False),
        ("terac_get_opportunity", "status", "completed", 4, "application/json", False),
        ("terac_get_context", "organizationId", "different-org", 3, "text/event-stream", False),
        ("terac_get_context", "organizationId", "wrong\u0085value", 3, "text/event-stream", True),
        ("terac_get_context", "organizationId", "wrong\u2028value", 3, "text/event-stream", True),
        ("terac_get_context", "organizationId", "wrong\u2029value", 3, "text/event-stream", True),
    ],
)
async def test_duplicate_raw_structured_fields_are_rejected(
    tool_name, field, conflicting_value, request_count, media_type, literal_unicode
):
    transport = DuplicateStructuredFieldTerac(
        tool_name,
        field,
        conflicting_value,
        media_type=media_type,
        literal_unicode=literal_unicode,
    )

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)

    assert failure.value.code == "invalid_response"
    assert len(transport.requests) == request_count


@pytest.mark.anyio
@pytest.mark.parametrize("media_type", ["application/json", "text/event-stream"])
@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "-Infinity"])
async def test_nonfinite_json_cannot_preempt_duplicate_field_rejection(media_type, nonfinite):
    transport = DuplicateStructuredFieldTerac(
        "terac_get_context",
        "organizationId",
        "different-org",
        media_type=media_type,
        nonfinite_value=nonfinite,
    )

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)

    assert failure.value.code == "invalid_response"
    assert len(transport.requests) == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    "markdown",
    ["Synthetic organization information.", "{}", "42"],
)
async def test_context_plain_markdown_is_accepted_only_when_exactly_bound(markdown):
    class MarkdownTerac(FakeTerac):
        def __init__(self):
            super().__init__()
            self.context["markdown"] = markdown

        def transform_result(self, message, result):
            if (
                message["method"] == "tools/call"
                and message["params"]["name"] == "terac_get_context"
            ):
                result["content"] = [{"type": "text", "text": self.context["markdown"]}]
            return result

    result = await read_study(binding(), "synthetic-fixture-token", transport=MarkdownTerac())
    assert result.status == "draft"


@pytest.mark.anyio
async def test_context_rejects_undocumented_fields_before_opportunity_read():
    transport = FakeTerac()
    transport.context["unexpected"] = "discarded"

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)

    assert failure.value.code == "invalid_response"
    assert len(transport.requests) == 3
    assert json.loads(transport.requests[-1].content)["params"]["name"] == "terac_get_context"


@pytest.mark.anyio
async def test_context_rejects_unknown_text_content_fields_before_opportunity_read():
    class ExtraTextFieldTerac(FakeTerac):
        def transform_result(self, message, result):
            if (
                message["method"] == "tools/call"
                and message["params"]["name"] == "terac_get_context"
            ):
                result["content"] = [
                    {
                        "type": "text",
                        "text": json.dumps(result["structuredContent"]),
                        "unexpected": "synthetic-canary",
                    }
                ]
            return result

    transport = ExtraTextFieldTerac()
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)

    assert failure.value.code == "invalid_response"
    assert len(transport.requests) == 3
    assert json.loads(transport.requests[-1].content)["params"]["name"] == "terac_get_context"


@pytest.mark.anyio
async def test_context_rejects_wrong_typed_optional_markdown():
    transport = FakeTerac()
    transport.context["markdown"] = 42

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)

    assert failure.value.code == "invalid_response"
    assert len(transport.requests) == 3


@pytest.mark.anyio
async def test_context_rejects_wrong_typed_organization_identity():
    transport = FakeTerac()
    transport.context["organizationId"] = 42

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)

    assert failure.value.code == "invalid_response"
    assert len(transport.requests) == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool_name", "representation", "markdown", "expected_requests"),
    [
        ("terac_get_context", "markdown", "{}", 3),
        ("terac_get_context", "markdown", "", 3),
        ("terac_get_opportunity", "structured", "{}", 4),
    ],
)
async def test_duplicate_equivalent_content_blocks_are_rejected(
    tool_name, representation, markdown, expected_requests
):
    class DuplicateContentTerac(FakeTerac):
        def __init__(self):
            super().__init__()
            self.context["markdown"] = markdown

        def transform_result(self, message, result):
            if message["method"] != "tools/call" or message["params"]["name"] != tool_name:
                return result
            text = (
                self.context["markdown"]
                if representation == "markdown"
                else json.dumps(result["structuredContent"], sort_keys=True)
            )
            result["content"] = [
                {"type": "text", "text": text},
                {"type": "text", "text": text},
            ]
            return result

    transport = DuplicateContentTerac()
    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=transport)

    assert failure.value.code == "invalid_response"
    assert len(transport.requests) == expected_requests


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text",
    [
        '{"id":"synthetic-study","id":"synthetic-study"}',
        '{"status":NaN}',
        '{"status":"draft",}',
        "Unstructured status information is not a supported study representation.",
    ],
)
async def test_ambiguous_or_malformed_text_is_not_ignored(text):
    class BadRepresentationTerac(FakeTerac):
        def transform_result(self, message, result):
            if (
                message["method"] == "tools/call"
                and message["params"]["name"] == "terac_get_opportunity"
            ):
                result["content"] = [{"type": "text", "text": text}]
            return result

    with pytest.raises(StudyReadError) as failure:
        await read_study(binding(), "synthetic-fixture-token", transport=BadRepresentationTerac())
    assert failure.value.code == "invalid_response"


@pytest.mark.parametrize("token", [None, "", " contains-space", "line\nbreak", "nonascii-é"])
def test_invalid_credentials_never_reach_transport(token):
    transport = FakeTerac()
    with pytest.raises(StudyReadError) as failure:
        reader.read_study_sync(binding(), token, transport=transport)
    assert failure.value.code == "invalid_credential"
    assert transport.requests == []
