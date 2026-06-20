"""Drop-in ``ClobClient`` UpDown colon-form resolution.

UpDown markets (BTC/ETH/… 5m, 15m, 1h…) carry ``Up`` / ``Down`` outcomes
rather than ``Yes`` / ``No``. The drop-in's ``condition_id:OUTCOME`` colon form
(its own convenience extension, **not** py-clob-client parity — see the v1 guard
in ``test_clob_parity.py``) must therefore resolve ``:UP`` / ``:DOWN`` the same
way it resolves ``:YES`` / ``:NO``: split off the outcome and keep routing on the
condition id, rather than swallowing the whole ``cid:UP`` string as the market id
(which 404s the backend with "Market not found: 0xCID:UP").

The backend matches the order ``outcome`` field case-insensitively, so the
uppercase ``"UP"`` / ``"DOWN"`` this returns is accepted as written.
"""

from __future__ import annotations

import httpx
import pytest

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"


@pytest.fixture
def clob():
    from polysim_clob_client.client import ClobClient

    c = ClobClient(host=BASE_URL, key=API_KEY)
    c._client._transport._floor_interval = 0.0  # keep the suite fast
    yield c
    c.close()


# ── _split_token / _resolve_token resolve the UpDown colon form ────────────────


def test_split_token_resolves_updown_colon_form(clob):
    assert clob._split_token("0xabc:UP") == ("0xabc", "UP")
    assert clob._split_token("0xabc:DOWN") == ("0xabc", "DOWN")
    # Case-insensitive in, uppercase out (consistent with :YES / :NO).
    assert clob._split_token("0xabc:up") == ("0xabc", "UP")
    assert clob._split_token("0xabc:down") == ("0xabc", "DOWN")


def test_split_token_updown_regression_does_not_swallow_outcome(clob):
    # Regression guard for the 404 bug: pre-fix this returned the whole
    # ``cid:UP`` string as the market id (outcome "YES").
    market_id, outcome = clob._split_token("0xCID:UP")
    assert market_id == "0xCID"
    assert ":" not in market_id
    assert outcome == "UP"


def test_resolve_token_updown_colon_form_makes_no_network(clob, respx_mock):
    # respx_mock with no routes raises on ANY request, so reaching the
    # assertions proves the UpDown colon form resolves purely locally (no
    # markets-by-token round-trip).
    assert clob._resolve_token("c1:UP") == ("c1", "UP")
    assert clob._resolve_token("c1:down") == ("c1", "DOWN")
    assert respx_mock.calls.call_count == 0


# ── the colon form routes book reads to the right UpDown outcome ───────────────


def test_get_order_book_updown_colon_form_routes_to_condition(clob, respx_mock):
    # ``condition_id:UP`` must keep condition-id routing and thread "UP" through
    # as the outcome query param so it reads the UP book — not fall through to
    # the bare-token endpoint with the whole colon string as the token id.
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
    book = clob.get_order_book("c1:UP")
    assert dict(route.calls.last.request.url.params)["outcome"] == "UP"
    # asset_id echoes exactly what the caller passed (py-clob contract).
    assert book.asset_id == "c1:UP"


def test_create_and_post_order_updown_colon_form_sends_up_outcome(clob, respx_mock):
    # End-to-end: an order on a ``condition_id:UP`` token must POST with
    # market_id == the condition id and outcome == "UP" — exactly what a bot
    # trading a fresh BTC/ETH 5m UpDown market naturally writes.
    from polysim_clob_client.clob_types import OrderArgs

    route = respx_mock.post(f"{BASE_URL}/v1/orders").mock(
        return_value=httpx.Response(200, json={"order_id": "o_up", "status": "OPEN"})
    )
    resp = clob.create_and_post_order(
        OrderArgs(token_id="0xCID:UP", price=0.55, size=10, side="BUY")
    )
    assert resp["order_id"] == "o_up"
    body = route.calls.last.request.read().decode().replace(" ", "")
    assert '"market_id":"0xCID"' in body
    assert '"outcome":"UP"' in body
