"""Behavioral-parity tests for the SecureClient's SHARED CLOB reads.

The premise: the reads ``SecureClient`` shares with ``PublicClient`` are not a
second implementation — ``SecureClient`` composes a ``PublicClient`` internally
and delegates every shared read to it. This suite proves that delegation is
behaviourally exact in TWO directions, for the same logical order book:

  * **SecureClient read == PublicClient read** — feed an identical ``/v1/book``
    to both mirror clients and assert they return the same ``Decimal`` / model
    answers. (If SecureClient ever grew a divergent read copy, this fails.)
  * **SecureClient read == real py-sdk Secure/PublicClient read** — feed the
    same logical book to the mirror SecureClient and the REAL py-sdk and assert
    identical scalar answers (skipped if ``polymarket`` isn't installed).

Together with the existing ``test_polymarket_behavioral_parity`` suite (mirror
PublicClient == real py-sdk PublicClient), this closes the chain:
``SecureClient == PublicClient == real py-sdk``.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

MIRROR_HOST = "https://securemirror.parity.test"
REAL_HOST = "https://securereal.parity.test"
API_KEY = "ps_live_paritykey"
TOKEN = "711"

# One shared multi-level book. py-sdk OrderBook ordering: bids ASCENDING (best =
# last), asks DESCENDING (best = last). best bid = 0.45, best ask = 0.55.
BIDS = [
    {"price": "0.40", "size": "100"},
    {"price": "0.43", "size": "50"},
    {"price": "0.45", "size": "20"},
]
ASKS = [
    {"price": "0.65", "size": "100"},
    {"price": "0.60", "size": "50"},
    {"price": "0.55", "size": "20"},
]
TICK = "0.01"

MID = Decimal("0.50")  # (0.45 + 0.55) / 2
SPREAD = Decimal("0.10")  # 0.55 - 0.45


def _book_payload() -> dict:
    return {
        "market": "0xcond",
        "asset_id": TOKEN,
        "bids": BIDS,
        "asks": ASKS,
        "min_order_size": "1",
        "tick_size": TICK,
        "neg_risk": False,
        "hash": "0xhash",
    }


# ── mirror SecureClient read == mirror PublicClient read ────────────────────


@pytest.fixture
def secure_client():
    from polysim_polymarket import SecureClient

    c = SecureClient(host=MIRROR_HOST, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


@pytest.fixture
def public_client():
    from polysim_polymarket import PublicClient

    c = PublicClient(host=MIRROR_HOST, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


def _mock_mirror_book(respx_mock):
    respx_mock.get(f"{MIRROR_HOST}/v1/book").mock(
        return_value=httpx.Response(200, json=_book_payload())
    )


def test_secure_midpoint_equals_public(secure_client, public_client, respx_mock):
    _mock_mirror_book(respx_mock)
    assert secure_client.get_midpoint(token_id=TOKEN) == public_client.get_midpoint(
        token_id=TOKEN
    ) == MID


def test_secure_spread_equals_public(secure_client, public_client, respx_mock):
    _mock_mirror_book(respx_mock)
    assert secure_client.get_spread(token_id=TOKEN) == public_client.get_spread(
        token_id=TOKEN
    ) == SPREAD


def test_secure_price_buy_equals_public(secure_client, public_client, respx_mock):
    _mock_mirror_book(respx_mock)
    # BUY -> best ask (0.55)
    s = secure_client.get_price(token_id=TOKEN, side="BUY")
    p = public_client.get_price(token_id=TOKEN, side="BUY")
    assert s == p == Decimal("0.55")


def test_secure_price_sell_equals_public(secure_client, public_client, respx_mock):
    _mock_mirror_book(respx_mock)
    # SELL -> best bid (0.45)
    s = secure_client.get_price(token_id=TOKEN, side="SELL")
    p = public_client.get_price(token_id=TOKEN, side="SELL")
    assert s == p == Decimal("0.45")


def test_secure_estimate_market_price_equals_public(secure_client, public_client, respx_mock):
    _mock_mirror_book(respx_mock)
    # BUY 20 USD walks the asks cheapest-first. First ask level (0.55, size 20)
    # yields 0.55 * 20 = 11 notional < 20, so it walks to the next level
    # (0.60, size 50): cumulative 11 + 30 = 41 >= 20 -> marginal price 0.60.
    s = secure_client.estimate_market_price(token_id=TOKEN, side="BUY", amount="20")
    p = public_client.estimate_market_price(token_id=TOKEN, side="BUY", amount="20")
    assert s == p == Decimal("0.60")


def test_secure_order_book_equals_public(secure_client, public_client, respx_mock):
    _mock_mirror_book(respx_mock)
    sb = secure_client.get_order_book(token_id=TOKEN)
    pb = public_client.get_order_book(token_id=TOKEN)
    # Same model, same best levels, same ordering contract.
    assert sb == pb
    assert sb.bids[-1].price == Decimal("0.45")  # best bid
    assert sb.asks[-1].price == Decimal("0.55")  # best ask


def test_secure_side_validation_matches_public(secure_client, public_client, respx_mock):
    from polysim_polymarket import UserInputError

    # No route registered: the case-sensitive side guard must fire before any read
    # on BOTH clients, identically.
    with pytest.raises(UserInputError):
        secure_client.get_price(token_id=TOKEN, side="buy")  # type: ignore[arg-type]
    with pytest.raises(UserInputError):
        public_client.get_price(token_id=TOKEN, side="buy")  # type: ignore[arg-type]


# The singular scalar reads SecureClient delegates to its internal PublicClient.
# ``get_price`` also takes ``side``; a valid side is supplied so the empty-token
# guard (not the side guard) is what's exercised.
_SECURE_SINGULAR_EMPTY_TOKEN_READS = [
    ("get_midpoint", {}),
    ("get_spread", {}),
    ("get_price", {"side": "BUY"}),
    ("get_last_trade_price", {}),
]


@pytest.mark.parametrize("method, extra", _SECURE_SINGULAR_EMPTY_TOKEN_READS)
def test_secure_empty_token_guard_carries_through_delegation(
    secure_client, public_client, method, extra
):
    """The empty-token guard carries through ``SecureClient`` -> ``PublicClient``.

    ``SecureClient`` composes a ``PublicClient`` and delegates these reads; the
    guard lives on ``PublicClient``, so it must fire on the SecureClient call too
    — before any read (no respx route registered), identically to the public
    client, with py-sdk's exact message.
    """
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError) as secure_exc:
        getattr(secure_client, method)(token_id="", **extra)
    with pytest.raises(UserInputError) as public_exc:
        getattr(public_client, method)(token_id="", **extra)
    assert (
        str(secure_exc.value) == str(public_exc.value) == "token_id is required"
    )


# ── mirror SecureClient read == REAL py-sdk read ────────────────────────────

polymarket = pytest.importorskip("polymarket")

from polymarket import PRODUCTION as REAL_PRODUCTION  # noqa: E402
from polymarket.clients.public import PublicClient as RealPublicClient  # noqa: E402


@pytest.fixture
def real_public_client():
    env = replace(
        REAL_PRODUCTION,
        clob_url=REAL_HOST,
        gamma_url=REAL_HOST,
        data_url=REAL_HOST,
        rfq_url=REAL_HOST,
    )
    c = RealPublicClient(environment=env)
    yield c
    c.close()


def _mock_real_endpoints(respx_mock):
    respx_mock.get(f"{REAL_HOST}/book").mock(
        return_value=httpx.Response(200, json=_book_payload())
    )
    respx_mock.get(f"{REAL_HOST}/midpoint").mock(
        return_value=httpx.Response(200, json={"mid": str(MID)})
    )
    respx_mock.get(f"{REAL_HOST}/spread").mock(
        return_value=httpx.Response(200, json={"spread": str(SPREAD)})
    )
    respx_mock.get(f"{REAL_HOST}/tick-size").mock(
        return_value=httpx.Response(200, json={"minimum_tick_size": TICK})
    )


def test_secure_midpoint_equals_real_pysdk(secure_client, real_public_client, respx_mock):
    """The mirror SecureClient's midpoint == the REAL py-sdk's midpoint."""
    _mock_mirror_book(respx_mock)
    _mock_real_endpoints(respx_mock)
    mirror = secure_client.get_midpoint(token_id=TOKEN)
    real = real_public_client.get_midpoint(token_id=TOKEN)
    assert mirror == real == MID


def test_secure_spread_equals_real_pysdk(secure_client, real_public_client, respx_mock):
    _mock_mirror_book(respx_mock)
    _mock_real_endpoints(respx_mock)
    mirror = secure_client.get_spread(token_id=TOKEN)
    real = real_public_client.get_spread(token_id=TOKEN)
    assert mirror == real == SPREAD


def test_secure_price_buy_equals_real_pysdk(secure_client, real_public_client, respx_mock):
    _mock_mirror_book(respx_mock)
    _mock_real_endpoints(respx_mock)
    respx_mock.get(f"{REAL_HOST}/price").mock(
        return_value=httpx.Response(200, json={"price": "0.55"})
    )
    mirror = secure_client.get_price(token_id=TOKEN, side="BUY")
    real = real_public_client.get_price(token_id=TOKEN, side="BUY")
    assert mirror == real == Decimal("0.55")
