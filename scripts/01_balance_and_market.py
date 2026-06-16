#!/usr/bin/env python3
"""Smallest possible end-to-end demo.

Reads the caller's profile + balance, then places a tiny IOC market BUY
and prints the realised fill (price, fee, slippage_bps) so a developer
trying the API for the first time has a concrete success-path
template to copy.

Note: the API returns monetary fields (balance, notional, fee, …) as JSON
*strings* — by design, so the thin SDK never lags the server on new fields.
Cast them with ``float()`` before doing arithmetic or ``:.2f`` formatting.
The ``_money`` helper below does exactly that.

Usage:
    POLYSIM_API_KEY=ps_live_… python 01_balance_and_market.py [MARKET_ID]
    POLYSIM_API_KEY=ps_live_… POLYSIM_BASE_URL=https://staging-api.polysimulator.com \\
        python 01_balance_and_market.py
"""

from __future__ import annotations

import sys

from polysim_sdk import PolySimClient
from polysim_sdk.exceptions import ApiError


def _money(value: object, default: float = 0.0) -> float:
    """Cast a string/number money field to float (API sends them as strings)."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def main() -> int:
    market_id = sys.argv[1] if len(sys.argv) > 1 else None

    client = PolySimClient()
    me = client.me()
    print(f"You: {me.get('email') or '?'}  tier={me.get('tier') or '?'}")
    print(f"Balance: ${_money(me.get('balance')):,.2f}")

    # If no market id given, pick the first hot one.
    if market_id is None:
        markets = client.list_markets(limit=5, hot_only=True)
        if not markets:
            print("No hot markets available. Pass a condition_id explicitly.", file=sys.stderr)
            return 1
        market_id = markets[0]["condition_id"]
        print(f"Using first hot market: {markets[0]['question'][:60]}")

    # Tiny IOC market BUY on Yes — 1 share, worst-price 0.99 (YES outcomes
    # trade < $1 so any fill is acceptable). PolySim follows Polymarket's
    # "marketable limit" model — every market order requires a worst-price
    # cap. There are no unlimited market orders.
    #
    # Markets enforce a minimum order size (commonly 5 shares — the value
    # GET /v1/markets/{id}/book advertises as ``min_order_size``); a too-small
    # order is rejected with a clear INVALID_ORDER_MIN_SIZE-style message.
    try:
        fill = client.place_order(
            market_id=market_id,
            side="BUY",
            outcome="Yes",
            quantity=5.0,
            order_type="market",
            price="0.99",
            time_in_force="IOC",
        )
    except ApiError as exc:
        print(f"Order failed: {exc}", file=sys.stderr)
        return 2

    print()
    print("✓ Filled:")
    print(f"  order_id    = {fill.get('order_id') or fill.get('id')}")
    print(f"  shares      = {fill.get('quantity')}")
    print(f"  fill price  = {fill.get('price')}")
    if fill.get("notional") is not None:
        print(f"  notional    = ${_money(fill.get('notional')):,.4f}")
    else:
        print("  notional    = (unset)")
    if fill.get("fee") is not None:
        print(f"  fee         = ${_money(fill['fee']):,.4f}  (Polymarket V2 — pass-through)")
    if fill.get("slippage_bps") is not None:
        print(f"  slippage    = {fill['slippage_bps']} bps")
    print(f"  new balance = ${_money(fill.get('account_balance')):,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
