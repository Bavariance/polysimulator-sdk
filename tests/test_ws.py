"""WebSocket helpers: url scheme, token extraction, subscribe frame, decode."""

from __future__ import annotations

import json

import httpx
import pytest

from polysim_sdk import ws as wsmod
from polysim_sdk.ws import _default_subscribe, _extract_token, _ws_base

BASE_URL = "https://api.polysimulator.test"


def test_ws_base_https_to_wss():
    assert _ws_base("https://api.polysimulator.com") == "wss://api.polysimulator.com"


def test_ws_base_http_to_ws():
    assert _ws_base("http://localhost:8000") == "ws://localhost:8000"


def test_extract_token_variants():
    assert _extract_token({"token": "T1"}) == "T1"
    assert _extract_token({"ws_token": "T2"}) == "T2"
    assert _extract_token({"access_token": "T3"}) == "T3"
    assert _extract_token({"jwt": "T4"}) == "T4"


def test_extract_token_missing_raises():
    with pytest.raises(ValueError):
        _extract_token({"nope": "x"})


def test_default_subscribe_frame():
    # The backend /v1/ws/prices subscribe dispatch only recognises the
    # ``markets`` (PolySim) and ``conditions`` (PM) keys; any other key —
    # including the legacy ``condition_ids`` — falls to the else branch,
    # subscribes nothing, and acks ``markets: []`` → zero ticks. The default
    # frame MUST use ``markets``.
    assert _default_subscribe(["c1", "c2"]) == {
        "action": "subscribe",
        "markets": ["c1", "c2"],
    }
    assert _default_subscribe(None) is None
    assert _default_subscribe([]) is None


# ── streaming: fake the websocket connection ───────────────────────────────


class _FakeWS:
    """Minimal async-context + async-iterable websocket double."""

    def __init__(self, frames):
        self._frames = frames
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        async def gen():
            for f in self._frames:
                yield f

        return gen()


async def test_prices_stream_decodes_and_subscribes(aclient, respx_mock, monkeypatch):
    respx_mock.post(f"{BASE_URL}/v1/keys/ws-token").mock(
        return_value=httpx.Response(200, json={"token": "jwt-abc"})
    )

    captured_urls = []
    fake = _FakeWS(
        [json.dumps({"type": "price", "p": "0.5"}), json.dumps({"type": "price", "p": "0.6"})]
    )

    def fake_connect(url, **kwargs):
        captured_urls.append(url)
        # second connection attempt raises to terminate the reconnect loop
        if len(captured_urls) > 1:
            raise OSError("closed")
        return fake

    monkeypatch.setattr(wsmod.websockets, "connect", fake_connect)

    events = []
    with pytest.raises(OSError):
        async for ev in wsmod.aprices_stream(aclient, ["c1", "c2"], max_reconnects=0):
            events.append(ev)

    assert events == [{"type": "price", "p": "0.5"}, {"type": "price", "p": "0.6"}]
    # token was put on the URL
    assert "token=jwt-abc" in captured_urls[0]
    assert "/v1/ws/prices" in captured_urls[0]
    # subscribe frame was sent with the backend-recognised ``markets`` key
    assert json.loads(fake.sent[0]) == {"action": "subscribe", "markets": ["c1", "c2"]}


async def test_stream_non_json_frame_wrapped(aclient, respx_mock, monkeypatch):
    respx_mock.post(f"{BASE_URL}/v1/keys/ws-token").mock(
        return_value=httpx.Response(200, json={"token": "jwt-xyz"})
    )
    fake = _FakeWS(["not-json-at-all"])
    calls = []

    def fake_connect(url, **kwargs):
        calls.append(url)
        if len(calls) > 1:
            raise OSError("closed")
        return fake

    monkeypatch.setattr(wsmod.websockets, "connect", fake_connect)

    events = []
    with pytest.raises(OSError):
        async for ev in wsmod.aexecutions_stream(aclient, max_reconnects=0):
            events.append(ev)
    assert events == [{"raw": "not-json-at-all"}]
