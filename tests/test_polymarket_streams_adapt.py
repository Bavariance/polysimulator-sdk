"""Pure-adapter unit tests — frame -> py-sdk event mapping, no transport.

The adapters are the single home of the wire->event logic. They take the raw
dict frames our ``polysim_sdk`` transports yield and the consumer's spec, and
return the parsed py-sdk CORE events (filtered by the spec). These tests feed
canned frames in the EXACT shapes the backend emits and assert the right event
types + field mapping come out.

Backend frame shapes (verified against the backend producers — see
``app/api_v1/websocket.py``, ``app/polymarket_ws.py``,
``app/api_v1/matching_engine.py``, ``app/main.py``):

* ``aprices_stream`` (/v1/ws/prices) yields the Redis ``price:{cid}`` cache blob
  verbatim (``ConnectionManager.broadcast_price``). ``last_trade_side`` /
  ``last_trade_size`` live at the FRAME TOP LEVEL (stamped by
  ``_handle_last_trade``), NOT inside the per-outcome dict — the outcome entry
  only carries ``price`` / ``best_bid`` / ``best_ask`` / ``last_trade`` (price)::

    {"type": "price", "market_id": "<condition_id>", "emit_ts_ms": 169...,
     "last_trade": "0.55", "last_trade_size": "10", "last_trade_side": "BUY",
     "best_bid": "...", "best_ask": "...", "updated_at": "<iso>",
     "outcomes": [{"token_id": "...", "price": "0.55", "best_bid": "0.54",
                   "best_ask": "0.56", "last_trade": "0.55"}, ...]}

* ``aexecutions_stream`` (/v1/ws/executions) yields the fill frame — it carries
  ``market_id`` + ``outcome`` but NO CLOB token/asset id::

    {"type": "fill", "order_id": "...", "market_id": "...", "side": "BUY",
     "outcome": "Yes", "price": "0.5", "quantity": "5",
     "filled_at": "<iso>"}  (plus non-fill {"type": "connected"/"pong"} frames)

* ``aspot_stream`` (/prices/stream, SSE) yields parsed blocks. The Binance feed's
  ``source`` is the literal ``polymarket_rtds`` (Polymarket-relayed Binance);
  the Chainlink feed's is ``chainlink_rtds``::

    {"event": "crypto_price", "data": {"symbol": "BTC", "price": 64588.0,
     "source": "chainlink_rtds", "timestamp": "<iso>"}}
    {"event": "crypto_price_batch", "data": {"updates": [{...}, ...], "count": N}}
"""

from __future__ import annotations

from decimal import Decimal

from polysim_polymarket.streams import (
    CryptoPricesBinanceEvent,
    CryptoPricesChainlinkEvent,
    CryptoPricesSpec,
    MarketBookEvent,
    MarketLastTradePriceEvent,
    MarketPriceChangeEvent,
    MarketSpec,
    UserOrderEvent,
    UserSpec,
    UserTradeEvent,
)
from polysim_polymarket.streams._adapt import (
    adapt_execution_frame,
    adapt_prices_frame,
    adapt_spot_frame,
)

# ── market (prices) ───────────────────────────────────────────────────────


# Real CLOB token ids are long all-digit strings (from Gamma ``clobTokenIds``);
# the SDK token a bot subscribes with is the ``condition_id:LABEL`` colon form.
# These namespaces are DISJOINT — the bug this guards against was filtering the
# delivered frame's CLOB-digit ``outcomes[].token_id`` against a spec built from
# the SDK's ``condition_id:LABEL`` tokens, which never matched, so the market
# stream yielded ZERO events end to end. The fixtures below therefore carry the
# digit token in the outcome (+ a ``label``) and the colon form in the spec.
_CID = "0xMARKET"
_DIGIT_YES = "71808113132989822442855088598318057266442063750485037658851536022100948393492"
_DIGIT_NO = "30192116683577436706185918334038714565383684895337088280549299804989307289613"


def _price_frame(
    label: str = "Yes",
    digit_token_id: str = _DIGIT_YES,
    cid: str = _CID,
    *,
    last_trade_side: str = "BUY",
    last_trade_size: str = "10",
) -> dict:
    # Mirrors the real Redis ``price:{cid}`` blob: ``market_id`` is the condition
    # id; ``last_trade_side`` / ``last_trade_size`` are TOP-LEVEL; the per-outcome
    # dict carries a ``label`` + the CLOB-NUMERIC ``token_id`` + the ``last_trade``
    # PRICE (plus price + TOB). The adapter DERIVES the SDK token ``cid:LABEL`` and
    # filters on THAT, never the digit ``token_id``.
    return {
        "type": "price",
        "market_id": cid,
        "emit_ts_ms": 1700000000000,
        "updated_at": "2026-06-14T12:00:00+00:00",
        "best_bid": "0.54",
        "best_ask": "0.56",
        "last_trade": "0.55",
        "last_trade_size": last_trade_size,
        "last_trade_side": last_trade_side,
        "outcomes": [
            {
                "label": label,
                "token_id": digit_token_id,
                "price": "0.55",
                "best_bid": "0.54",
                "best_ask": "0.56",
                "last_trade": "0.55",
            }
        ],
    }


def test_adapt_prices_emits_price_change_and_last_trade() -> None:
    spec = MarketSpec(token_ids=[f"{_CID}:YES"])
    events = adapt_prices_frame(_price_frame(), spec)
    types = [type(e) for e in events]
    assert MarketPriceChangeEvent in types
    assert MarketLastTradePriceEvent in types
    # no book event from a TOB-only price frame
    assert MarketBookEvent not in types


def test_adapt_prices_price_change_field_mapping() -> None:
    spec = MarketSpec(token_ids=[f"{_CID}:YES"])
    events = adapt_prices_frame(_price_frame(), spec)
    pc = next(e for e in events if isinstance(e, MarketPriceChangeEvent))
    assert pc.topic == "market"
    assert pc.payload.market == "0xMARKET"
    change = pc.payload.price_changes[0]
    # The emitted token is the derived SDK ``cid:LABEL`` form, NOT the CLOB digit.
    assert change.token_id == f"{_CID}:YES"
    assert change.token_id != _DIGIT_YES
    assert change.price == Decimal("0.55")
    assert change.best_bid == Decimal("0.54")
    assert change.best_ask == Decimal("0.56")


def test_adapt_prices_last_trade_field_mapping() -> None:
    spec = MarketSpec(token_ids=[f"{_CID}:YES"])
    events = adapt_prices_frame(_price_frame(), spec)
    lt = next(e for e in events if isinstance(e, MarketLastTradePriceEvent))
    # token is the derived SDK form, not the CLOB digit
    assert lt.payload.token_id == f"{_CID}:YES"
    assert lt.payload.token_id != _DIGIT_YES
    assert lt.payload.price == Decimal("0.55")
    assert lt.payload.size == Decimal("10")
    assert lt.payload.side == "BUY"


def test_adapt_prices_last_trade_side_from_top_level_sell() -> None:
    # Regression: a SELL frame's side+size live at the TOP LEVEL. The adapter
    # must read them from there (not the per-outcome dict, which carries
    # neither) and a SELL frame must yield side='SELL' — never a fabricated BUY.
    spec = MarketSpec(token_ids=[f"{_CID}:YES"])
    frame = _price_frame(last_trade_side="SELL", last_trade_size="7")
    events = adapt_prices_frame(frame, spec)
    lt = next(e for e in events if isinstance(e, MarketLastTradePriceEvent))
    assert lt.payload.side == "SELL"
    assert lt.payload.size == Decimal("7")
    assert lt.payload.price == Decimal("0.55")


def test_adapt_prices_no_last_trade_event_when_side_absent() -> None:
    # Defensive seam: if the top-level side is missing, no honest side can be
    # emitted, so the last_trade event is dropped rather than faked as BUY.
    spec = MarketSpec(token_ids=[f"{_CID}:YES"])
    frame = _price_frame()
    del frame["last_trade_side"]
    events = adapt_prices_frame(frame, spec)
    assert not any(isinstance(e, MarketLastTradePriceEvent) for e in events)
    # the price_change event is unaffected
    assert any(isinstance(e, MarketPriceChangeEvent) for e in events)


def test_adapt_prices_real_clob_digit_token_yields_nonzero_filtered_events() -> None:
    # Core regression for the namespace bug: the delivered frame carries the
    # Polymarket CLOB-DIGIT ``token_id`` in the outcome, while the spec carries
    # the SDK ``condition_id:LABEL`` token. The adapter must DERIVE the SDK token
    # from market_id + label and emit NON-ZERO, correctly-filtered events whose
    # token field is the SDK form — NOT filter the disjoint CLOB digit (which
    # would yield zero). A spec built from the documented SDK tokens used to
    # silently produce nothing end to end.
    spec = MarketSpec(token_ids=[f"{_CID}:YES"])
    frame = _price_frame("Yes", _DIGIT_YES)
    events = adapt_prices_frame(frame, spec)
    assert events, "real-shape CLOB-digit frame must NOT yield zero events"
    pc = next(e for e in events if isinstance(e, MarketPriceChangeEvent))
    lt = next(e for e in events if isinstance(e, MarketLastTradePriceEvent))
    assert pc.payload.price_changes[0].token_id == f"{_CID}:YES"
    assert lt.payload.token_id == f"{_CID}:YES"
    # the CLOB digit never appears as an emitted token
    assert pc.payload.price_changes[0].token_id != _DIGIT_YES


def test_adapt_prices_filters_by_token_ids() -> None:
    # frame carries the YES outcome, spec only wants NO -> nothing emitted
    spec = MarketSpec(token_ids=[f"{_CID}:NO"])
    events = adapt_prices_frame(_price_frame("Yes", _DIGIT_YES), spec)
    assert events == []


def test_adapt_prices_multiple_outcomes_filtered() -> None:
    # Binary market frame: YES (digit) + NO (digit). Spec wants only NO -> only
    # the NO outcome's DERIVED SDK token survives; the YES outcome is filtered
    # out. Proves the per-outcome derivation + filter, not a global digit match.
    frame = _price_frame("Yes", _DIGIT_YES)
    frame["outcomes"].append(
        {
            "label": "No",
            "token_id": _DIGIT_NO,
            "price": "0.45",
            "best_bid": "0.44",
            "best_ask": "0.46",
        }
    )
    spec = MarketSpec(token_ids=[f"{_CID}:NO"])
    events = adapt_prices_frame(frame, spec)
    # only the NO outcome's events
    for e in events:
        if isinstance(e, MarketPriceChangeEvent):
            assert e.payload.price_changes[0].token_id == f"{_CID}:NO"
    assert any(isinstance(e, MarketPriceChangeEvent) for e in events)


def test_adapt_prices_no_last_trade_when_absent() -> None:
    frame = _price_frame()
    del frame["outcomes"][0]["last_trade"]
    spec = MarketSpec(token_ids=[f"{_CID}:YES"])
    events = adapt_prices_frame(frame, spec)
    assert not any(isinstance(e, MarketLastTradePriceEvent) for e in events)
    assert any(isinstance(e, MarketPriceChangeEvent) for e in events)


def test_adapt_prices_non_binary_up_down_labels() -> None:
    # Non-binary (e.g. crypto Up/Down) market: outcomes labelled "Up"/"Down"
    # with their own CLOB digits. The spec carries ``cid:UP`` / ``cid:DOWN`` SDK
    # tokens (a generic colon split, NOT the binary-only YES/NO seam). Each
    # outcome must derive + filter by its ``cid:LABEL`` token, so a DOWN-only
    # spec yields the DOWN outcome and filters out UP.
    frame = {
        "type": "price",
        "market_id": _CID,
        "emit_ts_ms": 1700000000000,
        "last_trade": "0.62",
        "last_trade_size": "3",
        "last_trade_side": "BUY",
        "outcomes": [
            {"label": "Up", "token_id": _DIGIT_YES, "price": "0.62",
             "best_bid": "0.61", "best_ask": "0.63", "last_trade": "0.62"},
            {"label": "Down", "token_id": _DIGIT_NO, "price": "0.38",
             "best_bid": "0.37", "best_ask": "0.39", "last_trade": "0.38"},
        ],
    }
    spec = MarketSpec(token_ids=[f"{_CID}:DOWN"])
    events = adapt_prices_frame(frame, spec)
    assert events, "UP/DOWN frame must yield events for the DOWN-only spec"
    for e in events:
        if isinstance(e, MarketPriceChangeEvent):
            assert e.payload.price_changes[0].token_id == f"{_CID}:DOWN"
        if isinstance(e, MarketLastTradePriceEvent):
            assert e.payload.token_id == f"{_CID}:DOWN"
    # UP is filtered out
    assert all(
        not (isinstance(e, MarketPriceChangeEvent)
             and e.payload.price_changes[0].token_id == f"{_CID}:UP")
        for e in events
    )


def test_adapt_prices_ignores_non_price_frame() -> None:
    spec = MarketSpec(token_ids=[f"{_CID}:YES"])
    assert adapt_prices_frame({"type": "subscribed", "markets": ["0xM"]}, spec) == []
    assert adapt_prices_frame({"raw": "garbage"}, spec) == []


def test_adapt_prices_does_not_emit_custom_feature_events() -> None:
    # Even with custom_feature_enabled, the paper stream emits no
    # best_bid_ask / new_market / market_resolved events.
    spec = MarketSpec(token_ids=[f"{_CID}:YES"], custom_feature_enabled=True)
    events = adapt_prices_frame(_price_frame(), spec)
    from polysim_polymarket.streams import (
        MarketBestBidAskEvent,
        MarketResolvedEvent,
        NewMarketEvent,
    )

    for e in events:
        assert not isinstance(e, MarketBestBidAskEvent | NewMarketEvent | MarketResolvedEvent)


# ── user (executions) ─────────────────────────────────────────────────────


def _fill_frame(market: str = "0xMARKET", outcome: str = "Yes") -> dict:
    return {
        "type": "fill",
        "order_id": "ord1",
        "market_id": market,
        "side": "BUY",
        "outcome": outcome,
        "price": "0.50",
        "quantity": "5",
        "filled_at": "2026-06-14T12:00:00+00:00",
    }


def test_adapt_execution_emits_trade_and_order() -> None:
    spec = UserSpec()
    events = adapt_execution_frame(_fill_frame(), spec)
    types = {type(e) for e in events}
    assert UserTradeEvent in types
    assert UserOrderEvent in types


def test_adapt_execution_trade_field_mapping() -> None:
    spec = UserSpec()
    events = adapt_execution_frame(_fill_frame(), spec)
    trade = next(e for e in events if isinstance(e, UserTradeEvent))
    assert trade.topic == "user"
    assert trade.payload.market == "0xMARKET"
    assert trade.payload.side == "BUY"
    assert trade.payload.price == Decimal("0.50")
    assert trade.payload.size == Decimal("5")
    assert trade.payload.taker_order_id == "ord1"
    assert trade.payload.outcome == "Yes"
    # token_id is the honest market:OUTCOME token, NOT the order id.
    assert trade.payload.token_id == "0xMARKET:YES"
    assert trade.payload.token_id != "ord1"


def test_adapt_execution_order_field_mapping() -> None:
    spec = UserSpec()
    events = adapt_execution_frame(_fill_frame(), spec)
    order = next(e for e in events if isinstance(e, UserOrderEvent))
    assert order.payload.id == "ord1"
    assert order.payload.market == "0xMARKET"
    assert order.payload.side == "BUY"
    # id stays the real order id; token_id is the honest market:OUTCOME token.
    assert order.payload.token_id == "0xMARKET:YES"


def test_adapt_execution_token_id_no_outcome() -> None:
    # Regression: a fill with outcome='No' yields token_id == f"{market}:NO"
    # (the SDK's canonical convention), on BOTH the trade and order payloads.
    spec = UserSpec()
    events = adapt_execution_frame(_fill_frame(market="0xABC", outcome="No"), spec)
    trade = next(e for e in events if isinstance(e, UserTradeEvent))
    order = next(e for e in events if isinstance(e, UserOrderEvent))
    assert trade.payload.token_id == "0xABC:NO"
    assert order.payload.token_id == "0xABC:NO"


def test_adapt_execution_drops_fill_without_outcome() -> None:
    # No outcome -> no honest token -> the frame yields nothing (rather than
    # faking the asset id from the order id).
    spec = UserSpec()
    frame = _fill_frame()
    del frame["outcome"]
    assert adapt_execution_frame(frame, spec) == []


def test_adapt_execution_filters_by_markets() -> None:
    spec = UserSpec(markets=["0xOTHER"])
    assert adapt_execution_frame(_fill_frame(market="0xMARKET"), spec) == []


def test_adapt_execution_no_filter_passes_all() -> None:
    spec = UserSpec()  # markets=None -> all markets
    assert adapt_execution_frame(_fill_frame(market="0xANY"), spec)


def test_adapt_execution_ignores_non_fill_frames() -> None:
    spec = UserSpec()
    assert adapt_execution_frame({"type": "connected", "feed": "executions"}, spec) == []
    assert adapt_execution_frame({"type": "pong", "ts": 1}, spec) == []


# ── crypto (spot SSE) ─────────────────────────────────────────────────────


def test_adapt_spot_chainlink_single() -> None:
    spec = CryptoPricesSpec(topic="prices.crypto.chainlink")
    frame = {
        "event": "crypto_price",
        "data": {
            "symbol": "BTC",
            "price": 64588.0,
            "source": "chainlink_rtds",
            "timestamp": "2026-06-14T12:00:00+00:00",
        },
    }
    events = adapt_spot_frame(frame, spec)
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, CryptoPricesChainlinkEvent)
    assert ev.payload.symbol == "BTC"
    assert ev.payload.value == Decimal("64588.0")


def test_adapt_spot_binance_single() -> None:
    # The real live Binance label is ``polymarket_rtds`` — it has NO 'binance'
    # substring, so the old ``'binance' in source`` test would have mis-routed
    # it to chainlink. Membership routing maps it correctly to the binance topic.
    spec = CryptoPricesSpec(topic="prices.crypto.binance")
    frame = {
        "event": "crypto_price",
        "data": {"symbol": "ETH", "price": 3400.5, "source": "polymarket_rtds"},
    }
    events = adapt_spot_frame(frame, spec)
    assert len(events) == 1
    assert isinstance(events[0], CryptoPricesBinanceEvent)
    assert events[0].payload.value == Decimal("3400.5")


def test_adapt_spot_polymarket_rtds_routes_to_binance_not_chainlink() -> None:
    # Regression: a ``polymarket_rtds`` tick is a Binance tick. It must land on
    # the binance topic AND be filtered OUT of a chainlink spec.
    data = {"symbol": "BTC", "price": 64000.0, "source": "polymarket_rtds"}
    binance_spec = CryptoPricesSpec(topic="prices.crypto.binance")
    chainlink_spec = CryptoPricesSpec(topic="prices.crypto.chainlink")
    on_binance = adapt_spot_frame({"event": "crypto_price", "data": data}, binance_spec)
    on_chainlink = adapt_spot_frame({"event": "crypto_price", "data": data}, chainlink_spec)
    assert len(on_binance) == 1
    assert isinstance(on_binance[0], CryptoPricesBinanceEvent)
    assert on_chainlink == []


def test_adapt_spot_relay_binance_routes_to_binance() -> None:
    # ``relay_binance`` is the secondary relay label — also a Binance source.
    spec = CryptoPricesSpec(topic="prices.crypto.binance")
    frame = {
        "event": "crypto_price",
        "data": {"symbol": "BTC", "price": 64000.0, "source": "relay_binance"},
    }
    events = adapt_spot_frame(frame, spec)
    assert len(events) == 1
    assert isinstance(events[0], CryptoPricesBinanceEvent)


def test_adapt_spot_unknown_source_dropped() -> None:
    # An unrecognised source label matches NEITHER topic and is dropped (not
    # silently routed to chainlink as the substring test used to do).
    for topic in ("prices.crypto.binance", "prices.crypto.chainlink"):
        spec = CryptoPricesSpec(topic=topic)
        frame = {
            "event": "crypto_price",
            "data": {"symbol": "BTC", "price": 1.0, "source": "polymarket_rtds_pyth"},
        }
        assert adapt_spot_frame(frame, spec) == []


def test_adapt_spot_filters_by_topic_source() -> None:
    # spec wants binance; frame is chainlink-sourced -> filtered out
    spec = CryptoPricesSpec(topic="prices.crypto.binance")
    frame = {
        "event": "crypto_price",
        "data": {"symbol": "BTC", "price": 1.0, "source": "chainlink_rtds"},
    }
    assert adapt_spot_frame(frame, spec) == []


def test_adapt_spot_filters_by_symbols() -> None:
    spec = CryptoPricesSpec(topic="prices.crypto.binance", symbols=["ETH"])
    frame = {
        "event": "crypto_price",
        "data": {"symbol": "BTC", "price": 1.0, "source": "polymarket_rtds"},
    }
    assert adapt_spot_frame(frame, spec) == []


def test_adapt_spot_batch_fans_out() -> None:
    spec = CryptoPricesSpec(topic="prices.crypto.binance")
    frame = {
        "event": "crypto_price_batch",
        "data": {
            "updates": [
                {"symbol": "BTC", "price": 64000.0, "source": "polymarket_rtds"},
                {"symbol": "ETH", "price": 3400.0, "source": "polymarket_rtds"},
                {"symbol": "SOL", "price": 150.0, "source": "chainlink_rtds"},
            ],
            "count": 3,
        },
    }
    events = adapt_spot_frame(frame, spec)
    # only the 2 binance-sourced updates pass the topic filter
    assert len(events) == 2
    assert {e.payload.symbol for e in events} == {"BTC", "ETH"}
    assert all(isinstance(e, CryptoPricesBinanceEvent) for e in events)


def test_adapt_spot_ignores_non_crypto_events() -> None:
    spec = CryptoPricesSpec(topic="prices.crypto.binance")
    assert adapt_spot_frame({"event": "keepalive", "data": {"n": 1}}, spec) == []
    assert adapt_spot_frame({"event": "market_price", "data": {}}, spec) == []
    assert adapt_spot_frame({"event": "snapshot", "data": {}}, spec) == []
