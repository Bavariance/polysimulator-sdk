#!/usr/bin/env python3
"""The ``py-clob-client`` drop-in surface, end to end.

If you already have a Polymarket bot, this is the file to read. Every call
below has the same name and shape as Polymarket's ``ClobClient`` — you change
the import path, the host, and the auth (one API key, no wallet), and delete
the on-chain prelude. Nothing here signs anything or touches a chain.

What it shows:
  * constructing ClobClient with on-chain kwargs still accepted (and ignored)
  * the auth-level asserts collapsing to one key
  * the base64-cursor pagination loop (``while cursor != END_CURSOR``)
  * book / midpoint / price / spread reads
  * create_order producing an UNSIGNED payload (no ``signature`` field)
  * the token_id ↔ (market, outcome) seam (bare = YES, ``:NO`` = NO)
  * placing one tiny real FOK market order via create_market_order + post_order

Usage:
    POLYSIM_API_KEY=ps_live_… python 02_clob_dropin_demo.py
    POLYSIM_API_KEY=ps_live_… POLYSIM_BASE_URL=https://staging-api.polysimulator.com \\
        python 02_clob_dropin_demo.py
"""

from __future__ import annotations

import os
import sys

from polysim_clob_client.client import ClobClient
from polysim_clob_client.clob_types import MarketOrderArgs, OrderArgs
from polysim_clob_client.constants import END_CURSOR
from polysim_clob_client.exceptions import PolyApiException
from polysim_clob_client.order_builder.constants import BUY


def main() -> int:
    host = os.environ.get("POLYSIM_BASE_URL", "https://api.polysimulator.com")
    key = os.environ.get("POLYSIM_API_KEY")
    if not key:
        print("Set POLYSIM_API_KEY first.", file=sys.stderr)
        return 1

    # Drop-in construction: the on-chain kwargs are accepted for source
    # compatibility and quietly ignored — there is no chain, signer, or funder.
    client = ClobClient(
        host=host,
        key=key,  # your ps_live_… key goes in the old "private key" slot
        chain_id=137,  # ignored
        signature_type=1,  # ignored
        funder="0x0000000000000000000000000000000000000000",  # ignored
    )

    # The three py-clob auth levels collapse into one key. These asserts are
    # no-ops except assert_level_2_auth, which only checks a key is configured.
    client.assert_level_1_auth()
    client.assert_level_2_auth()
    client.assert_builder_auth()
    print(f"reachable={client.get_ok()}  server_time={client.get_server_time()}")

    # ── base64-cursor pagination (the classic py-clob loop) ─────────────────
    cursor = ""  # START
    pages = 0
    sample = None
    while cursor != END_CURSOR and pages < 2:
        page = client.get_markets(cursor)
        rows = page["data"]
        if sample is None and rows:
            sample = rows[0]
        pages += 1
        nxt = page["next_cursor"]
        shown = "END" if nxt == END_CURSOR else nxt[:8] + "…"
        print(f"page {pages}: {len(rows)} markets, next_cursor={shown}")
        cursor = nxt
    if sample is None:
        print("No markets returned.", file=sys.stderr)
        return 1

    token_id = sample.get("condition_id") or sample.get("id")
    print(f"\nusing market: {str(sample.get('question', ''))[:60]}  token={token_id[:14]}…")

    # ── market-data reads (each maps to a PolySim REST call) ────────────────
    try:
        ob = client.get_order_book(token_id)
        print(f"order book: bids={len(ob.bids)} asks={len(ob.asks)}")
        print(f"midpoint={client.get_midpoint(token_id)}  spread={client.get_spread(token_id)}")
        print(f"best BUY price={client.get_price(token_id, BUY)}")
    except PolyApiException as exc:
        print(f"market-data read failed: {exc}", file=sys.stderr)

    # ── create_order builds an UNSIGNED payload (verify: no signature) ──────
    unsigned = client.create_order(OrderArgs(token_id=token_id, price=0.40, size=5, side=BUY))
    assert "signature" not in unsigned, "drop-in must never emit a signature"
    print(f"\ncreate_order payload keys: {sorted(unsigned)} (no 'signature' — nothing to sign)")

    # ── token_id ↔ (market, outcome) seam ───────────────────────────────────
    yes = client.create_order(OrderArgs(token_id=token_id, price=0.40, size=5, side=BUY))
    no = client.create_order(OrderArgs(token_id=f"{token_id}:NO", price=0.40, size=5, side=BUY))
    print(f"seam: bare→outcome={yes.get('outcome')}   ':NO'→outcome={no.get('outcome')}")

    # ── place ONE tiny real FOK market order (5 shares) ─────────────────────
    try:
        args = MarketOrderArgs(token_id=token_id, amount=5, side=BUY)
        fill = client.post_order(client.create_market_order(args))
        print("\n✓ posted market order:")
        print(f"  status={fill.get('status')}  price={fill.get('price')}")
        print(f"  qty={fill.get('quantity')}  fee={fill.get('fee')}")
        print(f"  order_id={fill.get('order_id') or fill.get('id')}")
    except PolyApiException as exc:
        print(f"\norder rejected: {exc}", file=sys.stderr)

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
