"""Agent 6: Master Strategist (Lane 2 — Behavioral/Political Flow + Century Memory + Calibration).

Synthesizes typed `AgentReport`s from Agents 1-5 & 7 alongside:
- Semantic `pgvector` century macro & crypto twins from Supabase `public.market_memory`
- Active daily calibration lessons (`calibration_lesson`) from Supabase
- 6-key AI Rotator (`gemini-4-hydrogen` / `gemini-3.5-flash-lite` -> `Groq` -> `OpenRouter`,
  upgraded from deprecated `gemini-2.5-flash` per current Gemini API spec) with clean deterministic fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from backend.agents.contracts import AgentReport, StrategistBrief, normalize_assets_bias
from backend.ai_strategist import MultiProviderRotator, _run_llm_with_fallback
from backend.config import SUPABASE_SERVER_USER_AGENT, NO_RAW_SYRIA_EMOJI_PROMPT_RULE, Settings, sanitize_syria_flag_emoji
from backend.evaluator import build_deterministic_embedding, normalize_supabase_rest_base
from backend.seed_history import get_historical_macro_twins

_VALID_HISTORICAL_TWIN_KEYS: frozenset[str] = frozenset(
    str(t["event_key"]) for t in get_historical_macro_twins()
)
_TWIN_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_TWIN_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "into", "over", "after",
        "amid", "news", "market", "markets", "price", "prices", "global", "macro",
        "data", "live", "index", "today", "says", "will", "have", "has", "are",
        "was", "were", "its", "their", "about", "below", "above", "under", "still",
        "extreme", "fear", "greed", "fear_greed", "neutral", "healthy", "guardian",
    }
)
# Domain-aware synonym expansion so macro/crypto headlines hit the exact historical twin
_TWIN_SYNONYM_GROUPS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (
        frozenset({"oil", "crude", "brent", "wti", "opec", "energy", "fuel", "hormuz", "shipping", "tanker", "embargo"}),
        frozenset({"oil", "embargo", "stagflation", "energy", "shock", "crude", "shipping"}),
    ),
    (
        frozenset({"tariff", "tariffs", "trade", "customs", "duties", "duty", "import", "export"}),
        frozenset({"tariff", "tariffs", "smoot", "hawley", "trade", "war", "protectionism"}),
    ),
    (
        frozenset({"fed", "fomc", "powell", "ecb", "boe", "inflation", "cpi", "pce", "rates", "hike", "yields"}),
        frozenset({"volcker", "inflation", "stagflation", "fed", "rate", "hike", "yield"}),
    ),
    (
        frozenset({"war", "missile", "strike", "strikes", "sanctions", "sanction", "conflict", "geopolitical", "iran", "russia"}),
        frozenset({"war", "sanctions", "embargo", "wwi", "gold", "oil", "geopolitical"}),
    ),
    (
        frozenset({"gold", "xau", "bullion", "reserves", "sovereign", "central"}),
        frozenset({"gold", "nixon", "sanctions", "bretton", "reserve", "revaluation"}),
    ),
    (
        frozenset({"depeg", "usdc", "usdt", "stablecoin", "tvl", "drain", "solvency", "insolvency", "bankrupt", "withdrawal", "hack"}),
        frozenset({"depeg", "terra", "luna", "ftx", "usdc", "svb", "mtgox", "solvency", "insolvency", "contagion"}),
    ),
    (
        frozenset({"bank", "banking", "lehman", "credit", "default", "downgrade", "debt", "ceiling"}),
        frozenset({"gfc", "lehman", "svb", "debt", "downgrade", "liquidity", "crash"}),
    ),
    (
        frozenset({"etf", "spot", "blackrock", "institutional", "inflows"}),
        frozenset({"etf", "spot", "institutional", "inflows"}),
    ),
    (
        frozenset({"euphoria", "fomo", "mania", "parabolic", "funding", "leverage", "liquidation", "washout"}),
        frozenset({"ico", "mania", "perp", "funding", "leverage", "flush", "retail"}),
    ),
)


def _expand_twin_tokens(text: str) -> set[str]:
    base = {t for t in _TWIN_WORD_RE.findall(text.lower()) if t not in _TWIN_STOPWORDS}
    expanded = set(base)
    for triggers, boosts in _TWIN_SYNONYM_GROUPS:
        if base & triggers:
            expanded |= boosts
    return expanded


def _build_prioritized_twin_query(
    reports: list[AgentReport],
    *,
    retail_trap_hint: bool = False,
) -> str:
    """Prioritize Tier-1 hard-truth headlines over crowd chatter when matching century twins."""
    hard_active = [
        r for r in reports
        if r.healthy and r.tier == "hard_truth" and r.signal_direction != "neutral"
    ]
    hard_all = [r for r in reports if r.healthy and r.tier == "hard_truth"]
    crowd_active = [
        r for r in reports
        if r.healthy and r.tier == "crowd_chatter" and r.signal_direction != "neutral"
    ]

    parts: list[str] = []
    # Put active hard-truth headlines and flags first (e.g. Oil/CPI/Tariffs or TVL Drain/Depeg)
    for r in (hard_active or hard_all):
        parts.append(r.headline)
        if r.flags:
            parts.append(" ".join(f.lower().replace("_", " ") for f in r.flags))

    # Only include crowd chatter if a real RETAIL_TRAP is active or hard-truth reports are quiet
    if retail_trap_hint or not hard_active:
        for r in crowd_active[:2]:
            parts.append(r.headline)

    query = " ".join(parts).strip()[:600]
    return query or "global macro gold usd"


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
        "User-Agent": SUPABASE_SERVER_USER_AGENT,
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
    """Retrieve grounded century macro + crypto twins using domain scoring + Supabase pgvector RPC.

    Guarantees ONLY valid `historical_macro_twin` anchors (in `_VALID_HISTORICAL_TWIN_KEYS`)
    are returned so the Auditor never rejects a `calibration_lesson` or `live_prediction` key,
    and ensures domain-relevant historical twins always outrank SHA-256 hash noise.
    """
    twins_catalog = get_historical_macro_twins()
    q_tokens = _expand_twin_tokens(query_text)
    is_crypto_query = bool(
        q_tokens & {"tvl", "depeg", "usdc", "usdt", "stablecoin", "solvency", "bitcoin", "btc", "eth", "sol", "crypto", "ftx", "luna", "ico", "perp", "funding", "defi"}
    )

    scored: list[tuple[int, dict[str, Any]]] = []
    for twin in twins_catalog:
        hay_text = (
            f"{twin['event_key']} {twin['macro_regime']} "
            f"{twin['headline_en']} {twin['summary_en']} {twin['lesson_en']}"
        )
        twin_tokens = {t for t in _TWIN_WORD_RE.findall(hay_text.lower()) if t not in _TWIN_STOPWORDS}
        overlap = len(q_tokens & twin_tokens)
        is_crypto_twin = str(twin["event_key"]).startswith("crypto-")
        # Keep pure macro queries focused on macro twins unless crypto signals are explicitly present
        domain_bonus = 2 if (is_crypto_query == is_crypto_twin) else 0
        score = overlap * 3 + domain_bonus
        scored.append((score, twin))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_scored = [row for score, row in scored if score >= 5][:match_count]
    if len(top_scored) >= match_count:
        return top_scored[:match_count]

    # Secondary check: Supabase `pgvector` RPC (strictly filtered to valid historical twin anchors)
    rpc_twins: list[dict[str, Any]] = []
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
                    "User-Agent": SUPABASE_SERVER_USER_AGENT,
                    "Authorization": f"Bearer {settings.supabase_key}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(
                    {
                        "query_embedding": vec_literal,
                        "match_threshold": 0,
                        "match_count": match_count * 3,
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            if resp.status_code == 200:
                rows = json.loads(resp.text)
                if isinstance(rows, list):
                    rpc_twins = [
                        r for r in rows
                        if isinstance(r, dict) and str(r.get("event_key", "")) in _VALID_HISTORICAL_TWIN_KEYS
                    ]
        except Exception:
            pass
        finally:
            if owns_client:
                await http_client.aclose()

    merged: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in top_scored + [r for _, r in scored] + rpc_twins:
        key = str(row.get("event_key", ""))
        if key and key not in seen_keys and key in _VALID_HISTORICAL_TWIN_KEYS:
            seen_keys.add(key)
            merged.append(row)
            if len(merged) >= match_count:
                break
    return merged


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
    quant = by_id.get("agent_8_quant_macro")

    quant_biases: dict[str, str] = {}
    if quant and quant.healthy and quant.metrics.get("quant_biases_json"):
        try:
            parsed_q = json.loads(quant.metrics["quant_biases_json"])
            if isinstance(parsed_q, dict):
                quant_biases = normalize_assets_bias(parsed_q)
        except Exception:
            quant_biases = {}

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
    default_biases = quant_biases or {
        "USD": "neutral",
        "EUR": "neutral",
        "GBP": "neutral",
        "XAU": "neutral",
        "BTC": "neutral",
        "ETH": "neutral",
        "SOL": "neutral",
    }
    has_quant_edge = any(v in ("bullish", "bearish") for v in default_biases.values())
    return StrategistBrief(
        event_en=lead_headline[:240],
        event_ar=(
            "إشارة كمية رقمية واضحة في اتجاهات الذهب والعملات والكريبتو (تحليل بيانات)"
            if has_quant_edge
            else "استقرار نسبي في الأسواق العالمية مع ترقّب المحفزات الكلية القادمة"
        ),
        money_flow_en=(
            "Quantitative EMA/RSI, funding & DXY indicators show directional divergence across major assets."
            if has_quant_edge
            else "Balanced capital rotation across USD, Gold, and top Crypto liquidity."
        ),
        money_flow_ar=(
            "مؤشرات الاتجاه الكمي (EMA/RSI) والتمويل ومؤشر الدولار تُظهر تبايناً رقمياً بين الأصول الرئيسية."
            if has_quant_edge
            else "توزيع متوازن للسيولة بين الدولار والذهب وأكبر العملات الرقمية."
        ),
        verdict_en=(
            "Probabilistic data bias active — review levels carefully; market numbers can change rapidly."
            if has_quant_edge
            else "Low-edge noise regime — neutral stance until hard numerical confirmation appears."
        ),
        verdict_ar=(
            "قراءة احتمالية مبنية على الأرقام الحالية فقط وليست ضماناً — راقب تغير السيولة والمستويات."
            if has_quant_edge
            else "حركة هادئة بدون محفز رقمي قوي — الحياد أفضل حتى ظهور تأكيد واضح في البيانات."
        ),
        matched_twin_key=twin_key,
        stage="consolidation",
        priced_in_active=not has_quant_edge,
        assets_bias_24h=default_biases,
        raw_conviction=78 if has_quant_edge else 62,
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
    query_blob = _build_prioritized_twin_query(reports, retail_trap_hint=retail_trap_hint)

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
                "Hard liquidity/macro/quant truth (Agents 4, 5, 7, 8 including EMA/RSI/Funding/DXY) ALWAYS overrides retail social chatter (Agents 1, 2, 3). "
                "Use selective abstention: output 'neutral' for any asset whose signals conflict or lack numerical conviction. "
                "LEGAL & ETHICAL RULE: Frame all insights strictly as informational data & probability analysis of changing numbers — NEVER promise guaranteed price moves, NEVER give financial advice, and NEVER mention '100-year memory'. "
                "Only mention retail hype or a 'retail trap' when retail_trap_warning is true. "
                "`assets_bias_24h` MUST use exactly these keys: USD, EUR, GBP, XAU, BTC, ETH, SOL "
                "(values: bullish/bearish/neutral). `matched_twin_key` MUST be one of the provided "
                "matched_century_twins event_key values. "
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
                    "assets_bias_24h": normalize_assets_bias(brief_obj.assets_bias_24h),
                    "provider_used": "live_llm_rotator",
                }
            )
        except Exception:
            pass
        finally:
            if owns_client:
                await http_client.aclose()

    return _deterministic_swarm_synthesis(reports, twins, retail_trap_hint=retail_trap_hint)
