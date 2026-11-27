"""Tests for US & Middle East Market Session Bells, 4-Hour Exclusive News Digest, Noise Filtering & Clean Arrows."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.fetchers import _NOISE_HEADLINE_RE
from backend.market_sessions import (
    build_session_brief_payload,
    detect_due_market_session,
    is_us_daylight_saving,
)
from backend.news_digest_4h import (
    Digest4hSynthesis,
    build_4h_digest_payload,
    detect_4h_digest_slot,
    extract_top_crypto_movers,
)
from tests.test_contract_and_publisher import make_valid_feed_payload


def test_us_and_middle_east_session_schedule_and_dst() -> None:
    """Verify exact UTC trigger windows for ME Open, ME Close/US Pre-Open, US Open & US Close across DST."""
    # Winter (November 26, 2026 = Thursday, EST = UTC-5)
    assert is_us_daylight_saving(datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)) is False
    assert detect_due_market_session(datetime(2026, 11, 26, 7, 5, tzinfo=timezone.utc)) == "me_open"
    assert detect_due_market_session(datetime(2026, 11, 26, 12, 5, tzinfo=timezone.utc)) == "me_close_us_preopen"
    assert detect_due_market_session(datetime(2026, 11, 26, 14, 35, tzinfo=timezone.utc)) == "us_open"
    assert detect_due_market_session(datetime(2026, 11, 26, 21, 5, tzinfo=timezone.utc)) == "us_close"
    # Saturday: all markets closed
    assert detect_due_market_session(datetime(2026, 11, 28, 7, 5, tzinfo=timezone.utc)) is None
    assert detect_due_market_session(datetime(2026, 11, 28, 14, 35, tzinfo=timezone.utc)) is None

    # Summer (July 15, 2026 = Wednesday, EDT = UTC-4 -> US Open at 13:30 UTC, Close at 20:00 UTC)
    assert is_us_daylight_saving(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)) is True
    assert detect_due_market_session(datetime(2026, 7, 15, 13, 35, tzinfo=timezone.utc)) == "us_open"
    assert detect_due_market_session(datetime(2026, 7, 15, 20, 5, tzinfo=timezone.utc)) == "us_close"


def test_session_brief_clean_formatting_and_public_promo_vs_vip() -> None:
    """Session bells carry FX, Gold & BTC with clean arrows, promo on Public, and zero filler/VIP nagging."""
    feed = make_valid_feed_payload()
    catalog = {
        "metals": {"XAG_OZ": {"price_usd": "34.25"}},
        "crypto": [
            {"symbol": "BTC", "name": "Bitcoin", "price_usd": "95400.00", "change_24h_pct": "+3.4"},
            {"symbol": "ETH", "name": "Ethereum", "price_usd": "3210.00", "change_24h_pct": "-2.1"},
            {"symbol": "SOL", "name": "Solana", "price_usd": "188.50", "change_24h_pct": "+5.8"},
        ],
    }

    pub_ar = build_session_brief_payload("us_open", feed, catalog, chat_id="@RatePath_AR", lang="ar", is_vip=False)["text"]
    vip_ar = build_session_brief_payload("us_open", feed, catalog, chat_id="@RatePathVIP_AR", lang="ar", is_vip=True)["text"]

    assert "افتتاح السوق الأمريكي" in pub_ar
    assert "ratepathlive.com" in pub_ar  # Promotional funnel in Public
    assert "@RatePathTerminalBot" in pub_ar
    assert "▲ (+3.4%)" in pub_ar
    assert "🔻 (-2.1%)" in pub_ar

    # VIP has extended Silver/24K/GBP/SOL desk & ZERO repetitive VIP nagging or Conviction noise
    assert "أونصة الفضة" in vip_ar
    assert "سولانا" in vip_ar
    assert "غرام 24" in vip_ar
    for msg in (pub_ar, vip_ar):
        assert "🟢 ⬆️" not in msg
        assert "🔴 ⬇️" not in msg
        assert "القناعة" not in msg
        assert "[VIP]" not in msg


def test_4h_exclusive_digest_slots_movers_and_noise_blocker() -> None:
    """Verify 4-hour schedule, crypto mover extraction, noise filtering, and Public vs VIP digest separation."""
    assert detect_4h_digest_slot(datetime(2026, 11, 26, 8, 5, tzinfo=timezone.utc)) == "2026-11-26:H08"
    assert detect_4h_digest_slot(datetime(2026, 11, 26, 9, 5, tzinfo=timezone.utc)) is None

    # Noise blocker permanently rejects MarketWatch personal-finance lifestyle columns
    assert _NOISE_HEADLINE_RE.search("I've been a stay-at-home mom, should my husband raid his 401(k)?")
    assert not _NOISE_HEADLINE_RE.search("Tether mints $1B USDT as institutional crypto inflows surge")

    movers = extract_top_crypto_movers(
        {
            "crypto": [
                {"symbol": "PEPE", "name": "Pepe", "price_usd": "0.000012", "change_24h_pct": "18.5"},
                {"symbol": "LUNA", "name": "Terra", "price_usd": "0.42", "change_24h_pct": "-14.2"},
                {"symbol": "USDT", "name": "Tether", "price_usd": "1.00", "change_24h_pct": "0.01"},
            ]
        }
    )
    assert movers["gainers"][0]["symbol"] == "PEPE"
    assert movers["losers"][0]["symbol"] == "LUNA"

    syn = Digest4hSynthesis(
        whale_bullets_ar=["طباعة 500 مليون USDT جديدة على شبكة ترون ▲ مما يرفع سيولة السوق"],
        crypto_bullets_ar=["صعود عملة PEPE بنسبة 18.5% ▲ بعد إدراج فوري جديد، وهبوط LUNA بنسبة 14.2% 🔻"],
        corporate_macro_bullets_ar=["إعلان شركة كبرى عن خسائر فصلية حادة 🔻 وخفض 12% من موظفيها"],
        whale_bullets_en=["$500M fresh USDT minted on TRON ▲ lifting spot liquidity"],
        crypto_bullets_en=["PEPE surged +18.5% ▲ on exchange listing; LUNA dropped -14.2% 🔻"],
        corporate_macro_bullets_en=["Major corporation posts steep quarterly loss 🔻 and 12% layoffs"],
    )
    vip_ar = build_4h_digest_payload(syn, chat_id="@VIP_AR", lang="ar", is_vip=True)["text"]
    pub_ar = build_4h_digest_payload(syn, chat_id="@PUB_AR", lang="ar", is_vip=False)["text"]
    vip_en = build_4h_digest_payload(syn, chat_id="@VIP_EN", lang="en", is_vip=True)["text"]
    pub_en = build_4h_digest_payload(syn, chat_id="@PUB_EN", lang="en", is_vip=False)["text"]
    assert "حركة الحيتان ومعروض USDT" in vip_ar
    assert "الشركات العملاقة والاقتصاد العالمي" in vip_ar
    assert "ratepathlive.com" in pub_ar
    for msg in (vip_ar, pub_ar, vip_en, pub_en):
        assert "[VIP]" not in msg
        assert "القناعة" not in msg
        assert "كل 4 ساعات" not in msg
        assert "4-Hour" not in msg
