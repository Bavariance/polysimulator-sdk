"""Tests for the Phase-1 ``AsyncPaginator`` mirror.

py-sdk's **async** list-style reads (``AsyncPublicClient.list_markets`` etc.)
return a ``polymarket.pagination.AsyncPaginator[T]`` whose public surface bots
rely on: ``await .first_page()`` -> ``Page``, ``async for item in .iter_items()``,
``async for page in paginator`` (``__aiter__``), and ``.from_cursor()`` (sync).
The mirror's ``AsyncPaginator`` must expose the same surface so a ported bot's
``async for m in client.list_markets(...).iter_items()`` works identically on
real Polymarket.

The ``Page`` object is shared with the sync paginator (same frozen dataclass);
only the *driver* differs (awaitable fetch, async iteration). These tests assert
the async surface, async iteration across pages, the empty-from-cursor short
circuit, and the malformed-page (``has_more=True`` + ``next_cursor=None``) raise
— the same strictness the sync paginator enforces.
"""

from __future__ import annotations

import pytest

from polysim_polymarket.pagination import AsyncPaginator, Page


async def test_async_paginator_first_page_awaits_fetch_with_initial_cursor():
    seen: list[str | None] = []

    async def fetch(cursor):
        seen.append(cursor)
        return Page(items=("a",), has_more=False)

    pag = AsyncPaginator(fetch=fetch)
    page = await pag.first_page()
    assert page.items == ("a",)
    # Unfetched paginator starts at the None cursor (py-sdk default).
    assert seen == [None]


async def test_async_paginator_iter_items_walks_all_pages():
    pages = {
        None: Page(items=(1, 2), has_more=True, next_cursor="c1"),
        "c1": Page(items=(3,), has_more=False),
    }

    async def fetch(cursor):
        return pages[cursor]

    pag = AsyncPaginator(fetch=fetch)
    out = [item async for item in pag.iter_items()]
    assert out == [1, 2, 3]


async def test_async_paginator_aiter_yields_pages():
    pages = {
        None: Page(items=(1,), has_more=True, next_cursor="c1"),
        "c1": Page(items=(2,), has_more=False),
    }

    async def fetch(cursor):
        return pages[cursor]

    pag = AsyncPaginator(fetch=fetch)
    collected = [page async for page in pag]
    assert len(collected) == 2
    assert collected[0].items == (1,)
    assert collected[1].items == (2,)


async def test_async_paginator_from_cursor_none_is_empty():
    async def fetch(cursor):
        raise AssertionError("must not fetch")

    pag = AsyncPaginator(fetch=fetch)
    empty = pag.from_cursor(None)
    # from_cursor(None) yields an empty paginator that never fetches.
    assert (await empty.first_page()).items == ()
    assert [item async for item in empty.iter_items()] == []


async def test_async_paginator_from_cursor_value_resumes():
    seen: list[str | None] = []

    async def fetch(cursor):
        seen.append(cursor)
        return Page(items=("x",), has_more=False)

    pag = AsyncPaginator(fetch=fetch)
    resumed = pag.from_cursor("c5")
    page = await resumed.first_page()
    assert page.items == ("x",)
    # Resuming at a cursor threads it into the first fetch.
    assert seen == ["c5"]


async def test_async_paginator_raises_when_has_more_without_next_cursor():
    """has_more=True but next_cursor=None is a malformed page — raise, not stop.

    py-sdk's AsyncPaginator raises ``UnexpectedResponseError`` here rather than
    silently truncating the result set. The mirror must do the same.
    """
    from polysim_polymarket.errors import UnexpectedResponseError

    async def fetch(cursor):
        return Page(items=(1, 2), has_more=True, next_cursor=None)

    pag = AsyncPaginator(fetch=fetch)
    with pytest.raises(UnexpectedResponseError):
        _ = [item async for item in pag.iter_items()]
    with pytest.raises(UnexpectedResponseError):
        _ = [page async for page in pag]


def test_async_paginator_repr_does_not_fetch():
    async def fetch(cursor):
        raise AssertionError("repr must not fetch")

    pag = AsyncPaginator(fetch=fetch)
    assert "AsyncPaginator" in repr(pag)
