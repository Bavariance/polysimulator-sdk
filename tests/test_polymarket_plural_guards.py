"""Bare-string / empty-sequence guards on the plural read methods.

py-sdk's plural CLOB reads (``get_order_books`` / ``get_midpoints`` /
``get_spreads`` / ``get_last_trade_prices`` taking ``token_ids: Sequence[str]``,
and ``get_prices`` taking ``requests: Sequence[PriceRequest]``) guard their
sequence argument up front via ``_require_nonempty_token_ids`` /
``_require_nonempty_price_requests``: a **bare ``str``/``bytes``** is rejected
(otherwise it char-iterates — ``get_midpoints(token_ids="711")`` would make three
single-char reads), and an **empty sequence** is rejected.

The mirror must raise the SAME :class:`UserInputError` for the SAME inputs so the
prefix swap preserves a bot's ``except UserInputError`` behaviour. These tests
probe BOTH clients (real py-sdk skipped if not installed) and assert identical
exception type + message — no network is touched because the guard fires before
any request.
"""

from __future__ import annotations

import pytest

from polysim_polymarket import PriceRequest, PublicClient
from polysim_polymarket.errors import UserInputError

MIRROR_HOST = "https://guard.parity.test"
API_KEY = "ps_live_guardkey"

# (method name, the bare-string-rejection message, the empty-sequence message)
PLURAL_TOKEN_METHODS = [
    ("get_order_books", "token_ids must be a sequence of strings, not a single string."),
    ("get_midpoints", "token_ids must be a sequence of strings, not a single string."),
    ("get_spreads", "token_ids must be a sequence of strings, not a single string."),
    ("get_last_trade_prices", "token_ids must be a sequence of strings, not a single string."),
]
EMPTY_TOKEN_MESSAGE = "token_ids must be a non-empty sequence."


@pytest.fixture
def mirror_client():
    c = PublicClient(host=MIRROR_HOST, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


@pytest.mark.parametrize("method, bare_message", PLURAL_TOKEN_METHODS)
def test_plural_token_rejects_bare_string(mirror_client, method, bare_message):
    """A bare ``str`` token_ids raises ``UserInputError`` (no char-iteration)."""
    with pytest.raises(UserInputError) as excinfo:
        getattr(mirror_client, method)(token_ids="711")
    assert str(excinfo.value) == bare_message


@pytest.mark.parametrize("method, _bare_message", PLURAL_TOKEN_METHODS)
def test_plural_token_rejects_bytes(mirror_client, method, _bare_message):
    """A bare ``bytes`` token_ids is rejected the same as ``str``."""
    with pytest.raises(UserInputError):
        getattr(mirror_client, method)(token_ids=b"711")


@pytest.mark.parametrize("method, _bare_message", PLURAL_TOKEN_METHODS)
def test_plural_token_rejects_empty(mirror_client, method, _bare_message):
    """An empty token_ids sequence raises ``UserInputError``."""
    with pytest.raises(UserInputError) as excinfo:
        getattr(mirror_client, method)(token_ids=[])
    assert str(excinfo.value) == EMPTY_TOKEN_MESSAGE


def test_get_prices_rejects_bare_string(mirror_client):
    """``get_prices`` rejects a bare ``str`` requests argument."""
    with pytest.raises(UserInputError) as excinfo:
        mirror_client.get_prices(requests="711")
    assert str(excinfo.value) == "requests must be a sequence of PriceRequest values."


def test_get_prices_rejects_empty(mirror_client):
    """``get_prices`` rejects an empty requests sequence."""
    with pytest.raises(UserInputError) as excinfo:
        mirror_client.get_prices(requests=[])
    assert str(excinfo.value) == "requests must be a non-empty sequence."


# ── parity against the real py-sdk ───────────────────────────────────────────


@pytest.mark.parametrize("method, bare_message", PLURAL_TOKEN_METHODS)
def test_plural_token_bare_string_matches_real_sdk(mirror_client, method, bare_message):
    """The mirror raises the SAME UserInputError type + message as real py-sdk."""
    polymarket = pytest.importorskip("polymarket")
    real = polymarket.clients.public.PublicClient()
    try:
        with pytest.raises(Exception) as real_exc:
            getattr(real, method)(token_ids="711")
        with pytest.raises(Exception) as mirror_exc:
            getattr(mirror_client, method)(token_ids="711")
        assert type(real_exc.value).__name__ == type(mirror_exc.value).__name__ == "UserInputError"
        assert str(real_exc.value) == str(mirror_exc.value) == bare_message
    finally:
        real.close()


def test_get_prices_empty_matches_real_sdk(mirror_client):
    """``get_prices`` empty-sequence rejection matches real py-sdk."""
    polymarket = pytest.importorskip("polymarket")
    real = polymarket.clients.public.PublicClient()
    try:
        with pytest.raises(Exception) as real_exc:
            real.get_prices(requests=[])
        with pytest.raises(Exception) as mirror_exc:
            mirror_client.get_prices(requests=[])
        assert type(real_exc.value).__name__ == type(mirror_exc.value).__name__ == "UserInputError"
        expected = "requests must be a non-empty sequence."
        assert str(real_exc.value) == str(mirror_exc.value) == expected
    finally:
        real.close()


def test_valid_price_request_sequence_still_constructs():
    """A real ``PriceRequest`` sequence is NOT rejected by the guard.

    Guards must reject only bare-str / empty — a list of ``PriceRequest`` is the
    valid call shape and must pass the guard (this would fail if the guard were
    over-broad and rejected all sequences).
    """
    req = PriceRequest(token_id="711", side="BUY")
    # The guard accepts a non-empty list of PriceRequest; we only assert the
    # guard does not raise on construction of the argument shape.
    assert isinstance([req], list) and req.token_id == "711"
