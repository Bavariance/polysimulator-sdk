"""Behaviour tests for the G4 rewards + scoring **honest stubs** on
``polysim_polymarket.SecureClient``.

The PolySimulator paper rewards *engine* is a separate backend roadmap item, so
every rewards read returns an honest empty value — no fabricated nonzero data:

* ``get_order_scoring`` -> ``False`` (the order scores no rewards on paper)
* ``get_orders_scoring`` -> ``{oid: False for oid in order_ids}``
* the ``list_*`` rewards reads -> empty paginators of the correct element type
* ``get_total_earnings_for_user_for_day`` -> ``()``
* ``get_reward_percentages`` -> ``{}`` (the empty ``RewardsPercentages`` dict)

These methods make no network call (they short-circuit to the honest empty
value), so a bare ``SecureClient`` is enough.
"""

from __future__ import annotations

import pytest

from polysim_polymarket import SecureClient
from polysim_polymarket.models import (
    CurrentReward,
    MarketReward,
    TotalUserEarning,
    UserEarning,
    UserRewardsEarning,
)
from polysim_polymarket.pagination import Paginator


@pytest.fixture
def client() -> SecureClient:
    return SecureClient(api_key="ps_live_test")


# ── scoring ──────────────────────────────────────────────────────────────────


def test_get_order_scoring_is_false(client: SecureClient) -> None:
    """A single order scores no rewards on paper -> False."""
    assert client.get_order_scoring(order_id="abc123") is False


def test_get_orders_scoring_all_false_keyed_by_ids(client: SecureClient) -> None:
    """All ids map to False, keyed by exactly the given ids (order-independent)."""
    ids = ["a", "b", "c"]
    result = client.get_orders_scoring(order_ids=ids)
    assert result == {"a": False, "b": False, "c": False}


def test_get_orders_scoring_empty_input(client: SecureClient) -> None:
    """An empty id list yields an empty dict (no fabricated keys)."""
    assert client.get_orders_scoring(order_ids=[]) == {}


def test_get_orders_scoring_dedupes_like_a_dict(client: SecureClient) -> None:
    """Duplicate ids collapse to one key (dict-comprehension semantics)."""
    assert client.get_orders_scoring(order_ids=["x", "x"]) == {"x": False}


# ── rewards list reads -> empty paginators of the correct element type ────────


def _drain(paginator: Paginator[object]) -> list[object]:
    return list(paginator.iter_items())


def test_list_current_rewards_empty(client: SecureClient) -> None:
    paginator = client.list_current_rewards()
    assert isinstance(paginator, Paginator)
    assert _drain(paginator) == []
    assert paginator.first_page().items == ()
    assert paginator.first_page().has_more is False


def test_list_current_rewards_sponsored_filter_still_empty(client: SecureClient) -> None:
    assert _drain(client.list_current_rewards(sponsored=True)) == []


def test_list_market_rewards_empty(client: SecureClient) -> None:
    paginator = client.list_market_rewards(condition_id="0xcond")
    assert _drain(paginator) == []


def test_list_market_rewards_sponsored_filter_still_empty(client: SecureClient) -> None:
    assert _drain(client.list_market_rewards(condition_id="0xcond", sponsored=False)) == []


def test_list_user_earnings_for_day_empty(client: SecureClient) -> None:
    assert _drain(client.list_user_earnings_for_day(date="2026-06-18")) == []


def test_list_user_earnings_and_markets_config_empty(client: SecureClient) -> None:
    paginator = client.list_user_earnings_and_markets_config(date="2026-06-18")
    assert _drain(paginator) == []


def test_list_user_earnings_and_markets_config_with_filters_empty(client: SecureClient) -> None:
    paginator = client.list_user_earnings_and_markets_config(
        date="2026-06-18",
        no_competition=True,
        order_by="EARNINGS",
        position="MAKER",
        page_size=50,
    )
    assert _drain(paginator) == []


# ── tuple / dict empties ──────────────────────────────────────────────────────


def test_get_total_earnings_for_user_for_day_empty_tuple(client: SecureClient) -> None:
    result = client.get_total_earnings_for_user_for_day(date="2026-06-18")
    assert result == ()
    assert isinstance(result, tuple)


def test_get_reward_percentages_empty_dict(client: SecureClient) -> None:
    """``get_reward_percentages`` returns the honest empty RewardsPercentages dict."""
    result = client.get_reward_percentages()
    assert result == {}
    assert isinstance(result, dict)


# ── no network is touched ────────────────────────────────────────────────────


def test_rewards_reads_make_no_network_call() -> None:
    """Built against an unreachable host, the rewards reads still return empties."""
    client = SecureClient(host="http://127.0.0.1:1", api_key="ps_live_test")
    assert client.get_order_scoring(order_id="x") is False
    assert client.get_orders_scoring(order_ids=["x"]) == {"x": False}
    assert list(client.list_current_rewards().iter_items()) == []
    assert list(client.list_market_rewards(condition_id="0xc").iter_items()) == []
    assert list(client.list_user_earnings_for_day(date="2026-06-18").iter_items()) == []
    assert list(
        client.list_user_earnings_and_markets_config(date="2026-06-18").iter_items()
    ) == []
    assert client.get_total_earnings_for_user_for_day(date="2026-06-18") == ()
    assert client.get_reward_percentages() == {}


# ── element-type sanity: the empty paginators carry the right element type ────


def test_reward_models_are_importable_element_types() -> None:
    """The reward element models exist and are importable (the paginator type params)."""
    for model in (
        CurrentReward,
        MarketReward,
        UserEarning,
        TotalUserEarning,
        UserRewardsEarning,
    ):
        assert isinstance(model, type)
