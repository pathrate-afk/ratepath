"""Unit Tests for CoinMarketCap Integration, 155+ Global FX Catalog, SP-Today SYP & Workspace Sync."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from backend.config import WORKSPACE_ROOT
from backend.sources.coinmarketcap import (
    CMCCatalogResult,
    build_catalog_feed_payload,
    fetch_cmc_top_cryptos,
    format_adaptive_usd_price,
    parse_cmc_pro_listing,
    parse_cmc_public_listing,
    parse_sp_today_usd_syp_html,
    publish_catalog_feed_atomic,
)


def _assert_no_floats(node: Any) -> None:
    assert not isinstance(node, float), f"Unexpected float: {node!r}"
    if isinstance(node, dict):
        for v in node.values():
            _assert_no_floats(v)
    elif isinstance(node, list):
        for item in node:
            _assert_no_floats(item)


def test_cmc_public_and_pro_parsers_decimal_precision_and_icons() -> None:
    """Verify CMC public v3 & Pro v1 parsers produce zero floats, adaptive precision, and 64x64 logos."""
    public_payload = {
        "data": {
            "cryptoCurrencyList": [
                {
                    "id": 1,
                    "symbol": "BTC",
                    "name": "Bitcoin",
                    "cmcRank": 1,
                    "quotes": [{"name": "USD", "price": Decimal("60277.968"), "percentChange24h": Decimal("1.245")}],
                },
                {
                    "id": 52,
                    "symbol": "XRP",
                    "name": "XRP",
                    "cmcRank": 4,
                    "quotes": [{"name": "USD", "price": Decimal("2.456"), "percentChange24h": Decimal("-0.854")}],
                },
                {
                    "id": 24478,
                    "symbol": "PEPE",
                    "name": "Pepe",
                    "cmcRank": 28,
                    "quotes": [{"name": "USD", "price": Decimal("0.00000912"), "percentChange24h": Decimal("5.5")}],
                },
            ]
        }
    }

    parsed = parse_cmc_public_listing(public_payload, limit=300)
    _assert_no_floats(parsed)
    assert len(parsed) == 3

    btc, xrp, pepe = parsed
    assert btc["id"] == "BTC"
    assert btc["price_usd"] == "60277.97"
    assert btc["change_24h_pct"] == "+1.25"
    assert btc["logo_url"] == "https://s2.coinmarketcap.com/static/img/coins/64x64/1.png"

    assert xrp["id"] == "CMC_52"
    assert xrp["rank"] == 4
    assert xrp["price_usd"] == "2.46"
    assert xrp["change_24h_pct"] == "-0.85"
    assert xrp["logo_url"] == "https://s2.coinmarketcap.com/static/img/coins/64x64/52.png"

    assert pepe["id"] == "CMC_24478"
    assert pepe["price_usd"] == "0.00000912"
    assert format_adaptive_usd_price(Decimal("0.00000045")) == "0.00000045"

    # Pro v1 parser check
    pro_payload = {
        "data": [
            {
                "id": 11419,
                "symbol": "TON",
                "name": "Toncoin",
                "cmc_rank": 12,
                "quote": {"USD": {"price": Decimal("5.4321"), "percent_change_24h": Decimal("3.12")}},
            }
        ]
    }
    pro_parsed = parse_cmc_pro_listing(pro_payload, limit=10)
    assert pro_parsed[0]["id"] == "CMC_11419"
    assert pro_parsed[0]["price_usd"] == "5.43"
    assert pro_parsed[0]["logo_url"] == "https://s2.coinmarketcap.com/static/img/coins/64x64/11419.png"


def test_cmc_multi_tier_failover_to_coingecko_and_binance() -> None:
    """Verify CMC Pro -> CMC Public -> CoinGecko -> Binance failover chain never halts."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "pro-api.coinmarketcap.com" in url:
            return httpx.Response(401, text="Invalid key")
        if "api.coinmarketcap.com" in url:
            return httpx.Response(429, text="Rate limited")
        if "api.coingecko.com" in url:
            return httpx.Response(503, text="Upstream busy")
        if "data-api.binance.vision" in url:
            return httpx.Response(
                200,
                json=[
                    {"symbol": "BTCUSDT", "lastPrice": "60120.50", "quoteVolume": "950000000", "priceChangePercent": "1.10"},
                    {"symbol": "ETHUSDT", "lastPrice": "1710.25", "quoteVolume": "620000000", "priceChangePercent": "-0.45"},
                    {"symbol": "XRPUSDT", "lastPrice": "2.3500", "quoteVolume": "410000000", "priceChangePercent": "4.20"},
                ],
            )
        return httpx.Response(404)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            res = await fetch_cmc_top_cryptos(client, limit=50, cmc_pro_api_key="test_live_key")
            assert res.source == "binance_vision_fallback"
            assert len(res.coins) == 3
            assert res.coins[0]["id"] == "BTC"
            assert res.coins[2]["id"] == "CMC_52"
            assert res.coins[2]["symbol"] == "XRP"
            _assert_no_floats(res.coins)

    asyncio.run(_run())
    assert len(calls) == 4


def test_sp_today_parser_and_global_catalog_atomic_publish(tmp_path: Path) -> None:
    """Verify SP-Today USD/SYP Buy/Sell parsing + 155+ FX, Silver & CMC atomic catalog publish."""
    sample_html = (
        '<p class="text-white/70">Buy</p>'
        '<p class="text-2xl"><span class="inline-block">152.25</span><span>SYP (new)</span></p>'
        '<p class="text-white/70">Sell</p>'
        '<p class="text-2xl"><span class="inline-block">152.75</span><span>SYP (new)</span></p>'
    )
    sp_parsed = parse_sp_today_usd_syp_html(sample_html)
    assert sp_parsed is not None
    assert sp_parsed["buy_rate"] == "152.2500"
    assert sp_parsed["sell_rate"] == "152.7500"
    assert sp_parsed["mid_rate"] == "152.5000"
    assert sp_parsed["source"] == "sp-today"

    # Build a synthetic 160-currency global + Arab FX basket
    arab_and_major = {
        "USD": Decimal("1"),
        "EUR": Decimal("0.9200"),
        "GBP": Decimal("0.7900"),
        "SAR": Decimal("3.7500"),
        "AED": Decimal("3.6725"),
        "EGP": Decimal("48.5000"),
        "TRY": Decimal("36.2000"),
        "SYP": Decimal("152.5000"),
        "JOD": Decimal("0.7090"),
        "IQD": Decimal("1310.0000"),
        "KWD": Decimal("0.3070"),
        "QAR": Decimal("3.6400"),
        "BHD": Decimal("0.3760"),
        "OMR": Decimal("0.3850"),
        "LBP": Decimal("89500.0000"),
        "DZD": Decimal("134.5000"),
        "MAD": Decimal("9.8500"),
        "TND": Decimal("3.1200"),
        "LYD": Decimal("4.8500"),
        "CHF": Decimal("0.8800"),
        "CAD": Decimal("1.3900"),
        "AUD": Decimal("1.5200"),
        "JPY": Decimal("154.2000"),
        "CNY": Decimal("7.2400"),
    }
    # Pad to 160 valid 3-letter ISO-like codes
    idx = 0
    while len(arab_and_major) < 160:
        c = f"X{chr(65 + (idx // 26))}{chr(65 + (idx % 26))}"
        arab_and_major.setdefault(c, Decimal("10.0000"))
        idx += 1

    cmc_res = CMCCatalogResult(
        source="coinmarketcap_public_v3",
        updated_at="2026-11-25T23:55:00Z",
        coins=[
            {
                "id": "BTC",
                "cmc_id": 1,
                "symbol": "BTC",
                "name": "Bitcoin",
                "rank": 1,
                "price_usd": "60277.97",
                "change_24h_pct": "+0.95",
                "logo_url": "https://s2.coinmarketcap.com/static/img/coins/64x64/1.png",
                "category": "crypto",
            }
        ],
    )

    payload = build_catalog_feed_payload(
        fiat_rates_raw=arab_and_major,
        fiat_source="open.er-api.com",
        syp_sp_today=sp_parsed,
        gold_oz_usd=Decimal("3569.90"),
        silver_oz_usd=Decimal("41.13"),
        silver_source="swissquote_xag",
        cmc_result=cmc_res,
    )
    _assert_no_floats(payload)
    assert payload["meta"]["fiat_count"] >= 155
    assert payload["meta"]["syp_source"] == "sp-today"
    assert payload["fiat"]["SYP"]["units_per_usd"] == "152.5000"
    assert payload["fiat"]["SYP"]["source"] == "sp-today"
    assert payload["fiat"]["JOD"]["name_ar"] == "الدينار الأردني"
    assert payload["fiat"]["KWD"]["name_ar"] == "الدينار الكويتي"
    assert payload["fiat"]["LBP"]["usd_per_unit"] == "0.00001117"
    assert payload["metals"]["XAG_OZ"]["price_usd"] == "41.13"
    assert Decimal(payload["metals"]["XAG_GRAM"]["price_usd"]) > Decimal("1")

    out_file = tmp_path / "catalog_feed.json"
    publish_catalog_feed_atomic(payload, target_path=out_file)
    assert out_file.exists()
    assert not (tmp_path / "catalog_feed.tmp.json").exists()
    reloaded = json.loads(out_file.read_text(encoding="utf-8"))
    assert reloaded["fiat"]["JOD"]["name_ar"] == "الدينار الأردني"


def test_cloudflare_cmc_proxy_frontend_workspace_and_security_invariants() -> None:
    """Verify Cloudflare Function, Personal Workspace UI, Email Sync & zero service_role exposure."""
    fn_src = (WORKSPACE_ROOT / "functions" / "api" / "cmc-search.js").read_text(
        encoding="utf-8"
    )
    assert "Access-Control-Allow-Origin" in fn_src
    assert "max-age=120" in fn_src
    assert "api.coinmarketcap.com" in fn_src
    assert "api.coingecko.com" in fn_src

    mon_cfg = (WORKSPACE_ROOT / "frontend" / "monetization.config.js").read_text(encoding="utf-8")
    assert "CATALOG_FEED_URL" in mon_cfg
    assert "CMC_SEARCH_API_URL" in mon_cfg
    assert "SUPABASE_PUBLIC_ANON_KEY" in mon_cfg
    # Critical Security Check: NEVER expose sb_secret_ or service_role keys in frontend files
    assert "sb_secret_" not in mon_cfg
    assert "service_role" not in mon_cfg

    ws_js = (WORKSPACE_ROOT / "frontend" / "js" / "workspace.js").read_text(encoding="utf-8")
    assert "ratepath_workspace_v1" in ws_js
    assert "CloudStorage" in ws_js
    assert "reorderAssetBefore" in ws_js
    assert "pinAssetToTop" in ws_js
    assert "removeAssetFromWorkspace" in ws_js

    auth_js = (WORKSPACE_ROOT / "frontend" / "js" / "auth_sync.js").read_text(encoding="utf-8")
    assert "isSafePublicSupabaseKey" in auth_js
    assert "sb_secret_" in auth_js  # explicitly blocked in guard check
    assert "user_preferences" in auth_js
    assert "/auth/v1/otp" in auth_js

    wf = (WORKSPACE_ROOT / ".github" / "workflows" / "ratepath_worker.yml").read_text(
        encoding="utf-8"
    )
    assert "catalog_feed.json" in wf
    assert "CMC_PRO_API_KEY" in wf
