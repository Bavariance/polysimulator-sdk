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
