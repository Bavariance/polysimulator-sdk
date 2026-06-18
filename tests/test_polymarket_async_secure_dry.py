"""DRY-guarantee tests: ``async_secure.py`` is pure async wiring — it must REUSE
the shared transport-free modules, never re-declare their business logic.

The whole point of the async twin is DRY mirroring, not rewriting: the sync
``SecureClient`` already holds every method's logic, and ``AsyncSecureClient``
must expose the same surface with ``async def`` + ``await`` REUSING the shared
``_common`` / ``_account`` / ``_trade`` / ``_onchain`` modules verbatim. These
tests assert — by source inspection + by identity — that no guard, order-builder,
response-adapter, price-walk, or NotImplementedError string was copied into
``async_secure.py``.
"""

from __future__ import annotations

import ast
import inspect

from polysim_polymarket.clients import _onchain
from polysim_polymarket.clients import async_secure as async_secure_mod

ASYNC_SECURE_SOURCE = inspect.getsource(async_secure_mod)


def _imported_names(module) -> set[str]:
    """Top-level names imported by a module's source (via ast)."""
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def test_async_secure_imports_the_shared_modules():
    """``async_secure`` imports the shared ``_account`` / ``_trade`` / ``_onchain``.

    These are the SAME modules the sync ``SecureClient`` imports. Their presence
    in the import set is the structural proof the async twin shares the logic
    rather than re-implementing it.
    """
    imported = _imported_names(async_secure_mod)
    assert {"_account", "_trade", "_onchain"}.issubset(imported), (
        f"async_secure must import the shared modules; imported: {sorted(imported)}"
    )


def test_async_secure_composes_async_public_client_for_reads():
    """The reads are delegated to a composed ``AsyncPublicClient`` (not re-read).

    Mirrors how the sync ``SecureClient`` composes ``PublicClient`` — the read
    behaviour is the SAME ``_common`` code path, awaited, with no second copy.
    """
    imported = _imported_names(async_secure_mod)
    assert "AsyncPublicClient" in imported
    assert "AsyncPublicClient(" in ASYNC_SECURE_SOURCE


def test_builder_rfq_not_simulated_strings_are_not_redeclared():
    """The NotImplementedError messages reuse the SHARED ``_onchain`` constants.

    ``async_secure.py`` must reference ``_onchain.BUILDER_NOT_SIMULATED`` /
    ``_onchain.RFQ_NOT_SIMULATED`` — never re-declare the literal strings (which
    would let the sync + async messages drift).
    """
    # The literal text lives ONLY in _onchain; the async module must not inline it.
    assert _onchain.BUILDER_NOT_SIMULATED not in ASYNC_SECURE_SOURCE
    assert _onchain.RFQ_NOT_SIMULATED not in ASYNC_SECURE_SOURCE
    # And it must reference the shared constant by name.
    assert "_onchain.BUILDER_NOT_SIMULATED" in ASYNC_SECURE_SOURCE


def test_address_guard_regex_is_not_redeclared():
    """The 40-hex address guard is NOT re-implemented in ``async_secure``.

    The guard's regex literal + the ``re`` machinery live only in ``_onchain``;
    the async client must call ``_onchain.validate_address`` instead of inlining a
    second regex.
    """
    assert "validate_address" in ASYNC_SECURE_SOURCE
    # No regex literal / re import smuggled into the async client.
    assert "re.compile" not in ASYNC_SECURE_SOURCE
    assert "[0-9a-fA-F]{40}" not in ASYNC_SECURE_SOURCE


def test_order_builder_bodies_are_not_redeclared():
    """The order-builder + response-adapter logic is NOT re-implemented.

    ``async_secure`` must call ``_trade.build_limit_order`` /
    ``_trade.build_market_order`` / ``_trade.paper_order_kwargs`` /
    ``_trade.adapt_order_response`` / ``_trade.adapt_cancel_response`` /
    ``_trade.build_cancel_orders_response`` / ``_trade.validate_cancel_order_ids``
    — never inline the price-walk, the worst-price-cap arithmetic, or the
    OrderResponse parsing.
    """
    for shared_call in (
        "_trade.build_limit_order(",
        "_trade.build_market_order(",
        "_trade.paper_order_kwargs(",
        "_trade.adapt_order_response(",
        "_trade.adapt_cancel_response(",
        "_trade.build_cancel_orders_response(",
        "_trade.validate_cancel_order_ids(",
    ):
        assert shared_call in ASYNC_SECURE_SOURCE, f"async_secure must call {shared_call}"
    # No worst-price-cap default literals re-declared here (they live in _trade).
    assert "0.99" not in ASYNC_SECURE_SOURCE
    assert "DEFAULT_BUY_WORST_PRICE" not in ASYNC_SECURE_SOURCE


def test_onchain_guards_are_not_redeclared():
    """The on-chain combination/amount guards reuse the shared ``_onchain`` fns.

    ``async_secure`` must call ``_onchain.require_exactly_one`` /
    ``_onchain.require_positive_amount`` — never inline the
    ``(condition_id is None) == (legs is None)`` / ``amount <= 0`` checks.
    """
    assert "_onchain.require_exactly_one(" in ASYNC_SECURE_SOURCE
    assert "_onchain.require_positive_amount(" in ASYNC_SECURE_SOURCE
    # The raw guard expressions must NOT appear inline in the async client.
    assert "is None) == (" not in ASYNC_SECURE_SOURCE


def test_paper_outcome_built_via_shared_onchain():
    """The async paper handle reuses ``_onchain.paper_transaction_outcome``.

    ``async_secure`` builds its on-chain returns via ``_onchain.paper_async_handle``
    (which wraps the SAME ``paper_transaction_outcome`` the sync handle uses), so
    the paper outcome is identical regardless of sync vs async — and the async
    client never re-declares the placeholder hash.
    """
    assert "_onchain.paper_async_handle(" in ASYNC_SECURE_SOURCE
    # The async handle's wait() returns the shared outcome (identity proof below).
    sync_outcome = _onchain.paper_sync_handle().wait()
    # PaperAsyncTransactionHandle.wait is a coroutine; build one to compare values.
    async_handle = _onchain.paper_async_handle()
    assert async_handle.transaction_hash == sync_outcome.transaction_hash
    assert async_handle.transaction_id == sync_outcome.transaction_id


def test_account_helpers_are_not_redeclared():
    """The account validation/adaptation reuse the shared ``_account`` fns.

    ``async_secure`` must call ``_account.validate_asset_type`` /
    ``_account.require_nonempty`` / ``_account.adapt_balance_allowance`` /
    ``_account.api_key_ids`` / ``_account.adapt_open_order`` /
    ``_account.adapt_open_orders_page`` / ``_account.adapt_account_trades_page``
    — never inline the USD->base-unit math or the cursor->Page mapping.
    """
    for shared_call in (
        "_account.validate_asset_type(",
        "_account.require_nonempty(",
        "_account.adapt_balance_allowance(",
        "_account.api_key_ids(",
        "_account.adapt_open_order(",
        "_account.adapt_open_orders_page(",
        "_account.adapt_account_trades_page(",
    ):
        assert shared_call in ASYNC_SECURE_SOURCE, f"async_secure must call {shared_call}"
    # No USD->base-unit constant re-declared (it lives in _account).
    assert "USDC_BASE_UNITS_PER_USD" not in ASYNC_SECURE_SOURCE
    assert "1_000_000" not in ASYNC_SECURE_SOURCE


def test_no_live_network_or_chain_imports():
    """``async_secure`` imports NO web3 / eth_account / eth_utils / socket / requests.

    The on-chain methods are paper no-ops; nothing here may pull a real chain /
    HTTP-client dependency. The only transport is the paced
    ``AsyncPolySimClient`` (via the composed ``AsyncPublicClient``).
    """
    forbidden = {
        "web3",
        "eth_account",
        "eth_utils",
        "eth_keys",
        "socket",
        "requests",
        "websockets",
        "websocket",
    }
    imported = _imported_names(async_secure_mod)
    leaked = imported & forbidden
    assert leaked == set(), f"async_secure leaked live network/chain imports: {leaked}"
