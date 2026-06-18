"""Public types for the PolySimulator realtime stream consumers.

Mirrors ``polymarket.streams`` for the **CORE** topic subset the mirror ships:

* **market**        (:class:`MarketSpec`)        — book / price_change / last_trade_price
* **user**          (:class:`UserSpec`)          — authenticated order / trade fills
* **crypto_prices** (:class:`CryptoPricesSpec`)  — Binance / Chainlink crypto ticks

A consumer subscribes via the async client and iterates the handle::

    async with await client.subscribe(MarketSpec(token_ids=["..."])) as h:
        async for ev in h:
            ...

DEFERRED — py-sdk exposes these in ``polymarket.streams``; the mirror does NOT:
``sports`` (``SportsSpec`` / ``SportsEvent``), ``comments``
(``CommentsSpec`` / ``CommentsEvent`` / reactions), and ``prices.equity.pyth``
(``EquityPricesSpec`` / ``EquityPricesEvent``), plus the ``RtdsSpec`` /
``StreamEvent`` aliases that fold them in. See the package README for the
deferral rationale.

Note on the custom-feature market events: ``MarketBestBidAskEvent``,
``NewMarketEvent``, ``MarketResolvedEvent``, and ``MarketTickSizeChangeEvent``
are defined here for **type parity** (so the ``MarketEvent`` union has all seven
members and a ported bot's imports resolve) but the PolySimulator paper stream
does **not** emit them — only ``book`` / ``price_change`` / ``last_trade_price``
arrive. See ``streams/_adapt.py``.
"""

from __future__ import annotations

from polysim_polymarket.streams._crypto_events import (
    CryptoPricesBinanceEvent,
    CryptoPricesChainlinkEvent,
    CryptoPricesEvent,
    PriceUpdatePayload,
)
from polysim_polymarket.streams._handle import SubscriptionHandle
from polysim_polymarket.streams._market_events import (
    MarketBestBidAskEvent,
    MarketBestBidAskPayload,
    MarketBookEvent,
    MarketBookPayload,
    MarketEvent,
    MarketEventMessage,
    MarketLastTradePriceEvent,
    MarketLastTradePricePayload,
    MarketPriceChangeEvent,
    MarketPriceChangePayload,
    MarketResolvedEvent,
    MarketResolvedPayload,
    MarketTickSizeChangeEvent,
    MarketTickSizeChangePayload,
    NewMarketEvent,
    NewMarketPayload,
    PriceChange,
)
from polysim_polymarket.streams._specs import (
    CryptoPricesSpec,
    CryptoPricesTopic,
    MarketSpec,
    PublicSubscription,
    SecureSubscription,
    Subscription,
    UserSpec,
)
from polysim_polymarket.streams._user_events import (
    UserEvent,
    UserOrderEvent,
    UserOrderPayload,
    UserTradeEvent,
    UserTradeMakerOrder,
    UserTradePayload,
)

__all__ = [
    "CryptoPricesBinanceEvent",
    "CryptoPricesChainlinkEvent",
    "CryptoPricesEvent",
    "CryptoPricesSpec",
    "CryptoPricesTopic",
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
    "MarketSpec",
    "MarketTickSizeChangeEvent",
    "MarketTickSizeChangePayload",
    "NewMarketEvent",
    "NewMarketPayload",
    "PriceChange",
    "PriceUpdatePayload",
    "PublicSubscription",
    "SecureSubscription",
    "Subscription",
    "SubscriptionHandle",
    "UserEvent",
    "UserOrderEvent",
    "UserOrderPayload",
    "UserSpec",
    "UserTradeEvent",
    "UserTradeMakerOrder",
    "UserTradePayload",
]
