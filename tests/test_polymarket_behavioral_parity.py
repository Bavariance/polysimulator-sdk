"""Behavioral-parity tests: feed an IDENTICAL book to the mirror and the REAL
py-sdk ``PublicClient`` and assert they compute the same ``Decimal`` answers.

The surface-parity suite (``test_polymarket_public_parity``) proves the *call
signatures* match py-sdk. This suite proves the *behaviour* matches: for the
same underlying order book, the mirror's ``get_price`` / ``get_midpoint`` /
``get_spread`` / ``estimate_market_price`` return values equal to what the real
``polymarket-client`` ``PublicClient`` returns.

The two clients read different endpoints (the real py-sdk hits server-computed
``/midpoint`` / ``/price`` / ``/spread`` + ``/book`` + ``/tick-size``; the mirror
computes everything from PolySim's ``/v1/book``). We respx-mock *both* endpoint
shapes from the *same* logical book, so any divergence in the mirror's
computation (price direction, midpoint, spread, the marginal-price walk) fails
here. Skipped if the real ``polymarket`` package isn't installed.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

polymarket = pytest.importorskip("polymarket")

from polymarket import PRODUCTION as REAL_PRODUCTION  # noqa: E402
from polymarket.clients.public import PublicClient as RealPublicClient  # noqa: E402

from polysim_polymarket import PublicClient as MirrorPublicClient  # noqa: E402

MIRROR_HOST = "https://mirror.parity.test"
REAL_HOST = "https://realpysdk.parity.test"
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
MID = Decimal("0.50")  # (0.45 + 0.55) / 2
SPREAD = Decimal("0.10")  # 0.55 - 0.45


@pytest.fixture
def mirror_client():
    c = MirrorPublicClient(host=MIRROR_HOST, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


@pytest.fixture
def real_client():
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
    """Mock the mirror's /v1/book and the real py-sdk's server endpoints.

    Every server-computed value the real py-sdk reads (/midpoint, /price,
    /spread, /tick-size) is derived from the SAME book the mirror computes from,
    so the two clients see one identical book.
    """
    # Mirror reads everything off /v1/book.
    respx_mock.get(f"{MIRROR_HOST}/v1/book").mock(
        return_value=httpx.Response(200, json=_book_payload())
    )
    # Real py-sdk endpoints (httpx → respx intercepts them).
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


def test_parity_get_midpoint(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    mirror = mirror_client.get_midpoint(token_id=TOKEN)
    real = real_client.get_midpoint(token_id=TOKEN)
    assert mirror == real == MID


def test_parity_get_spread(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    mirror = mirror_client.get_spread(token_id=TOKEN)
    real = real_client.get_spread(token_id=TOKEN)
    assert mirror == real == SPREAD


def test_parity_get_price_buy_is_best_ask(mirror_client, real_client, respx_mock):
    # py-sdk's /price for BUY returns the executable price = best ask.
    _mock_both(respx_mock, price_side="BUY", price_value=str(BEST_ASK))
    mirror = mirror_client.get_price(token_id=TOKEN, side="BUY")
    real = real_client.get_price(token_id=TOKEN, side="BUY")
    assert mirror == real == BEST_ASK


def test_parity_get_price_sell_is_best_bid(mirror_client, real_client, respx_mock):
    # py-sdk's /price for SELL returns the executable price = best bid.
    _mock_both(respx_mock, price_side="SELL", price_value=str(BEST_BID))
    mirror = mirror_client.get_price(token_id=TOKEN, side="SELL")
    real = real_client.get_price(token_id=TOKEN, side="SELL")
    assert mirror == real == BEST_BID


def test_parity_estimate_market_price_buy_marginal(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    # $20 notional: best ask 0.55 carries 0.55*20 = $11; the order must touch the
    # 0.60 level to complete, so the marginal price is 0.60 on BOTH clients.
    mirror = mirror_client.estimate_market_price(token_id=TOKEN, side="BUY", amount=20)
    real = real_client.estimate_market_price(token_id=TOKEN, side="BUY", amount=20)
    assert mirror == real == Decimal("0.60")


def test_parity_estimate_market_price_sell_marginal(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    # Sell 30 shares: best bid 0.45 has 20 shares, then 10 more at 0.43 -> the
    # marginal (worst-touched) price is 0.43 on BOTH clients.
    mirror = mirror_client.estimate_market_price(token_id=TOKEN, side="SELL", shares=30)
    real = real_client.estimate_market_price(token_id=TOKEN, side="SELL", shares=30)
    assert mirror == real == Decimal("0.43")


def test_parity_estimate_market_price_buy_single_level(mirror_client, real_client, respx_mock):
    _mock_both(respx_mock)
    # $5 notional fills entirely at the best ask 0.55 (0.55*20 = $11 >= $5) -> the
    # marginal price is 0.55 on both.
    mirror = mirror_client.estimate_market_price(token_id=TOKEN, side="BUY", amount=5)
    real = real_client.estimate_market_price(token_id=TOKEN, side="BUY", amount=5)
    assert mirror == real == Decimal("0.55")


# ── get_market lookup-arg guard (error type + message parity) ───────────────


def test_parity_get_market_no_arg_raises_same_user_input_error(mirror_client, real_client):
    """No-arg ``get_market`` raises py-sdk's EXACT ``UserInputError`` on both.

    The mirror must reject a no-arg market lookup with the same exception *type*
    and *message* the real py-sdk raises — not a bare ``ValueError`` — so a
    ported bot's ``except UserInputError`` catches it identically.
    """
    from polymarket.errors import UserInputError as RealUserInputError

    from polysim_polymarket.errors import UserInputError as MirrorUserInputError

    with pytest.raises(RealUserInputError) as real_exc:
        real_client.get_market()
    with pytest.raises(MirrorUserInputError) as mirror_exc:
        mirror_client.get_market()

    expected = "Provide exactly one of id, slug, or url for market lookup."
    assert str(real_exc.value) == expected
    assert str(mirror_exc.value) == expected
    # Same type identity: the mirror's UserInputError IS its named error class,
    # mirroring py-sdk's UserInputError (both subclass their PolyException).
    assert type(real_exc.value).__name__ == type(mirror_exc.value).__name__


def test_parity_get_market_both_id_and_slug_raises_same_error(mirror_client, real_client):
    """Providing BOTH id and slug raises py-sdk's EXACT ``UserInputError`` on both.

    py-sdk's market lookup demands *exactly one* of id/slug/url; supplying two
    is the same ``UserInputError`` as supplying none. The mirror matches.
    """
    from polymarket.errors import UserInputError as RealUserInputError

    from polysim_polymarket.errors import UserInputError as MirrorUserInputError

    with pytest.raises(RealUserInputError) as real_exc:
        real_client.get_market(id="0xcond", slug="will-it-rain")
    with pytest.raises(MirrorUserInputError) as mirror_exc:
        mirror_client.get_market(id="0xcond", slug="will-it-rain")

    expected = "Provide exactly one of id, slug, or url for market lookup."
    assert str(real_exc.value) == expected
    assert str(mirror_exc.value) == expected
