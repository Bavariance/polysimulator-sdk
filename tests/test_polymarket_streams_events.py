"""Event-model parity tests for the CORE stream subset.

The mirror's stream event models must parse the same wire shapes py-sdk's do and
carry the same fields/types, so a ported bot reading ``ev.payload.token_id`` /
``ev.payload.side`` / ``ev.payload.value`` gets identical attributes across the
prefix swap.

CORE events covered:
* market: MarketBookEvent / MarketPriceChangeEvent / MarketLastTradePriceEvent
  (+ the defined-for-parity custom_feature/lifecycle types) + PriceChange +
  MarketEventMessage
* user: UserOrderEvent / UserTradeEvent (+ UserTradeMakerOrder)
* crypto: CryptoPricesBinanceEvent / CryptoPricesChainlinkEvent
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from polysim_polymarket.streams import (
    CryptoPricesBinanceEvent,
    CryptoPricesChainlinkEvent,
    CryptoPricesEvent,
    MarketBestBidAskEvent,
    MarketBookEvent,
    MarketBookPayload,
    MarketEvent,
    MarketEventMessage,
    MarketLastTradePriceEvent,
    MarketPriceChangeEvent,
    MarketResolvedEvent,
    MarketTickSizeChangeEvent,
    NewMarketEvent,
    PriceChange,
    UserEvent,
    UserOrderEvent,
    UserTradeEvent,
    UserTradeMakerOrder,
)


def test_market_book_event_parses() -> None:
    ev = MarketBookEvent.model_validate(
        {
            "type": "book",
            "payload": {
                "market": "0xMARKET",
                "asset_id": "tok1",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
                "hash": "h",
                "timestamp": "1700000000000",
            },
        }
    )
    assert ev.topic == "market"
    assert ev.type == "book"
    assert ev.payload.token_id == "tok1"
    assert ev.payload.bids[0].price == Decimal("0.40")
    assert isinstance(ev.payload.timestamp, datetime)


def test_market_price_change_event_parses() -> None:
    ev = MarketPriceChangeEvent.model_validate(
        {
            "type": "price_change",
            "payload": {
                "market": "0xMARKET",
                "price_changes": [
                    {"asset_id": "tok1", "price": "0.55", "size": "0", "side": "BUY"}
                ],
                "timestamp": "1700000000000",
            },
        }
    )
    assert ev.type == "price_change"
    pc = ev.payload.price_changes[0]
    assert pc.token_id == "tok1"
    assert pc.price == Decimal("0.55")
    assert pc.side == "BUY"


def test_price_change_uppercases_side() -> None:
    pc = PriceChange.model_validate({"asset_id": "t", "price": "0.5", "size": "1", "side": "buy"})
    assert pc.side == "BUY"


def test_market_last_trade_price_event_parses() -> None:
    ev = MarketLastTradePriceEvent.model_validate(
        {
            "type": "last_trade_price",
            "payload": {
                "market": "0xMARKET",
                "asset_id": "tok1",
                "price": "0.52",
                "size": "10",
                "side": "SELL",
            },
        }
    )
    assert ev.type == "last_trade_price"
    assert ev.payload.token_id == "tok1"
    assert ev.payload.price == Decimal("0.52")
    assert ev.payload.side == "SELL"


def test_market_event_message_model() -> None:
    m = MarketEventMessage.model_validate({"id": "e1", "slug": "who-wins", "title": "Who?"})
    assert m.id == "e1"
    assert m.slug == "who-wins"


def test_user_trade_event_parses() -> None:
    ev = UserTradeEvent.model_validate(
        {
            "type": "trade",
            "payload": {
                "id": "trade1",
                "taker_order_id": "ord1",
                "market": "0xMARKET",
                "asset_id": "tok1",
                "side": "buy",
                "size": "5",
                "price": "0.50",
                "status": "MATCHED",
                "owner": "0xUSER",
            },
        }
    )
    assert ev.topic == "user"
    assert ev.type == "trade"
    assert ev.payload.token_id == "tok1"
    assert ev.payload.side == "BUY"
    assert ev.payload.size == Decimal("5")
    assert ev.payload.status == "MATCHED"


def test_user_trade_event_normalizes_trade_status_prefix() -> None:
    ev = UserTradeEvent.model_validate(
        {
            "type": "trade",
            "payload": {
                "id": "t",
                "taker_order_id": "o",
                "market": "m",
                "asset_id": "tok",
                "side": "BUY",
                "size": "1",
                "price": "0.5",
                "status": "TRADE_STATUS_CONFIRMED",
                "owner": "0xU",
            },
        }
    )
    assert ev.payload.status == "CONFIRMED"


def test_user_order_event_parses() -> None:
    ev = UserOrderEvent.model_validate(
        {
            "type": "order",
            "payload": {
                "id": "ord1",
                "owner": "0xUSER",
                "market": "0xMARKET",
                "asset_id": "tok1",
                "side": "BUY",
                "original_size": "10",
                "size_matched": "4",
                "price": "0.50",
                "type": "PLACEMENT",
            },
        }
    )
    assert ev.type == "order"
    assert ev.payload.order_event_type == "PLACEMENT"
    assert ev.payload.token_id == "tok1"


def test_user_trade_maker_order_model() -> None:
    mo = UserTradeMakerOrder.model_validate(
        {
            "order_id": "o1",
            "owner": "0xM",
            "matched_amount": "3",
            "price": "0.5",
            "asset_id": "tok",
            "side": "SELL",
        }
    )
    assert mo.token_id == "tok"
    assert mo.matched_amount == Decimal("3")


def test_market_price_change_accepts_numeric_decimals() -> None:
    # Validator parity: out-of-band ``model_validate`` accepts bare numeric
    # decimal fields (not only strings), matching py-sdk's
    # ``_DecimalFromNumberOrString``. (The integrated adapter pre-stringifies, so
    # this guards the out-of-band parsing path.)
    ev = MarketPriceChangeEvent.model_validate(
        {
            "type": "price_change",
            "payload": {
                "market": "0xMARKET",
                "price_changes": [
                    {"asset_id": "tok1", "price": 0.55, "size": 0, "side": "BUY",
                     "best_bid": 0.54, "best_ask": 0.56}
                ],
            },
        }
    )
    pc = ev.payload.price_changes[0]
    assert pc.price == Decimal("0.55")
    assert pc.size == Decimal("0")
    assert pc.best_bid == Decimal("0.54")


def test_user_trade_event_accepts_numeric_decimals() -> None:
    # Same number-or-string parity for the user stream-event decimal fields.
    ev = UserTradeEvent.model_validate(
        {
            "type": "trade",
            "payload": {
                "id": "t",
                "taker_order_id": "o",
                "market": "m",
                "asset_id": "tok",
                "side": "BUY",
                "size": 5,
                "price": 0.5,
                "status": "MATCHED",
                "owner": "0xU",
            },
        }
    )
    assert ev.payload.size == Decimal("5")
    assert ev.payload.price == Decimal("0.5")


def test_crypto_binance_event_accepts_float_value() -> None:
    # The backend SSE crypto frame carries ``price`` as a bare float — the
    # crypto event's number-or-string decimal coercion must accept it.
    ev = CryptoPricesBinanceEvent.model_validate(
        {
            "type": "update",
            "timestamp": "1700000000000",
            "payload": {"symbol": "BTC", "timestamp": 1700000000000, "value": 64588.0},
        }
    )
    assert ev.topic == "prices.crypto.binance"
    assert ev.payload.symbol == "BTC"
    assert ev.payload.value == Decimal("64588.0")


def test_crypto_chainlink_event_accepts_iso_timestamp() -> None:
    ev = CryptoPricesChainlinkEvent.model_validate(
        {
            "type": "update",
            "timestamp": "2026-06-14T12:00:00Z",
            "payload": {"symbol": "ETH", "timestamp": 1700000000000, "value": "3400.5"},
        }
    )
    assert ev.topic == "prices.crypto.chainlink"
    assert ev.payload.value == Decimal("3400.5")
    assert isinstance(ev.timestamp, datetime)


# ── union discriminator dispatch (via the parse helpers) ──────────────────


def test_market_event_union_includes_all_seven_types() -> None:
    import typing

    args = typing.get_args(MarketEvent)
    # MarketEvent is Annotated[Union[...], Field(discriminator=...)] — the union
    # is the first arg.
    union = args[0]
    members = set(typing.get_args(union))
    assert members == {
        MarketBookEvent,
        MarketPriceChangeEvent,
        MarketLastTradePriceEvent,
        MarketTickSizeChangeEvent,
        MarketBestBidAskEvent,
        NewMarketEvent,
        MarketResolvedEvent,
    }


def test_user_event_union_is_order_and_trade() -> None:
    import typing

    union = typing.get_args(UserEvent)[0]
    assert set(typing.get_args(union)) == {UserOrderEvent, UserTradeEvent}


def test_crypto_event_union_is_binance_and_chainlink() -> None:
    import typing

    assert set(typing.get_args(CryptoPricesEvent)) == {
        CryptoPricesBinanceEvent,
        CryptoPricesChainlinkEvent,
    }


def test_book_payload_book_levels_ascending_descending_doc() -> None:
    # Sanity: MarketBookPayload accepts the OrderBookLevel tuples (best-first
    # is the caller's responsibility; the model just holds them).
    p = MarketBookPayload.model_validate(
        {"market": "m", "asset_id": "t", "bids": [], "asks": []}
    )
    assert p.bids == ()
    assert p.asks == ()
