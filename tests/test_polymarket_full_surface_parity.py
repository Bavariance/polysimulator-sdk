"""Consolidated full-surface parity + coverage-honesty gate (G7).

This is the single "nothing silently diverged" gate for the whole
``polysim_polymarket`` family. The per-gate parity suites
(``test_polymarket_public_parity`` / ``..._secure_parity`` /
``..._secure_trading_parity`` / ``..._secure_g4_parity`` /
``..._async_public_parity`` / ``..._async_secure_parity`` /
``..._streams_subscribe``) each anchor one slice of the surface. This module ties
them together and adds the axis none of them assert on their own: **coverage
honesty** — that the set of py-sdk methods we do NOT implement is *exactly* the
documented-deferred set, so the test fails if py-sdk grows a method we silently
miss OR if we drop one we claim to provide.

Two axes, for EVERY client we ship (``PublicClient`` / ``AsyncPublicClient`` /
``SecureClient`` / ``AsyncSecureClient``) plus the stream ``subscribe`` surface:

1. **Signature parity** — for every method WE implement, its parameter names +
   kinds + async-ness match the REAL installed ``polymarket`` counterpart's. The
   per-request method lists are *imported* from the existing per-client parity
   modules (reused, not re-derived) and extended with the shared
   ``LIFECYCLE_METHODS`` (``close`` — sync on the sync clients, a coroutine on the
   async ones) so the "EVERY implemented method" claim is literally true; the
   ``_param_signature`` helper is the single shared copy in
   ``tests._parity_helpers``, so this gate can never drift from the per-gate
   anchors.
2. **Coverage honesty** — the py-sdk methods we don't implement on each client,
   computed live by introspection, equal the explicitly-enumerated ``DEFERRED_*``
   set — the deferred categories documented in the README compat matrix,
   enumerated method-by-method here. Any drift — a new py-sdk method, or a
   regressed mirror method — fails here.

Pinned against ``polymarket-client==0.1.0b8`` (the divergence-tracker pin).
"""

from __future__ import annotations

import inspect

import pytest

polymarket = pytest.importorskip("polymarket")

# Real py-sdk clients.
from polymarket.clients.async_public import AsyncPublicClient as RealAsyncPublicClient  # noqa: E402
from polymarket.clients.async_secure import AsyncSecureClient as RealAsyncSecureClient  # noqa: E402
from polymarket.clients.public import PublicClient as RealPublicClient  # noqa: E402
from polymarket.clients.secure import SecureClient as RealSecureClient  # noqa: E402

# Mirror clients.
from polysim_polymarket import (  # noqa: E402
    AsyncPublicClient as MirrorAsyncPublicClient,
)
from polysim_polymarket import (  # noqa: E402
    AsyncSecureClient as MirrorAsyncSecureClient,
)
from polysim_polymarket import (  # noqa: E402
    PublicClient as MirrorPublicClient,
)
from polysim_polymarket import (  # noqa: E402
    SecureClient as MirrorSecureClient,
)

# ── Reuse the per-gate parity anchors (import, never re-derive) ──────────────
# The single source of truth for "which methods does the mirror implement" is the
# per-gate parity suites. Importing their method lists here — and the shared
# ``_param_signature`` from ``tests._parity_helpers`` (the one copy every parity
# suite uses) — means this consolidated gate can never disagree with them.
from tests._parity_helpers import _param_signature  # noqa: E402
from tests.test_polymarket_async_public_parity import (  # noqa: E402
    PHASE1_METHODS as ASYNC_PUBLIC_METHODS,
)
from tests.test_polymarket_public_parity import (  # noqa: E402
    PHASE1_METHODS as PUBLIC_METHODS,
)
from tests.test_polymarket_secure_g4_parity import G4_METHODS  # noqa: E402
from tests.test_polymarket_secure_parity import G2_METHODS  # noqa: E402
from tests.test_polymarket_secure_trading_parity import G3_TRADING_METHODS  # noqa: E402

# The mirror's SecureClient surface is the union of the G2 (reads/account/auth),
# G3 (trading), and G4 (on-chain/rewards/builder) anchors. The async secure twin
# mirrors exactly that set (every per-request method an ``async def``), so the same
# list applies — both clients are checked against their respective py-sdk twin.
SECURE_METHODS = sorted(set(G2_METHODS) | set(G3_TRADING_METHODS) | set(G4_METHODS))

# Lifecycle methods present on EVERY client (sync + async). py-sdk's clients all
# expose ``close`` — SYNC on the sync clients (``PublicClient`` / ``SecureClient``),
# a COROUTINE on the async clients (``AsyncPublicClient`` / ``AsyncSecureClient``).
# The per-gate parity suites anchor the per-request reads/writes but not these, so
# they're folded into every client's checked-method set below — that's what makes
# the gate's "EVERY implemented method" signature/async-flavour claim literally
# true. (``aclose`` is NOT a py-sdk method — it's a mirror-only async-close alias on
# ``AsyncPublicClient`` — so it is not a parity item; its async flavour is checked
# by the async-public adapter suite, and it is the sanctioned mirror-only name in
# ``test_no_mirror_only_surface_methods_beyond_known_conveniences`` below.)
LIFECYCLE_METHODS = ["close"]


def _public_method_names(cls: type) -> set[str]:
    """Public (non-dunder, non-underscore) method names on ``cls``."""
    return {
        name
        for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


# ─────────────────────────────────────────────────────────────────────────────
# Documented-deferred sets (the README compat-matrix "Deferred" rows, made
# machine-checkable). Each name below is a method REAL py-sdk exposes on that
# client that the mirror deliberately does NOT implement yet. These are the
# Gamma/Data reads (events, series, tags, comments, sports, portfolio/positions,
# equity, leaderboards, combo markets, public-profile, search, accounting-export)
# plus the rewards-engine reads that the PUBLIC client doesn't share, and a few
# auth/notification lifecycle methods on the secure clients. If py-sdk grows a
# method we silently miss, or the mirror drops one it claims, the
# ``test_*_coverage_is_exactly_documented_deferred`` cases below fail.
# ─────────────────────────────────────────────────────────────────────────────

# Gamma + Data API reads (markets/events/series/tags/comments/sports/positions/
# portfolio/leaderboards/combo/search) + the accounting-snapshot export. These
# are common to BOTH the sync and async PUBLIC clients.
_PUBLIC_DEFERRED = frozenset(
    {
        # accounting export (CSV download helper)
        "download_accounting_snapshot",
        # gamma event / series / tag / comment / sports reads
        "get_comment_thread",
        "get_event",
        "get_event_live_volumes",
        "get_event_tags",
        "get_market_holders",
        "get_market_tags",
        "get_open_interests",
        "get_portfolio_values",
        "get_public_profile",
        "get_related_tag_resources",
        "get_related_tags",
        "get_series",
        "get_sports",
        "get_sports_market_types",
        "get_tag",
        "get_traded_market_count",
        "list_activity",
        "list_closed_positions",
        "list_combo_markets",
        "list_combo_positions",
        "list_comments",
        "list_comments_by_user_address",
        "list_events",
        "list_market_positions",
        "list_positions",
        "list_series",
        "list_tags",
        "list_teams",
        "list_trader_leaderboard",
        "list_trades",
        "search",
        # builder + rewards-engine reads — the PUBLIC client surfaces these, but
        # the mirror only stubs them on the SECURE client (where a bot's
        # reward-accounting loop lives); on the public client they're deferred.
        "get_builder_volumes",
        "list_builder_leaderboard",
        "list_builder_trades",
        "list_current_rewards",
        "list_market_rewards",
    }
)

# The secure clients implement the builder/rewards stubs (G4), so those names are
# NOT deferred there. What IS deferred on the secure surface is the same Gamma/Data
# read family as the public client (minus the builder/rewards names, which are
# implemented) PLUS two auth/notification lifecycle methods py-sdk's secure client
# adds (``end_authentication`` / ``drop_notifications``).
_SECURE_GAMMA_DATA_DEFERRED = frozenset(
    {
        "download_accounting_snapshot",
        "get_comment_thread",
        "get_event",
        "get_event_live_volumes",
        "get_event_tags",
        "get_market_holders",
        "get_market_tags",
        "get_open_interests",
        "get_portfolio_values",
        "get_public_profile",
        "get_related_tag_resources",
        "get_related_tags",
        "get_series",
        "get_sports",
        "get_sports_market_types",
        "get_tag",
        "get_traded_market_count",
        "list_activity",
        "list_closed_positions",
        "list_combo_markets",
        "list_combo_positions",
        "list_comments",
        "list_comments_by_user_address",
        "list_events",
        "list_market_positions",
        "list_positions",
        "list_series",
        "list_tags",
        "list_teams",
        "list_trader_leaderboard",
        "list_trades",
        "search",
    }
)

# Sync SecureClient: the Gamma/Data family + the two auth/notification lifecycle
# methods. (``end_authentication`` is py-sdk's wallet-auth finaliser; the mirror
# has no wallet auth — paper auth is a single API key. ``drop_notifications`` is
# the notifications-clear write; the mirror ships only the ``get_notifications``
# read.)
_SECURE_DEFERRED = _SECURE_GAMMA_DATA_DEFERRED | frozenset(
    {"end_authentication", "drop_notifications"}
)

# Async SecureClient: everything the sync secure client defers, plus
# ``open_rfq_session`` — py-sdk's async-only RFQ maker-session opener (the mirror
# ships the RFQ *types* but simulates no RFQ session, so the action method is
# deferred; documented in the README's RFQ row).
_ASYNC_SECURE_DEFERRED = _SECURE_DEFERRED | frozenset({"open_rfq_session"})


# Each client paired with: its real twin, its mirror, the implemented-method list
# (the per-gate anchors PLUS the shared ``LIFECYCLE_METHODS``), and its
# documented-deferred set.
_CLIENT_CASES = [
    (
        "PublicClient",
        RealPublicClient,
        MirrorPublicClient,
        [*PUBLIC_METHODS, *LIFECYCLE_METHODS],
        _PUBLIC_DEFERRED,
    ),
    (
        "AsyncPublicClient",
        RealAsyncPublicClient,
        MirrorAsyncPublicClient,
        [*ASYNC_PUBLIC_METHODS, *LIFECYCLE_METHODS],
        _PUBLIC_DEFERRED,
    ),
    (
        "SecureClient",
        RealSecureClient,
        MirrorSecureClient,
        [*SECURE_METHODS, *LIFECYCLE_METHODS],
        _SECURE_DEFERRED,
    ),
    (
        "AsyncSecureClient",
        RealAsyncSecureClient,
        MirrorAsyncSecureClient,
        [*SECURE_METHODS, *LIFECYCLE_METHODS],
        _ASYNC_SECURE_DEFERRED,
    ),
]

# Flattened (client_name, method) params for the per-method signature/async checks.
_SIGNATURE_PARAMS = [
    pytest.param(client_name, real, mirror, method, id=f"{client_name}.{method}")
    for client_name, real, mirror, methods, _deferred in _CLIENT_CASES
    for method in methods
]


# ── 1. Signature + async-ness parity, every implemented method, every client ──


@pytest.mark.parametrize(("client_name", "real", "mirror", "method"), _SIGNATURE_PARAMS)
def test_implemented_method_signature_matches_pysdk(
    client_name: str, real: type, mirror: type, method: str
) -> None:
    """For every method WE implement, the parameter names + kinds match py-sdk's.

    (Annotations/defaults are excluded, exactly as the per-gate anchors do — a
    port is mechanical iff the same call expression binds identically.)
    """
    assert hasattr(real, method), f"py-sdk's {client_name} lacks {method} (pin moved?)"
    assert hasattr(mirror, method), f"mirror's {client_name} is missing {method}"
    real_sig = _param_signature(real, method)
    mirror_sig = _param_signature(mirror, method)
    assert mirror_sig == real_sig, (
        f"{client_name}.{method} signature drift:\n"
        f"  py-sdk: {real_sig}\n"
        f"  mirror: {mirror_sig}"
    )


@pytest.mark.parametrize(("client_name", "real", "mirror", "method"), _SIGNATURE_PARAMS)
def test_implemented_method_async_flavor_matches_pysdk(
    client_name: str, real: type, mirror: type, method: str
) -> None:
    """Each implemented method is a coroutine fn iff py-sdk's same method is.

    A ported ``await client.get_midpoint(...)`` only binds if BOTH sides agree on
    the coroutine/sync flavour — the sync clients have zero coroutines; the async
    clients' per-request reads/writes are ``async def`` while the ``list_*`` reads
    stay sync (returning an ``AsyncPaginator``). Whatever py-sdk does, the mirror
    must match.
    """
    real_is_coro = inspect.iscoroutinefunction(getattr(real, method))
    mirror_is_coro = inspect.iscoroutinefunction(getattr(mirror, method))
    assert mirror_is_coro == real_is_coro, (
        f"{client_name}.{method}: py-sdk coroutine={real_is_coro}, "
        f"mirror coroutine={mirror_is_coro} — flavour must match for the prefix swap"
    )


# ── 2. Coverage honesty: deferred set is EXACTLY the documented set ───────────


@pytest.mark.parametrize(
    ("client_name", "real", "mirror", "deferred"),
    [
        pytest.param(name, real, mirror, deferred, id=name)
        for name, real, mirror, _methods, deferred in _CLIENT_CASES
    ],
)
def test_coverage_is_exactly_documented_deferred(
    client_name: str, real: type, mirror: type, deferred: frozenset[str]
) -> None:
    """The py-sdk methods the mirror does NOT implement == the documented-deferred set.

    Computed live: ``real_surface - mirror_surface``, ignoring the async-only
    ``subscribe`` (its parity is the stream gate below) and mirror-only conveniences
    (``aclose``). If py-sdk grows a method we silently miss, it lands in the
    computed set but not the documented one → ``missing_from_docs`` non-empty →
    FAIL. If the mirror drops a method it claims to provide, the documented set
    over-promises → ``no_longer_deferred`` non-empty → FAIL.
    """
    real_surface = _public_method_names(real)
    mirror_surface = _public_method_names(mirror)
    # ``subscribe`` is the streams gate's concern (checked separately); exclude it
    # from the read/write coverage diff so it doesn't masquerade as "deferred".
    computed_deferred = (real_surface - mirror_surface) - {"subscribe"}

    missing_from_docs = computed_deferred - deferred
    no_longer_deferred = deferred - computed_deferred
    assert not missing_from_docs, (
        f"{client_name}: py-sdk exposes method(s) the mirror neither implements nor "
        f"documents as deferred — coverage dishonesty: {sorted(missing_from_docs)}"
    )
    assert not no_longer_deferred, (
        f"{client_name}: the documented-deferred set names method(s) NOT actually "
        f"missing from the mirror (mirror grew them, or py-sdk dropped them) — stale "
        f"deferral claim: {sorted(no_longer_deferred)}"
    )


def test_no_mirror_only_surface_methods_beyond_known_conveniences() -> None:
    """The mirror exposes no method py-sdk lacks, beyond a tiny known allowlist.

    A mirror-only method would break the prefix swap (a bot writing it can't move
    to real Polymarket). The only sanctioned mirror-only names are ``aclose`` (an
    async-close alias the async clients add for the ``polysim_sdk`` transport)
    and ``subscribe`` (async-only on BOTH py-sdk and the mirror, but the mirror
    keeps it on the same async clients). Anything else is an accidental divergence.
    """
    allowed_mirror_only = {"aclose", "subscribe"}
    for client_name, real, mirror, _methods, _deferred in _CLIENT_CASES:
        extra = _public_method_names(mirror) - _public_method_names(real) - allowed_mirror_only
        assert not extra, (
            f"{client_name}: mirror exposes method(s) absent from py-sdk (breaks the "
            f"prefix swap): {sorted(extra)}"
        )


# ── 3. Stream ``subscribe`` surface ──────────────────────────────────────────

# CORE stream specs the mirror ships; the deferred specs are the
# sports/comments/equity topics + the RtdsSpec base alias.
_MIRROR_STREAM_SPECS = frozenset({"MarketSpec", "CryptoPricesSpec", "UserSpec"})
_DEFERRED_STREAM_SPECS = frozenset(
    {"SportsSpec", "CommentsSpec", "EquityPricesSpec", "RtdsSpec"}
)


def test_subscribe_present_on_async_clients_absent_on_sync() -> None:
    """``subscribe`` is async-only on BOTH py-sdk and the mirror.

    Present on the async public + secure clients (both sides), absent from the
    sync clients (both sides). This is the stream-surface anchor of the prefix
    swap: a ported ``await client.subscribe(spec)`` binds identically.
    """
    for real_async, mirror_async in (
        (RealAsyncPublicClient, MirrorAsyncPublicClient),
        (RealAsyncSecureClient, MirrorAsyncSecureClient),
    ):
        assert hasattr(real_async, "subscribe"), "py-sdk async client lost subscribe (pin moved?)"
        assert hasattr(mirror_async, "subscribe"), "mirror async client is missing subscribe"
        assert inspect.iscoroutinefunction(real_async.subscribe)
        assert inspect.iscoroutinefunction(mirror_async.subscribe)
    for real_sync, mirror_sync in (
        (RealPublicClient, MirrorPublicClient),
        (RealSecureClient, MirrorSecureClient),
    ):
        assert not hasattr(real_sync, "subscribe"), "py-sdk grew a sync subscribe (pin moved?)"
        assert not hasattr(mirror_sync, "subscribe"), "mirror grew a sync subscribe"


def test_subscribe_specs_param_kind_matches_pysdk() -> None:
    """The runtime ``specs`` parameter binds identically (POSITIONAL_OR_KEYWORD).

    py-sdk's runtime ``subscribe`` def takes ``specs`` as a plain
    positional-or-keyword arg (the ``/`` is overload-only); the mirror matches, so
    both ``subscribe(spec)`` and ``subscribe(specs=spec)`` port unchanged.
    """
    for real_async, mirror_async in (
        (RealAsyncPublicClient, MirrorAsyncPublicClient),
        (RealAsyncSecureClient, MirrorAsyncSecureClient),
    ):
        real_param = inspect.signature(real_async.subscribe).parameters["specs"]
        mirror_param = inspect.signature(mirror_async.subscribe).parameters["specs"]
        assert mirror_param.kind == real_param.kind, (
            f"subscribe(specs=) kind drift: py-sdk={real_param.kind}, "
            f"mirror={mirror_param.kind}"
        )


def test_stream_spec_coverage_is_exactly_documented() -> None:
    """The mirror's stream specs == CORE topics; the deferred specs == the
    documented sports/comments/equity + RtdsSpec set.

    Computed live from ``polymarket.streams`` vs ``polysim_polymarket.streams``:
    if py-sdk grows a spec we miss, or we ship a deferred one early, this fails.
    """
    import polymarket.streams as real_streams

    import polysim_polymarket.streams as mirror_streams

    real_specs = {n for n in dir(real_streams) if n.endswith("Spec")}
    mirror_specs = {n for n in dir(mirror_streams) if n.endswith("Spec")}

    assert mirror_specs == _MIRROR_STREAM_SPECS, (
        f"mirror stream-spec surface drift: {sorted(mirror_specs)} "
        f"!= documented {sorted(_MIRROR_STREAM_SPECS)}"
    )
    computed_deferred_specs = real_specs - mirror_specs
    assert computed_deferred_specs == _DEFERRED_STREAM_SPECS, (
        f"deferred stream-spec set drift: computed {sorted(computed_deferred_specs)} "
        f"!= documented {sorted(_DEFERRED_STREAM_SPECS)}"
    )


# ── 4. Self-consistency guards (the test can't silently rot vacuous) ──────────


def test_documented_deferred_sets_are_disjoint_from_implemented() -> None:
    """A method can't be BOTH implemented and documented-deferred.

    Guards against a copy-paste error where a name lands in both the implemented
    list and the deferred set (which would make the coverage check vacuously pass).
    """
    for client_name, _real, _mirror, methods, deferred in _CLIENT_CASES:
        overlap = set(methods) & set(deferred)
        assert not overlap, (
            f"{client_name}: method(s) listed BOTH implemented and deferred: {sorted(overlap)}"
        )


def test_every_documented_deferred_method_really_exists_on_pysdk() -> None:
    """Every name in a deferred set is a REAL py-sdk method (not a typo/phantom).

    A misspelled deferred name would silently never appear in the computed set,
    weakening the coverage gate. Asserting each exists on the real client keeps the
    documented set honest.
    """
    for client_name, real, _mirror, _methods, deferred in _CLIENT_CASES:
        for name in deferred:
            assert hasattr(real, name), (
                f"{client_name}: documented-deferred {name!r} is not a real py-sdk method "
                f"(typo, or py-sdk dropped it — pin moved?)"
            )
