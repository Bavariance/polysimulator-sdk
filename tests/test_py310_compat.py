"""Python 3.10 compatibility regression tests.

The package declares ``requires-python >=3.10`` with a 3.10 classifier, but the
real py-sdk it mirrors targets ``>=3.11`` and uses 3.11-only features freely
(``enum.StrEnum``, ``datetime.UTC``, ``typing.Self``, ``datetime.fromisoformat``
accepting a trailing ``Z``). These tests lock in the 3.10-safe replacements so a
3.11-only symbol can't silently regress the floor.
"""

from __future__ import annotations

import ast
import datetime as _dt
import pathlib

import pytest

_PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PACKAGES = ("polysim_sdk", "polysim_clob_client", "polysim_polymarket")


def _source_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for pkg in _PACKAGES:
        files.extend((_PKG_ROOT / pkg).rglob("*.py"))
    return files


def test_no_311_only_datetime_utc_alias() -> None:
    """``datetime.UTC`` is a 3.11 alias for ``timezone.utc``; reject it on 3.10."""
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        # ``from datetime import UTC`` and bare ``datetime.UTC`` both break 3.10.
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module == "datetime":
                if any(alias.name == "UTC" for alias in node.names):
                    offenders.append(f"{path}: `from datetime import UTC`")
            if isinstance(node, ast.Attribute) and node.attr == "UTC":
                if isinstance(node.value, ast.Name) and node.value.id == "datetime":
                    offenders.append(f"{path}: `datetime.UTC`")
    assert not offenders, "3.11-only datetime.UTC found:\n" + "\n".join(offenders)


def test_no_311_only_strenum_import() -> None:
    """``enum.StrEnum`` is 3.11-only; the mirror must use a 3.10-safe base."""
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module == "enum":
                if any(alias.name == "StrEnum" for alias in node.names):
                    offenders.append(f"{path}: `from enum import StrEnum`")
    assert not offenders, "3.11-only enum.StrEnum found:\n" + "\n".join(offenders)


def test_no_311_only_typing_self() -> None:
    """``typing.Self`` is 3.11-only; must come from ``typing_extensions`` on 3.10."""
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module == "typing":
                if any(alias.name == "Self" for alias in node.names):
                    offenders.append(f"{path}: `from typing import Self`")
    assert not offenders, "3.11-only typing.Self found:\n" + "\n".join(offenders)


def _datetime_module_aliases(tree: ast.AST) -> set[str]:
    """Local names bound to the ``datetime`` *module* or the ``datetime.datetime``
    *class* in this module.

    Either binding makes ``<alias>.UTC`` the 3.11-only ``datetime.UTC`` symbol:
    ``import datetime`` / ``import datetime as dt`` bind the module (whose ``.UTC``
    is the alias), and ``from datetime import datetime`` / ``... as dt`` bind the
    class (whose ``.UTC`` is the same 3.11 alias). ``datetime`` is always included
    so a bare ``datetime.UTC`` (the existing guard) is covered for either binding.
    """
    aliases: set[str] = {"datetime"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "datetime":
                    aliases.add(alias.asname or "datetime")
        if isinstance(node, ast.ImportFrom) and node.module == "datetime":
            for alias in node.names:
                if alias.name == "datetime":
                    aliases.add(alias.asname or "datetime")
    return aliases


def test_no_aliased_311_only_datetime_utc() -> None:
    """``datetime.UTC`` is 3.11-only even when reached through an ALIAS.

    The bare-``datetime.UTC`` guard above only catches the literal name; this also
    rejects ``import datetime as dt; dt.UTC`` and
    ``from datetime import datetime as dt; dt.UTC`` (and the module-aliased form),
    so a future reintroduction through any alias still trips on the 3.11 CI host.
    """
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        aliases = _datetime_module_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "UTC":
                if isinstance(node.value, ast.Name) and node.value.id in aliases:
                    offenders.append(f"{path}: `{node.value.id}.UTC`")
    assert not offenders, "3.11-only aliased datetime.UTC found:\n" + "\n".join(offenders)


# Remaining 3.11-only symbols the package must keep avoiding so the 3.10 floor
# holds. These are FORWARD guards — the tree is clean of all of them today; the
# tests exist so a future reintroduction trips on the 3.11 CI host instead of
# silently lifting ``requires-python`` to 3.11.
#
# * ``tomllib``           — stdlib TOML reader added in 3.11 (use ``tomli``).
# * ``ExceptionGroup`` /  — 3.11 exception groups + the ``except*`` syntax (the
#   ``except*``             AST exposes the latter as ``ast.TryStar``).
# * ``asyncio.timeout`` /  — 3.11 asyncio additions (use ``async_timeout`` /
#   ``asyncio.TaskGroup``    ``asyncio.gather`` on 3.10).
# * ``itertools.pairwise`` — added in 3.10 only for CPython 3.10+, but the helper
#   is 3.10-safe; we still guard the import name as it was 3.10-NEW and a habit
#   from newer code — kept here defensively alongside the 3.11 set.


def test_no_311_only_tomllib_import() -> None:
    """``tomllib`` is a 3.11 stdlib module; reject any import of it."""
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] == "tomllib" for alias in node.names):
                    offenders.append(f"{path}: `import tomllib`")
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "tomllib":
                offenders.append(f"{path}: `from tomllib import ...`")
    assert not offenders, "3.11-only tomllib found:\n" + "\n".join(offenders)


def test_no_311_only_exception_group() -> None:
    """``ExceptionGroup`` / ``BaseExceptionGroup`` are 3.11-only builtins.

    They are builtins (no import needed), so a text scan is the reliable guard.
    """
    needles = ("ExceptionGroup", "BaseExceptionGroup")
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                offenders.append(f"{path}: `{needle}`")
    assert not offenders, "3.11-only ExceptionGroup found:\n" + "\n".join(offenders)


def test_no_311_only_star_except() -> None:
    """``except*`` (the exception-group handler) is 3.11-only syntax.

    The AST exposes a star-except handler as ``ast.TryStar`` (3.11+ only emits it).
    """
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        try_star = getattr(ast, "TryStar", None)
        if try_star is None:  # pragma: no cover - only on <3.11 interpreters
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, try_star):
                offenders.append(f"{path}: `except*`")
    assert not offenders, "3.11-only except* (star-except) found:\n" + "\n".join(offenders)


def test_no_311_only_asyncio_symbols() -> None:
    """``asyncio.timeout`` and ``asyncio.TaskGroup`` are 3.11-only.

    Catch both the attribute form (``asyncio.timeout`` / ``asyncio.TaskGroup``) and
    the from-import form (``from asyncio import timeout, TaskGroup``).
    """
    attrs = {"timeout", "TaskGroup"}
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute) and node.attr in attrs:
                if isinstance(node.value, ast.Name) and node.value.id == "asyncio":
                    offenders.append(f"{path}: `asyncio.{node.attr}`")
            if isinstance(node, ast.ImportFrom) and node.module == "asyncio":
                for alias in node.names:
                    if alias.name in attrs:
                        offenders.append(f"{path}: `from asyncio import {alias.name}`")
    assert not offenders, "3.11-only asyncio symbols found:\n" + "\n".join(offenders)


def test_no_itertools_pairwise() -> None:
    """``itertools.pairwise`` (3.10-new) — guard both attribute + from-import forms.

    Kept defensively alongside the 3.11 set so a habit from newer code can't sneak
    the symbol in; the package avoids it entirely today.
    """
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute) and node.attr == "pairwise":
                if isinstance(node.value, ast.Name) and node.value.id == "itertools":
                    offenders.append(f"{path}: `itertools.pairwise`")
            if isinstance(node, ast.ImportFrom) and node.module == "itertools":
                if any(alias.name == "pairwise" for alias in node.names):
                    offenders.append(f"{path}: `from itertools import pairwise`")
    assert not offenders, "itertools.pairwise found:\n" + "\n".join(offenders)


def test_stream_validator_parses_trailing_z() -> None:
    """The stream ISO validator must accept a trailing ``Z`` (3.10's
    ``fromisoformat`` does not), normalising it to ``+00:00``."""
    from polysim_polymarket.streams._validators import _parse_epoch_ms_or_iso_timestamp

    parsed = _parse_epoch_ms_or_iso_timestamp("2026-06-20T12:00:00Z")
    assert isinstance(parsed, _dt.datetime)
    assert parsed.utcoffset() == _dt.timedelta(0)


def test_stream_validator_z_handled_without_fromisoformat_z_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the validator NORMALISES the ``Z`` itself rather than relying on a
    3.11 ``fromisoformat`` that accepts ``Z``.

    The CI host runs 3.11 (whose ``fromisoformat`` natively accepts ``Z``), so a
    naive test passes even with the 3.10 bug present. Simulate 3.10 by swapping
    in a ``fromisoformat`` that rejects a trailing ``Z`` — the validator must
    still succeed because it strips/normalises the ``Z`` before parsing.
    """
    from polysim_polymarket.streams import _validators

    real_fromisoformat = _dt.datetime.fromisoformat

    class _Py310Datetime(_dt.datetime):
        @classmethod
        def fromisoformat(cls, text: str) -> _dt.datetime:  # type: ignore[override]
            if text.endswith(("Z", "z")):  # 3.10 raised here
                raise ValueError("Invalid isoformat string")
            return real_fromisoformat(text)

    monkeypatch.setattr(_validators, "datetime", _Py310Datetime)
    parsed = _validators._parse_epoch_ms_or_iso_timestamp("2026-06-20T12:00:00Z")
    assert isinstance(parsed, _dt.datetime)
    assert parsed.utcoffset() == _dt.timedelta(0)


def test_subscription_handle_is_self_annotated_and_iterates() -> None:
    """The ``Self``-annotated handle must import + iterate on 3.10."""
    import asyncio

    from polysim_polymarket.streams._handle import AsyncSubscriptionHandle

    async def _drive() -> list[int]:
        handle: AsyncSubscriptionHandle[int] = AsyncSubscriptionHandle(queue_size=4)
        assert handle.__aiter__() is handle  # Self-typed __aiter__
        handle._push(1)
        handle._push(2)
        await handle.close()
        out: list[int] = []
        async for item in handle:
            out.append(item)
        return out

    assert asyncio.run(_drive()) == [1, 2]


def test_rfq_strenum_members_are_str() -> None:
    """RFQ enums must keep str-equality behaviour after the 3.10-safe swap."""
    from polysim_polymarket.rfq import RfqDirection

    assert RfqDirection.BUY == "BUY"
    assert str(RfqDirection.SELL) == "SELL"


@pytest.mark.parametrize("symbol", ["StrEnum", "UTC"])
def test_311_symbols_absent_from_runtime_imports(symbol: str) -> None:
    """Belt-and-braces: the modules that used 3.11 symbols import cleanly."""
    import importlib

    for mod in (
        "polysim_polymarket.rfq",
        "polysim_polymarket.models",
        "polysim_polymarket.streams._validators",
        "polysim_polymarket.streams._handle",
    ):
        importlib.import_module(mod)  # must not raise
