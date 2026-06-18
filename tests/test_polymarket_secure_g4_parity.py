"""Surface-parity tests for the G4 SecureClient surface: on-chain paper no-ops,
the rewards/scoring honest stubs, and the builder/RFQ NotImplementedError methods.

The same premise as the G2/G3 parity suites: a bot ported from
``polysim_polymarket`` to ``polymarket`` swaps only the import prefix + host +
auth, so for every G4 method a bot can call, the keyword names + parameter kinds
must be byte-for-byte identical to py-sdk's. These tests load the real
``polymarket`` package (skipped if absent) and diff our ``SecureClient`` against
it method-by-method.

We compare **parameter names + kinds** (not annotations/defaults) — a port is
mechanical iff the same call expression binds the same way on both clients.
"""

from __future__ import annotations

import pytest

polymarket = pytest.importorskip("polymarket")

from polymarket.clients.secure import SecureClient as RealSecureClient  # noqa: E402

from polysim_polymarket import SecureClient as MirrorSecureClient  # noqa: E402
from tests._parity_helpers import _param_signature  # noqa: E402

# Bucket 1 — on-chain paper no-ops.
G4_ONCHAIN_METHODS = [
    "approve_erc20",
    "approve_erc1155_for_all",
    "transfer_erc20",
    "split_position",
    "merge_positions",
    "redeem_positions",
    "setup_trading_approvals",
    "setup_gasless_wallet",
]

# Bucket 2 — rewards + scoring honest stubs.
G4_REWARDS_METHODS = [
    "get_order_scoring",
    "get_orders_scoring",
    "list_current_rewards",
    "list_market_rewards",
    "list_user_earnings_for_day",
    "get_total_earnings_for_user_for_day",
    "list_user_earnings_and_markets_config",
    "get_reward_percentages",
]

# Bucket 3 — builder + RFQ (importable types, NotImplementedError on use).
G4_BUILDER_METHODS = [
    "get_builder_volumes",
    "list_builder_trades",
    "get_builder_fee_rates",
    "list_builder_leaderboard",
]

G4_METHODS = G4_ONCHAIN_METHODS + G4_REWARDS_METHODS + G4_BUILDER_METHODS


def test_g4_methods_exist_on_mirror() -> None:
    """Every G4 method py-sdk exposes must exist on the mirror."""
    for name in G4_METHODS:
        assert hasattr(RealSecureClient, name), f"py-sdk lacks {name} (test list stale?)"
        assert hasattr(MirrorSecureClient, name), f"mirror is missing SecureClient.{name}"


@pytest.mark.parametrize("name", G4_METHODS)
def test_g4_method_signature_matches_pysdk(name: str) -> None:
    """The mirror's keyword names + parameter kinds match py-sdk's exactly."""
    real = _param_signature(RealSecureClient, name)
    mirror = _param_signature(MirrorSecureClient, name)
    assert mirror == real, (
        f"SecureClient.{name} signature drift:\n  py-sdk: {real}\n  mirror: {mirror}"
    )


def test_no_rfq_method_on_secure_client() -> None:
    """py-sdk's SecureClient has no RFQ entrypoint — neither should the mirror.

    RFQ on py-sdk is an async-only streaming feature (``polymarket.rfq``); the
    synchronous ``SecureClient`` exposes no RFQ method. The mirror must not invent
    one — its absence is the honest signal. (RFQ *types* still re-export at root;
    only the client method is absent.)
    """
    real_rfq = [n for n in dir(RealSecureClient) if "rfq" in n.lower()]
    mirror_rfq = [n for n in dir(MirrorSecureClient) if "rfq" in n.lower()]
    assert real_rfq == [], f"py-sdk grew an RFQ SecureClient method (test stale?): {real_rfq}"
    assert mirror_rfq == [], f"mirror invented an RFQ SecureClient method: {mirror_rfq}"
