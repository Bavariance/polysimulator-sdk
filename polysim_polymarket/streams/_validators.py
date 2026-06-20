"""Field validators for stream event models, mirroring
``polymarket.models.clob._validators``.

These differ from the strict CLOB-read validators in ``polysim_polymarket.models``:
stream events carry numbers (not only decimal strings) on the wire — the SSE
crypto frame sends ``price`` as a bare float — and richer timestamp shapes
(epoch-seconds, epoch-ms, or ISO depending on the field). We mirror py-sdk's
permissive parsers field-for-field so the parsed scalar types match.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

from pydantic import BeforeValidator

_EPOCH_MS_THRESHOLD = 10**11


def _coerce_decimalish(value: object) -> object:
    """Accept ``Decimal`` / ``str`` / ``int`` / ``float`` and coerce to a
    decimal-stringy form (rejecting ``bool``). Mirrors py-sdk."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"expected decimal-ish value, got bool {value!r}")
    if isinstance(value, Decimal | str):
        return value
    if isinstance(value, int | float):
        return str(value)
    raise ValueError(f"expected decimal-ish value, got {type(value).__name__}")


def _parse_epoch_ms_timestamp(value: object) -> object:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError(f"expected epoch-ms timestamp string, got {type(value).__name__}")
    if not value.isdecimal():
        raise ValueError(f"invalid epoch-ms timestamp: {value!r}")
    ms = int(value)
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError(f"invalid epoch-ms timestamp: {value!r}") from error


def _parse_epoch_ms_or_iso_timestamp(value: object) -> object:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        raise ValueError(f"expected timestamp, got bool {value!r}")
    if isinstance(value, int):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as error:
            raise ValueError(f"invalid epoch-ms timestamp: {value!r}") from error
    if isinstance(value, str):
        if value.isdecimal():
            ms = int(value)
            try:
                return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            except (OverflowError, OSError, ValueError) as error:
                raise ValueError(f"invalid epoch-ms timestamp: {value!r}") from error
        # 3.10's ``datetime.fromisoformat`` does NOT accept a trailing ``Z``
        # (Zulu/UTC) — that only landed in 3.11. The package floor is 3.10, so
        # normalise a trailing ``Z`` to ``+00:00`` before parsing, matching how
        # the rest of the SDK (``polysim_sdk.updown._parse_iso``) handles it.
        iso = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
        try:
            return datetime.fromisoformat(iso)
        except ValueError as error:
            raise ValueError(f"invalid timestamp: {value!r}") from error
    raise ValueError(f"expected epoch-ms or ISO timestamp, got {type(value).__name__}")


def _parse_epoch_seconds_or_ms_timestamp(value: object) -> object:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        raise ValueError(f"expected epoch timestamp, got bool {value!r}")
    if isinstance(value, int):
        magnitude = value
    elif isinstance(value, str):
        if not value.isdecimal():
            raise ValueError(f"invalid epoch timestamp: {value!r}")
        magnitude = int(value)
    else:
        raise ValueError(f"expected epoch timestamp, got {type(value).__name__}")
    seconds = magnitude / 1000 if magnitude >= _EPOCH_MS_THRESHOLD else magnitude
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError(f"invalid epoch timestamp: {value!r}") from error


def _parse_epoch_seconds_timestamp(value: object) -> object:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, bool):
        raise ValueError(f"expected epoch-seconds timestamp, got bool {value!r}")
    if isinstance(value, int):
        seconds = value
    elif isinstance(value, str):
        if not value.isdecimal():
            raise ValueError(f"invalid epoch-seconds timestamp: {value!r}")
        seconds = int(value)
    else:
        raise ValueError(f"expected epoch-seconds timestamp, got {type(value).__name__}")
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError(f"invalid epoch-seconds timestamp: {value!r}") from error


def _parse_expiration_timestamp(value: object) -> object:
    if value == 0 or value == "0":
        return None
    return _parse_epoch_seconds_timestamp(value)


if TYPE_CHECKING:
    _DecimalFromNumberOrString = Decimal
else:
    _DecimalFromNumberOrString = Annotated[Decimal, BeforeValidator(_coerce_decimalish)]

EpochMsTimestamp = Annotated[datetime | None, BeforeValidator(_parse_epoch_ms_timestamp)]
EpochMsOrIsoTimestamp = Annotated[
    datetime | None, BeforeValidator(_parse_epoch_ms_or_iso_timestamp)
]
EpochSecondsTimestamp = Annotated[datetime | None, BeforeValidator(_parse_epoch_seconds_timestamp)]
EpochSecondsOrMsTimestamp = Annotated[
    datetime | None, BeforeValidator(_parse_epoch_seconds_or_ms_timestamp)
]
ExpirationTimestamp = Annotated[datetime | None, BeforeValidator(_parse_expiration_timestamp)]


__all__ = [
    "EpochMsOrIsoTimestamp",
    "EpochMsTimestamp",
    "EpochSecondsOrMsTimestamp",
    "EpochSecondsTimestamp",
    "ExpirationTimestamp",
]
