"""Adapter tests for ``polysim_polymarket.SecureClient`` (G2 subset).

All respx-mocked: no real network, no credentials, no prod writes. Each test
proves one method hits the right PolySim REST endpoint with the right
params/cursor and adapts the response onto py-sdk's return model.

Covers:
  * constructor drop-in (paper ``api_key=`` form AND the real-PM
    ``SecureClient.create(private_key=...)`` form with on-chain kwargs inert);
  * the auth bootstrap (``fetch_api_keys`` / ``delete_api_key`` /
    ``credentials``);
  * account/liveness reads (``get_balance_allowance`` USD->base-unit mapping +
    case-sensitive ``asset_type`` guard / ``is_gasless_ready`` /
    ``get_closed_only_mode`` / ``get_notifications``);
  * authenticated order reads (``get_order`` row adaptation + empty-id guard /
    ``list_open_orders`` + ``list_account_trades`` cursor walk + server-side
    filter forwarding);
  * the shared CLOB reads delegate to the same v1 read path the public client
    uses (a spot-check that delegation is wired — full read parity is in
    test_polymarket_secure_public_parity.py).
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"

# A real Polymarket CLOB outcome-token id: a long all-digit string.
LONG_TOKEN = "71321045679252212594626385532706912750332728571942532289631379312455583992563"


@pytest.fixture
def secure():
    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key=API_KEY)
    # keep the suite fast — zero the floor pacing on the internal transport
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


# ── constructor drop-in ─────────────────────────────────────────────────────


def test_paper_constructor_accepts_api_key():
    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key=API_KEY)
    assert c._client._api_key == API_KEY
    c.close()


def test_create_accepts_real_pm_kwargs_inert():
    """``SecureClient.create`` accepts py-sdk's on-chain kwargs without TypeError.

    ``private_key`` / ``wallet`` / ``nonce`` / ``logger`` and any extra on-chain
    kwarg are accepted-and-inert on paper; ``api_key`` is what authenticates.
    """
    from polysim_polymarket import SecureClient

    c = SecureClient.create(
        private_key="0xabc123",
        wallet="0xWALLET",
        nonce=7,
        api_key=API_KEY,
        host=BASE_URL,
        chain_id=137,  # extra on-chain kwarg -> **_ignored
        signature_type=1,
        funder="0xFUND",
    )
    assert c._client._api_key == API_KEY
    c.close()


def test_constructor_ignores_onchain_kwargs():
    from polysim_polymarket import SecureClient

    # Every on-chain kwarg a ported bot might still pass is accepted, no TypeError.
    c = SecureClient(
        host=BASE_URL,
        api_key=API_KEY,
        private_key="0xkey",
        chain_id=137,
        signature_type=2,
        funder="0xfund",
        nonce=3,
    )
    assert c._client._api_key == API_KEY
    c.close()


# ── credentials property ────────────────────────────────────────────────────


def test_credentials_is_property_and_defaults_none():
    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key=API_KEY)
    assert c.credentials is None  # bare api_key -> no full creds triple
    c.close()


def test_credentials_returns_supplied_creds():
    from polysim_polymarket import ApiKeyCreds, SecureClient

    creds = ApiKeyCreds(key="k1", secret="s1", passphrase="p1")
    c = SecureClient(host=BASE_URL, api_key=API_KEY, credentials=creds)
    assert c.credentials is creds
    assert c.credentials.key == "k1"
    c.close()


def test_environment_is_property():
    from polysim_polymarket import SecureClient
    from polysim_polymarket.environments import PRODUCTION

    c = SecureClient(PRODUCTION, host=BASE_URL, api_key=API_KEY)
    assert c.environment is PRODUCTION
    c.close()


# ── auth bootstrap ──────────────────────────────────────────────────────────


def test_fetch_api_keys_projects_key_ids(secure, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/keys").mock(
        return_value=httpx.Response(
            200,
            json={
                "keys": [
                    {"id": "key_aaa", "name": "bot-1"},
                    {"id": "key_bbb", "name": "bot-2"},
                ]
            },
        )
    )
    ids = secure.fetch_api_keys()
    assert ids == ("key_aaa", "key_bbb")
    assert isinstance(ids, tuple)


def test_fetch_api_keys_empty(secure, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/keys").mock(
        return_value=httpx.Response(200, json={"keys": []})
    )
    assert secure.fetch_api_keys() == ()


def test_delete_api_key_returns_none_no_network(secure, respx_mock):
    # No route registered: respx raises on any request, so reaching the assertion
    # proves delete_api_key made ZERO network calls (paper keys are dashboard-managed).
    assert secure.delete_api_key() is None


def test_is_gasless_ready_true_no_network(secure, respx_mock):
    assert secure.is_gasless_ready() is True


# ── balance / allowance ─────────────────────────────────────────────────────


def test_get_balance_allowance_usd_to_base_units(secure, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/account/balance").mock(
        return_value=httpx.Response(200, json={"balance": "2500.00"})
    )
    from polysim_polymarket import BalanceAllowance

    ba = secure.get_balance_allowance(asset_type="COLLATERAL")
    assert isinstance(ba, BalanceAllowance)
    # 2500 USD * 1_000_000 base units/USD = 2_500_000_000
    assert ba.balance == 2_500_000_000
    assert ba.allowances == {}
    assert route.called
    # /v1/account/balance takes no query params
    assert dict(route.calls.last.request.url.params) == {}


def test_get_balance_allowance_cash_fallback_key(secure, respx_mock):
    # COLLATERAL reads cash; the ``cash`` fallback key is honoured when the
    # payload has no top-level ``balance`` field. (CONDITIONAL now reports the
    # conditional-token position balance, not cash — see
    # test_polymarket_conditional_balance.py.)
    respx_mock.get(f"{BASE_URL}/v1/account/balance").mock(
        return_value=httpx.Response(200, json={"cash": 100.5})
    )
    ba = secure.get_balance_allowance(asset_type="COLLATERAL")
    assert ba.balance == 100_500_000


def test_get_balance_allowance_rejects_bad_asset_type(secure, respx_mock):
    from polysim_polymarket import UserInputError

    # Case-sensitive: lowercase is rejected BEFORE any network call (no route).
    with pytest.raises(UserInputError, match="asset_type must be"):
        secure.get_balance_allowance(asset_type="collateral")  # type: ignore[arg-type]


def test_get_balance_allowance_malformed_payload_raises(secure, respx_mock):
    from polysim_polymarket import UnexpectedResponseError

    respx_mock.get(f"{BASE_URL}/v1/account/balance").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    with pytest.raises(UnexpectedResponseError):
        secure.get_balance_allowance(asset_type="COLLATERAL")


# ── liveness stubs ──────────────────────────────────────────────────────────


def test_get_closed_only_mode_false_no_network(secure, respx_mock):
    assert secure.get_closed_only_mode() is False


def test_get_notifications_empty_no_network(secure, respx_mock):
    assert secure.get_notifications() == ()


# ── authenticated order reads ───────────────────────────────────────────────


def test_get_order_adapts_row(secure, respx_mock):
    from polysim_polymarket import OpenOrder

    route = respx_mock.get(f"{BASE_URL}/v1/orders/ord_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "ord_1",
                "market": "0xcond",
                "asset_id": LONG_TOKEN,
                "side": "BUY",
                "price": "0.42",
                "original_size": "100",
                "size_matched": "30",
                "outcome": "Yes",
                "order_type": "GTC",
                "status": "LIVE",
            },
        )
    )
    order = secure.get_order(order_id="ord_1")
    assert isinstance(order, OpenOrder)
    assert order.id == "ord_1"
    assert order.token_id == LONG_TOKEN  # asset_id -> token_id alias
    assert order.side == "BUY"
    assert order.price == Decimal("0.42")
    assert order.original_size == Decimal("100")
    assert order.size_matched == Decimal("30")
    assert order.status == "LIVE"
    assert route.called


def test_get_order_rejects_empty_id(secure, respx_mock):
    from polysim_polymarket import UserInputError

    # No route registered: the guard must fire BEFORE any network call.
    with pytest.raises(UserInputError, match="order_id is required"):
        secure.get_order(order_id="")


def test_list_open_orders_walks_cursor_and_adapts(secure, respx_mock):
    def _pages(request: httpx.Request) -> httpx.Response:
        cursor = dict(request.url.params).get("next_cursor")
        if cursor in (None, "MA=="):
            return httpx.Response(
                200,
                json={
                    "limit": 100,
                    "count": 1,
                    "next_cursor": "MTAw",
                    "data": [{"id": "0xa", "market": "0xcond", "side": "BUY", "price": "0.5",
                              "original_size": "10", "size_matched": "0"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "limit": 100,
                "count": 1,
                "next_cursor": "LTE=",
                "data": [{"id": "0xb", "market": "0xcond", "side": "SELL", "price": "0.6",
                          "original_size": "5", "size_matched": "5"}],
            },
        )

    route = respx_mock.get(f"{BASE_URL}/v1/data/orders").mock(side_effect=_pages)
    pag = secure.list_open_orders(market="0xcond")
    items = list(pag.iter_items())
    assert [o.id for o in items] == ["0xa", "0xb"]
    assert items[0].side == "BUY"
    assert items[1].side == "SELL"
    # server-side filter forwarded
    assert dict(route.calls[0].request.url.params)["market"] == "0xcond"
    # walked both pages then stopped at the LTE= sentinel
    assert route.call_count == 2


def test_list_open_orders_forwards_token_id_as_asset_id(secure, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/data/orders").mock(
        return_value=httpx.Response(
            200, json={"limit": 100, "count": 0, "next_cursor": "LTE=", "data": []}
        )
    )
    pag = secure.list_open_orders(token_id=LONG_TOKEN, id="ord_x")
    assert pag.first_page().items == ()
    params = dict(route.calls[0].request.url.params)
    assert params["asset_id"] == LONG_TOKEN  # token_id -> asset_id
    assert params["id"] == "ord_x"


def test_list_account_trades_walks_cursor_and_adapts(secure, respx_mock):
    def _pages(request: httpx.Request) -> httpx.Response:
        cursor = dict(request.url.params).get("next_cursor")
        if cursor in (None, "MA=="):
            return httpx.Response(
                200,
                json={
                    "limit": 100,
                    "count": 1,
                    "next_cursor": "MTAw",
                    "data": [{"id": "0xt1", "market": "0xcond", "side": "BUY",
                              "price": "0.5", "size": "10"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "limit": 100,
                "count": 1,
                "next_cursor": "LTE=",
                "data": [{"id": "0xt2", "market": "0xcond", "side": "SELL",
                          "price": "0.55", "size": "4"}],
            },
        )

    route = respx_mock.get(f"{BASE_URL}/v1/data/trades").mock(side_effect=_pages)
    pag = secure.list_account_trades(market="0xcond")
    items = list(pag.iter_items())
    assert [t.id for t in items] == ["0xt1", "0xt2"]
    assert items[0].price == Decimal("0.5")
    assert items[1].size == Decimal("4")
    assert route.call_count == 2


def test_list_account_trades_forwards_filters(secure, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/data/trades").mock(
        return_value=httpx.Response(
            200, json={"limit": 100, "count": 0, "next_cursor": "LTE=", "data": []}
        )
    )
    pag = secure.list_account_trades(
        token_id=LONG_TOKEN, market="0xcond", before="100", after="50"
    )
    assert pag.first_page().items == ()
    params = dict(route.calls[0].request.url.params)
    assert params["asset_id"] == LONG_TOKEN
    assert params["market"] == "0xcond"
    assert params["before"] == "100"
    assert params["after"] == "50"


# ── shared read delegation (spot check) ─────────────────────────────────────


def test_get_order_book_delegates_to_v1_read(secure, respx_mock):
    from polysim_polymarket import OrderBook

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
                "tick_size": "0.01",
                "neg_risk": False,
            },
        )
    )
    book = secure.get_order_book(token_id=LONG_TOKEN)
    assert isinstance(book, OrderBook)
    assert book.token_id == LONG_TOKEN
    assert book.bids[-1].price == Decimal("0.40")  # best bid
    assert book.asks[-1].price == Decimal("0.60")  # best ask


def test_get_midpoint_delegates(secure, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
            },
        )
    )
    assert secure.get_midpoint(token_id=LONG_TOKEN) == Decimal("0.50")
