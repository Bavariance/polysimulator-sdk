"""Asynchronous secure (authenticated) client mirroring py-sdk's
``polymarket.clients.async_secure.AsyncSecureClient`` — the async twin of the
mirror's sync :class:`~polysim_polymarket.clients.secure.SecureClient`.

``AsyncSecureClient`` exposes the SAME authenticated surface as
:class:`SecureClient` — every CLOB read, the auth-bootstrap + account reads, the
full trading surface, the on-chain PAPER no-ops, and the rewards/builder/RFQ
stubs — with every per-request method an ``async def`` coroutine (and the
list-style reads kept synchronous, returning an
:class:`~polysim_polymarket.pagination.AsyncPaginator` the bot then awaits),
exactly as py-sdk's ``AsyncSecureClient`` relates to its sync ``SecureClient``.

**DRY note — this client owns essentially NO business logic.** It is pure async
wiring:

* the shared CLOB reads are delegated whole to a composed
  :class:`~polysim_polymarket.clients.async_public.AsyncPublicClient` (built
  against the same host / api_key / environment) — exactly as the sync
  ``SecureClient`` composes ``PublicClient`` — so the read behaviour is the SAME
  ``_common`` code path, awaited;
* the authenticated account/auth/trading logic reuses the transport-free
  :mod:`~polysim_polymarket.clients._account` /
  :mod:`~polysim_polymarket.clients._trade` helpers verbatim (the SAME functions
  the sync client calls), with only the transport call ``await``\\ed;
* the on-chain guards + paper outcome reuse
  :mod:`~polysim_polymarket.clients._onchain` verbatim (``require_exactly_one`` /
  ``require_positive_amount`` / ``validate_address`` /
  ``paper_transaction_outcome``); the only async-specific addition is the async
  paper handle (:class:`~polysim_polymarket.clients._onchain.PaperAsyncTransactionHandle`),
  which itself lives in ``_onchain`` so it is shared/discoverable;
* the builder/RFQ ``NotImplementedError`` text reuses the shared
  ``_onchain.BUILDER_NOT_SIMULATED`` / ``_onchain.RFQ_NOT_SIMULATED`` constants —
  no re-declared strings.

There is therefore no second copy of any guard, price-walk, order-builder,
response-adapter, or error string here that could drift from the sync client.

**No live network / chain code.** Nothing in this module imports web3 /
eth_account / eth_utils or opens a socket; the on-chain methods are paper no-ops
whose ``await handle.wait()`` resolves instantly with no I/O. The async transport
is the same paced/retried :class:`polysim_sdk.aio.AsyncPolySimClient` the
:class:`AsyncPublicClient` uses.

**The sim->real auth swap.** Identical to the sync client's: on real Polymarket a
bot authenticates with ``await AsyncSecureClient.create(private_key=...,
wallet=...)`` and the SDK derives HMAC credentials from an on-chain signature;
PolySimulator is paper trading, so ``private_key`` / ``wallet`` / ``nonce`` and
the on-chain kwargs are accepted-and-inert — a single ``ps_live_*`` API key is
what authenticates. The mirror accepts BOTH the real-PM
``await AsyncSecureClient.create(private_key=...)`` form and the paper-native
``AsyncSecureClient(api_key=...)`` / ``await AsyncSecureClient.create(api_key=...)``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from types import TracebackType
from typing import Any, Literal, overload

from polysim_polymarket.clients import _account, _onchain, _trade
from polysim_polymarket.clients.async_public import AsyncPublicClient
from polysim_polymarket.environments import PRODUCTION, Environment
from polysim_polymarket.errors import PolyApiException, UserInputError
from polysim_polymarket.models import (
    ApiKeyCreds,
    AssetType,
    BalanceAllowance,
    BuilderFeeRates,
    BuilderTrade,
    BuilderVolumeEntry,
    BuilderVolumeTimePeriod,
    CancelOrdersResponse,
    ClobTrade,
    CurrentReward,
    LastTradePrice,
    LastTradePriceForToken,
    LeaderboardTimePeriod,
    Market,
    MarketOrderType,
    MarketReward,
    Notification,
    OpenOrder,
    OrderBook,
    OrderResponse,
    OrderSide,
    PriceHistoryInterval,
    PriceHistoryPoint,
    PriceRequest,
    RewardsPercentages,
    SignedOrder,
    TotalUserEarning,
    UserEarning,
    UserRewardsEarning,
)
from polysim_polymarket.pagination import AsyncPaginator, Page, _EmptyAsyncPaginator
from polysim_polymarket.streams import (
    CryptoPricesEvent,
    CryptoPricesSpec,
    MarketEvent,
    MarketSpec,
    SubscriptionHandle,
    UserEvent,
    UserSpec,
)
from polysim_polymarket.streams._stream_open import open_secure_stream as _open_secure_stream

# ``TransactionHandle`` is the py-sdk name for the ASYNC on-chain return type (an
# alias of the async paper handle, defined in ``_onchain``). The on-chain methods
# annotate ``-> TransactionHandle`` with the bare py-sdk name (annotation parity);
# ``_onchain`` is already imported above, so bind the name into this module's
# globals at RUNTIME (not only under TYPE_CHECKING) — otherwise
# ``typing.get_type_hints(AsyncSecureClient.<onchain method>)`` raises
# ``NameError``. The returned object is the same ``_onchain`` async paper handle.
TransactionHandle = _onchain.TransactionHandle


class AsyncSecureClient:
    """Async authenticated Polymarket-compatible client over the PolySim paper API.

    The async twin of :class:`~polysim_polymarket.clients.secure.SecureClient`.
    Holds an internal
    :class:`~polysim_polymarket.clients.async_public.AsyncPublicClient` (used for
    every shared CLOB read) and routes the authenticated account/auth/trading
    reads through that public client's internal
    :class:`polysim_sdk.aio.AsyncPolySimClient` transport (same host, same API
    key, same pacing/retry) — so the read surface is byte-identical to
    ``AsyncPublicClient`` and the account surface shares one async transport.

    Construct it either way:

    * ``AsyncSecureClient(api_key="ps_live_...")`` — the paper-native form.
    * ``await AsyncSecureClient.create(private_key="0x...", wallet="0x...")`` —
      the real-Polymarket form. ``private_key`` / ``wallet`` / ``nonce`` and the
      on-chain kwargs are accepted-and-inert; on paper the API key authenticates.

    Lifecycle is async (matching py-sdk): ``await client.close()`` closes the
    transport, and the client is an async context manager
    (``async with AsyncSecureClient(...) as client:``).
    """

    def __init__(
        self,
        environment: Environment = PRODUCTION,
        *,
        host: str | None = None,
        api_key: str | None = None,
        credentials: ApiKeyCreds | None = None,
        **_ignored: Any,
    ) -> None:
        # On-chain / py-sdk-only kwargs (private_key, wallet, chain_id,
        # signature_type, funder, nonce, logger, …) land in **_ignored and have
        # no effect — paper trading has no chain, no signer, no funder.
        self._environment = environment
        self._credentials = credentials
        # The internal AsyncPublicClient owns the shared read surface (and its own
        # AsyncPolySimClient transport). The AsyncSecureClient delegates every CLOB
        # read to it, so the read behaviour is the SAME code path as
        # AsyncPublicClient — no drift possible.
        self._public = AsyncPublicClient(environment, host=host, api_key=api_key)
        # The account/auth reads route through the public client's internal
        # AsyncPolySimClient transport (same host, same API key, same pacing/retry).
        self._client = self._public._client
        # token-id -> (market_id, outcome) reverse-resolution cache, populated
        # lazily for real-Polymarket outcome-token ids on the order paths.
        self._token_coordinates: dict[str, tuple[str, str]] = {}

    # ── alternate constructor (real-PM parity) ──────────────────────────────

    @classmethod
    async def create(
        cls,
        *,
        private_key: str | None = None,
        wallet: str | None = None,
        environment: Environment = PRODUCTION,
        credentials: ApiKeyCreds | None = None,
        api_key: str | None = None,
        nonce: int = 0,
        host: str | None = None,
        logger: logging.Logger | None = None,
        **_ignored: Any,
    ) -> AsyncSecureClient:
        """Create an authenticated async client, mirroring py-sdk's
        ``AsyncSecureClient.create`` (an ``async def`` classmethod).

        On real Polymarket this derives HMAC credentials from ``private_key`` (an
        on-chain signature, hence the ``await``). PolySimulator is paper trading,
        so ``private_key`` / ``wallet`` / ``nonce`` / ``logger`` and the on-chain
        kwargs are accepted-and-inert — the ``api_key`` (or ``POLYSIM_API_KEY``
        env) is what authenticates, and there is no network round-trip to await. A
        bot ports by deleting the on-chain prelude and swapping the import prefix;
        this call site (``await ...create(...)``) otherwise stays put.
        """
        return cls(
            environment,
            host=host,
            api_key=api_key,
            credentials=credentials,
        )

    # ── environment / credentials / lifecycle ───────────────────────────────

    @property
    def environment(self) -> Environment:
        """The :class:`Environment` this client is configured against.

        A **property** (not a method), mirroring the sync client + py-sdk's
        ``AsyncSecureClient.environment`` so a ported bot reads
        ``client.environment.clob_url`` without the call parens.
        """
        return self._environment

    @property
    def credentials(self) -> ApiKeyCreds | None:
        """The API credentials this client authenticates with.

        Mirrors the sync client's ``credentials`` property. On paper it is
        whatever ``credentials=`` was constructed with (``None`` when only a bare
        ``api_key`` was supplied).
        """
        return self._credentials

    # ── realtime streams (CORE topics, incl. the authenticated user feed) ────

    @overload
    async def subscribe(
        self, specs: MarketSpec, /, *, queue_size: int = ...
    ) -> SubscriptionHandle[MarketEvent]: ...
    @overload
    async def subscribe(
        self, specs: CryptoPricesSpec, /, *, queue_size: int = ...
    ) -> SubscriptionHandle[CryptoPricesEvent]: ...
    @overload
    async def subscribe(
        self, specs: UserSpec, /, *, queue_size: int = ...
    ) -> SubscriptionHandle[UserEvent]: ...
    async def subscribe(
        self,
        specs: MarketSpec | CryptoPricesSpec | UserSpec,
        *,
        queue_size: int = 1024,
    ) -> SubscriptionHandle[Any]:
        """Subscribe to a CORE realtime stream (``market`` / ``crypto_prices`` /
        ``user``).

        Mirrors py-sdk's positional-only ``specs`` ``subscribe`` for the CORE
        specs — and, like py-sdk's secure client, adds the authenticated
        :class:`~polysim_polymarket.streams.UserSpec` (order/trade fills) on top of
        the public topics. The user stream uses the client's API key (a short-lived
        WS JWT minted from it) and is scoped to that account — see
        ``streams/_stream_open.py`` for the auth seam.

        ``queue_size`` (a mirror-only keyword) sizes the handle's bounded queue.

        SCOPE: a single CORE spec per call (no merged multi-spec ``subscribe``).
        """
        return _open_secure_stream(self._client, specs, queue_size=queue_size)

    async def close(self) -> None:
        """Close the underlying async HTTP transport (``await client.close()``)."""
        await self._public.close()

    async def __aenter__(self) -> AsyncSecureClient:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ── shared CLOB reads (delegated to the internal AsyncPublicClient) ──────
    # These are the reads AsyncSecureClient shares with AsyncPublicClient. Every
    # one is a straight awaited delegation to the composed AsyncPublicClient, so
    # behaviour is identical by construction — same validation guards, same
    # _common logic, same models.

    async def get_order_book(self, *, token_id: str) -> OrderBook:
        """Get the order book for a token. Shares ``AsyncPublicClient.get_order_book``."""
        return await self._public.get_order_book(token_id=token_id)

    async def get_order_books(self, *, token_ids: Sequence[str]) -> tuple[OrderBook, ...]:
        """Get order books for multiple tokens. Shares ``AsyncPublicClient.get_order_books``."""
        return await self._public.get_order_books(token_ids=token_ids)

    async def get_midpoint(self, *, token_id: str) -> Decimal:
        """Get the midpoint price for a token. Shares ``AsyncPublicClient.get_midpoint``."""
        return await self._public.get_midpoint(token_id=token_id)

    async def get_midpoints(self, *, token_ids: Sequence[str]) -> dict[str, Decimal]:
        """Get midpoint prices for multiple tokens. Shares ``AsyncPublicClient.get_midpoints``."""
        return await self._public.get_midpoints(token_ids=token_ids)

    async def get_price(self, *, token_id: str, side: OrderSide) -> Decimal:
        """Get the executable price for a token side. Shares ``AsyncPublicClient.get_price``."""
        return await self._public.get_price(token_id=token_id, side=side)

    async def get_prices(
        self, *, requests: Sequence[PriceRequest]
    ) -> dict[str, dict[OrderSide, Decimal]]:
        """Get prices for multiple token-side requests. Shares ``AsyncPublicClient.get_prices``."""
        return await self._public.get_prices(requests=requests)

    async def get_spread(self, *, token_id: str) -> Decimal:
        """Get the bid-ask spread for a token. Shares ``AsyncPublicClient.get_spread``."""
        return await self._public.get_spread(token_id=token_id)

    async def get_spreads(self, *, token_ids: Sequence[str]) -> dict[str, Decimal]:
        """Get bid-ask spreads for multiple tokens. Shares ``AsyncPublicClient.get_spreads``."""
        return await self._public.get_spreads(token_ids=token_ids)

    async def get_last_trade_price(self, *, token_id: str) -> LastTradePrice:
        """Get the most recent trade price for a token. Shares the public singular."""
        return await self._public.get_last_trade_price(token_id=token_id)

    async def get_last_trade_prices(
        self, *, token_ids: Sequence[str]
    ) -> tuple[LastTradePriceForToken, ...]:
        """Get the most recent trade prices for multiple tokens. Shares the public plural."""
        return await self._public.get_last_trade_prices(token_ids=token_ids)

    async def get_price_history(
        self,
        *,
        token_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = None,
        interval: PriceHistoryInterval | None = None,
    ) -> tuple[PriceHistoryPoint, ...]:
        """Get historical price points for a token. Shares the public read."""
        return await self._public.get_price_history(
            token_id=token_id,
            start_ts=start_ts,
            end_ts=end_ts,
            fidelity=fidelity,
            interval=interval,
        )

    async def estimate_market_price(
        self,
        *,
        token_id: str,
        side: OrderSide,
        amount: Decimal | int | float | str | None = None,
        shares: Decimal | int | float | str | None = None,
        order_type: str = "FOK",
    ) -> Decimal:
        """Estimate the marginal market-order price.

        Shares ``AsyncPublicClient.estimate_market_price``.
        """
        return await self._public.estimate_market_price(
            token_id=token_id,
            side=side,
            amount=amount,
            shares=shares,
            order_type=order_type,
        )

    async def get_market(
        self,
        *,
        id: str | None = None,
        slug: str | None = None,
        url: str | None = None,
        include_tag: bool | None = None,
        locale: str | None = None,
    ) -> Market:
        """Get a market. Shares ``AsyncPublicClient.get_market``."""
        return await self._public.get_market(
            id=id, slug=slug, url=url, include_tag=include_tag, locale=locale
        )

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
        """List markets. Shares ``AsyncPublicClient.list_markets`` (full gamma keyword set).

        Returns an :class:`~polysim_polymarket.pagination.AsyncPaginator`
        **synchronously** (no ``await``), exactly like py-sdk's async client: the
        awaiting happens when the bot drives the paginator.
        """
        return self._public.list_markets(
            ascending=ascending,
            closed=closed,
            clob_token_ids=clob_token_ids,
            condition_ids=condition_ids,
            cyom=cyom,
            decimalized=decimalized,
            end_date_max=end_date_max,
            end_date_min=end_date_min,
            game_id=game_id,
            ids=ids,
            include_tag=include_tag,
            liquidity_num_max=liquidity_num_max,
            liquidity_num_min=liquidity_num_min,
            locale=locale,
            market_maker_addresses=market_maker_addresses,
            order=order,
            position_ids=position_ids,
            question_ids=question_ids,
            related_tags=related_tags,
            rfq_enabled=rfq_enabled,
            rewards_min_size=rewards_min_size,
            slug=slug,
            sports_market_types=sports_market_types,
            start_date_max=start_date_max,
            start_date_min=start_date_min,
            tag_id=tag_id,
            tag_match=tag_match,
            uma_resolution_status=uma_resolution_status,
            volume_num_max=volume_num_max,
            volume_num_min=volume_num_min,
            page_size=page_size,
        )

    # ── auth bootstrap ──────────────────────────────────────────────────────

    async def fetch_api_keys(self) -> tuple[str, ...]:
        """Fetch API key identifiers for the authenticated account.

        Async twin of ``SecureClient.fetch_api_keys``: reads PolySimulator's
        ``GET /v1/keys`` (awaited) and projects each key record's identifier onto
        py-sdk's bare-id tuple via the shared :func:`_account.api_key_ids`.
        """
        keys = await self._client.list_keys()
        return _account.api_key_ids(keys)

    async def delete_api_key(self) -> None:
        """Delete the API key currently used by this client.

        Async twin of ``SecureClient.delete_api_key``: a no-op that returns
        ``None`` (py-sdk's return type) — paper keys are managed via the
        dashboard, so there is nothing to delete here.
        """
        return None

    async def is_gasless_ready(self) -> bool:
        """Return ``True``. Async twin of ``SecureClient.is_gasless_ready``.

        There is no gasless wallet to ready on paper, so this is unconditionally
        ``True`` — the porting author's ``if not await client.is_gasless_ready():``
        branch is correctly never taken.
        """
        return True

    # ── account / liveness reads ────────────────────────────────────────────

    async def get_balance_allowance(
        self, *, asset_type: AssetType, token_id: str | None = None
    ) -> BalanceAllowance:
        """Get balance and allowance information for an asset.

        Async twin of ``SecureClient.get_balance_allowance``: validates
        ``asset_type`` (case-SENSITIVE) via the shared
        :func:`_account.validate_asset_type`. ``COLLATERAL`` awaits
        ``GET /v1/account/balance`` and adapts the paper-cash figure;
        ``CONDITIONAL`` (token_id required) resolves the token, awaits
        ``GET /v1/account/positions`` and reports the open position's share count
        as the conditional-token balance (matching real py-sdk's CONDITIONAL
        semantics — the held conditional token, not collateral; flat = 0).
        """
        _account.validate_asset_type(asset_type)
        if asset_type == "CONDITIONAL":
            if not token_id:
                raise UserInputError("token_id is required for a CONDITIONAL balance.")
            market_id, outcome = await self._resolve_coordinates(token_id)
            positions = await self._client.positions()
            return _account.adapt_conditional_balance(positions, market_id, outcome)
        payload = await self._client.balance()
        return _account.adapt_balance_allowance(payload)

    async def get_closed_only_mode(self) -> bool:
        """Return whether the authenticated account is in closed-only mode.

        Async twin of ``SecureClient.get_closed_only_mode``: paper accounts are
        never closing-only, so unconditionally ``False``.
        """
        return False

    async def get_notifications(self) -> tuple[Notification, ...]:
        """Get notifications for the authenticated account.

        Async twin of ``SecureClient.get_notifications``: the paper CLOB has no
        notifications feed, so an empty tuple of the real :class:`Notification`
        element type.
        """
        return ()

    # ── authenticated order reads ───────────────────────────────────────────

    async def get_order(self, *, order_id: str) -> OpenOrder:
        """Get one open order for the authenticated account.

        Async twin of ``SecureClient.get_order``: validates ``order_id`` via the
        shared :func:`_account.require_nonempty`, reads
        ``GET /v1/orders/{order_id}`` (awaited), and adapts the row onto
        ``OpenOrder`` via :func:`_account.adapt_open_order`.
        """
        validated = _account.require_nonempty("order_id", order_id)
        raw = await self._client.get_order(validated)
        return _account.adapt_open_order(raw)

    def list_open_orders(
        self,
        *,
        token_id: str | None = None,
        id: str | None = None,
        market: str | None = None,
    ) -> AsyncPaginator[OpenOrder]:
        """List open orders for the authenticated account.

        Async twin of ``SecureClient.list_open_orders`` (returns an
        ``AsyncPaginator[OpenOrder]`` synchronously). Each page-fetch awaits
        PolySimulator's Polymarket-shape ``GET /v1/data/orders`` and adapts via the
        shared :func:`_account.adapt_open_orders_page`.
        """

        async def fetch(cursor: str | None) -> Page[OpenOrder]:
            envelope = await self._client.data_orders(
                id=id,
                market=market,
                asset_id=token_id,
                next_cursor=cursor or _account.START_CURSOR,
                limit=_account.PAGE_LIMIT,
            )
            return _account.adapt_open_orders_page(envelope)

        return AsyncPaginator(fetch=fetch)

    def list_account_trades(
        self,
        *,
        token_id: str | None = None,
        id: str | None = None,
        market: str | None = None,
        maker_address: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> AsyncPaginator[ClobTrade]:
        """List trades for the authenticated account.

        Async twin of ``SecureClient.list_account_trades`` (returns an
        ``AsyncPaginator[ClobTrade]`` synchronously). Each page-fetch awaits
        PolySimulator's ``GET /v1/data/trades`` and adapts via the shared
        :func:`_account.adapt_account_trades_page`. ``id`` / ``maker_address`` are
        accepted for py-sdk signature parity and ignored (documented seam).
        """

        async def fetch(cursor: str | None) -> Page[ClobTrade]:
            envelope = await self._client.data_trades(
                market=market,
                asset_id=token_id,
                before=before,
                after=after,
                next_cursor=cursor or _account.START_CURSOR,
                limit=_account.PAGE_LIMIT,
            )
            return _account.adapt_account_trades_page(envelope)

        return AsyncPaginator(fetch=fetch)

    # ── trading: token-id resolution ────────────────────────────────────────

    async def _resolve_coordinates(self, token_id: str) -> tuple[str, str]:
        """Resolve a token id to PolySim ``(market_id, outcome)`` for an order.

        Async twin of ``SecureClient._resolve_coordinates``: the colon form and
        short/non-numeric ids resolve **locally** (no network) via the shared
        :mod:`_trade` seam; a long all-digit real-Polymarket outcome-token id is
        reverse-resolved by awaiting ``GET /v1/markets-by-token/{id}`` (cached per
        token). All routing logic is the transport-free ``_trade`` seam; only the
        awaited fetch lives here.

        ``token_id`` is validated (non-empty string) UP FRONT — before the
        reverse-resolution network call — so a bad token raises ``UserInputError``
        rather than hitting the network.
        """
        _trade.validate_token_id(token_id)
        if not _trade.needs_token_reverse_resolution(token_id):
            return _trade.split_token_local(token_id)
        cached = self._token_coordinates.get(token_id)
        if cached is not None:
            return cached
        market = await self._client.get_market_by_token(token_id)
        resolved = _trade.coordinates_from_market_payload(token_id, market)
        self._token_coordinates[token_id] = resolved
        return resolved

    # ── trading: build (create) ─────────────────────────────────────────────

    async def create_limit_order(
        self,
        *,
        token_id: str,
        price: Decimal | int | float | str,
        size: Decimal | int | float | str,
        side: OrderSide,
        post_only: bool = False,
        expiration: int | None = None,
        builder_code: str | None = None,
    ) -> SignedOrder:
        """Build (and inertly sign) a limit order WITHOUT posting it.

        Async twin of ``SecureClient.create_limit_order``: awaits the
        token->coordinates resolution then builds the inert-signed
        :class:`SignedOrder` via the shared :func:`_trade.build_limit_order`
        (same validation, same body). No network is hit for the build itself when
        the token resolves locally.
        """
        market_id, outcome = await self._resolve_coordinates(token_id)
        return _trade.build_limit_order(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            post_only=post_only,
            expiration=expiration,
            builder_code=builder_code,
            market_id=market_id,
            outcome=outcome,
        )

    async def create_market_order(
        self,
        *,
        token_id: str,
        side: OrderSide,
        amount: Decimal | int | float | str | None = None,
        shares: Decimal | int | float | str | None = None,
        max_spend: Decimal | int | float | str | None = None,
        max_price: Decimal | int | float | str | None = None,
        min_price: Decimal | int | float | str | None = None,
        order_type: MarketOrderType = "FAK",
        builder_code: str | None = None,
    ) -> SignedOrder:
        """Build (and inertly sign) a market order WITHOUT posting it.

        Async twin of ``SecureClient.create_market_order``: awaits the
        token->coordinates resolution then builds via the shared
        :func:`_trade.build_market_order` — same BUY/SELL split, same
        worst-acceptable-price cap (the BUY/SELL worst-price defaults defined in
        ``_trade``), same ``max_spend`` clamp.
        """
        market_id, outcome = await self._resolve_coordinates(token_id)
        return _trade.build_market_order(
            token_id=token_id,
            side=side,
            amount=amount,
            shares=shares,
            max_spend=max_spend,
            max_price=max_price,
            min_price=min_price,
            order_type=order_type,
            builder_code=builder_code,
            market_id=market_id,
            outcome=outcome,
        )

    # ── trading: build + post (place) ───────────────────────────────────────

    async def place_limit_order(
        self,
        *,
        token_id: str,
        price: Decimal | int | float | str,
        size: Decimal | int | float | str,
        side: OrderSide,
        post_only: bool = False,
        expiration: int | None = None,
        builder_code: str | None = None,
    ) -> OrderResponse:
        """Build, inertly sign, and post a limit order in one call.

        Async twin of ``SecureClient.place_limit_order``: equivalent to
        ``await post_order(await create_limit_order(...))``.
        """
        signed = await self.create_limit_order(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            post_only=post_only,
            expiration=expiration,
            builder_code=builder_code,
        )
        return await self.post_order(signed)

    async def place_market_order(
        self,
        *,
        token_id: str,
        side: OrderSide,
        amount: Decimal | int | float | str | None = None,
        shares: Decimal | int | float | str | None = None,
        max_spend: Decimal | int | float | str | None = None,
        max_price: Decimal | int | float | str | None = None,
        min_price: Decimal | int | float | str | None = None,
        order_type: MarketOrderType = "FAK",
        builder_code: str | None = None,
    ) -> OrderResponse:
        """Build, inertly sign, and post a market order in one call.

        Async twin of ``SecureClient.place_market_order``: carries the same
        worst-price-cap guarantee as ``create_market_order``.
        """
        signed = await self.create_market_order(
            token_id=token_id,
            side=side,
            amount=amount,
            shares=shares,
            max_spend=max_spend,
            max_price=max_price,
            min_price=min_price,
            order_type=order_type,
            builder_code=builder_code,
        )
        return await self.post_order(signed)

    # ── trading: post ───────────────────────────────────────────────────────

    async def post_order(self, signed_order: SignedOrder) -> OrderResponse:
        """Post a built order (``POST /v1/orders``).

        Async twin of ``SecureClient.post_order``: projects the unsigned
        ``paper_body`` onto the wire via the shared :func:`_trade.paper_order_kwargs`
        (the placeholder signature is never sent), awaits the submit, and adapts
        via :func:`_trade.adapt_order_response`.
        """
        raw = await self._client.place_order(**_trade.paper_order_kwargs(signed_order.paper_body))
        return _trade.adapt_order_response(raw)

    async def post_orders(self, signed_orders: Sequence[SignedOrder]) -> tuple[OrderResponse, ...]:
        """Post a batch of built orders (``POST /v1/orders/batch``).

        Async twin of ``SecureClient.post_orders``: each order's unsigned
        ``paper_body`` is projected via the SAME :func:`_trade.paper_order_kwargs`
        glue ``post_order`` uses, so the single + batch post paths never drift.
        """
        bodies = [_trade.paper_order_kwargs(order.paper_body) for order in signed_orders]
        rows = await self._client.place_orders(bodies)
        return tuple(_trade.adapt_order_response(row) for row in rows)

    # ── trading: cancel ─────────────────────────────────────────────────────

    async def cancel_order(self, *, order_id: str) -> CancelOrdersResponse:
        """Cancel one open order (``DELETE /v1/orders/{order_id}``).

        Async twin of ``SecureClient.cancel_order``: validates ``order_id`` via
        the shared :func:`_account.require_nonempty`, awaits the cancel, and
        tallies onto py-sdk's :class:`CancelOrdersResponse` via the shared
        :func:`_trade.build_cancel_orders_response`.
        """
        validated = _account.require_nonempty("order_id", order_id)
        try:
            await self._client.cancel_order(validated)
        except PolyApiException as exc:
            return _trade.build_cancel_orders_response([], {validated: str(exc)})
        return _trade.build_cancel_orders_response([validated], {})

    async def cancel_orders(self, *, order_ids: Sequence[str]) -> CancelOrdersResponse:
        """Cancel several open orders (loops ``DELETE /v1/orders/{id}``).

        Async twin of ``SecureClient.cancel_orders``: ALL ``order_ids`` are
        validated UP FRONT via the shared :func:`_trade.validate_cancel_order_ids`
        (so an invalid id raises ``UserInputError`` BEFORE any network cancel
        fires), then each is awaited and tallied onto ``CancelOrdersResponse``.
        """
        validated_ids = _trade.validate_cancel_order_ids(order_ids)
        canceled: list[str] = []
        not_canceled: dict[str, str] = {}
        for oid in validated_ids:
            try:
                await self._client.cancel_order(oid)
                canceled.append(oid)
            except PolyApiException as exc:
                not_canceled[oid] = str(exc)
        return _trade.build_cancel_orders_response(canceled, not_canceled)

    async def cancel_all(self) -> CancelOrdersResponse:
        """Cancel EVERY open order on the account (``POST /v1/cancel-all``).

        Async twin of ``SecureClient.cancel_all``: the underlying transport sends
        the backend's mandatory confirmation form (``X-Confirm-Cancel-All: true``);
        the awaited reply is adapted via the shared :func:`_trade.adapt_cancel_response`.
        """
        raw = await self._client.cancel_all()
        return _trade.adapt_cancel_response(raw)

    async def cancel_market_orders(
        self, *, market: str | None = None, token_id: str | None = None
    ) -> CancelOrdersResponse:
        """Cancel all open orders matching a market/token filter
        (``DELETE /v1/cancel-market-orders``).

        Async twin of ``SecureClient.cancel_market_orders``: either ``market`` or
        ``token_id`` scopes the cancel; at least one is required, else
        ``UserInputError`` with py-sdk's exact message. A ``token_id`` is first
        reverse-resolved to its market id (awaited) before the cancel is scoped by
        that market. Reply adapted via the shared :func:`_trade.adapt_cancel_response`.
        """
        if market:
            market_id = market
        elif token_id:
            market_id, _ = await self._resolve_coordinates(token_id)
        else:
            raise UserInputError("At least one of market or token_id is required.")
        raw = await self._client.cancel_market_orders(market_id)
        return _trade.adapt_cancel_response(raw)

    # ── on-chain workflows (PAPER no-ops) ────────────────────────────────────
    # Async twins of the sync client's on-chain no-ops. Each replicates py-sdk's
    # pre-chain INPUT guards (the SHARED _onchain.require_exactly_one /
    # require_positive_amount / validate_address) then returns an ASYNC PAPER
    # handle whose ``await wait()`` resolves INSTANTLY with the same paper
    # TransactionOutcome the sync handle returns — no chain, no signer, no web3, no
    # socket, and no paper-state mutation. The DRY paper core + guards live in
    # :mod:`_onchain` so there is one copy across the sync + async clients.

    async def approve_erc20(
        self,
        *,
        token_address: str,
        spender_address: str,
        amount: int | Literal["max"],
        metadata: str | None = None,
    ) -> TransactionHandle:
        """Submit an ERC-20 approval transaction — PAPER no-op.

        Async twin of ``SecureClient.approve_erc20``. Validates the addresses via
        the shared :func:`_onchain.validate_address` (40-hex format check, not an
        EIP-55 checksum) and returns an async paper handle whose
        ``await wait()`` resolves instantly with a placeholder
        :class:`~polysim_polymarket.models.TransactionOutcome`. ``amount`` accepts
        ``"max"`` for parity; ``metadata`` is accepted and inert.
        """
        _onchain.validate_address("token_address", token_address)
        _onchain.validate_address("spender_address", spender_address)
        return _onchain.paper_async_handle()

    async def approve_erc1155_for_all(
        self,
        *,
        token_address: str,
        operator_address: str,
        approved: bool = True,
        metadata: str | None = None,
    ) -> TransactionHandle:
        """Approve/revoke an ERC-1155 operator for all tokens — PAPER no-op.

        Async twin of ``SecureClient.approve_erc1155_for_all``. Validates the
        addresses via the shared :func:`_onchain.validate_address` and returns an
        async paper handle whose ``await wait()`` resolves instantly. ``approved``
        / ``metadata`` are accepted and inert.
        """
        _onchain.validate_address("token_address", token_address)
        _onchain.validate_address("operator_address", operator_address)
        return _onchain.paper_async_handle()

    async def transfer_erc20(
        self,
        *,
        token_address: str,
        recipient_address: str,
        amount: int,
        metadata: str | None = None,
    ) -> TransactionHandle:
        """Submit an ERC-20 transfer transaction — PAPER no-op.

        Async twin of ``SecureClient.transfer_erc20``. Validates the addresses via
        the shared :func:`_onchain.validate_address` and returns an async paper
        handle whose ``await wait()`` resolves instantly. ``metadata`` is accepted
        and inert.
        """
        _onchain.validate_address("token_address", token_address)
        _onchain.validate_address("recipient_address", recipient_address)
        return _onchain.paper_async_handle()

    async def split_position(
        self,
        *,
        condition_id: str | None = None,
        legs: Sequence[str] | None = None,
        amount: int,
        metadata: str | None = None,
    ) -> TransactionHandle:
        """Split collateral into market or combo positions — PAPER no-op.

        Async twin of ``SecureClient.split_position``. Provide EXACTLY one of
        ``condition_id`` / ``legs`` (the shared :func:`_onchain.require_exactly_one`
        guard, py-sdk's exact message); the combo branch also requires a positive
        ``amount`` (the shared :func:`_onchain.require_positive_amount`). Returns
        an async paper handle whose ``await wait()`` resolves instantly.
        """
        _onchain.require_exactly_one(
            "Provide exactly one of condition_id or legs",
            condition_id=condition_id,
            legs=legs,
        )
        if legs is not None:
            _onchain.require_positive_amount(
                amount, "Split amount must be positive for combo positions"
            )
        return _onchain.paper_async_handle()

    async def merge_positions(
        self,
        *,
        condition_id: str | None = None,
        legs: Sequence[str] | None = None,
        amount: int | Literal["max"],
        metadata: str | None = None,
    ) -> TransactionHandle:
        """Merge market or combo positions back into collateral — PAPER no-op.

        Async twin of ``SecureClient.merge_positions``. Provide EXACTLY one of
        ``condition_id`` / ``legs`` (the shared :func:`_onchain.require_exactly_one`
        guard). ``amount`` accepts ``"max"`` for parity. Unlike real py-sdk, paper
        mode does NOT balance-check the position (the paper position ledger isn't
        wired into on-chain methods in this gate), so this always succeeds on
        paper; ``await wait()`` resolves instantly.
        """
        _onchain.require_exactly_one(
            "Provide exactly one of condition_id or legs",
            condition_id=condition_id,
            legs=legs,
        )
        return _onchain.paper_async_handle()

    async def redeem_positions(
        self,
        *,
        condition_id: str | None = None,
        market_id: str | None = None,
        position_id: str | None = None,
        metadata: str | None = None,
    ) -> TransactionHandle:
        """Redeem resolved market or combo positions — PAPER no-op.

        Async twin of ``SecureClient.redeem_positions``. Provide EXACTLY one of
        ``condition_id`` / ``market_id`` / ``position_id`` (the shared
        :func:`_onchain.require_exactly_one` guard). Unlike real py-sdk, paper mode
        does NOT balance-check the position, so this always succeeds on paper;
        ``await wait()`` resolves instantly.
        """
        _onchain.require_exactly_one(
            "Provide exactly one of condition_id, market_id, or position_id",
            condition_id=condition_id,
            market_id=market_id,
            position_id=position_id,
        )
        return _onchain.paper_async_handle()

    async def setup_trading_approvals(self) -> _onchain.PaperAsyncDeprecatedTransactionHandle:
        """Approve the standard trading allowances — PAPER no-op.

        Async twin of ``SecureClient.setup_trading_approvals`` (mirrors py-sdk's
        deprecated async ``setup_trading_approvals``, which returns a
        ``DeprecatedTransactionHandle`` whose ``await wait()`` returns ``None``).
        On paper there is nothing to approve, so it returns the deprecated async
        paper handle directly and ``await wait()`` is a ``None``-returning no-op.
        """
        return _onchain.paper_async_deprecated_handle()

    async def setup_gasless_wallet(self) -> AsyncSecureClient:
        """Return this client — PAPER no-op.

        Async twin of ``SecureClient.setup_gasless_wallet`` (mirrors py-sdk's
        deprecated async ``setup_gasless_wallet``, which returns ``Self``). On
        paper there is no gasless wallet to set up, so it just returns ``self``.
        """
        return self

    # ── rewards + scoring (honest empty / scoring:false stubs) ───────────────
    # Async twins of the sync client's honest rewards stubs: scoring is False,
    # the list reads are EMPTY AsyncPaginators of the correct element type, the
    # totals are an empty tuple, and the percentages are an empty dict. No
    # network is hit — the paper rewards engine is a separate backend roadmap
    # item, so a bot's reward-accounting loop runs but finds nothing (truthful
    # for paper).

    async def get_order_scoring(self, *, order_id: str) -> bool:
        """Return whether an order is currently scoring rewards — PAPER stub.

        Async twin of ``SecureClient.get_order_scoring``: an order never scores
        rewards on paper, so unconditionally ``False``.
        """
        return False

    async def get_orders_scoring(self, *, order_ids: Sequence[str]) -> dict[str, bool]:
        """Return reward-scoring status for multiple orders — PAPER stub.

        Async twin of ``SecureClient.get_orders_scoring``: every id maps to
        ``False``, keyed by exactly the given ``order_ids``.
        """
        return {order_id: False for order_id in order_ids}

    def list_current_rewards(
        self, *, sponsored: bool | None = None
    ) -> AsyncPaginator[CurrentReward]:
        """List current rewards — PAPER stub (empty async paginator).

        Async twin of ``SecureClient.list_current_rewards``. ``sponsored`` is
        accepted for parity and inert.
        """
        return self._empty_paginator()

    def list_market_rewards(
        self, *, condition_id: str, sponsored: bool | None = None
    ) -> AsyncPaginator[MarketReward]:
        """List rewards for a market condition — PAPER stub (empty async paginator).

        Async twin of ``SecureClient.list_market_rewards``. ``condition_id`` /
        ``sponsored`` are accepted for parity and inert.
        """
        return self._empty_paginator()

    def list_user_earnings_for_day(self, *, date: str) -> AsyncPaginator[UserEarning]:
        """List reward earnings for the user on a date — PAPER stub (empty async paginator).

        Async twin of ``SecureClient.list_user_earnings_for_day``. ``date`` is
        accepted for parity and inert.
        """
        return self._empty_paginator()

    async def get_total_earnings_for_user_for_day(
        self, *, date: str
    ) -> tuple[TotalUserEarning, ...]:
        """Total reward earnings for the user on a date — PAPER stub (empty tuple).

        Async twin of ``SecureClient.get_total_earnings_for_user_for_day``.
        ``date`` is accepted for parity and inert.
        """
        return ()

    def list_user_earnings_and_markets_config(
        self,
        *,
        date: str,
        no_competition: bool | None = None,
        order_by: str | None = None,
        position: str | None = None,
        page_size: int | None = None,
    ) -> AsyncPaginator[UserRewardsEarning]:
        """List reward earnings + market config — PAPER stub (empty async paginator).

        Async twin of ``SecureClient.list_user_earnings_and_markets_config``.
        Every filter kwarg is accepted for parity and inert.
        """
        return self._empty_paginator()

    async def get_reward_percentages(self) -> RewardsPercentages:
        """Current reward-percentage allocations — PAPER stub (empty dict).

        Async twin of ``SecureClient.get_reward_percentages``: an honest empty
        ``{}`` — no fabricated allocations.
        """
        return {}

    @staticmethod
    def _empty_paginator() -> AsyncPaginator[Any]:
        """A typed, fetch-free empty async paginator for the rewards list stubs.

        Reuses the package's
        :class:`~polysim_polymarket.pagination._EmptyAsyncPaginator` (one shared
        empty-async-paginator implementation across the mirror) so every rewards
        list read yields one empty page and stops, with no network fetch.
        """
        return _EmptyAsyncPaginator()

    # ── builder attribution (NOT simulated — raises NotImplementedError) ──────
    # Async twins of the sync client's builder methods: each mirrors py-sdk's
    # EXACT signature but raises NotImplementedError with the SHARED
    # _onchain.BUILDER_NOT_SIMULATED message — no re-declared string. Builder
    # attribution has no analog on paper.

    async def get_builder_volumes(
        self, *, time_period: BuilderVolumeTimePeriod | None = None
    ) -> tuple[BuilderVolumeEntry, ...]:
        """Get builder volume leaderboard entries — NOT simulated on paper.

        Async twin of ``SecureClient.get_builder_volumes``; raises
        :class:`NotImplementedError` (``_onchain.BUILDER_NOT_SIMULATED``).
        """
        raise NotImplementedError(_onchain.BUILDER_NOT_SIMULATED)

    def list_builder_trades(
        self,
        *,
        builder_code: str,
        market: str | None = None,
        token_id: str | None = None,
        id: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> AsyncPaginator[BuilderTrade]:
        """List builder-attributed trades — NOT simulated on paper.

        Async twin of ``SecureClient.list_builder_trades``; raises
        :class:`NotImplementedError` (``_onchain.BUILDER_NOT_SIMULATED``). It is a
        plain ``def`` (matching py-sdk's async client, whose ``list_builder_trades``
        is synchronous and returns an ``AsyncPaginator``) so the not-simulated
        signal surfaces at call time, exactly as the sync client's does.
        """
        raise NotImplementedError(_onchain.BUILDER_NOT_SIMULATED)

    async def get_builder_fee_rates(self, builder_code: str) -> BuilderFeeRates:
        """Get fee rates for a builder code — NOT simulated on paper.

        Async twin of ``SecureClient.get_builder_fee_rates`` (a POSITIONAL
        ``builder_code``); raises :class:`NotImplementedError`
        (``_onchain.BUILDER_NOT_SIMULATED``).
        """
        raise NotImplementedError(_onchain.BUILDER_NOT_SIMULATED)

    def list_builder_leaderboard(
        self,
        *,
        time_period: LeaderboardTimePeriod | None = None,
        page_size: int = 20,
    ) -> AsyncPaginator[Any]:
        """List builder leaderboard entries — NOT simulated on paper.

        Async twin of ``SecureClient.list_builder_leaderboard``: ``time_period`` is
        annotated ``LeaderboardTimePeriod`` (py-sdk's own alias for this read) for
        annotation parity. py-sdk returns an ``AsyncPaginator[LeaderboardEntry]``;
        the element type belongs to a later leaderboard gate, so the mirror types
        it ``AsyncPaginator[Any]``. The method raises :class:`NotImplementedError`
        (``_onchain.BUILDER_NOT_SIMULATED``).
        """
        raise NotImplementedError(_onchain.BUILDER_NOT_SIMULATED)
