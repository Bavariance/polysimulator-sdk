"""Subscription specs for the CORE realtime stream topics.

Mirrors ``polymarket.streams._specs`` for the CORE subset the mirror ships:

* :class:`MarketSpec`   — topic ``"market"``   (book / price_change / last_trade)
* :class:`UserSpec`     — topic ``"user"``     (authenticated fills)
* :class:`CryptoPricesSpec` — topic ``"prices.crypto.{binance,chainlink}"``

The dataclass shape (frozen / slots / kw_only, the ``topic`` ``init=False``
field on Market/User) and the ``__post_init__`` validation are kept identical
to py-sdk so a ported bot constructs them the same way across the prefix swap.

DEFERRED (intentionally absent — py-sdk has them, the mirror does not): the
``sports`` (``SportsSpec``), ``comments`` (``CommentsSpec``), and
``prices.equity.pyth`` (``EquityPricesSpec``) topics, plus the ``RtdsSpec``
alias. See the package README for the deferral rationale.
"""

# pyright: reportUnnecessaryIsInstance=false
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from polysim_polymarket.errors import UserInputError

_CRYPTO_PRICES_TOPICS: frozenset[str] = frozenset(
    {"prices.crypto.binance", "prices.crypto.chainlink"}
)

CryptoPricesTopic = Literal["prices.crypto.binance", "prices.crypto.chainlink"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketSpec:
    """Subscribe to realtime market updates for one or more token ids.

    Set ``custom_feature_enabled=True`` to additionally receive
    ``MarketBestBidAskEvent``, ``NewMarketEvent``, and ``MarketResolvedEvent``.

    Note: against PolySimulator the custom-feature top-of-book and lifecycle
    events are **not emitted** by the paper stream — the flag is accepted for
    py-sdk parity but only ``book`` / ``price_change`` / ``last_trade_price``
    events arrive. See the adapter module for the seam.
    """

    token_ids: Sequence[str]
    """Token ids whose market events should be delivered."""
    custom_feature_enabled: bool = False
    """Whether to enable top-of-book and market lifecycle events."""
    topic: Literal["market"] = field(default="market", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.custom_feature_enabled, bool):
            raise UserInputError("custom_feature_enabled must be a bool")
        if isinstance(self.token_ids, str | bytes):
            raise UserInputError("token_ids must be a sequence of token ids, not a single string")
        normalized: list[str] = []
        for tid in self.token_ids:
            if not isinstance(tid, str):
                raise UserInputError(f"token_id must be a string, got {type(tid).__name__}")
            if not tid:
                raise UserInputError("token_id must be non-empty")
            normalized.append(tid)
        if not normalized:
            raise UserInputError("token_ids must be a non-empty sequence")
        object.__setattr__(self, "token_ids", tuple(normalized))


@dataclass(frozen=True, slots=True, kw_only=True)
class CryptoPricesSpec:
    """Subscribe to realtime crypto price updates for a topic.

    When ``symbols`` is omitted, the subscription receives all symbols for the
    selected topic.
    """

    topic: CryptoPricesTopic
    symbols: Sequence[str] | None = None

    def __post_init__(self) -> None:
        if self.topic not in _CRYPTO_PRICES_TOPICS:
            raise UserInputError(
                f"topic must be one of {sorted(_CRYPTO_PRICES_TOPICS)}, got {self.topic!r}"
            )
        if self.symbols is not None:
            if isinstance(self.symbols, str | bytes):
                raise UserInputError("symbols must be a sequence of symbols, not a single string")
            normalized: list[str] = []
            for s in self.symbols:
                if not isinstance(s, str):
                    raise UserInputError(f"symbol must be a string, got {type(s).__name__}")
                if not s:
                    raise UserInputError("symbol must be non-empty")
                normalized.append(s)
            if not normalized:
                raise UserInputError("symbols must be non-empty when provided")
            object.__setattr__(self, "symbols", tuple(normalized))


@dataclass(frozen=True, slots=True, kw_only=True)
class UserSpec:
    """Subscribe to authenticated user order and trade events.

    When ``markets`` is omitted, the subscription receives user events for all
    markets available to the authenticated account.
    """

    markets: Sequence[str] | None = None
    topic: Literal["user"] = field(default="user", init=False)

    def __post_init__(self) -> None:
        if self.markets is None:
            return
        if isinstance(self.markets, str | bytes):
            raise UserInputError("markets must be a sequence of market ids, not a single string")
        normalized: list[str] = []
        for m in self.markets:
            if isinstance(m, bool) or not isinstance(m, str):
                raise UserInputError(f"market must be a string, got {type(m).__name__}")
            if not m:
                raise UserInputError("market must be non-empty")
            normalized.append(m)
        object.__setattr__(self, "markets", tuple(normalized) if normalized else None)


# CORE-subset aliases. py-sdk's ``PublicSubscription`` / ``SecureSubscription``
# also fold in SportsSpec / CommentsSpec / EquityPricesSpec — those topics are
# DEFERRED here, so the mirror's aliases name only the core specs.
PublicSubscription = MarketSpec | CryptoPricesSpec
SecureSubscription = PublicSubscription | UserSpec
Subscription = SecureSubscription


__all__ = [
    "CryptoPricesSpec",
    "CryptoPricesTopic",
    "MarketSpec",
    "PublicSubscription",
    "SecureSubscription",
    "Subscription",
    "UserSpec",
]
