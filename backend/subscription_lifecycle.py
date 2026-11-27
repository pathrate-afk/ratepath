"""Daily Subscription Lifecycle Worker — 2-Day Expiry Reminders & Clean Auto-Downgrade.

Runs automatically inside the daily calibration cron (`backend/agents/daily_drill.py`)
and standalone via `python -m backend.subscription_lifecycle` (Pure integer/Decimal math, ZERO floats):
1. **2-Day Expiry Reminder**: Finds active paid subscriptions (`finished`/`confirmed`/`active`)
   with `0 < (expires_at - now) <= 48 hours` and `expiry_reminded_at IS NULL`, sends a polite
   bilingual renewal reminder with a 1-tap `/upgrade` CTA button, and stamps `expiry_reminded_at`.
2. **Clean Auto-Downgrade**: Finds paid accounts where `expires_at <= now`, marks subscription
   `expired`, downgrades `plan_tier` back to `'free'` across Supabase (`vip_subscriptions`,
   `user_tracker_profiles`, `telegram_bot_users`) AND Upstash Redis (`ratepath:tg_user:{id}`),
   cleanly preserving the user's **first 3 active assets**, and notifies the user gracefully.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.config import SUPABASE_SERVER_USER_AGENT, Settings, enforce_utf8_runtime
from backend.evaluator import normalize_supabase_rest_base
from backend.personal_bot_worker import (
    TG_USER_REDIS_PREFIX,
    send_telegram_with_retry,
)

logger = logging.getLogger("ratepath.subscription_lifecycle")

REMINDER_WINDOW_HOURS: int = 48
FREE_TIER_MAX_ASSETS: int = 3


def _parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


async def _upstash_cmd(client: httpx.AsyncClient, settings: Settings, cmd: list[str]) -> Any:
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


def build_expiry_reminder_message(lang: str, plan_tier: str, hours_left: int) -> tuple[str, dict[str, Any]]:
    days_label_ar = "يومين" if hours_left > 24 else "24 ساعة"
    days_label_en = "2 days" if hours_left > 24 else "24 hours"
    is_pro = plan_tier == "monthly_personal_bot"
    plan_ar = "البوت الشخصي Pro (19$ USDT)" if is_pro else "باقة Business VIP (10$ USDT)"
    plan_en = "Personal Bot Pro ($19 USDT)" if is_pro else "Business VIP ($10 USDT)"

    if lang == "ar":
        text = (
            f"⏳ <b>تذكير قبل انتهاء اشتراكك بـ {days_label_ar}</b>\n\n"
            f"اشتراكك الحالي في <b>{plan_ar}</b> سينتهي خلال أقل من {days_label_ar}.\n\n"
            f"لضمان استمرار تنبيهاتك الفورية وقناة VIP بدون انقطاع، يمكنك التجديد بضغطة زر عبر USDT "
            f"(بدون تجديد تلقائي إجباري).\n\n"
            f"📌 إذا لم تجدّد، سيعود حسابك تلقائياً إلى الباقة المجانية مع الاحتفاظ بأول 3 أصول في قائمتك."
        )
        markup = {
            "inline_keyboard": [
                [{"text": "🔄 تجديد الاشتراك الآن (USDT)", "callback_data": "cb:upgrade_pro" if is_pro else "cb:upgrade_vip"}],
                [{"text": "📊 قائمة متابعتي", "callback_data": "cb:watchlist"}],
            ]
        }
    else:
        text = (
            f"⏳ <b>Subscription Renewal Reminder ({days_label_en} left)</b>\n\n"
            f"Your <b>{plan_en}</b> plan expires in less than {days_label_en}.\n\n"
            f"Renew anytime in 1 tap with USDT (no forced auto-billing) to keep your expanded watchlist and VIP alerts active.\n\n"
            f"📌 If not renewed, your account will cleanly return to the Free tier keeping your first 3 assets active."
        )
        markup = {
            "inline_keyboard": [
                [{"text": "🔄 Renew Now (USDT)", "callback_data": "cb:upgrade_pro" if is_pro else "cb:upgrade_vip"}],
                [{"text": "📊 My Watchlist", "callback_data": "cb:watchlist"}],
            ]
        }
    return text, markup


def build_downgrade_notice_message(lang: str, kept_count: int) -> tuple[str, dict[str, Any]]:
    if lang == "ar":
        text = (
            "ℹ️ <b>انتهت صلاحية اشتراكك المدفوع</b>\n\n"
            f"تم تحويل حسابك بسلاسة إلى الباقة المجانية مع الاحتفاظ بأول <b>{kept_count} أصول</b> نشطة في قائمتك.\n\n"
            "يمكنك استعادة 10–50 أصلاً وقناة VIP في أي وقت بضغطة زر عبر /upgrade."
        )
        markup = {
            "inline_keyboard": [
                [{"text": "🚀 إعادة تفعيل باقة VIP — 10$ USDT", "callback_data": "cb:upgrade_vip"}],
                [{"text": "📊 عرض قائمتي (3/3)", "callback_data": "cb:watchlist"}],
            ]
        }
    else:
        text = (
            "ℹ️ <b>Your Paid Subscription Has Expired</b>\n\n"
            f"Your account has returned cleanly to the Free tier, keeping your first <b>{kept_count} assets</b> active.\n\n"
            "Re-activate 10–50 assets + VIP Channel anytime via /upgrade."
        )
        markup = {
            "inline_keyboard": [
                [{"text": "🚀 Re-Activate VIP — $10 USDT", "callback_data": "cb:upgrade_vip"}],
                [{"text": "📊 View My Watchlist", "callback_data": "cb:watchlist"}],
            ]
        }
    return text, markup


async def _downgrade_user_storage(
    client: httpx.AsyncClient,
    settings: Settings,
    rest_base: str,
    supa_headers: dict[str, str],
    tg_user_id: str | None,
    user_email: str | None,
) -> int:
    """Downgrade user tier to `free` while preserving their first 3 active assets."""
    kept_assets_count = 0

    if settings.has_live_supabase:
        # 1. Downgrade `user_tracker_profiles` (by telegram_chat_id or email)
        filters: list[tuple[str, str]] = []
        if tg_user_id:
            filters.append(("telegram_chat_id", f"eq.{tg_user_id}"))
        if user_email:
            filters.append(("email", f"eq.{user_email}"))

        for col, expr in filters:
            try:
                r_get = await client.get(
                    f"{rest_base}/user_tracker_profiles",
                    params={col: expr, "select": "user_id,watchlist", "limit": "5"},
                    headers=supa_headers,
                )
                if r_get.status_code == 200:
                    rows = json.loads(r_get.text)
                    for row in rows if isinstance(rows, list) else []:
                        wl = row.get("watchlist") if isinstance(row.get("watchlist"), list) else []
                        trimmed = wl[:FREE_TIER_MAX_ASSETS]
                        kept_assets_count = max(kept_assets_count, len(trimmed))
                        await client.patch(
                            f"{rest_base}/user_tracker_profiles",
                            params={"user_id": f"eq.{row['user_id']}"},
                            headers={**supa_headers, "Prefer": "return=minimal"},
                            content=json.dumps(
                                {"plan_tier": "free", "watchlist": trimmed},
                                ensure_ascii=False,
                            ).encode("utf-8"),
                        )
            except Exception:
                pass

        # 2. Downgrade standalone `telegram_bot_users`
        if tg_user_id:
            try:
                r_tg = await client.get(
                    f"{rest_base}/telegram_bot_users",
                    params={"telegram_user_id": f"eq.{tg_user_id}", "select": "watchlist", "limit": "1"},
                    headers=supa_headers,
                )
                if r_tg.status_code == 200:
                    rows = json.loads(r_tg.text)
                    if isinstance(rows, list) and rows:
                        wl = rows[0].get("watchlist") if isinstance(rows[0].get("watchlist"), list) else []
                        trimmed = wl[:FREE_TIER_MAX_ASSETS]
                        kept_assets_count = max(kept_assets_count, len(trimmed))
                        await client.patch(
                            f"{rest_base}/telegram_bot_users",
                            params={"telegram_user_id": f"eq.{tg_user_id}"},
                            headers={**supa_headers, "Prefer": "return=minimal"},
                            content=json.dumps(
                                {"plan_tier": "free", "expires_at": None, "watchlist": trimmed},
                                ensure_ascii=False,
                            ).encode("utf-8"),
                        )
            except Exception:
                pass

    # 3. Downgrade Upstash Redis hot profile (`ratepath:tg_user:{tg_user_id}`)
    if settings.has_live_upstash and tg_user_id:
        raw_redis = await _upstash_cmd(client, settings, ["GET", f"{TG_USER_REDIS_PREFIX}{tg_user_id}"])
        if isinstance(raw_redis, str) and raw_redis.strip():
            try:
                obj = json.loads(raw_redis)
                if isinstance(obj, dict):
                    wl = obj.get("watchlist") if isinstance(obj.get("watchlist"), list) else []
                    trimmed = wl[:FREE_TIER_MAX_ASSETS]
                    kept_assets_count = max(kept_assets_count, len(trimmed))
                    obj["plan_tier"] = "free"
                    obj["expires_at"] = None
                    obj["watchlist"] = trimmed
                    obj["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    await _upstash_cmd(
                        client,
                        settings,
                        ["SET", f"{TG_USER_REDIS_PREFIX}{tg_user_id}", json.dumps(obj, ensure_ascii=False)],
                    )
            except Exception:
                pass

    return kept_assets_count


async def run_subscription_lifecycle_check(
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Run daily 2-day expiry reminders and clean auto-downgrades for expired VIP/Pro accounts."""
    cfg = settings or Settings.load()
    if not cfg.has_live_supabase:
        return {"status": "skipped_unconfigured", "reminded": 0, "downgraded": 0}

    now_dt = now_utc or datetime.now(timezone.utc)
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    reminder_cutoff = now_dt + timedelta(hours=REMINDER_WINDOW_HOURS)
    rest_base = normalize_supabase_rest_base(cfg.supabase_url)
    supa_headers = {
        "apikey": cfg.supabase_key,
        "Authorization": f"Bearer {cfg.supabase_key}",
        "User-Agent": SUPABASE_SERVER_USER_AGENT,
        "Content-Type": "application/json",
    }

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    reminded = 0
    downgraded = 0

    try:
        resp = await http_client.get(
            f"{rest_base}/vip_subscriptions",
            params={
                "payment_status": "in.(finished,confirmed,active)",
                "expires_at": "not.is.null",
                "select": "id,order_id,user_email,telegram_user_id,plan_tier,lang,expires_at,expiry_reminded_at",
                "limit": "500",
            },
            headers=supa_headers,
        )
        if resp.status_code != 200:
            # Fallback if `expiry_reminded_at` column isn't migrated yet
            resp = await http_client.get(
                f"{rest_base}/vip_subscriptions",
                params={
                    "payment_status": "in.(finished,confirmed,active)",
                    "expires_at": "not.is.null",
                    "select": "id,order_id,user_email,telegram_user_id,plan_tier,lang,expires_at",
                    "limit": "500",
                },
                headers=supa_headers,
            )
        if resp.status_code != 200:
            return {"status": f"supabase_http_{resp.status_code}", "reminded": 0, "downgraded": 0}

        rows = json.loads(resp.text)
        if not isinstance(rows, list):
            return {"status": "ok", "reminded": 0, "downgraded": 0}

        # Group by user to avoid downgrading someone who already renewed with a newer active subscription
        active_future_users: set[str] = set()
        for row in rows:
            exp_dt = _parse_iso_utc(row.get("expires_at"))
            if exp_dt and exp_dt > now_dt:
                if row.get("telegram_user_id"):
                    active_future_users.add(f"tg:{row['telegram_user_id']}")
                if row.get("user_email"):
                    active_future_users.add(f"em:{str(row['user_email']).lower()}")

        for row in rows:
            sub_id = row.get("id")
            exp_dt = _parse_iso_utc(row.get("expires_at"))
            if not sub_id or exp_dt is None:
                continue

            tg_uid = str(row.get("telegram_user_id") or "").strip() or None
            email = str(row.get("user_email") or "").strip().lower() or None
            lang = "en" if str(row.get("lang") or "ar").lower() == "en" else "ar"
            plan_tier = str(row.get("plan_tier") or "monthly_business_vip")

            # Case 1: Expired -> Mark subscription expired & downgrade back to Free (keep 3 assets)
            if exp_dt <= now_dt:
                await http_client.patch(
                    f"{rest_base}/vip_subscriptions",
                    params={"id": f"eq.{sub_id}"},
                    headers={**supa_headers, "Prefer": "return=minimal"},
                    content=json.dumps({"payment_status": "expired"}, ensure_ascii=False).encode("utf-8"),
                )
                has_newer_active = bool(
                    (tg_uid and f"tg:{tg_uid}" in active_future_users)
                    or (email and f"em:{email}" in active_future_users)
                )
                if not has_newer_active:
                    kept = await _downgrade_user_storage(
                        http_client, cfg, rest_base, supa_headers, tg_uid, email
                    )
                    downgraded += 1
                    if tg_uid and cfg.has_live_telegram:
                        msg_text, markup = build_downgrade_notice_message(lang, kept)
                        await send_telegram_with_retry(
                            http_client, cfg.telegram_bot_token, tg_uid, msg_text, reply_markup=markup
                        )
                continue

            # Case 2: Expiring within 48 hours -> Send 2-day renewal reminder once
            if exp_dt <= reminder_cutoff and not row.get("expiry_reminded_at"):
                delta = exp_dt - now_dt
                hours_left = max(1, (delta.days * 86400 + delta.seconds) // 3600)
                if tg_uid and cfg.has_live_telegram:
                    msg_text, markup = build_expiry_reminder_message(lang, plan_tier, hours_left)
                    res = await send_telegram_with_retry(
                        http_client, cfg.telegram_bot_token, tg_uid, msg_text, reply_markup=markup
                    )
                    if res.get("ok"):
                        reminded += 1
                        try:
                            await http_client.patch(
                                f"{rest_base}/vip_subscriptions",
                                params={"id": f"eq.{sub_id}"},
                                headers={**supa_headers, "Prefer": "return=minimal"},
                                content=json.dumps(
                                    {"expiry_reminded_at": now_iso}, ensure_ascii=False
                                ).encode("utf-8"),
                            )
                        except Exception:
                            pass

        return {"status": "ok", "reminded": reminded, "downgraded": downgraded}
    except Exception as exc:
        logger.warning("Subscription lifecycle check failed: %s", type(exc).__name__)
        return {"status": f"error:{type(exc).__name__}", "reminded": reminded, "downgraded": downgraded}
    finally:
        if owns_client:
            await http_client.aclose()


if __name__ == "__main__":
    enforce_utf8_runtime()
    summary = asyncio.run(run_subscription_lifecycle_check())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
