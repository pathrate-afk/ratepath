"""Personal AI Tracker Bot Alert & Daily Digest Worker (Pure Decimal, ZERO floats).

Runs safely inside Lane-1 (`backend/run_cycle.py`) when Supabase + Telegram Bot Token are active:
- Loads linked `public.user_tracker_profiles` (`telegram_chat_id IS NOT NULL`).
- Evaluates each user's custom watchlist across World Currencies, Gold/Silver, Crypto & Global Stocks.
- Fires private 1-on-1 Telegram alerts when an asset crosses `alert_above` or `alert_below`
  (with a 4-hour anti-spam cooldown per item).
- Dispatches an optional daily custom digest (at `digest_hour_utc`) containing live prices &
  latest company/market news headlines strictly framed as informational data (never financial advice).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote_plus

import httpx

from backend.config import SUPABASE_SERVER_USER_AGENT, Settings
from backend.evaluator import normalize_supabase_rest_base
from backend.math_engine import parse_strict_decimal

ALERT_COOLDOWN_HOURS: int = 4
_YAHOO_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RatePathPersonalBot/2.0",
}
_ITEM_TITLE_RE = re.compile(
    r"<item>[\s\S]*?<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>[\s\S]*?<\/item>",
    re.IGNORECASE,
)


def _escape_html(text: str) -> str:
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def resolve_price_from_feeds(
    symbol: str,
    category: str,
    live_feed: dict[str, Any] | None,
    catalog_feed: dict[str, Any] | None,
) -> Decimal | None:
    """Resolve exact Decimal price for a watched fiat, metal, or crypto symbol from local feeds."""
    sym = symbol.strip().upper()
    cat = category.strip().lower()

    if sym in ("XAU", "XAU_OZ", "GOLD"):
        val = (live_feed or {}).get("gold", {}).get("prices", {}).get("XAU_OZ") or (
            (catalog_feed or {}).get("metals", {}).get("XAU_OZ", {}).get("price_usd")
        )
        return parse_strict_decimal(str(val), field_name="tracker.XAU") if val else None
    if sym in ("GRAM_24K", "GRAM_21K", "GRAM_18K"):
        val = (live_feed or {}).get("gold", {}).get("prices", {}).get(sym)
        return parse_strict_decimal(str(val), field_name=f"tracker.{sym}") if val else None
    if sym in ("XAG", "XAG_OZ", "SILVER"):
        val = (catalog_feed or {}).get("metals", {}).get("XAG_OZ", {}).get("price_usd")
        return parse_strict_decimal(str(val), field_name="tracker.XAG") if val else None
    if sym == "XAG_GRAM":
        val = (catalog_feed or {}).get("metals", {}).get("XAG_GRAM", {}).get("price_usd")
        return parse_strict_decimal(str(val), field_name="tracker.XAG_GRAM") if val else None

    if cat == "fiat":
        fiat_entry = ((catalog_feed or {}).get("fiat", {}) or {}).get(sym) or (
            ((live_feed or {}).get("fiat", {}) or {}).get("rates", {}) or {}
        ).get(sym)
        if isinstance(fiat_entry, dict) and fiat_entry.get("units_per_usd"):
            return parse_strict_decimal(str(fiat_entry["units_per_usd"]), field_name=f"tracker.{sym}")

    # Crypto lookup
    live_c = (((live_feed or {}).get("crypto", {}) or {}).get("assets", {}) or {}).get(sym)
    if isinstance(live_c, dict) and live_c.get("spot_price"):
        return parse_strict_decimal(str(live_c["spot_price"]), field_name=f"tracker.{sym}")
    for coin in (catalog_feed or {}).get("crypto", []) or []:
        if isinstance(coin, dict) and str(coin.get("symbol", "")).upper() == sym and coin.get("price_usd"):
            return parse_strict_decimal(str(coin["price_usd"]), field_name=f"tracker.{sym}")

    return None


async def fetch_stock_price_decimal(client: httpx.AsyncClient, symbol: str) -> Decimal | None:
    """Fetch live global stock/ETF/commodity price via Yahoo Finance public chart API as Decimal."""
    try:
        resp = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}",
            params={"range": "5d", "interval": "1d"},
            headers=_YAHOO_HEADERS,
            timeout=7,
        )
        if resp.status_code != 200:
            return None
        payload = json.loads(resp.text)
        meta = ((payload.get("chart", {}).get("result") or [{}])[0] or {}).get("meta", {})
        raw = meta.get("regularMarketPrice")
        if raw is None:
            return None
        return parse_strict_decimal(str(raw), field_name=f"yahoo.{symbol}")
    except Exception:
        return None


async def fetch_company_news_titles(
    client: httpx.AsyncClient, symbol: str, name: str, *, limit: int = 2
) -> list[str]:
    """Fetch concise company/asset RSS headlines (zero API key)."""
    topic = f"{name or symbol} {symbol} market news"
    try:
        resp = await client.get(
            f"https://news.google.com/rss/search?q={quote_plus(topic)}&hl=en-US&gl=US&ceid=US:en",
            headers={"User-Agent": "RatePathPersonalBot/2.0", "Accept": "application/rss+xml, text/xml"},
            timeout=7,
        )
        if resp.status_code != 200:
            return []
        titles: list[str] = []
        for match in _ITEM_TITLE_RE.finditer(resp.text):
            cleaned = re.sub(r"<[^>]+>", " ", match.group(1))
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if len(cleaned) >= 12:
                titles.append(cleaned[:140])
            if len(titles) >= limit:
                break
        return titles
    except Exception:
        return []


def check_alert_trigger(
    current_price: Decimal,
    alert_above_raw: str | None,
    alert_below_raw: str | None,
) -> str | None:
    """Return `'above'`, `'below'`, or `None` using exact Decimal comparison."""
    if alert_above_raw:
        try:
            target_hi = parse_strict_decimal(str(alert_above_raw), field_name="alert.above")
            if current_price >= target_hi:
                return "above"
        except Exception:
            pass
    if alert_below_raw:
        try:
            target_lo = parse_strict_decimal(str(alert_below_raw), field_name="alert.below")
            if current_price <= target_lo:
                return "below"
        except Exception:
            pass
    return None


def _is_cooldown_elapsed(last_alerted_at: str | None, now_utc: datetime) -> bool:
    if not last_alerted_at:
        return True
    try:
        dt = datetime.fromisoformat(str(last_alerted_at).replace("Z", "+00:00")).astimezone(timezone.utc)
        return (now_utc - dt) >= timedelta(hours=ALERT_COOLDOWN_HOURS)
    except Exception:
        return True


async def _send_private_tg(
    client: httpx.AsyncClient, bot_token: str, chat_id: str, html_text: str
) -> bool:
    try:
        resp = await client.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": html_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


async def process_personal_tracker_alerts(
    live_feed: dict[str, Any] | None,
    catalog_feed: dict[str, Any] | None,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate all linked personal tracker profiles and send private price/digest alerts."""
    cfg = settings or Settings.load()
    if not (cfg.has_live_supabase and cfg.has_live_telegram):
        return {"status": "skipped_unconfigured", "profiles_checked": 0, "alerts_sent": 0, "digests_sent": 0}

    now_dt = now_utc or datetime.now(timezone.utc)
    today_iso = now_dt.strftime("%Y-%m-%d")
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    rest_base = normalize_supabase_rest_base(cfg.supabase_url)
    supa_headers = {
        "apikey": cfg.supabase_key,
        "Authorization": f"Bearer {cfg.supabase_key}",
        "User-Agent": SUPABASE_SERVER_USER_AGENT,
        "Content-Type": "application/json",
    }

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    alerts_sent = 0
    digests_sent = 0
    profiles_checked = 0

    try:
        resp = await http_client.get(
            f"{rest_base}/user_tracker_profiles",
            params={
                "telegram_chat_id": "not.is.null",
                "select": "user_id,telegram_chat_id,lang,plan_tier,watchlist,daily_digest_enabled,digest_hour_utc,last_digest_date",
                "limit": "200",
            },
            headers=supa_headers,
        )
        if resp.status_code != 200:
            return {"status": f"supabase_http_{resp.status_code}", "profiles_checked": 0, "alerts_sent": 0, "digests_sent": 0}
        profiles = json.loads(resp.text)
        if not isinstance(profiles, list):
            return {"status": "ok", "profiles_checked": 0, "alerts_sent": 0, "digests_sent": 0}

        stock_price_cache: dict[str, Decimal | None] = {}

        for prof in profiles:
            if not isinstance(prof, dict):
                continue
            chat_id = str(prof.get("telegram_chat_id") or "").strip()
            watchlist = prof.get("watchlist")
            if not chat_id or not isinstance(watchlist, list) or not watchlist:
                continue

            profiles_checked += 1
            lang = "en" if str(prof.get("lang", "ar")).lower() == "en" else "ar"
            updated_watchlist: list[dict[str, Any]] = []
            watchlist_mutated = False
            digest_lines: list[str] = []

            for raw_item in watchlist:
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                sym = str(item.get("symbol", "")).strip().upper()
                cat = str(item.get("category", "crypto")).strip().lower()
                name = str(item.get("name_ar") if lang == "ar" else item.get("name_en") or sym)
                if not sym:
                    continue

                price = resolve_price_from_feeds(sym, cat, live_feed, catalog_feed)
                if price is None and cat == "stock":
                    if sym not in stock_price_cache:
                        stock_price_cache[sym] = await fetch_stock_price_decimal(http_client, sym)
                    price = stock_price_cache[sym]

                if price is not None:
                    price_str = format(price, "f")
                    digest_lines.append(f"• <b>{_escape_html(name)}</b> (<code>{_escape_html(sym)}</code>): <code>{price_str}</code>")
                    trigger = check_alert_trigger(
                        price,
                        item.get("alert_above"),
                        item.get("alert_below"),
                    )
                    if trigger and _is_cooldown_elapsed(item.get("last_alerted_at"), now_dt):
                        arrow = "🟢 ⬆️" if trigger == "above" else "🔴 ⬇️"
                        target_val = item.get("alert_above") if trigger == "above" else item.get("alert_below")
                        if lang == "ar":
                            alert_html = (
                                f"{arrow} <b>تنبيه البوت الشخصي — {_escape_html(name)} ({_escape_html(sym)})</b>\n\n"
                                f"📌 السعر الحالي: <code>{price_str}</code>\n"
                                f"🎯 المستوى المراقَب: <code>{_escape_html(str(target_val))}</code> "
                                f"({'صعوداً' if trigger == 'above' else 'هبوطاً'})\n\n"
                                f"ℹ️ <i>خدمة معلوماتية لمتابعة الأرقام فقط وليست نصيحة استثمارية.</i>"
                            )
                        else:
                            alert_html = (
                                f"{arrow} <b>Personal Bot Alert — {_escape_html(name)} ({_escape_html(sym)})</b>\n\n"
                                f"📌 Current Price: <code>{price_str}</code>\n"
                                f"🎯 Watched Level: <code>{_escape_html(str(target_val))}</code> "
                                f"({'Crossed Above' if trigger == 'above' else 'Dropped Below'})\n\n"
                                f"ℹ️ <i>Informational price monitoring service only — not financial advice.</i>"
                            )
                        if await _send_private_tg(http_client, cfg.telegram_bot_token, chat_id, alert_html):
                            alerts_sent += 1
                            item["last_alerted_at"] = now_iso
                            watchlist_mutated = True

                updated_watchlist.append(item)

            # Daily custom digest dispatch (once per UTC day at user's chosen hour)
            digest_due = (
                bool(prof.get("daily_digest_enabled", True))
                and str(prof.get("last_digest_date") or "") != today_iso
                and now_dt.hour == int(prof.get("digest_hour_utc", 7))
                and len(digest_lines) > 0
            )
            patch_body: dict[str, Any] = {}
            if watchlist_mutated:
                patch_body["watchlist"] = updated_watchlist

            if digest_due:
                news_blocks: list[str] = []
                for it in updated_watchlist[:3]:
                    if it.get("news_alerts", True):
                        titles = await fetch_company_news_titles(
                            http_client, str(it.get("symbol", "")), str(it.get("name_en", "")), limit=1
                        )
                        if titles:
                            news_blocks.append(f"• <b>{_escape_html(str(it.get('symbol', '')))}:</b> {_escape_html(titles[0])}")

                if lang == "ar":
                    news_sec = ("\n\n📰 <b>أحدث أخبار قائمتك:</b>\n" + "\n".join(news_blocks)) if news_blocks else ""
                    digest_html = (
                        f"🤖 <b>ملخص بوت المتابعة الشخصي — ريت باث</b>\n\n"
                        + "\n".join(digest_lines[:15])
                        + news_sec
                        + "\n\nℹ️ <i>ملخص معلوماتي للأرقام والأخبار فقط؛ لا نضمن اتجاه السوق ولا نقدم توصية استثمارية.</i>"
                    )
                else:
                    news_sec = ("\n\n📰 <b>Latest Watchlist News:</b>\n" + "\n".join(news_blocks)) if news_blocks else ""
                    digest_html = (
                        f"🤖 <b>RatePath Personal Tracker Digest</b>\n\n"
                        + "\n".join(digest_lines[:15])
                        + news_sec
                        + "\n\nℹ️ <i>Informational market &amp; news summary only — not investment advice.</i>"
                    )
                if await _send_private_tg(http_client, cfg.telegram_bot_token, chat_id, digest_html):
                    digests_sent += 1
                    patch_body["last_digest_date"] = today_iso

            if patch_body:
                await http_client.patch(
                    f"{rest_base}/user_tracker_profiles",
                    params={"user_id": f"eq.{prof['user_id']}"},
                    headers={**supa_headers, "Prefer": "return=minimal"},
                    content=json.dumps(patch_body, ensure_ascii=False).encode("utf-8"),
                )

        return {
            "status": "ok",
            "profiles_checked": profiles_checked,
            "alerts_sent": alerts_sent,
            "digests_sent": digests_sent,
        }
    except Exception as exc:
        return {
            "status": f"error:{type(exc).__name__}",
            "profiles_checked": profiles_checked,
            "alerts_sent": alerts_sent,
            "digests_sent": digests_sent,
        }
    finally:
        if owns_client:
            await http_client.aclose()
