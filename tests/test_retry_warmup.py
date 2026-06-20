"""``retry_on_market_warmup`` — retry a callable through a market's warm-up 404.

A brand-new UpDown market (especially the 5m windows that roll every few
minutes) has a short window after creation before it enters the order-validation
catalog, during which order placement transiently 404s with
``code="MARKET_NOT_FOUND"``. The SDK does not auto-retry 404s by design (a 404
is normally permanent), so this opt-in wrapper retries ONLY that specific
transient signal with capped backoff and re-raises everything else.
"""

from __future__ import annotations

import pytest

from polysim_sdk import ApiError, ValidationError, retry_on_market_warmup


def _market_not_found() -> ApiError:
    return ApiError(404, "Market not found: 0xCID", code="MARKET_NOT_FOUND")


def test_retries_warmup_404_then_returns_result():
    sleeps: list[float] = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _market_not_found()
        return {"order_id": "o1"}

    result = retry_on_market_warmup(
        fn, attempts=6, base_delay=2.0, sleep=sleeps.append
    )
    assert result == {"order_id": "o1"}
    # 2 failed attempts -> 2 backoff sleeps before the 3rd succeeds.
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_success_on_first_call_does_not_sleep():
    sleeps: list[float] = []
    result = retry_on_market_warmup(
        lambda: "ok", attempts=6, base_delay=2.0, sleep=sleeps.append
    )
    assert result == "ok"
    assert sleeps == []


def test_does_not_retry_non_404_error():
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValidationError(422, "bad price", code="INVALID_PRICE")

    with pytest.raises(ValidationError):
        retry_on_market_warmup(fn, attempts=6, base_delay=2.0, sleep=lambda _s: None)
    # Re-raised immediately, never retried.
    assert calls["n"] == 1


def test_does_not_retry_404_with_other_code():
    # A 404 that is NOT the warm-up signal (e.g. an unknown order id) is a real
    # permanent 404 and must NOT be retried.
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ApiError(404, "no such order", code="ORDER_NOT_FOUND")

    with pytest.raises(ApiError) as exc:
        retry_on_market_warmup(fn, attempts=6, base_delay=2.0, sleep=lambda _s: None)
    assert exc.value.code == "ORDER_NOT_FOUND"
    assert calls["n"] == 1


def test_reraises_404_after_budget_exhausted():
    sleeps: list[float] = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _market_not_found()

    with pytest.raises(ApiError) as exc:
        retry_on_market_warmup(fn, attempts=4, base_delay=2.0, sleep=sleeps.append)
    assert exc.value.status_code == 404
    assert exc.value.code == "MARKET_NOT_FOUND"
    # 4 attempts total -> 3 sleeps between them (none after the final failure).
    assert calls["n"] == 4
    assert len(sleeps) == 3


def test_backoff_is_exponential_and_capped():
    sleeps: list[float] = []
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _market_not_found()

    with pytest.raises(ApiError):
        retry_on_market_warmup(
            fn, attempts=6, base_delay=2.0, max_delay=10.0, sleep=sleeps.append
        )
    # base * 2**i: 2, 4, 8, then capped at 10, 10 (5 sleeps across 6 attempts).
    assert sleeps == [2.0, 4.0, 8.0, 10.0, 10.0]


def test_attempts_must_be_positive():
    with pytest.raises(ValueError):
        retry_on_market_warmup(lambda: "x", attempts=0)
