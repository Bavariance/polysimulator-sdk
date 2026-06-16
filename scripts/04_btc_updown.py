#!/usr/bin/env python3
"""Discover the live BTC Up/Down window (read-only).

BTC Up/Down is ~89% of real API order flow, but these markets don't surface
through ``list_markets`` — they have a dedicated endpoint. This script shows the
documented discovery path: ``get_updown`` for the live underlying spot price and
``list_updown(..., live=True)`` for the currently-tradeable window, then prints
everything a bot needs to place an order (condition_id, outcome token_ids,
current prices, and the resolution strike).

It places NO orders — copy the ``cid`` / ``token_ids`` it prints into
``01_balance_and_market.py``'s ``place_order`` call to actually trade.

Usage:
    POLYSIM_API_KEY=ps_live_… python 04_btc_updown.py [ASSET] [INTERVAL]
    POLYSIM_API_KEY=ps_live_… python 04_btc_updown.py BTC 5M
    POLYSIM_API_KEY=ps_live_… POLYSIM_BASE_URL=https://staging-api.polysimulator.com \\
        python 04_btc_updown.py
"""

from __future__ import annotations

import json
import sys

from polysim_sdk import PolySimClient


def main() -> int:
    asset = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    interval = sys.argv[2] if len(sys.argv) > 2 else "5M"

    with PolySimClient() as client:  # POLYSIM_API_KEY from env
        # Live underlying spot + what assets/intervals exist live in the full payload.
        payload = client.get_updown(asset=asset)
        spot = (payload.get("crypto_prices") or {}).get(asset) or {}
        if spot.get("price") is not None:
            print(
                f"{asset} spot: ${float(spot['price']):,.2f} "
                f"(source={spot.get('source')}, 24h={spot.get('change_24h')})"
            )
        print(f"available intervals: {payload.get('available_intervals')}")
        print(f"interval counts:     {payload.get('interval_counts')}\n")

        # The currently-tradeable window(s) for this asset+interval.
        live = client.list_updown(asset=asset, interval=interval, live=True)
        if not live:
            print(f"No live {asset}/{interval} window right now. Try another interval.")
            return 1

        window = live[0]
        print(f"Live {asset}/{interval} window:")
        print(f"  slug         = {window.get('slug')}")
        print(f"  time_range   = {window.get('time_range')}")
        print(f"  condition_id = {window.get('condition_id')}")
        print(f"  start / end  = {window.get('start_date')}  →  {window.get('end_date')}")
        # Strike (the BTC reference price the window resolves against). It is
        # None on a window that has not opened yet; populated once it's live.
        strike = window.get("group_item_threshold")
        print(f"  strike       = {strike}  (None until the window opens)")
        print(f"  live_price   = {window.get('live_price')}")

        # Outcome tokens live in the nested markets[] array. The list-ish fields
        # come back as JSON strings, so parse them.
        nested = (window.get("markets") or [{}])[0]
        outcomes = _maybe_json(nested.get("outcomes"))
        prices = _maybe_json(nested.get("outcome_prices"))
        token_ids = _maybe_json(nested.get("token_ids"))
        print("\n  outcomes / prices / token_ids:")
        for o, p, t in zip(outcomes, prices, token_ids, strict=False):
            print(f"    {o:>5}  price={p}  token_id={t}")

        print(
            "\nTo trade: call place_order(market_id=<condition_id>, side='BUY', "
            "outcome='Up'|'Down', quantity>=5, order_type='market', price='0.99')."
        )
    return 0


def _maybe_json(value: object) -> list:
    """Parse a JSON-string list field; tolerate already-parsed lists."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


if __name__ == "__main__":
    raise SystemExit(main())
