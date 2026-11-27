"""Tests for automatic Mobile / Tablet / Desktop / Wide responsive layout & device detection."""

from __future__ import annotations

from backend.config import WORKSPACE_ROOT


def test_automatic_device_profile_and_responsive_breakpoints() -> None:
    frontend = WORKSPACE_ROOT / "frontend"
    index_html = (frontend / "index.html").read_text(encoding="utf-8")
    terminal_css = (frontend / "styles" / "terminal.css").read_text(encoding="utf-8")
    app_js = (frontend / "js" / "app.js").read_text(encoding="utf-8")
    quant_js = (frontend / "js" / "quant_and_bot.js").read_text(encoding="utf-8")
    sw_js = (frontend / "sw.js").read_text(encoding="utf-8")

    # 1. Automatic viewport & orientation detector in app.js
    assert "function detectAndApplyDeviceProfile()" in app_js
    assert '"mobile"' in app_js and '"tablet"' in app_js and '"desktop"' in app_js and '"wide"' in app_js
    assert "root.dataset.device = device" in app_js
    assert "root.dataset.orientation = orientation" in app_js
    assert '"orientationchange"' in app_js
    assert "visualViewport" in app_js
    # Tablet (>= 768px) displays full adaptive layout instead of phone tab mode
    assert 'window.matchMedia("(min-width: 768px)").matches' in app_js
    assert 'window.matchMedia("(min-width: 1024px)")' not in app_js

    # 2. Mobile bottom nav restricted strictly to phones (< 768px -> md:hidden)
    assert 'id="mobile-bottom-nav"' in index_html
    assert "md:hidden rp-mobile-nav" in index_html

    # 3. Fluid containers up to wide monitors + Tablet/Desktop grid adaptation
    assert "max-w-[1600px]" in index_html
    assert "md:grid-cols-3 lg:grid-cols-3 xl:grid-cols-4" in index_html
    assert "md:grid-cols-2 lg:grid-cols-1" in index_html
    assert "md:grid-cols-3 lg:grid-cols-2" in quant_js
    assert "max-w-[80vw]" in quant_js

    # 4. Adaptive modals (mobile bottom sheet + dvh safe heights + overflow-y-auto)
    for modal_id in (
        "modal-quote-card",
        "modal-vip-alerts",
        "modal-asset-picker",
        "modal-auth-sync",
        "modal-asset-detail",
    ):
        assert modal_id in index_html
    assert index_html.count("rp-modal-sheet") >= 5
    assert "items-end sm:items-center" in index_html

    # 5. CSS overflow prevention, dvh support, safe-area inset & iOS non-zoom input sizing
    assert "overflow-x: clip;" in terminal_css
    assert "max-height: 92dvh;" in terminal_css
    assert "env(safe-area-inset-bottom" in terminal_css
    assert "min-height: 44px;" in terminal_css
    assert "scroll-margin-top:" in terminal_css

    # 6. Zero CSP violations (no inline onclick/onerror) and fresh PWA cache version
    assert "onclick=" not in index_html
    assert "onerror=" not in index_html
    assert "ratepath-pwa-v15" in sw_js
