"""Serverless Single-Cycle Runner for GitHub Actions (`*/5 * * * *`) — RatePath (ريت باث).

Executes exactly ONE complete cycle per invocation:
1. Fetches live Fiat, Gold (XAU/24K/21K/18K), Crypto Spot & 8h Funding Spreads, and Global RSS.
2. Runs Dual-Pass AI analysis via the 6-key rotator pool (Gemini 1..3 -> Groq 1..3 -> OpenRouter -> Fallback).
3. Atomically validates and writes `frontend/public/live_feed.json` against `shared/contract.schema.json`.
4. Broadcasts deduplicated pure-EN (`?lang=en`) & pure-AR (`?lang=ar`) posts across 4 Telegram channels.
5. Uses Upstash Redis key `ratepath:last_eval_ts` (with safe local JSON fallback) to execute
   `evaluator.py` 24-hour accuracy grading automatically once every 60 minutes (`3600` seconds).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from backend.config import (
    CATALOG_FEED_TARGET_PATH,
    CONTRACT_SCHEMA_PATH,
    LIVE_FEED_TARGET_PATH,
    WORKSPACE_ROOT,
    Settings,
    enforce_utf8_runtime,
)
from backend.evaluator import MarketMemoryStore, extract_price_snapshot
from backend.math_engine import parse_strict_decimal
from backend.publisher import execute_live_cycle
from backend.sources.coinmarketcap import generate_and_publish_catalog_feed
from backend.telegram_bot import broadcast_feed_to_telegram, send_admin_watchdog_alert

LAST_EVAL_REDIS_KEY: str = "ratepath:last_eval_ts"
HOURLY_EVAL_INTERVAL_SECONDS: int = 3600
LOCAL_LAST_EVAL_FALLBACK_PATH: Path = WORKSPACE_ROOT / "backend" / "last_eval_ts.tmp.json"
EPOCH_UTC: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)


def utc_epoch_seconds(now: datetime | None = None) -> int:
    """Compute exact integer UTC Unix epoch seconds with ZERO floating-point operations."""
    dt = now or datetime.now(timezone.utc)
    delta = dt - EPOCH_UTC
    return delta.days * 86400 + delta.seconds


def is_hourly_evaluation_due(
    last_eval_ts: int | None,
    now_ts: int,
    *,
    interval_seconds: int = HOURLY_EVAL_INTERVAL_SECONDS,
) -> bool:
    """Return True if evaluator has never run or >= `interval_seconds` (3600s) have elapsed."""
    if last_eval_ts is None:
        return True
    return (now_ts - last_eval_ts) >= interval_seconds


class HourlyEvaluationGate:
    """Tracks `ratepath:last_eval_ts` in Upstash Redis REST with safe local UTF-8 JSON fallback."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        local_state_path: Path = LOCAL_LAST_EVAL_FALLBACK_PATH,
        interval_seconds: int = HOURLY_EVAL_INTERVAL_SECONDS,
    ) -> None:
        self.settings = settings or Settings.load()
        self.local_state_path = local_state_path
        self.interval_seconds = interval_seconds

    def _read_local_ts(self) -> int | None:
        if not self.local_state_path.exists():
            return None
        try:
            raw = self.local_state_path.read_text(encoding="utf-8")
            if not raw.strip():
                return None
            data = json.loads(raw)
            val = data.get("last_eval_ts")
            return int(val) if val is not None else None
        except Exception:
            return None

    def _write_local_ts(self, epoch_sec: int) -> None:
        self.local_state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "key": LAST_EVAL_REDIS_KEY,
            "last_eval_ts": epoch_sec,
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self.local_state_path.write_text(serialized, encoding="utf-8", newline="\n")

    async def get_last_eval_ts(
        self,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> tuple[int | None, str]:
        """Read `ratepath:last_eval_ts` from Upstash Redis, falling back safely to local file."""
        if self.settings.has_live_upstash:
            owns_client = client is None
            http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10))
            try:
                endpoint = self.settings.upstash_redis_rest_url.rstrip("/")
                resp = await http_client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.upstash_redis_rest_token}",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(["GET", LAST_EVAL_REDIS_KEY], ensure_ascii=False).encode(
                        "utf-8"
                    ),
                )
                resp.raise_for_status()
                raw_result = json.loads(resp.text).get("result")
                if raw_result is None or str(raw_result).strip() == "":
                    return None, "upstash"
                return int(str(raw_result).strip()), "upstash"
            except Exception:
                # Safe fallback to local JSON state if Upstash is temporarily unreachable
                pass
            finally:
                if owns_client:
                    await http_client.aclose()

        return self._read_local_ts(), "local_fallback"

    async def record_eval_ts(
        self,
        epoch_sec: int,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> str:
        """Persist `ratepath:last_eval_ts` to Upstash Redis REST and local fallback."""
        self._write_local_ts(epoch_sec)
        if self.settings.has_live_upstash:
            owns_client = client is None
            http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10))
            try:
                endpoint = self.settings.upstash_redis_rest_url.rstrip("/")
                resp = await http_client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.upstash_redis_rest_token}",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(
                        ["SET", LAST_EVAL_REDIS_KEY, str(epoch_sec)],
                        ensure_ascii=False,
                    ).encode("utf-8"),
                )
                resp.raise_for_status()
                return "upstash"
            except Exception:
                return "local_fallback"
            finally:
                if owns_client:
                    await http_client.aclose()
        return "local_fallback"


async def run_single_cycle(
    *,
    settings: Settings | None = None,
    target_path: Path = LIVE_FEED_TARGET_PATH,
    catalog_target_path: Path = CATALOG_FEED_TARGET_PATH,
    schema_path: Path = CONTRACT_SCHEMA_PATH,
    eval_gate: HourlyEvaluationGate | None = None,
    memory_store: MarketMemoryStore | None = None,
    cycle_executor: Any = None,
    catalog_generator: Any = None,
    telegram_broadcaster: Any = None,
    http_client: httpx.AsyncClient | None = None,
    now_utc: datetime | None = None,
    lane1_fast_pricing: bool = True,
) -> dict[str, Any]:
    """Run 1 complete Lane-1 fast cycle (<5s, zero LLM, Agent 7 Price Guardian) + catalog + hourly eval gate."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    gate = eval_gate or HourlyEvaluationGate(settings=cfg)
    store = memory_store or MarketMemoryStore(settings=cfg)
    now_dt = now_utc or datetime.now(timezone.utc)
    now_ts = utc_epoch_seconds(now_dt)

    try:
        # 1. Execute market + RSS + Agent 7 Price Guardian + atomic `live_feed.json` write
        if cycle_executor is not None:
            feed_payload = await cycle_executor()
        else:
            feed_payload = await execute_live_cycle(
                target_path=target_path,
                schema_path=schema_path,
                settings=cfg,
                run_evaluator=False,
                broadcast_telegram=False,
                use_llm=not lane1_fast_pricing,
            )

        # 1b. Build lightweight `catalog_feed.json` (155+ World FX + SP-Today SYP + Silver + Top-300 CMC)
        catalog_meta: dict[str, Any] = {}
        if catalog_generator is not None:
            catalog_payload = await catalog_generator(feed_payload)
            catalog_meta = catalog_payload.get("meta", {}) if isinstance(catalog_payload, dict) else {}
        elif cycle_executor is None:
            try:
                gold_oz = parse_strict_decimal(
                    feed_payload["gold"]["prices"]["XAU_OZ"], field_name="catalog.gold_oz"
                )
                catalog_payload = await generate_and_publish_catalog_feed(
                    gold_oz_usd=gold_oz,
                    settings=cfg,
                    target_path=catalog_target_path,
                    client=http_client,
                )
                catalog_meta = catalog_payload.get("meta", {})
            except Exception as cat_exc:
                catalog_meta = {"error": f"{type(cat_exc).__name__}: {cat_exc}"}

        # 2. Deduplicated 4-channel EN/AR Telegram broadcast
        if telegram_broadcaster is not None:
            tg_summary = await telegram_broadcaster(feed_payload)
        else:
            tg_summary = await broadcast_feed_to_telegram(
                feed_payload, settings=cfg, client=http_client
            )

        # 3. Hourly 24h Self-Evaluation Gate via Upstash `ratepath:last_eval_ts`
        last_ts, gate_backend = await gate.get_last_eval_ts(client=http_client)
        eval_due = is_hourly_evaluation_due(
            last_ts, now_ts, interval_seconds=gate.interval_seconds
        )

        graded_count = 0
        next_eval_in_seconds = 0
        if eval_due:
            snapshot = extract_price_snapshot(feed_payload)
            graded_rows = await store.evaluate_due_predictions(
                snapshot, reference_now=now_dt
            )
            graded_count = len(graded_rows)
            gate_backend = await gate.record_eval_ts(now_ts, client=http_client)
            next_eval_in_seconds = gate.interval_seconds
        else:
            assert last_ts is not None
            next_eval_in_seconds = max(0, gate.interval_seconds - (now_ts - last_ts))

        return {
            "status": "ok",
            "platform": feed_payload["meta"]["platform"],
            "platform_ar": feed_payload["meta"]["platform_ar"],
            "generated_at": feed_payload["meta"]["generated_at"],
            "ai_mode": feed_payload["meta"]["ai_mode"],
            "ai_keys_active": cfg.active_ai_key_counts["total"],
            "xau_oz": feed_payload["gold"]["prices"]["XAU_OZ"],
            "btc_spot": feed_payload["crypto"]["assets"]["BTC"]["spot_price"],
            "news_count": len(feed_payload["news"]),
            "catalog_fiat_count": catalog_meta.get("fiat_count", 0),
            "catalog_crypto_count": catalog_meta.get("crypto_count", 0),
            "catalog_syp_source": catalog_meta.get("syp_source", ""),
            "catalog_cmc_source": catalog_meta.get("cmc_source", ""),
            "telegram": tg_summary,
            "evaluator_ran": eval_due,
            "graded_count": graded_count,
            "eval_gate_backend": gate_backend,
            "next_eval_in_seconds": next_eval_in_seconds,
            "live_feed_path": str(target_path),
            "catalog_feed_path": str(catalog_target_path),
        }
    except Exception as exc:
        await send_admin_watchdog_alert(
            "GitHub Actions Single-Cycle Failure",
            f"{type(exc).__name__}: {exc}",
            settings=cfg,
            client=http_client,
        )
        raise


if __name__ == "__main__":
    enforce_utf8_runtime()
    summary = asyncio.run(run_single_cycle())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
