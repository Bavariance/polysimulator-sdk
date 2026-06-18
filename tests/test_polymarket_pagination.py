"""Tests for the Phase-1 ``Paginator`` / ``Page`` mirror.

py-sdk's list-style reads (``list_markets`` etc.) return a
``polymarket.pagination.Paginator[T]`` whose public surface bots rely on:
``.first_page()`` -> ``Page`` (with ``.items``/``.has_more``/``.next_cursor``/
``.total_count``), ``.iter_items()``, ``__iter__`` over pages, and
``.from_cursor()``. The mirror's Paginator must expose the same surface so a
ported bot's ``client.list_markets(...).iter_items()`` / ``.first_page().items``
works identically on real Polymarket.
"""

from __future__ import annotations

import pytest


def test_page_is_frozen_with_pysdk_fields():
    from dataclasses import FrozenInstanceError

    from polysim_polymarket.pagination import Page

    page = Page(items=(1, 2, 3), has_more=True, next_cursor="MTA=", total_count=99)
    assert page.items == (1, 2, 3)
    assert page.has_more is True
    assert page.next_cursor == "MTA="
    assert page.total_count == 99
    # Frozen, mirroring py-sdk's Page dataclass.
    with pytest.raises(FrozenInstanceError):
        page.has_more = False  # type: ignore[misc]


def test_page_optional_fields_default():
    from polysim_polymarket.pagination import Page

    page = Page(items=(), has_more=False)
    assert page.next_cursor is None
    assert page.total_count is None


def test_paginator_first_page_calls_fetch_with_initial_cursor():
    from polysim_polymarket.pagination import Page, Paginator

    seen: list[str | None] = []

    def fetch(cursor):
        seen.append(cursor)
        return Page(items=("a",), has_more=False)

    pag = Paginator(fetch=fetch)
    page = pag.first_page()
    assert page.items == ("a",)
    # Unfetched paginator starts at the None cursor (py-sdk default).
    assert seen == [None]


def test_paginator_iter_items_walks_all_pages():
    from polysim_polymarket.pagination import Page, Paginator

    pages = {
        None: Page(items=(1, 2), has_more=True, next_cursor="c1"),
        "c1": Page(items=(3,), has_more=False),
    }

    pag = Paginator(fetch=lambda cursor: pages[cursor])
    assert list(pag.iter_items()) == [1, 2, 3]


def test_paginator_iter_yields_pages():
    from polysim_polymarket.pagination import Page, Paginator

    pages = {
        None: Page(items=(1,), has_more=True, next_cursor="c1"),
        "c1": Page(items=(2,), has_more=False),
    }
    pag = Paginator(fetch=lambda cursor: pages[cursor])
    collected = list(pag)
    assert len(collected) == 2
    assert collected[0].items == (1,)
    assert collected[1].items == (2,)


def test_paginator_from_cursor_none_is_empty():
    from polysim_polymarket.pagination import Paginator

    pag = Paginator(fetch=lambda cursor: (_ for _ in ()).throw(AssertionError("must not fetch")))
    empty = pag.from_cursor(None)
    # from_cursor(None) yields an empty paginator that never fetches.
    assert empty.first_page().items == ()
    assert list(empty.iter_items()) == []


def test_paginator_raises_when_has_more_without_next_cursor():
    """has_more=True but next_cursor=None is a malformed page — raise, not stop.

    py-sdk's Paginator raises ``UnexpectedResponseError`` in this case rather
    than silently truncating the result set (which would hide pages from a bot).
    The mirror must do the same so a ported bot sees identical behaviour.
    """
    from polysim_polymarket.errors import UnexpectedResponseError
    from polysim_polymarket.pagination import Page, Paginator

    pag = Paginator(fetch=lambda cursor: Page(items=(1, 2), has_more=True, next_cursor=None))
    with pytest.raises(UnexpectedResponseError):
        list(pag.iter_items())
    with pytest.raises(UnexpectedResponseError):
        list(pag)


def test_paginator_unexpected_response_subclasses_polyexception():
    """The pagination error stays catchable by the shared ``PolyException`` base."""
    from polysim_polymarket.errors import PolyException, UnexpectedResponseError

    assert issubclass(UnexpectedResponseError, PolyException)
