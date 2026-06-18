"""Unit tests for the transport-free on-chain paper primitives + guards in
:mod:`polysim_polymarket.clients._onchain`.

These cover the DRY core the sync ``SecureClient`` (and, in G5, the async
``AsyncSecureClient``) compose: the paper :class:`TransactionOutcome`
construction, the paper sync transaction handle, the identifier-combination
guards, and the lightweight 40-hex (optionally 0x/0X-prefixed) address-format
guard. Nothing here touches
the network or a chain — the whole point is that the paper outcome resolves
instantly with a valid-format placeholder hash.
"""

from __future__ import annotations

import re

import pytest

from polysim_polymarket.clients import _onchain
from polysim_polymarket.errors import UserInputError

_HEX64 = re.compile(r"\A0x[0-9a-f]{64}\Z")


# ── paper TransactionOutcome + handle ────────────────────────────────────────


def test_paper_outcome_hash_is_valid_format() -> None:
    """The paper placeholder hash is a 0x-prefixed 64-hex string (valid format)."""
    outcome = _onchain.paper_transaction_outcome()
    assert _HEX64.match(outcome.transaction_hash), (
        f"paper hash {outcome.transaction_hash!r} is not 0x + 64 hex"
    )
    # EOA-style: no relayer transaction id on paper.
    assert outcome.transaction_id is None


def test_paper_handle_wait_returns_outcome_instantly() -> None:
    """The paper handle's ``wait()`` returns a TransactionOutcome with no network."""
    handle = _onchain.paper_sync_handle()
    outcome = handle.wait()
    assert _HEX64.match(outcome.transaction_hash)
    assert outcome.transaction_id is None
    # handle exposes the same attributes a real SyncEoaTransactionHandle does
    assert handle.transaction_id is None
    assert _HEX64.match(handle.transaction_hash)


def test_paper_handle_wait_hash_matches_handle_hash() -> None:
    """The outcome ``.wait()`` yields carries the same hash the handle advertises."""
    handle = _onchain.paper_sync_handle()
    assert handle.wait().transaction_hash == handle.transaction_hash


def test_paper_handle_is_frozen() -> None:
    """The paper handle is a frozen dataclass (mirrors py-sdk's frozen handles)."""
    handle = _onchain.paper_sync_handle()
    with pytest.raises((AttributeError, TypeError)):
        handle.transaction_hash = "0x" + "1" * 64  # type: ignore[misc]


# ── identifier-combination guards ────────────────────────────────────────────


def test_require_exactly_one_passes_with_one() -> None:
    """Exactly one non-None identifier passes (no raise)."""
    _onchain.require_exactly_one(
        "Provide exactly one of condition_id or legs",
        condition_id="0xabc",
        legs=None,
    )


def test_require_exactly_one_raises_with_none() -> None:
    """Zero identifiers raises UserInputError with the given message."""
    with pytest.raises(UserInputError, match="Provide exactly one of condition_id or legs"):
        _onchain.require_exactly_one(
            "Provide exactly one of condition_id or legs",
            condition_id=None,
            legs=None,
        )


def test_require_exactly_one_raises_with_two() -> None:
    """Two identifiers raises UserInputError with the given message."""
    with pytest.raises(UserInputError, match="Provide exactly one of condition_id or legs"):
        _onchain.require_exactly_one(
            "Provide exactly one of condition_id or legs",
            condition_id="0xabc",
            legs=["a", "b"],
        )


def test_require_exactly_one_three_way() -> None:
    """The guard generalises to three identifiers (redeem_positions)."""
    msg = "Provide exactly one of condition_id, market_id, or position_id"
    # exactly one ok
    _onchain.require_exactly_one(msg, condition_id="0xabc", market_id=None, position_id=None)
    # two raises
    with pytest.raises(UserInputError, match=re.escape(msg)):
        _onchain.require_exactly_one(msg, condition_id="0xabc", market_id="m", position_id=None)
    # zero raises
    with pytest.raises(UserInputError, match=re.escape(msg)):
        _onchain.require_exactly_one(msg, condition_id=None, market_id=None, position_id=None)


# ── address-format guard ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "addr",
    [
        "0x" + "aB" * 20,  # 0x-prefixed, mixed case (no EIP-55 checksum on paper)
        "0X" + "aB" * 20,  # 0X-prefixed (py-sdk's to_checksum_address accepts this)
        "ab" * 20,  # bare 40-hex, no prefix (py-sdk accepts this too)
        "AB" * 20,  # bare 40-hex, upper case
    ],
)
def test_validate_address_accepts_well_formed(addr: str) -> None:
    """Every shape py-sdk's ``to_checksum_address`` accepts passes, returned unchanged.

    The paper guard matches py-sdk's acceptance set: an optional ``0x``/``0X``
    prefix + 40 hex digits, any case.
    """
    assert _onchain.validate_address("token_address", addr) == addr


@pytest.mark.parametrize(
    "bad",
    [
        "",  # empty
        "0x123",  # too short
        "0x" + "g" * 40,  # non-hex
        "1" * 42,  # 42 hex digits — wrong length (40 required)
        "0x" + "a" * 41,  # too long
        "0x" + "a" * 39,  # too short by one
        "0y" + "a" * 40,  # 0y is not a valid prefix (only 0x/0X)
    ],
)
def test_validate_address_rejects_malformed(bad: str) -> None:
    """A malformed address raises UserInputError naming the offending param."""
    with pytest.raises(UserInputError, match="Invalid token_address"):
        _onchain.validate_address("token_address", bad)


def test_validate_address_error_names_the_param() -> None:
    """The error message carries the parameter name so a bot knows which arg broke."""
    with pytest.raises(UserInputError, match="Invalid spender_address"):
        _onchain.validate_address("spender_address", "nope")


def test_require_positive_amount_passes() -> None:
    """A positive amount passes (no raise)."""
    _onchain.require_positive_amount(5, "Split amount must be positive for combo positions")


@pytest.mark.parametrize("amount", [0, -1, -1000])
def test_require_positive_amount_raises(amount: int) -> None:
    """A non-positive amount raises UserInputError with the given message."""
    with pytest.raises(UserInputError, match="Split amount must be positive"):
        _onchain.require_positive_amount(
            amount, "Split amount must be positive for combo positions"
        )


# ── NotImplementedError message constants ────────────────────────────────────


def test_not_implemented_messages_exist() -> None:
    """The builder + RFQ NotImplementedError messages are shared constants."""
    assert "Builder attribution is not simulated" in _onchain.BUILDER_NOT_SIMULATED
    assert "paper mode" in _onchain.BUILDER_NOT_SIMULATED
    assert "RFQ quoting is not simulated" in _onchain.RFQ_NOT_SIMULATED
    assert "paper mode" in _onchain.RFQ_NOT_SIMULATED
