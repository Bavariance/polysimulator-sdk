"""Crypto-price stream event models — mirror the CORE crypto subset of
``polymarket.models.rtds_events``.

Only the two crypto-price topics are in scope:
* ``prices.crypto.binance``   -> :class:`CryptoPricesBinanceEvent`
* ``prices.crypto.chainlink`` -> :class:`CryptoPricesChainlinkEvent`

The comments / equity RTDS events py-sdk also defines are DEFERRED.

The payload's ``value`` accepts a wire number (the SSE crypto frame sends
``price`` as a bare float) and coerces to :class:`~decimal.Decimal`, matching
py-sdk's ``_DecimalFromNumberOrString``.
"""

from __future__ import annotations

from typing import Literal

from polysim_polymarket.models import _BaseModel
from polysim_polymarket.streams._validators import (
    EpochMsOrIsoTimestamp,
    _DecimalFromNumberOrString,
)


class PriceUpdatePayload(_BaseModel):
    symbol: str
    timestamp: int
    value: _DecimalFromNumberOrString


class CryptoPricesBinanceEvent(_BaseModel):
    topic: Literal["prices.crypto.binance"] = "prices.crypto.binance"
    type: Literal["update"]
    timestamp: EpochMsOrIsoTimestamp
    payload: PriceUpdatePayload


class CryptoPricesChainlinkEvent(_BaseModel):
    topic: Literal["prices.crypto.chainlink"] = "prices.crypto.chainlink"
    type: Literal["update"]
    timestamp: EpochMsOrIsoTimestamp
    payload: PriceUpdatePayload


CryptoPricesEvent = CryptoPricesBinanceEvent | CryptoPricesChainlinkEvent


__all__ = [
    "CryptoPricesBinanceEvent",
    "CryptoPricesChainlinkEvent",
    "CryptoPricesEvent",
    "PriceUpdatePayload",
]
