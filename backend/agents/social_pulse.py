"""Agent 1: Social Pulse Collector (Lane 2 — Crowd Sentiment, Fault-Isolated, timeout=8s).

Primary: Crypto Fear & Greed Index (alternative.me, free, no key, datacenter-friendly).
Secondary (best effort): public Reddit RSS — Reddit now blocks most unauthenticated/datacenter
requests (HTTP 403/404), so it is never required for a healthy report.
"""

from __future__ import annotations

import asyncio

import feedparser
import httpx

from backend.agents.contracts import AgentReport, utc_now_iso
from backend.math_engine import loads_decimal_json

FEAR_GREED_URL: str = "https://api.alternative.me/fng/?limit=2"
REDDIT_RSS_FEEDS: tuple[str, ...] = (
    "https://www.reddit.com/r/CryptoCurrency/.rss?limit=8",
    "https://www.reddit.com/r/Economics/.rss?limit=8",
)

EXTREME_GREED_MIN: int = 75
EXTREME_FEAR_MAX: int = 25


async def _read_fear_greed(client: httpx.AsyncClient) -> tuple[int, str, int | None]:
    resp = await client.get(
        FEAR_GREED_URL,
        headers={"User-Agent": "RatePathSwarmBot/1.1 (+https://ratepath.io)"},
        timeout=httpx.Timeout(8),
    )
    resp.raise_for_status()
    data = loads_decimal_json(resp.text).get("data", [])
    if not data:
        raise ValueError("fear_greed_empty")
    current = int(str(data[0].get("value", "50")))
    label = str(data[0].get("value_classification", "Neutral"))
    previous = int(str(data[1].get("value"))) if len(data) > 1 and data[1].get("value") else None
    return current, label, previous


async def _read_single_reddit_rss(client: httpx.AsyncClient, url: str) -> list[tuple[str, str]]:
    resp = await client.get(
        url,
        headers={"User-Agent": "RatePathSwarmBot/1.1 (+https://ratepath.io)"},
        timeout=httpx.Timeout(8),
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    rows: list[tuple[str, str]] = []
    for entry in parsed.entries[:6]:
        title = str(entry.get("title", "")).strip()
        link = str(entry.get("link", "")).strip()
        if title and link.startswith(("https://", "http://")):
            rows.append((title, link))
    return rows


async def collect_social_pulse(client: httpx.AsyncClient | None = None) -> AgentReport:
    """Collect crowd sentiment (Fear & Greed primary, Reddit best-effort) with 8s fault isolation."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(8))

    try:
        fng_res, *reddit_outcomes = await asyncio.gather(
            _read_fear_greed(http_client),
            *(_read_single_reddit_rss(http_client, url) for url in REDDIT_RSS_FEEDS),
            return_exceptions=True,
        )
        reddit_items: list[tuple[str, str]] = []
        for out in reddit_outcomes:
            if isinstance(out, list):
                reddit_items.extend(out)

        if not isinstance(fng_res, tuple) and not reddit_items:
            return AgentReport(
                agent_id="agent_1_social_pulse",
                tier="crowd_chatter",
                timestamp=utc_now_iso(),
                healthy=False,
                signal_direction="neutral",
                confidence=0,
                headline="Crowd sentiment feeds temporarily unreachable.",
                error="sentiment_feeds_failed",
            )

        flags: list[str] = []
        metrics: dict[str, str] = {"reddit_posts_scanned": str(len(reddit_items))}
        direction = "neutral"
        confidence = 50
        headline = "Crowd sentiment balanced."

        if isinstance(fng_res, tuple):
            value, label, prev = fng_res
            metrics["fear_greed_value"] = str(value)
            metrics["fear_greed_label"] = label
            if prev is not None:
                metrics["fear_greed_previous"] = str(prev)
            headline = f"Crypto Fear & Greed Index: {value}/100 ({label})"
            if value >= EXTREME_GREED_MIN:
                direction = "euphoria"
                flags.append("RETAIL_EUPHORIA")
                confidence = 64
            elif value <= EXTREME_FEAR_MAX:
                direction = "risk_off"
                flags.append("CROWD_PANIC")
                confidence = 60
        elif reddit_items:
            headline = f"Community Pulse ({len(reddit_items)} posts): {reddit_items[0][0][:180]}"

        return AgentReport(
            agent_id="agent_1_social_pulse",
            tier="crowd_chatter",
            timestamp=utc_now_iso(),
            healthy=True,
            signal_direction=direction,
            confidence=confidence,
            headline=headline[:300],
            evidence_urls=[FEAR_GREED_URL] + [link for _, link in reddit_items[:2]],
            metrics=metrics,
            flags=flags,
        )
    except Exception as exc:
        return AgentReport(
            agent_id="agent_1_social_pulse",
            tier="crowd_chatter",
            timestamp=utc_now_iso(),
            healthy=False,
            signal_direction="neutral",
            confidence=0,
            headline="Agent 1 Social Pulse encountered transient error.",
            error=type(exc).__name__,
        )
    finally:
        if owns_client:
            await http_client.aclose()
