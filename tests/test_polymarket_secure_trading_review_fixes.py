"""G3 review-fix tests for ``SecureClient`` TRADING.

These pin the exact py-sdk error messages + behaviours the G3 review flagged:

* the limit-order ``expiration`` future-buffer guard (≥ now+60s, py-sdk's exact
  message);
* the market-BUY ``max_spend`` spend-ceiling clamp (``min(amount, max_spend)`` on
  paper, where there are no fees);
* the trade-path numeric/side validation now raising py-sdk's EXACT messages
  (the ``_trade`` coerce/side twins were deleted in favour of the parity-exact
  ``_common`` helpers — this pins the messages so they can't drift back);
* the ``cancel_orders`` all-or-nothing up-front validation;
* the ``cancel_market_orders`` missing-filter message wording.

Every test asserts parity with the real ``polymarket`` package where it pins an
exact message — ``polymarket`` is loaded (skipped if absent) and its own
validator is invoked so the mirror's text can never silently drift from py-sdk's.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"
COLON_TOKEN = "0xcond:YES"

# Load the real py-sdk so the exact-message assertions diff against IT, not a
# hand-copied string. Skipped (whole module) if the package is absent.
polymarket = pytest.importorskip("polymarket")


@pytest.fixture
def secure():
    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


def _pysdk_error_text(call) -> str:
    """Invoke a py-sdk validator expected to raise ``UserInputError`` and return its text."""
    from polymarket.errors import UserInputError

    with pytest.raises(UserInputError) as excinfo:
        call()
    return str(excinfo.value)


# ── Finding 1: expiration future-buffer guard (py-sdk's exact rule + message) ─


def test_pysdk_expiration_message_is_what_we_pin():
    """Document the exact py-sdk message this finding mirrors (parity anchor)."""
    from polymarket._internal.actions.orders.limit import validate_limit_order_params

    # A sub-60s-in-the-future (but non-negative) expiration is rejected by py-sdk
    # with this precise message; we mirror it byte-for-byte below.
    text = _pysdk_error_text(
        lambda: validate_limit_order_params(
            token_id="711", price="0.5", size="10", side="BUY", expiration=1
        )
    )
    assert text == "expiration must be at least 60 seconds in the future."


def test_limit_order_subminute_future_expiration_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    soon = int(time.time()) + 30  # 30s out — under the 60s buffer
    with pytest.raises(
        UserInputError, match=r"^expiration must be at least 60 seconds in the future\.$"
    ):
        secure.create_limit_order(
            token_id=COLON_TOKEN, price="0.5", size="10", side="BUY", expiration=soon
        )


def test_limit_order_past_timestamp_expiration_raises(secure, respx_mock):
    from polysim_polymarket import UserInputError

    past = int(time.time()) - 10  # already in the past, but non-negative
    with pytest.raises(
        UserInputError, match=r"^expiration must be at least 60 seconds in the future\.$"
    ):
        secure.create_limit_order(
            token_id=COLON_TOKEN, price="0.5", size="10", side="BUY", expiration=past
        )


def test_limit_order_valid_future_expiration_accepted(secure, respx_mock):
    # Comfortably beyond now+60s — builds a GTD order carrying the timestamp.
    valid = int(time.time()) + 3600
    order = secure.create_limit_order(
        token_id=COLON_TOKEN, price="0.5", size="10", side="BUY", expiration=valid
    )
    assert order.order_type == "GTD"
    assert order.expiration == valid
    assert order.paper_body["expiration"] == valid


def test_limit_order_negative_expiration_still_non_negative_message(secure, respx_mock):
    # The earlier "< 0" guard keeps its own (distinct) message — the future-buffer
    # check is layered AFTER it, exactly as py-sdk orders the two.
    from polysim_polymarket import UserInputError

    with pytest.raises(
        UserInputError, match=r"^expiration must be a non-negative integer\.$"
    ):
        secure.create_limit_order(
            token_id=COLON_TOKEN, price="0.5", size="10", side="BUY", expiration=-5
        )


def test_expiration_buffer_matches_pysdk_constant():
    """The 60s buffer is py-sdk's, not a hard-coded guess."""
    from polymarket._internal.actions.orders.limit import _MIN_EXPIRATION_BUFFER_S

    assert _MIN_EXPIRATION_BUFFER_S == 60


# ── Finding 2: max_spend spend-ceiling clamp on market BUY ───────────────────


def test_market_buy_max_spend_below_amount_clamps_submitted_amount(secure, respx_mock):
    # On paper there are no fees, so py-sdk's adjust_buy_amount_for_fees collapses
    # to min(amount, max_spend): max_spend=12 < amount=50 -> submit amount=12.
    order = secure.create_market_order(
        token_id=COLON_TOKEN, side="BUY", amount="50", max_spend="12"
    )
    assert order.paper_body["amount"] == 12.0


def test_market_buy_max_spend_above_amount_leaves_amount(secure, respx_mock):
    # max_spend=80 >= amount=50 -> no clamp, submit the full amount.
    order = secure.create_market_order(
        token_id=COLON_TOKEN, side="BUY", amount="50", max_spend="80"
    )
    assert order.paper_body["amount"] == 50.0


def test_market_buy_max_spend_equal_amount_leaves_amount(secure, respx_mock):
    # Boundary: max_spend == amount -> min is amount (no change).
    order = secure.create_market_order(
        token_id=COLON_TOKEN, side="BUY", amount="50", max_spend="50"
    )
    assert order.paper_body["amount"] == 50.0


def test_market_buy_clamp_matches_pysdk_no_fee_semantics():
    """Cross-check: py-sdk's adjust_buy_amount_for_fees with a zero-fee profile == min()."""
    from decimal import Decimal

    from polymarket._internal.actions.orders.market import adjust_buy_amount_for_fees
    from polymarket._internal.actions.orders.market_data import PlatformFeeInfo

    zero_fee = PlatformFeeInfo(rate=Decimal(0), exponent=Decimal(0))
    # max_spend below amount -> returns max_spend
    assert adjust_buy_amount_for_fees(
        amount=Decimal(50), price=Decimal("0.5"), max_spend=Decimal(12), fee=zero_fee
    ) == Decimal(12)
    # max_spend above amount -> returns amount
    assert adjust_buy_amount_for_fees(
        amount=Decimal(50), price=Decimal("0.5"), max_spend=Decimal(80), fee=zero_fee
    ) == Decimal(50)


# ── Finding 3: trade-path validation raises py-sdk's EXACT messages ──────────


def test_trade_path_nonpositive_price_exact_message(secure, respx_mock):
    from polysim_polymarket import UserInputError

    expected = _pysdk_error_text(
        lambda: polymarket._internal.actions.orders._numeric.coerce_positive_decimal(
            "price", "0"
        )
    )
    assert expected == "price must be a positive number."
    with pytest.raises(UserInputError) as excinfo:
        secure.create_limit_order(token_id=COLON_TOKEN, price="0", size="10", side="BUY")
    assert str(excinfo.value) == expected


def test_trade_path_bad_decimal_string_exact_message(secure, respx_mock):
    from polysim_polymarket import UserInputError

    expected = _pysdk_error_text(
        lambda: polymarket._internal.actions.orders._numeric.coerce_positive_decimal(
            "size", "abc"
        )
    )
    assert expected == "size must be a valid decimal number: 'abc'"
    with pytest.raises(UserInputError) as excinfo:
        secure.create_limit_order(token_id=COLON_TOKEN, price="0.5", size="abc", side="BUY")
    assert str(excinfo.value) == expected


def test_trade_path_bad_side_exact_message(secure, respx_mock):
    from polysim_polymarket import UserInputError

    # py-sdk's market/limit validators raise this exact text for a bad side.
    expected = "side must be 'BUY' or 'SELL', got 'HODL'."
    with pytest.raises(UserInputError) as excinfo:
        secure.create_limit_order(
            token_id=COLON_TOKEN, price="0.5", size="10", side="HODL"  # type: ignore[arg-type]
        )
    assert str(excinfo.value) == expected


def test_trade_coerce_and_validate_side_twins_are_gone():
    """The duplicated helpers were deleted from _trade and dropped from __all__."""
    from polysim_polymarket.clients import _common, _trade

    assert not hasattr(_trade, "_validate_side")
    assert "coerce_positive_decimal" not in _trade.__all__
    # _trade now reuses the parity-exact _common copy.
    assert _trade.coerce_positive_decimal is _common.coerce_positive_decimal


# ── Finding 5: cancel_orders all-or-nothing up-front validation ──────────────


def test_cancel_orders_invalid_id_raises_before_any_network(secure, respx_mock):
    # respx_mock with no route raises on ANY request, so reaching pytest.raises
    # proves no cancel fired for the valid id before the invalid one was rejected.
    # py-sdk's plural path uses the field name "order id" (with a space).
    from polysim_polymarket import UserInputError

    expected = _pysdk_error_text(
        lambda: polymarket._internal.actions.orders.cancel.build_cancel_orders_request(
            order_ids=["good_1", ""]
        )
    )
    assert expected == "order id is required"
    with pytest.raises(UserInputError) as excinfo:
        secure.cancel_orders(order_ids=["good_1", ""])
    assert str(excinfo.value) == expected
    assert respx_mock.calls.call_count == 0


def test_cancel_orders_bare_string_rejected(secure, respx_mock):
    from polysim_polymarket import UserInputError

    # A bare string would char-iterate ("ab" -> "a","b"); reject it up front with
    # py-sdk's exact message.
    expected = _pysdk_error_text(
        lambda: polymarket._internal.actions.orders.cancel.build_cancel_orders_request(
            order_ids="abc"
        )
    )
    assert expected == "order_ids must be a sequence of strings, not a single string."
    with pytest.raises(UserInputError) as excinfo:
        secure.cancel_orders(order_ids="abc")  # type: ignore[arg-type]
    assert str(excinfo.value) == expected
    assert respx_mock.calls.call_count == 0


def test_cancel_orders_empty_sequence_rejected(secure, respx_mock):
    from polysim_polymarket import UserInputError

    expected = _pysdk_error_text(
        lambda: polymarket._internal.actions.orders.cancel.build_cancel_orders_request(
            order_ids=[]
        )
    )
    assert expected == "order_ids must be a non-empty sequence."
    with pytest.raises(UserInputError) as excinfo:
        secure.cancel_orders(order_ids=[])
    assert str(excinfo.value) == expected
    assert respx_mock.calls.call_count == 0


def test_cancel_orders_all_valid_ids_still_loop(secure, respx_mock):
    # Sanity: a fully-valid list still cancels each id (pre-validation is additive).
    r1 = respx_mock.delete(f"{BASE_URL}/v1/orders/a").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    r2 = respx_mock.delete(f"{BASE_URL}/v1/orders/b").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    resp = secure.cancel_orders(order_ids=["a", "b"])
    assert r1.called and r2.called
    assert resp.canceled == ("a", "b")


# ── Finding 6: post_order/post_orders share one body->kwargs projection ──────


def test_paper_order_kwargs_projects_limit_body():
    from polysim_polymarket.clients import _trade

    body = {
        "market_id": "0xcond",
        "outcome": "YES",
        "side": "BUY",
        "price": 0.55,
        "order_type": "limit",
        "time_in_force": "GTC",
        "quantity": 10.0,
    }
    kwargs = _trade.paper_order_kwargs(body)
    assert kwargs["market_id"] == "0xcond"
    assert kwargs["side"] == "BUY"
    assert kwargs["outcome"] == "YES"
    assert kwargs["order_type"] == "limit"
    assert kwargs["time_in_force"] == "GTC"
    assert kwargs["post_only"] is False
    assert kwargs["price"] == 0.55
    assert kwargs["quantity"] == 10.0
    # absent optionals are OMITTED (never sent as None) so the wire matches the body
    assert "amount" not in kwargs
    assert "expiration" not in kwargs


def test_paper_order_kwargs_projects_market_buy_body():
    from polysim_polymarket.clients import _trade

    body = {
        "market_id": "0xcond",
        "outcome": "YES",
        "side": "BUY",
        "order_type": "market",
        "time_in_force": "FAK",
        "amount": 20.0,
        "price": 0.99,
    }
    kwargs = _trade.paper_order_kwargs(body)
    assert kwargs["amount"] == 20.0
    assert kwargs["price"] == 0.99
    assert "quantity" not in kwargs


def test_post_order_and_post_orders_share_projection(secure, respx_mock):
    # post_order routes through paper_order_kwargs; post_orders builds each batch
    # entry from the SAME projection. Pin that a batch entry carries the projected
    # keys (post_only made explicit, absent optionals omitted).
    route = respx_mock.post(f"{BASE_URL}/v1/orders/batch").mock(
        return_value=httpx.Response(200, json={"orders": [{"order_id": "a", "status": "live"}]})
    )
    o1 = secure.create_limit_order(token_id=COLON_TOKEN, price="0.5", size="10", side="BUY")
    secure.post_orders([o1])
    sent = json.loads(route.calls.last.request.content)
    entry = sent["orders"][0]
    assert entry["market_id"] == "0xcond"
    assert entry["side"] == "BUY"
    assert entry["price"] == 0.5
    assert entry["quantity"] == 10.0
    assert entry["order_type"] == "limit"
    assert entry["post_only"] is False
    assert "amount" not in entry


# ── Finding 7: cancel_market_orders missing-filter message wording ───────────


def test_cancel_market_orders_missing_filter_exact_message(secure, respx_mock):
    from polysim_polymarket import UserInputError

    expected = _pysdk_error_text(
        lambda: polymarket._internal.actions.orders.cancel.build_cancel_market_orders_request()
    )
    assert expected == "At least one of market or token_id is required."
    with pytest.raises(UserInputError) as excinfo:
        secure.cancel_market_orders()
    assert str(excinfo.value) == expected
