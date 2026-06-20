"""``polysim_polymarket`` — py-sdk (``polymarket-client``) drop-in parity surface.

The premise of this package: a bot developed/paper-tested against
``polysim_polymarket`` runs unchanged on real Polymarket by swapping the import
prefix (``polysim_polymarket`` -> ``polymarket``), the host, and the auth.

This suite is the Phase-1 (CLOB market-data READ) foundation:
  * package imports + re-exports;
  * shared exception identity with the v1 ``polysim_clob_client`` mirror;
  * the ``Environment`` config carrying PolySimulator hosts;
  * the pydantic read models mirroring py-sdk field names/types;
  * the ``PublicClient`` constructor (sim->real-swap kwargs) + lifecycle;
  * ``get_order_book`` delegating to the proven v1 read path.
"""

from __future__ import annotations

import httpx
import pytest

BASE_URL = "https://api.polysimulator.test"
API_KEY = "ps_live_testkey"


# ── Task 1: package skeleton ───────────────────────────────────────────────


def test_package_imports():
    import polysim_polymarket as pm

    assert hasattr(pm, "PublicClient")
    assert "PublicClient" in pm.__all__


# ── Task 2: shared exceptions ──────────────────────────────────────────────


def test_errors_share_identity_with_v1_mirror():
    import polysim_clob_client.exceptions as v1_exc
    import polysim_polymarket.errors as pm_exc

    # The py-clob-client exception tree is preserved: PolyApiException is a
    # PolyException so a single ``except PolyException`` catches both.
    assert issubclass(pm_exc.PolyApiException, pm_exc.PolyException)
    # Re-exported by identity (NOT a re-declared parallel class), so an
    # ``except`` against either mirror catches errors raised by the other.
    assert pm_exc.PolyException is v1_exc.PolyException
    assert pm_exc.PolyApiException is v1_exc.PolyApiException
    assert "PolyException" in pm_exc.__all__
    assert "PolyApiException" in pm_exc.__all__


# ── Task 3: environments ───────────────────────────────────────────────────


def test_production_environment():
    from dataclasses import FrozenInstanceError

    from polysim_polymarket.environments import PRODUCTION, Environment

    assert isinstance(PRODUCTION, Environment)
    # Field names mirror py-sdk's Environment; values carry PolySim hosts.
    assert PRODUCTION.clob_url.startswith("https://")
    assert PRODUCTION.data_url.startswith("https://")
    assert PRODUCTION.name == "production"
    # Frozen — a ported bot can rely on the env being immutable.
    with pytest.raises(FrozenInstanceError):
        PRODUCTION.clob_url = "https://evil.example"  # type: ignore[misc]


# ── Task 5: models ─────────────────────────────────────────────────────────


def test_order_book_model_mirrors_pysdk_fields():
    from decimal import Decimal

    from polysim_polymarket.models import OrderBook, OrderBookLevel

    # Construct with py-sdk's wire field names. ``asset_id`` is the validation
    # alias for ``token_id`` (py-sdk uses the same alias).
    book = OrderBook(
        market="0xcond",
        asset_id="711",
        timestamp="1718600000000",
        bids=[{"price": "0.40", "size": "100"}],
        asks=[{"price": "0.60", "size": "50"}],
        min_order_size="5",
        tick_size="0.01",
        neg_risk=False,
        last_trade_price="0.55",
        hash="0xabc",
    )
    assert book.market == "0xcond"
    assert book.token_id == "711"
    # Prices/sizes parse as Decimal (py-sdk's _DecimalFromString).
    assert book.bids[0].price == Decimal("0.40")
    assert book.asks[0].size == Decimal("50")
    assert isinstance(book.bids[0], OrderBookLevel)
    assert book.tick_size == Decimal("0.01")
    assert book.neg_risk is False
    assert book.last_trade_price == Decimal("0.55")
    assert book.hash == "0xabc"
    # Empty-string last_trade_price coerces to None (py-sdk behaviour).
    book2 = OrderBook(
        market="m",
        asset_id="1",
        bids=[],
        asks=[],
        min_order_size="0",
        tick_size="0.01",
        neg_risk=False,
        last_trade_price="",
        hash="h",
    )
    assert book2.last_trade_price is None


def test_last_trade_price_model():
    from decimal import Decimal

    from polysim_polymarket.models import LastTradePrice

    ltp = LastTradePrice(price="0.42", side="BUY")
    assert ltp.price == Decimal("0.42")
    assert ltp.side == "BUY"


def test_last_trade_price_for_token_model():
    """py-sdk's plural last-trade-prices returns LastTradePriceForToken (it adds
    a token_id to the price+side pair). The mirror needs the same model."""
    from decimal import Decimal

    from polysim_polymarket.models import LastTradePriceForToken

    ltp = LastTradePriceForToken(token_id="711", price="0.42", side="SELL")
    assert ltp.token_id == "711"
    assert ltp.price == Decimal("0.42")
    assert ltp.side == "SELL"


def test_price_request_namedtuple():
    """py-sdk's get_prices takes a Sequence[PriceRequest] — a (token_id, side)
    NamedTuple. The mirror's PriceRequest must have the same shape/fields."""
    from polysim_polymarket.models import PriceRequest

    req = PriceRequest(token_id="711", side="BUY")
    assert req.token_id == "711"
    assert req.side == "BUY"
    # NamedTuple positional order matches py-sdk (token_id, side).
    assert tuple(req) == ("711", "BUY")


def test_price_history_point_model():
    from polysim_polymarket.models import PriceHistoryPoint

    # py-sdk has no PriceHistory wrapper — get_price_history returns a bare
    # tuple of these points (see test_get_price_history_returns_bare_tuple).
    pt = PriceHistoryPoint(t=1718600000, p=0.55)
    assert pt.t == 1718600000
    assert pt.p == 0.55
    # t/p are strict-typed (py-sdk parity): a float t or string p is rejected.
    pt2 = PriceHistoryPoint.model_validate({"t": 1718600060, "p": 0.56})
    assert pt2.p == 0.56


def test_market_model_mirrors_pysdk_field_names():
    from polysim_polymarket.models import Market, MarketState

    # active/closed/neg_risk nest under ``state`` (a MarketState sub-model),
    # mirroring py-sdk's ``Market.state`` — NOT top-level. A ported bot reads
    # ``market.state.closed`` exactly as on real Polymarket.
    m = Market(
        id="0xmarket",
        condition_id="0xcond",
        question="Will it rain?",
        slug="will-it-rain",
        state={"active": True, "closed": False, "neg_risk": False},
    )
    assert m.id == "0xmarket"
    assert m.condition_id == "0xcond"
    assert m.question == "Will it rain?"
    assert isinstance(m.state, MarketState)
    assert m.state.active is True
    assert m.state.closed is False
    assert m.state.neg_risk is False


def test_market_condition_id_alias_choices():
    """py-sdk's Market.condition_id accepts both ``conditionId`` and ``condition``
    wire keys via AliasChoices; the mirror must too."""
    from polysim_polymarket.models import Market

    # camelCase ``conditionId`` (Polymarket gamma wire shape).
    m1 = Market.model_validate({"id": "m1", "conditionId": "0xfromcamel"})
    assert m1.condition_id == "0xfromcamel"
    # the shorter ``condition`` alias.
    m2 = Market.model_validate({"id": "m2", "condition": "0xfromshort"})
    assert m2.condition_id == "0xfromshort"


def test_market_state_defaults_to_empty_when_absent():
    """A market with no state keys still exposes a MarketState (all-None), so
    ``market.state.closed`` never raises AttributeError on a ported bot."""
    from polysim_polymarket.models import Market, MarketState

    m = Market.model_validate({"id": "m"})
    assert isinstance(m.state, MarketState)
    assert m.state.active is None
    assert m.state.closed is None


# ── Task 6: PublicClient skeleton ──────────────────────────────────────────


def test_public_client_constructs_with_host_and_api_key():
    from polysim_polymarket import PublicClient
    from polysim_polymarket.environments import Environment

    client = PublicClient(host=BASE_URL, api_key=API_KEY)
    try:
        # api_key threads through to the internal PolySimClient.
        assert client._client._api_key == API_KEY
        assert client._client.base_url == BASE_URL
        # environment is a PROPERTY (no parens), matching py-sdk — a ported bot
        # reads client.environment.clob_url, which must not crash on a method.
        assert isinstance(client.environment, Environment)
        assert client.environment.clob_url.startswith("https://")
    finally:
        client.close()


def test_public_client_accepts_pysdk_onchain_kwargs_without_typeerror():
    from polysim_polymarket import PublicClient

    # The full sim->real-swap construction shape must not TypeError: a porting
    # author leaves the on-chain kwargs in place; they are accepted and ignored.
    client = PublicClient(
        host=BASE_URL,
        api_key=API_KEY,
        chain_id=137,
        signature_type=2,
        funder="0xFunderAddress",
        private_key="0xdeadbeef",
        logger=None,
    )
    client.close()


def test_public_client_is_a_context_manager():
    from polysim_polymarket import PublicClient

    with PublicClient(host=BASE_URL, api_key=API_KEY) as client:
        assert client._client._api_key == API_KEY


def test_public_client_api_key_from_env(monkeypatch):
    from polysim_polymarket import PublicClient

    monkeypatch.setenv("POLYSIM_API_KEY", "ps_live_fromenv")
    client = PublicClient(host=BASE_URL)
    try:
        assert client._client._api_key == "ps_live_fromenv"
    finally:
        client.close()


def test_public_client_routes_from_environment_clob_url_when_host_omitted():
    """With no ``host=``, the client routes to ``environment.clob_url``.

    py-sdk uses ``Environment`` to route every transport. The mirror must too,
    so ``client.environment.clob_url`` is the host the client actually talks to
    — otherwise ``.environment`` would lie about where requests go.
    """
    from dataclasses import replace

    from polysim_polymarket import PRODUCTION, PublicClient

    custom = replace(PRODUCTION, clob_url="https://clob.custom.test")
    client = PublicClient(custom, api_key=API_KEY)
    try:
        assert client._client.base_url == "https://clob.custom.test"
        assert client.environment.clob_url == "https://clob.custom.test"
    finally:
        client.close()


def test_public_client_host_overrides_environment_clob_url():
    """An explicit ``host=`` still wins over the environment's ``clob_url``."""
    from dataclasses import replace

    from polysim_polymarket import PRODUCTION, PublicClient

    custom = replace(PRODUCTION, clob_url="https://clob.custom.test")
    client = PublicClient(custom, host=BASE_URL, api_key=API_KEY)
    try:
        # host= is the explicit override; environment still reports its own url.
        assert client._client.base_url == BASE_URL
        assert client.environment.clob_url == "https://clob.custom.test"
    finally:
        client.close()


# ── Task 7: get_order_book (delegation) ────────────────────────────────────


@pytest.fixture
def public_client():
    from polysim_polymarket import PublicClient

    c = PublicClient(host=BASE_URL, api_key=API_KEY)
    # Keep the suite fast — drop the inter-request pacing floor.
    c._client._transport._floor_interval = 0.0
    yield c
    c.close()


def test_get_order_book_delegates_to_token_book(public_client, respx_mock):
    from decimal import Decimal

    from polysim_polymarket.models import OrderBook

    route = respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
                "tick_size": "0.01",
                "neg_risk": False,
            },
        )
    )
    book = public_client.get_order_book(token_id="711")
    assert isinstance(book, OrderBook)
    # Bare token id routes to GET /v1/book?token_id=711 (the parity path).
    assert dict(route.calls.last.request.url.params)["token_id"] == "711"
    # Parsed via the shared book helpers, adapted to the py-sdk model.
    assert book.bids[0].price == Decimal("0.40")
    assert book.bids[0].size == Decimal("100")
    assert book.asks[0].price == Decimal("0.60")
    assert book.asks[0].size == Decimal("50")
    # token_id echoes the asset the caller asked for; market carries condition.
    assert book.token_id == "711"
    assert book.market == "0xcond"
    assert book.neg_risk is False
    assert book.tick_size == Decimal("0.01")


def test_get_order_book_is_keyword_only(public_client, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200, json={"market": "m", "asset_id": "1", "bids": [], "asks": []}
        )
    )
    # token_id is keyword-only (py-sdk signature). Positional must fail.
    with pytest.raises(TypeError):
        public_client.get_order_book("1")  # type: ignore[misc]


def test_get_order_book_colon_form_routes_to_condition(public_client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets/c1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "c1",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
            },
        )
    )
    book = public_client.get_order_book(token_id="c1:NO")
    # The outcome rides through as a query param so :NO reads the NO book.
    assert dict(route.calls.last.request.url.params)["outcome"] == "NO"
    # token_id echoes exactly what the caller passed.
    assert book.token_id == "c1:NO"


def test_get_order_book_colon_up_routes_to_condition(public_client, respx_mock):
    # UpDown markets carry Up/Down outcomes; the colon form must route a :UP
    # token to the per-condition book endpoint with outcome=UP (mirroring the
    # :NO/:YES routing), NOT fall through to the bare-token /v1/book endpoint.
    route = respx_mock.get(f"{BASE_URL}/v1/markets/c1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "c1",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
            },
        )
    )
    book = public_client.get_order_book(token_id="c1:UP")
    # The outcome rides through as a query param so :UP reads the UP book.
    assert dict(route.calls.last.request.url.params)["outcome"] == "UP"
    # token_id echoes exactly what the caller passed.
    assert book.token_id == "c1:UP"


def test_get_order_book_normalises_level_ordering_to_pysdk_contract(
    public_client, respx_mock
):
    from decimal import Decimal

    # PolySim may serve levels in any order; py-sdk's OrderBook contract is
    # bids ASCENDING by price (best = bids[-1]) and asks DESCENDING by price
    # (best = asks[-1]). The adapter must normalise to that contract regardless
    # of the wire order, so a ported bot's bids[-1]/asks[-1] best-level reads
    # match real Polymarket.
    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                # Deliberately scrambled on the wire.
                "bids": [
                    {"price": "0.45", "size": "10"},
                    {"price": "0.40", "size": "20"},
                    {"price": "0.43", "size": "30"},
                ],
                "asks": [
                    {"price": "0.55", "size": "10"},
                    {"price": "0.60", "size": "20"},
                    {"price": "0.57", "size": "30"},
                ],
            },
        )
    )
    book = public_client.get_order_book(token_id="711")
    # Bids ascending: 0.40, 0.43, 0.45 — best (highest) bid last.
    assert [lvl.price for lvl in book.bids] == [
        Decimal("0.40"),
        Decimal("0.43"),
        Decimal("0.45"),
    ]
    assert book.bids[-1].price == Decimal("0.45")
    # Asks descending: 0.60, 0.57, 0.55 — best (lowest) ask last.
    assert [lvl.price for lvl in book.asks] == [
        Decimal("0.60"),
        Decimal("0.57"),
        Decimal("0.55"),
    ]
    assert book.asks[-1].price == Decimal("0.55")


# ── Task B: get_order_books (plural) ───────────────────────────────────────


def test_get_order_books_returns_tuple_per_token(public_client, respx_mock):
    from polysim_polymarket.models import OrderBook

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
            },
        )
    )
    books = public_client.get_order_books(token_ids=["711", "712"])
    # py-sdk returns a tuple[OrderBook, ...] — one entry per requested token.
    assert isinstance(books, tuple)
    assert len(books) == 2
    assert all(isinstance(b, OrderBook) for b in books)
    assert books[0].token_id == "711"
    assert books[1].token_id == "712"


# ── Task B: midpoints ──────────────────────────────────────────────────────


def _book_route(respx_mock):
    return respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
                "tick_size": "0.01",
            },
        )
    )


def test_get_midpoint_returns_decimal(public_client, respx_mock):
    from decimal import Decimal

    _book_route(respx_mock)
    mid = public_client.get_midpoint(token_id="711")
    # (0.40 + 0.60) / 2 == 0.50, returned as a Decimal (py-sdk return type).
    assert isinstance(mid, Decimal)
    assert mid == Decimal("0.5000")


def test_get_midpoints_keyed_by_token(public_client, respx_mock):
    from decimal import Decimal

    _book_route(respx_mock)
    mids = public_client.get_midpoints(token_ids=["711", "712"])
    assert set(mids) == {"711", "712"}
    assert mids["711"] == Decimal("0.5000")
    assert all(isinstance(v, Decimal) for v in mids.values())


# ── Task B: prices ─────────────────────────────────────────────────────────


def test_get_price_buy_is_best_ask(public_client, respx_mock):
    from decimal import Decimal

    _book_route(respx_mock)
    # py-sdk's /price convention (the EXECUTABLE price): BUY -> best ASK (the
    # price you'd pay to buy), SELL -> best BID (the price you'd receive to sell).
    assert public_client.get_price(token_id="711", side="BUY") == Decimal("0.6000")
    assert public_client.get_price(token_id="711", side="SELL") == Decimal("0.4000")


def test_get_prices_returns_nested_dict(public_client, respx_mock):
    from decimal import Decimal

    from polysim_polymarket.models import PriceRequest

    _book_route(respx_mock)
    prices = public_client.get_prices(
        requests=[
            PriceRequest(token_id="711", side="BUY"),
            PriceRequest(token_id="711", side="SELL"),
        ]
    )
    # py-sdk shape: {token_id: {side: Decimal}}; BUY -> best ASK, SELL -> best BID.
    assert prices["711"]["BUY"] == Decimal("0.6000")
    assert prices["711"]["SELL"] == Decimal("0.4000")


# ── Task B: spreads ────────────────────────────────────────────────────────


def test_get_spread_returns_decimal(public_client, respx_mock):
    from decimal import Decimal

    _book_route(respx_mock)
    # best_ask - best_bid == 0.60 - 0.40 == 0.20.
    assert public_client.get_spread(token_id="711") == Decimal("0.2000")


def test_get_spreads_keyed_by_token(public_client, respx_mock):
    from decimal import Decimal

    _book_route(respx_mock)
    spreads = public_client.get_spreads(token_ids=["711", "712"])
    assert set(spreads) == {"711", "712"}
    assert spreads["711"] == Decimal("0.2000")


# ── Task B: last trade price ───────────────────────────────────────────────


def test_get_last_trade_price_returns_model(public_client, respx_mock):
    from decimal import Decimal

    from polysim_polymarket.models import LastTradePrice

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
                "last_trade_price": "0.55",
            },
        )
    )
    ltp = public_client.get_last_trade_price(token_id="711")
    assert isinstance(ltp, LastTradePrice)
    assert ltp.price == Decimal("0.55")
    # side defaults to BUY when the book carries no explicit trade side.
    assert ltp.side in ("BUY", "SELL")


def test_get_last_trade_prices_returns_tuple_of_for_token(public_client, respx_mock):
    from decimal import Decimal

    from polysim_polymarket.models import LastTradePriceForToken

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
                "last_trade_price": "0.55",
            },
        )
    )
    out = public_client.get_last_trade_prices(token_ids=["711", "712"])
    assert isinstance(out, tuple)
    assert len(out) == 2
    assert all(isinstance(x, LastTradePriceForToken) for x in out)
    assert out[0].token_id == "711"
    assert out[0].price == Decimal("0.55")

# ── Task B/C.1: get_price_history (bare tuple) ─────────────────────────────


def test_get_price_history_returns_bare_tuple(public_client, respx_mock):
    from polysim_polymarket.models import PriceHistoryPoint

    route = respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(
            200,
            json={"history": [{"t": 1718600000, "p": 0.55}, {"t": 1718600060, "p": 0.56}]},
        )
    )
    out = public_client.get_price_history(token_id="711")
    # Task C.1: py-sdk returns a BARE tuple[PriceHistoryPoint, ...], not a
    # PriceHistory wrapper.
    assert isinstance(out, tuple)
    assert all(isinstance(p, PriceHistoryPoint) for p in out)
    assert out[0].t == 1718600000
    assert out[0].p == 0.55
    assert out[1].p == 0.56
    # token_id rides as the PM ``market`` query param (py-sdk's wire name).
    assert dict(route.calls.last.request.url.params)["market"] == "711"


def test_get_price_history_forwards_pm_params(public_client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(200, json={"history": []})
    )
    public_client.get_price_history(
        token_id="711", start_ts=1718600000, end_ts=1718700000, fidelity=5, interval="1h"
    )
    params = dict(route.calls.last.request.url.params)
    # py-sdk's exact PM param names: market / startTs / endTs / fidelity / interval.
    assert params["startTs"] == "1718600000"
    assert params["endTs"] == "1718700000"
    assert params["fidelity"] == "5"
    assert params["interval"] == "1h"


def test_get_price_history_is_keyword_only(public_client, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(200, json={"history": []})
    )
    with pytest.raises(TypeError):
        public_client.get_price_history("711")  # type: ignore[misc]


def test_get_price_history_malformed_point_raises(public_client, respx_mock):
    """A ``history`` list whose entries don't match the point shape raises.

    The envelope is valid (dict with a ``history`` list) but an entry can't be
    validated as a ``PriceHistoryPoint`` -> ``UnexpectedResponseError`` (not a
    silent drop), matching py-sdk and exercising the shared mapping helper's
    error path through the client.
    """
    from polysim_polymarket.errors import UnexpectedResponseError

    respx_mock.get(f"{BASE_URL}/v1/prices-history").mock(
        return_value=httpx.Response(200, json={"history": ["not-a-point"]})
    )
    with pytest.raises(UnexpectedResponseError):
        public_client.get_price_history(token_id="711")


# ── _common.map_price_history (shared mapping helper) ──────────────────────


def test_common_map_price_history_maps_points():
    from polysim_polymarket.clients import _common
    from polysim_polymarket.models import PriceHistoryPoint

    out = _common.map_price_history([{"t": 1718600000, "p": 0.55}, {"t": 1718600060, "p": 0.56}])
    assert isinstance(out, tuple)
    assert all(isinstance(p, PriceHistoryPoint) for p in out)
    assert out[0].t == 1718600000
    assert out[1].p == 0.56


def test_common_map_price_history_empty_is_empty_tuple():
    from polysim_polymarket.clients import _common

    assert _common.map_price_history([]) == ()


def test_common_map_price_history_malformed_raises():
    from polysim_polymarket.clients import _common
    from polysim_polymarket.errors import UnexpectedResponseError

    with pytest.raises(UnexpectedResponseError):
        _common.map_price_history(["not-a-point"])


# ── Task B: estimate_market_price ──────────────────────────────────────────


def test_estimate_market_price_buy_walks_asks(public_client, respx_mock):
    from decimal import Decimal

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}, {"price": "0.70", "size": "50"}],
            },
        )
    )
    # py-sdk returns the MARGINAL (limit) price — the worst level touched to
    # fill. $30 notional fills entirely at the best ask (0.60 * 50 = 30 >= 30),
    # so the marginal price is 0.60. Returned as a Decimal.
    px = public_client.estimate_market_price(token_id="711", side="BUY", amount=30)
    assert isinstance(px, Decimal)
    assert px == Decimal("0.60")


def test_estimate_market_price_sell_walks_bids(public_client, respx_mock):
    from decimal import Decimal

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "50"}],
            },
        )
    )
    # SELL 50 shares fills against the single 0.40 bid -> marginal price 0.40.
    px = public_client.estimate_market_price(token_id="711", side="SELL", shares=50)
    assert px == Decimal("0.40")


def test_estimate_market_price_buy_marginal_crosses_levels(public_client, respx_mock):
    from decimal import Decimal

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                # Best ask 0.50 carries only $5 notional (0.50 * 10); the order
                # has to touch the 0.60 level to complete.
                "asks": [{"price": "0.50", "size": "10"}, {"price": "0.60", "size": "100"}],
            },
        )
    )
    # py-sdk MARGINAL semantics: $20 notional exhausts the 0.50 level ($5) and
    # continues into 0.60, so the marginal (worst-level-touched) price is 0.60 —
    # NOT a size-weighted average. This is the test that VWAP would get wrong.
    px = public_client.estimate_market_price(token_id="711", side="BUY", amount=20)
    assert px == Decimal("0.60")


def test_estimate_market_price_sell_marginal_crosses_levels(public_client, respx_mock):
    from decimal import Decimal

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                # Best bid 0.60 has only 10 shares; selling 30 must reach 0.50.
                "bids": [{"price": "0.60", "size": "10"}, {"price": "0.50", "size": "100"}],
                "asks": [{"price": "0.70", "size": "50"}],
            },
        )
    )
    # Sell 30 shares: 10 at 0.60 then 20 at 0.50 -> marginal price 0.50.
    px = public_client.estimate_market_price(token_id="711", side="SELL", shares=30)
    assert px == Decimal("0.50")


def test_estimate_market_price_fok_underfill_raises(public_client, respx_mock):
    from polysim_polymarket.errors import InsufficientLiquidityError

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                # Only $30 of asks available ($6 + $24): a $50 FOK can't fill.
                "asks": [{"price": "0.60", "size": "10"}, {"price": "0.80", "size": "30"}],
            },
        )
    )
    # FOK (the default) that can't be fully filled raises, matching py-sdk's
    # InsufficientLiquidityError.
    with pytest.raises(InsufficientLiquidityError):
        public_client.estimate_market_price(token_id="711", side="BUY", amount=50)


def test_estimate_market_price_fak_underfill_returns_worst_level(public_client, respx_mock):
    from decimal import Decimal

    respx_mock.get(f"{BASE_URL}/v1/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "market": "0xcond",
                "asset_id": "711",
                "bids": [{"price": "0.40", "size": "100"}],
                "asks": [{"price": "0.60", "size": "10"}, {"price": "0.80", "size": "30"}],
            },
        )
    )
    # FAK underfill returns the worst (deepest) ask level price reached, not an
    # error — py-sdk's FAK fallback (asks[0].price = highest ask).
    px = public_client.estimate_market_price(
        token_id="711", side="BUY", amount=50, order_type="FAK"
    )
    assert px == Decimal("0.80")


def test_estimate_market_price_rejects_buy_with_shares(public_client):
    from polysim_polymarket.errors import UserInputError

    # No network call should be made — validation fires before the book read.
    with pytest.raises(UserInputError):
        public_client.estimate_market_price(token_id="711", side="BUY", shares=10)


def test_estimate_market_price_rejects_sell_with_amount(public_client):
    from polysim_polymarket.errors import UserInputError

    with pytest.raises(UserInputError):
        public_client.estimate_market_price(token_id="711", side="SELL", amount=10)


def test_estimate_market_price_rejects_missing_args(public_client):
    from polysim_polymarket.errors import UserInputError

    # BUY without amount, SELL without shares -> contradictory/missing input.
    with pytest.raises(UserInputError):
        public_client.estimate_market_price(token_id="711", side="BUY")
    with pytest.raises(UserInputError):
        public_client.estimate_market_price(token_id="711", side="SELL")


def test_estimate_market_price_rejects_bad_order_type(public_client):
    from polysim_polymarket.errors import UserInputError

    with pytest.raises(UserInputError):
        public_client.estimate_market_price(
            token_id="711", side="BUY", amount=10, order_type="GTC"
        )


def test_estimate_market_price_rejects_nonpositive_amount(public_client):
    from polysim_polymarket.errors import UserInputError

    with pytest.raises(UserInputError):
        public_client.estimate_market_price(token_id="711", side="BUY", amount=0)


def test_estimate_market_price_input_errors_subclass_polyexception(public_client):
    # The py-sdk-named errors stay catchable by the mirror's shared base, so a
    # bot that wraps calls in ``except PolyException`` still catches them.
    from polysim_polymarket.errors import (
        InsufficientLiquidityError,
        PolyException,
        UnexpectedResponseError,
        UserInputError,
    )

    assert issubclass(UserInputError, PolyException)
    assert issubclass(InsufficientLiquidityError, PolyException)
    assert issubclass(UnexpectedResponseError, PolyException)


# ── Task B: get_market ─────────────────────────────────────────────────────


def test_get_market_by_id(public_client, respx_mock):
    from polysim_polymarket.models import Market

    respx_mock.get(f"{BASE_URL}/v1/markets/0xcond").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "m1",
                "condition_id": "0xcond",
                "question": "Will it rain?",
                "slug": "will-it-rain",
                "active": True,
                "closed": False,
                "neg_risk": False,
            },
        )
    )
    market = public_client.get_market(id="0xcond")
    assert isinstance(market, Market)
    assert market.condition_id == "0xcond"
    assert market.question == "Will it rain?"
    # PolySim returns active/closed/neg_risk top-level; the mirror nests them
    # under state to match py-sdk, so a ported bot reads market.state.closed.
    assert market.state.active is True
    assert market.state.closed is False
    assert market.state.neg_risk is False


def test_get_market_by_slug(public_client, respx_mock):
    from polysim_polymarket.models import Market

    route = respx_mock.get(f"{BASE_URL}/v1/markets/by-slug/will-it-rain").mock(
        return_value=httpx.Response(
            200, json={"id": "m1", "condition_id": "0xcond", "slug": "will-it-rain"}
        )
    )
    market = public_client.get_market(slug="will-it-rain")
    assert isinstance(market, Market)
    assert market.slug == "will-it-rain"
    assert route.called


def test_get_market_is_keyword_only(public_client, respx_mock):
    respx_mock.get(f"{BASE_URL}/v1/markets/0xcond").mock(
        return_value=httpx.Response(200, json={"id": "m1", "condition_id": "0xcond"})
    )
    with pytest.raises(TypeError):
        public_client.get_market("0xcond")  # type: ignore[misc]


# ── Task B: list_markets (Paginator) ───────────────────────────────────────


def test_list_markets_returns_paginator_of_market(public_client, respx_mock):
    from polysim_polymarket.models import Market
    from polysim_polymarket.pagination import Paginator

    respx_mock.get(f"{BASE_URL}/v1/markets").mock(
        return_value=httpx.Response(
            200,
            json={
                "markets": [
                    {"id": "m1", "condition_id": "0xc1", "active": True, "closed": False},
                    {"id": "m2", "condition_id": "0xc2", "active": False, "closed": True},
                ]
            },
        )
    )
    pag = public_client.list_markets(closed=False)
    assert isinstance(pag, Paginator)
    page = pag.first_page()
    assert all(isinstance(m, Market) for m in page.items)
    assert page.items[0].condition_id == "0xc1"
    assert page.items[1].state.closed is True
    # The Market items expose nested state from PolySim's top-level keys.
    assert page.items[0].state.active is True


def test_list_markets_iter_items_adapts_each(public_client, respx_mock):
    from polysim_polymarket.models import Market

    # Short page (< page_size) → has_more False → single page.
    respx_mock.get(f"{BASE_URL}/v1/markets").mock(
        return_value=httpx.Response(
            200, json={"markets": [{"id": "m1", "condition_id": "0xc1"}]}
        )
    )
    items = list(public_client.list_markets().iter_items())
    assert len(items) == 1
    assert isinstance(items[0], Market)
    assert items[0].condition_id == "0xc1"


def test_list_markets_forwards_closed_filter(public_client, respx_mock):
    route = respx_mock.get(f"{BASE_URL}/v1/markets").mock(
        return_value=httpx.Response(200, json={"markets": []})
    )
    public_client.list_markets(closed=True).first_page()
    # The closed filter forwards to the native list_markets query (httpx
    # serialises the bool to a lowercase ``true`` on the wire).
    assert dict(route.calls.last.request.url.params).get("closed") == "true"
