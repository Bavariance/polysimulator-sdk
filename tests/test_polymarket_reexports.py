"""Top-level re-export tests for ``polysim_polymarket``.

The drop-in premise is that a bot swaps only the import *prefix* —
``from polymarket import OrderBook`` becomes ``from polysim_polymarket import
OrderBook``. py-sdk's top-level ``polymarket`` package re-exports the public
types a bot imports (``PublicClient``, ``OrderBook``, ``Environment``,
``Paginator``, …) so they're reachable straight off the package root. Our mirror
must expose the **Phase-1 subset** of that surface from its own ``__init__`` so
the prefix swap doesn't break on ``from polysim_polymarket import OrderBook``.

These tests assert each Phase-1 public name is importable from the package root,
binds to the right object, and is listed in ``__all__`` (so ``from
polysim_polymarket import *`` and tooling pick it up).
"""

from __future__ import annotations

import polysim_polymarket


def test_public_client_is_reexported() -> None:
    """``PublicClient`` is importable from the package root and is the class."""
    from polysim_polymarket import PublicClient
    from polysim_polymarket.clients.public import PublicClient as _Canonical

    assert PublicClient is _Canonical


def test_read_models_are_reexported() -> None:
    """The Phase-1 read models a bot references are importable from the root.

    A ported bot writes ``OrderBook`` / ``Market`` / ``LastTradePrice`` etc. in
    type hints and ``isinstance`` checks, so each must resolve off the package
    root to the same class the client returns.
    """
    from polysim_polymarket import (
        LastTradePrice,
        LastTradePriceForToken,
        Market,
        OrderBook,
        OrderBookLevel,
        PriceHistoryPoint,
    )
    from polysim_polymarket import models as _models

    assert OrderBook is _models.OrderBook
    assert OrderBookLevel is _models.OrderBookLevel
    assert LastTradePrice is _models.LastTradePrice
    assert LastTradePriceForToken is _models.LastTradePriceForToken
    assert PriceHistoryPoint is _models.PriceHistoryPoint
    assert Market is _models.Market


def test_marketstate_is_not_reexported_from_root() -> None:
    """``MarketState`` must NOT be a package-root export.

    py-sdk does NOT expose ``MarketState`` off the ``polymarket`` root (it lives
    nested under ``polymarket.models.gamma.market``), so re-exporting it here
    would let a bot write ``from polysim_polymarket import MarketState`` that
    breaks the moment it swaps the prefix to ``from polymarket import
    MarketState``. It stays reachable via ``polysim_polymarket.models``.
    """
    import polysim_polymarket
    from polysim_polymarket import models as _models

    assert "MarketState" not in polysim_polymarket.__all__
    assert not hasattr(polysim_polymarket, "MarketState")
    # Still available via the models module (just not promoted to the root).
    assert hasattr(_models, "MarketState")


def test_root_exports_are_subset_of_real_polymarket_root() -> None:
    """Every Phase-1 name the mirror promotes to its root exists on py-sdk's root.

    This is the contract that makes the prefix swap mechanical: ``from
    polysim_polymarket import X`` must keep working as ``from polymarket import
    X``. So the mirror's ``__all__`` (minus the ONE deliberate error-tree
    divergence — we ship the py-clob-client-lineage ``PolyException`` /
    ``PolyApiException`` instead of py-sdk's ``PolymarketError``) must be a
    subset of the real ``polymarket`` package's root attributes.
    """
    import pytest

    polymarket = pytest.importorskip("polymarket")

    import polysim_polymarket

    # The deliberate error-tree divergence documented in __init__.py: the mirror
    # raises the shared py-clob-client error names, not py-sdk's PolymarketError.
    deliberate_divergence = {"PolyException", "PolyApiException"}
    promoted = set(polysim_polymarket.__all__) - deliberate_divergence
    real_root = set(dir(polymarket))
    missing = promoted - real_root
    assert not missing, (
        f"mirror promotes names absent from py-sdk's root (breaks the swap): {missing}"
    )


def test_named_errors_are_reexported() -> None:
    """The three named errors the Phase-1 surface raises re-export off the root.

    ``UserInputError`` / ``InsufficientLiquidityError`` / ``UnexpectedResponseError``
    are the error names py-sdk raises from its market-data surface (and the ones
    this mirror raises). A bot writes ``from polymarket import UserInputError``;
    the prefix swap to ``from polysim_polymarket import UserInputError`` must
    resolve to the same class the client actually raises (the one declared in
    ``polysim_polymarket.errors``), so each must be importable off the package
    root and listed in ``__all__``.
    """
    from polysim_polymarket import (
        InsufficientLiquidityError,
        UnexpectedResponseError,
        UserInputError,
    )
    from polysim_polymarket import errors as _errors

    assert UserInputError is _errors.UserInputError
    assert InsufficientLiquidityError is _errors.InsufficientLiquidityError
    assert UnexpectedResponseError is _errors.UnexpectedResponseError
    for name in (
        "UserInputError",
        "InsufficientLiquidityError",
        "UnexpectedResponseError",
    ):
        assert name in polysim_polymarket.__all__, f"{name} missing from __all__"


def test_pysdk_root_error_names_are_present_on_mirror_root() -> None:
    """py-sdk's Phase-1 root error names are ALL present on the mirror root.

    The prefix swap must keep ``from polymarket import UserInputError`` working
    as ``from polysim_polymarket import UserInputError``. py-sdk exposes its named
    error types off the ``polymarket`` root; the mirror must expose the same names
    off its own root for every error its Phase-1 surface raises.

    The ONLY deliberate divergence is the **base name**: py-sdk's tree is rooted
    at ``PolymarketError``; the mirror's at the py-clob-client-lineage
    ``PolyException`` / ``PolyApiException`` (shared by identity with the v1
    mirror — see ``errors.py``). We carve out ``PolymarketError`` for that reason,
    and we scope the comparison to the named errors the mirror's Phase-1 surface
    actually raises (the write / auth / RFQ / transaction error names py-sdk also
    exposes belong to deferred phases — the honest seam — and are intentionally
    not mirrored yet).
    """
    import pytest

    polymarket = pytest.importorskip("polymarket")

    import polysim_polymarket

    # The named errors py-sdk raises from the Phase-1 market-data READ surface
    # (clob actions + market-order estimate). These are the names the mirror
    # actually raises, so the swap must resolve every one off both roots.
    phase1_error_names = {
        "UserInputError",
        "InsufficientLiquidityError",
        "UnexpectedResponseError",
    }
    real_root = set(dir(polymarket))
    # Sanity: these names really are on py-sdk's root (guards against a stale set
    # if py-sdk renames an error).
    assert phase1_error_names <= real_root, (
        f"test list stale — names absent from py-sdk's root: {phase1_error_names - real_root}"
    )
    mirror_root = set(dir(polysim_polymarket))
    missing = phase1_error_names - mirror_root
    assert not missing, (
        f"mirror root is missing py-sdk Phase-1 error names (breaks the swap): {missing}"
    )


def test_request_and_alias_types_are_reexported() -> None:
    """``PriceRequest`` (a call-input type) + the model type-aliases re-export.

    ``PriceRequest`` is the ``NamedTuple`` a bot constructs to call
    ``get_prices``; ``OrderSide`` / ``PriceHistoryInterval`` are the ``Literal``
    aliases it annotates with — all part of the call-site surface.
    """
    from polysim_polymarket import OrderSide, PriceHistoryInterval, PriceRequest
    from polysim_polymarket import models as _models

    assert PriceRequest is _models.PriceRequest
    assert OrderSide is _models.OrderSide
    assert PriceHistoryInterval is _models.PriceHistoryInterval


def test_environment_is_reexported() -> None:
    """``Environment`` + ``PRODUCTION`` re-export — the host-swap surface.

    The sim->real swap points the client at a different ``Environment``, so a
    bot imports both straight off the root, exactly as on real Polymarket.
    """
    from polysim_polymarket import PRODUCTION, Environment
    from polysim_polymarket.environments import PRODUCTION as _CanonProd
    from polysim_polymarket.environments import Environment as _CanonEnv

    assert Environment is _CanonEnv
    assert PRODUCTION is _CanonProd
    assert isinstance(PRODUCTION, Environment)


def test_errors_are_reexported() -> None:
    """``PolyException`` + ``PolyApiException`` re-export and are exception types.

    A bot wraps calls in ``except PolyException`` — the base of the mirror's
    error tree — so both names must resolve off the package root to the shared
    exception classes (identical to the v1 mirror's, per ``errors.py``).
    """
    from polysim_polymarket import PolyApiException, PolyException
    from polysim_polymarket.errors import PolyApiException as _CanonApi
    from polysim_polymarket.errors import PolyException as _CanonBase

    assert PolyException is _CanonBase
    assert PolyApiException is _CanonApi
    assert issubclass(PolyException, Exception)
    assert issubclass(PolyApiException, PolyException)


def test_pagination_types_are_reexported() -> None:
    """``Page`` + ``Paginator`` re-export — the list-read drive surface.

    ``list_markets`` returns a ``Paginator[Market]`` yielding ``Page[Market]``;
    a bot imports both off the root to type-annotate and drive the iteration.
    """
    from polysim_polymarket import Page, Paginator
    from polysim_polymarket.pagination import Page as _CanonPage
    from polysim_polymarket.pagination import Paginator as _CanonPaginator

    assert Page is _CanonPage
    assert Paginator is _CanonPaginator


def test_async_public_client_is_reexported() -> None:
    """``AsyncPublicClient`` is importable from the package root and is the class.

    py-sdk exports ``AsyncPublicClient`` off the ``polymarket`` root; the prefix
    swap (``from polymarket import AsyncPublicClient`` ->
    ``from polysim_polymarket import AsyncPublicClient``) must resolve to the
    mirror's canonical class.
    """
    from polysim_polymarket import AsyncPublicClient
    from polysim_polymarket.clients.async_public import AsyncPublicClient as _Canonical

    assert AsyncPublicClient is _Canonical


def test_async_paginator_is_reexported() -> None:
    """``AsyncPaginator`` re-export — the async list-read drive surface.

    The async ``list_markets`` returns an ``AsyncPaginator[Market]``; a bot
    imports it off the root to type-annotate and drive ``async for``.
    """
    from polysim_polymarket import AsyncPaginator
    from polysim_polymarket.pagination import AsyncPaginator as _Canon

    assert AsyncPaginator is _Canon


def test_the_prefix_swap_import_line_succeeds() -> None:
    """The exact one-line prefix-swap a bot performs must succeed and type-check.

    This is the regression that the nit fixes: ``from polysim_polymarket import
    OrderBook, Environment, PolyException, Paginator`` used to raise
    ``ImportError`` because only ``PublicClient`` was exported.
    """
    from polysim_polymarket import Environment, OrderBook, Paginator, PolyException

    assert isinstance(OrderBook, type)
    assert isinstance(Environment, type)
    assert isinstance(Paginator, type)
    assert isinstance(PolyException, type) and issubclass(PolyException, Exception)


def test_secure_client_is_reexported() -> None:
    """``SecureClient`` is importable from the package root and is the class.

    py-sdk exports ``SecureClient`` off the ``polymarket`` root; the prefix swap
    (``from polymarket import SecureClient`` -> ``from polysim_polymarket import
    SecureClient``) must resolve to the mirror's canonical class.
    """
    from polysim_polymarket import SecureClient
    from polysim_polymarket.clients.secure import SecureClient as _Canonical

    assert SecureClient is _Canonical


def test_g2_account_models_are_reexported() -> None:
    """The G2 account/auth models a bot references re-export off the root.

    ``ApiKeyCreds`` / ``BalanceAllowance`` / ``OpenOrder`` / ``ClobTrade`` /
    ``Notification`` / ``MakerOrder`` are the return/credential models the secure
    client surfaces; ``AssetType`` is the ``Literal`` a bot annotates with. Each
    must resolve off the package root to the same object the client uses, so the
    prefix swap keeps ``from polymarket import BalanceAllowance`` working.
    """
    from polysim_polymarket import (
        ApiKeyCreds,
        AssetType,
        BalanceAllowance,
        ClobTrade,
        MakerOrder,
        Notification,
        OpenOrder,
    )
    from polysim_polymarket import models as _models

    assert ApiKeyCreds is _models.ApiKeyCreds
    assert BalanceAllowance is _models.BalanceAllowance
    assert ClobTrade is _models.ClobTrade
    assert MakerOrder is _models.MakerOrder
    assert Notification is _models.Notification
    assert OpenOrder is _models.OpenOrder
    assert AssetType is _models.AssetType


def test_g2_root_names_exist_on_real_polymarket_root() -> None:
    """Every G2 name the mirror promotes exists on py-sdk's root (keeps swap safe).

    ``SecureClient`` + the G2 account models the mirror promotes to its root must
    all exist on the real ``polymarket`` root, so ``from polysim_polymarket import
    X`` keeps working as ``from polymarket import X`` after the swap.
    """
    import pytest

    polymarket = pytest.importorskip("polymarket")

    g2_names = {
        "SecureClient",
        "ApiKeyCreds",
        "BalanceAllowance",
        "ClobTrade",
        "Notification",
        "OpenOrder",
        "AssetType",
    }
    real_root = set(dir(polymarket))
    missing = g2_names - real_root
    assert not missing, (
        f"mirror promotes G2 names absent from py-sdk's root (breaks the swap): {missing}"
    )


def test_all_lists_every_phase1_public_name() -> None:
    """``__all__`` lists every Phase-1 public name and each is a real attribute.

    ``__all__`` is the export contract: ``from polysim_polymarket import *`` and
    static tooling rely on it, so every name we re-export must be in it and bind.
    """
    expected = {
        "PublicClient",
        "AsyncPublicClient",
        "AsyncPaginator",
        "OrderBook",
        "OrderBookLevel",
        "LastTradePrice",
        "LastTradePriceForToken",
        "PriceHistoryPoint",
        "Market",
        "OrderSide",
        "PriceHistoryInterval",
        "PriceRequest",
        "Environment",
        "PRODUCTION",
        "PolyException",
        "PolyApiException",
        "UserInputError",
        "InsufficientLiquidityError",
        "UnexpectedResponseError",
        "Page",
        "Paginator",
    }
    all_set = set(polysim_polymarket.__all__)
    assert expected <= all_set, f"missing from __all__: {expected - all_set}"
    for name in polysim_polymarket.__all__:
        assert hasattr(polysim_polymarket, name), f"__all__ lists {name!r} but it is not an attr"
