"""Pure, transport-free logic shared by the sync + async public clients.

Both :class:`polysim_polymarket.clients.public.PublicClient` and
:class:`polysim_polymarket.clients.async_public.AsyncPublicClient` mirror the
**same** py-sdk CLOB-read subset. Only the HTTP call differs between them — the
sync client calls a blocking transport, the async client ``await``\\s an async
transport. Everything else (the parity-verified validation guards, the
side/token validation, the marginal-price walk, the book/market model
adaptation, the get_price BUY->ask mapping inputs) is *identical* by contract.

To keep the two clients from drifting, that identical logic lives here as free
functions of their arguments — never duplicated, never re-implemented per
client. A client method is then just "validate (shared) -> read (its own
transport) -> adapt (shared)". If parity logic changes, it changes in ONE place
and both clients move together.

These were proven in the original synchronous ``public.py`` (parity-verified
against the real py-sdk ``PublicClient``); this module is a pure-logic
extraction of that file, with zero behavioural change.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from polysim_polymarket.errors import (
    InsufficientLiquidityError,
    UnexpectedResponseError,
    UserInputError,
)
from polysim_polymarket.models import (
    Market,
    OrderBook,
    OrderSide,
    PriceHistoryPoint,
    PriceRequest,
)
from polysim_sdk._shared import _book_sides

# Native list_markets reads one offset-page of this many rows per cursor step.
PAGE_LIMIT = 100

# PolySim's standard minimum price increment when a book omits ``tick_size``.
DEFAULT_TICK_SIZE = "0.01"
# Price/size scalars are quantised to 4 decimal places before becoming Decimals
# — the same precision the v1 mirror's string prices use, so a midpoint/spread
# Decimal here matches what a bot would compute on real Polymarket's 4dp grid.
PRICE_QUANT = Decimal("0.0001")

# py-sdk's valid order sides + price-history intervals (case-SENSITIVE).
VALID_ORDER_SIDES: frozenset[str] = frozenset({"BUY", "SELL"})
PRICE_HISTORY_INTERVALS: frozenset[str] = frozenset({"max", "1w", "1d", "6h", "1h"})


# ── validation guards ──────────────────────────────────────────────────────


def require_market_lookup_arg(id: str | None, slug: str | None) -> None:
    """Reject a market lookup that isn't given exactly one of ``id`` / ``slug``.

    Mirrors py-sdk's market-path builder (``_resolve_lookup`` in
    ``polymarket._internal.gamma_paths``): a market lookup must receive **exactly
    one** of ``id`` / ``slug`` / ``url`` — zero (no-arg) and two-or-more both
    raise the SAME ``UserInputError`` with py-sdk's exact message, so a ported
    bot's ``except UserInputError`` behaves identically. The mirror's
    ``get_market`` only routes ``id`` / ``slug`` (``url`` is accepted-and-ignored
    for signature parity), so the guard validates those two; this fires BEFORE
    any read so a bad-arg call never hits the network.
    """
    if (id is None) == (slug is None):
        raise UserInputError("Provide exactly one of id, slug, or url for market lookup.")


def require_nonempty_token_ids(token_ids: Sequence[str]) -> tuple[str, ...]:
    """Reject a bare ``str``/``bytes`` or empty ``token_ids`` like py-sdk.

    Mirrors py-sdk's ``_require_nonempty_token_ids``: a bare ``str``/``bytes``
    would char-iterate (``token_ids="711"`` -> three single-char reads), so it is
    rejected; an empty sequence is rejected too. Returns the validated ids as a
    tuple. Error type + messages match py-sdk exactly so the prefix swap keeps a
    bot's ``except UserInputError`` behaviour identical.
    """
    if isinstance(token_ids, (str, bytes)):
        raise UserInputError("token_ids must be a sequence of strings, not a single string.")
    if not token_ids:
        raise UserInputError("token_ids must be a non-empty sequence.")
    return tuple(token_ids)


def require_nonempty_price_requests(
    requests: Sequence[PriceRequest],
) -> tuple[PriceRequest, ...]:
    """Reject a bare ``str``/``bytes``/``PriceRequest`` or empty ``requests``.

    Mirrors py-sdk's ``_require_nonempty_price_requests`` (the ``get_prices``
    analog of the token-ids guard): a bare scalar would mis-iterate, and an empty
    sequence is rejected. Each entry's ``token_id`` and ``side`` are validated up
    front (before any read) exactly as py-sdk does — a non-``PriceRequest`` entry,
    an empty ``token_id``, or a non-uppercase ``side`` raises ``UserInputError``.
    Error type + messages match py-sdk exactly.
    """
    if isinstance(requests, (str, bytes, PriceRequest)):
        raise UserInputError("requests must be a sequence of PriceRequest values.")
    if not requests:
        raise UserInputError("requests must be a non-empty sequence.")
    validated: list[PriceRequest] = []
    for raw in requests:
        if not isinstance(raw, PriceRequest):
            raise UserInputError(f"each entry must be a PriceRequest, got {type(raw).__name__}.")
        require_nonempty_token_id(raw.token_id)
        validate_side(raw.side)
        validated.append(raw)
    return tuple(validated)


def require_nonempty_token_id(token_id: object) -> str:
    """Reject a non-string / empty ``token_id`` like py-sdk's ``require_nonempty``.

    Mirrors py-sdk: a non-``str`` raises ``"token_id must be a string, got
    <type>."`` and an empty string raises ``"token_id is required"``.
    """
    if not isinstance(token_id, str):
        raise UserInputError(f"token_id must be a string, got {type(token_id).__name__}.")
    if not token_id:
        raise UserInputError("token_id is required")
    return token_id


def validate_side(side: object) -> None:
    """Reject any side that is not exactly ``"BUY"`` / ``"SELL"`` (case-SENSITIVE).

    Mirrors py-sdk's ``_validate_side``: there is NO ``.upper()`` — ``"buy"`` /
    ``"Buy"`` are rejected with ``"side must be 'BUY' or 'SELL', got <repr>."``.
    """
    if side not in VALID_ORDER_SIDES:
        raise UserInputError(f"side must be 'BUY' or 'SELL', got {side!r}.")


def require_nonneg_int(name: str, value: object) -> None:
    """Reject a non-int / negative ``value`` like py-sdk's ``_require_nonneg_int``.

    ``None`` passes (the param is optional); ``bool`` is rejected (it is an
    ``int`` subclass); a non-``int`` raises ``"<name> must be an integer."`` and a
    negative value raises ``"<name> must be a non-negative integer."``.
    """
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise UserInputError(f"{name} must be an integer.")
    if value < 0:
        raise UserInputError(f"{name} must be a non-negative integer.")


def require_positive_int(name: str, value: object) -> None:
    """Reject a non-int / non-positive ``value`` like py-sdk's ``_require_positive_int``."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise UserInputError(f"{name} must be an integer.")
    if value <= 0:
        raise UserInputError(f"{name} must be a positive integer.")


def to_decimal(value: float | None) -> Decimal | None:
    """Quantise a float price/size to the 4dp grid as a :class:`Decimal`.

    Routes through ``str`` then ``Decimal`` (never ``Decimal(float)``) so no
    binary-float artefact leaks into the value, then snaps to 4dp.
    """
    if value is None:
        return None
    return Decimal(f"{value:.4f}").quantize(PRICE_QUANT)


def coerce_positive_decimal(name: str, value: object) -> Decimal:
    """Coerce a price/size/amount to a positive :class:`Decimal`.

    Mirrors py-sdk's ``coerce_positive_decimal``: ``bool`` is rejected (it is an
    ``int`` subclass), ``float`` routes through ``str`` to avoid binary-float
    artefacts, and a non-finite or non-positive value raises ``UserInputError``.
    """
    if isinstance(value, bool):
        raise UserInputError(f"{name} must be a positive number.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        result = Decimal(str(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except (ValueError, InvalidOperation) as error:
            raise UserInputError(
                f"{name} must be a valid decimal number: {value!r}"
            ) from error
    else:
        raise UserInputError(f"{name} must be a number, got {type(value).__name__}.")
    if not result.is_finite() or result <= 0:
        raise UserInputError(f"{name} must be a positive number.")
    return result


# ── order-book level parsing (Decimal) ─────────────────────────────────────


def decimal_book_sides(
    book: dict[str, Any],
) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
    """Parse a PolySim book's sides as exact ``(price, size)`` Decimals.

    The marginal-price walk uses Decimals (not the float ``_book_sides``) so its
    cumulative-notional / cumulative-shares comparisons match py-sdk's exact
    Decimal arithmetic level-for-level.
    """
    return decimal_levels(book.get("bids")), decimal_levels(book.get("asks"))


def decimal_levels(raw: Any) -> list[tuple[Decimal, Decimal]]:
    """Normalise one side of the book to ``[(Decimal price, Decimal size), …]``."""
    out: list[tuple[Decimal, Decimal]] = []
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
            out.append((Decimal(str(price)), Decimal(str(size))))
        except (TypeError, ValueError, InvalidOperation):
            continue
    return out


def book_tick_size(book: dict[str, Any]) -> Decimal:
    """The book's tick size as a Decimal, defaulting to PolySim's 0.01 grid."""
    raw = book.get("tick_size")
    if raw is None:
        return Decimal(DEFAULT_TICK_SIZE)
    try:
        return Decimal(str(raw))
    except (TypeError, ValueError, InvalidOperation):
        return Decimal(DEFAULT_TICK_SIZE)


def marginal_price(
    levels: list[tuple[Decimal, Decimal]],
    target: Decimal,
    order_type: str,
    *,
    by_notional: bool,
) -> Decimal:
    """Walk ``levels`` best-first; return the marginal (worst-touched) price.

    Mirrors py-sdk's ``_calculate_buy_market_price`` / ``_calculate_sell_market_price``:
    a BUY accumulates notional (``size * price``) toward ``target`` (USD); a SELL
    accumulates shares (``size``) toward ``target`` (share count). The price of
    the first level at which the cumulative reaches ``target`` is returned.

    ``levels`` must already be ordered best-execution-first. On under-fill an
    ``FOK`` raises ``InsufficientLiquidityError``; an ``FAK`` falls back to the
    worst (deepest) level's price (``levels[-1]``), matching py-sdk's fallback.
    """
    if not levels:
        raise InsufficientLiquidityError("No resting liquidity.")
    cumulative = Decimal(0)
    for price, size in levels:
        cumulative += (size * price) if by_notional else size
        if cumulative >= target:
            return price
    if order_type == "FOK":
        raise InsufficientLiquidityError("Insufficient liquidity for full fill.")
    return levels[-1][0]


# ── book / market model adaptation ─────────────────────────────────────────


def adapt_book(token_id: str, book: dict[str, Any]) -> OrderBook:
    """Adapt a raw PolySim book payload onto the py-sdk-shape ``OrderBook``.

    Parses the sides with the shared :func:`polysim_sdk._shared._book_sides`
    helper and formats the normalised float levels as strings (the model's
    ``_DecimalFromString`` accepts str/Decimal only). ``token_id`` echoes
    exactly what the caller asked for — py-sdk's contract is that the book is
    keyed by the requested asset id.

    Levels are normalised to py-sdk's :class:`~polysim_polymarket.models.OrderBook`
    ordering contract regardless of the wire order PolySim served: **bids
    ascending** by price (best/highest bid = ``bids[-1]``) and **asks
    descending** by price (best/lowest ask = ``asks[-1]``). That way a ported
    bot's ``book.bids[-1]`` / ``book.asks[-1]`` best-level reads behave the
    same here as on real Polymarket.
    """
    bids, asks = _book_sides(book)
    # py-sdk OrderBook contract: bids ascending, asks descending by price.
    # NOTE: this re-sort is a deliberate, fidelity-IMPROVING deviation from
    # py-sdk. py-sdk trusts the wire order (its OrderBook binds levels as the
    # server sends them); we instead normalise PolySim's book to the contract
    # ordering regardless of the order PolySim served, so a ported bot's
    # best-level reads (``book.bids[-1]`` / ``book.asks[-1]``) are correct
    # even if PolySim's wire order ever drifts. Safe because re-sorting a
    # correct book is a no-op and a mis-ordered book is repaired, never
    # corrupted.
    bids = sorted(bids, key=lambda lvl: lvl[0])
    asks = sorted(asks, key=lambda lvl: lvl[0], reverse=True)
    return OrderBook.model_validate(
        {
            "market": book.get("market") or str(token_id),
            "asset_id": str(token_id),
            "timestamp": (str(book["timestamp"]) if book.get("timestamp") else None),
            "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
            "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
            "min_order_size": (
                str(book["min_order_size"]) if book.get("min_order_size") is not None else "0"
            ),
            "tick_size": (
                str(book["tick_size"]) if book.get("tick_size") is not None
                else DEFAULT_TICK_SIZE
            ),
            "neg_risk": bool(book.get("neg_risk", False)),
            "last_trade_price": (
                str(book["last_trade_price"])
                if book.get("last_trade_price") is not None
                else None
            ),
            "hash": str(book.get("hash") or ""),
        }
    )


def adapt_market(raw: dict[str, Any]) -> Market:
    """Adapt a raw PolySim market dict onto the py-sdk-shape ``Market``.

    PolySim returns ``active`` / ``closed`` / ``neg_risk`` at the **top
    level**; py-sdk nests them under ``Market.state``. Build that nested
    ``state`` sub-dict here (unless the payload already nests it) so a ported
    bot reads ``market.state.closed`` exactly as on real Polymarket. Other
    fields (``id`` / ``condition_id`` / ``question`` / ``slug``) carry their
    names; extras are ignored by the model.
    """
    data = dict(raw)
    if "state" not in data:
        data["state"] = {
            "active": data.get("active"),
            "closed": data.get("closed"),
            "neg_risk": data.get("neg_risk"),
        }
    return Market.model_validate(data)


# ── derived scalar reads from a book (best bid/ask → mid/price/spread) ──────


def best_bid_ask_from_book(book: dict[str, Any]) -> tuple[float | None, float | None]:
    """Best bid / best ask floats for a raw book payload."""
    from polysim_sdk._shared import _best_ask, _best_bid

    bids, asks = _book_sides(book)
    return _best_bid(bids), _best_ask(asks)


def midpoint_from_book(book: dict[str, Any]) -> Decimal:
    """``(best_bid + best_ask) / 2`` on a book, as a Decimal (0 if one-sided)."""
    bid, ask = best_bid_ask_from_book(book)
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    result = to_decimal(mid)
    return result if result is not None else Decimal("0")


def price_from_book(book: dict[str, Any], side: OrderSide) -> Decimal:
    """Executable price for a side: BUY -> best ASK, SELL -> best BID.

    The caller must have already validated ``side`` via :func:`validate_side`.
    """
    bid, ask = best_bid_ask_from_book(book)
    px = ask if side == "BUY" else bid
    result = to_decimal(px)
    return result if result is not None else Decimal("0")


def spread_from_book(book: dict[str, Any]) -> Decimal:
    """``best_ask - best_bid`` on a book, as a Decimal (0 if one-sided)."""
    bid, ask = best_bid_ask_from_book(book)
    spread = (ask - bid) if bid is not None and ask is not None else None
    result = to_decimal(spread)
    return result if result is not None else Decimal("0")


def last_trade_from_book(book: dict[str, Any]) -> tuple[Decimal | None, OrderSide]:
    """Last trade ``(price, side)`` from a book snapshot.

    PolySim's book carries ``last_trade_price`` but no trade side; py-sdk's
    ``LastTradePrice`` requires a side, so we default to ``BUY`` (the book
    snapshot does not distinguish maker/taker side). Price is ``None`` when the
    book has never traded.
    """
    raw = book.get("last_trade_price")
    price = Decimal(str(raw)) if raw is not None else None
    return price, "BUY"


# ── price-history forwarding + parsing ─────────────────────────────────────


def build_price_history_params(
    *,
    token_id: str,
    start_ts: int | None,
    end_ts: int | None,
    fidelity: int | None,
    interval: str | None,
) -> dict[str, Any]:
    """Validate price-history inputs (py-sdk's contract) and build PM params.

    Validation mirrors py-sdk's ``build_price_history_request``: ``token_id``
    must be a non-empty string; ``start_ts`` / ``end_ts`` are non-negative ints;
    ``fidelity`` is a positive int; ``interval`` must be one of py-sdk's allowed
    values — each bad value raises ``UserInputError`` before any request.
    """
    require_nonempty_token_id(token_id)
    require_nonneg_int("start_ts", start_ts)
    require_nonneg_int("end_ts", end_ts)
    require_positive_int("fidelity", fidelity)
    if interval is not None and interval not in PRICE_HISTORY_INTERVALS:
        raise UserInputError(
            f"interval must be one of {sorted(PRICE_HISTORY_INTERVALS)}, got {interval!r}."
        )
    params: dict[str, Any] = {"market": str(token_id), "format": "pm"}
    if start_ts is not None:
        params["startTs"] = start_ts
    if end_ts is not None:
        params["endTs"] = end_ts
    if fidelity is not None:
        params["fidelity"] = fidelity
    if interval is not None:
        params["interval"] = interval
    return params


def parse_price_history(payload: Any) -> list[Any]:
    """Validate a price-history payload's envelope and return its ``history`` list.

    py-sdk's ``parse_price_history`` contract: the payload must be a dict and its
    ``history`` must be a list — anything else is ``UnexpectedResponseError``, not
    a silent empty tuple. The caller maps the returned list onto
    ``PriceHistoryPoint`` (the mapping itself may raise, which the caller wraps).
    """
    if not isinstance(payload, dict):
        raise UnexpectedResponseError("price history response did not match expected shape")
    history = payload.get("history")
    if not isinstance(history, list):
        raise UnexpectedResponseError("price history response did not match expected shape")
    return history


def map_price_history(history: list[Any]) -> tuple[PriceHistoryPoint, ...]:
    """Map a validated ``history`` list onto a ``tuple[PriceHistoryPoint, ...]``.

    The caller has already run :func:`parse_price_history` to obtain the
    envelope's ``history`` list; this validates each entry as a
    :class:`~polysim_polymarket.models.PriceHistoryPoint` and returns py-sdk's
    bare-tuple shape. An entry that doesn't match the point shape raises
    ``UnexpectedResponseError`` (py-sdk's contract) rather than being silently
    dropped. Shared by both public clients so the mapping lives in ONE place.
    """
    try:
        return tuple(PriceHistoryPoint.model_validate(pt) for pt in history)
    except (TypeError, ValueError) as error:
        raise UnexpectedResponseError(
            "price history response did not match expected shape"
        ) from error


# ── estimate_market_price input validation + result-band check ─────────────


def validate_estimate_inputs(
    *,
    token_id: str,
    side: OrderSide,
    amount: Decimal | int | float | str | None,
    shares: Decimal | int | float | str | None,
    order_type: str,
) -> Decimal:
    """Mirror py-sdk's ``_validate_estimate_inputs`` (raises ``UserInputError``).

    Validation order matches py-sdk: ``token_id`` first (non-empty string),
    then case-SENSITIVE ``side`` ("BUY"/"SELL" only — NO ``.upper()``), then
    the side-specific quantity, then ``order_type``.
    """
    require_nonempty_token_id(token_id)
    if side == "BUY":
        if amount is None:
            raise UserInputError("amount is required for BUY estimates.")
        if shares is not None:
            raise UserInputError("shares must not be set for BUY estimates.")
        notional = coerce_positive_decimal("amount", amount)
    elif side == "SELL":
        if shares is None:
            raise UserInputError("shares is required for SELL estimates.")
        if amount is not None:
            raise UserInputError("amount must not be set for SELL estimates.")
        notional = coerce_positive_decimal("shares", shares)
    else:
        raise UserInputError(f"side must be 'BUY' or 'SELL', got {side!r}.")
    if order_type not in ("FAK", "FOK"):
        raise UserInputError(f"order_type must be 'FAK' or 'FOK', got {order_type!r}.")
    return notional


def estimate_from_book(
    book: dict[str, Any],
    *,
    side: OrderSide,
    notional: Decimal,
    order_type: str,
) -> Decimal:
    """Compute the marginal market-order price from a book (post-validation).

    The caller has already run :func:`validate_estimate_inputs` to obtain
    ``notional`` (USD for BUY, share count for SELL). A BUY walks the asks
    cheapest-first accumulating notional; a SELL walks the bids highest-first
    accumulating shares. A resolved price outside ``[tick_size, 1 - tick_size]``
    raises ``UnexpectedResponseError`` (py-sdk's band check).
    """
    bids, asks = decimal_book_sides(book)
    # py-sdk OrderBook ordering: bids ascending, asks descending. Walk
    # best-first: asks cheapest-first, bids highest-first.
    if side == "BUY":
        levels = sorted(asks, key=lambda lvl: lvl[0])  # cheapest ask first
        price = marginal_price(levels, notional, order_type, by_notional=True)
    else:
        levels = sorted(bids, key=lambda lvl: lvl[0], reverse=True)  # highest bid first
        price = marginal_price(levels, notional, order_type, by_notional=False)
    tick_size = book_tick_size(book)
    if price < tick_size or price > Decimal(1) - tick_size:
        raise UnexpectedResponseError(
            f"Resolved market price {price} fell outside the valid range "
            f"for tick size {tick_size}."
        )
    return price


# ── list_markets filter forwarding ─────────────────────────────────────────


def list_markets_forward(
    *,
    closed: bool | None,
    order: str | None,
    ascending: bool | None,
) -> dict[str, Any]:
    """Build the subset of py-sdk's gamma filters PolySim's ``/v1/markets`` honours.

    Only ``closed`` / ``order`` (-> ``sort``) / ``ascending`` forward server-side;
    the rest of py-sdk's gamma keyword set is accepted for signature parity and
    dropped (no PolySim analog).
    """
    forward: dict[str, Any] = {}
    if closed is not None:
        forward["closed"] = closed
    if order is not None:
        forward["sort"] = order
    if ascending is not None:
        forward["ascending"] = ascending
    return forward


def resolve_page_size(page_size: int) -> int:
    """Validate + clamp a ``list_markets`` page size to the effective request limit.

    Mirrors py-sdk's ``paginate_offset`` guard — a ``page_size < 1`` raises
    ``UserInputError("page_size must be a positive integer.")`` (a non-int /
    bool is also rejected). PolySimulator's ``/v1/markets`` hard-caps a page at
    :data:`PAGE_LIMIT` (100) rows, so a larger ``page_size`` is clamped down to
    that ceiling — the returned value is the effective ``limit`` the fetch +
    cursor boundary use, so ``page_size`` is honoured rather than ignored.
    """
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise UserInputError("page_size must be a positive integer.")
    if page_size < 1:
        raise UserInputError("page_size must be a positive integer.")
    return min(page_size, PAGE_LIMIT)
