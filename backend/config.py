"""UTF-8 Environment & Unified 6-Key AI + 4-Channel Telegram Config Loader for RatePath (ريت باث).

Enforces UTF-8 console/file encoding on Windows and loads `.env` without external dependencies.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

WORKSPACE_ROOT: Path = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH: Path = WORKSPACE_ROOT / ".env"
CONTRACT_SCHEMA_PATH: Path = WORKSPACE_ROOT / "shared" / "contract.schema.json"
LIVE_FEED_TARGET_PATH: Path = WORKSPACE_ROOT / "frontend" / "public" / "live_feed.json"
CATALOG_FEED_TARGET_PATH: Path = WORKSPACE_ROOT / "frontend" / "public" / "catalog_feed.json"
LOCAL_MEMORY_FALLBACK_PATH: Path = WORKSPACE_ROOT / "backend" / "market_memory.tmp.json"
LOCAL_TELEGRAM_DEDUP_PATH: Path = WORKSPACE_ROOT / "backend" / "telegram_dedup.tmp.json"

# Raw Unicode Regional Indicator pair (U+1F1F8 U+1F1FE) is strictly banned in text outputs
# because un-updated Android/Windows fonts can still render the old flag.
RAW_SYRIA_FLAG_EMOJI: str = "\U0001F1F8\U0001F1FE"
SYRIA_SAFE_TICKER_EN: str = "\U0001F7E2 SYP"
SYRIA_SAFE_TICKER_AR: str = "\U0001F7E2 \u0644.\u0633"
NO_RAW_SYRIA_EMOJI_PROMPT_RULE: str = (
    "STRICT SYMBOL POLICY: NEVER output the raw Unicode Syria flag emoji (U+1F1F8 U+1F1FE), "
    "because un-updated devices may render the old flag. Whenever referencing Syria or the "
    "Syrian Pound in text, strictly use '🟢 SYP' in English and '🟢 ل.س' in Arabic."
)


def sanitize_syria_flag_emoji(
    text: Any,
    lang: Literal["en", "ar"] = "en",
) -> str:
    """Strip any raw Unicode Syria flag emoji and replace with `🟢 SYP` (EN) or `🟢 ل.س` (AR)."""
    raw = str(text or "")
    if RAW_SYRIA_FLAG_EMOJI not in raw:
        return raw
    replacement = SYRIA_SAFE_TICKER_AR if lang == "ar" else SYRIA_SAFE_TICKER_EN
    cleaned = raw.replace(f"{RAW_SYRIA_FLAG_EMOJI}\uFE0F", replacement)
    return cleaned.replace(RAW_SYRIA_FLAG_EMOJI, replacement)

PLACEHOLDER_TOKENS: frozenset[str] = frozenset(
    {"", "placeholder", "none", "null", "changeme", "your_api_key_here"}
)

# Supabase rejects `sb_secret_` keys with HTTP 401 ("Forbidden use of secret API key in browser")
# whenever the User-Agent looks like a browser (contains "Mozilla"). All server-side Supabase
# requests MUST send this explicit non-browser User-Agent.
SUPABASE_SERVER_USER_AGENT: str = "RatePath-Backend/2.0 (server)"


def enforce_utf8_runtime() -> None:
    """Ensure Windows stdio streams use UTF-8 so Arabic script never corrupts."""
    os.environ.setdefault("PYTHONUTF8", "1")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="strict")
            except Exception:
                pass


def parse_dotenv_file(env_path: Path = DEFAULT_ENV_PATH) -> dict[str, str]:
    """Read a `.env` file using explicit UTF-8 encoding."""
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    content = env_path.read_text(encoding="utf-8")
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_val = line.split("=", 1)
        key = key.strip()
        val = raw_val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def is_placeholder_secret(value: str | None) -> bool:
    """Return True when an API key is unset or configured as a placeholder."""
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in PLACEHOLDER_TOKENS:
        return True
    return normalized.startswith(("placeholder_", "your_", "<"))


def _collect_numbered_keys(
    getter: Callable[[str], str],
    base_name: str,
    *,
    slots: int = 3,
) -> tuple[str, ...]:
    """Collect `{base}_1..N` plus optional legacy `{base}` ignoring empty/placeholder entries."""
    collected: list[str] = []
    seen: set[str] = set()
    candidate_names = [f"{base_name}_{idx}" for idx in range(1, slots + 1)] + [base_name]
    for env_name in candidate_names:
        val = getter(env_name)
        if not is_placeholder_secret(val) and val not in seen:
            collected.append(val)
            seen.add(val)
    return tuple(collected)


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings resolved from environment and `.env`."""

    gemini_api_key: str
    groq_api_key: str
    openrouter_api_key: str
    supabase_url: str
    supabase_key: str
    upstash_redis_rest_url: str
    upstash_redis_rest_token: str
    telegram_bot_token: str
    telegram_public_channel: str
    telegram_vip_channel: str
    admin_chat_id: str
    mini_app_url: str = "https://ratepath.io/"
    telegram_public_channel_en: str = "placeholder"
    telegram_vip_channel_en: str = "placeholder"
    telegram_public_channel_ar: str = "placeholder"
    telegram_vip_channel_ar: str = "placeholder"
    gemini_api_keys: tuple[str, ...] = ()
    groq_api_keys: tuple[str, ...] = ()
    cmc_pro_api_key: str = "placeholder"
    public_site_url: str = "https://ratepath.io"
    telegram_webhook_secret: str = "placeholder"

    def __post_init__(self) -> None:
        # Synchronize numbered key pools with single-key fields cleanly (backward-compatible)
        clean_gem = tuple(k.strip() for k in self.gemini_api_keys if not is_placeholder_secret(k))
        if not clean_gem and not is_placeholder_secret(self.gemini_api_key):
            clean_gem = (self.gemini_api_key.strip(),)
        object.__setattr__(self, "gemini_api_keys", clean_gem)
        if clean_gem and is_placeholder_secret(self.gemini_api_key):
            object.__setattr__(self, "gemini_api_key", clean_gem[0])

        clean_groq = tuple(k.strip() for k in self.groq_api_keys if not is_placeholder_secret(k))
        if not clean_groq and not is_placeholder_secret(self.groq_api_key):
            clean_groq = (self.groq_api_key.strip(),)
        object.__setattr__(self, "groq_api_keys", clean_groq)
        if clean_groq and is_placeholder_secret(self.groq_api_key):
            object.__setattr__(self, "groq_api_key", clean_groq[0])

    @classmethod
    def load(cls, env_path: Path = DEFAULT_ENV_PATH, *, prefer_environ: bool = True) -> Settings:
        enforce_utf8_runtime()
        file_vars = parse_dotenv_file(env_path)

        def _get(name: str) -> str:
            if prefer_environ and name in os.environ:
                return os.environ[name].strip()
            return file_vars.get(name, os.environ.get(name, "placeholder")).strip()

        gemini_pool = _collect_numbered_keys(_get, "GEMINI_API_KEY", slots=3)
        groq_pool = _collect_numbered_keys(_get, "GROQ_API_KEY", slots=3)

        raw_mini_app = (
            _get("MINI_APP_URL")
            if not is_placeholder_secret(_get("MINI_APP_URL"))
            else "https://ratepath.io/"
        )
        raw_site = (
            _get("PUBLIC_SITE_URL")
            if not is_placeholder_secret(_get("PUBLIC_SITE_URL"))
            else raw_mini_app.rstrip("/")
        )

        return cls(
            gemini_api_key=gemini_pool[0] if gemini_pool else "placeholder",
            groq_api_key=groq_pool[0] if groq_pool else "placeholder",
            openrouter_api_key=_get("OPENROUTER_API_KEY"),
            supabase_url=_get("SUPABASE_URL"),
            supabase_key=_get("SUPABASE_KEY"),
            upstash_redis_rest_url=_get("UPSTASH_REDIS_REST_URL"),
            upstash_redis_rest_token=_get("UPSTASH_REDIS_REST_TOKEN"),
            telegram_bot_token=_get("TELEGRAM_BOT_TOKEN"),
            telegram_public_channel=_get("TELEGRAM_PUBLIC_CHANNEL"),
            telegram_vip_channel=_get("TELEGRAM_VIP_CHANNEL"),
            telegram_public_channel_en=_get("TELEGRAM_PUBLIC_CHANNEL_EN"),
            telegram_vip_channel_en=_get("TELEGRAM_VIP_CHANNEL_EN"),
            telegram_public_channel_ar=_get("TELEGRAM_PUBLIC_CHANNEL_AR"),
            telegram_vip_channel_ar=_get("TELEGRAM_VIP_CHANNEL_AR"),
            admin_chat_id=_get("ADMIN_CHAT_ID"),
            mini_app_url=raw_mini_app,
            gemini_api_keys=gemini_pool,
            groq_api_keys=groq_pool,
            cmc_pro_api_key=_get("CMC_PRO_API_KEY"),
            public_site_url=raw_site.rstrip("/") or "https://ratepath.io",
            telegram_webhook_secret=_get("TELEGRAM_WEBHOOK_SECRET"),
        )

    @property
    def active_ai_key_counts(self) -> dict[str, int]:
        openrouter_count = 0 if is_placeholder_secret(self.openrouter_api_key) else 1
        return {
            "gemini": len(self.gemini_api_keys),
            "groq": len(self.groq_api_keys),
            "openrouter": openrouter_count,
            "total": len(self.gemini_api_keys) + len(self.groq_api_keys) + openrouter_count,
        }

    @property
    def has_live_llm(self) -> bool:
        return self.active_ai_key_counts["total"] > 0

    @property
    def has_live_supabase(self) -> bool:
        return not (
            is_placeholder_secret(self.supabase_url)
            or is_placeholder_secret(self.supabase_key)
        )

    @property
    def has_live_upstash(self) -> bool:
        return not (
            is_placeholder_secret(self.upstash_redis_rest_url)
            or is_placeholder_secret(self.upstash_redis_rest_token)
        )

    @property
    def has_live_telegram(self) -> bool:
        return not is_placeholder_secret(self.telegram_bot_token)

    def resolve_telegram_targets(self) -> dict[str, str]:
        """Resolve active EN/AR channel IDs with backward-compatible legacy single-var fallback."""
        targets: dict[str, str] = {}
        has_split_public = not (
            is_placeholder_secret(self.telegram_public_channel_en)
            and is_placeholder_secret(self.telegram_public_channel_ar)
        )
        has_split_vip = not (
            is_placeholder_secret(self.telegram_vip_channel_en)
            and is_placeholder_secret(self.telegram_vip_channel_ar)
        )

        if not is_placeholder_secret(self.telegram_public_channel_en):
            targets["public_en"] = self.telegram_public_channel_en
        if not is_placeholder_secret(self.telegram_public_channel_ar):
            targets["public_ar"] = self.telegram_public_channel_ar
        if not has_split_public and not is_placeholder_secret(self.telegram_public_channel):
            targets["public_legacy"] = self.telegram_public_channel

        if not is_placeholder_secret(self.telegram_vip_channel_en):
            targets["vip_en"] = self.telegram_vip_channel_en
        if not is_placeholder_secret(self.telegram_vip_channel_ar):
            targets["vip_ar"] = self.telegram_vip_channel_ar
        if not has_split_vip and not is_placeholder_secret(self.telegram_vip_channel):
            targets["vip_legacy"] = self.telegram_vip_channel

        return targets
