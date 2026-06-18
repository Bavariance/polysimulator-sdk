"""Surface-parity tests: assert ``polysim_polymarket.PublicClient`` matches the
REAL Polymarket py-sdk (``polymarket-client``).

This is the **drop-in anchor**. The premise of the whole package is that a bot
ported from ``polysim_polymarket`` to ``polymarket`` swaps only the import
prefix + host + auth — so for every method a bot can call, the keyword names and
parameter kinds must be byte-for-byte identical to py-sdk's. These tests load the
real ``polymarket`` package (skipped if it isn't installed) and diff our
``PublicClient`` against it method-by-method, so a signature drift fails CI.

We compare **parameter names + kinds** (not type annotations or defaults): a
port is mechanical iff the same call expression binds the same way on both
clients. py-sdk-properties (e.g. ``environment``) must be properties on ours too,
since a bot reads them without call parens.
"""

from __future__ import annotations

import inspect

import pytest

polymarket = pytest.importorskip("polymarket")

from polymarket.clients.public import PublicClient as RealPublicClient  # noqa: E402

from polysim_polymarket import PublicClient as MirrorPublicClient  # noqa: E402
from tests._parity_helpers import _param_signature  # noqa: E402

# The Phase-1 CLOB market-data READ subset we mirror. Each must exist on both
# clients with identical keyword names + parameter kinds.
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

# py-sdk members that are PROPERTIES (read without call parens) — must be
# properties on the mirror too, else a ported ``client.environment.clob_url``
# would either crash (method) or read the wrong thing.
PHASE1_PROPERTIES = ["environment"]


def test_phase1_methods_exist_on_mirror():
    """Every Phase-1 method py-sdk exposes must exist on the mirror."""
    for name in PHASE1_METHODS:
        assert hasattr(RealPublicClient, name), f"py-sdk lacks {name} (test list stale?)"
        assert hasattr(MirrorPublicClient, name), f"mirror is missing PublicClient.{name}"


@pytest.mark.parametrize("name", PHASE1_METHODS)
def test_phase1_method_signature_matches_pysdk(name: str):
    """The mirror's keyword names + parameter kinds match py-sdk's exactly."""
    real = _param_signature(RealPublicClient, name)
    mirror = _param_signature(MirrorPublicClient, name)
    assert mirror == real, (
        f"PublicClient.{name} signature drift:\n"
        f"  py-sdk: {real}\n"
        f"  mirror: {mirror}"
    )


@pytest.mark.parametrize("name", PHASE1_PROPERTIES)
def test_phase1_properties_are_properties_on_mirror(name: str):
    """Members py-sdk exposes as @property must be properties on the mirror too."""
    real_member = inspect.getattr_static(RealPublicClient, name)
    mirror_member = inspect.getattr_static(MirrorPublicClient, name)
    assert isinstance(real_member, property), f"py-sdk's {name} is not a property (test stale?)"
    assert isinstance(mirror_member, property), (
        f"PublicClient.{name} must be a @property (py-sdk exposes it as one); "
        f"got {type(mirror_member).__name__}"
    )
