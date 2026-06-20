"""Write-path request-shape contract tests.

These pin the EXACT bytes the SDK puts on the wire for mutating endpoints whose
shape the backend strictly validates, so a mock-only green suite can never bless
a request the backend would reject.

The batch envelope, ``post_only`` forwarding, market-order amount/quantity
semantics, and ``cancel_market_orders`` query binding are already pinned in
``test_clob_parity.py`` / ``test_native_client.py``. The one mutating contract
not covered there is ``POST /v1/cancel-all``'s **confirmation guard**: the
backend (``_require_cancel_all_confirmation``, trading.py) 400s any cancel-all
that lacks ``?confirm=true`` OR the ``X-Confirm-Cancel-All: true`` header (the
P1-J footgun guard). A wrapper that omits both confirmation forms looks fine
against a permissive mock but always fails in production.
"""

from __future__ import annotations

import httpx

BASE_URL = "https://api.polysimulator.test"


def _confirms_cancel_all(request) -> bool:
    """A request confirms a cancel-all via EITHER accepted form."""
    header_ok = request.headers.get("X-Confirm-Cancel-All", "").lower() == "true"
    query_ok = dict(request.url.params).get("confirm", "").lower() == "true"
    return header_ok or query_ok


def test_cancel_all_sends_confirmation(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/cancel-all").mock(
        return_value=httpx.Response(200, json={"canceled": 3})
    )
    client.cancel_all()
    assert route.called
    assert _confirms_cancel_all(route.calls.last.request)


async def test_async_cancel_all_sends_confirmation(aclient, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/cancel-all").mock(
        return_value=httpx.Response(200, json={"canceled": 1})
    )
    await aclient.cancel_all()
    assert route.called
    assert _confirms_cancel_all(route.calls.last.request)


def test_get_order_forwards_real_query_params(client, respx_mock):
    # The backend honours ``source`` + ``wallet_id`` on GET /v1/orders/{id};
    # it has no ``market_id`` query param. The SDK must forward the real ones
    # and never the dead ``market_id`` (which the server silently ignores).
    route = respx_mock.get(f"{BASE_URL}/v1/orders/42").mock(
        return_value=httpx.Response(200, json={"order_id": 42})
    )
    client.get_order(42, source="pending", wallet_id=7)
    params = dict(route.calls.last.request.url.params)
    assert params.get("source") == "pending"
    assert params.get("wallet_id") == "7"
    assert "market_id" not in params


def test_get_order_bare_sends_no_params(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/orders/42").mock(
        return_value=httpx.Response(200, json={"order_id": 42})
    )
    client.get_order(42)
    assert dict(route.calls.last.request.url.params) == {}


async def test_async_get_order_forwards_real_query_params(aclient, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/orders/42").mock(
        return_value=httpx.Response(200, json={"order_id": 42})
    )
    await aclient.get_order(42, source="filled", wallet_id=3)
    params = dict(route.calls.last.request.url.params)
    assert params.get("source") == "filled"
    assert params.get("wallet_id") == "3"
    assert "market_id" not in params


def test_clob_cancel_all_sends_confirmation(respx_mock):
    from polysim_clob_client.client import ClobClient

    c = ClobClient(host=BASE_URL, key="ps_live_testkey")
    c._client._transport._floor_interval = 0.0
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/cancel-all").mock(
            return_value=httpx.Response(200, json={"canceled": 0})
        )
        c.cancel_all()
        assert route.called
        assert _confirms_cancel_all(route.calls.last.request)
    finally:
        c.close()


# ── SecureClient (py-sdk mirror) write-path contracts ───────────────────────
# The G3 trading surface must put the same backend-validated bytes on the wire as
# the v1 mirror: cancel_all's confirmation guard, and the worst-acceptable-price
# cap a market order MUST carry (an uncapped market order is the footgun the
# backend's marketable-limit model rejects — the v1 mirror defaults 0.99 BUY /
# 0.01 SELL). A mock-only green suite can't bless a request the backend rejects.


def test_secure_cancel_all_sends_confirmation(respx_mock):
    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key="ps_live_testkey")
    c._client._transport._floor_interval = 0.0
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/cancel-all").mock(
            return_value=httpx.Response(200, json={"canceled": []})
        )
        c.cancel_all()
        assert route.called
        assert _confirms_cancel_all(route.calls.last.request)
    finally:
        c.close()


def test_secure_market_buy_sends_worst_price_cap(respx_mock):
    """A market BUY with no ``max_price`` MUST still forward a worst-price cap.

    The backend's marketable-limit model requires a price on every market order;
    an uncapped BUY is rejected. The mirror defaults the cap to 0.99 (the v1
    mirror's BUY default) — pin that it lands in the body, never absent.
    """
    import json

    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key="ps_live_testkey")
    c._client._transport._floor_interval = 0.0
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
            return_value=httpx.Response(200, json={"order_id": "o1", "status": "FILLED"})
        )
        # colon-form token resolves locally — no reverse-resolution network call.
        c.place_market_order(token_id="0xcond:YES", side="BUY", amount="20")
        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert "price" in body, "market order MUST carry a worst-acceptable price cap"
        assert body["price"] == "0.99"
        assert body["order_type"] == "market"
        assert body["amount"] == "20"
    finally:
        c.close()


def test_secure_market_sell_sends_worst_price_floor(respx_mock):
    """A market SELL with no ``min_price`` MUST still forward a worst-price floor (0.01)."""
    import json

    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key="ps_live_testkey")
    c._client._transport._floor_interval = 0.0
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
            return_value=httpx.Response(200, json={"order_id": "o2", "status": "FILLED"})
        )
        c.place_market_order(token_id="0xcond:YES", side="SELL", shares="15")
        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert body["price"] == "0.01"
        assert body["quantity"] == "15"
    finally:
        c.close()


# ── v1 drop-in: market-order time-in-force must NOT downgrade to GTC ─────────
# create_market_order(args) builds time_in_force='FOK' (or 'FAK'); post_order's
# default orderType is OrderType.GTC. The submit path must NOT override an
# embedded market FOK/FAK with that default GTC — a market order shipped as
# order_type=market + time_in_force=GTC is a contradictory shape the backend's
# marketable-limit model would mishandle.


def _clob_client():
    from polysim_clob_client.client import ClobClient

    c = ClobClient(host=BASE_URL, key="ps_live_testkey")
    c._client._transport._floor_interval = 0.0
    return c


def test_clob_market_order_default_post_preserves_fok(respx_mock):
    """``create_market_order(args) -> post_order(order)`` (default orderType) must
    ship ``time_in_force='FOK'`` on the wire, NOT the GTC default."""
    import json

    from polysim_clob_client.clob_types import MarketOrderArgs

    c = _clob_client()
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
            return_value=httpx.Response(200, json={"order_id": "m1", "status": "FILLED"})
        )
        order = c.create_market_order(MarketOrderArgs(token_id="0xcond:YES", amount=20.0))
        c.post_order(order)  # default orderType == OrderType.GTC
        body = json.loads(route.calls.last.request.content)
        assert body["order_type"] == "market"
        assert body["time_in_force"] == "FOK"
    finally:
        c.close()


def test_clob_market_order_fak_variant_preserved(respx_mock):
    """An ``OrderType.FAK`` market order keeps ``time_in_force='FAK'`` through the
    default-orderType post path."""
    import json

    from polysim_clob_client.clob_types import MarketOrderArgs, OrderType

    c = _clob_client()
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
            return_value=httpx.Response(200, json={"order_id": "m2", "status": "FILLED"})
        )
        order = c.create_market_order(
            MarketOrderArgs(token_id="0xcond:YES", amount=20.0, order_type=OrderType.FAK)
        )
        c.post_order(order)
        body = json.loads(route.calls.last.request.content)
        assert body["time_in_force"] == "FAK"
    finally:
        c.close()


def test_clob_explicit_order_type_on_limit_still_wins(respx_mock):
    """An EXPLICIT ``orderType`` on a LIMIT order still wins (the market-FOK
    preservation must not regress the limit path)."""
    import json

    from polysim_clob_client.clob_types import OrderArgs, OrderType

    c = _clob_client()
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
            return_value=httpx.Response(200, json={"order_id": "l1", "status": "live"})
        )
        order = c.create_order(OrderArgs(token_id="0xcond:YES", price=0.55, size=10, side="BUY"))
        c.post_order(order, OrderType.GTC)
        body = json.loads(route.calls.last.request.content)
        assert body["order_type"] == "limit"
        assert body["time_in_force"] == "GTC"
    finally:
        c.close()


def test_clob_market_order_explicit_fok_via_post_order(respx_mock):
    """An explicit ``OrderType.FOK`` passed to post_order on a market order wins."""
    import json

    from polysim_clob_client.clob_types import MarketOrderArgs, OrderType

    c = _clob_client()
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
            return_value=httpx.Response(200, json={"order_id": "m3", "status": "FILLED"})
        )
        order = c.create_market_order(MarketOrderArgs(token_id="0xcond:YES", amount=20.0))
        c.post_order(order, OrderType.FOK)
        body = json.loads(route.calls.last.request.content)
        assert body["time_in_force"] == "FOK"
    finally:
        c.close()


def test_clob_batch_market_order_preserves_fok(respx_mock):
    """``post_orders`` (batch) with a default-orderType market entry preserves FOK."""
    import json

    from polysim_clob_client.clob_types import MarketOrderArgs, PostOrdersArgs

    c = _clob_client()
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/orders/batch").mock(
            return_value=httpx.Response(200, json=[{"order_id": "b1", "status": "FILLED"}])
        )
        order = c.create_market_order(MarketOrderArgs(token_id="0xcond:YES", amount=20.0))
        c.post_orders([PostOrdersArgs(order=order)])
        sent = json.loads(route.calls.last.request.content)
        # batch body is {"orders": [...]} (see PolySimClient.place_orders).
        entry = sent["orders"][0]
        assert entry["order_type"] == "market"
        assert entry["time_in_force"] == "FOK"
    finally:
        c.close()


def test_clob_batch_gtd_order_preserves_gtd(respx_mock):
    """``post_orders`` (batch) with a default-orderType GTD limit entry must
    preserve ``time_in_force='GTD'`` — NOT silently downgrade to the GTC default
    of ``PostOrdersArgs.orderType``. Mirrors ``_submit``'s GTD guard: an embedded
    ``expiration`` (built by ``create_order(expiration=...)``) keeps GTD when the
    caller left orderType at the GTC default."""
    import json

    from polysim_clob_client.clob_types import OrderArgs, PostOrdersArgs

    c = _clob_client()
    try:
        route = respx_mock.post(f"{BASE_URL}/v1/orders/batch").mock(
            return_value=httpx.Response(200, json=[{"order_id": "g1", "status": "live"}])
        )
        order = c.create_order(
            OrderArgs(
                token_id="0xcond:YES",
                price=0.55,
                size=10,
                side="BUY",
                expiration=1700000000,
            )
        )
        # create_order embedded time_in_force='GTD' + expiration.
        assert order["time_in_force"] == "GTD"
        c.post_orders([PostOrdersArgs(order=order)])  # default orderType == GTC
        entry = json.loads(route.calls.last.request.content)["orders"][0]
        assert entry["time_in_force"] == "GTD"
        assert entry["expiration"] == 1700000000
    finally:
        c.close()


# ── v1 drop-in: cancel_market_orders(asset_id=<real token>) reverse-resolves ──
# A real Polymarket CLOB outcome-token id (long all-digit) must be reverse-resolved
# via GET /v1/markets-by-token to find its condition id — a local _split_token
# would treat the whole digit string as the market id and cancel the wrong market.

_REAL_TOKEN = "7" * 40


def test_clob_cancel_market_orders_reverse_resolves_real_token(respx_mock):
    """``cancel_market_orders(asset_id=<real numeric token>)`` must reverse-resolve
    the token to its condition id (via /v1/markets-by-token), not pass the raw
    digit string through as the market."""
    c = _clob_client()
    try:
        respx_mock.get(f"{BASE_URL}/v1/markets-by-token/{_REAL_TOKEN}").mock(
            return_value=httpx.Response(
                200, json={"condition_id": "0xcond", "outcome": "Yes"}
            )
        )
        route = respx_mock.delete(f"{BASE_URL}/v1/cancel-market-orders").mock(
            return_value=httpx.Response(200, json={"canceled": 1})
        )
        c.cancel_market_orders(asset_id=_REAL_TOKEN)
        params = dict(route.calls.last.request.url.params)
        assert params == {"market": "0xcond"}  # the resolved condition id, not the token
    finally:
        c.close()


def test_clob_cancel_market_orders_colon_form_stays_local(respx_mock):
    """The colon form resolves locally (no markets-by-token network call)."""
    c = _clob_client()
    try:
        route = respx_mock.delete(f"{BASE_URL}/v1/cancel-market-orders").mock(
            return_value=httpx.Response(200, json={"canceled": 0})
        )
        c.cancel_market_orders(asset_id="0xcond:YES")
        params = dict(route.calls.last.request.url.params)
        assert params == {"market": "0xcond"}
    finally:
        c.close()
