"""Synchronous HTTP client for the PolySimulator REST API.

Design notes:
* Synchronous on purpose — strategy bots are usually one-thread-per-decision
  and async adds machinery the simple case doesn't need. An async twin lives
  in :mod:`polysim_sdk.aio` for fan-out workloads.
* Pacing, retry and error mapping live in :mod:`polysim_sdk._http` so the sync
  client, the async client and the py-clob-client parity layer all share one
  implementation.
* Responses come back as plain ``dict`` / ``list`` — the SDK is intentionally
  not an ORM, so it never lags behind the API on a new field.
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
    SyncTransport,
    unwrap_list,
)


class PolySimClient:
    """Thin sync wrapper around the public PolySimulator REST API.

    Pass an ``api_key`` explicitly or let the client read ``POLYSIM_API_KEY``
    from the environment. ``base_url`` defaults to production; point it at
    ``https://staging-api.polysimulator.com`` for staging.
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
        self._transport = SyncTransport(
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

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> PolySimClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── Account / identity ──────────────────────────────────────────────

    def me(self, wallet_id: str | None = None) -> dict[str, Any]:
        """``GET /v1/me`` — caller's profile, tier, balances."""
        params = {"wallet_id": wallet_id} if wallet_id else None
        return self._transport.request("GET", "/v1/me", params=params)

    def balance(self) -> dict[str, Any]:
        """``GET /v1/account/balance`` — balance + unrealized PnL.

        The endpoint takes no query params and always reports the **API
        wallet**; there is deliberately no ``wallet_id`` argument so callers
        aren't misled into thinking they can scope it (use :meth:`portfolio`
        with ``wallet_id`` for a per-wallet view).
        """
        return self._transport.request("GET", "/v1/account/balance")

    def positions(
        self, status: str | None = None, wallet_id: str | None = None
    ) -> list[dict[str, Any]]:
        """``GET /v1/account/positions``. ``status`` filters OPEN / CLOSED.

        ``wallet_id`` scopes the read: ``"all"`` (every wallet you own),
        ``"api"`` (your API wallet) or a wallet id you own. **Server default
        flipped 2026-06-10**: omitted now means the API wallet (was: all) —
        pass ``wallet_id="all"`` for the old behaviour.
        """
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if wallet_id:
            params["wallet_id"] = wallet_id
        return unwrap_list(
            self._transport.request("GET", "/v1/account/positions", params=params or None),
            keys=("positions", "items"),
        )

    def portfolio(self, wallet_id: str | None = None) -> dict[str, Any]:
        """``GET /v1/account/portfolio`` — balance + positions overview."""
        params = {"wallet_id": wallet_id} if wallet_id else None
        return self._transport.request("GET", "/v1/account/portfolio", params=params)

    def history(
        self,
        *,
        market_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        wallet_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """``GET /v1/account/history`` — filled-order trade history."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if market_id:
            params["market_id"] = market_id
        if wallet_id:
            params["wallet_id"] = wallet_id
        return unwrap_list(
            self._transport.request("GET", "/v1/account/history", params=params),
            keys=("history", "trades", "items"),
        )

    def equity(
        self, *, limit: int | None = None, wallet_id: str | None = None
    ) -> list[dict[str, Any]]:
        """``GET /v1/account/equity`` — equity curve from portfolio snapshots."""
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if wallet_id:
            params["wallet_id"] = wallet_id
        return unwrap_list(
            self._transport.request("GET", "/v1/account/equity", params=params or None),
            keys=("equity", "points", "items"),
        )

    def entitlements(self) -> dict[str, Any]:
        """``GET /v1/account/me/entitlements`` — tier + entitlement info."""
        return self._transport.request("GET", "/v1/account/me/entitlements")

    def reset_api_balance(self) -> dict[str, Any]:
        """``POST /v1/account/reset-api-balance`` — reset API wallet to baseline."""
        return self._transport.request("POST", "/v1/account/reset-api-balance", json_body={})

    # ── Wallets ─────────────────────────────────────────────────────────

    def list_wallets(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """``GET /v1/me/wallets`` — your MAIN / API / SANDBOX wallets."""
        return unwrap_list(
            self._transport.request(
                "GET", "/v1/me/wallets", params={"limit": limit, "offset": offset}
            ),
            keys=("wallets", "items"),
        )

    def create_wallet(self, *, name: str, kind: str) -> dict[str, Any]:
        """``POST /v1/me/wallets`` — the request body field is ``label``."""
        return self._transport.request(
            "POST", "/v1/me/wallets", json_body={"label": name, "kind": kind}
        )

    def get_wallet(self, wallet_id: str) -> dict[str, Any]:
        """``GET /v1/me/wallets/{wallet_id}``."""
        return self._transport.request("GET", f"/v1/me/wallets/{wallet_id}")

    def update_wallet(self, wallet_id: str, *, name: str) -> dict[str, Any]:
        """``PATCH /v1/me/wallets/{wallet_id}`` — rename a wallet.

        The request body field is ``label`` (the Python arg stays ``name`` for
        ergonomics).
        """
        return self._transport.request(
            "PATCH", f"/v1/me/wallets/{wallet_id}", json_body={"label": name}
        )

    def archive_wallet(self, wallet_id: str) -> dict[str, Any]:
        """``DELETE /v1/me/wallets/{wallet_id}`` — archive a wallet."""
        return self._transport.request("DELETE", f"/v1/me/wallets/{wallet_id}")

    def reset_wallet(self, wallet_id: str) -> dict[str, Any]:
        """``POST /v1/me/wallets/{wallet_id}/reset`` — reset to baseline."""
        return self._transport.request("POST", f"/v1/me/wallets/{wallet_id}/reset", json_body={})

    # ── Market data ─────────────────────────────────────────────────────

    def list_markets(self, limit: int = 50, **filters: Any) -> list[dict[str, Any]]:
        """``GET /v1/markets`` — public market list with filters (hot_only,
        q, status, sort, offset, ...).

        Free-text search is the ``q`` filter (max 120 chars), **not**
        ``search``; pass it through ``**filters`` (e.g. ``q="trump"``). Note
        ``q`` does not match short-horizon Up/Down crypto markets — use
        :meth:`list_updown` for those.
        """
        params = {"limit": limit, **{k: v for k, v in filters.items() if v is not None}}
        return unwrap_list(
            self._transport.request("GET", "/v1/markets", params=params),
            keys=("markets", "items"),
        )

    def get_market(self, condition_id: str) -> dict[str, Any]:
        """``GET /v1/markets/{condition_id}``."""
        return self._transport.request("GET", f"/v1/markets/{condition_id}")

    def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        """``GET /v1/markets/by-slug/{slug}``."""
        return self._transport.request("GET", f"/v1/markets/by-slug/{slug}")

    def get_market_by_token(self, token_id: str) -> dict[str, Any]:
        """``GET /v1/markets-by-token/{token_id}`` — resolve an outcome-token id.

        Returns ``{"condition_id", "primary_token_id", "outcome"}`` for the
        market that mints ``token_id``. This is the Polymarket-parity bridge
        from a long numeric CLOB token id back to a PolySimulator condition id
        + outcome; the py-clob-client parity layer uses it to resolve order
        tokens. Raises :class:`~polysim_sdk.exceptions.ApiError` with
        ``status_code == 404`` (``TOKEN_NOT_FOUND``) for an unknown token.
        """
        return self._transport.request("GET", f"/v1/markets-by-token/{token_id}")

    def get_book(
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
        return self._transport.request(
            "GET", f"/v1/markets/{condition_id}/book", params=params or None
        )

    def get_book_by_token(
        self, token_id: str, *, depth: int | None = None
    ) -> dict[str, Any]:
        """``GET /v1/book?token_id=...`` — order-book snapshot by outcome-token id.

        Token-id-native counterpart to :meth:`get_book`; this is the endpoint
        that gives true parity with Polymarket's CLOB book reads.
        """
        params: dict[str, Any] = {"token_id": token_id}
        if depth is not None:
            params["depth"] = depth
        return self._transport.request("GET", "/v1/book", params=params)

    def get_candles(
        self,
        condition_id: str,
        *,
        interval: str | None = None,
        start_time: Any = None,
        end_time: Any = None,
    ) -> list[dict[str, Any]]:
        """``GET /v1/markets/{condition_id}/candles`` — OHLC candles."""
        params: dict[str, Any] = {}
        if interval is not None:
            params["interval"] = interval
        if start_time is not None:
            params["start_time"] = start_time
        if end_time is not None:
            params["end_time"] = end_time
        return unwrap_list(
            self._transport.request(
                "GET", f"/v1/markets/{condition_id}/candles", params=params or None
            ),
            keys=("candles", "items"),
        )

    def list_events(
        self, *, limit: int = 50, offset: int = 0, search: str | None = None
    ) -> list[dict[str, Any]]:
        """``GET /v1/events`` — event (market-group) metadata."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        return unwrap_list(
            self._transport.request("GET", "/v1/events", params=params),
            keys=("events", "items"),
        )

    def list_updown(
        self,
        *,
        asset: str | None = None,
        interval: str | None = None,
        live: bool = False,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """``GET /v1/markets/updown`` — flat list of crypto Up/Down markets.

        Short-horizon BTC/ETH/SOL/… "is the asset up or down over this window"
        markets are the platform's most-traded product, but they do **not**
        surface through :meth:`list_markets` (whose ``search`` does not match
        them). This is the documented path to find them.

        ``asset`` (e.g. ``"BTC"``) and ``interval`` (one of
        ``5M``/``15M``/``1H``/``4H``/``daily`` — **uppercase**) filter
        server-side. ``live=True`` additionally keeps only currently-tradeable
        windows (``active and not closed and not resolved``) client-side.

        Each row carries ``slug``, ``condition_id``, ``time_range``,
        ``start_date``/``end_date``, ``group_item_threshold`` (the resolution
        strike — ``None`` until the window opens), ``live_price`` and a nested
        ``markets`` array with ``token_ids`` / ``outcomes`` / ``outcome_prices``.
        For the live underlying spot price and available assets/intervals, use
        :meth:`get_updown` and read its top-level keys.
        """
        params: dict[str, Any] = {}
        if asset is not None:
            params["asset"] = asset
        if interval is not None:
            params["interval"] = interval
        params.update({k: v for k, v in filters.items() if v is not None})
        rows = unwrap_list(
            self._transport.request("GET", "/v1/markets/updown", params=params or None),
            keys=("markets", "items"),
        )
        if live:
            rows = [
                m
                for m in rows
                if m.get("active") and not m.get("closed") and not m.get("resolved")
            ]
        return rows

    def get_updown(
        self, *, asset: str | None = None, interval: str | None = None
    ) -> dict[str, Any]:
        """``GET /v1/markets/updown`` — the full crypto Up/Down payload (dict).

        Unlike :meth:`list_updown`, this returns the raw response, which carries
        far more than the market list: ``crypto_prices`` (live spot per asset,
        e.g. ``payload["crypto_prices"]["BTC"]["price"]``), ``available_assets``,
        ``available_intervals``, ``interval_counts`` / ``asset_counts``, and the
        pre-grouped ``grouped_by_asset`` / ``grouped_by_interval`` views.

        ``asset`` and ``interval`` filter server-side (same values as
        :meth:`list_updown`).
        """
        params: dict[str, Any] = {}
        if asset is not None:
            params["asset"] = asset
        if interval is not None:
            params["interval"] = interval
        return self._transport.request("GET", "/v1/markets/updown", params=params or None)

    def get_price_to_beat(self, condition_id: str) -> dict[str, Any]:
        """``GET /prices/ptb/{condition_id}`` — the per-window resolution strike.

        The "price to beat" is the underlying-asset reference price captured at
        the interval's open; the Up/Down market resolves on whether spot
        finishes above or below it. Returns ``{"price", "asset", "start_date",
        "source"}``. ``condition_id`` may also be an outcome-token id (the
        server resolves it via its ``token:{id}`` map).

        Check ``source`` for provenance: ``polymarket_open_price`` /
        ``polymarket_scrape`` / ``gamma_event_metadata`` are Polymarket's own
        reported strike and ``chainlink_onchain`` / ``chainlink_timeline`` are
        the resolution oracle — treat these as the settlement strike. The
        lower-confidence fallbacks (``gamma_api``, ``cryptocompare``) are
        best-effort reconstructions; treat them as indicative only.

        This is **fresher and more precise** than the ``group_item_threshold``
        on :meth:`list_updown` rows — that field is the same value at the
        updown-cache refresh cadence (15–60s), whereas this endpoint resolves
        on demand. For the live underlying spot to compare against, use
        :meth:`get_spot`.

        Raises :class:`~polysim_sdk.exceptions.ApiError` with
        ``status_code == 404`` when the strike is **not yet available** — i.e.
        the window just opened and no authoritative source has resolved. Treat
        a 404 as *pending* (retry shortly), not *absent*.
        """
        return self._transport.request("GET", f"/prices/ptb/{condition_id}")

    def get_spot(self, symbol: str) -> dict[str, Any]:
        """``GET /prices/live/{symbol}`` — live underlying spot for one asset.

        ``symbol`` is an asset ticker (``BTC``/``ETH``/``SOL``/``XRP``/…,
        case-insensitive). Returns ``{"symbol", "price", "timestamp",
        "source", "age_seconds", "stale"}`` where ``source`` is the upstream
        feed (``chainlink_rtds`` preferred, ``polymarket_rtds`` / ``coingecko``
        as fallbacks) and ``stale`` flags a value older than ~30s. Served
        ``no-cache`` — this is the freshest poll-able spot snapshot.

        For a *push* feed of the same ticks, use
        :func:`polysim_sdk.sse.spot_stream` (the JWT WS carries market prices
        only, never underlying spot).
        """
        return self._transport.request("GET", f"/prices/live/{symbol}")

    def get_spots(self) -> dict[str, Any]:
        """``GET /prices/live`` — live spot for **all** supported assets.

        Returns ``{"prices": {SYM: {...}}, "supported_symbols": [...],
        "timestamp": ...}``. Per-symbol entries match :meth:`get_spot`'s shape.
        Served ``no-cache``.
        """
        return self._transport.request("GET", "/prices/live")

    # ── Orders ──────────────────────────────────────────────────────────

    def place_order(
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
        """``POST /v1/orders``.

        Pass **either** ``quantity`` (number of shares) **or** ``amount`` — the
        two are mutually exclusive server-side. ``amount`` is a **USD notional**
        and is only valid for a **market BUY**: the server derives the share
        count as ``floor4(amount / price)``. Use ``quantity`` for limit orders
        and for sells.

        ``price`` is the worst-acceptable fill price (0.01–1.00) and is
        **required for both market and limit orders** — PolySimulator follows
        Polymarket's "marketable limit" model where every market order is a
        limit order with FOK/FAK time-in-force at a worst-price cap. For a YES
        BUY where the ask is ~0.65, pass e.g. ``price="0.99"`` to allow any
        fill at or below 99¢.

        ``time_in_force`` defaults: limit → GTC, market → FOK. ``post_only``
        (maker-only) and ``expiration`` (a unix-seconds timestamp for a GTD
        order) are forwarded only when set; both are honoured by the server
        only when the PM-v2 order-semantics flag is enabled there.

        ``idempotency_key`` is forwarded as ``Idempotency-Key`` — pass a UUID4
        to make place_order safe to retry on a network blip without
        double-firing. If you don't supply one, the ``client_order_id`` is used
        as the idempotency key when given (so your own dedup id also guards the
        retry), otherwise a UUID4 is auto-generated.
        """
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
        return self._transport.request(
            "POST",
            "/v1/orders",
            json_body=body,
            idempotency_key=idempotency_key or client_order_id or str(uuid.uuid4()),
        )

    def place_orders(self, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``POST /v1/orders/batch`` — native tier-aware batch submission.

        ``orders`` is a list of order bodies (same shape as :meth:`place_order`
        takes). The per-tier batch cap is enforced server-side; exceeding it
        surfaces as :class:`~polysim_sdk.exceptions.ValidationError`.

        Each entry is auto-stamped with its own ``client_order_id`` (a UUID4,
        unless you already supplied one) so the orders are **independently**
        idempotent on the server. There is intentionally no batch-level
        ``Idempotency-Key`` — that would dedupe the whole batch as a single
        unit, which is the wrong granularity for a multi-order submission.

        The endpoint expects the orders wrapped in an ``{"orders": [...]}``
        envelope; this method does that wrapping for you.
        """
        stamped: list[dict[str, Any]] = []
        for order in orders:
            entry = dict(order)
            entry.setdefault("client_order_id", str(uuid.uuid4()))
            stamped.append(entry)
        result = self._transport.request(
            "POST",
            "/v1/orders/batch",
            json_body={"orders": stamped},
        )
        return unwrap_list(result, keys=("orders", "results", "items"))

    def get_order(
        self,
        order_id: str | int,
        *,
        source: str | None = None,
        wallet_id: int | str | None = None,
    ) -> dict[str, Any]:
        """``GET /v1/orders/{order_id}``.

        ``source`` disambiguates the table lookup (``pending`` forces the
        limit-order table, ``filled`` the market-fill table; default tries
        pending then falls back). ``wallet_id`` scopes the result to one wallet
        (404 if the row is on a different wallet). These are the only query
        params the endpoint honours — there is no ``market_id`` scoping here.
        """
        params: dict[str, Any] = {}
        if source is not None:
            params["source"] = source
        if wallet_id is not None:
            params["wallet_id"] = wallet_id
        return self._transport.request(
            "GET", f"/v1/orders/{order_id}", params=params or None
        )

    def cancel_order(self, order_id: str | int) -> dict[str, Any]:
        """``DELETE /v1/orders/{order_id}``."""
        return self._transport.request("DELETE", f"/v1/orders/{order_id}")

    def cancel_all(self) -> dict[str, Any]:
        """``POST /v1/cancel-all`` — cancel **every** open order on the account.

        Account-wide and irreversible. The backend ignores any body scoping and
        always sweeps the whole book, so this takes no ``market_id`` — to cancel
        a single market use :meth:`cancel_market_orders`. The
        ``X-Confirm-Cancel-All: true`` header satisfies the server's footgun
        guard, which 400s any cancel-all that omits a confirmation.
        """
        return self._transport.request(
            "POST",
            "/v1/cancel-all",
            json_body={},
            extra_headers={"X-Confirm-Cancel-All": "true"},
        )

    def cancel_market_orders(self, market_id: str) -> dict[str, Any]:
        """``DELETE /v1/cancel-market-orders`` — cancel all orders in a market.

        The server reads the market from the ``market`` query parameter (the
        Python arg stays ``market_id`` for ergonomics).
        """
        return self._transport.request(
            "DELETE", "/v1/cancel-market-orders", params={"market": market_id}
        )

    def list_orders(
        self,
        *,
        status: str | None = None,
        market_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        wallet_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """``GET /v1/orders`` — list orders with filters."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if market_id:
            params["market_id"] = market_id
        if wallet_id:
            params["wallet_id"] = wallet_id
        return unwrap_list(
            self._transport.request("GET", "/v1/orders", params=params),
            keys=("orders", "items"),
        )

    def data_orders(
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

        Returns the **raw envelope** ``{"limit", "count", "next_cursor",
        "data"}`` unchanged so callers can drive cursor pagination themselves
        (walk until ``next_cursor`` is the end sentinel ``"LTE="``). All filters
        are forwarded server-side; this is the data-API counterpart to
        :meth:`list_orders` and the basis for the py-clob-client parity
        ``get_orders`` walk.
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
        return self._transport.request("GET", "/v1/data/orders", params=params)

    def data_trades(
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

        Returns the **raw envelope** ``{"limit", "count", "next_cursor",
        "data"}`` unchanged (walk until ``next_cursor == "LTE="``). This is the
        data-API counterpart to :meth:`history` and the basis for the
        py-clob-client parity ``get_trades`` walk.
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
        return self._transport.request("GET", "/v1/data/trades", params=params)

    # ── Keys ────────────────────────────────────────────────────────────

    def list_keys(self, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """``GET /v1/keys`` — list your API keys (hashes/metadata only)."""
        return unwrap_list(
            self._transport.request("GET", "/v1/keys", params={"limit": limit, "offset": offset}),
            keys=("keys", "items"),
        )

    def create_key(
        self,
        *,
        name: str,
        tier: str = "free",
        permissions: list[str] | None = None,
    ) -> dict[str, Any]:
        """``POST /v1/keys`` — mint a new API key. Requires an active
        ``api_access`` grant for any tier above free. The raw key value is
        returned ONCE in the response.

        ``tier`` defaults to ``"free"``; pass e.g. ``"pro"`` / ``"pro_plus"``
        when your grant allows it. ``permissions`` optionally scopes the key.
        """
        body: dict[str, Any] = {"name": name, "tier": tier}
        if permissions is not None:
            body["permissions"] = permissions
        return self._transport.request("POST", "/v1/keys", json_body=body)

    @classmethod
    def bootstrap(
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
        """``POST /v1/keys/bootstrap`` — mint the FIRST key from a Supabase JWT.

        This is the one call you make **before** you hold any ``ps_live_*`` key,
        so it's a classmethod: it builds a transient keyless transport and
        authenticates purely with the JWT (sent as ``Authorization: Bearer``).
        The raw key value is returned ONCE in the response — construct a normal
        :class:`PolySimClient` with it for everything else.
        """
        resolved_base = base_url or os.environ.get("POLYSIM_BASE_URL") or DEFAULT_BASE_URL
        transport = SyncTransport(
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
            return transport.request(
                "POST",
                "/v1/keys/bootstrap",
                json_body=body,
                extra_headers={"Authorization": f"Bearer {jwt}"},
            )
        finally:
            transport.close()

    def bootstrap_key(self, *, jwt: str, name: str) -> dict[str, Any]:
        """``POST /v1/keys/bootstrap`` from an already-constructed client.

        Prefer the :meth:`bootstrap` classmethod for the real first-key flow
        (it needs no pre-existing key). This instance method is kept for
        callers that already hold a client. The JWT is sent as an
        ``Authorization`` bearer header for this one call.
        """
        return self._transport.request(
            "POST",
            "/v1/keys/bootstrap",
            json_body={"name": name},
            extra_headers={"Authorization": f"Bearer {jwt}"},
        )

    def rotate_key(self, key_id: str) -> dict[str, Any]:
        """``POST /v1/keys/{key_id}/rotate`` — rotate to a fresh value."""
        return self._transport.request("POST", f"/v1/keys/{key_id}/rotate", json_body={})

    def rename_key(self, key_id: str, *, name: str) -> dict[str, Any]:
        """``PATCH /v1/keys/{key_id}`` — rename a key."""
        return self._transport.request("PATCH", f"/v1/keys/{key_id}", json_body={"name": name})

    def delete_key(self, key_id: str) -> dict[str, Any]:
        """``DELETE /v1/keys/{key_id}`` — revoke a key."""
        return self._transport.request("DELETE", f"/v1/keys/{key_id}")

    def tiers(self) -> list[dict[str, Any]]:
        """``GET /v1/keys/tiers`` — authoritative rate-limit tiers.

        Always fetch limits here rather than hardcoding them — the numbers
        differ across environments and over time.
        """
        return unwrap_list(
            self._transport.request("GET", "/v1/keys/tiers"),
            keys=("tiers", "items"),
        )

    def ws_token(self) -> dict[str, Any]:
        """``POST /v1/keys/ws-token`` — mint a short-lived (60s) WS JWT."""
        return self._transport.request("POST", "/v1/keys/ws-token", json_body={})

    # ── Export ──────────────────────────────────────────────────────────

    def export_trades_csv(
        self,
        *,
        wallet_id: int | str | None = None,
        from_: str | None = None,
        to: str | None = None,
        market_id: str | None = None,
    ) -> str:
        """``GET /v1/export/trades.csv`` — filled trades as raw CSV text.

        ``wallet_id`` scopes the export to one wallet; ``from_`` / ``to`` bound
        the date range (the lower bound is sent under the wire alias ``from``,
        which is a Python keyword). ``market_id`` filters to one market. The
        endpoint streams the full filtered set — there is no ``limit``/``offset``
        pagination.
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
        return self._transport.request(
            "GET", "/v1/export/trades.csv", params=params or None, raw=True
        )
