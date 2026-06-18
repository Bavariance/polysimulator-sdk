"""Open a CORE-topic stream over the matching ``polysim_sdk`` transport.

Each opener binds (the transport generator, the pure adapter, the spec) and
hands them to :func:`run_stream`, returning a ready
:class:`AsyncSubscriptionHandle`. This is the ONE place the stream package
touches a transport, so the per-topic transport choice + the identifier/auth
seams live here, not scattered across the clients.

Topic -> transport mapping:

* **market**        -> :func:`polysim_sdk.ws.aprices_stream`     (JWT WS ``/v1/ws/prices``)
* **crypto_prices** -> :func:`polysim_sdk.sse.aspot_stream`      (public SSE ``/prices/stream``)
* **user**          -> :func:`polysim_sdk.ws.aexecutions_stream` (JWT WS ``/v1/ws/executions``)

SEAMS — documented:

* **Auth handshake (user stream).** ``aexecutions_stream`` mints a short-lived
  WS JWT via the client's ``ws_token()`` (which signs with the client's API
  key) and connects to the authenticated ``/v1/ws/executions`` channel. So the
  user stream is scoped to the account behind the secure client's key — there is
  no per-subscription credential. ``UserSpec.markets`` is applied as a
  client-side filter in the adapter (the channel itself delivers all of the
  account's fills).

* **Market subscription identifiers.** ``MarketSpec.token_ids`` hold the SDK's
  canonical ``condition_id:LABEL`` tokens (the same form ``get_*`` + trading
  use). The backend prices WS subscribes by ``condition_id`` (its ``markets``
  key — token ids are explicitly REJECTED there), so the opener strips the
  ``:LABEL`` suffix from each token via ``_split_condition_id`` (a GENERIC
  last-colon split, so non-binary ``UP``/``DOWN`` labels strip correctly — NOT
  the SDK's ``_split_token``, which only round-trips ``YES``/``NO``) and passes
  the de-duplicated condition ids to ``aprices_stream``. Each delivered frame
  carries per-outcome entries with a ``label`` ("Yes"/"No"/"Up"/"Down") and the
  Polymarket CLOB-NUMERIC ``token_id``. The adapter does NOT filter on that raw
  CLOB digit (it is a different namespace from the SDK token and would match
  nothing); it DERIVES the SDK token ``f"{condition_id}:{LABEL}"`` from the
  frame's top-level ``market_id`` + each outcome's ``label`` and filters that
  against the spec's ``condition_id:LABEL`` token set — so passing the SDK's own
  token ids produces a working, correctly-filtered stream end to end.

* **Crypto source -> topic.** ``CryptoPricesSpec.topic`` selects which RTDS
  source the SSE filters to: ``prices.crypto.binance`` -> ``crypto_source=binance``,
  ``prices.crypto.chainlink`` -> ``crypto_source=chainlink``. ``symbols`` (when
  set) is forwarded as the SSE ``crypto`` filter AND re-checked in the adapter.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from polysim_polymarket.streams._adapt import (
    _split_condition_id,
    adapt_execution_frame,
    adapt_prices_frame,
    adapt_spot_frame,
)
from polysim_polymarket.streams._crypto_events import CryptoPricesEvent
from polysim_polymarket.streams._handle import AsyncSubscriptionHandle
from polysim_polymarket.streams._market_events import MarketEvent
from polysim_polymarket.streams._runner import DEFAULT_QUEUE_SIZE, run_stream
from polysim_polymarket.streams._specs import CryptoPricesSpec, MarketSpec, UserSpec
from polysim_polymarket.streams._user_events import UserEvent

if TYPE_CHECKING:
    from polysim_sdk.aio import AsyncPolySimClient

# Map a crypto topic to the SSE ``crypto_source`` filter value.
_TOPIC_TO_CRYPTO_SOURCE: dict[str, str] = {
    "prices.crypto.binance": "binance",
    "prices.crypto.chainlink": "chainlink",
}


def _condition_ids_for(token_ids: Sequence[str]) -> list[str]:
    """Strip the ``:LABEL`` suffix from each SDK token to its condition id.

    ``MarketSpec.token_ids`` are the SDK's canonical ``condition_id:LABEL``
    tokens, but the backend prices WS subscribes by ``condition_id`` (it
    explicitly REJECTS token ids on its ``markets`` key). We reduce each token to
    its bare condition id via :func:`._adapt._split_condition_id` — a GENERIC
    last-colon split, NOT ``polysim_sdk._shared._split_token`` (which only
    round-trips the binary ``YES``/``NO`` labels and would mis-handle a
    non-binary ``0xCID:UP`` by treating the whole string as the condition id).
    The result is de-duplicated, preserving first-seen order, so UP/DOWN specs
    subscribe the right condition id and the adapter filters by
    ``0xCID:UP``/``0xCID:DOWN`` consistently.
    """
    seen: dict[str, None] = {}
    for token_id in token_ids:
        condition_id = _split_condition_id(token_id)
        seen.setdefault(condition_id, None)
    return list(seen)


def open_market_stream(
    client: AsyncPolySimClient,
    spec: MarketSpec,
    *,
    queue_size: int = DEFAULT_QUEUE_SIZE,
) -> AsyncSubscriptionHandle[MarketEvent]:
    """Open a ``market`` stream over ``ws.aprices_stream`` for ``spec``.

    Subscribes by the condition ids derived from ``spec.token_ids`` (the prices
    WS subscribes by condition id); the adapter then DERIVES each outcome's SDK
    token (``condition_id:LABEL``) from the frame's ``market_id`` + outcome
    ``label`` and filters those against the spec's ``condition_id:LABEL`` token
    set — never the frame's raw CLOB-numeric ``outcomes[].token_id``.
    """
    from polysim_sdk import ws

    source = ws.aprices_stream(client, _condition_ids_for(spec.token_ids))
    return run_stream(
        source=source,
        adapt=adapt_prices_frame,
        spec=spec,
        queue_size=queue_size,
    )


def open_crypto_stream(
    client: AsyncPolySimClient,
    spec: CryptoPricesSpec,
    *,
    queue_size: int = DEFAULT_QUEUE_SIZE,
) -> AsyncSubscriptionHandle[CryptoPricesEvent]:
    """Open a ``crypto_prices`` stream over ``sse.aspot_stream`` for ``spec``."""
    from polysim_sdk import sse

    source = sse.aspot_stream(
        client,
        list(spec.symbols) if spec.symbols is not None else None,
        crypto_source=_TOPIC_TO_CRYPTO_SOURCE.get(spec.topic),
    )
    return run_stream(
        source=source,
        adapt=adapt_spot_frame,
        spec=spec,
        queue_size=queue_size,
    )


def open_user_stream(
    client: AsyncPolySimClient,
    spec: UserSpec,
    *,
    queue_size: int = DEFAULT_QUEUE_SIZE,
) -> AsyncSubscriptionHandle[UserEvent]:
    """Open an authenticated ``user`` stream over ``ws.aexecutions_stream``.

    The JWT minted by ``client.ws_token()`` (signed with the client's API key)
    scopes the channel to that account — this is the auth-handshake seam.
    """
    from polysim_sdk import ws

    source = ws.aexecutions_stream(client)
    return run_stream(
        source=source,
        adapt=adapt_execution_frame,
        spec=spec,
        queue_size=queue_size,
    )


def open_public_stream(
    client: AsyncPolySimClient,
    spec: MarketSpec | CryptoPricesSpec,
    *,
    queue_size: int = DEFAULT_QUEUE_SIZE,
) -> AsyncSubscriptionHandle[MarketEvent] | AsyncSubscriptionHandle[CryptoPricesEvent]:
    """Dispatch a CORE PUBLIC spec (market / crypto) to its opener.

    Shared by ``AsyncPublicClient.subscribe`` and ``AsyncSecureClient.subscribe``
    so the public-topic wiring has exactly one home. A non-public spec raises
    :class:`UserInputError`.
    """
    if isinstance(spec, MarketSpec):
        return open_market_stream(client, spec, queue_size=queue_size)
    if isinstance(spec, CryptoPricesSpec):
        return open_crypto_stream(client, spec, queue_size=queue_size)
    from polysim_polymarket.errors import UserInputError

    raise UserInputError(f"unsupported public subscription type: {type(spec).__name__}")


def open_secure_stream(
    client: AsyncPolySimClient,
    spec: MarketSpec | CryptoPricesSpec | UserSpec,
    *,
    queue_size: int = DEFAULT_QUEUE_SIZE,
) -> (
    AsyncSubscriptionHandle[MarketEvent]
    | AsyncSubscriptionHandle[CryptoPricesEvent]
    | AsyncSubscriptionHandle[UserEvent]
):
    """Dispatch a CORE SECURE spec (market / crypto / user) to its opener.

    Adds the authenticated ``user`` topic on top of the public dispatch — only
    secure clients can open it. A non-core spec raises :class:`UserInputError`.
    """
    if isinstance(spec, UserSpec):
        return open_user_stream(client, spec, queue_size=queue_size)
    return open_public_stream(client, spec, queue_size=queue_size)


__all__ = [
    "open_crypto_stream",
    "open_market_stream",
    "open_public_stream",
    "open_secure_stream",
    "open_user_stream",
]
