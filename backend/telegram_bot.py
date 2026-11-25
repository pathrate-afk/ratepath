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
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from backend.config import (
    LOCAL_TELEGRAM_DEDUP_PATH,
    Settings,
    enforce_utf8_runtime,
    is_placeholder_secret,
)
from backend.math_engine import parse_strict_decimal
from backend.seed_history import get_historical_macro_twins

DEDUP_TTL_SECONDS_STR: str = "604800"  # 7 days
MAX_LOCAL_DEDUP_ENTRIES: int = 1000
HIGH_YIELD_CARRY_THRESHOLD_PCT: Decimal = Decimal("2.00")

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


def build_lang_mini_app_url(base_url: str, lang: Literal["en", "ar"]) -> str:
    """Append or overwrite `?lang=en` / `?lang=ar` on the Mini App URL cleanly."""
    cleaned = (base_url or "https://ratepath.io/").strip()
    parts = urlsplit(cleaned)
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "lang"]
    query_pairs.append(("lang", lang))
    new_query = urlencode(query_pairs)
    path = parts.path or "/"
    return urlunsplit((parts.scheme or "https", parts.netloc or "ratepath.io", path, new_query, ""))


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


def build_public_channel_payload(
    news_item: dict[str, Any],
    feed_payload: dict[str, Any],
    *,
    chat_id: str,
    mini_app_url: str = "https://ratepath.io/",
    lang: ChannelLang = "en",
) -> dict[str, Any]:
    """Build pure-EN (`?lang=en`), pure-AR (`?lang=ar`), or legacy bilingual public flash post."""
    gold_oz = feed_payload.get("gold", {}).get("prices", {}).get("XAU_OZ", "N/A")
    btc_spot = feed_payload.get("crypto", {}).get("assets", {}).get("BTC", {}).get("spot_price", "N/A")

    en_block = news_item.get("en", {})
    ar_block = news_item.get("ar", {})
    src_name = str(news_item.get("source_name", "Global Wire"))
    src_name_ar = _SOURCE_AR_LABELS.get(src_name, src_name)
    src_url = str(news_item.get("source_url", "https://ratepath.io/"))

    if lang == "en":
        launch_url = build_lang_mini_app_url(mini_app_url, "en")
        text = (
            f"⚡ <b>RatePath — Live Macro Flash</b>\n\n"
            f"<b>{_esc(en_block.get('title'))}</b>\n"
            f"{_esc(en_block.get('summary'))}\n\n"
            f"📊 <code>XAU/USD: ${_esc(gold_oz)}</code>  •  <code>BTC/USD: ${_esc(btc_spot)}</code>\n"
            f"🔗 Source: <a href=\"{_esc(src_url)}\">{_esc(src_name)}</a>"
        )
        keyboard = [
            [{"text": "📊 Open RatePath Terminal", "web_app": {"url": launch_url}}],
            [{"text": f"🔗 {src_name} — Verified Source", "url": src_url}],
        ]
    elif lang == "ar":
        launch_url = build_lang_mini_app_url(mini_app_url, "ar")
        text = (
            f"⚡ <b>ريت باث — عاجل الأسواق العالمية</b>\n\n"
            f"<b>{_esc(ar_block.get('title'))}</b>\n"
            f"{_esc(ar_block.get('summary'))}\n\n"
            f"📊 أونصة الذهب: <code>${_esc(gold_oz)}</code>  •  البيتكوين: <code>${_esc(btc_spot)}</code>\n"
            f"🔗 المصدر الموثّق: <a href=\"{_esc(src_url)}\">{_esc(src_name_ar)}</a>"
        )
        keyboard = [
            [{"text": "📊 افتح منصة ريت باث التفاعلية", "web_app": {"url": launch_url}}],
            [{"text": f"🔗 {src_name_ar} — المصدر الأصلي", "url": src_url}],
        ]
    else:
        # Backward-compatible legacy bilingual format when single channel var is used
        text = (
            f"⚡ <b>RatePath | ريت باث — Live Macro Flash</b>\n\n"
            f"🇬🇧 <b>{_esc(en_block.get('title'))}</b>\n"
            f"{_esc(en_block.get('summary'))}\n\n"
            f"🇸🇦 <b>{_esc(ar_block.get('title'))}</b>\n"
            f"{_esc(ar_block.get('summary'))}\n\n"
            f"📊 <code>XAU/USD: ${_esc(gold_oz)}</code>  •  <code>BTC/USD: ${_esc(btc_spot)}</code>\n"
            f"🔗 Source / المصدر: <a href=\"{_esc(src_url)}\">{_esc(src_name)}</a>"
        )
        keyboard = [
            [{"text": "📊 Open RatePath Terminal | فتح منصة ريت باث", "web_app": {"url": mini_app_url}}],
            [{"text": f"🔗 {src_name} — Verified Source", "url": src_url}],
        ]

    return {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard},
    }


def build_vip_channel_payload(
    news_item: dict[str, Any],
    feed_payload: dict[str, Any],
    *,
    chat_id: str,
    mini_app_url: str = "https://ratepath.io/",
    lang: ChannelLang = "en",
) -> dict[str, Any]:
    """Build pure-EN (`?lang=en`), pure-AR (`?lang=ar`), or legacy VIP institutional brief."""
    twin = match_closest_historical_twin(news_item)
    spreads = extract_high_yield_funding_opportunities(feed_payload)

    en_block = news_item.get("en", {})
    ar_block = news_item.get("ar", {})
    pw = news_item.get("priced_in_warning", {})
    biases = news_item.get("assets_bias_24h", {})
    src_name = str(news_item.get("source_name", "Global Wire"))
    src_name_ar = _SOURCE_AR_LABELS.get(src_name, src_name)
    src_url = str(news_item.get("source_url", "https://ratepath.io/"))
    twin_moves = " • ".join(f"{k}: {v}%" for k, v in twin.get("realized_changes_pct", {}).items())

    if lang == "en":
        launch_url = build_lang_mini_app_url(mini_app_url, "en")
        bias_line = " | ".join(f"{k}: {str(v).upper()}" for k, v in biases.items())
        pw_badge = (
            f"⚠️ HIGH PRICED-IN RISK ({str(pw.get('severity', 'low')).upper()})"
            if pw.get("active")
            else f"✅ FRESH CATALYST ({str(pw.get('severity', 'low')).upper()})"
        )
        if spreads:
            funding_lines = "\n".join(
                f"• <code>{_esc(s['symbol'])}</code>: <b>{_esc(s['spread_bps'])} bps</b> "
                f"(<b>{_esc(s['annualized_spread_pct'])}% APR</b>) → <code>{_esc(s['arbitrage_signal'])}</code>"
                for s in spreads
            )
        else:
            funding_lines = "• All BTC/ETH/SOL perpetual spreads currently inside neutral band (&lt;2.00% APR)."

        text = (
            f"🏛️ <b>RatePath VIP — Institutional Desk Brief</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 <b>{_esc(en_block.get('title'))}</b>\n"
            f"{_esc(en_block.get('macro_thesis'))}\n\n"
            f"🧭 <b>24h Institutional Biases:</b>\n<code>{_esc(bias_line)}</code>\n\n"
            f"🛡️ <b>{_esc(pw_badge)}</b>\n"
            f"{_esc(pw.get('en'))}\n\n"
            f"🧬 <b>Historical Macro Twin Projection:</b>\n"
            f"• Match: <i>{_esc(twin.get('headline_en'))}</i>\n"
            f"• Realized 24h Reaction: <code>{_esc(twin_moves)}</code>\n"
            f"• Calibration Lesson: {_esc(twin.get('lesson_en'))}\n\n"
            f"💎 <b>Perpetual Funding Arbitrage (8h Carry):</b>\n"
            f"{funding_lines}\n\n"
            f"🔗 Provenance: <a href=\"{_esc(src_url)}\">{_esc(src_name)}</a>"
        )
        keyboard = [[{"text": "🚀 Launch RatePath VIP Terminal", "web_app": {"url": launch_url}}]]

    elif lang == "ar":
        launch_url = build_lang_mini_app_url(mini_app_url, "ar")
        bias_line_ar = " | ".join(
            f"{k}: {_BIAS_AR_LABELS.get(str(v), str(v))}" for k, v in biases.items()
        )
        sev_ar = _SEVERITY_AR_LABELS.get(str(pw.get("severity", "low")), "منخفض")
        pw_badge_ar = (
            f"⚠️ تحذير تسعير مسبق ({sev_ar})"
            if pw.get("active")
            else f"✅ محفز سعري جديد ({sev_ar})"
        )
        if spreads:
            funding_lines_ar = "\n".join(
                f"• <code>{_esc(s['symbol'])}</code>: فرق <b>{_esc(s['spread_bps'])} نقطة أساس</b> "
                f"(عائد سنوي <b>{_esc(s['annualized_spread_pct'])}%</b>) ← {_esc(_translate_arbitrage_signal_ar(s['arbitrage_signal']))}"
                for s in spreads
            )
        else:
            funding_lines_ar = "• جميع فروق التمويل الدائمة للعملات القيادية ضمن النطاق المحايد حالياً (أقل من 2.00% سنوياً)."

        text = (
            f"🏛️ <b>ريت باث لكبار المشتركين — التقرير المؤسسي الفوري</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 <b>{_esc(ar_block.get('title'))}</b>\n"
            f"{_esc(ar_block.get('macro_thesis'))}\n\n"
            f"🧭 <b>توقعات اتجاه الأصول خلال 24 ساعة:</b>\n<code>{_esc(bias_line_ar)}</code>\n\n"
            f"🛡️ <b>{_esc(pw_badge_ar)}</b>\n"
            f"{_esc(pw.get('ar'))}\n\n"
            f"🧬 <b>إسقاط توأم الحدث التاريخي:</b>\n"
            f"• الحدث المطابق: <i>{_esc(twin.get('headline_ar'))}</i>\n"
            f"• الحركة التاريخية المتحققة خلال 24 ساعة: <code>{_esc(twin_moves)}</code>\n"
            f"• درس المعايرة المؤسسي: {_esc(twin.get('lesson_ar'))}\n\n"
            f"💎 <b>فرص مراجحة فروق التمويل الدائمة (كل 8 ساعات):</b>\n"
            f"{funding_lines_ar}\n\n"
            f"🔗 المصدر الموثّق: <a href=\"{_esc(src_url)}\">{_esc(src_name_ar)}</a>"
        )
        keyboard = [[{"text": "🚀 فتح تيرمينال ريت باث الفوري", "web_app": {"url": launch_url}}]]

    else:
        # Backward-compatible legacy bilingual VIP format
        bias_line = " | ".join(f"{k}: {str(v).upper()}" for k, v in biases.items())
        pw_badge = (
            f"⚠️ HIGH PRICED-IN RISK ({str(pw.get('severity', 'low')).upper()})"
            if pw.get("active")
            else f"✅ FRESH CATALYST ({str(pw.get('severity', 'low')).upper()})"
        )
        if spreads:
            funding_lines = "\n".join(
                f"• <code>{_esc(s['symbol'])}</code>: <b>{_esc(s['spread_bps'])} bps</b> "
                f"(<b>{_esc(s['annualized_spread_pct'])}% APR</b>) → <code>{_esc(s['arbitrage_signal'])}</code>"
                for s in spreads
            )
        else:
            funding_lines = "• All BTC/ETH/SOL perpetual spreads currently inside neutral band (&lt;2.00% APR)."

        text = (
            f"🏛️ <b>RatePath VIP | ريت باث لكبار المشتركين</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📰 <b>{_esc(en_block.get('title'))}</b>\n"
            f"🇸🇦 <b>{_esc(ar_block.get('title'))}</b>\n\n"
            f"🧭 <b>24h Institutional Biases:</b>\n<code>{_esc(bias_line)}</code>\n\n"
            f"🛡️ <b>{_esc(pw_badge)}</b>\n"
            f"• EN: {_esc(pw.get('en'))}\n"
            f"• AR: {_esc(pw.get('ar'))}\n\n"
            f"🧬 <b>Historical Macro Twin Projection (توأم الحدث التاريخي):</b>\n"
            f"• Match: <i>{_esc(twin.get('headline_en'))}</i>\n"
            f"• المطابقة: <i>{_esc(twin.get('headline_ar'))}</i>\n"
            f"• Realized 24h Reaction: <code>{_esc(twin_moves)}</code>\n"
            f"• Lesson: {_esc(twin.get('lesson_ar'))}\n\n"
            f"💎 <b>Perpetual Funding Arbitrage (فروق عقود التمويل):</b>\n"
            f"{funding_lines}\n\n"
            f"🔗 Provenance: <a href=\"{_esc(src_url)}\">{_esc(src_name)}</a>"
        )
        keyboard = [
            [{"text": "🚀 Launch VIP Terminal | فتح تيرمينال ريت باث", "web_app": {"url": mini_app_url}}]
        ]

    return {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": keyboard},
    }


def build_admin_watchdog_payload(
    alert_title: str,
    details: str,
    *,
    admin_chat_id: str,
) -> dict[str, Any]:
    """Build silent (`disable_notification=True`) operational watchdog message for ADMIN_CHAT_ID."""
    text = (
        f"🛠️ <b>[RatePath Watchdog | مراقب النظام]</b>\n"
        f"<b>Event:</b> {_esc(alert_title)}\n"
        f"<b>Timestamp:</b> <code>{_utc_now_iso()}</code>\n"
        f"<b>Details:</b> <code>{_esc(details[:900])}</code>"
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
) -> dict[str, Any]:
    """POST `sendMessage` via Telegram Bot API with channel `web_app` -> `url` fallback."""
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = await client.post(
        api_url,
        headers={"Content-Type": "application/json"},
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )

    if resp.status_code == 400 and "BUTTON_TYPE_INVALID" in resp.text:
        fallback_payload = json.loads(json.dumps(payload, ensure_ascii=False))
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
                        mini_app_url=cfg.mini_app_url,
                        lang=lang_mode,
                    )
                else:
                    payload = build_vip_channel_payload(
                        item,
                        feed_payload,
                        chat_id=chat_id,
                        mini_app_url=cfg.mini_app_url,
                        lang=lang_mode,
                    )

                try:
                    await _send_telegram_payload(http_client, cfg.telegram_bot_token, payload)
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
