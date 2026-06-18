"""User-stream event models — mirror ``polymarket.models.clob.user_events``.

The authenticated user stream carries ``order`` and ``trade`` events for the
account behind the client's API key. Models match py-sdk field-for-field so a
ported fill-tracking bot reads identical attributes across the prefix swap.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AliasChoices, BeforeValidator, Field

from polysim_polymarket.models import _BaseModel
from polysim_polymarket.streams._validators import (
    EpochSecondsOrMsTimestamp,
    EpochSecondsTimestamp,
    ExpirationTimestamp,
    _DecimalFromNumberOrString,
)

# Number-or-string parity with py-sdk for out-of-band parsing; see the same
# note in ``_market_events``. The integrated adapter path pre-stringifies, so it
# is unaffected — this restores field-for-field parity for ``parse_*`` callers.


def _uppercase_string(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


_OrderSide = Annotated[Literal["BUY", "SELL"], BeforeValidator(_uppercase_string)]
_OrderEventType = Literal["PLACEMENT", "UPDATE", "CANCELLATION"]
_OrderStatus = Literal["LIVE", "MATCHED", "DELAYED", "UNMATCHED", "CANCELED"]
_OrderType = Literal["GTC", "FOK", "IOC", "GTD", "FAK"]
_TraderSide = Annotated[Literal["TAKER", "MAKER"], BeforeValidator(_uppercase_string)]
_TradeStatus = Literal[
    "MATCHED",
    "MATCHED_NOT_BROADCASTED",
    "MINED",
    "CONFIRMED",
    "RETRYING",
    "FAILED",
]


def _normalize_trade_status(value: object) -> object:
    if isinstance(value, str) and value.startswith("TRADE_STATUS_"):
        return value[len("TRADE_STATUS_") :]
    return value


_TradeStatusValidator = Annotated[_TradeStatus, BeforeValidator(_normalize_trade_status)]


class UserOrderPayload(_BaseModel):
    id: str
    owner: str
    market: str
    token_id: str = Field(validation_alias="asset_id")
    side: _OrderSide
    original_size: _DecimalFromNumberOrString
    size_matched: _DecimalFromNumberOrString
    price: _DecimalFromNumberOrString
    order_event_type: _OrderEventType = Field(validation_alias="type")
    timestamp: EpochSecondsOrMsTimestamp = None
    created_at: EpochSecondsTimestamp = None
    expires_at: ExpirationTimestamp = Field(default=None, validation_alias="expiration")
    order_type: _OrderType | None = None
    status: _OrderStatus | None = None
    maker_address: str | None = None
    order_owner: str | None = None
    associate_trades: tuple[str, ...] | None = None
    outcome: str | None = None


class UserTradeMakerOrder(_BaseModel):
    order_id: str
    owner: str
    maker_address: str | None = None
    matched_amount: _DecimalFromNumberOrString
    price: _DecimalFromNumberOrString
    fee_rate_bps: _DecimalFromNumberOrString | None = None
    token_id: str = Field(validation_alias="asset_id")
    side: _OrderSide
    outcome: str | None = None
    outcome_index: int | None = None


class UserTradePayload(_BaseModel):
    id: str
    taker_order_id: str
    market: str
    token_id: str = Field(validation_alias="asset_id")
    side: _OrderSide
    size: _DecimalFromNumberOrString
    price: _DecimalFromNumberOrString
    status: _TradeStatusValidator
    owner: str
    timestamp: EpochSecondsOrMsTimestamp = None
    fee_rate_bps: _DecimalFromNumberOrString | None = None
    matched_at: EpochSecondsTimestamp = Field(
        default=None, validation_alias=AliasChoices("match_time", "matchtime")
    )
    updated_at: EpochSecondsTimestamp = Field(default=None, validation_alias="last_update")
    trade_owner: str | None = None
    maker_address: str | None = None
    transaction_hash: str | None = None
    bucket_index: int | None = None
    maker_orders: tuple[UserTradeMakerOrder, ...] | None = None
    trader_side: _TraderSide | None = None
    outcome: str | None = None


class UserOrderEvent(_BaseModel):
    topic: Literal["user"] = "user"
    type: Literal["order"]
    payload: UserOrderPayload


class UserTradeEvent(_BaseModel):
    topic: Literal["user"] = "user"
    type: Literal["trade"]
    payload: UserTradePayload


UserEvent = Annotated[UserOrderEvent | UserTradeEvent, Field(discriminator="type")]


__all__ = [
    "UserEvent",
    "UserOrderEvent",
    "UserOrderPayload",
    "UserTradeEvent",
    "UserTradeMakerOrder",
    "UserTradePayload",
]
