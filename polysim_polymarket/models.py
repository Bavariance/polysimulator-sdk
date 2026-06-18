"""Pydantic read models mirroring py-sdk's CLOB market-data subset.

Field names and per-field scalar types track ``polymarket.models`` so a bot that
reads ``book.bids[0].price`` / ``book.token_id`` / ``ltp.side`` against
PolySimulator gets the same attributes (and the same Decimal / datetime types)
when it swaps to real Polymarket.

Phase 1 covers the CLOB read surface the foundation needs:

* :class:`OrderBookLevel` / :class:`OrderBook` — mirror ``polymarket.models.clob.order_book``
* :class:`LastTradePrice` — mirror ``polymarket.models.clob.last_trade``
* :class:`PriceHistoryPoint` — mirror ``...clob.price_history`` (bare tuple, no wrapper)
* :class:`Market` — a focused subset of ``polymarket.models.gamma.market.Market``

py-sdk parses prices/sizes with a ``_DecimalFromString`` validator that accepts
only ``str`` / ``Decimal`` wire values (never bare float/int, to avoid binary
float rounding) and coerces them to :class:`~decimal.Decimal`. We replicate that
exactly so the scalar types match. Timestamps mirror py-sdk's epoch-ms parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, NamedTuple, TypeAlias

from pydantic import (
    AliasChoices,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
)
from pydantic import BaseModel as _PydanticBaseModel

OrderSide = Literal["BUY", "SELL"]

# Mirrors ``polymarket.types.TransactionHash`` — py-sdk declares it as a bare
# ``NewType("TransactionHash", str)`` with no runtime validation, so it is a
# plain ``str`` at runtime. Kept as a module alias so a ported bot's
# ``hash: TransactionHash`` annotation resolves across the prefix swap.
TransactionHash: TypeAlias = str

# Mirrors ``polymarket.models.clob.account.AssetType`` — the asset class a
# balance/allowance read is scoped to (``COLLATERAL`` = USDC cash,
# ``CONDITIONAL`` = an outcome-token position).
AssetType: TypeAlias = Literal["COLLATERAL", "CONDITIONAL"]

# Mirrors ``polymarket.models.clob.price_history.PriceHistoryInterval`` — the
# accepted ``interval`` values for ``get_price_history``.
PriceHistoryInterval: TypeAlias = Literal["max", "1w", "1d", "6h", "1h"]


def _require_decimal_string(value: object) -> object:
    """Mirror py-sdk: accept only ``str`` / ``Decimal`` (reject float/int).

    py-sdk refuses bare ``float``/``int`` here so binary-float rounding never
    enters a price; the wire always carries decimal strings.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if not isinstance(value, str):
        raise ValueError(f"expected decimal string, got {type(value).__name__}")
    return value


def _parse_epoch_ms_timestamp(value: object) -> object:
    """Mirror py-sdk's epoch-ms timestamp parsing (digit-string -> UTC datetime)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError(f"expected epoch-ms timestamp string, got {type(value).__name__}")
    if not value.isdecimal():
        raise ValueError(f"invalid epoch-ms timestamp: {value!r}")
    ms = int(value)
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError(f"invalid epoch-ms timestamp: {value!r}") from error


_DecimalFromString = Annotated[Decimal, BeforeValidator(_require_decimal_string)]
_EpochMsTimestamp = Annotated[datetime | None, BeforeValidator(_parse_epoch_ms_timestamp)]


class _BaseModel(_PydanticBaseModel):
    """Immutable base, mirroring py-sdk's ``polymarket.models.base.BaseModel``."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class OrderBookLevel(_BaseModel):
    """One price level. Mirrors ``polymarket.models.clob.OrderBookLevel``."""

    price: _DecimalFromString
    size: _DecimalFromString


class OrderBook(_BaseModel):
    """A CLOB order book. Mirrors ``polymarket.models.clob.OrderBook``.

    Level ordering mirrors py-sdk's contract: ``bids`` are in **ascending** price
    order (lowest bid first, so the best/highest bid is ``bids[-1]``) and
    ``asks`` are in **descending** price order (highest ask first, so the
    best/lowest ask is ``asks[-1]``).
    """

    market: str
    token_id: str = Field(validation_alias="asset_id")
    timestamp: _EpochMsTimestamp = None
    bids: tuple[OrderBookLevel, ...]
    """Ascending price order, lowest bid first (best bid = ``bids[-1]``)."""
    asks: tuple[OrderBookLevel, ...]
    """Descending price order, highest ask first (best ask = ``asks[-1]``)."""
    min_order_size: _DecimalFromString
    tick_size: _DecimalFromString
    neg_risk: bool
    last_trade_price: _DecimalFromString | None = None
    hash: str

    @field_validator("last_trade_price", mode="before")
    @classmethod
    def _parse_last_trade_price(cls, value: object) -> object:
        return None if value in (None, "") else value


class LastTradePrice(_BaseModel):
    """Last trade price for a token. Mirrors ``polymarket.models.clob.LastTradePrice``."""

    price: _DecimalFromString
    side: OrderSide


class LastTradePriceForToken(_BaseModel):
    """Last trade price tagged with its token id.

    Mirrors ``polymarket.models.clob.LastTradePriceForToken`` — the element type
    py-sdk's plural ``get_last_trade_prices`` returns (it adds ``token_id`` to
    the price+side pair so a multi-token result is self-describing).
    """

    token_id: str
    price: _DecimalFromString
    side: OrderSide


class PriceRequest(NamedTuple):
    """One ``(token_id, side)`` price request.

    Mirrors ``polymarket.models.clob.requests.PriceRequest`` (a ``NamedTuple``);
    py-sdk's ``get_prices`` takes a ``Sequence[PriceRequest]``. Positional order
    is ``(token_id, side)`` so ``PriceRequest("711", "BUY")`` works either way.
    """

    token_id: str
    side: OrderSide


class PriceHistoryPoint(_BaseModel):
    """One ``(t, p)`` history point. Mirrors ``polymarket.models.clob.PriceHistoryPoint``.

    ``get_price_history`` returns a **bare** ``tuple[PriceHistoryPoint, ...]``,
    exactly like py-sdk — there is no ``PriceHistory`` wrapper (py-sdk has none),
    so this point type is the whole price-history surface.
    """

    t: int = Field(strict=True)
    p: float = Field(strict=True)


class MarketState(_BaseModel):
    """Operational state for a market, mirroring ``polymarket.models.gamma.market.MarketState``.

    py-sdk nests ``active`` / ``closed`` / ``neg_risk`` (and more) under
    ``Market.state`` rather than at the top level, so a bot reads
    ``market.state.closed``. This is a **focused** subset — the full py-sdk
    ``MarketState`` is wider (``archived``, ``accepting_orders``,
    ``enable_order_book``, ``start_date`` / ``end_date`` / ``closed_time``) and
    those fields are deferred to a later phase. ``neg_risk`` carries py-sdk's
    ``negRisk`` validation alias.
    """

    active: bool | None = None
    closed: bool | None = None
    neg_risk: bool | None = Field(default=None, validation_alias="negRisk")


class Market(_BaseModel):
    """A market. Focused subset of ``polymarket.models.gamma.market.Market``.

    The full py-sdk ``Market`` is a deeply-nested gamma-API model. Phase 1 keeps
    the top-level identity fields plus a focused ``state`` sub-model a CLOB read
    bot actually reads, with the same field names + nesting, so the swap stays
    mechanical. Later phases can widen this toward py-sdk's full nested
    ``state`` / ``metrics`` / ``prices`` / ``trading`` structure.
    """

    id: str
    # py-sdk accepts either the camelCase ``conditionId`` or the short
    # ``condition`` wire key via AliasChoices — mirror both so a gamma-shaped
    # payload binds identically on the swap.
    condition_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("conditionId", "condition"),
    )
    question: str | None = None
    slug: str | None = None
    # active/closed/neg_risk live under ``state`` (a MarketState sub-model) in
    # py-sdk, NOT top-level. Defaults to an empty MarketState so ``state.closed``
    # is always readable even when the payload omits the state keys.
    state: MarketState = Field(default_factory=MarketState)


# ── authenticated account / auth models (mirror polymarket.models.clob.account
#    + polymarket.models.clob.api_key) ────────────────────────────────────────


class ApiKeyCreds(_BaseModel):
    """API credentials triple. Mirrors ``polymarket.models.clob.ApiKeyCreds``.

    On real Polymarket these are the HMAC L2 credentials the SDK derives from an
    on-chain signature (``key`` / ``secret`` / ``passphrase``). On PolySimulator
    paper trading there is a single ``ps_live_*`` API key, so a bot rarely needs
    to construct this — but a porting author who carries a ``credentials=`` kwarg
    through gets the same field names + types here as on real Polymarket.
    """

    key: str
    secret: str = ""
    passphrase: str = ""


class BalanceAllowance(_BaseModel):
    """Balance + allowance for an asset, in base units.

    Mirrors ``polymarket.models.clob.account.BalanceAllowance``: integer
    ``balance`` (base units — USDC has 6 decimals) and per-spender
    ``allowances``. On paper there is no on-chain allowance, so ``allowances`` is
    empty; a ported bot still reads ``ba.balance`` as the same base-unit int it
    gets on real Polymarket.
    """

    balance: int
    allowances: dict[str, int] = Field(default_factory=dict)


class Notification(_BaseModel):
    """Account notification. Mirrors ``polymarket.models.clob.account.Notification``.

    The paper CLOB serves no notifications, but the model exists so a ported
    bot's ``for n in client.get_notifications(): n.type`` type-checks unchanged.
    """

    id: int
    owner: str = ""
    type: int = 0
    payload: object = None
    timestamp: _EpochMsTimestamp = None


class MakerOrder(_BaseModel):
    """Maker-side fill info on a trade. Mirrors ``polymarket.models.clob.account.MakerOrder``."""

    order_id: str
    token_id: str = Field(validation_alias=AliasChoices("token_id", "asset_id"))
    maker_address: str = ""
    owner: str = ""
    side: OrderSide
    price: _DecimalFromString
    matched_amount: _DecimalFromString
    outcome: str = ""
    fee_rate_bps: _DecimalFromString | None = None


class OpenOrder(_BaseModel):
    """Open order owned by an account. Mirrors ``polymarket.models.clob.account.OpenOrder``.

    Field names + scalar types track py-sdk so a ported bot reads
    ``order.price`` / ``order.size_matched`` / ``order.status`` identically.
    Fields PolySimulator's order row omits carry safe defaults (empty
    string/zero) so a sparse paper row binds without a ported bot seeing a
    different *shape* — only emptier values where the paper API has no data.
    """

    id: str
    market: str = ""
    token_id: str = Field(default="", validation_alias=AliasChoices("token_id", "asset_id"))
    owner: str = ""
    maker_address: str = ""
    side: OrderSide = "BUY"
    price: _DecimalFromString = Decimal("0")
    original_size: _DecimalFromString = Decimal("0")
    size_matched: _DecimalFromString = Decimal("0")
    outcome: str = ""
    order_type: str = ""
    status: str = ""
    associate_trades: tuple[str, ...] = ()
    created_at: _EpochMsTimestamp = None
    expires_at: _EpochMsTimestamp = Field(
        default=None, validation_alias=AliasChoices("expires_at", "expiration")
    )

    @field_validator("price", "original_size", "size_matched", mode="before")
    @classmethod
    def _empty_decimal_to_zero(cls, value: object) -> object:
        return "0" if value in (None, "") else value


# ── trading models (mirror polymarket.models.clob.orders +
#    polymarket.models.clob.order_response + polymarket.models.clob.cancel) ─────

# Mirrors ``polymarket.models.clob.orders.OrderType`` — the time-in-force a CLOB
# order carries. py-sdk's create/place limit path picks GTC (no expiration) or
# GTD (with one); the market path is FAK/FOK.
OrderType: TypeAlias = Literal["GTC", "GTD", "FAK", "FOK"]

# Mirrors ``polymarket.models.clob.orders.MarketOrderType`` — the time-in-force
# subset valid for a market order (immediate-or-cancel family only).
MarketOrderType: TypeAlias = Literal["FAK", "FOK"]

# Mirrors ``polymarket.models.clob.order_response.OrderPostStatus`` — the live
# status a posted-and-accepted order can carry.
OrderPostStatus: TypeAlias = Literal["live", "matched", "delayed"]

# Mirrors ``polymarket.models.clob.orders.TickSize`` — the closed set of minimum
# price increments a CLOB market can use. py-sdk defines it exactly as below; a
# ported bot that type-hints a tick size against this alias keeps the same type
# across the prefix swap.
TickSize: TypeAlias = Literal["0.1", "0.01", "0.001", "0.0001"]

# Mirrors ``polymarket.models.clob.order_response.OrderResponseErrorCode`` — the
# closed set of reasons a posted order is rejected.
OrderResponseErrorCode: TypeAlias = Literal[
    "unmatched",
    "market_not_ready",
    "not_enough_balance",
    "invalid_nonce",
    "invalid_expiration",
    "post_only_would_cross",
    "fok_not_filled",
    "fak_not_filled",
    "unknown",
]


class SignedOrder(_BaseModel):
    """A built order ready to submit. Mirrors ``polymarket.models.clob.SignedOrder``.

    On real Polymarket this is the EIP-712-signed order object: ``signature`` /
    ``signer`` / ``salt`` / ``maker_amount`` / ``taker_amount`` carry the on-chain
    settlement data. PolySimulator is **paper trading** — there is no chain, no
    ``eth_account``/``web3``, no private key — so signing is **accepted-and-inert**:
    the build path sets the trading-semantic fields (``token_id`` / ``side`` /
    ``order_type`` plus the size/price the paper backend needs) and leaves the
    on-chain fields at empty/zero placeholders (``signature=""``, ``signer=""``,
    ``salt=0``, …). A ported bot reads ``order.token_id`` / ``order.side`` /
    ``order.order_type`` identically; the placeholder signing fields are simply
    never used on paper (and are populated by the real SDK after the swap).

    The field set mirrors py-sdk's ``SignedOrder`` dataclass so a bot that passes
    the object straight to :meth:`post_order` (or inspects it) type-checks
    unchanged across the swap. The mirror keeps the PolySim ``_paper_body`` (the
    unsigned dict the paper backend actually accepts) on the object too, under a
    private attr, so ``post_order`` can submit it without re-deriving it.
    """

    token_id: str
    side: OrderSide
    order_type: OrderType
    # Trading-semantic numeric fields the paper backend needs; py-sdk carries
    # integer base-unit maker/taker amounts derived from price*size. On paper we
    # carry the human price/size the backend body uses and leave the base-unit
    # amounts at 0 (the chain math has no analog here).
    maker_amount: int = 0
    taker_amount: int = 0
    expiration: int = 0
    post_only: bool = False
    # On-chain settlement fields — EMPTY/ZERO placeholders on paper (no signing).
    signature: str = ""
    signer: str = ""
    maker: str = ""
    salt: int = 0
    signature_type: int = 0
    timestamp: int = 0
    builder: str = ""
    metadata: str = ""

    # The unsigned PolySim order body the paper backend accepts (market_id /
    # outcome / price / quantity|amount / time_in_force …). Carried on the object
    # as a PRIVATE attr so ``post_order`` submits exactly what the build path
    # computed, WITHOUT it ever leaking into the public field set / parity diff.
    _paper_body: dict[str, object] = PrivateAttr(default_factory=dict)

    @property
    def paper_body(self) -> dict[str, object]:
        """The unsigned PolySim order body this order submits on paper (read-only).

        Not part of py-sdk's ``SignedOrder`` — a mirror-only accessor the paper
        ``post_order`` uses to recover the exact backend body the build path
        computed (so it never re-derives it). A copy, so callers can't mutate the
        order's stored body.
        """
        return dict(self._paper_body)


class AcceptedOrder(_BaseModel):
    """A posted order the book accepted. Mirrors ``polymarket.models.clob.AcceptedOrder``.

    ``ok`` is always ``True`` so a ported bot branches on ``resp.ok`` identically
    across the swap. Field names + scalar types track py-sdk.
    """

    ok: Literal[True] = True
    order_id: str
    status: OrderPostStatus
    making_amount: Decimal = Decimal("0")
    taking_amount: Decimal = Decimal("0")
    trade_ids: tuple[str, ...] = ()
    transactions_hashes: tuple[str, ...] = ()


class RejectedOrder(_BaseModel):
    """A posted order the book rejected. Mirrors ``polymarket.models.clob.RejectedOrder``.

    ``ok`` is always ``False`` so a ported bot's ``if not resp.ok:`` branch works
    unchanged. ``code`` is one of py-sdk's :data:`OrderResponseErrorCode` values.
    """

    ok: Literal[False] = False
    code: OrderResponseErrorCode
    message: str


# Mirrors ``polymarket.models.clob.OrderResponse`` — the accepted|rejected union
# ``post_order`` / ``place_*_order`` return. A ported bot narrows on ``resp.ok``.
OrderResponse: TypeAlias = AcceptedOrder | RejectedOrder


class CancelOrdersResponse(_BaseModel):
    """The result of a cancel. Mirrors ``polymarket.models.clob.CancelOrdersResponse``.

    ``canceled`` is the tuple of order ids that were cancelled; ``not_canceled``
    maps each id that could NOT be cancelled to the reason. Field names track
    py-sdk so a ported bot reads ``resp.canceled`` / ``resp.not_canceled``
    identically.
    """

    canceled: tuple[str, ...] = ()
    not_canceled: dict[str, str] = Field(default_factory=dict)


class ClobTrade(_BaseModel):
    """Executed trade for an account. Mirrors ``polymarket.models.clob.account.ClobTrade``.

    Field names + scalar types track py-sdk. PolySimulator-absent fields carry
    safe defaults so a paper trade row binds while a ported bot reads
    ``trade.price`` / ``trade.size`` / ``trade.side`` identically.
    """

    id: str
    market: str = ""
    token_id: str = Field(default="", validation_alias=AliasChoices("token_id", "asset_id"))
    owner: str = ""
    maker_address: str = ""
    taker_order_id: str = ""
    side: OrderSide = "BUY"
    trader_side: Literal["TAKER", "MAKER"] = "TAKER"
    price: _DecimalFromString = Decimal("0")
    size: _DecimalFromString = Decimal("0")
    outcome: str = ""
    status: str = ""
    fee_rate_bps: _DecimalFromString = Decimal("0")
    bucket_index: int = 0
    transaction_hash: str = ""
    maker_orders: tuple[MakerOrder, ...] = ()
    matched_at: _EpochMsTimestamp = Field(
        default=None, validation_alias=AliasChoices("matched_at", "match_time")
    )
    updated_at: _EpochMsTimestamp = Field(
        default=None, validation_alias=AliasChoices("updated_at", "last_update")
    )

    @field_validator("price", "size", "fee_rate_bps", mode="before")
    @classmethod
    def _empty_decimal_to_zero(cls, value: object) -> object:
        return "0" if value in (None, "") else value


# ── on-chain transaction models (mirror polymarket.models.clob.relayer +
#    polymarket.transactions) ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TransactionOutcome:
    """The terminal outcome of a submitted transaction.

    Mirrors ``polymarket.models.clob.relayer.TransactionOutcome`` — a
    ``@dataclass(frozen=True, slots=True)`` carrying the on-chain
    ``transaction_hash`` plus an optional relayer ``transaction_id`` (``None`` for
    a directly-broadcast EOA transaction). On real Polymarket this is what an
    on-chain method's ``handle.wait()`` resolves to once the transaction reaches a
    terminal state.

    **Paper seam.** PolySimulator is paper trading: there is no chain, so an
    on-chain method records the *intent* and returns a paper handle whose
    ``wait()`` yields this outcome **instantly** with a valid-format placeholder
    ``transaction_hash`` (0x + 64 hex — see
    :data:`polysim_polymarket.clients._onchain.PAPER_TRANSACTION_HASH`) and
    ``transaction_id=None``. The hash is a synthetic paper sentinel, NOT a real
    settled transaction.
    """

    transaction_hash: TransactionHash
    transaction_id: str | None


# ── rewards / scoring read models (mirror polymarket.models.clob.rewards) ─────
#
# These are the element types the rewards reads return. The PolySimulator paper
# rewards *engine* is a separate backend roadmap item, so every rewards read
# returns an honest empty container (an empty paginator / empty tuple / empty
# ``RewardsPercentages`` dict); these models exist so the container's element
# type resolves and a ported bot's iteration type-checks unchanged. Field names +
# scalar types track py-sdk.


class CurrentRewardConfig(_BaseModel):
    """One reward-config window. Mirrors ``...clob.rewards.CurrentRewardConfig``."""

    id: int | None = None
    asset_address: str
    start_date: _EpochMsTimestamp = None
    end_date: _EpochMsTimestamp = None
    rate_per_day: _DecimalFromString
    total_rewards: _DecimalFromString | None = None


class CurrentReward(_BaseModel):
    """A market's current reward config. Mirrors ``...clob.rewards.CurrentReward``."""

    condition_id: str
    rewards_max_spread: float | None = None
    rewards_min_size: _DecimalFromString | None = None
    rewards_config: tuple[CurrentRewardConfig, ...] = ()
    sponsored_daily_rate: _DecimalFromString | None = None
    sponsors_count: int | None = None
    native_daily_rate: _DecimalFromString | None = None
    total_daily_rate: _DecimalFromString | None = None


class MarketRewardConfig(_BaseModel):
    """One market reward-config window. Mirrors ``...clob.rewards.MarketRewardConfig``."""

    asset_address: str
    start_date: _EpochMsTimestamp = None
    end_date: _EpochMsTimestamp = None
    rate_per_day: _DecimalFromString
    total_rewards: _DecimalFromString | None = None


class MarketRewardToken(_BaseModel):
    """A token within a market reward. Mirrors ``...clob.rewards.MarketRewardToken``."""

    token_id: str
    outcome: str
    price: _DecimalFromString


class MarketReward(_BaseModel):
    """Rewards for a market condition. Mirrors ``...clob.rewards.MarketReward``."""

    condition_id: str
    question: str
    market_slug: str | None = None
    event_slug: str | None = None
    image: str | None = None
    rewards_max_spread: float | None = None
    rewards_min_size: _DecimalFromString | None = None
    market_competitiveness: float | None = None
    tokens: tuple[MarketRewardToken, ...]
    rewards_config: tuple[MarketRewardConfig, ...] = ()


class UserEarning(_BaseModel):
    """One day's reward earning for a user. Mirrors ``...clob.rewards.UserEarning``."""

    asset_address: str
    asset_rate: _DecimalFromString
    condition_id: str
    date: _EpochMsTimestamp = None
    earnings: _DecimalFromString
    maker_address: str


class TotalUserEarning(_BaseModel):
    """A user's total earning for a day. Mirrors ``...clob.rewards.TotalUserEarning``."""

    asset_address: str
    asset_rate: _DecimalFromString
    date: _EpochMsTimestamp = None
    earnings: _DecimalFromString
    maker_address: str


class UserRewardsConfig(_BaseModel):
    """A user-rewards config window. Mirrors ``...clob.rewards.UserRewardsConfig``."""

    asset_address: str
    end_date: _EpochMsTimestamp = None
    rate_per_day: _DecimalFromString
    start_date: _EpochMsTimestamp = None
    total_rewards: _DecimalFromString


class EarningBreakdown(_BaseModel):
    """A per-asset earning breakdown. Mirrors ``...clob.rewards.EarningBreakdown``."""

    asset_address: str
    asset_rate: _DecimalFromString
    earnings: _DecimalFromString


class UserRewardsEarning(_BaseModel):
    """A user's reward earning + market config. Mirrors ``...clob.rewards.UserRewardsEarning``."""

    condition_id: str
    earning_percentage: float
    earnings: tuple[EarningBreakdown, ...]
    event_slug: str
    image: str
    maker_address: str
    market_competitiveness: float
    market_slug: str
    question: str
    rewards_config: tuple[UserRewardsConfig, ...]
    rewards_max_spread: float
    rewards_min_size: _DecimalFromString
    tokens: tuple[MarketRewardToken, ...]


# Mirrors ``polymarket.models.clob.rewards.RewardsPercentages`` — py-sdk declares
# it as a ``TypeAlias = dict[CtfConditionId, float]`` (NOT a model). An honest
# empty ``{}`` is the paper value (no fabricated allocations).
RewardsPercentages: TypeAlias = dict[str, float]


# ── builder-attribution read models (mirror polymarket.models.clob.builder +
#    polymarket.models.data.leaderboard) ────────────────────────────────────────
#
# Builder attribution is NOT simulated on paper — every builder method raises
# NotImplementedError — but the TYPES must still be importable so a ported bot's
# ``from polymarket import BuilderFeeRates`` survives the prefix swap. Field names
# + scalar types track py-sdk.

# Mirrors ``polymarket.models.data.leaderboard.BuilderVolumeTimePeriod`` — the
# accepted ``time_period`` values for the builder-volume reads.
BuilderVolumeTimePeriod: TypeAlias = Literal["DAY", "WEEK", "MONTH", "ALL"]

# Mirrors ``polymarket.models.data.leaderboard.LeaderboardTimePeriod`` — the
# accepted ``time_period`` values for the builder-leaderboard read. py-sdk keeps
# this as a SEPARATE alias from ``BuilderVolumeTimePeriod`` (even though both are
# the same ``Literal``) and annotates ``list_builder_leaderboard``'s
# ``time_period`` with it, so the mirror carries it for annotation-string parity.
LeaderboardTimePeriod: TypeAlias = Literal["DAY", "WEEK", "MONTH", "ALL"]


class BuilderFeeRates(_BaseModel):
    """A builder code's maker/taker fee rates. Mirrors ``...clob.builder.BuilderFeeRates``."""

    maker: Decimal
    taker: Decimal


class BuilderTrade(_BaseModel):
    """A builder-attributed trade. Mirrors ``...clob.builder.BuilderTrade``."""

    id: str
    trade_type: str = Field(validation_alias=AliasChoices("trade_type", "tradeType"))
    taker_order_hash: str = Field(
        validation_alias=AliasChoices("taker_order_hash", "takerOrderHash")
    )
    builder: str
    market: str
    token_id: str = Field(validation_alias=AliasChoices("token_id", "assetId"))
    side: OrderSide
    size: _DecimalFromString
    size_usdc: _DecimalFromString = Field(validation_alias=AliasChoices("size_usdc", "sizeUsdc"))
    price: _DecimalFromString
    status: str
    outcome: str
    outcome_index: int = Field(validation_alias=AliasChoices("outcome_index", "outcomeIndex"))
    owner: str
    maker: str
    transaction_hash: str = Field(
        validation_alias=AliasChoices("transaction_hash", "transactionHash")
    )
    matched_at: _EpochMsTimestamp = Field(
        default=None, validation_alias=AliasChoices("matched_at", "matchTime")
    )
    bucket_index: int = Field(validation_alias=AliasChoices("bucket_index", "bucketIndex"))
    fee: _DecimalFromString
    fee_usdc: _DecimalFromString = Field(validation_alias=AliasChoices("fee_usdc", "feeUsdc"))
    error_msg: str | None = Field(
        default=None, validation_alias=AliasChoices("error_msg", "err_msg")
    )
    created_at: _EpochMsTimestamp = Field(
        default=None, validation_alias=AliasChoices("created_at", "createdAt")
    )
    updated_at: _EpochMsTimestamp = Field(
        default=None, validation_alias=AliasChoices("updated_at", "updatedAt")
    )


class BuilderVolumeEntry(_BaseModel):
    """A builder-volume leaderboard row. Mirrors ``...data.leaderboard.BuilderVolumeEntry``."""

    bucket_at: _EpochMsTimestamp = Field(
        default=None, validation_alias=AliasChoices("bucket_at", "dt")
    )
    builder: str | None = None
    builder_logo: str | None = Field(
        default=None, validation_alias=AliasChoices("builder_logo", "builderLogo")
    )
    verified: bool | None = None
    volume: Decimal | None = None
    active_users: int | None = Field(
        default=None, validation_alias=AliasChoices("active_users", "activeUsers")
    )
    rank: str | None = None


__all__ = [
    "AcceptedOrder",
    "ApiKeyCreds",
    "AssetType",
    "BalanceAllowance",
    "BuilderFeeRates",
    "BuilderTrade",
    "BuilderVolumeEntry",
    "BuilderVolumeTimePeriod",
    "CancelOrdersResponse",
    "ClobTrade",
    "CurrentReward",
    "CurrentRewardConfig",
    "EarningBreakdown",
    "LastTradePrice",
    "LastTradePriceForToken",
    "LeaderboardTimePeriod",
    "MakerOrder",
    "Market",
    "MarketOrderType",
    "MarketReward",
    "MarketRewardConfig",
    "MarketRewardToken",
    "MarketState",
    "Notification",
    "OpenOrder",
    "OrderBook",
    "OrderBookLevel",
    "OrderPostStatus",
    "OrderResponse",
    "OrderResponseErrorCode",
    "OrderSide",
    "OrderType",
    "PriceHistoryInterval",
    "PriceHistoryPoint",
    "PriceRequest",
    "RejectedOrder",
    "RewardsPercentages",
    "SignedOrder",
    "TickSize",
    "TotalUserEarning",
    "TransactionHash",
    "TransactionOutcome",
    "UserEarning",
    "UserRewardsConfig",
    "UserRewardsEarning",
]
