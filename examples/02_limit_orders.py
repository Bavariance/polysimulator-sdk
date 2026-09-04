"""Resting a limit, watching it fill, cancelling. Post-only and IOC/FOK.

This is the file that shows what a paper venue with a real book gives you that a
formula-priced sandbox cannot: an order that SITS until the market comes to it.

    python examples/02_limit_orders.py
"""
import time

from polysim_sdk import PolySimClient


def main() -> None:
    with PolySimClient() as client:
        market = client.list_markets(limit=1, hot_only=True)[0]
        cid = market["condition_id"]
        book = client.get_book(cid)
        bids = book.get("bids") or []
        if not bids:
            print("no bids on this market; try another")
            return
        best_bid = max(float(b["price"]) for b in bids)

        # Rest well below the best bid so it CANNOT fill immediately. On a real
        # book this order waits. On an LMSR-style sandbox there is nothing to wait
        # for — the price is a function of size, so a "limit" fills or is rejected.
        resting_price = round(max(0.01, best_bid - 0.05), 2)
        order = client.place_order(
            market_id=cid, side="BUY", outcome="YES",
            quantity=10, order_type="limit", price=str(resting_price),
        )
        print(f"resting BUY 10 @ {resting_price} (best bid {best_bid:.3f}) "
              f"· status {order['status']}")

        time.sleep(2)
        pending = [o for o in client.list_pending_orders()
                   if o.get("id") == order.get("id")]
        print(f"still resting after 2s: {bool(pending)}")

        # post_only refuses rather than crossing. Send one AT the best ask, which
        # would cross, and it is rejected as INVALID_POST_ONLY_ORDER — that
        # rejection is the feature, not an error.
        asks = book.get("asks") or []
        if asks:
            crossing = min(float(a["price"]) for a in asks)
            try:
                client.place_order(
                    market_id=cid, side="BUY", outcome="YES", quantity=10,
                    order_type="limit", price=str(crossing), post_only=True,
                )
                print("post-only crossed and was accepted — unexpected")
            except Exception as exc:                     # noqa: BLE001
                print(f"post-only correctly refused to cross: "
                      f"{type(exc).__name__}")

        if pending:
            client.cancel_order(order["id"])
            print("cancelled; reservation released back to balance")


if __name__ == "__main__":
    main()
