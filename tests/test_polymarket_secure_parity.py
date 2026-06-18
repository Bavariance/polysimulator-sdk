"""Surface-parity tests: assert ``polysim_polymarket.SecureClient`` matches the
REAL Polymarket py-sdk (``polymarket-client``) for the G2 subset.

The premise of the whole package is that a bot ported from ``polysim_polymarket``
to ``polymarket`` swaps only the import prefix + host + auth — so for every method
a bot can call, the keyword names and parameter kinds must be byte-for-byte
identical to py-sdk's. These tests load the real ``polymarket`` package (skipped
if it isn't installed) and diff our ``SecureClient`` against it method-by-method,
so a signature drift fails CI.

G2 scope (this gate): the CLOB market-data READS the secure client shares with
the public client, the auth-bootstrap surface (``fetch_api_keys`` /
``delete_api_key`` / ``credentials``), and the account/version/liveness reads
(``get_balance_allowance`` / ``is_gasless_ready`` / ``get_closed_only_mode`` /
``get_notifications``) plus the order reads (``get_order`` / ``list_open_orders``
/ ``list_account_trades``). Trading (G3) is now implemented — its surface
parity lives in ``test_polymarket_secure_trading_parity.py`` — and the on-chain /
rewards / builder / RFQ (G4) surface in ``test_polymarket_secure_g4_parity.py``.
What is still deferred at the secure surface is the ASYNC client (G5) and the
STREAMS (G6); this module asserts their async/streaming entrypoints are ABSENT
from the SYNC client (and that it exposes no coroutine methods).

We compare **parameter names + kinds** (not type annotations or defaults): a port
is mechanical iff the same call expression binds the same way on both clients.
py-sdk-properties (e.g. ``environment`` / ``credentials``) must be properties on
ours too, since a bot reads them without call parens.
"""

from __future__ import annotations

import inspect

import pytest

polymarket = pytest.importorskip("polymarket")

from polymarket.clients.secure import SecureClient as RealSecureClient  # noqa: E402

from polysim_polymarket import SecureClient as MirrorSecureClient  # noqa: E402
from tests._parity_helpers import _param_signature  # noqa: E402

# ── G2 implemented surface ──────────────────────────────────────────────────

# CLOB market-data READS the secure client shares with the public client. Each
# must exist on both clients with identical keyword names + parameter kinds.
G2_READ_METHODS = [
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

# Auth bootstrap + account/version/liveness + order reads.
G2_AUTH_ACCOUNT_METHODS = [
    "fetch_api_keys",
    "delete_api_key",
    "get_balance_allowance",
    "is_gasless_ready",
    "get_closed_only_mode",
    "get_notifications",
    "get_order",
    "list_open_orders",
    "list_account_trades",
]

G2_METHODS = G2_READ_METHODS + G2_AUTH_ACCOUNT_METHODS

# py-sdk members that are PROPERTIES (read without call parens) — must be
# properties on the mirror too, else a ported ``client.credentials.key`` /
# ``client.environment.clob_url`` would crash or read the wrong thing.
G2_PROPERTIES = ["environment", "credentials"]

# ASYNC / STREAMING entrypoints that belong to LATER gates and must NOT exist on
# the SYNC mirror SecureClient — their absence is what keeps the sync client a
# purely-synchronous surface.
#
# G3 trading and the G4 on-chain + rewards/builder/scoring surface are now
# IMPLEMENTED, so those names are no longer asserted absent here (their parity
# lives in ``test_polymarket_secure_trading_parity.py`` /
# ``test_polymarket_secure_g4_parity.py``). What IS still deferred at the secure
# surface is the ASYNC client (G5 ``AsyncSecureClient``) and the STREAMS (G6).
# Each name below is genuinely present on py-sdk's ``AsyncSecureClient`` (or its
# async-context-manager / stream surface) but NOT on py-sdk's SYNC ``SecureClient``
# — so asserting the mirror's sync client lacks them confirms the sync surface
# didn't accidentally grow an async/streaming entrypoint ahead of its gate.
SYNC_CLIENT_MUST_NOT_HAVE = [
    "subscribe",  # G6 stream entrypoint — on AsyncSecureClient, not the sync client
    "open_rfq_session",  # async-only RFQ session opener on AsyncSecureClient
    "__aenter__",  # async context-manager protocol (AsyncSecureClient only)
    "__aexit__",
]


def test_g2_methods_exist_on_mirror():
    """Every G2 method py-sdk exposes must exist on the mirror."""
    for name in G2_METHODS:
        assert hasattr(RealSecureClient, name), f"py-sdk lacks {name} (test list stale?)"
        assert hasattr(MirrorSecureClient, name), f"mirror is missing SecureClient.{name}"


@pytest.mark.parametrize("name", G2_METHODS)
def test_g2_method_signature_matches_pysdk(name: str):
    """The mirror's keyword names + parameter kinds match py-sdk's exactly."""
    real = _param_signature(RealSecureClient, name)
    mirror = _param_signature(MirrorSecureClient, name)
    assert mirror == real, (
        f"SecureClient.{name} signature drift:\n"
        f"  py-sdk: {real}\n"
        f"  mirror: {mirror}"
    )


@pytest.mark.parametrize("name", G2_PROPERTIES)
def test_g2_properties_are_properties_on_mirror(name: str):
    """Members py-sdk exposes as @property must be properties on the mirror too."""
    real_member = inspect.getattr_static(RealSecureClient, name)
    mirror_member = inspect.getattr_static(MirrorSecureClient, name)
    assert isinstance(real_member, property), f"py-sdk's {name} is not a property (test stale?)"
    assert isinstance(mirror_member, property), (
        f"SecureClient.{name} must be a @property (py-sdk exposes it as one); "
        f"got {type(mirror_member).__name__}"
    )


@pytest.mark.parametrize("name", SYNC_CLIENT_MUST_NOT_HAVE)
def test_async_stream_entrypoints_absent_on_sync_mirror(name: str):
    """Async/streaming entrypoints (G5/G6) must NOT exist on the SYNC mirror client.

    Each name is genuinely on py-sdk's ``AsyncSecureClient`` but NOT on py-sdk's
    SYNC ``SecureClient`` (asserted here so the test can't silently rot into a
    vacuous check). The mirror's sync client must likewise lack it — otherwise the
    sync surface grew an async/streaming entrypoint ahead of its gate.
    """
    real_async = pytest.importorskip("polymarket.clients.async_secure")
    RealAsyncSecureClient = real_async.AsyncSecureClient
    assert hasattr(RealAsyncSecureClient, name), (
        f"py-sdk's AsyncSecureClient lacks {name} (test list stale?)"
    )
    assert not hasattr(RealSecureClient, name), (
        f"py-sdk's SYNC SecureClient unexpectedly has {name} (test list stale?)"
    )
    assert not hasattr(MirrorSecureClient, name), (
        f"SecureClient.{name} is an async/streaming entrypoint (G5/G6) but present "
        f"on the SYNC mirror client — the sync surface must not grow it early."
    )


def test_sync_mirror_client_has_no_coroutine_methods():
    """The sync mirror SecureClient exposes ZERO coroutine methods.

    A bot ports a sync client by calling its methods directly (no ``await``); if
    any public method were a coroutine function, the port would silently get an
    un-awaited coroutine. The async surface is G5's ``AsyncSecureClient``, a
    separate class — the sync client must stay fully synchronous.
    """
    coroutine_methods = [
        name
        for name in dir(MirrorSecureClient)
        if not name.startswith("__")
        and inspect.iscoroutinefunction(getattr(MirrorSecureClient, name, None))
    ]
    assert coroutine_methods == [], (
        f"sync SecureClient must expose no coroutine methods; found: {coroutine_methods}"
    )
