"""CoinMarketCap Top-300 + Global 155+ Currencies + Silver + SP-Today SYP Catalog Builder.

Architectural Guarantees:
- ZERO Python `float` instances (strict `Decimal` via `loads_decimal_json`).
- Multi-tier resilience: CMC Pro (optional `CMC_PRO_API_KEY`) -> CMC Public v3 -> CoinGecko -> Binance.
- Full 155+ global & Arab fiat catalog (`open.er-api.com` -> jsdelivr -> Cloudflare mirror)
  enriched with official `sp-today` Damascus USD/SYP market rate and live Silver (`XAG`).
- Explicit UTF-8 JSON (`ensure_ascii=False`) atomic publish to `frontend/public/catalog_feed.json`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from backend.config import (
    CATALOG_FEED_TARGET_PATH,
    Settings,
    enforce_utf8_runtime,
    is_placeholder_secret,
    sanitize_syria_flag_emoji,
)
from backend.fetchers import create_http_client, utc_now_iso
from backend.math_engine import (
    QUANT_2DP,
    QUANT_4DP,
    QUANT_6DP,
    QUANT_8DP,
    TROY_OUNCE_GRAMS,
    compute_gold_prices,
    format_decimal,
    loads_decimal_json,
    parse_strict_decimal,
)
from backend.sources.sptoday import fetch_syp_sptoday_3tier, parse_sptoday_web_html

CMC_PUBLIC_LISTING_URL: str = (
    "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing"
)
CMC_PRO_LISTING_URL: str = (
    "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
)
CMC_LOGO_TEMPLATE: str = "https://s2.coinmarketcap.com/static/img/coins/64x64/{cmc_id}.png"
COINGECKO_MARKETS_URL: str = "https://api.coingecko.com/api/v3/coins/markets"
BINANCE_TICKER_24H_URL: str = "https://data-api.binance.vision/api/v3/ticker/24hr"
SP_TODAY_USD_URL: str = "https://sp-today.com/en/currency/us_dollar"
SWISSQUOTE_XAG_URL: str = (
    "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAG/USD"
)
GOLDPRICE_ORG_URL: str = "https://data-asg.goldprice.org/dbXRates/USD"

# Core symbols that share canonical IDs with `live_feed.json`
CORE_CRYPTO_IDS: dict[str, str] = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}

# Well-known CMC IDs for clean icon URLs if fallback exchanges are ever used
KNOWN_CMC_IDS: dict[str, int] = {
    "BTC": 1,
    "ETH": 1027,
    "USDT": 825,
    "XRP": 52,
    "BNB": 1839,
    "SOL": 5426,
    "USDC": 3408,
    "DOGE": 74,
    "ADA": 2010,
    "TRX": 1958,
    "TON": 11419,
    "AVAX": 5805,
    "SHIB": 5994,
    "LINK": 1975,
    "DOT": 6636,
    "BCH": 1831,
    "LTC": 2,
    "UNI": 7083,
    "XLM": 512,
    "NEAR": 6535,
    "SUI": 20947,
    "APT": 21794,
    "PEPE": 24478,
    "PAXG": 4705,
    "ARB": 11841,
    "OP": 11840,
}

# Comprehensive Bilingual (EN / AR) Currency Metadata for Arab + Major Global Currencies
# Tuple format: (name_en, name_ar, symbol)
CURRENCY_METADATA: dict[str, tuple[str, str, str]] = {
    # Core & Arab World
    "USD": ("US Dollar", "الدولار الأمريكي", "$"),
    "EUR": ("Euro", "اليورو الأوروبي", "€"),
    "GBP": ("British Pound", "الجنيه الإسترليني", "£"),
    "SAR": ("Saudi Riyal", "الريال السعودي", "﷼"),
    "AED": ("UAE Dirham", "الدرهم الإماراتي", "د.إ"),
    "EGP": ("Egyptian Pound", "الجنيه المصري", "E£"),
    "TRY": ("Turkish Lira", "الليرة التركية", "₺"),
    "SYP": ("Syrian Pound", "الليرة السورية", "ل.س"),
    "JOD": ("Jordanian Dinar", "الدينار الأردني", "د.أ"),
    "IQD": ("Iraqi Dinar", "الدينار العراقي", "د.ع"),
    "KWD": ("Kuwaiti Dinar", "الدينار الكويتي", "د.ك"),
    "QAR": ("Qatari Riyal", "الريال القطري", "ر.ق"),
    "BHD": ("Bahraini Dinar", "الدينار البحريني", "د.ب"),
    "OMR": ("Omani Rial", "الريال العماني", "ر.ع"),
    "LBP": ("Lebanese Pound", "الليرة اللبنانية", "ل.ل"),
    "DZD": ("Algerian Dinar", "الدينار الجزائري", "د.ج"),
    "MAD": ("Moroccan Dirham", "الدرهم المغربي", "د.م"),
    "TND": ("Tunisian Dinar", "الدينار التونسي", "د.ت"),
    "LYD": ("Libyan Dinar", "الدينار الليبي", "د.ل"),
    "YER": ("Yemeni Rial", "الريال اليمني", "ر.ي"),
    "SDG": ("Sudanese Pound", "الجنيه السوداني", "ج.س"),
    "MRU": ("Mauritanian Ouguiya", "الأوقية الموريتانية", "أ.م"),
    "DJF": ("Djiboutian Franc", "الفرنك الجيبوتي", "ف.ج"),
    "KMF": ("Comorian Franc", "الفرنك القمري", "ف.ق"),
    "SOS": ("Somali Shilling", "الشلن الصومالي", "ش.ص"),
    # Major Global & G20
    "CHF": ("Swiss Franc", "الفرنك السويسري", "CHF"),
    "CAD": ("Canadian Dollar", "الدولار الكندي", "C$"),
    "AUD": ("Australian Dollar", "الدولار الأسترالي", "A$"),
    "NZD": ("New Zealand Dollar", "الدولار النيوزيلندي", "NZ$"),
    "JPY": ("Japanese Yen", "الين الياباني", "¥"),
    "CNY": ("Chinese Yuan", "اليوان الصيني", "¥"),
    "HKD": ("Hong Kong Dollar", "دولار هونغ كونغ", "HK$"),
    "SGD": ("Singapore Dollar", "الدولار السنغافوري", "S$"),
    "TWD": ("New Taiwan Dollar", "الدولار التايواني", "NT$"),
    "KRW": ("South Korean Won", "الوون الكوري الجنوبي", "₩"),
    "INR": ("Indian Rupee", "الروبية الهندية", "₹"),
    "PKR": ("Pakistani Rupee", "الروبية الباكستانية", "₨"),
    "BDT": ("Bangladeshi Taka", "التاكا البنغلاديشية", "৳"),
    "IDR": ("Indonesian Rupiah", "الروبية الإندونيسية", "Rp"),
    "MYR": ("Malaysian Ringgit", "الرينغيت الماليزي", "RM"),
    "THB": ("Thai Baht", "البات التايلاندي", "฿"),
    "VND": ("Vietnamese Dong", "الدونغ الفيتنامي", "₫"),
    "PHP": ("Philippine Peso", "البيزو الفلبيني", "₱"),
    "BRL": ("Brazilian Real", "الريال البرازيلي", "R$"),
    "MXN": ("Mexican Peso", "البيزو المكسيكي", "MX$"),
    "ARS": ("Argentine Peso", "البيزو الأرجنتيني", "AR$"),
    "CLP": ("Chilean Peso", "البيزو التشيلي", "CL$"),
    "COP": ("Colombian Peso", "البيزو الكولومبي", "CO$"),
    "PEN": ("Peruvian Sol", "السول البيروفي", "S/"),
    "ZAR": ("South African Rand", "الراند الجنوب أفريقي", "R"),
    "NGN": ("Nigerian Naira", "النيرة النيجيرية", "₦"),
    "KES": ("Kenyan Shilling", "الشلن الكيني", "KSh"),
    "GHS": ("Ghanaian Cedi", "السيدي الغاني", "₵"),
    "ETB": ("Ethiopian Birr", "البير الإثيوبي", "Br"),
    "SEK": ("Swedish Krona", "الكرونة السويدية", "kr"),
    "NOK": ("Norwegian Krone", "الكرونة النرويجية", "kr"),
    "DKK": ("Danish Krone", "الكرونة الدنماركية", "kr"),
    "PLN": ("Polish Zloty", "الزلوتي البولندي", "zł"),
    "CZK": ("Czech Koruna", "الكرونة التشيكية", "Kč"),
    "HUF": ("Hungarian Forint", "الفورنت المجري", "Ft"),
    "RON": ("Romanian Leu", "الليو الروماني", "lei"),
    "BGN": ("Bulgarian Lev", "الليف البلغاري", "лв"),
    "RUB": ("Russian Ruble", "الروبل الروسي", "₽"),
    "UAH": ("Ukrainian Hryvnia", "الهريفنيا الأوكرانية", "₴"),
    "ISK": ("Icelandic Krona", "الكرونة الأيسلندية", "kr"),
    "RSD": ("Serbian Dinar", "الدينار الصربي", "дин"),
    "BAM": ("Bosnia Convertible Mark", "المارك البوسني", "KM"),
    "ALL": ("Albanian Lek", "الليك الألباني", "L"),
    "GEL": ("Georgian Lari", "اللاري الجورجي", "₾"),
    "AZN": ("Azerbaijani Manat", "المانات الأذربيجاني", "₼"),
    "AMD": ("Armenian Dram", "الدرام الأرميني", "֏"),
    "KZT": ("Kazakhstani Tenge", "التينغ الكازاخستاني", "₸"),
    "UZS": ("Uzbekistani Som", "السوم الأوزبكي", "so'm"),
    "IRR": ("Iranian Rial", "الريال الإيراني", "﷼"),
    "AFN": ("Afghan Afghani", "الأفغاني", "؋"),
    "LKR": ("Sri Lankan Rupee", "الروبية السريلانكية", "Rs"),
    "NPR": ("Nepalese Rupee", "الروبية النيبالية", "Rs"),
    "ILS": ("Israeli Shekel", "الشيكل", "₪"),
}

_ISO_CODE_RE = re.compile(r"^[A-Z]{3}$")
_SP_BUY_RE = re.compile(
    r">Buy</p>\s*<p[^>]*>\s*<span[^>]*>\s*([0-9][0-9,\.]*)\s*</span>",
    re.IGNORECASE | re.DOTALL,
)
_SP_SELL_RE = re.compile(
    r">Sell</p>\s*<p[^>]*>\s*<span[^>]*>\s*([0-9][0-9,\.]*)\s*</span>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class CMCCatalogResult:
    """Normalized Top-N cryptocurrency catalog from CMC / CoinGecko / Binance."""

    source: str
    updated_at: str
    coins: list[dict[str, Any]]


def _assert_zero_floats_recursive(node: Any, path: str = "$") -> None:
    if isinstance(node, float):
        raise ValueError(f"Forbidden Python float {node!r} detected at {path}.")
    if isinstance(node, dict):
        for k, v in node.items():
            _assert_zero_floats_recursive(v, f"{path}.{k}")
    elif isinstance(node, (list, tuple)):
        for idx, item in enumerate(node):
            _assert_zero_floats_recursive(item, f"{path}[{idx}]")


def format_adaptive_usd_price(price: Decimal) -> str:
    """Format USD token price using adaptive Decimal precision so micro-cap tokens never round to 0."""
    if price >= Decimal("1"):
        return format_decimal(price, QUANT_2DP)
    if price >= Decimal("0.01"):
        return format_decimal(price, QUANT_4DP)
    if price >= Decimal("0.0001"):
        return format_decimal(price, QUANT_6DP)
    return format_decimal(price, QUANT_8DP)


def format_signed_pct(raw_val: Any) -> str:
    """Format signed 24h percentage change as exact 2dp Decimal string (e.g. '+2.45' or '-1.10')."""
    if raw_val is None or str(raw_val).strip() == "":
        return "0.00"
    try:
        dec = Decimal(str(raw_val).strip())
        if not dec.is_finite():
            return "0.00"
        q = format_decimal(dec, QUANT_2DP)
        if dec > Decimal("0") and not q.startswith("+"):
            return f"+{q}"
        return q
    except Exception:
        return "0.00"


def _normalize_cmc_entry(
    *,
    cmc_id: int | None,
    symbol: str,
    name: str,
    rank: int,
    price_dec: Decimal,
    change_24h_raw: Any,
    logo_url: str | None = None,
) -> dict[str, Any]:
    sym = sanitize_syria_flag_emoji(symbol.strip().upper(), lang="en")
    clean_name = sanitize_syria_flag_emoji(name.strip(), lang="en")
    # Preserve canonical IDs for BTC / ETH / SOL so live_feed & catalog_feed merge seamlessly
    if sym in CORE_CRYPTO_IDS and rank <= 25:
        asset_id = CORE_CRYPTO_IDS[sym]
    elif cmc_id is not None:
        asset_id = f"CMC_{cmc_id}"
    else:
        asset_id = f"CRYPTO_{sym}"

    resolved_logo = (
        logo_url
        if logo_url
        else (CMC_LOGO_TEMPLATE.format(cmc_id=cmc_id) if cmc_id is not None else "")
    )
    return {
        "id": asset_id,
        "cmc_id": cmc_id,
        "symbol": sym,
        "name": clean_name,
        "rank": int(rank),
        "price_usd": format_adaptive_usd_price(price_dec),
        "change_24h_pct": format_signed_pct(change_24h_raw),
        "logo_url": resolved_logo,
        "category": "crypto",
    }


def parse_cmc_public_listing(payload: dict[str, Any], *, limit: int = 300) -> list[dict[str, Any]]:
    """Parse CoinMarketCap public v3 listing JSON (Decimal-decoded) into normalized coin records."""
    raw_list = payload.get("data", {}).get("cryptoCurrencyList", [])
    if not isinstance(raw_list, list):
        return []

    coins: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(raw_list[:limit], start=1):
        if not isinstance(item, dict):
            continue
        quotes = item.get("quotes")
        if not isinstance(quotes, list) or not quotes:
            continue
        usd_quote = next(
            (q for q in quotes if isinstance(q, dict) and str(q.get("name", "")).upper() == "USD"),
            quotes[0],
        )
        if not isinstance(usd_quote, dict) or usd_quote.get("price") is None:
            continue
        try:
            price_dec = parse_strict_decimal(
                usd_quote["price"], field_name=f"cmc.{item.get('symbol')}.price"
            )
        except Exception:
            continue

        cmc_id = int(item["id"]) if item.get("id") is not None else None
        rank = int(item.get("cmcRank") or idx)
        entry = _normalize_cmc_entry(
            cmc_id=cmc_id,
            symbol=str(item.get("symbol", "")),
            name=str(item.get("name", "")),
            rank=rank,
            price_dec=price_dec,
            change_24h_raw=usd_quote.get("percentChange24h"),
        )
        if entry["id"] and entry["symbol"] and entry["id"] not in seen_ids:
            seen_ids.add(entry["id"])
            coins.append(entry)
    return coins


def parse_cmc_pro_listing(payload: dict[str, Any], *, limit: int = 300) -> list[dict[str, Any]]:
    """Parse CoinMarketCap Pro API v1 listing JSON (Decimal-decoded)."""
    raw_list = payload.get("data", [])
    if not isinstance(raw_list, list):
        return []

    coins: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(raw_list[:limit], start=1):
        if not isinstance(item, dict):
            continue
        usd_quote = item.get("quote", {}).get("USD", {})
        if not isinstance(usd_quote, dict) or usd_quote.get("price") is None:
            continue
        try:
            price_dec = parse_strict_decimal(
                usd_quote["price"], field_name=f"cmc_pro.{item.get('symbol')}.price"
            )
        except Exception:
            continue
        cmc_id = int(item["id"]) if item.get("id") is not None else None
        rank = int(item.get("cmc_rank") or idx)
        entry = _normalize_cmc_entry(
            cmc_id=cmc_id,
            symbol=str(item.get("symbol", "")),
            name=str(item.get("name", "")),
            rank=rank,
            price_dec=price_dec,
            change_24h_raw=usd_quote.get("percent_change_24h"),
        )
        if entry["id"] and entry["symbol"] and entry["id"] not in seen_ids:
            seen_ids.add(entry["id"])
            coins.append(entry)
    return coins


def parse_coingecko_markets(payload: list[Any], *, limit: int = 250) -> list[dict[str, Any]]:
    """Parse CoinGecko `/coins/markets` fallback response using strict Decimal."""
    coins: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(payload[:limit], start=1):
        if not isinstance(item, dict) or item.get("current_price") is None:
            continue
        try:
            price_dec = parse_strict_decimal(
                item["current_price"], field_name=f"cg.{item.get('symbol')}.price"
            )
        except Exception:
            continue
        sym = str(item.get("symbol", "")).upper()
        cmc_id = KNOWN_CMC_IDS.get(sym)
        rank = int(item.get("market_cap_rank") or idx)
        logo = (
            CMC_LOGO_TEMPLATE.format(cmc_id=cmc_id)
            if cmc_id is not None
            else str(item.get("image") or "")
        )
        entry = _normalize_cmc_entry(
            cmc_id=cmc_id,
            symbol=sym,
            name=str(item.get("name", sym)),
            rank=rank,
            price_dec=price_dec,
            change_24h_raw=item.get("price_change_percentage_24h"),
            logo_url=logo,
        )
        if entry["id"] not in seen_ids and entry["symbol"]:
            seen_ids.add(entry["id"])
            coins.append(entry)
    return coins


def parse_binance_tickers_fallback(payload: list[Any], *, limit: int = 150) -> list[dict[str, Any]]:
    """Parse Binance Vision `/api/v3/ticker/24hr` USDT pairs sorted by quoteVolume."""
    usdt_rows: list[tuple[Decimal, dict[str, Any]]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        pair = str(item.get("symbol", ""))
        if not pair.endswith("USDT") or len(pair) <= 4:
            continue
        base_sym = pair[:-4]
        if base_sym.endswith(("UP", "DOWN", "BULL", "BEAR")):
            continue
        try:
            price_dec = parse_strict_decimal(item.get("lastPrice"), field_name=f"bn.{base_sym}")
            qvol = parse_strict_decimal(
                item.get("quoteVolume", "0"), field_name=f"bn.{base_sym}.qvol", allow_zero=True
            )
        except Exception:
            continue
        usdt_rows.append((qvol, {"symbol": base_sym, "price": price_dec, "chg": item.get("priceChangePercent")}))

    usdt_rows.sort(key=lambda pair: pair[0], reverse=True)
    coins: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for rank, (_, row) in enumerate(usdt_rows[:limit], start=1):
        sym = str(row["symbol"])
        cmc_id = KNOWN_CMC_IDS.get(sym)
        entry = _normalize_cmc_entry(
            cmc_id=cmc_id,
            symbol=sym,
            name=sym,
            rank=rank,
            price_dec=row["price"],
            change_24h_raw=row["chg"],
        )
        if entry["id"] not in seen_ids:
            seen_ids.add(entry["id"])
            coins.append(entry)
    return coins


async def fetch_cmc_top_cryptos(
    client: httpx.AsyncClient,
    *,
    limit: int = 300,
    cmc_pro_api_key: str = "placeholder",
) -> CMCCatalogResult:
    """Fetch Top-N CoinMarketCap cryptos with Pro -> Public v3 -> CoinGecko -> Binance failover."""
    errors: list[str] = []

    # Tier 1: Optional CoinMarketCap Pro API if user adds CMC_PRO_API_KEY
    if not is_placeholder_secret(cmc_pro_api_key):
        try:
            resp = await client.get(
                CMC_PRO_LISTING_URL,
                params={"start": "1", "limit": str(limit), "convert": "USD"},
                headers={"X-CMC_PRO_API_KEY": cmc_pro_api_key.strip()},
            )
            resp.raise_for_status()
            parsed = parse_cmc_pro_listing(loads_decimal_json(resp.text), limit=limit)
            if len(parsed) >= 10:
                return CMCCatalogResult(
                    source="coinmarketcap_pro_v1",
                    updated_at=utc_now_iso(),
                    coins=parsed,
                )
        except Exception as exc:
            errors.append(f"cmc_pro:{type(exc).__name__}")

    # Tier 2: CoinMarketCap Free Public Data API v3 (Top 300)
    try:
        resp = await client.get(
            CMC_PUBLIC_LISTING_URL,
            params={"start": "1", "limit": str(limit), "convert": "USD"},
        )
        resp.raise_for_status()
        parsed = parse_cmc_public_listing(loads_decimal_json(resp.text), limit=limit)
        if len(parsed) >= 10:
            return CMCCatalogResult(
                source="coinmarketcap_public_v3",
                updated_at=utc_now_iso(),
                coins=parsed,
            )
    except Exception as exc:
        errors.append(f"cmc_public:{type(exc).__name__}")

    # Tier 3: CoinGecko Public Markets Fallback
    try:
        resp = await client.get(
            COINGECKO_MARKETS_URL,
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": str(min(limit, 250)),
                "page": "1",
            },
        )
        resp.raise_for_status()
        raw_cg = loads_decimal_json(resp.text)
        if isinstance(raw_cg, list):
            parsed = parse_coingecko_markets(raw_cg, limit=limit)
            if len(parsed) >= 10:
                return CMCCatalogResult(
                    source="coingecko_markets_fallback",
                    updated_at=utc_now_iso(),
                    coins=parsed,
                )
    except Exception as exc:
        errors.append(f"coingecko:{type(exc).__name__}")

    # Tier 4: Binance Vision 24h Ticker Fallback
    try:
        resp = await client.get(BINANCE_TICKER_24H_URL)
        resp.raise_for_status()
        raw_bn = loads_decimal_json(resp.text)
        if isinstance(raw_bn, list):
            parsed = parse_binance_tickers_fallback(raw_bn, limit=min(limit, 150))
            if parsed:
                return CMCCatalogResult(
                    source="binance_vision_fallback",
                    updated_at=utc_now_iso(),
                    coins=parsed,
                )
    except Exception as exc:
        errors.append(f"binance:{type(exc).__name__}")

    raise RuntimeError(f"All crypto catalog sources failed -> {' | '.join(errors)}")


# ---------------------------------------------------------------------------
# SP-Today Official Damascus Market SYP Rate, Silver (XAG) & 155+ World FX
# ---------------------------------------------------------------------------


def parse_sp_today_usd_syp_html(html_text: str) -> dict[str, str] | None:
    """Extract official Damascus Buy/Sell USD/SYP market rate from `sp-today.com` SSR HTML."""
    m_buy = _SP_BUY_RE.search(html_text)
    m_sell = _SP_SELL_RE.search(html_text)
    if not m_buy and not m_sell:
        return None
    try:
        buy_dec = (
            parse_strict_decimal(m_buy.group(1).replace(",", ""), field_name="sp_today.buy")
            if m_buy
            else None
        )
        sell_dec = (
            parse_strict_decimal(m_sell.group(1).replace(",", ""), field_name="sp_today.sell")
            if m_sell
            else None
        )
        if buy_dec is not None and sell_dec is not None:
            mid_dec = (buy_dec + sell_dec) / Decimal("2")
        elif sell_dec is not None:
            mid_dec = sell_dec
            buy_dec = sell_dec
        elif buy_dec is not None:
            mid_dec = buy_dec
            sell_dec = buy_dec
        else:
            return None

        return {
            "buy_rate": format_decimal(buy_dec, QUANT_4DP),
            "sell_rate": format_decimal(sell_dec, QUANT_4DP),
            "mid_rate": format_decimal(mid_dec, QUANT_4DP),
            "source": "sp-today",
        }
    except Exception:
        return None


async def fetch_sp_today_syp_rate(
    client: httpx.AsyncClient,
    *,
    settings: Settings | None = None,
) -> dict[str, str] | None:
    """Delegate SYP strictly to the 3-tier `backend/sources/sptoday.py` fetcher."""
    quote = await fetch_syp_sptoday_3tier(client, settings=settings)
    return quote.to_dict() if quote is not None else None


async def fetch_silver_spot(
    client: httpx.AsyncClient,
    *,
    fallback_gold_oz: Decimal | None = None,
) -> tuple[Decimal, str]:
    """Fetch live Silver (`XAG/USD`) spot price via Swissquote -> Goldprice.org -> safe fallback."""
    # Tier 1: Swissquote XAG/USD BBO
    try:
        resp = await client.get(SWISSQUOTE_XAG_URL, timeout=httpx.Timeout(8))
        resp.raise_for_status()
        arr = loads_decimal_json(resp.text)
        if isinstance(arr, list) and arr:
            for quote in arr:
                profiles = quote.get("spreadProfilePrices", []) if isinstance(quote, dict) else []
                for p in profiles:
                    if isinstance(p, dict) and p.get("bid") is not None and p.get("ask") is not None:
                        bid = parse_strict_decimal(p["bid"], field_name="xag.bid")
                        ask = parse_strict_decimal(p["ask"], field_name="xag.ask")
                        return (bid + ask) / Decimal("2"), "swissquote_xag"
    except Exception:
        pass

    # Tier 2: Goldprice.org dbXRates xagPrice
    try:
        resp = await client.get(GOLDPRICE_ORG_URL, timeout=httpx.Timeout(8))
        resp.raise_for_status()
        data = loads_decimal_json(resp.text)
        items = data.get("items", []) if isinstance(data, dict) else []
        if items and isinstance(items[0], dict) and items[0].get("xagPrice") is not None:
            return parse_strict_decimal(items[0]["xagPrice"], field_name="xag.goldprice"), "goldprice_org"
    except Exception:
        pass

    # Tier 3: Derived gold/silver ratio safeguard so catalog never halts
    base_xau = fallback_gold_oz if fallback_gold_oz and fallback_gold_oz > Decimal("0") else Decimal("3500")
    return (base_xau / Decimal("85")).quantize(QUANT_2DP), "derived_xau_ratio_fallback"


async def fetch_global_155_fiat_rates(
    client: httpx.AsyncClient,
    *,
    settings: Settings | None = None,
) -> tuple[dict[str, Decimal], str, dict[str, str] | None]:
    """Fetch 155+ global & Arab ISO fiat currencies; SYP is strictly sourced ONLY via SP-Today."""
    fiat_map: dict[str, Decimal] = {}
    source_used = "open.er-api.com"

    # Attempt 1: open.er-api.com (160+ world & Arab currencies — SYP STRICTLY EXCLUDED)
    try:
        resp = await client.get("https://open.er-api.com/v6/latest/USD")
        resp.raise_for_status()
        data = loads_decimal_json(resp.text)
        rates = data.get("rates", {}) if isinstance(data, dict) else {}
        for raw_code, raw_val in rates.items():
            code = str(raw_code).strip().upper()
            if code == "SYP" or not _ISO_CODE_RE.match(code):
                continue
            try:
                fiat_map[code] = parse_strict_decimal(raw_val, field_name=f"fx.{code}")
            except Exception:
                continue
        if len(fiat_map) >= 100:
            source_used = "open.er-api.com"
    except Exception:
        fiat_map = {}

    # Attempt 2 / 3: jsdelivr & Cloudflare currency-api mirrors (SYP STRICTLY EXCLUDED)
    if len(fiat_map) < 100:
        for url, label in (
            ("https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json", "jsdelivr_currency_api"),
            ("https://latest.currency-api.pages.dev/v1/currencies/usd.json", "cloudflare_currency_mirror"),
        ):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                data = loads_decimal_json(resp.text)
                usd_map = data.get("usd", {}) if isinstance(data, dict) else {}
                for raw_code, raw_val in usd_map.items():
                    code = str(raw_code).strip().upper()
                    if code == "SYP" or not _ISO_CODE_RE.match(code):
                        continue
                    try:
                        fiat_map[code] = parse_strict_decimal(raw_val, field_name=f"fx.{code}")
                    except Exception:
                        continue
                if len(fiat_map) >= 100:
                    source_used = label
                    break
            except Exception:
                continue

    fiat_map.setdefault("USD", Decimal("1"))
    fiat_map.pop("SYP", None)

    # Populate SYP EXCLUSIVELY from 3-tier SP-Today fetcher (Web -> t.me/s -> Upstash last_good)
    sp_today_info = await fetch_sp_today_syp_rate(client, settings=settings)
    if sp_today_info and sp_today_info.get("mid_rate"):
        fiat_map["SYP"] = parse_strict_decimal(sp_today_info["mid_rate"], field_name="sp_today.mid")

    return fiat_map, source_used, sp_today_info


def build_catalog_feed_payload(
    *,
    fiat_rates_raw: dict[str, Decimal],
    fiat_source: str,
    syp_sp_today: dict[str, str] | None,
    gold_oz_usd: Decimal,
    silver_oz_usd: Decimal,
    silver_source: str,
    cmc_result: CMCCatalogResult,
) -> dict[str, Any]:
    """Assemble lightweight `catalog_feed.json` (155+ FX, Gold/Silver metals, Top-300 CMC)."""
    fiat_catalog: dict[str, dict[str, Any]] = {}
    for code in sorted(fiat_rates_raw.keys()):
        if not _ISO_CODE_RE.match(code):
            continue
        units = fiat_rates_raw[code]
        if units <= Decimal("0"):
            continue
        inv_quant = QUANT_8DP if units >= Decimal("1000") else QUANT_4DP
        usd_per_unit = Decimal("1") / units
        name_en, name_ar, symbol = CURRENCY_METADATA.get(code, (code, code, code))
        entry: dict[str, Any] = {
            "code": code,
            "name_en": name_en,
            "name_ar": name_ar,
            "symbol": symbol,
            "units_per_usd": format_decimal(units, QUANT_4DP),
            "usd_per_unit": format_decimal(usd_per_unit, inv_quant),
            "pair_label": "USD/USD" if code == "USD" else f"USD/{code}",
            "display_rate": "1.0000" if code == "USD" else format_decimal(units, QUANT_4DP),
        }
        if code == "SYP" and syp_sp_today:
            entry["source"] = "sp-today"
            entry["buy_rate"] = syp_sp_today["buy_rate"]
            entry["sell_rate"] = syp_sp_today["sell_rate"]
        fiat_catalog[code] = entry

    gold_table = compute_gold_prices(gold_oz_usd)
    xag_gram = silver_oz_usd / TROY_OUNCE_GRAMS
    metals_catalog: dict[str, dict[str, str]] = {
        "XAU_OZ": {
            "id": "XAU_OZ",
            "code": "XAU",
            "name_en": "Gold Troy Ounce",
            "name_ar": "أونصة الذهب الفوري",
            "symbol": "Au",
            "price_usd": gold_table["XAU_OZ"],
        },
        "GRAM_24K": {
            "id": "GRAM_24K",
            "code": "24K",
            "name_en": "Gold 24K Gram",
            "name_ar": "جرام الذهب عيار 24",
            "symbol": "Au",
            "price_usd": gold_table["GRAM_24K"],
        },
        "GRAM_21K": {
            "id": "GRAM_21K",
            "code": "21K",
            "name_en": "Gold 21K Gram",
            "name_ar": "جرام الذهب عيار 21",
            "symbol": "Au",
            "price_usd": gold_table["GRAM_21K"],
        },
        "GRAM_18K": {
            "id": "GRAM_18K",
            "code": "18K",
            "name_en": "Gold 18K Gram",
            "name_ar": "جرام الذهب عيار 18",
            "symbol": "Au",
            "price_usd": gold_table["GRAM_18K"],
        },
        "XAG_OZ": {
            "id": "XAG_OZ",
            "code": "XAG",
            "name_en": "Silver Troy Ounce",
            "name_ar": "أونصة الفضة الفورية",
            "symbol": "Ag",
            "price_usd": format_decimal(silver_oz_usd, QUANT_2DP),
        },
        "XAG_GRAM": {
            "id": "XAG_GRAM",
            "code": "Ag/g",
            "name_en": "Silver Gram (999)",
            "name_ar": "جرام الفضة عيار 999",
            "symbol": "Ag",
            "price_usd": format_decimal(xag_gram, QUANT_4DP),
        },
    }

    payload: dict[str, Any] = {
        "meta": {
            "platform": "RatePath",
            "platform_ar": "ريت باث",
            "schema_version": "2.0.0",
            "generated_at": utc_now_iso(),
            "fiat_count": len(fiat_catalog),
            "metals_count": len(metals_catalog),
            "crypto_count": len(cmc_result.coins),
            "fiat_source": fiat_source,
            "syp_source": "sp-today" if syp_sp_today else fiat_source,
            "silver_source": silver_source,
            "cmc_source": cmc_result.source,
        },
        "fiat": fiat_catalog,
        "metals": metals_catalog,
        "crypto": cmc_result.coins,
    }
    _assert_zero_floats_recursive(payload)
    return payload


def publish_catalog_feed_atomic(
    payload: dict[str, Any],
    *,
    target_path: Path = CATALOG_FEED_TARGET_PATH,
) -> Path:
    """Atomically write UTF-8 `catalog_feed.json` with zero floats via `catalog_feed.tmp.json`."""
    enforce_utf8_runtime()
    _assert_zero_floats_recursive(payload)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name("catalog_feed.tmp.json")
    try:
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target_path)
        return target_path
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


async def generate_and_publish_catalog_feed(
    *,
    gold_oz_usd: Decimal,
    settings: Settings | None = None,
    target_path: Path = CATALOG_FEED_TARGET_PATH,
    client: httpx.AsyncClient | None = None,
    crypto_limit: int = 300,
) -> dict[str, Any]:
    """Fetch 155+ FX, SP-Today SYP, Silver, and Top-300 CMC concurrently and publish atomically."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    owns_client = client is None
    http_client = client or create_http_client()
    try:
        (fiat_map, fiat_source, syp_sp_today), (silver_oz, silver_source), cmc_res = (
            await asyncio.gather(
                fetch_global_155_fiat_rates(http_client, settings=cfg),
                fetch_silver_spot(http_client, fallback_gold_oz=gold_oz_usd),
                fetch_cmc_top_cryptos(
                    http_client,
                    limit=crypto_limit,
                    cmc_pro_api_key=cfg.cmc_pro_api_key,
                ),
            )
        )
        payload = build_catalog_feed_payload(
            fiat_rates_raw=fiat_map,
            fiat_source=fiat_source,
            syp_sp_today=syp_sp_today,
            gold_oz_usd=gold_oz_usd,
            silver_oz_usd=silver_oz,
            silver_source=silver_source,
            cmc_result=cmc_res,
        )
        publish_catalog_feed_atomic(payload, target_path=target_path)
        return payload
    finally:
        if owns_client:
            await http_client.aclose()
