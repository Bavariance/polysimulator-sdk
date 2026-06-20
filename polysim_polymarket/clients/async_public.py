"""Asynchronous public client mirroring py-sdk's ``polymarket.clients.async_public``.

The async twin of :class:`polysim_polymarket.clients.public.PublicClient`. It
holds an internal :class:`polysim_sdk.aio.AsyncPolySimClient` and routes HTTP
through it over ``httpx.AsyncClient``, so a fan-out workload (polling many
markets concurrently with ``asyncio.gather``) does N reads without N threads.

The sim->real swap is identical to the sync client's: a bot built against
``polysim_polymarket.AsyncPublicClient`` runs unchanged on real Polymarket by
swapping the import prefix (``polysim_polymarket`` -> ``polymarket``), the host,
and the auth.

**DRY note:** this client shares *all* of its transport-free logic with the sync
:class:`~polysim_polymarket.clients.public.PublicClient` by importing
:mod:`polysim_polymarket.clients._common`. The validation guards, the
marginal-price walk, the book/market adaptation, and the derived scalar reads are
literally the same functions both clients call — the *only* difference here is
that every HTTP read is ``await``\\ed. Behaviour parity with the sync client (and
hence with py-sdk) is structural, not copy-pasted: there is no second copy of the
pure logic that could drift.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from types import TracebackType
from typing import Any, overload

from polysim_polymarket.clients import _common
from polysim_polymarket.environments import PRODUCTION, Environment
from polysim_polymarket.models import (
    LastTradePrice,
    LastTradePriceForToken,
    Market,
    OrderBook,
    OrderSide,
    PriceHistoryInterval,
    PriceHistoryPoint,
    PriceRequest,
)
from polysim_polymarket.pagination import AsyncPaginator, Page
from polysim_polymarket.streams import (
    CryptoPricesEvent,
    CryptoPricesSpec,
    MarketEvent,
    MarketSpec,
    SubscriptionHandle,
)
from polysim_polymarket.streams._stream_open import open_public_stream as _open_public_stream
from polysim_sdk._http import DEFAULT_BASE_URL
from polysim_sdk._shared import _decode_cursor, _next_cursor
from polysim_sdk.aio import AsyncPolySimClient


class AsyncPublicClient:
    """Async, read-only Polymarket-compatible client over the PolySim API.

    Drop-in constructor identical to the sync client: ``host`` + ``api_key`` cover
    the PolySim paper path, ``environment`` mirrors py-sdk's
    ``AsyncPublicClient(environment=...)``, and any on-chain kwargs a ported bot
    still passes are accepted and ignored. The API key resolves from ``api_key=``
    then the ``POLYSIM_API_KEY`` env (via ``AsyncPolySimClient``).

    Lifecycle is async (matching py-sdk): ``await client.close()`` /
    ``await client.aclose()`` close the transport, and the client is an async
    context manager (``async with AsyncPublicClient(...) as client:``).

    Routing mirrors py-sdk: requests go to ``environment.clob_url`` unless an
    explicit ``host=`` overrides it; ``client.environment.clob_url`` is honest
    about where requests actually go.
    """

    def __init__(
        self,
        environment: Environment = PRODUCTION,
        *,
        host: str | None = None,
        api_key: str | None = None,
        **_ignored: Any,
    ) -> None:
        # On-chain / py-sdk-only kwargs land in **_ignored and have no effect.
        self._environment = environment
        base_url = host or environment.clob_url or DEFAULT_BASE_URL
        # AsyncPolySimClient falls back to POLYSIM_API_KEY when api_key is None.
        self._client = AsyncPolySimClient(api_key=api_key, base_url=base_url)

    # ── environment / lifecycle ─────────────────────────────────────────────

    @property
    def environment(self) -> Environment:
        """The :class:`Environment` this client is configured against.

        A **property** (not a method), mirroring py-sdk's
        ``AsyncPublicClient.environment``.
        """
        return self._environment

    # ── realtime streams (CORE topics) ──────────────────────────────────────

    @overload
    async def subscribe(
        self, specs: MarketSpec, /, *, queue_size: int = ...
    ) -> SubscriptionHandle[MarketEvent]: ...
    @overload
    async def subscribe(
        self, specs: CryptoPricesSpec, /, *, queue_size: int = ...
    ) -> SubscriptionHandle[CryptoPricesEvent]: ...
    async def subscribe(
        self,
        specs: MarketSpec | CryptoPricesSpec,
        *,
        queue_size: int = 1024,
    ) -> SubscriptionHandle[Any]:
        """Subscribe to a CORE public realtime stream (``market`` / ``crypto_prices``).

        Mirrors py-sdk's positional-only ``specs`` ``subscribe`` for the CORE
        public specs. Pass one :class:`~polysim_polymarket.streams.MarketSpec` or
        :class:`~polysim_polymarket.streams.CryptoPricesSpec`. Returns a
        :class:`~polysim_polymarket.streams.SubscriptionHandle` — iterate it for
        events and ``close()`` it (or use it as an async context manager) when
        done.

        ``queue_size`` (a mirror-only keyword) sizes the handle's bounded queue;
        the handle drops oldest events under backpressure and counts losses in
        ``handle.dropped``.

        SCOPE: a single CORE spec per call. The DEFERRED topics
        (sports / comments / equity) and py-sdk's merged multi-spec
        ``subscribe(Sequence[...])`` (which returns one merged handle) are out of
        scope — pass specs one at a time and merge in application code if needed.
        """
        return _open_public_stream(self._client, specs, queue_size=queue_size)

    async def close(self) -> None:
        """Close the underlying async HTTP transport.

        Mirrors py-sdk's ``AsyncPublicClient.close`` (an ``async def``).
        """
        await self._client.aclose()

    async def aclose(self) -> None:
        """Alias for :meth:`close` matching ``AsyncPolySimClient.aclose`` naming.

        A mirror convenience (py-sdk's async public client ships only ``close``);
        provided so both spellings work for a bot used to either convention.
        """
        await self.close()

    async def __aenter__(self) -> AsyncPublicClient:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ── order book ──────────────────────────────────────────────────────────

    async def _book_for_token(self, token_id: str) -> dict[str, Any]:
        """Fetch the raw order-book payload for a token id (true-parity read).

        Same routing as the sync client's ``_book_for_token``: a bare token id
        routes to the token-native ``GET /v1/book?token_id=`` endpoint; the
        ``condition_id:YES`` / ``:NO`` colon form (plus ``:UP`` / ``:DOWN`` for
        UpDown markets) keeps condition-id routing and threads the outcome
        through as a query param so ``:NO`` reads the NO book and ``:UP`` reads
        the UP book.
        """
        tid = str(token_id)
        if ":" in tid:
            market_id, _, outcome = tid.rpartition(":")
            outcome = outcome.upper()
            if outcome in ("YES", "NO", "UP", "DOWN") and market_id:
                return await self._client.get_book(market_id, outcome=outcome)
        return await self._client.get_book_by_token(tid)

    async def get_order_book(self, *, token_id: str) -> OrderBook:
        """Get the order book for a token. Mirrors py-sdk's ``get_order_book``."""
        _common.require_nonempty_token_id(token_id)
        return _common.adapt_book(token_id, await self._book_for_token(token_id))

    async def get_order_books(self, *, token_ids: Sequence[str]) -> tuple[OrderBook, ...]:
        """Get order books for multiple tokens. Mirrors ``get_order_books``.

        One REST read per token in request order. A bare ``str`` / empty
        ``token_ids`` raises ``UserInputError`` (py-sdk's guard) before any read.
        """
        validated = _common.require_nonempty_token_ids(token_ids)
        out: list[OrderBook] = []
        for tid in validated:
            out.append(await self.get_order_book(token_id=tid))
        return tuple(out)

    # ── midpoints / prices / spreads ────────────────────────────────────────

    async def get_midpoint(self, *, token_id: str) -> Decimal:
        """Get the midpoint price for a token. Mirrors py-sdk's ``get_midpoint``.

        An empty / non-string ``token_id`` raises ``UserInputError`` (py-sdk's
        guard) before any read.
        """
        _common.require_nonempty_token_id(token_id)
        return _common.midpoint_from_book(await self._book_for_token(token_id))

    async def get_midpoints(self, *, token_ids: Sequence[str]) -> dict[str, Decimal]:
        """Get midpoint prices for multiple tokens, keyed by token id."""
        validated = _common.require_nonempty_token_ids(token_ids)
        out: dict[str, Decimal] = {}
        for tid in validated:
            out[tid] = await self.get_midpoint(token_id=tid)
        return out

    async def get_price(self, *, token_id: str, side: OrderSide) -> Decimal:
        """Get the executable price for a token side. Mirrors ``get_price``.

        Polymarket convention: ``BUY`` -> best **ASK**, ``SELL`` -> best **BID**.
        ``token_id`` is validated BEFORE ``side`` (py-sdk's ``build_price_request``
        order): an empty / non-string ``token_id`` raises ``UserInputError`` even
        when ``side`` is also invalid. ``side`` is then case-SENSITIVE — anything
        else raises ``UserInputError``.
        """
        _common.require_nonempty_token_id(token_id)
        _common.validate_side(side)
        return _common.price_from_book(await self._book_for_token(token_id), side)

    async def get_prices(
        self, *, requests: Sequence[PriceRequest]
    ) -> dict[str, dict[OrderSide, Decimal]]:
        """Get prices for multiple token-side requests. Mirrors ``get_prices``.

        Returns py-sdk's nested shape ``{token_id: {side: Decimal}}``. A bare
        ``str`` / ``PriceRequest`` / empty ``requests`` raises ``UserInputError``
        before any read.
        """
        validated = _common.require_nonempty_price_requests(requests)
        out: dict[str, dict[OrderSide, Decimal]] = {}
        for req in validated:
            out.setdefault(req.token_id, {})[req.side] = await self.get_price(
                token_id=req.token_id, side=req.side
            )
        return out

    async def get_spread(self, *, token_id: str) -> Decimal:
        """Get the bid-ask spread for a token. Mirrors py-sdk's ``get_spread``.

        An empty / non-string ``token_id`` raises ``UserInputError`` (py-sdk's
        guard) before any read.
        """
        _common.require_nonempty_token_id(token_id)
        return _common.spread_from_book(await self._book_for_token(token_id))

    async def get_spreads(self, *, token_ids: Sequence[str]) -> dict[str, Decimal]:
        """Get bid-ask spreads for multiple tokens, keyed by token id."""
        validated = _common.require_nonempty_token_ids(token_ids)
        out: dict[str, Decimal] = {}
        for tid in validated:
            out[tid] = await self.get_spread(token_id=tid)
        return out

    # ── last trade price ────────────────────────────────────────────────────

    async def get_last_trade_price(self, *, token_id: str) -> LastTradePrice:
        """Get the most recent trade price for a token. Mirrors the singular.

        An empty / non-string ``token_id`` raises ``UserInputError`` (py-sdk's
        guard) before any read.
        """
        _common.require_nonempty_token_id(token_id)
        price, side = _common.last_trade_from_book(await self._book_for_token(token_id))
        return LastTradePrice(price=price if price is not None else Decimal("0"), side=side)

    async def get_last_trade_prices(
        self, *, token_ids: Sequence[str]
    ) -> tuple[LastTradePriceForToken, ...]:
        """Get the most recent trade prices for multiple tokens. Mirrors the plural.

        Returns a ``tuple[LastTradePriceForToken, ...]`` — the per-token element
        carries ``token_id`` alongside the price + side. A bare ``str`` / empty
        ``token_ids`` raises ``UserInputError`` (py-sdk's guard).
        """
        validated = _common.require_nonempty_token_ids(token_ids)
        out: list[LastTradePriceForToken] = []
        for tid in validated:
            price, side = _common.last_trade_from_book(await self._book_for_token(tid))
            out.append(
                LastTradePriceForToken(
                    token_id=str(tid),
                    price=price if price is not None else Decimal("0"),
                    side=side,
                )
            )
        return tuple(out)

    # ── price history ───────────────────────────────────────────────────────

    async def get_price_history(
        self,
        *,
        token_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = None,
        interval: PriceHistoryInterval | None = None,
    ) -> tuple[PriceHistoryPoint, ...]:
        """Get historical price points for a token. Mirrors ``get_price_history``.

        Reads PolySimulator's PM-wire ``GET /v1/prices-history`` and returns a
        bare ``tuple[PriceHistoryPoint, ...]`` (py-sdk's return shape). Input
        validation + envelope checks are the shared ``_common`` ones — identical
        to the sync client and to py-sdk.
        """
        params = _common.build_price_history_params(
            token_id=token_id,
            start_ts=start_ts,
            end_ts=end_ts,
            fidelity=fidelity,
            interval=interval,
        )
        payload = await self._client._transport.request(
            "GET", "/v1/prices-history", params=params
        )
        history = _common.parse_price_history(payload)
        return _common.map_price_history(history)

    # ── market-order price estimation ───────────────────────────────────────

    async def estimate_market_price(
        self,
        *,
        token_id: str,
        side: OrderSide,
        amount: Decimal | int | float | str | None = None,
        shares: Decimal | int | float | str | None = None,
        order_type: str = "FOK",
    ) -> Decimal:
        """Estimate the execution (marginal) price for a market order.

        Mirrors py-sdk's ``estimate_market_price`` exactly (the shared ``_common``
        logic): the **marginal (limit) price**, not a size-weighted average. A
        BUY walks the asks cheapest-first against ``amount`` (USD); a SELL walks
        the bids highest-first against ``shares``. ``order_type`` FOK/FAK controls
        under-fill handling; a resolved price outside ``[tick, 1-tick]`` raises
        ``UnexpectedResponseError``. All input validation matches py-sdk.
        """
        notional = _common.validate_estimate_inputs(
            token_id=token_id, side=side, amount=amount, shares=shares, order_type=order_type
        )
        book = await self._book_for_token(token_id)
        return _common.estimate_from_book(
            book, side=side, notional=notional, order_type=order_type
        )

    # ── markets (gamma reads) ───────────────────────────────────────────────

    async def get_market(
        self,
        *,
        id: str | None = None,
        slug: str | None = None,
        url: str | None = None,
        include_tag: bool | None = None,
        locale: str | None = None,
    ) -> Market:
        """Get a market. Mirrors py-sdk's ``get_market``.

        ``id`` (a condition id here) routes to ``GET /v1/markets/{id}`` and
        ``slug`` to ``GET /v1/markets/by-slug/{slug}``. ``url`` / ``include_tag``
        / ``locale`` are accepted for signature parity and ignored.

        Exactly one of ``id`` / ``slug`` must be given: zero (no-arg) or both
        raise ``UserInputError`` with py-sdk's exact market-lookup message
        (via :func:`_common.require_market_lookup_arg`), before any read.
        """
        _common.require_market_lookup_arg(id, slug)
        # require_market_lookup_arg guarantees exactly one of id/slug is set, so
        # the elif narrows ``slug`` to ``str`` for the type-checker.
        if id is not None:
            raw = await self._client.get_market(id)
        elif slug is not None:
            raw = await self._client.get_market_by_slug(slug)
        return _common.adapt_market(raw)

    def list_markets(
        self,
        *,
        ascending: bool | None = None,
        closed: bool | None = None,
        clob_token_ids: str | Sequence[str] | None = None,
        condition_ids: str | Sequence[str] | None = None,
        cyom: bool | None = None,
        decimalized: bool | None = None,
        end_date_max: str | None = None,
        end_date_min: str | None = None,
        game_id: str | None = None,
        ids: int | Sequence[int] | None = None,
        include_tag: bool | None = None,
        liquidity_num_max: float | None = None,
        liquidity_num_min: float | None = None,
        locale: str | None = None,
        market_maker_addresses: str | Sequence[str] | None = None,
        order: str | None = None,
        position_ids: str | Sequence[str] | None = None,
        question_ids: str | Sequence[str] | None = None,
        related_tags: bool | None = None,
        rfq_enabled: bool | None = None,
        rewards_min_size: float | None = None,
        slug: str | Sequence[str] | None = None,
        sports_market_types: str | Sequence[str] | None = None,
        start_date_max: str | None = None,
        start_date_min: str | None = None,
        tag_id: int | None = None,
        tag_match: str | None = None,
        uma_resolution_status: str | None = None,
        volume_num_max: float | None = None,
        volume_num_min: float | None = None,
        page_size: int = 20,
    ) -> AsyncPaginator[Market]:
        """List markets. Mirrors py-sdk's async ``list_markets``.

        Returns an :class:`~polysim_polymarket.pagination.AsyncPaginator` of
        :class:`~polysim_polymarket.models.Market` — **synchronously** (no
        ``await``), exactly like py-sdk's async client: the awaiting happens when
        the bot drives the paginator (``await pag.first_page()`` /
        ``async for m in pag.iter_items()``).

        Same filter-forwarding contract as the sync client: only the filters
        PolySim's ``/v1/markets`` honours (``closed`` / ``order`` / ``ascending``)
        forward; the rest of py-sdk's gamma keyword set is accepted for signature
        parity and ignored.
        """
        forward = _common.list_markets_forward(closed=closed, order=order, ascending=ascending)

        async def fetch(cursor: str | None) -> Page[Market]:
            offset = _decode_cursor(cursor)
            if offset < 0:
                return Page(items=(), has_more=False)
            rows = await self._client.list_markets(
                limit=_common.PAGE_LIMIT, offset=offset, **forward
            )
            items = tuple(_common.adapt_market(row) for row in rows)
            next_cur = _next_cursor(offset, len(rows), _common.PAGE_LIMIT)
            return Page(
                items=items, has_more=len(rows) >= _common.PAGE_LIMIT, next_cursor=next_cur
            )

        return AsyncPaginator(fetch=fetch)
