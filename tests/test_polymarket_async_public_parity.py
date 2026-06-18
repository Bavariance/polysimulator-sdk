"""Surface-parity tests: assert ``polysim_polymarket.AsyncPublicClient`` matches
the REAL Polymarket py-sdk (``polymarket.AsyncPublicClient``).

The async twin of ``test_polymarket_public_parity``. The premise is identical: a
bot ported from ``polysim_polymarket`` to ``polymarket`` swaps only the import
prefix + host + auth — so for every async method a bot can ``await``, the keyword
names + parameter kinds must be byte-for-byte identical to py-sdk's, AND the
method must be a coroutine on both sides (so ``await client.get_midpoint(...)``
binds the same way). ``list_markets`` is the one read that is **synchronous** on
py-sdk's async client (it returns an ``AsyncPaginator`` immediately, no await) —
the mirror must match that too, else a ported ``client.list_markets(...)`` either
needs an erroneous ``await`` or breaks.

These load the real ``polymarket`` package (skipped if not installed) and diff
the mirror's ``AsyncPublicClient`` against it method-by-method, so a signature or
sync/async drift fails CI.
"""

from __future__ import annotations

import inspect

import pytest

polymarket = pytest.importorskip("polymarket")

from polymarket.clients.async_public import AsyncPublicClient as RealAsyncPublicClient  # noqa: E402

from polysim_polymarket import AsyncPublicClient as MirrorAsyncPublicClient  # noqa: E402
from tests._parity_helpers import _param_signature  # noqa: E402

# The Phase-1 CLOB market-data READ subset we mirror on the async client. Every
# one must exist on both clients with identical keyword names + parameter kinds.
PHASE1_METHODS = [
    "get_order_book",
    "get_order_books",
    "get_midpoint",
    "get_midpoints",
    "get_price",
    "get_prices",
    "get_spread",
    "get_spreads",
    "get_last_trade_price",
    "get_last_trade_prices",
    "get_price_history",
    "estimate_market_price",
    "list_markets",
    "get_market",
]

# Methods that are ``async def`` (coroutines) on py-sdk's async client — i.e.
# everything EXCEPT list_markets, which returns an AsyncPaginator synchronously.
ASYNC_METHODS = [m for m in PHASE1_METHODS if m != "list_markets"]
SYNC_METHODS = ["list_markets"]

PHASE1_PROPERTIES = ["environment"]


def test_phase1_async_methods_exist_on_mirror():
    """Every Phase-1 method py-sdk's async client exposes must exist on the mirror."""
    for name in PHASE1_METHODS:
        assert hasattr(RealAsyncPublicClient, name), f"py-sdk lacks {name} (test list stale?)"
        assert hasattr(MirrorAsyncPublicClient, name), f"mirror missing AsyncPublicClient.{name}"


@pytest.mark.parametrize("name", PHASE1_METHODS)
def test_phase1_async_method_signature_matches_pysdk(name: str):
    """The mirror's keyword names + parameter kinds match py-sdk's exactly."""
    real = _param_signature(RealAsyncPublicClient, name)
    mirror = _param_signature(MirrorAsyncPublicClient, name)
    assert mirror == real, (
        f"AsyncPublicClient.{name} signature drift:\n"
        f"  py-sdk: {real}\n"
        f"  mirror: {mirror}"
    )


@pytest.mark.parametrize("name", ASYNC_METHODS)
def test_phase1_async_methods_are_coroutines_on_both(name: str):
    """A method py-sdk defines with ``async def`` must be a coroutine on the mirror.

    A ported ``await client.get_midpoint(...)`` only binds if BOTH are coroutine
    functions. A mirror that made one of these synchronous would force the port
    to drop the ``await`` — exactly the drift this anchor prevents.
    """
    assert inspect.iscoroutinefunction(getattr(RealAsyncPublicClient, name)), (
        f"py-sdk's {name} is not async (test stale?)"
    )
    assert inspect.iscoroutinefunction(getattr(MirrorAsyncPublicClient, name)), (
        f"AsyncPublicClient.{name} must be ``async def`` (py-sdk defines it async)"
    )


@pytest.mark.parametrize("name", SYNC_METHODS)
def test_phase1_sync_methods_are_not_coroutines_on_both(name: str):
    """``list_markets`` returns an AsyncPaginator synchronously on py-sdk's async
    client (no ``await``); the mirror must match — a ported ``client.list_markets(...)``
    must NOT require an await."""
    assert not inspect.iscoroutinefunction(getattr(RealAsyncPublicClient, name)), (
        f"py-sdk's {name} is unexpectedly async (test stale?)"
    )
    assert not inspect.iscoroutinefunction(getattr(MirrorAsyncPublicClient, name)), (
        f"AsyncPublicClient.{name} must be a plain (sync) method returning an "
        f"AsyncPaginator, matching py-sdk"
    )


@pytest.mark.parametrize("name", PHASE1_PROPERTIES)
def test_phase1_async_properties_are_properties_on_mirror(name: str):
    """Members py-sdk exposes as @property must be properties on the mirror too."""
    real_member = inspect.getattr_static(RealAsyncPublicClient, name)
    mirror_member = inspect.getattr_static(MirrorAsyncPublicClient, name)
    assert isinstance(real_member, property), f"py-sdk's {name} is not a property (test stale?)"
    assert isinstance(mirror_member, property), (
        f"AsyncPublicClient.{name} must be a @property; got {type(mirror_member).__name__}"
    )


def test_async_close_and_context_manager_surface():
    """py-sdk's async client closes via ``await close()`` and is an async context
    manager (``async with``). The mirror must expose both, plus an ``aclose``
    alias that matches the underlying AsyncPolySimClient.aclose() naming."""
    assert inspect.iscoroutinefunction(RealAsyncPublicClient.close)
    assert inspect.iscoroutinefunction(MirrorAsyncPublicClient.close)
    assert hasattr(MirrorAsyncPublicClient, "__aenter__")
    assert hasattr(MirrorAsyncPublicClient, "__aexit__")
    assert inspect.iscoroutinefunction(MirrorAsyncPublicClient.__aenter__)
    assert inspect.iscoroutinefunction(MirrorAsyncPublicClient.__aexit__)
    # aclose is a mirror convenience (the real client only ships ``close``).
    assert inspect.iscoroutinefunction(MirrorAsyncPublicClient.aclose)
