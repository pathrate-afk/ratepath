"""Unit Tests for Single Pinned Channel Header, Link-Free Posts, Special-Topic Deep Links & VIP Edge Bot."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import httpx

from backend.agents.contracts import AgentReport, OrchestratorDecision, StrategistBrief
from backend.agents.orchestrator import classify_special_topic
from backend.config import WORKSPACE_ROOT, Settings
from backend.pin_manager import (
    PinnedMessageStore,
    build_pinned_header_text,
    ensure_pinned_channel_header,
    pinned_redis_key,
)
from backend.rates_bulletin import RateMover, _SPEC_BY_ID, build_rates_bulletin_payload
from backend.setup_telegram_live import activate_telegram_live
from backend.telegram_bot import (
    build_public_channel_payload,
    build_special_topic_deep_link,
    build_vip_channel_payload,
)


def _make_live_mock_settings(site_url: str = "https://ratepath.io") -> Settings:
    return Settings(
        gemini_api_key="placeholder",
        groq_api_key="placeholder",
        openrouter_api_key="placeholder",
        supabase_url="placeholder",
        supabase_key="placeholder",
        upstash_redis_rest_url="https://mock-redis.upstash.io",
        upstash_redis_rest_token="live_upstash_token",
        telegram_bot_token="123456:LIVE_BOT_TOKEN",
        telegram_public_channel="placeholder",
        telegram_vip_channel="placeholder",
        telegram_public_channel_en="@RatePathEN",
        telegram_vip_channel_en="@RatePathVIP_EN",
        telegram_public_channel_ar="@RatePathAR",
        telegram_vip_channel_ar="@RatePathVIP_AR",
        admin_chat_id="999888777",
        mini_app_url=f"{site_url}/",
        public_site_url=site_url,
        telegram_webhook_secret="super_secret_webhook_token_123",
    )


def test_pin_manager_idempotency_and_in_place_url_update(tmp_path: Path) -> None:
    """Verify pin_manager sends+pins once, skips duplicates on re-run, and edits in-place when URL changes."""
    ar_header = build_pinned_header_text("https://ratepath.io", "ar")
    en_header = build_pinned_header_text("https://ratepath.io", "en")
    assert "لمزيد من المعلومات وتفاصيل أكثر وأسعار السوق اللحظية" in ar_header
    assert "https://ratepath.io/?lang=ar" in ar_header
    assert "For deeper market details, full analytics, and live rates" in en_header
    assert "https://ratepath.io/?lang=en" in en_header

    redis_kv: dict[str, str] = {}
    tg_calls: list[tuple[str, dict[str, object]]] = []
    next_msg_id = {"n": 500}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "upstash.io" in url:
            cmd = json.loads(request.content.decode("utf-8"))
            if cmd[0] == "GET":
                return httpx.Response(200, json={"result": redis_kv.get(cmd[1])})
            if cmd[0] == "SET":
                redis_kv[cmd[1]] = str(cmd[2])
                return httpx.Response(200, json={"result": "OK"})
        if "api.telegram.org" in url:
            method = url.rsplit("/", 1)[-1]
            body = json.loads(request.content.decode("utf-8"))
            tg_calls.append((method, body))
            if method == "sendMessage":
                next_msg_id["n"] += 1
                return httpx.Response(200, json={"ok": True, "result": {"message_id": next_msg_id["n"]}})
            if method in ("pinChatMessage", "editMessageText", "setWebhook"):
                return httpx.Response(200, json={"ok": True, "result": True, "description": "OK"})
        return httpx.Response(404)

    pin_file = tmp_path / "pinned_messages.tmp.json"
    cfg_v1 = _make_live_mock_settings("https://ratepath.io")
    cfg_v2 = _make_live_mock_settings("https://app.ratepath.io")
    store_v1 = PinnedMessageStore(settings=cfg_v1, local_path=pin_file)
    store_v2 = PinnedMessageStore(settings=cfg_v2, local_path=pin_file)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            # 1. First run -> sendMessage + pinChatMessage(disable_notification=True) + Redis SET
            r1 = await ensure_pinned_channel_header("@RatePathAR", "ar", settings=cfg_v1, store=store_v1, client=client)
            assert r1["status"] == "sent_and_pinned"
            assert r1["message_id"] == 501
            assert pinned_redis_key("@RatePathAR") in redis_kv
            assert tg_calls[-2][0] == "sendMessage"
            assert tg_calls[-1][0] == "pinChatMessage"
            assert tg_calls[-1][1]["disable_notification"] is True

            # 2. Second run (same URL) -> 100% idempotent, ZERO new Telegram API calls!
            calls_before = len(tg_calls)
            r2 = await ensure_pinned_channel_header("@RatePathAR", "ar", settings=cfg_v1, store=store_v1, client=client)
            assert r2["status"] == "unchanged"
            assert r2["message_id"] == 501
            assert len(tg_calls) == calls_before

            # 3. Third run (PUBLIC_SITE_URL changed) -> editMessageText in-place on message_id=501!
            r3 = await ensure_pinned_channel_header("@RatePathAR", "ar", settings=cfg_v2, store=store_v2, client=client)
            assert r3["status"] == "edited_in_place"
            assert r3["message_id"] == 501
            assert tg_calls[-1][0] == "editMessageText"
            assert "https://app.ratepath.io/?lang=ar" in str(tg_calls[-1][1]["text"])

            # 4. Full one-command activation script across all 4 channels + setWebhook
            act = await activate_telegram_live(settings=cfg_v2, pin_store=store_v2, client=client)
            assert len(act["pins"]) == 4
            assert act["webhook"]["status"] == "registered"
            assert act["webhook"]["webhook_url"] == "https://app.ratepath.io/api/telegram-webhook"
            webhook_calls = [body for m, body in tg_calls if m == "setWebhook"]
            assert len(webhook_calls) == 1
            assert webhook_calls[0]["secret_token"] == "super_secret_webhook_token_123"

    asyncio.run(_run())


def test_link_free_regular_posts_vs_special_topic_deep_links() -> None:
    """Standard News & Rates Bulletins have ZERO URLs/buttons; ONLY `is_special_topic=True` attaches deep link."""
    sample_news = {
        "source_name": "CNBC",
        "source_url": "https://www.cnbc.com/story",
        "en": {
            "title": "Treasury yields hold steady as markets digest retail sales https://spam.example.com",
            "summary": "Consumers kept spending steadily last month with minimal inflationary pressure.",
            "macro_thesis": "Balanced capital positioning keeps gold and bitcoin range-bound for now.",
        },
        "ar": {
            "title": "استقرار عوائد السندات الأمريكية مع هدوء بيانات التجزئة",
            "summary": "حافظ المستهلكون على وتيرة إنفاق متوازنة خلال الشهر الماضي بدون ضغوط تضخمية مفاجئة.",
            "macro_thesis": "توازن السيولة يبقي الذهب والبيتكوين ضمن نطاقات مستقرة حالياً.",
        },
        "priced_in_warning": {"active": False, "severity": "low", "en": "Fresh", "ar": "جديد"},
        "assets_bias_24h": {"USD": "neutral", "XAU": "bullish", "BTC": "bearish"},
    }

    # 1. Regular News Alert (Public & VIP) -> STRICTLY ZERO URLs and NO reply_markup
    reg_ar = build_public_channel_payload(sample_news, {}, chat_id="@RatePathAR", mini_app_url="https://ratepath.io", lang="ar")
    reg_en = build_public_channel_payload(sample_news, {}, chat_id="@RatePathEN", mini_app_url="https://ratepath.io", lang="en")
    reg_vip = build_vip_channel_payload(sample_news, {}, chat_id="@RatePathVIP_AR", mini_app_url="https://ratepath.io", lang="ar")
    for p in (reg_ar, reg_en, reg_vip):
        assert "reply_markup" not in p
        assert "http://" not in p["text"].lower()
        assert "https://" not in p["text"].lower()
        assert p["text"].count("\n\n") >= 2

    # 2. Regular Rates Bulletin -> STRICTLY ZERO URLs and NO reply_markup
    mover = RateMover(
        spec=_SPEC_BY_ID["SYP"],
        previous_price=Decimal("150.00"),
        current_price=Decimal("152.50"),
        change_pct=Decimal("1.67"),
    )
    bul_ar = build_rates_bulletin_payload([mover], chat_id="@RatePathAR", lang="ar")
    assert "reply_markup" not in bul_ar
    assert "http" not in bul_ar["text"].lower()

    # 3. Special-Topic Alert (`is_special_topic=True`) -> exact `{PUBLIC_SITE_URL}/?asset={SYMBOL}&lang={lang}`
    assert build_special_topic_deep_link("https://ratepath.io/", "btc", "ar") == "https://ratepath.io/?asset=BTC&lang=ar"
    special_ar = build_public_channel_payload(
        {**sample_news, "is_special_topic": True, "topic_asset": "BTC"},
        {},
        chat_id="@RatePathAR",
        mini_app_url="https://ratepath.io",
        lang="ar",
    )
    assert "https://ratepath.io/?asset=BTC&lang=ar" in special_ar["text"]
    assert special_ar["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://ratepath.io/?asset=BTC&lang=ar"

    # 4. Orchestrator `classify_special_topic`: solvency trap -> True ("BTC"); calm consolidation -> False
    trap_brief = StrategistBrief(
        event_en="Major stablecoin depeg and TVL drain",
        event_ar="نزيف سيولة وفك ارتباط عملة مستقرة",
        money_flow_en="Capital exits altcoins into cash.",
        money_flow_ar="خروج السيولة نحو الكاش والذهب.",
        verdict_en="RETAIL_TRAP active.",
        verdict_ar="فخ أفراد نشط.",
        matched_twin_key="crypto-2023-usdc-svb-depeg",
        stage="stage_1_cash_dash",
        priced_in_active=True,
        assets_bias_24h={"USD": "bullish", "XAU": "bullish", "BTC": "bearish"},
        raw_conviction=90,
    )
    trap_decision = OrchestratorDecision(
        timestamp="2026-11-26T00:00:00Z",
        publish_allowed=True,
        auditor_approved=True,
        conviction_score=90,
        retail_trap_flagged=True,
        content_hash="abc123456789",
        brief=trap_brief,
    )
    solv_report = AgentReport(
        agent_id="agent_5_crypto_solvency",
        tier="hard_truth",
        healthy=True,
        signal_direction="stress",
        confidence=92,
        headline="TVL drain",
        flags=["TVL_DRAIN"],
    )
    assert classify_special_topic(trap_decision, [solv_report]) == (True, "BTC")

    calm_decision = trap_decision.model_copy(
        update={
            "conviction_score": 76,
            "retail_trap_flagged": False,
            "brief": trap_brief.model_copy(update={"stage": "consolidation"}),
        }
    )
    calm_report = solv_report.model_copy(update={"flags": []})
    assert classify_special_topic(calm_decision, [calm_report]) == (False, None)


def test_cloudflare_vip_telegram_webhook_gating_caching_and_coin_parsing() -> None:
    """Execute real Node.js ESM tests directly against `frontend/functions/api/telegram-webhook.js`."""
    webhook_path = WORKSPACE_ROOT / "frontend" / "functions" / "api" / "telegram-webhook.js"
    assert webhook_path.exists()
    node_bin = shutil.which("node")
    assert node_bin is not None, "Node.js is required to verify Cloudflare Pages Function ESM code"

    webhook_uri = webhook_path.as_uri()
    js_test_script = f"""
import assert from "node:assert/strict";
import {{
  verifyWebhookSecret,
  sanitizeTelegramHtml,
  parseUserAssetQuery,
  checkVipMembership,
  processTelegramUpdate,
  onRequestPost,
}} from "{webhook_uri}";

// 1. Secret token verification & strict Telegram HTML sanitization
assert.equal(verifyWebhookSecret("my_sec", "my_sec"), true);
assert.equal(verifyWebhookSecret("wrong", "my_sec"), false);
assert.equal(verifyWebhookSecret("", "my_sec"), false);
assert.equal(verifyWebhookSecret("placeholder", "placeholder"), false);
const cleanedHtml = sanitizeTelegramHtml(
  '<b>Price</b>: BTC < $60k & ETH > $2k<br><a href="https://phish.test">Click</a><script>alert(1)</script>'
);
assert.ok(cleanedHtml.includes("<b>Price</b>"));
assert.ok(cleanedHtml.includes("&lt; $60k &amp; ETH &gt; $2k"));
assert.ok(!cleanedHtml.includes("<a "));
assert.ok(!cleanedHtml.includes("<script"));
assert.ok(!cleanedHtml.includes("alert(1)"));

// Reject unauthorized POST with HTTP 401
const badResp = await onRequestPost({{
  request: new Request("https://ratepath.io/api/telegram-webhook", {{
    method: "POST",
    headers: {{ "X-Telegram-Bot-Api-Secret-Token": "bad_token" }},
    body: JSON.stringify({{}}),
  }}),
  env: {{ TELEGRAM_WEBHOOK_SECRET: "real_secret_999" }},
}});
assert.equal(badResp.status, 401);

// 2. Natural AR/EN custom coin & currency parsing
assert.equal(parseUserAssetQuery("اخبار عملة سولانا").symbol, "SOL");
assert.equal(parseUserAssetQuery("اخبار عملة سولانا").lang, "ar");
assert.equal(parseUserAssetQuery("وضع XRP").symbol, "XRP");
assert.equal(parseUserAssetQuery("وضع XRP").lang, "ar");
assert.equal(parseUserAssetQuery("تحليل الذهب").symbol, "XAU");
assert.equal(parseUserAssetQuery("الليرة السورية").symbol, "SYP");
assert.equal(parseUserAssetQuery("/ask PEPE", "en").symbol, "PEPE");
assert.equal(parseUserAssetQuery("/ask PEPE", "en").lang, "en");

// 3. Mocked environment testing Non-VIP lock, VIP getChatMember, 15m cache, 25/day rate limit & Supabase fallback
const redisStore = new Map();
const sentMessages = [];
let llmCalls = 0;

const mockFetch = async (url, opts = {{}}) => {{
  const u = String(url);
  if (u.includes("upstash.io")) {{
    const cmd = JSON.parse(opts.body);
    if (cmd[0] === "INCR") {{
      const next = (Number(redisStore.get(cmd[1])) || 0) + 1;
      redisStore.set(cmd[1], String(next));
      return new Response(JSON.stringify({{ result: next }}), {{ status: 200 }});
    }}
    if (cmd[0] === "EXPIRE") return new Response(JSON.stringify({{ result: 1 }}), {{ status: 200 }});
    if (cmd[0] === "GET") return new Response(JSON.stringify({{ result: redisStore.get(cmd[1]) ?? null }}), {{ status: 200 }});
    if (cmd[0] === "SET") {{
      redisStore.set(cmd[1], cmd[2]);
      return new Response(JSON.stringify({{ result: "OK" }}), {{ status: 200 }});
    }}
  }}
  if (u.includes("/getChatMember")) {{
    if (u.includes("user_id=2002")) {{
      return new Response(JSON.stringify({{ ok: true, result: {{ status: "member" }} }}), {{ status: 200 }});
    }}
    return new Response(JSON.stringify({{ ok: true, result: {{ status: "left" }} }}), {{ status: 200 }});
  }}
  if (u.includes("/rest/v1/vip_subscriptions")) {{
    if (u.includes("telegram_user_id=eq.3003")) {{
      return new Response(JSON.stringify([{{ id: "sub-1", expires_at: "2099-01-01T00:00:00Z" }}]), {{ status: 200 }});
    }}
    return new Response(JSON.stringify([]), {{ status: 200 }});
  }}
  if (u.includes("live_feed.json")) {{
    return new Response(JSON.stringify({{
      gold: {{ prices: {{ XAU_OZ: "3569.90", GRAM_21K: "100.43", GRAM_24K: "114.77" }} }},
      fiat: {{ rates: {{ SYP: {{ units_per_usd: "152.5000" }} }} }},
      crypto: {{ assets: {{ SOL: {{ spot_price: "148.20" }} }} }},
    }}), {{ status: 200 }});
  }}
  if (u.includes("catalog_feed.json")) {{
    return new Response(JSON.stringify({{
      fiat: {{ SYP: {{ units_per_usd: "152.5000", buy_rate: "152.2500", sell_rate: "152.7500", source: "sp-today" }} }},
      crypto: [
        {{ symbol: "SOL", name: "Solana", rank: 6, price_usd: "148.20", change_24h_pct: "+2.45" }},
        {{ symbol: "XRP", name: "XRP", rank: 4, price_usd: "2.48", change_24h_pct: "+1.80" }},
      ],
    }}), {{ status: 200 }});
  }}
  if (u.includes("news.google.com/rss")) {{
    return new Response("<rss><channel><item><title>Solana network fees rise on institutional DEX volume</title></item></channel></rss>", {{ status: 200 }});
  }}
  if (u.includes("api.groq.com")) {{
    llmCalls += 1;
    return new Response(JSON.stringify({{
      choices: [{{ message: {{ content: "📌 <b>السعر والزخم:</b>\\nسولانا عند <code>$148.20</code> بزخم صاعد.\\n\\n📰 <b>أهم أخبار العملة الآن:</b>\\n• نشاط مؤسسي قوي على الشبكة.\\n\\n💡 <b>الخلاصة واتجاه السيولة:</b>\\nالسيولة إيجابية مع مراقبة مستويات المقاومة." }} }}],
    }}), {{ status: 200 }});
  }}
  if (u.includes("/sendMessage")) {{
    sentMessages.push(JSON.parse(opts.body));
    return new Response(JSON.stringify({{ ok: true, result: {{ message_id: sentMessages.length }} }}), {{ status: 200 }});
  }}
  return new Response("Not Found", {{ status: 404 }});
}};

const mockEnv = {{
  TELEGRAM_BOT_TOKEN: "123:TOKEN",
  TELEGRAM_WEBHOOK_SECRET: "sec_123",
  TELEGRAM_VIP_CHANNEL_AR: "@RatePathVIP_AR",
  TELEGRAM_VIP_CHANNEL_EN: "@RatePathVIP_EN",
  ADMIN_CHAT_ID: "9999",
  SUPABASE_URL: "https://mock.supabase.co",
  SUPABASE_KEY: "mock_key",
  UPSTASH_REDIS_REST_URL: "https://mock.upstash.io",
  UPSTASH_REDIS_REST_TOKEN: "mock_tok",
  GROQ_API_KEY_1: "mock_groq_key",
  PUBLIC_SITE_URL: "https://ratepath.io",
}};

// 3a. Non-VIP user gets live price + VIP lock notice (ZERO LLM call)
const nonVipRes = await processTelegramUpdate({{
  message: {{ chat: {{ id: 1001, type: "private" }}, from: {{ id: 1001, language_code: "ar" }}, text: "الليرة السورية" }},
}}, mockEnv, mockFetch);
assert.equal(nonVipRes.mode, "non_vip_locked");
assert.equal(llmCalls, 0);
assert.ok(sentMessages[0].text.includes("152.50 ل.س"));
assert.ok(sentMessages[0].text.includes("RatePath VIP"));
assert.ok(!sentMessages[0].text.includes("\\uD83C\\uDDF8\\uD83C\\uDDF9"));

// 3b. Verified VIP channel member gets 3-part AI briefing (cached 15m)
const vipRes1 = await processTelegramUpdate({{
  message: {{ chat: {{ id: 2002, type: "private" }}, from: {{ id: 2002, language_code: "ar" }}, text: "اخبار عملة سولانا" }},
}}, mockEnv, mockFetch);
assert.equal(vipRes1.mode, "vip_live_generated");
assert.equal(llmCalls, 1);
assert.ok(sentMessages[1].text.includes("📌 <b>السعر والزخم:</b>"));
assert.ok(sentMessages[1].text.includes("📰 <b>أهم أخبار العملة الآن:</b>"));
assert.ok(sentMessages[1].text.includes("💡 <b>الخلاصة واتجاه السيولة:</b>"));
assert.ok(sentMessages[1].text.split("\\n\\n").length >= 3);

// 3c. Admin / Supabase VIP user asking same coin hits 15-min shared cache (ZERO extra LLM calls!)
const supaVipCheck = await checkVipMembership(3003, mockEnv, mockFetch);
assert.equal(supaVipCheck.isVip, true);
assert.equal(supaVipCheck.reason, "supabase_vip_subscription");

const vipRes2 = await processTelegramUpdate({{
  message: {{ chat: {{ id: 9999, type: "private" }}, from: {{ id: 9999, language_code: "ar" }}, text: "سولانا" }},
}}, mockEnv, mockFetch);
assert.equal(vipRes2.mode, "vip_cache_hit");
assert.equal(llmCalls, 1);

// 3d. Enforce 25/day rate limit
const today = new Date().toISOString().slice(0, 10);
redisStore.set(`ratepath:vip_rl:2002:${{today}}`, "25");
const rlRes = await processTelegramUpdate({{
  message: {{ chat: {{ id: 2002, type: "private" }}, from: {{ id: 2002, language_code: "ar" }}, text: "وضع XRP" }},
}}, mockEnv, mockFetch);
assert.equal(rlRes.mode, "vip_rate_limited");

console.log("VIP_WEBHOOK_NODE_TESTS_PASSED");
"""
    proc = subprocess.run(
        [node_bin, "--input-type=module", "-e", js_test_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"Node webhook tests failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "VIP_WEBHOOK_NODE_TESTS_PASSED" in proc.stdout
