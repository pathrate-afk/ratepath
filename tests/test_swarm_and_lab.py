"""Comprehensive QA Suite for Century Memory, 7-Agent Swarm, Orchestrator & Daily Lab."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

from backend.agents.contracts import AgentReport, StrategistBrief
from backend.agents.crypto_solvency import evaluate_solvency_telemetry
from backend.agents.daily_drill import (
    calibrate_source_trust_weights,
    run_adversarial_gauntlet,
    run_daily_calibration_lab,
)
from backend.agents.orchestrator import (
    DEFAULT_TRUST_WEIGHTS,
    audit_strategist_brief,
    detect_hierarchy_and_retail_trap,
    evaluate_swarm_reports,
)
from backend.agents.price_guardian import evaluate_screen_vs_real_price
from backend.agents.telegram_scanner import parse_telegram_preview_html
from backend.config import WORKSPACE_ROOT, Settings
from backend.seed_history import get_historical_macro_twins


def _offline_settings() -> Settings:
    return Settings(
        gemini_api_key="placeholder",
        groq_api_key="placeholder",
        openrouter_api_key="placeholder",
        supabase_url="placeholder",
        supabase_key="placeholder",
        upstash_redis_rest_url="placeholder",
        upstash_redis_rest_token="placeholder",
        telegram_bot_token="placeholder",
        telegram_public_channel="placeholder",
        telegram_vip_channel="placeholder",
        admin_chat_id="placeholder",
    )


def test_century_macro_and_crypto_memory_completeness() -> None:
    """Verify >=22 macro archetypes (1914-2025) + >=10 crypto history anchors (2011-2025)."""
    twins = get_historical_macro_twins()
    macro_twins = [t for t in twins if not str(t["macro_regime"]).startswith("crypto_history_")]
    crypto_twins = [t for t in twins if str(t["macro_regime"]).startswith("crypto_history_")]

    assert len(macro_twins) >= 22
    assert len(crypto_twins) >= 10
    keys = {t["event_key"] for t in twins}
    for required_key in (
        "hist-1914-wwi-exchange-freeze",
        "hist-1923-weimar-hyperinflation",
        "hist-1929-wall-street-crash",
        "hist-1971-nixon-shock",
        "hist-1980-volcker-rate-shock",
        "hist-2008-gfc-lehman-liquidity",
        "hist-2020-covid-cash-to-stimulus",
        "hist-2022-sanctions-gold-rush",
        "crypto-2014-mtgox-insolvency",
        "crypto-2016-dao-fork-eth",
        "crypto-2022-terra-luna-depeg",
        "crypto-2022-ftx-solvency-collapse",
        "crypto-2023-usdc-svb-depeg",
        "crypto-2024-spot-etf-era",
    ):
        assert required_key in keys


def test_agent7_price_guardian_rejects_corrupt_spike_over_4_pct() -> None:
    """Agent 7 rejects >4% single-source screen spike and emits exact Decimal spread %."""
    ok = evaluate_screen_vs_real_price("3610.00", "3600.00")
    assert ok.corrupt_spike_rejected is False
    assert ok.accepted_price == Decimal("3610.00")
    assert ok.screen_vs_real_spread_pct == "0.28"

    corrupt = evaluate_screen_vs_real_price("3924.00", "3600.00")
    assert corrupt.corrupt_spike_rejected is True
    assert corrupt.accepted_price == Decimal("3600.00")
    assert corrupt.screen_vs_real_spread_pct == "9.00"
    assert "CORRUPT_SPIKE_REJECTED" in corrupt.report.flags


def test_orchestrator_hierarchy_retail_trap_auditor_and_conviction_gate(tmp_path: Path) -> None:
    """Verify hard truth overrides crowd euphoria, flags RETAIL_TRAP, and blocks hallucinations."""
    crowd_hype = AgentReport(
        agent_id="agent_1_social_pulse",
        tier="crowd_chatter",
        healthy=True,
        signal_direction="euphoria",
        confidence=90,
        headline="Reddit parabolic moon FOMO!",
        flags=["RETAIL_EUPHORIA"],
    )
    solvency_drain = evaluate_solvency_telemetry(
        tvl_change_24h_pct=Decimal("-7.50"),
        stablecoin_pegs={"USDC": Decimal("0.9720"), "USDT": Decimal("0.9990")},
        btc_quote_volume_usd=Decimal("4500000000"),
        btc_24h_change_pct=Decimal("-4.20"),
    )

    hierarchy = detect_hierarchy_and_retail_trap([crowd_hype, solvency_drain])
    assert hierarchy["retail_trap_flagged"] is True

    # Auditor rejects hallucinated twin keys
    fake_brief = StrategistBrief(
        event_en="Fake event",
        event_ar="حدث تجريبي",
        money_flow_en="Cash flow",
        money_flow_ar="حركة السيولة",
        verdict_en="Hold fire",
        verdict_ar="تريث الآن",
        matched_twin_key="nonexistent-fake-twin-9999",
        assets_bias_24h={"USD": "neutral", "XAU": "neutral", "BTC": "neutral"},
        raw_conviction=90,
    )
    ok, _score, _b, err = audit_strategist_brief(fake_brief, hierarchy)
    assert ok is False and err and "hallucinated_twin_key" in err

    # Full Orchestrator evaluation + Content-Hash deduplication
    dedup_file = tmp_path / "swarm_dedup.tmp.json"
    cfg = _offline_settings()

    async def _run() -> None:
        first = await evaluate_swarm_reports(
            [crowd_hype, solvency_drain],
            settings=cfg,
            use_llm=False,
            dry_run=False,
            local_dedup_path=dedup_file,
        )
        assert first.retail_trap_flagged is True
        assert first.auditor_approved is True
        assert first.conviction_score >= 75
        assert first.publish_allowed is True
        assert first.brief.assets_bias_24h["BTC"] == "bearish"

        # Second identical content hash must be silenced
        second = await evaluate_swarm_reports(
            [crowd_hype, solvency_drain],
            settings=cfg,
            use_llm=False,
            dry_run=False,
            local_dedup_path=dedup_file,
        )
        assert second.publish_allowed is False
        assert second.rejection_reason == "duplicate_content_hash_silenced"

    asyncio.run(_run())


def test_agent3_telegram_preview_parser_and_daily_adversarial_gauntlet(tmp_path: Path) -> None:
    """Verify safe t.me/s HTML parser, 100% readiness gauntlet, and trust weight calibration."""
    sample_html = (
        '<div class="tgme_widget_message_text js-message_text">'
        "BREAKING: Central banks boost physical gold purchases amid shipping delays."
        "</div>"
    )
    parsed = parse_telegram_preview_html(sample_html)
    assert len(parsed) == 1 and "Central banks boost physical gold" in parsed[0]

    cfg = _offline_settings()
    gauntlet = asyncio.run(run_adversarial_gauntlet(settings=cfg))
    assert gauntlet["readiness_pct"] == 100
    assert gauntlet["drills"] == {
        "corrupt_9pct_spike_blocked": True,
        "retail_trap_tvl_drain_caught": True,
        "blind_crisis_replay_passed": True,
    }

    adjusted = calibrate_source_trust_weights(DEFAULT_TRUST_WEIGHTS, Decimal("45.00"))
    assert adjusted["agent_1_social_pulse"] < DEFAULT_TRUST_WEIGHTS["agent_1_social_pulse"]
    assert adjusted["agent_4_macro_life_filter"] > DEFAULT_TRUST_WEIGHTS["agent_4_macro_life_filter"]

    lab_res = asyncio.run(
        run_daily_calibration_lab(
            settings=cfg,
            feed_path=tmp_path / "missing_feed.json",
            benchmark_live_llms=False,
            send_admin_report=False,
        )
    )
    assert lab_res["readiness_pct"] == 100


def test_zero_fluff_ui_and_new_github_workflows() -> None:
    """Verify Zero-Fluff EN/AR headers and all 3 new GitHub Actions workflow specs."""
    i18n_js = (WORKSPACE_ROOT / "frontend" / "js" / "i18n.js").read_text(encoding="utf-8")
    for label in (
        "World Currencies",
        "أسعار العملات",
        "Gold & Karats",
        "الذهب والعيارات",
        "Crypto",
        "العملات الرقمية",
        "Market Pulse",
        "نبض السوق",
    ):
        assert label in i18n_js

    swarm_wf = (WORKSPACE_ROOT / ".github" / "workflows" / "ratepath_swarm.yml").read_text(
        encoding="utf-8"
    )
    drill_wf = (WORKSPACE_ROOT / ".github" / "workflows" / "ratepath_daily_drill.yml").read_text(
        encoding="utf-8"
    )
    keep_wf = (WORKSPACE_ROOT / ".github" / "workflows" / "keepalive.yml").read_text(
        encoding="utf-8"
    )

    assert 'cron: "3,23,43 * * * *"' in swarm_wf
    assert "python -m backend.agents.orchestrator" in swarm_wf
    assert 'cron: "30 5 * * *"' in drill_wf
    assert "python -m backend.agents.daily_drill" in drill_wf
    assert 'cron: "0 0 1 * *"' in keep_wf
