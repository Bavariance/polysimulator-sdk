"""PolySimulator reference Python SDK.

A small, opinionated wrapper around the public REST + WebSocket API
that handles the boring parts (auth, rate-limit pacing, retry on
transient 5xx, structured exceptions) so example strategy code can
stay focused on the strategy.

The SDK is intentionally NOT a full ORM. Responses come back as
``dict`` (the actual JSON the server sent); a strategy that wants
typed access can do its own casting. This keeps the package thin
and avoids the SDK lagging behind the API on every new field.

Example:
    from polysim_sdk import PolySimClient

    client = PolySimClient()  # picks up POLYSIM_API_KEY from env
    me = client.me()
    print(me["api_balance"])
    fill = client.place_order(
        market_id="0x…",
        side="BUY",
        outcome="Yes",
        quantity=10,
        order_type="market",
    )
    print(fill["fee"], fill["slippage_bps"])
"""

from polysim_sdk.aio import AsyncPolySimClient
from polysim_sdk.client import PolySimClient
from polysim_sdk.constants import OrderType, Side
from polysim_sdk.exceptions import (
    ApiError,
    EdgeBlockedError,
    PolySimError,
    RateLimitError,
    ValidationError,
)

__all__ = [
    "PolySimClient",
    "AsyncPolySimClient",
    "OrderType",
    "Side",
    "ApiError",
    "EdgeBlockedError",
    "PolySimError",
    "RateLimitError",
    "ValidationError",
]

__version__ = "0.2.1"
