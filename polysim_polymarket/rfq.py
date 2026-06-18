"""RFQ (request-for-quote) types mirroring ``polymarket.rfq`` — IMPORTABLE, but
the behaviour is **not simulated** on paper.

Polymarket's RFQ flow is an async streaming protocol: a market-maker opens an
``RfqSession``, receives ``RfqQuoteRequestEvent`` / ``RfqConfirmationRequestEvent``
events, and responds with quotes / confirmations. PolySimulator is paper trading
and does **not** simulate RFQ quoting (there is no maker network, no live quote
auction). So this module mirrors py-sdk's RFQ **type surface** — every enum,
frozen dataclass event, id alias, the ``RfqSession`` protocol, the ``RfqEvent``
union, and the rejection error classes — so a bot's ``from polymarket import
RfqQuoteRequestEvent`` (etc.) survives the prefix swap and its type hints /
``isinstance`` checks resolve. But any RFQ *action* (the event methods that need
a live session) raises :class:`NotImplementedError` with the shared
``RFQ_NOT_SIMULATED`` message — the honest "not simulated in paper" signal.

Field names + kinds track py-sdk's ``polymarket.rfq`` so the shape is identical
across the swap. The one deliberate divergence — consistent with the rest of the
mirror — is the error BASE: our ``Rfq*RejectedError`` subclass the shared
``PolyException`` base (see ``errors.py``), not py-sdk's ``PolymarketError``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from types import TracebackType
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable

from polysim_polymarket.clients._onchain import RFQ_NOT_SIMULATED
from polysim_polymarket.errors import PolyException

# Id aliases — bare ``str`` NewType-equivalents (py-sdk uses ``TypeAlias = str``).
RfqId: TypeAlias = str
RfqQuoteId: TypeAlias = str
RfqRequestorPublicId: TypeAlias = str


class RfqDirection(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class RfqSide(StrEnum):
    YES = "YES"


class RfqQuoteSource(StrEnum):
    COLLATERAL = "collateral"
    INVENTORY = "inventory"


class RfqRequestedSizeUnit(StrEnum):
    NOTIONAL = "notional"
    SHARES = "shares"


class RfqConfirmationDecision(StrEnum):
    CONFIRM = "CONFIRM"
    DECLINE = "DECLINE"


class RfqExecutionStatus(StrEnum):
    MATCHED = "MATCHED"
    MINED = "MINED"
    CONFIRMED = "CONFIRMED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"


class RfqErrorCode(StrEnum):
    ADDRESS_MISMATCH = "ADDRESS_MISMATCH"
    ALLOWANCE_VALIDATION_FAILED = "ALLOWANCE_VALIDATION_FAILED"
    BALANCE_VALIDATION_FAILED = "BALANCE_VALIDATION_FAILED"
    CONTRADICTORY_LEGS = "CONTRADICTORY_LEGS"
    EXPIRED_RFQ = "EXPIRED_RFQ"
    INVALID_ACCEPTANCE = "INVALID_ACCEPTANCE"
    INVALID_CONFIRMATION = "INVALID_CONFIRMATION"
    INVALID_EXECUTION_RESULT = "INVALID_EXECUTION_RESULT"
    INVALID_IDENTITY = "INVALID_IDENTITY"
    INVALID_MESSAGE = "INVALID_MESSAGE"
    INVALID_QUOTE = "INVALID_QUOTE"
    INVALID_RFQ = "INVALID_RFQ"
    INVALID_RFQ_STATE = "INVALID_RFQ_STATE"
    INVALID_ROLE = "INVALID_ROLE"
    LEG_METADATA_UNAVAILABLE = "LEG_METADATA_UNAVAILABLE"
    MAKER_ALREADY_RESPONDED = "MAKER_ALREADY_RESPONDED"
    MAKER_NOT_REQUIRED = "MAKER_NOT_REQUIRED"
    PRE_EXECUTION_BALANCE_RESERVATION_FAILED = "PRE_EXECUTION_BALANCE_RESERVATION_FAILED"
    QUOTE_MISMATCH = "QUOTE_MISMATCH"
    QUOTE_UNAVAILABLE = "QUOTE_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    REQUEST_FAILED = "REQUEST_FAILED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    SUBMISSION_WINDOW_CLOSED = "SUBMISSION_WINDOW_CLOSED"
    TRADE_SUBMISSION_FAILED = "TRADE_SUBMISSION_FAILED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    UNAUTHORIZED_ROLE = "UNAUTHORIZED_ROLE"
    UNKNOWN_RFQ = "UNKNOWN_RFQ"


@dataclass(frozen=True, slots=True, kw_only=True)
class RfqRequestedSize:
    unit: RfqRequestedSizeUnit
    value: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class RfqQuoteReference:
    rfq_id: RfqId
    quote_id: RfqQuoteId


@dataclass(frozen=True, slots=True, kw_only=True)
class RfqCancelQuoteAck:
    rfq_id: RfqId
    quote_id: RfqQuoteId


@dataclass(frozen=True, slots=True, kw_only=True)
class RfqConfirmationAck:
    rfq_id: RfqId
    quote_id: RfqQuoteId


@dataclass(frozen=True, slots=True, kw_only=True)
class RfqExecutionUpdateEvent:
    type: Literal["execution_update"]
    rfq_id: RfqId
    status: RfqExecutionStatus
    tx_hash: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RfqQuoteRequestEvent:
    """A maker's quote-request event. Mirrors ``polymarket.rfq.RfqQuoteRequestEvent``.

    The TYPE is importable so a bot's handler signature type-checks across the
    prefix swap. ``quote()`` needs a live RFQ session, which paper mode does not
    simulate — it raises :class:`NotImplementedError`.
    """

    type: Literal["quote_request"]
    rfq_id: RfqId
    requestor_public_id: RfqRequestorPublicId
    leg_position_ids: tuple[str, ...]
    condition_id: str
    yes_position_id: str
    no_position_id: str
    direction: RfqDirection
    side: RfqSide
    requested_size: RfqRequestedSize
    submission_deadline: int
    _session: RfqSession | None = field(default=None, repr=False, compare=False)

    async def quote(
        self,
        *,
        price: Decimal | int | float | str,
        size: Decimal | int | float | str | None = None,
        source: RfqQuoteSource | str = RfqQuoteSource.COLLATERAL,
    ) -> RfqQuoteReference:
        """Submit a quote — NOT simulated on paper (raises NotImplementedError)."""
        raise NotImplementedError(RFQ_NOT_SIMULATED)


@dataclass(frozen=True, slots=True, kw_only=True)
class RfqConfirmationRequestEvent:
    """A maker's confirmation-request event. Mirrors ``...RfqConfirmationRequestEvent``.

    Importable type; ``confirm()`` / ``decline()`` need a live RFQ session, which
    paper mode does not simulate — they raise :class:`NotImplementedError`.
    """

    type: Literal["confirmation_request"]
    rfq_id: RfqId
    quote_id: RfqQuoteId
    signer_address: str
    maker_address: str
    signature_type: int
    leg_position_ids: tuple[str, ...]
    condition_id: str
    yes_position_id: str
    no_position_id: str
    direction: RfqDirection
    side: RfqSide
    fill_size: Decimal
    price: Decimal
    confirm_by: int
    _session: RfqSession | None = field(default=None, repr=False, compare=False)

    async def confirm(self) -> RfqConfirmationAck:
        """Confirm a quote — NOT simulated on paper (raises NotImplementedError)."""
        raise NotImplementedError(RFQ_NOT_SIMULATED)

    async def decline(self) -> RfqConfirmationAck:
        """Decline a quote — NOT simulated on paper (raises NotImplementedError)."""
        raise NotImplementedError(RFQ_NOT_SIMULATED)


RfqEvent = RfqQuoteRequestEvent | RfqConfirmationRequestEvent | RfqExecutionUpdateEvent


class RfqQuoteRejectedError(PolyException):
    """A quote was rejected. Mirrors ``polymarket.rfq.RfqQuoteRejectedError``.

    Subclasses the shared ``PolyException`` base (the mirror's one deliberate
    error-tree divergence from py-sdk's ``PolymarketError``).
    """

    def __init__(self, message: str, *, rfq_id: RfqId, code: RfqErrorCode | None = None) -> None:
        super().__init__(message)
        self.rfq_id = rfq_id
        self.code = code


class RfqCancelQuoteRejectedError(PolyException):
    """A quote-cancel was rejected. Mirrors ``...RfqCancelQuoteRejectedError``."""

    def __init__(
        self,
        message: str,
        *,
        rfq_id: RfqId,
        quote_id: RfqQuoteId,
        code: RfqErrorCode | None = None,
    ) -> None:
        super().__init__(message)
        self.rfq_id = rfq_id
        self.quote_id = quote_id
        self.code = code


class RfqConfirmationRejectedError(PolyException):
    """A confirmation was rejected. Mirrors ``...RfqConfirmationRejectedError``."""

    def __init__(
        self,
        message: str,
        *,
        rfq_id: RfqId,
        quote_id: RfqQuoteId,
        code: RfqErrorCode | None = None,
    ) -> None:
        super().__init__(message)
        self.rfq_id = rfq_id
        self.quote_id = quote_id
        self.code = code


@runtime_checkable
class RfqSession(Protocol):
    """An RFQ maker session. Mirrors ``polymarket.rfq.RfqSession`` (a Protocol).

    The protocol surface is mirrored so a bot's type hints resolve, but paper mode
    does not simulate RFQ — there is no concrete ``RfqSession`` implementation in
    the mirror, and the event methods raise :class:`NotImplementedError`.
    """

    def __await__(self) -> Generator[Any, None, RfqSession]: ...
    def __aiter__(self) -> AsyncIterator[RfqEvent]: ...
    async def __anext__(self) -> RfqEvent: ...
    async def close(self) -> None: ...
    async def cancel_quote(self, quote: RfqQuoteReference) -> RfqCancelQuoteAck: ...
    async def quote(
        self,
        request: RfqQuoteRequestEvent,
        *,
        price: Decimal | int | float | str,
        size: Decimal | int | float | str | None = None,
        source: RfqQuoteSource | str = RfqQuoteSource.COLLATERAL,
    ) -> RfqQuoteReference: ...
    async def respond_to_confirmation(
        self,
        rfq_id: RfqId,
        quote_id: RfqQuoteId,
        decision: RfqConfirmationDecision,
    ) -> RfqConfirmationAck: ...
    async def __aenter__(self) -> RfqSession: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


__all__ = [
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
