"""24-Hour Self-Training Accuracy Grading Loop for RatePath (ريت باث).

Grades `assets_bias_24h` forecasts made at T+0 against realized market prices at T+24h
using pure `decimal.Decimal` arithmetic (ZERO floats) and records bilingual (`en`/`ar`)
post-mortem accuracy lessons into Supabase `market_memory` (or local UTF-8 ledger fallback).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from backend.ai_strategist import AnalyzedNewsRecord
from backend.config import (
    LIVE_FEED_TARGET_PATH,
    LOCAL_MEMORY_FALLBACK_PATH,
    SUPABASE_SERVER_USER_AGENT,
    Settings,
)
from backend.math_engine import (
    QUANT_2DP,
    compute_return_pct,
    format_decimal,
    loads_decimal_json,
    parse_strict_decimal,
)

NEUTRAL_DEADBAND_PCT: Decimal = Decimal("0.1500")
EMBEDDING_DIM: int = 768
REMOTE_LIVE_FEED_URL: str = (
    "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/live_feed.json"
)
LIVE_FEED_MAX_LOCAL_AGE_SECONDS: int = 1800

# Map bias asset keys (and common LLM aliases) in `assets_bias_24h` to price snapshot keys
ASSET_PRICE_KEY_MAP: dict[str, str] = {
    "USD": "USD_INDEX_PROXY",
    "DXY": "USD_INDEX_PROXY",
    "DOLLAR": "USD_INDEX_PROXY",
    "EUR": "EUR_USD",
    "EURO": "EUR_USD",
    "GBP": "GBP_USD",
    "POUND": "GBP_USD",
    "XAU": "XAU_OZ",
    "GOLD": "XAU_OZ",
    "BTC": "BTC",
    "BITCOIN": "BTC",
    "CRYPTO": "BTC",
    "ETH": "ETH",
    "ETHEREUM": "ETH",
    "SOL": "SOL",
    "SOLANA": "SOL",
}


async def load_latest_live_feed(
    feed_path: Path = LIVE_FEED_TARGET_PATH,
    *,
    client: httpx.AsyncClient | None = None,
    allow_remote_live_data: bool = True,
    max_local_age_seconds: int = LIVE_FEED_MAX_LOCAL_AGE_SECONDS,
) -> dict[str, Any]:
    """Load fresh `live_feed.json`, pulling from `live-data` branch if the local `main` copy is stale.

    Remote fetch is ONLY attempted when `feed_path == LIVE_FEED_TARGET_PATH` and
    `allow_remote_live_data=True`, keeping unit tests that pass custom `tmp_path` files 100% offline.
    """
    local_data: dict[str, Any] = {}
    local_fresh = False
    if feed_path.exists():
        try:
            local_data = loads_decimal_json(feed_path.read_text(encoding="utf-8"))
            gen_raw = str(local_data.get("meta", {}).get("generated_at", "")).replace("Z", "+00:00")
            if gen_raw:
                gen_dt = datetime.fromisoformat(gen_raw).astimezone(timezone.utc)
                age = datetime.now(timezone.utc) - gen_dt
                age_sec = age.days * 86400 + age.seconds
                local_fresh = age_sec <= max_local_age_seconds
        except Exception:
            local_data = {}

    if local_fresh or not allow_remote_live_data or feed_path != LIVE_FEED_TARGET_PATH:
        return local_data

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10))
    try:
        resp = await http_client.get(
            REMOTE_LIVE_FEED_URL,
            headers={"User-Agent": SUPABASE_SERVER_USER_AGENT},
        )
        if resp.status_code == 200 and resp.text.strip():
            remote_data = loads_decimal_json(resp.text)
            if isinstance(remote_data, dict) and remote_data.get("gold") and remote_data.get("fiat"):
                return remote_data
    except Exception:
        pass
    finally:
        if owns_client:
            await http_client.aclose()

    return local_data


def build_deterministic_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[str]:
    """Generate a deterministic 768-d Decimal string vector without floats for offline/seed mode."""
    seed_bytes = text.encode("utf-8")
    values: list[str] = []
    with localcontext() as ctx:
        ctx.prec = 28
        for idx in range(dim):
            digest = hashlib.sha256(seed_bytes + idx.to_bytes(4, "big")).digest()
            raw_int = int.from_bytes(digest[:4], "big")
            # Map [0, 2^32 - 1] -> [-0.500000, +0.500000] in pure Decimal
            centered = (Decimal(raw_int) / Decimal(4294967295)) - Decimal("0.5")
            values.append(format(centered.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), "f"))
    return values


def extract_price_snapshot(feed_payload: dict[str, Any]) -> dict[str, str]:
    """Extract standardized Decimal price snapshot from `live_feed.json` structure."""
    fiat_rates = feed_payload.get("fiat", {}).get("rates", {})
    gold_prices = feed_payload.get("gold", {}).get("prices", {})
    crypto_assets = feed_payload.get("crypto", {}).get("assets", {})

    eur_usd = parse_strict_decimal(
        fiat_rates["EUR"]["usd_per_unit"], field_name="snapshot.EUR_USD"
    )
    gbp_usd = parse_strict_decimal(
        fiat_rates["GBP"]["usd_per_unit"], field_name="snapshot.GBP_USD"
    )
    # Inverse basket proxy for USD strength against EUR & GBP using pure Decimal
    with localcontext() as ctx:
        ctx.prec = 28
        usd_proxy = (Decimal("1") / eur_usd + Decimal("1") / gbp_usd) / Decimal("2")
        usd_proxy_str = format_decimal(usd_proxy, Decimal("0.0001"))

    return {
        "USD_INDEX_PROXY": usd_proxy_str,
        "EUR_USD": fiat_rates["EUR"]["usd_per_unit"],
        "GBP_USD": fiat_rates["GBP"]["usd_per_unit"],
        "XAU_OZ": gold_prices["XAU_OZ"],
        "BTC": crypto_assets["BTC"]["spot_price"],
        "ETH": crypto_assets["ETH"]["spot_price"],
        "SOL": crypto_assets["SOL"]["spot_price"],
    }


def grade_prediction_24h(
    assets_bias_24h: dict[str, str],
    baseline_prices: dict[str, str],
    realized_prices: dict[str, str],
    *,
    deadband_pct: Decimal = NEUTRAL_DEADBAND_PCT,
) -> dict[str, Any]:
    """Grade 24h directional forecast accuracy using pure Decimal math (ZERO floats)."""
    total_points = Decimal("0")
    graded_count = Decimal("0")
    realized_changes_pct: dict[str, str] = {}
    hits: list[str] = []
    misses: list[str] = []

    for asset, predicted_bias in assets_bias_24h.items():
        asset_norm = str(asset).strip().upper()
        price_key = ASSET_PRICE_KEY_MAP.get(asset_norm, asset_norm)
        if price_key not in baseline_prices or price_key not in realized_prices:
            continue

        pct_move = compute_return_pct(
            baseline_prices[price_key],
            realized_prices[price_key],
            field_name=f"eval.{asset}",
        )
        realized_changes_pct[asset] = format(pct_move, "f")
        graded_count += Decimal("1")

        if pct_move > deadband_pct:
            realized_dir = "bullish"
        elif pct_move < -deadband_pct:
            realized_dir = "bearish"
        else:
            realized_dir = "neutral"

        if predicted_bias == realized_dir:
            total_points += Decimal("100")
            hits.append(f"{asset} ({predicted_bias}: {realized_changes_pct[asset]}%)")
        elif realized_dir == "neutral" or predicted_bias == "neutral":
            # Partial credit when market consolidated inside deadband or neutral call saw mild drift
            total_points += Decimal("50")
            misses.append(
                f"{asset} (predicted {predicted_bias} vs realized {realized_dir} {realized_changes_pct[asset]}%)"
            )
        else:
            misses.append(
                f"{asset} (predicted {predicted_bias} vs realized {realized_dir} {realized_changes_pct[asset]}%)"
            )

    if graded_count == Decimal("0"):
        accuracy_str = "0.00"
    else:
        with localcontext() as ctx:
            ctx.prec = 28
            score = total_points / graded_count
            accuracy_str = format_decimal(score, QUANT_2DP)

    hits_summary = ", ".join(hits) if hits else "none"
    misses_summary = ", ".join(misses) if misses else "none"

    lesson_en = (
        f"24h Self-Evaluation Score: {accuracy_str}/100. "
        f"Accurate calls: [{hits_summary}]. Divergences: [{misses_summary}]. "
        f"Calibration rule: discount consensus headlines when 24h drift stays inside +/-0.15%."
    )
    lesson_ar = (
        f"نتيجة التقييم الذاتي بعد 24 ساعة في ريت باث: {accuracy_str} من 100. "
        f"التوقعات الصائبة: [{hits_summary}]. الانحرافات المسجلة: [{misses_summary}]. "
        f"درس المعايرة المؤسسي: تقليص وزن الأخبار المسعّرة مسبقاً عند بقاء التذبذب ضمن نطاق 0.15%."
    )

    return {
        "accuracy_score": accuracy_str,
        "realized_changes_pct": realized_changes_pct,
        "lesson_en": lesson_en,
        "lesson_ar": lesson_ar,
    }


def normalize_supabase_rest_base(supabase_url: str) -> str:
    """Normalize `SUPABASE_URL` whether provided as project root or `/rest/v1/` URL."""
    cleaned = supabase_url.strip().rstrip("/")
    parsed = urlsplit(cleaned)
    if parsed.scheme and parsed.netloc:
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")
        if path in ("", "/rest/v1"):
            return f"{origin}/rest/v1"
        if path.endswith("/rest/v1"):
            return f"{origin}{path}"
        return f"{origin}{path}/rest/v1"
    return f"{cleaned}/rest/v1"


class MarketMemoryStore:
    """Dual-backend store writing to Supabase PostgREST or UTF-8 local JSON ledger."""

    def __init__(
        self,
        settings: Settings | None = None,
        local_path: Path = LOCAL_MEMORY_FALLBACK_PATH,
    ) -> None:
        self.settings = settings or Settings.load()
        self.local_path = local_path

    def _supabase_headers(self, *, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.settings.supabase_key,
            "User-Agent": SUPABASE_SERVER_USER_AGENT,
            "Content-Type": "application/json",
        }
        # Legacy JWT keys (`eyJ...`) require Bearer Authorization; new opaque `sb_*` keys work with apikey/Bearer
        if not self.settings.supabase_key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {self.settings.supabase_key}"
        else:
            headers["Authorization"] = f"Bearer {self.settings.supabase_key}"
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _read_local_rows(self) -> list[dict[str, Any]]:
        if not self.local_path.exists():
            return []
        raw = self.local_path.read_text(encoding="utf-8")
        if not raw.strip():
            return []
        parsed = loads_decimal_json(raw)
        return parsed if isinstance(parsed, list) else []

    def _write_local_rows(self, rows: list[dict[str, Any]]) -> None:
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
        self.local_path.write_text(serialized + "\n", encoding="utf-8", newline="\n")

    async def upsert_records(self, records: list[dict[str, Any]]) -> int:
        """Idempotently upsert `market_memory` rows by `event_key`."""
        if not records:
            return 0

        if self.settings.has_live_supabase:
            rest_base = normalize_supabase_rest_base(self.settings.supabase_url)
            url = f"{rest_base}/market_memory?on_conflict=event_key"
            headers = self._supabase_headers(prefer="resolution=merge-duplicates,return=minimal")
            async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
                resp = await client.post(
                    url,
                    headers=headers,
                    content=json.dumps(records, ensure_ascii=False, default=str).encode("utf-8"),
                )
                resp.raise_for_status()
            return len(records)

        existing = self._read_local_rows()
        by_key = {row["event_key"]: row for row in existing if "event_key" in row}
        for rec in records:
            by_key[rec["event_key"]] = rec
        self._write_local_rows(list(by_key.values()))
        return len(records)

    async def record_live_predictions(
        self,
        news_items: list[AnalyzedNewsRecord],
        baseline_prices: dict[str, str],
    ) -> int:
        """Store T+0 macro predictions for grading 24 hours later."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows: list[dict[str, Any]] = []
        for item in news_items:
            vec_vals = build_deterministic_embedding(f"{item.en.title} {item.en.macro_thesis}")
            rows.append(
                {
                    "event_key": item.id,
                    "record_type": "live_prediction",
                    "event_timestamp": item.published_at or now_iso,
                    "macro_regime": "live_macro_cycle",
                    "source_name": item.source_name,
                    "source_url": item.source_url,
                    "headline_en": item.en.title,
                    "headline_ar": item.ar.title,
                    "summary_en": item.en.summary,
                    "summary_ar": item.ar.summary,
                    "priced_in_warning": item.priced_in_warning.model_dump(),
                    "assets_bias_24h": item.assets_bias_24h.model_dump(),
                    "baseline_prices": baseline_prices,
                    "evaluation_status": "pending",
                    "embedding": "[" + ",".join(vec_vals) + "]",
                }
            )
        return await self.upsert_records(rows)

    async def evaluate_due_predictions(
        self,
        current_prices: dict[str, str],
        *,
        reference_now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Grade all pending predictions older than 24 hours against `current_prices`."""
        now_utc = reference_now or datetime.now(timezone.utc)
        cutoff = now_utc - timedelta(hours=24)
        evaluated_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        if self.settings.has_live_supabase:
            rest_base = normalize_supabase_rest_base(self.settings.supabase_url)
            cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
            headers = self._supabase_headers()
            async with httpx.AsyncClient(timeout=httpx.Timeout(15)) as client:
                resp = await client.get(
                    f"{rest_base}/market_memory",
                    params={
                        "evaluation_status": "eq.pending",
                        "record_type": "eq.live_prediction",
                        "event_timestamp": f"lte.{cutoff_iso}",
                        "select": "*",
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                due_rows = loads_decimal_json(resp.text)

                graded_results: list[dict[str, Any]] = []
                for row in due_rows:
                    grade = grade_prediction_24h(
                        row["assets_bias_24h"],
                        {k: str(v) for k, v in row["baseline_prices"].items()},
                        current_prices,
                    )
                    patch_payload = {
                        "record_type": "evaluated_lesson",
                        "realized_prices_24h": current_prices,
                        "realized_changes_pct": grade["realized_changes_pct"],
                        "accuracy_score": grade["accuracy_score"],
                        "evaluation_status": "graded",
                        "evaluated_at": evaluated_iso,
                        "lesson_en": grade["lesson_en"],
                        "lesson_ar": grade["lesson_ar"],
                    }
                    patch_resp = await client.patch(
                        f"{rest_base}/market_memory",
                        params={"event_key": f"eq.{row['event_key']}"},
                        headers=headers,
                        content=json.dumps(patch_payload, ensure_ascii=False).encode("utf-8"),
                    )
                    patch_resp.raise_for_status()
                    graded_results.append({**row, **patch_payload})
                return graded_results

        rows = self._read_local_rows()
        graded_local: list[dict[str, Any]] = []
        updated_rows: list[dict[str, Any]] = []

        for row in rows:
            if (
                row.get("evaluation_status") == "pending"
                and row.get("record_type") == "live_prediction"
            ):
                ts_raw = str(row.get("event_timestamp", "")).replace("Z", "+00:00")
                try:
                    event_dt = datetime.fromisoformat(ts_raw).astimezone(timezone.utc)
                except ValueError:
                    event_dt = now_utc

                if event_dt <= cutoff:
                    grade = grade_prediction_24h(
                        row["assets_bias_24h"],
                        {k: str(v) for k, v in row["baseline_prices"].items()},
                        current_prices,
                    )
                    updated = {
                        **row,
                        "record_type": "evaluated_lesson",
                        "realized_prices_24h": current_prices,
                        "realized_changes_pct": grade["realized_changes_pct"],
                        "accuracy_score": grade["accuracy_score"],
                        "evaluation_status": "graded",
                        "evaluated_at": evaluated_iso,
                        "lesson_en": grade["lesson_en"],
                        "lesson_ar": grade["lesson_ar"],
                    }
                    updated_rows.append(updated)
                    graded_local.append(updated)
                    continue
            updated_rows.append(row)

        if graded_local:
            self._write_local_rows(updated_rows)
        return graded_local
