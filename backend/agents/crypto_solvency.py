"""Agent 5: Crypto Solvency & Liquidity Guardian (Lane 2 — Tier-1 Hard Truth, timeout=8s).

Tracks DefiLlama TVL drains, stablecoin peg health (`USDT`, `USDC`, `DAI`), and
spot volume anomalies, matching stress patterns against 2011-2025 Crypto History anchors.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, localcontext

import httpx

from backend.agents.contracts import AgentReport, utc_now_iso
from backend.math_engine import (
    QUANT_2DP,
    QUANT_4DP,
    format_decimal,
    loads_decimal_json,
    parse_strict_decimal,
)

DEFILLAMA_HISTORICAL_TVL_URL: str = "https://api.llama.fi/v2/historicalChainTvl"
DEFILLAMA_STABLECOINS_URL: str = "https://stablecoins.llama.fi/stablecoins?includePrices=true"
BINANCE_24HR_TICKER_URL: str = "https://data-api.binance.vision/api/v3/ticker/24hr"

TVL_DRAIN_ALERT_PCT: Decimal = Decimal("-5.00")
STABLECOIN_DEPEG_THRESHOLD: Decimal = Decimal("0.0080")  # >0.80% off $1.00 peg
VOLUME_SPIKE_QUOTE_USD: Decimal = Decimal("4000000000")  # $4B single-pair 24h BTCUSDT surge


async def _check_defillama_tvl_change(client: httpx.AsyncClient) -> Decimal | None:
    resp = await client.get(DEFILLAMA_HISTORICAL_TVL_URL, timeout=httpx.Timeout(8))
    resp.raise_for_status()
    rows = loads_decimal_json(resp.text)
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    prev_tvl = parse_strict_decimal(rows[-2].get("tvl"), field_name="tvl.prev")
    curr_tvl = parse_strict_decimal(rows[-1].get("tvl"), field_name="tvl.curr")
    with localcontext() as ctx:
        ctx.prec = 28
        return ((curr_tvl - prev_tvl) / prev_tvl) * Decimal("100")


async def _check_stablecoin_pegs(client: httpx.AsyncClient) -> dict[str, Decimal]:
    resp = await client.get(DEFILLAMA_STABLECOINS_URL, timeout=httpx.Timeout(8))
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    pegged = data.get("peggedAssets", [])
    prices: dict[str, Decimal] = {}
    for row in pegged:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol", "")).upper()
        if sym in ("USDT", "USDC", "DAI") and row.get("price") is not None:
            try:
                prices[sym] = parse_strict_decimal(row["price"], field_name=f"peg.{sym}")
            except Exception:
                continue
    return prices


async def _check_btc_spot_volume(client: httpx.AsyncClient) -> tuple[Decimal | None, Decimal | None]:
    resp = await client.get(
        BINANCE_24HR_TICKER_URL,
        params={"symbol": "BTCUSDT"},
        timeout=httpx.Timeout(8),
    )
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    quote_vol = parse_strict_decimal(data.get("quoteVolume"), field_name="btc.quoteVolume")
    pct_chg = parse_strict_decimal(
        data.get("priceChangePercent"),
        field_name="btc.priceChangePercent",
        allow_zero=True,
        allow_negative=True,
    )
    return quote_vol, pct_chg


def evaluate_solvency_telemetry(
    *,
    tvl_change_24h_pct: Decimal | None,
    stablecoin_pegs: dict[str, Decimal],
    btc_quote_volume_usd: Decimal | None,
    btc_24h_change_pct: Decimal | None,
) -> AgentReport:
    """Pure-Decimal solvency analyzer matching TVL drains & stablecoin depegs to Crypto History."""
    flags: list[str] = []
    matched_history = "crypto-2024-spot-etf-era"
    max_depeg = Decimal("0")
    depegged_coins: list[str] = []

    for sym, px in stablecoin_pegs.items():
        dev = abs(px - Decimal("1"))
        if dev > max_depeg:
            max_depeg = dev
        if dev >= STABLECOIN_DEPEG_THRESHOLD:
            depegged_coins.append(f"{sym}={format_decimal(px, QUANT_4DP)}")

    if depegged_coins:
        flags.append("STABLECOIN_DEPEG")
        matched_history = (
            "crypto-2023-usdc-svb-depeg"
            if any("USDC" in c for c in depegged_coins)
            else "crypto-2022-terra-luna-depeg"
        )

    if tvl_change_24h_pct is not None and tvl_change_24h_pct <= TVL_DRAIN_ALERT_PCT:
        flags.append("TVL_DRAIN")
        if "STABLECOIN_DEPEG" not in flags:
            matched_history = "crypto-2022-ftx-solvency-collapse"

    if btc_quote_volume_usd is not None and btc_quote_volume_usd >= VOLUME_SPIKE_QUOTE_USD:
        flags.append("VOLUME_ANOMALY")

    if "STABLECOIN_DEPEG" in flags or "TVL_DRAIN" in flags:
        direction = "stress"
        confidence = 92
        headline = (
            f"Crypto Solvency Alert ({', '.join(flags)}): "
            f"TVL 24h={format_decimal(tvl_change_24h_pct or Decimal('0'), QUANT_2DP)}%, "
            f"Depeg={', '.join(depegged_coins) or 'none'} — matched {matched_history}."
        )
    elif btc_24h_change_pct is not None and btc_24h_change_pct >= Decimal("3.00"):
        direction = "bullish"
        confidence = 80
        headline = (
            f"Crypto Liquidity Healthy: TVL steady, pegs intact, BTC 24h +"
            f"{format_decimal(btc_24h_change_pct, QUANT_2DP)}%."
        )
    elif btc_24h_change_pct is not None and btc_24h_change_pct <= Decimal("-3.00"):
        direction = "bearish"
        confidence = 80
        headline = (
            f"Crypto De-Risking Flow: pegs intact, BTC 24h "
            f"{format_decimal(btc_24h_change_pct, QUANT_2DP)}%."
        )
    else:
        direction = "neutral"
        confidence = 78
        headline = "Crypto Solvency Healthy: DefiLlama TVL and USDT/USDC/DAI pegs stable."

    metrics = {
        "tvl_change_24h_pct": format_decimal(tvl_change_24h_pct or Decimal("0"), QUANT_2DP),
        "max_stablecoin_deviation": format_decimal(max_depeg, QUANT_4DP),
        "btc_24h_change_pct": format_decimal(btc_24h_change_pct or Decimal("0"), QUANT_2DP),
        "matched_crypto_history": matched_history,
    }
    if btc_quote_volume_usd is not None:
        metrics["btc_quote_volume_usd"] = format_decimal(btc_quote_volume_usd, QUANT_2DP)

    return AgentReport(
        agent_id="agent_5_crypto_solvency",
        tier="hard_truth",
        timestamp=utc_now_iso(),
        healthy=True,
        signal_direction=direction,
        confidence=confidence,
        headline=headline[:300],
        evidence_urls=[DEFILLAMA_HISTORICAL_TVL_URL, DEFILLAMA_STABLECOINS_URL],
        metrics=metrics,
        flags=flags,
    )


async def collect_crypto_solvency(client: httpx.AsyncClient | None = None) -> AgentReport:
    """Collect live DefiLlama TVL, stablecoin pegs, and Binance spot volume (timeout=8s)."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(8))

    try:
        tvl_res, peg_res, vol_res = await asyncio.gather(
            _check_defillama_tvl_change(http_client),
            _check_stablecoin_pegs(http_client),
            _check_btc_spot_volume(http_client),
            return_exceptions=True,
        )
        tvl_chg = tvl_res if isinstance(tvl_res, Decimal) else None
        pegs = peg_res if isinstance(peg_res, dict) else {}
        btc_vol, btc_chg = vol_res if isinstance(vol_res, tuple) else (None, None)

        if tvl_chg is None and not pegs and btc_vol is None:
            return AgentReport(
                agent_id="agent_5_crypto_solvency",
                tier="hard_truth",
                timestamp=utc_now_iso(),
                healthy=False,
                signal_direction="neutral",
                confidence=0,
                headline="Crypto solvency feeds unreachable.",
                error="solvency_feeds_unavailable",
            )

        return evaluate_solvency_telemetry(
            tvl_change_24h_pct=tvl_chg,
            stablecoin_pegs=pegs,
            btc_quote_volume_usd=btc_vol,
            btc_24h_change_pct=btc_chg,
        )
    except Exception as exc:
        return AgentReport(
            agent_id="agent_5_crypto_solvency",
            tier="hard_truth",
            timestamp=utc_now_iso(),
            healthy=False,
            signal_direction="neutral",
            confidence=0,
            headline="Agent 5 Crypto Solvency error.",
            error=type(exc).__name__,
        )
    finally:
        if owns_client:
            await http_client.aclose()
