"""Surface-parity tests: assert ``polysim_polymarket.AsyncSecureClient`` matches
the REAL Polymarket py-sdk (``polymarket.clients.async_secure.AsyncSecureClient``)
AND stays the structural async twin of the mirror's own sync ``SecureClient``.

The premise of the whole package is that a bot ported from ``polysim_polymarket``
to ``polymarket`` swaps only the import prefix + host + auth. ``AsyncSecureClient``
is the async twin of ``SecureClient``: it exposes the SAME surface as the sync
client, with every per-request method an ``async def`` coroutine (and the
list-style reads kept synchronous, returning an ``AsyncPaginator`` — exactly as
py-sdk's async client does).

These tests load the real ``polymarket`` package (skipped if it isn't installed)
and diff our ``AsyncSecureClient`` against it method-by-method (keyword names +
parameter kinds), assert each method's coroutine/sync flavour matches py-sdk's,
assert the construction surface (``create`` async classmethod, ``close`` /
``__aenter__`` / ``__aexit__`` async context-manager protocol), and assert the
class is importable straight off the package root.

The method set under test is the mirror's CURRENT sync ``SecureClient`` surface —
the async twin must mirror exactly that (no more, no less): the async client must
not grow a method the sync client lacks (a later gate's surface) nor drop one the
sync client has.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

polymarket = pytest.importorskip("polymarket")

from polymarket.clients.async_secure import AsyncSecureClient as RealAsyncSecureClient  # noqa: E402

from polysim_polymarket import AsyncSecureClient as MirrorAsyncSecureClient  # noqa: E402
from polysim_polymarket import SecureClient as MirrorSecureClient  # noqa: E402
from tests._parity_helpers import _param_signature  # noqa: E402


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


# The async twin must mirror exactly the mirror's CURRENT sync SecureClient
# surface. Derive it from the sync client so the list can never drift stale.
MIRROR_SYNC_METHODS = sorted(_public_methods(MirrorSecureClient))

# py-sdk members that are PROPERTIES (read without call parens) — must be
# properties on the async mirror too (a ported ``client.credentials.key`` /
# ``client.environment.clob_url`` reads them without parens).
ASYNC_PROPERTIES = ["environment", "credentials"]


def test_async_secure_client_importable_from_package_root():
    """``AsyncSecureClient`` resolves straight off the package root (prefix swap)."""
    import polysim_polymarket

    assert polysim_polymarket.AsyncSecureClient is MirrorAsyncSecureClient
    assert "AsyncSecureClient" in polysim_polymarket.__all__


# Methods that are legitimately ASYNC-ONLY — present on the async client but not
# the sync one, mirroring py-sdk exactly. ``subscribe`` (realtime streams) is
# async-only on BOTH py-sdk and the mirror: py-sdk's sync PublicClient/SecureClient
# have no ``subscribe``; only the async twins do.
ASYNC_ONLY_METHODS = {"subscribe"}


def test_async_twin_surface_equals_sync_surface_plus_async_only():
    """The async client exposes the sync client's public method set, plus the
    async-only additions (``subscribe``) and nothing else.

    No spurious extra (a later gate's surface leaking in), no dropped method.
    """
    sync_surface = _public_methods(MirrorSecureClient)
    async_surface = _public_methods(MirrorAsyncSecureClient)
    expected = sync_surface | ASYNC_ONLY_METHODS
    assert async_surface == expected, (
        "async twin surface drift vs sync SecureClient (+ async-only):\n"
        f"  only on sync:  {sorted(sync_surface - async_surface)}\n"
        f"  unexpected on async: {sorted(async_surface - expected)}"
    )


def test_subscribe_is_async_only_matching_pysdk():
    """``subscribe`` is async-only on BOTH py-sdk and the mirror: present on the
    async secure client, absent from the sync secure client."""
    from polymarket.clients.secure import SecureClient as RealSyncSecure

    assert hasattr(MirrorAsyncSecureClient, "subscribe")
    assert hasattr(RealAsyncSecureClient, "subscribe")
    assert not hasattr(MirrorSecureClient, "subscribe")
    assert not hasattr(RealSyncSecure, "subscribe")


@pytest.mark.parametrize("name", MIRROR_SYNC_METHODS)
def test_async_method_exists_on_pysdk_and_mirror(name: str):
    """Every method the async twin exposes exists on py-sdk's AsyncSecureClient."""
    assert hasattr(RealAsyncSecureClient, name), f"py-sdk async client lacks {name} (test stale?)"
    assert hasattr(MirrorAsyncSecureClient, name), f"mirror is missing AsyncSecureClient.{name}"


@pytest.mark.parametrize("name", MIRROR_SYNC_METHODS)
def test_async_method_signature_matches_pysdk(name: str):
    """The async twin's keyword names + parameter kinds match py-sdk's exactly."""
    real = _param_signature(RealAsyncSecureClient, name)
    mirror = _param_signature(MirrorAsyncSecureClient, name)
    assert mirror == real, (
        f"AsyncSecureClient.{name} signature drift:\n"
        f"  py-sdk: {real}\n"
        f"  mirror: {mirror}"
    )


@pytest.mark.parametrize("name", MIRROR_SYNC_METHODS)
def test_async_method_coroutine_flavor_matches_pysdk(name: str):
    """Each method is a coroutine fn iff py-sdk's same method is.

    The per-request reads/writes are ``async def`` coroutines; the list-style
    reads stay synchronous (they return an ``AsyncPaginator`` the bot then awaits).
    Whatever py-sdk's async client does for a given name, the mirror must do too.
    """
    real_is_coro = asyncio.iscoroutinefunction(getattr(RealAsyncSecureClient, name))
    mirror_is_coro = asyncio.iscoroutinefunction(getattr(MirrorAsyncSecureClient, name))
    assert mirror_is_coro == real_is_coro, (
        f"AsyncSecureClient.{name}: py-sdk coroutine={real_is_coro}, "
        f"mirror coroutine={mirror_is_coro} — flavour must match"
    )


@pytest.mark.parametrize("name", ASYNC_PROPERTIES)
def test_async_properties_are_properties_on_mirror(name: str):
    """Members the sync client exposes as @property are properties on the async twin."""
    sync_member = inspect.getattr_static(MirrorSecureClient, name)
    async_member = inspect.getattr_static(MirrorAsyncSecureClient, name)
    assert isinstance(sync_member, property), f"sync's {name} is not a property (test stale?)"
    assert isinstance(async_member, property), (
        f"AsyncSecureClient.{name} must be a @property; got {type(async_member).__name__}"
    )


# ── construction surface ────────────────────────────────────────────────────


def test_create_is_async_classmethod():
    """``AsyncSecureClient.create`` is an async classmethod (py-sdk's factory).

    py-sdk constructs async secure clients with ``await AsyncSecureClient.create``;
    the mirror must expose ``create`` as a coroutine classmethod too.
    """
    raw = inspect.getattr_static(MirrorAsyncSecureClient, "create")
    assert isinstance(raw, classmethod), "create must be a classmethod"
    assert asyncio.iscoroutinefunction(MirrorAsyncSecureClient.create), (
        "create must be an async (coroutine) classmethod"
    )
    # py-sdk's create is also an async classmethod.
    real_raw = inspect.getattr_static(RealAsyncSecureClient, "create")
    assert isinstance(real_raw, classmethod)
    assert asyncio.iscoroutinefunction(RealAsyncSecureClient.create)


def test_close_is_async():
    """``close`` is a coroutine (``await client.close()``), matching py-sdk."""
    assert asyncio.iscoroutinefunction(MirrorAsyncSecureClient.close)
    assert asyncio.iscoroutinefunction(RealAsyncSecureClient.close)


def test_async_context_manager_protocol():
    """``__aenter__`` / ``__aexit__`` exist and are coroutines (async ``with``)."""
    for dunder in ("__aenter__", "__aexit__"):
        assert hasattr(MirrorAsyncSecureClient, dunder), f"missing {dunder}"
        assert asyncio.iscoroutinefunction(getattr(MirrorAsyncSecureClient, dunder)), (
            f"{dunder} must be a coroutine"
        )
        assert asyncio.iscoroutinefunction(getattr(RealAsyncSecureClient, dunder))


def test_no_sync_context_manager_protocol():
    """The async client must NOT expose the SYNC ``__enter__`` / ``__exit__``.

    A bot must drive it with ``async with`` — a plain ``with`` would silently not
    close the async transport. py-sdk's async client likewise has only the async
    protocol.
    """
    assert not hasattr(RealAsyncSecureClient, "__enter__"), "py-sdk async client has __enter__?"
    assert not hasattr(MirrorAsyncSecureClient, "__enter__")
    assert not hasattr(MirrorAsyncSecureClient, "__exit__")
