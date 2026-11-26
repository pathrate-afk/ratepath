"""Every-4-Hours Exclusive Economic, Whale, Stablecoin & Corporate Shocks Digest (Pure Decimal, ZERO floats).

Delivers genuinely high-value investor intelligence every 4 hours (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC):
1. On-Chain Stablecoin Treasury Flows: Real 24h USDT (Tether) & USDC (Circle) mint/burn supply deltas via DefiLlama.
2. Top Crypto Surges & Collapses: Biggest 24h gainers & losers from `catalog_feed.json` + WHY they moved.
3. Corporate Shocks & Global Sovereign Economy: Bankruptcies, massive losses, company closures, SEC/Fed/ECB moves,
   whale wallet movements — strictly filtered against lifestyle/personal-finance noise.
4. Language & Tier Separation:
   - VIP Channels: Full structured 3-section executive brief (zero filler, zero Conviction X/100, zero repetitive VIP tags).
   - Public Channels: Crisp breaking highlights + smart promotional link driving traffic to `https://ratepathlive.com` & `@RatePathTerminalBot`.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any, Literal

import feedparser
import httpx
from pydantic import BaseModel, ConfigDict, Field

from backend.ai_strategist import MultiProviderRotator, _run_llm_with_fallback
from backend.config import (
    NO_RAW_SYRIA_EMOJI_PROMPT_RULE,
    WORKSPACE_ROOT,
    Settings,
    enforce_utf8_runtime,
    sanitize_syria_flag_emoji,
)
from backend.math_engine import loads_decimal_json, parse_strict_decimal
from backend.telegram_bot import _esc, _send_telegram_payload, normalize_preserve_newlines

LOCAL_DIGEST4H_STATE_PATH: Path = WORKSPACE_ROOT / "backend" / "digest_4h_state.tmp.json"
DEFILLAMA_STABLECOINS_URL: str = "https://stablecoins.llama.fi/stablecoins?includePrices=true"

_HSPACE_RE = re.compile(r"[ \t]+")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NOISE_RE = re.compile(
    r"(401\(k\)|stay-at-home|my husband|my wife|should i retire|social security|wedding|divorce|"
    r"horoscope|credit card tips|best savings account|gift guide|holiday shopping|inheritance)",
    re.IGNORECASE,
)

DIGEST_FEEDS: tuple[tuple[str, str, str], ...] = (
    # (category, source_name, url)
    ("whale_crypto", "CoinTelegraph", "https://cointelegraph.com/rss"),
    ("whale_crypto", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    (
        "whale_crypto",
        "Whale & Stablecoin Wire",
        "https://news.google.com/rss/search?q=Tether+USDT+mint+OR+crypto+whale+transfer+OR+crypto+hack+OR+token+crash&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "corporate",
        "Corporate Shocks Wire",
        "https://news.google.com/rss/search?q=company+bankruptcy+OR+layoffs+OR+massive+loss+OR+shuts+down+OR+stock+plunges+OR+stock+surges&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "corporate",
        "CNBC Finance",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    ),
    (
        "macro",
        "CNBC Economy",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    ),
    (
        "macro",
        "Global Macro Wire",
        "https://news.google.com/rss/search?q=central+bank+interest+rate+OR+inflation+shock+OR+oil+price+OR+gold+surge+OR+currency+crisis&hl=en-US&gl=US&ceid=US:en",
    ),
)

_PRIORITY_TERMS_RE = re.compile(
    r"\b(tether|usdt|usdc|mint|burn|whale|wallet|billion|million|bankrupt|bankruptcy|insolvency|"
    r"collapse|crash|plunge|surge|soar|hack|exploit|depeg|layoffs|shutdown|closure|loss|sec|fed|"
    r"ecb|interest rate|inflation|oil|gold|bitcoin|ethereum|solana|etf|sanctions|tariff)\b",
    re.IGNORECASE,
)


class Digest4hSynthesis(BaseModel):
    """Structured bilingual synthesis for the 4-hour exclusive investor brief."""

    model_config = ConfigDict(extra="forbid")

    whale_bullets_ar: list[str] = Field(min_length=1, max_length=3)
    crypto_bullets_ar: list[str] = Field(min_length=1, max_length=3)
    corporate_macro_bullets_ar: list[str] = Field(min_length=1, max_length=4)
    whale_bullets_en: list[str] = Field(min_length=1, max_length=3)
    crypto_bullets_en: list[str] = Field(min_length=1, max_length=3)
    corporate_macro_bullets_en: list[str] = Field(min_length=1, max_length=4)


def _clean_text(raw: str) -> str:
    txt = _HTML_TAG_RE.sub(" ", html.unescape(raw or ""))
    lines = [_HSPACE_RE.sub(" ", ln).strip() for ln in txt.replace("\r\n", "\n").splitlines() if ln.strip()]
    return " ".join(lines).strip()


def detect_4h_digest_slot(now_utc: datetime | None = None, *, window_minutes: int = 25) -> str | None:
    """Return slot key `YYYY-MM-DD:H{hour}` when inside `[0, window_minutes)` of a 4-hour boundary."""
    dt = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if dt.hour % 4 == 0 and dt.minute < window_minutes:
        return f"{dt.strftime('%Y-%m-%d')}:H{dt.hour:02d}"
    return None


async def fetch_stablecoin_supply_deltas(client: httpx.AsyncClient) -> dict[str, dict[str, str]]:
    """Compute real 24h USDT & USDC supply mint/burn delta in $Millions using pure Decimal."""
    out: dict[str, dict[str, str]] = {}
    try:
        resp = await client.get(DEFILLAMA_STABLECOINS_URL, timeout=httpx.Timeout(9))
        resp.raise_for_status()
        data = loads_decimal_json(resp.text)
        for row in data.get("peggedAssets") or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol", "")).upper()
            if sym not in ("USDT", "USDC"):
                continue
            curr_raw = (row.get("circulating") or {}).get("peggedUSD")
            prev_raw = (row.get("circulatingPrevDay") or {}).get("peggedUSD")
            if curr_raw is None or prev_raw is None:
                continue
            curr = parse_strict_decimal(curr_raw, field_name=f"llama.{sym}.curr")
            prev = parse_strict_decimal(prev_raw, field_name=f"llama.{sym}.prev")
            with localcontext() as ctx:
                ctx.prec = 28
                delta_m = ((curr - prev) / Decimal("1000000")).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
                total_b = (curr / Decimal("1000000000")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            direction = "up" if delta_m > Decimal("0") else ("down" if delta_m < Decimal("0") else "flat")
            sign = "+" if delta_m > Decimal("0") else ""
            out[sym] = {
                "delta_m": f"{sign}{format(delta_m, 'f')}",
                "total_b": format(total_b, "f"),
                "direction": direction,
            }
    except Exception:
        pass
    return out


def extract_top_crypto_movers(catalog_payload: dict[str, Any] | None) -> dict[str, list[dict[str, str]]]:
    """Extract top 24h crypto gainers & losers from `catalog_feed.json` using pure Decimal."""
    coins = (catalog_payload or {}).get("crypto") or []
    scored: list[tuple[Decimal, dict[str, str]]] = []
    for c in coins[:120]:
        if not isinstance(c, dict):
            continue
        sym = str(c.get("symbol") or "").upper()
        chg_raw = c.get("change_24h_pct")
        price_raw = c.get("price_usd")
        if not sym or chg_raw is None or price_raw is None:
            continue
        try:
            chg_dec = parse_strict_decimal(
                chg_raw, field_name=f"mover.{sym}", allow_zero=True, allow_negative=True
            )
            scored.append(
                (
                    chg_dec,
                    {
                        "symbol": sym,
                        "name": str(c.get("name") or sym),
                        "price_usd": str(price_raw),
                        "change_24h_pct": format(chg_dec.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f"),
                    },
                )
            )
        except Exception:
            continue
    scored.sort(key=lambda pair: pair[0], reverse=True)
    gainers = [row for dec, row in scored if dec >= Decimal("4.0")][:2]
    losers = [row for dec, row in reversed(scored) if dec <= Decimal("-4.0")][:2]
    return {"gainers": gainers, "losers": losers}


async def collect_high_impact_digest_wires(client: httpx.AsyncClient) -> dict[str, list[dict[str, str]]]:
    """Fetch & rank exclusive whale, crypto, corporate-shock, and global macro headlines."""
    async def _one(cat: str, src: str, url: str) -> list[dict[str, str]]:
        try:
            r = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 RatePathDigest/2.0"},
                timeout=httpx.Timeout(8),
            )
            r.raise_for_status()
            parsed = feedparser.parse(r.content)
            rows: list[dict[str, str]] = []
            for e in parsed.entries[:8]:
                title = _clean_text(str(e.get("title") or ""))
                summary = _clean_text(str(e.get("summary") or title))
                if len(title) < 14 or _NOISE_RE.search(title):
                    continue
                score = len(_PRIORITY_TERMS_RE.findall(f"{title} {summary[:140]}"))
                rows.append(
                    {
                        "category": cat,
                        "source": src,
                        "title": title[:220],
                        "summary": summary[:260],
                        "impact": str(score),
                    }
                )
            return rows
        except Exception:
            return []

    batches = await asyncio.gather(*(_one(cat, src, url) for cat, src, url in DIGEST_FEEDS))
    grouped: dict[str, list[dict[str, str]]] = {"whale_crypto": [], "corporate": [], "macro": []}
    seen: set[str] = set()
    for batch in batches:
        for row in batch:
            key = row["title"].lower()[:75]
            if key in seen:
                continue
            seen.add(key)
            grouped.setdefault(row["category"], []).append(row)

    for cat in grouped:
        grouped[cat].sort(key=lambda r: int(r.get("impact", "0")), reverse=True)
        grouped[cat] = grouped[cat][:5]
    return grouped


def _build_deterministic_synthesis(
    stablecoins: dict[str, dict[str, str]],
    movers: dict[str, list[dict[str, str]]],
    wires: dict[str, list[dict[str, str]]],
) -> Digest4hSynthesis:
    """Clean bilingual deterministic synthesis when LLMs are unavailable."""
    whale_ar: list[str] = []
    whale_en: list[str] = []
    for sym in ("USDT", "USDC"):
        st = stablecoins.get(sym)
        if not st:
            continue
        arrow = "▲" if st["direction"] == "up" else ("🔻" if st["direction"] == "down" else "➖")
        verb_ar = "زيادة/طباعة سيولة" if st["direction"] == "up" else ("سحب/تراجع سيولة" if st["direction"] == "down" else "استقرار معروض")
        verb_en = "Net Mint/Inflow" if st["direction"] == "up" else ("Net Burn/Outflow" if st["direction"] == "down" else "Supply Flat")
        whale_ar.append(f"{sym}: {verb_ar} بمقدار {st['delta_m']}M$ {arrow} (الإجمالي {st['total_b']}B$)")
        whale_en.append(f"{sym}: {verb_en} {st['delta_m']}M USD {arrow} (Total ${st['total_b']}B)")
    if not whale_ar:
        whale_ar.append("مراقبة محافظ العملات المستقرة USDT وUSDC بدون تغيّر استثنائي في المعروض خلال الساعات الماضية.")
        whale_en.append("USDT & USDC treasury supply holding steady over the past 4-hour window.")

    crypto_ar: list[str] = []
    crypto_en: list[str] = []
    for g in movers.get("gainers", []):
        crypto_ar.append(f"{g['symbol']} ({g['name']}): صعود قوي ▲ (+{g['change_24h_pct']}%) عند ${g['price_usd']}")
        crypto_en.append(f"{g['symbol']} ({g['name']}): Surged ▲ (+{g['change_24h_pct']}%) to ${g['price_usd']}")
    for l in movers.get("losers", []):
        crypto_ar.append(f"{l['symbol']} ({l['name']}): هبوط حاد 🔻 ({l['change_24h_pct']}%) عند ${l['price_usd']}")
        crypto_en.append(f"{l['symbol']} ({l['name']}): Dropped 🔻 ({l['change_24h_pct']}%) to ${l['price_usd']}")
    for w in (wires.get("whale_crypto") or [])[:2]:
        if len(crypto_en) < 3:
            crypto_en.append(f"{w['source']}: {w['title']}")
            crypto_ar.append(f"تقرير ({w['source']}): {w['title']}")
    if not crypto_ar:
        crypto_ar.append("تداولات متوازنة في أكبر العملات الرقمية مع ترقّب تدفقات السيولة.")
        crypto_en.append("Major cryptocurrencies trading in balanced ranges awaiting fresh liquidity.")

    corp_ar: list[str] = []
    corp_en: list[str] = []
    for item in ((wires.get("corporate") or [])[:2] + (wires.get("macro") or [])[:2])[:3]:
        corp_en.append(f"{item['source']}: {item['title']}")
        corp_ar.append(f"خبر أسواق ({item['source']}): {item['title']}")
    if not corp_ar:
        corp_ar.append("الأسواق العالمية تتابع بيانات التضخم ونتائج الشركات الكبرى خلال الجلسات الحالية.")
        corp_en.append("Global desks monitoring corporate earnings and central-bank liquidity signals.")

    return Digest4hSynthesis(
        whale_bullets_ar=whale_ar[:3],
        crypto_bullets_ar=crypto_ar[:3],
        corporate_macro_bullets_ar=corp_ar[:3],
        whale_bullets_en=whale_en[:3],
        crypto_bullets_en=crypto_en[:3],
        corporate_macro_bullets_en=corp_en[:3],
    )


async def synthesize_4h_digest(
    stablecoins: dict[str, dict[str, str]],
    movers: dict[str, list[dict[str, str]]],
    wires: dict[str, list[dict[str, str]]],
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    use_llm: bool = True,
) -> Digest4hSynthesis:
    """Synthesize real whale/USDT, crypto movers, and corporate/macro headlines into crisp AR & EN."""
    if use_llm and settings.has_live_llm:
        sys_prompt = (
            "You are a senior Bloomberg-style market desk editor for RatePath (ريت باث). "
            "Turn the provided REAL live data (USDT/USDC 24h supply changes, top crypto gainers/losers, "
            "whale wallet headlines, corporate bankruptcies/losses/closures, and global macro wires) into "
            "razor-sharp, genuinely valuable investor bullet points in BOTH fluent Modern Standard Arabic and English. "
            "STRICT RULES: "
            "1) Translate EVERY Arabic bullet fully into crisp natural Arabic (NEVER leave English sentences inside Arabic bullets!). "
            "2) Focus on WHY coins pumped/crashed, concrete whale/USDT print numbers, corporate losses/bankruptcies/closures, and country/central-bank moves. "
            "3) Use ONLY `▲` for rises and `🔻` for drops — NEVER use colored balls (🟢/🔴) or blue arrows (⬆️/⬇️). "
            "4) ZERO filler, ZERO 'Conviction X/100', ZERO 'VIP' reminders, ZERO '100-year memory', ZERO investment promises or guarantees. "
            f"{NO_RAW_SYRIA_EMOJI_PROMPT_RULE}"
        )
        usr_prompt = json.dumps(
            {
                "stablecoin_24h_supply": stablecoins,
                "top_crypto_movers": movers,
                "whale_crypto_wires": wires.get("whale_crypto", [])[:4],
                "corporate_shocks_wires": wires.get("corporate", [])[:4],
                "global_macro_wires": wires.get("macro", [])[:4],
            },
            ensure_ascii=False,
        )
        try:
            rotator = MultiProviderRotator(settings)
            result = await _run_llm_with_fallback(
                client, settings, sys_prompt, usr_prompt, Digest4hSynthesis, rotator=rotator
            )
            assert isinstance(result, Digest4hSynthesis)
            return result
        except Exception:
            pass

    return _build_deterministic_synthesis(stablecoins, movers, wires)


def build_4h_digest_payload(
    synthesis: Digest4hSynthesis,
    *,
    chat_id: str,
    lang: Literal["en", "ar"] = "ar",
    is_vip: bool = False,
    public_site_url: str = "https://ratepathlive.com",
) -> dict[str, Any]:
    """Build either the full VIP 4-hour exclusive brief or the Public teaser + promotional funnel."""
    site_base = (public_site_url or "https://ratepathlive.com").strip().rstrip("/")

    if lang == "ar":
        if is_vip:
            sections = [
                "⚡ <b>الموجز الحصري للأسواق والسيولة (كل 4 ساعات)</b>",
                "🐋 <b>حركة الحيتان ومعروض USDT / USDC:</b>\n"
                + "\n".join(f"• {_esc(b)}" for b in synthesis.whale_bullets_ar),
                "📊 <b>تحركات العملات الرقمية وأسبابها:</b>\n"
                + "\n".join(f"• {_esc(b)}" for b in synthesis.crypto_bullets_ar),
                "🏢 <b>الشركات العملاقة والاقتصاد العالمي:</b>\n"
                + "\n".join(f"• {_esc(b)}" for b in synthesis.corporate_macro_bullets_ar),
                "ℹ️ <i>ملخص معلوماتي لحركة الأرقام والأخبار فقط — ليس توصية استثمارية.</i>",
            ]
        else:
            top_public = (
                synthesis.whale_bullets_ar[:1]
                + synthesis.crypto_bullets_ar[:1]
                + synthesis.corporate_macro_bullets_ar[:1]
            )
            sections = [
                "🗞️ <b>موجز أبرز أخبار الاقتصاد والعملات (كل 4 ساعات)</b>",
                "\n".join(f"• {_esc(b)}" for b in top_public),
                f"🌐 <b>اقرأ كل الأخبار المتجددة كل ساعة وتابع أسعار العملات والذهب وأسهم الشركات مباشرة:</b>\n"
                f"{site_base}/?lang=ar\n\n"
                f"🤖 للحصول على التقرير التفصيلي الكامل + بوت متابعة خاص لعملاتك وأسهمك: @RatePathTerminalBot",
            ]
    else:
        if is_vip:
            sections = [
                "⚡ <b>4-Hour Exclusive Market, Whale &amp; Corporate Brief</b>",
                "🐋 <b>Whale Flows &amp; USDT / USDC Treasury Supply:</b>\n"
                + "\n".join(f"• {_esc(b)}" for b in synthesis.whale_bullets_en),
                "📊 <b>Crypto Surges, Drops &amp; Catalysts:</b>\n"
                + "\n".join(f"• {_esc(b)}" for b in synthesis.crypto_bullets_en),
                "🏢 <b>Major Corporate Shocks &amp; Global Macro:</b>\n"
                + "\n".join(f"• {_esc(b)}" for b in synthesis.corporate_macro_bullets_en),
                "ℹ️ <i>Informational market &amp; news data only — not financial advice.</i>",
            ]
        else:
            top_public_en = (
                synthesis.whale_bullets_en[:1]
                + synthesis.crypto_bullets_en[:1]
                + synthesis.corporate_macro_bullets_en[:1]
            )
            sections = [
                "🗞️ <b>4-Hour Global Economy &amp; Crypto Flash</b>",
                "\n".join(f"• {_esc(b)}" for b in top_public_en),
                f"🌐 <b>Read live hourly news, global stocks &amp; instant rates on RatePath:</b>\n"
                f"{site_base}/?lang=en\n\n"
                f"🤖 Unlock full whale/corporate briefs &amp; your personal tracker bot: @RatePathTerminalBot",
            ]

    text = sanitize_syria_flag_emoji(normalize_preserve_newlines("\n\n".join(sections)), lang=lang)
    return {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }


async def _claim_digest_slot(
    slot_key: str,
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    local_state_path: Path = LOCAL_DIGEST4H_STATE_PATH,
) -> bool:
    local_map: dict[str, str] = {}
    if local_state_path.exists():
        try:
            loaded = json.loads(local_state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                local_map = loaded
        except Exception:
            local_map = {}

    if slot_key in local_map:
        return False

    claimed = True
    if settings.has_live_upstash:
        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
        try:
            resp = await http_client.post(
                settings.upstash_redis_rest_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {settings.upstash_redis_rest_token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(["SET", slot_key, "1", "EX", "21600", "NX"], ensure_ascii=False).encode("utf-8"),
            )
            resp.raise_for_status()
            claimed = json.loads(resp.text).get("result") == "OK"
        except Exception:
            claimed = True
        finally:
            if owns_client:
                await http_client.aclose()

    local_map[slot_key] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if len(local_map) > 100:
        local_map = dict(list(local_map.items())[-100:])
    local_state_path.parent.mkdir(parents=True, exist_ok=True)
    local_state_path.write_text(json.dumps(local_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return claimed


async def maybe_broadcast_4h_digest(
    catalog_payload: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    now_utc: datetime | None = None,
    use_llm: bool = True,
    local_state_path: Path = LOCAL_DIGEST4H_STATE_PATH,
) -> dict[str, Any]:
    """Run every 4 hours: collect whale/USDT/crypto/corporate wires and dispatch Public + VIP digests."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    dt = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    slot = detect_4h_digest_slot(dt)
    if not slot:
        return {"status": "outside_4h_window", "slot": None, "sent": 0}

    if not cfg.has_live_telegram:
        return {"status": "skipped_placeholder_telegram", "slot": slot, "sent": 0}

    slot_key = f"ratepath:digest4h:{slot}"
    is_new = await _claim_digest_slot(slot_key, cfg, client=client, local_state_path=local_state_path)
    if not is_new:
        return {"status": "already_sent_slot", "slot": slot, "sent": 0}

    owns_client = client is None
    http_client = client or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(12))
    sent = 0
    try:
        stablecoins, wires = await asyncio.gather(
            fetch_stablecoin_supply_deltas(http_client),
            collect_high_impact_digest_wires(http_client),
        )
        movers = extract_top_crypto_movers(catalog_payload)
        synthesis = await synthesize_4h_digest(
            stablecoins, movers, wires, settings=cfg, client=http_client, use_llm=use_llm
        )

        targets = cfg.resolve_telegram_targets()
        dispatch_specs: tuple[tuple[str, Literal["en", "ar"], bool], ...] = (
            ("public_en", "en", False),
            ("vip_en", "en", True),
            ("public_ar", "ar", False),
            ("vip_ar", "ar", True),
        )
        for key, lang_code, is_vip_ch in dispatch_specs:
            chat_id = targets.get(key)
            if not chat_id:
                continue
            payload = build_4h_digest_payload(
                synthesis,
                chat_id=chat_id,
                lang=lang_code,
                is_vip=is_vip_ch,
                public_site_url=cfg.public_site_url,
            )
            await _send_telegram_payload(
                http_client,
                cfg.telegram_bot_token,
                payload,
                enforce_paragraph_spacing=True,
            )
            sent += 1
    finally:
        if owns_client:
            await http_client.aclose()

    return {"status": "sent", "slot": slot, "sent": sent}
