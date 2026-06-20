# PolySimulator Python SDK

[![PyPI](https://img.shields.io/pypi/v/polysimulator.svg)](https://pypi.org/project/polysimulator/)
[![Python versions](https://img.shields.io/pypi/pyversions/polysimulator.svg)](https://pypi.org/project/polysimulator/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)
[![py-clob-client compat](https://img.shields.io/badge/py--clob--client-v1%20drop--in-blue.svg)](https://github.com/Polymarket/py-clob-client)
[![py-sdk mirror](https://img.shields.io/badge/polymarket--client-0.1.0b8%20mirror-blueviolet.svg)](https://pypi.org/project/polymarket-client/)

> **Mirrors Polymarket's unified py-sdk** — `polysim_polymarket` is a paper-mode mirror of [`polymarket-client`](https://pypi.org/project/polymarket-client/) **`0.1.0b8`**: sync + async PublicClient/SecureClient, trading, and core streams. Swap the import prefix + host + auth and your py-sdk bot runs on paper. [Compat matrix ↓](#compat-matrix)

The official Python client for the [PolySimulator](https://polysimulator.com)
paper-trading API. **One package, three import surfaces:**

| Import surface | Use it when |
|---|---|
| `polysim_sdk` | You're starting fresh. A clean, modern client — sync **and** async, WebSocket streaming, pagination iterators, typed exceptions. |
| `polysim_clob_client` | You already have a bot written against Polymarket's [`py-clob-client`](https://github.com/Polymarket/py-clob-client). This is a **drop-in mirror** — port by changing the import path, the host, and the auth call. |
| `polysim_polymarket` | You target Polymarket's newer unified **[py-sdk](https://pypi.org/project/polymarket-client/)** (`pip install polymarket-client`, import `polymarket`). A **paper-mode mirror** of its unified surface — sync **and** async `PublicClient` / `SecureClient` (reads, account/auth, trading) plus core realtime streams. Port by swapping the import prefix + host + auth. See [Polymarket py-sdk (v2) mirror](#polymarket-py-sdk-v2-mirror) — read the [sim→real seam contract](#the-simreal-seam-contract-what-paper-mode-does-and-doesnt-do) and [compat matrix](#compat-matrix) before you rely on a surface area. |

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

### New-market warm-up: retry order placement on a 404

A **brand-new** market — especially the 5m Up/Down windows that roll every few
minutes — has a short window after creation before it enters the
order-validation catalog. An order placed in that window transiently fails with
a `404` (`code="MARKET_NOT_FOUND"`). The window is short — on the order of ~30s,
the Up/Down roll cadence — but it is a cadence, **not** a guarantee.

The SDK does **not** auto-retry 404s (by design — a 404 is normally permanent),
so a bot trading freshly-rolled markets should catch and retry that one signal
with backoff. The native client ships a small opt-in helper that retries **only**
the warm-up 404 and re-raises everything else (and the 404 itself once the budget
is spent):

```python
from polysim_sdk import PolySimClient, retry_on_market_warmup

with PolySimClient() as client:
    fill = retry_on_market_warmup(
        lambda: client.place_order(
            market_id=cid, side="BUY", outcome="Up", amount="10", price="0.99",
        ),
        attempts=6, base_delay=2.0,   # ~2,4,8,16,30s capped backoff between tries
    )
```

It wraps any zero-argument callable, so the same helper works with a
`polysim_clob_client` drop-in call (e.g.
`retry_on_market_warmup(lambda: clob.create_and_post_order(args))`); the drop-in
surfaces the same condition as a `PolyApiException` with `status_code == 404`.
See [Errors](#errors) for the standalone retry-on-404 pattern if you'd rather
roll your own.

---

## Polymarket compatibility

`polysim_clob_client` is a drop-in mirror of Polymarket's
[`py-clob-client`](https://github.com/Polymarket/py-clob-client) **v1** — the
`0.x` line, surface-parity with v1 `0.34.x`. Method names, argument shapes, and
return shapes match v1, so a v1 bot ports by changing three things (import path,
host, auth) and deleting the on-chain prelude — see
[Migrating from `py-clob-client`](#migrating-from-py-clob-client) below.

Polymarket's newer [`py-clob-client-v2`](https://github.com/Polymarket/py-clob-client-v2)
is a **separate PyPI package** (`pip install py-clob-client-v2`, import
`py_clob_client_v2`; current `1.0.1`) and a hard breaking release — it renames the
package, changes the constructor and the order-signing payload, and moves to pUSD
collateral. A bot written against v2 will **not** run against this mirror
unchanged (the `py_clob_client_v2` import and the v2 `create_or_derive_api_key()`
bootstrap don't exist here). If you're on v2, either pin `py-clob-client<2` for
the drop-in path, or use the native
[`polysim_sdk`](#quickstart--native-polysim_sdk) client, which is
paper-mode-native and not tied to any `py-clob-client` generation.

| Polymarket client | Compatibility |
|---|---|
| `py-clob-client` v1 (`< 2.0`; latest `0.34.6`) | ✅ Drop-in via `polysim_clob_client` |
| `py-clob-client-v2` (`1.0.x`) | ❌ Not supported — use `polysim_sdk`, or pin `py-clob-client<2` |
| unified **py-sdk** (`polymarket-client`, import `polymarket`; `0.1.0b8`) | 🔶 **Paper-mode mirror** via `polysim_polymarket` — sync + async `PublicClient` / `SecureClient` (reads, account/auth, trading) + core streams; on-chain are paper no-ops and Gamma/Data reads are deferred. See [below](#polymarket-py-sdk-v2-mirror) and the [compat matrix](#compat-matrix) |

The v2-only concepts — EIP-712 v2 signing, pUSD collateral, protocol-side fees,
builder codes — have no analog in paper trading, so there is nothing to port:
you drop them entirely.

---

## Polymarket py-sdk (v2) mirror

Polymarket also ships a newer **unified Python SDK**, published to PyPI as
[`polymarket-client`](https://pypi.org/project/polymarket-client/) and imported
as `polymarket`. It folds the CLOB, gamma (markets/events), data, RFQ, and
realtime-stream surfaces into one client and returns **typed pydantic models**
(`OrderBook`, `Market`, `LastTradePrice`, …) instead of raw dicts.
`polysim_polymarket` is our **paper-mode mirror** of that SDK — the package we
call the **v2 mirror** in this repo (the `polysim_clob_client` mirror above is
v1).

It mirrors the unified client family in paper mode: **sync and async**
`PublicClient` and `SecureClient`, the authenticated **trading** write surface,
the **account/auth/order** reads, the **on-chain** methods (as paper no-ops), the
**rewards/builder/RFQ** stubs, and the **core realtime streams** (`market` /
`user` / `crypto_prices`). What it does *not* mirror are the **Gamma/Data reads**
(events, series, tags, comments, sports, portfolio/positions, leaderboards) and
the **rewards-engine** reads — those are documented-deferred. The exact
support level for every surface area is in the [compat matrix](#compat-matrix)
below; the precise places paper diverges from real are in the
[sim→real seam contract](#the-simreal-seam-contract-what-paper-mode-does-and-doesnt-do).
Read both before you rely on a surface area — this is a **paper-trading** mirror,
not a drop-in for *everything* py-sdk does.

The port is the same mechanical swap as v1 — change the import **prefix**, the
**host**, and the **auth**:

```diff
- from polymarket import SecureClient
+ from polysim_polymarket import SecureClient

- client = SecureClient.create(private_key="0x…")  # real Polymarket wallet + EIP-712 signing
+ client = SecureClient(host="https://api.polysimulator.com", api_key="ps_live_…")

  # unchanged from here on — same method names, same keyword-only call shapes,
  # same model field names/types. A read:
  book = client.get_order_book(token_id="711811…")
  # py-sdk OrderBook ordering: bids ascending (best = bids[-1]), asks
  # descending (best = asks[-1]).
  print(book.bids[-1].price, book.asks[-1].price, book.token_id)

  # …and a trade — flat keyword args, py-sdk's SignedOrder / OrderResponse:
  signed = client.create_market_order(token_id="711811…", side="BUY", amount=10)
  resp = client.post_order(signed)               # paper-accepted, not on-chain-settled
  print(resp.order_id, resp.status)
```

(The `PublicClient` swap is identical — drop the auth entirely:
`PublicClient(host="https://api.polysimulator.com", api_key="ps_live_…")` against
`polymarket`'s `PublicClient()`.)

The model field names and types (`OrderBook`, `OrderBookLevel`,
`LastTradePrice`, `PriceHistoryPoint`, `Market` — with its nested `.state`
`MarketState` — `Page`, `Paginator`, the trading types `OrderSide` / `OrderType`
/ `SignedOrder` / `OrderResponse` / `CancelOrdersResponse`, and the RFQ types) all
track py-sdk so the swap stays mechanical, and every public name a bot imports is
re-exported off the package root, exactly as on real Polymarket — including the
named errors the surface raises (`UserInputError`,
`InsufficientLiquidityError`, `UnexpectedResponseError`), so
`from polymarket import UserInputError` survives the prefix swap unchanged. (Like
py-sdk, `MarketState` itself is *not* promoted to the package root — it's reached
via `market.state` or `polysim_polymarket.models.MarketState`, never `from
polysim_polymarket import MarketState`.) The **one** deliberate divergence is the
error-tree *base* name: those three named errors subclass the
py-clob-client-lineage `PolyException` / `PolyApiException` base (shared by
identity with the v1 mirror) rather than py-sdk's `PolymarketError`, so a bot
that catches the broad base by name needs `except PolyException` here vs `except
PolymarketError` on real Polymarket; an `except UserInputError` block is
identical on both:

```python
from polysim_polymarket import PublicClient, OrderBook, Environment, PolyException, Paginator
```

### The sim→real seam contract: what paper mode does and doesn't do

This is a **paper-trading** mirror. The call shapes and model fields match py-sdk
so the port is mechanical, but paper mode is not Polymarket — it has no chain, no
wallet, and its own market universe. Every place paper deliberately diverges from
real Polymarket is enumerated here, up front, so a bot author knows exactly what
they're getting before they rely on a surface area:

- **Signing is inert (accepted, never settled on-chain).** The authenticated
  trading write surface IS implemented — `create_limit_order` /
  `create_market_order` / `place_limit_order` / `place_market_order` /
  `post_order` / `post_orders` / `cancel_order` / `cancel_orders` / `cancel_all` /
  `cancel_market_orders`, with py-sdk's flat keyword args and `SignedOrder` /
  `OrderResponse` / `CancelOrdersResponse` return models. But paper trading needs
  no signer, so signing is **accepted-and-inert**: the order is produced with an
  **empty signature** and the *unsigned* paper body is submitted. There is **no
  EIP-712 signing, no on-chain settlement, and no Polygon transaction** — the fill
  happens entirely in PolySimulator's paper matching engine.
- **Collateral / balances are PAPER.** Your balance is PolySimulator's paper
  balance (the `balance` from `me()` on the native client), not an on-chain USDC
  balance. `get_balance_allowance(asset_type="COLLATERAL")` reports the paper
  account's cash collateral; `get_balance_allowance(asset_type="CONDITIONAL",
  token_id=…)` reports the **conditional-token** balance — the open position's
  share count for that token, in base units (matching real py-sdk, where a
  CONDITIONAL read returns the held conditional token, not collateral; a flat
  position is `0`). There is no real USDC, no allowance/approval transaction, no
  funder wallet — so `allowances` is always empty.
- **A settled trade carries the per-fill fee RATE, not a fee amount.**
  PolySimulator's matching engine charges and records a real (paper) fee on each
  fill. What it surfaces over the typed read surface is the fee *rate*: a settled
  trade read via `list_account_trades` returns a `ClobTrade` model carrying the
  per-fill fee rate as `fee_rate_bps` (basis points — the category taker rate
  applied to the fill, `0` for fee-free fills), exactly matching py-sdk's
  `ClobTrade`. **What IS surfaced:** the per-fill `fee_rate_bps`. **What is NOT
  surfaced:** a realized per-fill fee *amount* — there is no `fee` / `fee_usdc`
  field on `ClobTrade` (the only model carrying those is `BuilderTrade`, whose
  reads are stubbed — see below), so the sim's debited fee amount is not exposed
  as a typed field on the implemented read surface. **What is NOT mirrored at
  all:** Polymarket **builder** fees (the builder-attribution program — see the
  builder stub row), and any protocol-side / pUSD v2 fee economics; the
  `builder_code=` kwarg on the order builders is accepted for parity but stays
  **inert** (no builder fee taken, no builder-revenue ledger). On maker/taker: the
  sim classifies a marketable-at-placement GTC (one that crosses the book on
  submit) as a **taker** and charges the taker fee, matching Polymarket; only a
  genuinely-resting GTC that is filled later — when the market moves into it — is
  booked as a $0-fee maker. This is a fidelity *match*, not a gap.
- **The market universe is PolySimulator's, NOT Polymarket's live universe.**
  Reads resolve against the markets PolySimulator carries, which is a curated
  subset of Polymarket plus the platform's own crypto **Up/Down** products. A
  `token_id` / `condition_id` / slug that exists on Polymarket but not in
  PolySimulator's universe will not resolve here. Do not assume `list_markets` /
  `get_market` returns the same set, count, or ordering as real Polymarket.
- **On-chain methods are instant PAPER no-ops.** `approve_erc20` /
  `approve_erc1155_for_all` / `transfer_erc20` / `split_position` /
  `merge_positions` / `redeem_positions` / `setup_trading_approvals` /
  `setup_gasless_wallet` carry py-sdk's exact signatures, but paper mode accepts
  the call (replicating py-sdk's pre-chain input guards so a bot hits the SAME
  `UserInputError`) and **settles nothing**: no chain write, no ledger mutation,
  no balance change. Each returns a paper transaction handle whose `.wait()`
  returns **instantly** with a valid-format placeholder hash
  (`0x` + 64 hex, `transaction_id=None`). Unlike real py-sdk,
  `redeem_positions` / `merge_positions` do **not** balance-check the position on
  paper. (Address checks are a 40-hex FORMAT check, not an EIP-55 checksum, and
  the returned address is not checksum-normalized — moot on paper, where it's
  never used on a chain.)
- **Rewards / builder / RFQ are honest stubs.** The rewards/scoring reads return
  honest **empties** (`get_order_scoring` → `False`, the `list_*` rewards reads →
  empty paginators, `get_reward_percentages` → `{}`, …) — the paper rewards engine
  is a separate roadmap item, so a reward-accounting loop runs and truthfully finds
  nothing. The builder-attribution reads (`get_builder_volumes` /
  `list_builder_trades` / `get_builder_fee_rates` / `list_builder_leaderboard`)
  mirror py-sdk's signatures but **raise `NotImplementedError`** — there is no
  builder economy on paper. RFQ has no synchronous `SecureClient` method on py-sdk
  (so the mirror invents none), the RFQ **types** re-export for type-hint parity,
  and any RFQ *action* (incl. the async `open_rfq_session`) **raises
  `NotImplementedError`** — RFQ quoting is not simulated.
- **Streams are core-topics-only, with documented seams.** `subscribe()` is
  **async-only** (sync clients have no `subscribe`, matching py-sdk) and covers
  py-sdk's **CORE** topics: `market` (`MarketSpec`), `crypto_prices`
  (`CryptoPricesSpec`), and the authenticated `user` feed (`UserSpec`, secure
  client only). The **sports / comments / equity** topics are **deferred** (their
  specs/events aren't shipped). The documented stream seams: the market feed is
  **top-of-book, not L2** (it emits `price_change` / `last_trade_price`, never a
  `book` event — call `get_order_book` for the ladder); `MarketSpec.token_ids` are
  the SDK's **`condition_id:LABEL`** tokens (not raw Polymarket numeric token ids);
  stream id fields are plain `str` (not py-sdk's hex-validated newtypes); and the
  lifecycle market events (`NewMarketEvent`, `MarketResolvedEvent`, …) are defined
  for `match`-parity but never emitted on paper. Full detail in
  [Realtime streams](#realtime-streams-v2-mirror).
- **Read-model fidelity gaps.** A handful of implemented reads are present but
  *narrower* than py-sdk's (e.g. `Market` is a focused identity+state subset, not
  the full gamma model; `list_markets` accepts py-sdk's full gamma keyword set but
  only forwards the few PolySimulator honours; `get_last_trade_price` reports a
  constant `side="BUY"` from a book snapshot with no trade side; `Decimal`
  string-form may carry extra trailing zeros; `0` is the no-quote sentinel). None
  break the call shape — see [Fidelity gaps](#fidelity-gaps-within-the-implemented-surface).

Because the real py-sdk is a **superset**, swapping back to real Polymarket only
ever *adds* capability over this seam — it never breaks a bot that stuck to the
mirrored surface and respected the seams above.

### Compat matrix

> Pinned to **`polymarket-client==0.1.0b8`** — parity is re-locked against this exact pin by the full-surface parity gate; when the pin bumps, this matrix + the badge bump with it.

Support level for every py-sdk surface area, cross-checked against what's
actually implemented in `polysim_polymarket`:

| Surface area | Support | Notes |
|---|---|---|
| **Public reads** (CLOB market data) | ✅ **Full parity** | `get_order_book(s)` / `get_midpoint(s)` / `get_price(s)` / `get_spread(s)` / `get_last_trade_price(s)` / `get_price_history` / `estimate_market_price` / `get_market` / `list_markets` — sync + async, identical call shapes & model fields (subset/sentinel gaps documented; see [Fidelity gaps](#fidelity-gaps-within-the-implemented-surface) for the `Market` subset + the ignored `list_markets` filters). |
| **Secure reads** (CLOB market data) | ✅ **Full parity** | The secure client shares the public reads, identical signatures. |
| **Account / auth** | 🟡 **Paper seam** | `fetch_api_keys` / `delete_api_key` / `get_balance_allowance` / `is_gasless_ready` / `get_closed_only_mode` / `get_notifications` / `get_order` / `list_open_orders` / `list_account_trades` implemented over paper auth (single `ps_live_*` key). Note: `is_gasless_ready` / `get_closed_only_mode` / `get_notifications` are **constant honest stubs** (return `True` / `False` / `()` with **no network call**), distinct from `get_balance_allowance` / `get_order` / `list_open_orders` / `list_account_trades`, which are genuinely backed by the paper API. py-sdk's wallet-auth `end_authentication` and the `drop_notifications` write are **deferred**. |
| **Trading** (orders) | 🟡 **Paper seam** | Full write surface (`create_*` / `place_*` / `post_order(s)` / `cancel_*`) with py-sdk's flat kwargs + `SignedOrder` / `OrderResponse` / `CancelOrdersResponse`. **Signing inert, settled in paper engine, not on-chain.** |
| **On-chain** (ERC-20/1155, split/merge/redeem, approvals) | 🟡 **Paper seam (no-op)** | All methods implemented with exact signatures; each is an **instant no-op** returning a placeholder tx handle — no chain write, no balance/ledger mutation, no balance-check. |
| **Rewards / scoring** | ⚪ **Stub (honest empties)** | `get_order(s)_scoring` / `list_*` rewards / `get_total_earnings_*` / `get_reward_percentages` implemented but return honest empties — no paper rewards engine yet. |
| **Builder attribution** | ⚪ **Stub (NotImplementedError)** | `get_builder_volumes` / `list_builder_trades` / `get_builder_fee_rates` / `list_builder_leaderboard` mirror signatures, raise `NotImplementedError` — no builder economy on paper. |
| **RFQ** | ⚪ **Stub (types + NotImplementedError)** | RFQ **types** re-export for type-hint parity; py-sdk has no sync `SecureClient` RFQ method (mirror invents none); the async `open_rfq_session` and any RFQ action raise `NotImplementedError`. |
| **Market stream** | 🟡 **Paper seam** | `subscribe(MarketSpec)` → `price_change` / `last_trade_price` over `/v1/ws/prices`. **TOB, not L2** (no `book` event); `condition_id:LABEL` token keying; lifecycle events defined-but-never-emitted. |
| **User stream** | 🟡 **Paper seam** | `subscribe(UserSpec)` (secure only) → order/trade fills over the authenticated executions channel; `UserSpec.markets` is a client-side filter; lean frames with honest derivations. |
| **Crypto stream** | 🟡 **Paper seam** | `subscribe(CryptoPricesSpec)` → Binance / Chainlink ticks, routed by exact source label, over the spot SSE feed. |
| **Sports / comments / equity streams** | 🔴 **Deferred** | `SportsSpec` / `CommentsSpec` / `EquityPricesSpec` (+ the `RtdsSpec` base alias) and their events are **not shipped**; pass a single CORE spec per `subscribe()`. |
| **Gamma / Data reads** (events, series, tags, comments, sports, portfolio, positions, leaderboards, combo, search, accounting export) | 🔴 **Deferred** | Not implemented on `PublicClient` / `SecureClient` (calling one raises `AttributeError`). Real py-sdk is a superset, so the swap to real Polymarket only *adds* them. |

Legend — **✅ Full parity**: same behaviour & shape as py-sdk (documented
narrowness aside). **🟡 Paper seam**: implemented with paper-mode semantics
(see the seam contract). **⚪ Stub**: importable & callable, returns an honest
empty or raises `NotImplementedError` — no fabricated data. **🔴 Deferred**: not
implemented yet; absent from the surface.

### Implemented surface — method reference

The **CLOB market-data READ** core (the foundation, shared by every client and
backed by the same proven read path as the v1 mirror):

| Method | Returns |
|---|---|
| `get_order_book(*, token_id)` / `get_order_books(*, token_ids)` | `OrderBook` / `tuple[OrderBook, …]` |
| `get_midpoint(*, token_id)` / `get_midpoints(*, token_ids)` | `Decimal` / `dict[str, Decimal]` |
| `get_price(*, token_id, side)` / `get_prices(*, requests)` | `Decimal` / `dict[str, dict[side, Decimal]]` |
| `get_spread(*, token_id)` / `get_spreads(*, token_ids)` | `Decimal` / `dict[str, Decimal]` |
| `get_last_trade_price(*, token_id)` / `get_last_trade_prices(*, token_ids)` | `LastTradePrice` / `tuple[LastTradePriceForToken, …]` |
| `get_price_history(*, token_id, …)` | `tuple[PriceHistoryPoint, …]` (a bare tuple, like py-sdk) |
| `estimate_market_price(*, token_id, side, …)` | `Decimal` (marginal/limit price — the worst book level touched to fill, like py-sdk; `FOK` underfill raises `InsufficientLiquidityError`, `FAK` falls back to the deepest level) |
| `get_market(*, id=…/slug=…)` | `Market` (with a nested `.state`) |
| `list_markets(*, …)` | `Paginator[Market]` — drive with `.first_page()` / `.iter_items()` |

The **authenticated `SecureClient`** adds, on top of those reads (see the
[compat matrix](#compat-matrix) for support level and the
[seam contract](#the-simreal-seam-contract-what-paper-mode-does-and-doesnt-do)
for paper semantics):

| Group | Methods |
|---|---|
| Account / auth | `fetch_api_keys` / `delete_api_key` / `get_balance_allowance` / `is_gasless_ready` / `get_closed_only_mode` / `get_notifications` / `get_order` / `list_open_orders` / `list_account_trades` |
| Trading | `create_limit_order` / `create_market_order` / `place_limit_order` / `place_market_order` / `post_order` / `post_orders` / `cancel_order` / `cancel_orders` / `cancel_all` / `cancel_market_orders` |
| On-chain (paper no-ops) | `approve_erc20` / `approve_erc1155_for_all` / `transfer_erc20` / `split_position` / `merge_positions` / `redeem_positions` / `setup_trading_approvals` / `setup_gasless_wallet` |
| Rewards / scoring (honest stubs) | `get_order_scoring` / `get_orders_scoring` / `list_current_rewards` / `list_market_rewards` / `list_user_earnings_for_day` / `get_total_earnings_for_user_for_day` / `list_user_earnings_and_markets_config` / `get_reward_percentages` |
| Builder (NotImplementedError) | `get_builder_volumes` / `list_builder_trades` / `get_builder_fee_rates` / `list_builder_leaderboard` |

Develop and paper-test a bot here against PolySimulator (no wallet, no key, no
gas), then run the **same code** on real Polymarket by swapping the prefix back to
`polymarket`, pointing at the real host, and supplying real credentials.

**Async twins.** Both clients have async counterparts — `AsyncPublicClient` (the
async twin of `PublicClient`) and `AsyncSecureClient` (the async twin of
`SecureClient`) — exposing the identical surface with every per-request method an
`async def` coroutine (the `list_*` reads stay synchronous, returning an
`AsyncPaginator` you then `await`). Construct with `AsyncSecureClient(api_key=…)`
or `await AsyncSecureClient.create(private_key=…)`, drive with `await`, and use
`async with AsyncSecureClient(…) as client:` for lifecycle — exactly as py-sdk's
async client does. The async twins share the same transport-free logic as the sync
clients (no second copy), so their behaviour is identical by construction.

### Seam details — implementation reference

The [seam contract](#the-simreal-seam-contract-what-paper-mode-does-and-doesnt-do)
above is the high-level "what diverges"; this section is the precise
implementation reference for each seam — the exact input guards, return values,
and edge behaviours. (A genuinely *deferred* method — the Gamma/Data reads — is
absent and raises `AttributeError`, which is honest: the surface simply isn't
there yet, rather than a stub that pretends and fails later. The stubs below
instead exist, are callable, and return honest empties / raise
`NotImplementedError`.)

- **EIP-712 signing & on-chain machinery** — the real-Polymarket signer /
  builder-auth path. Paper trading needs no signer, so signing is
  **accepted-and-inert**: the authenticated `SecureClient`'s **trading write
  surface IS implemented** (`create_limit_order` / `create_market_order` /
  `place_limit_order` / `place_market_order` / `post_order` / `post_orders` /
  `cancel_order` / `cancel_orders` / `cancel_all` / `cancel_market_orders`, with
  py-sdk's flat keyword args and `SignedOrder` / `OrderResponse` /
  `CancelOrdersResponse` return models) — it just produces an inert-signed order
  (empty signature) and submits the unsigned paper body.
- **On-chain methods are PAPER no-ops.** `approve_erc20` /
  `approve_erc1155_for_all` / `transfer_erc20` / `split_position` /
  `merge_positions` / `redeem_positions` / `setup_trading_approvals` /
  `setup_gasless_wallet` are **implemented** with py-sdk's exact signatures, but
  paper mode accepts the call (validating inputs) and **does NOT settle on-chain
  or mutate any paper state** (no ledger write, no balance change): each returns a
  paper transaction handle whose `.wait()` returns **instantly** with a
  valid-format placeholder `TransactionOutcome` (`0x` + 64 hex hash,
  `transaction_id=None`). In particular — unlike real py-sdk, which consults
  on-chain ERC-1155 balances — `redeem_positions` / `merge_positions` do NOT
  balance-check the position on paper, so they always succeed (the paper position
  ledger isn't wired into the on-chain methods in this gate). py-sdk's pre-chain
  input guards are replicated so a bot hits the SAME `UserInputError` in paper as
  in prod (exactly-one of
  `condition_id`/`legs`; exactly-one of `condition_id`/`market_id`/`position_id`;
  positive amount on a combo split). **Address checks are a 40-hex FORMAT check,
  NOT an EIP-55 checksum.** The accepted shape matches py-sdk's
  `to_checksum_address` acceptance set — a 40-hex address with an **optional**
  `0x`/`0X` prefix, case-insensitive — so the guard accepts every input py-sdk
  accepts and rejects the same malformed ones (wrong length, non-hex). Paper
  trading never touches a chain, so to keep the dependency surface thin the mirror
  does not pull in `eth-utils`/`web3`/`eth_account`; that means — unlike py-sdk's
  `to_checksum_address`, which normalizes any accepted address to its
  EIP-55-checksummed form for on-chain use — the mirror does NOT compute the
  checksum normalization and returns the address unchanged (moot on paper, where
  the address is never used on a chain).
- **Rewards / scoring reads are honest empty stubs.** `get_order_scoring` →
  `False`, `get_orders_scoring` → all-`False` dict, the `list_*` rewards reads →
  empty paginators, `get_total_earnings_for_user_for_day` → `()`,
  `get_reward_percentages` → `{}`. The paper **rewards engine** is a separate
  backend roadmap item, so these return honest empties with no fabricated data —
  a bot's reward-accounting loop runs and truthfully finds nothing (rewards
  aren't earned on paper).
- **Builder attribution raises `NotImplementedError`.** `get_builder_volumes` /
  `list_builder_trades` / `get_builder_fee_rates` / `list_builder_leaderboard`
  mirror py-sdk's signatures but raise `NotImplementedError("Builder attribution
  is not simulated in PolySimulator paper mode.")` — there is no builder fee, no
  builder revenue ledger, no builder leaderboard on paper. The `builder_code=`
  kwarg on the order builders stays inert (no builder fee).
- **Collateral / balances / positions** — the account balance/order reads ARE
  implemented (`get_balance_allowance` / `get_order` / `list_open_orders` /
  `list_account_trades`); the wider portfolio/positions reads are deferred (see
  the Gamma/Data row of the [compat matrix](#compat-matrix)).
- **Realtime streams (CORE topics ARE implemented).** The async clients'
  `subscribe()` covers py-sdk's **CORE** stream topics — `market`
  (`MarketSpec` → book / price_change / last_trade), `crypto_prices`
  (`CryptoPricesSpec` → Binance / Chainlink ticks), and the authenticated `user`
  feed (`UserSpec` → order / trade fills, secure client only) — backed by our
  `polysim_sdk` WS + SSE transport. See the **Realtime streams (v2 mirror)**
  section below for the topic→transport map, the deferred topics, and the seams.
  Streaming is **async-only** (the sync `PublicClient` / `SecureClient` have no
  `subscribe`, mirroring py-sdk). The **sports / comments / equity** topics are
  DEFERRED (their specs/events aren't shipped).
- **RFQ** — the RFQ session flow is not simulated. The **RFQ types**
  (`RfqQuoteRequestEvent`, `RfqSession`, the enums, the rejection errors, …) DO
  re-export from the package root so a maker bot's type hints survive the prefix
  swap; py-sdk has no synchronous `SecureClient` RFQ method, so the mirror
  invents none, and any RFQ *action* raises `NotImplementedError("RFQ quoting is
  not simulated in PolySimulator paper mode.")`.
- **`Paginator` dataframe exports** — `.to_pandas` / `.to_polars` / `.to_arrow`
  (they need pandas/polars/pyarrow) are not shipped; the paginator's
  `.first_page()` / `.iter_items()` / iteration surface is here.

Because the real py-sdk is a **superset**, the swap to real Polymarket only ever
*adds* these — it never breaks a bot that stuck to the implemented surface.

### Fidelity gaps within the implemented surface

A handful of implemented methods are present but **narrower** than py-sdk's. None
break the prefix swap (the field names and call shapes match), but a bot that
relies on the listed behaviour will see *more* on real Polymarket than the
mirror provides:

- **`Market` is a focused subset.** The mirror's `Market` carries only the
  identity fields (`id`, `condition_id`, `question`, `slug`) plus a focused
  nested `.state` (`active` / `closed` / `neg_risk`). py-sdk's `Market` is a
  deeply-nested gamma model — the **`outcomes`, per-outcome `prices`, and the
  `metrics` / `volume` / `liquidity` blocks are not present here**, and
  `market.state` omits py-sdk's wider fields (`archived`, `accepting_orders`,
  `enable_order_book`, `start_date` / `end_date` / `closed_time`). The mirror
  also **does not hex-validate `condition_id`** and **does not reject
  non-binary markets** the way py-sdk's `Market` model does — a payload py-sdk
  would raise `UnexpectedResponseError` on may bind here. Read only the focused
  identity+state subset for a clean swap; the rest lands when `Market` widens.
- **`list_markets` accepts py-sdk's full gamma keyword set but only forwards a
  few.** Only the filters PolySimulator's `GET /v1/markets` honours (`closed`,
  `order`→`sort`, `ascending`) reach the server; **every other gamma keyword
  (`liquidity_num_min/max`, `volume_num_min/max`, `tag_id`, `clob_token_ids`,
  `question_ids`, `start_date_*` / `end_date_*`, …) is accepted for signature
  parity and silently ignored**. The call won't error, but the result set is
  *not* filtered by an ignored knob. (On real Polymarket every one of these
  filters applies server-side.)
- **`get_last_trade_price` always reports side `BUY`.** PolySim's book snapshot
  carries `last_trade_price` but **no trade side**, so the mirror fills py-sdk's
  required `side` field with a constant `"BUY"`. Don't branch on the last-trade
  side against the mirror — it isn't real provenance; on Polymarket it reflects
  the actual maker/taker side.
- **`Decimal` string-form (trailing zeros) may differ.** The mirror computes
  prices from PolySim's `/v1/book` and quantises to the 4-decimal grid, so a
  midpoint/price/spread can render as `Decimal("0.5000")` where py-sdk's
  server-computed value renders `Decimal("0.5")`. The **numeric value is equal**
  (`==` and arithmetic match — the behavioural-parity suite asserts this), but
  `str(value)` and `value.as_tuple()` can differ in trailing zeros. Compare by
  numeric value, never by string form, for a clean swap.
- **`0` is the no-quote sentinel.** When a token's book has no two-sided quote,
  `get_midpoint` / `get_price` / `get_spread` return `Decimal("0")` (py-sdk's
  "no value available" sentinel) rather than `None` or an error. A genuine `0`
  and a missing quote are indistinguishable — treat a `0` result as "no quote",
  not as a real price of zero.

### Realtime streams (v2 mirror)

The async clients mirror py-sdk's `subscribe()` for the **CORE** stream topics,
backed by our `polysim_sdk` WS + SSE transport. A consumer iterates a
`SubscriptionHandle` and closes it (or uses it as an async context manager):

```python
from polysim_polymarket import AsyncSecureClient
from polysim_polymarket.streams import MarketSpec, CryptoPricesSpec, UserSpec

async with await AsyncSecureClient.create(api_key="ps_live_…") as client:
    async with await client.subscribe(MarketSpec(token_ids=["…"])) as h:
        async for ev in h:          # MarketPriceChangeEvent / MarketLastTradePriceEvent
            print(ev.payload.market, ev.type)
```

The stream surface lives in `polysim_polymarket.streams` (the specs, the event
types, `SubscriptionHandle`) — **exactly as py-sdk keeps it in
`polymarket.streams`. Neither package promotes any stream name to its package
root**, so nothing stream-related is re-exported from `polysim_polymarket` (the
re-export subset is empty by design).

**CORE topics → transport.** Each topic maps to one `polysim_sdk` stream:

| Spec | Topic | Events emitted | Transport |
|---|---|---|---|
| `MarketSpec` | `market` | `MarketPriceChangeEvent`, `MarketLastTradePriceEvent` | `ws.aprices_stream` (`/v1/ws/prices`) |
| `CryptoPricesSpec` | `prices.crypto.{binance,chainlink}` | `CryptoPricesBinanceEvent` / `CryptoPricesChainlinkEvent` | `sse.aspot_stream` (`/prices/stream`) |
| `UserSpec` *(secure only)* | `user` | `UserTradeEvent`, `UserOrderEvent` | `ws.aexecutions_stream` (`/v1/ws/executions`) |

`subscribe()` is **async-only** (the sync clients have no `subscribe`, mirroring
py-sdk). The handle is a bounded queue with **drop-oldest backpressure**: under a
slow consumer it drops the oldest events and counts the losses in
`handle.dropped`. Pass `queue_size=` (a mirror-only keyword) to size it.

**Seams — documented, not fabricated.** Because PolySimulator's push surface is
leaner than Polymarket's, the mirror is explicit where a py-sdk field has no
real provenance:

- **Auth handshake (user stream).** The `user` feed mints a short-lived WS JWT
  from the secure client's API key (`ws_token()`) and connects to the
  authenticated `/v1/ws/executions` channel — so it's scoped to that one account.
  There is no per-subscription credential; `UserSpec.markets`, when set, is
  applied as a **client-side filter** in the adapter (the channel delivers all of
  the account's fills).
- **`MarketSpec.token_ids` are the SDK's `condition_id:LABEL` tokens** (the
  same form `get_*` and trading use) — **not** raw Polymarket numeric token ids.
  The prices WS subscribes by `condition_id`, so the opener strips each token's
  `:LABEL` suffix (a **generic last-colon split**, so non-binary `UP`/`DOWN`
  labels strip correctly — not the binary-only `_split_token`), de-duplicates,
  and subscribes those condition ids. Each delivered frame carries per-outcome
  entries with a `label` (`Yes`/`No`/`Up`/`Down`) and the Polymarket
  **CLOB-numeric** `token_id`; that raw digit is a *different namespace* from the
  SDK token, so the adapter does **not** filter on it (it would match nothing).
  Instead the adapter **derives** each outcome's SDK token
  `f"{condition_id}:{LABEL}"` from the frame's `market_id` + outcome `label` and
  both filters on and emits that — so a `MarketSpec` built from the SDK's own
  `condition_id:LABEL` tokens (e.g. `["0xCID:YES", "0xCID:NO"]`) subscribes
  `["0xCID"]` AND receives correctly-filtered events for those outcomes, end to
  end. The emitted event's `token_id` is the `condition_id:LABEL` form a bot
  uses for reads/trading, never the backend CLOB digit. (A raw pm numeric token
  id is not a valid `MarketSpec.token_id` here.)
- **No `book` event from the market stream.** `/v1/ws/prices` is a top-of-book
  cache, not an L2 ladder feed — so the market stream emits `price_change` and
  `last_trade_price` but never a full `book` event. (Call `get_order_book` for
  the ladder.)
- **Stream id fields are plain `str`.** Token / condition / order id fields on
  the stream-event models (`token_id`, `market`, `id`, `taker_order_id`, …) are
  plain `str`, consistent with the mirror-wide id-typing decision — they are
  **not** py-sdk's hex-validated `TokenId` / `CtfConditionId` newtypes, so no
  hex-shape validation is applied to ids arriving on the wire.
- **Custom-feature / lifecycle market events are defined for parity but never
  emitted.** `MarketBestBidAskEvent`, `NewMarketEvent`, `MarketResolvedEvent`,
  and `MarketTickSizeChangeEvent` are part of the `MarketEvent` union (so a ported
  bot's imports and `match`-on-`type` resolve) but the paper stream produces none
  of them — `MarketSpec(custom_feature_enabled=True)` is accepted for signature
  parity and is otherwise inert.
- **Lean user frames, honest derivation.** The backend fill frame carries
  `market_id` + `outcome` but **no CLOB token/asset id**, so the event's
  `token_id` is *derived* — `f"{market_id}:{OUTCOME}"`, the SDK's canonical token
  convention (the inverse of `_split_token`, the same form `get_*`/trading use),
  not faked from the order id. `id`/`taker_order_id` keep the fill's real
  `order_id` (those are order ids). Remaining py-sdk fields the frame lacks get
  honest defaults: the order's `original_size` == `size_matched` == the filled
  `quantity` with `order_event_type="UPDATE"`, `status="MATCHED"`. A fill missing
  its `outcome` yields no event (no honest token could be built).
- **`last_trade_price` side + size are read from the frame top level.** The
  prices producer stamps the most-recent trade's `last_trade_side` /
  `last_trade_size` at the **frame top level** (not on the per-outcome entry,
  which carries only the trade *price*). The adapter reads them there and emits
  the real side — a SELL trade yields `side="SELL"`, never a fabricated `BUY`. If
  a frame ever lacks the top-level side, the `last_trade_price` event is dropped
  rather than mislabeled.
- **Crypto topic routing is by exact source label.** The Binance feed's wire
  `source` is `polymarket_rtds` (with `relay_binance` as a fallback-relay label);
  Chainlink's is `chainlink_rtds`. The adapter routes by exact membership in
  those label sets — `prices.crypto.binance` receives the live Binance ticks and
  Binance ticks never leak into a `prices.crypto.chainlink` subscription.

**DEFERRED topics.** py-sdk's `polymarket.streams` also ships `sports`
(`SportsSpec` / `SportsEvent`), `comments` (`CommentsSpec` / `CommentsEvent` /
reactions), and `prices.equity.pyth` (`EquityPricesSpec` / `EquityPricesEvent`),
plus the `RtdsSpec` / `StreamEvent` aliases and the merged multi-spec
`subscribe(Sequence[...])`. **None of those are shipped here** — pass a single
CORE spec per `subscribe()` call. Because real py-sdk is a superset, the swap to
real Polymarket only *adds* them.

---

## Migrating from `py-clob-client`

> The import below is the **v1** path (`py_clob_client`). `py-clob-client-v2`
> (`py_clob_client_v2`) is a different package and is out of scope — see
> [Polymarket compatibility](#polymarket-compatibility) above.

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
side explicitly. **Up/Down markets** carry `Up`/`Down` outcomes, so the same
colon form also accepts `":UP"` / `":DOWN"` (case-insensitive); the backend
matches the order outcome case-insensitively, so either case is accepted.

```python
client.create_order(OrderArgs(token_id="0xMARKET",     ...))  # → market 0xMARKET, YES
client.create_order(OrderArgs(token_id="0xMARKET:NO",  ...))  # → market 0xMARKET, NO
client.create_order(OrderArgs(token_id="0xUPDOWN:UP",  ...))  # → Up/Down market, UP
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
"just work" with full parity. The `:YES`/`:NO` suffix (and `:UP`/`:DOWN` for
Up/Down markets) exists for callers who prefer to address PolySimulator markets
by condition id directly.

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

### New-market warm-up `404` (retry-on-404)

The SDK auto-retries `429`/`5xx` but **never** a `404` — a 404 is normally
permanent. The one benign exception is a **freshly-created market** (most often
a 5m Up/Down window): for a short window after creation — on the order of ~30s,
the Up/Down roll cadence, though not a guarantee — it is not yet in the
order-validation catalog and order placement transiently fails with
`ApiError(status_code=404, code="MARKET_NOT_FOUND")`. Retry **only** that signal,
with backoff; re-raise any other error (including a 404 with a different `code`,
which is a real "not found").

Use the shipped helper (recommended) …

```python
from polysim_sdk import PolySimClient, retry_on_market_warmup

with PolySimClient() as client:
    fill = retry_on_market_warmup(
        lambda: client.place_order(
            market_id=cid, side="BUY", outcome="Up", amount="10", price="0.99",
        ),
        attempts=6, base_delay=2.0,
    )
```

… or roll your own on the native `ApiError`:

```python
import time
from polysim_sdk import ApiError

for i in range(6):
    try:
        fill = client.place_order(market_id=cid, side="BUY", outcome="Up",
                                  amount="10", price="0.99")
        break
    except ApiError as exc:
        if exc.status_code == 404 and exc.code == "MARKET_NOT_FOUND" and i < 5:
            time.sleep(min(2.0 * 2**i, 30.0))   # capped backoff, then retry
            continue
        raise   # any other error — and the final 404 — propagates
```

With the **`polysim_clob_client` drop-in**, the same condition surfaces as a
`PolyApiException` with `status_code == 404`; catch it and retry the same way (or
pass your drop-in call to `retry_on_market_warmup(lambda: clob.create_and_post_order(...))`,
which works with any callable):

```python
from polysim_clob_client.exceptions import PolyApiException

try:
    resp = clob.create_and_post_order(order_args)
except PolyApiException as exc:
    if exc.status_code == 404 and getattr(exc, "code", None) == "MARKET_NOT_FOUND":
        ...  # warm-up — retry with backoff
    else:
        raise   # any other error — including a 404 with a different code — propagates
```

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
2. **Maker/taker classification.** A marketable-at-placement GTC (one that
   crosses the book on submit) is classified as a **taker** and charged the taker
   fee, matching Polymarket; only a genuinely-resting GTC that is filled later —
   when the market moves into it — is booked as a $0-fee maker. The realized
   per-fill fee *rate* is surfaced as `ClobTrade.fee_rate_bps` (the realized fee
   *amount* is not a typed field).
3. **Orderbook freshness.** The book cache TTL is 300 s. For markets Polymarket
   updates less often than that, a fill may revert to displayed midpoint; a ±15%
   sanity guard bounds the drift. High-volume markets always fill at real ask/bid.

---

## What's included

| Path | Purpose |
|---|---|
| `polysim_sdk/` | Native client: `PolySimClient` (sync) + `AsyncPolySimClient` (async), `ws`, `sse` (underlying-spot stream), `updown` (Up/Down row helpers), `pagination`, `exceptions`, `constants`. |
| `polysim_clob_client/` | `py-clob-client` drop-in: `ClobClient` + matching `clob_types`, `constants`, `order_builder`, `exceptions`. |
| `polysim_polymarket/` | Unified **py-sdk** (`polymarket`) paper-mode mirror: sync + async `PublicClient` / `SecureClient` (CLOB reads, account/auth, trading), on-chain paper no-ops, rewards/builder/RFQ stubs, and core realtime streams — returning typed pydantic models. See [Polymarket py-sdk (v2) mirror](#polymarket-py-sdk-v2-mirror). |
| `scripts/01_balance_and_market.py` | Smallest end-to-end native demo. |
| `scripts/02_clob_dropin_demo.py` | The `py-clob-client` drop-in surface end-to-end. |
| `scripts/03_async_concurrent.py` | Concurrent reads via the async client. |
| `scripts/04_btc_updown.py` | Discover the live BTC Up/Down window: spot, strike, prices, tokens. |
| `tests/` | respx-mocked unit tests for both surfaces (no creds, no network). |

Run the test suite with `pytest`. Lint/type with `ruff check . && mypy polysim_sdk polysim_clob_client polysim_polymarket`.

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
