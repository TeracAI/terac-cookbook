from __future__ import annotations

import gzip
import json
import random
import string
import zlib

import httpx
import pytest

from adapters.terac_context import (
    DASHBOARD_FIELDS,
    MAX_DECODED_CHUNK_BYTES,
    MAX_RESPONSE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    TERAC_CONTEXT_URL,
    ContextReadError,
    _iter_decoded_body_chunks,
    read_context,
)


def valid_payload() -> dict[str, object]:
    return {
        "markdown": "private organization context",
        "organizationId": "private-id",
        "organizationName": "private-name",
        "organizationSlug": "private-slug",
        "balanceDollars": 12.5,
        "dashboard": {field: f"https://private.example/{field}" for field in DASHBOARD_FIELDS},
    }


def response_for(payload: object, *, status_code: int = 200) -> httpx.Response:
    return raw_response(
        status_code,
        json.dumps(payload, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
    )


def raw_response(
    status_code: int,
    body: bytes = b"",
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        stream=httpx.ByteStream(body),
        headers=headers,
    )


class FailingByteStream(httpx.SyncByteStream):
    def __init__(self, exception: type[Exception]) -> None:
        self.exception = exception

    def __iter__(self):
        yield b"{"
        raise self.exception("private stream detail fixture-token")

    def close(self) -> None:
        pass


def test_valid_context_uses_one_fixed_get_and_returns_only_safe_presence_facts() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response_for({**valid_payload(), "privateExtra": "private-extra-sentinel"})

    observation = read_context(
        "fixture-token-do-not-return",
        transport=httpx.MockTransport(handler),
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == TERAC_CONTEXT_URL
    assert request.url.query == b""
    assert request.content == b""
    assert request.headers["authorization"] == "Bearer fixture-token-do-not-return"
    assert request.headers["accept-encoding"] == "gzip, deflate, identity"
    assert "x-api-key" not in request.headers
    assert "cookie" not in request.headers
    assert request.extensions["timeout"]["read"] == REQUEST_TIMEOUT_SECONDS
    assert observation.http_status == 200
    assert observation.schema_valid is True
    assert observation.markdown_present is True
    assert observation.organization_id_present is True
    assert observation.organization_name_present is True
    assert observation.organization_slug_present is True
    assert observation.balance_present is True
    assert observation.dashboard_present is True
    assert observation.dashboard_fields_present == {field: True for field in DASHBOARD_FIELDS}

    rendered = repr(observation)
    for private_value in (
        "private organization context",
        "private-id",
        "private-name",
        "private-slug",
        "private-extra-sentinel",
        "12.5",
        "fixture-token-do-not-return",
        "private.example",
    ):
        assert private_value not in rendered


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**valid_payload(), "organizationId": None},
        {**valid_payload(), "organizationName": 7},
        {**valid_payload(), "organizationSlug": False},
        {**valid_payload(), "markdown": []},
        {**valid_payload(), "balanceDollars": True},
        {**valid_payload(), "balanceDollars": "12.5"},
        {**valid_payload(), "dashboard": None},
        {**valid_payload(), "dashboard": {field: "ok" for field in DASHBOARD_FIELDS[:-1]}},
        {
            **valid_payload(),
            "dashboard": {
                **{field: "ok" for field in DASHBOARD_FIELDS},
                "api_keys": None,
            },
        },
    ],
)
def test_invalid_context_shapes_raise_a_sanitized_schema_error(payload: object) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response_for(payload)

    with pytest.raises(ContextReadError) as raised:
        read_context("fixture-token", transport=httpx.MockTransport(handler))

    assert raised.value.code == "invalid_schema"
    assert raised.value.status_code == 200
    assert "fixture-token" not in str(raised.value)
    assert "private" not in str(raised.value)
    assert len(requests) == 1


@pytest.mark.parametrize("body", [b"not-json", b"NaN"])
def test_invalid_json_and_non_finite_numbers_are_rejected(body: bytes) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return raw_response(200, body, headers={"Content-Type": "application/json"})

    with pytest.raises(ContextReadError) as raised:
        read_context("fixture-token", transport=httpx.MockTransport(handler))

    assert raised.value.code in {"invalid_json", "invalid_schema"}
    assert len(requests) == 1


def test_non_finite_numeric_balance_is_rejected_after_parsing() -> None:
    body = json.dumps(valid_payload(), separators=(",", ":")).replace("12.5", "1e999").encode()

    with pytest.raises(ContextReadError) as raised:
        read_context(
            "fixture-token",
            transport=httpx.MockTransport(
                lambda request: raw_response(
                    200, body, headers={"Content-Type": "application/json"}
                )
            ),
        )

    assert raised.value.code == "invalid_schema"


def test_deeply_nested_ignored_json_is_normalized() -> None:
    valid = json.dumps(valid_payload(), separators=(",", ":")).encode()[:-1]
    body = valid + b',"ignored":' + b"[" * 20_000 + b"null" + b"]" * 20_000 + b"}"
    assert len(body) < MAX_RESPONSE_BYTES

    with pytest.raises(ContextReadError) as raised:
        read_context(
            "fixture-token",
            transport=httpx.MockTransport(
                lambda request: raw_response(
                    200, body, headers={"Content-Type": "application/json"}
                )
            ),
        )

    assert raised.value.code == "invalid_json"
    assert raised.value.status_code == 200
    assert "ignored" not in str(raised.value)


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (400, "bad_request"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (429, "rate_limited"),
        (500, "provider_error"),
        (503, "provider_error"),
        (418, "unexpected_status"),
        (302, "redirect"),
    ],
)
def test_http_errors_are_normalized_without_following_or_echoing_response_data(
    status_code: int,
    expected_code: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return raw_response(
            status_code,
            b"private provider error body",
            headers={
                "Location": "https://elsewhere.example/",
                "Content-Type": "text/plain",
            },
        )

    with pytest.raises(ContextReadError) as raised:
        read_context("fixture-token", transport=httpx.MockTransport(handler))

    assert raised.value.code == expected_code
    assert raised.value.status_code == status_code
    assert raised.value.retry_after_seconds is None
    assert "private" not in str(raised.value)
    assert "elsewhere.example" not in str(raised.value)
    assert "fixture-token" not in str(raised.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("120", 120),
        ("1", 1),
        ("0", None),
        ("-1", None),
        ("1.5", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", None),
    ],
)
def test_retry_after_only_exposes_a_positive_integer_and_never_waits(
    header: str,
    expected: int | None,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return raw_response(429, headers={"Retry-After": header})

    with pytest.raises(ContextReadError) as raised:
        read_context("fixture-token", transport=httpx.MockTransport(handler))

    assert raised.value.code == "rate_limited"
    assert raised.value.retry_after_seconds == expected
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [
        (httpx.ConnectError, "transport_error"),
        (httpx.ReadTimeout, "timeout"),
    ],
)
def test_transport_failures_are_sanitized_and_not_retried(
    exception: type[Exception],
    expected_code: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise exception("private provider detail fixture-token")

    with pytest.raises(ContextReadError) as raised:
        read_context("fixture-token", transport=httpx.MockTransport(handler))

    assert raised.value.code == expected_code
    assert raised.value.status_code is None
    assert "private" not in str(raised.value)
    assert "fixture-token" not in str(raised.value)
    assert len(requests) == 1


def test_decoded_body_limit_accepts_exact_boundary_and_rejects_one_byte_over() -> None:
    prefix = b'{"markdown":"'
    suffix = (
        b'","organizationId":"i","organizationName":"n","organizationSlug":"s",'
        b'"balanceDollars":1,"dashboard":'
        + json.dumps({field: "x" for field in DASHBOARD_FIELDS}, separators=(",", ":")).encode()
        + b"}"
    )
    boundary = prefix + b"x" * (MAX_RESPONSE_BYTES - len(prefix) - len(suffix)) + suffix
    assert len(boundary) == MAX_RESPONSE_BYTES

    def compressed_response(body: bytes) -> httpx.Response:
        return raw_response(
            200,
            gzip.compress(body),
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )

    boundary_requests: list[httpx.Request] = []
    boundary_observation = read_context(
        "fixture-token",
        transport=httpx.MockTransport(
            lambda request: (boundary_requests.append(request), compressed_response(boundary))[1]
        ),
    )
    assert boundary_observation.schema_valid is True
    assert len(boundary_requests) == 1

    oversized = boundary + b" "
    oversized_requests: list[httpx.Request] = []
    with pytest.raises(ContextReadError) as raised:
        read_context(
            "fixture-token",
            transport=httpx.MockTransport(
                lambda request: (
                    oversized_requests.append(request),
                    compressed_response(oversized),
                )[1]
            ),
        )
    assert raised.value.code == "response_too_large"
    assert raised.value.status_code == 200
    assert len(oversized_requests) == 1


def test_compressed_expansion_is_decoded_in_bounded_chunks() -> None:
    response = raw_response(
        200,
        gzip.compress(b"x" * (4 * 1024 * 1024)),
        headers={"Content-Encoding": "gzip"},
    )
    chunks: list[bytes] = []

    with pytest.raises(ContextReadError) as raised:
        chunks.extend(_iter_decoded_body_chunks(response))

    assert raised.value.code == "response_too_large"
    assert raised.value.status_code == 200
    assert chunks
    assert max(map(len, chunks)) <= MAX_DECODED_CHUNK_BYTES
    assert sum(map(len, chunks)) <= MAX_RESPONSE_BYTES


def test_deflate_content_encoding_is_supported() -> None:
    body = json.dumps(valid_payload(), separators=(",", ":")).encode()
    response = raw_response(
        200,
        zlib.compress(body),
        headers={"Content-Type": "application/json", "Content-Encoding": "deflate"},
    )

    observation = read_context(
        "fixture-token", transport=httpx.MockTransport(lambda request: response)
    )

    assert observation.schema_valid is True


def test_gzip_reader_handles_a_response_split_across_raw_chunks() -> None:
    payload = valid_payload()
    generator = random.Random(0)
    payload["markdown"] = "".join(generator.choices(string.ascii_letters, k=200_000))
    compressed = gzip.compress(json.dumps(payload, separators=(",", ":")).encode())
    assert len(compressed) > MAX_DECODED_CHUNK_BYTES
    response = raw_response(
        200,
        compressed,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )

    observation = read_context(
        "fixture-token", transport=httpx.MockTransport(lambda request: response)
    )

    assert observation.schema_valid is True


@pytest.mark.parametrize("compressed", [gzip.compress(b"truncated")[:-1], b"not-gzip"])
def test_truncated_or_invalid_gzip_is_normalized(compressed: bytes) -> None:
    response = raw_response(
        200,
        compressed,
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )

    with pytest.raises(ContextReadError) as raised:
        read_context("fixture-token", transport=httpx.MockTransport(lambda request: response))

    assert raised.value.code == "invalid_response"
    assert raised.value.status_code == 200


def test_unsupported_content_encoding_is_rejected_without_decoding() -> None:
    response = raw_response(
        200,
        b"opaque-provider-body",
        headers={"Content-Encoding": "br"},
    )

    with pytest.raises(ContextReadError) as raised:
        read_context("fixture-token", transport=httpx.MockTransport(lambda request: response))

    assert raised.value.code == "invalid_response"
    assert raised.value.status_code == 200


@pytest.mark.parametrize("token", ["", " token", "token ", "token\nvalue", "token\x7fvalue"])
def test_invalid_bearer_tokens_fail_before_transport(token: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response_for(valid_payload())

    with pytest.raises(ValueError, match="non-empty bearer token"):
        read_context(token, transport=httpx.MockTransport(handler))

    assert requests == []


@pytest.mark.parametrize("token", ["token-é", "é"])
def test_non_ascii_bearer_tokens_fail_before_transport(token: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response_for(valid_payload())

    with pytest.raises(ValueError, match="non-empty bearer token") as raised:
        read_context(token, transport=httpx.MockTransport(handler))

    assert token not in str(raised.value)
    assert requests == []


@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [(httpx.ReadTimeout, "timeout"), (httpx.ReadError, "transport_error")],
)
def test_stream_failures_preserve_the_received_http_status(
    exception: type[Exception],
    expected_code: str,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            stream=FailingByteStream(exception),
            headers={"Content-Type": "application/json"},
        )

    with pytest.raises(ContextReadError) as raised:
        read_context("fixture-token", transport=httpx.MockTransport(handler))

    assert raised.value.code == expected_code
    assert raised.value.status_code == 200
    assert "private" not in str(raised.value)
    assert "fixture-token" not in str(raised.value)
    assert len(requests) == 1


def test_local_limits_are_explicit() -> None:
    assert REQUEST_TIMEOUT_SECONDS == 20
    assert MAX_RESPONSE_BYTES == 1024 * 1024
