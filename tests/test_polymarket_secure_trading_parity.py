"""Surface-parity tests for ``polysim_polymarket.SecureClient`` TRADING (G3).

The drop-in premise: a bot ported from ``polysim_polymarket`` to ``polymarket``
swaps only the import prefix + host + auth, so every trading method a bot can
call — ``create_limit_order`` / ``create_market_order`` / ``place_limit_order`` /
``place_market_order`` / ``post_order`` / ``post_orders`` / ``cancel_order`` /
``cancel_orders`` / ``cancel_all`` / ``cancel_market_orders`` — must have
keyword names + parameter kinds byte-for-byte identical to py-sdk's. These tests
load the real ``polymarket`` package (skipped if absent) and diff the mirror's
``SecureClient`` against it method-by-method, so a signature drift fails CI.

A second axis: the root TRADING TYPES py-sdk's ``polymarket`` package exposes
(``OrderType`` / ``MarketOrderType`` / ``SignedOrder`` / ``OrderResponse`` /
``OrderResponseErrorCode`` / ``CancelOrdersResponse`` and the already-present
``OrderSide``) must re-export off the mirror's root too — and py-sdk's
dataclass-arg types ``OrderArgs`` / ``MarketOrderArgs`` (which py-sdk does NOT
have) must NOT appear, on either the root or as a ``SecureClient`` signature
leak. py-sdk uses FLAT KEYWORD ARGS, never the v1 mirror's dataclasses.
"""

from __future__ import annotations

import inspect

import pytest

polymarket = pytest.importorskip("polymarket")

from polymarket.clients.secure import SecureClient as RealSecureClient  # noqa: E402

from polysim_polymarket import SecureClient as MirrorSecureClient  # noqa: E402
from tests._parity_helpers import _param_signature  # noqa: E402

# ── G3 trading surface ──────────────────────────────────────────────────────

G3_TRADING_METHODS = [
    "create_limit_order",
    "create_market_order",
    "place_limit_order",
    "place_market_order",
    "post_order",
    "post_orders",
    "cancel_order",
    "cancel_orders",
    "cancel_all",
    "cancel_market_orders",
]

# Root trading TYPES py-sdk exposes off the ``polymarket`` package root. ``OrderSide``
# is already present from G2; the rest are the G3 additions.
G3_ROOT_TRADING_TYPES = [
    "OrderType",
    "MarketOrderType",
    "SignedOrder",
    "OrderResponse",
    "OrderResponseErrorCode",
    "CancelOrdersResponse",
    "OrderSide",
    "OrderPostStatus",
    "TickSize",
]

# py-sdk has NEITHER of these — they're the v1 mirror's dataclasses. Surfacing
# them anywhere on the mirror would break the prefix swap.
PYSDK_ABSENT_DATACLASSES = ["OrderArgs", "MarketOrderArgs"]


def test_g3_trading_methods_exist_on_mirror():
    """Every G3 trading method py-sdk exposes must now exist on the mirror."""
    for name in G3_TRADING_METHODS:
        assert hasattr(RealSecureClient, name), f"py-sdk lacks {name} (test list stale?)"
        assert hasattr(MirrorSecureClient, name), f"mirror is missing SecureClient.{name}"


@pytest.mark.parametrize("name", G3_TRADING_METHODS)
def test_g3_trading_signature_matches_pysdk(name: str):
    """The mirror's keyword names + parameter kinds match py-sdk's exactly."""
    real = _param_signature(RealSecureClient, name)
    mirror = _param_signature(MirrorSecureClient, name)
    assert mirror == real, (
        f"SecureClient.{name} signature drift:\n"
        f"  py-sdk: {real}\n"
        f"  mirror: {mirror}"
    )


@pytest.mark.parametrize("name", G3_ROOT_TRADING_TYPES)
def test_g3_root_trading_types_reexported(name: str):
    """Each root trading type py-sdk exports is re-exported off the mirror root."""
    import polysim_polymarket

    assert hasattr(polymarket, name), f"py-sdk root lacks {name} (test list stale?)"
    assert hasattr(polysim_polymarket, name), f"mirror root is missing {name}"
    assert name in polysim_polymarket.__all__, f"{name} not in mirror __all__"


@pytest.mark.parametrize("name", PYSDK_ABSENT_DATACLASSES)
def test_orderargs_dataclasses_absent_from_pysdk_and_mirror_root(name: str):
    """``OrderArgs`` / ``MarketOrderArgs`` are NOT on py-sdk's root, nor the mirror's.

    py-sdk uses flat keyword args; surfacing the v1 mirror's dataclasses at the
    root would let a bot write ``from polysim_polymarket import OrderArgs`` that
    breaks on the prefix swap to ``from polymarket import OrderArgs``.
    """
    import polysim_polymarket

    assert not hasattr(polymarket, name), f"py-sdk root unexpectedly has {name} (test stale?)"
    assert not hasattr(polysim_polymarket, name), (
        f"mirror root must NOT surface {name} (py-sdk has no such dataclass)"
    )
    assert name not in polysim_polymarket.__all__


def test_secure_create_market_order_takes_no_dataclass_arg():
    """``create_market_order`` binds flat kwargs, never an ``order_args`` dataclass.

    The v1 mirror's ``create_market_order(order_args: MarketOrderArgs)`` shape must
    NOT leak through — py-sdk's signature is flat keyword-only args.
    """
    params = dict(inspect.signature(MirrorSecureClient.create_market_order).parameters)
    assert "order_args" not in params
    assert "amount" in params and "shares" in params and "token_id" in params
