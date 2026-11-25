"""Single Pinned Website Header Manager for RatePath (ريت باث) Telegram Channels.

Policy:
- Sends ONE clean website header message per EN/AR channel and pins it silently
  (`pinChatMessage` with `disable_notification=True`).
- Persists `ratepath:pinned_msg_id:{chat_id}` in Upstash Redis (with local UTF-8 JSON fallback).
- Idempotent: never sends a duplicate pinned message when already active.
- If `PUBLIC_SITE_URL` changes later, edits the existing pinned message in-place via `editMessageText`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

from backend.config import (
    WORKSPACE_ROOT,
    Settings,
    enforce_utf8_runtime,
    sanitize_syria_flag_emoji,
)

PINNED_MSG_KEY_PREFIX: str = "ratepath:pinned_msg_id:"
LOCAL_PIN_STORE_PATH: Path = WORKSPACE_ROOT / "backend" / "pinned_messages.tmp.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_site_url(site_url: str) -> str:
    cleaned = (site_url or "https://ratepath.io").strip().rstrip("/")
    return cleaned if cleaned.startswith(("https://", "http://")) else "https://ratepath.io"


def build_pinned_header_text(site_url: str, lang: Literal["en", "ar"]) -> str:
    """Return the exact canonical AR/EN pinned website header message."""
    base = normalize_site_url(site_url)
    if lang == "ar":
        raw = (
            "🌐 <b>لمزيد من المعلومات وتفاصيل أكثر وأسعار السوق اللحظية، "
            "قم بزيارة الموقع الرسمي:</b>\n"
            f"{base}/?lang=ar"
        )
        return sanitize_syria_flag_emoji(raw, lang="ar")

    raw = (
        "🌐 <b>For deeper market details, full analytics, and live rates, "
        "visit our official website:</b>\n"
        f"{base}/?lang=en"
    )
    return sanitize_syria_flag_emoji(raw, lang="en")


def pinned_redis_key(chat_id: str) -> str:
    return f"{PINNED_MSG_KEY_PREFIX}{chat_id}"


class PinnedMessageStore:
    """Stores `ratepath:pinned_msg_id:{chat_id}` in Upstash Redis REST + local UTF-8 fallback."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        local_path: Path = LOCAL_PIN_STORE_PATH,
    ) -> None:
        self.settings = settings or Settings.load()
        self.local_path = local_path

    def _read_local(self) -> dict[str, Any]:
        if not self.local_path.exists():
            return {}
        try:
            data = json.loads(self.local_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_local(self, mapping: dict[str, Any]) -> None:
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_path.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    @staticmethod
    def _parse_record(raw_val: Any) -> dict[str, Any] | None:
        if raw_val is None:
            return None
        if isinstance(raw_val, dict) and raw_val.get("message_id") is not None:
            return {
                "message_id": int(raw_val["message_id"]),
                "site_url": str(raw_val.get("site_url", "")),
                "lang": str(raw_val.get("lang", "")),
            }
        text = str(raw_val).strip()
        if not text:
            return None
        if text.isdigit():
            return {"message_id": int(text), "site_url": "", "lang": ""}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("message_id") is not None:
                return {
                    "message_id": int(parsed["message_id"]),
                    "site_url": str(parsed.get("site_url", "")),
                    "lang": str(parsed.get("lang", "")),
                }
        except Exception:
            return None
        return None

    async def get_pinned_record(
        self,
        chat_id: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any] | None:
        key = pinned_redis_key(chat_id)
        if self.settings.has_live_upstash:
            owns_client = client is None
            http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
            try:
                endpoint = self.settings.upstash_redis_rest_url.rstrip("/")
                resp = await http_client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.upstash_redis_rest_token}",
                        "Content-Type": "application/json",
                    },
                    content=json.dumps(["GET", key], ensure_ascii=False).encode("utf-8"),
                )
                if resp.status_code == 200:
                    rec = self._parse_record(json.loads(resp.text).get("result"))
                    if rec is not None:
                        return rec
            except Exception:
                pass
            finally:
                if owns_client:
                    await http_client.aclose()

        local_map = self._read_local()
        return self._parse_record(local_map.get(key))

    async def save_pinned_record(
        self,
        chat_id: str,
        message_id: int,
        site_url: str,
        lang: Literal["en", "ar"],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        key = pinned_redis_key(chat_id)
        record = {
            "message_id": int(message_id),
            "site_url": normalize_site_url(site_url),
            "lang": lang,
            "updated_at": _utc_now_iso(),
        }
        local_map = self._read_local()
        local_map[key] = record
        self._write_local(local_map)

        if not self.settings.has_live_upstash:
            return

        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
        try:
            endpoint = self.settings.upstash_redis_rest_url.rstrip("/")
            await http_client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self.settings.upstash_redis_rest_token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(
                    ["SET", key, json.dumps(record, ensure_ascii=False)],
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
        except Exception:
            pass
        finally:
            if owns_client:
                await http_client.aclose()


async def _tg_api_post(
    client: httpx.AsyncClient,
    bot_token: str,
    method: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    resp = await client.post(
        url,
        headers={"Content-Type": "application/json"},
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )
    try:
        body = json.loads(resp.text)
    except Exception:
        body = {"ok": False, "description": resp.text[:200]}
    return resp.status_code, body


async def ensure_pinned_channel_header(
    chat_id: str,
    lang: Literal["en", "ar"],
    *,
    settings: Settings | None = None,
    store: PinnedMessageStore | None = None,
    client: httpx.AsyncClient | None = None,
    force_edit: bool = False,
) -> dict[str, Any]:
    """Ensure ONE pinned website header exists for `chat_id`; edit in-place if URL changed."""
    cfg = settings or Settings.load()
    if not cfg.has_live_telegram:
        return {"chat_id": chat_id, "lang": lang, "status": "skipped_placeholder_telegram"}

    pin_store = store or PinnedMessageStore(settings=cfg)
    site_url = normalize_site_url(cfg.public_site_url)
    header_text = build_pinned_header_text(site_url, lang)

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    try:
        existing = await pin_store.get_pinned_record(chat_id, client=http_client)

        if existing is not None:
            msg_id = int(existing["message_id"])
            if not force_edit and normalize_site_url(existing.get("site_url", "")) == site_url and existing.get("site_url"):
                return {
                    "chat_id": chat_id,
                    "lang": lang,
                    "status": "unchanged",
                    "message_id": msg_id,
                    "site_url": site_url,
                }

            # Site URL changed (or force_edit) -> update existing pinned message in-place!
            status_code, edit_body = await _tg_api_post(
                http_client,
                cfg.telegram_bot_token,
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": msg_id,
                    "text": header_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            desc = str(edit_body.get("description", "")).lower()
            if edit_body.get("ok") or "message is not modified" in desc:
                await pin_store.save_pinned_record(
                    chat_id, msg_id, site_url, lang, client=http_client
                )
                return {
                    "chat_id": chat_id,
                    "lang": lang,
                    "status": "edited_in_place",
                    "message_id": msg_id,
                    "site_url": site_url,
                }
            # Only fall through to send+pin if the original message was manually deleted
            if status_code not in (400, 404):
                return {
                    "chat_id": chat_id,
                    "lang": lang,
                    "status": "edit_failed",
                    "error": edit_body.get("description", f"HTTP {status_code}"),
                }

        # No valid existing pinned message -> send ONE message and pin silently
        send_status, send_body = await _tg_api_post(
            http_client,
            cfg.telegram_bot_token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": header_text,
                "parse_mode": "HTML",
                "disable_notification": True,
                "disable_web_page_preview": True,
            },
        )
        if not send_body.get("ok"):
            return {
                "chat_id": chat_id,
                "lang": lang,
                "status": "send_failed",
                "error": send_body.get("description", f"HTTP {send_status}"),
            }

        new_msg_id = int(send_body["result"]["message_id"])
        _pin_status, pin_body = await _tg_api_post(
            http_client,
            cfg.telegram_bot_token,
            "pinChatMessage",
            {
                "chat_id": chat_id,
                "message_id": new_msg_id,
                "disable_notification": True,
            },
        )
        await pin_store.save_pinned_record(
            chat_id, new_msg_id, site_url, lang, client=http_client
        )
        return {
            "chat_id": chat_id,
            "lang": lang,
            "status": "sent_and_pinned",
            "pinned_ok": bool(pin_body.get("ok")),
            "message_id": new_msg_id,
            "site_url": site_url,
        }
    finally:
        if owns_client:
            await http_client.aclose()


async def sync_all_channel_pins(
    *,
    settings: Settings | None = None,
    store: PinnedMessageStore | None = None,
    client: httpx.AsyncClient | None = None,
    force_edit: bool = False,
) -> list[dict[str, Any]]:
    """Sync the single pinned website header across all active EN/AR channels."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    targets = cfg.resolve_telegram_targets()
    channel_specs: tuple[tuple[str, Literal["en", "ar"]], ...] = (
        ("public_en", "en"),
        ("vip_en", "en"),
        ("public_ar", "ar"),
        ("vip_ar", "ar"),
    )

    results: list[dict[str, Any]] = []
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    try:
        for key, lang_code in channel_specs:
            chat_id = targets.get(key)
            if not chat_id:
                continue
            res = await ensure_pinned_channel_header(
                chat_id,
                lang_code,
                settings=cfg,
                store=store,
                client=http_client,
                force_edit=force_edit,
            )
            results.append({"channel_key": key, **res})
        return results
    finally:
        if owns_client:
            await http_client.aclose()


if __name__ == "__main__":
    enforce_utf8_runtime()
    out = asyncio.run(sync_all_channel_pins())
    print(json.dumps({"channels": out}, ensure_ascii=False, indent=2))
