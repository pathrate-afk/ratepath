"""Standalone Change-Only Rates Bulletin for RatePath (ريت باث).

Architectural Rules:
1. Triggered ONLY from `backend/run_cycle.py` (`ratepath_worker.yml`), completely separate from News Alerts.
2. Fires ONLY when:
   - Minimum 3-hour cooldown (`10800` seconds) has elapsed since `ratepath:last_bulletin_ts`, AND
   - At least one tracked asset moved meaningfully vs `ratepath:last_broadcast_rates` in Upstash Redis:
     * `>= 0.50%` for FX, Gold, Silver, or SYP
     * `>= 2.00%` for Crypto (`BTC`, `ETH`, `SOL`)
3. Includes ONLY the assets that actually changed — NEVER broadcasts static/unchanged rates.
4. Strict Anti-Scramble BiDi Layout:
   - NEVER puts multiple currencies on one horizontal line separated by `|`.
   - Strictly ONE asset per line, grouped under spaced headers (`\\n\\n`), with prices wrapped in `<code>...</code>`.
   - Uses `🟢 الليرة السورية` (AR) / `🟢 SYP` (EN) — ZERO raw Unicode Syria flag emoji.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Literal

import httpx

from backend.agents.price_guardian import SYP_NEW_MAX_RATE, SYP_NEW_MIN_RATE
from backend.config import (
    WORKSPACE_ROOT,
    Settings,
    enforce_utf8_runtime,
    sanitize_syria_flag_emoji,
)
from backend.math_engine import QUANT_2DP, QUANT_4DP, format_decimal, parse_strict_decimal
from backend.telegram_bot import (
    _esc,
    _send_telegram_payload,
    build_lang_mini_app_url,
    normalize_preserve_newlines,
)

LAST_BROADCAST_RATES_KEY: str = "ratepath:last_broadcast_rates"
LAST_BULLETIN_TS_KEY: str = "ratepath:last_bulletin_ts"
BULLETIN_COOLDOWN_SECONDS: int = 10800  # 3 hours minimum cooldown

FX_GOLD_SYP_THRESHOLD_PCT: Decimal = Decimal("0.50")
CRYPTO_THRESHOLD_PCT: Decimal = Decimal("2.00")
PCT_MULTIPLIER: Decimal = Decimal("100")

LOCAL_BULLETIN_STATE_PATH: Path = WORKSPACE_ROOT / "backend" / "rates_bulletin_state.tmp.json"
EPOCH_UTC: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)

AssetGroup = Literal["gold", "fiat", "crypto"]


@dataclass(frozen=True)
class AssetDisplaySpec:
    asset_id: str
    group: AssetGroup
    emoji: str
    label_en: str
    label_ar: str
    prefix: str
    suffix_en: str
    suffix_ar: str
    quant: Decimal
    threshold_pct: Decimal


TRACKED_ASSET_SPECS: tuple[AssetDisplaySpec, ...] = (
    # Gold & Metals (>= 0.50% move)
    AssetDisplaySpec("XAU_OZ", "gold", "🪙", "Gold Ounce (XAU)", "أونصة الذهب", "$", "", "", QUANT_2DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("GRAM_24K", "gold", "🪙", "Gold 24K", "عيار 24", "$", "", "", QUANT_2DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("GRAM_21K", "gold", "🪙", "Gold 21K", "عيار 21", "$", "", "", QUANT_2DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("GRAM_18K", "gold", "🪙", "Gold 18K", "عيار 18", "$", "", "", QUANT_2DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("XAG_OZ", "gold", "⚪", "Silver Ounce (XAG)", "أونصة الفضة", "$", "", "", QUANT_2DP, FX_GOLD_SYP_THRESHOLD_PCT),
    # World & Arab FX + SP-Today SYP (>= 0.50% move)
    AssetDisplaySpec("SYP", "fiat", "🟢", "SYP", "الليرة السورية", "", " SYP", " ل.س", QUANT_2DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("EUR", "fiat", "💶", "Euro (USD/EUR)", "اليورو", "", "", "", QUANT_4DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("GBP", "fiat", "💷", "British Pound (USD/GBP)", "الجنيه الإسترليني", "", "", "", QUANT_4DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("TRY", "fiat", "💱", "Turkish Lira (USD/TRY)", "الليرة التركية", "", " TRY", " ل.ت", QUANT_2DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("EGP", "fiat", "💱", "Egyptian Pound (USD/EGP)", "الجنيه المصري", "", " EGP", " ج.م", QUANT_2DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("SAR", "fiat", "💱", "Saudi Riyal (USD/SAR)", "الريال السعودي", "", " SAR", " ر.س", QUANT_4DP, FX_GOLD_SYP_THRESHOLD_PCT),
    AssetDisplaySpec("AED", "fiat", "💱", "UAE Dirham (USD/AED)", "الدرهم الإماراتي", "", " AED", " د.إ", QUANT_4DP, FX_GOLD_SYP_THRESHOLD_PCT),
    # Crypto (>= 2.00% move)
    AssetDisplaySpec("BTC", "crypto", "₿", "Bitcoin (BTC)", "بيتكوين", "$", "", "", QUANT_2DP, CRYPTO_THRESHOLD_PCT),
    AssetDisplaySpec("ETH", "crypto", "Ξ", "Ethereum (ETH)", "إيثيريوم", "$", "", "", QUANT_2DP, CRYPTO_THRESHOLD_PCT),
    AssetDisplaySpec("SOL", "crypto", "◎", "Solana (SOL)", "سولانا", "$", "", "", QUANT_2DP, CRYPTO_THRESHOLD_PCT),
)

_SPEC_BY_ID: dict[str, AssetDisplaySpec] = {s.asset_id: s for s in TRACKED_ASSET_SPECS}


@dataclass(frozen=True)
class RateMover:
    """Single asset that crossed its significant-change threshold."""

    spec: AssetDisplaySpec
    previous_price: Decimal
    current_price: Decimal
    change_pct: Decimal


def _utc_epoch_seconds(now: datetime | None = None) -> int:
    dt = now or datetime.now(timezone.utc)
    delta = dt - EPOCH_UTC
    return delta.days * 86400 + delta.seconds


def _format_comma_decimal(val: Decimal, quant: Decimal) -> str:
    formatted = format_decimal(val, quant)
    if "." in formatted:
        int_part, frac_part = formatted.split(".", 1)
        return f"{int(int_part):,}.{frac_part}"
    return f"{int(formatted):,}"


def extract_tracked_rates(
    feed_payload: dict[str, Any],
    catalog_payload: dict[str, Any] | None = None,
) -> dict[str, Decimal]:
    """Extract clean Decimal spot prices for all tracked Gold/FX/SYP/Crypto assets."""
    rates: dict[str, Decimal] = {}

    gold_prices = feed_payload.get("gold", {}).get("prices", {})
    for g_key in ("XAU_OZ", "GRAM_24K", "GRAM_21K", "GRAM_18K"):
        raw = gold_prices.get(g_key)
        if raw is not None:
            try:
                rates[g_key] = parse_strict_decimal(raw, field_name=f"bulletin.{g_key}")
            except Exception:
                pass

    if catalog_payload:
        xag_raw = catalog_payload.get("metals", {}).get("XAG_OZ", {}).get("price_usd")
        if xag_raw is not None:
            try:
                rates["XAG_OZ"] = parse_strict_decimal(xag_raw, field_name="bulletin.XAG_OZ")
            except Exception:
                pass

    fiat_rates = feed_payload.get("fiat", {}).get("rates", {})
    for f_key in ("EUR", "GBP", "TRY", "EGP", "SAR", "AED"):
        entry = fiat_rates.get(f_key, {})
        raw = entry.get("units_per_usd") if isinstance(entry, dict) else None
        if raw is not None:
            try:
                rates[f_key] = parse_strict_decimal(raw, field_name=f"bulletin.{f_key}")
            except Exception:
                pass

    # SYP: enforce strict new-SYP sanity range (50 < rate < 500) so bank numbers can NEVER leak
    syp_raw = None
    if catalog_payload:
        cat_syp = catalog_payload.get("fiat", {}).get("SYP", {})
        if isinstance(cat_syp, dict) and cat_syp.get("source") == "sp-today":
            syp_raw = cat_syp.get("units_per_usd")
    if syp_raw is None:
        feed_syp = fiat_rates.get("SYP", {})
        if isinstance(feed_syp, dict):
            syp_raw = feed_syp.get("units_per_usd")
    if syp_raw is not None:
        try:
            syp_dec = parse_strict_decimal(syp_raw, field_name="bulletin.SYP")
            if SYP_NEW_MIN_RATE < syp_dec < SYP_NEW_MAX_RATE:
                rates["SYP"] = syp_dec
        except Exception:
            pass

    crypto_assets = feed_payload.get("crypto", {}).get("assets", {})
    for c_key in ("BTC", "ETH", "SOL"):
        entry = crypto_assets.get(c_key, {})
        raw = entry.get("spot_price") if isinstance(entry, dict) else None
        if raw is not None:
            try:
                rates[c_key] = parse_strict_decimal(raw, field_name=f"bulletin.{c_key}")
            except Exception:
                pass

    return rates


def detect_significant_rate_movers(
    current_rates: dict[str, Decimal],
    previous_rates: dict[str, Decimal],
) -> list[RateMover]:
    """Return ONLY assets whose move meets or exceeds their threshold (>=0.5% FX/Gold/SYP, >=2.0% Crypto)."""
    movers: list[RateMover] = []
    for spec in TRACKED_ASSET_SPECS:
        curr = current_rates.get(spec.asset_id)
        prev = previous_rates.get(spec.asset_id)
        if curr is None or prev is None or prev <= Decimal("0"):
            continue
        with localcontext() as ctx:
            ctx.prec = 28
            pct = ((curr - prev) / prev) * PCT_MULTIPLIER
        if abs(pct) >= spec.threshold_pct:
            movers.append(
                RateMover(
                    spec=spec,
                    previous_price=prev,
                    current_price=curr,
                    change_pct=pct,
                )
            )
    return movers


def format_single_mover_line(mover: RateMover, lang: Literal["en", "ar"]) -> str:
    """Format strictly ONE asset line with price in `<code>...</code>` (zero `|` horizontal packing)."""
    spec = mover.spec
    price_num = _format_comma_decimal(mover.current_price, spec.quant)
    suffix = spec.suffix_ar if lang == "ar" else spec.suffix_en
    code_price = f"{spec.prefix}{price_num}{suffix}"

    pct_q = format_decimal(abs(mover.change_pct), Decimal("0.1"))
    if mover.change_pct >= Decimal("0"):
        arrow_badge = f"(▲ +{pct_q}%)"
    else:
        arrow_badge = f"(▼ -{pct_q}%)"

    label = spec.label_ar if lang == "ar" else spec.label_en
    return f"{spec.emoji} {_esc(label)}: <code>{_esc(code_price)}</code> {arrow_badge}"


def build_rates_bulletin_payload(
    movers: list[RateMover],
    *,
    chat_id: str,
    mini_app_url: str = "https://ratepath.io/",
    lang: Literal["en", "ar"] = "ar",
) -> dict[str, Any]:
    """Build anti-scramble 1-asset-per-line Rates Bulletin separated by `\\n\\n`."""
    if not movers:
        raise ValueError("Cannot build change-only Rates Bulletin with zero moved assets.")

    group_headers: dict[AssetGroup, tuple[str, str]] = {
        "gold": ("🟡 <b>Gold &amp; Metals:</b>", "🟡 <b>الذهب والمعادن:</b>"),
        "fiat": ("💱 <b>Currencies:</b>", "💱 <b>العملات:</b>"),
        "crypto": ("₿ <b>Crypto:</b>", "₿ <b>العملات الرقمية:</b>"),
    }

    sections: list[str] = []
    if lang == "ar":
        sections.append("📊 <b>تحديث حركة الأسعار — ريت باث</b>")
        launch_url = build_lang_mini_app_url(mini_app_url, "ar")
        btn_label = "📊 افتح محول الأسعار المباشر"
        footer = "⏱ <i>نشرة التغيّرات الفعلية فقط — ريت باث</i>"
    else:
        sections.append("📊 <b>Market Movers — RatePath</b>")
        launch_url = build_lang_mini_app_url(mini_app_url, "en")
        btn_label = "📊 Open Live Converter"
        footer = "⏱ <i>Significant movers only — RatePath</i>"

    for grp in ("gold", "fiat", "crypto"):
        grp_movers = [m for m in movers if m.spec.group == grp]
        if not grp_movers:
            continue
        hdr = group_headers[grp][1] if lang == "ar" else group_headers[grp][0]
        lines = [hdr] + [format_single_mover_line(m, lang) for m in grp_movers]
        sections.append("\n".join(lines))

    sections.append(footer)
    text = sanitize_syria_flag_emoji(
        normalize_preserve_newlines("\n\n".join(sections)),
        lang=lang,
    )

    assert text.count("\n\n") >= 2, "Rates bulletin must preserve >= 2 paragraph blank lines"
    assert "|" not in text, "Rates bulletin must NEVER pack multiple assets on one line with '|'"

    return {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": {
            "inline_keyboard": [[{"text": btn_label, "web_app": {"url": launch_url}}]]
        },
    }


class RatesBulletinStateStore:
    """Persists `ratepath:last_broadcast_rates` and `ratepath:last_bulletin_ts` in Upstash Redis + local fallback."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        local_state_path: Path = LOCAL_BULLETIN_STATE_PATH,
    ) -> None:
        self.settings = settings or Settings.load()
        self.local_state_path = local_state_path

    def _read_local(self) -> dict[str, Any]:
        if not self.local_state_path.exists():
            return {}
        try:
            data = json.loads(self.local_state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_local(self, payload: dict[str, Any]) -> None:
        self.local_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    async def load_state(
        self, *, client: httpx.AsyncClient | None = None
    ) -> tuple[dict[str, Decimal], int | None]:
        """Load last broadcast rates and timestamp from Upstash Redis (or local fallback)."""
        local = self._read_local()
        rates_raw: dict[str, Any] = local.get("rates", {}) if isinstance(local.get("rates"), dict) else {}
        last_ts: int | None = int(local["last_ts"]) if local.get("last_ts") is not None else None

        if self.settings.has_live_upstash:
            owns_client = client is None
            http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
            try:
                endpoint = self.settings.upstash_redis_rest_url.rstrip("/")
                headers = {
                    "Authorization": f"Bearer {self.settings.upstash_redis_rest_token}",
                    "Content-Type": "application/json",
                }
                r_rates = await http_client.post(
                    endpoint,
                    headers=headers,
                    content=json.dumps(["GET", LAST_BROADCAST_RATES_KEY], ensure_ascii=False).encode("utf-8"),
                )
                if r_rates.status_code == 200:
                    res_val = json.loads(r_rates.text).get("result")
                    if isinstance(res_val, str) and res_val.strip():
                        parsed_rates = json.loads(res_val)
                        if isinstance(parsed_rates, dict):
                            rates_raw = parsed_rates

                r_ts = await http_client.post(
                    endpoint,
                    headers=headers,
                    content=json.dumps(["GET", LAST_BULLETIN_TS_KEY], ensure_ascii=False).encode("utf-8"),
                )
                if r_ts.status_code == 200:
                    ts_val = json.loads(r_ts.text).get("result")
                    if ts_val is not None and str(ts_val).strip():
                        last_ts = int(str(ts_val).strip())
            except Exception:
                pass
            finally:
                if owns_client:
                    await http_client.aclose()

        parsed: dict[str, Decimal] = {}
        for k, v in rates_raw.items():
            try:
                parsed[k] = parse_strict_decimal(v, field_name=f"last_rates.{k}")
            except Exception:
                continue
        return parsed, last_ts

    async def save_state(
        self,
        rates: dict[str, Decimal],
        last_ts: int | None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Save latest baseline rates & optional bulletin timestamp to Upstash Redis + local fallback."""
        serialized_rates = {
            k: format_decimal(v, _SPEC_BY_ID[k].quant if k in _SPEC_BY_ID else QUANT_4DP)
            for k, v in rates.items()
        }
        self._write_local({"rates": serialized_rates, "last_ts": last_ts})

        if not self.settings.has_live_upstash:
            return

        owns_client = client is None
        http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(8))
        try:
            endpoint = self.settings.upstash_redis_rest_url.rstrip("/")
            headers = {
                "Authorization": f"Bearer {self.settings.upstash_redis_rest_token}",
                "Content-Type": "application/json",
            }
            await http_client.post(
                endpoint,
                headers=headers,
                content=json.dumps(
                    ["SET", LAST_BROADCAST_RATES_KEY, json.dumps(serialized_rates, ensure_ascii=False)],
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
            if last_ts is not None:
                await http_client.post(
                    endpoint,
                    headers=headers,
                    content=json.dumps(
                        ["SET", LAST_BULLETIN_TS_KEY, str(last_ts)],
                        ensure_ascii=False,
                    ).encode("utf-8"),
                )
        except Exception:
            pass
        finally:
            if owns_client:
                await http_client.aclose()


async def maybe_broadcast_rates_bulletin(
    feed_payload: dict[str, Any],
    catalog_payload: dict[str, Any] | None = None,
    *,
    settings: Settings | None = None,
    state_store: RatesBulletinStateStore | None = None,
    client: httpx.AsyncClient | None = None,
    now_utc: datetime | None = None,
    cooldown_seconds: int = BULLETIN_COOLDOWN_SECONDS,
) -> dict[str, Any]:
    """Check 3h cooldown & change thresholds; send 1-asset-per-line Rates Bulletin ONLY when changed."""
    enforce_utf8_runtime()
    cfg = settings or Settings.load()
    store = state_store or RatesBulletinStateStore(settings=cfg)
    now_ts = _utc_epoch_seconds(now_utc)

    current_rates = extract_tracked_rates(feed_payload, catalog_payload)
    previous_rates, last_ts = await store.load_state(client=client)

    # First run with no baseline -> store baseline silently (zero spam)
    if not previous_rates:
        await store.save_state(current_rates, last_ts, client=client)
        return {"status": "baseline_initialized", "sent": 0, "movers": []}

    # Enforce minimum 3-hour cooldown between bulletins
    if last_ts is not None and (now_ts - last_ts) < cooldown_seconds:
        remaining = cooldown_seconds - (now_ts - last_ts)
        return {
            "status": "cooldown_active",
            "sent": 0,
            "movers": [],
            "next_bulletin_in_seconds": remaining,
        }

    movers = detect_significant_rate_movers(current_rates, previous_rates)
    if not movers:
        return {"status": "no_significant_change", "sent": 0, "movers": []}

    if not cfg.has_live_telegram:
        return {
            "status": "skipped_placeholder_telegram",
            "sent": 0,
            "movers": [m.spec.asset_id for m in movers],
        }

    targets = cfg.resolve_telegram_targets()
    dispatch_targets: tuple[tuple[str, Literal["en", "ar"]], ...] = (
        ("public_en", "en"),
        ("vip_en", "en"),
        ("public_ar", "ar"),
        ("vip_ar", "ar"),
    )

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(12))
    sent_count = 0
    try:
        for key, lang_code in dispatch_targets:
            chat_id = targets.get(key)
            if not chat_id:
                continue
            payload = build_rates_bulletin_payload(
                movers,
                chat_id=chat_id,
                mini_app_url=cfg.mini_app_url,
                lang=lang_code,
            )
            await _send_telegram_payload(
                http_client,
                cfg.telegram_bot_token,
                payload,
                enforce_paragraph_spacing=True,
            )
            sent_count += 1
    finally:
        if owns_client:
            await http_client.aclose()

    await store.save_state(current_rates, now_ts, client=client)
    return {
        "status": "sent",
        "sent": sent_count,
        "movers": [m.spec.asset_id for m in movers],
    }
