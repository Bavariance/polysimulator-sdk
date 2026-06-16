"""Shared HTTP transport core for the PolySimulator SDK.

Both the synchronous :class:`~polysim_sdk.client.PolySimClient` and the
asynchronous :class:`~polysim_sdk.aio.AsyncPolySimClient` delegate every
network call through a transport defined here, so pacing, retry and error
mapping live in exactly one place. The drop-in ``polysim_clob_client``
parity layer holds a ``PolySimClient`` and therefore inherits the same
behaviour for free.

Behaviour:
* **Floor pacing** — a tiny minimum gap between requests so a runaway loop
  can't trip the per-second bucket mid-sleep.
* **Retry** — transport errors and ``425/502/503/504`` are retried with
  exponential back-off; ``429`` honours ``Retry-After`` then raises
  :class:`RateLimitError` once ``max_retries`` is exhausted.
* **Errors** — ``400/422`` raise :class:`ValidationError`; every other
  non-2xx raises :class:`ApiError`. Both the server's
  ``{error, code, message}`` shape and FastAPI's ``{detail}`` shape are
  normalised, and ``x-request-id`` is captured for support.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from polysim_sdk.exceptions import (
    ApiError,
    EdgeBlockedError,
    RateLimitError,
    ValidationError,
)

_log = logging.getLogger("polysim_sdk")

DEFAULT_BASE_URL = "https://api.polysimulator.com"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.5

# Even with the X-RateLimit headers reporting many requests remaining, hold to
# a tiny floor so a tight loop can't trip the per-second bucket. Free tier is
# 2 rps / 120 rpm, so the per-minute cap matters more than the per-second one.
DEFAULT_FLOOR_INTERVAL_SECONDS = 0.05

# Status codes treated as transient (retry with back-off). 425 = the
# Polymarket CLOB engine restart window.
_TRANSIENT_STATUS = (425, 502, 503, 504)


def build_headers(api_key: str, user_agent: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": user_agent,
    }
    # Omit the auth header entirely when there's no key, rather than sending an
    # empty ``X-API-Key:``. The only caller with an empty key is the JWT-authed
    # bootstrap flow (:meth:`PolySimClient.bootstrap`), which carries a
    # ``Authorization: Bearer`` header instead.
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def resolve_url(base_url: str, path: str) -> str:
    return path if path.startswith("http") else urljoin(base_url + "/", path.lstrip("/"))


def safe_json(resp: httpx.Response) -> dict[str, Any]:
    """Decode a response body to a dict.

    Endpoints that return a top-level array (e.g. ``/v1/markets``) are wrapped
    under a ``_list`` sentinel so the rest of the client can keep treating
    every response as a dict and unwrap at the public-method level.
    """
    try:
        decoded = resp.json()
    except Exception:
        return {"raw": resp.text}
    if isinstance(decoded, dict):
        return decoded
    return {"_list": decoded}


def unwrap_list(result: dict[str, Any], *, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Return the list inside a response that may be a bare array or a
    dict-with-list-key. Honours the ``_list`` sentinel from :func:`safe_json`
    plus the conventional ``markets``/``orders``/``items`` keys."""
    if "_list" in result and isinstance(result["_list"], list):
        return result["_list"]
    for key in keys:
        v = result.get(key)
        if isinstance(v, list):
            return v
    return []


def safe_error_message(resp: httpx.Response) -> str:
    body = safe_json(resp)
    message = body.get("message") or body.get("error") or body.get("detail")
    if isinstance(message, str):
        return message
    return resp.text or f"HTTP {resp.status_code}"


# Cloudflare-style edge errors carry a plain-text ``error code: 1XXX`` marker
# (1010 = blocked browser/UA signature, 1020 = access rule, etc.). We match the
# whole ``1XXX`` family so a tightened edge rule still surfaces cleanly.
_CF_EDGE_RE = re.compile(r"error code:\s*1\d{3}", re.IGNORECASE)


def _edge_block_code(resp: httpx.Response) -> str | None:
    """Return the Cloudflare ``error code: 1XXX`` string if this response looks
    like a CDN edge block, else ``None``.

    Guarded so our own JSON 403s are never misread: a JSON content-type, or a
    body that decodes to a dict, is treated as a real API error, not an edge
    block. The plain-text ``error code: 1XXX`` marker is the discriminator.
    """
    if "json" in resp.headers.get("content-type", "").lower():
        return None
    text = resp.text or ""
    match = _CF_EDGE_RE.search(text)
    return match.group(0) if match else None


def raise_for_status(resp: httpx.Response) -> None:
    """Map a non-2xx response to the right exception and raise it."""
    edge = _edge_block_code(resp)
    if edge is not None:
        raise EdgeBlockedError(
            status_code=resp.status_code,
            message=(
                f"Request blocked at the CDN edge ({edge}). This is almost "
                "always a disallowed User-Agent — Python's stdlib urllib "
                "default (Python-urllib/x.y) is blocked at the edge. Use this "
                "SDK, requests, or httpx, or set a custom User-Agent header."
            ),
            code="EDGE_BLOCKED",
            payload={"raw": (resp.text or "")[:500]},
            request_id=resp.headers.get("x-request-id"),
        )
    payload = safe_json(resp)
    message = (
        payload.get("message")
        or payload.get("error")
        or payload.get("detail")
        or resp.reason_phrase
        or f"HTTP {resp.status_code}"
    )
    if isinstance(message, dict):
        message = message.get("message") or message.get("msg") or str(message)
    code = payload.get("code") or payload.get("error")
    if isinstance(code, dict):
        code = code.get("code") or code.get("error")
    request_id = resp.headers.get("x-request-id")

    if resp.status_code in (400, 422):
        raise ValidationError(
            status_code=resp.status_code,
            message=str(message),
            code=code if isinstance(code, str) else None,
            payload=payload,
            request_id=request_id,
        )
    raise ApiError(
        status_code=resp.status_code,
        message=str(message),
        code=code if isinstance(code, str) else None,
        payload=payload,
        request_id=request_id,
    )


def _retry_after_seconds(resp: httpx.Response) -> float:
    try:
        return float(resp.headers.get("Retry-After") or "1")
    except (TypeError, ValueError):
        return 1.0


class SyncTransport:
    """Owns an ``httpx.Client`` and applies pacing + retry + error mapping."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        floor_interval: float = DEFAULT_FLOOR_INTERVAL_SECONDS,
        user_agent: str = "polysim-sdk/0.2.1",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._floor_interval = floor_interval
        self._last_request_ts = 0.0
        self._http = httpx.Client(
            base_url=self.base_url,
            headers=build_headers(api_key, user_agent),
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    def close(self) -> None:
        self._http.close()

    def _pace(self) -> None:
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self._floor_interval:
            time.sleep(self._floor_interval - elapsed)
        self._last_request_ts = time.monotonic()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        url = resolve_url(self.base_url, path)
        headers: dict[str, str] = dict(extra_headers) if extra_headers else {}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        last_error: Exception | None = None
        last_status = 0
        for attempt in range(self._max_retries + 1):
            self._pace()
            try:
                resp = self._http.request(
                    method, url, params=params, json=json_body, headers=headers or None
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                wait = (BACKOFF_BASE_SECONDS**attempt) * 0.5
                _log.warning("transport error: %s (sleep %.2fs, attempt %d)", exc, wait, attempt)
                time.sleep(wait)
                continue

            if resp.status_code in _TRANSIENT_STATUS:
                last_status = resp.status_code
                wait = BACKOFF_BASE_SECONDS**attempt
                _log.info(
                    "transient %d on %s %s — retrying in %.2fs",
                    resp.status_code,
                    method,
                    url,
                    wait,
                )
                time.sleep(wait)
                continue

            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                if attempt < self._max_retries:
                    _log.info("429 on %s %s — sleeping %.1fs", method, url, retry_after)
                    time.sleep(max(retry_after, 0.1))
                    continue
                raise RateLimitError(
                    message=safe_error_message(resp),
                    retry_after=retry_after,
                    payload=safe_json(resp),
                    request_id=resp.headers.get("x-request-id"),
                )

            if resp.is_error:
                raise_for_status(resp)

            return resp.text if raw else safe_json(resp)

        if last_error is not None:
            raise ApiError(
                status_code=0,
                message=f"Network error after {self._max_retries + 1} attempts: {last_error}",
                code="NETWORK_ERROR",
            )
        raise ApiError(
            status_code=last_status,
            message=(
                f"Exhausted retries for {method} {path}"
                + (f" (last status {last_status})" if last_status else "")
            ),
            code="RETRIES_EXHAUSTED",
        )


class AsyncTransport:
    """Async twin of :class:`SyncTransport` over ``httpx.AsyncClient``."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        floor_interval: float = DEFAULT_FLOOR_INTERVAL_SECONDS,
        user_agent: str = "polysim-sdk/0.2.1",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._floor_interval = floor_interval
        self._last_request_ts = 0.0
        # Serialize the read-sleep-write in ``_pace`` so concurrent
        # ``asyncio.gather``-ed requests pace one-after-another instead of all
        # reading the same stale timestamp and firing in one burst. Constructing
        # the lock here is safe on 3.10+ (it binds lazily to the running loop).
        self._pace_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            headers=build_headers(api_key, user_agent),
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _pace(self) -> None:
        async with self._pace_lock:
            elapsed = time.monotonic() - self._last_request_ts
            if elapsed < self._floor_interval:
                await asyncio.sleep(self._floor_interval - elapsed)
            self._last_request_ts = time.monotonic()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        url = resolve_url(self.base_url, path)
        headers: dict[str, str] = dict(extra_headers) if extra_headers else {}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        last_error: Exception | None = None
        last_status = 0
        for attempt in range(self._max_retries + 1):
            await self._pace()
            try:
                resp = await self._http.request(
                    method, url, params=params, json=json_body, headers=headers or None
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                wait = (BACKOFF_BASE_SECONDS**attempt) * 0.5
                _log.warning("transport error: %s (sleep %.2fs, attempt %d)", exc, wait, attempt)
                await asyncio.sleep(wait)
                continue

            if resp.status_code in _TRANSIENT_STATUS:
                last_status = resp.status_code
                wait = BACKOFF_BASE_SECONDS**attempt
                _log.info(
                    "transient %d on %s %s — retrying in %.2fs",
                    resp.status_code,
                    method,
                    url,
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            if resp.status_code == 429:
                retry_after = _retry_after_seconds(resp)
                if attempt < self._max_retries:
                    _log.info("429 on %s %s — sleeping %.1fs", method, url, retry_after)
                    await asyncio.sleep(max(retry_after, 0.1))
                    continue
                raise RateLimitError(
                    message=safe_error_message(resp),
                    retry_after=retry_after,
                    payload=safe_json(resp),
                    request_id=resp.headers.get("x-request-id"),
                )

            if resp.is_error:
                raise_for_status(resp)

            return resp.text if raw else safe_json(resp)

        if last_error is not None:
            raise ApiError(
                status_code=0,
                message=f"Network error after {self._max_retries + 1} attempts: {last_error}",
                code="NETWORK_ERROR",
            )
        raise ApiError(
            status_code=last_status,
            message=(
                f"Exhausted retries for {method} {path}"
                + (f" (last status {last_status})" if last_status else "")
            ),
            code="RETRIES_EXHAUSTED",
        )
