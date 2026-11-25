"""Tests for Pure-Decimal Math Engine & Sanity Guard (`backend/math_engine.py`)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.math_engine import (
    TROY_OUNCE_GRAMS,
    SanityGuardError,
    build_fiat_rates,
    compute_fiat_cross_rate,
    compute_funding_spread,
    compute_gold_prices,
    compute_return_pct,
    loads_decimal_json,
    parse_strict_decimal,
    validate_price_tick,
)


def test_troy_ounce_gold_karat_exact_decimal_math() -> None:
    """Verify exact Troy Ounce (31.1034768g) -> 24K, 21K, 18K gram prices without float drift."""
    assert TROY_OUNCE_GRAMS == Decimal("31.1034768")

    # 100 * 31.1034768 = 3110.34768 USD/oz -> 24K = 100.00, 21K = 87.50, 18K = 75.00
    gold = compute_gold_prices("3110.34768")
    assert gold == {
        "XAU_OZ": "3110.35",
        "GRAM_24K": "100.00",
        "GRAM_21K": "87.50",
        "GRAM_18K": "75.00",
    }

    spot_gold = compute_gold_prices(Decimal("2654.35"))
    assert spot_gold["XAU_OZ"] == "2654.35"
    assert Decimal(spot_gold["GRAM_24K"]) > Decimal(spot_gold["GRAM_21K"]) > Decimal(spot_gold["GRAM_18K"])


def test_fiat_rates_and_cross_rates_decimal_accuracy() -> None:
    """Verify USD Fiat basket and cross-rate calculation with zero floats."""
    raw_rates = {
        "EUR": "0.9500",
        "GBP": "0.7900",
        "TRY": "34.5600",
        "SAR": "3.7500",
        "AED": "3.6725",
        "EGP": "49.6500",
    }
    table = build_fiat_rates(raw_rates)

    assert table["EUR"]["pair_label"] == "EUR/USD"
    assert table["EUR"]["units_per_usd"] == "0.9500"
    assert table["EUR"]["usd_per_unit"] == "1.0526"
    assert table["EUR"]["display_rate"] == "1.0526"

    assert table["SAR"]["pair_label"] == "USD/SAR"
    assert table["SAR"]["display_rate"] == "3.7500"
    assert table["AED"]["display_rate"] == "3.6725"
    assert table["EGP"]["display_rate"] == "49.6500"

    eur_to_sar = compute_fiat_cross_rate("0.9500", "3.7500")
    assert eur_to_sar == Decimal("3.9474")


def test_funding_spread_and_annualized_yield_math() -> None:
    """Verify cross-exchange 8h perpetual funding spread in bps and annualized yield."""
    spread = compute_funding_spread(
        "0.00010000",
        "-0.00005000",
        primary_exchange="Binance",
        secondary_exchange="Bybit",
    )
    assert spread["primary_exchange"] == "binance"
    assert spread["secondary_exchange"] == "bybit"
    assert spread["net_spread"] == "0.00015000"
    assert spread["spread_bps"] == "1.50"
    # 0.00015 * 3 * 365 * 100 = 16.425 -> 16.43%
    assert spread["annualized_spread_pct"] == "16.43"
    assert spread["arbitrage_signal"] == "short_binance_long_bybit"


@pytest.mark.parametrize(
    "bad_input",
    [
        1.05,  # IEEE-754 float strictly banned
        0.0,
        True,  # bool strictly banned
        False,
        "0",
        "0.0000",
        "-1.25",
        "NaN",
        "Infinity",
        "-Infinity",
        "",
        "   ",
        "not-a-number",
    ],
)
def test_sanity_guard_rejects_floats_non_positive_and_nan(bad_input: object) -> None:
    """Reject floats, booleans, <=0, NaN, Infinity, and malformed strings."""
    with pytest.raises(SanityGuardError):
        validate_price_tick(bad_input)  # type: ignore[arg-type]


def test_sanity_guard_rejects_single_tick_anomaly_over_15_pct() -> None:
    """Reject >15% single-tick upward spikes or flash-crash drops while allowing <=15%."""
    # Exact 15% boundaries pass
    assert validate_price_tick("115.00", "100.00") == Decimal("115.00")
    assert validate_price_tick("85.00", "100.00") == Decimal("85.00")

    # >15% upward spike fails
    with pytest.raises(SanityGuardError, match="Single-tick anomaly rejected"):
        validate_price_tick("115.01", "100.00", field_name="BTC")

    # >15% downward crash fails
    with pytest.raises(SanityGuardError, match="Single-tick anomaly rejected"):
        validate_price_tick("84.99", "100.00", field_name="XAU_OZ")


def test_loads_decimal_json_never_creates_floats_and_rejects_nan() -> None:
    """Verify raw JSON numbers decode directly to Decimal and NaN constants fail."""
    parsed = loads_decimal_json('{"price": 2650.45, "count": 3}')
    assert isinstance(parsed["price"], Decimal)
    assert parsed["price"] == Decimal("2650.45")
    assert isinstance(parsed["count"], Decimal)

    with pytest.raises(SanityGuardError):
        loads_decimal_json('{"price": NaN}')

    assert compute_return_pct("100.00", "102.50") == Decimal("2.5000")
    assert parse_strict_decimal("-0.0001", allow_negative=True) == Decimal("-0.0001")
