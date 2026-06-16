"""Native PolySimulator enums and string constants.

These are the values the native :class:`~polysim_sdk.client.PolySimClient`
speaks. The drop-in ``polysim_clob_client`` parity layer re-exports the
Polymarket-shaped equivalents (``BUY``/``SELL`` strings, the ``OrderType``
enum) separately so ported py-clob-client code keeps resolving its imports.
"""

from __future__ import annotations

from enum import Enum


class Side(str, Enum):
    """Order side. ``str`` mixin so ``Side.BUY == "BUY"`` and JSON-serialises."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """CLOB time-in-force / order kind, matching Polymarket semantics."""

    GTC = "GTC"  # good-til-cancelled — a resting limit order
    GTD = "GTD"  # good-til-date — an expiring limit order
    FOK = "FOK"  # fill-or-kill — market order, all-or-nothing
    FAK = "FAK"  # fill-and-kill — immediate-or-cancel


# Tier names as returned by GET /v1/keys/tiers. Exposed for readability only —
# never hardcode the numeric limits; fetch them with PolySimClient.tiers().
TIER_FREE = "free"
TIER_PRO = "pro"
TIER_PRO_PLUS = "pro_plus"
TIER_ENTERPRISE = "enterprise"

__all__ = [
    "Side",
    "OrderType",
    "TIER_FREE",
    "TIER_PRO",
    "TIER_PRO_PLUS",
    "TIER_ENTERPRISE",
]
