"""Read one pre-bound Terac study through the official MCP client."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar

import anyio
import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from pydantic import StrictBool, ValidationError

from adapters.terac_study_contract import (
    PROVIDER_STATUSES,
    ReviewedStudyBinding,
    StudyObservation,
    StudyReadError,
)

MCP_URL = "https://terac.com/api/mcp"
OPERATION_TIMEOUT_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10
MAX_REQUEST_BYTES = 8 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES = 2 * MAX_RESPONSE_BYTES
DEFINITION_FIELDS = (
    "id",
    "project_id",
    "title",
    "internal_title",
    "description",
    "num_participants",
    "estimated_duration_minutes",
    "filters",
    "screening_questions",
    "quotas",
    "cross_quotas",
    "tasks",
    "device_types",
    "business_type",
    "candidate_profile",
    "required_accounts",
    "video_required",
    "screen_share_required",
    "customer_screening_review",
    "pay_frequency",
    "screening_option_randomization",
    "forwarding_email_enabled",
    "max_responses_per_expert",
    "feasibility_request_id",
)
DOCUMENTED_IGNORED_RESPONSE_FIELDS = frozenset(
    {
        "funding",
        "created_at",
        "updated_at",
        "ending_at",
        "launched_at",
        "links",
        "submission_stats",
        "quota_progress",
        "cross_quota_progress",
        "screening_stats",
    }
)
OPPORTUNITY_RESPONSE_FIELDS = (
    frozenset(DEFINITION_FIELDS) | DOCUMENTED_IGNORED_RESPONSE_FIELDS | {"status", "pricing"}
)
CONTEXT_RESPONSE_FIELDS = frozenset(
    {
        "organizationId",
        "markdown",
        "organizationName",
        "organizationSlug",
        "balanceDollars",
        "dashboard",
    }
)
_private_read = ContextVar("terac_private_read", default=False)
_PRIVATE_LOGGERS = (
    "mcp.client.streamable_http",
    "client",
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
)


class _ReadLogFilter(logging.Filter):
    def filter(self, record):
        if not _private_read.get():
            return True
        # SDK 1.30 shared/session.py emits some raw-input diagnostics at root.
        shared_session = record.pathname.replace("\\", "/").endswith("/mcp/shared/session.py")
        return record.name not in _PRIVATE_LOGGERS and not shared_session


@contextmanager
def _private_sdk_logs():
    token = _private_read.set(True)
    guard = _ReadLogFilter()
    loggers = [logging.getLogger(name) for name in (*_PRIVATE_LOGGERS, "")]
    for logger in loggers:
        logger.addFilter(guard)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(guard)
        _private_read.reset(token)


class _StrictToolResult(types.CallToolResult):
    isError: StrictBool = False


class _ReadTransport(httpx.AsyncBaseTransport):
    """Enforce read authority and budgets before SDK-controlled network I/O."""

    def __init__(self, source: httpx.AsyncBaseTransport, binding: ReviewedStudyBinding):
        self.source = source
        self.binding = binding
        self.posts = 0
        self.gets = 0
        self.total_bytes = 0
        self.failure_code: str | None = None

    def reject(self, code: str):
        if self.failure_code is None:
            self.failure_code = code
        raise StudyReadError(self.failure_code)

    def ensure_healthy(self):
        if self.failure_code is not None:
            raise StudyReadError(self.failure_code)

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        if response.is_stream_consumed:
            body = response.content
            self.total_bytes += len(body)
            if len(body) > MAX_RESPONSE_BYTES or self.total_bytes > MAX_TOTAL_RESPONSE_BYTES:
                self.reject("response_too_large")
            return body
        size = 0
        chunks = []
        try:
            async for chunk in response.aiter_raw():
                size += len(chunk)
                self.total_bytes += len(chunk)
                if size > MAX_RESPONSE_BYTES or self.total_bytes > MAX_TOTAL_RESPONSE_BYTES:
                    self.reject("response_too_large")
                chunks.append(chunk)
        except StudyReadError:
            raise
        except httpx.TimeoutException:
            self.reject("timeout")
        except Exception:
            self.reject("transport_or_protocol")
        return b"".join(chunks)

    async def _reject_response(self, response: httpx.Response, code: str):
        if self.failure_code is None:
            self.failure_code = code
        try:
            await response.aclose()
        finally:
            self.ensure_healthy()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.ensure_healthy()
        if request.url != httpx.URL(MCP_URL):
            self.reject("destination_rejected")
        if request.method == "GET":
            self.gets += 1
            if self.gets > 1 or "last-event-id" in request.headers:
                self.reject("request_budget")
        elif request.method == "POST":
            raw = await request.aread()
            if len(raw) > MAX_REQUEST_BYTES:
                self.reject("request_too_large")
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeError):
                self.reject("request_rejected")
            expected = ("initialize", "notifications/initialized", "tools/call", "tools/call")
            if self.posts >= len(expected) or message.get("method") != expected[self.posts]:
                self.reject("request_budget")
            if self.posts >= 2:
                tool = "terac_get_context" if self.posts == 2 else "terac_get_opportunity"
                arguments = (
                    {} if self.posts == 2 else {"opportunityId": self.binding.opportunity_id}
                )
                if message.get("params") != {"name": tool, "arguments": arguments}:
                    self.reject("request_rejected")
            self.posts += 1
        else:
            self.reject("request_rejected")
        try:
            response = await self.source.handle_async_request(request)
        except httpx.TimeoutException:
            self.reject("timeout")
        except Exception:
            self.reject("transport_or_protocol")
        if 300 <= response.status_code < 400:
            await self._reject_response(response, "redirect")
        if response.status_code >= 400 and not (
            request.method == "GET" and response.status_code == 405
        ):
            await self._reject_response(
                response,
                {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(
                    response.status_code, "provider_http_error"
                ),
            )
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            await self._reject_response(response, "unsupported_encoding")
        length = response.headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
            await self._reject_response(response, "response_too_large")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if response.status_code == 200 and media_type not in {
            "application/json",
            "text/event-stream",
        }:
            await self._reject_response(response, "invalid_response")
        body = await self._read_bounded(response)
        if response.status_code == 200 and _raw_json_is_unsafe(body, media_type):
            self.failure_code = "invalid_response"
            await response.aclose()
            return httpx.Response(
                502,
                headers={"content-type": "text/plain"},
                content=b"",
                request=request,
            )
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.pop("transfer-encoding", None)
        extensions = response.extensions
        await response.aclose()
        return httpx.Response(
            response.status_code,
            headers=headers,
            content=body,
            extensions=extensions,
            request=request,
        )

    async def aclose(self):
        await self.source.aclose()


def study_definition_digest(value: dict) -> str:
    """Hash the complete v1 reviewed definition, excluding mutable observations."""
    try:
        projection = {key: value[key] for key in DEFINITION_FIELDS}
        arrays = {
            "filters",
            "screening_questions",
            "quotas",
            "cross_quotas",
            "tasks",
            "device_types",
            "required_accounts",
        }
        nullable_strings = {
            "internal_title",
            "description",
            "candidate_profile",
            "feasibility_request_id",
        }
        integers = {"num_participants", "estimated_duration_minutes", "max_responses_per_expert"}
        for key, item in projection.items():
            if key in arrays:
                valid = isinstance(item, list)
            elif key in nullable_strings:
                valid = item is None or isinstance(item, str)
            elif key in integers:
                valid = (key == "max_responses_per_expert" and item is None) or (
                    type(item) is int and item >= (1 if key == "num_participants" else 0)
                )
            else:
                valid = isinstance(item, str) and bool(item)
            if not valid:
                raise ValueError("Invalid projected field")
        encoded = json.dumps(
            projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise StudyReadError("invalid_response") from None
    return hashlib.sha256(encoded).hexdigest()


def _observation(value: dict, binding: ReviewedStudyBinding) -> StudyObservation:
    if not isinstance(value, dict):
        raise StudyReadError("invalid_response")
    if set(value) - OPPORTUNITY_RESPONSE_FIELDS:
        raise StudyReadError("invalid_response")
    if value.get("id") != binding.opportunity_id or value.get("project_id") != binding.project_id:
        raise StudyReadError("identity_mismatch")
    tasks = value.get("tasks")
    if (
        not isinstance(tasks, list)
        or len(tasks) != 1
        or not isinstance(tasks[0], dict)
        or type(tasks[0].get("sequence")) is not int
        or tasks[0]["sequence"] != 1
        or tasks[0].get("task_type") != "file_upload"
        or not isinstance(tasks[0].get("description"), str)
        or not tasks[0]["description"]
    ):
        raise StudyReadError("invalid_response")
    brief_digest = hashlib.sha256(tasks[0]["description"].encode("utf-8")).hexdigest()
    definition_digest = study_definition_digest(value)
    if brief_digest != binding.brief_sha256 or definition_digest != binding.definition_sha256:
        raise StudyReadError("binding_mismatch")
    status = value.get("status")
    if not isinstance(status, str) or status not in PROVIDER_STATUSES | {
        item.upper() for item in PROVIDER_STATUSES
    }:
        raise StudyReadError("unrecognized_status")
    count = value["num_participants"]
    if "pricing" not in value:
        raise StudyReadError("invalid_response")
    price = value["pricing"]
    if price is not None and (
        not isinstance(price, dict)
        or set(price) != {"cost_per_participant_cents", "total_cost_cents", "currency"}
        or price.get("currency") != "usd"
        or type(price.get("total_cost_cents")) is not int
        or price["total_cost_cents"] < 0
        or type(price.get("cost_per_participant_cents")) is not int
        or price["cost_per_participant_cents"] < 0
    ):
        raise StudyReadError("invalid_response")
    return StudyObservation(
        opportunity_id=binding.opportunity_id,
        project_id=binding.project_id,
        status=status.lower(),
        definition_sha256=definition_digest,
        brief_sha256=brief_digest,
        num_participants=count,
        total_cost_cents=price["total_cost_cents"] if price else None,
        cost_per_participant_cents=price["cost_per_participant_cents"] if price else None,
        currency=price["currency"] if price else None,
    )


def _validate_context(value: dict, binding: ReviewedStudyBinding) -> None:
    if (
        not isinstance(value, dict)
        or set(value) - CONTEXT_RESPONSE_FIELDS
        or not isinstance(value.get("organizationId"), str)
        or ("markdown" in value and not isinstance(value["markdown"], str))
    ):
        raise StudyReadError("invalid_response")
    if value["organizationId"] != binding.organization_id:
        raise StudyReadError("organization_mismatch")


async def _call(session: ClientSession, name: str, arguments: dict) -> dict:
    try:
        result = await session.send_request(
            types.ClientRequest(
                types.CallToolRequest(
                    params=types.CallToolRequestParams(name=name, arguments=arguments)
                )
            ),
            _StrictToolResult,
        )
    except ValidationError:
        raise StudyReadError("invalid_response") from None
    if result.isError:
        raise StudyReadError("provider_tool_error")
    if not isinstance(result.structuredContent, dict):
        raise StudyReadError("invalid_response")
    seen_markdown = False
    seen_structured_content = False
    for block in result.content:
        if not isinstance(block, types.TextContent):
            raise StudyReadError("invalid_response")
        if block.model_extra:
            raise StudyReadError("invalid_response")
        if name == "terac_get_context" and block.text == result.structuredContent.get("markdown"):
            if seen_markdown:
                raise StudyReadError("invalid_response")
            seen_markdown = True
            continue
        if not block.text:
            continue
        try:
            represented = json.loads(
                block.text, object_pairs_hook=_unique_fields, parse_constant=_invalid_constant
            )
        except json.JSONDecodeError:
            raise StudyReadError("invalid_response") from None
        except ValueError:
            raise StudyReadError("invalid_response") from None
        try:
            text_facts = json.dumps(represented, sort_keys=True, allow_nan=False)
            structured_facts = json.dumps(result.structuredContent, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError):
            raise StudyReadError("invalid_response") from None
        if not isinstance(represented, dict) or text_facts != structured_facts:
            raise StudyReadError("conflicting_content")
        if seen_structured_content:
            raise StudyReadError("invalid_response")
        seen_structured_content = True
    return result.structuredContent


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonField(key)
        result[key] = value
    return result


class _DuplicateJsonField(ValueError):
    pass


def _invalid_constant(value):
    raise ValueError("Nonfinite JSON value")


def _raw_json_is_unsafe(body: bytes, media_type: str) -> bool:
    """Reject ambiguous keys and malformed JSON before the SDK can normalize it."""
    if media_type == "application/json":
        payloads = (body,)
    elif media_type == "text/event-stream":
        try:
            text = body.decode("utf-8")
        except UnicodeError:
            return True
        # Match SSE framing: Unicode line/paragraph separators inside JSON strings
        # are data; only CR, LF, and CRLF end a line.
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        events = []
        data_lines = []
        for line in (*lines, ""):
            if not line:
                if data_lines:
                    events.append("\n".join(data_lines).encode("utf-8"))
                    data_lines = []
            elif line.startswith("data:"):
                data = line[5:]
                data_lines.append(data[1:] if data.startswith(" ") else data)
        payloads = events
    else:
        return False

    for payload in payloads:
        try:
            json.loads(payload, object_pairs_hook=_unique_fields, parse_constant=_invalid_constant)
        except _DuplicateJsonField:
            return True
        except (ValueError, UnicodeError):
            return True
    return False


async def read_study(
    binding: ReviewedStudyBinding,
    token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> StudyObservation:
    """Perform only context and known-study reads; never create or launch work."""
    if not isinstance(binding, ReviewedStudyBinding):
        raise StudyReadError("invalid_binding")
    if (
        not isinstance(token, str)
        or not token
        or any(not 33 <= ord(character) <= 126 for character in token)
    ):
        raise StudyReadError("invalid_credential")
    guard = _ReadTransport(
        transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False), binding
    )
    try:
        with _private_sdk_logs(), anyio.fail_after(OPERATION_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(
                transport=guard,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
                headers={"x-api-key": token, "Accept-Encoding": "identity"},
            ) as client:
                async with streamable_http_client(
                    MCP_URL, http_client=client, terminate_on_close=False
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        context = await _call(session, "terac_get_context", {})
                        _validate_context(context, binding)
                        value = await _call(
                            session,
                            "terac_get_opportunity",
                            {"opportunityId": binding.opportunity_id},
                        )
                        observation = _observation(value, binding)
            guard.ensure_healthy()
            return observation
    except Exception as exc:

        def code_for(error):
            if isinstance(error, StudyReadError):
                return error.code
            if isinstance(error, (TimeoutError, httpx.TimeoutException)):
                return "timeout"
            for child in getattr(error, "exceptions", ()):
                if code := code_for(child):
                    return code
            return None

        raise StudyReadError(
            guard.failure_code or code_for(exc) or "transport_or_protocol"
        ) from None


def read_study_sync(
    binding: ReviewedStudyBinding,
    token: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> StudyObservation:
    """Synchronous caller facade; async consumers use read_study directly."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(read_study(binding, token, transport=transport))
    raise StudyReadError("async_context_requires_async_reader")
