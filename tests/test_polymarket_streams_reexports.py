"""Re-export contract for the stream surface.

py-sdk does NOT promote any stream spec / event / handle to its *package root*
(``polymarket``) — the entire stream surface lives in ``polymarket.streams``.
So the mirror must add NOTHING stream-related to its package root either; the
"root is a strict subset of py-sdk's root" contract demands it. The stream
surface is reached via ``polysim_polymarket.streams``, exactly as py-sdk's is via
``polymarket.streams``.

These tests pin both halves of that contract:
1. the mirror's ``streams`` ``__all__`` is a strict subset of py-sdk's
   ``streams`` ``__all__`` (CORE subset, nothing invented), and
2. the mirror's package root exposes no stream name py-sdk's root lacks.
"""

from __future__ import annotations

import pytest

import polysim_polymarket
import polysim_polymarket.streams as mirror_streams

polymarket = pytest.importorskip("polymarket")

import polymarket as real_root  # noqa: E402
import polymarket.streams as real_streams  # noqa: E402

# The CORE subset the mirror ships — every one MUST exist in py-sdk's streams.
CORE_STREAM_NAMES = {
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
}

DEFERRED_STREAM_NAMES = {
    "SportsSpec",
    "SportsEvent",
    "CommentsSpec",
    "CommentsEvent",
    "EquityPricesSpec",
    "EquityPricesEvent",
    "RtdsSpec",
    "RtdsEvent",
    "StreamEvent",
}


def test_mirror_streams_all_is_subset_of_pysdk_streams_all() -> None:
    mirror_all = set(mirror_streams.__all__)
    real_all = set(real_streams.__all__)
    extra = mirror_all - real_all
    assert not extra, f"mirror streams export names py-sdk's streams lacks: {extra}"


def test_mirror_streams_export_the_core_subset() -> None:
    assert CORE_STREAM_NAMES.issubset(set(mirror_streams.__all__))


def test_mirror_streams_omit_deferred_topics() -> None:
    present = DEFERRED_STREAM_NAMES & set(mirror_streams.__all__)
    assert not present, f"mirror exposes DEFERRED stream names: {present}"


def test_pysdk_root_exposes_no_core_stream_names() -> None:
    # The premise of "re-export nothing at root": py-sdk itself promotes none of
    # the CORE stream names to its package root.
    real_root_all = set(getattr(real_root, "__all__", dir(real_root)))
    leaked = CORE_STREAM_NAMES & real_root_all
    assert not leaked, f"py-sdk root unexpectedly exposes stream names: {leaked}"


def test_mirror_root_adds_no_stream_names_pysdk_root_lacks() -> None:
    mirror_root_all = set(getattr(polysim_polymarket, "__all__", dir(polysim_polymarket)))
    real_root_all = set(getattr(real_root, "__all__", dir(real_root)))
    # Any stream-ish name on the mirror root must also be on py-sdk's root.
    stream_ish = {n for n in mirror_root_all if n in CORE_STREAM_NAMES | DEFERRED_STREAM_NAMES}
    extra = stream_ish - real_root_all
    assert not extra, f"mirror root adds stream names py-sdk root lacks: {extra}"


def test_mirror_root_does_not_promote_stream_surface() -> None:
    # Concretely: the mirror root must NOT export the stream specs/events — they
    # live in polysim_polymarket.streams, mirroring py-sdk's layout.
    mirror_root_all = set(getattr(polysim_polymarket, "__all__", []))
    assert "MarketSpec" not in mirror_root_all
    assert "SubscriptionHandle" not in mirror_root_all
    assert "CryptoPricesSpec" not in mirror_root_all
