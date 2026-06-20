"""Validation / error-parity tests for ``SecureClient`` TRADING (G3).

Bad order args must raise the SAME error type py-sdk raises (``UserInputError``,
re-exported off the mirror root), with the guard firing BEFORE any network call —
no route is registered, so reaching the ``pytest.raises`` proves zero requests.

py-sdk's market-order arg contract (``_internal.actions.orders.market`` /
``limit``):
  * BUY uses ``amount`` (required), forbids ``shares`` / ``min_price``;
  * SELL uses ``shares`` (required), forbids ``amount`` / ``max_spend`` / ``max_price``;
  * ``side`` must be BUY/SELL; ``order_type`` must be FAK/FOK on market orders;
  * ``price`` / ``size`` / ``amount`` / ``shares`` must be strictly positive.
"""

from __future__ import annotations

import pytest

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"
COLON_TOKEN = "0xcond:YES"


@pytest.fixture
def secure():
    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


# ── limit-order validation ──────────────────────────────────────────────────


def test_limit_order_empty_token_id_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="token_id is required"):
        secure.create_limit_order(token_id="", price="0.5", size="10", side="BUY")


def test_resolve_coordinates_validates_token_before_network(secure):
    """An empty / non-string token_id is rejected BEFORE any reverse-resolution
    network call — no ``/v1/markets-by-token`` route is registered, so reaching
    the raise proves zero requests fired."""
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError):
        secure._resolve_coordinates("")
    with pytest.raises(UserInputError):
        secure._resolve_coordinates(None)  # type: ignore[arg-type]


def test_create_order_bad_token_does_not_hit_network(secure, respx_mock):
    """A bad token must raise before the markets-by-token network call. The route
    is registered but assert_all_called is relaxed by checking it stayed
    uncalled."""
    from polysim_polymarket import UserInputError

    route = respx_mock.get(url__startswith=f"{BASE_URL}/v1/markets-by-token")
    with pytest.raises(UserInputError):
        secure.create_market_order(token_id="", side="BUY", amount="10")
    assert not route.called


def test_limit_order_nonpositive_price_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="price must be a positive number"):
        secure.create_limit_order(token_id=COLON_TOKEN, price="0", size="10", side="BUY")


def test_limit_order_negative_size_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="size must be a positive number"):
        secure.create_limit_order(token_id=COLON_TOKEN, price="0.5", size="-3", side="BUY")


def test_limit_order_bad_side_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="side must be 'BUY' or 'SELL'"):
        secure.create_limit_order(
            token_id=COLON_TOKEN, price="0.5", size="10", side="HODL"  # type: ignore[arg-type]
        )


def test_limit_order_negative_expiration_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="expiration must be a non-negative integer"):
        secure.create_limit_order(
            token_id=COLON_TOKEN, price="0.5", size="10", side="BUY", expiration=-5
        )


# ── market-order validation (py-sdk's side-specific contract) ───────────────


def test_market_buy_requires_amount(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="amount is required for BUY"):
        secure.create_market_order(token_id=COLON_TOKEN, side="BUY")


def test_market_buy_forbids_shares(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="shares must not be set for BUY"):
        secure.create_market_order(token_id=COLON_TOKEN, side="BUY", amount="10", shares="5")


def test_market_buy_forbids_min_price(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="min_price is only valid for SELL"):
        secure.create_market_order(
            token_id=COLON_TOKEN, side="BUY", amount="10", min_price="0.2"
        )


def test_market_sell_requires_shares(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="shares is required for SELL"):
        secure.create_market_order(token_id=COLON_TOKEN, side="SELL")


def test_market_sell_forbids_amount(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="amount must not be set for SELL"):
        secure.create_market_order(token_id=COLON_TOKEN, side="SELL", shares="5", amount="10")


def test_market_sell_forbids_max_price(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="max_price is only valid for BUY"):
        secure.create_market_order(
            token_id=COLON_TOKEN, side="SELL", shares="5", max_price="0.7"
        )


def test_market_order_bad_order_type_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="order_type must be 'FAK' or 'FOK'"):
        secure.create_market_order(
            token_id=COLON_TOKEN, side="BUY", amount="10", order_type="GTC"  # type: ignore[arg-type]
        )


def test_market_order_nonpositive_amount_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="amount must be a positive number"):
        secure.create_market_order(token_id=COLON_TOKEN, side="BUY", amount="0")


# ── cancel validation ───────────────────────────────────────────────────────


def test_cancel_order_empty_id_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError, match="order_id is required"):
        secure.cancel_order(order_id="")


def test_cancel_market_orders_requires_a_filter(secure, respx_mock):
    from polysim_polymarket import UserInputError

    # py-sdk's exact missing-filter message.
    with pytest.raises(
        UserInputError, match=r"^At least one of market or token_id is required\.$"
    ):
        secure.cancel_market_orders()
