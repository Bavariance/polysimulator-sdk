#!/usr/bin/env python3
"""Concurrent reads with the async client.

A bot or dashboard usually needs the book for *many* markets at once. The
async client lets you fan those reads out concurrently instead of paying the
round-trip latency serially. This script reads N markets' books both ways and
prints the speedup, then places one tiny order to show writes work async too.

The client still paces itself (a 50 ms floor) and backs off on 429, so a wide
``asyncio.gather`` won't stampede the rate limiter — it self-throttles.

Usage:
    POLYSIM_API_KEY=ps_live_… python 03_async_concurrent.py
    POLYSIM_API_KEY=ps_live_… POLYSIM_BASE_URL=https://staging-api.polysimulator.com \\
        python 03_async_concurrent.py
"""

from __future__ import annotations

import asyncio
import time

from polysim_sdk import AsyncPolySimClient
from polysim_sdk.exceptions import ApiError


def _money(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


async def main() -> int:
    async with AsyncPolySimClient() as client:  # POLYSIM_API_KEY from env
        me = await client.me()
        print(f"You: {me.get('email') or '?'}  balance=${_money(me.get('balance')):,.2f}")

        markets = await client.list_markets(limit=8, hot_only=True)
        cids = [m["condition_id"] for m in markets]
        if not cids:
            print("No hot markets available.")
            return 1
        print(f"fanning out book reads for {len(cids)} markets\n")

        # Sequential baseline.
        t0 = time.perf_counter()
        for cid in cids:
            await client.get_book(cid)
        seq = time.perf_counter() - t0

        # Concurrent: one gather, all books at once.
        t0 = time.perf_counter()
        books = await asyncio.gather(*(client.get_book(cid) for cid in cids))
        conc = time.perf_counter() - t0

        for m, book in zip(markets, books, strict=False):
            q = str(m.get("question", ""))[:48]
            nb, na = len(book.get("bids", [])), len(book.get("asks", []))
            print(f"  {q:48}  bids={nb:>3} asks={na:>3}")
        speedup = seq / conc if conc else float("inf")
        print(f"\nsequential={seq:.2f}s  concurrent={conc:.2f}s  speedup={speedup:.1f}×")

        # Writes work async too — one tiny FOK/IOC market order (5 shares).
        try:
            fill = await client.place_order(
                market_id=cids[0],
                side="BUY",
                outcome="Yes",
                quantity=5.0,
                order_type="market",
                price="0.99",
                time_in_force="IOC",
            )
            oid = fill.get("order_id") or fill.get("id")
            print(
                f"\n✓ async order: status={fill.get('status')} "
                f"price={fill.get('price')} order_id={oid}"
            )
        except ApiError as exc:
            print(f"\norder rejected: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
