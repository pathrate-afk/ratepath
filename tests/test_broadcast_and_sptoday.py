"""Unit Tests for Separated News/Rates Broadcasts, 2-Layer Dedup, Anti-Scramble Bulletin & 3-Tier SP-Today SYP."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from backend.agents.contracts import AgentReport
from backend.agents.orchestrator import (
    SWARM_HASH_TTL_SECONDS_STR,
    evaluate_swarm_reports,
    is_jaccard_duplicate,
)
from backend.agents.price_guardian import validate_syp_sptoday_spread
from backend.config import RAW_SYRIA_FLAG_EMOJI, WORKSPACE_ROOT, Settings
from backend.fetchers import fetch_fiat_rates
from backend.rates_bulletin import (
    BULLETIN_COOLDOWN_SECONDS,
    RatesBulletinStateStore,
    build_rates_bulletin_payload,
    detect_significant_rate_movers,
    maybe_broadcast_rates_bulletin,
)
from backend.sources.sptoday import (
    extract_dom_text,
    fetch_syp_sptoday_3tier,
    normalize_syp_new_rate,
    parse_sptoday_telegram_html,
    parse_sptoday_web_html,
)
from backend.telegram_bot import (
    AFFILIATES_ENABLED,
    build_public_channel_payload,
    normalize_preserve_newlines,
)


def _offline_settings(*, with_tg: bool = False, with_upstash: bool = False) -> Settings:
    return Settings(
        gemini_api_key="placeholder",
        groq_api_key="placeholder",
        openrouter_api_key="placeholder",
        supabase_url="placeholder",
        supabase_key="placeholder",
        upstash_redis_rest_url="https://mock-redis.upstash.io" if with_upstash else "placeholder",
        upstash_redis_rest_token="live_token" if with_upstash else "placeholder",
        telegram_bot_token="123456:LIVE_TOKEN" if with_tg else "placeholder",
        telegram_public_channel="placeholder",
        telegram_vip_channel="placeholder",
        telegram_public_channel_en="@RatePathEN" if with_tg else "placeholder",
        telegram_vip_channel_en="@RatePathVIP_EN" if with_tg else "placeholder",
        telegram_public_channel_ar="@RatePathAR" if with_tg else "placeholder",
        telegram_vip_channel_ar="@RatePathVIP_AR" if with_tg else "placeholder",
        admin_chat_id="999888" if with_tg else "placeholder",
    )


def test_paragraph_spacing_preserved_and_zero_newline_stripping_regex() -> None:
    """Verify `\\n\\n` blank lines are preserved strictly and no `re.sub(r'\\s+', ...)` remains."""
    sample = "🔥 <b>Headline   with   spaces</b>\n\n📖 <b>What Happened:</b>\nLine   one.\n\n💡 <b>Bottom Line:</b>\nLine\t\ttwo."
    cleaned = normalize_preserve_newlines(sample)
    assert cleaned.count("\n\n") == 2
    assert "Headline with spaces" in cleaned
    assert "Line one." in cleaned
    assert "Line two." in cleaned

    for rel_path in (
        "backend/telegram_bot.py",
        "backend/rates_bulletin.py",
        "backend/fetchers.py",
        "backend/agents/telegram_scanner.py",
        "backend/agents/master_strategist.py",
    ):
        src = (WORKSPACE_ROOT / rel_path).read_text(encoding="utf-8")
        assert r're.compile(r"\s+")' not in src, f"Forbidden \\s+ regex found in {rel_path}"
        assert r"re.sub(r'\s+'" not in src and r're.sub(r"\s+"' not in src

    # News-only payload contains ZERO exchange rates and >= 2 blank-line paragraph breaks
    news_item = {
        "source_name": "CNBC",
        "source_url": "https://www.cnbc.com/example",
        "en": {
            "title": "Central banks accelerate physical gold reserves",
            "summary": "Sovereign desks expanded bullion purchases for a third straight quarter. Reserve managers cited fiscal deficits.",
            "macro_thesis": "Physical gold stays supported on dips while long-term sovereign demand absorbs pullbacks.",
        },
        "ar": {
            "title": "البنوك المركزية تسرّع وتيرة شراء الذهب المادي",
            "summary": "زادت البنوك المركزية مشترياتها من السبائك للربع الثالث على التوالي وسط مخاوف العجز المالي.",
            "macro_thesis": "الذهب يحصل على دعم واضح عند التراجعات مع استمرار تدفق السيولة نحو الملاذات الآمنة.",
        },
        "priced_in_warning": {"active": False, "severity": "low", "en": "Fresh", "ar": "جديد"},
        "assets_bias_24h": {"XAU": "bullish", "USD": "neutral"},
    }
    feed_with_prices = {
        "gold": {"prices": {"XAU_OZ": "3569.90"}},
        "fiat": {"rates": {"SYP": {"units_per_usd": "152.5000", "display_rate": "152.5000"}}},
        "crypto": {"assets": {"BTC": {"spot_price": "60275.91"}}},
    }
    en_post = build_public_channel_payload(news_item, feed_with_prices, chat_id="@RatePathEN", lang="en")
    ar_post = build_public_channel_payload(news_item, feed_with_prices, chat_id="@RatePathAR", lang="ar")
    for post in (en_post, ar_post):
        assert post["text"].count("\n\n") >= 2
        for forbidden_price in ("3569.90", "60275.91", "152.5000"):
            assert forbidden_price not in post["text"]


def test_change_only_rates_bulletin_one_asset_per_line_and_3h_cooldown(tmp_path: Path) -> None:
    """Verify Rates Bulletin includes ONLY changed assets, 1 asset per line, no `|`, and 3h cooldown."""
    prev_rates = {
        "XAU_OZ": Decimal("3500.00"),
        "GRAM_21K": Decimal("100.00"),
        "SYP": Decimal("150.00"),
        "EUR": Decimal("0.8600"),
        "BTC": Decimal("60000.00"),
        "ETH": Decimal("1700.00"),
    }
    curr_rates = {
        "XAU_OZ": Decimal("3505.00"),   # +0.14% -> UNCHANGED (below 0.50% -> MUST be excluded!)
        "GRAM_21K": Decimal("100.90"),  # +0.90% -> MOVED (>= 0.50%)
        "SYP": Decimal("151.75"),       # +1.17% -> MOVED (>= 0.50%)
        "EUR": Decimal("0.8610"),       # +0.12% -> UNCHANGED (MUST be excluded!)
        "BTC": Decimal("61500.00"),     # +2.50% -> MOVED (>= 2.00%)
        "ETH": Decimal("1720.00"),      # +1.18% -> UNCHANGED (below 2.00% crypto threshold -> excluded!)
    }

    movers = detect_significant_rate_movers(curr_rates, prev_rates)
    moved_ids = [m.spec.asset_id for m in movers]
    assert moved_ids == ["GRAM_21K", "SYP", "BTC"]

    ar_msg = build_rates_bulletin_payload(movers, chat_id="@RatePathAR", lang="ar")["text"]
    en_msg = build_rates_bulletin_payload(movers, chat_id="@RatePathEN", lang="en")["text"]

    for msg in (ar_msg, en_msg):
        assert "|" not in msg
        assert msg.count("\n\n") >= 2
        assert RAW_SYRIA_FLAG_EMOJI not in msg
        assert "3,505.00" not in msg  # Unchanged XAU excluded
        assert "0.8610" not in msg    # Unchanged EUR excluded
        assert "1,720.00" not in msg  # Unchanged ETH excluded

    assert "🪙 عيار 21: <code>$100.90</code> (▲ +0.9%)" in ar_msg
    assert "🟢 الليرة السورية: <code>151.75 ل.س</code> (▲ +1.2%)" in ar_msg
    assert "₿ بيتكوين: <code>$61,500.00</code> (▲ +2.5%)" in ar_msg

    assert "🪙 Gold 21K: <code>$100.90</code> (▲ +0.9%)" in en_msg
    assert "🟢 SYP: <code>151.75 SYP</code> (▲ +1.2%)" in en_msg

    # Verify every asset line has strictly ONE `<code>` price block
    for line in ar_msg.splitlines():
        assert line.count("<code>") <= 1

    # End-to-end baseline -> send -> 3h cooldown test
    cfg = _offline_settings(with_tg=True, with_upstash=False)
    store = RatesBulletinStateStore(settings=cfg, local_state_path=tmp_path / "bulletin_state.tmp.json")
    sent_payloads: list[dict[str, object]] = []

    def tg_handler(request: httpx.Request) -> httpx.Response:
        sent_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    feed_t0 = {
        "gold": {"prices": {"XAU_OZ": "3500.00", "GRAM_21K": "100.00"}},
        "fiat": {"rates": {"SYP": {"units_per_usd": "150.0000"}}},
        "crypto": {"assets": {"BTC": {"spot_price": "60000.00"}}},
    }
    feed_t1 = {
        "gold": {"prices": {"XAU_OZ": "3502.00", "GRAM_21K": "101.20"}},
        "fiat": {"rates": {"SYP": {"units_per_usd": "151.7500"}}},
        "crypto": {"assets": {"BTC": {"spot_price": "62000.00"}}},
    }
    t0 = datetime(2026, 11, 26, 0, 0, 0, tzinfo=timezone.utc)

    async def _run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(tg_handler)) as client:
            r0 = await maybe_broadcast_rates_bulletin(feed_t0, settings=cfg, state_store=store, client=client, now_utc=t0)
            assert r0["status"] == "baseline_initialized" and r0["sent"] == 0

            r1 = await maybe_broadcast_rates_bulletin(feed_t1, settings=cfg, state_store=store, client=client, now_utc=t0 + timedelta(minutes=10))
            assert r1["status"] == "sent" and r1["sent"] == 4
            assert "SYP" in r1["movers"] and "BTC" in r1["movers"]

            # Inside 3h cooldown -> MUST NOT spam even on another big move
            r2 = await maybe_broadcast_rates_bulletin(feed_t0, settings=cfg, state_store=store, client=client, now_utc=t0 + timedelta(hours=1))
            assert r2["status"] == "cooldown_active" and r2["sent"] == 0

            # After 3h cooldown -> allowed again
            r3 = await maybe_broadcast_rates_bulletin(feed_t0, settings=cfg, state_store=store, client=client, now_utc=t0 + timedelta(seconds=BULLETIN_COOLDOWN_SECONDS + 700))
            assert r3["status"] == "sent" and r3["sent"] == 4

    asyncio.run(_run())
    assert len(sent_payloads) == 8


def test_sptoday_3tier_syp_accuracy_dom_separation_and_er_api_ban(tmp_path: Path) -> None:
    """Verify DOM space separation prevents `151.7515,175` merge, old->new /100 works, and open.er-api NEVER sets SYP."""
    # Adjacent spans with NO whitespace between tags MUST NOT concatenate
    tricky_html = (
        "<div><span>US Dollar</span><div><p>Buy</p><p><span>151.75</span><span>15,175 old</span></p></div>"
        "<div><p>Sell</p><p><span>152.25</span><span>15,225 old</span></p></div></div>"
    )
    dom_text = extract_dom_text(tricky_html)
    assert "151.75 15,175 old" in dom_text
    assert "151.7515" not in dom_text

    web_quote = parse_sptoday_web_html(tricky_html)
    assert web_quote is not None
    assert web_quote.buy_rate == Decimal("151.75")
    assert web_quote.sell_rate == Decimal("152.25")
    assert web_quote.mid_rate == Decimal("152.00")

    # Old-lira normalization (5000 < rate < 50000 -> divide by 100)
    assert normalize_syp_new_rate("15,225") == Decimal("152.25")
    assert normalize_syp_new_rate("151.75") == Decimal("151.75")
    assert normalize_syp_new_rate("12.00") is None  # Out of 50..500 sanity range

    # Tier 2 Telegram preview parser (old Arabic lira -> normalized new SYP)
    tg_html = '<div class="tgme_widget_message_text">دمشق - الدولار الأمريكي: شراء 15150 - مبيع 15250 ليرة سورية</div>'
    tg_quote = parse_sptoday_telegram_html(tg_html)
    assert tg_quote is not None
    assert tg_quote.buy_rate == Decimal("151.50")
    assert tg_quote.sell_rate == Decimal("152.50")
    assert tg_quote.mid_rate == Decimal("152.00")

    # Agent 7 SYP spread guard: valid <5% passes, wide >=5% or inverted rejects
    ok_v = validate_syp_sptoday_spread("151.75", "152.25")
    assert ok_v.corrupt_spike_rejected is False
    bad_spread = validate_syp_sptoday_spread("150.00", "165.00")
    assert bad_spread.corrupt_spike_rejected is True
    bad_range = validate_syp_sptoday_spread("13000.00", "13050.00")
    assert bad_range.corrupt_spike_rejected is True

    # Tier 1 403 -> Tier 2 Telegram 200 -> persists to Tier 3 cache; then Tier 1+2 fail -> Tier 3 serves!
    cache_file = tmp_path / "sptoday_last_good.tmp.json"
    cfg = _offline_settings()

    def tier2_handler(request: httpx.Request) -> httpx.Response:
        if "sp-today.com" in str(request.url):
            return httpx.Response(403, text="Forbidden")
        if "t.me/s/sp_today" in str(request.url):
            return httpx.Response(200, text=tg_html)
        return httpx.Response(404)

    def all_down_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Unavailable")

    async def _run_tiers() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(tier2_handler)) as c1:
            q2 = await fetch_syp_sptoday_3tier(c1, settings=cfg, local_cache_path=cache_file)
            assert q2 is not None and q2.source == "sp-today_telegram"
            assert q2.mid_rate == Decimal("152.00")

        async with httpx.AsyncClient(transport=httpx.MockTransport(all_down_handler)) as c2:
            q3 = await fetch_syp_sptoday_3tier(c2, settings=cfg, local_cache_path=cache_file)
            assert q3 is not None and q3.source == "upstash_sptoday_last_good"
            assert q3.mid_rate == Decimal("152.00")

        # Verify open.er-api.com NEVER sets SYP even if SYP is in its JSON payload
        def er_api_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "rates": {
                        "EUR": 0.92,
                        "GBP": 0.79,
                        "TRY": 36.2,
                        "SAR": 3.75,
                        "AED": 3.6725,
                        "EGP": 48.5,
                        "SYP": 13002.0,
                    }
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(er_api_handler)) as c3:
            fiat_res = await fetch_fiat_rates(c3)
            assert "SYP" not in fiat_res.units_per_usd

    asyncio.run(_run_tiers())


def test_two_layer_jaccard_dedup_workflows_and_affiliates_disabled(tmp_path: Path) -> None:
    """Verify 48h TTL, Supabase last-25 Jaccard >= 0.45 skip, workflow env parity, and AFFILIATES_ENABLED=false."""
    assert SWARM_HASH_TTL_SECONDS_STR == "172800"
    assert AFFILIATES_ENABLED is False

    # Same story phrased by two different websites -> Jaccard >= 0.45 catches duplicate
    story_a = "Emergency oil shipping blockade sends crude prices soaring amid household fuel pressure"
    story_b = "Oil shipping blockade sends crude soaring with fresh household fuel pressure"
    story_c = "Bitcoin spot ETF inflows hit quarterly record as institutional custody expands"
    assert is_jaccard_duplicate(story_a, story_b) is True
    assert is_jaccard_duplicate(story_a, story_c) is False

    # Orchestrator Layer-2 Jaccard dedup blocks duplicate even with a brand-new hash
    macro_rep = AgentReport(
        agent_id="agent_4_macro_life_filter",
        tier="hard_truth",
        healthy=True,
        signal_direction="risk_off",
        confidence=90,
        headline=story_a,
        flags=["ENERGY_SHIPPING_PRESSURE"],
    )
    cfg = _offline_settings()
    decision = asyncio.run(
        evaluate_swarm_reports(
            [macro_rep],
            settings=cfg,
            use_llm=False,
            dry_run=False,
            local_dedup_path=tmp_path / "fresh_swarm_dedup.tmp.json",
            recent_memory_rows=[
                {
                    "headline_en": story_b,
                    "headline_ar": "ضغوط طاقة وتوترات سياسية وتجارية ترفع تكلفة المعيشة",
                    "summary_en": "Crude oil shipping crisis lifts safe havens",
                    "summary_ar": "أزمة شحن النفط تدعم الملاذات الآمنة",
                }
            ],
        )
    )
    assert decision.publish_allowed is False
    assert decision.rejection_reason and decision.rejection_reason.startswith("duplicate_supabase_jaccard_silenced")

    # Telegram channels remain promo-free while frontend uses verified geo-filtered referral links
    mon_cfg = (WORKSPACE_ROOT / "frontend" / "monetization.config.js").read_text(encoding="utf-8")
    assert "AFFILIATES_ENABLED = true" in mon_cfg
    assert "N8PZMKN" in mon_cfg and "c_zmvyur7qg4" in mon_cfg and "N9R5T1J8" in mon_cfg and "1176056" in mon_cfg

    run_cycle_src = (WORKSPACE_ROOT / "backend" / "run_cycle.py").read_text(encoding="utf-8")
    assert "broadcast_feed_to_telegram" not in run_cycle_src
    assert "maybe_broadcast_rates_bulletin" in run_cycle_src

    for wf_name in ("ratepath_worker.yml", "ratepath_swarm.yml"):
        wf = (WORKSPACE_ROOT / ".github" / "workflows" / wf_name).read_text(encoding="utf-8")
        for req_env in (
            "UPSTASH_REDIS_REST_URL: ${{ secrets.UPSTASH_REDIS_REST_URL }}",
            "UPSTASH_REDIS_REST_TOKEN: ${{ secrets.UPSTASH_REDIS_REST_TOKEN }}",
            "SUPABASE_URL: ${{ secrets.SUPABASE_URL }}",
            "SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}",
        ):
            assert req_env in wf, f"Missing {req_env} in {wf_name}"
