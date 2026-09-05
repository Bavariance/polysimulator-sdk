# Catching `polysim_polymarket` up to `polymarket-client` 0.9.0

**Measured 2026-09-06.** Numbers below come from downloading both wheels from PyPI
and diffing the parsed AST of every public module — not from reading changelogs.

```
polymarket-client 0.1.0b13   <- what our mirror pins
polymarket-client 0.9.0      <- current, uploaded 2026-09-04
12 releases in between: 0.1.0 0.2.0 0.3.0 0.3.0b1 0.3.0b2 0.4.0 0.5.0 0.6.0
                        0.7.0 0.7.1 0.8.0 0.9.0
```

## The delta, public surface only

Internal modules (`polymarket._internal.*`) are excluded — a mirror does not owe
them parity.

| | 0.1.0b13 | 0.9.0 |
|---|---:|---:|
| public classes | 208 | 346 |
| **new** | | **140** |
| **removed** | | **2** |
| classes with changed methods | | **30** |

## What the 140 new classes actually are

They are not 140 independent things. They cluster, and the clusters have very
different value to us:

**1. Perpetuals — a new product line, and out of scope.**
`fetch_perps_book`, `fetch_perps_fees`, `fetch_perps_instruments`,
`fetch_perps_ticker(s)`, `list_perps_candles`, `list_perps_funding_history`,
`list_perps_trades`, `deposit_to_perps`. Polymarket added perps. We simulate
prediction markets against an order-book replay; we have no perps data, no
funding model, and no way to score a perps fill honestly. Mirroring these would
produce methods that return plausible nonsense. **Do not mirror.** Raise a clear
`NotImplementedError` naming the reason.

**2. Notifications — ~12 model classes.** `AutoRedeemedNotification`,
`ComboAutoRedeemedNotification`, `MarketResolvedNotification`,
`MarketRegisteredNotification`, `ChildCommentCreatedNotification`,
`NotificationType`, and their payload types. These are data shapes, cheap to
mirror, and they replace the single removed `models.clob.account:Notification`.

**3. Combo markets.** `list_combo_activity`, `accept_combo_quote`,
`MarketPositionContext.outcome_ids`. Real product surface. Worth mirroring, but
only after deciding whether our engine can price a combo at all.

**4. Builder API keys and session keys.** `create_builder_api_key`,
`fetch_builder_api_keys`, `BuilderApiKeyInfo`, `authorize_session_key`,
`fetch_session_keys`. Auth surface. Mirror the shapes; the semantics are ours.

**5. Two new error types.** `AutoCancelDailyLimitError`, `ConnectionLostError`.

## The highest-leverage single change

**`token_id` was added to the core trading models.** It appears on `ClobTrade`,
`MakerOrder`, `OpenOrder`, `BuilderTrade`, `LastTradePriceForToken`,
`MarketBestBidAskPayload`, and on the order-preparation params
(`PrepareLimitOrderParams`, `PrepareMarketOrderParams`, `OrderDraft`).

This is one field across the objects every bot touches on every order. A bot
written against 0.9.0 and pointed at a mirror missing it gets objects lacking a
field it reads — a silent `AttributeError` in user code, on the happy path.

**ALREADY DONE — do not spend the morning on it.** I flagged this as the first
task, then checked our mirror instead of assuming, and all six classes carry it:

```python
# polysim_polymarket/models.py — ClobTrade
token_id: str = Field(default="", validation_alias=AliasChoices("token_id", "asset_id"))
```

The alias accepts both `token_id` and the older `asset_id`, so the mirror is
ahead of its own pin here. Verified on `ClobTrade`, `MakerOrder`, `OpenOrder`,
`BuilderTrade`, `LastTradePriceForToken` and `MarketBestBidAskPayload`.

Recording it as done rather than deleting the section: the next person to read
the upstream diff will reach the same conclusion I did and should be told it was
already checked.

## Two removed public symbols

    polymarket.environments:WalletDerivation
    polymarket.models.clob.account:Notification

If our mirror exposes either, it is exposing dead API. `Notification` is
superseded by the notifications cluster above.

## Honest scope

Our mirror is 25 files and 8,499 lines. Full 0.9.0 parity is **days, not hours**,
and most of the gap is surface we should deliberately decline (perps).

Suggested order, highest value first:

1. ~~`token_id` across the shared models~~ — **already present**, verified
2. Drop the two removed symbols (`WalletDerivation`, `Notification`) — minutes
3. Re-pin to a chosen release and state the pin in the README badge — minutes
4. Notifications cluster — mechanical
5. Combo + builder/session keys — real design decisions, do them awake
6. Perps — explicitly refuse, in code, with a reason a caller can read

**Releasing is not automatable here.** `RELEASING.md` states no CI job or agent
uploads to PyPI: it is a human running `twine upload` with a Bitwarden token. So
none of the above reaches `pip install polysimulator` without a person.
