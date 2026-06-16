"""Reliability: retry on transient codes, 429 Retry-After, errors, floor pacing.

``time.sleep`` is monkeypatched to a recorder so back-off is exercised without
real waiting and so we can assert the sleep durations.
"""

from __future__ import annotations

import httpx
import pytest

import polysim_sdk._http as http_core
from polysim_sdk import PolySimClient
from polysim_sdk.exceptions import ApiError, RateLimitError, ValidationError

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"


@pytest.fixture
def sleeps(monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(http_core.time, "sleep", lambda s: recorded.append(s))
    return recorded


def _client(max_retries=3, floor=0.0):
    return PolySimClient(
        api_key=API_KEY, base_url=BASE_URL, max_retries=max_retries, floor_interval=floor
    )


def test_503_then_200_is_retried(sleeps, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"id": "ok"})]
    )
    c = _client()
    assert c.me()["id"] == "ok"
    assert len(sleeps) == 1  # one back-off between the two attempts
    c.close()


def test_425_is_retried(sleeps, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(
        side_effect=[httpx.Response(425), httpx.Response(200, json={"id": "ok"})]
    )
    c = _client()
    assert c.me()["id"] == "ok"
    c.close()


def test_429_honours_retry_after_then_succeeds(sleeps, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"id": "ok"}),
        ]
    )
    c = _client()
    assert c.me()["id"] == "ok"
    assert 2.0 in sleeps  # honoured Retry-After
    c.close()


def test_429_exhaustion_raises_rate_limit(sleeps, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(
        return_value=httpx.Response(
            429, headers={"Retry-After": "1"}, json={"message": "slow down"}
        )
    )
    c = _client(max_retries=2)
    with pytest.raises(RateLimitError) as exc:
        c.me()
    assert exc.value.retry_after == 1.0
    assert exc.value.code == "RATE_LIMIT_EXCEEDED"
    c.close()


def test_transient_exhaustion_raises_apierror(sleeps, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(return_value=httpx.Response(503))
    c = _client(max_retries=1)
    with pytest.raises(ApiError) as exc:
        c.me()
    assert exc.value.code == "RETRIES_EXHAUSTED"
    c.close()


def test_transient_exhaustion_preserves_status_code(sleeps, respx_mock):
    # When transient retries are exhausted the caller must still learn the real
    # upstream status (503 here), not an opaque status_code=0 — so code that
    # branches on ``err.status_code`` keeps working after exhaustion.
    respx_mock.get(f"{BASE_URL}/v1/me").mock(return_value=httpx.Response(503))
    c = _client(max_retries=1)
    with pytest.raises(ApiError) as exc:
        c.me()
    assert exc.value.code == "RETRIES_EXHAUSTED"
    assert exc.value.status_code == 503
    c.close()


def test_500_is_not_retried(sleeps, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/me").mock(
        return_value=httpx.Response(500, json={"message": "boom"})
    )
    c = _client()
    with pytest.raises(ApiError) as exc:
        c.me()
    assert exc.value.status_code == 500
    assert route.call_count == 1  # 500 is terminal, not transient
    assert sleeps == []
    c.close()


def test_422_raises_validation_error(sleeps, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(
        return_value=httpx.Response(422, json={"detail": "nope"})
    )
    c = _client()
    with pytest.raises(ValidationError):
        c.me()
    c.close()


def test_request_id_captured(sleeps, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(
        return_value=httpx.Response(500, headers={"x-request-id": "req-123"}, json={"message": "x"})
    )
    c = _client()
    with pytest.raises(ApiError) as exc:
        c.me()
    assert exc.value.request_id == "req-123"
    c.close()


def test_floor_pacing_sleeps_between_requests(sleeps, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(return_value=httpx.Response(200, json={"id": "ok"}))
    c = _client(floor=0.05)
    c.me()
    c.me()  # second back-to-back call must pace
    assert any(0 < s <= 0.05 for s in sleeps)
    c.close()


def test_network_error_after_retries_raises(sleeps, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(side_effect=httpx.ConnectError("down"))
    c = _client(max_retries=2)
    with pytest.raises(ApiError) as exc:
        c.me()
    assert exc.value.code == "NETWORK_ERROR"
    c.close()
