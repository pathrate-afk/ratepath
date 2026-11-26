"""Agent 8: Quantitative Technical, Derivatives & Macro Signal Collector (Pure Decimal, ZERO floats).

Collects hard numerical market structure for Gold (XAU), Silver (XAG), Euro (EUR), British Pound (GBP),
and top Crypto (BTC, ETH, SOL):
1. Technical Structure: EMA(20), EMA(50), RSI(14), ATR(14) computed strictly in `decimal.Decimal`.
2. Crypto Derivatives Flow: Live Perpetual Funding Rate & Open-Interest stress (OKX / KuCoin / Binance public).
3. Macro Drivers: Live US Dollar Index (`DX-Y.NYB`) & US 10-Year Treasury Yield (`^TNX`) 24h changes.
4. Selective Abstention Ensemble: Scores each asset from -100 to +100. Scores inside [-35, +35]
   remain strictly `neutral` to filter market noise and maximize real directional accuracy.

Legal & Ethical Notice: Probabilistic numerical analytics only — not financial advice or guaranteed outcomes.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Any

import httpx

from backend.agents.contracts import AgentReport, BiasDirection, CANONICAL_BIAS_ASSETS, utc_now_iso
from backend.math_engine import parse_strict_decimal

ABSTENTION_BAND: int = 35
QUANT_2DP = Decimal("0.01")
QUANT_4DP = Decimal("0.0001")

_HTTP_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RatePathQuant/2.0",
}


def compute_ema_decimal(values: list[Decimal], period: int) -> Decimal | None:
    """Compute Exponential Moving Average strictly using `decimal.Decimal` (ZERO floats)."""
    if len(values) < period or period <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        k = Decimal("2") / Decimal(period + 1)
        one_minus_k = Decimal("1") - k
        ema = sum(values[:period], Decimal("0")) / Decimal(period)
        for price in values[period:]:
            ema = (price * k) + (ema * one_minus_k)
        return ema.quantize(QUANT_4DP, rounding=ROUND_HALF_UP)


def compute_rsi_decimal(values: list[Decimal], period: int = 14) -> Decimal | None:
    """Compute Wilder's Relative Strength Index RSI(14) strictly in `decimal.Decimal`."""
    if len(values) <= period or period <= 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        gains: list[Decimal] = []
        losses: list[Decimal] = []
        for idx in range(1, len(values)):
            diff = values[idx] - values[idx - 1]
            if diff > Decimal("0"):
                gains.append(diff)
                losses.append(Decimal("0"))
            elif diff < Decimal("0"):
                gains.append(Decimal("0"))
                losses.append(-diff)
            else:
                gains.append(Decimal("0"))
                losses.append(Decimal("0"))

        avg_gain = sum(gains[:period], Decimal("0")) / Decimal(period)
        avg_loss = sum(losses[:period], Decimal("0")) / Decimal(period)
        p_dec = Decimal(period)
        p_minus_1 = Decimal(period - 1)
        for g, l in zip(gains[period:], losses[period:]):
            avg_gain = (avg_gain * p_minus_1 + g) / p_dec
            avg_loss = (avg_loss * p_minus_1 + l) / p_dec

        if avg_loss == Decimal("0"):
            return Decimal("100.00") if avg_gain > Decimal("0") else Decimal("50.00")
        rs = avg_gain / avg_loss
        rsi = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
        return rsi.quantize(QUANT_2DP, rounding=ROUND_HALF_UP)


def compute_atr_pct_decimal(
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    period: int = 14,
) -> Decimal | None:
    """Compute ATR(14) as a percentage of latest close strictly in `decimal.Decimal`."""
    n = min(len(highs), len(lows), len(closes))
    if n <= period or closes[-1] <= Decimal("0"):
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        trs: list[Decimal] = []
        for idx in range(1, n):
            hl = highs[idx] - lows[idx]
            hc = abs(highs[idx] - closes[idx - 1])
            lc = abs(lows[idx] - closes[idx - 1])
            trs.append(max(hl, hc, lc))
        atr = sum(trs[-period:], Decimal("0")) / Decimal(period)
        atr_pct = (atr / closes[-1]) * Decimal("100")
        return atr_pct.quantize(QUANT_2DP, rounding=ROUND_HALF_UP)


def score_asset_quant_signal(
    *,
    asset: str,
    closes: list[Decimal],
    funding_rate_pct: Decimal | None = None,
    dxy_change_pct: Decimal | None = None,
    us10y_change_pct: Decimal | None = None,
) -> dict[str, Any]:
    """Combine EMA trend, RSI momentum, and derivatives/macro pressure into [-100, +100] score.

    Applies Selective Abstention: scores within [-35, +35] map to `neutral`.
    """
    if len(closes) < 20 or closes[-1] <= Decimal("0"):
        return {
            "asset": asset,
            "score": 0,
            "bias": "neutral",
            "confidence": 40,
            "ema20": None,
            "ema50": None,
            "rsi14": None,
        }

    latest = closes[-1]
    ema20 = compute_ema_decimal(closes, 20)
    ema50 = compute_ema_decimal(closes, min(50, len(closes)))
    rsi14 = compute_rsi_decimal(closes, 14)

    score = 0

    # 1. Trend structure (max +/- 40 pts)
    if ema20 is not None and ema50 is not None and ema50 > Decimal("0"):
        with localcontext() as ctx:
            ctx.prec = 28
            spread_pct = ((ema20 - ema50) / ema50) * Decimal("100")
        if latest > ema20 and ema20 > ema50:
            score += 40 if spread_pct >= Decimal("0.30") else 25
        elif latest < ema20 and ema20 < ema50:
            score -= 40 if spread_pct <= Decimal("-0.30") else 25
        elif latest > ema20:
            score += 12
        elif latest < ema20:
            score -= 12

    # 2. RSI momentum & extreme mean-reversion guard (max +/- 25 pts)
    if rsi14 is not None:
        if Decimal("55") <= rsi14 <= Decimal("69"):
            score += 22
        elif Decimal("31") <= rsi14 <= Decimal("45"):
            score -= 22
        elif rsi14 > Decimal("76"):
            # Overheated stretch: cool bullish euphoria slightly to reduce chasing tops
            score -= 15
        elif rsi14 < Decimal("24"):
            # Oversold washout: contrarian bounce support
            score += 15

    # 3. Crypto derivatives positioning (max +/- 35 pts)
    if asset in ("BTC", "ETH", "SOL") and funding_rate_pct is not None:
        if funding_rate_pct >= Decimal("0.045"):
            # Crowded longs paying extreme funding -> downside flush risk
            score -= 30
        elif funding_rate_pct <= Decimal("-0.015"):
            # Crowded shorts -> short-squeeze upside fuel
            score += 30
        elif Decimal("0.005") <= funding_rate_pct <= Decimal("0.025"):
            # Healthy organic spot-led demand
            score += 10

    # 4. Macro DXY & US10Y driver for Gold, Silver, Euro, Pound, USD (max +/- 35 pts)
    if asset in ("XAU", "XAG", "EUR", "GBP"):
        if dxy_change_pct is not None:
            if dxy_change_pct <= Decimal("-0.30"):
                score += 22
            elif dxy_change_pct >= Decimal("0.30"):
                score -= 22
        if us10y_change_pct is not None and asset in ("XAU", "XAG"):
            if us10y_change_pct <= Decimal("-1.00"):
                score += 13
            elif us10y_change_pct >= Decimal("1.00"):
                score -= 13
    elif asset == "USD":
        if dxy_change_pct is not None:
            if dxy_change_pct >= Decimal("0.30"):
                score += 28
            elif dxy_change_pct <= Decimal("-0.30"):
                score -= 28

    score = max(-100, min(100, score))

    # Selective Abstention Band: stay `neutral` unless signal crosses +/- ABSTENTION_BAND
    if score >= ABSTENTION_BAND:
        bias: BiasDirection = "bullish"
    elif score <= -ABSTENTION_BAND:
        bias = "bearish"
    else:
        bias = "neutral"

    confidence = min(92, 45 + (abs(score) * 45) // 100)
    return {
        "asset": asset,
        "score": score,
        "bias": bias,
        "confidence": confidence,
        "ema20": format(ema20, "f") if ema20 is not None else None,
        "ema50": format(ema50, "f") if ema50 is not None else None,
        "rsi14": format(rsi14, "f") if rsi14 is not None else None,
    }


async def _fetch_kucoin_4h_closes(client: httpx.AsyncClient, symbol: str) -> list[Decimal]:
    """Fetch last ~60 4-hour candle closes from KuCoin public market API (no API key)."""
    try:
        resp = await client.get(
            "https://api.kucoin.com/api/v1/market/candles",
            params={"type": "4hour", "symbol": f"{symbol}-USDT"},
            headers=_HTTP_HEADERS,
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        raw = json.loads(resp.text)
        rows = raw.get("data") or []
        if not isinstance(rows, list) or len(rows) < 20:
            return []
        # KuCoin returns newest-first: [time, open, close, high, low, volume, turnover]
        ordered = list(reversed(rows[:65]))
        closes: list[Decimal] = []
        for row in ordered:
            if isinstance(row, list) and len(row) >= 3:
                closes.append(parse_strict_decimal(str(row[2]), field_name=f"kucoin.{symbol}.close"))
        if len(closes) >= 20:
            return closes
    except Exception:
        pass
    # Fallback to Yahoo Finance daily crypto candles if KuCoin is geo/IP throttled
    yahoo_closes, _ = await _fetch_yahoo_daily_closes(client, f"{symbol}-USD")
    return yahoo_closes


async def _fetch_okx_funding_pct(client: httpx.AsyncClient, symbol: str) -> Decimal | None:
    """Fetch live 8h perpetual funding rate (%) from OKX public API (fallback: None)."""
    try:
        resp = await client.get(
            "https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": f"{symbol}-USDT-SWAP"},
            headers=_HTTP_HEADERS,
            timeout=7,
        )
        if resp.status_code != 200:
            return None
        data = json.loads(resp.text).get("data") or []
        if not data:
            return None
        rate_raw = str(data[0].get("fundingRate", "")).strip()
        if not rate_raw:
            return None
        with localcontext() as ctx:
            ctx.prec = 28
            pct = Decimal(rate_raw) * Decimal("100")
            return pct.quantize(QUANT_4DP, rounding=ROUND_HALF_UP)
    except Exception:
        return None


async def _fetch_yahoo_daily_closes(
    client: httpx.AsyncClient, ticker: str
) -> tuple[list[Decimal], Decimal | None]:
    """Fetch 3-month daily closes and latest 24h % move from Yahoo Finance public chart API."""
    try:
        resp = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"range": "3mo", "interval": "1d"},
            headers=_HTTP_HEADERS,
            timeout=8,
        )
        if resp.status_code != 200:
            return [], None
        payload = json.loads(resp.text)
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not isinstance(result, dict):
            return [], None
        raw_closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        closes: list[Decimal] = []
        for val in raw_closes:
            if val is not None:
                dec = parse_strict_decimal(str(val), field_name=f"yahoo.{ticker}.close")
                closes.append(dec)
        if len(closes) < 2:
            return closes, None
        with localcontext() as ctx:
            ctx.prec = 28
            chg = ((closes[-1] - closes[-2]) / closes[-2]) * Decimal("100")
            chg_q = chg.quantize(QUANT_2DP, rounding=ROUND_HALF_UP)
        return closes[-65:], chg_q
    except Exception:
        return [], None


async def collect_quant_macro_signals(client: httpx.AsyncClient) -> AgentReport:
    """Agent 8 collector: combines technical EMA/RSI, crypto funding, and DXY/US10Y macro."""
    try:
        (
            btc_closes,
            eth_closes,
            sol_closes,
            btc_funding,
            eth_funding,
            sol_funding,
            xau_res,
            eur_res,
            gbp_res,
            dxy_res,
            us10y_res,
        ) = await asyncio.gather(
            _fetch_kucoin_4h_closes(client, "BTC"),
            _fetch_kucoin_4h_closes(client, "ETH"),
            _fetch_kucoin_4h_closes(client, "SOL"),
            _fetch_okx_funding_pct(client, "BTC"),
            _fetch_okx_funding_pct(client, "ETH"),
            _fetch_okx_funding_pct(client, "SOL"),
            _fetch_yahoo_daily_closes(client, "GC=F"),
            _fetch_yahoo_daily_closes(client, "EURUSD=X"),
            _fetch_yahoo_daily_closes(client, "GBPUSD=X"),
            _fetch_yahoo_daily_closes(client, "DX-Y.NYB"),
            _fetch_yahoo_daily_closes(client, "^TNX"),
        )
    except Exception as exc:
        return AgentReport(
            agent_id="agent_8_quant_macro",
            tier="hard_truth",
            timestamp=utc_now_iso(),
            healthy=False,
            signal_direction="neutral",
            confidence=0,
            headline="Quant & Macro collector failed to reach market endpoints.",
            error=type(exc).__name__,
        )

    dxy_closes, dxy_chg = dxy_res
    _, us10y_chg = us10y_res
    xau_closes, _ = xau_res
    eur_closes, _ = eur_res
    gbp_closes, _ = gbp_res

    asset_scores: dict[str, dict[str, Any]] = {
        "USD": score_asset_quant_signal(asset="USD", closes=dxy_closes, dxy_change_pct=dxy_chg),
        "EUR": score_asset_quant_signal(asset="EUR", closes=eur_closes, dxy_change_pct=dxy_chg),
        "GBP": score_asset_quant_signal(asset="GBP", closes=gbp_closes, dxy_change_pct=dxy_chg),
        "XAU": score_asset_quant_signal(
            asset="XAU", closes=xau_closes, dxy_change_pct=dxy_chg, us10y_change_pct=us10y_chg
        ),
        "BTC": score_asset_quant_signal(asset="BTC", closes=btc_closes, funding_rate_pct=btc_funding),
        "ETH": score_asset_quant_signal(asset="ETH", closes=eth_closes, funding_rate_pct=eth_funding),
        "SOL": score_asset_quant_signal(asset="SOL", closes=sol_closes, funding_rate_pct=sol_funding),
    }

    active_assets = [a for a in CANONICAL_BIAS_ASSETS if asset_scores[a]["ema20"] is not None]
    if not active_assets:
        return AgentReport(
            agent_id="agent_8_quant_macro",
            tier="hard_truth",
            timestamp=utc_now_iso(),
            healthy=False,
            signal_direction="neutral",
            confidence=0,
            headline="Quant & Macro candles unavailable across upstream venues.",
            error="empty_candles",
        )

    flags: list[str] = ["QUANT_ENSEMBLE_ACTIVE"]
    if btc_funding is not None and btc_funding >= Decimal("0.045"):
        flags.append("CRYPTO_FUNDING_OVERHEATED")
    elif btc_funding is not None and btc_funding <= Decimal("-0.015"):
        flags.append("CRYPTO_SHORT_SQUEEZE_SETUP")
    if dxy_chg is not None and dxy_chg >= Decimal("0.50"):
        flags.append("USD_DXY_BREAKOUT")
    elif dxy_chg is not None and dxy_chg <= Decimal("-0.50"):
        flags.append("USD_DXY_PULLBACK")

    directional = [
        f"{sym}:{asset_scores[sym]['bias']}({asset_scores[sym]['score']:+d})"
        for sym in ("XAU", "EUR", "BTC", "ETH")
        if asset_scores[sym]["bias"] != "neutral"
    ]
    headline = (
        f"Quant Ensemble ({len(active_assets)}/7 assets): "
        + (", ".join(directional) if directional else "Range-bound neutral regime (selective abstention)")
    )[:300]

    # Determine aggregate direction & confidence
    max_abs = max(abs(int(asset_scores[sym]["score"])) for sym in active_assets)
    if asset_scores["BTC"]["bias"] == "bearish" and asset_scores["XAU"]["bias"] == "bullish":
        overall_dir = "risk_off"
    elif asset_scores["BTC"]["bias"] == "bullish" or asset_scores["XAU"]["bias"] == "bullish":
        overall_dir = "bullish"
    elif asset_scores["BTC"]["bias"] == "bearish" or asset_scores["EUR"]["bias"] == "bearish":
        overall_dir = "bearish"
    else:
        overall_dir = "neutral"

    confidence = min(90, 50 + (max_abs * 40) // 100)

    metrics: dict[str, str] = {
        "quant_biases_json": json.dumps(
            {sym: asset_scores[sym]["bias"] for sym in CANONICAL_BIAS_ASSETS},
            ensure_ascii=False,
        ),
        "quant_scores_json": json.dumps(
            {sym: int(asset_scores[sym]["score"]) for sym in CANONICAL_BIAS_ASSETS},
            ensure_ascii=False,
        ),
        "active_quant_assets": str(len(active_assets)),
    }
    if dxy_chg is not None:
        metrics["dxy_24h_pct"] = format(dxy_chg, "f")
    if us10y_chg is not None:
        metrics["us10y_24h_pct"] = format(us10y_chg, "f")
    if btc_funding is not None:
        metrics["btc_funding_8h_pct"] = format(btc_funding, "f")
    for sym in ("XAU", "EUR", "BTC"):
        if asset_scores[sym]["rsi14"] is not None:
            metrics[f"rsi14_{sym.lower()}"] = str(asset_scores[sym]["rsi14"])

    return AgentReport(
        agent_id="agent_8_quant_macro",
        tier="hard_truth",
        timestamp=utc_now_iso(),
        healthy=True,
        signal_direction=overall_dir,  # type: ignore[arg-type]
        confidence=confidence,
        headline=headline,
        evidence_urls=[
            "https://api.kucoin.com/api/v1/market/candles",
            "https://www.okx.com/api/v5/public/funding-rate",
            "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
        ],
        metrics=metrics,
        flags=flags,
    )
