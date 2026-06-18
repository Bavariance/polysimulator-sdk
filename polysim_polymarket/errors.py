"""py-clob-client-compatible exception names, shared with the v1 mirror.

Both PolySimulator mirrors expose the same ``PolyException`` (base) ->
``PolyApiException`` tree, and they do so by **re-export, not redeclaration** —
``polysim_polymarket.errors.PolyException is polysim_clob_client.exceptions.PolyException``.

That shared identity matters: a bot that mixes both mirrors (or catches
``PolyException`` after a call into either) sees one exception hierarchy, so a
single ``except PolyException`` reliably catches errors raised anywhere.

py-sdk additionally raises a few **named** error types from its market-data
surface (``UserInputError`` for pre-request input validation,
``InsufficientLiquidityError`` when resting liquidity can't satisfy a market
order, ``UnexpectedResponseError`` when a resolved value falls outside its valid
range). The mirror raises classes with the **same names** so the prefix swap to
real Polymarket leaves a bot's ``except UserInputError`` / ``except
InsufficientLiquidityError`` blocks working unchanged. We make them subclasses
of the shared ``PolyException`` base too, so a bot that wraps calls in the
broader ``except PolyException`` still catches them.
"""

from __future__ import annotations

from polysim_clob_client.exceptions import PolyApiException, PolyException


class UserInputError(PolyException):
    """Input failed SDK validation before any request was sent.

    Mirrors ``polymarket.errors.UserInputError``. Raised for missing /
    contradictory / out-of-range call arguments (e.g. a BUY estimate with
    ``shares=`` set, or a non-positive ``amount``).
    """


class InsufficientLiquidityError(PolyException):
    """Resting liquidity cannot satisfy the requested execution.

    Mirrors ``polymarket.errors.InsufficientLiquidityError``. Raised by an
    ``FOK`` market-price estimate the book can't fully fill.
    """


class UnexpectedResponseError(PolyException):
    """A resolved value did not match the expected shape / range.

    Mirrors ``polymarket.errors.UnexpectedResponseError``. Raised when a
    resolved market price falls outside the valid ``[tick_size, 1 - tick_size]``
    band for the token's tick size.
    """


__all__ = [
    "InsufficientLiquidityError",
    "PolyApiException",
    "PolyException",
    "UnexpectedResponseError",
    "UserInputError",
]
