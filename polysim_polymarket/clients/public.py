"""Synchronous public client mirroring py-sdk's ``polymarket.clients.public``.

This is the Phase-1 CLOB market-data READ surface. It holds an internal
:class:`polysim_sdk.PolySimClient` and routes HTTP through it, reusing the
proven read-path helpers in :mod:`polysim_sdk._shared`.

The sim->real swap: a bot constructs this ``PublicClient`` with a PolySim host +
``ps_live_*`` key for paper testing, then on real Polymarket swaps the import
prefix (``polysim_polymarket`` -> ``polymarket``), the host, and the auth. py-sdk's
real ``PublicClient`` takes an ``Environment`` (+ ``logger``); here we also accept
``host`` / ``api_key`` directly (the PolySim auth model is a single API key, not
an on-chain wallet) and accept-and-ignore the on-chain kwargs a porting author
leaves in place, so neither shape ``TypeError``s.

**DRY note:** every transport-free piece of logic this client uses — the
validation guards, the marginal-price walk, the book/market model adaptation,
the derived scalar reads (midpoint/price/spread/last-trade), the
``estimate_market_price`` input validation + band check — lives in
:mod:`polysim_polymarket.clients._common` and is shared *by import* with the
asynchronous :class:`~polysim_polymarket.clients.async_public.AsyncPublicClient`.
The two clients differ only in how they perform the HTTP read (this one blocks;
the async one ``await``\\s). Behaviour parity is structural, not copy-pasted.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

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
from polysim_polymarket.pagination import Page, Paginator
from polysim_sdk import PolySimClient
from polysim_sdk._http import DEFAULT_BASE_URL
from polysim_sdk._shared import _decode_cursor, _next_cursor


class PublicClient:
    """Public, read-only Polymarket-compatible client over the PolySim API.

    Drop-in constructor: ``host`` + ``api_key`` cover the PolySim paper path,
    ``environment`` mirrors py-sdk's ``PublicClient(environment=...)``, and any
    on-chain kwargs a ported bot still passes (``chain_id``, ``signature_type``,
    ``funder``, ``private_key``, ``logger``, …) are accepted and ignored — paper
    trading has no chain, no signer, no funder. The API key is resolved from
    ``api_key=`` then the ``POLYSIM_API_KEY`` env (via ``PolySimClient``).

    Routing mirrors py-sdk: requests go to ``environment.clob_url``, so a custom
    :class:`Environment` actually changes where the client talks. An explicit
    ``host=`` overrides the environment's ``clob_url`` (a convenience the PolySim
    paper path keeps); without it, ``client.environment.clob_url`` is the real
    target host.
    """

    def __init__(
        self,
        environment: Environment = PRODUCTION,
        *,
        host: str | None = None,
        api_key: str | None = None,
        **_ignored: Any,
    ) -> None:
        # On-chain / py-sdk-only kwargs (chain_id, signature_type, funder,
        # private_key, logger, …) land in **_ignored and have no effect.
        self._environment = environment
        # py-sdk routes every transport off the Environment, so when no explicit
        # host= override is given we route to environment.clob_url — that keeps
        # ``client.environment.clob_url`` honest about where requests actually go
        # (a custom Environment is respected, not silently ignored). host= still
        # wins as the explicit override; DEFAULT_BASE_URL is the final fallback
        # only if the environment carries no clob_url.
        base_url = host or environment.clob_url or DEFAULT_BASE_URL
        # PolySimClient falls back to POLYSIM_API_KEY when api_key is None.
        self._client = PolySimClient(api_key=api_key, base_url=base_url)

    # ── environment / lifecycle ─────────────────────────────────────────────

    @property
    def environment(self) -> Environment:
        """The :class:`Environment` this client is configured against.

        A **property** (not a method), mirroring py-sdk's
        ``PublicClient.environment`` so a ported bot reads
        ``client.environment.clob_url`` without the call parens.
        """
        return self._environment

    def close(self) -> None:
        """Close the underlying HTTP transport."""
        self._client.close()

    def __enter__(self) -> PublicClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── order book ──────────────────────────────────────────────────────────

    def _book_for_token(self, token_id: str) -> dict[str, Any]:
        """Fetch the raw order-book payload for a token id (true-parity read).

        A **bare** token id routes to the token-native ``GET /v1/book?token_id=``
        endpoint — the parity path, since py-sdk always passes a real
        outcome-token id. The ``condition_id:YES`` / ``:NO`` **colon form** (plus
        ``:UP`` / ``:DOWN`` for UpDown markets) is our convenience extension: it
        keeps condition-id routing and threads the outcome through as a query
        param so ``:NO`` reads the NO book and ``:UP`` reads the UP book.
        """
        tid = str(token_id)
        if ":" in tid:
            market_id, _, outcome = tid.rpartition(":")
            outcome = outcome.upper()
            if outcome in ("YES", "NO", "UP", "DOWN") and market_id:
                return self._client.get_book(market_id, outcome=outcome)
        return self._client.get_book_by_token(tid)

    def get_order_book(self, *, token_id: str) -> OrderBook:
        """Get the order book for a token. Mirrors py-sdk's ``get_order_book``.

        Delegates to the proven v1 read path: route the token, fetch the book
        via :class:`polysim_sdk.PolySimClient`, and adapt onto the py-sdk-shape
        :class:`~polysim_polymarket.models.OrderBook`. An empty ``token_id``
        raises ``UserInputError("token_id is required")`` (py-sdk's guard).
        """
        _common.require_nonempty_token_id(token_id)
        return _common.adapt_book(token_id, self._book_for_token(token_id))

    def get_order_books(self, *, token_ids: Sequence[str]) -> tuple[OrderBook, ...]:
        """Get order books for multiple tokens. Mirrors py-sdk's ``get_order_books``.

        One REST read per token (PolySim has no batch-book endpoint), returned
        in request order as a ``tuple[OrderBook, ...]`` — py-sdk's return shape.
        A bare ``str`` / empty ``token_ids`` raises ``UserInputError`` (py-sdk's
        guard), so ``token_ids="711"`` doesn't char-iterate into three reads.
        """
        validated = _common.require_nonempty_token_ids(token_ids)
        return tuple(self.get_order_book(token_id=tid) for tid in validated)

    # ── midpoints / prices / spreads ────────────────────────────────────────

    def get_midpoint(self, *, token_id: str) -> Decimal:
        """Get the midpoint price for a token. Mirrors py-sdk's ``get_midpoint``.

        ``(best_bid + best_ask) / 2`` on the token's book, as a ``Decimal``.

        Up/Down warning: an Up/Down outcome trades on a synthetic ~0.99 ladder,
        so this midpoint is *not* the underlying spot. Use the native client's
        spot/strike reads for the underlying.

        An empty / non-string ``token_id`` raises ``UserInputError`` (py-sdk's
        guard) before any read.
        """
        _common.require_nonempty_token_id(token_id)
        return _common.midpoint_from_book(self._book_for_token(token_id))

    def get_midpoints(self, *, token_ids: Sequence[str]) -> dict[str, Decimal]:
        """Get midpoint prices for multiple tokens, keyed by token id.

        A bare ``str`` / empty ``token_ids`` raises ``UserInputError`` (py-sdk's
        guard), so ``token_ids="711"`` doesn't char-iterate into three reads.
        """
        validated = _common.require_nonempty_token_ids(token_ids)
        return {tid: self.get_midpoint(token_id=tid) for tid in validated}

    def get_price(self, *, token_id: str, side: OrderSide) -> Decimal:
        """Get the executable price for a token side. Mirrors py-sdk's ``get_price``.

        Polymarket convention (the price a marketable order would *execute* at):
        ``BUY`` -> best **ASK** (the price you'd pay to buy the token), ``SELL``
        -> best **BID** (the price you'd receive to sell it). Computed from the
        token's current book and returned as a ``Decimal``.

        ``token_id`` is validated BEFORE ``side`` (py-sdk's ``build_price_request``
        order): an empty / non-string ``token_id`` raises ``UserInputError``
        even when ``side`` is also invalid. ``side`` is then case-SENSITIVE
        (py-sdk's contract): anything other than exactly ``"BUY"`` / ``"SELL"``
        raises ``UserInputError``.
        """
        _common.require_nonempty_token_id(token_id)
        _common.validate_side(side)
        return _common.price_from_book(self._book_for_token(token_id), side)

    def get_prices(
        self, *, requests: Sequence[PriceRequest]
    ) -> dict[str, dict[OrderSide, Decimal]]:
        """Get prices for multiple token-side requests. Mirrors ``get_prices``.

        Returns py-sdk's nested shape ``{token_id: {side: Decimal}}``. A bare
        ``str`` / ``PriceRequest`` / empty ``requests`` raises ``UserInputError``
        (py-sdk's guard) before any read.
        """
        validated = _common.require_nonempty_price_requests(requests)
        out: dict[str, dict[OrderSide, Decimal]] = {}
        for req in validated:
            out.setdefault(req.token_id, {})[req.side] = self.get_price(
                token_id=req.token_id, side=req.side
            )
        return out

    def get_spread(self, *, token_id: str) -> Decimal:
        """Get the bid-ask spread for a token. Mirrors py-sdk's ``get_spread``.

        ``best_ask - best_bid`` on the token's book, as a ``Decimal``. An empty /
        non-string ``token_id`` raises ``UserInputError`` (py-sdk's guard) before
        any read.
        """
        _common.require_nonempty_token_id(token_id)
        return _common.spread_from_book(self._book_for_token(token_id))

    def get_spreads(self, *, token_ids: Sequence[str]) -> dict[str, Decimal]:
        """Get bid-ask spreads for multiple tokens, keyed by token id.

        A bare ``str`` / empty ``token_ids`` raises ``UserInputError`` (py-sdk's
        guard), so ``token_ids="711"`` doesn't char-iterate into three reads.
        """
        validated = _common.require_nonempty_token_ids(token_ids)
        return {tid: self.get_spread(token_id=tid) for tid in validated}

    # ── last trade price ────────────────────────────────────────────────────

    def get_last_trade_price(self, *, token_id: str) -> LastTradePrice:
        """Get the most recent trade price for a token. Mirrors ``get_last_trade_price``.

        An empty / non-string ``token_id`` raises ``UserInputError`` (py-sdk's
        guard) before any read.
        """
        _common.require_nonempty_token_id(token_id)
        price, side = _common.last_trade_from_book(self._book_for_token(token_id))
        return LastTradePrice(price=price if price is not None else Decimal("0"), side=side)

    def get_last_trade_prices(
        self, *, token_ids: Sequence[str]
    ) -> tuple[LastTradePriceForToken, ...]:
        """Get the most recent trade prices for multiple tokens. Mirrors the plural.

        Returns a ``tuple[LastTradePriceForToken, ...]`` (py-sdk's shape) — the
        per-token element carries ``token_id`` alongside the price + side. A bare
        ``str`` / empty ``token_ids`` raises ``UserInputError`` (py-sdk's guard).
        """
        validated = _common.require_nonempty_token_ids(token_ids)
        out: list[LastTradePriceForToken] = []
        for tid in validated:
            price, side = _common.last_trade_from_book(self._book_for_token(tid))
            out.append(
                LastTradePriceForToken(
                    token_id=str(tid),
                    price=price if price is not None else Decimal("0"),
                    side=side,
                )
            )
        return tuple(out)

    # ── price history ───────────────────────────────────────────────────────

    def get_price_history(
        self,
        *,
        token_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = None,
        interval: PriceHistoryInterval | None = None,
    ) -> tuple[PriceHistoryPoint, ...]:
        """Get historical price points for a token. Mirrors py-sdk's ``get_price_history``.

        Reads PolySimulator's PM-wire ``GET /v1/prices-history?market=<token>``
        (which returns the exact ``{"history": [{"t", "p"}]}`` envelope
        Polymarket serves) and returns a **bare** ``tuple[PriceHistoryPoint,
        ...]`` — py-sdk's return shape (Task C.1), NOT a ``PriceHistory``
        wrapper. ``start_ts`` / ``end_ts`` / ``fidelity`` / ``interval`` forward
        as py-sdk's exact PM query-param names (``startTs`` / ``endTs`` /
        ``fidelity`` / ``interval``).

        Input validation mirrors py-sdk's ``build_price_history_request``:
        ``token_id`` must be a non-empty string; ``start_ts`` / ``end_ts`` are
        non-negative ints; ``fidelity`` is a positive int; ``interval`` must be
        one of py-sdk's allowed values — each bad value raises ``UserInputError``
        before any request. A malformed response (non-dict, or ``history`` not a
        list) raises ``UnexpectedResponseError`` (py-sdk's ``parse_price_history``
        contract), rather than silently returning an empty tuple.
        """
        params = _common.build_price_history_params(
            token_id=token_id,
            start_ts=start_ts,
            end_ts=end_ts,
            fidelity=fidelity,
            interval=interval,
        )
        payload = self._client._transport.request("GET", "/v1/prices-history", params=params)
        history = _common.parse_price_history(payload)
        return _common.map_price_history(history)

    # ── market-order price estimation ───────────────────────────────────────

    def estimate_market_price(
        self,
        *,
        token_id: str,
        side: OrderSide,
        amount: Decimal | int | float | str | None = None,
        shares: Decimal | int | float | str | None = None,
        order_type: str = "FOK",
    ) -> Decimal:
        """Estimate the execution (marginal) price for a market order.

        Mirrors py-sdk's ``estimate_market_price``: it returns the **marginal
        (limit) price** — the price of the worst book level the order has to
        touch to fill — NOT a size-weighted average. A ``BUY`` walks the asks
        cheapest-first accumulating *notional* (``size * price``) against
        ``amount`` (USD); a ``SELL`` walks the bids highest-first accumulating
        *shares* against ``shares``. The price of the level at which the
        cumulative first reaches the target is the result.

        ``order_type`` (``"FOK"`` default / ``"FAK"``) controls under-fill
        handling, mirroring py-sdk: an ``FOK`` order the book cannot fully fill
        raises :class:`~polysim_polymarket.errors.InsufficientLiquidityError`; an
        ``FAK`` order falls back to the worst (deepest) resting level's price. A
        resolved price outside the ``[tick_size, 1 - tick_size]`` band raises
        :class:`~polysim_polymarket.errors.UnexpectedResponseError`.

        Input validation mirrors py-sdk's ``UserInputError`` contract: ``token_id``
        must be a non-empty string; ``side`` is case-SENSITIVE (exactly ``"BUY"``
        / ``"SELL"``); a ``BUY`` requires ``amount`` and forbids ``shares``; a
        ``SELL`` requires ``shares`` and forbids ``amount``; the side-specific
        quantity must be a positive number; ``order_type`` must be ``"FOK"`` or
        ``"FAK"``.

        Up/Down warning: walks the outcome's synthetic ~0.99 ladder, so the
        result is the cost of the *outcome* token, not the underlying asset.
        """
        notional = _common.validate_estimate_inputs(
            token_id=token_id, side=side, amount=amount, shares=shares, order_type=order_type
        )
        book = self._book_for_token(token_id)
        return _common.estimate_from_book(
            book, side=side, notional=notional, order_type=order_type
        )

    # ── markets (gamma reads) ───────────────────────────────────────────────

    def get_market(
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
        / ``locale`` are accepted for py-sdk signature parity and ignored — the
        PolySim market read has no URL-resolution, tag-inclusion or locale knob.

        Exactly one of ``id`` / ``slug`` must be given: zero (no-arg) or both
        raise ``UserInputError`` with py-sdk's exact market-lookup message
        (via :func:`_common.require_market_lookup_arg`), before any read.
        """
        _common.require_market_lookup_arg(id, slug)
        # require_market_lookup_arg guarantees exactly one of id/slug is set, so
        # the elif narrows ``slug`` to ``str`` for the type-checker.
        if id is not None:
            raw = self._client.get_market(id)
        elif slug is not None:
            raw = self._client.get_market_by_slug(slug)
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
    ) -> Paginator[Market]:
        """List markets. Mirrors py-sdk's ``list_markets``.

        Returns a :class:`~polysim_polymarket.pagination.Paginator` of
        :class:`~polysim_polymarket.models.Market`, so a ported bot drives it
        with ``.first_page()`` / ``.iter_items()`` exactly as on real Polymarket.

        The signature carries py-sdk's full gamma keyword set for a mechanical
        swap; the filters PolySimulator's ``GET /v1/markets`` supports
        (``closed`` and the common ones) forward server-side, and the gamma-only
        knobs PolySim has no analog for are accepted and ignored. Pagination is
        adapted from PolySim's offset model onto py-sdk's cursor surface via the
        shared cursor<->offset helpers.
        """
        forward = _common.list_markets_forward(closed=closed, order=order, ascending=ascending)

        def fetch(cursor: str | None) -> Page[Market]:
            offset = _decode_cursor(cursor)
            if offset < 0:
                return Page(items=(), has_more=False)
            rows = self._client.list_markets(limit=_common.PAGE_LIMIT, offset=offset, **forward)
            items = tuple(_common.adapt_market(row) for row in rows)
            next_cur = _next_cursor(offset, len(rows), _common.PAGE_LIMIT)
            return Page(
                items=items, has_more=len(rows) >= _common.PAGE_LIMIT, next_cursor=next_cur
            )

        return Paginator(fetch=fetch)
