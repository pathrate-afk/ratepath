"""Agent 1: Social Pulse Collector (Lane 2 — Fault-Isolated Public Reddit RSS, timeout=8s)."""

from __future__ import annotations

import asyncio

import feedparser
import httpx

from backend.agents.contracts import AgentReport, utc_now_iso

REDDIT_RSS_FEEDS: tuple[str, ...] = (
    "https://www.reddit.com/r/Economics/.rss?limit=8",
    "https://www.reddit.com/r/CryptoCurrency/.rss?limit=8",
    "https://www.reddit.com/r/Gold/.rss?limit=8",
)

_EUPHORIA_TERMS = ("moon", "all time high", "ath", "100x", "fomo", "bull run", "parabolic", "rally")
_PANIC_TERMS = ("crash", "recession", "collapse", "bank run", "panic", "liquidation", "rug", "dump")


async def _read_single_reddit_rss(client: httpx.AsyncClient, url: str) -> list[tuple[str, str]]:
    resp = await client.get(
        url,
        headers={"User-Agent": "RatePathSwarmBot/1.0 (+https://ratepath.io)"},
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
    """Collect crowd sentiment from public Reddit RSS feeds with strict 8s fault isolation."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(8))

    try:
        outcomes = await asyncio.gather(
            *(_read_single_reddit_rss(http_client, url) for url in REDDIT_RSS_FEEDS),
            return_exceptions=True,
        )
        items: list[tuple[str, str]] = []
        for out in outcomes:
            if isinstance(out, list):
                items.extend(out)

        if not items:
            return AgentReport(
                agent_id="agent_1_social_pulse",
                tier="crowd_chatter",
                timestamp=utc_now_iso(),
                healthy=False,
                signal_direction="neutral",
                confidence=0,
                headline="Reddit RSS feeds temporarily unreachable.",
                error="all_reddit_feeds_failed",
            )

        euphoria_hits = 0
        panic_hits = 0
        for title, _ in items:
            low = title.lower()
            if any(term in low for term in _EUPHORIA_TERMS):
                euphoria_hits += 1
            if any(term in low for term in _PANIC_TERMS):
                panic_hits += 1

        flags: list[str] = []
        if euphoria_hits >= 2 and euphoria_hits > panic_hits:
            direction = "euphoria"
            flags.append("RETAIL_EUPHORIA")
            confidence = 62
        elif panic_hits >= 2 and panic_hits > euphoria_hits:
            direction = "risk_off"
            flags.append("CROWD_PANIC")
            confidence = 60
        else:
            direction = "neutral"
            confidence = 50

        return AgentReport(
            agent_id="agent_1_social_pulse",
            tier="crowd_chatter",
            timestamp=utc_now_iso(),
            healthy=True,
            signal_direction=direction,
            confidence=confidence,
            headline=f"Community Pulse ({len(items)} posts): {items[0][0][:180]}",
            evidence_urls=[link for _, link in items[:3]],
            metrics={
                "posts_scanned": str(len(items)),
                "euphoria_hits": str(euphoria_hits),
                "panic_hits": str(panic_hits),
            },
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
