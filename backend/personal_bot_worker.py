"""Personal AI Tracker Bot — High-Speed Batch Price Cache, Debounced Alert Engine & Digest Worker.

Architecture & Guarantees (Pure Decimal, ZERO floats):
1. **Decoupled Batch Price Fetching (`BatchPriceCache`)**: Never calls external APIs per user.
   Builds a single normalized price map (FX, SYP, TRY, EGP, Gold XAU/24K/21K/18K, Silver XAG,
   Crypto BTC/ETH/SOL/USDT/Top-300, and deduplicated Global Stocks) once per 30-60s window in
   memory (plus optional Upstash Redis mirror `ratepath:batch_prices_v1`). Evaluates ALL users'
   alerts instantaneously against the shared cache.
2. **Anti-Spam Debouncing & Breakout Latch**: Marks triggered breakout thresholds (`triggered_above_target`
   / `triggered_below_target`) and enforces a cooldown so price oscillation around a target NEVER spams.
3. **Tier Priority & Post-Value Conversion Nudge**:
   - Evaluates Pro ($19/mo, 50 assets) and VIP ($10/mo, 10 assets) before Free (3 assets).
   - Appends a subtle 1-line `/upgrade` nudge whenever a Free user receives a triggered alert.
4. **Resilience, Exponential Backoff & 403 Blocked Handling**:
   - Retries Telegram 429 (honoring `retry_after`), 5xx, and transient network errors with
     integer exponential backoff (1s -> 2s -> 4s).
   - Gracefully detects HTTP 403 Forbidden (user blocked the bot) and sets `alerts_paused=true`
     in Supabase & Redis so the loop never crashes or wastes quota.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import quote_plus

import httpx

from backend.config import (
    CATALOG_FEED_TARGET_PATH,
    LIVE_FEED_TARGET_PATH,
    SUPABASE_SERVER_USER_AGENT,
    Settings,
    enforce_utf8_runtime,
)
from backend.evaluator import normalize_supabase_rest_base
from backend.math_engine import parse_strict_decimal

logger = logging.getLogger("ratepath.personal_bot")

ALERT_COOLDOWN_HOURS: int = 4
DEFAULT_PRICE_CACHE_TTL_SECONDS: int = 45
BATCH_PRICE_REDIS_KEY: str = "ratepath:batch_prices_v1"
TG_USERS_INDEX_REDIS_KEY: str = "ratepath:tg_users:index"
TG_USER_REDIS_PREFIX: str = "ratepath:tg_user:"
MAX_RETRY_ATTEMPTS: int = 3
TIER_PRIORITY: dict[str, int] = {
    "monthly_personal_bot": 0,
    "monthly_business_vip": 1,
    "free": 2,
}
TIER_MAX_ASSETS: dict[str, int] = {
    "free": 3,
    "monthly_business_vip": 10,
    "monthly_personal_bot": 50,
}

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


def _utc_epoch_sec(now_dt: datetime | None = None) -> int:
    dt = now_dt or datetime.now(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = dt - epoch
    return delta.days * 86400 + delta.seconds


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
        val = (live_feed or {}).get("gold", {}).get("prices", {}).get(sym) or (
            (catalog_feed or {}).get("metals", {}).get(sym, {}).get("price_usd")
        )
        return parse_strict_decimal(str(val), field_name=f"tracker.{sym}") if val else None
    if sym in ("XAG", "XAG_OZ", "SILVER"):
        val = (catalog_feed or {}).get("metals", {}).get("XAG_OZ", {}).get("price_usd") or (
            (live_feed or {}).get("gold", {}).get("prices", {}).get("XAG_OZ")
        )
        return parse_strict_decimal(str(val), field_name="tracker.XAG") if val else None
    if sym == "XAG_GRAM":
        val = (catalog_feed or {}).get("metals", {}).get("XAG_GRAM", {}).get("price_usd") or (
            (live_feed or {}).get("gold", {}).get("prices", {}).get("XAG_GRAM")
        )
        return parse_strict_decimal(str(val), field_name="tracker.XAG_GRAM") if val else None

    if sym == "USD":
        return Decimal("1.0000")

    if cat == "fiat" or sym in ("EUR", "GBP", "TRY", "SAR", "AED", "EGP", "SYP", "JOD", "KWD", "IQD", "LBP"):
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

    if sym == "USDT":
        return Decimal("1.0000")

    return None


async def fetch_stock_price_decimal(client: httpx.AsyncClient, symbol: str) -> Decimal | None:
    """Fetch live global stock/ETF/commodity price via Yahoo Finance with exponential backoff."""
    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            resp = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(symbol)}",
                params={"range": "5d", "interval": "1d"},
                headers=_YAHOO_HEADERS,
                timeout=7,
            )
            if resp.status_code == 200:
                payload = json.loads(resp.text)
                meta = ((payload.get("chart", {}).get("result") or [{}])[0] or {}).get("meta", {})
                raw = meta.get("regularMarketPrice")
                if raw is None:
                    return None
                return parse_strict_decimal(str(raw), field_name=f"yahoo.{symbol}")
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(1 << attempt)
                continue
            return None
        except Exception:
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                await asyncio.sleep(1 << attempt)
                continue
            return None
    return None


class BatchPriceCache:
    """Single-fetch in-memory (+ optional Upstash Redis) price cache shared across ALL users.

    Ensures external market APIs are NEVER called per user: every user's alert check reads
    O(1) from this pre-populated Decimal dictionary.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_PRICE_CACHE_TTL_SECONDS) -> None:
        self.ttl_seconds = max(15, int(ttl_seconds))
        self.prices: dict[str, Decimal] = {}
        self.refreshed_at_epoch: int = 0

    def is_fresh(self, now_dt: datetime | None = None) -> bool:
        if not self.prices or self.refreshed_at_epoch <= 0:
            return False
        return (_utc_epoch_sec(now_dt) - self.refreshed_at_epoch) < self.ttl_seconds

    def get(self, symbol: str) -> Decimal | None:
        return self.prices.get(symbol.strip().upper())

    def hydrate_from_feeds(
        self,
        live_feed: dict[str, Any] | None,
        catalog_feed: dict[str, Any] | None,
        *,
        now_dt: datetime | None = None,
    ) -> dict[str, Decimal]:
        """Extract ALL FX, Metals (Gold/Silver), and Crypto spot prices in one pass."""
        snapshot: dict[str, Decimal] = {"USD": Decimal("1.0000"), "USDT": Decimal("1.0000")}

        # 1. Gold & Silver
        for key, alias in (
            ("XAU_OZ", "XAU"),
            ("GRAM_24K", "GRAM_24K"),
            ("GRAM_21K", "GRAM_21K"),
            ("GRAM_18K", "GRAM_18K"),
            ("XAG_OZ", "XAG"),
            ("XAG_GRAM", "XAG_GRAM"),
        ):
            val = resolve_price_from_feeds(key, "metal", live_feed, catalog_feed)
            if val is not None:
                snapshot[key] = val
                snapshot[alias] = val

        # 2. All World & Regional Fiat (SYP, TRY, EGP, EUR, GBP, SAR, AED, +160 FX)
        fiat_sources = [
            ((live_feed or {}).get("fiat", {}) or {}).get("rates", {}) or {},
            (catalog_feed or {}).get("fiat", {}) or {},
        ]
        for fiat_map in fiat_sources:
            if not isinstance(fiat_map, dict):
                continue
            for code, entry in fiat_map.items():
                if not isinstance(entry, dict) or not entry.get("units_per_usd"):
                    continue
                try:
                    sym = str(code).strip().upper()
                    snapshot[sym] = parse_strict_decimal(
                        str(entry["units_per_usd"]), field_name=f"batch.fiat.{sym}"
                    )
                except Exception:
                    continue

        # 3. All Crypto (Top-300 CMC + Core BTC/ETH/SOL)
        for coin in (catalog_feed or {}).get("crypto", []) or []:
            if not isinstance(coin, dict) or not coin.get("price_usd"):
                continue
            sym = str(coin.get("symbol", "")).strip().upper()
            if sym and sym not in snapshot:
                try:
                    snapshot[sym] = parse_strict_decimal(
                        str(coin["price_usd"]), field_name=f"batch.cmc.{sym}"
                    )
                except Exception:
                    continue

        core_crypto = ((live_feed or {}).get("crypto", {}) or {}).get("assets", {}) or {}
        if isinstance(core_crypto, dict):
            for sym, entry in core_crypto.items():
                if isinstance(entry, dict) and entry.get("spot_price"):
                    try:
                        clean = str(sym).strip().upper()
                        snapshot[clean] = parse_strict_decimal(
                            str(entry["spot_price"]), field_name=f"batch.crypto.{clean}"
                        )
                    except Exception:
                        continue

        self.prices.update(snapshot)
        self.refreshed_at_epoch = _utc_epoch_sec(now_dt)
        return self.prices

    async def batch_populate_stocks(
        self,
        client: httpx.AsyncClient,
        unique_symbols: set[str],
        *,
        max_concurrency: int = 6,
    ) -> int:
        """Fetch each unique watched stock symbol AT MOST ONCE across the entire user base."""
        missing = sorted(
            sym.strip().upper()
            for sym in unique_symbols
            if sym.strip() and sym.strip().upper() not in self.prices
        )
        if not missing:
            return 0

        sem = asyncio.Semaphore(max(1, max_concurrency))
        fetched = 0

        async def _fetch_one(sym: str) -> tuple[str, Decimal | None]:
            async with sem:
                return sym, await fetch_stock_price_decimal(client, sym)

        results = await asyncio.gather(*(_fetch_one(s) for s in missing), return_exceptions=True)
        for res in results:
            if isinstance(res, tuple):
                sym, val = res
                if val is not None:
                    self.prices[sym] = val
                    fetched += 1
        return fetched

    async def mirror_to_upstash(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        ttl_seconds: int = 90,
    ) -> bool:
        """Mirror compact price map into Upstash Redis so Cloudflare Edge bot reads <50ms."""
        if not settings.has_live_upstash or not self.prices:
            return False
        serializable = {k: format(v, "f") for k, v in self.prices.items()}
        payload = json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "prices": serializable,
            },
            ensure_ascii=False,
        )
        try:
            resp = await client.post(
                settings.upstash_redis_rest_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {settings.upstash_redis_rest_token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(
                    ["SET", BATCH_PRICE_REDIS_KEY, payload, "EX", str(ttl_seconds)],
                    ensure_ascii=False,
                ).encode("utf-8"),
                timeout=8,
            )
            return resp.status_code == 200
        except Exception:
            return False


_SHARED_PRICE_CACHE = BatchPriceCache(ttl_seconds=DEFAULT_PRICE_CACHE_TTL_SECONDS)


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
            cleaned = re.sub(r"[ \t\r\n]+", " ", cleaned).strip()
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


def should_fire_debounced_alert(
    item: dict[str, Any],
    trigger: str,
    target_val: str | None,
    now_utc: datetime,
) -> bool:
    """Prevent spam & oscillation race conditions using one-time latch + cooldown debounce.

    - If `one_time` is True (default for breakout targets), once the exact target value has
      triggered (`triggered_above_target == target_val`), it will NOT fire again until the
      user edits/resets the target.
    - If `one_time` is False (recurring alert), enforces `ALERT_COOLDOWN_HOURS` debounce.
    """
    if not target_val:
        return False
    one_time = item.get("one_time", True)
    latch_key = "triggered_above_target" if trigger == "above" else "triggered_below_target"
    if one_time and str(item.get(latch_key) or "").strip() == str(target_val).strip():
        return False
    return _is_cooldown_elapsed(item.get("last_alerted_at"), now_utc)


def _is_cooldown_elapsed(last_alerted_at: str | None, now_utc: datetime) -> bool:
    if not last_alerted_at:
        return True
    try:
        dt = datetime.fromisoformat(str(last_alerted_at).replace("Z", "+00:00")).astimezone(timezone.utc)
        return (now_utc - dt) >= timedelta(hours=ALERT_COOLDOWN_HOURS)
    except Exception:
        return True


def build_free_post_value_nudge(lang: str) -> str:
    """Subtle 1-line conversion footer appended ONLY to Free-tier triggered alerts."""
    if lang == "ar":
        return "💡 تريد متابعة 10 أصول مع قناة VIP؟ اضغط /upgrade"
    return "💡 Want 10 assets + VIP Channel alerts? Tap /upgrade"


async def send_telegram_with_retry(
    client: httpx.AsyncClient,
    bot_token: str,
    chat_id: str,
    html_text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    max_attempts: int = MAX_RETRY_ATTEMPTS,
) -> dict[str, Any]:
    """Send Telegram HTML message with exponential backoff on 429/5xx and 403 block detection."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    for attempt in range(max_attempts):
        try:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                return {"ok": True, "blocked": False, "status": 200}
            if resp.status_code == 403:
                # User blocked the bot or account deactivated -> pause alerts cleanly
                logger.info("Telegram 403 Forbidden for chat_id=%s; pausing alerts.", chat_id)
                return {"ok": False, "blocked": True, "status": 403}
            if resp.status_code == 429 and attempt < max_attempts - 1:
                retry_after = 1 << attempt
                try:
                    body = json.loads(resp.text)
                    ra = int((body.get("parameters") or {}).get("retry_after") or retry_after)
                    retry_after = max(1, min(ra, 10))
                except Exception:
                    pass
                await asyncio.sleep(retry_after)
                continue
            if resp.status_code in (500, 502, 503, 504) and attempt < max_attempts - 1:
                await asyncio.sleep(1 << attempt)
                continue
            return {"ok": False, "blocked": False, "status": resp.status_code}
        except Exception as exc:
            logger.warning("Telegram send retry %d failed for %s: %s", attempt + 1, chat_id, type(exc).__name__)
            if attempt < max_attempts - 1:
                await asyncio.sleep(1 << attempt)
                continue
            return {"ok": False, "blocked": False, "status": 0, "error": type(exc).__name__}
    return {"ok": False, "blocked": False, "status": 0}


async def _upstash_cmd(
    client: httpx.AsyncClient, settings: Settings, cmd: list[str]
) -> Any:
    if not settings.has_live_upstash:
        return None
    try:
        resp = await client.post(
            settings.upstash_redis_rest_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.upstash_redis_rest_token}",
                "Content-Type": "application/json",
            },
            content=json.dumps(cmd, ensure_ascii=False).encode("utf-8"),
            timeout=8,
        )
        if resp.status_code == 200:
            return json.loads(resp.text).get("result")
    except Exception:
        return None
    return None


async def _load_all_active_profiles(
    client: httpx.AsyncClient,
    settings: Settings,
    rest_base: str,
    supa_headers: dict[str, str],
) -> list[dict[str, Any]]:
    """Load deduplicated active bot profiles from Supabase (`user_tracker_profiles`,
    `telegram_bot_users`) AND Upstash Redis (`ratepath:tg_users:index`).
    """
    by_chat: dict[str, dict[str, Any]] = {}

    if settings.has_live_supabase:
        # 1. Web-linked profiles (`user_tracker_profiles`)
        try:
            resp = await client.get(
                f"{rest_base}/user_tracker_profiles",
                params={
                    "telegram_chat_id": "not.is.null",
                    "select": "user_id,telegram_chat_id,lang,plan_tier,watchlist,daily_digest_enabled,digest_hour_utc,last_digest_date,alerts_paused",
                    "limit": "500",
                },
                headers=supa_headers,
            )
            if resp.status_code != 200:
                # Fallback select without `alerts_paused` if older schema hasn't run ALTER yet
                resp = await client.get(
                    f"{rest_base}/user_tracker_profiles",
                    params={
                        "telegram_chat_id": "not.is.null",
                        "select": "user_id,telegram_chat_id,lang,plan_tier,watchlist,daily_digest_enabled,digest_hour_utc,last_digest_date",
                        "limit": "500",
                    },
                    headers=supa_headers,
                )
            if resp.status_code == 200:
                rows = json.loads(resp.text)
                if isinstance(rows, list):
                    for r in rows:
                        if isinstance(r, dict) and r.get("telegram_chat_id"):
                            cid = str(r["telegram_chat_id"]).strip()
                            by_chat[cid] = {**r, "_source": "user_tracker_profiles"}
        except Exception as exc:
            logger.warning("Failed loading user_tracker_profiles: %s", type(exc).__name__)

        # 2. Standalone Telegram bot profiles (`telegram_bot_users`)
        try:
            resp2 = await client.get(
                f"{rest_base}/telegram_bot_users",
                params={
                    "alerts_paused": "eq.false",
                    "select": "telegram_user_id,lang,plan_tier,expires_at,watchlist,alerts_paused",
                    "limit": "500",
                },
                headers=supa_headers,
            )
            if resp2.status_code == 200:
                rows2 = json.loads(resp2.text)
                if isinstance(rows2, list):
                    for r in rows2:
                        if isinstance(r, dict) and r.get("telegram_user_id"):
                            cid = str(r["telegram_user_id"]).strip()
                            if cid not in by_chat:
                                by_chat[cid] = {
                                    "telegram_chat_id": cid,
                                    "telegram_user_id": cid,
                                    "lang": r.get("lang", "ar"),
                                    "plan_tier": r.get("plan_tier", "free"),
                                    "watchlist": r.get("watchlist", []),
                                    "alerts_paused": bool(r.get("alerts_paused", False)),
                                    "daily_digest_enabled": False,
                                    "_source": "telegram_bot_users",
                                }
        except Exception:
            pass

    # 3. Upstash Redis bot user index (instant state created via Telegram inline buttons)
    if settings.has_live_upstash:
        ids = await _upstash_cmd(client, settings, ["SMEMBERS", TG_USERS_INDEX_REDIS_KEY])
        if isinstance(ids, list):
            for raw_id in ids[:500]:
                cid = str(raw_id or "").strip()
                if not cid or cid in by_chat:
                    continue
                raw_json = await _upstash_cmd(client, settings, ["GET", f"{TG_USER_REDIS_PREFIX}{cid}"])
                if not isinstance(raw_json, str) or not raw_json.strip():
                    continue
                try:
                    obj = json.loads(raw_json)
                    if isinstance(obj, dict):
                        by_chat[cid] = {
                            "telegram_chat_id": cid,
                            "telegram_user_id": cid,
                            "lang": obj.get("lang", "ar"),
                            "plan_tier": obj.get("plan_tier", "free"),
                            "watchlist": obj.get("watchlist", []),
                            "alerts_paused": bool(obj.get("alerts_paused", False)),
                            "daily_digest_enabled": False,
                            "_source": "upstash_redis",
                            "_raw_redis_obj": obj,
                        }
                except Exception:
                    continue

    # Sort by priority: Pro ($19) -> VIP ($10) -> Free (3 assets)
    profiles = [p for p in by_chat.values() if not p.get("alerts_paused", False)]
    profiles.sort(key=lambda p: TIER_PRIORITY.get(str(p.get("plan_tier") or "free"), 2))
    return profiles


async def _persist_profile_update(
    client: httpx.AsyncClient,
    settings: Settings,
    rest_base: str,
    supa_headers: dict[str, str],
    prof: dict[str, Any],
    patch_body: dict[str, Any],
) -> None:
    source = str(prof.get("_source") or "user_tracker_profiles")
    chat_id = str(prof.get("telegram_chat_id") or "").strip()

    if source == "user_tracker_profiles" and settings.has_live_supabase and prof.get("user_id"):
        try:
            await client.patch(
                f"{rest_base}/user_tracker_profiles",
                params={"user_id": f"eq.{prof['user_id']}"},
                headers={**supa_headers, "Prefer": "return=minimal"},
                content=json.dumps(patch_body, ensure_ascii=False).encode("utf-8"),
            )
        except Exception:
            pass
    elif source == "telegram_bot_users" and settings.has_live_supabase and chat_id:
        try:
            tg_patch = {
                k: v for k, v in patch_body.items()
                if k in ("watchlist", "alerts_paused", "plan_tier", "lang")
            }
            if tg_patch:
                await client.patch(
                    f"{rest_base}/telegram_bot_users",
                    params={"telegram_user_id": f"eq.{chat_id}"},
                    headers={**supa_headers, "Prefer": "return=minimal"},
                    content=json.dumps(tg_patch, ensure_ascii=False).encode("utf-8"),
                )
        except Exception:
            pass

    # Mirror updated state back to Upstash Redis whenever configured
    if settings.has_live_upstash and chat_id:
        existing_obj = prof.get("_raw_redis_obj")
        if not isinstance(existing_obj, dict):
            raw_prev = await _upstash_cmd(client, settings, ["GET", f"{TG_USER_REDIS_PREFIX}{chat_id}"])
            try:
                existing_obj = json.loads(raw_prev) if isinstance(raw_prev, str) else None
            except Exception:
                existing_obj = None
        if isinstance(existing_obj, dict):
            merged = {**existing_obj}
            for k in ("watchlist", "alerts_paused", "plan_tier", "lang"):
                if k in patch_body:
                    merged[k] = patch_body[k]
            merged["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            await _upstash_cmd(
                client,
                settings,
                ["SET", f"{TG_USER_REDIS_PREFIX}{chat_id}", json.dumps(merged, ensure_ascii=False)],
            )


async def process_personal_tracker_alerts(
    live_feed: dict[str, Any] | None,
    catalog_feed: dict[str, Any] | None,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    now_utc: datetime | None = None,
    price_cache: BatchPriceCache | None = None,
) -> dict[str, Any]:
    """Evaluate all active personal bot users instantaneously against a single batch price cache."""
    cfg = settings or Settings.load()
    if not (cfg.has_live_telegram and (cfg.has_live_supabase or cfg.has_live_upstash)):
        return {
            "status": "skipped_unconfigured",
            "profiles_checked": 0,
            "alerts_sent": 0,
            "digests_sent": 0,
            "blocked_paused": 0,
            "cached_symbols": 0,
        }

    now_dt = now_utc or datetime.now(timezone.utc)
    today_iso = now_dt.strftime("%Y-%m-%d")
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    rest_base = normalize_supabase_rest_base(cfg.supabase_url) if cfg.has_live_supabase else ""
    supa_headers = {
        "apikey": cfg.supabase_key,
        "Authorization": f"Bearer {cfg.supabase_key}",
        "User-Agent": SUPABASE_SERVER_USER_AGENT,
        "Content-Type": "application/json",
    }

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    cache = price_cache or _SHARED_PRICE_CACHE

    alerts_sent = 0
    digests_sent = 0
    profiles_checked = 0
    blocked_paused = 0

    try:
        # Step 1: Load all active non-paused profiles sorted by tier priority (Pro -> VIP -> Free)
        profiles = await _load_all_active_profiles(http_client, cfg, rest_base, supa_headers)

        # Step 2: Single batch price hydration (FX + SYP/TRY/EGP + Gold + Silver + Crypto + Unique Stocks)
        cache.hydrate_from_feeds(live_feed, catalog_feed, now_dt=now_dt)
        unique_stocks: set[str] = set()
        for prof in profiles:
            for item in prof.get("watchlist") or []:
                if isinstance(item, dict) and str(item.get("category", "")).lower() == "stock":
                    sym = str(item.get("symbol", "")).strip().upper()
                    if sym:
                        unique_stocks.add(sym)
        if unique_stocks:
            await cache.batch_populate_stocks(http_client, unique_stocks)
        await cache.mirror_to_upstash(http_client, cfg)

        # Step 3: Evaluate EVERY user instantaneously against in-memory `cache` (0 per-user price API calls!)
        for prof in profiles:
            chat_id = str(prof.get("telegram_chat_id") or "").strip()
            raw_watchlist = prof.get("watchlist")
            if not chat_id or not isinstance(raw_watchlist, list) or not raw_watchlist:
                continue

            plan_tier = str(prof.get("plan_tier") or "free").strip().lower()
            max_allowed = TIER_MAX_ASSETS.get(plan_tier, 3)
            watchlist = raw_watchlist[:max_allowed]
            is_free_tier = plan_tier == "free"

            profiles_checked += 1
            lang = "en" if str(prof.get("lang", "ar")).lower() == "en" else "ar"
            updated_watchlist: list[dict[str, Any]] = []
            watchlist_mutated = False
            user_blocked = False
            digest_lines: list[str] = []

            for raw_item in watchlist:
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                sym = str(item.get("symbol", "")).strip().upper()
                name = str(item.get("name_ar") if lang == "ar" else item.get("name_en") or sym)
                if not sym:
                    continue

                price = cache.get(sym)
                if price is not None and not user_blocked:
                    price_str = format(price, "f")
                    digest_lines.append(
                        f"• <b>{_escape_html(name)}</b> (<code>{_escape_html(sym)}</code>): <code>{price_str}</code>"
                    )
                    trigger = check_alert_trigger(
                        price,
                        item.get("alert_above"),
                        item.get("alert_below"),
                    )
                    target_val = item.get("alert_above") if trigger == "above" else item.get("alert_below")
                    if trigger and should_fire_debounced_alert(item, trigger, target_val, now_dt):
                        arrow = "▲" if trigger == "above" else "🔻"
                        nudge = f"\n\n{build_free_post_value_nudge(lang)}" if is_free_tier else ""
                        if lang == "ar":
                            alert_html = (
                                f"{arrow} <b>تنبيه السعر الفوري — {_escape_html(name)} ({_escape_html(sym)})</b>\n\n"
                                f"📌 السعر الحالي: <code>{price_str}</code>\n"
                                f"🎯 المستوى المراقَب: <code>{_escape_html(str(target_val))}</code> "
                                f"({'صعوداً' if trigger == 'above' else 'هبوطاً'})\n\n"
                                f"⚖️ <i>تنبيه معلوماتي لمتابعة الأرقام فقط وليست نصيحة استثمارية.</i>"
                                f"{nudge}"
                            )
                        else:
                            alert_html = (
                                f"{arrow} <b>Instant Price Alert — {_escape_html(name)} ({_escape_html(sym)})</b>\n\n"
                                f"📌 Current Price: <code>{price_str}</code>\n"
                                f"🎯 Watched Level: <code>{_escape_html(str(target_val))}</code> "
                                f"({'Crossed Above' if trigger == 'above' else 'Dropped Below'})\n\n"
                                f"⚖️ <i>Informational price monitoring alert only — not investment advice.</i>"
                                f"{nudge}"
                            )
                        send_res = await send_telegram_with_retry(
                            http_client, cfg.telegram_bot_token, chat_id, alert_html
                        )
                        if send_res.get("ok"):
                            alerts_sent += 1
                            item["last_alerted_at"] = now_iso
                            if trigger == "above":
                                item["triggered_above_target"] = str(target_val).strip()
                            else:
                                item["triggered_below_target"] = str(target_val).strip()
                            watchlist_mutated = True
                        elif send_res.get("blocked"):
                            user_blocked = True
                            blocked_paused += 1

                updated_watchlist.append(item)

            patch_body: dict[str, Any] = {}
            if user_blocked:
                patch_body["alerts_paused"] = True
            if watchlist_mutated:
                # Preserve any extra items beyond tier cap untouched if downgraded later
                patch_body["watchlist"] = updated_watchlist + list(raw_watchlist[max_allowed:])

            # Daily custom digest dispatch (once per UTC day at user's chosen hour)
            digest_due = (
                not user_blocked
                and bool(prof.get("daily_digest_enabled", False))
                and str(prof.get("last_digest_date") or "") != today_iso
                and now_dt.hour == int(prof.get("digest_hour_utc", 7))
                and len(digest_lines) > 0
            )
            if digest_due:
                news_blocks: list[str] = []
                if plan_tier == "monthly_personal_bot":
                    for it in updated_watchlist[:3]:
                        if it.get("news_alerts", True):
                            titles = await fetch_company_news_titles(
                                http_client, str(it.get("symbol", "")), str(it.get("name_en", "")), limit=1
                            )
                            if titles:
                                news_blocks.append(
                                    f"• <b>{_escape_html(str(it.get('symbol', '')))}:</b> {_escape_html(titles[0])}"
                                )

                if lang == "ar":
                    news_sec = ("\n\n📰 <b>أحدث أخبار قائمتك:</b>\n" + "\n".join(news_blocks)) if news_blocks else ""
                    digest_html = (
                        f"🤖 <b>ملخص بوت المتابعة الشخصي — ريت باث</b>\n\n"
                        + "\n".join(digest_lines[:15])
                        + news_sec
                        + "\n\n⚖️ <i>ملخص معلوماتي للأرقام والأخبار فقط؛ لا نضمن اتجاه السوق ولا نقدم توصية استثمارية.</i>"
                    )
                else:
                    news_sec = ("\n\n📰 <b>Latest Watchlist News:</b>\n" + "\n".join(news_blocks)) if news_blocks else ""
                    digest_html = (
                        f"🤖 <b>RatePath Personal Tracker Digest</b>\n\n"
                        + "\n".join(digest_lines[:15])
                        + news_sec
                        + "\n\n⚖️ <i>Informational market &amp; news summary only — not investment advice.</i>"
                    )
                digest_res = await send_telegram_with_retry(
                    http_client, cfg.telegram_bot_token, chat_id, digest_html
                )
                if digest_res.get("ok"):
                    digests_sent += 1
                    patch_body["last_digest_date"] = today_iso
                elif digest_res.get("blocked"):
                    patch_body["alerts_paused"] = True
                    blocked_paused += 1

            if patch_body:
                await _persist_profile_update(
                    http_client, cfg, rest_base, supa_headers, prof, patch_body
                )

        return {
            "status": "ok",
            "profiles_checked": profiles_checked,
            "alerts_sent": alerts_sent,
            "digests_sent": digests_sent,
            "blocked_paused": blocked_paused,
            "cached_symbols": len(cache.prices),
        }
    except Exception as exc:
        logger.exception("Personal tracker alert cycle failed: %s", type(exc).__name__)
        return {
            "status": f"error:{type(exc).__name__}",
            "profiles_checked": profiles_checked,
            "alerts_sent": alerts_sent,
            "digests_sent": digests_sent,
            "blocked_paused": blocked_paused,
            "cached_symbols": len(cache.prices),
        }
    finally:
        if owns_client:
            await http_client.aclose()


async def run_continuous_alert_loop(interval_seconds: int = 45) -> None:
    """Optional standalone 30-60s continuous worker loop (`python -m backend.personal_bot_worker --loop`)."""
    enforce_utf8_runtime()
    safe_interval = max(30, min(60, int(interval_seconds)))
    cfg = Settings.load()
    cache = BatchPriceCache(ttl_seconds=safe_interval)
    async with httpx.AsyncClient(timeout=httpx.Timeout(12)) as client:
        while True:
            try:
                live_feed = (
                    json.loads(LIVE_FEED_TARGET_PATH.read_text(encoding="utf-8"))
                    if LIVE_FEED_TARGET_PATH.exists()
                    else None
                )
                catalog_feed = (
                    json.loads(CATALOG_FEED_TARGET_PATH.read_text(encoding="utf-8"))
                    if CATALOG_FEED_TARGET_PATH.exists()
                    else None
                )
                summary = await process_personal_tracker_alerts(
                    live_feed,
                    catalog_feed,
                    settings=cfg,
                    client=client,
                    price_cache=cache,
                )
                print(json.dumps(summary, ensure_ascii=False), flush=True)
            except Exception as exc:
                logger.warning("Alert loop iteration error: %s", type(exc).__name__)
            await asyncio.sleep(safe_interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RatePath Personal Bot Alert Worker")
    parser.add_argument("--loop", action="store_true", help="Run continuous 30-60s batch alert loop")
    parser.add_argument("--interval", type=int, default=45, help="Batch refresh interval in seconds (30-60)")
    args = parser.parse_args()
    if args.loop:
        asyncio.run(run_continuous_alert_loop(args.interval))
    else:
        live = (
            json.loads(LIVE_FEED_TARGET_PATH.read_text(encoding="utf-8"))
            if LIVE_FEED_TARGET_PATH.exists()
            else None
        )
        cat = (
            json.loads(CATALOG_FEED_TARGET_PATH.read_text(encoding="utf-8"))
            if CATALOG_FEED_TARGET_PATH.exists()
            else None
        )
        out = asyncio.run(process_personal_tracker_alerts(live, cat))
        print(json.dumps(out, ensure_ascii=False, indent=2))
