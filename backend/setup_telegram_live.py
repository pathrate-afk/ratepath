"""One-Command Live Activation Script (`python -m backend.setup_telegram_live`).

Performs two idempotent operational steps without ever printing secret values:
1. Syncs the single pinned website header across all 4 EN/AR Telegram channels (`pin_manager.py`).
2. Registers the Cloudflare Pages Telegram Webhook (`{PUBLIC_SITE_URL}/api/telegram-webhook`)
   via `setWebhook` protected by `TELEGRAM_WEBHOOK_SECRET`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from backend.config import Settings, enforce_utf8_runtime, is_placeholder_secret
from backend.pin_manager import (
    PinnedMessageStore,
    _tg_api_post,
    normalize_site_url,
    sync_all_channel_pins,
)


async def register_telegram_webhook(
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Register `{PUBLIC_SITE_URL}/api/telegram-webhook` with `secret_token` via Telegram Bot API."""
    cfg = settings or Settings.load()
    if not cfg.has_live_telegram:
        return {"status": "skipped_placeholder_telegram"}
    if is_placeholder_secret(cfg.telegram_webhook_secret):
        return {
            "status": "skipped_missing_webhook_secret",
            "hint": "Set TELEGRAM_WEBHOOK_SECRET in .env and Cloudflare Pages env, then re-run.",
        }

    site_base = normalize_site_url(cfg.public_site_url)
    webhook_url = f"{site_base}/api/telegram-webhook"
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    try:
        status_code, body = await _tg_api_post(
            http_client,
            cfg.telegram_bot_token,
            "setWebhook",
            {
                "url": webhook_url,
                "secret_token": cfg.telegram_webhook_secret,
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            },
        )
        return {
            "status": "registered" if body.get("ok") else "failed",
            "http_status": status_code,
            "webhook_url": webhook_url,
            "description": str(body.get("description", "")),
        }
    finally:
        if owns_client:
            await http_client.aclose()


async def activate_telegram_live(
    *,
    settings: Settings | None = None,
    pin_store: PinnedMessageStore | None = None,
    client: httpx.AsyncClient | None = None,
    force_edit_pins: bool = False,
) -> dict[str, Any]:
    """Run pin_manager across all 4 channels AND register the Cloudflare VIP Bot webhook."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(15))
    try:
        pin_results = await sync_all_channel_pins(
            settings=cfg,
            store=pin_store,
            client=http_client,
            force_edit=force_edit_pins,
        )
        webhook_result = await register_telegram_webhook(
            settings=cfg,
            client=http_client,
        )
        return {
            "public_site_url": normalize_site_url(cfg.public_site_url),
            "pins": pin_results,
            "webhook": webhook_result,
        }
    finally:
        if owns_client:
            await http_client.aclose()


if __name__ == "__main__":
    enforce_utf8_runtime()
    summary = asyncio.run(activate_telegram_live())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
