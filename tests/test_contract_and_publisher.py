"""Tests for `shared/contract.schema.json` & Atomic Publisher (`backend/publisher.py`)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from backend.publisher import (
    ContractValidationError,
    publish_live_feed_atomic,
    validate_feed_against_contract,
)


def make_valid_feed_payload() -> dict[str, Any]:
    """Construct a canonical schema-valid RatePath (`ريت باث`) payload."""
    return {
        "meta": {
            "platform": "RatePath",
            "platform_ar": "ريت باث",
            "schema_version": "1.0.0",
            "generated_at": "2026-11-23T00:30:00Z",
            "ai_mode": "deterministic_fallback",
            "status": "healthy",
        },
        "fiat": {
            "base": "USD",
            "updated_at": "2026-11-23T00:30:00Z",
            "source": "open.er-api.com",
            "rates": {
                "EUR": {
                    "units_per_usd": "0.9512",
                    "usd_per_unit": "1.0513",
                    "pair_label": "EUR/USD",
                    "display_rate": "1.0513",
                },
                "GBP": {
                    "units_per_usd": "0.7910",
                    "usd_per_unit": "1.2642",
                    "pair_label": "GBP/USD",
                    "display_rate": "1.2642",
                },
                "TRY": {
                    "units_per_usd": "34.6200",
                    "usd_per_unit": "0.0289",
                    "pair_label": "USD/TRY",
                    "display_rate": "34.6200",
                },
                "SAR": {
                    "units_per_usd": "3.7500",
                    "usd_per_unit": "0.2667",
                    "pair_label": "USD/SAR",
                    "display_rate": "3.7500",
                },
                "AED": {
                    "units_per_usd": "3.6725",
                    "usd_per_unit": "0.2723",
                    "pair_label": "USD/AED",
                    "display_rate": "3.6725",
                },
                "EGP": {
                    "units_per_usd": "49.5500",
                    "usd_per_unit": "0.0202",
                    "pair_label": "USD/EGP",
                    "display_rate": "49.5500",
                },
            },
        },
        "gold": {
            "currency": "USD",
            "troy_ounce_grams": "31.1034768",
            "updated_at": "2026-11-23T00:30:00Z",
            "source": "swissquote_xau_bbo",
            "prices": {
                "XAU_OZ": "2655.40",
                "GRAM_24K": "85.37",
                "GRAM_21K": "74.70",
                "GRAM_18K": "64.03",
            },
        },
        "crypto": {
            "quote_currency": "USD",
            "updated_at": "2026-11-23T00:30:00Z",
            "assets": {
                sym: {
                    "symbol": sym,
                    "spot_price": price,
                    "spot_source": "binance_vision",
                    "funding": {
                        "primary_exchange": "binance",
                        "primary_rate": "0.00010000",
                        "secondary_exchange": "bybit",
                        "secondary_rate": "0.00008000",
                        "net_spread": "0.00002000",
                        "spread_bps": "0.20",
                        "annualized_spread_pct": "2.19",
                        "arbitrage_signal": "short_binance_long_bybit",
                    },
                }
                for sym, price in (("BTC", "94500.00"), ("ETH", "3320.50"), ("SOL", "238.15"))
            },
        },
        "news": [
            {
                "id": "rp-1234567890abcdef",
                "published_at": "2026-11-23T00:15:00Z",
                "source_name": "CNBC",
                "source_url": "https://www.cnbc.com/2026/11/23/fed-rates-macro-outlook.html",
                "en": {
                    "title": "Treasury yields steady as markets weigh Fed liquidity path",
                    "summary": "Institutional desks monitor sovereign debt auctions and inflation expectations.",
                    "macro_thesis": "[Balanced Consolidation] Range-bound USD positioning ahead of PCE print.",
                },
                "ar": {
                    "title": "ريت باث | استقرار عوائد السندات الأمريكية مع ترقب مسار السيولة للفيدرالي",
                    "summary": "تراقب المؤسسات المالية مزادات الديون السيادية وتوقعات التضخم خلال الساعات القادمة.",
                    "macro_thesis": "تمركز متوازن للدولار والذهب الفوري خلال الـ 24 ساعة القادمة.",
                },
                "priced_in_warning": {
                    "active": False,
                    "severity": "low",
                    "en": "Fresh headline flow without acute consensus exhaustion.",
                    "ar": "زخم سعري متجدد دون مؤشرات تسعير مسبق حاد في دفاتر الأوامر.",
                },
                "assets_bias_24h": {
                    "USD": "neutral",
                    "EUR": "neutral",
                    "GBP": "neutral",
                    "XAU": "bullish",
                    "BTC": "bullish",
                    "ETH": "bullish",
                    "SOL": "neutral",
                },
                "confidence_score": 74,
                "auditor_verified": True,
            }
        ],
    }


def test_atomic_publish_writes_utf8_and_cleans_staging(tmp_path: Path) -> None:
    """Verify `live_feed.tmp.json` validation, atomic `os.replace`, and unescaped UTF-8 Arabic."""
    target = tmp_path / "frontend" / "public" / "live_feed.json"
    payload = make_valid_feed_payload()

    written_path = publish_live_feed_atomic(payload, target_path=target)
    assert written_path == target
    assert target.exists()
    assert not target.with_name("live_feed.tmp.json").exists()

    raw_disk_text = target.read_text(encoding="utf-8")
    assert "ريت باث" in raw_disk_text
    assert "استقرار عوائد السندات" in raw_disk_text
    assert "\\u0631" not in raw_disk_text  # ensure_ascii=False verified on disk


def test_contract_rejects_float_zero_missing_currency_and_corrupted_arabic(
    tmp_path: Path,
) -> None:
    """Ensure contract breaches abort atomic swap and preserve prior valid `live_feed.json`."""
    target = tmp_path / "frontend" / "public" / "live_feed.json"
    valid = make_valid_feed_payload()
    publish_live_feed_atomic(valid, target_path=target)
    original_bytes = target.read_bytes()

    # 1. Float injection rejected
    bad_float = copy.deepcopy(valid)
    bad_float["gold"]["prices"]["XAU_OZ"] = 2655.40  # Python float
    with pytest.raises(ContractValidationError, match="Forbidden Python float"):
        publish_live_feed_atomic(bad_float, target_path=target)

    # 2. Zero price string rejected by schema regex
    bad_zero = copy.deepcopy(valid)
    bad_zero["gold"]["prices"]["GRAM_21K"] = "0.00"
    with pytest.raises(ContractValidationError):
        publish_live_feed_atomic(bad_zero, target_path=target)

    # 3. Missing required Fiat currency (`EGP`) rejected
    bad_fiat = copy.deepcopy(valid)
    del bad_fiat["fiat"]["rates"]["EGP"]
    with pytest.raises(ContractValidationError):
        validate_feed_against_contract(bad_fiat)

    # 4. Corrupted non-Arabic text (`???`) in `ar.title` rejected by schema `[\u0600-\u06FF]`
    bad_arabic = copy.deepcopy(valid)
    bad_arabic["news"][0]["ar"]["title"] = "???? Corrupted Mojibake Title ????"
    with pytest.raises(ContractValidationError):
        publish_live_feed_atomic(bad_arabic, target_path=target)

    # Previous live_feed.json remains 100% intact and no .tmp.json leaked
    assert target.read_bytes() == original_bytes
    assert not target.with_name("live_feed.tmp.json").exists()


def test_generated_production_live_feed_complies_with_contract() -> None:
    """If `frontend/public/live_feed.json` exists on disk, verify strict contract & UTF-8 compliance."""
    import json
    from backend.config import LIVE_FEED_TARGET_PATH

    if LIVE_FEED_TARGET_PATH.exists():
        raw_text = LIVE_FEED_TARGET_PATH.read_text(encoding="utf-8")
        assert "ريت باث" in raw_text
        assert "\\u0631" not in raw_text
        payload = json.loads(raw_text)
        validate_feed_against_contract(payload)

