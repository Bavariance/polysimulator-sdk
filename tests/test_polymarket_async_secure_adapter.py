"""Behavioral (respx-mocked) tests for ``polysim_polymarket.AsyncSecureClient``.

The async twin of ``test_polymarket_secure_trading_adapter`` /
``test_polymarket_secure_onchain`` / ``test_polymarket_secure_rewards``. All
respx-mocked: NO real network, NO credentials, NO live trading, NO real chain /
web3 / eth_account, NO prod writes. Each test awaits one method and proves it
puts the right bytes on the wire (route + body, incl. the worst-acceptable-price
cap on market orders) and adapts the backend reply onto py-sdk's return model —
exactly as the sync suite proves for ``SecureClient``, demonstrating the async
twin's behaviour matches by construction (shared ``_account`` / ``_trade`` /
``_onchain`` / ``_common``).

Covers:
  * a CLOB read (``get_order_book``) returns the right model, awaited;
  * a limit + market order await-submit the right body incl. the worst-price cap;
  * ``post_orders`` batch;
  * each cancel route; ``cancel_all`` confirmation form;
  * on-chain methods' ``await handle.wait()`` yields an instant TransactionOutcome
    (no network); ``setup_trading_approvals``'s ``wait()`` is None;
  * rewards async-empty; scoring False;
  * builder/RFQ raise NotImplementedError with the shared messages.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"

# A real Polymarket CLOB outcome-token id is a long all-digit string. We address
# orders by the PolySim colon form to avoid a reverse-resolution network call in
# the body tests; LONG_TOKEN is used only where we assert the reverse-resolve.
COLON_TOKEN = "0xcond:YES"
LONG_TOKEN = "71321045679252212594626385532706912750332728571942532289631379312455583992563"

# A valid-format paper transaction hash the on-chain paper handle returns.
PAPER_TX_HASH = "0x" + "00" * 31 + "01"


@pytest.fixture
async def secure():
    from polysim_polymarket import AsyncSecureClient

    c = AsyncSecureClient(host=BASE_URL, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    await c.close()


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


# ── shared CLOB read: awaited, right model ──────────────────────────────────


async def test_get_order_book_awaits_and_adapts(secure, respx_mock):
    from polysim_polymarket import OrderBook

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.45", "size": "20"}],
                "asks": [{"price": "0.55", "size": "20"}],
                "min_order_size": "1",
                "tick_size": "0.01",
                "neg_risk": False,
                "hash": "0xhash",
            },
        )
    )
    book = await secure.get_order_book(token_id="711")
    assert isinstance(book, OrderBook)
    # best bid 0.45 / best ask 0.55 (py-sdk ordering: bids asc, asks desc)
    assert book.bids[-1].price == Decimal("0.45")
    assert book.asks[-1].price == Decimal("0.55")


# ── create_limit_order: inert-signed + paper body (await-resolves) ──────────


async def test_create_limit_order_returns_inert_signed_order(secure, respx_mock):
    from polysim_polymarket import SignedOrder

    order = await secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.55", size="10", side="BUY"
    )
    assert isinstance(order, SignedOrder)
    assert order.token_id == COLON_TOKEN
    assert order.side == "BUY"
    assert order.order_type == "GTC"
    # signing is inert — no real signature/signer/salt
    assert order.signature == ""
    assert order.signer == ""
    assert order.salt == 0
    body = order.paper_body
    assert body["market_id"] == "0xcond"
    assert body["outcome"] == "YES"
    assert body["order_type"] == "limit"
    assert body["price"] == "0.55"
    assert body["quantity"] == "10"


async def test_create_limit_order_reverse_resolves_long_token(secure, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets-by-token/{LONG_TOKEN}").mock(
        return_value=httpx.Response(200, json={"condition_id": "0xRESOLVED", "outcome": "NO"})
    )
    order = await secure.create_limit_order(
        token_id=LONG_TOKEN, price="0.3", size="7", side="BUY"
    )
    assert route.called
    assert order.paper_body["market_id"] == "0xRESOLVED"
    assert order.paper_body["outcome"] == "NO"


# ── create_market_order: worst-price cap (BUY 0.99 / SELL 0.01) ─────────────


async def test_create_market_buy_defaults_worst_price_099(secure, respx_mock):
    order = await secure.create_market_order(token_id=COLON_TOKEN, side="BUY", amount="20")
    body = order.paper_body
    assert body["order_type"] == "market"
    assert body["time_in_force"] == "FAK"
    assert body["amount"] == "20"
    assert "quantity" not in body
    # worst-acceptable price defaults to 0.99 for a BUY (never uncapped)
    assert body["price"] == "0.99"


async def test_create_market_sell_defaults_worst_price_001(secure, respx_mock):
    order = await secure.create_market_order(token_id=COLON_TOKEN, side="SELL", shares="15")
    body = order.paper_body
    assert body["quantity"] == "15"
    assert "amount" not in body
    assert body["price"] == "0.01"


# ── post_order / place_*: await-submit the right body ───────────────────────


async def test_post_order_sends_body_to_v1_orders(secure, respx_mock):
    from polysim_polymarket import AcceptedOrder

    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(
            200, json={"order_id": "o1", "status": "FILLED", "making_amount": "5.5"}
        )
    )
    order = await secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.55", size="10", side="BUY"
    )
    resp = await secure.post_order(order)
    assert route.called
    sent = _body(route.calls.last.request)
    assert sent["market_id"] == "0xcond"
    assert sent["side"] == "BUY"
    assert sent["price"] == "0.55"
    assert sent["quantity"] == "10"
    assert isinstance(resp, AcceptedOrder)
    assert resp.order_id == "o1"
    assert resp.status == "matched"
    assert resp.making_amount == Decimal("5.5")


async def test_place_market_order_builds_and_posts_with_cap(secure, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "p2", "status": "FILLED"})
    )
    resp = await secure.place_market_order(token_id=COLON_TOKEN, side="BUY", amount="25")
    sent = _body(route.calls.last.request)
    assert sent["amount"] == "25"
    assert sent["price"] == "0.99"  # worst-price cap forwarded
    assert resp.order_id == "p2"


async def test_post_orders_batch(secure, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/orders/batch").mock(
        return_value=httpx.Response(
            200,
            json={
                "orders": [
                    {"order_id": "a", "status": "live"},
                    {"order_id": "b", "status": "FILLED"},
                ]
            },
        )
    )
    o1 = await secure.create_limit_order(token_id=COLON_TOKEN, price="0.5", size="10", side="BUY")
    o2 = await secure.create_limit_order(token_id=COLON_TOKEN, price="0.6", size="5", side="SELL")
    resps = await secure.post_orders([o1, o2])
    assert route.called
    sent = _body(route.calls.last.request)
    assert len(sent["orders"]) == 2
    assert sent["orders"][0]["price"] == "0.5"
    assert sent["orders"][1]["side"] == "SELL"
    assert tuple(r.order_id for r in resps) == ("a", "b")
    assert resps[0].status == "live"
    assert resps[1].status == "matched"


# ── cancel routes ───────────────────────────────────────────────────────────


async def test_cancel_order_hits_delete_route(secure, respx_mock):
    from polysim_polymarket import CancelOrdersResponse

    route = respx_mock.delete(f"{BASE_URL}/v1/orders/ord_9").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    resp = await secure.cancel_order(order_id="ord_9")
    assert route.called
    assert isinstance(resp, CancelOrdersResponse)
    assert resp.canceled == ("ord_9",)
    assert resp.not_canceled == {}


async def test_cancel_orders_loops_single_route(secure, respx_mock):
    r1 = respx_mock.delete(f"{BASE_URL}/v1/orders/a").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    r2 = respx_mock.delete(f"{BASE_URL}/v1/orders/b").mock(
        return_value=httpx.Response(400, json={"detail": "already filled"})
    )
    resp = await secure.cancel_orders(order_ids=["a", "b"])
    assert r1.called and r2.called
    assert resp.canceled == ("a",)
    assert "b" in resp.not_canceled


async def test_cancel_market_orders_resolves_token_id(secure, respx_mock):
    route = respx_mock.delete(f"{BASE_URL}/v1/cancel-market-orders").mock(
        return_value=httpx.Response(200, json={"canceled": []})
    )
    await secure.cancel_market_orders(token_id=COLON_TOKEN)
    assert dict(route.calls.last.request.url.params)["market"] == "0xcond"


async def test_cancel_all_sends_confirmation_form(secure, respx_mock):
    route = respx_mock.post(f"{BASE_URL}/v1/cancel-all").mock(
        return_value=httpx.Response(200, json={"canceled": ["x", "y", "z"]})
    )
    resp = await secure.cancel_all()
    assert route.called
    req = route.calls.last.request
    header_ok = req.headers.get("X-Confirm-Cancel-All", "").lower() == "true"
    query_ok = dict(req.url.params).get("confirm", "").lower() == "true"
    assert header_ok or query_ok, "cancel_all MUST send a confirmation form"
    assert resp.canceled == ("x", "y", "z")


# ── account read: get_balance_allowance awaited ─────────────────────────────


async def test_get_balance_allowance_awaits_and_adapts(secure, respx_mock):
    from polysim_polymarket import BalanceAllowance

    respx_mock.get(f"{BASE_URL}/v1/account/balance").mock(
        return_value=httpx.Response(200, json={"balance": "12.5"})
    )
    ba = await secure.get_balance_allowance(asset_type="COLLATERAL")
    assert isinstance(ba, BalanceAllowance)
    # $12.5 -> 12_500_000 base units (USDC 6 decimals)
    assert ba.balance == 12_500_000
    assert ba.allowances == {}


# ── on-chain PAPER no-ops: await handle.wait() is instant, no network ────────


async def test_approve_erc20_paper_handle_waits_instantly(secure, respx_mock):
    from polysim_polymarket import TransactionOutcome

    # NO route registered: a paper no-op must make ZERO network calls.
    handle = await secure.approve_erc20(
        token_address="0x" + "ab" * 20,
        spender_address="0x" + "cd" * 20,
        amount="max",
    )
    # wait() is a coroutine resolving instantly with the paper outcome.
    outcome = await asyncio.wait_for(handle.wait(), timeout=1.0)
    assert isinstance(outcome, TransactionOutcome)
    assert outcome.transaction_hash == PAPER_TX_HASH
    assert outcome.transaction_id is None
    assert respx_mock.calls.call_count == 0


async def test_split_position_requires_exactly_one_identifier(secure):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="Provide exactly one of condition_id or legs"):
        await secure.split_position(amount=10)


async def test_redeem_positions_paper_handle(secure, respx_mock):
    from polysim_polymarket import TransactionOutcome

    handle = await secure.redeem_positions(condition_id="0xcond")
    outcome = await handle.wait()
    assert isinstance(outcome, TransactionOutcome)
    assert outcome.transaction_hash == PAPER_TX_HASH
    assert respx_mock.calls.call_count == 0


async def test_setup_trading_approvals_wait_returns_none(secure, respx_mock):
    handle = await secure.setup_trading_approvals()
    result = await handle.wait()
    assert result is None
    assert respx_mock.calls.call_count == 0


async def test_setup_gasless_wallet_returns_self(secure):
    same = await secure.setup_gasless_wallet()
    assert same is secure


async def test_is_gasless_ready_true(secure):
    assert await secure.is_gasless_ready() is True


# ── rewards + scoring: honest async empties ─────────────────────────────────


async def test_get_order_scoring_false(secure):
    assert await secure.get_order_scoring(order_id="o1") is False


async def test_get_orders_scoring_all_false(secure):
    result = await secure.get_orders_scoring(order_ids=["a", "b", "c"])
    assert result == {"a": False, "b": False, "c": False}


async def test_list_current_rewards_empty_async_paginator(secure, respx_mock):
    pag = secure.list_current_rewards()
    page = await pag.first_page()
    assert page.items == ()
    assert page.has_more is False
    items = [item async for item in pag.iter_items()]
    assert items == []
    assert respx_mock.calls.call_count == 0


async def test_get_total_earnings_empty_tuple(secure):
    assert await secure.get_total_earnings_for_user_for_day(date="2026-06-18") == ()


async def test_get_reward_percentages_empty_dict(secure):
    assert await secure.get_reward_percentages() == {}


# ── builder / RFQ: NotImplementedError with the SHARED messages ─────────────


async def test_get_builder_volumes_not_implemented(secure):
    from polysim_polymarket.clients import _onchain

    with pytest.raises(NotImplementedError) as exc:
        await secure.get_builder_volumes()
    assert str(exc.value) == _onchain.BUILDER_NOT_SIMULATED


def test_list_builder_trades_not_implemented(secure):
    from polysim_polymarket.clients import _onchain

    with pytest.raises(NotImplementedError) as exc:
        secure.list_builder_trades(builder_code="bc1")
    assert str(exc.value) == _onchain.BUILDER_NOT_SIMULATED


async def test_get_builder_fee_rates_not_implemented(secure):
    from polysim_polymarket.clients import _onchain

    with pytest.raises(NotImplementedError) as exc:
        await secure.get_builder_fee_rates("bc1")
    assert str(exc.value) == _onchain.BUILDER_NOT_SIMULATED


def test_list_builder_leaderboard_not_implemented(secure):
    from polysim_polymarket.clients import _onchain

    with pytest.raises(NotImplementedError) as exc:
        secure.list_builder_leaderboard()
    assert str(exc.value) == _onchain.BUILDER_NOT_SIMULATED


# ── list reads: awaited paging over the data API ────────────────────────────


async def test_list_open_orders_pages_data_api(secure, respx_mock):
    from polysim_polymarket import OpenOrder

    respx_mock.get(f"{BASE_URL}/v1/data/orders").mock(
        return_value=httpx.Response(
            200,
            json={
                "limit": 100,
                "count": 1,
                "next_cursor": "LTE=",
                "data": [
                    {
                        "id": "ord1",
                        "asset_id": "711",
                        "market": "0xcond",
                        "side": "BUY",
                        "price": "0.5",
                        "original_size": "10",
                        "size_matched": "0",
                    }
                ],
            },
        )
    )
    pag = secure.list_open_orders()
    page = await pag.first_page()
    assert page.has_more is False
    assert len(page.items) == 1
    assert isinstance(page.items[0], OpenOrder)


# ── async context manager ───────────────────────────────────────────────────


async def test_async_context_manager_closes(respx_mock):
    from polysim_polymarket import AsyncSecureClient

    async with AsyncSecureClient(host=BASE_URL, api_key=API_KEY) as c:
        c._client._transport._floor_interval = 0.0
        respx_mock.get(f"{BASE_URL}/v1/account/balance").mock(
            return_value=httpx.Response(200, json={"balance": "1.0"})
        )
        ba = await c.get_balance_allowance(asset_type="COLLATERAL")
        assert ba.balance == 1_000_000
