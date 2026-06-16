"""py-clob-client-compatible types.

Mirrors the dataclasses and enums that ported strategies construct, so
``from py_clob_client.clob_types import OrderArgs, ApiCreds`` becomes
``from polysim_clob_client.clob_types import OrderArgs, ApiCreds`` unchanged.

Field names and defaults match py-clob-client. On-chain-only fields
(``fee_rate_bps``, ``nonce``, ``expiration``, ``taker``, ``signature_type``)
are accepted and stored but ignored — there is no signing or settlement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from polysim_clob_client.constants import ZERO_ADDRESS


class OrderType(str, Enum):
    """Time-in-force / order kind, matching py-clob-client."""

    GTC = "GTC"  # good-til-cancelled (resting limit)
    GTD = "GTD"  # good-til-date (expiring limit)
    FOK = "FOK"  # fill-or-kill (market; MarketOrderArgs default)
    FAK = "FAK"  # fill-and-kill (IOC)


class AssetType(str, Enum):
    COLLATERAL = "COLLATERAL"  # USDC
    CONDITIONAL = "CONDITIONAL"  # outcome share token


@dataclass
class ApiCreds:
    api_key: str
    api_secret: str = ""
    api_passphrase: str = ""


@dataclass
class OrderArgs:
    token_id: str
    price: float
    size: float
    side: str  # BUY / SELL
    fee_rate_bps: int = 0
    nonce: int = 0
    expiration: int = 0
    taker: str = ZERO_ADDRESS


@dataclass
class MarketOrderArgs:
    token_id: str
    amount: float
    side: str = ""
    price: float = 0
    fee_rate_bps: int = 0
    nonce: int = 0
    taker: str = ZERO_ADDRESS
    order_type: OrderType = OrderType.FOK


@dataclass
class PostOrdersArgs:
    order: dict[str, Any]
    orderType: OrderType = OrderType.GTC
    postOnly: bool = False


@dataclass
class BookParams:
    token_id: str
    side: str = ""


@dataclass
class TradeParams:
    id: str | None = None
    maker_address: str | None = None
    market: str | None = None
    asset_id: str | None = None
    before: int | None = None
    after: int | None = None


@dataclass
class OpenOrderParams:
    id: str | None = None
    market: str | None = None
    asset_id: str | None = None


@dataclass
class DropNotificationParams:
    ids: list[str] = field(default_factory=list)


@dataclass
class OrderSummary:
    price: str | None = None
    size: str | None = None


@dataclass
class OrderBookSummary:
    market: str | None = None
    asset_id: str | None = None
    timestamp: str | None = None
    bids: list[OrderSummary] = field(default_factory=list)
    asks: list[OrderSummary] = field(default_factory=list)
    min_order_size: str | None = None
    neg_risk: bool | None = None
    tick_size: str | None = None
    last_trade_price: str | None = None
    hash: str | None = None


@dataclass
class BalanceAllowanceParams:
    asset_type: AssetType | None = None
    token_id: str | None = None
    signature_type: int = -1


@dataclass
class OrderScoringParams:
    orderId: str


@dataclass
class OrdersScoringParams:
    orderIds: list[str]


@dataclass
class CreateOrderOptions:
    tick_size: str
    neg_risk: bool


@dataclass
class PartialCreateOrderOptions:
    tick_size: str | None = None
    neg_risk: bool | None = None


@dataclass
class ReadonlyApiKeyResponse:
    api_key: str


__all__ = [
    "OrderType",
    "AssetType",
    "ApiCreds",
    "OrderArgs",
    "MarketOrderArgs",
    "PostOrdersArgs",
    "BookParams",
    "TradeParams",
    "OpenOrderParams",
    "DropNotificationParams",
    "OrderSummary",
    "OrderBookSummary",
    "BalanceAllowanceParams",
    "OrderScoringParams",
    "OrdersScoringParams",
    "CreateOrderOptions",
    "PartialCreateOrderOptions",
    "ReadonlyApiKeyResponse",
]
