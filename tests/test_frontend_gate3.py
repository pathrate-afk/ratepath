"""Gate 3 Frontend Contract, SEO, PWA, and Bilingual UTF-8 Verification Tests."""

from __future__ import annotations

import json

from backend.config import WORKSPACE_ROOT


def test_frontend_gate3_pwa_seo_and_bilingual_integrity() -> None:
    """Verify Gate 3 HTML5, hreflang, JSON-LD, PWA manifest/SW, and Arabic UTF-8 assets."""
    frontend_dir = WORKSPACE_ROOT / "frontend"
    index_html = (frontend_dir / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((frontend_dir / "manifest.json").read_text(encoding="utf-8"))
    sw_js = (frontend_dir / "sw.js").read_text(encoding="utf-8")
    i18n_js = (frontend_dir / "js" / "i18n.js").read_text(encoding="utf-8")
    math_js = (frontend_dir / "js" / "decimal-math.js").read_text(encoding="utf-8")
    card_js = (frontend_dir / "js" / "quote-card.js").read_text(encoding="utf-8")

    # 1. Bilingual SEO hreflang + JSON-LD FinancialService + Telegram Mini App
    assert 'hreflang="en"' in index_html
    assert 'hreflang="ar"' in index_html
    assert 'type="application/ld+json"' in index_html
    assert "telegram-web-app.js" in index_html
    assert "ريت باث" in index_html

    # 2. PWA Manifest & Offline Service Worker caching live_feed.json
    assert manifest["short_name"] == "RatePath"
    assert "ريت باث" in manifest["name"]
    assert "./public/live_feed.json" in sw_js

    # 3. Single-Input Cross-Asset BigInt Math & All 14 Required Assets
    assert "BigInt" in math_js
    for asset_key in (
        "USD",
        "EUR",
        "GBP",
        "TRY",
        "SAR",
        "AED",
        "EGP",
        "XAU_OZ",
        "GRAM_24K",
        "GRAM_21K",
        "GRAM_18K",
        "BTC",
        "ETH",
        "SOL",
    ):
        assert asset_key in i18n_js
        assert asset_key in math_js

    # 4. Monetization & Viral Sharing (Wise/Remitly, Bybit/Binance, A-Ads, Canvas PNG, VIP Modal)
    assert "Wise" in i18n_js and "Remitly" in i18n_js
    assert "Bybit" in i18n_js and "Binance" in i18n_js
    assert "aads-banner-slot" in index_html
    assert "quote-card-canvas" in index_html
    assert "RATEPATH • ريت باث" in card_js
    assert "modal-vip-alerts" in index_html

    # 5. Official Brand Logo & Cross-Platform Icon Suite (Browser, iPhone, iPad, Android, App Store)
    required_icons = (
        frontend_dir / "favicon.ico",
        frontend_dir / "icons" / "favicon-16x16.png",
        frontend_dir / "icons" / "favicon-32x32.png",
        frontend_dir / "icons" / "favicon-48x48.png",
        frontend_dir / "icons" / "apple-touch-icon.png",
        frontend_dir / "icons" / "apple-touch-icon-152x152.png",
        frontend_dir / "icons" / "apple-touch-icon-167x167.png",
        frontend_dir / "icons" / "icon-192x192.png",
        frontend_dir / "icons" / "icon-512x512.png",
        frontend_dir / "icons" / "icon-maskable-512x512.png",
        frontend_dir / "icons" / "app-store-1024x1024.png",
        frontend_dir / "icons" / "logo-mark.png",
        frontend_dir / "icons" / "logo-full.jpg",
    )
    for icon_path in required_icons:
        assert icon_path.exists() and icon_path.stat().st_size > 0, f"Missing icon: {icon_path}"
    assert "./icons/logo-mark.png" in index_html
    assert "./favicon.ico" in index_html
    assert "./icons/apple-touch-icon.png" in index_html
    assert any(i.get("purpose") == "maskable" for i in manifest.get("icons", []))

    # 6. Pre-Launch Security Hardening (CSP, zero inline onerror=, /server.py blocked, /api/ SW bypass, 404)
    headers_txt = (frontend_dir / "_headers").read_text(encoding="utf-8")
    redirects_txt = (frontend_dir / "_redirects").read_text(encoding="utf-8")
    robots_txt = (frontend_dir / "robots.txt").read_text(encoding="utf-8")
    app_js = (frontend_dir / "js" / "app.js").read_text(encoding="utf-8")

    assert "script-src 'self' https://telegram.org" in headers_txt
    assert "object-src 'none'" in headers_txt
    assert "base-uri 'self'" in headers_txt
    assert "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org" in headers_txt
    middleware_txt = (WORKSPACE_ROOT / "functions" / "_middleware.js").read_text(encoding="utf-8")
    assert "status: 404" in middleware_txt and ".py" in middleware_txt
    assert "/catalog_feed.json" in redirects_txt
    assert "Disallow: /api/" in robots_txt
    assert "Disallow: /server.py" in robots_txt
    assert (frontend_dir / "404.html").exists()
    assert 'url.pathname.startsWith("/api/")' in sw_js
    assert "onerror=" not in app_js
    assert "ALLOWED_LOGO_HOSTS" in app_js
