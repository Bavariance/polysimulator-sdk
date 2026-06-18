"""Transport-free on-chain **paper** primitives + input guards (the G4 DRY core).

py-sdk's ``SecureClient`` on-chain methods (``approve_erc20`` /
``split_position`` / ``redeem_positions`` / …) build a real EVM transaction,
broadcast it (EOA) or relay it (gasless), and return a transaction *handle* whose
``wait()`` blocks until the transaction reaches a terminal on-chain outcome.

PolySimulator is **paper trading**: there is no chain, no signer, no web3, no
``eth_account``, no RPC. So this module supplies the paper analogues the sync
``SecureClient`` (and, in G5, the async ``AsyncSecureClient``) compose instead:

* :class:`PaperSyncTransactionHandle` — a frozen handle whose ``wait()`` returns
  **instantly** with a paper :class:`~polysim_polymarket.models.TransactionOutcome`
  (no network, no chain). It exposes the same ``transaction_id`` /
  ``transaction_hash`` / ``wait()`` surface a real
  ``SyncEoaTransactionHandle`` does, so a ported bot's
  ``handle.wait().transaction_hash`` reads identically across the prefix swap.
* :class:`PaperSyncDeprecatedTransactionHandle` — the paper analogue of py-sdk's
  ``SyncDeprecatedTransactionHandle`` (``setup_trading_approvals``'s return),
  whose ``wait()`` returns ``None``.
* the input guards py-sdk applies BEFORE any chain work, replicated so a bot hits
  the **same** ``UserInputError`` in paper as in prod: the
  exactly-one-identifier combination guard
  (:func:`require_exactly_one`), the positive-amount guard
  (:func:`require_positive_amount`), and a **lightweight** 40-hex
  address-format guard (:func:`validate_address`).

**Address-format guard is a FORMAT check, not an EIP-55 checksum.** py-sdk runs
``eth_utils.to_checksum_address`` (which pulls in ``eth-utils`` / ``eth-hash`` /
the keccak stack); that helper accepts a 40-hex address in any of the forms a
bare 40-hex string (no prefix), ``0x``-prefixed, or ``0X``-prefixed — all
case-insensitive — and **normalizes** it to its EIP-55-checksummed form (it
raises only on a bad length / non-hex string), then uses the checksummed value
on-chain. To keep the paper SDK's dependency surface thin — paper trading never
touches a chain, so a checksummed address buys nothing and is never used — this
guard only verifies the **shape**, accepting the SAME set py-sdk accepts (an
**optional** ``0x``/``0X`` prefix + exactly 40 hex digits, any case), and returns
the input unchanged. It rejects the same malformed inputs py-sdk rejects (wrong
length, non-hex) but, unlike py-sdk, does NOT compute the EIP-55 checksum
normalization and does NOT import the keccak stack to do so. The seam is
documented in the README and in each on-chain method's docstring.

Nothing in this module imports web3 / eth_account / eth_utils or opens a socket.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from polysim_polymarket.errors import UserInputError
from polysim_polymarket.models import TransactionHash, TransactionOutcome

# A valid-format paper placeholder transaction hash: ``0x`` + 64 hex digits, so it
# satisfies anything expecting a well-formed transaction hash. It is a SYNTHETIC
# paper sentinel — NOT a settled on-chain transaction. The recognizable
# ``...0001`` tail distinguishes it from an all-zero "failed/empty" hash while
# staying a clearly-fixed marker (no per-call randomness — paper outcomes are
# deterministic).
PAPER_TRANSACTION_HASH: TransactionHash = "0x" + "00" * 31 + "01"

# Shared NotImplementedError messages for the un-simulated subtrees (Bucket 3).
# Kept as constants so the sync + async clients raise byte-identical text.
BUILDER_NOT_SIMULATED = "Builder attribution is not simulated in PolySimulator paper mode."
RFQ_NOT_SIMULATED = "RFQ quoting is not simulated in PolySimulator paper mode."

_ADDRESS_RE = re.compile(r"\A(0[xX])?[0-9a-fA-F]{40}\Z")


# ── paper transaction outcome + handles ──────────────────────────────────────


def paper_transaction_outcome() -> TransactionOutcome:
    """Build the instant-success paper :class:`TransactionOutcome`.

    Carries the valid-format :data:`PAPER_TRANSACTION_HASH` placeholder and
    ``transaction_id=None`` (EOA-style — paper trading has no relayer id). Shared
    by the sync + async paper handles so the outcome is identical regardless of
    which client built it.
    """
    return TransactionOutcome(
        transaction_hash=PAPER_TRANSACTION_HASH,
        transaction_id=None,
    )


@dataclass(frozen=True, slots=True)
class PaperSyncTransactionHandle:
    """A paper synchronous transaction handle.

    Mirrors the public surface of py-sdk's ``SyncEoaTransactionHandle`` /
    ``SyncGaslessTransactionHandle`` (the members of the ``SyncTransactionHandle``
    union): it exposes ``transaction_id`` + ``transaction_hash`` attributes and a
    ``wait()`` that resolves to a :class:`TransactionOutcome`. On paper there is
    no chain to poll, so ``wait()`` returns **instantly** with the paper outcome —
    no network, no blocking.
    """

    transaction_hash: TransactionHash = PAPER_TRANSACTION_HASH
    transaction_id: str | None = None

    def wait(self) -> TransactionOutcome:
        """Return the paper outcome immediately (no chain to wait on)."""
        return paper_transaction_outcome()


@dataclass(frozen=True, slots=True)
class PaperSyncDeprecatedTransactionHandle:
    """A paper synchronous *deprecated* transaction handle.

    Mirrors py-sdk's ``SyncDeprecatedTransactionHandle`` (the return of
    ``setup_trading_approvals``): a compatibility handle whose ``wait()`` returns
    ``None``. py-sdk's real ``setup_trading_approvals`` waits internally and hands
    back this no-payload handle; on paper there is nothing to approve, so the
    method returns this handle directly and ``wait()`` is a ``None``-returning
    no-op.
    """

    transaction_hash: None = None
    transaction_id: None = None

    def wait(self) -> None:
        """Return ``None`` immediately (mirrors py-sdk's deprecated handle)."""
        return None


@dataclass(frozen=True, slots=True)
class PaperAsyncTransactionHandle:
    """A paper **asynchronous** transaction handle.

    The async twin of :class:`PaperSyncTransactionHandle`. Mirrors the public
    surface of py-sdk's ``EoaTransactionHandle`` / ``GaslessTransactionHandle``
    (the members of the async ``TransactionHandle`` union the async on-chain
    methods return): it exposes ``transaction_id`` + ``transaction_hash``
    attributes and an ``async def wait()`` that resolves to a
    :class:`TransactionOutcome`. On paper there is no chain to poll, so ``wait()``
    returns **instantly** (no ``await`` on any network) with the SAME paper
    outcome :func:`paper_transaction_outcome` builds for the sync handle — the
    sync + async handles share one outcome, so a ported bot's
    ``(await handle.wait()).transaction_hash`` reads identically across the
    sync->async and prefix swaps.
    """

    transaction_hash: TransactionHash = PAPER_TRANSACTION_HASH
    transaction_id: str | None = None

    async def wait(self) -> TransactionOutcome:
        """Return the paper outcome immediately (no chain to await on)."""
        return paper_transaction_outcome()


@dataclass(frozen=True, slots=True)
class PaperAsyncDeprecatedTransactionHandle:
    """A paper **asynchronous** *deprecated* transaction handle.

    The async twin of :class:`PaperSyncDeprecatedTransactionHandle`. Mirrors
    py-sdk's ``DeprecatedTransactionHandle`` (the async
    ``setup_trading_approvals`` return): a compatibility handle whose
    ``async def wait()`` returns ``None``. On paper there is nothing to approve,
    so the method returns this handle directly and ``await handle.wait()`` is a
    ``None``-returning no-op.
    """

    transaction_hash: None = None
    transaction_id: None = None

    async def wait(self) -> None:
        """Return ``None`` immediately (mirrors py-sdk's deprecated async handle)."""
        return None


# py-sdk's ``SyncTransactionHandle`` is a ``TypeAlias`` for the
# ``SyncGaslessTransactionHandle | SyncEoaTransactionHandle`` union; its on-chain
# methods annotate their return as that alias. The mirror has ONE paper handle
# that plays both roles, so ``SyncTransactionHandle`` aliases it here — this is the
# canonical definition the package root re-exports, and the source the sync (and,
# in G5, the async) on-chain methods annotate their return with for
# annotation-string parity with py-sdk.
SyncTransactionHandle = PaperSyncTransactionHandle

# py-sdk's ``TransactionHandle`` is the ASYNC analogue — a ``TypeAlias`` for the
# ``GaslessTransactionHandle | EoaTransactionHandle`` union its ASYNC on-chain
# methods return, promoted to its package root. The mirror has ONE async paper
# handle that plays both roles, so ``TransactionHandle`` aliases it here — the
# canonical definition the package root re-exports, and the name the async
# on-chain methods annotate their return with for annotation-string parity with
# py-sdk's ``AsyncSecureClient`` (whose on-chain methods are typed
# ``-> TransactionHandle``).
TransactionHandle = PaperAsyncTransactionHandle


def paper_sync_handle() -> PaperSyncTransactionHandle:
    """Construct a fresh paper sync transaction handle."""
    return PaperSyncTransactionHandle()


def paper_sync_deprecated_handle() -> PaperSyncDeprecatedTransactionHandle:
    """Construct a fresh paper deprecated (None-waiting) sync handle."""
    return PaperSyncDeprecatedTransactionHandle()


def paper_async_handle() -> PaperAsyncTransactionHandle:
    """Construct a fresh paper async transaction handle."""
    return PaperAsyncTransactionHandle()


def paper_async_deprecated_handle() -> PaperAsyncDeprecatedTransactionHandle:
    """Construct a fresh paper deprecated (None-waiting) async handle."""
    return PaperAsyncDeprecatedTransactionHandle()


# ── input guards (replicate py-sdk's pre-chain validation) ───────────────────


def require_exactly_one(message: str, **identifiers: object) -> None:
    """Raise :class:`UserInputError` unless EXACTLY one identifier is non-``None``.

    Replicates py-sdk's combination guards so a bot hits the SAME error in paper
    as in prod:

    * ``split_position`` / ``merge_positions`` — exactly one of
      ``condition_id`` / ``legs`` (py-sdk: ``(condition_id is None) ==
      (legs is None)``);
    * ``redeem_positions`` — exactly one of ``condition_id`` / ``market_id`` /
      ``position_id`` (py-sdk: ``sum(v is not None ...) != 1``).

    ``message`` is py-sdk's exact wording for the offending method, so the raised
    text matches byte-for-byte.
    """
    provided = sum(1 for value in identifiers.values() if value is not None)
    if provided != 1:
        raise UserInputError(message)


def require_positive_amount(amount: int, message: str) -> None:
    """Raise :class:`UserInputError` unless ``amount`` is strictly positive.

    Replicates py-sdk's ``if amount <= 0: raise UserInputError(...)`` guard (e.g.
    the combo-``split_position`` ``"Split amount must be positive for combo
    positions"`` check). ``message`` is py-sdk's exact wording.
    """
    if amount <= 0:
        raise UserInputError(message)


def validate_address(param_name: str, address: str) -> str:
    """Lightweight 40-hex address-format guard; returns the address unchanged.

    Raises :class:`UserInputError` (message ``"Invalid {param_name}: ..."``,
    matching py-sdk's ``"Invalid token_address: ..."`` / ``"Invalid
    spender_address: ..."`` prefixes) for an address that is not 40 hex digits
    optionally prefixed by ``0x`` / ``0X``.

    The accepted SHAPE matches py-sdk's ``to_checksum_address`` acceptance set:
    that helper accepts a bare 40-hex string (no prefix), a ``0x``-prefixed one,
    and a ``0X``-prefixed one, all case-insensitive — so this guard accepts the
    same set (optional ``0x``/``0X`` prefix + exactly 40 hex digits, any case) and
    rejects the same malformed inputs (wrong length / non-hex). It is a **format
    check, not an EIP-55 checksum** (see module docstring): we deliberately do NOT
    pull ``eth-utils`` / web3 / ``eth_account`` into the paper SDK, so — unlike
    py-sdk's ``to_checksum_address`` — it does NOT compute the EIP-55 checksum
    normalization (which is moot on paper, where the address is never used on a
    chain). It returns the input unchanged. That checksum step is the only
    divergence, documented as a seam.
    """
    if not isinstance(address, str) or not _ADDRESS_RE.match(address):
        raise UserInputError(
            f"Invalid {param_name}: {address!r} is not a 40-hex address "
            f"(optionally 0x/0X-prefixed; paper mode does a format check, "
            f"not an EIP-55 checksum)"
        )
    return address


__all__ = [
    "BUILDER_NOT_SIMULATED",
    "PAPER_TRANSACTION_HASH",
    "PaperAsyncDeprecatedTransactionHandle",
    "PaperAsyncTransactionHandle",
    "PaperSyncDeprecatedTransactionHandle",
    "PaperSyncTransactionHandle",
    "RFQ_NOT_SIMULATED",
    "SyncTransactionHandle",
    "TransactionHandle",
    "paper_async_deprecated_handle",
    "paper_async_handle",
    "paper_sync_deprecated_handle",
    "paper_sync_handle",
    "paper_transaction_outcome",
    "require_exactly_one",
    "require_positive_amount",
    "validate_address",
]
