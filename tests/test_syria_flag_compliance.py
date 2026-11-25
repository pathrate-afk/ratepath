"""Compliance tests enforcing the Syrian Revolution Flag (Green-White-Black + 3 Red Stars)
and banning raw Unicode Syria flag emoji / external flag CDNs across the entire platform.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.agents.contracts import AgentReport, StrategistBrief
from backend.agents.orchestrator import audit_strategist_brief, detect_hierarchy_and_retail_trap
from backend.ai_strategist import ArabicTextBlock, EnglishTextBlock, PricedInWarningModel
from backend.config import (
    NO_RAW_SYRIA_EMOJI_PROMPT_RULE,
    RAW_SYRIA_FLAG_EMOJI,
    SYRIA_SAFE_TICKER_AR,
    SYRIA_SAFE_TICKER_EN,
    WORKSPACE_ROOT,
    sanitize_syria_flag_emoji,
)
from backend.math_engine import build_fiat_rates
from backend.telegram_bot import (
    build_admin_watchdog_payload,
    build_public_channel_payload,
    build_vip_channel_payload,
)

SCANNED_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".js", ".html", ".css", ".json", ".md", ".yml", ".yaml", ".sql"}
)
SKIPPED_DIRS: frozenset[str] = frozenset(
    {".venv", ".git", "__pycache__", ".pytest_cache", "node_modules"}
)


def _iter_repo_text_files() -> list[Path]:
    files: list[Path] = []
    for path in WORKSPACE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIPPED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SCANNED_EXTENSIONS:
            files.append(path)
    return files


def test_zero_raw_syria_emoji_or_external_flag_cdns_across_entire_repo() -> None:
    """Guarantee zero literal raw U+1F1F8 U+1F1FE emoji, zero FlagCDN/Twemoji, and zero old-flag SVG paths."""
    raw_emoji_hits: list[str] = []
    cdn_hits: list[str] = []
    old_flag_svg_hits: list[str] = []

    for file_path in _iter_repo_text_files():
        rel = file_path.relative_to(WORKSPACE_ROOT).as_posix()
        text = file_path.read_text(encoding="utf-8", errors="ignore")

        if RAW_SYRIA_FLAG_EMOJI in text:
            raw_emoji_hits.append(rel)

        # Exclude this test file's own string constants from CDN substring check
        if rel != "tests/test_syria_flag_compliance.py":
            low = text.lower()
            if "flagcdn" in low or "twemoji" in low:
                cdn_hits.append(rel)
            # Detect old-flag SVG geometry (red top band y="0" + green stars)
            if (
                re.search(r'<rect[^>]*y="0"[^>]*fill="#CE1126"', text, re.IGNORECASE)
                and re.search(r'<polygon[^>]*fill="#007A3D"', text, re.IGNORECASE)
            ):
                old_flag_svg_hits.append(rel)

    assert raw_emoji_hits == [], f"Literal raw Syria flag emoji found in: {raw_emoji_hits}"
    assert cdn_hits == [], f"Forbidden external flag CDN references found in: {cdn_hits}"
    assert old_flag_svg_hits == [], f"Old-flag red-top/green-star SVG found in: {old_flag_svg_hits}"


def test_syria_revolution_svg_exact_geometry_and_colors() -> None:
    """Verify hardcoded inline SVG uses Green (#007A3D) top, White (#FFFFFF) middle with 3 Red (#CE1126) stars, Black (#000000) bottom."""
    svg_js = (WORKSPACE_ROOT / "frontend" / "js" / "syria-flag.js").read_text(encoding="utf-8")

    # Top band Green (#007A3D), Middle band White (#FFFFFF), Bottom band Black (#000000)
    assert '<rect x="0" y="0" width="900" height="200" fill="#007A3D"/>' in svg_js
    assert '<rect x="0" y="200" width="900" height="200" fill="#FFFFFF"/>' in svg_js
    assert '<rect x="0" y="400" width="900" height="200" fill="#000000"/>' in svg_js

    # Exactly 3 five-pointed Red (#CE1126) stars horizontally centered at x=225, 450, 675
    red_stars = re.findall(r'<polygon fill="#CE1126" points="([^"]+)"\s*/>', svg_js)
    assert len(red_stars) == 3, f"Expected exactly 3 red star polygons, got {len(red_stars)}"
    for star_points, expected_cx in zip(red_stars, ("225,236", "450,236", "675,236"), strict=True):
        assert star_points.startswith(expected_cx)
        # 5-pointed star has 10 vertex coordinate pairs
        assert len(star_points.split()) == 10

    # Canvas renderer uses exact same constants and 3 stars
    assert "SYRIA_FLAG_COLORS.TOP_GREEN" in svg_js
    assert "SYRIA_FLAG_COLORS.MIDDLE_WHITE" in svg_js
    assert "SYRIA_FLAG_COLORS.BOTTOM_BLACK" in svg_js
    assert "SYRIA_FLAG_COLORS.STARS_RED" in svg_js
    assert "[0.25, 0.5, 0.75]" in svg_js


def test_frontend_wires_custom_syria_svg_in_cards_dropdown_ticker_and_canvas() -> None:
    """Confirm currency card badge, converter dropdown, top ticker, Canvas quote card, i18n & SW wire SYP cleanly."""
    app_js = (WORKSPACE_ROOT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    quote_js = (WORKSPACE_ROOT / "frontend" / "js" / "quote-card.js").read_text(encoding="utf-8")
    index_html = (WORKSPACE_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    i18n_js = (WORKSPACE_ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")
    sw_js = (WORKSPACE_ROOT / "frontend" / "sw.js").read_text(encoding="utf-8")

    assert 'import { getSyriaRevolutionFlagSvg } from "./syria-flag.js";' in app_js
    assert "renderConverterBaseFlag" in app_js
    assert 'id="converter-base-flag"' in index_html
    assert 'id="alert-asset-flag"' in index_html
    assert "SYP" in index_html
    assert 'import { drawSyriaRevolutionFlagOnCanvas } from "./syria-flag.js";' in quote_js
    assert "drawSyriaRevolutionFlagOnCanvas(ctx," in quote_js
    assert 'SYP: "Syrian Pound"' in i18n_js
    assert 'SYP: "الليرة السورية"' in i18n_js
    assert '"./js/syria-flag.js"' in sw_js


def test_telegram_sanitizer_and_syp_tickers_strip_raw_emoji() -> None:
    """Verify raw U+1F1F8 U+1F1FE is stripped into `🟢 SYP` (EN) / `🟢 ل.س` (AR) across all Telegram builders."""
    assert sanitize_syria_flag_emoji(f"Syria {RAW_SYRIA_FLAG_EMOJI} FX", lang="en") == (
        f"Syria {SYRIA_SAFE_TICKER_EN} FX"
    )
    assert sanitize_syria_flag_emoji(f"سوريا {RAW_SYRIA_FLAG_EMOJI} صرف", lang="ar") == (
        f"سوريا {SYRIA_SAFE_TICKER_AR} صرف"
    )

    sample_news = {
        "id": "syp-test-1",
        "published_at": "2026-11-25T18:00:00Z",
        "source_name": "CNBC",
        "source_url": "https://www.cnbc.com/2026/11/25/regional-fx-gold.html",
        "en": {
            "title": f"Regional FX {RAW_SYRIA_FLAG_EMOJI} and Gold liquidity shifts",
            "summary": "Safe-haven demand strengthens gold and regional currencies.",
            "macro_thesis": "Hard assets attract regional capital inflows.",
        },
        "ar": {
            "title": f"تحركات أسعار الصرف {RAW_SYRIA_FLAG_EMOJI} والذهب في الأسواق",
            "summary": "زيادة الطلب على الذهب والسيولة النقدية في المنطقة.",
            "macro_thesis": "تدفقات تحوطية نحو الذهب والعملات.",
        },
        "priced_in_warning": {
            "active": False,
            "severity": "low",
            "en": f"Fresh regional move {RAW_SYRIA_FLAG_EMOJI} watch spreads.",
            "ar": f"تحرك جديد {RAW_SYRIA_FLAG_EMOJI} راقب الفروق السعرية.",
        },
        "assets_bias_24h": {
            "USD": "neutral",
            "EUR": "neutral",
            "GBP": "neutral",
            "XAU": "bullish",
            "BTC": "neutral",
            "ETH": "neutral",
            "SOL": "neutral",
        },
        "confidence_score": 82,
        "auditor_verified": True,
    }
    sample_feed = {
        "fiat": {"rates": {"SYP": {"units_per_usd": "11000.0000", "display_rate": "11000.0000"}}},
        "gold": {"prices": {"XAU_OZ": "3568.81"}},
        "crypto": {"assets": {"BTC": {"spot_price": "104500.00"}}},
    }

    en_pub = build_public_channel_payload(sample_news, sample_feed, chat_id="@RatePathEN", lang="en")
    ar_pub = build_public_channel_payload(sample_news, sample_feed, chat_id="@RatePathAR", lang="ar")
    en_vip = build_vip_channel_payload(sample_news, sample_feed, chat_id="@RatePathVIP_EN", lang="en")
    ar_vip = build_vip_channel_payload(sample_news, sample_feed, chat_id="@RatePathVIP_AR", lang="ar")
    adm = build_admin_watchdog_payload(f"Check {RAW_SYRIA_FLAG_EMOJI}", "ok", admin_chat_id="123")

    for payload in (en_pub, ar_pub, en_vip, ar_vip, adm):
        assert RAW_SYRIA_FLAG_EMOJI not in payload["text"]

    # News alerts sanitize raw Syria emoji -> safe EN/AR ticker AND carry ZERO inline price clutter
    assert SYRIA_SAFE_TICKER_EN in en_pub["text"]
    assert "11000.0000" not in en_pub["text"]
    assert not re.search(r"[\u0600-\u06FF]", en_pub["text"])

    assert SYRIA_SAFE_TICKER_AR in ar_pub["text"]
    assert "11000.0000" not in ar_pub["text"]


def test_llm_prompts_and_pydantic_models_enforce_no_raw_syria_emoji() -> None:
    """Confirm LLM system prompts forbid U+1F1F8 U+1F1FE and Pydantic validators auto-sanitize."""
    ai_src = (WORKSPACE_ROOT / "backend" / "ai_strategist.py").read_text(encoding="utf-8")
    ms_src = (WORKSPACE_ROOT / "backend" / "agents" / "master_strategist.py").read_text(
        encoding="utf-8"
    )
    assert "NO_RAW_SYRIA_EMOJI_PROMPT_RULE" in ai_src
    assert "NO_RAW_SYRIA_EMOJI_PROMPT_RULE" in ms_src
    assert "🟢 SYP" in NO_RAW_SYRIA_EMOJI_PROMPT_RULE
    assert "🟢 ل.س" in NO_RAW_SYRIA_EMOJI_PROMPT_RULE

    en_blk = EnglishTextBlock(
        title=f"Syria {RAW_SYRIA_FLAG_EMOJI} FX update",
        summary="Gold and FX liquidity update across Middle East desks.",
        macro_thesis="Safe-haven demand holds steady across key corridors.",
    )
    ar_blk = ArabicTextBlock(
        title=f"تحديث الليرة {RAW_SYRIA_FLAG_EMOJI} والذهب",
        summary="متابعة حركة السيولة وأسعار الصرف والذهب في الأسواق.",
        macro_thesis="استقرار السيولة مع ترقّب المحفزات الكلية القادمة.",
    )
    pw = PricedInWarningModel(
        active=False,
        severity="low",
        en=f"Fresh {RAW_SYRIA_FLAG_EMOJI} FX signal.",
        ar=f"إشارة صرف جديدة {RAW_SYRIA_FLAG_EMOJI} في السوق.",
    )
    assert RAW_SYRIA_FLAG_EMOJI not in en_blk.title and SYRIA_SAFE_TICKER_EN in en_blk.title
    assert RAW_SYRIA_FLAG_EMOJI not in ar_blk.title and SYRIA_SAFE_TICKER_AR in ar_blk.title
    assert RAW_SYRIA_FLAG_EMOJI not in pw.en and RAW_SYRIA_FLAG_EMOJI not in pw.ar

    # Orchestrator auditor also sanitizes StrategistBrief
    brief = StrategistBrief(
        event_en=f"Syria {RAW_SYRIA_FLAG_EMOJI} FX shift",
        event_ar=f"تحرك {RAW_SYRIA_FLAG_EMOJI} في الصرف",
        money_flow_en="Capital rotating into Gold and USD.",
        money_flow_ar="تحوّل السيولة نحو الذهب والدولار.",
        verdict_en="Monitor liquidity closely before scaling.",
        verdict_ar="راقب السيولة بدقة قبل اتخاذ قرار.",
        matched_twin_key="hist-2008-gfc-lehman-liquidity",
        stage="consolidation",
        priced_in_active=False,
        assets_bias_24h={
            "USD": "bullish",
            "EUR": "neutral",
            "GBP": "neutral",
            "XAU": "bullish",
            "BTC": "neutral",
            "ETH": "neutral",
            "SOL": "neutral",
        },
        raw_conviction=82,
    )
    hard = AgentReport(
        agent_id="agent_4_macro_life_filter",
        tier="hard_truth",
        healthy=True,
        signal_direction="risk_off",
        confidence=84,
        headline="Macro liquidity shift verified.",
    )
    hierarchy = detect_hierarchy_and_retail_trap([hard])
    ok, _score, cleaned_brief, err = audit_strategist_brief(brief, hierarchy)
    assert ok and err is None
    assert RAW_SYRIA_FLAG_EMOJI not in cleaned_brief.event_en
    assert SYRIA_SAFE_TICKER_EN in cleaned_brief.event_en
    assert RAW_SYRIA_FLAG_EMOJI not in cleaned_brief.event_ar
    assert SYRIA_SAFE_TICKER_AR in cleaned_brief.event_ar


def test_math_engine_builds_syp_with_positive_inverse_rate() -> None:
    """Verify high-denomination SYP rate preserves strictly positive Decimal `usd_per_unit`."""
    fiat_table = build_fiat_rates(
        {
            "EUR": "0.8615",
            "GBP": "0.7420",
            "SAR": "3.7500",
            "AED": "3.6725",
            "EGP": "48.5000",
            "TRY": "36.2500",
            "SYP": "13002.5000",
        }
    )
    assert "SYP" in fiat_table
    assert fiat_table["SYP"]["pair_label"] == "USD/SYP"
    assert fiat_table["SYP"]["units_per_usd"] == "13002.5000"
    assert fiat_table["SYP"]["usd_per_unit"] == "0.00007691"
