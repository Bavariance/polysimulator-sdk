"""Pagination: cursor<->offset translation + native limit/offset iterators."""

from __future__ import annotations

import httpx

from polysim_clob_client.client import _decode_cursor, _encode_cursor, _next_cursor
from polysim_clob_client.constants import END_CURSOR, START_CURSOR
from polysim_sdk.pagination import iter_markets, iter_orders

BASE_URL = "https://api.polysimulator.test"


# ── cursor codec ───────────────────────────────────────────────────────────


def test_start_cursor_decodes_to_zero():
    assert _decode_cursor(START_CURSOR) == 0
    assert _decode_cursor(None) == 0
    assert _decode_cursor("") == 0


def test_end_cursor_decodes_to_minus_one():
    assert _decode_cursor(END_CURSOR) == -1


def test_round_trip_offset():
    for off in (0, 1, 50, 100, 12345):
        assert _decode_cursor(_encode_cursor(off)) == off


def test_garbage_cursor_falls_back_to_zero():
    assert _decode_cursor("!!!not-base64!!!") == 0


def test_next_cursor_full_page_advances():
    # full page (len == limit) -> advance by limit
    nxt = _next_cursor(offset=0, page_len=100, limit=100)
    assert _decode_cursor(nxt) == 100


def test_next_cursor_short_page_terminates():
    assert _next_cursor(offset=0, page_len=7, limit=100) == END_CURSOR


def test_full_cursor_loop_terminates(respx_mock):
    """A `while cursor != END_CURSOR` loop must terminate (parity with PM)."""
    from polysim_clob_client.client import ClobClient

    # page 1 full (100), page 2 short (3) -> loop ends.
    page1 = [{"condition_id": f"c{i}"} for i in range(100)]
    page2 = [{"condition_id": f"c{i}"} for i in range(100, 103)]

    def responder(request):
        offset = int(dict(request.url.params).get("offset", "0"))
        return httpx.Response(200, json=page1 if offset == 0 else page2)

    respx_mock.get(f"{BASE_URL}/v1/markets").mock(side_effect=responder)

    c = ClobClient(host=BASE_URL, key="ps_live_x")
    c._client._transport._floor_interval = 0.0
    seen = 0
    cursor = START_CURSOR
    guard = 0
    while cursor != END_CURSOR:
        guard += 1
        assert guard < 10, "cursor loop failed to terminate"
        page = c.get_markets(cursor)
        seen += len(page["data"])
        cursor = page["next_cursor"]
    assert seen == 103
    c.close()


# ── native iterators ───────────────────────────────────────────────────────


def test_iter_markets_stops_on_short_page(client, respx_mock):
    page1 = [{"condition_id": f"c{i}"} for i in range(100)]
    page2 = [{"condition_id": "c100"}]

    def responder(request):
        offset = int(dict(request.url.params).get("offset", "0"))
        return httpx.Response(200, json=page1 if offset == 0 else page2)

    respx_mock.get(f"{BASE_URL}/v1/markets").mock(side_effect=responder)
    rows = list(iter_markets(client))
    assert len(rows) == 101


def test_iter_orders_empty_first_page(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/orders").mock(return_value=httpx.Response(200, json=[]))
    assert list(iter_orders(client, status="OPEN")) == []
