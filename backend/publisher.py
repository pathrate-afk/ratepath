"""Atomic Contract-Validated Feed Publisher & Live Cycle Orchestrator for RatePath (ريت باث).

Atomic Write Protocol:
1. Verify payload contains ZERO Python `float` instances.
2. Serialize UTF-8 JSON (`ensure_ascii=False`) to `live_feed.tmp.json` in target directory.
3. Re-read `live_feed.tmp.json` with `encoding="utf-8"` and validate against `shared/contract.schema.json`.
4. Atomically swap `live_feed.tmp.json` -> `frontend/public/live_feed.json` via `os.replace()`.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import jsonschema

from backend.ai_strategist import analyze_macro_news_batch
from backend.config import (
    CONTRACT_SCHEMA_PATH,
    LIVE_FEED_TARGET_PATH,
    Settings,
    enforce_utf8_runtime,
)
from backend.evaluator import MarketMemoryStore, extract_price_snapshot
from backend.telegram_bot import broadcast_feed_to_telegram
from backend.fetchers import (
    CRYPTO_SYMBOLS,
    create_http_client,
    fetch_cross_exchange_funding,
    fetch_crypto_spot,
    fetch_fiat_rates,
    fetch_gold_spot,
    fetch_macro_news,
    utc_now_iso,
)
from backend.math_engine import (
    QUANT_2DP,
    SanityGuardError,
    build_fiat_rates,
    compute_funding_spread,
    compute_gold_prices,
    format_decimal,
    loads_decimal_json,
    validate_price_tick,
)


class ContractValidationError(ValueError):
    """Raised when staged `live_feed.tmp.json` violates `shared/contract.schema.json` or float guard."""


def _assert_zero_floats(node: Any, path: str = "$") -> None:
    """Recursively verify ZERO IEEE-754 floats exist anywhere in the outgoing payload."""
    if isinstance(node, float):
        raise ContractValidationError(
            f"Forbidden Python float {node!r} detected at payload path {path}."
        )
    if isinstance(node, dict):
        for k, v in node.items():
            _assert_zero_floats(v, f"{path}.{k}")
    elif isinstance(node, (list, tuple)):
        for idx, item in enumerate(node):
            _assert_zero_floats(item, f"{path}[{idx}]")


def load_contract_schema(schema_path: Path = CONTRACT_SCHEMA_PATH) -> dict[str, Any]:
    """Load `shared/contract.schema.json` using explicit UTF-8 encoding."""
    raw = schema_path.read_text(encoding="utf-8")
    return json.loads(raw)


def validate_feed_against_contract(
    payload: dict[str, Any],
    *,
    schema_path: Path = CONTRACT_SCHEMA_PATH,
) -> None:
    """Validate payload against ZERO-float rule and Draft 2020-12 JSON Schema."""
    _assert_zero_floats(payload)
    schema = load_contract_schema(schema_path)
    validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        loc = ".".join(str(p) for p in first.absolute_path) or "<root>"
        raise ContractValidationError(
            f"Schema contract violation at '{loc}': {first.message}"
        )


def publish_live_feed_atomic(
    payload: dict[str, Any],
    *,
    target_path: Path = LIVE_FEED_TARGET_PATH,
    schema_path: Path = CONTRACT_SCHEMA_PATH,
) -> Path:
    """Stage to `live_feed.tmp.json`, validate against contract, and atomically `os.replace()`."""
    enforce_utf8_runtime()
    _assert_zero_floats(payload)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name("live_feed.tmp.json")

    try:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())

        # Re-read staged UTF-8 file from disk to verify byte-level serialization & schema compliance
        staged_text = tmp_path.read_text(encoding="utf-8")
        staged_obj = json.loads(staged_text)
        validate_feed_against_contract(staged_obj, schema_path=schema_path)

        os.replace(tmp_path, target_path)
        return target_path
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def _load_previous_baselines(target_path: Path) -> tuple[dict[str, str] | None, str | None, dict[str, str] | None]:
    """Extract previous Fiat, Gold, and Crypto prices if an existing `live_feed.json` is present."""
    if not target_path.exists():
        return None, None, None
    try:
        raw = target_path.read_text(encoding="utf-8")
        prev = loads_decimal_json(raw)
        prev_fiat = {
            code: str(entry["units_per_usd"])
            for code, entry in prev.get("fiat", {}).get("rates", {}).items()
        }
        prev_gold = str(prev["gold"]["prices"]["XAU_OZ"])
        prev_crypto = {
            sym: str(entry["spot_price"])
            for sym, entry in prev.get("crypto", {}).get("assets", {}).items()
        }
        return prev_fiat, prev_gold, prev_crypto
    except Exception:
        return None, None, None


async def execute_live_cycle(
    *,
    target_path: Path = LIVE_FEED_TARGET_PATH,
    schema_path: Path = CONTRACT_SCHEMA_PATH,
    settings: Settings | None = None,
    enforce_previous_tick_guard: bool = False,
    run_evaluator: bool = True,
    broadcast_telegram: bool = True,
) -> dict[str, Any]:
    """Fetch live markets + macro RSS, compute Decimal rates, run Dual-Pass AI, and publish."""
    enforce_utf8_runtime()
    resolved_settings = settings or Settings.load()
    prev_fiat, prev_gold, prev_crypto = (
        _load_previous_baselines(target_path)
        if enforce_previous_tick_guard
        else (None, None, None)
    )

    async with create_http_client() as client:
        fiat_res, gold_res, spot_res, funding_pair, raw_news = await asyncio.gather(
            fetch_fiat_rates(client),
            fetch_gold_spot(client),
            fetch_crypto_spot(client),
            fetch_cross_exchange_funding(client),
            fetch_macro_news(client, max_items=5),
        )

        fiat_rates_table = build_fiat_rates(fiat_res.units_per_usd, prev_fiat)
        gold_prices_table = compute_gold_prices(gold_res.xau_oz_usd, prev_gold)

        primary_venue, secondary_venue = funding_pair
        crypto_assets_table: dict[str, dict[str, Any]] = {}
        for sym in CRYPTO_SYMBOLS:
            prev_sym_price = prev_crypto.get(sym) if prev_crypto else None
            validated_spot = validate_price_tick(
                spot_res.prices[sym],
                prev_sym_price,
                field_name=f"crypto.{sym}.spot_price",
            )
            funding_block = compute_funding_spread(
                primary_venue.rates[sym],
                secondary_venue.rates[sym],
                primary_exchange=primary_venue.exchange,
                secondary_exchange=secondary_venue.exchange,
            )
            crypto_assets_table[sym] = {
                "symbol": sym,
                "spot_price": format_decimal(validated_spot, QUANT_2DP),
                "spot_source": spot_res.source,
                "funding": funding_block,
            }

        market_context = {
            "XAU_OZ": gold_prices_table["XAU_OZ"],
            "GRAM_24K": gold_prices_table["GRAM_24K"],
            "BTC": crypto_assets_table["BTC"]["spot_price"],
            "ETH": crypto_assets_table["ETH"]["spot_price"],
            "SOL": crypto_assets_table["SOL"]["spot_price"],
            "EUR_USD": fiat_rates_table["EUR"]["usd_per_unit"],
            "USD_TRY": fiat_rates_table["TRY"]["units_per_usd"],
            "USD_EGP": fiat_rates_table["EGP"]["units_per_usd"],
        }

        analyzed_news, ai_mode = await analyze_macro_news_batch(
            raw_news,
            market_context,
            settings=resolved_settings,
            client=client,
        )

    generated_at = utc_now_iso()
    feed_payload: dict[str, Any] = {
        "meta": {
            "platform": "RatePath",
            "platform_ar": "ريت باث",
            "schema_version": "1.0.0",
            "generated_at": generated_at,
            "ai_mode": ai_mode,
            "status": "healthy",
        },
        "fiat": {
            "base": "USD",
            "updated_at": fiat_res.updated_at,
            "source": fiat_res.source,
            "rates": fiat_rates_table,
        },
        "gold": {
            "currency": "USD",
            "troy_ounce_grams": "31.1034768",
            "updated_at": gold_res.updated_at,
            "source": gold_res.source,
            "prices": gold_prices_table,
        },
        "crypto": {
            "quote_currency": "USD",
            "updated_at": spot_res.updated_at,
            "assets": crypto_assets_table,
        },
        "news": [item.model_dump() for item in analyzed_news],
    }

    publish_live_feed_atomic(
        feed_payload,
        target_path=target_path,
        schema_path=schema_path,
    )

    # Record T+0 predictions; optionally run 24h evaluator and Telegram broadcast
    memory_store = MarketMemoryStore(settings=resolved_settings)
    snapshot = extract_price_snapshot(feed_payload)
    await memory_store.record_live_predictions(analyzed_news, snapshot)
    if run_evaluator:
        await memory_store.evaluate_due_predictions(snapshot)
    if broadcast_telegram:
        await broadcast_feed_to_telegram(feed_payload, settings=resolved_settings)

    return feed_payload


if __name__ == "__main__":
    enforce_utf8_runtime()
    published = asyncio.run(execute_live_cycle())
    print(
        json.dumps(
            {
                "platform": published["meta"]["platform"],
                "platform_ar": published["meta"]["platform_ar"],
                "generated_at": published["meta"]["generated_at"],
                "ai_mode": published["meta"]["ai_mode"],
                "xau_oz": published["gold"]["prices"]["XAU_OZ"],
                "btc_spot": published["crypto"]["assets"]["BTC"]["spot_price"],
                "news_count": len(published["news"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
