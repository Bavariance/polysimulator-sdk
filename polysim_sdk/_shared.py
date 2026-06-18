"""Pure, client-agnostic helpers shared across the PolySim CLOB mirrors.

These were proven in ``polysim_clob_client.client`` (the v1 py-clob-client
mirror) and are extracted here so the ``polysim_polymarket`` py-sdk mirror reuses
the identical read-path logic — cursor <-> offset translation, order-book level
parsing, and the bare-vs-colon token-id seam — without either mirror importing
the other's client.

Everything here is a free function of its arguments (no client state), which is
exactly why it can be shared. The instance-bound ``_resolve_token`` (which makes
a network call to reverse-resolve real CLOB token ids) deliberately stays on the
v1 ``ClobClient``.
"""

from __future__ import annotations

import base64
from typing import Any

# ── cursor <-> offset translation ──────────────────────────────────────────
# py-clob-client paginates with base64 cursors ("MA=="=0, "LTE="=-1=done).
# PolySim REST is limit/offset, so we translate at the boundary.
#
# The sentinels are defined locally (not imported from polysim_clob_client)
# because polysim_sdk is the LOWER layer — polysim_clob_client imports it, so
# importing back up here would be a circular import. They are the canonical
# base64 values: START_CURSOR == base64("0"), END_CURSOR == base64("-1").
# polysim_clob_client.constants re-declares the same literals for its public API.
START_CURSOR = "MA=="
END_CURSOR = "LTE="


def _decode_cursor(cursor: str | None) -> int:
    """base64 cursor -> integer offset. START/empty -> 0, END -> -1."""
    if not cursor or cursor == START_CURSOR:
        return 0
    if cursor == END_CURSOR:
        return -1
    try:
        return int(base64.b64decode(cursor).decode())
    except (ValueError, TypeError):
        return 0


def _encode_cursor(offset: int) -> str:
    """integer offset -> base64 cursor."""
    return base64.b64encode(str(offset).encode()).decode()


def _next_cursor(offset: int, page_len: int, limit: int) -> str:
    """Synthesise the next cursor: END when the page was short."""
    return _encode_cursor(offset + limit) if page_len >= limit else END_CURSOR


# ── order-book parsing helpers ─────────────────────────────────────────────


def _to_levels(raw: Any) -> list[tuple[float, float]]:
    """Normalise a side of the book to ``[(price, size), ...]`` floats.

    Tolerates dict levels (``{"price","size"|"quantity"}``) and pair levels
    (``[price, size]``); skips anything unparseable.
    """
    out: list[tuple[float, float]] = []
    for lvl in raw or []:
        price: Any
        size: Any
        if isinstance(lvl, dict):
            price = lvl.get("price")
            size = lvl.get("size", lvl.get("quantity"))
        elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            price, size = lvl[0], lvl[1]
        else:
            continue
        try:
            out.append((float(price), float(size)))
        except (TypeError, ValueError):
            continue
    return out


def _book_sides(
    book: dict[str, Any],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Extract (bids, asks) level lists from a PolySim book payload."""
    bids = _to_levels(book.get("bids"))
    asks = _to_levels(book.get("asks"))
    return bids, asks


def _best_bid(bids: list[tuple[float, float]]) -> float | None:
    return max((p for p, _ in bids), default=None)


def _best_ask(asks: list[tuple[float, float]]) -> float | None:
    return min((p for p, _ in asks), default=None)


# ── token-id <-> (market_id, outcome) seam ─────────────────────────────────


def _split_token(token_id: str) -> tuple[str, str]:
    """Map a py-clob ``token_id`` onto PolySim ``(market_id, outcome)``.

    py-clob-client addresses a single outcome token; PolySim addresses a
    market plus an outcome (YES/NO). The parity seam: a bare ``token_id``
    is treated as the market id with outcome ``YES``; append ``":NO"`` /
    ``":YES"`` to target the other outcome explicitly.
    """
    tid = str(token_id)
    if ":" in tid:
        market_id, _, outcome = tid.rpartition(":")
        outcome = outcome.upper()
        if outcome in ("YES", "NO") and market_id:
            return market_id, outcome
    return tid, "YES"


__all__ = [
    "_best_ask",
    "_best_bid",
    "_book_sides",
    "_decode_cursor",
    "_encode_cursor",
    "_next_cursor",
    "_split_token",
    "_to_levels",
]
