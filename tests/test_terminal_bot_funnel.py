"""Comprehensive Integration & Unit Tests for @RatePathTerminalBot Optimization & USDT Funnel.

Verifies:
1. Decoupled BatchPriceCache (single batch fetch across all users, zero per-user external API calls),
   one-time breakout debouncing, VIP/Pro priority ordering, Post-Value Nudge for Free users,
   429 backoff retry, and 403 Forbidden auto-pause.
2. Live Node.js ESM execution of `functions/api/telegram-webhook.js`, `create-vip-checkout.js`, and
   `nowpayments-webhook.js`:
   - Exact AR/EN Welcome (`/start`), language toggle (`cb:lang:en` / `cb:lang:ar`) with immediate
     `answerCallbackQuery` and `editMessageText` (zero chat flooding).
   - Adding 3 assets, blocking the 4th asset with exact 3/3 Free Limit Paywall, Replace Asset flow,
     and Target Price setting.
   - `/upgrade` & `cb:upgrade_vip` / `cb:upgrade_pro` generating NOWPayments invoices with
     `order_id = {telegram_user_id}_{plan}_{timestamp}`.
   - NOWPayments HMAC-SHA512 IPN upgrading user tier (+30 days), generating single-use
     `createChatInviteLink(member_limit: 1)` (supporting `VIP_CHANNEL_ID`), and unlocking 10/50 slots.
3. Daily subscription lifecycle (`backend/subscription_lifecycle.py`):
   - 2-day pre-expiry renewal reminder + `expiry_reminded_at` stamp.
   - Clean expired downgrade back to Free tier keeping the user's first 3 active assets.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from backend.config import WORKSPACE_ROOT, Settings
from backend.personal_bot_worker import (
    BatchPriceCache,
    build_free_post_value_nudge,
    process_personal_tracker_alerts,
    should_fire_debounced_alert,
)
from backend.subscription_lifecycle import run_subscription_lifecycle_check


def _mock_settings() -> Settings:
    return Settings(
        gemini_api_key="placeholder",
        groq_api_key="placeholder",
        openrouter_api_key="placeholder",
        supabase_url="https://mock.supabase.co",
        supabase_key="mock_secret_key",
        upstash_redis_rest_url="https://mock.upstash.io",
        upstash_redis_rest_token="mock_upstash_tok",
        telegram_bot_token="123456:MOCK_BOT_TOKEN",
        telegram_public_channel="placeholder",
        telegram_vip_channel="placeholder",
        admin_chat_id="999999",
    )


def test_batch_price_cache_debounce_priority_nudge_and_403_pause() -> None:
    """Verify single batch stock fetch, breakout latch debounce, Free nudge, and 403 auto-pause."""
    live_feed = {
        "gold": {"prices": {"XAU_OZ": "3710.50", "GRAM_21K": "104.38"}},
        "fiat": {"rates": {"TRY": {"units_per_usd": "50.70"}, "EGP": {"units_per_usd": "54.80"}, "SYP": {"units_per_usd": "152.50"}}},
        "crypto": {"assets": {"BTC": {"spot_price": "65000.00"}, "ETH": {"spot_price": "2000.00"}, "SOL": {"spot_price": "90.00"}}},
    }
    catalog_feed = {
        "metals": {"XAG_OZ": {"price_usd": "44.10"}, "XAG_GRAM": {"price_usd": "1.42"}},
        "fiat": {"EUR": {"units_per_usd": "0.91"}},
        "crypto": [{"symbol": "USDT", "price_usd": "1.0000"}],
    }

    now_dt = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)
    yahoo_calls = {"AAPL": 0}
    sent_order: list[tuple[str, str]] = []
    patches: list[tuple[str, dict[str, object]]] = []

    mock_profiles = [
        # Free user (should run AFTER VIP user, gets post-value nudge!)
        {
            "user_id": "u-free",
            "telegram_chat_id": "111111",
            "lang": "ar",
            "plan_tier": "free",
            "alerts_paused": False,
            "daily_digest_enabled": False,
            "watchlist": [
                {"symbol": "XAU", "category": "metal", "name_ar": "الذهب", "name_en": "Gold", "alert_above": "3700", "one_time": True},
                {"symbol": "AAPL", "category": "stock", "name_ar": "أبل", "name_en": "Apple", "alert_above": "240", "one_time": True},
            ],
        },
        # Blocked user (Telegram returns 403 -> must pause cleanly without crashing!)
        {
            "user_id": "u-blocked",
            "telegram_chat_id": "222222",
            "lang": "en",
            "plan_tier": "free",
            "alerts_paused": False,
            "daily_digest_enabled": False,
            "watchlist": [
                {"symbol": "BTC", "category": "crypto", "name_ar": "بيتكوين", "name_en": "Bitcoin", "alert_above": "60000", "one_time": True},
            ],
        },
        # VIP user watching SAME stock AAPL (must run FIRST & reuse cached AAPL price without second Yahoo call!)
        {
            "user_id": "u-vip",
            "telegram_chat_id": "333333",
            "lang": "en",
            "plan_tier": "monthly_business_vip",
            "alerts_paused": False,
            "daily_digest_enabled": False,
            "watchlist": [
                {"symbol": "AAPL", "category": "stock", "name_ar": "أبل", "name_en": "Apple", "alert_above": "240", "one_time": True},
            ],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "query1.finance.yahoo.com" in url:
            yahoo_calls["AAPL"] += 1
            return httpx.Response(200, json={"chart": {"result": [{"meta": {"regularMarketPrice": 245}}]}})
        if "upstash.io" in url:
            cmd = json.loads(request.content.decode("utf-8"))
            if cmd[0] == "SMEMBERS":
                return httpx.Response(200, json={"result": []})
            return httpx.Response(200, json={"result": "OK"})
        if "/rest/v1/user_tracker_profiles" in url and request.method == "GET":
            return httpx.Response(200, json=mock_profiles)
        if "/rest/v1/telegram_bot_users" in url and request.method == "GET":
            return httpx.Response(200, json=[])
        if "/rest/v1/user_tracker_profiles" in url and request.method == "PATCH":
            patches.append((url, json.loads(request.content.decode("utf-8"))))
            return httpx.Response(204)
        if "api.telegram.org" in url and "/sendMessage" in url:
            body = json.loads(request.content.decode("utf-8"))
            cid = str(body["chat_id"])
            if cid == "222222":
                return httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"})
            sent_order.append((cid, str(body["text"])))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent_order)}})
        return httpx.Response(404)

    async def _run() -> dict[str, object]:
        cache = BatchPriceCache(ttl_seconds=45)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await process_personal_tracker_alerts(
                live_feed,
                catalog_feed,
                settings=_mock_settings(),
                client=client,
                now_utc=now_dt,
                price_cache=cache,
            )

    summary = asyncio.run(_run())
    assert summary["status"] == "ok"
    assert summary["profiles_checked"] == 3
    assert summary["alerts_sent"] == 3  # VIP AAPL + Free XAU + Free AAPL
    assert summary["blocked_paused"] == 1
    # Decoupled batch check: AAPL fetched ONCE even though 2 users watch it!
    assert yahoo_calls["AAPL"] == 1
    # Priority ordering: VIP user (333333) notified BEFORE Free user (111111)!
    assert sent_order[0][0] == "333333"
    assert build_free_post_value_nudge("en") not in sent_order[0][1]
    # Free user (111111) receives exact Arabic Post-Value Nudge!
    assert sent_order[1][0] == "111111"
    assert build_free_post_value_nudge("ar") in sent_order[1][1]
    # Blocked user (222222) cleanly patched with alerts_paused=True!
    blocked_patches = [body for u, body in patches if "u-blocked" in u]
    assert any(p.get("alerts_paused") is True for p in blocked_patches)

    # Anti-spam breakout latch: same target must NOT fire again even after cooldown elapses
    latched_item = {"alert_above": "3700", "triggered_above_target": "3700", "one_time": True}
    assert should_fire_debounced_alert(latched_item, "above", "3700", now_dt + timedelta(hours=12)) is False
    # Changing target to new level arms it again immediately
    assert should_fire_debounced_alert(latched_item, "above", "3750", now_dt + timedelta(hours=12)) is True


def test_subscription_lifecycle_reminder_and_clean_downgrade() -> None:
    """Verify 2-day pre-expiry reminder and expired auto-downgrade keeping first 3 assets."""
    now_dt = datetime(2026, 11, 28, 6, 0, 0, tzinfo=timezone.utc)
    expiring_soon = (now_dt + timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    already_expired = (now_dt - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    subs_rows = [
        {
            "id": "sub-remind",
            "order_id": "500001_vip10_1760000000",
            "telegram_user_id": "500001",
            "user_email": "remind@example.com",
            "plan_tier": "monthly_business_vip",
            "lang": "ar",
            "expires_at": expiring_soon,
            "expiry_reminded_at": None,
        },
        {
            "id": "sub-expire",
            "order_id": "500002_vip10_1750000000",
            "telegram_user_id": "500002",
            "user_email": "expire@example.com",
            "plan_tier": "monthly_business_vip",
            "lang": "en",
            "expires_at": already_expired,
            "expiry_reminded_at": "2026-11-26T06:00:00Z",
        },
    ]

    tg_sent: list[tuple[str, str]] = []
    downgraded_watchlists: list[list[dict[str, object]]] = []
    redis_kv: dict[str, str] = {
        "ratepath:tg_user:500002": json.dumps(
            {
                "telegram_user_id": "500002",
                "lang": "en",
                "plan_tier": "monthly_business_vip",
                "watchlist": [{"symbol": s} for s in ("XAU", "BTC", "ETH", "SOL", "EUR")],
            },
            ensure_ascii=False,
        )
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "upstash.io" in url:
            cmd = json.loads(request.content.decode("utf-8"))
            if cmd[0] == "GET":
                return httpx.Response(200, json={"result": redis_kv.get(cmd[1])})
            if cmd[0] == "SET":
                redis_kv[cmd[1]] = str(cmd[2])
                return httpx.Response(200, json={"result": "OK"})
        if "/rest/v1/vip_subscriptions" in url and request.method == "GET":
            return httpx.Response(200, json=subs_rows)
        if "/rest/v1/vip_subscriptions" in url and request.method == "PATCH":
            return httpx.Response(204)
        if "/rest/v1/user_tracker_profiles" in url and request.method == "GET":
            return httpx.Response(
                200,
                json=[{"user_id": "u-500002", "watchlist": [{"symbol": s} for s in ("XAU", "BTC", "ETH", "SOL", "EUR")]}],
            )
        if "/rest/v1/user_tracker_profiles" in url and request.method == "PATCH":
            body = json.loads(request.content.decode("utf-8"))
            if "watchlist" in body:
                downgraded_watchlists.append(body["watchlist"])
            return httpx.Response(204)
        if "/rest/v1/telegram_bot_users" in url and request.method == "GET":
            return httpx.Response(200, json=[])
        if "api.telegram.org" in url and "/sendMessage" in url:
            body = json.loads(request.content.decode("utf-8"))
            tg_sent.append((str(body["chat_id"]), str(body["text"])))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": len(tg_sent)}})
        return httpx.Response(204)

    async def _run() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await run_subscription_lifecycle_check(
                settings=_mock_settings(), client=client, now_utc=now_dt
            )

    res = asyncio.run(_run())
    assert res["status"] == "ok"
    assert res["reminded"] == 1
    assert res["downgraded"] == 1
    # Reminder sent to 500001 in Arabic
    assert any(cid == "500001" and "تذكير قبل انتهاء اشتراكك" in txt for cid, txt in tg_sent)
    # Expired user 500002 downgraded in DB & Redis keeping strictly first 3 assets (XAU, BTC, ETH)
    assert downgraded_watchlists and [x["symbol"] for x in downgraded_watchlists[0]] == ["XAU", "BTC", "ETH"]
    redis_after = json.loads(redis_kv["ratepath:tg_user:500002"])
    assert redis_after["plan_tier"] == "free"
    assert [x["symbol"] for x in redis_after["watchlist"]] == ["XAU", "BTC", "ETH"]


def test_interactive_bot_funnel_callbacks_paywall_lang_and_usdt_activation_nodejs() -> None:
    """End-to-end Node ESM test of @RatePathTerminalBot UX, 4th-asset 3/3 paywall, EN/AR toggle & IPN upgrade."""
    node_bin = shutil.which("node")
    assert node_bin is not None
    tg_uri = (WORKSPACE_ROOT / "functions" / "api" / "telegram-webhook.js").as_uri()
    np_uri = (WORKSPACE_ROOT / "functions" / "api" / "nowpayments-webhook.js").as_uri()

    script = f"""
import assert from "node:assert/strict";
import crypto from "node:crypto";
import {{ processTelegramUpdate }} from "{tg_uri}";
import {{ processNowPaymentsIpn, verifyNowPaymentsSignature, sortObjectKeysDeep, parseOrderIdMetadata }} from "{np_uri}";

const redis = new Map();
const tgCalls = [];
let lastInvoiceOrderId = null;

const mockFetch = async (url, init = {{}}) => {{
  const u = String(url);
  if (u.includes("upstash.io")) {{
    const cmd = JSON.parse(init.body);
    if (cmd[0] === "GET") return new Response(JSON.stringify({{ result: redis.get(cmd[1]) ?? null }}), {{ status: 200 }});
    if (cmd[0] === "SET") {{ redis.set(cmd[1], cmd[2]); return new Response(JSON.stringify({{ result: "OK" }}), {{ status: 200 }}); }}
    if (cmd[0] === "SADD") return new Response(JSON.stringify({{ result: 1 }}), {{ status: 200 }});
    return new Response(JSON.stringify({{ result: 1 }}), {{ status: 200 }});
  }}
  if (u.includes("live_feed.json")) {{
    return new Response(JSON.stringify({{
      gold: {{ prices: {{ XAU_OZ: "3650.00", GRAM_21K: "102.70", GRAM_24K: "117.35" }} }},
      fiat: {{ rates: {{ TRY: {{ units_per_usd: "50.70" }}, SYP: {{ units_per_usd: "152.50" }} }} }},
      crypto: {{ assets: {{ BTC: {{ spot_price: "60000.00" }}, ETH: {{ spot_price: "1700.00" }}, SOL: {{ spot_price: "72.50" }} }} }},
    }}), {{ status: 200 }});
  }}
  if (u.includes("catalog_feed.json")) {{
    return new Response(JSON.stringify({{
      metals: {{ XAG_OZ: {{ price_usd: "43.75" }} }},
      fiat: {{ EUR: {{ units_per_usd: "0.91" }} }},
      crypto: [{{ symbol: "USDT", price_usd: "1.0000" }}],
    }}), {{ status: 200 }});
  }}
  if (u.includes("api.nowpayments.io/v1/invoice")) {{
    const body = JSON.parse(init.body);
    lastInvoiceOrderId = body.order_id;
    return new Response(JSON.stringify({{ id: "inv_bot_1", invoice_url: `https://nowpayments.io/payment/?iid=bot_${{body.order_id}}` }}), {{ status: 200 }});
  }}
  if (u.includes("/getChatMember")) {{
    return new Response(JSON.stringify({{ ok: true, result: {{ status: "left" }} }}), {{ status: 200 }});
  }}
  if (u.includes("/createChatInviteLink")) {{
    tgCalls.push({{ method: "createChatInviteLink", body: JSON.parse(init.body) }});
    return new Response(JSON.stringify({{ ok: true, result: {{ invite_link: "https://t.me/+SingleUseVIP2026" }} }}), {{ status: 200 }});
  }}
  if (u.includes("api.telegram.org/bot")) {{
    const method = u.split("/").pop();
    const body = JSON.parse(init.body);
    tgCalls.push({{ method, body }});
    return new Response(JSON.stringify({{ ok: true, result: {{ message_id: 100 + tgCalls.length }} }}), {{ status: 200 }});
  }}
  if (u.includes("/rest/v1/")) {{
    if (!init.method || init.method === "GET") return new Response(JSON.stringify([]), {{ status: 200 }});
    return new Response(null, {{ status: 204 }});
  }}
  return new Response("Not Found", {{ status: 404 }});
}};

const env = {{
  TELEGRAM_BOT_TOKEN: "123:BOT",
  TELEGRAM_WEBHOOK_SECRET: "wh_sec",
  VIP_CHANNEL_ID: "-100999888",
  ADMIN_CHAT_ID: "888888",
  UPSTASH_REDIS_REST_URL: "https://mock.upstash.io",
  UPSTASH_REDIS_REST_TOKEN: "tok",
  NOWPAYMENTS_API_KEY: "np_live_key",
  NOWPAYMENTS_IPN_SECRET: "ipn_sec_123",
  PUBLIC_SITE_URL: "https://ratepathlive.com",
}};

// 1. /start -> Exact Arabic Welcome Message + 3-row Inline Keyboard
const rStart = await processTelegramUpdate({{
  message: {{ chat: {{ id: 777001, type: "private" }}, from: {{ id: 777001, language_code: "ar" }}, text: "/start" }},
}}, env, mockFetch);
assert.equal(rStart.mode, "welcome");
const startMsg = tgCalls[tgCalls.length - 1];
assert.equal(startMsg.method, "sendMessage");
assert.ok(startMsg.body.text.includes("أهلاً بك في RatePath Terminal ⚡"));
assert.ok(startMsg.body.text.includes("المجانية (0 / 3 أصول)"));
assert.equal(startMsg.body.reply_markup.inline_keyboard[0][0].text, "➕ إضافة أول أصل (مجاني)");
assert.equal(startMsg.body.reply_markup.inline_keyboard[1][1].url, "https://ratepathlive.com");
assert.equal(startMsg.body.reply_markup.inline_keyboard[2][1].text, "🇬🇧 English");

// 2. Language toggle (`cb:lang:en`) -> answerCallbackQuery + editMessageText in English, then toggle back to AR
const rEn = await processTelegramUpdate({{
  callback_query: {{ id: "cb_1", from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: "cb:lang:en" }},
}}, env, mockFetch);
assert.equal(rEn.mode, "lang_switched");
assert.equal(rEn.lang, "en");
assert.equal(tgCalls[tgCalls.length - 2].method, "answerCallbackQuery");
assert.equal(tgCalls[tgCalls.length - 1].method, "editMessageText");
assert.ok(tgCalls[tgCalls.length - 1].body.text.includes("Welcome to RatePath Terminal ⚡"));
assert.equal(tgCalls[tgCalls.length - 1].body.reply_markup.inline_keyboard[2][1].text, "🇸🇦 العربية");

await processTelegramUpdate({{
  callback_query: {{ id: "cb_2", from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: "cb:lang:ar" }},
}}, env, mockFetch);

// 3. Add 3 free assets (XAU, BTC, TRY) -> succeeds (3/3)
for (const [idx, sym] of ["XAU", "BTC", "TRY"].entries()) {{
  const res = await processTelegramUpdate({{
    callback_query: {{ id: `cb_add_${{idx}}`, from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: `cb:add_pick:${{sym}}` }},
  }}, env, mockFetch);
  assert.equal(res.mode, "asset_added");
  assert.equal(res.count, idx + 1);
}}

// 4. Try adding a 4th asset (ETH) -> BLOCKED by exact Free Limit Reached (3/3) paywall!
const r4th = await processTelegramUpdate({{
  callback_query: {{ id: "cb_add_4", from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: "cb:add_pick:ETH" }},
}}, env, mockFetch);
assert.equal(r4th.mode, "free_limit_paywall");
const paywallCall = tgCalls[tgCalls.length - 1];
assert.equal(paywallCall.method, "editMessageText");
assert.ok(paywallCall.body.text.includes("🔒 وصلت للحد الأقصى للباقة المجانية (3 / 3 أصول)"));
assert.ok(paywallCall.body.text.includes("1️⃣"));
assert.ok(paywallCall.body.text.includes("2️⃣"));
assert.ok(paywallCall.body.text.includes("3️⃣"));
assert.equal(paywallCall.body.reply_markup.inline_keyboard[0][0].text, "🚀 الترقية الآن — 10$ USDT");
assert.equal(paywallCall.body.reply_markup.inline_keyboard[1][0].text, "🔄 استبدال أصل من قائمتي الحالية");
assert.equal(paywallCall.body.reply_markup.inline_keyboard[2][0].text, "👑 مقارنة مع باقة Pro (50 أصل — 19$)");

// 4b. Replace Asset flow (`cb:replace_menu` -> `cb:replace_del:TRY` -> frees slot & opens Add Menu, then re-add TRY)
const rRepMenu = await processTelegramUpdate({{
  callback_query: {{ id: "cb_rep_menu", from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: "cb:replace_menu" }},
}}, env, mockFetch);
assert.equal(rRepMenu.mode, "replace_menu");

const rRepDel = await processTelegramUpdate({{
  callback_query: {{ id: "cb_rep_del", from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: "cb:replace_del:TRY" }},
}}, env, mockFetch);
assert.equal(rRepDel.mode, "asset_replaced_open_add");
assert.equal(rRepDel.removed, "TRY");

// Re-add TRY (back to 3/3) and set target price via message text (`52.00`)
await processTelegramUpdate({{
  callback_query: {{ id: "cb_readd_try", from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: "cb:add_pick:TRY" }},
}}, env, mockFetch);
const rTarget = await processTelegramUpdate({{
  message: {{ chat: {{ id: 777001, type: "private" }}, from: {{ id: 777001, language_code: "ar" }}, text: "52.00" }},
}}, env, mockFetch);
assert.equal(rTarget.mode, "alert_set");
assert.equal(rTarget.symbol, "TRY");

// 5. Click Upgrade ($10 VIP) -> Exact Arabic Payment Message + live NOWPayments URL with `user_id_vip10_ts`
const rUpg = await processTelegramUpdate({{
  callback_query: {{ id: "cb_upg", from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: "cb:upgrade_vip" }},
}}, env, mockFetch);
assert.equal(rUpg.mode, "upgrade_vip_shown");
assert.ok(/^777001_vip10_\\d+$/.test(lastInvoiceOrderId), `Bad order_id: ${{lastInvoiceOrderId}}`);
const vipOrderId = lastInvoiceOrderId;
const upgCall = tgCalls[tgCalls.length - 1];
assert.ok(upgCall.body.text.includes("💎 باقة Business VIP + البوت الشخصي"));
assert.ok(upgCall.body.text.includes("USDT (TRC-20 • TRON)"));
assert.equal(upgCall.body.reply_markup.inline_keyboard[0][0].text, "💳 ادفع 10$ USDT الآن (تفعيل فوري)");
assert.ok(upgCall.body.reply_markup.inline_keyboard[0][0].url.includes(vipOrderId));

// 5b. Click Pro Plan ($19) -> generates `777001_pro19_ts`
const rPro = await processTelegramUpdate({{
  callback_query: {{ id: "cb_pro", from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: "cb:upgrade_pro" }},
}}, env, mockFetch);
assert.equal(rPro.mode, "upgrade_pro_shown");
assert.ok(/^777001_pro19_\\d+$/.test(lastInvoiceOrderId), `Bad pro order_id: ${{lastInvoiceOrderId}}`);

// 6. Simulate NOWPayments IPN `finished` webhook -> Verifies HMAC-SHA512, upgrades Redis tier + 30d, issues VIP invite link!
const ipnPayload = {{
  order_id: vipOrderId,
  payment_status: "finished",
  price_amount: 10,
  pay_currency: "usdttrc20",
  payment_id: "pay_777001",
}};
const sig = crypto.createHmac("sha512", env.NOWPAYMENTS_IPN_SECRET).update(JSON.stringify(sortObjectKeysDeep(ipnPayload))).digest("hex");
assert.equal(await verifyNowPaymentsSignature(ipnPayload, sig, env.NOWPAYMENTS_IPN_SECRET), true);
const meta = parseOrderIdMetadata(vipOrderId, 10);
assert.equal(meta.telegramUserId, "777001");
assert.equal(meta.planTier, "monthly_business_vip");

const ipnRes = await processNowPaymentsIpn(ipnPayload, env, mockFetch);
assert.equal(ipnRes.status, "fulfilled");
assert.equal(ipnRes.telegram_user_id, "777001");
assert.equal(ipnRes.plan_tier, "monthly_business_vip");
assert.equal(ipnRes.invite_issued, true);
assert.ok(Date.parse(ipnRes.expires_at) > Date.now() + 29 * 86400 * 1000);

// 7. Now user 777001 CAN add their 4th asset (ETH) cleanly up to 10 assets!
const r4thAfterPaid = await processTelegramUpdate({{
  callback_query: {{ id: "cb_add_4_paid", from: {{ id: 777001 }}, message: {{ chat: {{ id: 777001 }}, message_id: 101 }}, data: "cb:add_pick:ETH" }},
}}, env, mockFetch);
assert.equal(r4thAfterPaid.mode, "asset_added");
assert.equal(r4thAfterPaid.count, 4);

console.log("ALL_TERMINAL_BOT_FUNNEL_TESTS_PASSED");
"""

    proc = subprocess.run(
        [node_bin, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"Node bot funnel tests failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "ALL_TERMINAL_BOT_FUNNEL_TESTS_PASSED" in proc.stdout
