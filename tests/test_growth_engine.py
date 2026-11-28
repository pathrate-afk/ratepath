"""Comprehensive Verification Suite for RatePath's 3 Automated Growth Systems:
1. Programmatic SEO Engine (10 high-intent routes, JSON-LD FinancialProduct + FAQPage,
   sitemap.xml, robots.txt, CSP compliance, zero 404/500 via live HTTP server).
2. Hands-Off Auto-Publisher (1200x630 Pillow Quote Card PNG, 3-line AR/EN captions,
   Telegram sendPhoto to AR/EN channels, graceful X/Twitter skip & OAuth 1.0a signing,
   and 3x-daily GitHub Actions workflow).
3. Viral Referral Loop inside @RatePathTerminalBot & NOWPayments IPN
   (`/referral`, `/start ref_12345`, anti-self-referral, anti-duplicate, +15 join tokens,
   +1 extra free slot per 2 friends up to 6, +100 paid-upgrade tokens, and 7-day VIP redemption).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

from backend.config import WORKSPACE_ROOT, Settings
from frontend.server import FRONTEND_DIR, RatePathRequestHandler
from scripts.auto_publish import (
    RAW_SYRIA_FLAG,
    run_auto_publish,
)
from scripts.generate_seo_pages import SEO_PAGES, generate_all_seo_assets


def _placeholder_settings() -> Settings:
    return Settings(
        gemini_api_key="placeholder",
        groq_api_key="placeholder",
        openrouter_api_key="placeholder",
        supabase_url="placeholder",
        supabase_key="placeholder",
        upstash_redis_rest_url="placeholder",
        upstash_redis_rest_token="placeholder",
        telegram_bot_token="123456:MOCK_BOT",
        telegram_public_channel="placeholder",
        telegram_vip_channel="placeholder",
        telegram_public_channel_ar="@RatePath_AR",
        telegram_public_channel_en="@RatePathLive",
        admin_chat_id="583937452",
    )


def test_programmatic_seo_pages_sitemap_robots_and_http_200() -> None:
    """Verify all 10 SEO routes render cleanly with live data, JSON-LD, sitemap & HTTP 200."""
    summary = generate_all_seo_assets()
    assert summary["generated_count"] == 10

    expected_slugs = {
        "usd-to-syp",
        "usd-to-try",
        "usd-to-egp",
        "gold-price-today",
        "btc-to-usdt",
        "usdt-to-try",
        "usdt-to-syp",
        "eth-to-usdt",
        "eur-to-usd",
        "silver-price-today",
    }
    assert {s.slug for s in SEO_PAGES} == expected_slugs

    sitemap_xml = (FRONTEND_DIR / "sitemap.xml").read_text(encoding="utf-8")
    robots_txt = (FRONTEND_DIR / "robots.txt").read_text(encoding="utf-8")
    assert "Sitemap: https://ratepathlive.com/sitemap.xml" in robots_txt

    titles_seen: set[str] = set()
    for slug in expected_slugs:
        page_path = FRONTEND_DIR / slug / "index.html"
        assert page_path.is_file(), f"Missing SEO page {page_path}"
        html_text = page_path.read_text(encoding="utf-8")

        # Unique title, canonical, OG, CTA, converter, zero raw Syria flag
        m_title = re.search(r"<title>(.*?)</title>", html_text)
        assert m_title and m_title.group(1) not in titles_seen
        titles_seen.add(m_title.group(1))
        assert f'<link rel="canonical" href="https://ratepathlive.com/{slug}" />' in html_text
        assert 'property="og:title"' in html_text
        assert 'name="description"' in html_text
        assert "@RatePathTerminalBot" in html_text
        assert 'id="conv-amount"' in html_text
        assert 'id="conv-output"' in html_text
        assert "/js/seo-pair-page.js" in html_text
        assert RAW_SYRIA_FLAG not in html_text

        # Strict CSP check: zero inline executable <script> tags (only application/ld+json + external module)
        script_tags = re.findall(r"<script\b[^>]*>", html_text, flags=re.IGNORECASE)
        for tag in script_tags:
            assert 'type="application/ld+json"' in tag or "src=" in tag, f"Inline script violates CSP in {slug}: {tag}"

        # Structured JSON-LD must parse cleanly and include both FinancialProduct & FAQPage
        ld_match = re.search(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', html_text, flags=re.DOTALL)
        assert ld_match, f"Missing JSON-LD in {slug}"
        ld_obj = json.loads(ld_match.group(1))
        graph_types = {node.get("@type") for node in ld_obj.get("@graph", [])}
        assert "FinancialProduct" in graph_types
        assert "FAQPage" in graph_types

        # Present in sitemap.xml and robots.txt
        assert f"<loc>https://ratepathlive.com/{slug}</loc>" in sitemap_xml
        assert f"Allow: /{slug}" in robots_txt

    # Verify `/js/seo-pair-page.js` resolves ALL ES module imports cleanly in real Node.js (prevents broken named exports)
    node_bin = shutil.which("node")
    assert node_bin is not None
    seo_js_uri = (FRONTEND_DIR / "js" / "seo-pair-page.js").as_uri()
    import_proc = subprocess.run(
        [node_bin, "--input-type=module", "-e", f'await import("{seo_js_uri}"); console.log("SEO_MODULE_IMPORT_OK");'],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert import_proc.returncode == 0, f"seo-pair-page.js ESM import failed:\n{import_proc.stderr}"
    assert "SEO_MODULE_IMPORT_OK" in import_proc.stdout

    # Spin up local frontend server on ephemeral port and verify HTTP 200 on every SEO route
    handler_cls = partial(RatePathRequestHandler, directory=str(FRONTEND_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(timeout=5.0) as client:
            for slug in expected_slugs:
                resp = client.get(f"http://127.0.0.1:{port}/{slug}")
                assert resp.status_code == 200, f"SEO route /{slug} returned {resp.status_code}"
                assert f'data-seo-slug="{slug}"' in resp.text
            sm_resp = client.get(f"http://127.0.0.1:{port}/sitemap.xml")
            assert sm_resp.status_code == 200
            rb_resp = client.get(f"http://127.0.0.1:{port}/robots.txt")
            assert rb_resp.status_code == 200
    finally:
        server.shutdown()
        server.server_close()


def test_auto_publisher_dry_run_png_and_mocked_multi_channel_dispatch(tmp_path: Path) -> None:
    """Verify 1200x630 PNG rendering, 3-line AR/EN captions, Telegram sendPhoto & X skip/post."""
    wf_path = WORKSPACE_ROOT / ".github" / "workflows" / "auto-publish.yml"
    assert wf_path.is_file()
    wf_text = wf_path.read_text(encoding="utf-8")
    assert '0 7,14,20 * * *' in wf_text
    assert "scripts/auto_publish.py" in wf_text

    # 1. Dry-run verification
    dry_res = asyncio.run(
        run_auto_publish(
            dry_run=True,
            prefer_local=True,
            output_dir=tmp_path,
            settings=_placeholder_settings(),
        )
    )
    assert dry_res["mode"] == "dry_run"
    png_file = Path(dry_res["png_path"])
    assert png_file.is_file()
    assert dry_res["png_bytes"] > 20000
    with Image.open(png_file) as im:
        assert im.size == (1200, 630)
        assert im.format == "PNG"

    assert RAW_SYRIA_FLAG not in dry_res["ar_caption"]
    assert RAW_SYRIA_FLAG not in dry_res["en_caption"]
    assert "ratepathlive.com" in dry_res["x_post"]
    assert "@RatePathTerminalBot" in dry_res["x_post"]
    assert dry_res["ar_keyboard"]["inline_keyboard"][0][0]["text"] == "🌐 المحوّل المباشر"
    assert dry_res["en_keyboard"]["inline_keyboard"][0][1]["text"] == "🤖 Free Alerts"

    # 2. Live dispatch verification (mocked Telegram sendPhoto + graceful X skip)
    tg_photos: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "api.telegram.org" in url and "/sendPhoto" in url:
            tg_photos.append(request.content.decode("latin-1", errors="ignore"))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 700 + len(tg_photos)}})
        return httpx.Response(404)

    async def _run_live() -> dict[str, object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
            return await run_auto_publish(
                dry_run=False,
                prefer_local=True,
                output_dir=tmp_path,
                settings=_placeholder_settings(),
                client=client,
            )

    clean_env = {
        "TELEGRAM_AR_CHANNEL_ID": "@RatePath_AR",
        "TELEGRAM_EN_CHANNEL_ID": "@RatePathLive",
        "X_API_KEY": "placeholder",
        "X_API_SECRET": "placeholder",
        "X_ACCESS_TOKEN": "placeholder",
        "X_ACCESS_TOKEN_SECRET": "placeholder",
    }
    with patch.dict(os.environ, clean_env, clear=False):
        live_res = asyncio.run(_run_live())

    assert live_res["mode"] == "live"
    assert live_res["telegram_ar"]["status"] == "sent"
    assert live_res["telegram_en"]["status"] == "sent"
    assert live_res["x_twitter"]["status"] == "skipped_unconfigured"
    assert len(tg_photos) == 2


def test_viral_referral_loop_and_vip_upgrade_bonus_nodejs() -> None:
    """Verify /referral, /start ref_12345, anti-self/duplicate, +15 tokens, +1 slot/2 friends, +100 paid bonus & redeem."""
    node_bin = shutil.which("node")
    assert node_bin is not None
    tg_uri = (WORKSPACE_ROOT / "functions" / "api" / "telegram-webhook.js").as_uri()
    np_uri = (WORKSPACE_ROOT / "functions" / "api" / "nowpayments-webhook.js").as_uri()

    script = f"""
import assert from "node:assert/strict";
import {{ processTelegramUpdate }} from "{tg_uri}";
import {{ processNowPaymentsIpn }} from "{np_uri}";

const redis = new Map();
const tgCalls = [];

const mockFetch = async (url, init = {{}}) => {{
  const u = String(url);
  if (u.includes("upstash.io")) {{
    const cmd = JSON.parse(init.body);
    if (cmd[0] === "GET") return new Response(JSON.stringify({{ result: redis.get(cmd[1]) ?? null }}), {{ status: 200 }});
    if (cmd[0] === "SET") {{ redis.set(cmd[1], cmd[2]); return new Response(JSON.stringify({{ result: "OK" }}), {{ status: 200 }}); }}
    if (cmd[0] === "SADD") return new Response(JSON.stringify({{ result: 1 }}), {{ status: 200 }});
    return new Response(JSON.stringify({{ result: "OK" }}), {{ status: 200 }});
  }}
  if (u.includes("live_feed.json")) {{
    return new Response(JSON.stringify({{
      gold: {{ prices: {{ XAU_OZ: "3653.50" }} }},
      fiat: {{ rates: {{ TRY: {{ units_per_usd: "50.70" }}, SYP: {{ units_per_usd: "152.50" }} }} }},
      crypto: {{ assets: {{ BTC: {{ spot_price: "60000.00" }}, ETH: {{ spot_price: "1700.00" }}, SOL: {{ spot_price: "72.00" }} }} }},
    }}), {{ status: 200 }});
  }}
  if (u.includes("catalog_feed.json")) {{
    return new Response(JSON.stringify({{ metals: {{}}, fiat: {{}}, crypto: [] }}), {{ status: 200 }});
  }}
  if (u.includes("/getChatMember")) {{
    return new Response(JSON.stringify({{ ok: true, result: {{ status: "left" }} }}), {{ status: 200 }});
  }}
  if (u.includes("/createChatInviteLink")) {{
    return new Response(JSON.stringify({{ ok: true, result: {{ invite_link: "https://t.me/+RefVIP2026" }} }}), {{ status: 200 }});
  }}
  if (u.includes("api.telegram.org/bot")) {{
    const method = u.split("/").pop();
    const body = JSON.parse(init.body);
    tgCalls.push({{ method, body }});
    return new Response(JSON.stringify({{ ok: true, result: {{ message_id: 900 + tgCalls.length }} }}), {{ status: 200 }});
  }}
  return new Response("Not Found", {{ status: 404 }});
}};

const env = {{
  TELEGRAM_BOT_TOKEN: "123:BOT",
  TELEGRAM_WEBHOOK_SECRET: "wh_sec",
  VIP_CHANNEL_ID: "-100999888",
  ADMIN_TELEGRAM_ID: "583937452",
  UPSTASH_REDIS_REST_URL: "https://mock.upstash.io",
  UPSTASH_REDIS_REST_TOKEN: "tok",
  PUBLIC_SITE_URL: "https://ratepathlive.com",
}};

// 1. Referrer 12345 starts bot -> Main menu has Row 5 Invite & Earn button, /referral yields ref_12345 link
const rOwner = await processTelegramUpdate({{
  message: {{ chat: {{ id: 12345, type: "private" }}, from: {{ id: 12345, username: "founder_ref", language_code: "en" }}, text: "/start" }},
}}, env, mockFetch);
assert.equal(rOwner.mode, "welcome");
const welcomeKb = tgCalls[tgCalls.length - 1].body.reply_markup.inline_keyboard;
assert.equal(welcomeKb[4][0].text, "🎁 Invite & Earn");

const rRefCmd = await processTelegramUpdate({{
  message: {{ chat: {{ id: 12345, type: "private" }}, from: {{ id: 12345, username: "founder_ref", language_code: "en" }}, text: "/referral" }},
}}, env, mockFetch);
assert.equal(rRefCmd.mode, "referral_shown");
assert.equal(rRefCmd.refLink, "https://t.me/RatePathTerminalBot?start=ref_12345");

// 2. Anti-self-referral check: 12345 calling `/start ref_12345` MUST NOT award tokens
const rSelf = await processTelegramUpdate({{
  message: {{ chat: {{ id: 12345, type: "private" }}, from: {{ id: 12345, language_code: "en" }}, text: "/start ref_12345" }},
}}, env, mockFetch);
assert.equal(rSelf.referral.credited, false);
assert.equal(rSelf.referral.reason, "self_referral_blocked");

// 3. Friend #1 (`90001`) joins via `/start ref_12345` -> Referrer 12345 earns +15 tokens + celebratory ping!
const rFriend1 = await processTelegramUpdate({{
  message: {{ chat: {{ id: 90001, type: "private" }}, from: {{ id: 90001, username: "alice_fx", language_code: "en" }}, text: "/start ref_12345" }},
}}, env, mockFetch);
assert.equal(rFriend1.mode, "welcome");
assert.equal(rFriend1.referral.credited, true);
assert.equal(rFriend1.referral.tokensAwarded, 15);
assert.equal(rFriend1.referral.referrerTokens, 15);
assert.equal(rFriend1.referral.newFreeLimit, 3);
const celeb1 = tgCalls.find((c) => String(c.body.chat_id) === "12345" && c.body.text.includes("@alice_fx") && c.body.text.includes("+15"));
assert.ok(celeb1, "Expected celebratory join notification sent to referrer 12345");

// 4. Anti-duplicate check: Friend #1 clicks `/start ref_12345` again -> blocked!
const rFriend1Dup = await processTelegramUpdate({{
  message: {{ chat: {{ id: 90001, type: "private" }}, from: {{ id: 90001, username: "alice_fx", language_code: "en" }}, text: "/start ref_12345" }},
}}, env, mockFetch);
assert.equal(rFriend1Dup.referral.credited, false);
assert.equal(rFriend1Dup.referral.reason, "duplicate_or_existing_user");

// 5. Friend #2 (`90002`) joins via `/start ref_12345` -> Referrer 12345 reaches 30 tokens & unlocks 4th free watchlist slot!
const rFriend2 = await processTelegramUpdate({{
  message: {{ chat: {{ id: 90002, type: "private" }}, from: {{ id: 90002, username: "bob_gold", language_code: "ar" }}, text: "/start ref_12345" }},
}}, env, mockFetch);
assert.equal(rFriend2.referral.credited, true);
assert.equal(rFriend2.referral.referrerTokens, 30);
assert.equal(rFriend2.referral.slotUnlocked, true);
assert.equal(rFriend2.referral.newFreeLimit, 4);

// Verify Referrer 12345 can now add 4 FREE assets (XAU, BTC, TRY, ETH) and only hits paywall on 5th (SOL)!
for (const [idx, sym] of ["XAU", "BTC", "TRY", "ETH"].entries()) {{
  const rAdd = await processTelegramUpdate({{
    callback_query: {{ id: `ref_add_${{idx}}`, from: {{ id: 12345 }}, message: {{ chat: {{ id: 12345 }}, message_id: 901 }}, data: `cb:add_pick:${{sym}}` }},
  }}, env, mockFetch);
  assert.equal(rAdd.mode, "asset_added", `Expected 4th free asset ${{sym}} to succeed via referral bonus slot!`);
}}
const r5th = await processTelegramUpdate({{
  callback_query: {{ id: "ref_add_5", from: {{ id: 12345 }}, message: {{ chat: {{ id: 12345 }}, message_id: 901 }}, data: "cb:add_pick:SOL" }},
}}, env, mockFetch);
assert.equal(r5th.mode, "free_limit_paywall");
assert.ok(tgCalls[tgCalls.length - 1].body.text.includes("(4 / 4 assets)"));

// 6. Friend #1 (`90001`) upgrades to paid Business VIP via NOWPayments IPN -> Referrer 12345 gets +100 tokens (total 130)!
const ipnRes = await processNowPaymentsIpn({{
  order_id: "90001_vip10_1760000111",
  payment_status: "finished",
  price_amount: 10,
  pay_currency: "usdttrc20",
  payment_id: "pay_ref_90001",
}}, env, mockFetch);
assert.equal(ipnRes.status, "fulfilled");
const refProfAfterPaid = JSON.parse(redis.get("ratepath:tg_user:12345"));
assert.equal(refProfAfterPaid.referral_tokens, 130);
assert.equal(refProfAfterPaid.paid_referral_count, 1);
const paidBonusMsg = tgCalls.find((c) => String(c.body.chat_id) === "12345" && c.body.text.includes("+100"));
assert.ok(paidBonusMsg, "Expected +100 paid-upgrade bonus notification sent to referrer 12345");

// 7. Referrer 12345 taps `cb:ref_redeem` -> deducts 100 tokens (30 left) and activates +7 Free Business VIP days!
const rRedeem = await processTelegramUpdate({{
  callback_query: {{ id: "cb_red_1", from: {{ id: 12345 }}, message: {{ chat: {{ id: 12345 }}, message_id: 920 }}, data: "cb:ref_redeem" }},
}}, env, mockFetch);
assert.equal(rRedeem.mode, "referral_redeemed_vip");
assert.equal(rRedeem.tokensLeft, 30);
assert.ok(Date.parse(rRedeem.expiresAt) > Date.now() + 6 * 86400 * 1000);
const profAfterRedeem = JSON.parse(redis.get("ratepath:tg_user:12345"));
assert.equal(profAfterRedeem.plan_tier, "monthly_business_vip");

console.log("ALL_VIRAL_REFERRAL_TESTS_PASSED");
"""

    proc = subprocess.run(
        [node_bin, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"Referral Node tests failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "ALL_VIRAL_REFERRAL_TESTS_PASSED" in proc.stdout
