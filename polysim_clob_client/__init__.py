"""py-clob-client-compatible surface for the PolySimulator paper-trading API.

Drop-in for Polymarket's ``py-clob-client``: a bot ports by changing only the
import path, the host, and the auth call — and deleting the on-chain prelude
(private key, ``chain_id``, ``funder``, ``signature_type``, USDC allowance,
web3/Polygon RPC). See :mod:`polysim_clob_client.client` for the method map.

    from polysim_clob_client.client import ClobClient   # was: py_clob_client.client
    client = ClobClient(host="https://api.polysimulator.com", key="ps_live_...")
"""

from __future__ import annotations

from polysim_clob_client.client import ClobClient
from polysim_clob_client.clob_types import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    BookParams,
    MarketOrderArgs,
    OrderArgs,
    OrderType,
    PostOrdersArgs,
)
from polysim_clob_client.constants import (
    AMOY,
    END_CURSOR,
    POLYGON,
    START_CURSOR,
    ZERO_ADDRESS,
)
from polysim_clob_client.exceptions import PolyApiException, PolyException

__all__ = [
    "ClobClient",
    "ApiCreds",
    "AssetType",
    "BalanceAllowanceParams",
    "BookParams",
    "MarketOrderArgs",
    "OrderArgs",
    "OrderType",
    "PostOrdersArgs",
    "PolyApiException",
    "PolyException",
    "AMOY",
    "POLYGON",
    "START_CURSOR",
    "END_CURSOR",
    "ZERO_ADDRESS",
]
