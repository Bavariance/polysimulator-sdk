"""``ClobClient`` — a drop-in mirror of Polymarket's ``py-clob-client``.

The goal is that a bot written against ``py_clob_client`` ports by changing
**only** the import path, the host, and the auth call — and deleting the entire
on-chain prelude (private key, ``chain_id``, ``funder``, ``signature_type``,
USDC allowance/approval txns, web3/Polygon RPC).

PolySimulator is paper trading: there is **no on-chain verification** — no
EIP-712 signing, no wallet, no allowances, no settlement. The three
py-clob-client auth levels (L0 public / L1 private-key-signing / L2 HMAC)
collapse into ONE mode: a single ``ps_live_*`` API key sent as ``X-API-Key``.

Every method maps by one of three strategies:

* **mirror**  — behaviour is identical; just delegate to the internal client.
* **adapt**   — translate args / response shape onto the PolySim REST surface.
* **stub-noop** — on-chain machinery with no analog; returns a benign canned
  value. Each such method says so in its docstring, so a porting author reading
  the source knows nothing silently broke.

The client **holds an internal** :class:`polysim_sdk.PolySimClient` and routes
all HTTP through it, inheriting pacing / retry / error mapping in one place.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

from polysim_clob_client.clob_types import (
    ApiCreds,
    BalanceAllowanceParams,
    BookParams,
    CreateOrderOptions,
    MarketOrderArgs,
    OpenOrderParams,
    OrderArgs,
    OrderBookSummary,
    OrderSummary,
    OrderType,
    PartialCreateOrderOptions,
    PostOrdersArgs,
    TradeParams,
)
from polysim_clob_client.constants import (
    COLLATERAL_ADDRESS,
    CONDITIONAL_TOKENS_ADDRESS,
    END_CURSOR,
    EXCHANGE_ADDRESS,
    NEG_RISK_EXCHANGE_ADDRESS,
    START_CURSOR,
)
from polysim_clob_client.exceptions import PolyApiException
from polysim_sdk import PolySimClient
from polysim_sdk._http import DEFAULT_BASE_URL

_PAGE_LIMIT = 100
_DEFAULT_TICK_SIZE = 0.01
# A real Polymarket CLOB outcome-token id is a uint256 rendered as a long
# all-digit decimal string (typically ~70+ digits). We treat any all-digit id
# at or above this length as such and reverse-resolve it via the data API;
# shorter numeric ids stay PolySim condition ids (the parity seam).
_TOKEN_ID_MIN_DIGITS = 30


# ── cursor <-> offset translation ──────────────────────────────────────────
# py-clob-client paginates with base64 cursors ("MA=="=0, "LTE="=-1=done).
# PolySim REST is limit/offset, so we translate at the boundary.


def _decode_cursor(cursor: str | None) -> int:
    """base64 cursor -> integer offset. START/empty -> 0, END -> -1."""
    if not cursor or cursor == START_CURSOR:
        return 0
    if cursor == END_CURSOR:
        return -1
    try:
        return int(base64.b64decode(cursor).decode())
    except (ValueError, TypeError):
        return 0


def _encode_cursor(offset: int) -> str:
    """integer offset -> base64 cursor."""
    return base64.b64encode(str(offset).encode()).decode()


def _next_cursor(offset: int, page_len: int, limit: int) -> str:
    """Synthesise the next cursor: END when the page was short."""
    return _encode_cursor(offset + limit) if page_len >= limit else END_CURSOR


# ── order-book parsing helpers ─────────────────────────────────────────────


def _to_levels(raw: Any) -> list[tuple[float, float]]:
    """Normalise a side of the book to ``[(price, size), ...]`` floats.

    Tolerates dict levels (``{"price","size"|"quantity"}``) and pair levels
    (``[price, size]``); skips anything unparseable.
    """
    out: list[tuple[float, float]] = []
    for lvl in raw or []:
        price: Any
        size: Any
        if isinstance(lvl, dict):
            price = lvl.get("price")
            size = lvl.get("size", lvl.get("quantity"))
        elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            price, size = lvl[0], lvl[1]
        else:
            continue
        try:
            out.append((float(price), float(size)))
        except (TypeError, ValueError):
            continue
    return out


def _book_sides(
    book: dict[str, Any],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Extract (bids, asks) level lists from a PolySim book payload."""
    bids = _to_levels(book.get("bids"))
    asks = _to_levels(book.get("asks"))
    return bids, asks


def _best_bid(bids: list[tuple[float, float]]) -> float | None:
    return max((p for p, _ in bids), default=None)


def _best_ask(asks: list[tuple[float, float]]) -> float | None:
    return min((p for p, _ in asks), default=None)


class ClobClient:
    """py-clob-client-compatible client over the PolySimulator paper API.

    Drop-in constructor: every py-clob-client kwarg is accepted so existing
    construction sites don't ``TypeError``. The on-chain ones
    (``chain_id``, ``signature_type``, ``funder``, ``builder_config``) are
    accepted and ignored. The API key is resolved from, in order:
    ``api_key=`` -> ``creds.api_key`` -> ``key=`` (the "private key" slot is
    reused as the API key) -> ``POLYSIM_API_KEY`` env.
    """

    def __init__(
        self,
        host: str | None = None,
        chain_id: int | None = None,
        key: str | None = None,
        creds: ApiCreds | None = None,
        signature_type: int | None = None,
        funder: str | None = None,
        builder_config: Any = None,
        tick_size_ttl: float = 300.0,
        *,
        api_key: str | None = None,
    ) -> None:
        # On-chain kwargs are kept in the signature for drop-in parity but have
        # no effect — there is no chain, no signer, no funder in paper trading.
        self._chain_id = chain_id
        self._signature_type = signature_type
        self._funder = funder
        self._builder_config = builder_config
        self._creds = creds

        resolved_key = (
            api_key or (creds.api_key if creds and getattr(creds, "api_key", None) else None) or key
        )
        base_url = host or DEFAULT_BASE_URL
        # PolySimClient falls back to POLYSIM_API_KEY when resolved_key is None.
        self._client = PolySimClient(api_key=resolved_key, base_url=base_url)
        self._tick_sizes: dict[str, float] = {}
        self._tick_size_ttl = tick_size_ttl
        # token_id -> (market_id, outcome) cache for long-numeric CLOB token ids
        # reverse-resolved via GET /v1/markets-by-token (see _resolve_token).
        self._token_markets: dict[str, tuple[str, str]] = {}

    # ── resource lifecycle ─────────────────────────────────────────────────

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ClobClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ── token-id <-> (market_id, outcome) seam ─────────────────────────────

    @staticmethod
    def _split_token(token_id: str) -> tuple[str, str]:
        """Map a py-clob ``token_id`` onto PolySim ``(market_id, outcome)``.

        py-clob-client addresses a single outcome token; PolySim addresses a
        market plus an outcome (YES/NO). The parity seam: a bare ``token_id``
        is treated as the market id with outcome ``YES``; append ``":NO"`` /
        ``":YES"`` to target the other outcome explicitly.
        """
        tid = str(token_id)
        if ":" in tid:
            market_id, _, outcome = tid.rpartition(":")
            outcome = outcome.upper()
            if outcome in ("YES", "NO") and market_id:
                return market_id, outcome
        return tid, "YES"

    def _resolve_token(self, token_id: str) -> tuple[str, str]:
        """Resolve a py-clob ``token_id`` to PolySim ``(market_id, outcome)``.

        Unlike :meth:`_split_token`, this understands **real Polymarket CLOB
        outcome-token ids** — long all-digit strings that aren't PolySim
        condition ids. For those it reverse-resolves via
        ``GET /v1/markets-by-token/{token_id}`` (result cached per token), so an
        order placed with a genuine Polymarket token id lands on the right
        market + outcome. The ``condition_id:YES``/``:NO`` colon form and short
        / non-numeric ids resolve **locally with no network call** via
        :meth:`_split_token` (preserving the parity seam).
        """
        tid = str(token_id)
        # Colon form is our own convenience extension — always resolve locally.
        if ":" in tid:
            return self._split_token(tid)
        # A long all-digit id is a real CLOB outcome token: reverse-resolve it.
        if tid.isdigit() and len(tid) >= _TOKEN_ID_MIN_DIGITS:
            cached = self._token_markets.get(tid)
            if cached is not None:
                return cached
            market = self._client.get_market_by_token(tid)
            resolved = (
                str(market.get("condition_id") or market.get("market_id") or tid),
                market.get("outcome") or "YES",
            )
            self._token_markets[tid] = resolved
            return resolved
        # Short numeric / non-numeric: treat as a market id (parity seam).
        return self._split_token(tid)

    def _book_for_token(self, token_id: str) -> dict[str, Any]:
        """Fetch the order book for a py-clob ``token_id`` (true-parity read).

        A **bare** token id routes to the token-native
        ``GET /v1/book?token_id=...`` endpoint — this is what gives genuine
        parity with Polymarket's CLOB book reads, where py-clob-client always
        passes a real outcome-token id. The ``condition_id:YES`` / ``:NO``
        **colon form** is our own convenience extension; it keeps condition-id
        routing and threads the outcome through as a query param so ``:NO``
        actually reads the NO book.
        """
        tid = str(token_id)
        if ":" in tid:
            market_id, _, outcome = tid.rpartition(":")
            outcome = outcome.upper()
            if outcome in ("YES", "NO") and market_id:
                return self._client.get_book(market_id, outcome=outcome)
        return self._client.get_book_by_token(tid)

    # ── health / time / identity ───────────────────────────────────────────

    def get_ok(self) -> Any:
        """mirror — liveness probe (``GET /v1/health/live``)."""
        return self._client._transport.request("GET", "/v1/health/live")

    def get_server_time(self) -> int:
        """adapt — local unix time; the paper API has no server-clock endpoint."""
        return int(time.time())

    def get_address(self) -> str | None:
        """adapt — your account identity (``GET /v1/me``).

        Returns the PolySim user id in place of py-clob's on-chain address.
        It is cosmetic here (no signing uses it).
        """
        me = self._client.me()
        ident = me.get("id") or me.get("user_id") or me.get("wallet_address")
        return str(ident) if ident is not None else None

    # ── auth-level assertions ──────────────────────────────────────────────

    def assert_level_1_auth(self) -> None:
        """stub-noop — L1 is private-key signing; nothing to assert in paper mode."""
        return None

    def assert_level_2_auth(self) -> None:
        """adapt — L2 (API-key) auth: assert a key is configured."""
        if not getattr(self._client, "_api_key", None):
            raise PolyApiException(401, "Level 2 auth required: no API key configured.")

    def assert_builder_auth(self) -> None:
        """stub-noop — builder auth has no analog in the paper SDK."""
        return None

    def can_builder_auth(self) -> bool:
        """stub-noop — builder auth is unsupported; always False."""
        return False

    # ── api-key management (L1 on-chain key derivation -> no-op) ────────────

    def create_api_key(self, nonce: int | None = None) -> ApiCreds:
        """stub-noop — py-clob derives keys by signing with a private key.

        Paper keys are minted out-of-band (the dashboard / ``/v1/keys``), so
        this returns the already-configured credential rather than deriving one.
        """
        return self._creds or ApiCreds(api_key=getattr(self._client, "_api_key", "") or "")

    def derive_api_key(self, nonce: int | None = None) -> ApiCreds:
        """stub-noop — see :meth:`create_api_key`; returns the configured creds."""
        return self.create_api_key(nonce)

    def create_or_derive_api_creds(self, nonce: int | None = None) -> ApiCreds:
        """stub-noop — returns the configured creds; no on-chain derivation."""
        return self.create_api_key(nonce)

    def set_api_creds(self, creds: ApiCreds) -> None:
        """adapt — store creds locally and rebind the API key if one is given."""
        self._creds = creds
        new_key = getattr(creds, "api_key", None)
        if new_key:
            self._client = PolySimClient(api_key=new_key, base_url=self._client.base_url)

    def get_api_keys(self) -> dict[str, Any]:
        """adapt — list your API keys (``GET /v1/keys``), wrapped py-clob-style."""
        keys = self._client.list_keys()
        return {"apiKeys": keys}

    def delete_api_key(self) -> Any:
        """stub-noop — py-clob deletes the *current* derived key; paper keys are
        managed via the dashboard / ``/v1/keys/{id}``. No-op here."""
        return {"success": True}

    def get_closed_only_mode(self) -> dict[str, Any]:
        """stub-noop — no closed-only flag in the paper API."""
        return {"closed_only": False}

    # readonly-api-key family — entirely on-chain in py-clob; no-op here.
    def create_readonly_api_key(self, nonce: int | None = None) -> dict[str, Any]:
        """stub-noop — readonly keys have no analog in the paper SDK."""
        return {"apiKey": getattr(self._client, "_api_key", "")}

    def get_readonly_api_key(self) -> dict[str, Any]:
        """stub-noop — readonly keys have no analog in the paper SDK."""
        return {"apiKey": getattr(self._client, "_api_key", "")}

    def get_readonly_api_keys(self) -> list[str]:
        """stub-noop — py-clob-client's plural list-readonly-keys; none on paper."""
        return []

    def delete_readonly_api_key(self, key: str | None = None) -> dict[str, Any]:
        """stub-noop — readonly keys have no analog in the paper SDK.

        ``key`` is accepted for py-clob-client signature parity and ignored.
        """
        return {"success": True}

    def validate_readonly_api_key(
        self, address: str | None = None, key: str | None = None
    ) -> bool:
        """stub-noop — readonly keys have no analog; always True.

        ``address`` / ``key`` are accepted for signature parity and ignored.
        """
        return True

    # ── on-chain addresses (display-only constants) ────────────────────────

    def get_collateral_address(self) -> str:
        """stub-noop — canned USDC address for display parity; unused on paper."""
        return COLLATERAL_ADDRESS

    def get_conditional_address(self) -> str:
        """stub-noop — canned CTF address for display parity; unused on paper."""
        return CONDITIONAL_TOKENS_ADDRESS

    def get_exchange_address(self, neg_risk: bool = False) -> str:
        """stub-noop — canned CTF-Exchange address for display parity; unused.

        ``neg_risk=True`` returns the neg-risk exchange address, matching
        py-clob-client's signature. Neither is used on paper.
        """
        return NEG_RISK_EXCHANGE_ADDRESS if neg_risk else EXCHANGE_ADDRESS

    # ── markets (paginated, py-clob {data,next_cursor,count} shape) ─────────

    def _paged_markets(self, next_cursor: str = START_CURSOR, **filters: Any) -> dict[str, Any]:
        offset = _decode_cursor(next_cursor)
        if offset < 0:
            return {"data": [], "next_cursor": END_CURSOR, "count": 0}
        rows = self._client.list_markets(limit=_PAGE_LIMIT, offset=offset, **filters)
        return {
            "data": rows,
            "next_cursor": _next_cursor(offset, len(rows), _PAGE_LIMIT),
            "count": len(rows),
        }

    def get_markets(self, next_cursor: str = START_CURSOR) -> dict[str, Any]:
        """adapt — paginated market list (``GET /v1/markets``)."""
        return self._paged_markets(next_cursor)

    def get_simplified_markets(self, next_cursor: str = START_CURSOR) -> dict[str, Any]:
        """adapt — same source as :meth:`get_markets` (no separate simplified feed)."""
        return self._paged_markets(next_cursor)

    def get_sampling_markets(self, next_cursor: str = START_CURSOR) -> dict[str, Any]:
        """adapt — "sampling" (rewards-eligible) markets ~ hot markets here."""
        return self._paged_markets(next_cursor, hot_only=True)

    def get_sampling_simplified_markets(self, next_cursor: str = START_CURSOR) -> dict[str, Any]:
        """adapt — sampling + simplified collapse to the hot-market feed."""
        return self._paged_markets(next_cursor, hot_only=True)

    def get_market(self, condition_id: str) -> dict[str, Any]:
        """adapt — a single market (``GET /v1/markets/{condition_id}``)."""
        return self._client.get_market(condition_id)

    # ── order book ─────────────────────────────────────────────────────────

    def _order_book_summary(self, token_id: str) -> OrderBookSummary:
        book = self._book_for_token(token_id)
        bids, asks = _book_sides(book)
        return OrderBookSummary(
            market=book.get("market") or str(token_id),
            asset_id=str(token_id),
            timestamp=str(book.get("timestamp")) if book.get("timestamp") else None,
            bids=[OrderSummary(price=str(p), size=str(s)) for p, s in bids],
            asks=[OrderSummary(price=str(p), size=str(s)) for p, s in asks],
            tick_size=str(book.get("tick_size")) if book.get("tick_size") else None,
            neg_risk=book.get("neg_risk"),
            last_trade_price=(
                str(book.get("last_trade_price"))
                if book.get("last_trade_price") is not None
                else None
            ),
            hash=book.get("hash"),
        )

    def get_order_book(self, token_id: str) -> OrderBookSummary:
        """adapt — order-book snapshot (``GET /v1/markets/{id}/book``)."""
        return self._order_book_summary(token_id)

    def get_order_books(self, params: list[BookParams]) -> list[OrderBookSummary]:
        """adapt — multiple order books; one REST call per token."""
        return [self._order_book_summary(p.token_id) for p in params]

    def get_order_book_hash(self, orderbook: OrderBookSummary) -> str:
        """mirror — deterministic local hash of a book snapshot.

        py-clob uses a keccak of the canonical book; we use a stable sha256 of
        the same fields. The value is only ever compared against itself, so the
        hash algorithm choice is immaterial to correctness.
        """
        payload = json.dumps(
            {
                "market": orderbook.market,
                "asset_id": orderbook.asset_id,
                "bids": [(b.price, b.size) for b in orderbook.bids],
                "asks": [(a.price, a.size) for a in orderbook.asks],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "0x" + hashlib.sha256(payload.encode()).hexdigest()

    # ── prices / midpoints / spreads ───────────────────────────────────────

    def get_midpoint(self, token_id: str) -> dict[str, Any]:
        """adapt — book midpoint ``(best_bid + best_ask) / 2``.

        Up/Down warning: an Up/Down outcome trades on a synthetic ~0.99 ladder,
        so this midpoint is *not* the underlying price. For the spot of the asset
        an Up/Down market is betting on, use the native client's ``get_spot`` /
        ``polysim_sdk.sse.spot_stream``; for the strike, ``get_price_to_beat``.
        """
        bids, asks = _book_sides(self._book_for_token(token_id))
        bid, ask = _best_bid(bids), _best_ask(asks)
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
        return {"mid": None if mid is None else f"{mid:.4f}"}

    def get_midpoints(self, params: list[BookParams]) -> dict[str, Any]:
        """adapt — midpoints for many tokens, keyed by token id."""
        return {p.token_id: self.get_midpoint(p.token_id)["mid"] for p in params}

    def get_price(self, token_id: str, side: str) -> dict[str, Any]:
        """adapt — best price for a side, Polymarket convention: BUY -> best
        BID, SELL -> best ASK.

        This matches Polymarket's CLOB ``get_price`` wire contract (the price a
        resting order on that side would post at), **not** the best *executable*
        price for a taker. py-clob-client callers rely on this exact mapping, so
        we mirror it for drop-in parity.

        Up/Down warning: this is the outcome-token price on its synthetic ~0.99
        ladder, not the underlying asset price. Use the native ``get_spot`` /
        ``get_price_to_beat`` for the underlying and the strike.
        """
        bids, asks = _book_sides(self._book_for_token(token_id))
        if side.upper() == "BUY":
            px = _best_bid(bids)
        else:
            px = _best_ask(asks)
        return {"price": None if px is None else f"{px:.4f}"}

    def get_prices(self, params: list[BookParams]) -> dict[str, Any]:
        """adapt — prices for many (token, side) pairs, keyed by token id."""
        return {p.token_id: self.get_price(p.token_id, p.side or "BUY")["price"] for p in params}

    def get_spread(self, token_id: str) -> dict[str, Any]:
        """adapt — book spread ``best_ask - best_bid``.

        Up/Down warning: computed on the outcome's synthetic ~0.99 ladder, so it
        does not describe the underlying asset's spread.
        """
        bids, asks = _book_sides(self._book_for_token(token_id))
        bid, ask = _best_bid(bids), _best_ask(asks)
        spread = (ask - bid) if bid is not None and ask is not None else None
        return {"spread": None if spread is None else f"{spread:.4f}"}

    def get_spreads(self, params: list[BookParams]) -> dict[str, Any]:
        """adapt — spreads for many tokens, keyed by token id."""
        return {p.token_id: self.get_spread(p.token_id)["spread"] for p in params}

    def get_last_trade_price(self, token_id: str) -> dict[str, Any]:
        """adapt — last trade price from the book snapshot.

        Falls back to the market record (resolved via the book's condition id)
        when the book carries no ``last_trade_price``.
        """
        book = self._book_for_token(token_id)
        ltp = book.get("last_trade_price")
        if ltp is None:
            cid = book.get("market")
            if cid:
                market = self._client.get_market(cid)
                ltp = market.get("last_trade_price") or market.get("last_price")
        return {"price": None if ltp is None else str(ltp)}

    def get_last_trades_prices(self, params: list[BookParams]) -> dict[str, Any]:
        """adapt — last trade prices for many tokens, keyed by token id."""
        return {p.token_id: self.get_last_trade_price(p.token_id)["price"] for p in params}

    # ── tick size / neg-risk / fees ────────────────────────────────────────

    def get_tick_size(self, token_id: str) -> float:
        """adapt — minimum price increment; cached per token.

        Reads ``tick_size`` from the token's order-book snapshot when present,
        else defaults to ``0.01`` (PolySim's standard tick).
        """
        tid = str(token_id)
        if tid in self._tick_sizes:
            return self._tick_sizes[tid]
        tick = _DEFAULT_TICK_SIZE
        try:
            book = self._book_for_token(token_id)
            raw = book.get("tick_size")
            if raw is not None:
                tick = float(raw)
        except (PolyApiException, ValueError, TypeError):
            pass
        self._tick_sizes[tid] = tick
        return tick

    def clear_tick_size_cache(self, token_id: str | None = None) -> None:
        """mirror — drop the per-token tick-size cache (one token, or all).

        ``token_id`` matches py-clob-client's signature: drop just that token's
        cached tick size when given, else clear the whole cache.
        """
        if token_id is None:
            self._tick_sizes.clear()
        else:
            self._tick_sizes.pop(str(token_id), None)

    def get_neg_risk(self, token_id: str) -> bool:
        """adapt — neg-risk flag from the token's book snapshot; defaults False."""
        try:
            return bool(self._book_for_token(token_id).get("neg_risk", False))
        except PolyApiException:
            return False

    def get_fee_rate_bps(self, token_id: str | None = None) -> int:
        """adapt — paper trading is fee-free; always 0 bps."""
        return 0

    # ── order construction (UNSIGNED) ──────────────────────────────────────
    # The single biggest behavioural difference vs py-clob-client: create_order
    # and create_market_order return a PLAIN dict payload with NO signature.
    # post_order serialises it straight to POST /v1/orders.

    def _order_payload(
        self,
        *,
        token_id: str,
        side: str,
        price: float,
        order_type: str,
        time_in_force: str,
        quantity: float | None = None,
        amount: float | None = None,
        post_only: bool = False,
        expiration: int | None = None,
    ) -> dict[str, Any]:
        market_id, outcome = self._resolve_token(token_id)
        payload: dict[str, Any] = {
            "market_id": market_id,
            "outcome": outcome,
            "side": side.upper(),
            "price": float(price),
            "order_type": order_type,
            "time_in_force": time_in_force.upper(),
            # No `signature` field — paper orders are never EIP-712 signed.
        }
        if quantity is not None:
            payload["quantity"] = float(quantity)
        if amount is not None:
            payload["amount"] = float(amount)
        if post_only:
            payload["post_only"] = True
        if expiration:
            payload["expiration"] = int(expiration)
        return payload

    def create_order(
        self,
        order_args: OrderArgs,
        options: CreateOrderOptions | PartialCreateOrderOptions | None = None,
    ) -> dict[str, Any]:
        """adapt — build an UNSIGNED limit-order payload (no signature field).

        Returns a plain dict; pass it to :meth:`post_order`, or use
        :meth:`create_and_post_order` to do both in one call (the recommended
        porting path, since it hides the now-unsigned intermediate).

        Honours ``order_args.expiration`` (py-clob's GTD field): a non-zero
        unix-seconds timestamp builds a GTD order carrying that ``expiration``;
        zero (the default) stays GTC.
        """
        expiration = int(getattr(order_args, "expiration", 0) or 0)
        return self._order_payload(
            token_id=order_args.token_id,
            side=order_args.side,
            quantity=order_args.size,
            price=order_args.price,
            order_type="limit",
            time_in_force="GTD" if expiration > 0 else "GTC",
            expiration=expiration if expiration > 0 else None,
        )

    def create_market_order(
        self,
        order_args: MarketOrderArgs,
        options: CreateOrderOptions | PartialCreateOrderOptions | None = None,
    ) -> dict[str, Any]:
        """adapt — build an UNSIGNED marketable order payload (FOK by default).

        PolySim uses a marketable-limit model: a market order is a FOK/FAK
        order with a worst-acceptable price cap. If ``order_args.price`` is 0
        we default the cap to 0.99 (BUY) / 0.01 (SELL) so the FOK can fill at
        any reasonable price.

        ``order_args.amount`` follows py-clob-client semantics: for a market
        **BUY** it is the **USD notional** to spend (sent as ``amount``; the
        server derives the share count), and for a market **SELL** it is the
        **number of shares** (sent as ``quantity``). ``options`` is accepted
        for py-clob-client signature parity and ignored (no tick-size rounding
        / neg-risk handling on paper).
        """
        side = (order_args.side or "BUY").upper()
        price = float(order_args.price) if order_args.price else (0.99 if side == "BUY" else 0.01)
        tif = order_args.order_type.value if hasattr(order_args.order_type, "value") else "FOK"
        size_kwargs: dict[str, Any] = (
            {"amount": order_args.amount} if side == "BUY" else {"quantity": order_args.amount}
        )
        return self._order_payload(
            token_id=order_args.token_id,
            side=side,
            price=price,
            order_type="market",
            time_in_force=tif,
            **size_kwargs,
        )

    def calculate_market_price(
        self, token_id: str, side: str, amount: float, order_type: Any = None
    ) -> float:
        """adapt — average fill price for ``amount`` by walking the local book.

        Up/Down warning: walks the outcome's synthetic ~0.99 ladder, so the
        result is the cost of the *outcome* token, not the underlying asset.
        """
        bids, asks = _book_sides(self._book_for_token(token_id))
        levels = sorted(asks) if side.upper() == "BUY" else sorted(bids, reverse=True)
        remaining = float(amount)
        spent = 0.0
        filled = 0.0
        for price, size in levels:
            take = min(remaining, size)
            spent += take * price
            filled += take
            remaining -= take
            if remaining <= 0:
                break
        if filled <= 0:
            return 0.0
        return spent / filled

    # ── order submission ───────────────────────────────────────────────────

    def _submit(
        self,
        order: dict[str, Any],
        order_type: OrderType | str | None,
        post_only: bool = False,
    ) -> dict[str, Any]:
        tif = order.get("time_in_force", "GTC")
        if order_type is not None:
            tif = order_type.value if hasattr(order_type, "value") else str(order_type)
        # An embedded expiration means GTD was intended (e.g. a `create_order`
        # with a non-zero `OrderArgs.expiration`). `post_order` / the
        # `create_and_post_order` path default `orderType` to GTC, which would
        # otherwise downgrade the order to a contradictory GTC+expiration shape.
        # Never downgrade an expiring order to GTC.
        if order.get("expiration") and tif.upper() == "GTC":
            tif = "GTD"
        return self._client.place_order(
            market_id=order["market_id"],
            side=order["side"],
            outcome=order.get("outcome", "YES"),
            quantity=order.get("quantity"),
            amount=order.get("amount"),
            order_type=order.get("order_type", "limit"),
            price=order.get("price"),
            time_in_force=tif,
            post_only=post_only or bool(order.get("post_only")),
            expiration=order.get("expiration"),
        )

    def post_order(
        self,
        order: dict[str, Any],
        orderType: OrderType | str = OrderType.GTC,
        post_only: bool = False,
    ) -> dict[str, Any]:
        """adapt — submit an order payload (``POST /v1/orders``).

        ``order`` is the unsigned dict from :meth:`create_order` /
        :meth:`create_market_order`. ``orderType`` overrides the
        time-in-force (GTC/GTD/FOK/FAK). ``post_only`` is forwarded to the
        server as the maker-only flag; the server honours it only when its
        PM-v2 order-semantics flag is enabled (otherwise it is a safe no-op).
        """
        return self._submit(order, orderType, post_only=post_only)

    def post_orders(self, args: list[PostOrdersArgs]) -> list[dict[str, Any]]:
        """adapt — submit a batch (``POST /v1/orders/batch``).

        Maps each :class:`PostOrdersArgs` to a native order body and uses the
        tier-aware batch endpoint; the per-tier cap surfaces as a
        ``ValidationError`` from the server.
        """
        bodies: list[dict[str, Any]] = []
        for a in args:
            order = dict(a.order)
            tif = a.orderType.value if hasattr(a.orderType, "value") else str(a.orderType)
            order["time_in_force"] = tif
            if getattr(a, "postOnly", False):
                order["post_only"] = True
            bodies.append(order)
        return self._client.place_orders(bodies)

    def create_and_post_order(
        self,
        order_args: OrderArgs,
        options: CreateOrderOptions | PartialCreateOrderOptions | None = None,
    ) -> dict[str, Any]:
        """adapt — build + submit a limit order in one call (recommended port path)."""
        return self.post_order(self.create_order(order_args, options), OrderType.GTC)

    # ── cancellation ───────────────────────────────────────────────────────

    def cancel(self, order_id: str) -> dict[str, Any]:
        """mirror — cancel one order (``DELETE /v1/orders/{id}``)."""
        return self._client.cancel_order(order_id)

    def cancel_orders(self, order_ids: list[str]) -> dict[str, Any]:
        """adapt — cancel several orders; loops the single-cancel endpoint."""
        canceled: list[str] = []
        not_canceled: dict[str, str] = {}
        for oid in order_ids:
            try:
                self._client.cancel_order(oid)
                canceled.append(oid)
            except PolyApiException as exc:
                not_canceled[oid] = str(exc)
        return {"canceled": canceled, "not_canceled": not_canceled}

    def cancel_all(self) -> dict[str, Any]:
        """mirror — cancel every open order (``POST /v1/cancel-all``)."""
        return self._client.cancel_all()

    def cancel_market_orders(self, market: str = "", asset_id: str = "") -> dict[str, Any]:
        """adapt — cancel all orders in a market (``DELETE /v1/cancel-market-orders``)."""
        market_id = market or asset_id
        if not market_id:
            raise PolyApiException(400, "cancel_market_orders requires market or asset_id.")
        if asset_id and not market:
            market_id, _ = self._split_token(asset_id)
        return self._client.cancel_market_orders(market_id)

    # ── order / trade reads ────────────────────────────────────────────────

    def get_orders(
        self, params: OpenOrderParams | None = None, next_cursor: str = START_CURSOR
    ) -> list[dict[str, Any]]:
        """adapt — open orders via the Polymarket-shape data API
        (``GET /v1/data/orders``), cursor-walked to completion.

        Filters (``id`` / ``market`` / ``asset_id``) are forwarded to the
        server rather than applied client-side, and the base64 cursor is walked
        until the ``LTE=`` end sentinel — true parity with py-clob-client's
        ``get_orders`` over the data API. ``asset_id`` is forwarded as the raw
        outcome-token id (the data API matches on it natively).
        """
        oid = getattr(params, "id", None) if params is not None else None
        market = getattr(params, "market", None) if params is not None else None
        asset_id = getattr(params, "asset_id", None) if params is not None else None
        cursor: str | None = next_cursor or START_CURSOR
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        while cursor and cursor != END_CURSOR and cursor not in seen:
            seen.add(cursor)
            env = self._client.data_orders(
                id=oid,
                market=market,
                asset_id=asset_id,
                next_cursor=cursor,
                limit=_PAGE_LIMIT,
            )
            out.extend(env.get("data") or [])
            cursor = env.get("next_cursor")
        return out

    def get_order(self, order_id: str) -> dict[str, Any]:
        """adapt — a single order (``GET /v1/orders/{id}``)."""
        return self._client.get_order(order_id)

    def get_trades(
        self, params: TradeParams | None = None, next_cursor: str = START_CURSOR
    ) -> list[dict[str, Any]]:
        """adapt — your fills via the Polymarket-shape data API
        (``GET /v1/data/trades``), cursor-walked to completion.

        Forwards ``market`` / ``asset_id`` / ``before`` / ``after`` server-side
        and walks the base64 cursor until the ``LTE=`` end sentinel. ``asset_id``
        is forwarded as the raw outcome-token id.
        """
        market = getattr(params, "market", None) if params is not None else None
        asset_id = getattr(params, "asset_id", None) if params is not None else None
        before = getattr(params, "before", None) if params is not None else None
        after = getattr(params, "after", None) if params is not None else None
        cursor: str | None = next_cursor or START_CURSOR
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        while cursor and cursor != END_CURSOR and cursor not in seen:
            seen.add(cursor)
            env = self._client.data_trades(
                market=market,
                asset_id=asset_id,
                before=before,
                after=after,
                next_cursor=cursor,
                limit=_PAGE_LIMIT,
            )
            out.extend(env.get("data") or [])
            cursor = env.get("next_cursor")
        return out

    # ── balances / allowances ──────────────────────────────────────────────

    def get_balance_allowance(self, params: BalanceAllowanceParams | None = None) -> dict[str, Any]:
        """adapt — paper balance (``GET /v1/account/balance``).

        py-clob reports on-chain USDC balance + exchange allowance. Paper
        trading has no allowance, so ``allowance`` mirrors ``balance``.
        """
        bal = self._client.balance()
        amount = bal.get("balance") or bal.get("cash") or bal.get("available")
        return {"balance": str(amount) if amount is not None else "0", "allowance": "unlimited"}

    def update_balance_allowance(
        self, params: BalanceAllowanceParams | None = None
    ) -> dict[str, Any]:
        """stub-noop — no on-chain allowance to set in paper trading."""
        return {}

    # ── order scoring (rewards) ────────────────────────────────────────────

    def is_order_scoring(self, params: Any = None) -> dict[str, Any]:
        """stub-noop — no liquidity-rewards scoring in the paper SDK."""
        return {"scoring": False}

    def are_orders_scoring(self, params: Any = None) -> dict[str, Any]:
        """stub-noop — no liquidity-rewards scoring in the paper SDK."""
        return {}

    # ── notifications ──────────────────────────────────────────────────────

    def get_notifications(self) -> dict[str, Any]:
        """stub-noop — CLOB notifications have no analog; empty list."""
        return {"notifications": []}

    def drop_notifications(self, params: Any = None) -> dict[str, Any]:
        """stub-noop — nothing to drop; no-op."""
        return {"success": True}

    # ── misc reads / builder ───────────────────────────────────────────────

    def get_market_trades_events(self, condition_id: str) -> dict[str, Any]:
        """stub-noop — public per-market trade-event feed not exposed; empty."""
        return {"data": []}

    def get_builder_trades(
        self, params: Any = None, next_cursor: str = START_CURSOR
    ) -> list[dict[str, Any]]:
        """stub-noop — builder/maker rewards have no analog; empty list.

        ``next_cursor`` is accepted for py-clob-client signature parity.
        """
        return []

    def post_heartbeat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """stub-noop — no heartbeat needed; the paper API is stateless per call."""
        return {"success": True}
