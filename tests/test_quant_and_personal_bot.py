"""Unit tests for Agent 8 Quant/Macro signals, Personal AI Tracker Bot, Dual USDT Plans,
Geo-Filtered Trading Bot Affiliates, and Legal/Ethical Informational Disclaimers."""

from __future__ import annotations

import json
import subprocess
from decimal import Decimal
from pathlib import Path

from backend.agents.quant_macro_signals import (
    compute_ema_decimal,
    compute_rsi_decimal,
    score_asset_quant_signal,
)
from backend.personal_bot_worker import (
    check_alert_trigger,
    resolve_price_from_feeds,
)

WORKSPACE = Path(__file__).resolve().parent.parent


def _run_node(script: str) -> dict:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(proc.stdout.strip())


def test_quant_decimal_ema_rsi_and_selective_abstention() -> None:
    """EMA, RSI & Quant Ensemble use pure Decimal (zero floats) and abstain in noisy ranges."""
    uptrend = [Decimal("100") + Decimal(i) * Decimal("1.5") for i in range(60)]
    ema20 = compute_ema_decimal(uptrend, 20)
    rsi14 = compute_rsi_decimal(uptrend, 14)
    assert isinstance(ema20, Decimal)
    assert isinstance(rsi14, Decimal)
    assert rsi14 > Decimal("70")

    # Strong Gold uptrend + falling DXY + falling US10Y -> bullish lean
    mild_up = [Decimal("2600") + Decimal(i) * Decimal("3") for i in range(55)]
    # Add mild pullback on last 2 bars so RSI sits in healthy 55-69 sweet spot
    mild_up[-2] -= Decimal("25")
    mild_up[-1] -= Decimal("10")
    xau_sig = score_asset_quant_signal(
        asset="XAU",
        closes=mild_up,
        dxy_change_pct=Decimal("-0.55"),
        us10y_change_pct=Decimal("-1.40"),
    )
    assert xau_sig["bias"] == "bullish"
    assert xau_sig["score"] >= 35

    # Flat/choppy closes -> selective abstention (`neutral`)
    choppy = [Decimal("100") + (Decimal("0.2") if i % 2 == 0 else Decimal("-0.2")) for i in range(55)]
    flat_sig = score_asset_quant_signal(asset="EUR", closes=choppy, dxy_change_pct=Decimal("0.05"))
    assert flat_sig["bias"] == "neutral"
    assert -35 < flat_sig["score"] < 35

    # Overheated BTC funding rate penalizes crowded longs
    btc_flat = [Decimal("90000") for _ in range(55)]
    btc_overheated = score_asset_quant_signal(
        asset="BTC",
        closes=btc_flat,
        funding_rate_pct=Decimal("0.065"),
    )
    assert btc_overheated["score"] <= -25


def test_personal_bot_worker_decimal_triggers() -> None:
    """Personal tracker resolves prices and evaluates above/below thresholds in pure Decimal."""
    live_feed = {
        "gold": {"prices": {"XAU_OZ": "3120.50", "GRAM_21K": "87.75"}},
        "fiat": {"rates": {"EUR": {"units_per_usd": "0.9210"}}},
        "crypto": {"assets": {"BTC": {"spot_price": "95400.00"}}},
    }
    catalog_feed = {
        "metals": {"XAG_OZ": {"price_usd": "34.15"}},
        "fiat": {"RUB": {"units_per_usd": "82.33"}},
        "crypto": [{"symbol": "PEPE", "price_usd": "0.00000351"}],
    }
    assert resolve_price_from_feeds("XAU", "metal", live_feed, catalog_feed) == Decimal("3120.50")
    assert resolve_price_from_feeds("XAG_OZ", "metal", live_feed, catalog_feed) == Decimal("34.15")
    assert resolve_price_from_feeds("RUB", "fiat", live_feed, catalog_feed) == Decimal("82.33")
    assert resolve_price_from_feeds("PEPE", "crypto", live_feed, catalog_feed) == Decimal("0.00000351")

    assert check_alert_trigger(Decimal("3120.50"), "3100", "2900") == "above"
    assert check_alert_trigger(Decimal("2890.00"), "3100", "2900") == "below"
    assert check_alert_trigger(Decimal("3000.00"), "3100", "2900") is None


def test_edge_dual_tier_checkout_and_personal_bot_sanitizer() -> None:
    """Verify $10 VIP vs $19 Personal Bot Pro checkout & watchlist sanitizer on Edge functions."""
    res = _run_node(
        """
        import { createVipCheckoutSession, normalizePlanTier } from './functions/api/create-vip-checkout.js';
        import { sanitizeWatchlist } from './functions/api/personal-bot.js';
        import { scoreAssetQuant } from './functions/api/strategist-signals.js';

        let capturedBody = null;
        const mockFetch = async (url, init) => {
          if (String(url).includes('/v1/invoice')) {
            capturedBody = JSON.parse(init.body);
            return new Response(JSON.stringify({ id: 'inv_19', invoice_url: 'https://nowpayments.io/payment/?iid=19' }), { status: 200 });
          }
          return new Response('{}', { status: 200 });
        };

        const out = await createVipCheckoutSession(
          { plan_tier: 'monthly_personal_bot', lang: 'ar', pay_currency: 'usdttrc20', email: 'test@example.com' },
          { NOWPAYMENTS_API_KEY: 'live_key_123', PUBLIC_SITE_URL: 'https://ratepathlive.com' },
          mockFetch
        );

        const cleanWl = sanitizeWatchlist([
          { symbol: 'AAPL', category: 'stock', name_en: 'Apple', alert_above: '250' },
          { symbol: 'aapl', category: 'stock', name_en: 'Duplicate Apple' },
          { symbol: 'BTC', category: 'crypto', alert_below: '80000' },
          { symbol: 'RUB', category: 'fiat' },
          { symbol: 'EXTRA', category: 'crypto' },
        ], 3);

        const flatCloses = Array.from({ length: 40 }, () => 100);
        const flatSig = scoreAssetQuant({ asset: 'XAU', closes: flatCloses, dxyChgPct: 0.0 });

        console.log(JSON.stringify({
          tierNorm: normalizePlanTier('personal_bot'),
          status: out.status,
          amountUsd: out.payload.amount_usd,
          orderPrefix: out.payload.order_id.split('-')[0],
          sentAmount: capturedBody?.price_amount,
          wlCount: cleanWl.length,
          wlSyms: cleanWl.map(x => x.symbol),
          flatBias: flatSig.bias,
        }));
        """
    )
    assert res["tierNorm"] == "monthly_personal_bot"
    assert res["status"] == 200
    assert res["amountUsd"] == 19
    assert res["sentAmount"] == 19
    assert res["orderPrefix"] == "RPBOT"
    assert res["wlCount"] == 3
    assert res["wlSyms"] == ["AAPL", "BTC", "RUB"]
    assert res["flatBias"] == "neutral"


def test_geo_compliant_trading_bots_affiliates() -> None:
    """Trading Bots affiliate shelf obeys strict country geo-filtering (blocks US/SY for Bybit/Bitget, keeps TradingView)."""
    res = _run_node(
        """
        import { MONETIZATION_CONFIG, AFFILIATES_ENABLED } from './frontend/monetization.config.js';
        import { selectGeoCompliantPartners } from './frontend/js/affiliates.js';

        const aeBots = selectGeoCompliantPartners({
          countryCode: 'AE',
          category: 'bots',
          affiliatesConfig: MONETIZATION_CONFIG.affiliates,
          affiliatesEnabled: AFFILIATES_ENABLED,
          maxCount: 4,
        }).map(p => p.id);

        const usBots = selectGeoCompliantPartners({
          countryCode: 'US',
          category: 'bots',
          affiliatesConfig: MONETIZATION_CONFIG.affiliates,
          affiliatesEnabled: AFFILIATES_ENABLED,
          maxCount: 4,
        }).map(p => p.id);

        const syBots = selectGeoCompliantPartners({
          countryCode: 'SY',
          category: 'bots',
          affiliatesConfig: MONETIZATION_CONFIG.affiliates,
          affiliatesEnabled: AFFILIATES_ENABLED,
          maxCount: 4,
        }).map(p => p.id);

        console.log(JSON.stringify({ aeBots, usBots, syBots }));
        """
    )
    assert "bybit" in res["aeBots"]
    assert "bitget" in res["aeBots"]
    assert "tradingview" in res["aeBots"]
    # US & SY visitors NEVER see blocked exchanges (Bybit/Bitget/Exness), only globally permitted TradingView
    assert "bybit" not in res["usBots"]
    assert "bitget" not in res["usBots"]
    assert "exness" not in res["usBots"]
    assert res["usBots"] == ["tradingview"]
    assert res["syBots"] == ["tradingview"]


def test_telegram_webhook_personal_bot_linking_and_stocks() -> None:
    """Telegram webhook parses stocks (AAPL/أبل/أرامكو) and links /start link_<token>."""
    res = _run_node(
        """
        import { parseUserAssetQuery, processTelegramUpdate } from './functions/api/telegram-webhook.js';

        const q1 = parseUserAssetQuery('سهم أبل', 'ar');
        const q2 = parseUserAssetQuery('/ask 2222.SR', 'en');

        let patchedBody = null;
        let sentMsg = '';
        const mockFetch = async (url, init) => {
          const u = String(url);
          if (u.includes('user_tracker_profiles?link_token=eq.')) {
            return new Response(JSON.stringify([{ user_id: 'u-1', email: 'a@b.com', lang: 'ar', plan_tier: 'free', watchlist: [] }]), { status: 200 });
          }
          if (u.includes('user_tracker_profiles?user_id=eq.u-1') && init?.method === 'PATCH') {
            patchedBody = JSON.parse(init.body);
            return new Response(null, { status: 204 });
          }
          if (u.includes('/sendMessage')) {
            sentMsg = JSON.parse(init.body).text;
            return new Response(JSON.stringify({ ok: true }), { status: 200 });
          }
          return new Response(JSON.stringify({ ok: true }), { status: 200 });
        };

        const pairOut = await processTelegramUpdate(
          { message: { chat: { id: 777123, type: 'private' }, from: { id: 777123, language_code: 'ar' }, text: '/start link_abcdef123456' } },
          { SUPABASE_URL: 'https://demo.supabase.co', SUPABASE_KEY: 'live_key', TELEGRAM_BOT_TOKEN: 'bot_tok' },
          mockFetch
        );

        console.log(JSON.stringify({
          q1Sym: q1?.symbol,
          q1Kind: q1?.kind,
          q2Sym: q2?.symbol,
          pairMode: pairOut.mode,
          linked: pairOut.linked,
          patchedChat: patchedBody?.telegram_chat_id,
          hasInformationalNotice: sentMsg.includes('معلوماتية'),
        }));
        """
    )
    assert res["q1Sym"] == "AAPL"
    assert res["q1Kind"] == "stock"
    assert res["q2Sym"] == "2222.SR"
    assert res["pairMode"] == "personal_bot_linked"
    assert res["linked"] is True
    assert res["patchedChat"] == "777123"
    assert res["hasInformationalNotice"] is True


def test_legal_and_ethical_compliance_copy() -> None:
    """Ensure explicit informational-only / non-guarantee notices & affiliate disclosure are present everywhere."""
    index_html = (WORKSPACE / "frontend" / "index.html").read_text(encoding="utf-8")
    i18n_js = (WORKSPACE / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")
    assert "100-Year Historical Twins" not in index_html
    assert "100-Year Historical Twins" not in i18n_js
    assert "التوائم التاريخية (100 عام)" not in i18n_js
    assert "لا نعلم الغيب" in i18n_js or "لا أحد يعلم الغيب" in i18n_js
    assert "Affiliate Disclosure" in index_html
    assert "إفصاح الشراكة" in i18n_js
    assert "onerror=" not in (WORKSPACE / "frontend" / "js" / "quant_and_bot.js").read_text(encoding="utf-8")
