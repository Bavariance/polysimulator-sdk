"""WebSocket streaming helpers for the PolySimulator real-time API.

The flow for every stream is the same:

1. Mint a short-lived (60s) WS JWT via ``POST /v1/keys/ws-token``.
2. Open ``wss://<host>/v1/ws/<channel>?token=<jwt>``.
3. (prices) send a subscribe frame naming the markets you want.
4. Yield decoded JSON events until the socket closes, then reconnect with a
   freshly-minted token and capped exponential back-off.

Async generators are the primary interface; thin synchronous wrappers drive a
private event loop so blocking strategy code can ``for event in
prices_stream(...)`` directly.

The exact subscribe-frame and event shapes follow the server's native
``/v1/ws/prices`` and ``/v1/ws/executions`` channels. Pass ``subscribe=`` to
override the frame if the server expects a different envelope (e.g. the
PM-compat ``/v1/ws/market`` channel).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Any

import websockets

if TYPE_CHECKING:
    from polysim_sdk.aio import AsyncPolySimClient
    from polysim_sdk.client import PolySimClient

_MAX_BACKOFF_SECONDS = 30.0
_BASE_BACKOFF_SECONDS = 0.5


def _ws_base(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :]
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :]
    return base_url


def _extract_token(resp: dict[str, Any]) -> str:
    for key in ("token", "ws_token", "access_token", "jwt"):
        val = resp.get(key)
        if isinstance(val, str) and val:
            return val
    raise ValueError(f"ws-token response had no recognisable token field: {list(resp)}")


def _default_subscribe(condition_ids: Sequence[str] | None) -> dict[str, Any] | None:
    # The backend ``/v1/ws/prices`` subscribe dispatch only recognises the
    # ``markets`` (PolySim-native) and ``conditions`` (PM-compat) keys. Any
    # other key — the legacy ``condition_ids`` included — falls through to the
    # else branch, subscribes nothing, and acks ``{"type":"subscribed",
    # "markets":[]}`` → the socket stays open but never delivers a single tick.
    # PolySim broadcasts by condition_id, so the values are condition_ids; only
    # the *envelope key* must be ``markets``.
    if not condition_ids:
        return None
    return {"action": "subscribe", "markets": list(condition_ids)}


# ── Async core ──────────────────────────────────────────────────────────


async def _astream(
    *,
    ws_base: str,
    channel: str,
    mint_token: Any,
    subscribe: dict[str, Any] | None,
    max_reconnects: int | None,
) -> AsyncGenerator[dict[str, Any], None]:
    attempts = 0
    backoff = _BASE_BACKOFF_SECONDS
    while True:
        try:
            token = _extract_token(await mint_token())
            url = f"{ws_base}/v1/ws/{channel}?token={token}"
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                backoff = _BASE_BACKOFF_SECONDS  # reset on a clean connect
                if subscribe is not None:
                    await ws.send(json.dumps(subscribe))
                async for raw in ws:
                    try:
                        yield json.loads(raw)
                    except (ValueError, TypeError):
                        yield {"raw": raw}
        except (OSError, websockets.WebSocketException) as exc:
            attempts += 1
            if max_reconnects is not None and attempts > max_reconnects:
                raise
            await asyncio.sleep(min(backoff, _MAX_BACKOFF_SECONDS))
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            _ = exc  # surfaced via logging by the caller if desired
            continue


async def aprices_stream(
    client: AsyncPolySimClient,
    condition_ids: Sequence[str] | None = None,
    *,
    subscribe: dict[str, Any] | None = None,
    max_reconnects: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Async: yield real-time price events from ``/v1/ws/prices``."""
    frame = subscribe if subscribe is not None else _default_subscribe(condition_ids)
    async for event in _astream(
        ws_base=_ws_base(client.base_url),
        channel="prices",
        mint_token=client.ws_token,
        subscribe=frame,
        max_reconnects=max_reconnects,
    ):
        yield event


async def aexecutions_stream(
    client: AsyncPolySimClient,
    *,
    subscribe: dict[str, Any] | None = None,
    max_reconnects: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Async: yield real-time fill/execution events from ``/v1/ws/executions``."""
    async for event in _astream(
        ws_base=_ws_base(client.base_url),
        channel="executions",
        mint_token=client.ws_token,
        subscribe=subscribe,
        max_reconnects=max_reconnects,
    ):
        yield event


# ── Sync bridge ─────────────────────────────────────────────────────────


def _drive(agen: AsyncGenerator[dict[str, Any], None]) -> Iterator[dict[str, Any]]:
    """Pump an async generator from synchronous code via a private loop."""
    loop = asyncio.new_event_loop()
    try:
        while True:
            try:
                yield loop.run_until_complete(agen.__anext__())
            except StopAsyncIteration:
                return
    finally:
        loop.run_until_complete(agen.aclose())
        loop.close()


def prices_stream(
    client: PolySimClient,
    condition_ids: Sequence[str] | None = None,
    *,
    subscribe: dict[str, Any] | None = None,
    max_reconnects: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Blocking: yield real-time price events from ``/v1/ws/prices``.

    Takes the sync :class:`PolySimClient`; only its ``base_url`` and
    ``ws_token`` are used, both bridged onto the private event loop.
    """
    frame = subscribe if subscribe is not None else _default_subscribe(condition_ids)

    async def _mint() -> dict[str, Any]:
        return client.ws_token()

    return _drive(
        _astream(
            ws_base=_ws_base(client.base_url),
            channel="prices",
            mint_token=_mint,
            subscribe=frame,
            max_reconnects=max_reconnects,
        )
    )


def executions_stream(
    client: PolySimClient,
    *,
    subscribe: dict[str, Any] | None = None,
    max_reconnects: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Blocking: yield real-time fill/execution events from ``/v1/ws/executions``."""

    async def _mint() -> dict[str, Any]:
        return client.ws_token()

    return _drive(
        _astream(
            ws_base=_ws_base(client.base_url),
            channel="executions",
            mint_token=_mint,
            subscribe=subscribe,
            max_reconnects=max_reconnects,
        )
    )


__all__ = [
    "aprices_stream",
    "aexecutions_stream",
    "prices_stream",
    "executions_stream",
]
