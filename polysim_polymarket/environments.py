"""Environment configuration, mirroring ``polymarket.environments``.

py-sdk's :class:`polymarket.environments.Environment` is a frozen, kw-only
dataclass whose ``PRODUCTION`` instance carries Polymarket's per-service hosts
(``clob_url``, ``data_url``, ``gamma_url``, ``rfq_url``, the CLOB market/user
WebSocket URLs, …) alongside on-chain config (chain id, contract addresses).

The sim->real swap is "point the client at a different ``Environment``". So we
keep the **same field names** for the host/URL surface a market-data read bot
touches, and populate them with PolySimulator hosts. PolySimulator exposes one
unified REST API (``api.polysimulator.com``), so the per-service URLs that are
distinct hosts on real Polymarket all resolve to that single host here — the
field names still line up for the swap.

The on-chain contract-address fields from py-sdk's ``Environment`` are
deliberately omitted: paper trading has no chain, no wallet, and no contracts,
so there is nothing to mirror. ``chain_id`` is kept (py-sdk has it) but is inert.
"""

from __future__ import annotations

from dataclasses import dataclass

_API_HOST = "https://api.polysimulator.com"
_WS_HOST = "wss://api.polysimulator.com"


@dataclass(frozen=True, slots=True, kw_only=True)
class Environment:
    """A PolySimulator environment, field-name-compatible with py-sdk.

    Frozen + kw-only to match ``polymarket.environments.Environment``. The
    fields here are the host/URL surface a market-data read client needs; the
    sim->real swap is constructing a :class:`PublicClient` against the real
    Polymarket ``Environment`` instead of this one.
    """

    name: str
    chain_id: int
    clob_url: str
    clob_market_ws_url: str
    clob_user_ws_url: str
    gamma_url: str
    data_url: str
    rfq_url: str


PRODUCTION = Environment(
    name="production",
    # Mirrors py-sdk's Polygon chain id for signature parity; inert on paper.
    chain_id=137,
    # PolySimulator serves every CLOB/data/gamma read off one unified REST host.
    clob_url=_API_HOST,
    clob_market_ws_url=f"{_WS_HOST}/v1/ws/market",
    clob_user_ws_url=f"{_WS_HOST}/v1/ws/user",
    gamma_url=_API_HOST,
    data_url=_API_HOST,
    rfq_url=_API_HOST,
)


__all__ = ["Environment", "PRODUCTION"]
