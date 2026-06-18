"""Root re-export tests for the G4 type surface.

The drop-in premise extends to G4: a bot that writes ``from polymarket import
BuilderFeeRates`` / ``TransactionOutcome`` / ``RfqQuoteRequestEvent`` (etc.) must
keep working as ``from polysim_polymarket import <same>`` after the prefix swap.
So every G4 type py-sdk promotes to its root that the new SecureClient methods
touch must be importable off the mirror's root and listed in ``__all__`` — and,
critically, the mirror must NOT promote any name py-sdk's root LACKS (that would
break the swap in the other direction).
"""

from __future__ import annotations

import pytest

import polysim_polymarket

# Every G4 name the mirror promotes to its root. Each must (a) resolve off the
# mirror root, (b) be in __all__, and (c) exist on py-sdk's root.
G4_ROOT_NAMES = [
    # transactions
    "TransactionOutcome",
    "TransactionHash",
    "SyncTransactionHandle",
    # rewards
    "CurrentReward",
    "CurrentRewardConfig",
    "MarketReward",
    "MarketRewardConfig",
    "MarketRewardToken",
    "UserEarning",
    "TotalUserEarning",
    "UserRewardsEarning",
    "UserRewardsConfig",
    "EarningBreakdown",
    "RewardsPercentages",
    # builder
    "BuilderFeeRates",
    "BuilderTrade",
    "BuilderVolumeEntry",
    "BuilderVolumeTimePeriod",
    "LeaderboardTimePeriod",
    # rfq
    "RfqCancelQuoteAck",
    "RfqCancelQuoteRejectedError",
    "RfqConfirmationAck",
    "RfqConfirmationDecision",
    "RfqConfirmationRejectedError",
    "RfqConfirmationRequestEvent",
    "RfqDirection",
    "RfqErrorCode",
    "RfqEvent",
    "RfqExecutionStatus",
    "RfqExecutionUpdateEvent",
    "RfqId",
    "RfqQuoteId",
    "RfqQuoteReference",
    "RfqQuoteRejectedError",
    "RfqQuoteRequestEvent",
    "RfqQuoteSource",
    "RfqRequestedSize",
    "RfqRequestedSizeUnit",
    "RfqRequestorPublicId",
    "RfqSession",
    "RfqSide",
]


@pytest.mark.parametrize("name", G4_ROOT_NAMES)
def test_g4_name_is_on_mirror_root_and_in_all(name: str) -> None:
    """Each G4 name resolves off the mirror root and is listed in ``__all__``."""
    assert hasattr(polysim_polymarket, name), f"mirror root is missing {name}"
    assert name in polysim_polymarket.__all__, f"{name} missing from __all__"


@pytest.mark.parametrize("name", G4_ROOT_NAMES)
def test_g4_name_exists_on_real_polymarket_root(name: str) -> None:
    """Each G4 name the mirror promotes also exists on py-sdk's root (swap-safe)."""
    polymarket = pytest.importorskip("polymarket")
    assert hasattr(polymarket, name), (
        f"mirror promotes {name} but py-sdk's root lacks it — breaks the swap"
    )


def test_mirror_root_is_subset_of_pysdk_root_for_g4() -> None:
    """The mirror promotes NO G4 name py-sdk lacks (full-root subset check).

    Beyond the per-name check, assert the WHOLE mirror ``__all__`` (minus the one
    deliberate error-tree divergence) is a subset of py-sdk's root — so no G4
    addition slipped in a name py-sdk's root doesn't carry.
    """
    polymarket = pytest.importorskip("polymarket")

    deliberate_divergence = {"PolyException", "PolyApiException"}
    promoted = set(polysim_polymarket.__all__) - deliberate_divergence
    real_root = set(dir(polymarket))
    missing = promoted - real_root
    assert not missing, (
        f"mirror promotes names absent from py-sdk's root (breaks the swap): {missing}"
    )


def test_transaction_outcome_resolves_to_mirror_model() -> None:
    """``TransactionOutcome`` off the root is the mirror's frozen dataclass."""
    from polysim_polymarket import TransactionOutcome
    from polysim_polymarket import models as _models

    assert TransactionOutcome is _models.TransactionOutcome
    outcome = TransactionOutcome(transaction_hash="0x" + "0" * 64, transaction_id=None)
    assert outcome.transaction_id is None


def test_sync_transaction_handle_is_the_paper_handle() -> None:
    """``SyncTransactionHandle`` off the root is the mirror's paper handle class.

    py-sdk's ``SyncTransactionHandle`` is a union alias; the mirror's single paper
    handle plays both roles, so the root name aliases that handle — and the
    on-chain methods return an instance of it.
    """
    from polysim_polymarket import SecureClient, SyncTransactionHandle
    from polysim_polymarket.clients._onchain import PaperSyncTransactionHandle

    assert SyncTransactionHandle is PaperSyncTransactionHandle
    handle = SecureClient(api_key="ps_live_test").approve_erc20(
        token_address="0x" + "ab" * 20, spender_address="0x" + "cd" * 20, amount=1
    )
    assert isinstance(handle, SyncTransactionHandle)


def test_builder_and_rfq_types_resolve_to_canonical_modules() -> None:
    """The builder types resolve to ``models`` and the Rfq* types to ``rfq``."""
    from polysim_polymarket import BuilderFeeRates, RfqQuoteReference
    from polysim_polymarket import models as _models
    from polysim_polymarket import rfq as _rfq

    assert BuilderFeeRates is _models.BuilderFeeRates
    assert RfqQuoteReference is _rfq.RfqQuoteReference


def test_deprecated_handle_names_not_promoted_to_root() -> None:
    """py-sdk does NOT export the Deprecated*TransactionHandle names at root.

    So the mirror must not promote its paper deprecated handle to the root either
    (it would break the subset contract). It stays reachable via the ``_onchain``
    module, just not on the package root.
    """
    polymarket = pytest.importorskip("polymarket")

    # py-sdk lacks these at root (sanity guard against a stale assumption).
    assert not hasattr(polymarket, "SyncDeprecatedTransactionHandle")
    assert "PaperSyncDeprecatedTransactionHandle" not in polysim_polymarket.__all__
    assert not hasattr(polysim_polymarket, "PaperSyncDeprecatedTransactionHandle")
    # ...and the mirror does not leak its internal paper-handle name to the root.
    assert "PaperSyncTransactionHandle" not in polysim_polymarket.__all__
