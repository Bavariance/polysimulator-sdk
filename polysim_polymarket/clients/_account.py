"""Pure, transport-free logic for the secure client's account/auth surface.

These free functions hold the validation guards, the PolySim->py-sdk row
adaptation, the USD->base-unit balance conversion, and the PolySim-cursor->py-sdk
``Page`` mapping that :class:`polysim_polymarket.clients.secure.SecureClient` (and,
in a later gate, its async twin) need. Keeping them here — as functions of their
arguments, never re-implemented per client — means the sync and async secure
clients can share ONE copy, exactly as the public clients share
:mod:`polysim_polymarket.clients._common`.

The CLOB *reads* the secure client shares with the public client are NOT here:
those are delegated whole to the composed ``PublicClient`` (see
``secure.py``), so they reuse ``_common`` transitively. This module is only the
authenticated-surface logic the public client has no analog for.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from polysim_polymarket.errors import UnexpectedResponseError, UserInputError
from polysim_polymarket.models import (
    BalanceAllowance,
    ClobTrade,
    OpenOrder,
)
from polysim_polymarket.pagination import Page

# PolySimulator's data-API cursor sentinels (base64): ``MA==`` is the start
# cursor, ``LTE=`` the end-of-results sentinel. The secure client's paginators
# start at START_CURSOR and treat END_CURSOR as "no more pages".
START_CURSOR = "MA=="
END_CURSOR = "LTE="

# One data-API page is this many rows; matches the v1 mirror's walk size.
PAGE_LIMIT = 100

# USDC (the Polymarket collateral) has 6 decimals, so 1 USD = 1_000_000 base
# units. py-sdk's BalanceAllowance.balance is an integer count of those base
# units; PolySimulator reports paper cash as a USD figure, so we scale by this.
USDC_BASE_UNITS_PER_USD = 1_000_000

# py-sdk's valid asset types (case-SENSITIVE).
VALID_ASSET_TYPES: frozenset[str] = frozenset({"COLLATERAL", "CONDITIONAL"})


# ── validation guards ──────────────────────────────────────────────────────


def require_nonempty(name: str, value: object) -> str:
    """Reject a non-string / empty ``value`` like py-sdk's ``require_nonempty``.

    Mirrors py-sdk's ``_internal.validation.require_nonempty``: a non-``str``
    raises ``"<name> must be a string, got <type>."`` and an empty string raises
    ``"<name> is required"``. Used for ``order_id`` (and is the same guard the
    public client's ``_common.require_nonempty_token_id`` applies to token ids).
    """
    if not isinstance(value, str):
        raise UserInputError(f"{name} must be a string, got {type(value).__name__}.")
    if not value:
        raise UserInputError(f"{name} is required")
    return value


def validate_asset_type(asset_type: object) -> None:
    """Reject an asset type that is not exactly ``"COLLATERAL"`` / ``"CONDITIONAL"``.

    Mirrors py-sdk's ``account._validate_asset_type`` (case-SENSITIVE — there is
    no ``.upper()``): anything else raises ``UserInputError`` with py-sdk's exact
    message, before any read.

    The ``isinstance(asset_type, str)`` guard runs FIRST so an unhashable arg (a
    ``list`` / ``dict`` a bot might pass by mistake) raises ``UserInputError``
    rather than letting a ``TypeError`` escape the ``in`` membership test against
    the frozenset.
    """
    if not isinstance(asset_type, str) or asset_type not in VALID_ASSET_TYPES:
        raise UserInputError(
            f"asset_type must be 'COLLATERAL' or 'CONDITIONAL', got {asset_type!r}."
        )


# ── auth-key projection ─────────────────────────────────────────────────────


def api_key_ids(keys: Sequence[Any]) -> tuple[str, ...]:
    """Project PolySim key records onto py-sdk's bare key-id tuple.

    py-sdk's ``fetch_api_keys`` returns a ``tuple[str, ...]`` of key identifiers.
    PolySimulator's ``GET /v1/keys`` returns richer records; we take each
    record's identifier (``id`` / ``key_id`` / ``key`` / ``api_key``, in that
    order of preference) so the result matches py-sdk's shape. A record already a
    bare string is taken as-is.
    """
    out: list[str] = []
    for record in keys:
        if isinstance(record, str):
            out.append(record)
            continue
        if isinstance(record, dict):
            ident = (
                record.get("id")
                or record.get("key_id")
                or record.get("key")
                or record.get("api_key")
            )
            if ident is not None:
                out.append(str(ident))
    return tuple(out)


# ── balance adaptation (PolySim USD cash -> py-sdk base-unit BalanceAllowance) ─


def adapt_balance_allowance(payload: Any) -> BalanceAllowance:
    """Adapt PolySim's ``/v1/account/balance`` payload onto ``BalanceAllowance``.

    PolySimulator reports paper cash as a USD figure under ``balance`` (with
    ``cash`` / ``available`` as fallback keys). py-sdk's ``BalanceAllowance``
    carries an integer ``balance`` in USDC **base units** (6 decimals), so we
    scale the USD value by :data:`USDC_BASE_UNITS_PER_USD` and round to the
    nearest base unit. Paper trading has no on-chain allowance, so ``allowances``
    is empty.

    The scaling goes through :class:`~decimal.Decimal` (``Decimal(str(raw))``),
    not ``float``, so a monetary value with binary-float drift (e.g. a value
    whose ``float * 1e6`` would round to the wrong base-unit count) converts
    exactly — money never rides a binary float through this adapter.

    A malformed payload (not a dict, or no numeric balance field) raises
    ``UnexpectedResponseError`` rather than silently reporting zero — a porting
    author should see the shape mismatch, not a wrong $0 balance.
    """
    if not isinstance(payload, dict):
        raise UnexpectedResponseError("balance response did not match expected shape")
    raw = payload.get("balance")
    if raw is None:
        raw = payload.get("cash")
    if raw is None:
        raw = payload.get("available")
    if raw is None:
        raise UnexpectedResponseError("balance response did not match expected shape")
    try:
        # ``Decimal(str(raw))`` keeps full decimal precision (str() avoids the
        # binary-float artefacts of Decimal(<float>)); round half-even to the
        # nearest integer base unit.
        usd = Decimal(str(raw))
    except (TypeError, ValueError, InvalidOperation) as error:
        raise UnexpectedResponseError(
            f"balance response 'balance' was not numeric: {raw!r}"
        ) from error
    base_units = int(round(usd * USDC_BASE_UNITS_PER_USD))
    return BalanceAllowance(balance=base_units, allowances={})


def adapt_conditional_balance(
    positions: Any, market_id: str, outcome: str
) -> BalanceAllowance:
    """Adapt the open-positions list onto a CONDITIONAL ``BalanceAllowance``.

    Resolution of the CodeRabbit/Codex disagreement: the real py-sdk's
    ``get_balance_allowance`` for ``asset_type='CONDITIONAL'`` returns the
    **conditional TOKEN** balance the server holds for that ``token_id`` (the
    position's share count), NOT collateral cash. PolySimulator is paper trading
    with no on-chain CTF balance, so the faithful analog is the open position's
    share count for the resolved ``(market_id, outcome)`` — the conditional token
    Polymarket would settle. Both the conditional token and USDC carry 6 decimals,
    so the share count scales by :data:`USDC_BASE_UNITS_PER_USD`; a flat / absent
    position is a genuine 0 conditional balance (not an error). Outcome matching
    is case-insensitive (positions store ``"Yes"`` / ``"No"``; the token resolver
    returns ``"YES"`` / ``"NO"`` / ``"UP"`` / ``"DOWN"``). Paper trading has no
    on-chain allowance, so ``allowances`` is empty.
    """
    rows = positions if isinstance(positions, list) else []
    target_outcome = outcome.upper()
    shares = Decimal("0")
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("market_id")) != str(market_id):
            continue
        if str(row.get("outcome", "")).upper() != target_outcome:
            continue
        try:
            shares += Decimal(str(row.get("quantity", "0")))
        except (TypeError, ValueError, InvalidOperation):
            continue
    base_units = int(round(shares * USDC_BASE_UNITS_PER_USD))
    return BalanceAllowance(balance=base_units, allowances={})


# ── order / trade row + page adaptation ─────────────────────────────────────


def adapt_open_order(raw: Any) -> OpenOrder:
    """Adapt a raw PolySim order row onto py-sdk's ``OpenOrder`` shape.

    A non-dict payload raises ``UnexpectedResponseError`` (py-sdk's
    ``parse_response`` contract). Field names track py-sdk; PolySim-absent fields
    fall back to the model's defaults.
    """
    if not isinstance(raw, dict):
        raise UnexpectedResponseError("order response did not match expected shape")
    try:
        return OpenOrder.model_validate(raw)
    except (TypeError, ValueError) as error:
        raise UnexpectedResponseError("order response did not match expected shape") from error


def adapt_clob_trade(raw: Any) -> ClobTrade:
    """Adapt a raw PolySim trade row onto py-sdk's ``ClobTrade`` shape."""
    if not isinstance(raw, dict):
        raise UnexpectedResponseError("trade response did not match expected shape")
    try:
        return ClobTrade.model_validate(raw)
    except (TypeError, ValueError) as error:
        raise UnexpectedResponseError("trade response did not match expected shape") from error


def _page_from_envelope(envelope: Any, adapt: Any, what: str) -> Page[Any]:
    """Map a PolySim data-API envelope onto a py-sdk ``Page``.

    PolySimulator's data API returns ``{"limit", "count", "next_cursor",
    "data"}``. We adapt each ``data`` row via ``adapt`` and translate the cursor
    model onto py-sdk's ``Page`` contract: a ``next_cursor`` other than the
    end sentinel :data:`END_CURSOR` means there is another page
    (``has_more=True``), the end sentinel (or a missing cursor) means this is the
    last page (``has_more=False``, ``next_cursor=None``). The Paginator then
    stops on ``has_more=False`` instead of looping the end sentinel forever.

    A malformed envelope (not a dict, or ``data`` not a list) raises
    ``UnexpectedResponseError`` (py-sdk's page-parse contract).
    """
    if not isinstance(envelope, dict):
        raise UnexpectedResponseError(f"{what} response did not match expected shape")
    rows = envelope.get("data")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise UnexpectedResponseError(f"{what} response 'data' must be a list")
    items = tuple(adapt(row) for row in rows)
    raw_cursor = envelope.get("next_cursor")
    has_more = raw_cursor is not None and raw_cursor != END_CURSOR
    next_cursor = raw_cursor if has_more else None
    total_count = envelope.get("count")
    return Page(
        items=items,
        has_more=has_more,
        next_cursor=next_cursor,
        total_count=total_count if isinstance(total_count, int) else None,
    )


def adapt_open_orders_page(envelope: Any) -> Page[OpenOrder]:
    """Map a PolySim ``/data/orders`` envelope onto a ``Page[OpenOrder]``."""
    return _page_from_envelope(envelope, adapt_open_order, "open orders")


def adapt_account_trades_page(envelope: Any) -> Page[ClobTrade]:
    """Map a PolySim ``/data/trades`` envelope onto a ``Page[ClobTrade]``."""
    return _page_from_envelope(envelope, adapt_clob_trade, "account trades")
