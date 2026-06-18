"""Paginator + Page mirroring py-sdk's ``polymarket.pagination`` (Phase-1 subset).

py-sdk's list-style reads return a ``Paginator[T]`` yielding ``Page[T]`` objects.
A ported bot drives them with ``.first_page()``, ``.iter_items()``, iteration
over pages, and ``.from_cursor()`` — so the mirror exposes the **same** public
surface, structurally identical to py-sdk's, backed by PolySimulator's
offset-paginated REST reads via the shared cursor<->offset helpers.

Iteration matches py-sdk's strictness: a page reporting ``has_more=True`` with
``next_cursor=None`` is malformed, so iteration raises
:class:`~polysim_polymarket.errors.UnexpectedResponseError` rather than silently
truncating the result set (which would hide pages from a bot).

Deliberately deferred (documented): the ``.to_arrow`` / ``.to_pandas`` /
``.to_polars`` dataframe exports py-sdk's ``Paginator`` / ``Page`` also carry.
They require py-sdk's heavy ``_frames_bridge`` plus optional pandas / polars /
pyarrow deps. The asymmetry is safe: a bot developed against this mirror simply
won't have used those methods, and the **real** SDK being a superset means the
swap to real Polymarket only *adds* them — never breaks. They are omitted (not
stubbed to raise) so they don't pass a ``hasattr`` check and fail later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from polysim_polymarket.errors import UnexpectedResponseError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """One page of results. Mirrors ``polymarket.pagination.Page``.

    Frozen + slotted so a ported bot reads ``page.items`` / ``page.has_more`` /
    ``page.next_cursor`` / ``page.total_count`` with the same attribute names +
    types it gets on real Polymarket.
    """

    items: tuple[T, ...]
    has_more: bool
    next_cursor: str | None = None
    total_count: int | None = None


class Paginator(Generic[T]):
    """Lazily-fetched paginator. Mirrors ``polymarket.pagination.Paginator``.

    Constructed with a ``fetch(cursor) -> Page`` closure and an optional
    ``initial_cursor``, exactly like py-sdk. The mirror's list methods build the
    ``fetch`` closure over PolySimulator's offset-paginated REST reads.
    """

    def __init__(
        self,
        fetch: Callable[[str | None], Page[T]],
        initial_cursor: str | None = None,
    ) -> None:
        self._fetch = fetch
        self._initial_cursor = initial_cursor

    def __repr__(self) -> str:
        return "Paginator(unfetched — call .first_page() or iterate)"

    def first_page(self) -> Page[T]:
        """Fetch the first page (from ``initial_cursor``)."""
        return self._fetch(self._initial_cursor)

    def from_cursor(self, cursor: str | None) -> Paginator[T]:
        """A paginator resuming at ``cursor``; an empty one for ``None``."""
        if cursor is None:
            return cast("Paginator[T]", _EmptyPaginator())
        return Paginator(self._fetch, initial_cursor=cursor)

    def __iter__(self) -> Iterator[Page[T]]:
        return self._iter_pages()

    def iter_items(self) -> Iterator[T]:
        """Stream individual items across all pages."""
        for page in self._iter_pages():
            yield from page.items

    def _iter_pages(self) -> Iterator[Page[T]]:
        cursor = self._initial_cursor
        while True:
            page = self._fetch(cursor)
            yield page
            if not page.has_more:
                return
            # has_more=True with no cursor is a malformed page: raise rather than
            # silently truncate (mirrors py-sdk's Paginator._iter_pages).
            if page.next_cursor is None:
                raise UnexpectedResponseError(
                    "Paginated response set has_more=True without a next cursor."
                )
            cursor = page.next_cursor


class _EmptyPaginator(Paginator[object]):
    """A paginator that never fetches — yields one empty page and stops.

    Mirrors py-sdk's ``_EmptyPaginator`` so ``paginator.from_cursor(None)``
    (pagination exhausted) returns a safe, fetch-free empty paginator.
    """

    def __init__(self) -> None:
        super().__init__(fetch=_empty_fetch, initial_cursor=None)

    def first_page(self) -> Page[object]:
        return Page(items=(), has_more=False)

    def from_cursor(self, cursor: str | None) -> Paginator[object]:
        if cursor is None:
            return self
        return Paginator(self._fetch, initial_cursor=cursor)

    def _iter_pages(self) -> Iterator[Page[object]]:
        return iter(())


def _empty_fetch(_cursor: str | None) -> Page[object]:
    return Page(items=(), has_more=False)


class AsyncPaginator(Generic[T]):
    """Lazily-fetched async paginator. Mirrors ``polymarket.pagination.AsyncPaginator``.

    The async twin of :class:`Paginator`: constructed with an **awaitable**
    ``fetch(cursor) -> Page`` closure and an optional ``initial_cursor``. A
    ported bot drives it with ``await .first_page()``, ``async for item in
    .iter_items()``, ``async for page in paginator`` (``__aiter__``), and the
    synchronous ``.from_cursor()`` — exactly py-sdk's surface. The mirror's async
    list methods build the ``fetch`` closure over PolySimulator's offset-paginated
    REST reads via the shared cursor<->offset helpers.

    Iteration matches py-sdk's strictness: a page reporting ``has_more=True`` with
    ``next_cursor=None`` is malformed, so iteration raises
    :class:`~polysim_polymarket.errors.UnexpectedResponseError` rather than
    silently truncating the result set.

    The dataframe exports (``to_arrow`` / ``to_pandas`` / ``to_polars``) py-sdk's
    ``AsyncPaginator`` also carries are deliberately deferred for the same reason
    as the sync :class:`Paginator` — see this module's docstring.
    """

    def __init__(
        self,
        fetch: Callable[[str | None], Awaitable[Page[T]]],
        initial_cursor: str | None = None,
    ) -> None:
        self._fetch = fetch
        self._initial_cursor = initial_cursor

    def __repr__(self) -> str:
        return "AsyncPaginator(unfetched — call await .first_page() or async-iterate)"

    async def first_page(self) -> Page[T]:
        """Fetch the first page (from ``initial_cursor``)."""
        return await self._fetch(self._initial_cursor)

    def from_cursor(self, cursor: str | None) -> AsyncPaginator[T]:
        """A paginator resuming at ``cursor``; an empty one for ``None``.

        Synchronous (no ``await``) — it only rebinds the cursor, matching py-sdk.
        """
        if cursor is None:
            return cast("AsyncPaginator[T]", _EmptyAsyncPaginator())
        return AsyncPaginator(self._fetch, initial_cursor=cursor)

    def __aiter__(self) -> AsyncIterator[Page[T]]:
        return self._iter_pages()

    def iter_items(self) -> AsyncIterator[T]:
        """Stream individual items across all pages."""
        return self._iter_items()

    async def _iter_pages(self) -> AsyncIterator[Page[T]]:
        cursor = self._initial_cursor
        while True:
            page = await self._fetch(cursor)
            yield page
            if not page.has_more:
                return
            # has_more=True with no cursor is a malformed page: raise rather than
            # silently truncate (mirrors py-sdk's AsyncPaginator._iter_pages).
            if page.next_cursor is None:
                raise UnexpectedResponseError(
                    "Paginated response set has_more=True without a next cursor."
                )
            cursor = page.next_cursor

    async def _iter_items(self) -> AsyncIterator[T]:
        async for page in self._iter_pages():
            for item in page.items:
                yield item


class _EmptyAsyncPaginator(AsyncPaginator[object]):
    """An async paginator that never fetches — yields one empty page and stops.

    Mirrors py-sdk's ``_EmptyAsyncPaginator`` so ``paginator.from_cursor(None)``
    (pagination exhausted) returns a safe, fetch-free empty async paginator.
    """

    def __init__(self) -> None:
        super().__init__(fetch=_empty_async_fetch, initial_cursor=None)

    async def first_page(self) -> Page[object]:
        return Page(items=(), has_more=False)

    def from_cursor(self, cursor: str | None) -> AsyncPaginator[object]:
        if cursor is None:
            return self
        return AsyncPaginator(self._fetch, initial_cursor=cursor)

    async def _iter_pages(self) -> AsyncIterator[Page[object]]:
        return
        yield  # pragma: no cover - forces this method to be an async generator


async def _empty_async_fetch(_cursor: str | None) -> Page[object]:
    return Page(items=(), has_more=False)


__all__ = ["AsyncPaginator", "Page", "Paginator"]
