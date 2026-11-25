"""Dual-Pass Pydantic Macro AI Pipeline (Strategist + Auditor) for RatePath (ريت باث).

Architecture:
- Pass 1 (Macro Strategist): Synthesizes bilingual (EN/AR) institutional thesis,
  priced-in risk warning, and 24-hour directional biases across USD, EUR, GBP, XAU, BTC, ETH, SOL.
- Pass 2 (Risk & Provenance Auditor): Audits factual grounding, priced-in calibration,
  macro directional coherence, and Arabic UTF-8 script integrity (`[\u0600-\u06FF]`).
- Provider Failover: Gemini (`gemini-4-hydrogen` via v1 Interactions API) -> Groq -> OpenRouter
  -> Deterministic Bilingual Fallback when `.env` keys are set to `placeholder` or unreachable.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.config import (
    NO_RAW_SYRIA_EMOJI_PROMPT_RULE,
    Settings,
    is_placeholder_secret,
    sanitize_syria_flag_emoji,
)
from backend.fetchers import RawNewsItem

BiasDirection = Literal["bullish", "bearish", "neutral"]
SeverityLevel = Literal["low", "moderate", "high"]
DEFAULT_KEY_COOLDOWN_SECONDS: int = 60

_ARABIC_SCRIPT_RE = re.compile(r"[\u0600-\u06FF]")
_ENGLISH_SCRIPT_RE = re.compile(r"[A-Za-z]")


class AsyncKeyPool:
    """Async round-robin API key rotator with 60s per-key cooldown on 429/timeout (ZERO floats)."""

    def __init__(
        self,
        provider: str,
        keys: tuple[str, ...],
        *,
        cooldown_seconds: int = DEFAULT_KEY_COOLDOWN_SECONDS,
    ) -> None:
        self.provider = provider
        self.keys: tuple[str, ...] = tuple(
            k.strip() for k in keys if not is_placeholder_secret(k)
        )
        self.cooldown_seconds = cooldown_seconds
        self._cursor: int = 0
        self._cooldown_until: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    @property
    def size(self) -> int:
        return len(self.keys)

    def is_in_cooldown(self, api_key: str, *, now: datetime | None = None) -> bool:
        now_dt = now or datetime.now(timezone.utc)
        until = self._cooldown_until.get(api_key)
        return until is not None and now_dt < until

    async def mark_cooldown(
        self,
        api_key: str,
        *,
        cooldown_seconds: int | None = None,
        now: datetime | None = None,
    ) -> None:
        secs = self.cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        now_dt = now or datetime.now(timezone.utc)
        async with self._lock:
            self._cooldown_until[api_key] = now_dt + timedelta(seconds=secs)

    async def acquire(
        self, *, now: datetime | None = None
    ) -> tuple[int, str] | None:
        """Return next available `(1_based_slot, api_key)` round-robin, skipping cooling keys."""
        if not self.keys:
            return None
        now_dt = now or datetime.now(timezone.utc)
        async with self._lock:
            total = len(self.keys)
            for _ in range(total):
                idx = self._cursor
                self._cursor = (self._cursor + 1) % total
                candidate = self.keys[idx]
                until = self._cooldown_until.get(candidate)
                if until is not None:
                    if now_dt < until:
                        continue
                    del self._cooldown_until[candidate]
                return (idx + 1, candidate)
        return None


class MultiProviderRotator:
    """Manages round-robin 6-key AI pools: Gemini (1..3) -> Groq (1..3) -> OpenRouter."""

    def __init__(
        self,
        settings: Settings,
        *,
        cooldown_seconds: int = DEFAULT_KEY_COOLDOWN_SECONDS,
    ) -> None:
        self.gemini_pool = AsyncKeyPool(
            "gemini", settings.gemini_api_keys, cooldown_seconds=cooldown_seconds
        )
        self.groq_pool = AsyncKeyPool(
            "groq", settings.groq_api_keys, cooldown_seconds=cooldown_seconds
        )
        openrouter_tuple = (
            (settings.openrouter_api_key,)
            if not is_placeholder_secret(settings.openrouter_api_key)
            else ()
        )
        self.openrouter_pool = AsyncKeyPool(
            "openrouter", openrouter_tuple, cooldown_seconds=cooldown_seconds
        )

    def pool_for(self, provider: Literal["gemini", "groq", "openrouter"]) -> AsyncKeyPool:
        if provider == "gemini":
            return self.gemini_pool
        if provider == "groq":
            return self.groq_pool
        return self.openrouter_pool


class BilingualTextBlock(BaseModel):
    """Strict English or Arabic editorial block."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=5, max_length=320)
    summary: str = Field(min_length=12, max_length=900)
    macro_thesis: str = Field(min_length=12, max_length=900)


class ArabicTextBlock(BilingualTextBlock):
    """Arabic editorial block enforcing authentic UTF-8 Arabic characters."""

    @field_validator("title", "summary", "macro_thesis")
    @classmethod
    def _ensure_arabic_script(cls, value: str) -> str:
        cleaned = sanitize_syria_flag_emoji(value.strip(), lang="ar")
        if not _ARABIC_SCRIPT_RE.search(cleaned):
            raise ValueError("Arabic text field must contain authentic UTF-8 Arabic script.")
        return cleaned


class EnglishTextBlock(BilingualTextBlock):
    """English editorial block enforcing Latin script characters."""

    @field_validator("title", "summary", "macro_thesis")
    @classmethod
    def _ensure_english_script(cls, value: str) -> str:
        cleaned = sanitize_syria_flag_emoji(value.strip(), lang="en")
        if not _ENGLISH_SCRIPT_RE.search(cleaned):
            raise ValueError("English text field must contain English characters.")
        return cleaned


class PricedInWarningModel(BaseModel):
    """Bilingual priced-in exhaustion warning."""

    model_config = ConfigDict(extra="forbid")

    active: bool
    severity: SeverityLevel
    en: str = Field(min_length=8, max_length=400)
    ar: str = Field(min_length=8, max_length=400)

    @field_validator("en")
    @classmethod
    def _validate_en(cls, value: str) -> str:
        return sanitize_syria_flag_emoji(value.strip(), lang="en")

    @field_validator("ar")
    @classmethod
    def _validate_ar(cls, value: str) -> str:
        cleaned = sanitize_syria_flag_emoji(value.strip(), lang="ar")
        if not _ARABIC_SCRIPT_RE.search(cleaned):
            raise ValueError("priced_in_warning.ar must contain UTF-8 Arabic script.")
        return cleaned


class AssetsBias24hModel(BaseModel):
    """24-hour institutional asset directional biases."""

    model_config = ConfigDict(extra="forbid")

    USD: BiasDirection
    EUR: BiasDirection
    GBP: BiasDirection
    XAU: BiasDirection
    BTC: BiasDirection
    ETH: BiasDirection
    SOL: BiasDirection


class StrategistDraft(BaseModel):
    """Pass 1 Output: Macro Strategist structured synthesis."""

    model_config = ConfigDict(extra="forbid")

    en: EnglishTextBlock
    ar: ArabicTextBlock
    priced_in_warning: PricedInWarningModel
    assets_bias_24h: AssetsBias24hModel
    confidence_score: int = Field(ge=0, le=100)


class AuditorReview(BaseModel):
    """Pass 2 Output: Risk & Fact Auditor structured verification."""

    model_config = ConfigDict(extra="forbid")

    approved: bool
    adjusted_confidence: int = Field(ge=0, le=100)
    priced_in_warning: PricedInWarningModel
    assets_bias_24h: AssetsBias24hModel
    en: EnglishTextBlock
    ar: ArabicTextBlock


class AnalyzedNewsRecord(BaseModel):
    """Final contract-compliant news item ready for `live_feed.json`."""

    model_config = ConfigDict(extra="forbid")

    id: str
    published_at: str
    source_name: str
    source_url: str
    en: EnglishTextBlock
    ar: ArabicTextBlock
    priced_in_warning: PricedInWarningModel
    assets_bias_24h: AssetsBias24hModel
    confidence_score: int = Field(ge=0, le=100)
    auditor_verified: bool


# ---------------------------------------------------------------------------
# Deterministic Bilingual Macro Engine (Pass 1 Strategist + Pass 2 Auditor)
# ---------------------------------------------------------------------------

_HAWKISH_USD_TERMS = (
    "rate hike",
    "hawkish",
    "higher for longer",
    "hot inflation",
    "inflation soars",
    "inflation accelerates",
    "cpi rises",
    "sticky inflation",
    "yields surge",
    "yields climb",
    "strong payrolls",
    "strong dollar",
    "dollar surges",
    "dollar climbs",
    "tariff",
    "tariffs",
)
_DOVISH_USD_TERMS = (
    "rate cut",
    "rate cuts",
    "dovish",
    "cooling inflation",
    "disinflation",
    "fed pause",
    "jobless claims rise",
    "unemployment rises",
    "yields fall",
    "yields drop",
    "weaker dollar",
    "dollar edges off",
    "dollar slips",
    "dollar falls",
)
_RISK_OFF_SAFE_HAVEN_TERMS = (
    "geopolitical",
    "war",
    "conflict",
    "sanctions",
    "crisis",
    "recession",
    "selloff",
    "sell-off",
    "market rout",
    "cyberattack",
    "cyberattacks",
    "doomsday",
    "turmoil",
    "safe haven",
    "safe-haven",
    "central bank gold",
    "gold rises",
    "gold hits",
)
_CRYPTO_BULL_TERMS = (
    "bitcoin",
    "btc",
    "crypto",
    "etf inflow",
    "institutional adoption",
    "digital asset",
    "ethereum",
    "solana",
    "risk-on",
    "rally",
    "generational",
)
_CRYPTO_BEAR_TERMS = (
    "hack",
    "crackdown",
    "liquidation",
    "liquidations",
    "outflow",
    "outflows",
    "sec lawsuit",
    "risk-off",
    "market rout",
)
_PRICED_IN_TERMS = (
    "as expected",
    "in line with expectations",
    "widely expected",
    "expected by",
    "polled economists",
    "consensus",
    "already priced",
    "unchanged",
    "steady",
    "anticipated",
    "holds rates",
    "digest",
)

_SOURCE_AR_MAP: dict[str, str] = {
    "CNBC": "سي إن بي سي (CNBC)",
    "MarketWatch": "ماركت ووتش (MarketWatch)",
    "Investing.com": "إنفستينج (Investing.com)",
    "Federal Reserve": "الاحتياطي الفيدرالي الأمريكي",
}


def _contains_any(text_lower: str, phrases: tuple[str, ...]) -> bool:
    return any(p in text_lower for p in phrases)


def _deterministic_strategist_pass1(
    item: RawNewsItem, market_context: dict[str, str]
) -> StrategistDraft:
    """Pass 1 (Deterministic Strategist): Derive macro biases & bilingual thesis."""
    corpus = f"{item.title} {item.summary}".lower()

    is_hawkish = _contains_any(corpus, _HAWKISH_USD_TERMS)
    is_dovish = _contains_any(corpus, _DOVISH_USD_TERMS)
    is_safe_haven = _contains_any(corpus, _RISK_OFF_SAFE_HAVEN_TERMS)
    is_crypto_bull = _contains_any(corpus, _CRYPTO_BULL_TERMS)
    is_crypto_bear = _contains_any(corpus, _CRYPTO_BEAR_TERMS)
    is_priced_in = _contains_any(corpus, _PRICED_IN_TERMS)

    if is_hawkish and not is_dovish:
        usd_bias: BiasDirection = "bullish"
        eur_bias: BiasDirection = "bearish"
        gbp_bias: BiasDirection = "bearish"
        xau_bias: BiasDirection = "bullish" if is_safe_haven else "bearish"
        btc_bias: BiasDirection = "bearish"
        regime_en = "Hawkish USD & Real-Yield Tightening Impulse"
        regime_ar = "زخم تشددي للدولار الأمريكي وارتفاع العوائد الحقيقية"
        thesis_en = (
            f"RatePath Strategist assesses upward pressure on US front-end yields following "
            f"{item.source_name} coverage, favoring USD liquidity over G10 peers while "
            f"constraining non-yielding precious metals unless safe-haven hedging dominates."
        )
        thesis_ar = (
            f"يرصد محلل ريت باث ضغوطاً صعودية على عوائد السندات الأمريكية قصيرة الأجل استناداً إلى "
            f"تغطية {_SOURCE_AR_MAP.get(item.source_name, item.source_name)}، مما يدعم قوة السيولة الدولارية "
            f"مقابل العملات الرئيسية ويحد من مكاسب الأصول غير المدرة للعائد."
        )
        base_conf = 76
    elif is_dovish and not is_hawkish:
        usd_bias = "bearish"
        eur_bias = "bullish"
        gbp_bias = "bullish"
        xau_bias = "bullish"
        btc_bias = "bullish"
        regime_en = "Dovish Liquidity Easing & Real-Rate Compression"
        regime_ar = "تيسير السيولة النقدية وتراجع العوائد الحقيقية"
        thesis_en = (
            f"Softer policy rate expectations highlighted by {item.source_name} compress real "
            f"US Treasury yields, structurally supporting spot Gold (XAU) and high-beta digital assets."
        )
        thesis_ar = (
            f"تشير معطيات {_SOURCE_AR_MAP.get(item.source_name, item.source_name)} حول مسار التيسير النقدي "
            f"إلى انحسار العوائد الحقيقية للسندات، مما يمنح دعماً هيكلياً للذهب الفوري والأصول الرقمية."
        )
        base_conf = 78
    elif is_safe_haven:
        usd_bias = "bullish"
        eur_bias = "bearish"
        gbp_bias = "bearish"
        xau_bias = "bullish"
        btc_bias = "bearish" if is_crypto_bear else "neutral"
        regime_en = "Defensive Safe-Haven Bid & Volatility Hedging"
        regime_ar = "تدفقات الملاذات الآمنة والتحوط من التقلبات الجيوسياسية"
        thesis_en = (
            f"Risk-aversion catalysts reported via {item.source_name} reinforce simultaneous "
            f"institutional allocation into physical Gold (XAU) and USD cash buffers."
        )
        thesis_ar = (
            f"تعزز محفزات التحوط من المخاطر الواردة عبر {_SOURCE_AR_MAP.get(item.source_name, item.source_name)} "
            f"التمركز المؤسسي المتزامن في الذهب الفوري والسيولة النقدية بالدولار الأمريكي."
        )
        base_conf = 80
    elif is_crypto_bull and not is_crypto_bear:
        usd_bias = "neutral"
        eur_bias = "neutral"
        gbp_bias = "neutral"
        xau_bias = "neutral"
        btc_bias = "bullish"
        regime_en = "Digital Asset Liquidity & Institutional Flow Impulse"
        regime_ar = "تدفقات السيولة المؤسسية في أسواق الأصول الرقمية"
        thesis_en = (
            f"Order-flow catalysts tracked by {item.source_name} point to constructive spot demand "
            f"across major digital assets alongside monitored perpetual funding spreads."
        )
        thesis_ar = (
            f"تعكس مؤشرات تدفق الأوامر المرصودة عبر {_SOURCE_AR_MAP.get(item.source_name, item.source_name)} "
            f"طلباً فوريّاً إيجابيّاً على الأصول الرقمية القيادية مع مراقبة فروق التمويل بين المنصات."
        )
        base_conf = 72
    else:
        usd_bias = "neutral"
        eur_bias = "neutral"
        gbp_bias = "neutral"
        xau_bias = "bullish" if "gold" in corpus else "neutral"
        btc_bias = "bearish" if is_crypto_bear else "neutral"
        regime_en = "Balanced Cross-Asset Consolidation"
        regime_ar = "توازن واستقرار عبر فئات الأصول الكلية"
        thesis_en = (
            f"Macro headline flow from {item.source_name} indicates range-bound institutional "
            f"positioning over the next 24 hours pending fresh sovereign yield or inflation prints."
        )
        thesis_ar = (
            f"تُشير التطورات الاقتصادية الكلية من {_SOURCE_AR_MAP.get(item.source_name, item.source_name)} "
            f"إلى تمركز مؤسسي متوازن خلال الـ 24 ساعة القادمة بانتظار بيانات التضخم وعوائد السندات."
        )
        base_conf = 68

    eth_bias: BiasDirection = btc_bias
    sol_bias: BiasDirection = btc_bias

    xau_spot = market_context.get("XAU_OZ", "N/A")
    btc_spot = market_context.get("BTC", "N/A")
    source_ar = _SOURCE_AR_MAP.get(item.source_name, f"المصدر المالي ({item.source_name})")

    if is_priced_in:
        priced_in = PricedInWarningModel(
            active=True,
            severity="high",
            en=(
                "Consensus alignment detected: catalyst carries elevated 'buy-the-rumor, "
                "sell-the-news' mean-reversion risk over a 24h horizon."
            ),
            ar=(
                "تحذير التسعير المسبق مرتفع: الخبر متوافق مع توقعات الإجماع مما يرفع مخاطر "
                "جني الأرباح والارتداد العكسي خلال 24 ساعة."
            ),
        )
    else:
        priced_in = PricedInWarningModel(
            active=False,
            severity="low",
            en=(
                "Fresh headline impulse: order books show room for incremental 24h discovery "
                "without acute pre-positioning exhaustion."
            ),
            ar=(
                "زخم سعري متجدد: لا تظهر مؤشرات استنفاد أو تسعير مسبق حاد في دفاتر الأوامر "
                "خلال نافذة الـ 24 ساعة."
            ),
        )

    ar_title = f"ريت باث | {regime_ar} — قراءة في تقرير {source_ar}"
    ar_summary = (
        f"رصدت منصة ريت باث تطوراً اقتصادياً من {source_ar} بعنوان: «{item.title}». "
        f"يأتي هذا الحدث بينما يتداول الذهب الفوري قرب {xau_spot} دولار للأونصة والبيتكوين عند "
        f"{btc_spot} دولار، مع تقييم انعكاساته المباشرة على السيولة خلال 24 ساعة."
    )

    return StrategistDraft(
        en=EnglishTextBlock(
            title=item.title,
            summary=item.summary,
            macro_thesis=f"[{regime_en}] {thesis_en}",
        ),
        ar=ArabicTextBlock(
            title=ar_title[:320],
            summary=ar_summary[:900],
            macro_thesis=thesis_ar[:900],
        ),
        priced_in_warning=priced_in,
        assets_bias_24h=AssetsBias24hModel(
            USD=usd_bias,
            EUR=eur_bias,
            GBP=gbp_bias,
            XAU=xau_bias,
            BTC=btc_bias,
            ETH=eth_bias,
            SOL=sol_bias,
        ),
        confidence_score=base_conf,
    )


def _deterministic_auditor_pass2(
    raw_item: RawNewsItem, draft: StrategistDraft
) -> AuditorReview:
    """Pass 2 (Deterministic Risk & Fact Auditor): Verify coherence & calibrate confidence."""
    adjusted_conf = draft.confidence_score
    biases = draft.assets_bias_24h

    # Rule 1: If priced_in_warning is active with high severity, cap directional overconfidence
    if draft.priced_in_warning.active and draft.priced_in_warning.severity == "high":
        adjusted_conf = min(adjusted_conf, 64)

    # Rule 2: Prevent contradictory G10 USD vs EUR identical non-neutral direction unless explicit
    if biases.USD == "bullish" and biases.EUR == "bullish":
        biases = biases.model_copy(update={"EUR": "neutral"})
    elif biases.USD == "bearish" and biases.EUR == "bearish":
        biases = biases.model_copy(update={"EUR": "neutral"})

    # Rule 3: Ensure source provenance is preserved & Arabic script passes strict check
    approved = bool(
        raw_item.source_url.startswith(("https://", "http://"))
        and _ARABIC_SCRIPT_RE.search(draft.ar.title)
        and _ARABIC_SCRIPT_RE.search(draft.ar.summary)
    )

    return AuditorReview(
        approved=approved,
        adjusted_confidence=max(50, min(95, adjusted_conf)),
        priced_in_warning=draft.priced_in_warning,
        assets_bias_24h=biases,
        en=draft.en,
        ar=draft.ar,
    )


# ---------------------------------------------------------------------------
# Live Multi-LLM Providers (Gemini -> Groq -> OpenRouter)
# ---------------------------------------------------------------------------


def _extract_json_object(raw_text: str) -> str:
    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = [ln for ln in stripped.splitlines() if not ln.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a valid JSON object.")
    return stripped[start : end + 1]


GEMINI_MODELS: tuple[str, ...] = (
    "gemini-3.5-flash-lite",
    "gemini-4-hydrogen",
    "gemini-3.1-flash-lite",
)
GROQ_PRIMARY_MODEL: str = "openai/gpt-oss-120b"
OPENROUTER_PRIMARY_MODEL: str = "meta-llama/llama-3.3-70b-instruct"


async def _call_gemini_structured(
    client: httpx.AsyncClient,
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    json_schema: dict[str, Any],
) -> str:
    """Call Gemini v1 Interactions API with model-level resilience against 503 demand spikes."""
    last_exc: Exception | None = None
    for model_name in GEMINI_MODELS:
        payload = {
            "model": model_name,
            "store": False,
            "system_instruction": system_prompt,
            "input": user_prompt,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": json_schema,
            },
        }
        resp = await client.post(
            "https://generativelanguage.googleapis.com/v1/interactions",
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        if resp.status_code == 503:
            last_exc = httpx.HTTPStatusError(
                f"Gemini {model_name} temporary 503", request=resp.request, response=resp
            )
            continue
        resp.raise_for_status()
        data = json.loads(resp.text)

        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"]

        text_chunks: list[str] = []
        for step in data.get("steps", []):
            if isinstance(step, dict) and step.get("type") == "model_output":
                for part in step.get("content", []):
                    if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                        text_chunks.append(part["text"])
        if not text_chunks:
            raise ValueError("Gemini v1/interactions returned no model_output text.")
        return "".join(text_chunks)

    if last_exc is not None:
        raise last_exc
    raise ValueError("No Gemini models configured.")


async def _call_openai_compatible_json(
    client: httpx.AsyncClient,
    endpoint: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    resp = await client.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    resp.raise_for_status()
    data = json.loads(resp.text)
    return str(data["choices"][0]["message"]["content"])


def _should_cooldown_exception(exc: Exception) -> bool:
    """Trigger 60s per-key cooldown on HTTP 429 rate limits or network timeouts."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return True
    return False


async def _run_llm_with_fallback(
    client: httpx.AsyncClient,
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    schema_model: type[BaseModel],
    *,
    rotator: MultiProviderRotator | None = None,
) -> BaseModel:
    schema_dict = schema_model.model_json_schema()
    pool_mgr = rotator or MultiProviderRotator(settings)
    errors: list[str] = []

    # 1. Gemini Pool (GEMINI_API_KEY_1..3 round-robin with 60s cooldown on 429/timeout)
    for _ in range(pool_mgr.gemini_pool.size):
        acquired = await pool_mgr.gemini_pool.acquire()
        if acquired is None:
            break
        slot, api_key = acquired
        try:
            raw = await _call_gemini_structured(
                client,
                api_key,
                system_prompt,
                user_prompt,
                schema_dict,
            )
            return schema_model.model_validate_json(_extract_json_object(raw))
        except Exception as exc:
            if _should_cooldown_exception(exc):
                await pool_mgr.gemini_pool.mark_cooldown(api_key)
            errors.append(f"gemini#{slot}: {type(exc).__name__}")

    # 2. Groq Pool (GROQ_API_KEY_1..3 round-robin with 60s cooldown on 429/timeout)
    for _ in range(pool_mgr.groq_pool.size):
        acquired = await pool_mgr.groq_pool.acquire()
        if acquired is None:
            break
        slot, api_key = acquired
        try:
            raw = await _call_openai_compatible_json(
                client,
                "https://api.groq.com/openai/v1/chat/completions",
                api_key,
                GROQ_PRIMARY_MODEL,
                f"{system_prompt}\nRespond strictly matching JSON schema: {json.dumps(schema_dict, ensure_ascii=False)}",
                user_prompt,
            )
            return schema_model.model_validate_json(_extract_json_object(raw))
        except Exception as exc:
            if _should_cooldown_exception(exc):
                await pool_mgr.groq_pool.mark_cooldown(api_key)
            errors.append(f"groq#{slot}: {type(exc).__name__}")

    # 3. OpenRouter Pool (OPENROUTER_API_KEY)
    for _ in range(pool_mgr.openrouter_pool.size):
        acquired = await pool_mgr.openrouter_pool.acquire()
        if acquired is None:
            break
        slot, api_key = acquired
        try:
            raw = await _call_openai_compatible_json(
                client,
                "https://openrouter.ai/api/v1/chat/completions",
                api_key,
                OPENROUTER_PRIMARY_MODEL,
                f"{system_prompt}\nRespond strictly matching JSON schema: {json.dumps(schema_dict, ensure_ascii=False)}",
                user_prompt,
            )
            return schema_model.model_validate_json(_extract_json_object(raw))
        except Exception as exc:
            if _should_cooldown_exception(exc):
                await pool_mgr.openrouter_pool.mark_cooldown(api_key)
            errors.append(f"openrouter#{slot}: {type(exc).__name__}")

    raise RuntimeError(f"Live LLM chain unavailable -> {' | '.join(errors)}")


class ProviderProbeResult(BaseModel):
    """Structured ping response for live Gemini/Groq pool connectivity checks."""

    model_config = ConfigDict(extra="forbid")

    status: str
    platform_ar: str


async def probe_provider_via_rotator(
    provider: Literal["gemini", "groq"],
    *,
    settings: Settings | None = None,
    rotator: MultiProviderRotator | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Execute one real structured call against the specified provider via `AsyncKeyPool`."""
    resolved = settings or Settings.load()
    pool_mgr = rotator or MultiProviderRotator(resolved)
    pool = pool_mgr.pool_for(provider)
    if pool.size == 0:
        return {"ok": False, "provider": provider, "error": "no_keys_configured"}

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(20))
    schema_dict = ProviderProbeResult.model_json_schema()
    sys_prompt = "Return a valid JSON object matching the schema with status='ok' and platform_ar='ريت باث'."
    usr_prompt = "Ping check for RatePath institutional engine."
    attempts: list[str] = []

    try:
        for _ in range(pool.size):
            acquired = await pool.acquire()
            if acquired is None:
                break
            slot, api_key = acquired
            try:
                if provider == "gemini":
                    raw = await _call_gemini_structured(
                        http_client, api_key, sys_prompt, usr_prompt, schema_dict
                    )
                else:
                    raw = await _call_openai_compatible_json(
                        http_client,
                        "https://api.groq.com/openai/v1/chat/completions",
                        api_key,
                        GROQ_PRIMARY_MODEL,
                        f"{sys_prompt}\nSchema: {json.dumps(schema_dict, ensure_ascii=False)}",
                        usr_prompt,
                    )
                parsed = ProviderProbeResult.model_validate_json(_extract_json_object(raw))
                return {
                    "ok": True,
                    "provider": provider,
                    "slot": slot,
                    "pool_size": pool.size,
                    "status": parsed.status,
                    "platform_ar": parsed.platform_ar,
                }
            except Exception as exc:
                if _should_cooldown_exception(exc):
                    await pool.mark_cooldown(api_key)
                status_info = (
                    f"HTTP {exc.response.status_code}"
                    if isinstance(exc, httpx.HTTPStatusError)
                    else type(exc).__name__
                )
                attempts.append(f"slot#{slot}:{status_info}")
        return {
            "ok": False,
            "provider": provider,
            "pool_size": pool.size,
            "attempts": attempts,
        }
    finally:
        if owns_client:
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Public Dual-Pass Entry Point
# ---------------------------------------------------------------------------


async def analyze_macro_news_batch(
    raw_items: list[RawNewsItem],
    market_context: dict[str, str],
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    rotator: MultiProviderRotator | None = None,
) -> tuple[list[AnalyzedNewsRecord], Literal["live_llm", "deterministic_fallback"]]:
    """Run Pass 1 (Strategist) + Pass 2 (Auditor) on verified news with deterministic fallback."""
    resolved_settings = settings or Settings.load()
    pool_mgr = rotator or MultiProviderRotator(resolved_settings)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(20))

    analyzed: list[AnalyzedNewsRecord] = []
    used_live_llm = False

    try:
        for item in raw_items:
            if resolved_settings.has_live_llm:
                try:
                    strat_sys = (
                        "You are the Principal Macro Strategist at RatePath (ريت باث). "
                        "Produce institutional English and Modern Standard Arabic macro analysis, "
                        "priced-in risk warning, and 24h biases (bullish/bearish/neutral). "
                        f"{NO_RAW_SYRIA_EMOJI_PROMPT_RULE}"
                    )
                    strat_usr = json.dumps(
                        {
                            "source_name": item.source_name,
                            "source_url": item.source_url,
                            "title": item.title,
                            "summary": item.summary,
                            "market_snapshot": market_context,
                        },
                        ensure_ascii=False,
                    )
                    draft_obj = await _run_llm_with_fallback(
                        http_client,
                        resolved_settings,
                        strat_sys,
                        strat_usr,
                        StrategistDraft,
                        rotator=pool_mgr,
                    )
                    assert isinstance(draft_obj, StrategistDraft)

                    audit_sys = (
                        "You are the Chief Risk & Provenance Auditor at RatePath (ريت باث). "
                        "Audit the Strategist draft against the source headline for factual grounding, "
                        "priced-in exhaustion risk, macro coherence, and authentic Arabic UTF-8 script. "
                        f"{NO_RAW_SYRIA_EMOJI_PROMPT_RULE}"
                    )
                    audit_usr = json.dumps(
                        {
                            "raw_source": {
                                "source_name": item.source_name,
                                "source_url": item.source_url,
                                "title": item.title,
                                "summary": item.summary,
                            },
                            "strategist_draft": draft_obj.model_dump(),
                        },
                        ensure_ascii=False,
                    )
                    verdict_obj = await _run_llm_with_fallback(
                        http_client,
                        resolved_settings,
                        audit_sys,
                        audit_usr,
                        AuditorReview,
                        rotator=pool_mgr,
                    )
                    assert isinstance(verdict_obj, AuditorReview)
                    used_live_llm = True

                    analyzed.append(
                        AnalyzedNewsRecord(
                            id=item.id,
                            published_at=item.published_at,
                            source_name=item.source_name,
                            source_url=item.source_url,
                            en=verdict_obj.en,
                            ar=verdict_obj.ar,
                            priced_in_warning=verdict_obj.priced_in_warning,
                            assets_bias_24h=verdict_obj.assets_bias_24h,
                            confidence_score=verdict_obj.adjusted_confidence,
                            auditor_verified=verdict_obj.approved,
                        )
                    )
                    continue
                except Exception:
                    # Seamless graceful degradation to deterministic bilingual engine
                    pass

            draft = _deterministic_strategist_pass1(item, market_context)
            verdict = _deterministic_auditor_pass2(item, draft)
            analyzed.append(
                AnalyzedNewsRecord(
                    id=item.id,
                    published_at=item.published_at,
                    source_name=item.source_name,
                    source_url=item.source_url,
                    en=verdict.en,
                    ar=verdict.ar,
                    priced_in_warning=verdict.priced_in_warning,
                    assets_bias_24h=verdict.assets_bias_24h,
                    confidence_score=verdict.adjusted_confidence,
                    auditor_verified=verdict.approved,
                )
            )
    finally:
        if owns_client:
            await http_client.aclose()

    mode: Literal["live_llm", "deterministic_fallback"] = (
        "live_llm" if used_live_llm else "deterministic_fallback"
    )
    return analyzed, mode
