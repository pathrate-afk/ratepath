"""Tests for CRO Funnel, Zero Empty States, Resilient Fallbacks, Clean Config, Quote Card & Bilingual SEO."""

from __future__ import annotations

import json
import subprocess

from backend.config import WORKSPACE_ROOT


def test_cro_resilience_config_seo_and_zero_dev_warnings() -> None:
    frontend = WORKSPACE_ROOT / "frontend"
    index_html = (frontend / "index.html").read_text(encoding="utf-8")
    mon_js = (frontend / "monetization.config.js").read_text(encoding="utf-8")
    app_js = (frontend / "js" / "app.js").read_text(encoding="utf-8")
    quant_js = (frontend / "js" / "quant_and_bot.js").read_text(encoding="utf-8")
    ws_js = (frontend / "js" / "workspace.js").read_text(encoding="utf-8")
    card_js = (frontend / "js" / "quote-card.js").read_text(encoding="utf-8")
    i18n_js = (frontend / "js" / "i18n.js").read_text(encoding="utf-8")
    headers_txt = (frontend / "_headers").read_text(encoding="utf-8")
    sitemap_xml = (frontend / "sitemap.xml").read_text(encoding="utf-8")
    robots_txt = (frontend / "robots.txt").read_text(encoding="utf-8")

    # 1. Zero developer warning text exposed to end users
    assert "requires a public Supabase anon key in monetization.config.js" not in index_html
    assert "(localStorage)" not in index_html
    assert "(localStorage)" not in i18n_js
    assert "Collecting samples" not in index_html
    assert "Collecting samples" not in quant_js
    assert "--:-- UTC" not in index_html

    # 2. Live UTC clock + instant fallback snapshot + USDT in default workspace
    assert "startLiveUtcClock" in app_js
    assert "bootstrapInitialSnapshot" in app_js
    assert "FALLBACK_FEED_SNAPSHOT" in app_js
    assert '"USDT"' in ws_js
    for required_asset in ("USD", "EUR", "TRY", "SYP", "EGP", "XAU_OZ", "XAG_OZ", "BTC", "ETH", "SOL", "USDT"):
        assert f'"{required_asset}"' in ws_js

    # 3. Centralized monetization.config.js with clean placeholders & feature flags
    assert "export const SUPABASE_URL" in mon_js
    assert "export const SUPABASE_ANON_KEY" in mon_js
    assert "export const FEATURE_FLAGS" in mon_js
    assert "nowPaymentsVipUrl" in mon_js
    assert "nowPaymentsPersonalBotUrl" in mon_js
    for partner in ("bybit", "okx", "bitget", "mexc", "tradingview"):
        assert f"{partner}:" in mon_js

    # 4. Free-to-USDT conversion upgrade modal ($10 & $19) + Quote Card traffic watermark
    assert 'id="modal-usdt-upgrade"' in index_html
    assert 'data-upgrade-plan="monthly_business_vip"' in index_html
    assert 'data-upgrade-plan="monthly_personal_bot"' in index_html
    assert "openUpgradeModal" in quant_js
    assert "data-pbot-instant-alert" in quant_js
    assert "ratepathlive.com" in card_js
    assert "@RatePath_AR" in card_js
    assert "@RatePathLive" in card_js

    # 5. CSP allows Cloudflare Insights beacon cleanly (zero console errors)
    assert "https://static.cloudflareinsights.com" in headers_txt
    assert "https://cloudflareinsights.com" in headers_txt

    # 6. Bilingual SEO tags + sitemap + robots
    assert "Live Currency Converter" in index_html
    assert "USD to SYP" in index_html
    assert 'property="og:url"' in index_html
    assert 'name="twitter:title"' in index_html
    assert "https://ratepathlive.com/en" in sitemap_xml
    assert "https://ratepathlive.com/ar" in sitemap_xml
    assert "Allow: /en" in robots_txt


def test_default_workspace_and_fallback_snapshot_in_nodejs() -> None:
    """Verify Node ESM catalog builds all 18 default assets including USDT & Silver even without network."""
    proc = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            """
            import { buildAssetCatalog, convertAllAssets } from './frontend/js/decimal-math.js';
            import { DEFAULT_WORKSPACE_IDS, normalizeWorkspace } from './frontend/js/workspace.js';
            import { FALLBACK_FEED_SNAPSHOT, FALLBACK_CATALOG_SNAPSHOT } from './frontend/js/fallback-snapshot.js';

            const cat = buildAssetCatalog(FALLBACK_FEED_SNAPSHOT, FALLBACK_CATALOG_SNAPSHOT);
            const missing = DEFAULT_WORKSPACE_IDS.filter((id) => !cat.byId[id]);
            const conv = convertAllAssets('100', 'USD', cat, [...DEFAULT_WORKSPACE_IDS]);
            const migrated = normalizeWorkspace({ version: 2, ordered_ids: ['USD', 'EUR', 'SOL'] });
            console.log(JSON.stringify({
              totalDefaults: DEFAULT_WORKSPACE_IDS.length,
              missing,
              usdtFormatted: conv.conversions.USDT?.formatted,
              xagFormatted: conv.conversions.XAG_OZ?.formatted,
              sypFormatted: conv.conversions.SYP?.formatted,
              migratedHasUsdt: migrated.ordered_ids.includes('USDT'),
            }));
            """,
        ],
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    out = json.loads(proc.stdout.strip())
    assert out["totalDefaults"] == 18
    assert out["missing"] == []
    assert out["usdtFormatted"] == "100.00"
    assert float(out["xagFormatted"].replace(",", "")) > 0
    assert float(out["sypFormatted"].replace(",", "")) > 0
    assert out["migratedHasUsdt"] is True
