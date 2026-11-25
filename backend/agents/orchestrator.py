"""Central Manager / Orchestrator for RatePath's 7-Agent Swarm (Lane 2 — Every 20m).

Architectural Guarantees:
1. Strict Typed Contracts: Accepts ONLY Pydantic `AgentReport` objects (no free-form agent chat).
2. Hierarchy of Truth: Hard liquidity/macro truth (Agents 4, 5, 7) overrides crowd chatter
   (Agents 1, 2, 3). Automatically flags `RETAIL_TRAP` when crowd euphoria diverges from
   liquidity outflows / TVL drains / macro tightening.
3. Independent Risk & Hallucination Auditor: Validates Arabic UTF-8 script, twin-key existence
   against our 35-anchor century/crypto memory, and directional integrity.
4. Conviction Gate (`>= 75/100`) & Upstash Content-Hash Deduplication: Silences weak,
   unverified, or duplicate signals before Telegram delivery.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx

from backend.agents.contracts import AgentReport, OrchestratorDecision, StrategistBrief, utc_now_iso
from backend.agents.crypto_solvency import collect_crypto_solvency
from backend.agents.macro_life_filter import collect_macro_life_filter
from backend.agents.master_strategist import synthesize_master_strategy
from backend.agents.price_guardian import verify_gold_with_price_guardian
from backend.agents.search_trends import collect_search_trends
from backend.agents.social_pulse import collect_social_pulse
from backend.agents.telegram_scanner import collect_telegram_scanner
from backend.ai_strategist import MultiProviderRotator
from backend.config import LIVE_FEED_TARGET_PATH, WORKSPACE_ROOT, Settings, enforce_utf8_runtime
from backend.fetchers import create_http_client, fetch_gold_spot
from backend.math_engine import loads_decimal_json
from backend.seed_history import get_historical_macro_twins
from backend.telegram_bot import broadcast_feed_to_telegram

CONVICTION_PUBLISH_THRESHOLD: int = 75
SWARM_HASH_TTL_SECONDS_STR: str = "14400"  # 4 hours
SOURCE_TRUST_WEIGHTS_REDIS_KEY: str = "ratepath:source_trust_weights"
LOCAL_SWARM_DEDUP_PATH: Path = WORKSPACE_ROOT / "backend" / "swarm_dedup.tmp.json"

DEFAULT_TRUST_WEIGHTS: dict[str, int] = {
    "agent_1_social_pulse": 20,
    "agent_2_search_trends": 20,
    "agent_3_telegram_scanner": 25,
    "agent_4_macro_life_filter": 90,
    "agent_5_crypto_solvency": 95,
    "agent_7_price_guardian": 100,
}

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
_ENGLISH_RE = re.compile(r"[A-Za-z]")
_VALID_TWIN_KEYS: frozenset[str] = frozenset(
    str(t["event_key"]) for t in get_historical_macro_twins()
)


def detect_hierarchy_and_retail_trap(
    reports: list[AgentReport],
    trust_weights: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Apply Hierarchy of Truth (Agents 4, 5, 7 > Agents 1, 2, 3) & detect `RETAIL_TRAP`."""
    weights = trust_weights or DEFAULT_TRUST_WEIGHTS
    hard_reports = [r for r in reports if r.tier == "hard_truth" and r.healthy]
    crowd_reports = [r for r in reports if r.tier == "crowd_chatter" and r.healthy]

    hard_flags = {f for r in hard_reports for f in r.flags}
    crowd_flags = {f for r in crowd_reports for f in r.flags}

    crowd_euphoric = any(
        r.signal_direction in ("euphoria", "bullish") for r in crowd_reports
    ) or bool(crowd_flags & {"RETAIL_EUPHORIA", "SEARCH_SURGE_MACRO", "TELEGRAM_BREAKING_HYPE"})

    hard_stress_or_outflow = any(
        r.signal_direction in ("stress", "bearish", "risk_off") for r in hard_reports
    ) or bool(hard_flags & {"TVL_DRAIN", "STABLECOIN_DEPEG", "CORRUPT_SPIKE_REJECTED"})

    corrupt_price_rejected = "CORRUPT_SPIKE_REJECTED" in hard_flags
    retail_trap_flagged = bool(crowd_euphoric and hard_stress_or_outflow)

    # Pure-integer weighted score emphasizing hard truth (3x weight vs crowd 1x)
    weighted_sum = 0
    weight_total = 0
    for r in hard_reports:
        w = max(50, int(weights.get(r.agent_id, 90))) * 3
        weighted_sum += r.confidence * w
        weight_total += w
    for r in crowd_reports:
        w = max(10, int(weights.get(r.agent_id, 20)))
        effective_conf = 25 if retail_trap_flagged else r.confidence
        weighted_sum += effective_conf * w
        weight_total += w

    hierarchy_confidence = (weighted_sum // weight_total) if weight_total > 0 else 0

    return {
        "hard_healthy_count": len(hard_reports),
        "crowd_healthy_count": len(crowd_reports),
        "crowd_euphoric": crowd_euphoric,
        "hard_stress_or_outflow": hard_stress_or_outflow,
        "retail_trap_flagged": retail_trap_flagged,
        "corrupt_price_rejected": corrupt_price_rejected,
        "hierarchy_confidence": max(0, min(100, hierarchy_confidence)),
    }


def compute_swarm_content_hash(brief: StrategistBrief) -> str:
    """Deterministic content hash for Upstash dedup gating of swarm alerts."""
    bias_str = ",".join(f"{k}:{v}" for k, v in sorted(brief.assets_bias_24h.items()))
    canonical = (
        f"{brief.matched_twin_key}|{brief.stage}|{brief.event_en.strip().lower()[:120]}|{bias_str}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def audit_strategist_brief(
    brief: StrategistBrief,
    hierarchy: dict[str, Any],
) -> tuple[bool, int, StrategistBrief, str | None]:
    """Independent Risk & Hallucination Auditor enforcing grounding, UTF-8 AR & trap safety."""
    # 1. Grounded historical twin check (anti-hallucination)
    if brief.matched_twin_key not in _VALID_TWIN_KEYS:
        return False, 0, brief, f"hallucinated_twin_key:{brief.matched_twin_key}"

    # 2. Bilingual script integrity check
    if not (
        _ENGLISH_RE.search(brief.event_en)
        and _ENGLISH_RE.search(brief.money_flow_en)
        and _ENGLISH_RE.search(brief.verdict_en)
    ):
        return False, 0, brief, "invalid_english_copy"
    if not (
        _ARABIC_RE.search(brief.event_ar)
        and _ARABIC_RE.search(brief.money_flow_ar)
        and _ARABIC_RE.search(brief.verdict_ar)
    ):
        return False, 0, brief, "invalid_arabic_utf8_copy"

    # 3. Require at least one healthy Tier-1 hard-truth collector
    if hierarchy["hard_healthy_count"] == 0:
        return False, 30, brief, "no_hard_truth_confirmation"

    calibrated_brief = brief
    # 4. Enforce RETAIL_TRAP protection: crowd euphoria cannot produce unchecked crypto bullishness
    if hierarchy["retail_trap_flagged"]:
        adjusted_biases = dict(brief.assets_bias_24h)
        for sym in ("BTC", "ETH", "SOL"):
            if adjusted_biases.get(sym) == "bullish":
                adjusted_biases[sym] = "bearish"
        verdict_en = brief.verdict_en
        if "RETAIL_TRAP" not in verdict_en:
            verdict_en = f"RETAIL_TRAP: {verdict_en}"[:260]
        verdict_ar = brief.verdict_ar
        if "فخ" not in verdict_ar:
            verdict_ar = f"تنبيه فخ أفراد: {verdict_ar}"[:260]
        calibrated_brief = brief.model_copy(
            update={
                "assets_bias_24h": adjusted_biases,
                "verdict_en": verdict_en,
                "verdict_ar": verdict_ar,
                "priced_in_active": True,
            }
        )

    # 5. Calibrated integer conviction blending Strategist raw conviction + Hierarchy of Truth
    blended = (brief.raw_conviction * 55 + hierarchy["hierarchy_confidence"] * 45) // 100
    if hierarchy["retail_trap_flagged"]:
        blended = max(blended, 82)
    if hierarchy["corrupt_price_rejected"] and not hierarchy["retail_trap_flagged"]:
        # Single-source corrupt price glitches are silently blocked, not pushed as trading alerts
        blended = min(blended, 55)

    final_score = max(0, min(100, blended))
    return True, final_score, calibrated_brief, None


async def load_dynamic_trust_weights(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, int]:
    """Load calibrated agent source trust weights from Upstash Redis (with safe defaults)."""
    merged = dict(DEFAULT_TRUST_WEIGHTS)
    if not settings.has_live_upstash:
        return merged

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
            content=json.dumps(["GET", SOURCE_TRUST_WEIGHTS_REDIS_KEY], ensure_ascii=False).encode(
                "utf-8"
            ),
        )
        if resp.status_code == 200:
            raw_val = json.loads(resp.text).get("result")
            if raw_val:
                loaded = json.loads(str(raw_val))
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        if k in merged:
                            merged[k] = max(10, min(100, int(v)))
    except Exception:
        pass
    finally:
        if owns_client:
            await http_client.aclose()
    return merged


async def claim_swarm_content_hash(
    content_hash: str,
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    local_cache_path: Path = LOCAL_SWARM_DEDUP_PATH,
) -> bool:
    """Atomic Upstash Redis content-hash gate (`SET ... EX 14400 NX`) with local JSON fallback."""
    redis_key = f"ratepath:swarm:hash:{content_hash}"
    now_iso = utc_now_iso()

    local_map: dict[str, str] = {}
    if local_cache_path.exists():
        try:
            parsed = json.loads(local_cache_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                local_map = parsed
        except Exception:
            local_map = {}

    if redis_key in local_map:
        return False

    claimed = True
    if settings.has_live_upstash:
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
                    ["SET", redis_key, now_iso, "EX", SWARM_HASH_TTL_SECONDS_STR, "NX"],
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            resp.raise_for_status()
            claimed = json.loads(resp.text).get("result") == "OK"
        except Exception:
            claimed = True
        finally:
            if owns_client:
                await http_client.aclose()

    local_map[redis_key] = now_iso
    local_cache_path.parent.mkdir(parents=True, exist_ok=True)
    local_cache_path.write_text(
        json.dumps(local_map, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return claimed


async def evaluate_swarm_reports(
    reports: list[AgentReport],
    *,
    settings: Settings | None = None,
    rotator: MultiProviderRotator | None = None,
    client: httpx.AsyncClient | None = None,
    use_llm: bool = True,
    dry_run: bool = False,
    local_dedup_path: Path = LOCAL_SWARM_DEDUP_PATH,
    trust_weights: dict[str, int] | None = None,
) -> OrchestratorDecision:
    """Core Central Orchestrator pipeline: Hierarchy -> Agent 6 -> Auditor -> Conviction & Hash Gate."""
    cfg = settings or Settings.load()
    weights = trust_weights or await load_dynamic_trust_weights(cfg, client=client)
    hierarchy = detect_hierarchy_and_retail_trap(reports, trust_weights=weights)

    brief = await synthesize_master_strategy(
        reports,
        settings=cfg,
        rotator=rotator,
        client=client,
        use_llm=use_llm,
        retail_trap_hint=hierarchy["retail_trap_flagged"],
    )

    auditor_ok, conviction, audited_brief, audit_err = audit_strategist_brief(brief, hierarchy)
    content_hash = compute_swarm_content_hash(audited_brief)

    publish_allowed = True
    rejection_reason = audit_err

    if not auditor_ok:
        publish_allowed = False
    elif conviction < CONVICTION_PUBLISH_THRESHOLD:
        publish_allowed = False
        rejection_reason = f"conviction_below_gate:{conviction}<{CONVICTION_PUBLISH_THRESHOLD}"
    elif not dry_run:
        is_new = await claim_swarm_content_hash(
            content_hash, cfg, client=client, local_cache_path=local_dedup_path
        )
        if not is_new:
            publish_allowed = False
            rejection_reason = "duplicate_content_hash_silenced"

    return OrchestratorDecision(
        timestamp=utc_now_iso(),
        publish_allowed=publish_allowed,
        auditor_approved=auditor_ok,
        conviction_score=conviction,
        retail_trap_flagged=hierarchy["retail_trap_flagged"],
        corrupt_price_rejected=hierarchy["corrupt_price_rejected"],
        content_hash=content_hash,
        rejection_reason=rejection_reason,
        brief=audited_brief,
        agent_reports=reports,
    )


async def run_lane2_swarm(
    *,
    settings: Settings | None = None,
    feed_path: Path = LIVE_FEED_TARGET_PATH,
    dry_run: bool = False,
    use_llm: bool = True,
) -> OrchestratorDecision:
    """Execute full Lane-2 7-Agent Swarm cycle (every 20m) and publish if Conviction >= 75."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    rotator = MultiProviderRotator(cfg)

    async with create_http_client() as client:
        # Collect Agents 1, 2, 3, 4, 5 + Agent 7 live check concurrently (fault-isolated, 8s timeout)
        async def _agent7_check() -> AgentReport:
            try:
                gold_res = await fetch_gold_spot(client)
                verdict = await verify_gold_with_price_guardian(
                    client, gold_res.xau_oz_usd, screen_source=gold_res.source
                )
                return verdict.report
            except Exception as exc:
                return AgentReport(
                    agent_id="agent_7_price_guardian",
                    tier="hard_truth",
                    healthy=False,
                    confidence=0,
                    headline="Price Guardian gold cross-check failed.",
                    error=type(exc).__name__,
                )

        reports = list(
            await asyncio.gather(
                collect_social_pulse(client),
                collect_search_trends(client),
                collect_telegram_scanner(client),
                collect_macro_life_filter(client),
                collect_crypto_solvency(client),
                _agent7_check(),
            )
        )

        decision = await evaluate_swarm_reports(
            reports,
            settings=cfg,
            rotator=rotator,
            client=client,
            use_llm=use_llm,
            dry_run=dry_run,
        )

        if decision.publish_allowed and not dry_run:
            feed_snapshot: dict[str, Any] = {}
            if feed_path.exists():
                try:
                    feed_snapshot = loads_decimal_json(feed_path.read_text(encoding="utf-8"))
                except Exception:
                    feed_snapshot = {}

            swarm_news_item = {
                "id": f"swarm-{decision.content_hash[:12]}",
                "published_at": decision.timestamp,
                "source_name": "RatePath Swarm",
                "source_url": f"https://ratepath.io/#swarm-{decision.content_hash[:12]}",
                "en": {
                    "title": decision.brief.event_en,
                    "summary": decision.brief.money_flow_en,
                    "macro_thesis": decision.brief.verdict_en,
                },
                "ar": {
                    "title": decision.brief.event_ar,
                    "summary": decision.brief.money_flow_ar,
                    "macro_thesis": decision.brief.verdict_ar,
                },
                "priced_in_warning": {
                    "active": decision.brief.priced_in_active,
                    "severity": "high" if decision.retail_trap_flagged else "low",
                    "en": decision.brief.verdict_en,
                    "ar": decision.brief.verdict_ar,
                },
                "assets_bias_24h": decision.brief.assets_bias_24h,
                "confidence_score": decision.conviction_score,
                "auditor_verified": decision.auditor_approved,
            }
            payload_for_tg = {
                "gold": feed_snapshot.get("gold", {"prices": {"XAU_OZ": "N/A"}}),
                "crypto": feed_snapshot.get("crypto", {"assets": {"BTC": {"spot_price": "N/A"}}}),
                "news": [swarm_news_item],
            }
            await broadcast_feed_to_telegram(
                payload_for_tg, settings=cfg, client=client, max_posts_per_cycle=1
            )

        return decision


if __name__ == "__main__":
    enforce_utf8_runtime()
    result = asyncio.run(run_lane2_swarm())
    print(
        json.dumps(
            {
                "timestamp": result.timestamp,
                "publish_allowed": result.publish_allowed,
                "auditor_approved": result.auditor_approved,
                "conviction_score": result.conviction_score,
                "retail_trap_flagged": result.retail_trap_flagged,
                "rejection_reason": result.rejection_reason,
                "matched_twin_key": result.brief.matched_twin_key,
                "provider_used": result.brief.provider_used,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
