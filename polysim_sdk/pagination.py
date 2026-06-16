"""Pagination iterators for the PolySimulator limit/offset endpoints.

These walk a listing endpoint page by page and stop when a short page comes
back (fewer rows than the page size), yielding one row at a time so callers
never have to manage ``offset`` by hand::

    from polysim_sdk import PolySimClient
    from polysim_sdk.pagination import iter_orders

    with PolySimClient() as client:
        for order in iter_orders(client, status="OPEN"):
            ...

Async equivalents (``aiter_*``) take an :class:`AsyncPolySimClient` and are
used with ``async for``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polysim_sdk.aio import AsyncPolySimClient
    from polysim_sdk.client import PolySimClient

_DEFAULT_PAGE = 100


def _iterate(fetch: Any, page_size: int) -> Iterator[dict[str, Any]]:
    offset = 0
    while True:
        rows = fetch(offset)
        if not rows:
            return
        yield from rows
        if len(rows) < page_size:
            return
        offset += page_size


# ── Sync iterators ──────────────────────────────────────────────────────


def iter_markets(
    client: PolySimClient, *, page_size: int = _DEFAULT_PAGE, **filters: Any
) -> Iterator[dict[str, Any]]:
    """Yield every market matching ``filters``, paging by ``page_size``."""
    return _iterate(
        lambda off: client.list_markets(limit=page_size, offset=off, **filters), page_size
    )


def iter_orders(
    client: PolySimClient, *, page_size: int = _DEFAULT_PAGE, **filters: Any
) -> Iterator[dict[str, Any]]:
    """Yield every order matching ``filters`` (status, market_id, wallet_id)."""
    return _iterate(
        lambda off: client.list_orders(limit=page_size, offset=off, **filters), page_size
    )


def iter_history(
    client: PolySimClient, *, page_size: int = _DEFAULT_PAGE, **filters: Any
) -> Iterator[dict[str, Any]]:
    """Yield every filled-order history row matching ``filters``."""
    return _iterate(lambda off: client.history(limit=page_size, offset=off, **filters), page_size)


def iter_wallets(
    client: PolySimClient, *, page_size: int = _DEFAULT_PAGE
) -> Iterator[dict[str, Any]]:
    """Yield every wallet you own."""
    return _iterate(lambda off: client.list_wallets(limit=page_size, offset=off), page_size)


# ── Async iterators ─────────────────────────────────────────────────────


async def _aiterate(fetch: Any, page_size: int) -> AsyncIterator[dict[str, Any]]:
    offset = 0
    while True:
        rows = await fetch(offset)
        if not rows:
            return
        for row in rows:
            yield row
        if len(rows) < page_size:
            return
        offset += page_size


def aiter_markets(
    client: AsyncPolySimClient, *, page_size: int = _DEFAULT_PAGE, **filters: Any
) -> AsyncIterator[dict[str, Any]]:
    """Async: yield every market matching ``filters``."""
    return _aiterate(
        lambda off: client.list_markets(limit=page_size, offset=off, **filters), page_size
    )


def aiter_orders(
    client: AsyncPolySimClient, *, page_size: int = _DEFAULT_PAGE, **filters: Any
) -> AsyncIterator[dict[str, Any]]:
    """Async: yield every order matching ``filters``."""
    return _aiterate(
        lambda off: client.list_orders(limit=page_size, offset=off, **filters), page_size
    )


def aiter_history(
    client: AsyncPolySimClient, *, page_size: int = _DEFAULT_PAGE, **filters: Any
) -> AsyncIterator[dict[str, Any]]:
    """Async: yield every filled-order history row matching ``filters``."""
    return _aiterate(lambda off: client.history(limit=page_size, offset=off, **filters), page_size)


__all__ = [
    "iter_markets",
    "iter_orders",
    "iter_history",
    "iter_wallets",
    "aiter_markets",
    "aiter_orders",
    "aiter_history",
]
