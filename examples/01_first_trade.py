"""Balance, a market, its book, one order. The 60-second version.

    export POLYSIM_API_KEY=ps_live_...
    python examples/01_first_trade.py
"""
from polysim_sdk import PolySimClient


def main() -> None:
    with PolySimClient() as client:          # reads POLYSIM_API_KEY from the env
        me = client.me()
        print(f"balance: {me['balance']}")

        # `hot_only` sorts by recent activity — a market with a real book, not a
        # dormant one. An empty book is the usual reason a first order does nothing.
        market = client.list_markets(limit=1, hot_only=True)[0]
        cid = market["condition_id"]
        print(f"market: {market.get('question', cid)[:70]}")

        book = client.get_book(cid)
        asks = book.get("asks") or []
        bids = book.get("bids") or []
        if not asks:
            print("no asks — nothing to buy against; try another market")
            return
        best_ask = min(float(a["price"]) for a in asks)
        best_bid = max((float(b["price"]) for b in bids), default=0.0)
        print(f"book: {len(bids)} bids / {len(asks)} asks · "
              f"best bid {best_bid:.3f} · best ask {best_ask:.3f}")

        # A market BUY still needs a worst-acceptable price. A YES share can never
        # settle above $1, so "0.99" means "any fill" while still bounding you if
        # the book is thin — the SDK will not send an unbounded market order.
        fill = client.place_order(
            market_id=cid, side="BUY", outcome="YES",
            quantity=10, order_type="market", price="0.99",
        )
        print(f"order {fill['status']} · filled @ {fill.get('price')} "
              f"· id {fill.get('id')}")

        for p in client.list_positions():
            if p.get("market_id") == cid:
                print(f"position: {p.get('quantity')} {p.get('outcome')} "
                      f"@ avg {p.get('avg_entry_price')}")


if __name__ == "__main__":
    main()
