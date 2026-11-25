"""3-Tier Resilient Syrian Pound (`SYP`) Fetcher for RatePath (ريت باث).

CRITICAL INVARIANT:
- `open.er-api.com` and generic FX APIs are STRICTLY BANNED from setting or overriding `SYP`.
- Tier 1: `https://sp-today.com/en` (and `/en/currency/us_dollar`) parsed with DOM boundary
  spaces (`extract_dom_text`, equivalent to `get_text(" ", strip=True)`) so adjacent
  `152.25` (new SYP) and `15,225 old` NEVER concatenate.
- Tier 2: SP-Today public Telegram web preview (`https://t.me/s/sp_today`), normalizing
  old-to-new SYP (`÷ 100` if `> 2000`).
- Tier 3: Upstash Redis `ratepath:sptoday_last_good` cache (with local UTF-8 JSON fallback).
- Validated strictly via Agent 7 (`validate_syp_sptoday_spread`: `50 < rate < 500`, `buy <= sell`, `< 5%` spread).
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from backend.agents.price_guardian import (
    SYP_NEW_MAX_RATE,
    SYP_NEW_MIN_RATE,
    validate_syp_sptoday_spread,
)
from backend.config import WORKSPACE_ROOT, Settings, enforce_utf8_runtime
from backend.fetchers import utc_now_iso
from backend.math_engine import QUANT_4DP, format_decimal, parse_strict_decimal

SPTODAY_HOME_URL: str = "https://sp-today.com/en"
SPTODAY_USD_URL: str = "https://sp-today.com/en/currency/us_dollar"
SPTODAY_TELEGRAM_URLS: tuple[str, ...] = (
    "https://t.me/s/sp_today",
    "https://t.me/s/sptoday",
)
SPTODAY_REDIS_LAST_GOOD_KEY: str = "ratepath:sptoday_last_good"
LOCAL_SPTODAY_CACHE_PATH: Path = WORKSPACE_ROOT / "backend" / "sptoday_last_good.tmp.json"

OLD_SYP_THRESHOLD: Decimal = Decimal("2000")
OLD_SYP_MIN: Decimal = Decimal("5000")
OLD_SYP_MAX: Decimal = Decimal("50000")
SYP_REDENOM_DIVISOR: Decimal = Decimal("100")

_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
}

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
_HSPACE_RE = re.compile(r"[ \t]+")
_MSG_BLOCK_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)

# Matches `Buy 152.25 ... Sell 152.75` or `US Dollar 152.25 152.75` on space-separated DOM text
_DOM_BUY_SELL_RE = re.compile(
    r"\bBuy\s+([0-9][0-9,\.]*)\b.{0,80}?\bSell\s+([0-9][0-9,\.]*)\b",
    re.IGNORECASE,
)
_DOM_USD_TABLE_RE = re.compile(
    r"\bUS\s+Dollar\s+([0-9][0-9,\.]*)\s+([0-9][0-9,\.]*)\b",
    re.IGNORECASE,
)
_TG_AR_BUY_SELL_RE = re.compile(
    r"(?:مبيع|بيع|sell)\s*[:\-]?\s*([0-9][0-9,\.]*).{0,60}?(?:شراء|buy)\s*[:\-]?\s*([0-9][0-9,\.]*)",
    re.IGNORECASE,
)
_TG_AR_BUY_FIRST_RE = re.compile(
    r"(?:شراء|buy)\s*[:\-]?\s*([0-9][0-9,\.]*).{0,60}?(?:مبيع|بيع|sell)\s*[:\-]?\s*([0-9][0-9,\.]*)",
    re.IGNORECASE,
)
_TG_USD_SINGLE_RE = re.compile(
    r"(?:الدولار|دولار|USD)[^\d]{0,40}([0-9][0-9,\.]{1,10})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SYPQuote:
    """Verified Syrian Pound (`USD/SYP`) quote in new SYP scale (`50 < rate < 500`)."""

    buy_rate: Decimal
    sell_rate: Decimal
    mid_rate: Decimal
    source: str
    updated_at: str
    spread_pct: str

    def to_dict(self) -> dict[str, str]:
        return {
            "buy_rate": format_decimal(self.buy_rate, QUANT_4DP),
            "sell_rate": format_decimal(self.sell_rate, QUANT_4DP),
            "mid_rate": format_decimal(self.mid_rate, QUANT_4DP),
            "source": self.source,
            "updated_at": self.updated_at,
            "spread_pct": self.spread_pct,
        }


class _SpaceSeparatedDomParser(HTMLParser):
    """DOM text extractor mirroring `BeautifulSoup.get_text(' ', strip=True)` cleanly."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        if low in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
        elif self._skip_depth == 0:
            self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        low = tag.lower()
        if low in ("script", "style", "noscript", "svg"):
            self._skip_depth = max(0, self._skip_depth - 1)
        elif self._skip_depth == 0:
            self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        cleaned = data.strip()
        if cleaned:
            self._chunks.append(cleaned)

    def get_text(self) -> str:
        joined = " ".join(self._chunks)
        return _HSPACE_RE.sub(" ", joined.replace("\r", " ").replace("\n", " ")).strip()


def extract_dom_text(raw_html: str) -> str:
    """Extract space-separated DOM text so adjacent spans (`152.25` & `15,225 old`) NEVER merge."""
    parser = _SpaceSeparatedDomParser()
    parser.feed(html.unescape(raw_html or ""))
    return parser.get_text()


def normalize_syp_new_rate(raw_token: Any) -> Decimal | None:
    """Normalize raw SYP rate to new SYP scale (`50 < rate < 500`), dividing old rates by 100."""
    text = str(raw_token or "").translate(_ARABIC_DIGITS).replace(",", "").strip()
    if not text:
        return None
    try:
        val = parse_strict_decimal(text, field_name="syp.raw")
    except Exception:
        return None

    if SYP_NEW_MIN_RATE < val < SYP_NEW_MAX_RATE:
        return val
    if OLD_SYP_MIN < val < OLD_SYP_MAX:
        scaled = val / SYP_REDENOM_DIVISOR
        if SYP_NEW_MIN_RATE < scaled < SYP_NEW_MAX_RATE:
            return scaled
    if val > OLD_SYP_THRESHOLD:
        scaled = val / SYP_REDENOM_DIVISOR
        if SYP_NEW_MIN_RATE < scaled < SYP_NEW_MAX_RATE:
            return scaled
    return None


def _build_verified_syp_quote(
    raw_buy: Any,
    raw_sell: Any,
    *,
    source: str,
    updated_at: str | None = None,
) -> SYPQuote | None:
    buy_norm = normalize_syp_new_rate(raw_buy)
    sell_norm = normalize_syp_new_rate(raw_sell)
    if buy_norm is None or sell_norm is None:
        return None
    # Guard against inverted sell/buy ordering in Arabic text blocks
    if buy_norm > sell_norm:
        buy_norm, sell_norm = sell_norm, buy_norm

    verdict = validate_syp_sptoday_spread(buy_norm, sell_norm, source=source)
    if verdict.corrupt_spike_rejected:
        return None

    return SYPQuote(
        buy_rate=buy_norm,
        sell_rate=sell_norm,
        mid_rate=verdict.accepted_price,
        source=source,
        updated_at=updated_at or utc_now_iso(),
        spread_pct=verdict.screen_vs_real_spread_pct,
    )


def parse_sptoday_web_html(raw_html: str) -> SYPQuote | None:
    """Tier 1 parser: extract Damascus USD Buy/Sell from `sp-today.com/en` DOM text."""
    dom_text = extract_dom_text(raw_html)
    if not dom_text:
        return None

    # Scope near `US Dollar` to avoid picking up EUR/TRY rates elsewhere on the page
    usd_idx = dom_text.lower().find("us dollar")
    search_window = dom_text[max(0, usd_idx - 40) : usd_idx + 500] if usd_idx != -1 else dom_text[:1500]

    for pattern in (_DOM_BUY_SELL_RE, _DOM_USD_TABLE_RE):
        m = pattern.search(search_window)
        if m:
            quote = _build_verified_syp_quote(m.group(1), m.group(2), source="sp-today")
            if quote is not None:
                return quote

    # Secondary pass on full DOM text if `US Dollar` header differed
    for pattern in (_DOM_BUY_SELL_RE, _DOM_USD_TABLE_RE):
        m = pattern.search(dom_text)
        if m:
            quote = _build_verified_syp_quote(m.group(1), m.group(2), source="sp-today")
            if quote is not None:
                return quote

    return None


def parse_sptoday_telegram_html(raw_html: str) -> SYPQuote | None:
    """Tier 2 parser: extract latest Damascus USD rate from `https://t.me/s/sp_today` HTML."""
    blocks = _MSG_BLOCK_RE.findall(raw_html or "")
    for raw_block in reversed(blocks[-12:]):
        text = extract_dom_text(raw_block).translate(_ARABIC_DIGITS)
        low = text.lower()
        if not any(k in low or k in text for k in ("دولار", "usd", "damascus", "دمشق")):
            continue

        m_sell_first = _TG_AR_BUY_SELL_RE.search(text)
        if m_sell_first:
            quote = _build_verified_syp_quote(
                m_sell_first.group(2), m_sell_first.group(1), source="sp-today_telegram"
            )
            if quote is not None:
                return quote

        m_buy_first = _TG_AR_BUY_FIRST_RE.search(text)
        if m_buy_first:
            quote = _build_verified_syp_quote(
                m_buy_first.group(1), m_buy_first.group(2), source="sp-today_telegram"
            )
            if quote is not None:
                return quote

        m_single = _TG_USD_SINGLE_RE.search(text)
        if m_single:
            single_norm = normalize_syp_new_rate(m_single.group(1))
            if single_norm is not None:
                quote = _build_verified_syp_quote(
                    single_norm, single_norm, source="sp-today_telegram"
                )
                if quote is not None:
                    return quote

    return None


async def write_sptoday_last_good_cache(
    quote: SYPQuote,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    local_cache_path: Path = LOCAL_SPTODAY_CACHE_PATH,
) -> None:
    """Persist verified SP-Today SYP quote to Upstash `ratepath:sptoday_last_good` + local JSON."""
    payload_str = json.dumps(quote.to_dict(), ensure_ascii=False)
    try:
        local_cache_path.parent.mkdir(parents=True, exist_ok=True)
        local_cache_path.write_text(payload_str + "\n", encoding="utf-8", newline="\n")
    except Exception:
        pass

    cfg = settings or Settings.load()
    if not cfg.has_live_upstash:
        return

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
    try:
        endpoint = cfg.upstash_redis_rest_url.rstrip("/")
        await http_client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {cfg.upstash_redis_rest_token}",
                "Content-Type": "application/json",
            },
            content=json.dumps(
                ["SET", SPTODAY_REDIS_LAST_GOOD_KEY, payload_str],
                ensure_ascii=False,
            ).encode("utf-8"),
        )
    except Exception:
        pass
    finally:
        if owns_client:
            await http_client.aclose()


async def read_sptoday_last_good_cache(
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    local_cache_path: Path = LOCAL_SPTODAY_CACHE_PATH,
) -> SYPQuote | None:
    """Tier 3 fallback: read last-known-good SYP quote from Upstash `ratepath:sptoday_last_good`."""
    cfg = settings or Settings.load()
    raw_json_str: str | None = None

    if cfg.has_live_upstash:
        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
        try:
            endpoint = cfg.upstash_redis_rest_url.rstrip("/")
            resp = await http_client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {cfg.upstash_redis_rest_token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(
                    ["GET", SPTODAY_REDIS_LAST_GOOD_KEY], ensure_ascii=False
                ).encode("utf-8"),
            )
            if resp.status_code == 200:
                val = json.loads(resp.text).get("result")
                if isinstance(val, str) and val.strip():
                    raw_json_str = val
        except Exception:
            pass
        finally:
            if owns_client:
                await http_client.aclose()

    if raw_json_str is None and local_cache_path.exists():
        try:
            raw_json_str = local_cache_path.read_text(encoding="utf-8")
        except Exception:
            raw_json_str = None

    if not raw_json_str:
        return None

    try:
        obj = json.loads(raw_json_str)
        if not isinstance(obj, dict):
            return None
        return _build_verified_syp_quote(
            obj.get("buy_rate"),
            obj.get("sell_rate"),
            source="upstash_sptoday_last_good",
            updated_at=str(obj.get("updated_at") or utc_now_iso()),
        )
    except Exception:
        return None


async def fetch_syp_sptoday_3tier(
    client: httpx.AsyncClient | None = None,
    *,
    settings: Settings | None = None,
    local_cache_path: Path = LOCAL_SPTODAY_CACHE_PATH,
) -> SYPQuote | None:
    """Fetch verified Damascus USD/SYP via Tier 1 (sp-today web) -> Tier 2 (t.me/s) -> Tier 3 (Redis)."""
    cfg = settings or Settings.load()
    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        headers=_BROWSER_HEADERS, follow_redirects=True, timeout=httpx.Timeout(10)
    )

    try:
        # Tier 1: Direct SP-Today SSR pages (`/en` & `/en/currency/us_dollar`)
        for web_url in (SPTODAY_HOME_URL, SPTODAY_USD_URL):
            try:
                resp = await http_client.get(
                    web_url, headers=_BROWSER_HEADERS, timeout=httpx.Timeout(10)
                )
                if resp.status_code == 200:
                    quote = parse_sptoday_web_html(resp.text)
                    if quote is not None:
                        await write_sptoday_last_good_cache(
                            quote, settings=cfg, client=http_client, local_cache_path=local_cache_path
                        )
                        return quote
            except Exception:
                continue

        # Tier 2: Public Telegram web preview (`https://t.me/s/sp_today`)
        for tg_url in SPTODAY_TELEGRAM_URLS:
            try:
                resp = await http_client.get(
                    tg_url, headers=_BROWSER_HEADERS, timeout=httpx.Timeout(10)
                )
                if resp.status_code == 200:
                    quote = parse_sptoday_telegram_html(resp.text)
                    if quote is not None:
                        await write_sptoday_last_good_cache(
                            quote, settings=cfg, client=http_client, local_cache_path=local_cache_path
                        )
                        return quote
            except Exception:
                continue

        # Tier 3: Upstash Redis `ratepath:sptoday_last_good` (NEVER open.er-api)
        return await read_sptoday_last_good_cache(
            settings=cfg, client=http_client, local_cache_path=local_cache_path
        )
    finally:
        if owns_client:
            await http_client.aclose()


if __name__ == "__main__":
    enforce_utf8_runtime()
    res = asyncio.run(fetch_syp_sptoday_3tier())
    if res is None:
        print(json.dumps({"ok": False, "error": "syp_unavailable_across_3_tiers"}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps({"ok": True, **res.to_dict()}, ensure_ascii=False, indent=2))
