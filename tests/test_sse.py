"""SSE spot-stream helper: event parsing, param building, reconnect."""

from __future__ import annotations

import httpx
import pytest
import respx

from polysim_sdk.exceptions import ApiError
from polysim_sdk.sse import _parse_event_block, aspot_stream, spot_stream

BASE_URL = "https://api.polysimulator.test"

# Mirrors the real /prices/stream framing: `event:`/`data:` fields, a blank
# line per event, and the keepalive's `: <padding>` comment line.
_SSE_BODY = (
    "event: crypto_price\n"
    'data: {"symbol": "BTC", "price": 64588.0, "source": "chainlink_rtds"}\n'
    "\n"
    "event: keepalive\n"
    'data: {"ts": "2026-06-14T12:00:00Z", "n": 1}\n'
    ": xxxxxxxxxx\n"
    "\n"
    "event: crypto_price_batch\n"
    'data: {"updates": [{"symbol": "ETH", "price": 3400.0}], "count": 1}\n'
    "\n"
)


# ── block parser ──────────────────────────────────────────────────────────


def test_parse_event_block_decodes_json_data():
    ev = _parse_event_block('event: crypto_price\ndata: {"symbol": "BTC"}')
    assert ev == {"event": "crypto_price", "data": {"symbol": "BTC"}}


def test_parse_event_block_ignores_comment_only():
    # A pure keepalive-padding comment block has no usable fields.
    assert _parse_event_block(": keepalive-padding-xxxxx") is None
    assert _parse_event_block("") is None


def test_parse_event_block_non_json_data_falls_back_to_raw():
    ev = _parse_event_block("event: note\ndata: hello world")
    assert ev == {"event": "note", "data": "hello world"}


def test_parse_event_block_multiline_data_joined():
    ev = _parse_event_block("data: line1\ndata: line2")
    # No `event:` field → defaults to "message"; data lines join with newline.
    assert ev == {"event": "message", "data": "line1\nline2"}


def test_parse_event_block_strips_single_leading_space():
    # Per SSE spec a single space after the colon is stripped, not more.
    ev = _parse_event_block("data:  two-spaces")
    assert ev == {"event": "message", "data": " two-spaces"}


# ── sync stream ─────────────────────────────────────────────────────────────


def test_spot_stream_yields_events_and_sets_params(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/prices/stream").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_SSE_BODY
        )
    )
    gen = spot_stream(client, ["BTC", "ETH"], crypto_source="chainlink", max_reconnects=0)
    events = []
    for ev in gen:
        events.append(ev)
        if len(events) >= 3:
            break
    gen.close()

    assert [e["event"] for e in events] == ["crypto_price", "keepalive", "crypto_price_batch"]
    assert events[0]["data"]["symbol"] == "BTC"
    assert events[2]["data"]["updates"][0]["symbol"] == "ETH"

    params = dict(route.calls.last.request.url.params)
    assert params["crypto"] == "BTC,ETH"
    assert params["crypto_source"] == "chainlink"
    # per-connection cache-buster present (defeats CDN stream coalescing)
    assert params["_"]


def test_spot_stream_condition_ids_param(client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/prices/stream").mock(
        return_value=httpx.Response(200, content=_SSE_BODY)
    )
    gen = spot_stream(client, condition_ids=["0xA", "0xB"], background=True, max_reconnects=0)
    next(gen)
    gen.close()
    params = dict(route.calls.last.request.url.params)
    assert params["condition_ids"] == "0xA,0xB"
    assert params["background"] == "true"
    assert "crypto" not in params


def test_spot_stream_permanent_4xx_raises(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/prices/stream").mock(
        return_value=httpx.Response(404, json={"error": "nope"})
    )
    gen = spot_stream(client, ["BTC"], max_reconnects=0)
    with pytest.raises(ApiError) as exc:
        next(gen)
    assert exc.value.status_code == 404


def test_spot_stream_retryable_5xx_exhausts_to_api_error(client, respx_mock):
    respx_mock.get(f"{BASE_URL}/prices/stream").mock(
        return_value=httpx.Response(503, json={"error": "unavailable"})
    )
    gen = spot_stream(client, ["BTC"], max_reconnects=0, reconnect_min_interval=0)
    with pytest.raises(ApiError) as exc:
        next(gen)
    assert exc.value.status_code == 503


def test_spot_stream_cache_buster_unique_per_connection(client, respx_mock):
    # First connection serves a single event then the stream ends (clean
    # close); the helper reconnects. Both connections must carry distinct
    # cache-buster nonces.
    bodies = [
        httpx.Response(200, content='event: crypto_price\ndata: {"symbol": "BTC"}\n\n'),
        httpx.Response(200, content='event: crypto_price\ndata: {"symbol": "ETH"}\n\n'),
    ]
    route = respx_mock.get(f"{BASE_URL}/prices/stream").mock(side_effect=bodies)
    gen = spot_stream(client, ["BTC"], max_reconnects=1, reconnect_min_interval=0)
    events = []
    for ev in gen:
        events.append(ev)
        if len(events) >= 2:
            break
    gen.close()
    assert [e["data"]["symbol"] for e in events] == ["BTC", "ETH"]
    nonces = {dict(c.request.url.params).get("_") for c in route.calls}
    assert len(nonces) == 2  # distinct per connection


# ── async stream ────────────────────────────────────────────────────────────


@respx.mock
async def test_aspot_stream_yields_events(aclient):
    route = respx.get(f"{BASE_URL}/prices/stream").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_SSE_BODY
        )
    )
    gen = aspot_stream(aclient, ["BTC"], crypto_source="binance", max_reconnects=0)
    events = []
    async for ev in gen:
        events.append(ev)
        if len(events) >= 2:
            break
    await gen.aclose()
    assert events[0]["event"] == "crypto_price"
    assert events[0]["data"]["symbol"] == "BTC"
    params = dict(route.calls.last.request.url.params)
    assert params["crypto"] == "BTC"
    assert params["crypto_source"] == "binance"
    assert params["_"]


@respx.mock
async def test_aspot_stream_permanent_4xx_raises(aclient):
    respx.get(f"{BASE_URL}/prices/stream").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )
    gen = aspot_stream(aclient, ["BTC"], max_reconnects=0)
    with pytest.raises(ApiError) as exc:
        await gen.__anext__()
    assert exc.value.status_code == 403
