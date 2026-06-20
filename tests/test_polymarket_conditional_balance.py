"""Tests for ``get_balance_allowance(asset_type='CONDITIONAL', token_id=...)``.

Resolution of the CodeRabbit/Codex disagreement: the real py-sdk's
``get_balance_allowance`` for ``asset_type='CONDITIONAL'`` returns the
**conditional TOKEN** balance the server holds for that token_id (the position's
share count), NOT collateral cash. Our paper mirror has no on-chain CTF balance,
so it reports the open position's share count for the resolved (market, outcome)
in USDC base units (the conditional token also has 6 decimals); a flat position
is a genuine 0 conditional balance.
"""

from __future__ import annotations

import httpx
import pytest

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"


@pytest.fixture
def secure():
    from polysim_polymarket import SecureClient

    c = SecureClient(host=BASE_URL, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


def test_collateral_reports_cash(secure, respx_mock):
    """COLLATERAL still reports paper cash USD scaled to base units (unchanged)."""
    respx_mock.get(f"{BASE_URL}/v1/account/balance").mock(
        return_value=httpx.Response(200, json={"balance": "10000"})
    )
    ba = secure.get_balance_allowance(asset_type="COLLATERAL")
    assert ba.balance == 10_000 * 1_000_000


def test_conditional_reports_position_share_balance(secure, respx_mock):
    """CONDITIONAL with a token_id reports the conditional token's position
    balance (share count) in base units, not cash."""
    respx_mock.get(f"{BASE_URL}/v1/account/positions").mock(
        return_value=httpx.Response(
            200,
            json={
                "positions": [
                    {"market_id": "0xcond", "outcome": "Yes", "quantity": "42.5"},
                    {"market_id": "0xother", "outcome": "No", "quantity": "9"},
                ]
            },
        )
    )
    ba = secure.get_balance_allowance(asset_type="CONDITIONAL", token_id="0xcond:YES")
    # 42.5 shares -> 42.5 * 1e6 base units.
    assert ba.balance == 42_500_000


def test_conditional_flat_position_is_zero(secure, respx_mock):
    """No matching position -> a genuine 0 conditional balance (not an error)."""
    respx_mock.get(f"{BASE_URL}/v1/account/positions").mock(
        return_value=httpx.Response(200, json={"positions": []})
    )
    ba = secure.get_balance_allowance(asset_type="CONDITIONAL", token_id="0xcond:YES")
    assert ba.balance == 0


def test_conditional_outcome_match_is_case_insensitive(secure, respx_mock):
    """The resolver returns upper-case outcomes; positions store mixed case."""
    respx_mock.get(f"{BASE_URL}/v1/account/positions").mock(
        return_value=httpx.Response(
            200, json={"positions": [{"market_id": "0xcond", "outcome": "No", "quantity": "7"}]}
        )
    )
    ba = secure.get_balance_allowance(asset_type="CONDITIONAL", token_id="0xcond:NO")
    assert ba.balance == 7_000_000


def test_conditional_requires_token_id(secure, respx_mock):
    """CONDITIONAL without a token_id raises UserInputError (there is no token to
    scope the conditional balance to)."""
    from polysim_polymarket import UserInputError

    with pytest.raises(UserInputError):
        secure.get_balance_allowance(asset_type="CONDITIONAL")


async def test_async_conditional_reports_position_balance(respx_mock):
    """Async twin: CONDITIONAL reports the conditional-token position balance."""
    from polysim_polymarket import AsyncSecureClient

    c = AsyncSecureClient(host=BASE_URL, api_key=API_KEY)
    c._client._transport._floor_interval = 0.0
    try:
        respx_mock.get(f"{BASE_URL}/v1/account/positions").mock(
            return_value=httpx.Response(
                200,
                json={"positions": [{"market_id": "0xcond", "outcome": "Yes", "quantity": "3"}]},
            )
        )
        ba = await c.get_balance_allowance(asset_type="CONDITIONAL", token_id="0xcond:YES")
        assert ba.balance == 3_000_000
    finally:
        await c.close()
