"""Comprehensive Tests for Executive VIP vs Public Differentiation, $10 USDT NOWPayments & Geo-Filtered Affiliates."""

from __future__ import annotations

import shutil
import subprocess

from backend.config import WORKSPACE_ROOT
from backend.telegram_bot import build_public_channel_payload, build_vip_channel_payload
from tests.test_contract_and_publisher import make_valid_feed_payload


def test_vip_channel_is_distinct_and_richer_than_public_channel() -> None:
    """VIP ($10/mo) posts MUST deliver exclusive Direction Matrix, 100-Yr Precedent, Conviction & Carry Radar."""
    feed = make_valid_feed_payload()
    # Ensure BTC has a high-yield annualized funding spread (>= 2.00%) so VIP Carry Radar renders
    feed["crypto"]["assets"]["BTC"]["funding"]["annualized_spread_pct"] = "12.40"
    feed["crypto"]["assets"]["BTC"]["funding"]["spread_bps"] = "1.13"
    feed["crypto"]["assets"]["BTC"]["funding"]["arbitrage_signal"] = "short_bybit_long_binance"

    news_item = {
        **feed["news"][0],
        "matched_twin_key": "hist-1973-oil-embargo-stagflation",
        "stage": "stage_2_hard_asset_rotation",
        "confidence_score": 86,
    }

    pub_ar = build_public_channel_payload(news_item, feed, chat_id="@RatePath_AR", lang="ar")["text"]
    vip_ar = build_vip_channel_payload(news_item, feed, chat_id="@RatePathVIP_AR", lang="ar")["text"]

    pub_en = build_public_channel_payload(news_item, feed, chat_id="@RatePathLive", lang="en")["text"]
    vip_en = build_vip_channel_payload(news_item, feed, chat_id="@RatePathVIP_EN", lang="en")["text"]

    # VIP must be materially richer and strictly distinct from Public
    assert len(vip_ar) > len(pub_ar) + 200
    assert len(vip_en) > len(pub_en) + 200

    # Public channel stays clean summary WITHOUT VIP-only institutional modules
    assert "خريطة اتجاه الأصول" not in pub_ar
    assert "24h Institutional Direction Matrix" not in pub_en
    assert "رادار فروق التمويل" not in pub_ar
    assert "VIP Funding Carry Radar" not in pub_en

    # VIP AR delivers full executive playbook
    assert "غرفة رجال الأعمال VIP" in vip_ar
    assert "خريطة اتجاه الأصول (24 ساعة)" in vip_ar
    assert "التوأم التاريخي (ذاكرة 100 عام)" in vip_ar
    assert "86/100" in vip_ar
    assert "رادار فروق التمويل" in vip_ar
    assert "@RatePathTerminalBot" in vip_ar
    assert "http" not in vip_ar.lower()  # Standard post remains link-free

    # VIP EN delivers full executive playbook
    assert "RatePath Business VIP Desk" in vip_en
    assert "24h Institutional Direction Matrix" in vip_en
    assert "100-Year Historical Twin" in vip_en
    assert "86/100" in vip_en
    assert "VIP Funding Carry Radar" in vip_en
    assert "@RatePathTerminalBot" in vip_en
    assert "http" not in vip_en.lower()


def test_nowpayments_10_usd_usdt_checkout_webhook_and_geo_affiliates_in_nodejs() -> None:
    """Run live Node.js ESM tests against create-vip-checkout.js, nowpayments-webhook.js, geo.js & affiliates.js."""
    node_bin = shutil.which("node")
    assert node_bin is not None, "Node.js is required to test Cloudflare Edge Functions and affiliates.js"

    checkout_uri = (WORKSPACE_ROOT / "functions" / "api" / "create-vip-checkout.js").as_uri()
    webhook_uri = (WORKSPACE_ROOT / "functions" / "api" / "nowpayments-webhook.js").as_uri()
    geo_uri = (WORKSPACE_ROOT / "functions" / "api" / "geo.js").as_uri()
    aff_uri = (WORKSPACE_ROOT / "frontend" / "js" / "affiliates.js").as_uri()
    mon_uri = (WORKSPACE_ROOT / "frontend" / "monetization.config.js").as_uri()

    js_script = f"""
import assert from "node:assert/strict";
import crypto from "node:crypto";
import {{ createVipCheckoutSession, normalizePayCurrency }} from "{checkout_uri}";
import {{ verifyNowPaymentsSignature, sortObjectKeysDeep, processNowPaymentsIpn }} from "{webhook_uri}";
import {{ onRequestGet as geoGet }} from "{geo_uri}";
import {{ selectGeoCompliantPartners }} from "{aff_uri}";
import {{ MONETIZATION_CONFIG }} from "{mon_uri}";

// 1. Verify $10/month USDT pricing config
assert.equal(MONETIZATION_CONFIG.vipPayments.vipMonthlyPriceUsd, "10");
assert.equal(MONETIZATION_CONFIG.vipPayments.defaultPayCurrency, "usdttrc20");
assert.equal(normalizePayCurrency("usdtton"), "usdtton");
assert.equal(normalizePayCurrency("usdtsol"), "usdtsol");
assert.equal(normalizePayCurrency("invalid_coin"), "usdttrc20");

// 2. Verify NOWPayments $10 USDT Checkout Session creation
const calls = [];
const mockCheckoutFetch = async (url, init) => {{
  calls.push({{ url: String(url), method: init?.method || "GET", body: init?.body ? JSON.parse(init.body) : null }});
  if (String(url).includes("api.nowpayments.io/v1/invoice")) {{
    return new Response(JSON.stringify({{ id: "inv_999", invoice_url: "https://nowpayments.io/payment/?iid=inv_999" }}), {{ status: 200 }});
  }}
  return new Response("", {{ status: 201 }});
}};

const ck = await createVipCheckoutSession(
  {{ lang: "ar", pay_currency: "usdttrc20", telegram_user_id: "55512345" }},
  {{
    NOWPAYMENTS_API_KEY: "np_live_test_key",
    SUPABASE_URL: "https://example.supabase.co",
    SUPABASE_KEY: "sb_secret_mock",
    PUBLIC_SITE_URL: "https://ratepath.io",
  }},
  mockCheckoutFetch
);
assert.equal(ck.status, 200);
assert.equal(ck.payload.ok, true);
assert.equal(ck.payload.amount_usd, 10);
assert.equal(ck.payload.pay_currency, "usdttrc20");
assert.equal(calls[0].body.price_amount, 10);
assert.equal(calls[0].body.pay_currency, "usdttrc20");
assert.equal(calls[0].body.is_fee_paid_by_user, true);

// 3. Verify NOWPayments HMAC-SHA512 IPN signature + single-use VIP invite link issuance
const ipnSecret = "super_ipn_secret_123";
const ipnBody = {{ order_id: ck.payload.order_id, payment_status: "finished", price_amount: 10, pay_currency: "usdttrc20", payment_id: "pay_77" }};
const canonical = JSON.stringify(sortObjectKeysDeep(ipnBody));
const validSig = crypto.createHmac("sha512", ipnSecret).update(canonical).digest("hex");
assert.equal(await verifyNowPaymentsSignature(ipnBody, validSig, ipnSecret), true);
assert.equal(await verifyNowPaymentsSignature(ipnBody, "0".repeat(128), ipnSecret), false);

const tgApiCalls = [];
const mockIpnFetch = async (url, init) => {{
  const u = String(url);
  if (u.includes("vip_subscriptions") && (!init?.method || init.method === "GET")) {{
    return new Response(JSON.stringify([{{ order_id: ipnBody.order_id, lang: "ar", telegram_user_id: "55512345", payment_status: "waiting" }}]), {{ status: 200 }});
  }}
  if (u.includes("createChatInviteLink")) {{
    tgApiCalls.push({{ method: "createChatInviteLink", body: JSON.parse(init.body) }});
    return new Response(JSON.stringify({{ ok: true, result: {{ invite_link: "https://t.me/+UniqueSingleUseVIP" }} }}), {{ status: 200 }});
  }}
  if (u.includes("sendMessage")) {{
    tgApiCalls.push({{ method: "sendMessage", body: JSON.parse(init.body) }});
    return new Response(JSON.stringify({{ ok: true }}), {{ status: 200 }});
  }}
  return new Response("", {{ status: 204 }});
}};

const ipnRes = await processNowPaymentsIpn(
  ipnBody,
  {{
    SUPABASE_URL: "https://example.supabase.co",
    SUPABASE_KEY: "sb_secret_mock",
    TELEGRAM_BOT_TOKEN: "123:BOT",
    TELEGRAM_VIP_CHANNEL_AR: "-100111222",
    TELEGRAM_VIP_CHANNEL_EN: "-100333444",
    ADMIN_CHAT_ID: "999888",
  }},
  mockIpnFetch
);
assert.equal(ipnRes.status, "fulfilled");
assert.equal(ipnRes.invite_issued, true);
assert.equal(tgApiCalls[0].method, "createChatInviteLink");
assert.equal(tgApiCalls[0].body.member_limit, 1);
assert.equal(tgApiCalls[0].body.chat_id, "-100111222");

// 4. Verify Cloudflare Edge /api/geo returns ISO-2 country
const geoResp = await geoGet({{ request: {{ cf: {{ country: "AE", continent: "AS" }} }} }});
const geoJson = await geoResp.json();
assert.equal(geoJson.country, "AE");

// 5. Verify Strict Geo-Compliant Affiliate Routing (Zero blocked platforms per country!)
const allDemoLinks = {{
  bybit: "https://partner.bybit.com/b/demo",
  okx: "https://www.okx.com/join/demo",
  bitget: "https://partner.bitget.com/bg/demo",
  binance: "https://accounts.binance.com/register?ref=demo",
  exness: "https://one.exness-track.com/a/demo",
  kraken: "https://kraken.pxf.io/demo",
  coinbase: "https://advanced.coinbase.com/join/demo",
  tradingview: "https://www.tradingview.com/?aff_id=demo",
  ledger: "https://shop.ledger.com/?r=demo",
  wise: "https://wise.com/invite/u/demo",
  remitly: "https://remit.ly/demo",
}};

// US visitor MUST NEVER see Bybit, Binance, OKX, Bitget, or Exness
const usCrypto = selectGeoCompliantPartners({{ countryCode: "US", category: "crypto", affiliatesConfig: allDemoLinks, affiliatesEnabled: true, maxCount: 5 }}).map(p => p.id);
assert.ok(usCrypto.includes("kraken") && usCrypto.includes("coinbase"));
for (const banned of ["bybit", "binance", "okx", "bitget", "exness"]) {{
  assert.ok(!usCrypto.includes(banned), `US must NOT see ${{banned}}`);
}}

// Syria (SY) visitor MUST NEVER see blocked exchanges or Wise/Remitly -> ONLY accessible tools like TradingView
const syCrypto = selectGeoCompliantPartners({{ countryCode: "SY", category: "crypto", affiliatesConfig: allDemoLinks, affiliatesEnabled: true, maxCount: 5 }}).map(p => p.id);
const syFiat = selectGeoCompliantPartners({{ countryCode: "SY", category: "fiat", affiliatesConfig: allDemoLinks, affiliatesEnabled: true, maxCount: 5 }}).map(p => p.id);
assert.deepEqual(syCrypto, ["tradingview"]);
assert.deepEqual(syFiat, ["tradingview"]);

// UAE / Saudi (AE / SA) MENA visitors get top USDT-paying Bybit, OKX, Exness (Gold)!
const aeGold = selectGeoCompliantPartners({{ countryCode: "AE", category: "gold", affiliatesConfig: allDemoLinks, affiliatesEnabled: true, maxCount: 3 }}).map(p => p.id);
assert.equal(aeGold[0], "exness");
assert.ok(aeGold.includes("bybit"));
console.log("ALL_MONETIZATION_AND_GEO_TESTS_OK");
"""
    proc = subprocess.run(
        [node_bin, "--input-type=module", "-e", js_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, f"Node ESM monetization tests failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    assert "ALL_MONETIZATION_AND_GEO_TESTS_OK" in proc.stdout
