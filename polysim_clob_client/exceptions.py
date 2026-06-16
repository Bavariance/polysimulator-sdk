"""py-clob-client-compatible exception names.

Ported strategies catch ``PolyApiException``. We alias it onto the native
:mod:`polysim_sdk.exceptions` tree so ``except PolyApiException`` keeps
working and callers can still catch the finer-grained native classes.
"""

from __future__ import annotations

from polysim_sdk.exceptions import (
    ApiError,
    PolySimError,
    RateLimitError,
    ValidationError,
)

# py-clob-client's exception tree is PolyException (base) -> PolyApiException.
# Map the base onto our PolySimError and the API-level onto ApiError (itself a
# PolySimError subclass), so the real `except PolyException` /
# `except PolyApiException` hierarchy is preserved for ported bots.
PolyException = PolySimError
PolyApiException = ApiError

__all__ = [
    "PolyException",
    "PolyApiException",
    "ApiError",
    "PolySimError",
    "RateLimitError",
    "ValidationError",
]
