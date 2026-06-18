"""Input-validation parity with py-sdk (drop-in fidelity).

py-sdk validates call arguments **before** any request and raises
``UserInputError`` (or, for a malformed response, ``UnexpectedResponseError``)
with specific messages. The mirror must match so a ported bot's validation
behaviour is identical after the prefix swap. This suite covers:

* **case-SENSITIVE ``side``** — ``get_price`` / ``get_prices`` /
  ``estimate_market_price`` reject anything that is not exactly ``"BUY"`` /
  ``"SELL"`` (py-sdk does NOT upper-case);
* **empty ``token_id``** — ``get_order_book`` / ``estimate_market_price`` reject
  an empty ``token_id`` with ``UserInputError("token_id is required")``;
* **``get_price_history`` validators** — ``fidelity`` / ``interval`` /
  ``start_ts`` / ``end_ts`` are validated like py-sdk (``UserInputError`` on bad
  values), and a malformed history payload (non-dict, or ``history`` not a list)
  raises ``UnexpectedResponseError`` instead of silently returning ``()``.

Where the real ``polymarket`` package is installed, the side / history-payload
cases also assert byte-for-byte message parity against py-sdk's own validators.
"""

from __future__ import annotations

import httpx
import pytest

from polysim_polymarket import PriceRequest, PublicClient
from polysim_polymarket.errors import UnexpectedResponseError, UserInputError

BASE_URL = "https://validation.polysimulator.test"
API_KEY = "ps_live_validationkey"

# A two-sided book so the side/token_id guards fire BEFORE any liquidity issue.
_BOOK = {
    "market": "0xcond",
    "asset_id": "711",
    "bids": [{"price": "0.40", "size": "100"}],
    "asks": [{"price": "0.60", "size": "100"}],
    "tick_size": "0.01",
    "neg_risk": False,
}


@pytest.fixture
def public_client():
    c = PublicClient(host=BASE_URL, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


def _mock_book(respx_mock):
    return respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(200, json=_BOOK)
    )


# ── (a) case-SENSITIVE side ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad_side", ["buy", "sell", "Buy", "Sell", "bid", "ask", ""])
def test_get_price_rejects_non_uppercase_side(public_client, respx_mock, bad_side):
    """``get_price`` rejects any side that is not exactly ``"BUY"`` / ``"SELL"``."""
    _mock_book(respx_mock)
    with pytest.raises(UserInputError) as excinfo:
        public_client.get_price(token_id="711", side=bad_side)  # type: ignore[arg-type]
    assert str(excinfo.value) == f"side must be 'BUY' or 'SELL', got {bad_side!r}."


@pytest.mark.parametrize("good_side", ["BUY", "SELL"])
def test_get_price_accepts_uppercase_side(public_client, respx_mock, good_side):
    """The exact ``"BUY"`` / ``"SELL"`` sides still resolve a price."""
    _mock_book(respx_mock)
    px = public_client.get_price(token_id="711", side=good_side)
    assert px > 0


def test_get_prices_rejects_non_uppercase_side(public_client, respx_mock):
    """``get_prices`` rejects a lowercase side inside a ``PriceRequest``."""
    _mock_book(respx_mock)
    with pytest.raises(UserInputError) as excinfo:
        public_client.get_prices(requests=[PriceRequest(token_id="711", side="buy")])  # type: ignore[arg-type]
    assert str(excinfo.value) == "side must be 'BUY' or 'SELL', got 'buy'."


def test_estimate_rejects_non_uppercase_side(public_client, respx_mock):
    """``estimate_market_price`` rejects a lowercase side."""
    _mock_book(respx_mock)
    with pytest.raises(UserInputError) as excinfo:
        public_client.estimate_market_price(token_id="711", side="buy", amount=10)  # type: ignore[arg-type]
    assert str(excinfo.value) == "side must be 'BUY' or 'SELL', got 'buy'."


# ── (b) empty token_id ───────────────────────────────────────────────────────


def test_get_order_book_rejects_empty_token_id(public_client):
    """``get_order_book`` rejects an empty ``token_id`` (py-sdk's guard)."""
    with pytest.raises(UserInputError) as excinfo:
        public_client.get_order_book(token_id="")
    assert str(excinfo.value) == "token_id is required"


def test_estimate_rejects_empty_token_id(public_client):
    """``estimate_market_price`` rejects an empty ``token_id``."""
    with pytest.raises(UserInputError) as excinfo:
        public_client.estimate_market_price(token_id="", side="BUY", amount=10)
    assert str(excinfo.value) == "token_id is required"


# The four SINGULAR scalar reads py-sdk guards with the same token_id check.
# ``get_price`` also takes a ``side``; the guard fires BEFORE the side check so
# a valid side is supplied here (the empty-token rejection is what's asserted).
_SINGULAR_EMPTY_TOKEN_READS = [
    ("get_midpoint", {}),
    ("get_spread", {}),
    ("get_price", {"side": "BUY"}),
    ("get_last_trade_price", {}),
]


@pytest.mark.parametrize("method, extra", _SINGULAR_EMPTY_TOKEN_READS)
def test_singular_read_rejects_empty_token_id(public_client, method, extra):
    """Each singular scalar read rejects an empty ``token_id`` before any read.

    No respx route is registered, so if the guard were absent the call would
    raise a transport error (or hit the network) instead of the clean
    ``UserInputError`` — proving the guard fires before the HTTP call.
    """
    with pytest.raises(UserInputError) as excinfo:
        getattr(public_client, method)(token_id="", **extra)
    assert str(excinfo.value) == "token_id is required"


@pytest.mark.parametrize("method, extra", _SINGULAR_EMPTY_TOKEN_READS)
def test_singular_read_rejects_non_string_token_id(public_client, method, extra):
    """A non-string ``token_id`` raises py-sdk's type message before any read."""
    with pytest.raises(UserInputError) as excinfo:
        getattr(public_client, method)(token_id=None, **extra)  # type: ignore[arg-type]
    assert str(excinfo.value) == "token_id must be a string, got NoneType."


def test_get_price_validates_token_id_before_side(public_client):
    """``get_price`` validates ``token_id`` BEFORE ``side`` (py-sdk's order).

    An empty ``token_id`` combined with an invalid ``side`` must surface the
    token_id error, not the side error — matching py-sdk's ``build_price_request``
    which checks the token first.
    """
    with pytest.raises(UserInputError) as excinfo:
        public_client.get_price(token_id="", side="bogus")  # type: ignore[arg-type]
    assert str(excinfo.value) == "token_id is required"


# ── (c) get_price_history validators ─────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"fidelity": 0}, "fidelity must be a positive integer."),
        ({"fidelity": -1}, "fidelity must be a positive integer."),
        (
            {"interval": "bad"},
            "interval must be one of ['1d', '1h', '1w', '6h', 'max'], got 'bad'.",
        ),
        ({"start_ts": -1}, "start_ts must be a non-negative integer."),
        ({"end_ts": -5}, "end_ts must be a non-negative integer."),
    ],
)
def test_get_price_history_validates_params(public_client, respx_mock, kwargs, message):
    """Bad ``fidelity`` / ``interval`` / ``start_ts`` / ``end_ts`` raise UserInputError."""
    # Mock the endpoint so that, were the guard absent, the call would 200 — the
    # guard must fire before any request.
    respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(200, json={"history": []})
    )
    with pytest.raises(UserInputError) as excinfo:
        public_client.get_price_history(token_id="711", **kwargs)
    assert str(excinfo.value) == message


def test_get_price_history_rejects_empty_token_id(public_client):
    """An empty ``token_id`` is rejected before any request."""
    with pytest.raises(UserInputError) as excinfo:
        public_client.get_price_history(token_id="")
    assert str(excinfo.value) == "token_id is required"


@pytest.mark.parametrize(
    "payload",
    [[], "x", 5, {"history": "notalist"}, {"history": 5}, {"nohistory": 1}],
)
def test_get_price_history_malformed_payload_raises(public_client, respx_mock, payload):
    """A malformed history payload raises UnexpectedResponseError, not ``()``.

    py-sdk's ``parse_price_history`` raises ``UnexpectedResponseError`` when the
    payload is not a dict, or its ``history`` is not a list. The mirror used to
    silently return ``()`` — match py-sdk instead.
    """
    respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with pytest.raises(UnexpectedResponseError) as excinfo:
        public_client.get_price_history(token_id="711")
    assert str(excinfo.value) == "price history response did not match expected shape"


def test_get_price_history_empty_history_is_empty_tuple(public_client, respx_mock):
    """A well-formed empty history (``{"history": []}``) still returns ``()``."""
    respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(200, json={"history": []})
    )
    assert public_client.get_price_history(token_id="711") == ()


# ── parity probes against the real py-sdk ────────────────────────────────────


@pytest.mark.parametrize("bad_side", ["buy", "Buy", "sell", "foo"])
def test_side_validation_matches_real_sdk(bad_side):
    """The mirror's side message matches py-sdk's ``_validate_side`` exactly."""
    pytest.importorskip("polymarket")
    from polymarket._internal.actions import clob as real_clob

    with pytest.raises(Exception) as real_exc:
        real_clob.build_price_request(token_id="711", side=bad_side)

    client = PublicClient(host=BASE_URL, api_key=API_KEY)
    client._client._transport._floor_interval = 0.0
    try:
        import respx as _respx

        with _respx.mock:
            _respx.get(f"{BASE_URL}/v1/book").mock(
                return_value=httpx.Response(200, json=_BOOK)
            )
            with pytest.raises(Exception) as mirror_exc:
                client.get_price(token_id="711", side=bad_side)  # type: ignore[arg-type]
    finally:
        client.close()

    assert type(real_exc.value).__name__ == type(mirror_exc.value).__name__ == "UserInputError"
    assert str(real_exc.value) == str(mirror_exc.value)


# Each mirror read paired with the py-sdk ``build_*_request`` that runs the same
# token guard. ``get_price`` also takes a ``side`` (a valid one, so the token
# guard — not the side guard — is exercised).
_REAL_SINGULAR_BUILDERS = [
    ("get_midpoint", "build_midpoint_request", {}),
    ("get_spread", "build_spread_request", {}),
    ("get_price", "build_price_request", {"side": "BUY"}),
    ("get_last_trade_price", "build_last_trade_price_request", {}),
]


@pytest.mark.parametrize("method, builder, extra", _REAL_SINGULAR_BUILDERS)
def test_singular_empty_token_matches_real_sdk(method, builder, extra):
    """The mirror's empty-token message matches py-sdk's request builders exactly.

    py-sdk runs the same token guard for each singular read inside its
    ``build_*_request``; assert the mirror's ``require_nonempty_token_id`` raises
    a byte-identical ``UserInputError``.
    """
    pytest.importorskip("polymarket")
    from polymarket._internal.actions import clob as real_clob

    with pytest.raises(Exception) as real_exc:
        getattr(real_clob, builder)(token_id="", **extra)

    client = PublicClient(host=BASE_URL, api_key=API_KEY)
    client._client._transport._floor_interval = 0.0
    try:
        with pytest.raises(Exception) as mirror_exc:
            getattr(client, method)(token_id="", **extra)
    finally:
        client.close()

    assert (
        type(real_exc.value).__name__ == type(mirror_exc.value).__name__ == "UserInputError"
    )
    assert str(real_exc.value) == str(mirror_exc.value) == "token_id is required"


@pytest.mark.parametrize("payload", [[], "x", {"history": "notalist"}, {"nohistory": 1}])
def test_price_history_malformed_matches_real_sdk(payload):
    """The malformed-history error message matches py-sdk's ``parse_price_history``."""
    pytest.importorskip("polymarket")
    from polymarket._internal.actions import clob as real_clob

    with pytest.raises(Exception) as real_exc:
        real_clob.parse_price_history(payload)

    client = PublicClient(host=BASE_URL, api_key=API_KEY)
    client._client._transport._floor_interval = 0.0
    try:
        import respx as _respx

        with _respx.mock:
            _respx.get(f"{BASE_URL}/v1/prices-history").mock(
                return_value=httpx.Response(200, json=payload)
            )
            with pytest.raises(Exception) as mirror_exc:
                client.get_price_history(token_id="711")
    finally:
        client.close()

    assert (
        type(real_exc.value).__name__
        == type(mirror_exc.value).__name__
        == "UnexpectedResponseError"
    )
    assert str(real_exc.value) == str(mirror_exc.value)
