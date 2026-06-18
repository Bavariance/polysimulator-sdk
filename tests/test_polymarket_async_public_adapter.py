"""Per-method async adapter tests for ``AsyncPublicClient`` (respx-mocked).

The async twin of ``test_polymarket_public_adapter``. Each test drives one
``await``\\ed method against a respx-mocked PolySim ``/v1/...`` endpoint and
asserts the same adapted result the sync client produces — proving the async
client routes, reads, and adapts identically (it shares the pure logic via
``_common``; only the HTTP read is awaited).

All respx-mocked: no real network, no credentials.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from polysim_polymarket import AsyncPublicClient
from polysim_polymarket.models import (
    LastTradePrice,
    LastTradePriceForToken,
    Market,
    OrderBook,
    PriceHistoryPoint,
    PriceRequest,
)
from polysim_polymarket.pagination import AsyncPaginator

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"


@pytest.fixture
async def aclient():
    c = AsyncPublicClient(host=BASE_URL, api_key=API_KEY)
    # Keep the suite fast — drop the inter-request pacing floor.
    c._client._transport._floor_interval = 0.0
    yield c
    await c.close()


def _book_route(respx_mock, **extra):
    payload = {
        "market": "0xcond",
        "asset_id": "711",
        "bids": [{"price": "0.40", "size": "100"}],
        "asks": [{"price": "0.60", "size": "50"}],
        "tick_size": "0.01",
        "neg_risk": False,
        **extra,
    }
    return respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(200, json=payload)
    )


# ── lifecycle ──────────────────────────────────────────────────────────────


async def test_async_client_constructs_and_threads_api_key():
    c = AsyncPublicClient(host=BASE_URL, api_key=API_KEY)
    try:
        assert c._client._api_key == API_KEY
        assert c._client.base_url == BASE_URL
        assert c.environment.clob_url.startswith("https://")
    finally:
        await c.close()


async def test_async_client_is_async_context_manager(respx_mock):
    _book_route(respx_mock)
    async with AsyncPublicClient(host=BASE_URL, api_key=API_KEY) as c:
        c._client._transport._floor_interval = 0.0
        book = await c.get_order_book(token_id="711")
        assert isinstance(book, OrderBook)


async def test_async_client_aclose_alias():
    c = AsyncPublicClient(host=BASE_URL, api_key=API_KEY)
    # aclose is the convenience alias; must close cleanly.
    await c.aclose()


async def test_async_client_accepts_onchain_kwargs_without_typeerror():
    c = AsyncPublicClient(
        host=BASE_URL,
        api_key=API_KEY,
        chain_id=137,
        signature_type=2,
        funder="0xFunder",
        private_key="0xdead",
        logger=None,
    )
    await c.close()


async def test_async_client_routes_from_environment_clob_url_when_host_omitted():
    from dataclasses import replace

    from polysim_polymarket import PRODUCTION

    custom = replace(PRODUCTION, clob_url="https://clob.custom.test")
    c = AsyncPublicClient(custom, api_key=API_KEY)
    try:
        assert c._client.base_url == "https://clob.custom.test"
        assert c.environment.clob_url == "https://clob.custom.test"
    finally:
        await c.close()


# ── order book ─────────────────────────────────────────────────────────────


async def test_get_order_book_delegates_to_token_book(aclient, respx_mock):
    route = _book_route(respx_mock)
    book = await aclient.get_order_book(token_id="711")
    assert isinstance(book, OrderBook)
    assert dict(route.calls.last.request.url.params)["token_id"] == "711"
    assert book.bids[0].price == Decimal("0.40")
    assert book.asks[0].price == Decimal("0.60")
    assert book.token_id == "711"
    assert book.market == "0xcond"


async def test_get_order_book_colon_form_routes_to_condition(aclient, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets/c1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "c1",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
            },
        )
    )
    book = await aclient.get_order_book(token_id="c1:NO")
    assert dict(route.calls.last.request.url.params)["outcome"] == "NO"
    assert book.token_id == "c1:NO"


async def test_get_order_book_normalises_level_ordering(aclient, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [
                    {"price": "0.45", "size": "10"},
                    {"price": "0.40", "size": "20"},
                    {"price": "0.43", "size": "30"},
                ],
                "asks": [
                    {"price": "0.55", "size": "10"},
                    {"price": "0.60", "size": "20"},
                    {"price": "0.57", "size": "30"},
                ],
            },
        )
    )
    book = await aclient.get_order_book(token_id="711")
    # bids ascending (best last), asks descending (best last) — py-sdk contract.
    assert book.bids[-1].price == Decimal("0.45")
    assert book.asks[-1].price == Decimal("0.55")


async def test_get_order_books_returns_tuple_per_token(aclient, respx_mock):
    _book_route(respx_mock)
    books = await aclient.get_order_books(token_ids=["711", "712"])
    assert isinstance(books, tuple)
    assert len(books) == 2
    assert all(isinstance(b, OrderBook) for b in books)
    assert books[0].token_id == "711"
    assert books[1].token_id == "712"


# ── midpoints / prices / spreads ───────────────────────────────────────────


async def test_get_midpoint_returns_decimal(aclient, respx_mock):
    _book_route(respx_mock)
    mid = await aclient.get_midpoint(token_id="711")
    assert isinstance(mid, Decimal)
    assert mid == Decimal("0.5000")


async def test_get_midpoints_keyed_by_token(aclient, respx_mock):
    _book_route(respx_mock)
    mids = await aclient.get_midpoints(token_ids=["711", "712"])
    assert set(mids) == {"711", "712"}
    assert mids["711"] == Decimal("0.5000")


async def test_get_price_buy_is_best_ask_sell_is_best_bid(aclient, respx_mock):
    _book_route(respx_mock)
    assert await aclient.get_price(token_id="711", side="BUY") == Decimal("0.6000")
    assert await aclient.get_price(token_id="711", side="SELL") == Decimal("0.4000")


async def test_get_prices_returns_nested_dict(aclient, respx_mock):
    _book_route(respx_mock)
    prices = await aclient.get_prices(
        requests=[
            PriceRequest(token_id="711", side="BUY"),
            PriceRequest(token_id="711", side="SELL"),
        ]
    )
    assert prices["711"]["BUY"] == Decimal("0.6000")
    assert prices["711"]["SELL"] == Decimal("0.4000")


async def test_get_spread_returns_decimal(aclient, respx_mock):
    _book_route(respx_mock)
    assert await aclient.get_spread(token_id="711") == Decimal("0.2000")


async def test_get_spreads_keyed_by_token(aclient, respx_mock):
    _book_route(respx_mock)
    spreads = await aclient.get_spreads(token_ids=["711", "712"])
    assert set(spreads) == {"711", "712"}
    assert spreads["711"] == Decimal("0.2000")


# ── last trade price ───────────────────────────────────────────────────────


async def test_get_last_trade_price_returns_model(aclient, respx_mock):
    _book_route(respx_mock, last_trade_price="0.55")
    ltp = await aclient.get_last_trade_price(token_id="711")
    assert isinstance(ltp, LastTradePrice)
    assert ltp.price == Decimal("0.55")
    assert ltp.side in ("BUY", "SELL")


async def test_get_last_trade_prices_returns_tuple_of_for_token(aclient, respx_mock):
    _book_route(respx_mock, last_trade_price="0.55")
    out = await aclient.get_last_trade_prices(token_ids=["711", "712"])
    assert isinstance(out, tuple)
    assert len(out) == 2
    assert all(isinstance(x, LastTradePriceForToken) for x in out)
    assert out[0].token_id == "711"
    assert out[0].price == Decimal("0.55")


# ── price history ──────────────────────────────────────────────────────────


async def test_get_price_history_returns_bare_tuple(aclient, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(
            200,
            json={"history": [{"t": 1718600000, "p": 0.55}, {"t": 1718600060, "p": 0.56}]},
        )
    )
    out = await aclient.get_price_history(token_id="711")
    assert isinstance(out, tuple)
    assert all(isinstance(p, PriceHistoryPoint) for p in out)
    assert out[0].t == 1718600000
    assert out[1].p == 0.56
    assert dict(route.calls.last.request.url.params)["market"] == "711"


async def test_get_price_history_forwards_pm_params(aclient, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(200, json={"history": []})
    )
    await aclient.get_price_history(
        token_id="711", start_ts=1718600000, end_ts=1718700000, fidelity=5, interval="1h"
    )
    params = dict(route.calls.last.request.url.params)
    assert params["startTs"] == "1718600000"
    assert params["endTs"] == "1718700000"
    assert params["fidelity"] == "5"
    assert params["interval"] == "1h"


async def test_get_price_history_malformed_response_raises(aclient, respx_mock):
    from polysim_polymarket.errors import UnexpectedResponseError

    respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(200, json={"not_history": []})
    )
    with pytest.raises(UnexpectedResponseError):
        await aclient.get_price_history(token_id="711")


# ── estimate_market_price ──────────────────────────────────────────────────


async def test_estimate_market_price_buy_marginal_crosses_levels(aclient, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.50", "size": "10"}, {"price": "0.60", "size": "100"}],
            },
        )
    )
    # $20 notional exhausts 0.50 ($5) and touches 0.60 -> marginal 0.60 (NOT VWAP).
    px = await aclient.estimate_market_price(token_id="711", side="BUY", amount=20)
    assert px == Decimal("0.60")


async def test_estimate_market_price_fok_underfill_raises(aclient, respx_mock):
    from polysim_polymarket.errors import InsufficientLiquidityError

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "10"}, {"price": "0.80", "size": "30"}],
            },
        )
    )
    with pytest.raises(InsufficientLiquidityError):
        await aclient.estimate_market_price(token_id="711", side="BUY", amount=50)


async def test_estimate_market_price_fak_underfill_returns_worst_level(aclient, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "10"}, {"price": "0.80", "size": "30"}],
            },
        )
    )
    px = await aclient.estimate_market_price(
        token_id="711", side="BUY", amount=50, order_type="FAK"
    )
    assert px == Decimal("0.80")


async def test_estimate_market_price_validation_fires_before_read(aclient):
    from polysim_polymarket.errors import UserInputError

    # No respx route registered: if a read were attempted, it would error
    # differently. The validation guard must fire first.
    with pytest.raises(UserInputError):
        await aclient.estimate_market_price(token_id="711", side="BUY", shares=10)


# ── markets ────────────────────────────────────────────────────────────────


async def test_get_market_by_id(aclient, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/markets/0xcond").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "condition_id": "0xcond",
                "question": "Will it rain?",
                "slug": "will-it-rain",
                "active": True,
                "closed": False,
                "neg_risk": False,
            },
        )
    )
    market = await aclient.get_market(id="0xcond")
    assert isinstance(market, Market)
    assert market.condition_id == "0xcond"
    assert market.state.active is True
    assert market.state.closed is False


async def test_get_market_by_slug(aclient, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets/by-slug/will-it-rain").mock(
        return_value=httpx.Response(
            200, json={"id": "m1", "condition_id": "0xcond", "slug": "will-it-rain"}
        )
    )
    market = await aclient.get_market(slug="will-it-rain")
    assert market.slug == "will-it-rain"
    assert route.called


# ── list_markets (AsyncPaginator) ──────────────────────────────────────────


async def test_list_markets_returns_async_paginator(aclient, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/markets").mock(
        return_value=httpx.Response(
            200,
            json={
                "markets": [
                    {"id": "m1", "condition_id": "0xc1", "active": True, "closed": False},
                    {"id": "m2", "condition_id": "0xc2", "active": False, "closed": True},
                ]
            },
        )
    )
    pag = aclient.list_markets(closed=False)
    # list_markets is synchronous — it returns the paginator with no await.
    assert isinstance(pag, AsyncPaginator)
    page = await pag.first_page()
    assert all(isinstance(m, Market) for m in page.items)
    assert page.items[0].condition_id == "0xc1"
    assert page.items[1].state.closed is True


async def test_list_markets_iter_items_adapts_each(aclient, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/markets").mock(
        return_value=httpx.Response(
            200, json={"markets": [{"id": "m1", "condition_id": "0xc1"}]}
        )
    )
    items = [m async for m in aclient.list_markets().iter_items()]
    assert len(items) == 1
    assert isinstance(items[0], Market)
    assert items[0].condition_id == "0xc1"


async def test_list_markets_forwards_closed_filter(aclient, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets").mock(
        return_value=httpx.Response(200, json={"markets": []})
    )
    await aclient.list_markets(closed=True).first_page()
    assert dict(route.calls.last.request.url.params).get("closed") == "true"
