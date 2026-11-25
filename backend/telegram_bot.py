"""Autonomous 4-Channel Language-Separated Telegram Publisher for RatePath (ريت باث).

Capabilities (Single Bot Token • Zero Extra LLM Calls • Pure `httpx` + `Decimal`):
1. English Audience Channels:
   - `TELEGRAM_PUBLIC_CHANNEL_EN`: Pure English breaking macro flash + Mini App (`?lang=en`).
   - `TELEGRAM_VIP_CHANNEL_EN`: Pure English Historical Twin, Priced-In Warning, Biases & Funding Carry.
2. Arabic Audience Channels:
   - `TELEGRAM_PUBLIC_CHANNEL_AR`: Pure Arabic breaking macro flash + Mini App (`?lang=ar`).
   - `TELEGRAM_VIP_CHANNEL_AR`: Pure Arabic Historical Twin, Priced-In Warning, Biases & Funding Carry.
3. Backward Compatibility:
   - Falls back cleanly to legacy `TELEGRAM_PUBLIC_CHANNEL` / `TELEGRAM_VIP_CHANNEL` if `_EN`/`_AR` are unset.
4. Admin Watchdog (`ADMIN_CHAT_ID`): Silent (`disable_notification=True`) operational alerts.
5. Deduplication: Atomic `Upstash Redis` REST (`SET key ts EX 604800 NX`) with UTF-8 local JSON cache fallback.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from backend.config import (
    LOCAL_TELEGRAM_DEDUP_PATH,
    RAW_SYRIA_FLAG_EMOJI,
    SYRIA_SAFE_TICKER_AR,
    SYRIA_SAFE_TICKER_EN,
    Settings,
    enforce_utf8_runtime,
    is_placeholder_secret,
    sanitize_syria_flag_emoji,
)
from backend.math_engine import parse_strict_decimal
from backend.seed_history import get_historical_macro_twins

# Strictly disabled until real verified affiliate accounts are configured
AFFILIATES_ENABLED: bool = False

DEDUP_TTL_SECONDS_STR: str = "172800"  # 48 hours
MAX_LOCAL_DEDUP_ENTRIES: int = 1000
HIGH_YIELD_CARRY_THRESHOLD_PCT: Decimal = Decimal("2.00")

_HSPACE_ONLY_RE = re.compile(r"[ \t]+")
_MULTI_BLANK_LINES_RE = re.compile(r"\n{3,}")
_RAW_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

ChannelLang = Literal["en", "ar", "bilingual"]

_BIAS_AR_LABELS: dict[str, str] = {
    "bullish": "صعودي",
    "bearish": "هبوطي",
    "neutral": "محايد",
}

_SEVERITY_AR_LABELS: dict[str, str] = {
    "high": "مرتفع",
    "moderate": "متوسط",
    "low": "منخفض",
}

_SOURCE_AR_LABELS: dict[str, str] = {
    "CNBC": "سي إن بي سي",
    "MarketWatch": "ماركت ووتش",
    "Investing.com": "إنفستينج",
    "Federal Reserve": "الاحتياطي الفيدرالي الأمريكي",
    "Bureau of Labor Statistics": "مكتب إحصاءات العمل الأمريكي",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _esc(text: Any) -> str:
    """Escape HTML special characters for Telegram HTML parse_mode."""
    return html.escape(str(text or ""), quote=True)


def normalize_preserve_newlines(text: Any) -> str:
    """Collapse horizontal spaces (`[ \\t]+`) per line while strictly preserving `\\n` and `\\n\\n`."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines = [_HSPACE_ONLY_RE.sub(" ", line).strip() for line in raw.split("\n")]
    joined = "\n".join(cleaned_lines).strip()
    return _MULTI_BLANK_LINES_RE.sub("\n\n", joined)


def _clean_single_block(text: Any) -> str:
    """Clean one prose paragraph using horizontal-space-only collapsing (never strips intentional breaks)."""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_HSPACE_ONLY_RE.sub(" ", ln).strip() for ln in raw.split("\n") if ln.strip()]
    return " ".join(lines).strip()


def build_lang_mini_app_url(base_url: str, lang: Literal["en", "ar"]) -> str:
    """Append or overwrite `?lang=en` / `?lang=ar` on the Mini App URL cleanly."""
    cleaned = (base_url or "https://ratepath.io/").strip()
    parts = urlsplit(cleaned)
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "lang"]
    query_pairs.append(("lang", lang))
    new_query = urlencode(query_pairs)
    path = parts.path or "/"
    return urlunsplit((parts.scheme or "https", parts.netloc or "ratepath.io", path, new_query, ""))


def build_special_topic_deep_link(
    base_url: str,
    asset_symbol: str,
    lang: Literal["en", "ar"],
) -> str:
    """Build exact contextual deep-link `{PUBLIC_SITE_URL}/?asset={SYMBOL}&lang={lang}`."""
    cleaned_base = (base_url or "https://ratepath.io").strip().rstrip("/")
    if not cleaned_base.startswith(("https://", "http://")):
        cleaned_base = "https://ratepath.io"
    clean_sym = re.sub(r"[^A-Za-z0-9_]", "", (asset_symbol or "XAU").strip().upper()) or "XAU"
    return f"{cleaned_base}/?asset={clean_sym}&lang={lang}"


def _resolve_topic_asset_symbol(news_item: dict[str, Any], explicit_asset: str | None) -> str:
    if explicit_asset and explicit_asset.strip():
        return re.sub(r"[^A-Za-z0-9_]", "", explicit_asset.strip().upper()) or "XAU"
    item_asset = news_item.get("topic_asset")
    if isinstance(item_asset, str) and item_asset.strip():
        return re.sub(r"[^A-Za-z0-9_]", "", item_asset.strip().upper()) or "XAU"
    biases = news_item.get("assets_bias_24h", {})
    for sym in ("XAU", "BTC", "USD", "ETH", "SOL", "EUR", "GBP"):
        if str(biases.get(sym, "")).lower() in ("bullish", "bearish"):
            return sym
    return "XAU"


def _strip_urls(text: str) -> str:
    return _HSPACE_ONLY_RE.sub(" ", _RAW_URL_RE.sub("", text)).strip()


def normalize_news_url(source_url: str) -> str:
    """Normalize a news URL before hashing so trivial trailing slashes match."""
    cleaned = source_url.strip()
    if cleaned.endswith("/") and cleaned.count("/") > 3:
        cleaned = cleaned.rstrip("/")
    return cleaned


def compute_dedup_redis_key(source_url: str) -> str:
    """Return deterministic Upstash Redis key for a normalized news URL."""
    norm = normalize_news_url(source_url)
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:28]
    return f"ratepath:tg:url:{digest}"


class NewsDeduplicator:
    """Upstash Redis REST + local UTF-8 JSON cache ensuring each news URL posts at most once."""

    def __init__(
        self,
        settings: Settings | None = None,
        local_cache_path: Path = LOCAL_TELEGRAM_DEDUP_PATH,
    ) -> None:
        self.settings = settings or Settings.load()
        self.local_cache_path = local_cache_path

    def _read_local_map(self) -> dict[str, str]:
        if not self.local_cache_path.exists():
            return {}
        try:
            raw = self.local_cache_path.read_text(encoding="utf-8")
            if not raw.strip():
                return {}
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_local_map(self, mapping: dict[str, str]) -> None:
        self.local_cache_path.parent.mkdir(parents=True, exist_ok=True)
        if len(mapping) > MAX_LOCAL_DEDUP_ENTRIES:
            items = list(mapping.items())[-MAX_LOCAL_DEDUP_ENTRIES:]
            mapping = dict(items)
        serialized = json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"
        self.local_cache_path.write_text(serialized, encoding="utf-8", newline="\n")

    async def claim_url_once(
        self,
        source_url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> bool:
        """Atomically claim `source_url`. Returns True if unseen, False if already posted."""
        norm_url = normalize_news_url(source_url)
        if not norm_url:
            return False

        redis_key = compute_dedup_redis_key(norm_url)
        now_iso = _utc_now_iso()
        local_map = self._read_local_map()

        if redis_key in local_map:
            return False

        if self.settings.has_live_upstash:
            owns_client = client is None
            http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10))
            try:
                endpoint = self.settings.upstash_redis_rest_url.rstrip("/")
                cmd = ["SET", redis_key, now_iso, "EX", DEDUP_TTL_SECONDS_STR, "NX"]
                resp = await http_client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.upstash_redis_rest_token}",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(cmd, ensure_ascii=False).encode("utf-8"),
                )
                resp.raise_for_status()
                body = json.loads(resp.text)
                claimed = body.get("result") == "OK"
                local_map[redis_key] = now_iso
                self._write_local_map(local_map)
                return claimed
            except Exception:
                pass
            finally:
                if owns_client:
                    await http_client.aclose()

        local_map[redis_key] = now_iso
        self._write_local_map(local_map)
        return True


# ---------------------------------------------------------------------------
# Historical Macro Twin Matcher & High-Yield Spread Extractor
# ---------------------------------------------------------------------------


def match_closest_historical_twin(news_item: dict[str, Any]) -> dict[str, Any]:
    """Deterministically match the closest historical macro twin by 24h bias alignment."""
    twins = get_historical_macro_twins()
    item_biases = news_item.get("assets_bias_24h", {})
    pw_active = bool(news_item.get("priced_in_warning", {}).get("active", False))

    best_twin = twins[0]
    best_score = -10
    for twin in twins:
        score = 0
        twin_biases = twin.get("assets_bias_24h", {})
        for asset in ("USD", "XAU", "BTC", "EUR"):
            if item_biases.get(asset) and item_biases.get(asset) == twin_biases.get(asset):
                score += 2
        if bool(twin.get("priced_in_warning", {}).get("active", False)) == pw_active:
            score += 3
        if score > best_score:
            best_score = score
            best_twin = twin

    return best_twin


def extract_high_yield_funding_opportunities(
    feed_payload: dict[str, Any],
    *,
    min_annualized_pct: Decimal = HIGH_YIELD_CARRY_THRESHOLD_PCT,
) -> list[dict[str, str]]:
    """Return perpetual funding arbitrage opportunities sorted by annualized carry %."""
    crypto_assets = feed_payload.get("crypto", {}).get("assets", {})
    opportunities: list[tuple[Decimal, dict[str, str]]] = []

    for sym in ("BTC", "ETH", "SOL"):
        entry = crypto_assets.get(sym, {})
        funding = entry.get("funding")
        if not isinstance(funding, dict):
            continue
        ann_dec = parse_strict_decimal(
            str(funding.get("annualized_spread_pct", "0")),
            field_name=f"tg_funding.{sym}",
            allow_zero=True,
        )
        if ann_dec >= min_annualized_pct:
            opportunities.append(
                (
                    ann_dec,
                    {
                        "symbol": sym,
                        "spot_price": str(entry.get("spot_price", "0")),
                        "spread_bps": str(funding.get("spread_bps", "0.00")),
                        "annualized_spread_pct": str(funding.get("annualized_spread_pct", "0.00")),
                        "arbitrage_signal": str(funding.get("arbitrage_signal", "neutral_carry")),
                    },
                )
            )

    opportunities.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in opportunities]


def _translate_arbitrage_signal_ar(signal: str) -> str:
    """Translate funding arbitrage signal slug into crisp Arabic."""
    if signal == "neutral_carry":
        return "توازن محايد"
    parts = signal.split("_")
    if len(parts) == 4 and parts[0] == "short" and parts[2] == "long":
        return f"بيع على {parts[1].upper()} وشراء على {parts[3].upper()}"
    return signal


# ---------------------------------------------------------------------------
# Language-Separated Telegram Payload Builders (Pure EN, Pure AR, Legacy)
# ---------------------------------------------------------------------------


def _compact_money_flow(biases: dict[str, Any], lang: Literal["en", "ar"]) -> str:
    """Produce a 1-line jargon-free money flow summary from 24h asset biases."""
    inflows = [k for k, v in biases.items() if str(v).lower() == "bullish"]
    outflows = [k for k, v in biases.items() if str(v).lower() == "bearish"]
    if lang == "en":
        if inflows and outflows:
            return f"Into {'/'.join(inflows[:3])}; out of {'/'.join(outflows[:3])}"
        if inflows:
            return f"Rotating into {'/'.join(inflows[:4])}"
        if outflows:
            return f"De-risking out of {'/'.join(outflows[:4])}"
        return "Balanced cash positioning across USD, Gold & Crypto"

    if inflows and outflows:
        return f"السيولة تتجه إلى {'/'.join(inflows[:3])} وتخرج من {'/'.join(outflows[:3])}"
    if inflows:
        return f"تدفقات شرائية نحو {'/'.join(inflows[:4])}"
    if outflows:
        return f"تخفيف مراكز وخروج من {'/'.join(outflows[:4])}"
    return "توازن وترقّب للسيولة بين الدولار والذهب والكريبتو"


def _compact_verdict(pw: dict[str, Any], lang: Literal["en", "ar"]) -> str:
    """Return a crisp 1-line actionable verdict."""
    active = bool(pw.get("active", False))
    if lang == "en":
        if active:
            return "Mostly priced in — avoid chasing spikes; wait for pullback."
        return "Fresh catalyst — monitor follow-through before scaling."
    if active:
        return "الخبر مسعّر مسبقاً غالباً — لا تلاحق القمم وانتظر التهدئة."
    return "محفز جديد — راقب استمرارية السيولة قبل اتخاذ القرار."


def build_public_channel_payload(
    news_item: dict[str, Any],
    feed_payload: dict[str, Any],
    *,
    chat_id: str,
    mini_app_url: str = "https://ratepath.io/",
    lang: ChannelLang = "en",
    is_special_topic: bool | None = None,
    topic_asset: str | None = None,
) -> dict[str, Any]:
    """Build clean 3-block news-only post (ZERO prices/tickers, ZERO links on regular posts).

    Attaches `{PUBLIC_SITE_URL}/?asset={SYMBOL}&lang={lang}` ONLY when `is_special_topic=True`.
    """
    del feed_payload  # News alerts strictly contain ZERO exchange rates or price tickers

    special_flag = (
        bool(news_item.get("is_special_topic", False))
        if is_special_topic is None
        else bool(is_special_topic)
    )
    en_block = news_item.get("en", {})
    ar_block = news_item.get("ar", {})
    pw = news_item.get("priced_in_warning", {})
    biases = news_item.get("assets_bias_24h", {})

    deep_link_url: str | None = None
    deep_link_btn_text: str | None = None
    if special_flag:
        resolved_sym = _resolve_topic_asset_symbol(news_item, topic_asset)
        link_lang: Literal["en", "ar"] = "ar" if lang == "ar" else "en"
        deep_link_url = build_special_topic_deep_link(mini_app_url, resolved_sym, link_lang)
        deep_link_btn_text = (
            f"🔎 تفاصيل وتحليل {resolved_sym} الكامل"
            if link_lang == "ar"
            else f"🔎 Full {resolved_sym} Deep Dive"
        )

    if lang == "en":
        headline_en = _strip_urls(_clean_single_block(en_block.get("title") or "Market Update"))
        story_en = _strip_urls(
            _clean_single_block(en_block.get("summary") or _compact_money_flow(biases, "en"))
        )[:320]
        bottom_en = _strip_urls(
            _clean_single_block(en_block.get("macro_thesis") or _compact_verdict(pw, "en"))
        )[:300]
        body = (
            f"🔥 <b>{_esc(headline_en)}</b>\n\n"
            f"📖 <b>What Happened:</b>\n"
            f"{_esc(story_en)}\n\n"
            f"💡 <b>Bottom Line:</b>\n"
            f"{_esc(bottom_en)}"
        )
        if deep_link_url:
            body += f"\n\n🔎 <b>Full Deep Dive:</b> {deep_link_url}"
        text = sanitize_syria_flag_emoji(normalize_preserve_newlines(body), lang="en")
    elif lang == "ar":
        headline_ar = _strip_urls(_clean_single_block(ar_block.get("title") or "تحديث الأسواق"))
        story_ar = _strip_urls(
            _clean_single_block(ar_block.get("summary") or _compact_money_flow(biases, "ar"))
        )[:320]
        bottom_ar = _strip_urls(
            _clean_single_block(ar_block.get("macro_thesis") or _compact_verdict(pw, "ar"))
        )[:300]
        body = (
            f"🔥 <b>{_esc(headline_ar)}</b>\n\n"
            f"📖 <b>ما القصة؟</b>\n"
            f"{_esc(story_ar)}\n\n"
            f"💡 <b>الخلاصة ومسار السيولة:</b>\n"
            f"{_esc(bottom_ar)}"
        )
        if deep_link_url:
            body += f"\n\n🔎 <b>التقرير الكامل:</b> {deep_link_url}"
        text = sanitize_syria_flag_emoji(normalize_preserve_newlines(body), lang="ar")
    else:
        headline_en = _strip_urls(_clean_single_block(en_block.get("title") or "Market Update"))
        story_en = _strip_urls(
            _clean_single_block(en_block.get("summary") or _compact_money_flow(biases, "en"))
        )[:280]
        bottom_en = _strip_urls(
            _clean_single_block(en_block.get("macro_thesis") or _compact_verdict(pw, "en"))
        )[:260]
        body = (
            f"🔥 <b>{_esc(headline_en)}</b>\n\n"
            f"📖 <b>What Happened:</b>\n"
            f"{_esc(story_en)}\n\n"
            f"💡 <b>Bottom Line:</b>\n"
            f"{_esc(bottom_en)}"
        )
        if deep_link_url:
            body += f"\n\n🔎 <b>Full Deep Dive:</b> {deep_link_url}"
        text = sanitize_syria_flag_emoji(normalize_preserve_newlines(body), lang="en")

    assert text.count("\n\n") >= 2, "Telegram news message must contain >= 2 blank-line separators"

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if deep_link_url and deep_link_btn_text:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": deep_link_btn_text, "url": deep_link_url}]]
        }
    return payload


def build_vip_channel_payload(
    news_item: dict[str, Any],
    feed_payload: dict[str, Any],
    *,
    chat_id: str,
    mini_app_url: str = "https://ratepath.io/",
    lang: ChannelLang = "en",
    is_special_topic: bool | None = None,
    topic_asset: str | None = None,
) -> dict[str, Any]:
    """Build clean 3-block VIP news-only brief (ZERO price tickers, ZERO links on regular posts)."""
    del feed_payload  # News alerts strictly contain ZERO exchange rates or price tickers

    special_flag = (
        bool(news_item.get("is_special_topic", False))
        if is_special_topic is None
        else bool(is_special_topic)
    )
    twin = match_closest_historical_twin(news_item)
    en_block = news_item.get("en", {})
    ar_block = news_item.get("ar", {})
    pw = news_item.get("priced_in_warning", {})
    biases = news_item.get("assets_bias_24h", {})

    deep_link_url: str | None = None
    deep_link_btn_text: str | None = None
    if special_flag:
        resolved_sym = _resolve_topic_asset_symbol(news_item, topic_asset)
        link_lang: Literal["en", "ar"] = "ar" if lang == "ar" else "en"
        deep_link_url = build_special_topic_deep_link(mini_app_url, resolved_sym, link_lang)
        deep_link_btn_text = (
            f"🔎 ملف VIP الكامل لـ {resolved_sym}"
            if link_lang == "ar"
            else f"🔎 VIP {resolved_sym} Deep Dive"
        )

    if lang == "en":
        headline_en = _strip_urls(_clean_single_block(en_block.get("title") or "VIP Market Brief"))
        story_en = _strip_urls(
            _clean_single_block(en_block.get("summary") or _compact_money_flow(biases, "en"))
        )[:320]
        verdict_part = _strip_urls(
            _clean_single_block(
                en_block.get("macro_thesis") or pw.get("en") or _compact_verdict(pw, "en")
            )
        )[:220]
        twin_hint = _strip_urls(_clean_single_block(twin.get("headline_en") or ""))[:100]
        bottom_en = f"{verdict_part} (Precedent: {twin_hint})" if twin_hint else verdict_part
        body = (
            f"🔥 <b>[VIP] {_esc(headline_en)}</b>\n\n"
            f"📖 <b>What Happened:</b>\n"
            f"{_esc(story_en)}\n\n"
            f"💡 <b>Bottom Line:</b>\n"
            f"{_esc(bottom_en)}"
        )
        if deep_link_url:
            body += f"\n\n🔎 <b>Full Deep Dive:</b> {deep_link_url}"
        text = sanitize_syria_flag_emoji(normalize_preserve_newlines(body), lang="en")

    elif lang == "ar":
        headline_ar = _strip_urls(_clean_single_block(ar_block.get("title") or "ملخص VIP للأسواق"))
        story_ar = _strip_urls(
            _clean_single_block(ar_block.get("summary") or _compact_money_flow(biases, "ar"))
        )[:320]
        verdict_part_ar = _strip_urls(
            _clean_single_block(
                ar_block.get("macro_thesis") or pw.get("ar") or _compact_verdict(pw, "ar")
            )
        )[:220]
        twin_hint_ar = _strip_urls(_clean_single_block(twin.get("headline_ar") or ""))[:100]
        bottom_ar = f"{verdict_part_ar} (سابقة تاريخية: {twin_hint_ar})" if twin_hint_ar else verdict_part_ar
        body = (
            f"🔥 <b>[VIP] {_esc(headline_ar)}</b>\n\n"
            f"📖 <b>ما القصة؟</b>\n"
            f"{_esc(story_ar)}\n\n"
            f"💡 <b>الخلاصة ومسار السيولة:</b>\n"
            f"{_esc(bottom_ar)}"
        )
        if deep_link_url:
            body += f"\n\n🔎 <b>التقرير الكامل:</b> {deep_link_url}"
        text = sanitize_syria_flag_emoji(normalize_preserve_newlines(body), lang="ar")

    else:
        headline_en = _strip_urls(_clean_single_block(en_block.get("title") or "VIP Market Brief"))
        story_en = _strip_urls(
            _clean_single_block(en_block.get("summary") or _compact_money_flow(biases, "en"))
        )[:280]
        bottom_en = _strip_urls(
            _clean_single_block(
                en_block.get("macro_thesis") or pw.get("en") or _compact_verdict(pw, "en")
            )
        )[:260]
        body = (
            f"🔥 <b>[VIP] {_esc(headline_en)}</b>\n\n"
            f"📖 <b>What Happened:</b>\n"
            f"{_esc(story_en)}\n\n"
            f"💡 <b>Bottom Line:</b>\n"
            f"{_esc(bottom_en)}"
        )
        if deep_link_url:
            body += f"\n\n🔎 <b>Full Deep Dive:</b> {deep_link_url}"
        text = sanitize_syria_flag_emoji(normalize_preserve_newlines(body), lang="en")

    assert text.count("\n\n") >= 2, "Telegram VIP message must contain >= 2 blank-line separators"

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if deep_link_url and deep_link_btn_text:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": deep_link_btn_text, "url": deep_link_url}]]
        }
    return payload


def build_admin_watchdog_payload(
    alert_title: str,
    details: str,
    *,
    admin_chat_id: str,
) -> dict[str, Any]:
    """Build silent (`disable_notification=True`) operational watchdog message for ADMIN_CHAT_ID."""
    text = sanitize_syria_flag_emoji(
        (
            f"🛠️ <b>[RatePath Watchdog | مراقب النظام]</b>\n"
            f"<b>Event:</b> {_esc(alert_title)}\n"
            f"<b>Timestamp:</b> <code>{_utc_now_iso()}</code>\n"
            f"<b>Details:</b> <code>{_esc(details[:900])}</code>"
        ),
        lang="en",
    )
    return {
        "chat_id": admin_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_notification": True,
        "disable_web_page_preview": True,
    }


# ---------------------------------------------------------------------------
# Async Telegram HTTP Senders & 4-Channel Dispatcher
# ---------------------------------------------------------------------------


async def _send_telegram_payload(
    client: httpx.AsyncClient,
    bot_token: str,
    payload: dict[str, Any],
    *,
    enforce_paragraph_spacing: bool = False,
) -> dict[str, Any]:
    """POST `sendMessage` via Telegram Bot API with spacing guard & `web_app` -> `url` fallback."""
    safe_payload = dict(payload)
    if isinstance(safe_payload.get("text"), str):
        text_val = normalize_preserve_newlines(safe_payload["text"])
        if RAW_SYRIA_FLAG_EMOJI in text_val:
            has_arabic = any("\u0600" <= ch <= "\u06FF" for ch in text_val)
            text_val = sanitize_syria_flag_emoji(text_val, lang="ar" if has_arabic else "en")
        if enforce_paragraph_spacing:
            assert text_val.count("\n\n") >= 2, (
                f"Telegram post failed paragraph spacing guard (found {text_val.count(chr(10) * 2)} blank lines)"
            )
        safe_payload["text"] = text_val
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = await client.post(
        api_url,
        headers={"Content-Type": "application/json"},
        content=json.dumps(safe_payload, ensure_ascii=False).encode("utf-8"),
    )

    if resp.status_code == 400 and "BUTTON_TYPE_INVALID" in resp.text:
        fallback_payload = json.loads(json.dumps(safe_payload, ensure_ascii=False))
        keyboard = fallback_payload.get("reply_markup", {}).get("inline_keyboard", [])
        for row in keyboard:
            for btn in row:
                if "web_app" in btn and isinstance(btn["web_app"], dict):
                    btn["url"] = btn["web_app"]["url"]
                    del btn["web_app"]
        resp = await client.post(
            api_url,
            headers={"Content-Type": "application/json"},
            content=json.dumps(fallback_payload, ensure_ascii=False).encode("utf-8"),
        )

    resp.raise_for_status()
    return json.loads(resp.text)


async def send_admin_watchdog_alert(
    alert_title: str,
    details: str,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Send silent failover/watchdog alert to `ADMIN_CHAT_ID`; skip cleanly on `placeholder`."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    if not cfg.has_live_telegram or is_placeholder_secret(cfg.admin_chat_id):
        return {"status": "skipped_placeholder", "channel": "admin"}

    payload = build_admin_watchdog_payload(
        alert_title, details, admin_chat_id=cfg.admin_chat_id
    )
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    try:
        api_res = await _send_telegram_payload(http_client, cfg.telegram_bot_token, payload)
        return {"status": "sent", "response": api_res}
    finally:
        if owns_client:
            await http_client.aclose()


async def broadcast_feed_to_telegram(
    feed_payload: dict[str, Any],
    *,
    settings: Settings | None = None,
    deduplicator: NewsDeduplicator | None = None,
    client: httpx.AsyncClient | None = None,
    max_posts_per_cycle: int = 2,
) -> dict[str, Any]:
    """Deduplicate news items and dispatch pure-EN / pure-AR posts across up to 4 channels."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    if not cfg.has_live_telegram:
        return {
            "status": "skipped_placeholder",
            "public_sent": 0,
            "vip_sent": 0,
            "public_en_sent": 0,
            "vip_en_sent": 0,
            "public_ar_sent": 0,
            "vip_ar_sent": 0,
            "duplicates_skipped": 0,
        }

    targets = cfg.resolve_telegram_targets()
    dedup = deduplicator or NewsDeduplicator(settings=cfg)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(15))

    counters = {
        "public_en_sent": 0,
        "vip_en_sent": 0,
        "public_ar_sent": 0,
        "vip_ar_sent": 0,
        "public_legacy_sent": 0,
        "vip_legacy_sent": 0,
    }
    duplicates_skipped = 0
    posted_count = 0

    dispatch_specs: tuple[tuple[str, str, ChannelLang], ...] = (
        ("public_en", "public", "en"),
        ("public_ar", "public", "ar"),
        ("public_legacy", "public", "bilingual"),
        ("vip_en", "vip", "en"),
        ("vip_ar", "vip", "ar"),
        ("vip_legacy", "vip", "bilingual"),
    )

    try:
        for item in feed_payload.get("news", []):
            if posted_count >= max_posts_per_cycle:
                break
            src_url = str(item.get("source_url", ""))
            is_fresh = await dedup.claim_url_once(src_url, client=http_client)
            if not is_fresh:
                duplicates_skipped += 1
                continue

            posted_count += 1

            for target_key, tier, lang_mode in dispatch_specs:
                chat_id = targets.get(target_key)
                if not chat_id:
                    continue

                if tier == "public":
                    payload = build_public_channel_payload(
                        item,
                        feed_payload,
                        chat_id=chat_id,
                        mini_app_url=cfg.public_site_url,
                        lang=lang_mode,
                    )
                else:
                    payload = build_vip_channel_payload(
                        item,
                        feed_payload,
                        chat_id=chat_id,
                        mini_app_url=cfg.public_site_url,
                        lang=lang_mode,
                    )

                try:
                    await _send_telegram_payload(
                        http_client,
                        cfg.telegram_bot_token,
                        payload,
                        enforce_paragraph_spacing=True,
                    )
                    counters[f"{target_key}_sent"] += 1
                except Exception as exc:
                    await send_admin_watchdog_alert(
                        f"Telegram Channel ({target_key}) Send Failure",
                        str(exc),
                        settings=cfg,
                        client=http_client,
                    )
    finally:
        if owns_client:
            await http_client.aclose()

    return {
        "status": "completed",
        "public_sent": counters["public_en_sent"]
        + counters["public_ar_sent"]
        + counters["public_legacy_sent"],
        "vip_sent": counters["vip_en_sent"]
        + counters["vip_ar_sent"]
        + counters["vip_legacy_sent"],
        "public_en_sent": counters["public_en_sent"],
        "vip_en_sent": counters["vip_en_sent"],
        "public_ar_sent": counters["public_ar_sent"],
        "vip_ar_sent": counters["vip_ar_sent"],
        "duplicates_skipped": duplicates_skipped,
    }
