"""Opt-in retry helpers for transient, well-understood failure windows.

The SDK deliberately does **not** auto-retry ``404`` responses — a 404 is
normally permanent (unknown market / order id), and silently retrying it would
hide real bugs behind a long backoff. There is, however, one benign 404: a
brand-new market (especially the BTC/ETH UpDown 5m windows that roll every few
minutes) has a short warm-up window after creation before it enters the
order-validation catalog, during which order placement transiently 404s with
``code="MARKET_NOT_FOUND"``.

:func:`retry_on_market_warmup` is a small, explicit opt-in wrapper for exactly
that case: it retries a callable **only** on a ``MARKET_NOT_FOUND`` 404 with
capped exponential backoff, re-raising any other error immediately and the 404
itself once the attempt budget is spent. It wraps any zero-argument callable, so
it works with both the native ``polysim_sdk`` client and the ``polysim_clob_client``
drop-in.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from polysim_sdk.exceptions import ApiError

T = TypeVar("T")

# The server's ``error.code`` for an order placed against a market that is not
# (yet) in the validation catalog. The drop-in's PolyApiException carries the
# same status code; callers using it can wrap on status_code == 404 themselves.
MARKET_WARMUP_CODE = "MARKET_NOT_FOUND"


def _is_market_warmup_404(exc: BaseException) -> bool:
    """True iff ``exc`` is the transient "market not in catalog yet" 404."""
    return (
        isinstance(exc, ApiError)
        and exc.status_code == 404
        and exc.code == MARKET_WARMUP_CODE
    )


def retry_on_market_warmup(
    fn: Callable[[], T],
    *,
    attempts: int = 6,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` and retry it through a market's warm-up 404.

    Retries **only** when ``fn`` raises an :class:`~polysim_sdk.exceptions.ApiError`
    with ``status_code == 404`` and ``code == "MARKET_NOT_FOUND"`` — the transient
    signal that a freshly-created market has not yet entered the order-validation
    catalog (typically clears within ~30s, the UpDown roll cadence; this is a
    cadence, not a guarantee). Any other exception — including a 404 with a
    different ``code`` — is re-raised immediately, and the warm-up 404 itself is
    re-raised once ``attempts`` is exhausted.

    Backoff is exponential (``base_delay * 2**i``) capped at ``max_delay``; the
    sleep happens **between** attempts only (never after the final one). ``fn``
    is a zero-argument callable, so wrap your call site in a ``lambda`` /
    ``functools.partial`` — e.g.
    ``retry_on_market_warmup(lambda: client.place_order(...))``. It works with
    both the native client and the ``polysim_clob_client`` drop-in.

    :param attempts: total number of calls to ``fn`` (must be >= 1).
    :param base_delay: first backoff, in seconds.
    :param max_delay: ceiling for the per-attempt backoff, in seconds.
    :param sleep: injection seam for the backoff sleep (defaults to
        :func:`time.sleep`); override in tests to avoid real waits.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for i in range(attempts):
        try:
            return fn()
        except ApiError as exc:
            if not _is_market_warmup_404(exc) or i == attempts - 1:
                raise
            sleep(min(base_delay * (2**i), max_delay))
    # Unreachable: the final iteration either returns or re-raises above. Present
    # only so static checkers see every path returns a value or raises.
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["retry_on_market_warmup", "MARKET_WARMUP_CODE"]
