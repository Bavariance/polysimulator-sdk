# PolySimulator Python SDK

The official Python client for the [PolySimulator](https://polysimulator.com)
paper-trading API. **One package, two import surfaces:**

| Import surface | Use it when |
|---|---|
| `polysim_sdk` | You're starting fresh. A clean, modern client — sync **and** async, WebSocket streaming, pagination iterators, typed exceptions. |
| `polysim_clob_client` | You already have a bot written against Polymarket's [`py-clob-client`](https://github.com/Polymarket/py-clob-client). This is a **drop-in mirror** — port by changing the import path, the host, and the auth call. |

PolySimulator is paper trading, so there is **no on-chain anything**: no private
key, no `chain_id`, no `funder`, no `signature_type`, no EIP-712 signing, no USDC
allowance/approval transactions, no web3/Polygon RPC. The SDK depends only on
`httpx` and `websockets`. Authentication is a single `ps_live_*` API key sent as
the `X-API-Key` header.

---

## Install

Targets Python 3.10+.

```bash
pip install polysimulator
```

> The install name is **`polysimulator`**; the import name stays **`polysim_sdk`**
> (like `pip install pillow` → `import PIL`). The older `polysim-sdk` name is now a
> thin alias that installs `polysimulator` for you, so existing installs keep working.

From a checkout of this directory (for development):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # editable install + pytest/ruff/mypy/build/twine
```

Provide your key via the `POLYSIM_API_KEY` environment variable (recommended) or
pass it to the constructor. To target staging instead of production:

```bash
export POLYSIM_API_KEY="ps_live_…"
export POLYSIM_BASE_URL="https://staging-api.polysimulator.com"
```

---

## Quickstart — native `polysim_sdk`

```python
from polysim_sdk import PolySimClient

with PolySimClient() as client:        # POLYSIM_API_KEY from env
    me = client.me()
    print(me["balance"])               # your trading balance (a JSON string)

    market = client.list_markets(limit=1, hot_only=True)[0]
    cid = market["condition_id"]

    book = client.get_book(cid)                  # by condition id (+ outcome=/depth=)
    asks = book.get("asks") or []                # levels: [{"price","size"}, ...]
    best_ask = min((float(a["price"]) for a in asks), default=None)
    print("best ask:", best_ask)                 # lowest ask = best price to buy at

    # …or by outcome-token id, the Polymarket-native way:
    #   token_book = client.get_book_by_token("<outcome-token-id>")

    fill = client.place_order(
        market_id=cid,
        side="BUY",
        outcome="YES",
        quantity=10,
        order_type="market",
        price="0.99",              # worst-acceptable fill (YES < $1 → "any fill")
    )
    print(f"filled {fill['status']} @ {fill.get('price')}")
```

Async is the same API with `await`:

```python
import asyncio
from polysim_sdk import AsyncPolySimClient

async def main():
    async with AsyncPolySimClient() as client:
        me = await client.me()
        print(me["balance"])

asyncio.run(main())
```

---

## Finding BTC Up/Down markets

Short-horizon **crypto Up/Down** markets — "is BTC/ETH/SOL/… up or down over
this window" resolving on a 5-minute … daily horizon — are the platform's
most-traded product. They have a dedicated endpoint and do **not** show up
through `list_markets(q="btc")` (free-text search is the `q` filter). Use
`list_updown` / `get_updown`:

```python
with PolySimClient() as client:
    # Currently-tradeable BTC 5-minute windows, filtered server-side:
    live = client.list_updown(asset="BTC", interval="5M", live=True)
    window = live[0]
    print(window["slug"])                   # btc-updown-5m-1781400900
    print(window["time_range"])             # "9:35PM-9:40PM ET"
    print(window["group_item_threshold"])   # resolution strike (None until the window opens)

    # Outcome tokens live in the nested markets[] array (JSON-string fields):
    nested = window["markets"][0]
    print(nested["outcomes"])               # '["Up", "Down"]'
    print(nested["outcome_prices"])         # '["0.595", "0.405"]'
    cid = window["condition_id"]
```

`interval` is one of `5M` / `15M` / `1H` / `4H` / `daily` (**uppercase**).
`live=True` keeps only `active and not closed and not resolved` windows.

The **live underlying spot price** plus the available assets/intervals come from
the full payload via `get_updown`:

```python
payload = client.get_updown(asset="BTC")
btc = (payload.get("crypto_prices") or {}).get("BTC") or {}
print(btc.get("price"))               # 64588.0 — live BTC/USD (coingecko)
print(payload["available_intervals"]) # ['5M', '15M', '1H', '4H', 'daily']
print(payload["interval_counts"])     # {'5M': 169, '15M': 402, ...}
```

> The spot price can be momentarily absent right at a window boundary (the
> payload is cached and the feed can lag a second or two), so read it
> defensively with `.get(...)` rather than chained `[...]` indexing.

### Pricing an Up/Down market

The raw order book (`get_book`) on an Up/Down market is a **synthetic
placeholder** — its `asks[0]` sits near ~0.99 and is not a real resting quote.
For price, use the market's own fields instead:

- `window["live_price"]` → `{"buy": 0.595, "sell": 0.405, ...}` (current marketable prices)
- `nested["outcome_prices"]` → the latest Up/Down implied probabilities
- the realised `price` returned by `place_order` — the SDK fills against the real
  internal book and reports the true fill price, `fee`, and `book_walk_levels`

A market order with a worst-price cap still fills correctly regardless; just
don't read `get_book(...)["asks"][0]` as a tradeable quote on these markets.

> **Drop-in `polysim_clob_client` users:** `get_midpoint`, `get_price`,
> `get_spread` and `calculate_market_price` read the outcome token's **synthetic
> ~0.99 ladder** on Up/Down markets — they are *not* the underlying asset price.
> For the asset, use the native reads below.

### Up/Down for HFT: strike, spot, and a push feed

The two numbers an Up/Down strategy actually trades on — the **strike**
("price to beat") and the **live underlying spot** — have dedicated reads on the
native client, plus a push stream and a few pure helpers:

```python
from polysim_sdk import PolySimClient
from polysim_sdk import updown

with PolySimClient() as client:
    live = client.list_updown(asset="BTC", interval="5M", live=True)
    window = updown.next_to_expire(live) or live[0]   # the contract closing soonest
    cid = window["condition_id"]

    strike = client.get_price_to_beat(cid)["price"]   # GET /prices/ptb/{cid}; 404 = not set yet
    spot = client.get_spot("BTC")["price"]            # GET /prices/live/BTC — live underlying
    # all spots in one call: client.get_spots()["prices"]

    print(updown.ptb_distance(spot, strike))          # signed price units; >0 ⇒ "Up" in the money
    print(updown.ptb_distance_bps(spot, strike))      # same, in bps of the strike
    print(updown.seconds_to_expiry(window))           # time left on the window
```

`get_price_to_beat` raises `ApiError` with `status_code == 404` while a window's
strike is still pending — that is "not set yet", not "market absent". It accepts
a `condition_id` **or** an outcome-token id. Its `source` field tells you the
provenance: `polymarket_open_price` / `polymarket_scrape` / `gamma_event_metadata`
(Polymarket's own reported strike) and `chainlink_onchain` / `chainlink_timeline`
(the resolution oracle) are the settlement strike; `gamma_api` / `cryptocompare`
are best-effort fallbacks — treat them as indicative.

The `polysim_sdk.updown` helpers are pure functions over the row dicts (no
network, tolerant of missing fields): `seconds_to_expiry`, `is_window_open`,
`price_to_beat` (reads the strike off a row you already have), `ptb_distance` /
`ptb_distance_bps`, `open_windows`, `next_to_expire`, plus the `ASSETS` /
`INTERVALS` vocab tuples. For a **streaming** tap on the underlying, see
[SSE: live underlying spot](#sse-live-underlying-spot) below.

---

## Migrating from `py-clob-client`

If you have a Polymarket bot, you change **three things** and delete the on-chain
prelude. Everything else — method names, argument shapes, return shapes — stays.

```diff
- from py_clob_client.client import ClobClient
- from py_clob_client.clob_types import OrderArgs, OrderType
+ from polysim_clob_client.client import ClobClient
+ from polysim_clob_client.clob_types import OrderArgs, OrderType

- client = ClobClient(
-     host="https://clob.polymarket.com",
-     key=PRIVATE_KEY,                 # your wallet's private key
-     chain_id=POLYGON,
-     signature_type=1,
-     funder=PROXY_WALLET_ADDRESS,
- )
- client.set_api_creds(client.create_or_derive_api_creds())
- # ...plus USDC allowance/approval txns, web3 setup, etc.
+ client = ClobClient(
+     host="https://api.polysimulator.com",
+     key="ps_live_…",                 # your PolySimulator API key
+ )

  # unchanged from here on:
  order = client.create_order(OrderArgs(token_id=tid, price=0.55, size=10, side="BUY"))
  resp = client.post_order(order)
```

### What maps how

Every method maps by one of three strategies:

- **mirror** — identical behaviour, delegated to the HTTP core.
- **adapt** — translated onto the PolySim REST surface (e.g. `get_midpoint`,
  `get_price`, `get_spread` are computed from `GET /v1/markets/{id}/book`).
- **stub-noop** — on-chain machinery with no analog (allowances, signing,
  scoring, builder auth). These return a benign canned value and **make no
  network call**; each says so in its docstring so nothing breaks silently.

### `token_id` ↔ `(market_id, outcome)`

`py-clob-client` addresses a single outcome token by `token_id`; PolySimulator
addresses a **market plus an outcome**. The seam: a bare `token_id` is treated as
the market id with outcome `YES`. Append `":NO"` or `":YES"` to target the other
side explicitly.

```python
client.create_order(OrderArgs(token_id="0xMARKET",     ...))  # → market 0xMARKET, YES
client.create_order(OrderArgs(token_id="0xMARKET:NO",  ...))  # → market 0xMARKET, NO
```

**Reads are true-token-parity; writes use the market+outcome model.** This is an
intentional asymmetry:

- **Book/quote reads** — `get_order_book`, `get_midpoint`, `get_price`,
  `get_spread`, `get_tick_size`, `get_neg_risk`, `get_last_trade_price`,
  `calculate_market_price` — send a **bare** `token_id` to the token-native
  endpoint `GET /v1/book?token_id=...`. This matches Polymarket's CLOB book
  reads exactly: pass the real outcome-token id you already use with
  `py-clob-client` and the quote comes back for *that* token, no `:YES`/`:NO`
  needed. The `condition_id:OUTCOME` colon form still works and routes to the
  condition-id book endpoint (threading the outcome through), but it is a
  PolySimulator convenience extension, not the parity path.
- **Order writes** — `create_order` / `create_market_order` / `post_order` —
  resolve the `token_id` through the market+outcome seam above and submit to the
  rich `POST /v1/orders` endpoint (with slippage/impact/position telemetry in the
  response). A *bare* token id on the write path is read as a `market_id` with
  the `YES` outcome; if that id isn't a valid market, the server rejects the
  order loudly — it never silently places a different one.

If you only ever pass real outcome-token ids (the `py-clob-client` norm), reads
"just work" with full parity. The `:YES`/`:NO` suffix exists for callers who
prefer to address PolySimulator markets by condition id directly.

### Orders are never signed

`create_order` / `create_market_order` return a plain `dict` with **no
`signature` field** — there is nothing to sign. `post_order` serialises it
straight to `POST /v1/orders`. The recommended one-call path is
`create_and_post_order(...)`.

### Auth assertions

The three `py-clob-client` auth levels collapse into one. `assert_level_1_auth()`
and `assert_builder_auth()` are no-ops; `assert_level_2_auth()` raises
`PolyApiException` only if no API key is configured.

---

## Pagination

The native client exposes Python iterators that page transparently:

```python
from polysim_sdk import PolySimClient
from polysim_sdk.pagination import iter_markets, iter_orders

with PolySimClient() as client:
    for market in iter_markets(client, hot_only=True):
        ...
    for order in iter_orders(client, status="OPEN"):
        ...
```

The drop-in surface preserves `py-clob-client`'s base64 cursor protocol, so the
classic loop terminates correctly:

```python
from polysim_clob_client.constants import END_CURSOR

cursor = ""
while cursor != END_CURSOR:
    page = client.get_markets(cursor)
    handle(page["data"])
    cursor = page["next_cursor"]
```

---

## WebSocket streaming

```python
from polysim_sdk import PolySimClient
from polysim_sdk.ws import prices_stream, executions_stream

with PolySimClient() as client:
    for event in prices_stream(client, ["0xMARKET_A", "0xMARKET_B"]):
        print(event)            # blocks; reconnects with a fresh token + backoff
```

Async generators (`aprices_stream`, `aexecutions_stream`) are available for
`async for`. The SDK mints a short-lived WS JWT per connection automatically.

### SSE: live underlying spot

The WS channel above carries **market** prices. The live **underlying spot**
(BTC/ETH/SOL/… ticks — the number an Up/Down bet resolves against) comes from the
public, unauthenticated `/prices/stream` Server-Sent-Events firehose instead:

```python
from polysim_sdk import PolySimClient
from polysim_sdk.sse import spot_stream

with PolySimClient() as client:
    for event in spot_stream(client, ["BTC", "ETH"], crypto_source="chainlink"):
        if event["event"] == "crypto_price":
            tick = event["data"]
            print(tick["symbol"], tick["price"], tick["source"])
```

Each event is `{"event": <type>, "data": <payload>}`. The spot-bearing types are
`crypto_price` and `crypto_price_batch`; `keepalive` arrives every ~5 s. Pass
`crypto_source="chainlink"` (authoritative for 5m/15m settlement) or `"binance"`
(denser ~10 Hz ticks); omit for both. With `condition_ids=[...]` the same stream
also delivers `snapshot` / `market_price` / `orderbook` events. The async twin
`aspot_stream` is available for `async for`. Both reconnect with exponential
backoff and a per-connection cache-buster (so a CDN can't coalesce subscribers).

---

## Errors

Catch `PolySimError` to handle every SDK-raised error; `PolyApiException` (the
drop-in alias) is the same class.

```python
from polysim_sdk.exceptions import (
    EdgeBlockedError,
    PolySimError,
    RateLimitError,
    ValidationError,
)

try:
    fill = client.place_order(...)
except RateLimitError as exc:
    print(f"backing off for {exc.retry_after}s")
except ValidationError as exc:
    print(f"bad request: {exc.code} → {exc}")
except EdgeBlockedError as exc:
    print(f"blocked at the CDN edge — check your User-Agent: {exc}")
except PolySimError as exc:
    print(f"other API error ({exc.status_code}): {exc}")
```

The client paces itself (a 50 ms floor between requests) and backs off on
`429`/`425`/`5xx` using `Retry-After`. Opt out with `floor_interval=0.0` and
`max_retries=0` to handle pacing yourself.

### Troubleshooting: `error code: 1010` / blocked User-Agent

If a raw HTTP call returns **HTTP 403** with the body `error code: 1010`, the
request was blocked at the CDN edge — **not** by the API. The edge rejects
Python's stdlib `urllib` default User-Agent (`Python-urllib/x.y`). `requests`,
`httpx`, `aiohttp`, and **this SDK** all send User-Agents that pass, so you only
hit this with raw `urllib` or a custom client that forwards the stdlib UA.

This SDK sends a branded `User-Agent: polysim-sdk/<version>` and turns any edge
block into a clear `EdgeBlockedError` (an `ApiError`, `code="EDGE_BLOCKED"`). If
you must call the API without the SDK, set your own header:

```python
# raw urllib — set a User-Agent so the edge lets you through
import urllib.request, json
req = urllib.request.Request(
    "https://api.polysimulator.com/v1/markets?limit=1",
    headers={"User-Agent": "my-app/1.0", "X-API-Key": "ps_live_..."},
)
data = json.load(urllib.request.urlopen(req))
```

---

## Rate limits

Authoritative values are returned live by `GET /v1/keys/tiers` (`client.tiers()`).
At the time of writing:

| Tier | rps | rpm | WS conns | Batch | API balance |
|---|---|---|---|---|---|
| Free | 2 | 120 | 1 | 1 | none — read only |
| Pro | 10 | 600 | 3 | 5 | $10K |
| Pro+ | 30 | 1800 | 10 | 10 | $25K |
| Enterprise | 100 | 6000 | 50 | 25 | custom |

`free` allows 2 requests/second up to 120/minute and is read-only (no API
balance). Use `pro` or above for any continuously-running bot. The table is
illustrative — always trust `client.tiers()` for the live values.

---

## Beta caveats

During the closed-cohort phase, a few sim behaviours differ from Polymarket.
These are known limitations, not bugs:

1. **Shared API balance across keys.** Every key a user owns draws on the same
   balance pool today (the `balance` field returned by `me()`). Treat it as
   user-scoped, not key-scoped.
2. **Maker/taker heuristic.** `is_maker = (time_in_force == "GTC")`. A GTC limit
   that crosses on submit is booked as a $0-fee maker in the sim; on Polymarket
   it would be a taker. We over-count makers by design until the classifier is
   tightened.
3. **Orderbook freshness.** The book cache TTL is 300 s. For markets Polymarket
   updates less often than that, a fill may revert to displayed midpoint; a ±15%
   sanity guard bounds the drift. High-volume markets always fill at real ask/bid.

---

## What's included

| Path | Purpose |
|---|---|
| `polysim_sdk/` | Native client: `PolySimClient` (sync) + `AsyncPolySimClient` (async), `ws`, `sse` (underlying-spot stream), `updown` (Up/Down row helpers), `pagination`, `exceptions`, `constants`. |
| `polysim_clob_client/` | `py-clob-client` drop-in: `ClobClient` + matching `clob_types`, `constants`, `order_builder`, `exceptions`. |
| `scripts/01_balance_and_market.py` | Smallest end-to-end native demo. |
| `scripts/02_clob_dropin_demo.py` | The `py-clob-client` drop-in surface end-to-end. |
| `scripts/03_async_concurrent.py` | Concurrent reads via the async client. |
| `scripts/04_btc_updown.py` | Discover the live BTC Up/Down window: spot, strike, prices, tokens. |
| `tests/` | respx-mocked unit tests for both surfaces (no creds, no network). |

Run the test suite with `pytest`. Lint/type with `ruff check . && mypy polysim_sdk polysim_clob_client`.

---

## License

[Apache License 2.0](./LICENSE). You may use, modify, and redistribute this
SDK — including in commercial and closed-source projects — provided you keep
the license and attribution notices (see [`NOTICE`](./NOTICE)). Apache-2.0 also
grants an explicit patent license, which is why many companies prefer it over
MIT for dependencies.

The `polysim_clob_client` package mirrors the *public surface* of
[`py-clob-client`](https://github.com/Polymarket/py-clob-client) (MIT) for
drop-in compatibility; no upstream source is bundled. This project is not
affiliated with or endorsed by Polymarket.
