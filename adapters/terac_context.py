"""Read Terac's current-organization context without retaining its contents."""

from __future__ import annotations

import json
import math
import re
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import httpx

TERAC_CONTEXT_URL = "https://terac.com/api/external/v2/organizations/current/context"
REQUEST_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_DECODED_CHUNK_BYTES = 64 * 1024
DASHBOARD_FIELDS = (
    "home",
    "opportunities",
    "drafts",
    "feasibility",
    "finance",
    "api_keys",
)


class ContextReadError(RuntimeError):
    """A sanitized failure from the bounded read-only context request."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Terac context read failed: {code}")


@dataclass(frozen=True, slots=True)
class ContextObservation:
    """Safe response facts; provider values and response bodies are discarded."""

    http_status: int
    schema_valid: bool
    markdown_present: bool
    organization_id_present: bool
    organization_name_present: bool
    organization_slug_present: bool
    balance_present: bool
    dashboard_present: bool
    dashboard_fields_present: Mapping[str, bool]


def _validate_bearer_token(bearer_token: str) -> None:
    if (
        not isinstance(bearer_token, str)
        or not bearer_token
        or bearer_token.strip() != bearer_token
        or any(not 33 <= ord(character) <= 126 for character in bearer_token)
    ):
        raise ValueError(
            "A non-empty bearer token without whitespace or control characters is required."
        )


def _retry_after_seconds(headers: httpx.Headers) -> int | None:
    value = headers.get("Retry-After")
    if value is None or re.fullmatch(r"[0-9]{1,10}", value) is None:
        return None
    seconds = int(value)
    return seconds if seconds > 0 else None


def _status_error(response: httpx.Response) -> ContextReadError:
    status = response.status_code
    code_by_status = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        429: "rate_limited",
    }
    if 300 <= status < 400:
        code = "redirect"
    elif status in code_by_status:
        code = code_by_status[status]
    elif 500 <= status < 600:
        code = "provider_error"
    else:
        code = "unexpected_status"
    return ContextReadError(
        code,
        status_code=status,
        retry_after_seconds=_retry_after_seconds(response.headers),
    )


def _iter_decoded_body_chunks(response: httpx.Response):
    """Yield bounded decoded chunks, rejecting unsupported or oversized bodies."""
    encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
    if encoding in {"", "identity"}:
        decoder = None
    elif encoding == "gzip":
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif encoding == "deflate":
        decoder = zlib.decompressobj(zlib.MAX_WBITS)
    else:
        raise ContextReadError("invalid_response", status_code=response.status_code)

    decoded_size = 0
    raw_chunks = response.iter_raw(chunk_size=MAX_DECODED_CHUNK_BYTES)
    if decoder is None:
        for chunk in raw_chunks:
            decoded_size += len(chunk)
            if decoded_size > MAX_RESPONSE_BYTES:
                raise ContextReadError("response_too_large", status_code=response.status_code)
            yield chunk
        return

    try:
        for raw_chunk in raw_chunks:
            pending = raw_chunk
            drain_buffer = False
            while pending or drain_buffer:
                output_limit = min(
                    MAX_DECODED_CHUNK_BYTES,
                    MAX_RESPONSE_BYTES - decoded_size + 1,
                )
                input_chunk = pending
                decoded = decoder.decompress(input_chunk, output_limit)
                pending = decoder.unconsumed_tail
                if decoder.unused_data:
                    raise ContextReadError("invalid_response", status_code=response.status_code)
                decoded_size += len(decoded)
                if decoded_size > MAX_RESPONSE_BYTES:
                    raise ContextReadError("response_too_large", status_code=response.status_code)
                if decoded:
                    yield decoded
                drain_buffer = not pending and bool(decoded) and len(decoded) == output_limit
                if not input_chunk and not decoded:
                    break
        if not decoder.eof:
            raise ContextReadError("invalid_response", status_code=response.status_code)
    except zlib.error:
        raise ContextReadError("invalid_response", status_code=response.status_code) from None


def _read_bounded_body(response: httpx.Response) -> bytes:
    return b"".join(_iter_decoded_body_chunks(response))


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _context_observation(status_code: int, body: bytes) -> ContextObservation:
    try:
        payload = json.loads(body, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        raise ContextReadError("invalid_json", status_code=status_code) from None

    if not isinstance(payload, dict):
        raise ContextReadError("invalid_schema", status_code=status_code)

    string_fields = ("markdown", "organizationId", "organizationName", "organizationSlug")
    if any(not isinstance(payload.get(field), str) for field in string_fields):
        raise ContextReadError("invalid_schema", status_code=status_code)

    balance = payload.get("balanceDollars")
    if (
        isinstance(balance, bool)
        or not isinstance(balance, (int, float))
        or (isinstance(balance, float) and not math.isfinite(balance))
    ):
        raise ContextReadError("invalid_schema", status_code=status_code)

    dashboard = payload.get("dashboard")
    if not isinstance(dashboard, dict) or any(
        not isinstance(dashboard.get(field), str) for field in DASHBOARD_FIELDS
    ):
        raise ContextReadError("invalid_schema", status_code=status_code)

    return ContextObservation(
        http_status=status_code,
        schema_valid=True,
        markdown_present=True,
        organization_id_present=True,
        organization_name_present=True,
        organization_slug_present=True,
        balance_present=True,
        dashboard_present=True,
        dashboard_fields_present=MappingProxyType({field: True for field in DASHBOARD_FIELDS}),
    )


def read_context(
    bearer_token: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> ContextObservation:
    """Make one fixed GET and return only allowlisted schema-presence facts."""

    _validate_bearer_token(bearer_token)
    response_status_code: int | None = None
    try:
        with httpx.Client(
            transport=transport,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "GET",
                TERAC_CONTEXT_URL,
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate, identity",
                    "Authorization": f"Bearer {bearer_token}",
                },
            ) as response:
                response_status_code = response.status_code
                if response.status_code != 200:
                    raise _status_error(response)
                body = _read_bounded_body(response)
    except ContextReadError:
        raise
    except httpx.TimeoutException:
        raise ContextReadError("timeout", status_code=response_status_code) from None
    except httpx.DecodingError:
        raise ContextReadError("invalid_response", status_code=response_status_code) from None
    except httpx.TransportError:
        raise ContextReadError("transport_error", status_code=response_status_code) from None
    except httpx.HTTPError:
        raise ContextReadError("transport_error", status_code=response_status_code) from None

    return _context_observation(response.status_code, body)


__all__ = [
    "DASHBOARD_FIELDS",
    "MAX_RESPONSE_BYTES",
    "REQUEST_TIMEOUT_SECONDS",
    "TERAC_CONTEXT_URL",
    "ContextObservation",
    "ContextReadError",
    "read_context",
]
