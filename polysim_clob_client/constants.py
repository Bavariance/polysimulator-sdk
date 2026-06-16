"""py-clob-client-compatible constants.

Re-exported verbatim so ``from py_clob_client.constants import POLYGON`` ports
to ``from polysim_clob_client.constants import POLYGON`` with no other change.

The chain ids, auth levels and on-chain addresses have **no behavioural
meaning** in the paper-trading SDK — there is no chain, no signing and no
settlement. They exist only so import lines and constructor kwargs resolve.
The canned addresses are Polymarket's real Polygon mainnet contract addresses,
returned by the stubbed address getters for display parity.
"""

from __future__ import annotations

# Chain ids (ignored — no on-chain activity).
POLYGON = 137
AMOY = 80002

# Auth levels in the real client: L0 public, L1 private-key signing, L2 HMAC.
# The paper SDK collapses all three into one API-key mode; these are kept so
# code that references them still imports.
L0 = 0
L1 = 1
L2 = 2

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Pagination sentinels (base64 of 0 and -1). The parity layer translates these
# to/from limit/offset internally so `while cursor != END_CURSOR` loops work.
START_CURSOR = "MA=="
END_CURSOR = "LTE="

# Canned Polymarket Polygon-mainnet addresses (display parity only).
COLLATERAL_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e
CONDITIONAL_TOKENS_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # CTF
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # CTF Exchange
NEG_RISK_EXCHANGE_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

__all__ = [
    "POLYGON",
    "AMOY",
    "L0",
    "L1",
    "L2",
    "ZERO_ADDRESS",
    "START_CURSOR",
    "END_CURSOR",
    "COLLATERAL_ADDRESS",
    "CONDITIONAL_TOKENS_ADDRESS",
    "EXCHANGE_ADDRESS",
    "NEG_RISK_EXCHANGE_ADDRESS",
]
