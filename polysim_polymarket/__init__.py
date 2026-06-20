"""``polysim_polymarket`` — a drop-in mirror of Polymarket's unified **py-sdk**.

Polymarket ships an official unified Python SDK published to PyPI as
``polymarket-client`` and imported as ``polymarket``. This package mirrors its
**unified client surface** in paper mode so a strategy author can:

1. develop and paper-test a bot against PolySimulator using
   ``from polysim_polymarket import PublicClient, SecureClient`` (no on-chain
   wallet, no private key, no gas — paper trading); then
2. run the **same code** on real Polymarket by swapping only three things:

   * the import prefix — ``polysim_polymarket`` -> ``polymarket``
   * the host — point the client at ``https://clob.polymarket.com`` (via the
     real ``Environment``) instead of PolySimulator's ``api.polysimulator.com``
   * the auth — supply real Polymarket credentials instead of a ``ps_live_*``
     PolySimulator API key

Everything else — method names, keyword-only call signatures, and the returned
model field names/types (``OrderBook``, ``LastTradePrice``, ``Market``, …) —
is kept identical to py-sdk so the swap is mechanical.

This ships the full mirrored surface: sync + async ``PublicClient`` /
``SecureClient`` covering CLOB market-data reads, account/auth, and the trading
write surface, plus on-chain paper no-ops, rewards/builder/RFQ stubs, and the
core realtime streams (``market`` / ``user`` / ``crypto_prices``) — a paper-mode
mirror of ``polymarket-client`` ``0.1.0b8``, returning typed pydantic models.
"""

from __future__ import annotations

from polysim_polymarket.clients._onchain import SyncTransactionHandle, TransactionHandle
from polysim_polymarket.clients.async_public import AsyncPublicClient
from polysim_polymarket.clients.async_secure import AsyncSecureClient
from polysim_polymarket.clients.public import PublicClient
from polysim_polymarket.clients.secure import SecureClient
from polysim_polymarket.environments import PRODUCTION, Environment
from polysim_polymarket.errors import (
    InsufficientLiquidityError,
    PolyApiException,
    PolyException,
    UnexpectedResponseError,
    UserInputError,
)
from polysim_polymarket.models import (
    AcceptedOrder,
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
    CurrentRewardConfig,
    EarningBreakdown,
    LastTradePrice,
    LastTradePriceForToken,
    LeaderboardTimePeriod,
    MakerOrder,
    Market,
    MarketOrderType,
    MarketReward,
    MarketRewardConfig,
    MarketRewardToken,
    Notification,
    OpenOrder,
    OrderBook,
    OrderBookLevel,
    OrderPostStatus,
    OrderResponse,
    OrderResponseErrorCode,
    OrderSide,
    OrderType,
    PriceHistoryInterval,
    PriceHistoryPoint,
    PriceRequest,
    RejectedOrder,
    RewardsPercentages,
    SignedOrder,
    TickSize,
    TotalUserEarning,
    TransactionHash,
    TransactionOutcome,
    UserEarning,
    UserRewardsConfig,
    UserRewardsEarning,
)
from polysim_polymarket.pagination import AsyncPaginator, Page, Paginator
from polysim_polymarket.rfq import (
    RfqCancelQuoteAck,
    RfqCancelQuoteRejectedError,
    RfqConfirmationAck,
    RfqConfirmationDecision,
    RfqConfirmationRejectedError,
    RfqConfirmationRequestEvent,
    RfqDirection,
    RfqErrorCode,
    RfqEvent,
    RfqExecutionStatus,
    RfqExecutionUpdateEvent,
    RfqId,
    RfqQuoteId,
    RfqQuoteReference,
    RfqQuoteRejectedError,
    RfqQuoteRequestEvent,
    RfqQuoteSource,
    RfqRequestedSize,
    RfqRequestedSizeUnit,
    RfqRequestorPublicId,
    RfqSession,
    RfqSide,
)
from polysim_sdk import __version__

# py-sdk exports ``SyncTransactionHandle`` (a ``TypeAlias`` for the
# gasless|EOA sync-handle union) off its root; a ported bot type-hints
# ``handle: SyncTransactionHandle``. The mirror has ONE paper handle that plays
# both roles; ``SyncTransactionHandle`` is defined in ``_onchain`` (the canonical
# home of the paper handle) as an alias of it and re-exported here — the on-chain
# methods return this class, and the annotation resolves across the prefix swap.
# (We do NOT promote the mirror-internal ``PaperSyncTransactionHandle`` name to the
# root — it isn't a py-sdk name, so it would break the "root is a subset of
# py-sdk's root" contract.)

# Re-export the Phase-1 public surface so a bot's prefix swap
# (``from polymarket import X`` -> ``from polysim_polymarket import X``) resolves
# every name straight off the package root, exactly as py-sdk's top-level
# ``polymarket`` package does. Names track py-sdk's ``src/polymarket/__init__.py``
# for the subset we ship — including the async surface: ``AsyncPublicClient`` (the
# async twin of ``PublicClient``) and ``AsyncPaginator`` (returned by the async
# client's ``list_markets``) are exported at root, exactly as py-sdk exports them.
# The named errors the Phase-1 surface raises
# (``UserInputError`` / ``InsufficientLiquidityError`` / ``UnexpectedResponseError``)
# are promoted to the root here so ``from polymarket import UserInputError``
# survives the prefix swap. The ONE deliberate divergence is the error-tree
# BASE name: we re-export the py-clob-client-lineage ``PolyException`` /
# ``PolyApiException`` base (shared by identity with the v1 ``polysim_clob_client``
# mirror, see ``errors.py``) rather than py-sdk's ``PolymarketError`` — the three
# named errors above subclass that base, not py-sdk's.
#
# G4 adds the on-chain / rewards / builder / RFQ type surface those new
# SecureClient methods touch, promoted to the root exactly where py-sdk promotes
# the same names: ``TransactionOutcome`` / ``TransactionHash`` /
# ``SyncTransactionHandle`` (the on-chain return surface), the reward types
# (``CurrentReward`` & co + the ``RewardsPercentages`` dict alias), the builder
# types (``BuilderFeeRates`` / ``BuilderTrade`` / ``BuilderVolumeEntry`` /
# ``BuilderVolumeTimePeriod``), the ``LeaderboardTimePeriod`` alias
# (``list_builder_leaderboard``'s ``time_period`` annotation), and the ``Rfq*``
# types py-sdk re-exports at root.
# Each is verified present on py-sdk's root by the re-export subset test.
__all__ = [
    "PRODUCTION",
    "__version__",
    "AcceptedOrder",
    "ApiKeyCreds",
    "AssetType",
    "AsyncPaginator",
    "AsyncPublicClient",
    "AsyncSecureClient",
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
    "Environment",
    "InsufficientLiquidityError",
    "LastTradePrice",
    "LastTradePriceForToken",
    "LeaderboardTimePeriod",
    "MakerOrder",
    "Market",
    "MarketOrderType",
    "MarketReward",
    "MarketRewardConfig",
    "MarketRewardToken",
    "Notification",
    "OpenOrder",
    "OrderBook",
    "OrderBookLevel",
    "OrderPostStatus",
    "OrderResponse",
    "OrderResponseErrorCode",
    "OrderSide",
    "OrderType",
    "Page",
    "Paginator",
    "PolyApiException",
    "PolyException",
    "PriceHistoryInterval",
    "PriceHistoryPoint",
    "PriceRequest",
    "PublicClient",
    "RejectedOrder",
    "RewardsPercentages",
    "RfqCancelQuoteAck",
    "RfqCancelQuoteRejectedError",
    "RfqConfirmationAck",
    "RfqConfirmationDecision",
    "RfqConfirmationRejectedError",
    "RfqConfirmationRequestEvent",
    "RfqDirection",
    "RfqErrorCode",
    "RfqEvent",
    "RfqExecutionStatus",
    "RfqExecutionUpdateEvent",
    "RfqId",
    "RfqQuoteId",
    "RfqQuoteReference",
    "RfqQuoteRejectedError",
    "RfqQuoteRequestEvent",
    "RfqQuoteSource",
    "RfqRequestedSize",
    "RfqRequestedSizeUnit",
    "RfqRequestorPublicId",
    "RfqSession",
    "RfqSide",
    "SecureClient",
    "SignedOrder",
    "SyncTransactionHandle",
    "TickSize",
    "TotalUserEarning",
    "TransactionHandle",
    "TransactionHash",
    "TransactionOutcome",
    "UnexpectedResponseError",
    "UserEarning",
    "UserInputError",
    "UserRewardsConfig",
    "UserRewardsEarning",
]
