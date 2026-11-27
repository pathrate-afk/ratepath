"""Daily Pre-Publish Lab, Adversarial Readiness Gauntlet & Self-Calibration (`30 5 * * *`).

Runs strictly in DRY-RUN mode for adversarial drills (zero public Telegram channel spam):
1. 24h Post-Mortem Grader: Grades yesterday's predictions, dynamically calibrates source
   trust weights in Upstash Redis (`ratepath:source_trust_weights`), and stores 1-line
   mistake-prevention lessons (`record_type='calibration_lesson'`) into Supabase.
2. Adversarial Readiness Gauntlet (Dry-Run):
   (a) Synthetic 9% corrupt single-source XAU price spike -> Agent 7 must reject & Orchestrator silences.
   (b) Synthetic fake social hype vs. DefiLlama TVL drain -> Orchestrator must flag `RETAIL_TRAP`.
   (c) Blind historical crisis replay -> Agent 6 + Auditor must ground match & score >= 75 conviction.
3. Multi-LLM Latency Benchmark: Measures Gemini, Groq & OpenRouter response times in pure integer ms.
4. 90-Day Transient Cleanup: Prunes graded transient rows >90 days old (preserving historical anchors).
5. Admin Morning Scorecard: Sends concise readiness report privately to `ADMIN_CHAT_ID`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from backend.agents.contracts import AgentReport, utc_now_iso
from backend.agents.crypto_solvency import evaluate_solvency_telemetry
from backend.agents.orchestrator import (
    DEFAULT_TRUST_WEIGHTS,
    SOURCE_TRUST_WEIGHTS_REDIS_KEY,
    evaluate_swarm_reports,
    load_dynamic_trust_weights,
)
from backend.agents.price_guardian import evaluate_screen_vs_real_price
from backend.ai_strategist import (
    OPENROUTER_PRIMARY_MODEL,
    MultiProviderRotator,
    _call_openai_compatible_json,
    probe_provider_via_rotator,
)
from backend.config import (
    SUPABASE_SERVER_USER_AGENT,
    LIVE_FEED_TARGET_PATH,
    Settings,
    enforce_utf8_runtime,
    is_placeholder_secret,
)
from backend.evaluator import (
    MarketMemoryStore,
    build_deterministic_embedding,
    extract_price_snapshot,
    load_latest_live_feed,
    normalize_supabase_rest_base,
)
from backend.math_engine import loads_decimal_json, parse_strict_decimal
from backend.subscription_lifecycle import run_subscription_lifecycle_check
from backend.telegram_bot import send_admin_watchdog_alert


def _elapsed_ms(start: datetime, end: datetime) -> int:
    """Compute exact integer elapsed milliseconds without floats."""
    delta = end - start
    return max(0, (delta.days * 86400 + delta.seconds) * 1000 + (delta.microseconds // 1000))


def calibrate_source_trust_weights(
    current_weights: dict[str, int],
    avg_accuracy_pct: Decimal,
) -> dict[str, int]:
    """Dynamically adjust source trust weights using pure Decimal & integer bounds."""
    updated = dict(current_weights)
    if avg_accuracy_pct >= Decimal("75.00"):
        for k in ("agent_4_macro_life_filter", "agent_5_crypto_solvency", "agent_8_quant_macro"):
            updated[k] = min(100, int(updated.get(k, 90)) + 2)
    elif avg_accuracy_pct < Decimal("55.00"):
        # Down-weight crowd chatter when 24h post-mortem accuracy dips below 55%
        for k in ("agent_1_social_pulse", "agent_2_search_trends", "agent_3_telegram_scanner"):
            updated[k] = max(10, int(updated.get(k, 20)) - 3)
        for k in ("agent_4_macro_life_filter", "agent_5_crypto_solvency", "agent_8_quant_macro"):
            updated[k] = min(100, int(updated.get(k, 90)) + 3)
    return updated


async def save_source_trust_weights(
    weights: dict[str, int],
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Persist calibrated source trust weights to Upstash Redis."""
    if not settings.has_live_upstash:
        return False
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
    try:
        endpoint = settings.upstash_redis_rest_url.rstrip("/")
        resp = await http_client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {settings.upstash_redis_rest_token}",
                "Content-Type": "application/json",
            },
            content=json.dumps(
                ["SET", SOURCE_TRUST_WEIGHTS_REDIS_KEY, json.dumps(weights, ensure_ascii=False)],
                ensure_ascii=False,
            ).encode("utf-8"),
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False
    finally:
        if owns_client:
            await http_client.aclose()


async def store_calibration_lesson(
    lesson_en: str,
    lesson_ar: str,
    accuracy_score_str: str | None,
    settings: Settings,
    *,
    store: MarketMemoryStore | None = None,
) -> str:
    """Store 1-line daily calibration lesson (`record_type='calibration_lesson'`) into Supabase."""
    mem = store or MarketMemoryStore(settings=settings)
    today_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    vec = build_deterministic_embedding(f"calibration_lesson {lesson_en}")
    base_record: dict[str, Any] = {
        "event_key": f"calibration-lesson-{today_tag}",
        "record_type": "calibration_lesson",
        "event_timestamp": utc_now_iso(),
        "macro_regime": "calibration_lesson",
        "source_name": "RatePath Daily Lab",
        "source_url": "https://ratepath.io/#calibration",
        "headline_en": f"Daily Calibration Lesson ({today_tag})",
        "headline_ar": f"درس المعايرة اليومي ({today_tag})",
        "summary_en": lesson_en,
        "summary_ar": lesson_ar,
        "priced_in_warning": {"active": False, "severity": "low", "en": lesson_en, "ar": lesson_ar},
        "assets_bias_24h": {"USD": "neutral", "XAU": "neutral", "BTC": "neutral"},
        "baseline_prices": {},
        "evaluation_status": "graded",
        "accuracy_score": accuracy_score_str,
        "evaluated_at": utc_now_iso(),
        "lesson_en": lesson_en,
        "lesson_ar": lesson_ar,
        "embedding": "[" + ",".join(vec) + "]",
    }
    try:
        await mem.upsert_records([base_record])
        return "calibration_lesson"
    except httpx.HTTPStatusError as exc:
        # Backward-compatible fallback if live Supabase CHECK constraint has not been migrated yet
        if exc.response.status_code in (400, 409) and "23514" in exc.response.text:
            fallback_record = {**base_record, "record_type": "evaluated_lesson"}
            await mem.upsert_records([fallback_record])
            return "evaluated_lesson_compat"
        raise


async def cleanup_old_transient_rows(
    settings: Settings,
    *,
    retention_days: int = 90,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Delete transient graded rows older than 90 days (NEVER deletes `historical_anchor` rows)."""
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    if not settings.has_live_supabase:
        return {"ran": False, "cutoff": cutoff_iso, "reason": "placeholder_supabase"}

    rest_base = normalize_supabase_rest_base(settings.supabase_url)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    try:
        resp = await http_client.delete(
            f"{rest_base}/market_memory",
            params={
                "evaluation_status": "eq.graded",
                "record_type": "neq.historical_macro_twin",
                "created_at": f"lt.{cutoff_iso}",
            },
            headers={
                "apikey": settings.supabase_key,
                "User-Agent": SUPABASE_SERVER_USER_AGENT,
                "Authorization": f"Bearer {settings.supabase_key}",
                "Prefer": "return=minimal",
            },
        )
        return {"ran": resp.status_code in (200, 204), "status": resp.status_code, "cutoff": cutoff_iso}
    except Exception as exc:
        return {"ran": False, "cutoff": cutoff_iso, "error": type(exc).__name__}
    finally:
        if owns_client:
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Adversarial Readiness Gauntlet (Strict Dry-Run Only)
# ---------------------------------------------------------------------------


async def run_adversarial_gauntlet(
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run all 3 dry-run adversarial drills verifying Price Guardian, RETAIL_TRAP, and Crisis Replay."""
    cfg = settings or Settings.load()

    # Drill A: Synthetic 9.00% corrupt single-source gold spike -> MUST reject & silence
    verdict_a = evaluate_screen_vs_real_price(
        screen_price="3890.00",
        real_benchmark_price="3568.81",
        asset_label="XAU_OZ",
        screen_source="corrupt_screen_feed",
        benchmark_source="binance_vision_paxg",
    )
    decision_a = await evaluate_swarm_reports(
        [verdict_a.report],
        settings=cfg,
        use_llm=False,
        dry_run=True,
    )
    drill_a_pass = bool(
        verdict_a.corrupt_spike_rejected
        and verdict_a.accepted_price == Decimal("3568.81")
        and not decision_a.publish_allowed
    )

    # Drill B: Fake retail hype vs. DefiLlama TVL drain & USDC depeg -> MUST flag RETAIL_TRAP
    hype_report = AgentReport(
        agent_id="agent_1_social_pulse",
        tier="crowd_chatter",
        healthy=True,
        signal_direction="euphoria",
        confidence=85,
        headline="Reddit parabolic altcoin moon FOMO surge!",
        flags=["RETAIL_EUPHORIA"],
    )
    drain_report = evaluate_solvency_telemetry(
        tvl_change_24h_pct=Decimal("-8.40"),
        stablecoin_pegs={"USDC": Decimal("0.9750"), "USDT": Decimal("0.9995")},
        btc_quote_volume_usd=Decimal("5200000000"),
        btc_24h_change_pct=Decimal("-4.50"),
    )
    decision_b = await evaluate_swarm_reports(
        [hype_report, drain_report],
        settings=cfg,
        use_llm=False,
        dry_run=True,
    )
    drill_b_pass = bool(
        decision_b.retail_trap_flagged
        and decision_b.auditor_approved
        and decision_b.brief.assets_bias_24h.get("BTC") == "bearish"
    )

    # Drill C: Blind historical energy/oil embargo crisis replay -> MUST ground match & >= 75 conviction
    crisis_macro = AgentReport(
        agent_id="agent_4_macro_life_filter",
        tier="hard_truth",
        healthy=True,
        signal_direction="risk_off",
        confidence=90,
        headline="[Blind Replay] Emergency oil shipping blockade sends crude soaring and household fuel costs surging",
        flags=["ENERGY_SHIPPING_PRESSURE", "HOUSEHOLD_POLICY_PRESSURE"],
    )
    guardian_ok = evaluate_screen_vs_real_price("3600.00", "3595.00").report
    decision_c = await evaluate_swarm_reports(
        [crisis_macro, guardian_ok],
        settings=cfg,
        use_llm=False,
        dry_run=True,
    )
    drill_c_pass = bool(
        decision_c.auditor_approved
        and decision_c.conviction_score >= 75
        and decision_c.brief.assets_bias_24h.get("XAU") == "bullish"
    )

    passed_count = sum(1 for ok in (drill_a_pass, drill_b_pass, drill_c_pass) if ok)
    readiness_pct = (passed_count * 100) // 3

    return {
        "readiness_pct": readiness_pct,
        "drills": {
            "corrupt_9pct_spike_blocked": drill_a_pass,
            "retail_trap_tvl_drain_caught": drill_b_pass,
            "blind_crisis_replay_passed": drill_c_pass,
        },
    }


async def benchmark_llm_providers(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Benchmark live Gemini, Groq, and OpenRouter latency in integer milliseconds."""
    rotator = MultiProviderRotator(settings)
    benchmarks: dict[str, dict[str, Any]] = {}
    fastest_name = "deterministic_fallback"
    fastest_ms = 999999

    for provider in ("gemini", "groq"):
        t0 = datetime.now(timezone.utc)
        res = await probe_provider_via_rotator(
            provider, settings=settings, rotator=rotator, client=client
        )
        ms = _elapsed_ms(t0, datetime.now(timezone.utc))
        ok = bool(res.get("ok"))
        benchmarks[provider] = {"ok": ok, "latency_ms": ms}
        if ok and ms < fastest_ms:
            fastest_ms = ms
            fastest_name = f"{provider} ({ms}ms)"

    if not is_placeholder_secret(settings.openrouter_api_key):
        t0 = datetime.now(timezone.utc)
        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(15))
        try:
            await _call_openai_compatible_json(
                http_client,
                "https://openrouter.ai/api/v1/chat/completions",
                settings.openrouter_api_key,
                OPENROUTER_PRIMARY_MODEL,
                'Return JSON {"status":"ok"}',
                "Ping",
            )
            ms = _elapsed_ms(t0, datetime.now(timezone.utc))
            benchmarks["openrouter"] = {"ok": True, "latency_ms": ms}
            if ms < fastest_ms:
                fastest_ms = ms
                fastest_name = f"openrouter ({ms}ms)"
        except Exception as exc:
            ms = _elapsed_ms(t0, datetime.now(timezone.utc))
            benchmarks["openrouter"] = {"ok": False, "latency_ms": ms, "error": type(exc).__name__}
        finally:
            if owns_client:
                await http_client.aclose()

    return {"fastest_healthy_model": fastest_name, "providers": benchmarks}


async def run_daily_calibration_lab(
    *,
    settings: Settings | None = None,
    feed_path: Path = LIVE_FEED_TARGET_PATH,
    benchmark_live_llms: bool = True,
    send_admin_report: bool = True,
) -> dict[str, Any]:
    """Execute full daily calibration lab, adversarial gauntlet, 90d cleanup & Admin scorecard."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    store = MarketMemoryStore(settings=cfg)

    # 1. 24h Post-Mortem Grader (fetches fresh live-data feed if local `main` copy is stale)
    graded_rows: list[dict[str, Any]] = []
    try:
        feed_obj = await load_latest_live_feed(
            feed_path,
            allow_remote_live_data=(feed_path == LIVE_FEED_TARGET_PATH),
        )
        if feed_obj:
            snapshot = extract_price_snapshot(feed_obj)
            graded_rows = await store.evaluate_due_predictions(snapshot)
    except Exception:
        graded_rows = []

    prev_weights = await load_dynamic_trust_weights(cfg)
    if graded_rows:
        total_acc = sum(
            parse_strict_decimal(str(r.get("accuracy_score", "0")), allow_zero=True)
            for r in graded_rows
        )
        avg_acc = (total_acc / Decimal(len(graded_rows))).quantize(Decimal("0.01"))
        avg_acc_str: str | None = format(avg_acc, "f")
        new_weights = calibrate_source_trust_weights(prev_weights, avg_acc)
        await save_source_trust_weights(new_weights, cfg)
        lesson_en = (
            f"24h Accuracy {avg_acc_str}% across {len(graded_rows)} graded calls: "
            f"maintain 3x weighting on hard liquidity/macro truth and block RETAIL_TRAP euphoria."
        )
        lesson_ar = (
            f"دقة تقييم 24 ساعة بلغت {avg_acc_str}% عبر {len(graded_rows)} توقعات: "
            f"الاستمرار في ترجيح السيولة الحقيقية 3 أضعاف وحجب فخاخ حماس الأفراد."
        )
    else:
        avg_acc_str = None
        lesson_en = (
            "0 predictions reached the 24h grading window today: "
            "keeping calibrated source trust weights unchanged."
        )
        lesson_ar = (
            "لم تصل أي توقعات إلى نافذة التقييم (24 ساعة) اليوم: "
            "الإبقاء على أوزان ثقة المصادر الحالية دون تعديل."
        )

    # 2. Store 1-line Calibration Lesson in Supabase (NULL accuracy_score when 0 calls graded)
    lesson_status = await store_calibration_lesson(
        lesson_en, lesson_ar, avg_acc_str, cfg, store=store
    )

    # 3. Adversarial Readiness Gauntlet (Dry-Run Only)
    gauntlet = await run_adversarial_gauntlet(settings=cfg)

    # 4. Multi-LLM Latency Benchmark
    bench = (
        await benchmark_llm_providers(cfg)
        if benchmark_live_llms
        else {"fastest_healthy_model": "skipped_offline", "providers": {}}
    )

    # 5. 90-Day Transient Cleanup & Daily VIP/Pro Subscription Expiry Lifecycle
    cleanup = await cleanup_old_transient_rows(cfg, retention_days=90)
    sub_lifecycle = await run_subscription_lifecycle_check(settings=cfg)

    # 6. Private Admin Readiness Scorecard to ADMIN_CHAT_ID
    admin_sent = False
    if send_admin_report:
        acc_label = f"{avg_acc_str}%" if avg_acc_str is not None else "N/A (0 due)"
        scorecard_text = (
            f"Readiness: {gauntlet['readiness_pct']}% "
            f"(Spike={gauntlet['drills']['corrupt_9pct_spike_blocked']}, "
            f"Trap={gauntlet['drills']['retail_trap_tvl_drain_caught']}, "
            f"Crisis={gauntlet['drills']['blind_crisis_replay_passed']})\n"
            f"24h Accuracy: {acc_label} (graded={len(graded_rows)}, lesson={lesson_status})\n"
            f"Fastest Healthy Model: {bench['fastest_healthy_model']}\n"
            f"90d Transient Cleanup: ran={cleanup.get('ran')}\n"
            f"Subscriptions: reminded={sub_lifecycle.get('reminded', 0)}, downgraded={sub_lifecycle.get('downgraded', 0)}"
        )
        adm = await send_admin_watchdog_alert(
            "RatePath Morning Readiness Scorecard", scorecard_text, settings=cfg
        )
        admin_sent = adm.get("status") == "sent"

    return {
        "timestamp": utc_now_iso(),
        "readiness_pct": gauntlet["readiness_pct"],
        "drills": gauntlet["drills"],
        "accuracy_24h_pct": avg_acc_str,
        "graded_count": len(graded_rows),
        "lesson_storage": lesson_status,
        "fastest_healthy_model": bench["fastest_healthy_model"],
        "cleanup_90d": cleanup,
        "subscription_lifecycle": sub_lifecycle,
        "admin_scorecard_sent": admin_sent,
    }


if __name__ == "__main__":
    enforce_utf8_runtime()
    report = asyncio.run(run_daily_calibration_lab())
    print(json.dumps(report, ensure_ascii=False, indent=2))
