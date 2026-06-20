"""Review-fix tests for ``polysim_polymarket.clients._account``.

Covers two adversarial-review findings on the account adapter:
  * USD -> USDC base-unit conversion must use ``Decimal`` (not ``float``) so a
    monetary value with binary-float drift converts exactly.
  * ``validate_asset_type`` must reject an unhashable input with a
    ``UserInputError`` (py-sdk's guard), not let a ``TypeError`` escape from the
    ``in`` membership test against the frozenset.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from polysim_polymarket.clients import _account
from polysim_polymarket.errors import UserInputError


def test_balance_conversion_uses_decimal_not_float() -> None:
    """``1.0000005`` USD: ``float(1.0000005) * 1e6`` rounds to 1000001, but the
    exact decimal half-even rounds to 1000000. The adapter must report the exact
    decimal base-unit count."""
    ba = _account.adapt_balance_allowance({"balance": "1.0000005"})
    expected = int(round(Decimal("1.0000005") * _account.USDC_BASE_UNITS_PER_USD))
    assert ba.balance == expected
    assert ba.balance == 1000000  # exact, not the float-drift 1000001


def test_balance_conversion_common_values_exact() -> None:
    for usd, base in [("10000", 10_000_000_000), ("0.07", 70_000), ("12345.67", 12_345_670_000)]:
        ba = _account.adapt_balance_allowance({"balance": usd})
        assert ba.balance == base


def test_validate_asset_type_rejects_unhashable_with_user_input_error() -> None:
    """An unhashable arg (list/dict) must raise UserInputError, not TypeError."""
    with pytest.raises(UserInputError):
        _account.validate_asset_type(["COLLATERAL"])
    with pytest.raises(UserInputError):
        _account.validate_asset_type({"asset_type": "COLLATERAL"})


def test_validate_asset_type_rejects_non_string() -> None:
    with pytest.raises(UserInputError):
        _account.validate_asset_type(123)
    with pytest.raises(UserInputError):
        _account.validate_asset_type(None)


def test_validate_asset_type_accepts_valid() -> None:
    # Must not raise.
    _account.validate_asset_type("COLLATERAL")
    _account.validate_asset_type("CONDITIONAL")
