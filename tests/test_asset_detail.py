"""Unit tests for Per-Asset Deep Analysis (1D/1W/1M/1Y), Converter & Targeted Asset News."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent


def _run_node(script: str) -> dict:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=str(WORKSPACE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(proc.stdout.strip())


def test_asset_detail_ticker_resolution_and_window_math() -> None:
    """Verify ticker mapping for USD (DXY), Gold karats, Silver, EUR, RUB, SYP (SP-Today) & BTC."""
    res = _run_node(
        """
        import { resolveYahooSpec, summarizeWindow } from './functions/api/asset-detail.js';

        const usd = resolveYahooSpec('USD', 'USD', 'fiat');
        const g21 = resolveYahooSpec('GRAM_21K', '21K', 'gold');
        const xagG = resolveYahooSpec('XAG_GRAM', 'XAG_G', 'gold');
        const eur = resolveYahooSpec('EUR', 'EUR', 'fiat');
        const rub = resolveYahooSpec('RUB', 'RUB', 'fiat');
        const syp = resolveYahooSpec('SYP', 'SYP', 'fiat');
        const btc = resolveYahooSpec('BTC', 'BTC', 'crypto');

        const win = summarizeWindow([
          { t: 1, v: 100 },
          { t: 2, v: 120 },
          { t: 3, v: 90 },
          { t: 4, v: 110 },
        ]);

        console.log(JSON.stringify({
          usdTicker: usd.ticker,
          g21Ticker: g21.ticker,
          g21ScaleOk: g21.scale > 0.025 && g21.scale < 0.03,
          xagGTicker: xagG.ticker,
          eurTicker: eur.ticker,
          rubTicker: rub.ticker,
          sypSkipped: syp.skipYahoo,
          btcTicker: btc.ticker,
          winPct: win.change_pct,
          winHigh: win.high,
          winLow: win.low,
          winDir: win.direction,
        }));
        """
    )
    assert res["usdTicker"] == "DX-Y.NYB"
    assert res["g21Ticker"] == "GC=F"
    assert res["g21ScaleOk"] is True
    assert res["xagGTicker"] == "SI=F"
    assert res["eurTicker"] == "EURUSD=X"
    assert res["rubTicker"] == "RUB=X"
    assert res["sypSkipped"] is True
    assert res["btcTicker"] == "BTC-USD"
    assert res["winPct"] == 10
    assert res["winHigh"] == 120
    assert res["winLow"] == 90
    assert res["winDir"] == "up"


def test_asset_detail_modal_markup_and_csp_safety() -> None:
    """Verify #modal-asset-detail contains analysis, converter & news sections with zero inline handlers."""
    html_src = (WORKSPACE / "frontend" / "index.html").read_text(encoding="utf-8")
    js_src = (WORKSPACE / "frontend" / "js" / "asset-detail.js").read_text(encoding="utf-8")
    assert 'id="modal-asset-detail"' in html_src
    assert 'id="detail-analysis-box"' in html_src
    assert 'id="detail-conv-amount"' in html_src
    assert 'id="detail-conv-target"' in html_src
    assert 'id="detail-news-list"' in html_src
    assert "onerror=" not in js_src
    assert "onclick=" not in js_src
