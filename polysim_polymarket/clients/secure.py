"""Synchronous secure (authenticated) client mirroring py-sdk's
``polymarket.clients.secure.SecureClient`` — through gate G4.

py-sdk's ``SecureClient`` is the authenticated client: it carries credentials and
adds account/auth/trading/on-chain workflows on top of the public read surface.
This mirror ships:

* **READS + ACCOUNT + AUTH** (gate G2) — every CLOB market-data read
  ``SecureClient`` shares with ``PublicClient`` (``get_order_book(s)`` /
  ``get_midpoint(s)`` / ``get_price(s)`` / ``get_spread(s)`` /
  ``get_last_trade_price(s)`` / ``get_price_history`` / ``estimate_market_price``
  / ``list_markets`` / ``get_market`` + the ``environment`` property); the
  auth-bootstrap surface (``fetch_api_keys`` / ``delete_api_key`` + the
  ``credentials`` property); account / liveness reads
  (``get_balance_allowance`` / ``get_closed_only_mode`` / ``get_notifications`` /
  ``is_gasless_ready``); and the authenticated order reads (``get_order`` /
  ``list_open_orders`` / ``list_account_trades``);
* **TRADING** (gate G3) — ``create_*`` / ``place_*`` / ``post_*`` / ``cancel_*``;
* **ON-CHAIN as PAPER no-ops** (gate G4) — ``approve_erc20`` /
  ``approve_erc1155_for_all`` / ``transfer_erc20`` / ``split_position`` /
  ``merge_positions`` / ``redeem_positions`` / ``setup_trading_approvals`` /
  ``setup_gasless_wallet``: each replicates py-sdk's pre-chain input guards then
  returns an instant-success PAPER transaction handle (no chain, no network) —
  see :mod:`polysim_polymarket.clients._onchain`;
* **REWARDS + SCORING honest stubs** (gate G4) — ``get_order_scoring`` /
  ``get_orders_scoring`` / ``list_current_rewards`` / ``list_market_rewards`` /
  ``list_user_earnings_for_day`` / ``get_total_earnings_for_user_for_day`` /
  ``list_user_earnings_and_markets_config`` / ``get_reward_percentages``: the
  paper rewards engine is a separate backend roadmap item, so each returns an
  HONEST empty value (scoring False / empty paginator / empty tuple / empty dict)
  with no fabricated data;
* **BUILDER attribution** (gate G4) — ``get_builder_volumes`` /
  ``list_builder_trades`` / ``get_builder_fee_rates`` /
  ``list_builder_leaderboard``: NOT simulated on paper, so each mirrors py-sdk's
  signature but raises ``NotImplementedError``.

RFQ has no synchronous ``SecureClient`` entrypoint on py-sdk (it is an async-only
streaming feature); its TYPES re-export from the package root (see
:mod:`polysim_polymarket.rfq`) but no RFQ method is invented here. The G5 async
``AsyncSecureClient`` is the only surface still deferred at the client level.

**DRY note — how the reads are shared.** The CLOB reads are NOT re-implemented
here. ``SecureClient`` composes an internal
:class:`~polysim_polymarket.clients.public.PublicClient` (built against the same
host / api_key / environment) and delegates every shared read to it. The read
behaviour is therefore *literally* the same code path — there is no second copy
of the read logic that could drift from ``PublicClient`` (which itself routes its
transport-free logic through :mod:`polysim_polymarket.clients._common`, shared
with the async public client). ``SecureClient`` read == ``PublicClient`` read ==
real py-sdk read, by construction.

**The sim->real auth swap.** On real Polymarket, a bot authenticates with
``SecureClient.create(private_key=..., wallet=...)`` and the SDK derives HMAC API
credentials from an on-chain signature. PolySimulator is paper trading: there is
no chain, no signer, no L1/L2 HMAC — auth collapses to ONE mode, a single
``ps_live_*`` API key sent as ``X-API-Key``. So the mirror accepts BOTH shapes:
``SecureClient.create(private_key=...)`` (the real-PM call, with ``private_key``
and every on-chain kwarg accepted-and-inert) and the paper-native
``SecureClient(api_key=...)`` / ``SecureClient.create(api_key=...)``. A bot ports
by deleting its on-chain prelude and the import prefix; the call site otherwise
stays put.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from decimal import Decimal
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal

from polysim_polymarket.clients import _account, _onchain, _trade
from polysim_polymarket.clients.public import PublicClient
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
from polysim_polymarket.pagination import Page, Paginator, _EmptyPaginator

if TYPE_CHECKING:
    # ``SyncTransactionHandle`` is the py-sdk name for the on-chain return type (an
    # alias of the paper handle, defined in ``_onchain``). Imported under
    # TYPE_CHECKING so the on-chain methods can annotate ``-> SyncTransactionHandle``
    # with the bare py-sdk name (annotation-string parity) without a runtime import;
    # the returned object is the same ``_onchain`` paper handle either way.
    from polysim_polymarket.clients._onchain import SyncTransactionHandle


class SecureClient:
    """Authenticated Polymarket-compatible client over the PolySim paper API.

    Holds an internal :class:`~polysim_polymarket.clients.public.PublicClient`
    (used for every shared CLOB read) and an internal
    :class:`polysim_sdk.PolySimClient` (used for the authenticated account/auth
    reads). Both are built against the same resolved host + API key, so the read
    surface is byte-identical to ``PublicClient`` and the account surface uses the
    same paced/retried transport.

    Construct it either way:

    * ``SecureClient(api_key="ps_live_...")`` — the paper-native form.
    * ``SecureClient.create(private_key="0x...", wallet="0x...")`` — the
      real-Polymarket form. ``private_key`` / ``wallet`` / ``nonce`` and the
      on-chain kwargs are accepted-and-inert; on paper the API key is what
      authenticates. Pass ``api_key=`` to set it, else it falls back to the
      ``POLYSIM_API_KEY`` env (via ``PolySimClient``).
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
        # The internal PublicClient owns the shared read surface (and its own
        # PolySimClient transport). The SecureClient delegates every CLOB read to
        # it, so the read behaviour is the SAME code path as PublicClient — no
        # drift possible.
        self._public = PublicClient(environment, host=host, api_key=api_key)
        # The account/auth reads route through the public client's internal
        # PolySimClient transport (same host, same API key, same pacing/retry).
        self._client = self._public._client
        # token-id -> (market_id, outcome) reverse-resolution cache, populated
        # lazily for real-Polymarket outcome-token ids on the order paths.
        self._token_coordinates: dict[str, tuple[str, str]] = {}

    # ── alternate constructor (real-PM parity) ──────────────────────────────

    @classmethod
    def create(
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
    ) -> SecureClient:
        """Create an authenticated client, mirroring py-sdk's ``SecureClient.create``.

        On real Polymarket this derives HMAC credentials from ``private_key`` (an
        on-chain signature). PolySimulator is paper trading, so ``private_key`` /
        ``wallet`` / ``nonce`` / ``logger`` and the on-chain kwargs are
        accepted-and-inert — the ``api_key`` (or ``POLYSIM_API_KEY`` env) is what
        authenticates. A bot ports by deleting the on-chain prelude and swapping
        the import prefix; this call site otherwise stays put.
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

        A **property** (not a method), mirroring py-sdk's
        ``SecureClient.environment`` so a ported bot reads
        ``client.environment.clob_url`` without the call parens.
        """
        return self._environment

    @property
    def credentials(self) -> ApiKeyCreds | None:
        """The API credentials this client authenticates with.

        Mirrors py-sdk's ``SecureClient.credentials`` (a **property**). On real
        Polymarket this is the derived HMAC :class:`ApiKeyCreds`; on paper it is
        whatever ``credentials=`` was constructed with (``None`` when only a bare
        ``api_key`` was supplied — paper auth needs only the single API key, so
        the full key/secret/passphrase triple is optional here).
        """
        return self._credentials

    def close(self) -> None:
        """Close the underlying HTTP transport."""
        self._public.close()

    def __enter__(self) -> SecureClient:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.close()

    # ── shared CLOB reads (delegated to the internal PublicClient) ───────────
    # These are the reads SecureClient shares with PublicClient. Every one is a
    # straight delegation to the composed PublicClient, so behaviour is identical
    # by construction — same validation guards, same _common logic, same models.

    def get_order_book(self, *, token_id: str) -> OrderBook:
        """Get the order book for a token. Shares ``PublicClient.get_order_book``."""
        return self._public.get_order_book(token_id=token_id)

    def get_order_books(self, *, token_ids: Sequence[str]) -> tuple[OrderBook, ...]:
        """Get order books for multiple tokens. Shares ``PublicClient.get_order_books``."""
        return self._public.get_order_books(token_ids=token_ids)

    def get_midpoint(self, *, token_id: str) -> Decimal:
        """Get the midpoint price for a token. Shares ``PublicClient.get_midpoint``."""
        return self._public.get_midpoint(token_id=token_id)

    def get_midpoints(self, *, token_ids: Sequence[str]) -> dict[str, Decimal]:
        """Get midpoint prices for multiple tokens. Shares ``PublicClient.get_midpoints``."""
        return self._public.get_midpoints(token_ids=token_ids)

    def get_price(self, *, token_id: str, side: OrderSide) -> Decimal:
        """Get the executable price for a token side. Shares ``PublicClient.get_price``."""
        return self._public.get_price(token_id=token_id, side=side)

    def get_prices(
        self, *, requests: Sequence[PriceRequest]
    ) -> dict[str, dict[OrderSide, Decimal]]:
        """Get prices for multiple token-side requests. Shares ``PublicClient.get_prices``."""
        return self._public.get_prices(requests=requests)

    def get_spread(self, *, token_id: str) -> Decimal:
        """Get the bid-ask spread for a token. Shares ``PublicClient.get_spread``."""
        return self._public.get_spread(token_id=token_id)

    def get_spreads(self, *, token_ids: Sequence[str]) -> dict[str, Decimal]:
        """Get bid-ask spreads for multiple tokens. Shares ``PublicClient.get_spreads``."""
        return self._public.get_spreads(token_ids=token_ids)

    def get_last_trade_price(self, *, token_id: str) -> LastTradePrice:
        """Get the most recent trade price for a token. Shares the public singular."""
        return self._public.get_last_trade_price(token_id=token_id)

    def get_last_trade_prices(
        self, *, token_ids: Sequence[str]
    ) -> tuple[LastTradePriceForToken, ...]:
        """Get the most recent trade prices for multiple tokens. Shares the public plural."""
        return self._public.get_last_trade_prices(token_ids=token_ids)

    def get_price_history(
        self,
        *,
        token_id: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = None,
        interval: PriceHistoryInterval | None = None,
    ) -> tuple[PriceHistoryPoint, ...]:
        """Get historical price points for a token. Shares ``PublicClient.get_price_history``."""
        return self._public.get_price_history(
            token_id=token_id,
            start_ts=start_ts,
            end_ts=end_ts,
            fidelity=fidelity,
            interval=interval,
        )

    def estimate_market_price(
        self,
        *,
        token_id: str,
        side: OrderSide,
        amount: Decimal | int | float | str | None = None,
        shares: Decimal | int | float | str | None = None,
        order_type: str = "FOK",
    ) -> Decimal:
        """Estimate the marginal market-order price.

        Shares ``PublicClient.estimate_market_price``.
        """
        return self._public.estimate_market_price(
            token_id=token_id,
            side=side,
            amount=amount,
            shares=shares,
            order_type=order_type,
        )

    def get_market(
        self,
        *,
        id: str | None = None,
        slug: str | None = None,
        url: str | None = None,
        include_tag: bool | None = None,
        locale: str | None = None,
    ) -> Market:
        """Get a market. Shares ``PublicClient.get_market``."""
        return self._public.get_market(
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
    ) -> Paginator[Market]:
        """List markets. Shares ``PublicClient.list_markets`` (full gamma keyword set)."""
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

    def fetch_api_keys(self) -> tuple[str, ...]:
        """Fetch API key identifiers for the authenticated account.

        Mirrors py-sdk's ``SecureClient.fetch_api_keys`` (returns a
        ``tuple[str, ...]`` of key ids). Reads PolySimulator's ``GET /v1/keys``
        and projects each key record's identifier into py-sdk's bare-id tuple.
        """
        keys = self._client.list_keys()
        return _account.api_key_ids(keys)

    def delete_api_key(self) -> None:
        """Delete the API key currently used by this client.

        Mirrors py-sdk's ``SecureClient.delete_api_key`` (returns ``None``). On
        paper, keys are managed via the dashboard / ``/v1/keys/{id}``; py-sdk
        deletes the *current* derived key, which has no analog here, so this is a
        no-op that returns ``None`` (py-sdk's return type) rather than raising —
        matching the v1 mirror's documented stub-noop semantics.
        """
        return None

    def is_gasless_ready(self) -> bool:
        """Return ``True``. Mirrors py-sdk's deprecated ``is_gasless_ready``.

        py-sdk's secure-client creation performs the wallet setup, so this always
        returns ``True`` there. On paper there is no gasless wallet to ready, so
        it is unconditionally ``True`` — the porting author's
        ``if not client.is_gasless_ready(): ...`` branch is correctly never taken.
        """
        return True

    # ── account / liveness reads ────────────────────────────────────────────

    def get_balance_allowance(
        self, *, asset_type: AssetType, token_id: str | None = None
    ) -> BalanceAllowance:
        """Get balance and allowance information for an asset.

        Mirrors py-sdk's ``SecureClient.get_balance_allowance``: returns a
        :class:`~polysim_polymarket.models.BalanceAllowance` carrying integer
        base-unit ``balance`` + per-spender ``allowances``. ``asset_type`` is
        case-SENSITIVE (``"COLLATERAL"`` / ``"CONDITIONAL"`` only — py-sdk's
        contract); anything else raises ``UserInputError`` before any read.

        Maps to PolySimulator paper cash: reads ``GET /v1/account/balance`` and
        adapts the (USD, float) paper-cash figure onto py-sdk's base-unit
        :class:`BalanceAllowance` shape (USDC has 6 decimals, so 1 USD = 1_000_000
        base units). Paper trading has no on-chain allowance, so ``allowances`` is
        empty — matching the v1 mirror's "no allowance on paper" semantics.
        """
        _account.validate_asset_type(asset_type)
        payload = self._client.balance()
        return _account.adapt_balance_allowance(payload)

    def get_closed_only_mode(self) -> bool:
        """Return whether the authenticated account is in closed-only mode.

        Mirrors py-sdk's ``SecureClient.get_closed_only_mode`` (returns ``bool``).
        PolySimulator paper accounts are never restricted to closing-only orders,
        so this is unconditionally ``False`` — the v1 mirror's documented stub.
        """
        return False

    def get_notifications(self) -> tuple[Notification, ...]:
        """Get notifications for the authenticated account.

        Mirrors py-sdk's ``SecureClient.get_notifications`` (returns
        ``tuple[Notification, ...]``). The paper CLOB has no notifications feed,
        so this is an empty tuple — matching the v1 mirror's stub. The element
        type is the real :class:`Notification` model so a ported bot's iteration
        type-checks unchanged.
        """
        return ()

    # ── authenticated order reads ───────────────────────────────────────────

    def get_order(self, *, order_id: str) -> OpenOrder:
        """Get one open order for the authenticated account.

        Mirrors py-sdk's ``SecureClient.get_order`` (returns :class:`OpenOrder`).
        Reads PolySimulator's ``GET /v1/orders/{order_id}`` via the v1 mirror's
        proven order delegation and adapts the row onto py-sdk's ``OpenOrder``
        shape. An empty ``order_id`` raises ``UserInputError`` (py-sdk's guard)
        before any read.
        """
        validated = _account.require_nonempty("order_id", order_id)
        raw = self._client.get_order(validated)
        return _account.adapt_open_order(raw)

    def list_open_orders(
        self,
        *,
        token_id: str | None = None,
        id: str | None = None,
        market: str | None = None,
    ) -> Paginator[OpenOrder]:
        """List open orders for the authenticated account.

        Mirrors py-sdk's ``SecureClient.list_open_orders`` (returns
        ``Paginator[OpenOrder]``). Drives PolySimulator's Polymarket-shape data
        API (``GET /v1/data/orders``) — the same cursor-paginated endpoint the v1
        mirror's ``get_orders`` walks — and adapts each row onto ``OpenOrder``.
        Filters (``token_id`` -> ``asset_id`` / ``id`` / ``market``) forward
        server-side, matching py-sdk's filter names + the v1 mirror's forwarding.
        """

        def fetch(cursor: str | None) -> Page[OpenOrder]:
            envelope = self._client.data_orders(
                id=id,
                market=market,
                asset_id=token_id,
                next_cursor=cursor or _account.START_CURSOR,
                limit=_account.PAGE_LIMIT,
            )
            return _account.adapt_open_orders_page(envelope)

        return Paginator(fetch=fetch)

    def list_account_trades(
        self,
        *,
        token_id: str | None = None,
        id: str | None = None,
        market: str | None = None,
        maker_address: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> Paginator[ClobTrade]:
        """List trades for the authenticated account.

        Mirrors py-sdk's ``SecureClient.list_account_trades`` (returns
        ``Paginator[ClobTrade]``). Drives PolySimulator's Polymarket-shape data
        API (``GET /v1/data/trades``) — the same cursor-paginated endpoint the v1
        mirror's ``get_trades`` walks — and adapts each row onto ``ClobTrade``.
        Filters (``token_id`` -> ``asset_id`` / ``market`` / ``after`` /
        ``before``) forward server-side. ``id`` / ``maker_address`` are accepted
        for py-sdk signature parity; PolySimulator's data-trades endpoint has no
        analog for them, so they are ignored (documented seam).
        """

        def fetch(cursor: str | None) -> Page[ClobTrade]:
            envelope = self._client.data_trades(
                market=market,
                asset_id=token_id,
                before=before,
                after=after,
                next_cursor=cursor or _account.START_CURSOR,
                limit=_account.PAGE_LIMIT,
            )
            return _account.adapt_account_trades_page(envelope)

        return Paginator(fetch=fetch)

    # ── trading: token-id resolution ────────────────────────────────────────

    def _resolve_coordinates(self, token_id: str) -> tuple[str, str]:
        """Resolve a token id to PolySim ``(market_id, outcome)`` for an order.

        Mirrors the v1 mirror's ``_resolve_token``: the ``condition_id:NO`` /
        ``:YES`` colon form and short/non-numeric ids resolve **locally** (no
        network); a long all-digit real-Polymarket outcome-token id is
        reverse-resolved via ``GET /v1/markets-by-token/{id}`` (cached per token)
        so an order placed with a genuine Polymarket token id lands on the right
        market + outcome. All the routing logic is the transport-free
        :mod:`~polysim_polymarket.clients._trade` seam; only the network fetch
        lives here.
        """
        if not _trade.needs_token_reverse_resolution(token_id):
            return _trade.split_token_local(token_id)
        cached = self._token_coordinates.get(token_id)
        if cached is not None:
            return cached
        market = self._client.get_market_by_token(token_id)
        resolved = _trade.coordinates_from_market_payload(token_id, market)
        self._token_coordinates[token_id] = resolved
        return resolved

    # ── trading: build (create) ─────────────────────────────────────────────

    def create_limit_order(
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

        Mirrors py-sdk's ``SecureClient.create_limit_order``: returns a
        :class:`~polysim_polymarket.models.SignedOrder`. Pass it to
        :meth:`post_order` to submit, or use :meth:`place_limit_order` to build +
        post in one call. ``builder_code`` is accepted for parity and inert on
        paper (no builder fees). Invalid args raise ``UserInputError`` before any
        work; signing is **accepted-and-inert** (no key, placeholder signature).
        """
        market_id, outcome = self._resolve_coordinates(token_id)
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

    def create_market_order(
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

        Mirrors py-sdk's ``SecureClient.create_market_order`` (flat keyword args,
        NOT a dataclass): a **BUY** uses ``amount`` (USD notional) and may cap with
        ``max_price``; a **SELL** uses ``shares`` (share count) and may floor with
        ``min_price``. A worst-acceptable price is ALWAYS forwarded to the backend
        — the given ``max_price`` / ``min_price`` or the 0.99 BUY / 0.01 SELL
        default — so the FOK/FAK is never uncapped. ``max_spend`` is a hard spend
        ceiling on a BUY: the submitted ``amount`` is clamped to
        ``min(amount, max_spend)`` (py-sdk's fee-adjusted spend cap, fee-free on
        paper). ``builder_code`` is accepted for parity and inert on paper.
        Invalid args raise ``UserInputError``; signing is accepted-and-inert.
        """
        market_id, outcome = self._resolve_coordinates(token_id)
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

    def place_limit_order(
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

        Mirrors py-sdk's ``SecureClient.place_limit_order`` (returns the posted
        :class:`~polysim_polymarket.models.OrderResponse`). The build-then-submit
        convenience: equivalent to ``post_order(create_limit_order(...))``.
        """
        signed = self.create_limit_order(
            token_id=token_id,
            price=price,
            size=size,
            side=side,
            post_only=post_only,
            expiration=expiration,
            builder_code=builder_code,
        )
        return self.post_order(signed)

    def place_market_order(
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

        Mirrors py-sdk's ``SecureClient.place_market_order`` (returns the posted
        :class:`~polysim_polymarket.models.OrderResponse`). The build-then-submit
        convenience: equivalent to ``post_order(create_market_order(...))`` and
        carries the same worst-price-cap guarantee.
        """
        signed = self.create_market_order(
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
        return self.post_order(signed)

    # ── trading: post ───────────────────────────────────────────────────────

    def post_order(self, signed_order: SignedOrder) -> OrderResponse:
        """Post a built order (``POST /v1/orders``).

        Mirrors py-sdk's ``SecureClient.post_order``: submits the
        :class:`~polysim_polymarket.models.SignedOrder` built by
        :meth:`create_limit_order` / :meth:`create_market_order` and returns the
        py-sdk-shaped :class:`~polysim_polymarket.models.OrderResponse`. On paper
        the unsigned body the build path computed (``paper_body``) is what goes on
        the wire — the placeholder signature is never sent.
        """
        raw = self._client.place_order(**_trade.paper_order_kwargs(signed_order.paper_body))
        return _trade.adapt_order_response(raw)

    def post_orders(self, signed_orders: Sequence[SignedOrder]) -> tuple[OrderResponse, ...]:
        """Post a batch of built orders (``POST /v1/orders/batch``).

        Mirrors py-sdk's ``SecureClient.post_orders`` (returns
        ``tuple[OrderResponse, ...]``, one per input order, in order). Uses the
        tier-aware native batch endpoint; each order's unsigned ``paper_body`` is
        projected onto the wire via the SAME
        :func:`~polysim_polymarket.clients._trade.paper_order_kwargs` glue
        :meth:`post_order` uses, so the single + batch post paths never drift.
        """
        bodies = [_trade.paper_order_kwargs(order.paper_body) for order in signed_orders]
        rows = self._client.place_orders(bodies)
        return tuple(_trade.adapt_order_response(row) for row in rows)

    # ── trading: cancel ─────────────────────────────────────────────────────

    def cancel_order(self, *, order_id: str) -> CancelOrdersResponse:
        """Cancel one open order (``DELETE /v1/orders/{order_id}``).

        Mirrors py-sdk's ``SecureClient.cancel_order`` (returns
        :class:`~polysim_polymarket.models.CancelOrdersResponse`). An empty
        ``order_id`` raises ``UserInputError`` before any request.
        """
        validated = _account.require_nonempty("order_id", order_id)
        try:
            self._client.cancel_order(validated)
        except PolyApiException as exc:
            return _trade.build_cancel_orders_response([], {validated: str(exc)})
        return _trade.build_cancel_orders_response([validated], {})

    def cancel_orders(self, *, order_ids: Sequence[str]) -> CancelOrdersResponse:
        """Cancel several open orders (loops ``DELETE /v1/orders/{id}``).

        Mirrors py-sdk's ``SecureClient.cancel_orders``. PolySimulator has no
        plural-cancel route, so this loops the single-cancel endpoint and tallies
        successes/failures onto py-sdk's ``CancelOrdersResponse`` (``canceled``
        ids + per-id ``not_canceled`` reasons) — the same approach the v1 mirror
        uses.

        ALL ``order_ids`` are validated UP FRONT (py-sdk's all-or-nothing
        ``build_cancel_orders_request``): a bare ``str``/``bytes`` or an empty
        sequence is rejected, and every id must be a non-empty string — so an
        invalid id raises ``UserInputError`` BEFORE any network cancel fires,
        never leaving a partial cancel behind.
        """
        validated_ids = _trade.validate_cancel_order_ids(order_ids)
        canceled: list[str] = []
        not_canceled: dict[str, str] = {}
        for oid in validated_ids:
            try:
                self._client.cancel_order(oid)
                canceled.append(oid)
            except PolyApiException as exc:
                not_canceled[oid] = str(exc)
        return _trade.build_cancel_orders_response(canceled, not_canceled)

    def cancel_all(self) -> CancelOrdersResponse:
        """Cancel EVERY open order on the account (``POST /v1/cancel-all``).

        Mirrors py-sdk's ``SecureClient.cancel_all`` (returns
        :class:`~polysim_polymarket.models.CancelOrdersResponse`). Account-wide and
        irreversible. The underlying transport sends the backend's mandatory
        confirmation form (``X-Confirm-Cancel-All: true`` header) — the backend
        400s any cancel-all that omits it (the P1-J footgun guard).
        """
        raw = self._client.cancel_all()
        return _trade.adapt_cancel_response(raw)

    def cancel_market_orders(
        self, *, market: str | None = None, token_id: str | None = None
    ) -> CancelOrdersResponse:
        """Cancel all open orders matching a market/token filter
        (``DELETE /v1/cancel-market-orders``).

        Mirrors py-sdk's ``SecureClient.cancel_market_orders``. Either ``market``
        (a condition/market id) or ``token_id`` scopes the cancel; at least one is
        required, else ``UserInputError`` with py-sdk's exact message
        (``"At least one of market or token_id is required."``). Unlike py-sdk —
        whose backend accepts an ``asset_id`` filter directly — PolySimulator's
        ``DELETE /v1/cancel-market-orders`` route takes only ``market``, so a
        ``token_id`` is first resolved to its market id (via the same
        token→coordinates reverse-resolution the order paths use) and the cancel
        is scoped by that market. Returns the py-sdk-shaped
        :class:`~polysim_polymarket.models.CancelOrdersResponse`.
        """
        if market:
            market_id = market
        elif token_id:
            market_id, _ = self._resolve_coordinates(token_id)
        else:
            raise UserInputError("At least one of market or token_id is required.")
        raw = self._client.cancel_market_orders(market_id)
        return _trade.adapt_cancel_response(raw)

    # ── on-chain workflows (PAPER no-ops) ────────────────────────────────────
    # py-sdk's on-chain methods build + broadcast/relay a real EVM transaction
    # and return a handle whose wait() blocks for a terminal on-chain outcome.
    # PolySimulator is PAPER trading: there is no chain, no signer, no web3. So
    # each method here replicates py-sdk's pre-chain INPUT guards (so a bot hits
    # the SAME UserInputError in paper) then returns a PAPER handle whose wait()
    # resolves INSTANTLY with a valid-format placeholder TransactionOutcome — it
    # does NOT settle on-chain and does NOT mutate any paper state (no ledger write,
    # no balance change). The DRY paper core + guards live in
    # :mod:`polysim_polymarket.clients._onchain` so the G5 async client reuses one
    # copy. Address checks are a 40-hex FORMAT check, optionally 0x/0X-prefixed (NOT
    # an EIP-55 checksum — we keep eth-utils/web3 out of the paper SDK).

    def approve_erc20(
        self,
        *,
        token_address: str,
        spender_address: str,
        amount: int | Literal["max"],
        metadata: str | None = None,
    ) -> SyncTransactionHandle:
        """Submit an ERC-20 approval transaction — PAPER no-op.

        Mirrors py-sdk's ``SecureClient.approve_erc20``. Paper mode accepts the call
        (validating inputs) but does NOT settle on-chain or mutate any paper state:
        it validates the addresses (40-hex format check, optionally 0x/0X-prefixed,
        not an EIP-55 checksum) and returns a paper handle whose ``wait()`` returns
        instantly with a placeholder
        :class:`~polysim_polymarket.models.TransactionOutcome`. ``amount`` accepts
        ``"max"`` for parity; ``metadata`` is accepted and inert.
        """
        _onchain.validate_address("token_address", token_address)
        _onchain.validate_address("spender_address", spender_address)
        return _onchain.paper_sync_handle()

    def approve_erc1155_for_all(
        self,
        *,
        token_address: str,
        operator_address: str,
        approved: bool = True,
        metadata: str | None = None,
    ) -> SyncTransactionHandle:
        """Approve/revoke an ERC-1155 operator for all tokens — PAPER no-op.

        Mirrors py-sdk's ``SecureClient.approve_erc1155_for_all``. Paper mode
        accepts the call (validating inputs) but does NOT settle on-chain or mutate
        any paper state: it validates the addresses (40-hex format check, optionally
        0x/0X-prefixed, not an EIP-55 checksum) and returns a paper handle whose
        ``wait()`` returns instantly with a placeholder
        :class:`~polysim_polymarket.models.TransactionOutcome`. ``approved`` /
        ``metadata`` are accepted and inert.
        """
        _onchain.validate_address("token_address", token_address)
        _onchain.validate_address("operator_address", operator_address)
        return _onchain.paper_sync_handle()

    def transfer_erc20(
        self,
        *,
        token_address: str,
        recipient_address: str,
        amount: int,
        metadata: str | None = None,
    ) -> SyncTransactionHandle:
        """Submit an ERC-20 transfer transaction — PAPER no-op.

        Mirrors py-sdk's ``SecureClient.transfer_erc20``. Paper mode accepts the
        call (validating inputs) but does NOT settle on-chain or mutate any paper
        state: it validates the addresses (40-hex format check, optionally
        0x/0X-prefixed, not an EIP-55 checksum) and returns a paper handle whose
        ``wait()`` returns instantly with a placeholder
        :class:`~polysim_polymarket.models.TransactionOutcome`. ``metadata`` is
        accepted and inert.
        """
        _onchain.validate_address("token_address", token_address)
        _onchain.validate_address("recipient_address", recipient_address)
        return _onchain.paper_sync_handle()

    def split_position(
        self,
        *,
        condition_id: str | None = None,
        legs: Sequence[str] | None = None,
        amount: int,
        metadata: str | None = None,
    ) -> SyncTransactionHandle:
        """Split collateral into market or combo positions — PAPER no-op.

        Mirrors py-sdk's ``SecureClient.split_position``. Provide EXACTLY one of
        ``condition_id`` (market positions) or ``legs`` (combo positions), else
        ``UserInputError`` (py-sdk's ``"Provide exactly one of condition_id or
        legs"``); the combo branch also requires a positive ``amount``
        (``"Split amount must be positive for combo positions"``). Paper mode
        accepts the call (validating inputs) but does NOT settle on-chain or mutate
        any paper state: it returns a paper handle whose ``wait()`` returns
        instantly with a placeholder
        :class:`~polysim_polymarket.models.TransactionOutcome`.
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
        return _onchain.paper_sync_handle()

    def merge_positions(
        self,
        *,
        condition_id: str | None = None,
        legs: Sequence[str] | None = None,
        amount: int | Literal["max"],
        metadata: str | None = None,
    ) -> SyncTransactionHandle:
        """Merge market or combo positions back into collateral — PAPER no-op.

        Mirrors py-sdk's ``SecureClient.merge_positions``. Provide EXACTLY one of
        ``condition_id`` (market positions) or ``legs`` (combo positions), else
        ``UserInputError`` (py-sdk's ``"Provide exactly one of condition_id or
        legs"``). ``amount`` accepts ``"max"`` for parity. Paper mode accepts the
        call (validating inputs) but does NOT settle on-chain or mutate any paper
        state: it returns a paper handle whose ``wait()`` returns instantly with a
        placeholder :class:`~polysim_polymarket.models.TransactionOutcome`. Unlike
        real py-sdk — which resolves ``amount="max"`` from the on-chain ERC-1155
        balances — paper mode does NOT balance-check the position (the paper
        position ledger isn't wired into on-chain methods in this gate), so this
        always succeeds on paper.
        """
        _onchain.require_exactly_one(
            "Provide exactly one of condition_id or legs",
            condition_id=condition_id,
            legs=legs,
        )
        return _onchain.paper_sync_handle()

    def redeem_positions(
        self,
        *,
        condition_id: str | None = None,
        market_id: str | None = None,
        position_id: str | None = None,
        metadata: str | None = None,
    ) -> SyncTransactionHandle:
        """Redeem resolved market or combo positions — PAPER no-op.

        Mirrors py-sdk's ``SecureClient.redeem_positions``. Provide EXACTLY one of
        ``condition_id`` / ``market_id`` / ``position_id``, else ``UserInputError``
        (py-sdk's ``"Provide exactly one of condition_id, market_id, or
        position_id"``). Paper mode accepts the call (validating inputs) but does
        NOT settle on-chain or mutate any paper state: it returns a paper handle
        whose ``wait()`` returns instantly with a placeholder
        :class:`~polysim_polymarket.models.TransactionOutcome`. Unlike real py-sdk —
        which consults the on-chain ERC-1155 balances and raises
        ``UserInputError("Combo position has no balance to redeem")`` when empty —
        paper mode does NOT balance-check the position (the paper position ledger
        isn't wired into on-chain methods in this gate), so this always succeeds on
        paper.
        """
        _onchain.require_exactly_one(
            "Provide exactly one of condition_id, market_id, or position_id",
            condition_id=condition_id,
            market_id=market_id,
            position_id=position_id,
        )
        return _onchain.paper_sync_handle()

    def setup_trading_approvals(self) -> _onchain.PaperSyncDeprecatedTransactionHandle:
        """Approve the standard trading allowances — PAPER no-op.

        Mirrors py-sdk's deprecated ``SecureClient.setup_trading_approvals``
        (returns a ``SyncDeprecatedTransactionHandle`` whose ``wait()`` returns
        ``None``). On real Polymarket this submits any missing trading approvals
        and waits internally; on paper there is nothing to approve, so it returns
        the deprecated paper handle directly and ``wait()`` is a ``None``-returning
        no-op.
        """
        return _onchain.paper_sync_deprecated_handle()

    def setup_gasless_wallet(self) -> SecureClient:
        """Return this client — PAPER no-op.

        Mirrors py-sdk's deprecated ``SecureClient.setup_gasless_wallet`` (returns
        ``Self``). On real Polymarket secure-client creation now performs the
        wallet setup, so this is a deprecated identity method; on paper there is no
        gasless wallet to set up, so it likewise just returns ``self``.
        """
        return self

    # ── rewards + scoring (honest empty / scoring:false stubs) ───────────────
    # The PolySimulator paper rewards ENGINE is a separate backend roadmap item,
    # so every rewards read returns an HONEST empty value — no fabricated nonzero
    # data: scoring is False, the list reads are empty paginators of the correct
    # element type, the totals are an empty tuple, and the percentages are an
    # empty dict. A bot's reward-accounting loop runs but finds nothing, which is
    # truthful for paper (rewards aren't earned on paper). These short-circuit to
    # the empty value WITHOUT a network call.

    def get_order_scoring(self, *, order_id: str) -> bool:
        """Return whether an order is currently scoring rewards — PAPER stub.

        Mirrors py-sdk's ``SecureClient.get_order_scoring`` (returns ``bool``). The
        paper rewards engine is a separate backend roadmap item, so an order never
        scores rewards on paper: this is unconditionally ``False``.
        """
        return False

    def get_orders_scoring(self, *, order_ids: Sequence[str]) -> dict[str, bool]:
        """Return reward-scoring status for multiple orders — PAPER stub.

        Mirrors py-sdk's ``SecureClient.get_orders_scoring`` (returns
        ``dict[str, bool]``). No order scores rewards on paper, so every id maps to
        ``False``, keyed by exactly the given ``order_ids``.
        """
        return {order_id: False for order_id in order_ids}

    def list_current_rewards(self, *, sponsored: bool | None = None) -> Paginator[CurrentReward]:
        """List current rewards — PAPER stub (empty paginator).

        Mirrors py-sdk's ``SecureClient.list_current_rewards`` (returns
        ``Paginator[CurrentReward]``). The paper rewards engine is a separate
        backend roadmap item, so this is an empty :class:`CurrentReward` paginator.
        ``sponsored`` is accepted for parity and inert.
        """
        return self._empty_paginator()

    def list_market_rewards(
        self, *, condition_id: str, sponsored: bool | None = None
    ) -> Paginator[MarketReward]:
        """List rewards for a market condition — PAPER stub (empty paginator).

        Mirrors py-sdk's ``SecureClient.list_market_rewards`` (returns
        ``Paginator[MarketReward]``). Empty on paper (separate backend roadmap).
        ``condition_id`` / ``sponsored`` are accepted for parity and inert.
        """
        return self._empty_paginator()

    def list_user_earnings_for_day(self, *, date: str) -> Paginator[UserEarning]:
        """List reward earnings for the user on a date — PAPER stub (empty paginator).

        Mirrors py-sdk's ``SecureClient.list_user_earnings_for_day`` (returns
        ``Paginator[UserEarning]``). Empty on paper (separate backend roadmap).
        ``date`` is accepted for parity and inert.
        """
        return self._empty_paginator()

    def get_total_earnings_for_user_for_day(self, *, date: str) -> tuple[TotalUserEarning, ...]:
        """Total reward earnings for the user on a date — PAPER stub (empty tuple).

        Mirrors py-sdk's ``SecureClient.get_total_earnings_for_user_for_day``
        (returns ``tuple[TotalUserEarning, ...]``). Empty on paper (separate backend
        roadmap). ``date`` is accepted for parity and inert.
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
    ) -> Paginator[UserRewardsEarning]:
        """List reward earnings + market config — PAPER stub (empty paginator).

        Mirrors py-sdk's ``SecureClient.list_user_earnings_and_markets_config``
        (returns ``Paginator[UserRewardsEarning]``). Empty on paper (separate
        backend roadmap). Every filter kwarg is accepted for parity and inert.
        """
        return self._empty_paginator()

    def get_reward_percentages(self) -> RewardsPercentages:
        """Current reward-percentage allocations — PAPER stub (empty dict).

        Mirrors py-sdk's ``SecureClient.get_reward_percentages`` (returns
        ``RewardsPercentages``, py-sdk's ``dict[CtfConditionId, float]`` alias). The
        paper rewards engine is a separate backend roadmap item, so this is an
        honest empty ``{}`` — no fabricated allocations.
        """
        return {}

    @staticmethod
    def _empty_paginator() -> Paginator[Any]:
        """A typed, fetch-free empty paginator for the rewards list stubs.

        Reuses the package's :class:`~polysim_polymarket.pagination._EmptyPaginator`
        (one shared empty-paginator implementation across the mirror) so every
        rewards list read yields one empty page and stops, with no network fetch.
        """
        return _EmptyPaginator()

    # ── builder attribution (NOT simulated — raises NotImplementedError) ──────
    # Builder attribution (the fee-sharing program where a "builder" earns a cut
    # of the trades routed through its code) has no analog on paper: there is no
    # builder fee taken, no builder revenue ledger, no builder leaderboard. So
    # every builder method mirrors py-sdk's EXACT signature but raises
    # NotImplementedError with the shared _onchain.BUILDER_NOT_SIMULATED message —
    # the honest "not simulated in paper" signal, far better than returning a
    # fabricated zero/empty that a bot might mistake for real builder data. The
    # builder TYPES still re-export at the package root so a bot's type hints
    # resolve. (The builder_code= kwargs on create/place orders stay inert per G3
    # — unchanged here.)

    def get_builder_volumes(
        self, *, time_period: BuilderVolumeTimePeriod | None = None
    ) -> tuple[BuilderVolumeEntry, ...]:
        """Get builder volume leaderboard entries — NOT simulated on paper.

        Mirrors py-sdk's ``SecureClient.get_builder_volumes`` signature, but raises
        :class:`NotImplementedError` (``BUILDER_NOT_SIMULATED``): builder
        attribution is not simulated in PolySimulator paper mode.
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
    ) -> Paginator[BuilderTrade]:
        """List builder-attributed trades — NOT simulated on paper.

        Mirrors py-sdk's ``SecureClient.list_builder_trades`` signature, but raises
        :class:`NotImplementedError` (``BUILDER_NOT_SIMULATED``): builder
        attribution is not simulated in PolySimulator paper mode.
        """
        raise NotImplementedError(_onchain.BUILDER_NOT_SIMULATED)

    def get_builder_fee_rates(self, builder_code: str) -> BuilderFeeRates:
        """Get fee rates for a builder code — NOT simulated on paper.

        Mirrors py-sdk's ``SecureClient.get_builder_fee_rates`` (a POSITIONAL
        ``builder_code``), but raises :class:`NotImplementedError`
        (``BUILDER_NOT_SIMULATED``): builder attribution is not simulated in
        PolySimulator paper mode.
        """
        raise NotImplementedError(_onchain.BUILDER_NOT_SIMULATED)

    def list_builder_leaderboard(
        self,
        *,
        time_period: LeaderboardTimePeriod | None = None,
        page_size: int = 20,
    ) -> Paginator[Any]:
        """List builder leaderboard entries — NOT simulated on paper.

        Mirrors py-sdk's ``SecureClient.list_builder_leaderboard`` signature: its
        ``time_period`` is annotated ``LeaderboardTimePeriod`` (py-sdk's own
        separate alias for this read, distinct from the ``BuilderVolumeTimePeriod``
        used by ``get_builder_volumes`` even though both are the same ``Literal``),
        so the mirror uses ``LeaderboardTimePeriod`` here for annotation parity.
        py-sdk returns a ``Paginator[LeaderboardEntry]``; the element type is a
        leaderboard model that belongs to a later leaderboard gate, so the mirror
        types it ``Paginator[Any]`` rather than inventing a name py-sdk's root
        promotes for a different surface. The method itself raises
        :class:`NotImplementedError` (``BUILDER_NOT_SIMULATED``): builder
        attribution is not simulated in PolySimulator paper mode.
        """
        raise NotImplementedError(_onchain.BUILDER_NOT_SIMULATED)
