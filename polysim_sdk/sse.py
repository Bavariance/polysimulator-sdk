"""Server-Sent-Events helper for the public ``/prices/stream`` firehose.

``/prices/stream`` is the only **push** source for live *underlying spot*
prices — the ``crypto_price`` / ``crypto_price_batch`` events carry BTC / ETH /
SOL / … ticks (Polymarket RTDS: Chainlink preferred, Binance fallback). The
JWT-gated ``/v1/ws/prices`` channel carries *market* prices only, never the
underlying. So an HFT Up/Down strategy that wants a streaming tap on the asset
it's betting on comes here, not to the WS.

Unlike the WS channels this endpoint is **public and unauthenticated** — no
ws-token round-trip. With ``condition_ids`` it additionally streams
``market_price`` / ``market_price_batch`` / ``orderbook`` / ``orderbook_batch``
and an initial ``snapshot``; ``keepalive`` events arrive every ~5 s on quiet
markets.

Each connection appends a unique ``_=`` cache-buster so a CDN/edge can't
coalesce multiple subscribers onto one shared stream that's missing their
per-connection snapshot (2026-05-26 SSE-coalesce RCA). Reconnection mirrors the
:mod:`polysim_sdk.ws` helpers: a clean server close reconnects immediately; only
transport/5xx errors count toward ``max_reconnects`` and back off exponentially.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import TYPE_CHECKING, Any

import httpx

from polysim_sdk._http import raise_for_status, resolve_url
from polysim_sdk._version import DEFAULT_USER_AGENT

if TYPE_CHECKING:
    from polysim_sdk.aio import AsyncPolySimClient
    from polysim_sdk.client import PolySimClient

_MAX_BACKOFF_SECONDS = 30.0
_BASE_BACKOFF_SECONDS = 0.5
# Keepalives arrive every ~5 s, so a 30 s read gap means the connection is
# dead — let httpx raise a ReadTimeout and reconnect. Connect stays short.
_READ_TIMEOUT_SECONDS = 30.0
_CONNECT_TIMEOUT_SECONDS = 5.0

# Retry (reconnect + back off) on these; everything else 4xx is permanent.
_RETRYABLE_STATUS = frozenset({425, 429, 500, 502, 503, 504})


class _StreamRetry(Exception):
    """Internal: a retryable HTTP status opened the stream; reconnect."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"retryable SSE status {status_code}")


def _is_retryable_status(code: int) -> bool:
    return code in _RETRYABLE_STATUS or code >= 500


def _build_params(
    crypto: Sequence[str] | None,
    crypto_source: str | None,
    condition_ids: Sequence[str] | None,
    background: bool,
) -> dict[str, str]:
    """Build the ``/prices/stream`` query string for one connection.

    A fresh ``_`` cache-buster is minted on every call so each (re)connect is a
    distinct URL the edge can't coalesce.
    """
    params: dict[str, str] = {}
    if crypto:
        params["crypto"] = ",".join(crypto)
    if crypto_source:
        params["crypto_source"] = crypto_source
    if condition_ids:
        params["condition_ids"] = ",".join(condition_ids)
    if background:
        params["background"] = "true"
    params["_"] = uuid.uuid4().hex
    return params


def _sse_headers(api_key: str, user_agent: str) -> dict[str, str]:
    headers = {"Accept": "text/event-stream", "User-Agent": user_agent}
    # The endpoint is public, but sending the key keeps rate-limit attribution
    # consistent with the rest of the SDK and future-proofs an auth gate.
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _parse_event_block(block: str) -> dict[str, Any] | None:
    """Parse one SSE event block into ``{"event": <type>, "data": <decoded>}``.

    Fields are newline-separated; ``data:`` lines concatenate with newlines per
    the SSE spec, then JSON-decode (falling back to the raw string). A single
    space after the field colon is stripped. Comment lines (``:`` prefix) are
    ignored. Returns ``None`` for a block with no usable fields — e.g. a pure
    keepalive-padding comment block.
    """
    event_type = "message"
    data_lines: list[str] = []
    saw_field = False
    for line in block.split("\n"):
        if not line or line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_type = value
            saw_field = True
        elif field == "data":
            data_lines.append(value)
            saw_field = True
        # id / retry fields are not used by this stream — ignore.
    if not saw_field:
        return None
    raw = "\n".join(data_lines)
    data: Any
    try:
        data = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        data = raw
    return {"event": event_type, "data": data}


def _stream_timeout() -> httpx.Timeout:
    # No write/pool limits matter for a GET stream; cap connect + read only.
    return httpx.Timeout(_READ_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS)


# ── sync ────────────────────────────────────────────────────────────────────


def spot_stream(
    client: PolySimClient,
    crypto: Sequence[str] | None = None,
    *,
    crypto_source: str | None = None,
    condition_ids: Sequence[str] | None = None,
    background: bool = False,
    max_reconnects: int | None = None,
    reconnect_min_interval: float = _BASE_BACKOFF_SECONDS,
) -> Iterator[dict[str, Any]]:
    """Blocking iterator over the public ``/prices/stream`` SSE feed.

    Yields decoded events ``{"event": <type>, "data": <payload>}``. The event
    types that matter for Up/Down spot are ``crypto_price`` and
    ``crypto_price_batch`` (live underlying — the reason to use this over the
    WS); with ``condition_ids`` you also get ``snapshot`` / ``market_price`` /
    ``market_price_batch`` / ``orderbook`` / ``orderbook_batch``, plus periodic
    ``keepalive``.

    Args:
        crypto: symbols to filter (e.g. ``["BTC", "ETH"]``); omit for all.
        crypto_source: ``"chainlink"`` (authoritative for 5m/15m UpDown
            settlement) or ``"binance"`` (~10 Hz exchange ticks, denser for
            longer intervals); omit to receive both sources.
        condition_ids: market condition ids to also stream prices/books for.
        background: filter-only mode (skips priority CLOB polling) for passive
            consumers like portfolio PnL.
        max_reconnects: cap on error-driven reconnects; ``None`` = unbounded.
        reconnect_min_interval: starting back-off after an error reconnect.
    """
    backoff = reconnect_min_interval
    attempts = 0
    url = resolve_url(client.base_url, "/prices/stream")
    headers = _sse_headers(client._api_key, DEFAULT_USER_AGENT)
    with httpx.Client(timeout=_stream_timeout()) as http:
        while True:
            try:
                params = _build_params(crypto, crypto_source, condition_ids, background)
                with http.stream("GET", url, params=params, headers=headers) as resp:
                    if resp.is_error:
                        resp.read()
                        if not _is_retryable_status(resp.status_code):
                            raise_for_status(resp)
                        raise _StreamRetry(resp.status_code)
                    backoff = reconnect_min_interval  # reset on a clean connect
                    buf: list[str] = []
                    for line in resp.iter_lines():
                        if line == "":
                            ev = _parse_event_block("\n".join(buf))
                            buf = []
                            if ev is not None:
                                yield ev
                        else:
                            buf.append(line)
                # Clean server close → reconnect immediately (matches ws helper).
                continue
            except _StreamRetry as exc:
                attempts += 1
                if max_reconnects is not None and attempts > max_reconnects:
                    # _StreamRetry is an internal marker; don't chain it into
                    # the user-facing traceback.
                    raise _exhausted_error(exc.status_code) from None
                time.sleep(min(backoff, _MAX_BACKOFF_SECONDS))
                backoff = min(max(backoff, _BASE_BACKOFF_SECONDS) * 2, _MAX_BACKOFF_SECONDS)
                continue
            except (httpx.TransportError, httpx.TimeoutException):
                attempts += 1
                if max_reconnects is not None and attempts > max_reconnects:
                    raise
                time.sleep(min(backoff, _MAX_BACKOFF_SECONDS))
                backoff = min(max(backoff, _BASE_BACKOFF_SECONDS) * 2, _MAX_BACKOFF_SECONDS)
                continue


# ── async ───────────────────────────────────────────────────────────────────


async def aspot_stream(
    client: AsyncPolySimClient,
    crypto: Sequence[str] | None = None,
    *,
    crypto_source: str | None = None,
    condition_ids: Sequence[str] | None = None,
    background: bool = False,
    max_reconnects: int | None = None,
    reconnect_min_interval: float = _BASE_BACKOFF_SECONDS,
) -> AsyncIterator[dict[str, Any]]:
    """Async twin of :func:`spot_stream`. See it for the full contract."""
    backoff = reconnect_min_interval
    attempts = 0
    url = resolve_url(client.base_url, "/prices/stream")
    headers = _sse_headers(client._api_key, DEFAULT_USER_AGENT)
    async with httpx.AsyncClient(timeout=_stream_timeout()) as http:
        while True:
            try:
                params = _build_params(crypto, crypto_source, condition_ids, background)
                async with http.stream("GET", url, params=params, headers=headers) as resp:
                    if resp.is_error:
                        await resp.aread()
                        if not _is_retryable_status(resp.status_code):
                            raise_for_status(resp)
                        raise _StreamRetry(resp.status_code)
                    backoff = reconnect_min_interval
                    buf: list[str] = []
                    async for line in resp.aiter_lines():
                        if line == "":
                            ev = _parse_event_block("\n".join(buf))
                            buf = []
                            if ev is not None:
                                yield ev
                        else:
                            buf.append(line)
                continue
            except _StreamRetry as exc:
                attempts += 1
                if max_reconnects is not None and attempts > max_reconnects:
                    # _StreamRetry is an internal marker; don't chain it into
                    # the user-facing traceback.
                    raise _exhausted_error(exc.status_code) from None
                await asyncio.sleep(min(backoff, _MAX_BACKOFF_SECONDS))
                backoff = min(max(backoff, _BASE_BACKOFF_SECONDS) * 2, _MAX_BACKOFF_SECONDS)
                continue
            except (httpx.TransportError, httpx.TimeoutException):
                attempts += 1
                if max_reconnects is not None and attempts > max_reconnects:
                    raise
                await asyncio.sleep(min(backoff, _MAX_BACKOFF_SECONDS))
                backoff = min(max(backoff, _BASE_BACKOFF_SECONDS) * 2, _MAX_BACKOFF_SECONDS)
                continue


def _exhausted_error(status_code: int) -> Exception:
    """Build the terminal error raised when SSE reconnects are exhausted."""
    from polysim_sdk.exceptions import ApiError

    return ApiError(
        status_code=status_code,
        message=f"SSE stream failed after exhausting reconnects (HTTP {status_code})",
        code="SSE_RETRIES_EXHAUSTED",
    )


__all__ = ["spot_stream", "aspot_stream"]
