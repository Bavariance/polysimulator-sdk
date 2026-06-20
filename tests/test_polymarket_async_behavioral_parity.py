"""Async behavioral-parity: feed an IDENTICAL book to the mirror's
``AsyncPublicClient`` and the REAL py-sdk ``AsyncPublicClient`` and assert they
compute the same ``Decimal`` answers.

The async twin of ``test_polymarket_behavioral_parity``. The surface-parity suite
proves the async *call signatures* match py-sdk; this suite proves the
*behaviour* matches: for the same underlying order book, the mirror's awaited
``get_price`` / ``get_midpoint`` / ``get_spread`` / ``estimate_market_price``
equal what the real ``polymarket-client`` ``AsyncPublicClient`` returns.

The two clients read different endpoints (the real py-sdk hits server-computed
``/midpoint`` / ``/price`` / ``/spread`` + ``/book`` + ``/tick-size``; the mirror
computes everything from PolySim's ``/v1/book``). We respx-mock *both* endpoint
shapes from the *same* logical book, so any divergence in the mirror's
computation fails here. Skipped if the real ``polymarket`` package isn't
installed.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polysim_polymarket import AsyncPublicClient as MirrorAsyncPublicClient

# The real ``polymarket`` import-skip lives INSIDE the real_client fixture (not
# at module scope) so a missing real py-sdk skips only the tests that compare
# against it — the mirror fixture / mirror-only assertions never depend on the
# real package being installed.

MIRROR_HOST = "https://mirror.async.parity.test"
REAL_HOST = "https://realpysdk.async.parity.test"
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

BEST_BID = Decimal("0.45")
BEST_ASK = Decimal("0.55")
MID = Decimal("0.50")
SPREAD = Decimal("0.10")


@pytest.fixture
async def mirror_client():
    c = MirrorAsyncPublicClient(host=MIRROR_HOST, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    await c.close()


@pytest.fixture
async def real_client():
    pytest.importorskip("polymarket")
    from polymarket import PRODUCTION as REAL_PRODUCTION
    from polymarket.clients.async_public import AsyncPublicClient as RealAsyncPublicClient

    env = replace(
        REAL_PRODUCTION,
        clob_url=REAL_HOST,
        gamma_url=REAL_HOST,
        data_url=REAL_HOST,
        rfq_url=REAL_HOST,
    )
    c = RealAsyncPublicClient(environment=env)
    yield c
    await c.close()


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


def _mock_both(respx_mock, *, price_side: str | None = None, price_value: str | None = None):
    """Mock the mirror's /v1/book and the real py-sdk's server endpoints from the
    SAME book, so the two async clients see one identical book."""
    respx_mock.get(f"{MIRROR_HOST}/v1/book").mock(
        return_value=httpx.Response(200, json=_book_payload())
    )
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
    if price_side is not None:
        respx_mock.get(f"{REAL_HOST}/price").mock(
            return_value=httpx.Response(200, json={"price": price_value})
        )


async def test_async_parity_get_midpoint(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    mirror = await mirror_client.get_midpoint(token_id=TOKEN)
    real = await real_client.get_midpoint(token_id=TOKEN)
    assert mirror == real == MID


async def test_async_parity_get_spread(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    mirror = await mirror_client.get_spread(token_id=TOKEN)
    real = await real_client.get_spread(token_id=TOKEN)
    assert mirror == real == SPREAD


async def test_async_parity_get_price_buy_is_best_ask(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock, price_side="BUY", price_value=str(BEST_ASK))
    mirror = await mirror_client.get_price(token_id=TOKEN, side="BUY")
    real = await real_client.get_price(token_id=TOKEN, side="BUY")
    assert mirror == real == BEST_ASK


async def test_async_parity_get_price_sell_is_best_bid(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock, price_side="SELL", price_value=str(BEST_BID))
    mirror = await mirror_client.get_price(token_id=TOKEN, side="SELL")
    real = await real_client.get_price(token_id=TOKEN, side="SELL")
    assert mirror == real == BEST_BID


async def test_async_parity_estimate_buy_marginal(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    # $20 notional: best ask 0.55 carries $11; the order touches 0.60 -> 0.60.
    mirror = await mirror_client.estimate_market_price(token_id=TOKEN, side="BUY", amount=20)
    real = await real_client.estimate_market_price(token_id=TOKEN, side="BUY", amount=20)
    assert mirror == real == Decimal("0.60")


async def test_async_parity_estimate_sell_marginal(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    # Sell 30 shares: 0.45 has 20, then 10 at 0.43 -> marginal 0.43.
    mirror = await mirror_client.estimate_market_price(token_id=TOKEN, side="SELL", shares=30)
    real = await real_client.estimate_market_price(token_id=TOKEN, side="SELL", shares=30)
    assert mirror == real == Decimal("0.43")


async def test_async_parity_estimate_buy_single_level(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    # $5 fills entirely at best ask 0.55 -> marginal 0.55 on both.
    mirror = await mirror_client.estimate_market_price(token_id=TOKEN, side="BUY", amount=5)
    real = await real_client.estimate_market_price(token_id=TOKEN, side="BUY", amount=5)
    assert mirror == real == Decimal("0.55")


async def test_parity_get_market_no_arg_raises_same_user_input_error(mirror_client, real_client):
    """No-arg async ``get_market`` raises py-sdk's EXACT ``UserInputError`` on both.

    The async mirror must reject a no-arg market lookup with the same exception
    *type* and *message* the real py-sdk's ``AsyncPublicClient`` raises — not a
    bare ``ValueError`` — so the sync + async mirrors and py-sdk all agree.
    """
    from polymarket.errors import UserInputError as RealUserInputError

    from polysim_polymarket.errors import UserInputError as MirrorUserInputError

    with pytest.raises(RealUserInputError) as real_exc:
        await real_client.get_market()
    with pytest.raises(MirrorUserInputError) as mirror_exc:
        await mirror_client.get_market()

    expected = "Provide exactly one of id, slug, or url for market lookup."
    assert str(real_exc.value) == expected
    assert str(mirror_exc.value) == expected
    assert type(real_exc.value).__name__ == type(mirror_exc.value).__name__


async def test_parity_get_market_both_id_and_slug_raises_same_error(mirror_client, real_client):
    """Providing BOTH id and slug raises py-sdk's EXACT ``UserInputError`` on both."""
    from polymarket.errors import UserInputError as RealUserInputError

    from polysim_polymarket.errors import UserInputError as MirrorUserInputError

    with pytest.raises(RealUserInputError) as real_exc:
        await real_client.get_market(id="0xcond", slug="will-it-rain")
    with pytest.raises(MirrorUserInputError) as mirror_exc:
        await mirror_client.get_market(id="0xcond", slug="will-it-rain")

    expected = "Provide exactly one of id, slug, or url for market lookup."
    assert str(real_exc.value) == expected
    assert str(mirror_exc.value) == expected


async def test_async_mirror_matches_sync_mirror(mirror_client, respx_mock):
    """The async mirror computes the SAME Decimals as the sync mirror would.

    This is the DRY guarantee made observable: both clients call the identical
    ``_common`` pure logic, so feeding the same book must yield identical results
    for midpoint / spread / price / estimate.
    """
    from polysim_polymarket import PublicClient

    respx_mock.get(f"{MIRROR_HOST}/v1/book").mock(
        return_value=httpx.Response(200, json=_book_payload())
    )
    sync = PublicClient(host=MIRROR_HOST, api_key=API_KEY)
    sync._client._transport._floor_interval = 0.0
    try:
        assert await mirror_client.get_midpoint(token_id=TOKEN) == sync.get_midpoint(
            token_id=TOKEN
        )
        assert await mirror_client.get_spread(token_id=TOKEN) == sync.get_spread(token_id=TOKEN)
        assert await mirror_client.get_price(
            token_id=TOKEN, side="BUY"
        ) == sync.get_price(token_id=TOKEN, side="BUY")
        assert await mirror_client.estimate_market_price(
            token_id=TOKEN, side="BUY", amount=20
        ) == sync.estimate_market_price(token_id=TOKEN, side="BUY", amount=20)
    finally:
        sync.close()
