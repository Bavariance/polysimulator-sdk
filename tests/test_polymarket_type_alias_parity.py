"""Member parity for re-exported ``Literal`` type aliases.

The re-export suites assert a promoted name (e.g. ``TickSize``) exists on the
mirror root and in ``__all__`` — but a ``Literal`` alias's **members** are invisible
to a name-only check. So when py-sdk widened ``TickSize`` in 0.1.0b12 (adding the
``"0.005"`` / ``"0.0025"`` tick sizes), a mirror that still declared the old
four-member literal would have shipped **stale** with every existing test green.

This gate compares the members of each re-exported ``Literal`` alias against the
**real installed** ``polymarket`` package (skipped when it isn't installed), so a
future tick-grid change (or any widened order-model literal) fails here instead of
silently drifting. Members are compared as sets — ``Literal`` argument order is not
part of the contract, the accepted value set is.
"""

from __future__ import annotations

from typing import get_args

import pytest

import polysim_polymarket

real_polymarket = pytest.importorskip("polymarket")

# Re-exported ``Literal`` aliases whose accepted value set must track py-sdk. Each
# name must resolve on both roots; extend this list when the mirror promotes a new
# closed-set order-model literal.
_LITERAL_ALIASES = ["TickSize"]


@pytest.mark.parametrize("alias", _LITERAL_ALIASES)
def test_literal_alias_members_match_pysdk(alias: str) -> None:
    real = getattr(real_polymarket, alias)
    mirror = getattr(polysim_polymarket, alias)
    real_members = set(get_args(real))
    mirror_members = set(get_args(mirror))
    assert real_members, f"py-sdk {alias} has no Literal members (pin moved / not a Literal?)"
    assert mirror_members == real_members, (
        f"{alias} member drift vs py-sdk:\n"
        f"  missing from mirror: {sorted(real_members - mirror_members)}\n"
        f"  extra on mirror:     {sorted(mirror_members - real_members)}"
    )
