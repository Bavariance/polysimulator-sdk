"""Pure Up/Down row helpers: time-to-expiry, window state, price-to-beat math."""

from __future__ import annotations

from datetime import datetime, timezone

from polysim_sdk.updown import (
    ASSETS,
    INTERVALS,
    is_window_open,
    next_to_expire,
    open_windows,
    price_to_beat,
    ptb_distance,
    ptb_distance_bps,
    seconds_to_expiry,
)

# A fixed "now" so the time helpers are deterministic.
NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


# ── constants ────────────────────────────────────────────────────────────────


def test_assets_and_intervals_match_endpoint_vocab():
    assert ASSETS == ("BTC", "ETH", "SOL", "XRP", "SPX", "NDX")
    assert INTERVALS == ("5M", "15M", "1H", "4H", "daily")


# ── seconds_to_expiry ─────────────────────────────────────────────────────────


def test_seconds_to_expiry_future_is_positive():
    assert seconds_to_expiry({"end_date": "2026-06-14T12:05:00Z"}, now=NOW) == 300.0


def test_seconds_to_expiry_past_is_negative():
    assert seconds_to_expiry({"end_date": "2026-06-14T11:55:00Z"}, now=NOW) == -300.0


def test_seconds_to_expiry_naive_timestamp_treated_as_utc():
    # No trailing Z / offset → assume UTC, don't crash.
    assert seconds_to_expiry({"end_date": "2026-06-14T12:05:00"}, now=NOW) == 300.0


def test_seconds_to_expiry_missing_or_unparseable_is_none():
    assert seconds_to_expiry({}, now=NOW) is None
    assert seconds_to_expiry({"end_date": None}, now=NOW) is None
    assert seconds_to_expiry({"end_date": "not-a-date"}, now=NOW) is None


# ── is_window_open ─────────────────────────────────────────────────────────────


def test_is_window_open_true_when_active_and_not_closed_or_resolved():
    assert is_window_open({"active": True, "closed": False, "resolved": False}) is True


def test_is_window_open_false_when_closed_or_resolved_or_inactive():
    assert is_window_open({"active": True, "closed": True, "resolved": False}) is False
    assert is_window_open({"active": True, "closed": False, "resolved": True}) is False
    assert is_window_open({"closed": False, "resolved": False}) is False  # no active


# ── price_to_beat ──────────────────────────────────────────────────────────────


def test_price_to_beat_prefers_event_metadata_over_threshold():
    # event_metadata_ptb wins even when group_item_threshold is the "0" placeholder.
    row = {"event_metadata_ptb": 64500.0, "group_item_threshold": "0"}
    assert price_to_beat(row) == 64500.0


def test_price_to_beat_falls_back_to_threshold():
    row = {"event_metadata_ptb": None, "group_item_threshold": "63000.5"}
    assert price_to_beat(row) == 63000.5


def test_price_to_beat_zero_placeholder_and_missing_are_none():
    assert price_to_beat({"event_metadata_ptb": "0", "group_item_threshold": "0"}) is None
    assert price_to_beat({}) is None
    assert price_to_beat({"group_item_threshold": "abc"}) is None


# ── distance math ──────────────────────────────────────────────────────────────


def test_ptb_distance_signed_and_coerces_strings():
    assert ptb_distance(64600, 64500) == 100.0
    assert ptb_distance(64400, 64500) == -100.0
    assert ptb_distance("64600", "64500") == 100.0


def test_ptb_distance_none_on_missing():
    assert ptb_distance(None, 64500) is None
    assert ptb_distance(64600, None) is None


def test_ptb_distance_bps_of_strike():
    assert ptb_distance_bps(101000, 100000) == 100.0


def test_ptb_distance_bps_none_on_zero_strike_or_missing():
    assert ptb_distance_bps(101000, 0) is None
    assert ptb_distance_bps(None, 100000) is None


# ── list selectors ─────────────────────────────────────────────────────────────


def _row(end, *, active=True, closed=False, resolved=False):
    return {"end_date": end, "active": active, "closed": closed, "resolved": resolved}


def test_open_windows_filters_to_tradeable():
    rows = [
        _row("2026-06-14T12:05:00Z"),
        _row("2026-06-14T12:05:00Z", closed=True),
        _row("2026-06-14T12:05:00Z", resolved=True),
    ]
    assert open_windows(rows) == [rows[0]]


def test_next_to_expire_picks_soonest_open_future():
    soon = _row("2026-06-14T12:02:00Z")
    later = _row("2026-06-14T12:09:00Z")
    expired = _row("2026-06-14T11:50:00Z")
    closed = _row("2026-06-14T12:01:00Z", closed=True)
    assert next_to_expire([later, soon, expired, closed], now=NOW) is soon


def test_next_to_expire_none_when_no_open_future():
    rows = [_row("2026-06-14T11:50:00Z"), _row("2026-06-14T12:05:00Z", closed=True)]
    assert next_to_expire(rows, now=NOW) is None
