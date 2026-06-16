"""Asynchronous client for the PolySimulator REST API.

A 1:1 async mirror of :class:`polysim_sdk.client.PolySimClient` over
``httpx.AsyncClient``. Same pacing, retry and error semantics (shared via
:mod:`polysim_sdk._http`), just ``await``-ed. Use this for fan-out workloads
— polling many markets, placing orders across wallets concurrently.

Example::

    import asyncio
    from polysim_sdk.aio import AsyncPolySimClient

    async def main():
        async with AsyncPolySimClient() as client:
            me = await client.me()
            books = await asyncio.gather(*(client.get_book(c) for c in condition_ids))

    asyncio.run(main())
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from polysim_sdk._http import (
    DEFAULT_BASE_URL,
    DEFAULT_FLOOR_INTERVAL_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    AsyncTransport,
    unwrap_list,
)


class AsyncPolySimClient:
    """Async wrapper around the public PolySimulator REST API.

    Same constructor contract as :class:`~polysim_sdk.client.PolySimClient`.
    Use as an async context manager, or call :meth:`aclose` when done.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        floor_interval: float = DEFAULT_FLOOR_INTERVAL_SECONDS,
        user_agent: str = "polysim-sdk/0.2.2",
    ) -> None:
        resolved_key = api_key or os.environ.get("POLYSIM_API_KEY")
        if not resolved_key:
            raise ValueError("API key required. Pass api_key=... or set POLYSIM_API_KEY.")
        resolved_base = base_url or os.environ.get("POLYSIM_BASE_URL") or DEFAULT_BASE_URL
        self._api_key = resolved_key
        self._transport = AsyncTransport(
            resolved_key,
            resolved_base,
            timeout=timeout,
            max_retries=max_retries,
            floor_interval=floor_interval,
            user_agent=user_agent,
        )

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    # ── Resource lifecycle ──────────────────────────────────────────────

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> AsyncPolySimClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ── Account / identity ──────────────────────────────────────────────

    async def me(self, wallet_id: str | None = None) -> dict[str, Any]:
        """``GET /v1/me``."""
        params = {"wallet_id": wallet_id} if wallet_id else None
        return await self._transport.request("GET", "/v1/me", params=params)

    async def balance(self) -> dict[str, Any]:
        """``GET /v1/account/balance`` — always the API wallet, no params.

        Mirrors :meth:`polysim_sdk.client.PolySimClient.balance`: the endpoint
        takes no query params, so there is no ``wallet_id`` argument.
        """
        return await self._transport.request("GET", "/v1/account/balance")

    async def positions(
        self, status: str | None = None, wallet_id: str | None = None
    ) -> list[dict[str, Any]]:
        """``GET /v1/account/positions``."""
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if wallet_id:
            params["wallet_id"] = wallet_id
        return unwrap_list(
            await self._transport.request("GET", "/v1/account/positions", params=params or None),
            keys=("positions", "items"),
        )

    async def portfolio(self, wallet_id: str | None = None) -> dict[str, Any]:
        """``GET /v1/account/portfolio``."""
        params = {"wallet_id": wallet_id} if wallet_id else None
        return await self._transport.request("GET", "/v1/account/portfolio", params=params)

    async def history(
        self,
        *,
        market_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        wallet_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """``GET /v1/account/history``."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if market_id:
            params["market_id"] = market_id
        if wallet_id:
            params["wallet_id"] = wallet_id
        return unwrap_list(
            await self._transport.request("GET", "/v1/account/history", params=params),
            keys=("history", "trades", "items"),
        )

    async def equity(
        self, *, limit: int | None = None, wallet_id: str | None = None
    ) -> list[dict[str, Any]]:
        """``GET /v1/account/equity``."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if wallet_id:
            params["wallet_id"] = wallet_id
        return unwrap_list(
            await self._transport.request("GET", "/v1/account/equity", params=params or None),
            keys=("equity", "points", "items"),
        )

    async def entitlements(self) -> dict[str, Any]:
        """``GET /v1/account/me/entitlements``."""
        return await self._transport.request("GET", "/v1/account/me/entitlements")

    async def reset_api_balance(self) -> dict[str, Any]:
        """``POST /v1/account/reset-api-balance``."""
        return await self._transport.request("POST", "/v1/account/reset-api-balance", json_body={})

    # ── Wallets ─────────────────────────────────────────────────────────

    async def list_wallets(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """``GET /v1/me/wallets``."""
        return unwrap_list(
            await self._transport.request(
                "GET", "/v1/me/wallets", params={"limit": limit, "offset": offset}
            ),
            keys=("wallets", "items"),
        )

    async def create_wallet(self, *, name: str, kind: str) -> dict[str, Any]:
        """``POST /v1/me/wallets`` — the request body field is ``label``."""
        return await self._transport.request(
            "POST", "/v1/me/wallets", json_body={"label": name, "kind": kind}
        )

    async def get_wallet(self, wallet_id: str) -> dict[str, Any]:
        """``GET /v1/me/wallets/{wallet_id}``."""
        return await self._transport.request("GET", f"/v1/me/wallets/{wallet_id}")

    async def update_wallet(self, wallet_id: str, *, name: str) -> dict[str, Any]:
        """``PATCH /v1/me/wallets/{wallet_id}`` — the request body field is ``label``."""
        return await self._transport.request(
            "PATCH", f"/v1/me/wallets/{wallet_id}", json_body={"label": name}
        )

    async def archive_wallet(self, wallet_id: str) -> dict[str, Any]:
        """``DELETE /v1/me/wallets/{wallet_id}``."""
        return await self._transport.request("DELETE", f"/v1/me/wallets/{wallet_id}")

    async def reset_wallet(self, wallet_id: str) -> dict[str, Any]:
        """``POST /v1/me/wallets/{wallet_id}/reset``."""
        return await self._transport.request(
            "POST", f"/v1/me/wallets/{wallet_id}/reset", json_body={}
        )

    # ── Market data ─────────────────────────────────────────────────────

    async def list_markets(self, limit: int = 50, **filters: Any) -> list[dict[str, Any]]:
        """``GET /v1/markets`` — free-text search is the ``q`` filter (max 120
        chars), **not** ``search``; pass it through ``**filters``."""
        params = {"limit": limit, **{k: v for k, v in filters.items() if v is not None}}
        return unwrap_list(
            await self._transport.request("GET", "/v1/markets", params=params),
            keys=("markets", "items"),
        )

    async def get_market(self, condition_id: str) -> dict[str, Any]:
        """``GET /v1/markets/{condition_id}``."""
        return await self._transport.request("GET", f"/v1/markets/{condition_id}")

    async def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        """``GET /v1/markets/by-slug/{slug}``."""
        return await self._transport.request("GET", f"/v1/markets/by-slug/{slug}")

    async def get_market_by_token(self, token_id: str) -> dict[str, Any]:
        """``GET /v1/markets-by-token/{token_id}`` — resolve an outcome-token id.

        Returns ``{"condition_id", "primary_token_id", "outcome"}``; 404
        (``TOKEN_NOT_FOUND``) for an unknown token. Mirrors
        :meth:`polysim_sdk.client.PolySimClient.get_market_by_token`.
        """
        return await self._transport.request("GET", f"/v1/markets-by-token/{token_id}")

    async def get_book(
        self,
        condition_id: str,
        *,
        outcome: str | None = None,
        depth: int | None = None,
    ) -> dict[str, Any]:
        """``GET /v1/markets/{condition_id}/book`` — order-book snapshot.

        ``outcome`` selects the YES/NO side (defaults to the market's first
        outcome server-side); ``depth`` caps the number of price levels.
        """
        params: dict[str, Any] = {}
        if outcome is not None:
            params["outcome"] = outcome
        if depth is not None:
            params["depth"] = depth
        return await self._transport.request(
            "GET", f"/v1/markets/{condition_id}/book", params=params or None
        )

    async def get_book_by_token(
        self, token_id: str, *, depth: int | None = None
    ) -> dict[str, Any]:
        """``GET /v1/book?token_id=...`` — order-book snapshot by outcome-token id.

        Token-id-native counterpart to :meth:`get_book`; this is the endpoint
        that gives true parity with Polymarket's CLOB book reads.
        """
        params: dict[str, Any] = {"token_id": token_id}
        if depth is not None:
            params["depth"] = depth
        return await self._transport.request("GET", "/v1/book", params=params)

    async def get_candles(
        self,
        condition_id: str,
        *,
        interval: str | None = None,
        start_time: Any = None,
        end_time: Any = None,
    ) -> list[dict[str, Any]]:
        """``GET /v1/markets/{condition_id}/candles``."""
        params: dict[str, Any] = {}
        if interval is not None:
            params["interval"] = interval
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time
        return unwrap_list(
            await self._transport.request(
                "GET", f"/v1/markets/{condition_id}/candles", params=params or None
            ),
            keys=("candles", "items"),
        )

    async def list_events(
        self, *, limit: int = 50, offset: int = 0, search: str | None = None
    ) -> list[dict[str, Any]]:
        """``GET /v1/events``."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        return unwrap_list(
            await self._transport.request("GET", "/v1/events", params=params),
            keys=("events", "items"),
        )

    async def list_updown(
        self,
        *,
        asset: str | None = None,
        interval: str | None = None,
        live: bool = False,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """``GET /v1/markets/updown`` — flat list of crypto Up/Down markets.

        See :meth:`polysim_sdk.client.PolySimClient.list_updown` for the full
        contract. ``asset`` / ``interval`` filter server-side; ``live=True``
        keeps only ``active and not closed and not resolved`` windows.
        """
        params: dict[str, Any] = {}
        if asset is not None:
            params["asset"] = asset
        if interval is not None:
            params["interval"] = interval
        params.update({k: v for k, v in filters.items() if v is not None})
        rows = unwrap_list(
            await self._transport.request("GET", "/v1/markets/updown", params=params or None),
            keys=("markets", "items"),
        )
        if live:
            rows = [
                m
                for m in rows
                if m.get("active") and not m.get("closed") and not m.get("resolved")
            ]
        return rows

    async def get_updown(
        self, *, asset: str | None = None, interval: str | None = None
    ) -> dict[str, Any]:
        """``GET /v1/markets/updown`` — full crypto Up/Down payload (dict).

        See :meth:`polysim_sdk.client.PolySimClient.get_updown`. Carries
        ``crypto_prices`` (live spot), ``available_assets`` /
        ``available_intervals`` and the grouped views alongside ``markets``.
        """
        params: dict[str, Any] = {}
        if asset is not None:
            params["asset"] = asset
        if interval is not None:
            params["interval"] = interval
        return await self._transport.request("GET", "/v1/markets/updown", params=params or None)

    async def get_price_to_beat(self, condition_id: str) -> dict[str, Any]:
        """``GET /prices/ptb/{condition_id}`` — per-window resolution strike.

        See :meth:`polysim_sdk.client.PolySimClient.get_price_to_beat`. A
        ``404`` :class:`~polysim_sdk.exceptions.ApiError` means the strike is
        *not yet available* (window just opened), not absent — retry shortly.
        """
        return await self._transport.request("GET", f"/prices/ptb/{condition_id}")

    async def get_spot(self, symbol: str) -> dict[str, Any]:
        """``GET /prices/live/{symbol}`` — live underlying spot for one asset.

        See :meth:`polysim_sdk.client.PolySimClient.get_spot`. Served
        ``no-cache``; for a push feed use :func:`polysim_sdk.sse.aspot_stream`.
        """
        return await self._transport.request("GET", f"/prices/live/{symbol}")

    async def get_spots(self) -> dict[str, Any]:
        """``GET /prices/live`` — live spot for all supported assets.

        See :meth:`polysim_sdk.client.PolySimClient.get_spots`.
        """
        return await self._transport.request("GET", "/prices/live")

    # ── Orders ──────────────────────────────────────────────────────────

    async def place_order(
        self,
        *,
        market_id: str,
        side: str,
        outcome: str,
        quantity: float | str | None = None,
        amount: float | str | None = None,
        order_type: str = "market",
        price: float | str | None = None,
        time_in_force: str | None = None,
        post_only: bool = False,
        expiration: int | str | None = None,
        client_order_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """``POST /v1/orders``. See :meth:`PolySimClient.place_order` for the
        marketable-limit pricing model, ``quantity``-vs-``amount`` semantics,
        ``post_only`` / ``expiration`` forwarding, and idempotency semantics."""
        if quantity is None and amount is None:
            raise ValueError("place_order requires either quantity or amount.")
        body: dict[str, Any] = {
            "market_id": market_id,
            "side": side.upper(),
            "outcome": outcome,
            "order_type": order_type,
        }
        if quantity is not None:
            body["quantity"] = quantity
        if amount is not None:
            body["amount"] = amount
        if price is not None:
            body["price"] = price
        if time_in_force is not None:
            body["time_in_force"] = time_in_force.upper()
        if post_only:
            body["post_only"] = True
        if expiration is not None:
            body["expiration"] = expiration
        if client_order_id is not None:
            body["client_order_id"] = client_order_id
        return await self._transport.request(
            "POST",
            "/v1/orders",
            json_body=body,
            idempotency_key=idempotency_key or client_order_id or str(uuid.uuid4()),
        )

    async def place_orders(self, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``POST /v1/orders/batch`` — wraps ``orders`` in an ``{"orders": [...]}``
        envelope (the shape the endpoint expects).

        Each entry is auto-stamped with its own ``client_order_id`` (a UUID4,
        unless already supplied) so orders are independently idempotent; there
        is intentionally no batch-level ``Idempotency-Key``. Mirrors
        :meth:`polysim_sdk.client.PolySimClient.place_orders`.
        """
        stamped: list[dict[str, Any]] = []
        for order in orders:
            entry = dict(order)
            entry.setdefault("client_order_id", str(uuid.uuid4()))
            stamped.append(entry)
        result = await self._transport.request(
            "POST",
            "/v1/orders/batch",
            json_body={"orders": stamped},
        )
        return unwrap_list(result, keys=("orders", "results", "items"))

    async def get_order(
        self,
        order_id: str | int,
        *,
        source: str | None = None,
        wallet_id: int | str | None = None,
    ) -> dict[str, Any]:
        """``GET /v1/orders/{order_id}``.

        ``source`` disambiguates the table lookup (``pending``/``filled``);
        ``wallet_id`` scopes to one wallet. These are the only query params the
        endpoint honours — there is no ``market_id`` scoping here.
        """
        params: dict[str, Any] = {}
        if source is not None:
            params["source"] = source
        if wallet_id is not None:
            params["wallet_id"] = wallet_id
        return await self._transport.request(
            "GET", f"/v1/orders/{order_id}", params=params or None
        )

    async def cancel_order(self, order_id: str | int) -> dict[str, Any]:
        """``DELETE /v1/orders/{order_id}``."""
        return await self._transport.request("DELETE", f"/v1/orders/{order_id}")

    async def cancel_all(self) -> dict[str, Any]:
        """``POST /v1/cancel-all`` — cancel **every** open order on the account.

        Account-wide and irreversible (no ``market_id`` scoping — use
        :meth:`cancel_market_orders` for one market). Sends the
        ``X-Confirm-Cancel-All: true`` header the backend's footgun guard
        requires; without it the server 400s.
        """
        return await self._transport.request(
            "POST",
            "/v1/cancel-all",
            json_body={},
            extra_headers={"X-Confirm-Cancel-All": "true"},
        )

    async def cancel_market_orders(self, market_id: str) -> dict[str, Any]:
        """``DELETE /v1/cancel-market-orders`` — server reads the ``market`` query param."""
        return await self._transport.request(
            "DELETE", "/v1/cancel-market-orders", params={"market": market_id}
        )

    async def list_orders(
        self,
        *,
        status: str | None = None,
        market_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        wallet_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """``GET /v1/orders``."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if market_id:
            params["market_id"] = market_id
        if wallet_id:
            params["wallet_id"] = wallet_id
        return unwrap_list(
            await self._transport.request("GET", "/v1/orders", params=params),
            keys=("orders", "items"),
        )

    async def data_orders(
        self,
        *,
        id: str | None = None,
        market: str | None = None,
        asset_id: str | None = None,
        before: str | int | None = None,
        after: str | int | None = None,
        status: str | None = None,
        cursor: str | None = None,
        next_cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """``GET /v1/data/orders`` — Polymarket-shape cursor-paginated orders.

        Returns the raw envelope ``{"limit", "count", "next_cursor", "data"}``
        unchanged (walk until ``next_cursor == "LTE="``). Mirrors
        :meth:`polysim_sdk.client.PolySimClient.data_orders`.
        """
        params: dict[str, Any] = {"limit": limit}
        if id is not None:
            params["id"] = id
        if market is not None:
            params["market"] = market
        if asset_id is not None:
            params["asset_id"] = asset_id
        if before is not None:
            params["before"] = before
        if after is not None:
            params["after"] = after
        if status is not None:
            params["status"] = status
        if cursor is not None:
            params["cursor"] = cursor
        if next_cursor is not None:
            params["next_cursor"] = next_cursor
        return await self._transport.request("GET", "/v1/data/orders", params=params)

    async def data_trades(
        self,
        *,
        market: str | None = None,
        asset_id: str | None = None,
        before: str | int | None = None,
        after: str | int | None = None,
        cursor: str | None = None,
        next_cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """``GET /v1/data/trades`` — Polymarket-shape cursor-paginated trades.

        Returns the raw envelope ``{"limit", "count", "next_cursor", "data"}``
        unchanged (walk until ``next_cursor == "LTE="``). Mirrors
        :meth:`polysim_sdk.client.PolySimClient.data_trades`.
        """
        params: dict[str, Any] = {"limit": limit}
        if market is not None:
            params["market"] = market
        if asset_id is not None:
            params["asset_id"] = asset_id
        if before is not None:
            params["before"] = before
        if after is not None:
            params["after"] = after
        if cursor is not None:
            params["cursor"] = cursor
        if next_cursor is not None:
            params["next_cursor"] = next_cursor
        return await self._transport.request("GET", "/v1/data/trades", params=params)

    # ── Keys ────────────────────────────────────────────────────────────

    async def list_keys(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """``GET /v1/keys``."""
        return unwrap_list(
            await self._transport.request(
                "GET", "/v1/keys", params={"limit": limit, "offset": offset}
            ),
            keys=("keys", "items"),
        )

    async def create_key(
        self,
        *,
        name: str,
        tier: str = "free",
        permissions: list[str] | None = None,
    ) -> dict[str, Any]:
        """``POST /v1/keys`` — ``tier`` defaults ``"free"``; ``permissions`` optional."""
        body: dict[str, Any] = {"name": name, "tier": tier}
        if permissions is not None:
            body["permissions"] = permissions
        return await self._transport.request("POST", "/v1/keys", json_body=body)

    @classmethod
    async def bootstrap(
        cls,
        *,
        jwt: str,
        name: str,
        base_url: str | None = None,
        tier: str = "free",
        permissions: list[str] | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        floor_interval: float = DEFAULT_FLOOR_INTERVAL_SECONDS,
        user_agent: str = "polysim-sdk/0.2.2",
    ) -> dict[str, Any]:
        """``POST /v1/keys/bootstrap`` — JWT-authed first-key mint, no key needed.

        See :meth:`polysim_sdk.client.PolySimClient.bootstrap`. Builds a
        transient keyless transport and authenticates with the JWT.
        """
        resolved_base = base_url or os.environ.get("POLYSIM_BASE_URL") or DEFAULT_BASE_URL
        transport = AsyncTransport(
            "",  # no API key yet — the Bearer JWT below is the credential
            resolved_base,
            timeout=timeout,
            max_retries=max_retries,
            floor_interval=floor_interval,
            user_agent=user_agent,
        )
        body: dict[str, Any] = {"name": name, "tier": tier}
        if permissions is not None:
            body["permissions"] = permissions
        try:
            return await transport.request(
                "POST",
                "/v1/keys/bootstrap",
                json_body=body,
                extra_headers={"Authorization": f"Bearer {jwt}"},
            )
        finally:
            await transport.aclose()

    async def bootstrap_key(self, *, jwt: str, name: str) -> dict[str, Any]:
        """``POST /v1/keys/bootstrap`` from an already-constructed client.

        Prefer the :meth:`bootstrap` classmethod for the real first-key flow.
        """
        return await self._transport.request(
            "POST",
            "/v1/keys/bootstrap",
            json_body={"name": name},
            extra_headers={"Authorization": f"Bearer {jwt}"},
        )

    async def rotate_key(self, key_id: str) -> dict[str, Any]:
        """``POST /v1/keys/{key_id}/rotate``."""
        return await self._transport.request("POST", f"/v1/keys/{key_id}/rotate", json_body={})

    async def rename_key(self, key_id: str, *, name: str) -> dict[str, Any]:
        """``PATCH /v1/keys/{key_id}``."""
        return await self._transport.request(
            "PATCH", f"/v1/keys/{key_id}", json_body={"name": name}
        )

    async def delete_key(self, key_id: str) -> dict[str, Any]:
        """``DELETE /v1/keys/{key_id}``."""
        return await self._transport.request("DELETE", f"/v1/keys/{key_id}")

    async def tiers(self) -> list[dict[str, Any]]:
        """``GET /v1/keys/tiers`` — authoritative rate-limit tiers."""
        return unwrap_list(
            await self._transport.request("GET", "/v1/keys/tiers"),
            keys=("tiers", "items"),
        )

    async def ws_token(self) -> dict[str, Any]:
        """``POST /v1/keys/ws-token`` — mint a short-lived (60s) WS JWT."""
        return await self._transport.request("POST", "/v1/keys/ws-token", json_body={})

    # ── Export ──────────────────────────────────────────────────────────

    async def export_trades_csv(
        self,
        *,
        wallet_id: int | str | None = None,
        from_: str | None = None,
        to: str | None = None,
        market_id: str | None = None,
    ) -> str:
        """``GET /v1/export/trades.csv`` — filled trades as raw CSV text.

        ``wallet_id`` scopes the export; ``from_`` / ``to`` bound the date range
        (the lower bound is sent under the wire alias ``from``); ``market_id``
        filters to one market. No ``limit``/``offset`` — the endpoint streams
        the full filtered set. Mirrors
        :meth:`polysim_sdk.client.PolySimClient.export_trades_csv`.
        """
        params: dict[str, Any] = {}
        if wallet_id is not None:
            params["wallet_id"] = wallet_id
        if from_ is not None:
            params["from"] = from_
        if to is not None:
            params["to"] = to
        if market_id is not None:
            params["market_id"] = market_id
        return await self._transport.request(
            "GET", "/v1/export/trades.csv", params=params or None, raw=True
        )
