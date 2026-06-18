"""Spec parity + validation tests for the CORE stream subset.

The mirror's ``polysim_polymarket.streams`` ships only the CORE topics —
``market`` (MarketSpec), ``user`` (UserSpec), ``crypto_prices``
(CryptoPricesSpec). Sports / comments / equity are DEFERRED and intentionally
absent. For every CORE spec the dataclass shape (frozen / slots / kw_only /
fields / the ``topic`` ``init=False`` field) and the ``__post_init__``
validation must match py-sdk byte-for-byte so a ported bot constructs them
identically across the prefix swap.

These load the real ``polymarket`` package (skipped if not installed) and diff
the mirror's specs against it.
"""

from __future__ import annotations

import dataclasses

import pytest

from polysim_polymarket.errors import UserInputError
from polysim_polymarket.streams import (
    CryptoPricesSpec,
    MarketSpec,
    UserSpec,
)

polymarket = pytest.importorskip("polymarket")

from polymarket.streams import CryptoPricesSpec as RealCryptoPricesSpec  # noqa: E402
from polymarket.streams import MarketSpec as RealMarketSpec  # noqa: E402
from polymarket.streams import UserSpec as RealUserSpec  # noqa: E402

_PAIRS = [
    ("MarketSpec", MarketSpec, RealMarketSpec),
    ("UserSpec", UserSpec, RealUserSpec),
    ("CryptoPricesSpec", CryptoPricesSpec, RealCryptoPricesSpec),
]


def _field_shape(cls: type) -> list[tuple[str, bool, bool]]:
    return [(f.name, f.kw_only, f.init) for f in dataclasses.fields(cls)]


@pytest.mark.parametrize(("name", "mirror", "real"), _PAIRS)
def test_dataclass_flags_match(name: str, mirror: type, real: type) -> None:
    assert dataclasses.is_dataclass(mirror), name
    assert mirror.__dataclass_params__.frozen == real.__dataclass_params__.frozen
    assert hasattr(mirror, "__slots__") == hasattr(real, "__slots__")


@pytest.mark.parametrize(("name", "mirror", "real"), _PAIRS)
def test_field_shape_matches(name: str, mirror: type, real: type) -> None:
    assert _field_shape(mirror) == _field_shape(real), name


def test_market_spec_topic_field_is_init_false_constant() -> None:
    spec = MarketSpec(token_ids=["t1"])
    assert spec.topic == "market"
    # topic is init=False — passing it is a TypeError on both sides.
    with pytest.raises(TypeError):
        MarketSpec(token_ids=["t1"], topic="market")  # type: ignore[call-arg]


def test_user_spec_topic_field_is_init_false_constant() -> None:
    assert UserSpec().topic == "user"


def test_crypto_spec_topic_is_a_real_init_field() -> None:
    spec = CryptoPricesSpec(topic="prices.crypto.binance")
    assert spec.topic == "prices.crypto.binance"


# ── MarketSpec validation ────────────────────────────────────────────────


def test_market_spec_normalizes_token_ids_to_tuple() -> None:
    spec = MarketSpec(token_ids=["a", "b"])
    assert spec.token_ids == ("a", "b")
    assert spec.custom_feature_enabled is False


def test_market_spec_rejects_bare_string_token_ids() -> None:
    with pytest.raises(UserInputError):
        MarketSpec(token_ids="abc")  # type: ignore[arg-type]


def test_market_spec_rejects_empty_token_ids() -> None:
    with pytest.raises(UserInputError):
        MarketSpec(token_ids=[])


def test_market_spec_rejects_empty_token_id() -> None:
    with pytest.raises(UserInputError):
        MarketSpec(token_ids=["a", ""])


def test_market_spec_rejects_non_string_token_id() -> None:
    with pytest.raises(UserInputError):
        MarketSpec(token_ids=["a", 1])  # type: ignore[list-item]


def test_market_spec_rejects_non_bool_custom_feature() -> None:
    with pytest.raises(UserInputError):
        MarketSpec(token_ids=["a"], custom_feature_enabled="yes")  # type: ignore[arg-type]


def test_market_spec_is_frozen() -> None:
    spec = MarketSpec(token_ids=["a"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.custom_feature_enabled = True  # type: ignore[misc]


# ── CryptoPricesSpec validation ──────────────────────────────────────────


def test_crypto_spec_accepts_both_topics() -> None:
    assert CryptoPricesSpec(topic="prices.crypto.binance").topic == "prices.crypto.binance"
    assert CryptoPricesSpec(topic="prices.crypto.chainlink").topic == "prices.crypto.chainlink"


def test_crypto_spec_rejects_unknown_topic() -> None:
    with pytest.raises(UserInputError):
        CryptoPricesSpec(topic="prices.crypto.kraken")  # type: ignore[arg-type]


def test_crypto_spec_rejects_bare_string_symbols() -> None:
    with pytest.raises(UserInputError):
        CryptoPricesSpec(topic="prices.crypto.binance", symbols="BTC")  # type: ignore[arg-type]


def test_crypto_spec_rejects_empty_symbol() -> None:
    with pytest.raises(UserInputError):
        CryptoPricesSpec(topic="prices.crypto.binance", symbols=["BTC", ""])


def test_crypto_spec_normalizes_symbols_to_tuple() -> None:
    spec = CryptoPricesSpec(topic="prices.crypto.binance", symbols=["BTC", "ETH"])
    assert spec.symbols == ("BTC", "ETH")


def test_crypto_spec_symbols_default_none() -> None:
    assert CryptoPricesSpec(topic="prices.crypto.binance").symbols is None


# ── UserSpec validation ──────────────────────────────────────────────────


def test_user_spec_markets_default_none() -> None:
    assert UserSpec().markets is None


def test_user_spec_normalizes_markets_to_tuple() -> None:
    assert UserSpec(markets=["0xA", "0xB"]).markets == ("0xA", "0xB")


def test_user_spec_rejects_bare_string_markets() -> None:
    with pytest.raises(UserInputError):
        UserSpec(markets="0xA")  # type: ignore[arg-type]


def test_user_spec_rejects_non_string_market() -> None:
    with pytest.raises(UserInputError):
        UserSpec(markets=["0xA", 1])  # type: ignore[list-item]


def test_user_spec_rejects_bool_market() -> None:
    with pytest.raises(UserInputError):
        UserSpec(markets=[True])  # type: ignore[list-item]


def test_user_spec_empty_markets_normalizes_to_none() -> None:
    # py-sdk: an empty (but non-string) sequence normalizes back to None.
    assert UserSpec(markets=[]).markets is None


# ── shared UserInputError identity ───────────────────────────────────────


def test_specs_reuse_shared_user_input_error() -> None:
    from polysim_polymarket import UserInputError as RootError

    assert RootError is UserInputError
