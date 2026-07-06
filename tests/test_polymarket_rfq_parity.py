"""Coverage-honesty parity gate for the RFQ type surface.

The other parity suites diff the mirror's *client* surface against the live
installed ``polymarket`` package (see ``test_polymarket_full_surface_parity.py``),
but the **RFQ type surface had no such gate** — so a re-sync that forgot to mirror
a new ``polymarket.rfq`` name (e.g. ``RfqTradeEvent`` in 0.1.0b9+), a new
``RfqErrorCode`` member, or the ``error_id`` kwarg on a rejection error would have
left the whole suite green. The drop-in premise applies to RFQ exactly as it does
to clients: ``from polymarket.rfq import X`` must keep working as
``from polysim_polymarket.rfq import X`` after the prefix swap.

This gate introspects the **real installed** ``polymarket.rfq`` (skipped when it
isn't installed) and asserts the mirror's RFQ surface tracks it across every axis
a ported bot depends on:

* ``__all__`` — the import/name contract (equality, both directions)
* every RFQ **enum** — members + values (auto-discovered, so a new enum or member
  on the next prerelease is covered without editing this file)
* every RFQ **dataclass** — field names + order (auto-discovered)
* the ``RfqEvent`` union members
* the three rejection-error **constructor signatures** (the ``error_id`` axis)
* the ``RfqSession`` **protocol** method surface + the public methods' signatures

The discovery is driven off the **real** module, so this is a true coverage-honesty
gate: when the next ``polymarket-client`` prerelease moves the RFQ surface, THIS
test reports exactly what the mirror missed.

The mirror's one deliberate divergence — the ``Rfq*RejectedError`` classes subclass
the shared ``PolyException`` base rather than py-sdk's ``PolymarketError`` (see
``polysim_polymarket/errors.py``) — is NOT asserted here (it is intentional and
checked elsewhere); this gate compares the *call/import contract*, not the error
base class.
"""

from __future__ import annotations

import dataclasses
import inspect
from enum import Enum
from typing import get_args

import pytest

import polysim_polymarket.rfq as mirror_rfq
from tests._parity_helpers import _param_signature

real_rfq = pytest.importorskip("polymarket.rfq")


def _public_names(module: object) -> list[str]:
    return list(module.__all__)


# Auto-discover the RFQ enums + dataclasses py-sdk exports, so a new type on the
# next prerelease is parity-checked without anyone editing this file.
_REAL_ENUMS = sorted(
    n
    for n in _public_names(real_rfq)
    if isinstance(getattr(real_rfq, n), type) and issubclass(getattr(real_rfq, n), Enum)
)
_REAL_DATACLASSES = sorted(
    n for n in _public_names(real_rfq) if dataclasses.is_dataclass(getattr(real_rfq, n))
)

# The three RFQ rejection errors whose constructors a ported bot may call with
# keyword args — their __init__ signatures must match py-sdk name-for-name.
_REJECTION_ERRORS = (
    "RfqQuoteRejectedError",
    "RfqCancelQuoteRejectedError",
    "RfqConfirmationRejectedError",
)

# The RfqSession protocol's call contract (public methods a maker bot invokes).
_PROTOCOL_PUBLIC_METHODS = ("cancel_quote", "close", "quote", "respond_to_confirmation")


def _init_param_names(cls: type) -> list[str]:
    """``__init__`` parameter names (incl. ``self``) in declared order."""
    return list(inspect.signature(cls.__init__).parameters)


# ── __all__ name contract ────────────────────────────────────────────────────


def test_rfq_all_matches_pysdk() -> None:
    """The mirror's ``rfq.__all__`` is exactly py-sdk's ``rfq.__all__``.

    Equality (not subset) is the contract: a MISSING name breaks the import swap
    one way (this is the gap that hid ``RfqTradeEvent``), and an EXTRA name breaks
    it the other way (a bot porting back to py-sdk would import a non-existent
    name). Both directions are reported distinctly.
    """
    real = set(real_rfq.__all__)
    mirror = set(mirror_rfq.__all__)
    missing = real - mirror
    extra = mirror - real
    assert not missing, f"mirror rfq.__all__ is MISSING py-sdk RFQ names: {sorted(missing)}"
    assert not extra, f"mirror rfq.__all__ has names py-sdk's rfq LACKS: {sorted(extra)}"


def test_rfq_all_names_are_real_attributes() -> None:
    """Every name in ``__all__`` resolves on both modules (no dangling export)."""
    for name in mirror_rfq.__all__:
        assert hasattr(mirror_rfq, name), f"mirror rfq.__all__ lists {name!r} but it is not an attr"
        assert hasattr(real_rfq, name), f"py-sdk rfq lacks {name!r} (pin moved?)"


# ── enums (members + values) ─────────────────────────────────────────────────


def test_rfq_enum_discovery_is_nonempty() -> None:
    """Guard the auto-discovery: a refactor that empties it must not silently pass."""
    assert _REAL_ENUMS, "discovered no RFQ enums on py-sdk — discovery or pin is broken"
    assert "RfqErrorCode" in _REAL_ENUMS


@pytest.mark.parametrize("enum_name", _REAL_ENUMS)
def test_rfq_enum_members_match_pysdk(enum_name: str) -> None:
    """Each RFQ enum has exactly py-sdk's members, with identical values.

    Covers the ``INVALID_SIGNATURE`` / ``INTERNAL_ERROR`` additions (b9) on
    ``RfqErrorCode`` and any future member drop/rename/add on any RFQ enum.
    """
    real_cls = getattr(real_rfq, enum_name)
    mirror_cls = getattr(mirror_rfq, enum_name)
    real = {n: m.value for n, m in real_cls.__members__.items()}
    mirror = {n: m.value for n, m in mirror_cls.__members__.items()}
    assert mirror == real, (
        f"{enum_name} drift vs py-sdk:\n"
        f"  missing from mirror: {sorted(set(real) - set(mirror))}\n"
        f"  extra on mirror:     {sorted(set(mirror) - set(real))}\n"
        f"  value mismatches:    "
        f"{ {k: (real[k], mirror.get(k)) for k in real if k in mirror and real[k] != mirror[k]} }"
    )


# ── dataclasses (field names + order) ────────────────────────────────────────


def test_rfq_dataclass_discovery_is_nonempty() -> None:
    assert _REAL_DATACLASSES, "discovered no RFQ dataclasses on py-sdk — discovery/pin broken"
    assert "RfqTradeEvent" in _REAL_DATACLASSES


@pytest.mark.parametrize("dc_name", _REAL_DATACLASSES)
def test_rfq_dataclass_fields_match_pysdk(dc_name: str) -> None:
    """Each RFQ dataclass mirrors py-sdk's fields (names + order).

    Field *annotations* are not compared — py-sdk's ``ComboConditionId`` /
    ``PositionId`` are ``str`` aliases and the mirror uses bare ``str``; the
    call/construct contract is the field names + order.
    """
    mirror_cls = getattr(mirror_rfq, dc_name)
    assert dataclasses.is_dataclass(mirror_cls), f"mirror {dc_name} is not a dataclass"
    real_fields = [f.name for f in dataclasses.fields(getattr(real_rfq, dc_name))]
    mirror_fields = [f.name for f in dataclasses.fields(mirror_cls)]
    assert mirror_fields == real_fields, (
        f"{dc_name} field drift:\n  py-sdk: {real_fields}\n  mirror: {mirror_fields}"
    )


# ── RfqEvent union ───────────────────────────────────────────────────────────


def test_rfq_event_union_members_match_pysdk() -> None:
    """The ``RfqEvent`` union carries the same member types (by name) as py-sdk."""
    real = {t.__name__ for t in get_args(real_rfq.RfqEvent)}
    mirror = {t.__name__ for t in get_args(mirror_rfq.RfqEvent)}
    assert mirror == real, (
        "RfqEvent union drift:\n"
        f"  missing from mirror: {sorted(real - mirror)}\n"
        f"  extra on mirror:     {sorted(mirror - real)}"
    )


# ── rejection-error constructors (the error_id axis) ─────────────────────────


@pytest.mark.parametrize("error_name", _REJECTION_ERRORS)
def test_rfq_rejection_error_init_signature_matches_pysdk(error_name: str) -> None:
    """Each RFQ rejection error's ``__init__`` param names match py-sdk.

    This is what catches the ``error_id`` kwarg (b9) — and any future constructor
    param drift — on the three classes a ported bot may instantiate.
    """
    real_params = _init_param_names(getattr(real_rfq, error_name))
    mirror_params = _init_param_names(getattr(mirror_rfq, error_name))
    assert mirror_params == real_params, (
        f"{error_name}.__init__ signature drift:\n"
        f"  py-sdk: {real_params}\n  mirror: {mirror_params}"
    )
    # Spell out the b9 addition explicitly so the intent survives future edits.
    assert "error_id" in mirror_params, f"{error_name} is missing the error_id kwarg"


# ── RfqSession protocol ──────────────────────────────────────────────────────


def test_rfq_session_protocol_methods_match_pysdk() -> None:
    """The mirror's ``RfqSession`` protocol declares the same method surface."""

    def contract(proto: type) -> set[str]:
        # Public contract methods + the async-iteration/-context dunders a bot's
        # ``async with``/``async for`` over a session relies on — excluding
        # Protocol machinery (``_is_protocol`` etc.) and object dunders.
        async_dunders = {"__await__", "__aiter__", "__anext__", "__aenter__", "__aexit__"}
        return {n for n in dir(proto) if not n.startswith("_")} | (set(dir(proto)) & async_dunders)

    real = contract(real_rfq.RfqSession)
    mirror = contract(mirror_rfq.RfqSession)
    assert mirror == real, (
        "RfqSession protocol method drift:\n"
        f"  missing from mirror: {sorted(real - mirror)}\n"
        f"  extra on mirror:     {sorted(mirror - real)}"
    )


@pytest.mark.parametrize("method", _PROTOCOL_PUBLIC_METHODS)
def test_rfq_session_public_method_signatures_match_pysdk(method: str) -> None:
    """Each public ``RfqSession`` method's param (name, kind) matches py-sdk."""
    real_sig = _param_signature(real_rfq.RfqSession, method)
    mirror_sig = _param_signature(mirror_rfq.RfqSession, method)
    assert mirror_sig == real_sig, (
        f"RfqSession.{method} signature drift:\n  py-sdk: {real_sig}\n  mirror: {mirror_sig}"
    )
