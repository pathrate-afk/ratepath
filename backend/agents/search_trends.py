"""Agent 2: Search Trends Collector (Lane 2 — Google Trends RSS + CoinGecko Trending, timeout=8s)."""

from __future__ import annotations

import asyncio
import re

import feedparser
import httpx

from backend.agents.contracts import AgentReport, utc_now_iso
from backend.math_engine import loads_decimal_json

GOOGLE_TRENDS_RSS: str = "https://trends.google.com/trending/rss?geo=US"
COINGECKO_TRENDING_URL: str = "https://api.coingecko.com/api/v3/search/trending"

# Whole-word matching only (prevents "fed" -> "FedEx", "gold" -> "Golden State", "oil" -> "turmoil")
_MACRO_SEARCH_RE = re.compile(
    r"\b(gold price|fed|federal reserve|inflation|tariffs?|oil price|recession|dollar|interest rates?)\b",
    re.IGNORECASE,
)
_CRYPTO_SEARCH_RE = re.compile(
    r"\b(bitcoin|btc|crypto|cryptocurrency|ethereum|solana|xrp|dogecoin|memecoin)\b",
    re.IGNORECASE,
)


async def _fetch_google_trends(client: httpx.AsyncClient) -> list[str]:
    resp = await client.get(GOOGLE_TRENDS_RSS, timeout=httpx.Timeout(8))
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    return [str(e.get("title", "")).strip() for e in parsed.entries[:15] if e.get("title")]


async def _fetch_coingecko_trending(client: httpx.AsyncClient) -> list[str]:
    resp = await client.get(COINGECKO_TRENDING_URL, timeout=httpx.Timeout(8))
    resp.raise_for_status()
    data = loads_decimal_json(resp.text)
    coins = data.get("coins", [])
    symbols: list[str] = []
    for row in coins[:7]:
        item = row.get("item", {}) if isinstance(row, dict) else {}
        sym = str(item.get("symbol", "")).strip().upper()
        if sym:
            symbols.append(sym)
    return symbols


async def collect_search_trends(client: httpx.AsyncClient | None = None) -> AgentReport:
    """Collect Google Trends surges and CoinGecko trending tokens with strict 8s isolation."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(8))

    try:
        g_res, cg_res = await asyncio.gather(
            _fetch_google_trends(http_client),
            _fetch_coingecko_trending(http_client),
            return_exceptions=True,
        )
        g_terms = g_res if isinstance(g_res, list) else []
        cg_syms = cg_res if isinstance(cg_res, list) else []

        if not g_terms and not cg_syms:
            return AgentReport(
                agent_id="agent_2_search_trends",
                tier="crowd_chatter",
                timestamp=utc_now_iso(),
                healthy=False,
                signal_direction="neutral",
                confidence=0,
                headline="Search trend endpoints unreachable.",
                error="trends_unavailable",
            )

        macro_hits = [term for term in g_terms if _MACRO_SEARCH_RE.search(term)]
        crypto_hits = [term for term in g_terms if _CRYPTO_SEARCH_RE.search(term)]
        flags: list[str] = []
        if cg_syms:
            # Informational only: CoinGecko always has a trending list, so it is NOT a bullish signal
            flags.append("COINGECKO_TRENDING_ACTIVE")
        if len(macro_hits) >= 2:
            flags.append("SEARCH_SURGE_MACRO")

        if len(crypto_hits) >= 2:
            # Mainstream (Google) retail searching crypto = genuine retail-attention euphoria
            direction = "euphoria"
            flags.append("RETAIL_EUPHORIA")
            confidence = 60
        else:
            direction = "neutral"
            confidence = 48

        top_summary = (
            f"Macro search hits: {', '.join(macro_hits[:3]) or 'none'} | "
            f"Crypto search hits: {', '.join(crypto_hits[:3]) or 'none'} | "
            f"CoinGecko trending: {', '.join(cg_syms[:4]) or 'none'}"
        )
        return AgentReport(
            agent_id="agent_2_search_trends",
            tier="crowd_chatter",
            timestamp=utc_now_iso(),
            healthy=True,
            signal_direction=direction,
            confidence=confidence,
            headline=top_summary[:280],
            evidence_urls=[GOOGLE_TRENDS_RSS, COINGECKO_TRENDING_URL],
            metrics={
                "google_trends_count": str(len(g_terms)),
                "macro_search_hits": str(len(macro_hits)),
                "crypto_search_hits": str(len(crypto_hits)),
                "coingecko_trending": ",".join(cg_syms[:5]),
            },
            flags=flags,
        )
    except Exception as exc:
        return AgentReport(
            agent_id="agent_2_search_trends",
            tier="crowd_chatter",
            timestamp=utc_now_iso(),
            healthy=False,
            signal_direction="neutral",
            confidence=0,
            headline="Agent 2 Search Trends error.",
            error=type(exc).__name__,
        )
    finally:
        if owns_client:
            await http_client.aclose()
