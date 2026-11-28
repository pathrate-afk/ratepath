"""Hands-Off Multi-Channel Market Pulse Auto-Publisher for RatePath (ريت باث).

Scheduled 3x daily (07:00, 14:00, 20:00 UTC) via `.github/workflows/auto-publish.yml`:
1. Pulls the latest live market snapshot (Gold XAU/24K/21K, BTC, USD/TRY, USD/SYP,
   USD/EGP, and the Top 24h Crypto Mover from live-data / local feeds).
2. Renders a crisp 1200x630 RatePath Quote Card PNG using Pillow with
   `ratepathlive.com` and `@RatePathTerminalBot` stamped clearly at the bottom.
3. Reuses the project's existing multi-provider AI key rotator
   (`GEMINI_API_KEY_1..3` -> `GROQ_API_KEY_1..3` -> `OPENROUTER_API_KEY`) to write
   sharp 3-line factual market updates (1 Arabic, 1 English — zero financial advice).
4. Auto-posts the PNG + caption + inline CTA buttons to:
   - Arabic Telegram Channel (`TELEGRAM_AR_CHANNEL_ID` or fallback `TELEGRAM_PUBLIC_CHANNEL_AR`)
   - English Telegram Channel (`TELEGRAM_EN_CHANNEL_ID` or fallback `TELEGRAM_PUBLIC_CHANNEL_EN`)
   - X / Twitter API v2 (when `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`,
     `X_ACCESS_TOKEN_SECRET` are configured; gracefully skips otherwise).

Usage:
  python scripts/auto_publish.py --dry-run
  python scripts/auto_publish.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field

WORKSPACE_ROOT: Path = Path(__file__).resolve().parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from backend.ai_strategist import MultiProviderRotator, _run_llm_with_fallback
from backend.config import (
    NO_RAW_SYRIA_EMOJI_PROMPT_RULE,
    Settings,
    enforce_utf8_runtime,
    is_placeholder_secret,
    sanitize_syria_flag_emoji,
)

LIVE_FEED_REMOTE_URL = "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/live_feed.json"
CATALOG_FEED_REMOTE_URL = "https://raw.githubusercontent.com/pathrate-afk/ratepath/live-data/catalog_feed.json"
LOCAL_LIVE_FEED_PATH = WORKSPACE_ROOT / "frontend" / "public" / "live_feed.json"
LOCAL_CATALOG_FEED_PATH = WORKSPACE_ROOT / "frontend" / "public" / "catalog_feed.json"
BRAND_LOGO_PATH = WORKSPACE_ROOT / "frontend" / "icons" / "logo-mark.png"
DEFAULT_OUTPUT_DIR = WORKSPACE_ROOT / "tmp_autopublish"
SITE_URL = "https://ratepathlive.com"
BOT_USERNAME = "RatePathTerminalBot"
RAW_SYRIA_FLAG = "\U0001F1F8\U0001F1FE"


@dataclass(frozen=True)
class MarketPulseSnapshot:
    xau_oz: str
    gold_24k: str
    gold_21k: str
    btc_usd: str
    btc_change_24h: str
    usd_try: str
    usd_syp: str
    usd_egp: str
    top_mover_symbol: str
    top_mover_price: str
    top_mover_change_24h: str
    generated_at: str


class AutoPublishCaptions(BaseModel):
    """Strict 3-line bilingual market pulse captions (factual numbers only)."""

    model_config = ConfigDict(extra="forbid")

    ar_lines: list[str] = Field(min_length=3, max_length=3)
    en_lines: list[str] = Field(min_length=3, max_length=3)
    x_post: str = Field(min_length=40, max_length=275)


def _dec(val: Any) -> Decimal | None:
    try:
        d = Decimal(str(val).strip())
        return d
    except (InvalidOperation, ValueError, TypeError):
        return None


def _fmt_price(val: Any, decimals: int = 2) -> str:
    d = _dec(val)
    if d is None:
        return "—"
    if d > 0 and d < Decimal("0.1"):
        q = d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return f"{q:,.4f}"
    step = Decimal("10") ** (-decimals)
    q = d.quantize(step, rounding=ROUND_HALF_UP)
    return f"{q:,.{decimals}f}"


def _fmt_pct(val: Any) -> str:
    d = _dec(val)
    if d is None:
        return "0.00%"
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "+" if q >= 0 else ""
    return f"{sign}{q}%"


async def load_latest_feeds(
    client: httpx.AsyncClient,
    *,
    prefer_local: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fetch live-data JSON from GitHub with resilient fallback to committed local feeds."""
    live_feed: dict[str, Any] = {}
    catalog_feed: dict[str, Any] = {}

    if not prefer_local:
        try:
            r_live, r_cat = await asyncio.gather(
                client.get(LIVE_FEED_REMOTE_URL, timeout=httpx.Timeout(10)),
                client.get(CATALOG_FEED_REMOTE_URL, timeout=httpx.Timeout(10)),
                return_exceptions=True,
            )
            if isinstance(r_live, httpx.Response) and r_live.status_code == 200:
                live_feed = r_live.json()
            if isinstance(r_cat, httpx.Response) and r_cat.status_code == 200:
                catalog_feed = r_cat.json()
        except Exception:
            pass

    if not live_feed and LOCAL_LIVE_FEED_PATH.exists():
        live_feed = json.loads(LOCAL_LIVE_FEED_PATH.read_text(encoding="utf-8"))
    if not catalog_feed and LOCAL_CATALOG_FEED_PATH.exists():
        catalog_feed = json.loads(LOCAL_CATALOG_FEED_PATH.read_text(encoding="utf-8"))
    return live_feed, catalog_feed


def extract_market_pulse_snapshot(
    live_feed: dict[str, Any],
    catalog_feed: dict[str, Any],
) -> MarketPulseSnapshot:
    """Extract Gold XAU, BTC, USD/TRY, USD/SYP, USD/EGP, and Top 24h Mover."""
    gold_prices = (live_feed.get("gold") or {}).get("prices") or {}
    fiat_rates = (live_feed.get("fiat") or {}).get("rates") or {}
    crypto_assets = (live_feed.get("crypto") or {}).get("assets") or {}
    cat_crypto = catalog_feed.get("crypto") or []

    btc_chg = "0.00%"
    top_sym = "ETH"
    top_price = _fmt_price((crypto_assets.get("ETH") or {}).get("spot_price") or "1657.06")
    top_chg_dec = Decimal("0")

    stable_skip = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "PYUSD", "BUSD"}
    candidates = cat_crypto[:60] if isinstance(cat_crypto, list) else []
    for item in candidates:
        sym = str(item.get("symbol") or "").upper()
        chg_d = _dec(item.get("change_24h_pct"))
        if sym == "BTC" and chg_d is not None:
            btc_chg = _fmt_pct(chg_d)
        if not sym or sym in stable_skip or chg_d is None:
            continue
        if abs(chg_d) > abs(top_chg_dec):
            top_chg_dec = chg_d
            top_sym = sym
            top_price = _fmt_price(item.get("price_usd"))

    stamp = str(
        (live_feed.get("meta") or {}).get("generated_at")
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    return MarketPulseSnapshot(
        xau_oz=_fmt_price(gold_prices.get("XAU_OZ") or "3653.50"),
        gold_24k=_fmt_price(gold_prices.get("GRAM_24K") or "117.46"),
        gold_21k=_fmt_price(gold_prices.get("GRAM_21K") or "102.78"),
        btc_usd=_fmt_price((crypto_assets.get("BTC") or {}).get("spot_price") or "59749.90"),
        btc_change_24h=btc_chg,
        usd_try=_fmt_price((fiat_rates.get("TRY") or {}).get("units_per_usd") or "50.70"),
        usd_syp=_fmt_price((fiat_rates.get("SYP") or {}).get("units_per_usd") or "152.50"),
        usd_egp=_fmt_price((fiat_rates.get("EGP") or {}).get("units_per_usd") or "54.82"),
        top_mover_symbol=top_sym,
        top_mover_price=top_price,
        top_mover_change_24h=_fmt_pct(top_chg_dec),
        generated_at=stamp,
    )


def _load_font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[str] = []
    if mono:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "C:/Windows/Fonts/consolab.ttf" if bold else "C:/Windows/Fonts/consola.ttf",
        ]
    candidates += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path_str in candidates:
        if Path(path_str).is_file():
            try:
                return ImageFont.truetype(path_str, size=size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_syria_revolution_flag(draw: ImageDraw.ImageDraw, x: int, y: int, w: int = 34, h: int = 21) -> None:
    """Draw the official 3-stripe green/white/black Syrian flag with 3 red stars (zero raw emoji)."""
    sh = h // 3
    draw.rectangle([x, y, x + w, y + sh], fill=(0, 122, 61))
    draw.rectangle([x, y + sh, x + w, y + 2 * sh], fill=(248, 250, 252))
    draw.rectangle([x, y + 2 * sh, x + w, y + h], fill=(15, 23, 42))
    cy = y + sh + sh // 2
    for offset in (w // 4, w // 2, (3 * w) // 4):
        draw.ellipse([x + offset - 2, cy - 2, x + offset + 2, cy + 2], fill=(206, 17, 38))
    draw.rectangle([x, y, x + w, y + h], outline=(148, 163, 184), width=1)


def render_market_pulse_png(snapshot: MarketPulseSnapshot, output_path: Path) -> Path:
    """Render a crisp 1200x630 institutional RatePath Quote Card PNG with clear watermarks."""
    width, height = 1200, 630
    img = Image.new("RGB", (width, height), (7, 11, 20))
    draw = ImageDraw.Draw(img, "RGBA")

    # 1. Deep Obsidian vertical gradient
    for y in range(height):
        ratio = y / height
        r = int(7 + 9 * (1 - abs(2 * ratio - 1)))
        g = int(11 + 13 * (1 - abs(2 * ratio - 1)))
        b = int(20 + 24 * (1 - abs(2 * ratio - 1)))
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

    # 2. Subtle institutional terminal grid
    for gx in range(0, width, 48):
        draw.line([(gx, 0), (gx, height)], fill=(148, 163, 184, 14), width=1)
    for gy in range(0, height, 48):
        draw.line([(0, gy), (width, gy)], fill=(148, 163, 184, 14), width=1)

    # 3. Bullion Gold outer frame
    draw.rounded_rectangle([20, 20, width - 20, height - 20], radius=24, outline=(212, 175, 55, 225), width=3)

    # Fonts
    f_brand = _load_font(34, bold=True)
    f_sub = _load_font(16, bold=False)
    f_pill = _load_font(16, bold=True, mono=True)
    f_label = _load_font(17, bold=True)
    f_val = _load_font(34, bold=True, mono=True)
    f_meta = _load_font(16, bold=True, mono=True)
    f_footer_main = _load_font(22, bold=True, mono=True)
    f_footer_mid = _load_font(14, bold=False)

    # 4. Header Logo + Title + Live UTC Timestamp Pill
    brand_x = 54
    if BRAND_LOGO_PATH.is_file():
        try:
            logo = Image.open(BRAND_LOGO_PATH).convert("RGBA").resize((66, 66), Image.Resampling.LANCZOS)
            mask = Image.new("L", (66, 66), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, 66, 66], radius=14, fill=255)
            img.paste(logo, (50, 44), mask)
            draw.rounded_rectangle([50, 44, 116, 110], radius=14, outline=(212, 175, 55, 160), width=2)
            brand_x = 134
        except Exception:
            pass

    draw.text((brand_x, 46), "RATEPATH  LIVE MARKET PULSE", fill=(212, 175, 55, 255), font=f_brand)
    draw.text((brand_x, 88), "REAL-TIME FX  •  GOLD BULLION  •  CRYPTO SNAPSHOT", fill=(148, 163, 184, 255), font=f_sub)

    utc_stamp = snapshot.generated_at[:16].replace("T", " ") + " UTC"
    pill_box = [width - 332, 52, width - 50, 96]
    draw.rounded_rectangle(pill_box, radius=22, fill=(16, 185, 129, 36), outline=(52, 211, 153, 145), width=2)
    draw.ellipse([width - 314, 68, width - 302, 80], fill=(52, 211, 153, 255))
    draw.text((width - 292, 64), utc_stamp, fill=(110, 231, 183, 255), font=f_pill)

    # 5. 3x2 High-Contrast Live Metric Grid
    mover_up = not snapshot.top_mover_change_24h.startswith("-")
    btc_up = not snapshot.btc_change_24h.startswith("-")
    cards = [
        ("GOLD SPOT (XAU/OZ)", f"${snapshot.xau_oz}", f"24K: ${snapshot.gold_24k}/g  •  21K: ${snapshot.gold_21k}/g", (253, 230, 138, 255), False),
        ("BITCOIN (BTC/USD)", f"${snapshot.btc_usd}", f"24H: {'▲ ' if btc_up else '▼ '}{snapshot.btc_change_24h}", (52, 211, 153, 255) if btc_up else (251, 113, 133, 255), False),
        (f"TOP 24H MOVER ({snapshot.top_mover_symbol})", f"${snapshot.top_mover_price}", f"{'▲ ' if mover_up else '▼ '}{snapshot.top_mover_change_24h} (24H)", (52, 211, 153, 255) if mover_up else (251, 113, 133, 255), False),
        ("USD / TRY (TURKISH LIRA)", f"{snapshot.usd_try} TRY", "1 USD Live Exchange Rate", (148, 163, 184, 255), False),
        ("USD / SYP (SYRIAN POUND)", f"{snapshot.usd_syp} SYP", "1 USD Live Market Rate", (110, 231, 183, 255), True),
        ("USD / EGP (EGYPTIAN POUND)", f"{snapshot.usd_egp} EGP", "1 USD Live Exchange Rate", (148, 163, 184, 255), False),
    ]

    card_w, card_h = 344, 174
    gap_x, gap_y = 26, 22
    start_x, start_y = 54, 140

    for idx, (label, main_val, sub_val, sub_color, has_syp_flag) in enumerate(cards):
        col = idx % 3
        row = idx // 3
        cx = start_x + col * (card_w + gap_x)
        cy = start_y + row * (card_h + gap_y)

        draw.rounded_rectangle(
            [cx, cy, cx + card_w, cy + card_h],
            radius=18,
            fill=(15, 23, 42, 230),
            outline=(212, 175, 55, 95) if idx < 3 else (71, 85, 105, 150),
            width=2,
        )
        lbl_x = cx + 22
        if has_syp_flag:
            _draw_syria_revolution_flag(draw, cx + 22, cy + 20, 30, 18)
            lbl_x = cx + 62
        draw.text((lbl_x, cy + 20), label, fill=(148, 163, 184, 255), font=f_label)
        draw.text((cx + 22, cy + 64), main_val, fill=(248, 250, 252, 255), font=f_val)
        draw.text((cx + 22, cy + 126), sub_val, fill=sub_color, font=f_meta)

    # 6. Stamped Watermark Footer (`ratepathlive.com` & `@RatePathTerminalBot`)
    footer_y = height - 86
    draw.line([(50, footer_y - 14), (width - 50, footer_y - 14)], fill=(212, 175, 55, 110), width=1)
    draw.text((54, footer_y), "ratepathlive.com", fill=(253, 230, 138, 255), font=f_footer_main)
    draw.text((438, footer_y + 6), "LIVE MARKET DATA ONLY  •  NOT FINANCIAL ADVICE", fill=(148, 163, 184, 225), font=f_footer_mid)
    draw.text((width - 332, footer_y), "@RatePathTerminalBot", fill=(52, 211, 153, 255), font=f_footer_main)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="PNG", optimize=True)
    return output_path


def _deterministic_captions(snapshot: MarketPulseSnapshot) -> AutoPublishCaptions:
    """Zero-failure factual 3-line fallback captions when AI keys are offline/placeholder."""
    mover_arrow = "▲" if not snapshot.top_mover_change_24h.startswith("-") else "🔻"
    btc_arrow = "▲" if not snapshot.btc_change_24h.startswith("-") else "🔻"
    ar_lines = [
        f"⚡ الذهب الفوري يسجل ${snapshot.xau_oz}/أونصة (عيار 21: ${snapshot.gold_21k}) والبيتكوين عند ${snapshot.btc_usd} ({btc_arrow} {snapshot.btc_change_24h}).",
        f"💱 الصرف المباشر: الدولار عند {snapshot.usd_try} TRY • {snapshot.usd_syp} 🟢 ل.س • {snapshot.usd_egp} EGP.",
        f"📊 أبرز حركة خلال 24 ساعة: {snapshot.top_mover_symbol} عند ${snapshot.top_mover_price} ({mover_arrow} {snapshot.top_mover_change_24h}) — بيانات لحظية فقط.",
    ]
    en_lines = [
        f"⚡ Spot Gold trades at ${snapshot.xau_oz}/oz (21K: ${snapshot.gold_21k}) while Bitcoin holds ${snapshot.btc_usd} ({btc_arrow} {snapshot.btc_change_24h}).",
        f"💱 Live FX rates: USD/TRY {snapshot.usd_try} • USD/SYP {snapshot.usd_syp} (🟢 SYP) • USD/EGP {snapshot.usd_egp}.",
        f"📊 Top 24h crypto mover: {snapshot.top_mover_symbol} at ${snapshot.top_mover_price} ({mover_arrow} {snapshot.top_mover_change_24h}) — live data only.",
    ]
    x_post = (
        f"⚡ RatePath Market Pulse\n"
        f"Gold XAU: ${snapshot.xau_oz} | BTC: ${snapshot.btc_usd} ({snapshot.btc_change_24h})\n"
        f"USD/TRY: {snapshot.usd_try} | USD/SYP: {snapshot.usd_syp} | USD/EGP: {snapshot.usd_egp}\n"
        f"Top 24h: {snapshot.top_mover_symbol} ({snapshot.top_mover_change_24h})\n"
        f"Live converter: {SITE_URL} • Alerts: @{BOT_USERNAME}"
    )[:275]
    return AutoPublishCaptions(ar_lines=ar_lines, en_lines=en_lines, x_post=x_post)


async def generate_bilingual_captions(
    snapshot: MarketPulseSnapshot,
    settings: Settings,
    client: httpx.AsyncClient,
) -> tuple[AutoPublishCaptions, str]:
    """Generate crisp 3-line AR & EN factual updates reusing the project's existing AI rotator."""
    if not settings.has_live_llm:
        return _deterministic_captions(snapshot), "deterministic_fallback"

    system_prompt = (
        "You are a factual market-data wire editor for RatePath (ريت باث). "
        "Generate sharp, clean, 3-line market snapshots using ONLY the exact numbers provided.\n"
        "STRICT RULES:\n"
        "1. `ar_lines`: EXACTLY 3 concise Arabic lines (factual prices/moves only, clean spacing).\n"
        "2. `en_lines`: EXACTLY 3 concise English lines (factual prices/moves only, clean spacing).\n"
        "3. `x_post`: Under 265 characters, factual highlights + https://ratepathlive.com + @RatePathTerminalBot.\n"
        "4. ZERO financial advice, ZERO buy/sell calls, ZERO price guarantees, ZERO hype filler.\n"
        f"5. {NO_RAW_SYRIA_EMOJI_PROMPT_RULE}"
    )
    user_prompt = json.dumps(
        {
            "timestamp_utc": snapshot.generated_at,
            "gold_xau_oz_usd": snapshot.xau_oz,
            "gold_24k_gram_usd": snapshot.gold_24k,
            "gold_21k_gram_usd": snapshot.gold_21k,
            "btc_usd": snapshot.btc_usd,
            "btc_change_24h": snapshot.btc_change_24h,
            "usd_try": snapshot.usd_try,
            "usd_syp": snapshot.usd_syp,
            "usd_egp": snapshot.usd_egp,
            "top_24h_mover": {
                "symbol": snapshot.top_mover_symbol,
                "price_usd": snapshot.top_mover_price,
                "change_24h": snapshot.top_mover_change_24h,
            },
        },
        ensure_ascii=False,
    )

    try:
        rotator = MultiProviderRotator(settings)
        res = await _run_llm_with_fallback(
            client,
            settings,
            system_prompt,
            user_prompt,
            AutoPublishCaptions,
            rotator=rotator,
        )
        cleaned = AutoPublishCaptions(
            ar_lines=[sanitize_syria_flag_emoji(ln.strip(), lang="ar") for ln in res.ar_lines],
            en_lines=[sanitize_syria_flag_emoji(ln.strip(), lang="en") for ln in res.en_lines],
            x_post=sanitize_syria_flag_emoji(res.x_post.strip(), lang="en")[:275],
        )
        return cleaned, "live_llm"
    except Exception:
        return _deterministic_captions(snapshot), "deterministic_fallback"


def format_telegram_caption(lines: list[str], lang: str) -> str:
    body = "\n\n".join(html.escape(sanitize_syria_flag_emoji(ln.strip(), lang="ar" if lang == "ar" else "en")) for ln in lines)
    footer = (
        "⚖️ <i>بيانات لحظية معلوماتية فقط — ليست نصيحة استثمارية.</i>"
        if lang == "ar"
        else "⚖️ <i>Live informational data only — not financial advice.</i>"
    )
    return f"{body}\n\n{footer}"


def build_telegram_inline_keyboard(lang: str) -> dict[str, Any]:
    if lang == "ar":
        return {
            "inline_keyboard": [
                [
                    {"text": "🌐 المحوّل المباشر", "url": f"{SITE_URL}/?lang=ar"},
                    {"text": "🤖 تنبيهات مجانية", "url": f"https://t.me/{BOT_USERNAME}?start=ch_ar"},
                ]
            ]
        }
    return {
        "inline_keyboard": [
            [
                {"text": "🌐 Live Converter", "url": f"{SITE_URL}/?lang=en"},
                {"text": "🤖 Free Alerts", "url": f"https://t.me/{BOT_USERNAME}?start=ch_en"},
            ]
        ]
    }


async def publish_telegram_photo(
    client: httpx.AsyncClient,
    bot_token: str,
    chat_id: str,
    png_bytes: bytes,
    caption_html: str,
    reply_markup: dict[str, Any],
) -> dict[str, Any]:
    if is_placeholder_secret(bot_token) or is_placeholder_secret(chat_id):
        return {"status": "skipped_unconfigured"}
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    resp = await client.post(
        url,
        data={
            "chat_id": chat_id,
            "caption": caption_html[:1024],
            "parse_mode": "HTML",
            "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
        },
        files={"photo": ("ratepath_market_pulse.png", png_bytes, "image/png")},
        timeout=httpx.Timeout(20),
    )
    payload = resp.json() if resp.content else {}
    return {
        "status": "sent" if resp.status_code == 200 and payload.get("ok") else "failed",
        "http_status": resp.status_code,
        "message_id": (payload.get("result") or {}).get("message_id"),
    }


def _oauth1_header(
    method: str,
    url: str,
    *,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    extra_params: dict[str, str] | None = None,
) -> str:
    """Build RFC 5849 OAuth 1.0a HMAC-SHA1 Authorization header using stdlib only."""
    oauth_params = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    all_params = {**oauth_params, **(extra_params or {})}
    param_str = "&".join(
        f"{quote(str(k), safe='')}={quote(str(v), safe='')}"
        for k, v in sorted(all_params.items())
    )
    base_str = f"{method.upper()}&{quote(url, safe='')}&{quote(param_str, safe='')}"
    signing_key = f"{quote(api_secret, safe='')}&{quote(access_secret, safe='')}"
    sig = base64.b64encode(
        hmac.new(signing_key.encode("utf-8"), base_str.encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")
    oauth_params["oauth_signature"] = sig
    return "OAuth " + ", ".join(
        f'{quote(k, safe="")}="{quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
    )


async def publish_to_x(
    client: httpx.AsyncClient,
    tweet_text: str,
    png_bytes: bytes,
) -> dict[str, Any]:
    """Post PNG + text to X (Twitter API v2 Free Tier) if credentials exist; otherwise skip."""
    api_key = os.environ.get("X_API_KEY", "").strip()
    api_secret = os.environ.get("X_API_SECRET", "").strip()
    access_token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    access_secret = os.environ.get("X_ACCESS_TOKEN_SECRET", "").strip()

    if any(is_placeholder_secret(v) for v in (api_key, api_secret, access_token, access_secret)):
        return {"status": "skipped_unconfigured"}

    media_id: str | None = None
    try:
        upload_url = "https://upload.twitter.com/1.1/media/upload.json"
        auth_up = _oauth1_header(
            "POST",
            upload_url,
            api_key=api_key,
            api_secret=api_secret,
            access_token=access_token,
            access_secret=access_secret,
        )
        r_up = await client.post(
            upload_url,
            headers={"Authorization": auth_up},
            files={"media": ("ratepath_pulse.png", png_bytes, "image/png")},
            timeout=httpx.Timeout(20),
        )
        if r_up.status_code in (200, 201):
            media_id = str((r_up.json() or {}).get("media_id_string") or "") or None
    except Exception:
        media_id = None

    tweet_url = "https://api.twitter.com/2/tweets"
    auth_tw = _oauth1_header(
        "POST",
        tweet_url,
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_secret=access_secret,
    )
    body: dict[str, Any] = {"text": tweet_text[:280]}
    if media_id:
        body["media"] = {"media_ids": [media_id]}

    r_tw = await client.post(
        tweet_url,
        headers={"Authorization": auth_tw, "Content-Type": "application/json"},
        json=body,
        timeout=httpx.Timeout(15),
    )
    data = r_tw.json() if r_tw.content else {}
    return {
        "status": "posted" if r_tw.status_code in (200, 201) else "failed",
        "http_status": r_tw.status_code,
        "tweet_id": (data.get("data") or {}).get("id"),
        "with_media": bool(media_id),
    }


async def run_auto_publish(
    *,
    dry_run: bool = False,
    prefer_local: bool = False,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(20))

    try:
        live_feed, catalog_feed = await load_latest_feeds(http_client, prefer_local=prefer_local)
        snapshot = extract_market_pulse_snapshot(live_feed, catalog_feed)

        png_path = output_dir / "ratepath_market_pulse.png"
        render_market_pulse_png(snapshot, png_path)
        png_bytes = png_path.read_bytes()

        captions, ai_mode = await generate_bilingual_captions(snapshot, cfg, http_client)
        ar_caption = format_telegram_caption(captions.ar_lines, "ar")
        en_caption = format_telegram_caption(captions.en_lines, "en")

        assert RAW_SYRIA_FLAG not in ar_caption
        assert RAW_SYRIA_FLAG not in en_caption
        assert RAW_SYRIA_FLAG not in captions.x_post

        ar_channel = os.environ.get("TELEGRAM_AR_CHANNEL_ID", "").strip()
        if is_placeholder_secret(ar_channel):
            ar_channel = cfg.telegram_public_channel_ar

        en_channel = os.environ.get("TELEGRAM_EN_CHANNEL_ID", "").strip()
        if is_placeholder_secret(en_channel):
            en_channel = cfg.telegram_public_channel_en

        if dry_run:
            return {
                "mode": "dry_run",
                "ai_mode": ai_mode,
                "png_path": str(png_path),
                "png_bytes": len(png_bytes),
                "snapshot": snapshot.__dict__,
                "ar_caption": ar_caption,
                "en_caption": en_caption,
                "x_post": captions.x_post,
                "ar_keyboard": build_telegram_inline_keyboard("ar"),
                "en_keyboard": build_telegram_inline_keyboard("en"),
            }

        ar_res, en_res, x_res = await asyncio.gather(
            publish_telegram_photo(
                http_client,
                cfg.telegram_bot_token,
                ar_channel,
                png_bytes,
                ar_caption,
                build_telegram_inline_keyboard("ar"),
            ),
            publish_telegram_photo(
                http_client,
                cfg.telegram_bot_token,
                en_channel,
                png_bytes,
                en_caption,
                build_telegram_inline_keyboard("en"),
            ),
            publish_to_x(http_client, captions.x_post, png_bytes),
        )
        return {
            "mode": "live",
            "ai_mode": ai_mode,
            "png_path": str(png_path),
            "png_bytes": len(png_bytes),
            "telegram_ar": ar_res,
            "telegram_en": en_res,
            "x_twitter": x_res,
        }
    finally:
        if owns_client:
            await http_client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="RatePath 3x-Daily Market Pulse Auto-Publisher")
    parser.add_argument("--dry-run", action="store_true", help="Render PNG & captions without posting externally")
    parser.add_argument("--prefer-local", action="store_true", help="Read local frontend/public JSON snapshots first")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory for Quote Card PNG")
    args = parser.parse_args()

    result = asyncio.run(
        run_auto_publish(
            dry_run=args.dry_run,
            prefer_local=args.prefer_local,
            output_dir=args.output_dir,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
