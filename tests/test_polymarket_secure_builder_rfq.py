"""Behaviour tests for the G4 builder + RFQ surface.

Bucket 3 of G4: the TYPES must import (drop-in parity — ``from polymarket import
BuilderFeeRates`` / ``from polymarket import RfqQuoteRequestEvent`` must resolve
against our mirror too) but the BEHAVIOUR is **not simulated**:

* every builder-attribution method on ``SecureClient`` raises
  ``NotImplementedError`` with the shared builder message;
* RFQ has no ``SecureClient`` entrypoint (py-sdk's RFQ is async-only) — but the
  ``Rfq*`` types are importable from the package root.

A bot can write/type-check against these; calling a builder method gives the
honest "not simulated in paper" signal instead of a fabricated value.
"""

from __future__ import annotations

import pytest

from polysim_polymarket import SecureClient


@pytest.fixture
def client() -> SecureClient:
    return SecureClient(api_key="ps_live_test")


# ── builder methods raise NotImplementedError ────────────────────────────────


def test_get_builder_volumes_not_implemented(client: SecureClient) -> None:
    with pytest.raises(NotImplementedError, match="Builder attribution is not simulated"):
        client.get_builder_volumes()


def test_get_builder_volumes_with_time_period_not_implemented(client: SecureClient) -> None:
    with pytest.raises(NotImplementedError, match="paper mode"):
        client.get_builder_volumes(time_period="WEEK")


def test_list_builder_trades_not_implemented(client: SecureClient) -> None:
    with pytest.raises(NotImplementedError, match="Builder attribution is not simulated"):
        client.list_builder_trades(builder_code="bc1")


def test_get_builder_fee_rates_not_implemented(client: SecureClient) -> None:
    """``get_builder_fee_rates`` takes a POSITIONAL builder_code (py-sdk parity)."""
    with pytest.raises(NotImplementedError, match="Builder attribution is not simulated"):
        client.get_builder_fee_rates("bc1")


def test_list_builder_leaderboard_not_implemented(client: SecureClient) -> None:
    with pytest.raises(NotImplementedError, match="Builder attribution is not simulated"):
        client.list_builder_leaderboard()


def test_builder_methods_raise_before_any_network(client: SecureClient) -> None:
    """The NotImplementedError fires before any network use (built on a bad host)."""
    bad = SecureClient(host="http://127.0.0.1:1", api_key="ps_live_test")
    with pytest.raises(NotImplementedError):
        bad.get_builder_volumes()
    with pytest.raises(NotImplementedError):
        bad.list_builder_trades(builder_code="bc")
    with pytest.raises(NotImplementedError):
        bad.get_builder_fee_rates("bc")
    with pytest.raises(NotImplementedError):
        bad.list_builder_leaderboard()


# ── builder types are importable from the package root ───────────────────────


def test_builder_types_importable_from_root() -> None:
    """``from polysim_polymarket import BuilderFeeRates`` etc. resolve (drop-in)."""
    from polysim_polymarket import (
        BuilderFeeRates,
        BuilderTrade,
        BuilderVolumeEntry,
        BuilderVolumeTimePeriod,
        LeaderboardTimePeriod,
    )

    assert isinstance(BuilderFeeRates, type)
    assert isinstance(BuilderTrade, type)
    assert isinstance(BuilderVolumeEntry, type)
    # BuilderVolumeTimePeriod / LeaderboardTimePeriod are Literal aliases, not
    # classes — just resolve. py-sdk keeps them as two separate aliases (the
    # leaderboard read is annotated with LeaderboardTimePeriod), so the mirror
    # carries both for annotation-string parity.
    assert BuilderVolumeTimePeriod is not None
    assert LeaderboardTimePeriod is not None


# ── RFQ types are importable from the package root ───────────────────────────


def test_rfq_types_importable_from_root() -> None:
    """The Rfq* types py-sdk re-exports at root resolve against the mirror too."""
    from polysim_polymarket import (
        RfqConfirmationAck,
        RfqConfirmationDecision,
        RfqConfirmationRequestEvent,
        RfqDirection,
        RfqErrorCode,
        RfqEvent,
        RfqExecutionStatus,
        RfqExecutionUpdateEvent,
        RfqQuoteReference,
        RfqQuoteRequestEvent,
        RfqQuoteSource,
        RfqRequestedSize,
        RfqRequestedSizeUnit,
        RfqSession,
        RfqSide,
    )

    # The dataclass / enum types are classes; the id aliases (RfqId etc.) are str.
    for typ in (
        RfqConfirmationAck,
        RfqQuoteReference,
        RfqRequestedSize,
        RfqQuoteRequestEvent,
        RfqConfirmationRequestEvent,
        RfqExecutionUpdateEvent,
    ):
        assert isinstance(typ, type)
    for enum_type in (
        RfqDirection,
        RfqSide,
        RfqQuoteSource,
        RfqRequestedSizeUnit,
        RfqConfirmationDecision,
        RfqExecutionStatus,
        RfqErrorCode,
    ):
        assert isinstance(enum_type, type)
    assert RfqEvent is not None  # a union alias
    assert isinstance(RfqSession, type)  # a runtime-checkable Protocol


def test_rfq_id_aliases_importable() -> None:
    from polysim_polymarket import RfqId, RfqQuoteId, RfqRequestorPublicId

    assert RfqId is str
    assert RfqQuoteId is str
    assert RfqRequestorPublicId is str


def test_rfq_errors_importable_and_are_exceptions() -> None:
    """The Rfq* rejection error types import and are PolyException subclasses."""
    from polysim_polymarket import (
        PolyException,
        RfqCancelQuoteRejectedError,
        RfqConfirmationRejectedError,
        RfqQuoteRejectedError,
    )

    for err in (
        RfqQuoteRejectedError,
        RfqCancelQuoteRejectedError,
        RfqConfirmationRejectedError,
    ):
        assert issubclass(err, PolyException)


def test_rfq_request_event_constructs() -> None:
    """A representative Rfq* dataclass constructs with kw-only fields (shape parity)."""
    from polysim_polymarket import RfqQuoteReference

    ref = RfqQuoteReference(rfq_id="r1", quote_id="q1")
    assert ref.rfq_id == "r1"
    assert ref.quote_id == "q1"
