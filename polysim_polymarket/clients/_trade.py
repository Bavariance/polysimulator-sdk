"""Pure, transport-free TRADING logic for the secure client (G3).

These free functions hold everything the sync and async secure clients need to
turn a flat-keyword trading call into (a) a paper-native order body the backend
accepts, (b) a :class:`~polysim_polymarket.models.SignedOrder` whose
trading-semantic fields are set and whose signing fields are inert placeholders,
and (c) the py-sdk-shaped :class:`~polysim_polymarket.models.OrderResponse` /
:class:`~polysim_polymarket.models.CancelOrdersResponse` adaptation of the
backend's reply. Keeping them here — as functions of their arguments, never
re-implemented per client — means the sync ``SecureClient`` (G3) and the async
``AsyncSecureClient`` (G5) share ONE copy, exactly as the read surface shares
:mod:`polysim_polymarket.clients._common` and the account surface shares
:mod:`polysim_polymarket.clients._account`.

**Signing is inert.** PolySimulator is paper trading — no chain, no
``eth_account``/``web3``, no private key. The build functions produce a
``SignedOrder`` with ``token_id`` / ``side`` / ``order_type`` (and the size/price
the paper backend needs) set, and the on-chain settlement fields
(``signature`` / ``signer`` / ``salt`` / ``maker_amount`` / ``taker_amount`` / …)
left at empty/zero placeholders. The order's *paper body* — the unsigned dict the
backend ``POST /v1/orders`` accepts — rides along on a private attr so
:func:`post_order` submits exactly what the build path computed.

**Worst-acceptable price is mandatory on market orders.** PolySimulator follows
Polymarket's "marketable limit" model: every market order is a limit order with
FAK/FOK time-in-force at a worst-price cap. We NEVER send an uncapped market
order — when the caller gives no ``max_price`` (BUY) / ``min_price`` (SELL) we
default the cap to the v1 mirror's :data:`DEFAULT_BUY_WORST_PRICE` (0.99) /
:data:`DEFAULT_SELL_WORST_PRICE` (0.01) so a FOK can still fill at any reasonable
price.
"""

from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

from polysim_polymarket.clients._common import coerce_positive_decimal, validate_side
from polysim_polymarket.errors import UnexpectedResponseError, UserInputError
from polysim_polymarket.models import (
    AcceptedOrder,
    CancelOrdersResponse,
    OrderResponse,
    OrderResponseErrorCode,
    OrderSide,
    RejectedOrder,
    SignedOrder,
)
from polysim_sdk._shared import _split_token

# The worst-acceptable price the FOK/FAK cap defaults to when the caller gives
# none — mirrors the v1 mirror's ``create_market_order`` defaults (0.99 BUY /
# 0.01 SELL), so a market order is NEVER sent uncapped to the backend.
DEFAULT_BUY_WORST_PRICE = Decimal("0.99")
DEFAULT_SELL_WORST_PRICE = Decimal("0.01")

_VALID_MARKET_ORDER_TYPES = frozenset({"FAK", "FOK"})

# py-sdk requires a GTD ``expiration`` to be an ABSOLUTE Unix timestamp at least
# this many seconds in the future (``limit._MIN_EXPIRATION_BUFFER_S``); a sub-60s
# or past timestamp is rejected before any request. We mirror the constant + rule
# + message so a ported bot's bad-expiration call raises identically.
_MIN_EXPIRATION_BUFFER_S = 60

# A long all-digit token id is a real Polymarket CLOB outcome-token id (not a
# PolySim condition id); it needs reverse-resolution via the network. Matches the
# v1 mirror's ``_TOKEN_ID_MIN_DIGITS`` threshold.
TOKEN_ID_MIN_DIGITS = 30


# ── token-id -> (market_id, outcome) resolution (parity with v1 mirror) ──────


def split_token_local(token_id: str) -> tuple[str, str]:
    """Resolve a token id to ``(market_id, outcome)`` with NO network call.

    Reuses the shared :func:`polysim_sdk._shared._split_token` parity seam: a bare
    token id is the market id with outcome ``YES``; the ``condition_id:NO`` /
    ``:YES`` colon form targets the other outcome explicitly.
    """
    return _split_token(token_id)


def needs_token_reverse_resolution(token_id: str) -> bool:
    """Whether ``token_id`` is a real CLOB outcome id needing a network resolve.

    A long all-digit id (no colon) is a genuine Polymarket outcome-token id, which
    must be reverse-resolved via ``GET /v1/markets-by-token/{id}`` to find its
    market + outcome. The colon form and short/non-numeric ids resolve locally.
    """
    tid = str(token_id)
    return ":" not in tid and tid.isdigit() and len(tid) >= TOKEN_ID_MIN_DIGITS


def coordinates_from_market_payload(token_id: str, market: Any) -> tuple[str, str]:
    """Project a ``GET /v1/markets-by-token`` payload onto ``(market_id, outcome)``.

    Takes the resolved condition/market id + outcome from the reverse-resolution
    payload, falling back to the raw token id + ``YES`` when a field is absent —
    matching the v1 mirror's ``_resolve_token`` projection.
    """
    if not isinstance(market, dict):
        return token_id, "YES"
    market_id = str(market.get("condition_id") or market.get("market_id") or token_id)
    outcome = str(market.get("outcome") or "YES")
    return market_id, outcome


# Backend rejection -> py-sdk OrderResponseErrorCode mapping. The paper backend
# surfaces a coarse status string; we project it onto py-sdk's closed code set so
# a ported bot's ``resp.code`` narrows identically. Anything unrecognised is
# "unknown" (py-sdk's catch-all).
_STATUS_TO_ERROR_CODE: dict[str, OrderResponseErrorCode] = {
    "unmatched": "unmatched",
    "not_enough_balance": "not_enough_balance",
    "insufficient_balance": "not_enough_balance",
    "market_not_ready": "market_not_ready",
    "invalid_nonce": "invalid_nonce",
    "invalid_expiration": "invalid_expiration",
    "post_only_would_cross": "post_only_would_cross",
    "fok_not_filled": "fok_not_filled",
    "fak_not_filled": "fak_not_filled",
}

# Posted-and-accepted statuses, projected onto py-sdk's OrderPostStatus. PolySim
# fills synchronously, so "FILLED"/"MATCHED" map to "matched"; a resting order is
# "live"; a queued/delayed one is "delayed".
_ACCEPTED_STATUS_MAP: dict[str, str] = {
    "filled": "matched",
    "matched": "matched",
    "partially_filled": "matched",
    "live": "live",
    "open": "live",
    "resting": "live",
    "pending": "delayed",
    "delayed": "delayed",
    "queued": "delayed",
}

# Coarse backend rejection statuses with no finer py-sdk error code. ``/v1/orders``
# (and the per-row ``/v1/orders/batch`` entries) can return HTTP 200 with a row
# whose status is ``REJECTED`` / ``ERROR`` / ``FAILED`` / ``CANCELLED``. These map
# to a RejectedOrder with the "unknown" catch-all code (the more specific statuses
# in ``_STATUS_TO_ERROR_CODE`` keep their codes). Kept for naming clarity even
# though acceptance is now decided by the allowlist below, not this denylist —
# any status outside the accepted set is rejected regardless.
_REJECTED_STATUSES: frozenset[str] = frozenset(
    {"rejected", "error", "failed", "cancelled", "canceled"}
)

# Acceptance ALLOWLIST — mirrors py-sdk's ``order_response._is_accepted``. The real
# py-sdk accepts a row ONLY when ``status in {live, matched, delayed}`` AND
# ``success`` is truthy AND ``order_id != ''`` AND ``error_msg == ''``. We project
# the paper backend's wider status vocabulary onto those three post-statuses via
# ``_ACCEPTED_STATUS_MAP``, so the mirror's accepted set is exactly the keys of
# that map. An UNKNOWN / unexpected status is therefore NOT accepted (it falls
# through to RejectedOrder), matching py-sdk's strict allowlist rather than the
# previous denylist (which mistook an unrecognised status for ``matched``).
_ACCEPTED_STATUSES: frozenset[str] = frozenset(_ACCEPTED_STATUS_MAP)


# ── numeric / side validation ────────────────────────────────────────────────
# ``coerce_positive_decimal`` + ``validate_side`` are NOT re-implemented here:
# they are imported from :mod:`polysim_polymarket.clients._common` (the read
# surface's parity-exact copies). The earlier ``_trade``-local twins drifted from
# py-sdk's error text — the ``_common`` copies match py-sdk's
# ``_numeric.coerce_positive_decimal`` / ``_validate_side`` byte-for-byte, so the
# trade path now raises py-sdk's EXACT messages. No import cycle: ``_common`` does
# not import ``_trade``.


def validate_token_id(token_id: object) -> str:
    """Reject a non-string / empty ``token_id`` like py-sdk's token guard.

    A non-``str`` raises ``"token_id must be a string, got <type>."`` and an
    empty string raises ``"token_id is required"``. Public so the secure client
    can validate a token UP FRONT — before any reverse-resolution network call —
    rather than only inside ``build_*_order`` after resolution.
    """
    if not isinstance(token_id, str):
        raise UserInputError(f"token_id must be a string, got {type(token_id).__name__}.")
    if not token_id:
        raise UserInputError("token_id is required")
    return token_id


def validate_cancel_order_ids(order_ids: Any) -> list[str]:
    """Validate a plural-cancel ``order_ids`` UP FRONT, mirroring py-sdk exactly.

    Mirrors py-sdk's ``build_cancel_orders_request``: a bare ``str``/``bytes`` (it
    would char-iterate) and an empty sequence are both rejected, then EVERY id is
    required to be a non-empty string. This is all-or-nothing pre-validation — an
    invalid id raises ``UserInputError`` before the caller fires any network
    cancel, so a bad id never leaves a partial cancel behind. Field name is
    py-sdk's ``"order id"`` (with a space) for the per-item message.
    """
    if isinstance(order_ids, (str, bytes)):
        raise UserInputError(
            "order_ids must be a sequence of strings, not a single string."
        )
    items = list(order_ids)
    if not items:
        raise UserInputError("order_ids must be a non-empty sequence.")
    validated: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise UserInputError(f"order id must be a string, got {type(item).__name__}.")
        if not item:
            raise UserInputError("order id is required")
        validated.append(item)
    return validated


# ── limit-order build (validate -> paper body -> inert SignedOrder) ──────────


def build_limit_order(
    *,
    token_id: str,
    price: Decimal | int | float | str,
    size: Decimal | int | float | str,
    side: OrderSide,
    post_only: bool = False,
    expiration: int | None = None,
    builder_code: str | None = None,
    market_id: str,
    outcome: str,
) -> SignedOrder:
    """Validate a limit-order call and build an INERT-signed ``SignedOrder``.

    ``market_id`` / ``outcome`` are the resolved PolySim coordinates for
    ``token_id`` (the client resolves them; this stays transport-free). ``price``
    is the limit price; ``size`` is the share count. A non-``None`` ``expiration``
    makes it GTD (else GTC). ``builder_code`` is accepted for py-sdk parity and is
    inert on paper (no builder fees).

    Returns a ``SignedOrder`` carrying the trading-semantic fields + the unsigned
    paper body; signing fields stay empty placeholders.
    """
    validate_token_id(token_id)
    validated_price = coerce_positive_decimal("price", price)
    validated_size = coerce_positive_decimal("size", size)
    validate_side(side)
    if not isinstance(post_only, bool):
        raise UserInputError("post_only must be a bool.")
    if expiration is not None:
        if not isinstance(expiration, int) or isinstance(expiration, bool):
            raise UserInputError("expiration must be a non-negative integer.")
        if expiration < 0:
            raise UserInputError("expiration must be a non-negative integer.")
        # py-sdk: an ABSOLUTE Unix ts must be ≥ now + 60s. Layered AFTER the
        # non-negative check, exactly as py-sdk orders the two guards.
        minimum = int(time.time()) + _MIN_EXPIRATION_BUFFER_S
        if expiration < minimum:
            raise UserInputError(
                f"expiration must be at least {_MIN_EXPIRATION_BUFFER_S} "
                "seconds in the future."
            )

    gtd_expiration = expiration if expiration is not None and expiration > 0 else 0
    order_type = "GTD" if gtd_expiration > 0 else "GTC"
    # Serialize the monetary/size fields as decimal STRINGS, not floats: the
    # backend's order body is a string-decimal contract, and a float would let
    # binary-float drift (e.g. 0.1) ride onto the wire. ``str(Decimal(...))``
    # carries the exact decimal the caller gave.
    body: dict[str, Any] = {
        "market_id": market_id,
        "outcome": outcome,
        "side": side,
        "price": str(validated_price),
        "order_type": "limit",
        "time_in_force": order_type,
        "quantity": str(validated_size),
    }
    if post_only:
        body["post_only"] = True
    if gtd_expiration > 0:
        body["expiration"] = gtd_expiration

    return _make_signed_order(
        token_id=token_id,
        side=side,
        order_type=order_type,
        post_only=post_only,
        expiration=gtd_expiration,
        body=body,
    )


# ── market-order build (validate -> worst-price cap -> paper body -> order) ──


def build_market_order(
    *,
    token_id: str,
    side: OrderSide,
    amount: Decimal | int | float | str | None = None,
    shares: Decimal | int | float | str | None = None,
    max_spend: Decimal | int | float | str | None = None,
    max_price: Decimal | int | float | str | None = None,
    min_price: Decimal | int | float | str | None = None,
    order_type: str = "FAK",
    builder_code: str | None = None,
    market_id: str,
    outcome: str,
) -> SignedOrder:
    """Validate a market-order call and build an INERT-signed ``SignedOrder``.

    Mirrors py-sdk's market-order arg contract: a **BUY** uses ``amount`` (the USD
    notional to spend) and may cap with ``max_price``; a **SELL** uses ``shares``
    (the share count) and may floor with ``min_price``. ``max_spend`` is a hard
    spend ceiling on a BUY: py-sdk's ``adjust_buy_amount_for_fees`` reduces the
    submitted amount to fit ``max_spend`` (amount + platform/builder fees). On
    paper there are no fees, so that collapses to ``min(amount, max_spend)`` — we
    clamp the submitted ``amount`` to ``max_spend`` when it would otherwise spend
    more. ``builder_code`` is inert on paper.

    A worst-acceptable price is ALWAYS forwarded — ``max_price`` / ``min_price``
    when given, else the :data:`DEFAULT_BUY_WORST_PRICE` /
    :data:`DEFAULT_SELL_WORST_PRICE` default — so the FOK/FAK is never uncapped.
    """
    validate_token_id(token_id)
    validate_side(side)
    if order_type not in _VALID_MARKET_ORDER_TYPES:
        raise UserInputError(f"order_type must be 'FAK' or 'FOK', got {order_type!r}.")

    body: dict[str, Any] = {
        "market_id": market_id,
        "outcome": outcome,
        "side": side,
        "order_type": "market",
        "time_in_force": order_type,
    }

    if side == "BUY":
        if amount is None:
            raise UserInputError("amount is required for BUY market orders.")
        if shares is not None:
            raise UserInputError("shares must not be set for BUY market orders.")
        if min_price is not None:
            raise UserInputError("min_price is only valid for SELL market orders.")
        validated_amount = coerce_positive_decimal("amount", amount)
        if max_spend is not None:
            # py-sdk treats max_spend as a hard spend ceiling; with no paper fees
            # the fee-adjusted amount is exactly min(amount, max_spend).
            validated_max_spend = coerce_positive_decimal("max_spend", max_spend)
            validated_amount = min(validated_amount, validated_max_spend)
        cap = (
            coerce_positive_decimal("max_price", max_price)
            if max_price is not None
            else DEFAULT_BUY_WORST_PRICE
        )
        # USD notional for a market BUY -> sent as ``amount`` (server derives
        # shares). Serialized as a decimal string (string-decimal contract).
        body["amount"] = str(validated_amount)
        body["price"] = str(cap)
    else:  # SELL
        if shares is None:
            raise UserInputError("shares is required for SELL market orders.")
        if amount is not None:
            raise UserInputError("amount must not be set for SELL market orders.")
        if max_spend is not None:
            raise UserInputError("max_spend is only valid for BUY market orders.")
        if max_price is not None:
            raise UserInputError("max_price is only valid for BUY market orders.")
        validated_shares = coerce_positive_decimal("shares", shares)
        cap = (
            coerce_positive_decimal("min_price", min_price)
            if min_price is not None
            else DEFAULT_SELL_WORST_PRICE
        )
        # Share count for a market SELL -> sent as ``quantity`` (decimal string).
        body["quantity"] = str(validated_shares)
        body["price"] = str(cap)

    return _make_signed_order(
        token_id=token_id,
        side=side,
        order_type=order_type,
        post_only=False,
        expiration=0,
        body=body,
    )


def _make_signed_order(
    *,
    token_id: str,
    side: OrderSide,
    order_type: str,
    post_only: bool,
    expiration: int,
    body: dict[str, Any],
) -> SignedOrder:
    """Construct the inert-signed ``SignedOrder`` carrying the unsigned paper body."""
    order = SignedOrder(
        token_id=token_id,
        side=side,
        order_type=order_type,  # type: ignore[arg-type]
        post_only=post_only,
        expiration=expiration,
    )
    # Attach the unsigned PolySim body so post_order submits exactly this. A
    # private-attr assignment is allowed even though the model is frozen (frozen
    # blocks public-field mutation only); it lands in ``__pydantic_private__``.
    order._paper_body = dict(body)
    return order


# ── paper body -> place_order kwargs projection (shared by post_order(s)) ─────


def paper_order_kwargs(body: dict[str, Any]) -> dict[str, Any]:
    """Project a built order's ``paper_body`` onto ``PolySimClient.place_order`` kwargs.

    ``post_order`` (and the future G5 async ``post_order``) and ``post_orders``
    all submit the unsigned ``paper_body`` the build path computed; this is the
    ONE place that maps that body onto the transport's keyword arguments, so the
    sync + async + batch post paths can never drift in what they put on the wire.

    The always-present routing/typing fields (``market_id`` / ``side`` /
    ``outcome`` / ``order_type`` / ``time_in_force`` / ``post_only``) carry py-sdk
    /v1-mirror defaults when the body omits them; the optional numerics
    (``price`` / ``quantity`` / ``amount`` / ``expiration``) are included only
    when the body set them — so the wire bytes match the raw ``paper_body`` and a
    ``None`` is never sent for an absent optional.
    """
    kwargs: dict[str, Any] = {
        "market_id": str(body["market_id"]),
        "side": str(body["side"]),
        "outcome": str(body.get("outcome", "YES")),
        "order_type": str(body.get("order_type", "limit")),
        "time_in_force": str(body.get("time_in_force", "GTC")),
        "post_only": bool(body.get("post_only", False)),
    }
    for key in ("price", "quantity", "amount", "expiration"):
        if body.get(key) is not None:
            kwargs[key] = body[key]
    return kwargs


# ── post-response adaptation (PolySim row -> py-sdk OrderResponse) ───────────


def adapt_order_response(raw: Any) -> OrderResponse:
    """Adapt a PolySim ``POST /v1/orders`` reply onto py-sdk's ``OrderResponse``.

    A malformed reply (not a dict) raises ``UnexpectedResponseError``. Otherwise
    we decide accepted vs rejected with py-sdk's strict ALLOWLIST
    (``order_response._is_accepted``): a row is an :class:`AcceptedOrder` ONLY when
    its status is a recognised accepted post-status AND ``success`` is not
    explicitly false AND it carries a non-empty ``order_id`` AND no error message —
    exactly py-sdk's ``status in {live, matched, delayed} AND success AND
    order_id != '' AND error_msg == ''``. ANYTHING else (including an UNKNOWN /
    unexpected status the backend never documented) is a :class:`RejectedOrder`
    with a mapped :data:`OrderResponseErrorCode` (specific codes for the known
    rejection statuses, ``"unknown"`` otherwise). Field names track py-sdk so a
    ported bot's ``resp.ok`` / ``resp.order_id`` / ``resp.status`` read identically.
    """
    if not isinstance(raw, dict):
        raise UnexpectedResponseError("order response did not match expected shape")

    status_raw = str(raw.get("status", "") or "")
    status_key = status_raw.lower()
    success = raw.get("success")
    order_id = str(raw.get("order_id", "") or raw.get("id", "") or "")
    error_message = str(
        raw.get("error") or raw.get("message") or raw.get("detail") or ""
    )

    # py-sdk allowlist: accepted requires ALL four — a recognised accepted status,
    # success not explicitly false, a non-empty order id, and no error message.
    # (The paper backend omits ``success`` on accepted rows, so absence is truthy;
    # only an explicit ``success: false`` rejects on that axis.)
    accepted = (
        status_key in _ACCEPTED_STATUSES
        and success is not False
        and order_id != ""
        and error_message == ""
    )
    if not accepted:
        code = _STATUS_TO_ERROR_CODE.get(status_key, "unknown")
        message = error_message or status_raw or "rejected"
        return RejectedOrder(code=code, message=message)

    return AcceptedOrder(
        order_id=order_id,
        status=_ACCEPTED_STATUS_MAP.get(status_key, "matched"),  # type: ignore[arg-type]
        making_amount=_to_decimal(raw.get("making_amount") or raw.get("makingAmount")),
        taking_amount=_to_decimal(raw.get("taking_amount") or raw.get("takingAmount")),
        trade_ids=_to_str_tuple(raw.get("trade_ids") or raw.get("tradeIDs")),
        transactions_hashes=_to_str_tuple(
            raw.get("transactions_hashes") or raw.get("transactionsHashes")
        ),
    )


def _to_decimal(value: object) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _to_str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return ()


# ── cancel-response adaptation (PolySim row -> py-sdk CancelOrdersResponse) ───


def adapt_cancel_response(raw: Any) -> CancelOrdersResponse:
    """Adapt a PolySim cancel reply onto py-sdk's ``CancelOrdersResponse``.

    The backend returns either a ``{"canceled": [...], "not_canceled": {...}}``
    shape (already py-sdk-aligned) or a coarser ``{"canceled": <int>}`` count.
    We normalise both onto py-sdk's ``canceled`` tuple + ``not_canceled`` map. A
    non-dict reply raises ``UnexpectedResponseError``.
    """
    if not isinstance(raw, dict):
        raise UnexpectedResponseError("cancel response did not match expected shape")
    canceled = raw.get("canceled")
    not_canceled_raw = raw.get("not_canceled")
    canceled_tuple: tuple[str, ...]
    if isinstance(canceled, (list, tuple)):
        canceled_tuple = tuple(str(c) for c in canceled)
    else:
        # A bare integer count carries no ids; surface an empty id tuple (py-sdk's
        # field is a tuple of ids, not a count).
        canceled_tuple = ()
    not_canceled: dict[str, str] = {}
    if isinstance(not_canceled_raw, dict):
        not_canceled = {str(k): str(v) for k, v in not_canceled_raw.items()}
    return CancelOrdersResponse(canceled=canceled_tuple, not_canceled=not_canceled)


def build_cancel_orders_response(
    canceled: list[str], not_canceled: dict[str, str]
) -> CancelOrdersResponse:
    """Build a ``CancelOrdersResponse`` from a loop's accumulated ids/reasons.

    ``cancel_orders`` cancels each id via the single-cancel endpoint and tallies
    successes/failures itself (the backend has no plural-cancel route); this turns
    that tally into the py-sdk-shaped response.
    """
    return CancelOrdersResponse(canceled=tuple(canceled), not_canceled=dict(not_canceled))


__all__ = [
    "DEFAULT_BUY_WORST_PRICE",
    "DEFAULT_SELL_WORST_PRICE",
    "TOKEN_ID_MIN_DIGITS",
    "adapt_cancel_response",
    "adapt_order_response",
    "build_cancel_orders_response",
    "build_limit_order",
    "build_market_order",
    "coordinates_from_market_payload",
    "needs_token_reverse_resolution",
    "paper_order_kwargs",
    "split_token_local",
    "validate_cancel_order_ids",
    "validate_token_id",
]
