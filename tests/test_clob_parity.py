"""py-clob-client drop-in parity surface.

Covers (per spec §8.2):
  (a) import-shape assertions + constructor accepts py-clob kwargs;
  (b) each adapt method hits the right PolySim REST call;
  (c) each stub-noop returns its canned value and makes ZERO network calls;
  (d) create_order returns an UNSIGNED payload with no signature field.
"""

from __future__ import annotations

import httpx
import pytest

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"


@pytest.fixture
def clob():
    # Imports are inside the fixture so an import error surfaces as a clear
    # collection failure on the import-shape tests too.
    from polysim_clob_client.client import ClobClient

    c = ClobClient(host=BASE_URL, key=API_KEY)
    c._client._transport._floor_interval = 0.0  # keep the suite fast
    yield c
    c.close()


# ── (a) import shape + constructor drop-in ─────────────────────────────────


def test_import_shapes_resolve():
    from polysim_clob_client.client import ClobClient  # noqa: F401
    from polysim_clob_client.clob_types import (  # noqa: F401
        ApiCreds,
        MarketOrderArgs,
        OrderArgs,
    )
    from polysim_clob_client.constants import (  # noqa: F401
        AMOY,
        END_CURSOR,
        L0,
        L1,
        L2,
        POLYGON,
        START_CURSOR,
        ZERO_ADDRESS,
    )
    from polysim_clob_client.order_builder.constants import BUY, SELL

    assert BUY == "BUY" and SELL == "SELL"
    assert POLYGON == 137 and AMOY == 80002
    assert START_CURSOR == "MA==" and END_CURSOR == "LTE="


def test_constructor_accepts_all_pyclob_kwargs():
    from polysim_clob_client.client import ClobClient
    from polysim_clob_client.clob_types import ApiCreds
    from polysim_clob_client.constants import POLYGON

    # The full py-clob-client constructor shape must not TypeError.
    c = ClobClient(
        host=BASE_URL,
        chain_id=POLYGON,
        key="0xdeadbeefprivkeyslot",
        creds=ApiCreds(api_key="ps_live_fromcreds"),
        signature_type=2,
        funder="0xFunderAddress",
        builder_config={"x": 1},
        tick_size_ttl=120.0,
    )
    # creds.api_key wins over key= per the resolution order.
    assert c._client._api_key == "ps_live_fromcreds"
    c.close()


def test_api_key_resolution_order_key_slot():
    from polysim_clob_client.client import ClobClient

    c = ClobClient(host=BASE_URL, key="ps_live_fromkeyslot")
    assert c._client._api_key == "ps_live_fromkeyslot"
    c.close()


# ── (b) adapt methods hit the right REST call ──────────────────────────────


def test_get_address_hits_me(clob, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/me").mock(return_value=httpx.Response(200, json={"id": "u_42"}))
    assert clob.get_address() == "u_42"


def test_get_markets_paginates(clob, respx_mock):
    from polysim_clob_client.constants import END_CURSOR

    respx_mock.get(f"{BASE_URL}/v1/markets").mock(
        return_value=httpx.Response(200, json=[{"condition_id": "c1"}])
    )
    page = clob.get_markets()
    assert page["data"][0]["condition_id"] == "c1"
    # short page -> END cursor terminates the loop
    assert page["next_cursor"] == END_CURSOR
    assert page["count"] == 1


# F4 — true token-id parity: a BARE token id routes the whole read family to
# the token-native ``GET /v1/book?token_id=...`` endpoint (PM-compatible). The
# ``condition_id:YES``/``:NO`` colon form keeps condition-id routing (see the
# colon-form tests below). py-clob-client always passes a real outcome-token id,
# so the bare-token path is the primary parity path.


def test_get_midpoint_from_token_book(clob, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "100"}],
            },
        )
    )
    assert clob.get_midpoint("711")["mid"] == "0.5000"
    assert dict(route.calls.last.request.url.params)["token_id"] == "711"


def test_get_price_buy_is_best_bid(clob, respx_mock):
    # Polymarket convention (wire-authoritative): get_price(BUY) returns the
    # best BID, get_price(SELL) returns the best ASK.
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "bids": [{"price": "0.40", "size": "1"}],
                "asks": [{"price": "0.58", "size": "1"}, {"price": "0.62", "size": "1"}],
            },
        )
    )
    # BUY -> best bid
    assert clob.get_price("711", "BUY")["price"] == "0.4000"
    # SELL -> best ask (min of the asks)
    assert clob.get_price("711", "SELL")["price"] == "0.5800"


def test_get_spread(clob, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "bids": [{"price": "0.40", "size": "1"}],
                "asks": [{"price": "0.60", "size": "1"}],
            },
        )
    )
    assert clob.get_spread("711")["spread"] == "0.2000"


def test_get_order_book_returns_summary(clob, respx_mock):
    from polysim_clob_client.clob_types import OrderBookSummary

    route = respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
            },
        )
    )
    book = clob.get_order_book("711")
    assert isinstance(book, OrderBookSummary)
    assert book.bids[0].price == "0.4"
    assert book.asks[0].size == "50.0"
    # asset_id echoes the token the caller asked for; market carries condition id.
    assert book.asset_id == "711"
    assert book.market == "0xcond"
    assert dict(route.calls.last.request.url.params)["token_id"] == "711"


def test_calculate_market_price_walks_book(clob, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "asks": [{"price": "0.50", "size": "10"}, {"price": "0.60", "size": "10"}],
                "bids": [],
            },
        )
    )
    # buy 15 shares -> 10@0.50 + 5@0.60 = 8.0 / 15 = 0.5333...
    px = clob.calculate_market_price("711", "BUY", 15)
    assert round(px, 4) == 0.5333


# ── colon-form (condition_id:OUTCOME) keeps condition-id routing ────────────


def test_get_order_book_colon_form_routes_to_condition(clob, respx_mock):
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
    book = clob.get_order_book("c1:NO")
    # The outcome rides through as a query param so :NO reads the NO book.
    assert dict(route.calls.last.request.url.params)["outcome"] == "NO"
    # asset_id echoes exactly what the caller passed (py-clob contract).
    assert book.asset_id == "c1:NO"


def test_get_midpoint_colon_form_routes_to_condition(clob, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets/c1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "bids": [{"price": "0.40", "size": "1"}],
                "asks": [{"price": "0.60", "size": "1"}],
            },
        )
    )
    assert clob.get_midpoint("c1:YES")["mid"] == "0.5000"
    assert dict(route.calls.last.request.url.params)["outcome"] == "YES"


# ── tick size / neg-risk / last-trade read off the token book ──────────────


def test_get_tick_size_reads_from_token_book(clob, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(200, json={"tick_size": "0.001", "bids": [], "asks": []})
    )
    assert clob.get_tick_size("711") == 0.001
    # Cached: a second call makes no further network request.
    assert clob.get_tick_size("711") == 0.001
    assert respx_mock.calls.call_count == 1


def test_get_neg_risk_reads_from_token_book(clob, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(200, json={"neg_risk": True, "bids": [], "asks": []})
    )
    assert clob.get_neg_risk("711") is True


def test_get_last_trade_price_from_token_book(clob, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200, json={"last_trade_price": "0.57", "bids": [], "asks": []}
        )
    )
    assert clob.get_last_trade_price("711")["price"] == "0.57"


def test_get_last_trade_price_falls_back_to_market(clob, respx_mock):
    # Book carries no last trade -> fall back to the market via its condition id.
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(200, json={"market": "0xcond", "bids": [], "asks": []})
    )
    respx_mock.get(f"{BASE_URL}/v1/markets/0xcond").mock(
        return_value=httpx.Response(200, json={"last_trade_price": "0.61"})
    )
    assert clob.get_last_trade_price("711")["price"] == "0.61"


def test_post_order_submits(clob, respx_mock):
    from polysim_clob_client.clob_types import OrderArgs

    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1", "status": "OPEN"})
    )
    order = clob.create_order(OrderArgs(token_id="c1", price=0.55, size=10, side="BUY"))
    resp = clob.post_order(order)
    assert resp["order_id"] == "o1"
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"outcome":"YES"' in body
    assert '"signature"' not in body  # never signed


def test_create_and_post_order(clob, respx_mock):
    from polysim_clob_client.clob_types import OrderArgs

    respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o9"})
    )
    resp = clob.create_and_post_order(OrderArgs(token_id="c1:NO", price=0.30, size=5, side="SELL"))
    assert resp["order_id"] == "o9"


def test_get_balance_allowance(clob, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/account/balance").mock(
        return_value=httpx.Response(200, json={"balance": "2500.00"})
    )
    out = clob.get_balance_allowance()
    assert out["balance"] == "2500.00"
    assert out["allowance"] == "unlimited"


def test_token_split_no_suffix_defaults_yes(clob):
    assert clob._split_token("c1") == ("c1", "YES")
    assert clob._split_token("c1:NO") == ("c1", "NO")
    assert clob._split_token("c1:yes") == ("c1", "YES")


# ── (c) stub-noops return canned values and make ZERO network calls ────────


def test_stub_noops_make_no_network_calls(clob, respx_mock):
    # respx_mock with no routes registered raises on ANY request, so reaching
    # the assertions at all proves these made zero network calls.
    assert clob.get_collateral_address().startswith("0x")
    assert clob.get_conditional_address().startswith("0x")
    assert clob.get_exchange_address().startswith("0x")
    assert clob.get_closed_only_mode() == {"closed_only": False}
    assert clob.get_notifications() == {"notifications": []}
    assert clob.drop_notifications()["success"] is True
    assert clob.update_balance_allowance() == {}
    assert clob.is_order_scoring()["scoring"] is False
    assert clob.are_orders_scoring() == {}
    assert clob.get_market_trades_events("c1") == {"data": []}
    assert clob.get_builder_trades() == []
    assert clob.post_heartbeat()["success"] is True
    assert clob.can_builder_auth() is False
    assert clob.get_fee_rate_bps("c1") == 0
    assert clob.get_server_time() > 0
    clob.assert_level_1_auth()  # no raise
    clob.assert_builder_auth()  # no raise
    clob.assert_level_2_auth()  # key present -> no raise
    assert respx_mock.calls.call_count == 0


def test_assert_level_2_auth_raises_without_key(respx_mock):
    from polysim_clob_client.client import ClobClient
    from polysim_clob_client.exceptions import PolyApiException

    c = ClobClient(host=BASE_URL, key="ps_live_x")
    c._client._api_key = ""  # simulate missing key
    with pytest.raises(PolyApiException):
        c.assert_level_2_auth()
    c.close()


# ── (d) create_order emits no signature ────────────────────────────────────


def test_create_order_is_unsigned(clob):
    from polysim_clob_client.clob_types import OrderArgs

    order = clob.create_order(OrderArgs(token_id="c1", price=0.55, size=10, side="BUY"))
    assert isinstance(order, dict)
    assert "signature" not in order
    assert "signedOrder" not in order
    assert order["market_id"] == "c1"
    assert order["outcome"] == "YES"
    assert order["side"] == "BUY"
    assert order["order_type"] == "limit"


def test_create_market_order_is_unsigned_fok(clob):
    from polysim_clob_client.clob_types import MarketOrderArgs

    order = clob.create_market_order(MarketOrderArgs(token_id="c1", amount=20, side="BUY"))
    assert "signature" not in order
    assert order["order_type"] == "market"
    assert order["time_in_force"] == "FOK"
    # default worst-price cap for a BUY
    assert order["price"] == 0.99
    # py-clob semantics: a market BUY amount is USD notional -> sent as `amount`,
    # NOT as a share `quantity`.
    assert order["amount"] == 20.0
    assert "quantity" not in order


def test_create_market_order_sell_uses_quantity(clob):
    from polysim_clob_client.clob_types import MarketOrderArgs

    # A market SELL amount is a share count -> sent as `quantity`, not `amount`.
    order = clob.create_market_order(MarketOrderArgs(token_id="c1", amount=15, side="SELL"))
    assert order["order_type"] == "market"
    assert order["quantity"] == 15.0
    assert "amount" not in order
    # default worst-price cap for a SELL
    assert order["price"] == 0.01


def test_pyapiexception_is_apierror_alias():
    from polysim_clob_client.exceptions import PolyApiException
    from polysim_sdk.exceptions import ApiError

    assert PolyApiException is ApiError


def test_polyexception_is_base_of_polyapiexception():
    # py-clob-client's tree: PolyException (base) -> PolyApiException.
    from polysim_clob_client.exceptions import PolyApiException, PolyException

    assert issubclass(PolyApiException, PolyException)
    # `except PolyException` catches an API error, matching py-clob semantics.
    err = PolyApiException(500, "boom")
    assert isinstance(err, PolyException)


# ── py-clob-client signature parity (real client accepts these arg shapes) ──


def test_get_readonly_api_keys_plural_exists(clob, respx_mock):
    # Real py-clob-client exposes the PLURAL name returning a list[str].
    assert clob.get_readonly_api_keys() == []
    assert respx_mock.calls.call_count == 0


def test_signature_parity_for_widened_stubs(clob, respx_mock):
    # A mechanical port calls these with py-clob-client's argument shapes; none
    # must TypeError, and none must touch the network.
    assert clob.get_exchange_address(neg_risk=True).startswith("0x")
    assert clob.get_exchange_address(neg_risk=True) != clob.get_exchange_address()
    assert clob.delete_readonly_api_key("0xkey")["success"] is True
    assert clob.validate_readonly_api_key("0xaddr", "0xkey") is True
    assert clob.get_builder_trades(None, next_cursor="MA==") == []
    clob.clear_tick_size_cache("c1")  # one-token form must not raise
    assert respx_mock.calls.call_count == 0


def test_clear_tick_size_cache_single_token(clob):
    clob._tick_sizes = {"c1": 0.01, "c2": 0.001}
    clob.clear_tick_size_cache("c1")
    assert clob._tick_sizes == {"c2": 0.001}
    clob.clear_tick_size_cache()  # no arg clears all
    assert clob._tick_sizes == {}


def test_create_market_order_accepts_options(clob):
    from polysim_clob_client.clob_types import MarketOrderArgs, PartialCreateOrderOptions

    # options= is accepted (ignored) for signature parity.
    order = clob.create_market_order(
        MarketOrderArgs(token_id="c1", amount=10, side="BUY"),
        options=PartialCreateOrderOptions(tick_size="0.01", neg_risk=False),
    )
    assert order["order_type"] == "market"
    assert "signature" not in order


def test_post_order_accepts_post_only(clob, respx_mock):
    from polysim_clob_client.clob_types import OrderArgs

    respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1"})
    )
    order = clob.create_order(OrderArgs(token_id="c1", price=0.55, size=10, side="BUY"))
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1"})
    )
    resp = clob.post_order(order, post_only=True)  # must not TypeError
    assert resp["order_id"] == "o1"
    # post_only is forwarded to the server as a body field.
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"post_only":true' in body


def test_create_order_gtd_carries_expiration(clob, respx_mock):
    from polysim_clob_client.clob_types import OrderArgs

    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "og"})
    )
    # A non-zero py-clob `expiration` builds a GTD order carrying that timestamp.
    order = clob.create_order(
        OrderArgs(token_id="c1", price=0.55, size=10, side="BUY", expiration=1700000000)
    )
    assert order["time_in_force"] == "GTD"
    assert order["expiration"] == 1700000000
    clob.post_order(order)
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"expiration":1700000000' in body
    assert '"time_in_force":"GTD"' in body


def test_cancel_market_orders_uses_market_query_param(clob, respx_mock):
    route = respx_mock.delete(f"{BASE_URL}/v1/cancel-market-orders").mock(
        return_value=httpx.Response(200, json={"canceled": 2})
    )
    clob.cancel_market_orders(market="c1")
    params = dict(route.calls.last.request.url.params)
    # Backend reads `market` / `asset_id` query params, NOT `market_id`.
    assert params == {"market": "c1"}


def test_post_orders_batch_sends_envelope(clob, respx_mock):
    from polysim_clob_client.clob_types import OrderType, PostOrdersArgs

    route = respx_mock.post(f"{BASE_URL}/v1/orders/batch").mock(
        return_value=httpx.Response(200, json={"orders": [{"order_id": "o1"}]})
    )
    clob.post_orders(
        [
            PostOrdersArgs(
                order={"market_id": "c1", "outcome": "YES", "side": "BUY",
                       "price": 0.5, "quantity": 10, "order_type": "limit"},
                orderType=OrderType.GTC,
                postOnly=True,
            )
        ]
    )
    import json

    body = json.loads(route.calls.last.request.read().decode())
    assert "orders" in body and isinstance(body["orders"], list)
    assert body["orders"][0]["post_only"] is True
    assert body["orders"][0]["time_in_force"] == "GTC"


# ── token resolution: long-numeric outcome-token ids reverse-resolve ────────

LONG_TOKEN = "7" * 40  # py-clob outcome-token ids are long all-digit strings


def test_resolve_token_short_and_colon_make_no_network(clob, respx_mock):
    # respx_mock with no routes raises on ANY request, so reaching the
    # assertions proves these resolutions are purely local (no markets-by-token).
    assert clob._resolve_token("c1") == ("c1", "YES")
    assert clob._resolve_token("c1:NO") == ("c1", "NO")
    assert clob._resolve_token("711") == ("711", "YES")  # short numeric, not a CLOB id
    assert respx_mock.calls.call_count == 0


def test_resolve_token_long_numeric_uses_markets_by_token(clob, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets-by-token/{LONG_TOKEN}").mock(
        return_value=httpx.Response(
            200,
            json={"condition_id": "0xcond", "primary_token_id": LONG_TOKEN, "outcome": "No"},
        )
    )
    assert clob._resolve_token(LONG_TOKEN) == ("0xcond", "No")
    # second call is served from cache — still exactly one network hit
    assert clob._resolve_token(LONG_TOKEN) == ("0xcond", "No")
    assert route.call_count == 1


def test_order_payload_resolves_long_numeric_token(clob, respx_mock):
    from polysim_clob_client.clob_types import OrderArgs

    respx_mock.get(f"{BASE_URL}/v1/markets-by-token/{LONG_TOKEN}").mock(
        return_value=httpx.Response(
            200,
            json={"condition_id": "0xcond", "primary_token_id": LONG_TOKEN, "outcome": "No"},
        )
    )
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1"})
    )
    order = clob.create_order(OrderArgs(token_id=LONG_TOKEN, price=0.55, size=10, side="BUY"))
    assert order["market_id"] == "0xcond"
    assert order["outcome"] == "No"
    clob.post_order(order)
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"market_id":"0xcond"' in body
    assert '"outcome":"No"' in body


# ── order / trade reads walk the Polymarket-shape /data cursor ──────────────


def test_get_orders_walks_data_orders_cursor(clob, respx_mock):
    from polysim_clob_client.clob_types import OpenOrderParams

    def _pages(request: httpx.Request) -> httpx.Response:
        cursor = dict(request.url.params).get("next_cursor")
        if cursor in (None, "MA=="):
            return httpx.Response(
                200, json={"limit": 100, "count": 1, "next_cursor": "MTAw",
                           "data": [{"id": "0xa", "market": "0xcond"}]}
            )
        return httpx.Response(
            200, json={"limit": 100, "count": 1, "next_cursor": "LTE=",
                       "data": [{"id": "0xb", "market": "0xcond"}]}
        )

    route = respx_mock.get(f"{BASE_URL}/v1/data/orders").mock(side_effect=_pages)
    out = clob.get_orders(OpenOrderParams(market="0xcond"))
    assert [r["id"] for r in out] == ["0xa", "0xb"]
    # server-side filter forwarded
    assert dict(route.calls[0].request.url.params)["market"] == "0xcond"
    # walked both pages then stopped at the LTE= sentinel
    assert route.call_count == 2


def test_get_trades_walks_data_trades_cursor(clob, respx_mock):
    from polysim_clob_client.clob_types import TradeParams

    def _pages(request: httpx.Request) -> httpx.Response:
        cursor = dict(request.url.params).get("next_cursor")
        if cursor in (None, "MA=="):
            return httpx.Response(
                200, json={"limit": 100, "count": 1, "next_cursor": "MTAw",
                           "data": [{"id": "0xt1"}]}
            )
        return httpx.Response(
            200, json={"limit": 100, "count": 1, "next_cursor": "LTE=", "data": [{"id": "0xt2"}]}
        )

    route = respx_mock.get(f"{BASE_URL}/v1/data/trades").mock(side_effect=_pages)
    out = clob.get_trades(TradeParams(market="0xcond", asset_id=LONG_TOKEN))
    assert [r["id"] for r in out] == ["0xt1", "0xt2"]
    params0 = dict(route.calls[0].request.url.params)
    assert params0["market"] == "0xcond"
    assert params0["asset_id"] == LONG_TOKEN
    assert route.call_count == 2
