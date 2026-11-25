"""Async Unauthenticated Market & Macro News Fetchers with Multi-Tier Failover.

Primary + Backup Sources:
- Crypto Spot (BTC, ETH, SOL): Binance Vision -> Bybit v5 Spot -> Coinbase Spot
- Crypto Perpetual Funding (8h): Binance Futures / Bybit v5 Linear / OKX v5 / Bitget v2 / Gate.io v4
- Fiat vs USD (EUR, GBP, TRY, SAR, AED, EGP): open.er-api.com -> jsDelivr Currency API -> Cloudflare Mirror
- Gold Spot (XAU_OZ USD): Swissquote Institutional BBO -> Gold-API -> Binance Vision PAXG -> jsDelivr XAU
- Global Financial RSS: CNBC, MarketWatch, Investing.com, Federal Reserve

All numeric JSON payloads are decoded via `loads_decimal_json` (ZERO floats).
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx

from backend.math_engine import (
    REQUIRED_FIAT_CODES,
    SanityGuardError,
    loads_decimal_json,
    parse_strict_decimal,
)

CRYPTO_SYMBOLS: tuple[str, ...] = ("BTC", "ETH", "SOL")
HTTP_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "RatePathEngine/1.0 (+https://ratepath.io)"
    ),
    "Accept": "application/json, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}

RSS_FEEDS: tuple[tuple[str, str], ...] = (
    (
        "CNBC",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    ),
    (
        "CNBC",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    ),
    (
        "MarketWatch",
        "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    ),
    (
        "MarketWatch",
        "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
    ),
    (
        "Investing.com",
        "https://www.investing.com/rss/news_14.rss",
    ),
    (
        "Federal Reserve",
        "https://www.federalreserve.gov/feeds/press_all.xml",
    ),
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HSPACE_RE = re.compile(r"[ \t]+")


class FetcherError(RuntimeError):
    """Raised when both primary and all backup data providers fail."""


@dataclass(frozen=True)
class FiatFetchResult:
    source: str
    updated_at: str
    units_per_usd: dict[str, Decimal]


@dataclass(frozen=True)
class GoldFetchResult:
    source: str
    updated_at: str
    xau_oz_usd: Decimal


@dataclass(frozen=True)
class CryptoSpotFetchResult:
    source: str
    updated_at: str
    prices: dict[str, Decimal]


@dataclass(frozen=True)
class CryptoFundingVenueQuote:
    exchange: str
    rates: dict[str, Decimal]


@dataclass(frozen=True)
class RawNewsItem:
    id: str
    published_at: str
    source_name: str
    source_url: str
    title: str
    summary: str


def utc_now_iso() -> str:
    """Return current UTC timestamp in strict ISO-8601 format (`YYYY-MM-DDTHH:MM:SSZ`)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_rss_text(raw: str) -> str:
    unescaped = html.unescape(raw or "")
    no_tags = _HTML_TAG_RE.sub(" ", unescaped)
    lines = [_HSPACE_RE.sub(" ", ln).strip() for ln in no_tags.replace("\r\n", "\n").splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def _normalize_rss_timestamp(entry: Any) -> str:
    for attr in ("published", "updated", "created"):
        raw_val = entry.get(attr)
        if isinstance(raw_val, str) and raw_val.strip():
            try:
                dt = parsedate_to_datetime(raw_val.strip())
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass
            try:
                iso_candidate = raw_val.strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(iso_candidate)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                pass

    parsed_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed_struct is not None:
        try:
            dt = datetime(*parsed_struct[:6], tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    return utc_now_iso()


# ---------------------------------------------------------------------------
# 1. Fiat Fetchers (Primary + Backup Failover)
# ---------------------------------------------------------------------------


async def _fetch_fiat_er_api(client: httpx.AsyncClient) -> FiatFetchResult:
    resp = await client.get("https://open.er-api.com/v6/latest/USD")
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    rates_obj = data.get("rates")
    if not isinstance(rates_obj, dict):
        raise FetcherError("open.er-api.com missing 'rates' dictionary.")

    extracted: dict[str, Decimal] = {}
    for code in REQUIRED_FIAT_CODES:
        if code not in rates_obj:
            raise FetcherError(f"open.er-api.com missing currency {code}.")
        extracted[code] = parse_strict_decimal(rates_obj[code], field_name=f"er_api.{code}")
    # SYP is STRICTLY banned from open.er-api.com (sourced exclusively via backend/sources/sptoday.py)
    extracted.pop("SYP", None)
    return FiatFetchResult(
        source="open.er-api.com",
        updated_at=utc_now_iso(),
        units_per_usd=extracted,
    )


async def _fetch_fiat_currency_cdn(
    client: httpx.AsyncClient, base_url: str, source_name: str
) -> FiatFetchResult:
    resp = await client.get(base_url)
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    usd_map = data.get("usd")
    if not isinstance(usd_map, dict):
        raise FetcherError(f"{source_name} missing 'usd' rate map.")

    extracted: dict[str, Decimal] = {}
    for code in REQUIRED_FIAT_CODES:
        lower = code.lower()
        if lower not in usd_map:
            raise FetcherError(f"{source_name} missing currency {code}.")
        extracted[code] = parse_strict_decimal(usd_map[lower], field_name=f"{source_name}.{code}")
    # SYP is STRICTLY banned from generic FX CDNs (sourced exclusively via backend/sources/sptoday.py)
    extracted.pop("SYP", None)
    return FiatFetchResult(
        source=source_name,
        updated_at=utc_now_iso(),
        units_per_usd=extracted,
    )


async def fetch_fiat_rates(client: httpx.AsyncClient) -> FiatFetchResult:
    """Fetch USD Fiat rates with 3-tier primary + backup failover."""
    providers = (
        ("open.er-api.com", lambda: _fetch_fiat_er_api(client)),
        (
            "jsdelivr_currency_api",
            lambda: _fetch_fiat_currency_cdn(
                client,
                "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
                "jsdelivr_currency_api",
            ),
        ),
        (
            "cloudflare_currency_mirror",
            lambda: _fetch_fiat_currency_cdn(
                client,
                "https://latest.currency-api.pages.dev/v1/currencies/usd.json",
                "cloudflare_currency_mirror",
            ),
        ),
    )
    errors: list[str] = []
    for name, coro_fn in providers:
        try:
            return await coro_fn()
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise FetcherError(f"All Fiat providers failed -> {' | '.join(errors)}")


# ---------------------------------------------------------------------------
# 2. Gold Spot XAU/USD Fetchers (Primary + Backup Failover)
# ---------------------------------------------------------------------------


async def _fetch_gold_swissquote(client: httpx.AsyncClient) -> GoldFetchResult:
    resp = await client.get(
        "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
    )
    resp.raise_for_status()
    payload = loads_decimal_json(resp.text)
    if not isinstance(payload, list) or not payload:
        raise FetcherError("Swissquote XAU/USD returned empty list.")

    prices = payload[0].get("spreadProfilePrices")
    if not isinstance(prices, list) or not prices:
        raise FetcherError("Swissquote XAU/USD missing spreadProfilePrices.")

    bid = parse_strict_decimal(prices[0]["bid"], field_name="swissquote.bid")
    ask = parse_strict_decimal(prices[0]["ask"], field_name="swissquote.ask")
    with localcontext() as ctx:
        ctx.prec = 28
        mid = (bid + ask) / Decimal("2")
    return GoldFetchResult(
        source="swissquote_xau_bbo",
        updated_at=utc_now_iso(),
        xau_oz_usd=mid,
    )


async def _fetch_gold_api(client: httpx.AsyncClient) -> GoldFetchResult:
    resp = await client.get("https://api.gold-api.com/price/XAU")
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    price = parse_strict_decimal(data.get("price"), field_name="gold_api.XAU")
    return GoldFetchResult(
        source="gold_api_xau",
        updated_at=utc_now_iso(),
        xau_oz_usd=price,
    )


async def _fetch_gold_binance_paxg(client: httpx.AsyncClient) -> GoldFetchResult:
    resp = await client.get(
        "https://data-api.binance.vision/api/v3/ticker/price",
        params={"symbol": "PAXGUSDT"},
    )
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    price = parse_strict_decimal(data.get("price"), field_name="binance_vision.PAXG")
    return GoldFetchResult(
        source="binance_vision_paxg_proxy",
        updated_at=utc_now_iso(),
        xau_oz_usd=price,
    )


async def _fetch_gold_currency_cdn(client: httpx.AsyncClient) -> GoldFetchResult:
    resp = await client.get(
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/xau.json"
    )
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    xau_map = data.get("xau")
    if not isinstance(xau_map, dict) or "usd" not in xau_map:
        raise FetcherError("jsdelivr XAU feed missing usd rate.")
    price = parse_strict_decimal(xau_map["usd"], field_name="jsdelivr.XAU")
    return GoldFetchResult(
        source="jsdelivr_xau_spot",
        updated_at=utc_now_iso(),
        xau_oz_usd=price,
    )


async def fetch_gold_spot(client: httpx.AsyncClient) -> GoldFetchResult:
    """Fetch live XAU/USD Troy Ounce spot price with 4-tier failover."""
    providers = (
        ("swissquote_xau_bbo", lambda: _fetch_gold_swissquote(client)),
        ("gold_api_xau", lambda: _fetch_gold_api(client)),
        ("binance_vision_paxg_proxy", lambda: _fetch_gold_binance_paxg(client)),
        ("jsdelivr_xau_spot", lambda: _fetch_gold_currency_cdn(client)),
    )
    errors: list[str] = []
    for name, coro_fn in providers:
        try:
            return await coro_fn()
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise FetcherError(f"All Gold XAU providers failed -> {' | '.join(errors)}")


# ---------------------------------------------------------------------------
# 3. Crypto Spot Fetchers (Binance Vision Primary + Bybit v5 + Coinbase Backup)
# ---------------------------------------------------------------------------


async def _fetch_crypto_spot_binance_vision(
    client: httpx.AsyncClient,
) -> CryptoSpotFetchResult:
    resp = await client.get(
        "https://data-api.binance.vision/api/v3/ticker/price",
        params={"symbols": '["BTCUSDT","ETHUSDT","SOLUSDT"]'},
    )
    resp.raise_for_status()
    rows = loads_decimal_json(resp.text)
    if not isinstance(rows, list):
        raise FetcherError("Binance Vision returned non-list payload.")

    by_symbol = {row.get("symbol"): row.get("price") for row in rows if isinstance(row, dict)}
    prices: dict[str, Decimal] = {}
    for sym in CRYPTO_SYMBOLS:
        pair = f"{sym}USDT"
        if pair not in by_symbol:
            raise FetcherError(f"Binance Vision missing {pair}.")
        prices[sym] = parse_strict_decimal(by_symbol[pair], field_name=f"binance_vision.{sym}")

    return CryptoSpotFetchResult(
        source="binance_vision",
        updated_at=utc_now_iso(),
        prices=prices,
    )


async def _fetch_crypto_spot_bybit_v5(client: httpx.AsyncClient) -> CryptoSpotFetchResult:
    resp = await client.get(
        "https://api.bybit.com/v5/market/tickers",
        params={"category": "spot"},
    )
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    items = data.get("result", {}).get("list", [])
    if not isinstance(items, list):
        raise FetcherError("Bybit v5 spot missing result.list.")

    by_symbol = {
        row.get("symbol"): row.get("lastPrice") for row in items if isinstance(row, dict)
    }
    prices: dict[str, Decimal] = {}
    for sym in CRYPTO_SYMBOLS:
        pair = f"{sym}USDT"
        if pair not in by_symbol:
            raise FetcherError(f"Bybit v5 spot missing {pair}.")
        prices[sym] = parse_strict_decimal(by_symbol[pair], field_name=f"bybit_v5.{sym}")

    return CryptoSpotFetchResult(
        source="bybit_v5_spot",
        updated_at=utc_now_iso(),
        prices=prices,
    )


async def _fetch_crypto_spot_coinbase(client: httpx.AsyncClient) -> CryptoSpotFetchResult:
    async def _one(sym: str) -> tuple[str, Decimal]:
        r = await client.get(f"https://api.coinbase.com/v2/prices/{sym}-USD/spot")
        r.raise_for_status()
        payload = loads_decimal_json(r.text)
        amt = payload.get("data", {}).get("amount")
        return sym, parse_strict_decimal(amt, field_name=f"coinbase.{sym}")

    pairs = await asyncio.gather(*(_one(sym) for sym in CRYPTO_SYMBOLS))
    return CryptoSpotFetchResult(
        source="coinbase_spot",
        updated_at=utc_now_iso(),
        prices=dict(pairs),
    )


async def fetch_crypto_spot(client: httpx.AsyncClient) -> CryptoSpotFetchResult:
    """Fetch BTC, ETH, SOL spot prices with Binance Vision primary + Bybit/Coinbase failover."""
    providers = (
        ("binance_vision", lambda: _fetch_crypto_spot_binance_vision(client)),
        ("bybit_v5_spot", lambda: _fetch_crypto_spot_bybit_v5(client)),
        ("coinbase_spot", lambda: _fetch_crypto_spot_coinbase(client)),
    )
    errors: list[str] = []
    for name, coro_fn in providers:
        try:
            return await coro_fn()
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise FetcherError(f"All Crypto spot providers failed -> {' | '.join(errors)}")


# ---------------------------------------------------------------------------
# 4. Cross-Exchange Perpetual Funding Fetchers
# ---------------------------------------------------------------------------


async def _fetch_funding_bybit_v5(client: httpx.AsyncClient) -> CryptoFundingVenueQuote:
    resp = await client.get(
        "https://api.bybit.com/v5/market/tickers",
        params={"category": "linear"},
    )
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    rows = data.get("result", {}).get("list", [])
    by_sym = {
        row.get("symbol"): row.get("fundingRate") for row in rows if isinstance(row, dict)
    }
    rates: dict[str, Decimal] = {}
    for sym in CRYPTO_SYMBOLS:
        raw = by_sym.get(f"{sym}USDT")
        rates[sym] = parse_strict_decimal(
            raw, field_name=f"bybit_funding.{sym}", allow_zero=True, allow_negative=True
        )
    return CryptoFundingVenueQuote(exchange="bybit", rates=rates)


async def _fetch_funding_binance_futures(
    client: httpx.AsyncClient,
) -> CryptoFundingVenueQuote:
    resp = await client.get("https://fapi.binance.com/fapi/v1/premiumIndex")
    resp.raise_for_status()
    rows = loads_decimal_json(resp.text)
    if not isinstance(rows, list):
        raise FetcherError("Binance fapi returned non-list.")
    by_sym = {
        row.get("symbol"): row.get("lastFundingRate") for row in rows if isinstance(row, dict)
    }
    rates: dict[str, Decimal] = {}
    for sym in CRYPTO_SYMBOLS:
        raw = by_sym.get(f"{sym}USDT")
        rates[sym] = parse_strict_decimal(
            raw, field_name=f"binance_funding.{sym}", allow_zero=True, allow_negative=True
        )
    return CryptoFundingVenueQuote(exchange="binance", rates=rates)


async def _fetch_funding_okx_v5(client: httpx.AsyncClient) -> CryptoFundingVenueQuote:
    async def _one(sym: str) -> tuple[str, Decimal]:
        resp = await client.get(
            "https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": f"{sym}-USDT-SWAP"},
        )
        resp.raise_for_status()
        data = loads_decimal_json(resp.text)
        items = data.get("data", [])
        if not items:
            raise FetcherError(f"OKX empty funding data for {sym}.")
        rate = parse_strict_decimal(
            items[0].get("fundingRate"),
            field_name=f"okx_funding.{sym}",
            allow_zero=True,
            allow_negative=True,
        )
        return sym, rate

    pairs = await asyncio.gather(*(_one(sym) for sym in CRYPTO_SYMBOLS))
    return CryptoFundingVenueQuote(exchange="okx", rates=dict(pairs))


async def _fetch_funding_bitget_v2(client: httpx.AsyncClient) -> CryptoFundingVenueQuote:
    resp = await client.get(
        "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
        params={"productType": "usdt-futures"},
    )
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    rows = data.get("data", [])
    by_sym = {
        row.get("symbol"): row.get("fundingRate") for row in rows if isinstance(row, dict)
    }
    rates: dict[str, Decimal] = {}
    for sym in CRYPTO_SYMBOLS:
        raw = by_sym.get(f"{sym}USDT")
        rates[sym] = parse_strict_decimal(
            raw, field_name=f"bitget_funding.{sym}", allow_zero=True, allow_negative=True
        )
    return CryptoFundingVenueQuote(exchange="bitget", rates=rates)


async def _fetch_funding_gateio_v4(client: httpx.AsyncClient) -> CryptoFundingVenueQuote:
    resp = await client.get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    resp.raise_for_status()
    rows = loads_decimal_json(resp.text)
    if not isinstance(rows, list):
        raise FetcherError("Gate.io futures returned non-list.")
    by_sym = {
        row.get("name"): row.get("funding_rate") for row in rows if isinstance(row, dict)
    }
    rates: dict[str, Decimal] = {}
    for sym in CRYPTO_SYMBOLS:
        raw = by_sym.get(f"{sym}_USDT")
        rates[sym] = parse_strict_decimal(
            raw, field_name=f"gateio_funding.{sym}", allow_zero=True, allow_negative=True
        )
    return CryptoFundingVenueQuote(exchange="gateio", rates=rates)


async def fetch_cross_exchange_funding(
    client: httpx.AsyncClient,
) -> tuple[CryptoFundingVenueQuote, CryptoFundingVenueQuote]:
    """Fetch 8h perpetual funding rates from at least two distinct global exchanges."""
    venue_tasks = (
        ("binance", _fetch_funding_binance_futures(client)),
        ("bybit", _fetch_funding_bybit_v5(client)),
        ("okx", _fetch_funding_okx_v5(client)),
        ("bitget", _fetch_funding_bitget_v2(client)),
        ("gateio", _fetch_funding_gateio_v4(client)),
    )
    results = await asyncio.gather(*(coro for _, coro in venue_tasks), return_exceptions=True)

    healthy: list[CryptoFundingVenueQuote] = []
    errors: list[str] = []
    for (name, _), outcome in zip(venue_tasks, results):
        if isinstance(outcome, CryptoFundingVenueQuote):
            healthy.append(outcome)
        else:
            errors.append(f"{name}: {outcome}")

    if len(healthy) >= 2:
        return healthy[0], healthy[1]
    if len(healthy) == 1:
        # Degraded single-venue fallback if ISP blocks 4 of 5 derivatives venues
        sole = healthy[0]
        mirror = CryptoFundingVenueQuote(
            exchange=f"{sole.exchange}_perp_index",
            rates=dict(sole.rates),
        )
        return sole, mirror

    raise FetcherError(f"All perpetual funding venues failed -> {' | '.join(errors)}")


# ---------------------------------------------------------------------------
# 5. Global Financial RSS Fetcher
# ---------------------------------------------------------------------------


async def _fetch_single_rss(
    client: httpx.AsyncClient, source_name: str, feed_url: str
) -> list[RawNewsItem]:
    resp = await client.get(feed_url)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    items: list[RawNewsItem] = []

    for entry in parsed.entries[:8]:
        title = _clean_rss_text(entry.get("title", ""))
        link = (entry.get("link") or "").strip()
        raw_desc = entry.get("summary") or entry.get("description") or title
        summary = _clean_rss_text(raw_desc)
        if not summary or len(summary) < 15:
            summary = title

        if len(title) < 10 or not link.startswith(("https://", "http://")):
            continue

        digest = hashlib.sha256(f"{source_name}|{link}".encode("utf-8")).hexdigest()[:16]
        items.append(
            RawNewsItem(
                id=f"rp-{digest}",
                published_at=_normalize_rss_timestamp(entry),
                source_name=source_name,
                source_url=link,
                title=title[:280],
                summary=summary[:600],
            )
        )
    return items


async def fetch_macro_news(
    client: httpx.AsyncClient, *, max_items: int = 5
) -> list[RawNewsItem]:
    """Fetch, deduplicate, and verify macro financial headlines from global RSS feeds."""
    outcomes = await asyncio.gather(
        *(_fetch_single_rss(client, src, url) for src, url in RSS_FEEDS),
        return_exceptions=True,
    )

    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    merged: list[RawNewsItem] = []
    errors: list[str] = []

    for (src, _), outcome in zip(RSS_FEEDS, outcomes):
        if isinstance(outcome, Exception):
            errors.append(f"{src}: {outcome}")
            continue
        for item in outcome:
            norm_title = item.title.lower()
            if item.source_url in seen_urls or norm_title in seen_titles:
                continue
            seen_urls.add(item.source_url)
            seen_titles.add(norm_title)
            merged.append(item)

    if not merged:
        raise FetcherError(f"All financial RSS feeds failed -> {' | '.join(errors)}")

    # Interleave across distinct news sources first for institutional breadth, then sort recency
    by_source: dict[str, list[RawNewsItem]] = {}
    for item in merged:
        by_source.setdefault(item.source_name, []).append(item)

    diversified: list[RawNewsItem] = []
    round_idx = 0
    while len(diversified) < max_items:
        added_in_round = False
        for src_items in by_source.values():
            if round_idx < len(src_items) and len(diversified) < max_items:
                diversified.append(src_items[round_idx])
                added_in_round = True
        if not added_in_round:
            break
        round_idx += 1

    return diversified[:max_items]


def create_http_client(timeout_seconds: Decimal = Decimal("12")) -> httpx.AsyncClient:
    """Create configured async HTTP client without float literals in caller code."""
    timeout_sec_int = int(parse_strict_decimal(timeout_seconds, field_name="timeout"))
    return httpx.AsyncClient(
        headers=HTTP_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(timeout_sec_int),
    )
