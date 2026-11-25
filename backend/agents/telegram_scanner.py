"""Agent 3: Public Telegram Web-Preview Scanner (Lane 2 — Safe HTTP `t.me/s/...`, timeout=8s).

Reads public Telegram web previews strictly via anonymous HTTPS GET (zero personal
account login, zero MTProto session risk).
"""

from __future__ import annotations

import asyncio
import html
import re

import httpx

from backend.agents.contracts import AgentReport, utc_now_iso

PUBLIC_TELEGRAM_CHANNELS: tuple[str, ...] = (
    "https://t.me/s/cointelegraph",
    "https://t.me/s/watcherguru",
)

_MSG_BLOCK_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")

_HYPE_WORDS = ("just in", "breaking", "surges", "soars", "bullish", "etf inflow", "record high")
_STRESS_WORDS = ("hack", "exploit", "halt", "depeg", "war", "sanctions", "crash", "emergency")


def parse_telegram_preview_html(raw_html: str) -> list[str]:
    """Extract clean message snippets from `https://t.me/s/<channel>` HTML."""
    matches = _MSG_BLOCK_RE.findall(raw_html or "")
    cleaned: list[str] = []
    for raw_block in matches[-6:]:
        text = html.unescape(_TAG_STRIP_RE.sub(" ", raw_block))
        text = _SPACE_RE.sub(" ", text).strip()
        if len(text) >= 15:
            cleaned.append(text[:240])
    return cleaned


async def _scan_channel_preview(client: httpx.AsyncClient, url: str) -> list[str]:
    resp = await client.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; RatePathPreviewBot/1.0)"},
        timeout=httpx.Timeout(8),
    )
    resp.raise_for_status()
    return parse_telegram_preview_html(resp.text)


async def collect_telegram_scanner(client: httpx.AsyncClient | None = None) -> AgentReport:
    """Safely scan public Telegram channel web previews with 8s fault isolation."""
    owns_client = client is None
    http_client = client or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(8))

    try:
        outcomes = await asyncio.gather(
            *(_scan_channel_preview(http_client, url) for url in PUBLIC_TELEGRAM_CHANNELS),
            return_exceptions=True,
        )
        snippets: list[str] = []
        for out in outcomes:
            if isinstance(out, list):
                snippets.extend(out)

        if not snippets:
            return AgentReport(
                agent_id="agent_3_telegram_scanner",
                tier="crowd_chatter",
                timestamp=utc_now_iso(),
                healthy=False,
                signal_direction="neutral",
                confidence=0,
                headline="Public Telegram web previews unavailable.",
                error="telegram_preview_empty",
            )

        hype_cnt = sum(1 for s in snippets if any(w in s.lower() for w in _HYPE_WORDS))
        stress_cnt = sum(1 for s in snippets if any(w in s.lower() for w in _STRESS_WORDS))

        flags: list[str] = []
        if hype_cnt >= 2 and hype_cnt > stress_cnt:
            direction = "euphoria"
            flags.append("TELEGRAM_BREAKING_HYPE")
            confidence = 60
        elif stress_cnt >= 2:
            direction = "risk_off"
            flags.append("TELEGRAM_STRESS_ALERT")
            confidence = 64
        else:
            direction = "neutral"
            confidence = 50

        return AgentReport(
            agent_id="agent_3_telegram_scanner",
            tier="crowd_chatter",
            timestamp=utc_now_iso(),
            healthy=True,
            signal_direction=direction,
            confidence=confidence,
            headline=f"Public Telegram Wire: {snippets[-1][:200]}",
            evidence_urls=list(PUBLIC_TELEGRAM_CHANNELS),
            metrics={
                "snippets_scanned": str(len(snippets)),
                "hype_hits": str(hype_cnt),
                "stress_hits": str(stress_cnt),
            },
            flags=flags,
        )
    except Exception as exc:
        return AgentReport(
            agent_id="agent_3_telegram_scanner",
            tier="crowd_chatter",
            timestamp=utc_now_iso(),
            healthy=False,
            signal_direction="neutral",
            confidence=0,
            headline="Agent 3 Telegram Scanner error.",
            error=type(exc).__name__,
        )
    finally:
        if owns_client:
            await http_client.aclose()
