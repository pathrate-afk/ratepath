"""Agent 6: Master Strategist (Lane 2 — Behavioral/Political Flow + Century Memory + Calibration).

Synthesizes typed `AgentReport`s from Agents 1-5 & 7 alongside:
- Semantic `pgvector` century macro & crypto twins from Supabase `public.market_memory`
- Active daily calibration lessons (`calibration_lesson`) from Supabase
- 6-key AI Rotator (`gemini-4-hydrogen` / `gemini-3.5-flash-lite` -> `Groq` -> `OpenRouter`,
  upgraded from deprecated `gemini-2.5-flash` per current Gemini API spec) with clean deterministic fallback.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from backend.agents.contracts import AgentReport, StrategistBrief
from backend.ai_strategist import MultiProviderRotator, _run_llm_with_fallback
from backend.config import NO_RAW_SYRIA_EMOJI_PROMPT_RULE, Settings, sanitize_syria_flag_emoji
from backend.evaluator import build_deterministic_embedding, normalize_supabase_rest_base
from backend.seed_history import get_historical_macro_twins


async def fetch_active_calibration_lessons(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    limit: int = 5,
) -> list[str]:
    """Retrieve recent 1-line mistake-prevention calibration lessons from Supabase."""
    if not settings.has_live_supabase:
        return []
    rest_base = normalize_supabase_rest_base(settings.supabase_url)
    headers = {
        "apikey": settings.supabase_key,
        "Authorization": f"Bearer {settings.supabase_key}",
    }
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
    try:
        resp = await http_client.get(
            f"{rest_base}/market_memory",
            params={
                "select": "lesson_en,record_type,macro_regime",
                "or": "(record_type.eq.calibration_lesson,macro_regime.eq.calibration_lesson)",
                "order": "created_at.desc",
                "limit": str(limit),
            },
            headers=headers,
        )
        if resp.status_code != 200:
            return []
        rows = json.loads(resp.text)
        return [str(r["lesson_en"]) for r in rows if isinstance(r, dict) and r.get("lesson_en")]
    except Exception:
        return []
    finally:
        if owns_client:
            await http_client.aclose()


async def query_century_memory_twins(
    query_text: str,
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    match_count: int = 3,
) -> list[dict[str, Any]]:
    """Query Supabase `pgvector` RPC `match_market_memory`, falling back cleanly to local 35-twin index."""
    if settings.has_live_supabase:
        rest_base = normalize_supabase_rest_base(settings.supabase_url)
        vec = build_deterministic_embedding(query_text)
        vec_literal = "[" + ",".join(vec) + "]"
        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
        try:
            resp = await http_client.post(
                f"{rest_base}/rpc/match_market_memory",
                headers={
                    "apikey": settings.supabase_key,
                    "Authorization": f"Bearer {settings.supabase_key}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(
                    {
                        "query_embedding": vec_literal,
                        "match_threshold": 0,
                        "match_count": match_count,
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            if resp.status_code == 200:
                rows = json.loads(resp.text)
                if isinstance(rows, list) and rows:
                    return rows[:match_count]
        except Exception:
            pass
        finally:
            if owns_client:
                await http_client.aclose()

    # Deterministic keyword relevance fallback across all 35 century macro + crypto anchors
    q_low = query_text.lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for twin in get_historical_macro_twins():
        hay = f"{twin['macro_regime']} {twin['headline_en']} {twin['summary_en']} {twin['lesson_en']}".lower()
        hits = sum(1 for tok in q_low.split() if len(tok) >= 4 and tok in hay)
        scored.append((hits, twin))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in scored[:match_count]]


def _deterministic_swarm_synthesis(
    reports: list[AgentReport],
    twins: list[dict[str, Any]],
    *,
    retail_trap_hint: bool,
) -> StrategistBrief:
    """Deterministic zero-fluff synthesis honoring Hierarchy of Truth when LLMs are unavailable."""
    by_id = {r.agent_id: r for r in reports}
    macro = by_id.get("agent_4_macro_life_filter")
    solvency = by_id.get("agent_5_crypto_solvency")
    guardian = by_id.get("agent_7_price_guardian")

    lead_twin = twins[0] if twins else get_historical_macro_twins()[0]
    twin_key = str(lead_twin.get("event_key", "hist-2008-gfc-lehman-liquidity"))

    all_flags = {f for r in reports for f in r.flags}
    is_solvency_stress = "TVL_DRAIN" in all_flags or "STABLECOIN_DEPEG" in all_flags
    is_energy_or_tariff = "ENERGY_SHIPPING_PRESSURE" in all_flags
    is_corrupt_price = "CORRUPT_SPIKE_REJECTED" in all_flags

    if is_solvency_stress:
        return StrategistBrief(
            event_en=(solvency.headline if solvency else "Crypto solvency stress detected")[:240],
            event_ar="رصد ضغط ملاءة ونزيف سيولة في أسواق الكريبتو والعملات المستقرة",
            money_flow_en="Stage-1 capital fleeing altcoins into USD cash and physical Gold.",
            money_flow_ar="خروج السيولة من العملات الرقمية نحو الكاش الدولاري والذهب المادي.",
            verdict_en=(
                "RETAIL_TRAP active — ignore social hype; protect capital in USD/Gold until TVL stabilizes."
                if retail_trap_hint
                else "Hard solvency stress — avoid high-beta crypto until pegs and TVL recover."
            ),
            verdict_ar=(
                "فخ للمتداولين الأفراد — تجاهل ضجيج التفاؤل وحافظ على السيولة في الدولار والذهب."
                if retail_trap_hint
                else "ضغط سيولة حقيقي — تجنب العملات البديلة حتى استقرار الربط والسيولة."
            ),
            matched_twin_key=twin_key,
            stage="stage_1_cash_dash",
            priced_in_active=retail_trap_hint,
            assets_bias_24h={
                "USD": "bullish",
                "EUR": "neutral",
                "GBP": "neutral",
                "XAU": "bullish",
                "BTC": "bearish",
                "ETH": "bearish",
                "SOL": "bearish",
            },
            raw_conviction=88,
            provider_used="deterministic_swarm",
        )

    if is_energy_or_tariff or (macro and macro.signal_direction == "risk_off"):
        return StrategistBrief(
            event_en=(macro.headline if macro else "Energy and macro policy stress")[:240],
            event_ar="ضغوط طاقة وتوترات سياسية وتجارية ترفع تكلفة المعيشة وتدفع للتحوط",
            money_flow_en="Institutional hedges rotating into Gold (XAU) and USD liquidity buffers.",
            money_flow_ar="تحوط مؤسسي متزامن نحو الذهب الفوري والسيولة الدولارية.",
            verdict_en="Hard macro catalyst — favor Gold on dips; watch USD yield resistance.",
            verdict_ar="محفز كلي حقيقي — الأفضلية للذهب عند التراجعات مع مراقبة عوائد الدولار.",
            matched_twin_key=twin_key,
            stage="stage_2_hard_asset_rotation",
            priced_in_active=False,
            assets_bias_24h={
                "USD": "bullish",
                "EUR": "bearish",
                "GBP": "bearish",
                "XAU": "bullish",
                "BTC": "neutral",
                "ETH": "bearish",
                "SOL": "bearish",
            },
            raw_conviction=82,
            provider_used="deterministic_swarm",
        )

    if is_corrupt_price:
        return StrategistBrief(
            event_en=(guardian.headline if guardian else "Single-venue price anomaly blocked")[:240],
            event_ar="حارس الأسعار يرفض قفزة سعرية مشوهة ويعتمد سيولة السوق الحقيقية",
            money_flow_en="Liquidity intact on secondary venues; screen anomaly filtered out.",
            money_flow_ar="السيولة الحقيقية مستقرة وتم عزل التذبذب السعري المشوّه.",
            verdict_en="No trade signal — single-source feed glitch neutralized by Price Guardian.",
            verdict_ar="لا توجد إشارة تداول — خلل سعري أحادي تم حجبه تلقائياً.",
            matched_twin_key="crypto-2011-mtgox-first-hack",
            stage="consolidation",
            priced_in_active=True,
            assets_bias_24h={
                "USD": "neutral",
                "EUR": "neutral",
                "GBP": "neutral",
                "XAU": "neutral",
                "BTC": "neutral",
                "ETH": "neutral",
                "SOL": "neutral",
            },
            raw_conviction=45,
            provider_used="deterministic_swarm",
        )

    lead_headline = (
        macro.headline
        if macro and macro.healthy
        else (reports[0].headline if reports else "Global markets consolidating")
    )
    return StrategistBrief(
        event_en=lead_headline[:240],
        event_ar="استقرار نسبي في الأسواق العالمية مع ترقّب المحفزات الكلية القادمة",
        money_flow_en="Balanced capital rotation across USD, Gold, and top Crypto liquidity.",
        money_flow_ar="توزيع متوازن للسيولة بين الدولار والذهب وأكبر العملات الرقمية.",
        verdict_en="Low-edge noise regime — hold fire until hard liquidity divergence appears.",
        verdict_ar="حركة هادئة بدون محفز قوي — التريث أفضل حتى ظهور تدفق سيولة واضح.",
        matched_twin_key=twin_key,
        stage="consolidation",
        priced_in_active=True,
        assets_bias_24h={
            "USD": "neutral",
            "EUR": "neutral",
            "GBP": "neutral",
            "XAU": "neutral",
            "BTC": "neutral",
            "ETH": "neutral",
            "SOL": "neutral",
        },
        raw_conviction=62,
        provider_used="deterministic_swarm",
    )


async def synthesize_master_strategy(
    reports: list[AgentReport],
    *,
    settings: Settings | None = None,
    rotator: MultiProviderRotator | None = None,
    client: httpx.AsyncClient | None = None,
    use_llm: bool = True,
    retail_trap_hint: bool = False,
) -> StrategistBrief:
    """Agent 6 Master Strategist synthesizing typed reports + Century Memory + Calibration Lessons."""
    cfg = settings or Settings.load()
    query_blob = " ".join(r.headline for r in reports if r.healthy)[:600] or "global macro gold btc"

    twins = await query_century_memory_twins(query_blob, cfg, client=client, match_count=3)
    lessons = await fetch_active_calibration_lessons(cfg, client=client, limit=5)

    if use_llm and cfg.has_live_llm:
        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(18))
        try:
            sys_prompt = (
                "You are Agent 6 (Master Strategist) at RatePath (ريت باث). "
                "Synthesize typed swarm reports into clear, engaging English & Modern Standard Arabic "
                "news alerts readable by beginners with ZERO jargon and ZERO exchange rates or price tickers. "
                "Fields: `event_en`/`event_ar` = clear engaging headline; `money_flow_en`/`money_flow_ar` = "
                "2 crisp beginner-friendly sentences explaining what happened; `verdict_en`/`verdict_ar` = "
                "1-2 plain sentences explaining what goes up/down and why. "
                "Hard liquidity/macro truth (Agents 4, 5, 7) ALWAYS overrides retail social chatter (Agents 1, 2, 3). "
                "Respect calibration lessons. "
                f"{NO_RAW_SYRIA_EMOJI_PROMPT_RULE}"
            )
            usr_prompt = json.dumps(
                {
                    "retail_trap_warning": retail_trap_hint,
                    "agent_reports": [r.model_dump() for r in reports],
                    "matched_century_twins": [
                        {
                            "event_key": t.get("event_key"),
                            "headline_en": t.get("headline_en"),
                            "lesson_en": t.get("lesson_en"),
                            "realized_changes_pct": t.get("realized_changes_pct"),
                        }
                        for t in twins
                    ],
                    "active_calibration_lessons": lessons,
                },
                ensure_ascii=False,
            )
            brief_obj = await _run_llm_with_fallback(
                http_client,
                cfg,
                sys_prompt,
                usr_prompt,
                StrategistBrief,
                rotator=rotator,
            )
            assert isinstance(brief_obj, StrategistBrief)
            return brief_obj.model_copy(
                update={
                    "event_en": sanitize_syria_flag_emoji(brief_obj.event_en, lang="en"),
                    "event_ar": sanitize_syria_flag_emoji(brief_obj.event_ar, lang="ar"),
                    "money_flow_en": sanitize_syria_flag_emoji(brief_obj.money_flow_en, lang="en"),
                    "money_flow_ar": sanitize_syria_flag_emoji(brief_obj.money_flow_ar, lang="ar"),
                    "verdict_en": sanitize_syria_flag_emoji(brief_obj.verdict_en, lang="en"),
                    "verdict_ar": sanitize_syria_flag_emoji(brief_obj.verdict_ar, lang="ar"),
                    "provider_used": "live_llm_rotator",
                }
            )
        except Exception:
            pass
        finally:
            if owns_client:
                await http_client.aclose()

    return _deterministic_swarm_synthesis(reports, twins, retail_trap_hint=retail_trap_hint)
