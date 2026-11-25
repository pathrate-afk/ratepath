"""Agent 4: Macro & Household Life Pressure Filter (Lane 2 — Tier-1 Hard Truth, timeout=8s).

Filters global central-bank, energy, shipping, tariff, and cost-of-living RSS feeds
to detect lawmaker motives and household inflation stress before price repricing.
"""

from __future__ import annotations

import asyncio

import feedparser
import httpx

from backend.agents.contracts import AgentReport, utc_now_iso

MACRO_LIFE_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html"),
    (
        "CNBC Economy",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    ),
    ("Investing Commodities", "https://www.investing.com/rss/news_11.rss"),
)

_ENERGY_SHIPPING_TERMS = (
    "oil",
    "crude",
    "opec",
    "energy",
    "shipping",
    "red sea",
    "strait",
    "freight",
    "gas",
    "tariff",
    "sanctions",
)
_LIFE_INFLATION_TERMS = (
    "inflation",
    "cpi",
    "rent",
    "mortgage",
    "grocery",
    "food prices",
    "cost of living",
    "unemployment",
    "payrolls",
    "rate cut",
    "rate hike",
    "interest rate",
)


async def _fetch_feed(
    client: httpx.AsyncClient, name: str, url: str
) -> list[tuple[str, str, str]]:
    resp = await client.get(
        url,
        headers={"User-Agent": "RatePathMacroFilter/1.0 (+https://ratepath.io)"},
        timeout=httpx.Timeout(8),
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    rows: list[tuple[str, str, str]] = []
    for entry in parsed.entries[:6]:
        title = str(entry.get("title", "")).strip()
        link = str(entry.get("link", "")).strip()
        if title and link.startswith(("https://", "http://")):
            rows.append((name, title, link))
    return rows


async def collect_macro_life_filter(client: httpx.AsyncClient | None = None) -> AgentReport:
    """Collect hard central-bank, energy, shipping, and cost-of-living signals (timeout=8s)."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(8))

    try:
        outcomes = await asyncio.gather(
            *(_fetch_feed(http_client, name, url) for name, url in MACRO_LIFE_RSS_FEEDS),
            return_exceptions=True,
        )
        rows: list[tuple[str, str, str]] = []
        for out in outcomes:
            if isinstance(out, list):
                rows.extend(out)

        if not rows:
            return AgentReport(
                agent_id="agent_4_macro_life_filter",
                tier="hard_truth",
                timestamp=utc_now_iso(),
                healthy=False,
                signal_direction="neutral",
                confidence=0,
                headline="Macro and central bank RSS feeds unavailable.",
                error="macro_feeds_empty",
            )

        energy_hits: list[tuple[str, str, str]] = []
        life_hits: list[tuple[str, str, str]] = []
        for row in rows:
            low = row[1].lower()
            if any(k in low for k in _ENERGY_SHIPPING_TERMS):
                energy_hits.append(row)
            if any(k in low for k in _LIFE_INFLATION_TERMS):
                life_hits.append(row)

        flags: list[str] = []
        if energy_hits:
            flags.append("ENERGY_SHIPPING_PRESSURE")
        if life_hits:
            flags.append("HOUSEHOLD_POLICY_PRESSURE")

        lead = (energy_hits or life_hits or rows)[0]
        lead_low = lead[1].lower()
        if any(w in lead_low for w in ("sanctions", "oil", "tariff", "war", "shipping")):
            direction = "risk_off"
            confidence = 84
        elif any(w in lead_low for w in ("rate cut", "easing", "stimulus", "unemployment")):
            direction = "bullish"
            confidence = 82
        elif any(w in lead_low for w in ("rate hike", "inflation", "cpi")):
            direction = "bearish"
            confidence = 80
        else:
            direction = "neutral"
            confidence = 72

        evidence = [r[2] for r in (energy_hits + life_hits + rows)[:4]]
        return AgentReport(
            agent_id="agent_4_macro_life_filter",
            tier="hard_truth",
            timestamp=utc_now_iso(),
            healthy=True,
            signal_direction=direction,
            confidence=confidence,
            headline=f"[{lead[0]}] {lead[1][:220]}",
            evidence_urls=evidence,
            metrics={
                "headlines_scanned": str(len(rows)),
                "energy_shipping_hits": str(len(energy_hits)),
                "household_policy_hits": str(len(life_hits)),
            },
            flags=flags,
        )
    except Exception as exc:
        return AgentReport(
            agent_id="agent_4_macro_life_filter",
            tier="hard_truth",
            timestamp=utc_now_iso(),
            healthy=False,
            signal_direction="neutral",
            confidence=0,
            headline="Agent 4 Macro Life Filter error.",
            error=type(exc).__name__,
        )
    finally:
        if owns_client:
            await http_client.aclose()
