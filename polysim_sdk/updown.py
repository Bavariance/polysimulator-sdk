"""Pure helpers for Up/Down market rows from ``GET /v1/markets/updown``.

True to the SDK's non-ORM philosophy these take the plain ``dict`` rows the
server sent and never re-fetch. They tolerate missing or renamed fields by
returning ``None`` / ``False`` rather than raising, so a new server field can't
break a strategy mid-flight.

The fields they read off a row (all optional):

* ``end_date`` — ISO-8601 close time of the window (used for time-to-expiry).
* ``active`` / ``closed`` / ``resolved`` — booleans for window state.
* ``event_metadata_ptb`` — the strike Polymarket's own UI shows; present even
  while ``group_item_threshold`` is still the ``"0"`` placeholder common on a
  live 5M window.
* ``group_item_threshold`` — the per-window strike once resolved (string).

For the freshest per-window strike prefer
:meth:`~polysim_sdk.client.PolySimClient.get_price_to_beat`
(``GET /prices/ptb/{condition_id}``); :func:`price_to_beat` here reflects the
strike only as of the list fetch.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

# The asset / interval vocabulary accepted by ``GET /v1/markets/updown``.
# Exposed for readability and validation — the server is the source of truth.
ASSETS = ("BTC", "ETH", "SOL", "XRP", "SPX", "NDX")
INTERVALS = ("5M", "15M", "1H", "4H", "daily")


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string to an aware UTC datetime, or ``None``.

    Handles the trailing-``Z`` form (``datetime.fromisoformat`` only learned it
    in 3.11; the SDK supports 3.10) and assumes UTC for a naive timestamp.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _coerce_float(value: Any) -> float | None:
    """Best-effort float, or ``None`` (strings, ``None``, junk all tolerated)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def seconds_to_expiry(market: Mapping[str, Any], *, now: datetime | None = None) -> float | None:
    """Seconds until the window's ``end_date``; ``None`` if missing/unparseable.

    Negative once the window has expired. ``now`` defaults to the current UTC
    time; pass an explicit aware ``datetime`` for deterministic tests.
    """
    end = _parse_iso(market.get("end_date"))
    if end is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (end - current).total_seconds()


def is_window_open(market: Mapping[str, Any]) -> bool:
    """``True`` when a row is tradeable: active and neither closed nor resolved.

    Mirrors the server's ``live`` filter on ``GET /v1/markets/updown``. Does not
    consult the clock — use :func:`seconds_to_expiry` for time-to-close.
    """
    return bool(
        market.get("active") and not market.get("closed") and not market.get("resolved")
    )


def price_to_beat(market: Mapping[str, Any]) -> float | None:
    """Numeric resolution strike from a row, or ``None`` if not yet set.

    Prefers ``event_metadata_ptb`` (present even while ``group_item_threshold``
    is the ``"0"`` placeholder), then falls back to ``group_item_threshold``.
    Non-positive / non-numeric values are treated as "not set yet".
    """
    for key in ("event_metadata_ptb", "group_item_threshold"):
        val = _coerce_float(market.get(key))
        if val is not None and val > 0:
            return val
    return None


def ptb_distance(spot: Any, ptb: Any) -> float | None:
    """Signed distance ``spot - ptb`` in price units, or ``None`` if either is missing.

    Positive ⇒ the underlying is above the strike (the "Up" side is in the
    money). ``spot`` is a live tick (e.g. from
    :meth:`~polysim_sdk.client.PolySimClient.get_spot`); ``ptb`` is the strike
    (e.g. from :func:`price_to_beat` or ``get_price_to_beat``). Both inputs are
    scalars — pull ``["price"]`` off the respective payloads yourself.
    """
    s, p = _coerce_float(spot), _coerce_float(ptb)
    if s is None or p is None:
        return None
    return s - p


def ptb_distance_bps(spot: Any, ptb: Any) -> float | None:
    """:func:`ptb_distance` expressed in basis points of the strike.

    ``(spot - ptb) / ptb * 10_000``. ``None`` if either input is missing or the
    strike is zero (no meaningful denominator).
    """
    s, p = _coerce_float(spot), _coerce_float(ptb)
    if s is None or p is None or p == 0:
        return None
    return (s - p) / p * 10_000


def open_windows(markets: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Filter a list of rows to those that are tradeable (see :func:`is_window_open`)."""
    return [m for m in markets if is_window_open(m)]


def next_to_expire(
    markets: Iterable[Mapping[str, Any]], *, now: datetime | None = None
) -> Mapping[str, Any] | None:
    """The open window closing soonest (smallest positive time-to-expiry).

    Returns ``None`` if no row is both open and has a future ``end_date``. Use
    this to pick the active contract out of ``list_updown``'s array for one
    asset+interval (the live 5M usually has exactly one open window).
    """
    best: Mapping[str, Any] | None = None
    best_sec: float | None = None
    for m in markets:
        if not is_window_open(m):
            continue
        sec = seconds_to_expiry(m, now=now)
        if sec is None or sec <= 0:
            continue
        if best_sec is None or sec < best_sec:
            best, best_sec = m, sec
    return best


__all__ = [
    "ASSETS",
    "INTERVALS",
    "seconds_to_expiry",
    "is_window_open",
    "price_to_beat",
    "ptb_distance",
    "ptb_distance_bps",
    "open_windows",
    "next_to_expire",
]
