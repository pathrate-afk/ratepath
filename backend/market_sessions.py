"""Synchronized Middle East & US Market Session Open/Close Briefings (Pure Decimal, ZERO floats).

Automatically dispatches 4 crisp daily market-bell reports (deduplicated once per session/day via
Upstash Redis + local fallback) across Public & VIP EN/AR Telegram channels:
1. Middle East Markets Open  — 10:00 Riyadh/Damascus (07:00 UTC), Sun–Thu
2. Middle East Close & US Pre-Open Sync — 15:00 Riyadh (12:00 UTC), Sun–Thu
3. US Wall Street Open       — 09:30 New York (13:30 UTC EDT / 14:30 UTC EST), Mon–Fri
4. US Wall Street Close      — 16:00 New York (20:00 UTC EDT / 21:00 UTC EST), Mon–Fri

Formatting Guarantees:
- Zero filler ("القناعة 76/100", repetitive "VIP" reminders, "ذاكرة 100 عام").
- Clean arrows: `▲` (up) and `🔻` (red down) — zero colored balls or blue square arrows.
- Public channel includes a clean promotional invitation to `https://ratepathlive.com` & `@RatePathTerminalBot`.
- VIP channel delivers extended multi-asset session benchmarks + directional momentum takeaways.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx

from backend.config import WORKSPACE_ROOT, Settings, enforce_utf8_runtime, sanitize_syria_flag_emoji
from backend.math_engine import QUANT_2DP, QUANT_4DP, format_decimal, parse_strict_decimal
from backend.rates_bulletin import extract_tracked_rates
from backend.telegram_bot import _esc, _send_telegram_payload, normalize_preserve_newlines

LOCAL_SESSION_STATE_PATH: Path = WORKSPACE_ROOT / "backend" / "market_sessions_state.tmp.json"
SESSION_TRIGGER_WINDOW_MINUTES: int = 25

SessionId = Literal["me_open", "me_close_us_preopen", "us_open", "us_close"]


@dataclass(frozen=True)
class SessionSpec:
    session_id: SessionId
    title_ar: str
    title_en: str
    subtitle_ar: str
    subtitle_en: str


SESSION_SPECS: dict[SessionId, SessionSpec] = {
    "me_open": SessionSpec(
        session_id="me_open",
        title_ar="🔔 افتتاح أسواق الشرق الأوسط — الأسعار الافتتاحية",
        title_en="🔔 Middle East Markets Open — Benchmark Prices",
        subtitle_ar="بداية جلسة الخليج والشرق الأوسط (الرياض / دبي / الدوحة)",
        subtitle_en="Gulf & MENA session opening bell (Riyadh / Dubai / Doha)",
    ),
    "me_close_us_preopen": SessionSpec(
        session_id="me_close_us_preopen",
        title_ar="🔁 إغلاق الشرق الأوسط وتهيّؤ السوق الأمريكي",
        title_en="🔁 Middle East Close & US Pre-Open Handoff",
        subtitle_ar="تزامن إغلاق جلسة الشرق الأوسط مع بدء تسعير ما قبل السوق الأمريكي",
        subtitle_en="MENA closing bell synchronized with Wall Street pre-market liquidity",
    ),
    "us_open": SessionSpec(
        session_id="us_open",
        title_ar="🇺🇸 افتتاح السوق الأمريكي (وول ستريت) — أسعار الجلسة",
        title_en="🇺🇸 US Wall Street Open — Live Session Benchmarks",
        subtitle_ar="قرع جرس نيويورك (NYSE / NASDAQ) ودخول السيولة الأمريكية",
        subtitle_en="New York opening bell (NYSE / NASDAQ) — US liquidity active",
    ),
    "us_close": SessionSpec(
        session_id="us_close",
        title_ar="🏁 إغلاق السوق الأمريكي (وول ستريت) — خلاصة اليوم",
        title_en="🏁 US Wall Street Close — Daily Settlement Wrap",
        subtitle_ar="إغلاق وول ستريت وتسوية أسعار العملات والذهب والبيتكوين",
        subtitle_en="New York closing bell — FX, Gold & Bitcoin settlement snapshot",
    ),
}


def is_us_daylight_saving(now_utc: datetime) -> bool:
    """Compute US Eastern Daylight Time (EDT vs EST) purely in UTC without external tzdata."""
    year = now_utc.year
    # 2nd Sunday of March at 07:00 UTC (02:00 EST)
    mar1 = datetime(year, 3, 1, 7, 0, tzinfo=timezone.utc)
    days_to_sun_mar = (6 - mar1.weekday()) % 7
    dst_start = mar1 + timedelta(days=days_to_sun_mar + 7)
    # 1st Sunday of November at 06:00 UTC (02:00 EDT)
    nov1 = datetime(year, 11, 1, 6, 0, tzinfo=timezone.utc)
    days_to_sun_nov = (6 - nov1.weekday()) % 7
    dst_end = nov1 + timedelta(days=days_to_sun_nov)
    return dst_start <= now_utc < dst_end


def detect_due_market_session(
    now_utc: datetime | None = None,
    *,
    window_minutes: int = SESSION_TRIGGER_WINDOW_MINUTES,
) -> SessionId | None:
    """Return active session bell ID if `now_utc` falls inside its `[0, window_minutes]` bell window."""
    dt = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    weekday = dt.weekday()  # Mon=0 .. Fri=4, Sat=5, Sun=6
    minutes_of_day = dt.hour * 60 + dt.minute

    us_dst = is_us_daylight_saving(dt)
    us_open_min = (13 * 60 + 30) if us_dst else (14 * 60 + 30)
    us_close_min = (20 * 60) if us_dst else (21 * 60)

    # Middle East trading days: Sunday (6) through Thursday (3)
    me_trading_day = weekday in (6, 0, 1, 2, 3)
    # US trading days: Monday (0) through Friday (4)
    us_trading_day = weekday in (0, 1, 2, 3, 4)

    candidates: list[tuple[SessionId, bool, int]] = [
        ("me_open", me_trading_day, 7 * 60),
        ("me_close_us_preopen", me_trading_day, 12 * 60),
        ("us_open", us_trading_day, us_open_min),
        ("us_close", us_trading_day, us_close_min),
    ]
    for sess_id, is_day_ok, target_min in candidates:
        if is_day_ok and 0 <= (minutes_of_day - target_min) < window_minutes:
            return sess_id
    return None


def _fmt_num(val: Decimal, quant: Decimal) -> str:
    s = format_decimal(val, quant)
    if "." in s:
        i_part, f_part = s.split(".", 1)
        return f"{int(i_part):,}.{f_part}"
    return f"{int(s):,}"


def _extract_crypto_24h_pct(catalog_payload: dict[str, Any] | None, sym: str) -> str:
    if not isinstance(catalog_payload, dict):
        return ""
    for coin in catalog_payload.get("crypto") or []:
        if isinstance(coin, dict) and str(coin.get("symbol", "")).upper() == sym:
            raw = str(coin.get("change_24h_pct") or "").strip()
            if not raw or raw in ("0", "0.0", "0.00", "+0.00", "-0.00"):
                return ""
            if raw.startswith("-"):
                return f" 🔻 ({raw}%)"
            clean = raw.lstrip("+")
            return f" ▲ (+{clean}%)"
    return ""


def build_session_brief_payload(
    session_id: SessionId,
    feed_payload: dict[str, Any],
    catalog_payload: dict[str, Any] | None = None,
    *,
    chat_id: str,
    lang: Literal["en", "ar"] = "ar",
    is_vip: bool = False,
    public_site_url: str = "https://ratepathlive.com",
) -> dict[str, Any]:
    """Build crisp Market Session Open/Close report (zero filler, clean ▲ / 🔻 arrows)."""
    spec = SESSION_SPECS[session_id]
    rates = extract_tracked_rates(feed_payload, catalog_payload)
    site_base = (public_site_url or "https://ratepathlive.com").strip().rstrip("/")

    gold_lines: list[str] = []
    if "XAU_OZ" in rates:
        lbl = "أونصة الذهب" if lang == "ar" else "Gold Ounce (XAU)"
        gold_lines.append(f"• {lbl}: <code>${_fmt_num(rates['XAU_OZ'], QUANT_2DP)}</code>")
    if "GRAM_21K" in rates:
        lbl = "غرام 21" if lang == "ar" else "Gold 21K Gram"
        gold_lines.append(f"• {lbl}: <code>${_fmt_num(rates['GRAM_21K'], QUANT_2DP)}</code>")
    if is_vip and "GRAM_24K" in rates:
        lbl = "غرام 24" if lang == "ar" else "Gold 24K Gram"
        gold_lines.append(f"• {lbl}: <code>${_fmt_num(rates['GRAM_24K'], QUANT_2DP)}</code>")
    if "XAG_OZ" in rates:
        lbl = "أونصة الفضة" if lang == "ar" else "Silver Ounce (XAG)"
        gold_lines.append(f"• {lbl}: <code>${_fmt_num(rates['XAG_OZ'], QUANT_2DP)}</code>")

    fx_lines: list[str] = []
    fx_specs = (
        ("EUR", "اليورو (EUR)", "Euro (USD/EUR)", QUANT_4DP, ""),
        ("GBP", "الإسترليني (GBP)", "Pound (USD/GBP)", QUANT_4DP, ""),
        ("TRY", "الليرة التركية", "Turkish Lira (TRY)", QUANT_2DP, " TRY"),
        ("EGP", "الجنيه المصري", "Egyptian Pound (EGP)", QUANT_2DP, " EGP"),
        ("SAR", "الريال السعودي", "Saudi Riyal (SAR)", QUANT_4DP, " SAR"),
        ("AED", "الدرهم الإماراتي", "UAE Dirham (AED)", QUANT_4DP, " AED"),
        ("SYP", "الليرة السورية", "SYP", QUANT_2DP, " ل.س" if lang == "ar" else " SYP"),
    )
    for code, ar_lbl, en_lbl, q, suf in fx_specs:
        if code not in rates:
            continue
        if not is_vip and code in ("GBP", "AED"):
            continue  # Keep public compact; VIP gets the full currency desk
        lbl = ar_lbl if lang == "ar" else en_lbl
        fx_lines.append(f"• {lbl}: <code>{_fmt_num(rates[code], q)}{suf}</code>")

    crypto_lines: list[str] = []
    crypto_specs = (
        ("BTC", "بيتكوين (BTC)", "Bitcoin (BTC)"),
        ("ETH", "إيثيريوم (ETH)", "Ethereum (ETH)"),
        ("SOL", "سولانا (SOL)", "Solana (SOL)"),
    )
    for sym, ar_lbl, en_lbl in crypto_specs:
        if sym not in rates:
            continue
        if not is_vip and sym == "SOL":
            continue
        lbl = ar_lbl if lang == "ar" else en_lbl
        chg_tag = _extract_crypto_24h_pct(catalog_payload, sym)
        crypto_lines.append(f"• {lbl}: <code>${_fmt_num(rates[sym], QUANT_2DP)}</code>{chg_tag}")

    sections: list[str] = [
        f"<b>{_esc(spec.title_ar if lang == 'ar' else spec.title_en)}</b>\n<i>{_esc(spec.subtitle_ar if lang == 'ar' else spec.subtitle_en)}</i>"
    ]
    if gold_lines:
        hdr = "🟡 <b>الذهب والفضة:</b>" if lang == "ar" else "🟡 <b>Gold &amp; Silver:</b>"
        sections.append("\n".join([hdr] + gold_lines))
    if fx_lines:
        hdr = "💱 <b>العملات (مقابل الدولار):</b>" if lang == "ar" else "💱 <b>Currencies (vs USD):</b>"
        sections.append("\n".join([hdr] + fx_lines))
    if crypto_lines:
        hdr = "₿ <b>العملات الرقمية:</b>" if lang == "ar" else "₿ <b>Crypto:</b>"
        sections.append("\n".join([hdr] + crypto_lines))

    if is_vip:
        # Executive liquidity note for paid subscribers (no repetitive "VIP" nagging!)
        note = (
            "🧭 <b>ملاحظة الجلسة:</b> راقب تدفق السيولة خلال أول 30 دقيقة بعد الجرس؛ البيانات للمتابعة المعلوماتية فقط وليست نصيحة استثمارية."
            if lang == "ar"
            else "🧭 <b>Session Desk Note:</b> Watch liquidity depth in the first 30m after the bell; informational data only, not financial advice."
        )
        sections.append(note)
    else:
        # Clean promotional conversion footer for Public channels
        promo = (
            f"🌐 <b>المحوّل الفوري، أسهم الشركات، والأخبار الحية:</b>\n{site_base}/?lang=ar\n"
            f"🤖 لمتابعة عملاتك وأسهمك بتنبيهات خاصة: @RatePathTerminalBot"
            if lang == "ar"
            else f"🌐 <b>Live Converter, Global Stocks &amp; Market News:</b>\n{site_base}/?lang=en\n"
            f"🤖 Personal Tracker Bot &amp; Deep Desk Briefs: @RatePathTerminalBot"
        )
        sections.append(promo)

    text = sanitize_syria_flag_emoji(normalize_preserve_newlines("\n\n".join(sections)), lang=lang)
    return {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }


async def _claim_session_slot(
    slot_key: str,
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    local_state_path: Path = LOCAL_SESSION_STATE_PATH,
) -> bool:
    """Atomically claim a session slot once per day via Upstash Redis + local JSON fallback."""
    local_map: dict[str, str] = {}
    if local_state_path.exists():
        try:
            loaded = json.loads(local_state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                local_map = loaded
        except Exception:
            local_map = {}

    if slot_key in local_map:
        return False

    claimed = True
    if settings.has_live_upstash:
        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
        try:
            resp = await http_client.post(
                settings.upstash_redis_rest_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {settings.upstash_redis_rest_token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(["SET", slot_key, "1", "EX", "86400", "NX"], ensure_ascii=False).encode("utf-8"),
            )
            resp.raise_for_status()
            claimed = json.loads(resp.text).get("result") == "OK"
        except Exception:
            claimed = True
        finally:
            if owns_client:
                await http_client.aclose()

    local_map[slot_key] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if len(local_map) > 100:
        local_map = dict(list(local_map.items())[-100:])
    local_state_path.parent.mkdir(parents=True, exist_ok=True)
    local_state_path.write_text(json.dumps(local_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return claimed


async def maybe_broadcast_market_session(
    feed_payload: dict[str, Any],
    catalog_payload: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    now_utc: datetime | None = None,
    local_state_path: Path = LOCAL_SESSION_STATE_PATH,
) -> dict[str, Any]:
    """Check if Middle East or US open/close bell is due and broadcast once across all 4 channels."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    dt = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    due_session = detect_due_market_session(dt)
    if not due_session:
        return {"status": "outside_session_window", "session": None, "sent": 0}

    if not cfg.has_live_telegram:
        return {"status": "skipped_placeholder_telegram", "session": due_session, "sent": 0}

    slot_key = f"ratepath:session:{due_session}:{dt.strftime('%Y-%m-%d')}"
    is_new = await _claim_session_slot(slot_key, cfg, client=client, local_state_path=local_state_path)
    if not is_new:
        return {"status": "already_sent_today", "session": due_session, "sent": 0}

    targets = cfg.resolve_telegram_targets()
    dispatch_specs: tuple[tuple[str, Literal["en", "ar"], bool], ...] = (
        ("public_en", "en", False),
        ("vip_en", "en", True),
        ("public_ar", "ar", False),
        ("vip_ar", "ar", True),
    )

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    sent = 0
    try:
        for key, lang_code, is_vip_ch in dispatch_specs:
            chat_id = targets.get(key)
            if not chat_id:
                continue
            payload = build_session_brief_payload(
                due_session,
                feed_payload,
                catalog_payload,
                chat_id=chat_id,
                lang=lang_code,
                is_vip=is_vip_ch,
                public_site_url=cfg.public_site_url,
            )
            await _send_telegram_payload(
                http_client,
                cfg.telegram_bot_token,
                payload,
                enforce_paragraph_spacing=True,
            )
            sent += 1
    finally:
        if owns_client:
            await http_client.aclose()

    return {"status": "sent", "session": due_session, "sent": sent}
