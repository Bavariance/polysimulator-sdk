"""Adapter tests for ``polysim_polymarket.SecureClient`` TRADING (G3 subset).

All respx-mocked: no real network, no credentials, NO live trading, no prod
writes. Each test proves one trading method puts the right bytes on the wire
(route + body, incl. the worst-acceptable-price cap on market orders), and adapts
the backend reply onto py-sdk's return model.

Covers:
  * ``create_limit_order`` builds an inert-signed ``SignedOrder`` (no real
    signature) carrying the unsigned paper body; GTC vs GTD; post_only;
  * ``create_market_order`` BUY (amount=$ + max_price cap, default 0.99) and SELL
    (shares + min_price floor, default 0.01);
  * ``post_order`` / ``post_orders`` submit the right body to ``/v1/orders`` /
    ``/v1/orders/batch`` and adapt onto ``OrderResponse``;
  * ``place_limit_order`` / ``place_market_order`` build + post in one call;
  * each cancel hits the right route; ``cancel_all`` sends the confirmation form.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"

# A real Polymarket CLOB outcome-token id: a long all-digit string. We address
# orders by the PolySim colon form to avoid a reverse-resolution network call in
# the body tests; LONG_TOKEN is used only where we assert the reverse-resolve.
COLON_TOKEN = "0xcond:YES"
LONG_TOKEN = "71321045679252212594626385532706912750332728571942532289631379312455583992563"


@pytest.fixture
def secure():
    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


# ── create_limit_order: inert-signed SignedOrder + paper body ───────────────


def test_create_limit_order_returns_inert_signed_order(secure, respx_mock):
    from polysim_polymarket import SignedOrder

    order = secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.55", size="10", side="BUY"
    )
    assert isinstance(order, SignedOrder)
    # trading-semantic fields set
    assert order.token_id == COLON_TOKEN
    assert order.side == "BUY"
    assert order.order_type == "GTC"
    # signing is inert — no real signature/signer/salt
    assert order.signature == ""
    assert order.signer == ""
    assert order.salt == 0
    # carries the unsigned paper body for post_order
    body = order.paper_body
    assert body["market_id"] == "0xcond"
    assert body["outcome"] == "YES"
    assert body["order_type"] == "limit"
    assert body["time_in_force"] == "GTC"
    assert body["price"] == "0.55"
    assert body["quantity"] == "10"
    assert "post_only" not in body


def test_create_limit_order_no_network_for_colon_token(secure, respx_mock):
    # No route registered: building must make ZERO network calls for a colon-form
    # token (local resolution).
    order = secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.55", size="10", side="SELL"
    )
    assert order.side == "SELL"


def test_create_limit_order_gtd_with_expiration(secure, respx_mock):
    order = secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.4", size="5", side="BUY", expiration=2_000_000_000
    )
    assert order.order_type == "GTD"
    assert order.expiration == 2_000_000_000
    body = order.paper_body
    assert body["time_in_force"] == "GTD"
    assert body["expiration"] == 2_000_000_000


def test_create_limit_order_post_only_in_body(secure, respx_mock):
    order = secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.55", size="10", side="BUY", post_only=True
    )
    assert order.post_only is True
    assert order.paper_body["post_only"] is True


def test_create_limit_order_reverse_resolves_long_token(secure, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets-by-token/{LONG_TOKEN}").mock(
        return_value=httpx.Response(
            200, json={"condition_id": "0xRESOLVED", "outcome": "NO"}
        )
    )
    order = secure.create_limit_order(
        token_id=LONG_TOKEN, price="0.3", size="7", side="BUY"
    )
    assert route.called
    assert order.paper_body["market_id"] == "0xRESOLVED"
    assert order.paper_body["outcome"] == "NO"


# ── create_market_order: worst-price cap (BUY 0.99 / SELL 0.01) ─────────────


def test_create_market_buy_defaults_worst_price_099(secure, respx_mock):
    order = secure.create_market_order(token_id=COLON_TOKEN, side="BUY", amount="20")
    body = order.paper_body
    assert body["order_type"] == "market"
    assert body["time_in_force"] == "FAK"
    # USD notional -> amount, NOT quantity
    assert body["amount"] == "20"
    assert "quantity" not in body
    # worst-acceptable price defaults to 0.99 for a BUY (never uncapped)
    assert body["price"] == "0.99"


def test_create_market_buy_honors_max_price(secure, respx_mock):
    order = secure.create_market_order(
        token_id=COLON_TOKEN, side="BUY", amount="20", max_price="0.7"
    )
    assert order.paper_body["price"] == "0.7"


def test_create_market_sell_defaults_worst_price_001(secure, respx_mock):
    order = secure.create_market_order(token_id=COLON_TOKEN, side="SELL", shares="15")
    body = order.paper_body
    assert body["order_type"] == "market"
    # share count -> quantity, NOT amount
    assert body["quantity"] == "15"
    assert "amount" not in body
    # worst-acceptable price floor defaults to 0.01 for a SELL
    assert body["price"] == "0.01"


def test_create_market_sell_honors_min_price(secure, respx_mock):
    order = secure.create_market_order(
        token_id=COLON_TOKEN, side="SELL", shares="15", min_price="0.2"
    )
    assert order.paper_body["price"] == "0.2"


def test_create_market_order_fok(secure, respx_mock):
    order = secure.create_market_order(
        token_id=COLON_TOKEN, side="BUY", amount="20", order_type="FOK"
    )
    assert order.order_type == "FOK"
    assert order.paper_body["time_in_force"] == "FOK"


def test_order_numeric_fields_serialize_as_decimal_strings(secure, respx_mock):
    """price / size / amount ride the wire as decimal STRINGS, not floats, so a
    drift-prone decimal (0.1) stays exact (no binary-float artefact)."""
    limit = secure.create_limit_order(token_id=COLON_TOKEN, price="0.1", size="3", side="BUY")
    assert limit.paper_body["price"] == "0.1"
    assert isinstance(limit.paper_body["price"], str)
    assert limit.paper_body["quantity"] == "3"
    assert isinstance(limit.paper_body["quantity"], str)

    buy = secure.create_market_order(token_id=COLON_TOKEN, side="BUY", amount="0.3")
    assert buy.paper_body["amount"] == "0.3"
    assert isinstance(buy.paper_body["amount"], str)
    assert isinstance(buy.paper_body["price"], str)

    sell = secure.create_market_order(token_id=COLON_TOKEN, side="SELL", shares="7")
    assert sell.paper_body["quantity"] == "7"
    assert isinstance(sell.paper_body["quantity"], str)


# ── post_order: route + body + OrderResponse adaptation ─────────────────────


def test_post_order_sends_body_to_v1_orders(secure, respx_mock):
    from polysim_polymarket import AcceptedOrder

    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(
            200, json={"order_id": "o1", "status": "FILLED", "making_amount": "5.5"}
        )
    )
    order = secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.55", size="10", side="BUY"
    )
    resp = secure.post_order(order)
    assert route.called
    sent = _body(route.calls.last.request)
    assert sent["market_id"] == "0xcond"
    assert sent["side"] == "BUY"
    assert sent["price"] == "0.55"
    assert sent["quantity"] == "10"
    assert sent["time_in_force"] == "GTC"
    # adapted onto py-sdk's AcceptedOrder
    assert isinstance(resp, AcceptedOrder)
    assert resp.ok is True
    assert resp.order_id == "o1"
    assert resp.status == "matched"
    assert resp.making_amount == Decimal("5.5")


def test_post_order_market_buy_forwards_worst_price_cap(secure, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o2", "status": "FILLED"})
    )
    order = secure.create_market_order(token_id=COLON_TOKEN, side="BUY", amount="50")
    secure.post_order(order)
    sent = _body(route.calls.last.request)
    # the worst-acceptable price cap (default 0.99) IS forwarded — never uncapped
    assert sent["price"] == "0.99"
    assert sent["amount"] == "50"
    assert sent["order_type"] == "market"


def test_post_order_rejected_maps_to_rejected_order(secure, respx_mock):
    from polysim_polymarket import RejectedOrder

    respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(
            200, json={"order_id": "", "status": "not_enough_balance", "success": False}
        )
    )
    order = secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.55", size="10", side="BUY"
    )
    resp = secure.post_order(order)
    assert isinstance(resp, RejectedOrder)
    assert resp.ok is False
    assert resp.code == "not_enough_balance"


# ── post_orders: batch route + per-order adaptation ─────────────────────────


def test_post_orders_batch(secure, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders/batch").mock(
        return_value=httpx.Response(
            200,
            json={"orders": [{"order_id": "a", "status": "live"},
                             {"order_id": "b", "status": "FILLED"}]},
        )
    )
    o1 = secure.create_limit_order(token_id=COLON_TOKEN, price="0.5", size="10", side="BUY")
    o2 = secure.create_limit_order(token_id=COLON_TOKEN, price="0.6", size="5", side="SELL")
    resps = secure.post_orders([o1, o2])
    assert route.called
    sent = _body(route.calls.last.request)
    assert len(sent["orders"]) == 2
    assert sent["orders"][0]["price"] == "0.5"
    assert sent["orders"][1]["side"] == "SELL"
    assert tuple(r.order_id for r in resps) == ("a", "b")
    assert resps[0].status == "live"
    assert resps[1].status == "matched"


def test_post_orders_batch_rejected_row_maps_to_rejected_order(secure, respx_mock):
    """A batch returns HTTP 200 with per-entry rows that can be REJECTED/ERROR.

    Mirror py-sdk's ``normalize_order_response``: a row whose status is not an
    accepted post-status (``live`` / ``matched`` / ``delayed``) — e.g. the
    backend's coarse ``REJECTED`` / ``ERROR`` — is a ``RejectedOrder``, not an
    accepted/matched arm of the OrderResponse union.
    """
    from polysim_polymarket import AcceptedOrder, RejectedOrder

    respx_mock.post(f"{BASE_URL}/v1/orders/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "orders": [
                    {"order_id": "ok", "status": "live"},
                    {"order_id": "", "status": "REJECTED", "error": "post-only would cross"},
                    {"order_id": "", "status": "ERROR", "message": "engine error"},
                ]
            },
        )
    )
    o1 = secure.create_limit_order(token_id=COLON_TOKEN, price="0.5", size="10", side="BUY")
    o2 = secure.create_limit_order(token_id=COLON_TOKEN, price="0.6", size="5", side="BUY")
    o3 = secure.create_limit_order(token_id=COLON_TOKEN, price="0.7", size="5", side="BUY")
    resps = secure.post_orders([o1, o2, o3])
    assert isinstance(resps[0], AcceptedOrder)
    assert isinstance(resps[1], RejectedOrder)
    assert isinstance(resps[2], RejectedOrder)
    assert resps[1].ok is False
    assert resps[2].ok is False


def test_post_order_unknown_status_maps_to_rejected_order(secure, respx_mock):
    """An UNKNOWN / unexpected status is REJECTED, not silently accepted.

    Mirrors py-sdk's strict ALLOWLIST (``order_response._is_accepted``): a row is
    accepted ONLY when its status is a recognised accepted post-status. A status
    the backend never documented (``weird``) is outside the allowlist, so it
    yields a ``RejectedOrder`` with the ``"unknown"`` catch-all code — NOT an
    ``AcceptedOrder(status='matched')`` (the old denylist's fall-through bug)."""
    from polysim_polymarket import RejectedOrder

    respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "x1", "status": "weird"})
    )
    order = secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.55", size="10", side="BUY"
    )
    resp = secure.post_order(order)
    assert isinstance(resp, RejectedOrder)
    assert resp.ok is False
    assert resp.code == "unknown"


# ── place_*: build + post in one call ───────────────────────────────────────


def test_place_limit_order_builds_and_posts(secure, respx_mock):
    from polysim_polymarket import AcceptedOrder

    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "p1", "status": "live"})
    )
    resp = secure.place_limit_order(
        token_id=COLON_TOKEN, price="0.55", size="10", side="BUY"
    )
    assert route.called
    sent = _body(route.calls.last.request)
    assert sent["price"] == "0.55"
    assert sent["order_type"] == "limit"
    assert isinstance(resp, AcceptedOrder)
    assert resp.order_id == "p1"


def test_place_market_order_builds_and_posts_with_cap(secure, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "p2", "status": "FILLED"})
    )
    resp = secure.place_market_order(token_id=COLON_TOKEN, side="BUY", amount="25")
    sent = _body(route.calls.last.request)
    assert sent["amount"] == "25"
    assert sent["price"] == "0.99"  # worst-price cap forwarded
    assert resp.order_id == "p2"


# ── cancel routes ───────────────────────────────────────────────────────────


def test_cancel_order_hits_delete_route(secure, respx_mock):
    from polysim_polymarket import CancelOrdersResponse

    route = respx_mock.delete(f"{BASE_URL}/v1/orders/ord_9").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    resp = secure.cancel_order(order_id="ord_9")
    assert route.called
    assert isinstance(resp, CancelOrdersResponse)
    assert resp.canceled == ("ord_9",)
    assert resp.not_canceled == {}


def test_cancel_order_failure_records_not_canceled(secure, respx_mock):
    respx_mock.delete(f"{BASE_URL}/v1/orders/ord_x").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )
    resp = secure.cancel_order(order_id="ord_x")
    assert resp.canceled == ()
    assert "ord_x" in resp.not_canceled


def test_cancel_orders_loops_single_route(secure, respx_mock):
    r1 = respx_mock.delete(f"{BASE_URL}/v1/orders/a").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    r2 = respx_mock.delete(f"{BASE_URL}/v1/orders/b").mock(
        return_value=httpx.Response(400, json={"detail": "already filled"})
    )
    resp = secure.cancel_orders(order_ids=["a", "b"])
    assert r1.called and r2.called
    assert resp.canceled == ("a",)
    assert "b" in resp.not_canceled


def test_cancel_market_orders_uses_market_query(secure, respx_mock):
    route = respx_mock.delete(f"{BASE_URL}/v1/cancel-market-orders").mock(
        return_value=httpx.Response(200, json={"canceled": ["a", "b"]})
    )
    resp = secure.cancel_market_orders(market="0xcond")
    assert route.called
    assert dict(route.calls.last.request.url.params)["market"] == "0xcond"
    assert resp.canceled == ("a", "b")


def test_cancel_market_orders_resolves_token_id(secure, respx_mock):
    route = respx_mock.delete(f"{BASE_URL}/v1/cancel-market-orders").mock(
        return_value=httpx.Response(200, json={"canceled": []})
    )
    secure.cancel_market_orders(token_id=COLON_TOKEN)
    assert dict(route.calls.last.request.url.params)["market"] == "0xcond"


# ── cancel_all: MUST send the confirmation form ─────────────────────────────


def test_cancel_all_sends_confirmation_form(secure, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/cancel-all").mock(
        return_value=httpx.Response(200, json={"canceled": ["x", "y", "z"]})
    )
    resp = secure.cancel_all()
    assert route.called
    req = route.calls.last.request
    # The SDK sends the mandatory confirmation as the ``X-Confirm-Cancel-All``
    # header (the backend's accepted form) — assert it DIRECTLY rather than via a
    # header-or-query disjunction that an absent header could still pass.
    assert req.headers.get("X-Confirm-Cancel-All", "").lower() == "true", (
        "cancel_all MUST send the X-Confirm-Cancel-All: true header"
    )
    assert resp.canceled == ("x", "y", "z")
