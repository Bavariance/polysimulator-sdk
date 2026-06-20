"""Behaviour tests for the G4 on-chain **paper no-op** methods on
``polysim_polymarket.SecureClient``.

Each on-chain method (``approve_erc20`` / ``approve_erc1155_for_all`` /
``transfer_erc20`` / ``split_position`` / ``merge_positions`` /
``redeem_positions`` / ``setup_trading_approvals`` + the deprecated
``setup_gasless_wallet`` / ``is_gasless_ready``) records the *intent* but does
NO real chain work: it returns a paper handle whose ``wait()`` yields an
instant-success ``TransactionOutcome`` with a valid-format placeholder hash. The
py-sdk input guards (exactly-one-identifier combos, positive amount, malformed
address) are replicated so a bot hits the SAME ``UserInputError`` in paper.

No respx mock is needed: the paper on-chain methods touch neither the CLOB API
nor a chain. A bare ``SecureClient(api_key=...)`` is enough — construction does
not open a connection, and these methods never make a request.
"""

from __future__ import annotations

import re

import pytest

from polysim_polymarket import SecureClient, TransactionOutcome, UserInputError

_HEX64 = re.compile(r"\A0x[0-9a-f]{64}\Z")
_GOOD_ADDR = "0x" + "ab" * 20
_OTHER_ADDR = "0x" + "cd" * 20
_COND = "0x" + "ef" * 32  # a 64-hex condition id


@pytest.fixture
def client() -> SecureClient:
    """A bare paper SecureClient (no network is opened for the on-chain paths)."""
    return SecureClient(api_key="ps_live_test")


def _assert_paper_outcome(outcome: object) -> None:
    assert isinstance(outcome, TransactionOutcome)
    assert _HEX64.match(outcome.transaction_hash), (
        f"paper outcome hash {outcome.transaction_hash!r} is not 0x + 64 hex"
    )
    assert outcome.transaction_id is None


# ── annotation resolvability (get_type_hints) ────────────────────────────────


def test_get_type_hints_resolves_sync_transaction_handle() -> None:
    """The on-chain methods annotate ``-> SyncTransactionHandle``; that forward
    reference must resolve at runtime so ``typing.get_type_hints`` works (a tool
    or doc generator that introspects the SecureClient must not NameError)."""
    import inspect
    import typing

    failures: list[str] = []
    for name, method in inspect.getmembers(SecureClient, predicate=inspect.isfunction):
        try:
            typing.get_type_hints(method)
        except NameError as exc:
            failures.append(f"{name}: {exc}")
    assert not failures, "get_type_hints failed:\n" + "\n".join(failures)


# ── instant-success paper handles ────────────────────────────────────────────


def test_approve_erc20_returns_instant_paper_outcome(client: SecureClient) -> None:
    handle = client.approve_erc20(
        token_address=_GOOD_ADDR, spender_address=_OTHER_ADDR, amount=1_000_000
    )
    _assert_paper_outcome(handle.wait())


def test_approve_erc20_accepts_max_literal(client: SecureClient) -> None:
    """``amount="max"`` is accepted (py-sdk's ``int | Literal['max']``)."""
    handle = client.approve_erc20(
        token_address=_GOOD_ADDR, spender_address=_OTHER_ADDR, amount="max"
    )
    _assert_paper_outcome(handle.wait())


def test_approve_erc1155_for_all_returns_instant_paper_outcome(client: SecureClient) -> None:
    handle = client.approve_erc1155_for_all(
        token_address=_GOOD_ADDR, operator_address=_OTHER_ADDR
    )
    _assert_paper_outcome(handle.wait())


def test_approve_erc1155_for_all_revoke(client: SecureClient) -> None:
    handle = client.approve_erc1155_for_all(
        token_address=_GOOD_ADDR, operator_address=_OTHER_ADDR, approved=False
    )
    _assert_paper_outcome(handle.wait())


def test_transfer_erc20_returns_instant_paper_outcome(client: SecureClient) -> None:
    handle = client.transfer_erc20(
        token_address=_GOOD_ADDR, recipient_address=_OTHER_ADDR, amount=42
    )
    _assert_paper_outcome(handle.wait())


def test_split_position_by_condition_id(client: SecureClient) -> None:
    handle = client.split_position(condition_id=_COND, amount=1_000_000)
    _assert_paper_outcome(handle.wait())


def test_split_position_by_legs(client: SecureClient) -> None:
    handle = client.split_position(legs=["0x" + "11" * 32, "0x" + "22" * 32], amount=1_000_000)
    _assert_paper_outcome(handle.wait())


def test_merge_positions_by_condition_id(client: SecureClient) -> None:
    handle = client.merge_positions(condition_id=_COND, amount=1_000_000)
    _assert_paper_outcome(handle.wait())


def test_merge_positions_accepts_max(client: SecureClient) -> None:
    handle = client.merge_positions(condition_id=_COND, amount="max")
    _assert_paper_outcome(handle.wait())


def test_redeem_positions_by_condition_id(client: SecureClient) -> None:
    handle = client.redeem_positions(condition_id=_COND)
    _assert_paper_outcome(handle.wait())


def test_redeem_positions_by_market_id(client: SecureClient) -> None:
    handle = client.redeem_positions(market_id="0x123market")
    _assert_paper_outcome(handle.wait())


def test_redeem_positions_by_position_id(client: SecureClient) -> None:
    handle = client.redeem_positions(position_id="0x123position")
    _assert_paper_outcome(handle.wait())


def test_setup_trading_approvals_wait_is_none(client: SecureClient) -> None:
    """``setup_trading_approvals().wait()`` returns ``None`` (deprecated handle)."""
    handle = client.setup_trading_approvals()
    assert handle.wait() is None


def test_setup_gasless_wallet_returns_self(client: SecureClient) -> None:
    """``setup_gasless_wallet`` returns the client (py-sdk's deprecated ``-> Self``)."""
    assert client.setup_gasless_wallet() is client


def test_is_gasless_ready_is_true(client: SecureClient) -> None:
    assert client.is_gasless_ready() is True


def test_metadata_kwarg_accepted_on_all_onchain(client: SecureClient) -> None:
    """The ``metadata=`` kwarg is accepted (and inert) on every on-chain method."""
    client.approve_erc20(
        token_address=_GOOD_ADDR, spender_address=_OTHER_ADDR, amount=1, metadata="note"
    ).wait()
    client.transfer_erc20(
        token_address=_GOOD_ADDR, recipient_address=_OTHER_ADDR, amount=1, metadata="note"
    ).wait()
    client.split_position(condition_id=_COND, amount=1, metadata="note").wait()
    client.redeem_positions(condition_id=_COND, metadata="note").wait()


# ── identifier-combination guards (one test per guard) ───────────────────────


def test_split_position_requires_exactly_one_identifier_none(client: SecureClient) -> None:
    with pytest.raises(UserInputError, match="Provide exactly one of condition_id or legs"):
        client.split_position(amount=1)


def test_split_position_requires_exactly_one_identifier_both(client: SecureClient) -> None:
    with pytest.raises(UserInputError, match="Provide exactly one of condition_id or legs"):
        client.split_position(condition_id=_COND, legs=["a", "b"], amount=1)


def test_split_position_combo_amount_must_be_positive(client: SecureClient) -> None:
    """The combo (legs) branch enforces a positive amount (py-sdk's guard)."""
    with pytest.raises(UserInputError, match="Split amount must be positive"):
        client.split_position(legs=["0x" + "11" * 32, "0x" + "22" * 32], amount=0)


def test_merge_positions_requires_exactly_one_identifier_none(client: SecureClient) -> None:
    with pytest.raises(UserInputError, match="Provide exactly one of condition_id or legs"):
        client.merge_positions(amount=1)


def test_merge_positions_requires_exactly_one_identifier_both(client: SecureClient) -> None:
    with pytest.raises(UserInputError, match="Provide exactly one of condition_id or legs"):
        client.merge_positions(condition_id=_COND, legs=["a", "b"], amount=1)


def test_redeem_positions_requires_exactly_one_none(client: SecureClient) -> None:
    with pytest.raises(
        UserInputError, match="Provide exactly one of condition_id, market_id, or position_id"
    ):
        client.redeem_positions()


def test_redeem_positions_requires_exactly_one_two(client: SecureClient) -> None:
    with pytest.raises(
        UserInputError, match="Provide exactly one of condition_id, market_id, or position_id"
    ):
        client.redeem_positions(condition_id=_COND, market_id="m")


# ── address-format guards ────────────────────────────────────────────────────


def test_approve_erc20_rejects_malformed_token_address(client: SecureClient) -> None:
    with pytest.raises(UserInputError, match="Invalid token_address"):
        client.approve_erc20(token_address="nope", spender_address=_OTHER_ADDR, amount=1)


def test_approve_erc20_rejects_malformed_spender_address(client: SecureClient) -> None:
    with pytest.raises(UserInputError, match="Invalid spender_address"):
        client.approve_erc20(token_address=_GOOD_ADDR, spender_address="0x123", amount=1)


def test_approve_erc1155_rejects_malformed_operator_address(client: SecureClient) -> None:
    with pytest.raises(UserInputError, match="Invalid operator_address"):
        client.approve_erc1155_for_all(token_address=_GOOD_ADDR, operator_address="bad")


def test_transfer_erc20_rejects_malformed_recipient_address(client: SecureClient) -> None:
    with pytest.raises(UserInputError, match="Invalid recipient_address"):
        client.transfer_erc20(token_address=_GOOD_ADDR, recipient_address="bad", amount=1)


def test_no_network_call_made_on_onchain(client: SecureClient) -> None:
    """Sanity: the on-chain paths never touch the underlying transport.

    The client is built with an unreachable host; if any on-chain method tried to
    hit the network the test would error. It returns instantly instead.
    """
    bad_host_client = SecureClient(host="http://127.0.0.1:1", api_key="ps_live_test")
    bad_host_client.approve_erc20(
        token_address=_GOOD_ADDR, spender_address=_OTHER_ADDR, amount=1
    ).wait()
    bad_host_client.setup_trading_approvals().wait()
