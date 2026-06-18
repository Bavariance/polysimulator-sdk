"""Pure frame -> event adapters (transport-free).

These functions are the single home of the wire->event mapping. They take the
raw dict frames the ``polysim_sdk`` transports yield plus the consumer's spec,
and return parsed py-sdk CORE events filtered by the spec. No I/O, no asyncio —
so the mapping logic is unit-testable without any transport.

Backend frame shapes (verified against the backend producers):

* market: ``polysim_sdk.ws.aprices_stream`` (``/v1/ws/prices``) yields one
  ``{"type": "price", "market_id": <condition_id>, "outcomes": [...]}`` frame
  per cache update. Each outcome carries a per-token ``price`` + top-of-book
  (``best_bid`` / ``best_ask``) and, when that outcome just traded, a
  ``last_trade`` PRICE. The most-recent trade's ``last_trade_side`` /
  ``last_trade_size`` are stamped at the FRAME TOP LEVEL (not per-outcome) by
  the backend producer (``_handle_last_trade`` in ``app/polymarket_ws.py``). We
  project each matching outcome into a ``price_change`` event (price + TOB) and,
  when an outcome's ``last_trade`` price is present AND a top-level
  ``last_trade_side`` accompanies it, a ``last_trade_price`` event.

* user: ``polysim_sdk.ws.aexecutions_stream`` (``/v1/ws/executions``) yields a
  fill frame ``{"type": "fill", "order_id", "market_id", "side", "outcome",
  "price", "quantity", "filled_at"}`` per limit-order fill. We project it into
  BOTH a ``trade`` event (the fill itself) and an ``order`` event (the order's
  post-fill state), matching py-sdk's two user-event kinds.

* crypto: ``polysim_sdk.sse.aspot_stream`` (``/prices/stream``) yields parsed
  SSE blocks ``{"event": "crypto_price"|"crypto_price_batch", "data": {...}}``.
  Each crypto update carries ``symbol`` / ``price`` / ``source`` / ``timestamp``.
  ``source`` is the backend producer's KNOWN label string, not a substring: the
  Binance feed is labelled ``polymarket_rtds`` (with ``relay_binance`` as a
  fallback-relay label and the literal ``binance`` accepted for robustness) and
  the Chainlink feed ``chainlink_rtds`` (literal ``chainlink`` accepted too) —
  see ``_handle_rtds_message`` in ``app/polymarket_ws.py`` and the canonical
  source sets in ``app/main.py`` (the ``crypto_source`` suppress map). We route
  by exact membership in those label sets, mapping the Binance set to the
  ``prices.crypto.binance`` topic and the Chainlink set to
  ``prices.crypto.chainlink``. An unknown label matches neither topic and is
  dropped rather than mis-routed.

SEAMS — documented, not fabricated:

* **No ``book`` event from the price feed.** ``/v1/ws/prices`` is a top-of-book
  cache, not an L2 stream — it carries no full bid/ask ladder. So
  ``adapt_prices_frame`` emits ``price_change`` / ``last_trade_price`` but never
  ``book``. (A consumer that needs the full ladder calls ``get_order_book``.)

* **Custom-feature / lifecycle market events are never emitted.** The paper
  stream has no top-of-book ``best_bid_ask`` event channel nor market-lifecycle
  (``new_market`` / ``market_resolved`` / ``tick_size_change``) feed. Those event
  types are defined for type-parity (see ``_market_events``) but no adapter
  produces them — ``custom_feature_enabled`` on ``MarketSpec`` is accepted for
  signature parity and otherwise inert.

* **User ``trade``/``order`` derivation.** The backend fill frame is leaner than
  py-sdk's CLOB user event. The ``asset_id``/``token_id`` is DERIVED honestly
  from the fill's ``market_id`` + ``outcome`` via the SDK's canonical token
  convention (``f"{market_id}:{OUTCOME}"`` — the inverse of ``_split_token``),
  NOT faked from the order id. ``id``/``taker_order_id`` keep the fill's real
  ``order_id`` (those ARE order ids). The remaining py-sdk fields the frame
  lacks get honest defaults: the order's ``original_size`` == ``size_matched``
  == the filled ``quantity`` and its ``order_event_type`` is ``"UPDATE"`` (a
  fill is an update to the order), with ``status="MATCHED"``. The trade
  ``status`` is ``"MATCHED"``. ``owner`` is left empty (the frame carries no
  wallet — the stream is already scoped to the authenticated user).
"""

from __future__ import annotations

from typing import Any

from polysim_polymarket.streams._crypto_events import (
    CryptoPricesBinanceEvent,
    CryptoPricesChainlinkEvent,
    CryptoPricesEvent,
)
from polysim_polymarket.streams._market_events import (
    MarketEvent,
    MarketLastTradePriceEvent,
    MarketPriceChangeEvent,
)
from polysim_polymarket.streams._specs import CryptoPricesSpec, MarketSpec, UserSpec
from polysim_polymarket.streams._user_events import (
    UserEvent,
    UserOrderEvent,
    UserTradeEvent,
)

# Canonical backend ``source`` labels per crypto topic. The live Binance feed
# rides ``polymarket_rtds`` (Polymarket-relayed Binance), ``relay_binance`` is a
# secondary relay timeline, and ``binance`` is accepted for robustness against a
# future bare label. Chainlink rides ``chainlink_rtds`` (``chainlink`` accepted
# likewise). These mirror ``app/main.py``'s ``crypto_source`` suppress map
# (``chainlink``/``chainlink_rtds`` vs ``binance``/``polymarket``/
# ``polymarket_rtds``) and ``UPDOWN_PREFERRED_CRYPTO_SOURCES``. Routing is by
# EXACT membership, never a substring test.
_BINANCE_SOURCES: frozenset[str] = frozenset(
    {"binance", "polymarket_rtds", "relay_binance"}
)
_CHAINLINK_SOURCES: frozenset[str] = frozenset({"chainlink", "chainlink_rtds"})


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _make_token(market_id: str, outcome: str | None) -> str | None:
    """Join ``(market_id, outcome)`` into the SDK's canonical token id.

    The SDK's canonical token is ``f"{market_id}:{OUTCOME}"`` with an
    upper-cased outcome label — the SAME convention the trading + ``get_*``
    paths use (where ``token_id`` is a ``condition_id:OUTCOME`` colon form). For
    the binary YES/NO outcomes the executions feed carries, the result
    round-trips exactly back through :func:`polysim_sdk._shared._split_token` to
    ``(market_id, OUTCOME)``; for non-binary labels (e.g. UP/DOWN) the same join
    convention holds, but ``_split_token`` only special-cases YES/NO, so the
    generic strip in :func:`_split_condition_id` (used by the subscribe side AND
    here for filtering) is what keeps a non-binary token addressable. Returns
    ``None`` when either component is missing (no token can be honestly built) —
    falling back to a bare ``market_id`` would silently re-address to the YES
    outcome.
    """
    if not market_id or not outcome:
        return None
    return f"{market_id}:{outcome.upper()}"


def _split_condition_id(token_id: str) -> str:
    """Strip an SDK token down to its ``condition_id`` (generic last-colon split).

    ``MarketSpec.token_ids`` are the SDK's ``condition_id:LABEL`` tokens. The
    backend prices WS subscribes by ``condition_id`` only, so both the
    subscribe side (:mod:`._stream_open`) and the frame filter here must reduce a
    token to its bare ``condition_id``.

    Unlike :func:`polysim_sdk._shared._split_token` — which only round-trips the
    binary ``YES``/``NO`` labels and treats any other colon form (``0xCID:UP``)
    as a bare market id with outcome ``YES`` — this splits on the LAST colon
    generically, so non-binary labels (``UP``/``DOWN``/…) yield the right
    ``condition_id``. A token with no colon is already a bare ``condition_id``.
    Kept in the SDK-package code (NOT in ``polysim_sdk``) so the v1 token seam is
    untouched.
    """
    tid = str(token_id)
    if ":" in tid:
        condition_id, _, _label = tid.rpartition(":")
        if condition_id:
            return condition_id
    return tid


def _coerce_epoch_ms_str(value: Any) -> str | None:
    """Render an epoch-ms timestamp as the digit-string the strict market
    ``EpochMsTimestamp`` validator wants. Non-integer / non-digit inputs become
    None so a bad timestamp never sinks an otherwise-valid event."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.isdecimal():
        return value
    return None


# ── market ─────────────────────────────────────────────────────────────────


def adapt_prices_frame(frame: dict[str, Any], spec: MarketSpec) -> list[MarketEvent]:
    """Map one ``/v1/ws/prices`` frame into market events for ``spec``.

    Returns a (possibly empty) list of ``MarketPriceChangeEvent`` /
    ``MarketLastTradePriceEvent`` for every outcome whose DERIVED SDK token is in
    the spec's ``token_ids``. Non-``price`` frames (subscribe acks, raw frames)
    yield nothing.

    Token seam (the load-bearing detail): the frame's per-outcome
    ``outcomes[].token_id`` is the Polymarket CLOB NUMERIC token id (a long digit
    string from Gamma ``clobTokenIds``, stamped by ``app/polymarket_ws.py``'s
    ``{"label": …, "token_id": tok}`` builder). That is NOT the SDK's
    ``condition_id:OUTCOME`` token form, so filtering the frame against it would
    never match a spec built from the SDK's documented tokens. Instead we DERIVE
    the SDK token for each outcome from the frame's top-level ``market_id`` (==
    the condition id) + the outcome's ``label`` via :func:`_make_token` — the
    SAME ``condition_id:LABEL`` form ``get_*``/trading expose and the spec
    carries. We filter on (and emit) that derived token, never the raw CLOB
    digit, so a ``MarketSpec`` built from SDK tokens both subscribes by condition
    id (see ``_stream_open._condition_ids_for``) AND receives correctly-filtered
    events end to end.
    """
    if frame.get("type") != "price":
        return []
    wanted = set(spec.token_ids)
    market = _as_str(frame.get("market_id")) or ""
    # The market payload ``timestamp`` mirrors py-sdk's strict ``EpochMsTimestamp``
    # (string-only). ``emit_ts_ms`` is an int epoch-ms on the wire, so stringify
    # it; a non-digit value drops to None rather than failing the whole event.
    timestamp = _coerce_epoch_ms_str(frame.get("emit_ts_ms"))
    # ``last_trade_side`` / ``last_trade_size`` are stamped at the FRAME TOP
    # LEVEL by the backend producer (``_handle_last_trade`` in
    # ``app/polymarket_ws.py`` sets ``payload["last_trade_side"]`` /
    # ``payload["last_trade_size"]`` on the cache blob, NOT on the per-outcome
    # ``outcomes[]`` entry — that entry only carries ``price`` / ``last_trade``
    # / ``last_trade_at``). The blob is broadcast verbatim by
    # ``ConnectionManager.broadcast_price``, so the side/size for the most
    # recent trade live here, not inside the outcome dict.
    last_trade_side = _as_str(frame.get("last_trade_side"))
    last_trade_size = _as_str(frame.get("last_trade_size"))
    out: list[MarketEvent] = []
    for outcome in frame.get("outcomes") or []:
        if not isinstance(outcome, dict):
            continue
        # DERIVE the SDK token from market_id + the outcome's LABEL — NOT the
        # raw CLOB-numeric ``outcomes[].token_id`` (the two namespaces never
        # intersect). This is the token form the spec, reads, and trading all
        # use, so it both filters correctly and is what a bot expects on the
        # emitted event.
        label = _as_str(outcome.get("label"))
        token_id = _make_token(market, label)
        if token_id is None or token_id not in wanted:
            continue
        price = _as_str(outcome.get("price"))
        if price is not None:
            change: dict[str, Any] = {
                "asset_id": token_id,
                "price": price,
                "size": "0",
                "side": "BUY",
            }
            best_bid = _as_str(outcome.get("best_bid"))
            best_ask = _as_str(outcome.get("best_ask"))
            if best_bid:
                change["best_bid"] = best_bid
            if best_ask:
                change["best_ask"] = best_ask
            out.append(
                MarketPriceChangeEvent.model_validate(
                    {
                        "type": "price_change",
                        "payload": {
                            "market": market,
                            "price_changes": [change],
                            "timestamp": timestamp,
                        },
                    }
                )
            )
        # The per-outcome ``last_trade`` carries the trade PRICE; its side and
        # size come from the frame top level (read above). We only emit a
        # ``last_trade_price`` event when the side is actually present — the
        # py-sdk payload's ``side`` is required, and fabricating ``BUY`` would
        # mislabel a real SELL. Per ground truth the producer always stamps the
        # side alongside the price, so this is a defensive seam, not a routine
        # drop (documented in the README streams section).
        last_trade = _as_str(outcome.get("last_trade"))
        if last_trade and last_trade_side is not None:
            out.append(
                MarketLastTradePriceEvent.model_validate(
                    {
                        "type": "last_trade_price",
                        "payload": {
                            "market": market,
                            "asset_id": token_id,
                            "price": last_trade,
                            "size": last_trade_size,
                            "side": last_trade_side,
                            "timestamp": timestamp,
                        },
                    }
                )
            )
    return out


# ── user ───────────────────────────────────────────────────────────────────


def adapt_execution_frame(frame: dict[str, Any], spec: UserSpec) -> list[UserEvent]:
    """Map one ``/v1/ws/executions`` fill frame into user events for ``spec``.

    A fill projects into BOTH a ``UserTradeEvent`` (the fill) and a
    ``UserOrderEvent`` (the order's post-fill state). Non-``fill`` frames
    (``connected`` / ``pong``) yield nothing. When ``spec.markets`` is set,
    fills outside those markets are filtered out.
    """
    if frame.get("type") != "fill":
        return []
    market = _as_str(frame.get("market_id")) or ""
    if spec.markets is not None and market not in set(spec.markets):
        return []
    order_id = _as_str(frame.get("order_id")) or ""
    side = _as_str(frame.get("side")) or "BUY"
    price = _as_str(frame.get("price")) or "0"
    quantity = _as_str(frame.get("quantity")) or "0"
    outcome = _as_str(frame.get("outcome"))
    # The fill frame carries ``market_id`` + ``outcome`` but NO CLOB token/asset
    # id (see ``_broadcast_fill_sync`` in ``app/api_v1/matching_engine.py``). The
    # honest ``token_id`` is the SDK's canonical ``f"{market_id}:{OUTCOME}"``
    # (the inverse of ``_split_token``) — the SAME token the trading + ``get_*``
    # paths use. ``id`` / ``taker_order_id`` stay the real ``order_id`` (those
    # ARE order ids). When the outcome is absent we can't build an honest token,
    # so the frame yields nothing rather than re-using the order id as a fake
    # asset id.
    token_id = _make_token(market, outcome)
    if token_id is None:
        return []

    trade = UserTradeEvent.model_validate(
        {
            "type": "trade",
            "payload": {
                "id": order_id,
                "taker_order_id": order_id,
                "market": market,
                "asset_id": token_id,
                "side": side,
                "size": quantity,
                "price": price,
                "status": "MATCHED",
                "owner": "",
                "outcome": outcome,
            },
        }
    )
    order = UserOrderEvent.model_validate(
        {
            "type": "order",
            "payload": {
                "id": order_id,
                "owner": "",
                "market": market,
                "asset_id": token_id,
                "side": side,
                "original_size": quantity,
                "size_matched": quantity,
                "price": price,
                "type": "UPDATE",
                "status": "MATCHED",
                "outcome": outcome,
            },
        }
    )
    return [trade, order]


# ── crypto ─────────────────────────────────────────────────────────────────


def _crypto_event_for(update: dict[str, Any], spec: CryptoPricesSpec) -> CryptoPricesEvent | None:
    symbol = _as_str(update.get("symbol"))
    price = update.get("price")
    if symbol is None or price is None:
        return None
    source = (_as_str(update.get("source")) or "").lower()
    # Route by EXACT membership in the backend's known label sets — NOT a
    # ``'binance' in source`` substring (the real Binance label is
    # ``polymarket_rtds``, which has no 'binance' substring, so the old test
    # mis-routed every live Binance tick to the chainlink topic).
    if source in _BINANCE_SOURCES:
        is_binance = True
    elif source in _CHAINLINK_SOURCES:
        is_binance = False
    else:
        # Unknown label: neither topic claims it — drop rather than mis-route.
        return None
    topic = "prices.crypto.binance" if is_binance else "prices.crypto.chainlink"
    if topic != spec.topic:
        return None
    if spec.symbols is not None and symbol not in set(spec.symbols):
        return None
    payload = {
        "symbol": symbol,
        "timestamp": _coerce_payload_ts(update.get("timestamp")),
        "value": price,
    }
    envelope = {"type": "update", "timestamp": update.get("timestamp"), "payload": payload}
    if is_binance:
        return CryptoPricesBinanceEvent.model_validate(envelope)
    return CryptoPricesChainlinkEvent.model_validate(envelope)


def _coerce_payload_ts(value: Any) -> int:
    """The crypto payload's ``timestamp`` is a bare ``int`` (epoch ms) in
    py-sdk. The SSE frame's ``timestamp`` is often an ISO string; when it isn't
    an int-coercible value, default to 0 (the payload-level ts is a secondary
    field — the envelope-level ``timestamp`` carries the parsed datetime)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return 0


def adapt_spot_frame(frame: dict[str, Any], spec: CryptoPricesSpec) -> list[CryptoPricesEvent]:
    """Map one ``/prices/stream`` SSE block into crypto events for ``spec``.

    Handles both ``crypto_price`` (single) and ``crypto_price_batch`` (a
    ``data.updates[]`` fan-out). Each update is routed to the Binance or
    Chainlink topic by its ``source`` and filtered against the spec's topic +
    optional symbols. Non-crypto SSE events (``keepalive`` / ``market_price`` /
    ``snapshot`` / ``orderbook``) yield nothing.
    """
    event_type = frame.get("event")
    data = frame.get("data")
    if not isinstance(data, dict):
        return []
    updates: list[Any]
    if event_type == "crypto_price":
        updates = [data]
    elif event_type == "crypto_price_batch":
        raw_updates = data.get("updates")
        updates = list(raw_updates) if isinstance(raw_updates, list) else []
    else:
        return []
    out: list[CryptoPricesEvent] = []
    for update in updates:
        if not isinstance(update, dict):
            continue
        event = _crypto_event_for(update, spec)
        if event is not None:
            out.append(event)
    return out


__all__ = [
    "adapt_execution_frame",
    "adapt_prices_frame",
    "adapt_spot_frame",
]
