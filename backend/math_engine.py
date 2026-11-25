"""Pure Decimal Financial Math Engine & Sanity Guard for RatePath (ريت باث).

CRITICAL ARCHITECTURAL INVARIANT:
- ZERO IEEE-754 floats are permitted anywhere in this module.
- Passing a Python `float` or `bool` raises `SanityGuardError` immediately.
- Single-tick price deviations > 15%, `<= 0`, `NaN`, or `Infinity` are rejected.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any

TROY_OUNCE_GRAMS: Decimal = Decimal("31.1034768")
KARAT_24_RATIO: Decimal = Decimal("24") / Decimal("24")
KARAT_21_RATIO: Decimal = Decimal("21") / Decimal("24")
KARAT_18_RATIO: Decimal = Decimal("18") / Decimal("24")

MAX_SINGLE_TICK_DEVIATION: Decimal = Decimal("0.15")
MAX_ABS_FUNDING_RATE_8H: Decimal = Decimal("0.05")
FUNDING_INTERVALS_PER_DAY: Decimal = Decimal("3")
DAYS_PER_YEAR: Decimal = Decimal("365")
BPS_MULTIPLIER: Decimal = Decimal("10000")
PCT_MULTIPLIER: Decimal = Decimal("100")

REQUIRED_FIAT_CODES: tuple[str, ...] = ("EUR", "GBP", "TRY", "SAR", "AED", "EGP")
OPTIONAL_FIAT_CODES: tuple[str, ...] = ("SYP",)
INVERSE_QUOTED_FIAT: frozenset[str] = frozenset({"EUR", "GBP"})

QUANT_2DP: Decimal = Decimal("0.01")
QUANT_4DP: Decimal = Decimal("0.0001")
QUANT_8DP: Decimal = Decimal("0.00000001")


class SanityGuardError(ValueError):
    """Raised when market ticks violate Decimal purity, positivity, or anomaly bounds."""


DecimalInput = str | int | Decimal


def _reject_json_constant(constant: str) -> Any:
    """Reject NaN, Infinity, and -Infinity during raw JSON parsing."""
    raise SanityGuardError(f"Forbidden JSON numeric constant encountered: {constant!r}")


def loads_decimal_json(raw_json: str) -> Any:
    """Parse JSON text directly into Decimal instances without ever instantiating floats."""
    if not isinstance(raw_json, str):
        raise SanityGuardError("loads_decimal_json requires a UTF-8 JSON string.")
    try:
        return json.loads(
            raw_json,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise SanityGuardError(f"Malformed JSON payload: {exc}") from exc


def parse_strict_decimal(
    value: DecimalInput,
    *,
    field_name: str = "value",
    allow_zero: bool = False,
    allow_negative: bool = False,
) -> Decimal:
    """Convert string/int/Decimal into validated Decimal while banning floats and NaNs."""
    if isinstance(value, bool) or isinstance(value, float):
        raise SanityGuardError(
            f"[{field_name}] IEEE-754 float/bool forbidden in banking math engine: {value!r}"
        )
    if not isinstance(value, (str, int, Decimal)):
        raise SanityGuardError(
            f"[{field_name}] Unsupported numeric type {type(value).__name__}: {value!r}"
        )

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise SanityGuardError(f"[{field_name}] Empty numeric string is invalid.")
        raw_target: str | int | Decimal = stripped
    else:
        raw_target = value

    try:
        dec = Decimal(raw_target) if not isinstance(raw_target, Decimal) else raw_target
    except (InvalidOperation, ValueError) as exc:
        raise SanityGuardError(
            f"[{field_name}] Invalid Decimal literal {value!r}: {exc}"
        ) from exc

    if dec.is_nan() or dec.is_infinite():
        raise SanityGuardError(f"[{field_name}] NaN or Infinity rejected: {value!r}")

    if not allow_negative and dec < Decimal("0"):
        raise SanityGuardError(f"[{field_name}] Negative value rejected: {dec}")

    if not allow_zero and dec == Decimal("0"):
        raise SanityGuardError(f"[{field_name}] Zero value rejected: {dec}")

    if not allow_negative and not allow_zero and dec <= Decimal("0"):
        raise SanityGuardError(f"[{field_name}] Non-positive value rejected: {dec}")

    return dec


def format_decimal(value: Decimal, quantum: Decimal) -> str:
    """Quantize a Decimal with ROUND_HALF_UP and serialize as plain non-scientific string."""
    with localcontext() as ctx:
        ctx.prec = 28
        quantized = value.quantize(quantum, rounding=ROUND_HALF_UP)
        if quantized == Decimal("0"):
            quantized = abs(quantized)
        return format(quantized, "f")


def validate_price_tick(
    current: DecimalInput,
    previous: DecimalInput | None = None,
    *,
    field_name: str = "price",
    max_deviation: Decimal = MAX_SINGLE_TICK_DEVIATION,
) -> Decimal:
    """Sanity Guard: reject <=0, NaN, float, or >15% single-tick jump/crash anomalies."""
    curr_dec = parse_strict_decimal(
        current, field_name=field_name, allow_zero=False, allow_negative=False
    )

    if previous is not None:
        prev_dec = parse_strict_decimal(
            previous,
            field_name=f"{field_name}.previous",
            allow_zero=False,
            allow_negative=False,
        )
        with localcontext() as ctx:
            ctx.prec = 28
            relative_move = abs(curr_dec - prev_dec) / prev_dec
            if relative_move > max_deviation:
                pct_move = format_decimal(relative_move * PCT_MULTIPLIER, QUANT_2DP)
                limit_pct = format_decimal(max_deviation * PCT_MULTIPLIER, QUANT_2DP)
                raise SanityGuardError(
                    f"[{field_name}] Single-tick anomaly rejected: {pct_move}% move "
                    f"(current={curr_dec}, previous={prev_dec}) exceeds {limit_pct}% guard."
                )

    return curr_dec


def compute_fiat_cross_rate(
    from_units_per_usd: DecimalInput,
    to_units_per_usd: DecimalInput,
    *,
    quantum: Decimal = QUANT_4DP,
) -> Decimal:
    """Compute exact Decimal cross-rate (units of `to` per 1 unit of `from`) via USD leg."""
    from_dec = parse_strict_decimal(from_units_per_usd, field_name="from_units_per_usd")
    to_dec = parse_strict_decimal(to_units_per_usd, field_name="to_units_per_usd")

    with localcontext() as ctx:
        ctx.prec = 28
        cross = to_dec / from_dec
        return cross.quantize(quantum, rounding=ROUND_HALF_UP)


def build_fiat_rates(
    raw_units_per_usd: Mapping[str, DecimalInput],
    previous_units_per_usd: Mapping[str, DecimalInput] | None = None,
) -> dict[str, dict[str, str]]:
    """Validate and compute exact Decimal USD pairs & inverse rates for required + optional Fiat."""
    result: dict[str, dict[str, str]] = {}

    codes_to_process: list[str] = list(REQUIRED_FIAT_CODES)
    for opt_code in OPTIONAL_FIAT_CODES:
        if opt_code in raw_units_per_usd:
            codes_to_process.append(opt_code)

    for code in codes_to_process:
        if code not in raw_units_per_usd:
            raise SanityGuardError(f"Missing required Fiat rate for {code}.")

        prev_val = previous_units_per_usd.get(code) if previous_units_per_usd else None
        units_per_usd = validate_price_tick(
            raw_units_per_usd[code],
            prev_val,
            field_name=f"fiat.{code}",
        )

        with localcontext() as ctx:
            ctx.prec = 28
            usd_per_unit = Decimal("1") / units_per_usd

        units_str = format_decimal(units_per_usd, QUANT_4DP)
        inverse_str = format_decimal(usd_per_unit, QUANT_8DP if units_per_usd >= Decimal("1000") else QUANT_4DP)

        if code in INVERSE_QUOTED_FIAT:
            pair_label = f"{code}/USD"
            display_rate = inverse_str
        else:
            pair_label = f"USD/{code}"
            display_rate = units_str

        result[code] = {
            "units_per_usd": units_str,
            "usd_per_unit": inverse_str,
            "pair_label": pair_label,
            "display_rate": display_rate,
        }

    return result


def compute_gold_prices(
    xau_oz_usd: DecimalInput,
    previous_xau_oz_usd: DecimalInput | None = None,
) -> dict[str, str]:
    """Convert Troy Ounce (31.1034768g) spot USD into 24K, 21K, and 18K gram prices."""
    oz_dec = validate_price_tick(
        xau_oz_usd,
        previous_xau_oz_usd,
        field_name="gold.XAU_OZ",
    )

    with localcontext() as ctx:
        ctx.prec = 28
        gram_24k = (oz_dec / TROY_OUNCE_GRAMS) * KARAT_24_RATIO
        gram_21k = (oz_dec / TROY_OUNCE_GRAMS) * KARAT_21_RATIO
        gram_18k = (oz_dec / TROY_OUNCE_GRAMS) * KARAT_18_RATIO

    return {
        "XAU_OZ": format_decimal(oz_dec, QUANT_2DP),
        "GRAM_24K": format_decimal(gram_24k, QUANT_2DP),
        "GRAM_21K": format_decimal(gram_21k, QUANT_2DP),
        "GRAM_18K": format_decimal(gram_18k, QUANT_2DP),
    }


def compute_funding_spread(
    primary_rate: DecimalInput,
    secondary_rate: DecimalInput,
    *,
    primary_exchange: str = "binance",
    secondary_exchange: str = "bybit",
) -> dict[str, str]:
    """Calculate cross-exchange 8h perpetual funding spread, basis points, and annualized carry."""
    p_rate = parse_strict_decimal(
        primary_rate,
        field_name=f"funding.{primary_exchange}",
        allow_zero=True,
        allow_negative=True,
    )
    s_rate = parse_strict_decimal(
        secondary_rate,
        field_name=f"funding.{secondary_exchange}",
        allow_zero=True,
        allow_negative=True,
    )

    if abs(p_rate) > MAX_ABS_FUNDING_RATE_8H:
        raise SanityGuardError(
            f"[funding.{primary_exchange}] Extreme 8h funding rate {p_rate} exceeds 5% sanity cap."
        )
    if abs(s_rate) > MAX_ABS_FUNDING_RATE_8H:
        raise SanityGuardError(
            f"[funding.{secondary_exchange}] Extreme 8h funding rate {s_rate} exceeds 5% sanity cap."
        )

    p_name = primary_exchange.strip().lower()
    s_name = secondary_exchange.strip().lower()

    with localcontext() as ctx:
        ctx.prec = 28
        net_spread = p_rate - s_rate
        abs_spread = abs(net_spread)
        spread_bps = abs_spread * BPS_MULTIPLIER
        annualized_pct = (
            abs_spread * FUNDING_INTERVALS_PER_DAY * DAYS_PER_YEAR * PCT_MULTIPLIER
        )

    if abs_spread < Decimal("0.00001"):
        signal = "neutral_carry"
    elif p_rate > s_rate:
        signal = f"short_{p_name}_long_{s_name}"
    else:
        signal = f"short_{s_name}_long_{p_name}"

    return {
        "primary_exchange": p_name,
        "primary_rate": format_decimal(p_rate, QUANT_8DP),
        "secondary_exchange": s_name,
        "secondary_rate": format_decimal(s_rate, QUANT_8DP),
        "net_spread": format_decimal(net_spread, QUANT_8DP),
        "spread_bps": format_decimal(spread_bps, QUANT_2DP),
        "annualized_spread_pct": format_decimal(annualized_pct, QUANT_2DP),
        "arbitrage_signal": signal,
    }


def compute_return_pct(
    entry_price: DecimalInput,
    realized_price: DecimalInput,
    *,
    field_name: str = "return_pct",
) -> Decimal:
    """Compute exact Decimal percentage return ((realized - entry) / entry) * 100."""
    entry_dec = parse_strict_decimal(entry_price, field_name=f"{field_name}.entry")
    realized_dec = parse_strict_decimal(realized_price, field_name=f"{field_name}.realized")
    with localcontext() as ctx:
        ctx.prec = 28
        pct = ((realized_dec - entry_dec) / entry_dec) * PCT_MULTIPLIER
        return pct.quantize(QUANT_4DP, rounding=ROUND_HALF_UP)
