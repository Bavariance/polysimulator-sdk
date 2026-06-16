"""Native ``polysim_sdk`` surface — respx-mocked path/verb/param/body checks."""

from __future__ import annotations

import httpx
import pytest
import respx

from polysim_sdk.exceptions import ApiError, ValidationError

BASE_URL = "https://api.polysimulator.test"


def test_me_sends_api_key_header(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/me").mock(
        return_value=httpx.Response(200, json={"id": "u_1", "tier": "pro"})
    )
    result = client.me()
    assert result["id"] == "u_1"
    assert route.calls.last.request.headers["X-API-Key"] == "ps_live_testkey"


def test_balance_sends_no_params(client, respx_mock):
    # GET /v1/account/balance takes NO query params — it always reports the
    # API wallet. balance() must not send wallet_id (or anything else).
    route = respx_mock.get(f"{BASE_URL}/v1/account/balance").mock(
        return_value=httpx.Response(200, json={"balance": "1000.00"})
    )
    client.balance()
    assert dict(route.calls.last.request.url.params) == {}
    # balance() exposes no wallet_id arg — the endpoint always reports the API
    # wallet, so accepting one would mislead callers.
    with pytest.raises(TypeError):
        client.balance(wallet_id="api")


def test_positions_unwraps_bare_array(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/account/positions").mock(
        return_value=httpx.Response(200, json=[{"market_id": "m1"}, {"market_id": "m2"}])
    )
    positions = client.positions()
    assert isinstance(positions, list)
    assert len(positions) == 2


def test_positions_unwraps_keyed_object(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/account/positions").mock(
        return_value=httpx.Response(200, json={"positions": [{"market_id": "m1"}]})
    )
    assert client.positions()[0]["market_id"] == "m1"


def test_list_markets_passes_filters(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets").mock(
        return_value=httpx.Response(200, json={"markets": [{"condition_id": "c1"}]})
    )
    # Free-text search is the ``q`` param (max_length 120) — NOT ``search``.
    client.list_markets(limit=10, hot_only=True, q="trump")
    params = dict(route.calls.last.request.url.params)
    assert params["limit"] == "10"
    assert params["hot_only"] == "true"
    assert params["q"] == "trump"


def test_list_markets_drops_none_filters(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets").mock(return_value=httpx.Response(200, json=[]))
    client.list_markets(limit=5, status=None)
    assert "status" not in dict(route.calls.last.request.url.params)


def test_get_market_by_slug_path(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets/by-slug/will-x-happen").mock(
        return_value=httpx.Response(200, json={"condition_id": "c1"})
    )
    client.get_market_by_slug("will-x-happen")
    assert route.called


def test_place_order_body_and_idempotency(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1", "status": "FILLED"})
    )
    client.place_order(
        market_id="c1",
        side="buy",
        outcome="YES",
        quantity=10,
        order_type="market",
        price="0.99",
    )
    req = route.calls.last.request
    body = req.read().decode()
    assert '"side":"BUY"' in body.replace(" ", "")
    assert '"market_id":"c1"' in body.replace(" ", "")
    # idempotency key auto-generated
    assert req.headers.get("Idempotency-Key")


def test_place_order_respects_explicit_idempotency_key(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1"})
    )
    client.place_order(
        market_id="c1",
        side="SELL",
        outcome="NO",
        quantity=1,
        price="0.01",
        idempotency_key="fixed-key-123",
    )
    assert route.calls.last.request.headers["Idempotency-Key"] == "fixed-key-123"


def test_place_order_client_order_id_defaults_idempotency_key(client, respx_mock):
    # When no explicit idempotency_key is given but a client_order_id is, the
    # client_order_id becomes the Idempotency-Key (so the caller's own dedup id
    # also protects against double-fire on retry).
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1"})
    )
    client.place_order(
        market_id="c1",
        side="BUY",
        outcome="YES",
        quantity=1,
        price="0.99",
        client_order_id="my-coid-9",
    )
    assert route.calls.last.request.headers["Idempotency-Key"] == "my-coid-9"


def test_data_orders_returns_envelope_and_forwards_filters(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/data/orders").mock(
        return_value=httpx.Response(
            200,
            json={"limit": 100, "count": 1, "next_cursor": "LTE=", "data": [{"id": "0xa"}]},
        )
    )
    env = client.data_orders(market="0xcond", asset_id="711", next_cursor="MA==")
    assert env["data"] == [{"id": "0xa"}]
    assert env["next_cursor"] == "LTE="
    params = dict(route.calls.last.request.url.params)
    assert params["market"] == "0xcond"
    assert params["asset_id"] == "711"
    assert params["next_cursor"] == "MA=="
    assert params["limit"] == "100"


def test_data_trades_returns_envelope_and_forwards_filters(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/data/trades").mock(
        return_value=httpx.Response(
            200,
            json={"limit": 100, "count": 1, "next_cursor": "LTE=", "data": [{"id": "0xt"}]},
        )
    )
    env = client.data_trades(market="0xcond", before="1700000000")
    assert env["data"] == [{"id": "0xt"}]
    params = dict(route.calls.last.request.url.params)
    assert params["market"] == "0xcond"
    assert params["before"] == "1700000000"


def test_get_market_by_token_path(client, respx_mock):
    token = "7" * 40
    route = respx_mock.get(f"{BASE_URL}/v1/markets-by-token/{token}").mock(
        return_value=httpx.Response(
            200, json={"condition_id": "0xcond", "primary_token_id": token, "outcome": "Yes"}
        )
    )
    out = client.get_market_by_token(token)
    assert out["condition_id"] == "0xcond"
    assert route.called


def test_place_orders_batch_path(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders/batch").mock(
        return_value=httpx.Response(200, json={"orders": [{"order_id": "o1"}, {"order_id": "o2"}]})
    )
    out = client.place_orders([{"market_id": "c1"}, {"market_id": "c2"}])
    assert len(out) == 2
    # The endpoint expects an {"orders": [...]} envelope, not a bare array.
    import json

    req = route.calls.last.request
    body = json.loads(req.read().decode())
    assert [o["market_id"] for o in body["orders"]] == ["c1", "c2"]
    # Each entry gets its own auto-stamped client_order_id so individual orders
    # in the batch are independently idempotent...
    coids = [o["client_order_id"] for o in body["orders"]]
    assert all(coids) and len(set(coids)) == 2
    # ...and there is NO batch-level Idempotency-Key (that would dedupe the
    # whole batch as a unit).
    assert req.headers.get("Idempotency-Key") is None


def test_place_order_market_buy_sends_amount(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1"})
    )
    client.place_order(
        market_id="c1", side="BUY", outcome="YES", amount="25", price="0.99"
    )
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"amount":"25"' in body
    assert '"quantity"' not in body


def test_place_order_threads_post_only_and_expiration(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1"})
    )
    client.place_order(
        market_id="c1",
        side="BUY",
        outcome="YES",
        quantity=10,
        price="0.99",
        order_type="limit",
        time_in_force="GTD",
        post_only=True,
        expiration=1700000000,
    )
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"post_only":true' in body
    assert '"expiration":1700000000' in body
    assert '"time_in_force":"GTD"' in body


def test_place_order_requires_quantity_or_amount(client):
    with pytest.raises(ValueError):
        client.place_order(market_id="c1", side="BUY", outcome="YES", price="0.99")


def test_create_wallet_sends_label_body(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/me/wallets").mock(
        return_value=httpx.Response(200, json={"id": "w1"})
    )
    client.create_wallet(name="My bot", kind="SANDBOX")
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"label":"Mybot"' in body
    assert '"kind":"SANDBOX"' in body
    assert '"name"' not in body


def test_update_wallet_sends_label_body(client, respx_mock):
    route = respx_mock.patch(f"{BASE_URL}/v1/me/wallets/w1").mock(
        return_value=httpx.Response(200, json={"id": "w1"})
    )
    client.update_wallet("w1", name="Renamed")
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"label":"Renamed"' in body
    assert '"name"' not in body


def test_cancel_market_orders_uses_market_query_param(client, respx_mock):
    route = respx_mock.delete(f"{BASE_URL}/v1/cancel-market-orders").mock(
        return_value=httpx.Response(200, json={"canceled": 3})
    )
    client.cancel_market_orders("c1")
    params = dict(route.calls.last.request.url.params)
    assert params == {"market": "c1"}


def test_create_key_sends_tier_default_free(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/keys").mock(
        return_value=httpx.Response(200, json={"key": "ps_live_new"})
    )
    client.create_key(name="bot key")
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"tier":"free"' in body
    assert '"expires_at"' not in body


def test_create_key_threads_tier_and_permissions(client, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/keys").mock(
        return_value=httpx.Response(200, json={"key": "ps_live_new"})
    )
    client.create_key(name="pro key", tier="pro", permissions=["read", "trade"])
    import json

    body = json.loads(route.calls.last.request.read().decode())
    assert body["tier"] == "pro"
    assert body["permissions"] == ["read", "trade"]


def test_bootstrap_classmethod_uses_bearer_no_api_key(respx_mock):
    from polysim_sdk import PolySimClient

    route = respx_mock.post(f"{BASE_URL}/v1/keys/bootstrap").mock(
        return_value=httpx.Response(200, json={"key": "ps_live_first"})
    )
    out = PolySimClient.bootstrap(
        jwt="jwt-token-abc", name="first key", base_url=BASE_URL, floor_interval=0.0
    )
    assert out["key"] == "ps_live_first"
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer jwt-token-abc"
    # No pre-existing key was supplied, so no X-API-Key header is sent.
    assert "X-API-Key" not in req.headers
    body = req.read().decode().replace(" ", "")
    assert '"name":"firstkey"' in body
    assert '"tier":"free"' in body


def test_cancel_order_delete(client, respx_mock):
    route = respx_mock.delete(f"{BASE_URL}/v1/orders/o1").mock(
        return_value=httpx.Response(200, json={"canceled": True})
    )
    client.cancel_order("o1")
    assert route.called


def test_validation_error_on_422(client, respx_mock):
    respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(422, json={"detail": "bad price"})
    )
    with pytest.raises(ValidationError) as exc:
        client.place_order(market_id="c1", side="BUY", outcome="YES", quantity=1, price="9")
    assert exc.value.status_code == 422


def test_export_trades_csv_returns_raw_text(client, respx_mock):
    csv = "order_id,price\n o1,0.5\n"
    route = respx_mock.get(f"{BASE_URL}/v1/export/trades.csv").mock(
        return_value=httpx.Response(200, text=csv)
    )
    out = client.export_trades_csv(market_id="c1")
    assert out == csv
    assert dict(route.calls.last.request.url.params) == {"market_id": "c1"}


def test_export_trades_csv_sends_date_bounds_and_wallet(client, respx_mock):
    # Signature is (*, wallet_id, from_, to, market_id); the lower bound is sent
    # under the wire alias ``from`` (a Python keyword), not ``from_``. There is
    # no limit/offset on this endpoint.
    route = respx_mock.get(f"{BASE_URL}/v1/export/trades.csv").mock(
        return_value=httpx.Response(200, text="x\n")
    )
    client.export_trades_csv(
        wallet_id=7, from_="2026-06-01", to="2026-06-14", market_id="c1"
    )
    params = dict(route.calls.last.request.url.params)
    assert params == {
        "wallet_id": "7",
        "from": "2026-06-01",
        "to": "2026-06-14",
        "market_id": "c1",
    }


def test_tiers_unwraps(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/keys/tiers").mock(
        return_value=httpx.Response(200, json={"tiers": [{"name": "free"}, {"name": "pro"}]})
    )
    assert [t["name"] for t in client.tiers()] == ["free", "pro"]


# ── BTC Up/Down discovery ────────────────────────────────────────────────

_UPDOWN_PAYLOAD = {
    "total": 3,
    "available_intervals": ["5M", "15M"],
    "available_assets": ["BTC"],
    "interval_counts": {"5M": 2, "15M": 1},
    "crypto_prices": {"BTC": {"symbol": "BTC", "price": 64588.0, "source": "coingecko"}},
    "markets": [
        {"condition_id": "c_live", "active": True, "closed": False, "resolved": False},
        {"condition_id": "c_closed", "active": True, "closed": True, "resolved": False},
        {"condition_id": "c_done", "active": False, "closed": True, "resolved": True},
    ],
}


def test_list_updown_passes_asset_interval_and_unwraps(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets/updown").mock(
        return_value=httpx.Response(200, json=_UPDOWN_PAYLOAD)
    )
    rows = client.list_updown(asset="BTC", interval="5M")
    params = dict(route.calls.last.request.url.params)
    assert params["asset"] == "BTC"
    assert params["interval"] == "5M"
    assert [m["condition_id"] for m in rows] == ["c_live", "c_closed", "c_done"]


def test_list_updown_live_filter_keeps_only_open_windows(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/markets/updown").mock(
        return_value=httpx.Response(200, json=_UPDOWN_PAYLOAD)
    )
    rows = client.list_updown(asset="BTC", interval="5M", live=True)
    assert [m["condition_id"] for m in rows] == ["c_live"]


def test_list_updown_drops_none_filters(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets/updown").mock(
        return_value=httpx.Response(200, json=_UPDOWN_PAYLOAD)
    )
    client.list_updown()
    params = dict(route.calls.last.request.url.params)
    assert "asset" not in params
    assert "interval" not in params


def test_get_updown_returns_full_payload(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/markets/updown").mock(
        return_value=httpx.Response(200, json=_UPDOWN_PAYLOAD)
    )
    payload = client.get_updown(asset="BTC")
    assert payload["crypto_prices"]["BTC"]["price"] == 64588.0
    assert payload["available_intervals"] == ["5M", "15M"]


@respx.mock
async def test_async_list_updown_live_filter(aclient):
    respx.get(f"{BASE_URL}/v1/markets/updown").mock(
        return_value=httpx.Response(200, json=_UPDOWN_PAYLOAD)
    )
    rows = await aclient.list_updown(asset="BTC", interval="5M", live=True)
    assert [m["condition_id"] for m in rows] == ["c_live"]


@respx.mock
async def test_async_get_updown(aclient):
    respx.get(f"{BASE_URL}/v1/markets/updown").mock(
        return_value=httpx.Response(200, json=_UPDOWN_PAYLOAD)
    )
    payload = await aclient.get_updown()
    assert payload["crypto_prices"]["BTC"]["price"] == 64588.0


@respx.mock
async def test_async_me(aclient):
    respx.get(f"{BASE_URL}/v1/me").mock(return_value=httpx.Response(200, json={"id": "u_async"}))
    result = await aclient.me()
    assert result["id"] == "u_async"


@respx.mock
async def test_async_place_order(aclient):
    respx.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o_async"})
    )
    out = await aclient.place_order(
        market_id="c1", side="BUY", outcome="YES", quantity=1, price="0.99"
    )
    assert out["order_id"] == "o_async"


@respx.mock
async def test_async_balance_sends_no_params(aclient):
    route = respx.get(f"{BASE_URL}/v1/account/balance").mock(
        return_value=httpx.Response(200, json={"balance": "1000.00"})
    )
    await aclient.balance()
    assert dict(route.calls.last.request.url.params) == {}
    with pytest.raises(TypeError):
        await aclient.balance(wallet_id="api")


@respx.mock
async def test_async_list_markets_uses_q(aclient):
    route = respx.get(f"{BASE_URL}/v1/markets").mock(
        return_value=httpx.Response(200, json={"markets": []})
    )
    await aclient.list_markets(limit=5, q="trump")
    assert dict(route.calls.last.request.url.params)["q"] == "trump"


@respx.mock
async def test_async_place_order_client_order_id_defaults_idempotency_key(aclient):
    route = respx.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o1"})
    )
    await aclient.place_order(
        market_id="c1", side="BUY", outcome="YES", quantity=1, price="0.99",
        client_order_id="my-coid-9",
    )
    assert route.calls.last.request.headers["Idempotency-Key"] == "my-coid-9"


@respx.mock
async def test_async_place_orders_stamps_client_order_id(aclient):
    import json

    route = respx.post(f"{BASE_URL}/v1/orders/batch").mock(
        return_value=httpx.Response(200, json={"orders": [{"order_id": "o1"}]})
    )
    await aclient.place_orders([{"market_id": "c1"}])
    req = route.calls.last.request
    body = json.loads(req.read().decode())
    assert body["orders"][0]["client_order_id"]
    assert req.headers.get("Idempotency-Key") is None


@respx.mock
async def test_async_export_trades_csv_sends_bounds(aclient):
    route = respx.get(f"{BASE_URL}/v1/export/trades.csv").mock(
        return_value=httpx.Response(200, text="x\n")
    )
    await aclient.export_trades_csv(wallet_id=7, from_="2026-06-01", to="2026-06-14")
    params = dict(route.calls.last.request.url.params)
    assert params == {"wallet_id": "7", "from": "2026-06-01", "to": "2026-06-14"}


@respx.mock
async def test_async_data_orders_envelope(aclient):
    route = respx.get(f"{BASE_URL}/v1/data/orders").mock(
        return_value=httpx.Response(
            200, json={"limit": 100, "count": 0, "next_cursor": "LTE=", "data": []}
        )
    )
    env = await aclient.data_orders(market="0xcond", next_cursor="MA==")
    assert env["next_cursor"] == "LTE="
    assert dict(route.calls.last.request.url.params)["market"] == "0xcond"


@respx.mock
async def test_async_data_trades_envelope(aclient):
    route = respx.get(f"{BASE_URL}/v1/data/trades").mock(
        return_value=httpx.Response(
            200, json={"limit": 100, "count": 0, "next_cursor": "LTE=", "data": []}
        )
    )
    env = await aclient.data_trades(asset_id="711")
    assert env["next_cursor"] == "LTE="
    assert dict(route.calls.last.request.url.params)["asset_id"] == "711"


@respx.mock
async def test_async_get_market_by_token(aclient):
    token = "7" * 40
    route = respx.get(f"{BASE_URL}/v1/markets-by-token/{token}").mock(
        return_value=httpx.Response(200, json={"condition_id": "0xcond", "outcome": "Yes"})
    )
    out = await aclient.get_market_by_token(token)
    assert out["condition_id"] == "0xcond"
    assert route.called


# ── token-native order book (F4: true token-id parity) ─────────────────────


def test_get_book_by_token_hits_token_endpoint(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
                "tick_size": "0.01",
                "neg_risk": False,
            },
        )
    )
    book = client.get_book_by_token("711")
    assert book["asset_id"] == "711"
    assert dict(route.calls.last.request.url.params)["token_id"] == "711"
    # No depth param unless asked.
    assert "depth" not in dict(route.calls.last.request.url.params)


def test_get_book_by_token_threads_depth(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(200, json={"bids": [], "asks": []})
    )
    client.get_book_by_token("711", depth=5)
    params = dict(route.calls.last.request.url.params)
    assert params["token_id"] == "711"
    assert params["depth"] == "5"


def test_get_book_threads_outcome_and_depth(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets/c1/book").mock(
        return_value=httpx.Response(200, json={"bids": [], "asks": []})
    )
    client.get_book("c1", outcome="No", depth=3)
    params = dict(route.calls.last.request.url.params)
    assert params["outcome"] == "No"
    assert params["depth"] == "3"


def test_get_book_no_optional_params_by_default(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets/c1/book").mock(
        return_value=httpx.Response(200, json={"bids": [], "asks": []})
    )
    client.get_book("c1")
    assert dict(route.calls.last.request.url.params) == {}


@respx.mock
async def test_async_get_book_by_token(aclient):
    route = respx.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(200, json={"asset_id": "711", "bids": [], "asks": []})
    )
    book = await aclient.get_book_by_token("711", depth=10)
    assert book["asset_id"] == "711"
    params = dict(route.calls.last.request.url.params)
    assert params["token_id"] == "711"
    assert params["depth"] == "10"


# ── UpDown HFT data: price-to-beat (strike) + live underlying spot ─────────
# These hit the *bare-app* /prices/* routes (no /v1 prefix). The SDK base_url
# is the host root, so the transport targets them directly.

_PTB_PAYLOAD = {
    "price": 64500.0,
    "asset": "BTC",
    "start_date": "2026-06-14T12:00:00Z",
    "source": "polymarket_open_price",
}
_SPOT_PAYLOAD = {
    "symbol": "BTC",
    "price": 64588.0,
    "timestamp": "2026-06-14T12:00:03Z",
    "source": "chainlink_rtds",
    "age_seconds": 0.4,
    "stale": False,
}


def test_get_price_to_beat_hits_ptb_endpoint(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/prices/ptb/c_live").mock(
        return_value=httpx.Response(200, json=_PTB_PAYLOAD)
    )
    ptb = client.get_price_to_beat("c_live")
    assert ptb["price"] == 64500.0
    assert ptb["source"] == "polymarket_open_price"
    assert route.calls.last.request.url.path == "/prices/ptb/c_live"


def test_get_price_to_beat_accepts_token_id(client, respx_mock):
    # The server resolves an outcome-token id via its token:{id} map, so the
    # SDK forwards whatever identifier it's given verbatim.
    route = respx_mock.get(f"{BASE_URL}/prices/ptb/711").mock(
        return_value=httpx.Response(200, json=_PTB_PAYLOAD)
    )
    client.get_price_to_beat("711")
    assert route.calls.last.request.url.path == "/prices/ptb/711"


def test_get_price_to_beat_404_means_pending(client, respx_mock):
    # A just-opened window has no strike yet → backend 404s. The transport
    # surfaces that as ApiError(status_code=404); callers treat it as "retry",
    # not "absent".
    respx_mock.get(f"{BASE_URL}/prices/ptb/c_new").mock(
        return_value=httpx.Response(
            404, json={"error": "Price-to-beat not yet available for this market"}
        )
    )
    with pytest.raises(ApiError) as exc:
        client.get_price_to_beat("c_new")
    assert exc.value.status_code == 404


def test_get_spot_hits_live_symbol_endpoint(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/prices/live/BTC").mock(
        return_value=httpx.Response(200, json=_SPOT_PAYLOAD)
    )
    spot = client.get_spot("BTC")
    assert spot["price"] == 64588.0
    assert spot["stale"] is False
    assert route.calls.last.request.url.path == "/prices/live/BTC"


def test_get_spots_hits_live_endpoint(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/prices/live").mock(
        return_value=httpx.Response(
            200,
            json={
                "prices": {"BTC": _SPOT_PAYLOAD},
                "supported_symbols": ["BTC", "ETH"],
                "timestamp": "2026-06-14T12:00:03Z",
            },
        )
    )
    spots = client.get_spots()
    assert spots["prices"]["BTC"]["price"] == 64588.0
    assert "ETH" in spots["supported_symbols"]
    assert route.calls.last.request.url.path == "/prices/live"


@respx.mock
async def test_async_get_price_to_beat(aclient):
    respx.get(f"{BASE_URL}/prices/ptb/c_live").mock(
        return_value=httpx.Response(200, json=_PTB_PAYLOAD)
    )
    ptb = await aclient.get_price_to_beat("c_live")
    assert ptb["price"] == 64500.0


@respx.mock
async def test_async_get_price_to_beat_404_means_pending(aclient):
    respx.get(f"{BASE_URL}/prices/ptb/c_new").mock(
        return_value=httpx.Response(404, json={"error": "Price-to-beat not yet available"})
    )
    with pytest.raises(ApiError) as exc:
        await aclient.get_price_to_beat("c_new")
    assert exc.value.status_code == 404


@respx.mock
async def test_async_get_spot(aclient):
    respx.get(f"{BASE_URL}/prices/live/BTC").mock(
        return_value=httpx.Response(200, json=_SPOT_PAYLOAD)
    )
    spot = await aclient.get_spot("BTC")
    assert spot["source"] == "chainlink_rtds"


@respx.mock
async def test_async_get_spots(aclient):
    respx.get(f"{BASE_URL}/prices/live").mock(
        return_value=httpx.Response(
            200, json={"prices": {"BTC": _SPOT_PAYLOAD}, "supported_symbols": ["BTC"]}
        )
    )
    spots = await aclient.get_spots()
    assert spots["prices"]["BTC"]["price"] == 64588.0
