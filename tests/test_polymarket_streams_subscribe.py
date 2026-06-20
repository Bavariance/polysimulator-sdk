"""Stream behavioral + surface tests for ``subscribe()``.

No live network: the underlying ``polysim_sdk`` transport generators
(``ws.aprices_stream`` / ``ws.aexecutions_stream`` / ``sse.aspot_stream``) are
monkeypatched to yield canned frames. We assert the right py-sdk events arrive
in order through the handle, that drop-oldest backpressure + ``dropped`` work,
and that ``close()`` / async-context exit tear the background task down.
"""

from __future__ import annotations

import asyncio
import inspect

import polysim_sdk.sse as sse_mod
import polysim_sdk.ws as ws_mod
from polysim_polymarket import AsyncPublicClient, AsyncSecureClient
from polysim_polymarket.clients import public as sync_public_mod
from polysim_polymarket.clients import secure as sync_secure_mod
from polysim_polymarket.streams import (
    CryptoPricesBinanceEvent,
    CryptoPricesSpec,
    MarketLastTradePriceEvent,
    MarketPriceChangeEvent,
    MarketSpec,
    SubscriptionHandle,
    UserSpec,
    UserTradeEvent,
)

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"


def _aclient_public() -> AsyncPublicClient:
    return AsyncPublicClient(host=BASE_URL, api_key=API_KEY)


async def _aclient_secure() -> AsyncSecureClient:
    return await AsyncSecureClient.create(host=BASE_URL, api_key=API_KEY)


# Real CLOB token ids are long all-digit strings (from Gamma ``clobTokenIds``);
# the SDK token a bot subscribes with is the ``condition_id:LABEL`` colon form.
# These two namespaces are DISJOINT — the adapter derives the SDK token from
# ``market_id`` + ``label`` and never matches the raw digit, so the fixture must
# carry the digit in the outcome and the colon form in the spec.
_CID = "0xMARKET"
_DIGIT_YES = "71808113132989822442855088598318057266442063750485037658851536022100948393492"
_DIGIT_NO = "30192116683577436706185918334038714565383684895337088280549299804989307289613"


def _price_frame(
    label: str = "Yes",
    price: str = "0.55",
    *,
    cid: str = _CID,
    digit_token_id: str = _DIGIT_YES,
) -> dict:
    # Real blob shape: ``market_id`` is the condition id; ``last_trade_side`` /
    # ``last_trade_size`` are TOP-LEVEL; the per-outcome dict carries a ``label``
    # ("Yes"/"No"/...) + the Polymarket CLOB-NUMERIC ``token_id`` + the
    # ``last_trade`` price (+ price + TOB). The adapter DERIVES the SDK token
    # ``cid:LABEL`` and filters on that, never the digit ``token_id``.
    return {
        "type": "price",
        "market_id": cid,
        "emit_ts_ms": 1700000000000,
        "last_trade": price,
        "last_trade_size": "10",
        "last_trade_side": "BUY",
        "outcomes": [
            {
                "label": label,
                "token_id": digit_token_id,
                "price": price,
                "best_bid": "0.54",
                "best_ask": "0.56",
                "last_trade": price,
            }
        ],
    }


def _fake_async_gen(frames):
    async def gen(*_args, **_kwargs):
        for f in frames:
            yield f

    return gen


# ── market stream ──────────────────────────────────────────────────────────


async def test_public_subscribe_market_yields_events_in_order(monkeypatch) -> None:
    frames = [_price_frame("Yes", "0.55"), _price_frame("Yes", "0.56")]
    monkeypatch.setattr(ws_mod, "aprices_stream", _fake_async_gen(frames))

    client = _aclient_public()
    spec = MarketSpec(token_ids=[f"{_CID}:YES"])
    collected = []
    async with await client.subscribe(spec) as h:
        async for ev in h:
            collected.append(ev)
            if len(collected) >= 4:
                break
    await client.close()

    # 2 frames -> each emits price_change + last_trade -> 4 events
    assert len(collected) == 4
    assert isinstance(collected[0], MarketPriceChangeEvent)
    assert isinstance(collected[1], MarketLastTradePriceEvent)
    assert collected[0].payload.price_changes[0].price.__str__() == "0.55"
    # The emitted token is the SDK ``cid:LABEL`` form, NOT the CLOB digit.
    assert collected[0].payload.price_changes[0].token_id == f"{_CID}:YES"


async def test_public_subscribe_market_filters_token_ids(monkeypatch) -> None:
    # First frame is the YES outcome (digit token + label "Yes"), second is NO.
    frames = [
        _price_frame("Yes", "0.55"),
        _price_frame("No", "0.40", digit_token_id=_DIGIT_NO),
    ]
    monkeypatch.setattr(ws_mod, "aprices_stream", _fake_async_gen(frames))

    client = _aclient_public()
    spec = MarketSpec(token_ids=[f"{_CID}:NO"])
    collected = []
    async with await client.subscribe(spec) as h:
        async for ev in h:
            collected.append(ev)
            if len(collected) >= 2:
                break
    await client.close()
    # only the NO outcome's derived SDK token survives the filter
    for ev in collected:
        if isinstance(ev, MarketPriceChangeEvent):
            assert ev.payload.price_changes[0].token_id == f"{_CID}:NO"


async def test_market_subscribe_strips_outcome_to_condition_ids(monkeypatch) -> None:
    # The prices WS subscribes by CONDITION id, so the opener must strip the
    # SDK token's ``:OUTCOME`` suffix and pass de-duplicated condition ids to
    # ``aprices_stream`` — passing the SDK's own ``cid:OUTCOME`` tokens (the same
    # form get_*/trading use) must yield a working subscription.
    captured: dict[str, object] = {}

    def fake_aprices_stream(_client, condition_ids=None, **_kwargs):
        captured["condition_ids"] = condition_ids

        async def gen():
            if False:  # pragma: no cover — empty async generator
                yield {}

        return gen()

    monkeypatch.setattr(ws_mod, "aprices_stream", fake_aprices_stream)
    client = _aclient_public()
    # Both outcomes of one market + one outcome of another -> two condition ids.
    spec = MarketSpec(token_ids=["0xAAA:YES", "0xAAA:NO", "0xBBB:YES"])
    h = await client.subscribe(spec)
    assert captured["condition_ids"] == ["0xAAA", "0xBBB"]
    await h.close()
    await client.close()


async def test_market_subscribe_strips_non_binary_up_down_to_condition_ids(monkeypatch) -> None:
    # Regression: a non-binary market's SDK tokens use UP/DOWN labels. The strip
    # MUST split on the last colon generically — NOT the binary-only
    # ``_split_token``, which would treat ``0xCCC:UP`` as a whole condition id
    # (outcome YES) and mis-subscribe to a non-existent ``0xCCC:UP`` market.
    captured: dict[str, object] = {}

    def fake_aprices_stream(_client, condition_ids=None, **_kwargs):
        captured["condition_ids"] = condition_ids

        async def gen():
            if False:  # pragma: no cover — empty async generator
                yield {}

        return gen()

    monkeypatch.setattr(ws_mod, "aprices_stream", fake_aprices_stream)
    client = _aclient_public()
    spec = MarketSpec(token_ids=["0xCCC:UP", "0xCCC:DOWN"])
    h = await client.subscribe(spec)
    # both UP/DOWN tokens strip to the SAME bare condition id (de-duplicated)
    assert captured["condition_ids"] == ["0xCCC"]
    await h.close()
    await client.close()


async def test_subscribe_returns_subscription_handle(monkeypatch) -> None:
    monkeypatch.setattr(ws_mod, "aprices_stream", _fake_async_gen([]))
    client = _aclient_public()
    h = await client.subscribe(MarketSpec(token_ids=[f"{_CID}:YES"]))
    assert isinstance(h, SubscriptionHandle)
    await h.close()
    await client.close()


# ── crypto stream ──────────────────────────────────────────────────────────


async def test_public_subscribe_crypto_yields_events(monkeypatch) -> None:
    frames = [
        {
            "event": "crypto_price",
            "data": {"symbol": "BTC", "price": 64000.0, "source": "polymarket_rtds"},
        },
        {
            "event": "crypto_price",
            "data": {"symbol": "ETH", "price": 3400.0, "source": "polymarket_rtds"},
        },
    ]
    monkeypatch.setattr(sse_mod, "aspot_stream", _fake_async_gen(frames))

    client = _aclient_public()
    spec = CryptoPricesSpec(topic="prices.crypto.binance")
    collected = []
    async with await client.subscribe(spec) as h:
        async for ev in h:
            collected.append(ev)
            if len(collected) >= 2:
                break
    await client.close()

    assert all(isinstance(e, CryptoPricesBinanceEvent) for e in collected)
    assert [e.payload.symbol for e in collected] == ["BTC", "ETH"]


# ── user stream (secure only) ──────────────────────────────────────────────


def _fill_frame(market: str = "0xMARKET") -> dict:
    return {
        "type": "fill",
        "order_id": "ord1",
        "market_id": market,
        "side": "BUY",
        "outcome": "Yes",
        "price": "0.50",
        "quantity": "5",
        "filled_at": "2026-06-14T12:00:00+00:00",
    }


async def test_secure_subscribe_user_yields_trade_events(monkeypatch) -> None:
    monkeypatch.setattr(ws_mod, "aexecutions_stream", _fake_async_gen([_fill_frame()]))

    client = await _aclient_secure()
    spec = UserSpec()
    collected = []
    async with await client.subscribe(spec) as h:
        async for ev in h:
            collected.append(ev)
            if len(collected) >= 2:
                break
    await client.close()

    # one fill -> trade + order
    assert any(isinstance(e, UserTradeEvent) for e in collected)


async def test_secure_subscribe_market_also_works(monkeypatch) -> None:
    monkeypatch.setattr(ws_mod, "aprices_stream", _fake_async_gen([_price_frame("Yes", "0.55")]))
    client = await _aclient_secure()
    collected = []
    async with await client.subscribe(MarketSpec(token_ids=[f"{_CID}:YES"])) as h:
        async for ev in h:
            collected.append(ev)
            if len(collected) >= 2:
                break
    await client.close()
    assert collected


# ── backpressure + teardown ────────────────────────────────────────────────


async def test_drop_oldest_backpressure_on_slow_consumer(monkeypatch) -> None:
    # Many frames, a tiny queue, and a consumer that never reads until the
    # producer has filled past capacity -> dropped must be > 0.
    frames = [_price_frame("Yes", f"0.{i:02d}") for i in range(50)]
    monkeypatch.setattr(ws_mod, "aprices_stream", _fake_async_gen(frames))

    client = _aclient_public()
    h = await client.subscribe(MarketSpec(token_ids=[f"{_CID}:YES"]), queue_size=2)
    # DETERMINISTIC drain: yield to the pump task repeatedly until it has dropped
    # at least one frame, rather than racing a fixed sleep. 50 frames into a
    # size-2 queue with no consumer MUST drop; bound the loop so a real failure
    # surfaces as an assertion, not a hang.
    for _ in range(10_000):
        if h.dropped > 0:
            break
        await asyncio.sleep(0)  # one event-loop turn → let the pump advance
    assert h.dropped > 0
    await h.close()
    await client.close()


async def test_close_is_idempotent_and_cancels_pump(monkeypatch) -> None:
    # An infinite producer; close() must cancel the pump and not hang.
    async def infinite(*_a, **_k):
        while True:
            yield _price_frame("Yes", "0.55")
            await asyncio.sleep(0)

    monkeypatch.setattr(ws_mod, "aprices_stream", infinite)
    client = _aclient_public()
    h = await client.subscribe(MarketSpec(token_ids=[f"{_CID}:YES"]))
    await asyncio.sleep(0.01)
    await h.close()
    await h.close()  # idempotent
    # after close, iteration terminates promptly
    remaining = [ev async for ev in h]
    assert isinstance(remaining, list)
    await client.close()


async def test_context_exit_closes_handle(monkeypatch) -> None:
    async def infinite(*_a, **_k):
        while True:
            yield _price_frame("Yes", "0.55")
            await asyncio.sleep(0)

    monkeypatch.setattr(ws_mod, "aprices_stream", infinite)
    client = _aclient_public()
    h = await client.subscribe(MarketSpec(token_ids=[f"{_CID}:YES"]))
    async with h:
        pass
    # context exit closed it: a subsequent iteration ends immediately
    tail = [ev async for ev in h]
    assert isinstance(tail, list)
    await client.close()


# ── surface parity: sync clients have NO subscribe ─────────────────────────


def test_sync_public_client_has_no_subscribe() -> None:
    assert not hasattr(sync_public_mod.PublicClient, "subscribe")


def test_sync_secure_client_has_no_subscribe() -> None:
    assert not hasattr(sync_secure_mod.SecureClient, "subscribe")


def test_async_public_subscribe_is_coroutine() -> None:
    assert inspect.iscoroutinefunction(AsyncPublicClient.subscribe)


def test_async_secure_subscribe_is_coroutine() -> None:
    assert inspect.iscoroutinefunction(AsyncSecureClient.subscribe)


async def test_subscribe_accepts_specs_as_keyword(monkeypatch) -> None:
    # The runtime impl drops the positional-only ``/`` (matching py-sdk's
    # runtime def) so ``specs=`` works; the overload stubs keep the ``/`` to
    # constrain typed callers. Passing ``specs=`` must NOT TypeError.
    monkeypatch.setattr(ws_mod, "aprices_stream", _fake_async_gen([]))
    client = _aclient_public()
    h = await client.subscribe(specs=MarketSpec(token_ids=[f"{_CID}:YES"]))
    assert isinstance(h, SubscriptionHandle)
    await h.close()
    await client.close()


def test_subscribe_impl_specs_is_positional_or_keyword() -> None:
    # The runtime parameter is POSITIONAL_OR_KEYWORD (no ``/``) on both clients.
    for cls in (AsyncPublicClient, AsyncSecureClient):
        param = inspect.signature(cls.subscribe).parameters["specs"]
        assert param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
