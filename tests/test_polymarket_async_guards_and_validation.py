"""Async plural-read guards + input-validation parity for ``AsyncPublicClient``.

The async twin of ``test_polymarket_plural_guards`` + the relevant slices of
``test_polymarket_input_validation``. The guards live in the shared ``_common``
module, so the async client raises the SAME ``UserInputError`` (type + message)
for the SAME bad inputs as the sync client and as the real py-sdk async client.
These tests await the async methods and assert that:

* a bare ``str``/``bytes`` or empty sequence on a plural read raises before any
  network read (no char-iteration into N single-char reads);
* ``side`` is case-SENSITIVE on ``get_price`` / ``estimate_market_price``;
* ``estimate_market_price`` enforces the BUY/SELL amount/shares contract;
* the messages match the real py-sdk async client exactly (skipped if the real
  ``polymarket`` package isn't installed).

No network is touched because every guard fires before the read.
"""

from __future__ import annotations

import pytest

from polysim_polymarket import AsyncPublicClient
from polysim_polymarket.errors import UserInputError

MIRROR_HOST = "https://guard.async.parity.test"
API_KEY = "ps_live_guardkey"

PLURAL_TOKEN_METHODS = [
    ("get_order_books", "token_ids must be a sequence of strings, not a single string."),
    ("get_midpoints", "token_ids must be a sequence of strings, not a single string."),
    ("get_spreads", "token_ids must be a sequence of strings, not a single string."),
    ("get_last_trade_prices", "token_ids must be a sequence of strings, not a single string."),
]
EMPTY_TOKEN_MESSAGE = "token_ids must be a non-empty sequence."


@pytest.fixture
async def mirror_client():
    c = AsyncPublicClient(host=MIRROR_HOST, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    await c.close()


@pytest.mark.parametrize("method, bare_message", PLURAL_TOKEN_METHODS)
async def test_plural_token_rejects_bare_string(mirror_client, method, bare_message):
    with pytest.raises(UserInputError) as excinfo:
        await getattr(mirror_client, method)(token_ids="711")
    assert str(excinfo.value) == bare_message


@pytest.mark.parametrize("method, _bare", PLURAL_TOKEN_METHODS)
async def test_plural_token_rejects_bytes(mirror_client, method, _bare):
    with pytest.raises(UserInputError):
        await getattr(mirror_client, method)(token_ids=b"711")


@pytest.mark.parametrize("method, _bare", PLURAL_TOKEN_METHODS)
async def test_plural_token_rejects_empty(mirror_client, method, _bare):
    with pytest.raises(UserInputError) as excinfo:
        await getattr(mirror_client, method)(token_ids=[])
    assert str(excinfo.value) == EMPTY_TOKEN_MESSAGE


async def test_get_prices_rejects_bare_string(mirror_client):
    with pytest.raises(UserInputError) as excinfo:
        await mirror_client.get_prices(requests="711")
    assert str(excinfo.value) == "requests must be a sequence of PriceRequest values."


async def test_get_prices_rejects_empty(mirror_client):
    with pytest.raises(UserInputError) as excinfo:
        await mirror_client.get_prices(requests=[])
    assert str(excinfo.value) == "requests must be a non-empty sequence."


# ── side validation (case-sensitive) ────────────────────────────────────────


async def test_get_price_rejects_lowercase_side(mirror_client):
    with pytest.raises(UserInputError) as excinfo:
        await mirror_client.get_price(token_id="711", side="buy")  # type: ignore[arg-type]
    assert str(excinfo.value) == "side must be 'BUY' or 'SELL', got 'buy'."


async def test_get_order_book_rejects_empty_token_id(mirror_client):
    with pytest.raises(UserInputError) as excinfo:
        await mirror_client.get_order_book(token_id="")
    assert str(excinfo.value) == "token_id is required"


# ── singular scalar reads: empty / non-string token_id ──────────────────────

# ``get_price`` also takes ``side``; the token guard fires before the side check
# so a valid side is supplied (the empty-token rejection is what's asserted).
_SINGULAR_EMPTY_TOKEN_READS = [
    ("get_midpoint", {}),
    ("get_spread", {}),
    ("get_price", {"side": "BUY"}),
    ("get_last_trade_price", {}),
]


@pytest.mark.parametrize("method, extra", _SINGULAR_EMPTY_TOKEN_READS)
async def test_singular_read_rejects_empty_token_id(mirror_client, method, extra):
    """Each singular scalar read rejects an empty ``token_id`` before any read.

    No respx route is registered, so the clean ``UserInputError`` proves the
    guard fires before the ``await``\\ed HTTP call.
    """
    with pytest.raises(UserInputError) as excinfo:
        await getattr(mirror_client, method)(token_id="", **extra)
    assert str(excinfo.value) == "token_id is required"


@pytest.mark.parametrize("method, extra", _SINGULAR_EMPTY_TOKEN_READS)
async def test_singular_read_rejects_non_string_token_id(mirror_client, method, extra):
    """A non-string ``token_id`` raises py-sdk's type message before any read."""
    with pytest.raises(UserInputError) as excinfo:
        await getattr(mirror_client, method)(token_id=None, **extra)  # type: ignore[arg-type]
    assert str(excinfo.value) == "token_id must be a string, got NoneType."


async def test_get_price_validates_token_id_before_side(mirror_client):
    """``get_price`` validates ``token_id`` BEFORE ``side`` (py-sdk's order)."""
    with pytest.raises(UserInputError) as excinfo:
        await mirror_client.get_price(token_id="", side="bogus")  # type: ignore[arg-type]
    assert str(excinfo.value) == "token_id is required"


# ── estimate_market_price input contract ────────────────────────────────────


async def test_estimate_rejects_buy_with_shares(mirror_client):
    with pytest.raises(UserInputError):
        await mirror_client.estimate_market_price(token_id="711", side="BUY", shares=10)


async def test_estimate_rejects_sell_with_amount(mirror_client):
    with pytest.raises(UserInputError):
        await mirror_client.estimate_market_price(token_id="711", side="SELL", amount=10)


async def test_estimate_rejects_missing_args(mirror_client):
    with pytest.raises(UserInputError):
        await mirror_client.estimate_market_price(token_id="711", side="BUY")
    with pytest.raises(UserInputError):
        await mirror_client.estimate_market_price(token_id="711", side="SELL")


async def test_estimate_rejects_bad_order_type(mirror_client):
    with pytest.raises(UserInputError):
        await mirror_client.estimate_market_price(
            token_id="711", side="BUY", amount=10, order_type="GTC"
        )


async def test_estimate_rejects_nonpositive_amount(mirror_client):
    with pytest.raises(UserInputError):
        await mirror_client.estimate_market_price(token_id="711", side="BUY", amount=0)


async def test_price_history_rejects_bad_interval(mirror_client):
    with pytest.raises(UserInputError):
        await mirror_client.get_price_history(token_id="711", interval="2h")  # type: ignore[arg-type]


async def test_price_history_rejects_negative_start_ts(mirror_client):
    with pytest.raises(UserInputError):
        await mirror_client.get_price_history(token_id="711", start_ts=-1)


# ── parity against the real py-sdk async client ─────────────────────────────


@pytest.mark.parametrize("method, bare_message", PLURAL_TOKEN_METHODS)
async def test_plural_bare_string_matches_real_async_sdk(mirror_client, method, bare_message):
    """The async mirror raises the SAME UserInputError type + message as the real
    async py-sdk client."""
    polymarket = pytest.importorskip("polymarket")
    real = polymarket.clients.async_public.AsyncPublicClient()
    try:
        with pytest.raises(Exception) as real_exc:
            await getattr(real, method)(token_ids="711")
        with pytest.raises(Exception) as mirror_exc:
            await getattr(mirror_client, method)(token_ids="711")
        assert (
            type(real_exc.value).__name__
            == type(mirror_exc.value).__name__
            == "UserInputError"
        )
        assert str(real_exc.value) == str(mirror_exc.value) == bare_message
    finally:
        await real.close()


async def test_get_prices_empty_matches_real_async_sdk(mirror_client):
    polymarket = pytest.importorskip("polymarket")
    real = polymarket.clients.async_public.AsyncPublicClient()
    try:
        with pytest.raises(Exception) as real_exc:
            await real.get_prices(requests=[])
        with pytest.raises(Exception) as mirror_exc:
            await mirror_client.get_prices(requests=[])
        assert (
            type(real_exc.value).__name__
            == type(mirror_exc.value).__name__
            == "UserInputError"
        )
        expected = "requests must be a non-empty sequence."
        assert str(real_exc.value) == str(mirror_exc.value) == expected
    finally:
        await real.close()


async def test_side_message_matches_real_async_sdk(mirror_client):
    """The case-sensitive side rejection message matches the real async py-sdk."""
    polymarket = pytest.importorskip("polymarket")
    real = polymarket.clients.async_public.AsyncPublicClient()
    try:
        with pytest.raises(Exception) as real_exc:
            await real.get_price(token_id="711", side="buy")
        with pytest.raises(Exception) as mirror_exc:
            await mirror_client.get_price(token_id="711", side="buy")  # type: ignore[arg-type]
        assert str(real_exc.value) == str(mirror_exc.value)
    finally:
        await real.close()


@pytest.mark.parametrize("method, extra", _SINGULAR_EMPTY_TOKEN_READS)
async def test_singular_empty_token_matches_real_async_sdk(mirror_client, method, extra):
    """Each singular read's empty-token rejection matches the real async py-sdk.

    The real async client raises the same ``UserInputError("token_id is
    required")`` before any read; assert byte-for-byte parity.
    """
    polymarket = pytest.importorskip("polymarket")
    real = polymarket.clients.async_public.AsyncPublicClient()
    try:
        with pytest.raises(Exception) as real_exc:
            await getattr(real, method)(token_id="", **extra)
        with pytest.raises(Exception) as mirror_exc:
            await getattr(mirror_client, method)(token_id="", **extra)
        assert (
            type(real_exc.value).__name__
            == type(mirror_exc.value).__name__
            == "UserInputError"
        )
        assert str(real_exc.value) == str(mirror_exc.value) == "token_id is required"
    finally:
        await real.close()
