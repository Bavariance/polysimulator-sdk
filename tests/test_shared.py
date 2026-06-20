"""``polysim_sdk._shared`` — pure helpers extracted from the v1 CLOB mirror.

These cursor + order-book parsing helpers were proven in ``polysim_clob_client``
and are now shared so the ``polysim_polymarket`` mirror reuses the exact same
read-path logic. This suite locks their behaviour at the new home; the v1
parity suite (``test_clob_parity.py``) continues to assert the mirror still
works through the re-import shim.
"""

from __future__ import annotations

from polysim_sdk._shared import (
    _best_ask,
    _best_bid,
    _book_sides,
    _decode_cursor,
    _encode_cursor,
    _next_cursor,
    _split_token,
    _to_levels,
)

# ── cursor <-> offset translation ──────────────────────────────────────────


def test_encode_cursor_base64_mapping():
    # base64("0") == "MA==", base64("100") == "MTAw"
    assert _encode_cursor(0) == "MA=="
    assert _encode_cursor(100) == "MTAw"


def test_decode_cursor_roundtrip_and_sentinels():
    assert _decode_cursor(_encode_cursor(250)) == 250
    # START / empty -> 0; END sentinel -> -1.
    assert _decode_cursor("MA==") == 0
    assert _decode_cursor("") == 0
    assert _decode_cursor(None) == 0
    assert _decode_cursor("LTE=") == -1
    # Garbage decodes to a safe 0 rather than raising.
    assert _decode_cursor("not-base64!!") == 0


def test_next_cursor_terminates_on_short_page():
    # Full page -> advance by limit; short page -> END sentinel.
    assert _next_cursor(0, 100, 100) == _encode_cursor(100)
    assert _next_cursor(0, 7, 100) == "LTE="


# ── order-book parsing ─────────────────────────────────────────────────────


def test_to_levels_tolerates_dict_and_pair_shapes():
    dict_levels = _to_levels([{"price": "0.40", "size": "100"}])
    assert dict_levels == [(0.40, 100.0)]
    # "quantity" is accepted as a size alias.
    assert _to_levels([{"price": "0.5", "quantity": "3"}]) == [(0.5, 3.0)]
    # [price, size] pair form.
    assert _to_levels([["0.6", "50"]]) == [(0.6, 50.0)]
    # Unparseable entries are skipped, not raised.
    assert _to_levels([{"price": "x", "size": "1"}, None, []]) == []
    assert _to_levels(None) == []


def test_book_sides_best_bid_best_ask():
    book = {
        "bids": [{"price": "0.40", "size": "100"}, {"price": "0.42", "size": "10"}],
        "asks": [{"price": "0.60", "size": "50"}, {"price": "0.58", "size": "5"}],
    }
    bids, asks = _book_sides(book)
    assert _best_bid(bids) == 0.42
    assert _best_ask(asks) == 0.58
    # Empty sides -> None, not an error.
    assert _best_bid([]) is None
    assert _best_ask([]) is None


# ── token-id seam ──────────────────────────────────────────────────────────


def test_split_token_parity_seam():
    # A bare token id maps to (market_id, "YES").
    assert _split_token("c1") == ("c1", "YES")
    # The colon form targets the explicit outcome.
    assert _split_token("c1:NO") == ("c1", "NO")
    assert _split_token("c1:yes") == ("c1", "YES")


def test_split_token_resolves_updown_outcomes():
    # UpDown markets carry "Up"/"Down" outcomes, so the colon form must resolve
    # ``condition_id:UP`` / ``:DOWN`` just like ``:YES`` / ``:NO`` (the colon
    # form is the drop-in's own convenience extension, not py-clob parity).
    assert _split_token("0xabc:UP") == ("0xabc", "UP")
    assert _split_token("0xabc:DOWN") == ("0xabc", "DOWN")
    # Case-insensitive, returned uppercase (consistent with YES/NO behaviour).
    assert _split_token("0xabc:up") == ("0xabc", "UP")
    assert _split_token("0xabc:down") == ("0xabc", "DOWN")


def test_split_token_updown_regression_pre_fix_swallowed_outcome():
    # Regression guard for the UpDown 404 bug: before the whitelist was widened
    # to include UP/DOWN, ``_split_token("0xCID:UP")`` fell through and returned
    # the WHOLE ``cid:UP`` string as the market id (outcome "YES"), which made
    # the backend 404 "Market not found: 0xCID:UP". The market id must never
    # carry the colon suffix for a recognised UpDown outcome.
    market_id, outcome = _split_token("0xCID:UP")
    assert market_id == "0xCID"
    assert ":" not in market_id
    assert outcome == "UP"
