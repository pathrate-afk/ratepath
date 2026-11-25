"""Strict Typed JSON Contracts for RatePath's 7-Agent Swarm & Central Orchestrator.

Zero free-form inter-agent chat is permitted; every collector emits an immutable
Pydantic `AgentReport` using exact Decimal strings and integer scores (ZERO floats).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentTier = Literal["hard_truth", "crowd_chatter", "strategist"]
SignalDirection = Literal["bullish", "bearish", "neutral", "risk_off", "euphoria", "stress"]
BiasDirection = Literal["bullish", "bearish", "neutral"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


CANONICAL_BIAS_ASSETS: tuple[str, ...] = ("USD", "EUR", "GBP", "XAU", "BTC", "ETH", "SOL")
_BIAS_KEY_ALIASES: dict[str, str] = {
    "USD": "USD", "DOLLAR": "USD", "US_DOLLAR": "USD", "DXY": "USD", "USD_INDEX": "USD",
    "EUR": "EUR", "EURO": "EUR", "EURUSD": "EUR",
    "GBP": "GBP", "POUND": "GBP", "STERLING": "GBP", "GBPUSD": "GBP",
    "XAU": "XAU", "GOLD": "XAU", "XAU_OZ": "XAU", "XAUUSD": "XAU",
    "BTC": "BTC", "BITCOIN": "BTC", "CRYPTO": "BTC", "CRYPTOCURRENCY": "BTC",
    "ETH": "ETH", "ETHEREUM": "ETH", "ETHER": "ETH",
    "SOL": "SOL", "SOLANA": "SOL",
}


def normalize_assets_bias(raw: dict[str, str] | None) -> dict[str, BiasDirection]:
    """Map free-form LLM bias keys (e.g. `gold`, `crypto`, `oil`) onto canonical evaluator keys.

    Unknown keys (e.g. `oil`) are dropped; missing canonical assets default to `neutral`, so the
    24h evaluator, RETAIL_TRAP guard and special-topic routing always receive gradeable keys.
    """
    normalized: dict[str, BiasDirection] = {k: "neutral" for k in CANONICAL_BIAS_ASSETS}
    for key, val in (raw or {}).items():
        canon = _BIAS_KEY_ALIASES.get(str(key).strip().upper().replace(" ", "_").replace("/", ""))
        direction = str(val).strip().lower()
        if canon and direction in ("bullish", "bearish", "neutral"):
            normalized[canon] = direction  # type: ignore[assignment]
    return normalized


class AgentReport(BaseModel):
    """Typed JSON output emitted by each of Agents 1-7."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=3, max_length=64)
    tier: AgentTier
    timestamp: str = Field(default_factory=utc_now_iso)
    healthy: bool = True
    signal_direction: SignalDirection = "neutral"
    confidence: int = Field(ge=0, le=100, default=50)
    headline: str = Field(min_length=3, max_length=320)
    evidence_urls: list[str] = Field(default_factory=list)
    metrics: dict[str, str] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    error: str | None = None


class StrategistBrief(BaseModel):
    """Structured synthesis returned by Agent 6 (Master Strategist)."""

    model_config = ConfigDict(extra="forbid")

    event_en: str = Field(min_length=5, max_length=260)
    event_ar: str = Field(min_length=5, max_length=260)
    money_flow_en: str = Field(min_length=5, max_length=260)
    money_flow_ar: str = Field(min_length=5, max_length=260)
    verdict_en: str = Field(min_length=5, max_length=260)
    verdict_ar: str = Field(min_length=5, max_length=260)
    matched_twin_key: str = Field(min_length=4, max_length=96)
    stage: Literal["stage_1_cash_dash", "stage_2_hard_asset_rotation", "consolidation"] = (
        "consolidation"
    )
    priced_in_active: bool = False
    assets_bias_24h: dict[str, BiasDirection] = Field(default_factory=dict)
    raw_conviction: int = Field(ge=0, le=100, default=65)
    provider_used: str = "deterministic_swarm"


class OrchestratorDecision(BaseModel):
    """Audited final publication decision enforced by the Central Orchestrator."""

    model_config = ConfigDict(extra="forbid")

    timestamp: str = Field(default_factory=utc_now_iso)
    publish_allowed: bool
    auditor_approved: bool
    conviction_score: int = Field(ge=0, le=100)
    retail_trap_flagged: bool = False
    corrupt_price_rejected: bool = False
    content_hash: str = Field(min_length=8, max_length=64)
    rejection_reason: str | None = None
    brief: StrategistBrief
    agent_reports: list[AgentReport] = Field(default_factory=list)
