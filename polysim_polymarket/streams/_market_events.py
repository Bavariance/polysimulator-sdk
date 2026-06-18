"""Market-stream event models — mirror ``polymarket.models.clob.market_events``.

The CORE events the paper stream EMITS are ``book`` / ``price_change`` /
``last_trade_price``. The custom-feature top-of-book event (``best_bid_ask``)
and the lifecycle events (``new_market`` / ``market_resolved`` /
``tick_size_change``) are defined here for **type parity** — so the
``MarketEvent`` discriminated union has all seven members and a ported bot's
``from polymarket.streams import MarketResolvedEvent`` (etc.) resolves across the
prefix swap — but the PolySimulator paper stream does **not** emit them (see
``streams/_adapt.py``).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from polysim_polymarket.models import (
    OrderBookLevel,
    _BaseModel,
)
from polysim_polymarket.streams._validators import (
    EpochMsTimestamp,
    _DecimalFromNumberOrString,
)

# Stream events parse the same numeric wire shapes py-sdk accepts: a decimal
# field may arrive as a bare number (the integrated adapter path pre-stringifies,
# but out-of-band ``parse_*`` / ``model_validate`` callers may pass floats/ints).
# We use the number-or-string variant for field-for-field parity with py-sdk; the
# strict string-only ``_DecimalFromString`` (``polysim_polymarket.models``) stays
# on the CLOB-read models.


def _uppercase_order_side(value: object) -> object:
    return value.upper() if isinstance(value, str) else value


_OrderSide = Annotated[Literal["BUY", "SELL"], BeforeValidator(_uppercase_order_side)]


class MarketEventMessage(_BaseModel):
    id: str
    ticker: str | None = None
    slug: str | None = None
    title: str | None = None
    description: str | None = None


class PriceChange(_BaseModel):
    token_id: str = Field(validation_alias="asset_id")
    price: _DecimalFromNumberOrString
    size: _DecimalFromNumberOrString
    side: _OrderSide
    hash: str | None = None
    best_bid: _DecimalFromNumberOrString | None = None
    best_ask: _DecimalFromNumberOrString | None = None


# --- Payloads ---


class MarketBookPayload(_BaseModel):
    market: str
    token_id: str = Field(validation_alias="asset_id")
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    hash: str | None = None
    timestamp: EpochMsTimestamp = None
    min_order_size: _DecimalFromNumberOrString | None = None
    tick_size: _DecimalFromNumberOrString | None = None
    neg_risk: bool | None = None
    last_trade_price: _DecimalFromNumberOrString | None = None


class MarketPriceChangePayload(_BaseModel):
    market: str
    price_changes: tuple[PriceChange, ...]
    timestamp: EpochMsTimestamp = None


class MarketLastTradePricePayload(_BaseModel):
    market: str
    token_id: str = Field(validation_alias="asset_id")
    price: _DecimalFromNumberOrString
    size: _DecimalFromNumberOrString | None = None
    side: _OrderSide
    fee_rate_bps: _DecimalFromNumberOrString | None = None
    transaction_hash: str | None = None
    timestamp: EpochMsTimestamp = None


class MarketTickSizeChangePayload(_BaseModel):
    market: str
    token_id: str = Field(validation_alias="asset_id")
    old_tick_size: _DecimalFromNumberOrString | None = None
    new_tick_size: _DecimalFromNumberOrString
    timestamp: EpochMsTimestamp = None


class MarketBestBidAskPayload(_BaseModel):
    market: str
    token_id: str = Field(validation_alias="asset_id")
    best_bid: _DecimalFromNumberOrString | None = None
    best_ask: _DecimalFromNumberOrString | None = None
    spread: _DecimalFromNumberOrString | None = None
    timestamp: EpochMsTimestamp = None


class NewMarketPayload(_BaseModel):
    id: str
    market: str
    question: str | None = None
    slug: str | None = None
    description: str | None = None
    token_ids: tuple[str, ...] | None = Field(default=None, validation_alias="assets_ids")
    outcomes: tuple[str, ...] | None = None
    event_message: MarketEventMessage | None = None
    timestamp: EpochMsTimestamp = None
    tags: tuple[str, ...] | None = None
    condition_id: str | None = None
    active: bool | None = None
    clob_token_ids: tuple[str, ...] | None = None
    sports_market_type: str | None = None
    line: _DecimalFromNumberOrString | None = None
    game_start_time: EpochMsTimestamp = None
    order_price_min_tick_size: _DecimalFromNumberOrString | None = None
    group_item_title: str | None = None
    taker_base_fee: _DecimalFromNumberOrString | None = None
    fees_enabled: bool | None = None
    fee_schedule: object | None = None


class MarketResolvedPayload(_BaseModel):
    id: str
    market: str
    token_ids: tuple[str, ...] | None = Field(default=None, validation_alias="assets_ids")
    winning_token_id: str | None = Field(default=None, validation_alias="winning_asset_id")
    winning_outcome: str | None = None
    event_message: MarketEventMessage | None = None
    timestamp: EpochMsTimestamp = None
    tags: tuple[str, ...] | None = None


# --- Envelopes: {topic, type, payload} ---


class MarketBookEvent(_BaseModel):
    topic: Literal["market"] = "market"
    type: Literal["book"]
    payload: MarketBookPayload


class MarketPriceChangeEvent(_BaseModel):
    topic: Literal["market"] = "market"
    type: Literal["price_change"]
    payload: MarketPriceChangePayload


class MarketLastTradePriceEvent(_BaseModel):
    topic: Literal["market"] = "market"
    type: Literal["last_trade_price"]
    payload: MarketLastTradePricePayload


class MarketTickSizeChangeEvent(_BaseModel):
    topic: Literal["market"] = "market"
    type: Literal["tick_size_change"]
    payload: MarketTickSizeChangePayload


class MarketBestBidAskEvent(_BaseModel):
    topic: Literal["market"] = "market"
    type: Literal["best_bid_ask"]
    payload: MarketBestBidAskPayload


class NewMarketEvent(_BaseModel):
    topic: Literal["market"] = "market"
    type: Literal["new_market"]
    payload: NewMarketPayload


class MarketResolvedEvent(_BaseModel):
    topic: Literal["market"] = "market"
    type: Literal["market_resolved"]
    payload: MarketResolvedPayload


MarketEvent = Annotated[
    MarketBookEvent
    | MarketPriceChangeEvent
    | MarketLastTradePriceEvent
    | MarketTickSizeChangeEvent
    | MarketBestBidAskEvent
    | NewMarketEvent
    | MarketResolvedEvent,
    Field(discriminator="type"),
]


__all__ = [
    "MarketBestBidAskEvent",
    "MarketBestBidAskPayload",
    "MarketBookEvent",
    "MarketBookPayload",
    "MarketEvent",
    "MarketEventMessage",
    "MarketLastTradePriceEvent",
    "MarketLastTradePricePayload",
    "MarketPriceChangeEvent",
    "MarketPriceChangePayload",
    "MarketResolvedEvent",
    "MarketResolvedPayload",
    "MarketTickSizeChangeEvent",
    "MarketTickSizeChangePayload",
    "NewMarketEvent",
    "NewMarketPayload",
    "PriceChange",
]
