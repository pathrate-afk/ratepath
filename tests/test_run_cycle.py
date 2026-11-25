"""Unit Tests for Serverless Single-Cycle Runner (`backend/run_cycle.py`) & GitHub Actions."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from backend.config import WORKSPACE_ROOT, Settings
from backend.evaluator import MarketMemoryStore
from backend.run_cycle import (
    LAST_EVAL_REDIS_KEY,
    HourlyEvaluationGate,
    is_hourly_evaluation_due,
    run_single_cycle,
    utc_epoch_seconds,
)
from tests.test_contract_and_publisher import make_valid_feed_payload


def _make_settings(*, with_upstash: bool = False) -> Settings:
    return Settings(
        gemini_api_key="placeholder",
        groq_api_key="placeholder",
        openrouter_api_key="placeholder",
        supabase_url="placeholder",
        supabase_key="placeholder",
        upstash_redis_rest_url="https://mock-redis.upstash.io" if with_upstash else "placeholder",
        upstash_redis_rest_token="live_upstash_token" if with_upstash else "placeholder",
        telegram_bot_token="placeholder",
        telegram_public_channel="placeholder",
        telegram_vip_channel="placeholder",
        admin_chat_id="placeholder",
    )


def test_utc_epoch_and_hourly_evaluation_gate_upstash_and_fallback(tmp_path: Path) -> None:
    """Verify Upstash `ratepath:last_eval_ts` 60-min gating and safe local JSON fallback."""
    t0 = datetime(2026, 11, 25, 10, 0, 0, tzinfo=timezone.utc)
    ts0 = utc_epoch_seconds(t0)
    assert isinstance(ts0, int) and ts0 > 1700000000

    assert is_hourly_evaluation_due(None, ts0) is True
    assert is_hourly_evaluation_due(ts0, ts0 + 3599) is False
    assert is_hourly_evaluation_due(ts0, ts0 + 3600) is True

    redis_store: dict[str, str] = {}

    def upstash_handler(request: httpx.Request) -> httpx.Response:
        cmd = json.loads(request.content.decode("utf-8"))
        if cmd[0] == "GET":
            assert cmd[1] == LAST_EVAL_REDIS_KEY
            return httpx.Response(200, json={"result": redis_store.get(cmd[1])})
        if cmd[0] == "SET":
            assert cmd[1] == LAST_EVAL_REDIS_KEY
            redis_store[cmd[1]] = str(cmd[2])
            return httpx.Response(200, json={"result": "OK"})
        return httpx.Response(400)

    local_ts_file = tmp_path / "last_eval_ts.tmp.json"
    cfg_upstash = _make_settings(with_upstash=True)
    gate = HourlyEvaluationGate(settings=cfg_upstash, local_state_path=local_ts_file)

    async def _run_upstash() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstash_handler)) as client:
            last_ts, backend = await gate.get_last_eval_ts(client=client)
            assert last_ts is None and backend == "upstash"

            saved_backend = await gate.record_eval_ts(ts0, client=client)
            assert saved_backend == "upstash"
            assert redis_store[LAST_EVAL_REDIS_KEY] == str(ts0)

            fetched_ts, backend2 = await gate.get_last_eval_ts(client=client)
            assert fetched_ts == ts0 and backend2 == "upstash"

        # Simulate Upstash outage -> must safely fall back to local JSON without raising
        def failing_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Upstash temporarily unavailable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)) as fail_client:
            fallback_ts, fallback_backend = await gate.get_last_eval_ts(client=fail_client)
            assert fallback_ts == ts0
            assert fallback_backend == "local_fallback"

    asyncio.run(_run_upstash())


def test_run_single_cycle_executes_evaluator_once_per_hour(tmp_path: Path) -> None:
    """Verify `run_single_cycle` runs evaluator on first run, skips at +15m, and re-runs at +61m."""
    feed = make_valid_feed_payload()
    cfg = _make_settings(with_upstash=False)
    local_ts_file = tmp_path / "last_eval_ts.tmp.json"
    ledger_path = tmp_path / "market_memory.tmp.json"
    feed_path = tmp_path / "live_feed.json"

    gate = HourlyEvaluationGate(settings=cfg, local_state_path=local_ts_file)
    store = MarketMemoryStore(settings=cfg, local_path=ledger_path)

    async def fake_cycle() -> dict[str, Any]:
        return feed

    async def fake_tg(_payload: dict[str, Any]) -> dict[str, Any]:
        return {"status": "completed", "public_sent": 2, "vip_sent": 2, "duplicates_skipped": 0}

    t_start = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)

    async def _run() -> None:
        # Seed a >24h old pending prediction so first hourly evaluation grades it
        await store.upsert_records(
            [
                {
                    "event_key": "pred-hourly-test-001",
                    "record_type": "live_prediction",
                    "event_timestamp": (t_start - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "source_name": "CNBC",
                    "source_url": "https://www.cnbc.com/test",
                    "headline_en": "Test macro catalyst",
                    "headline_ar": "محفز اقتصادي تجريبي",
                    "summary_en": "Summary",
                    "summary_ar": "ملخص",
                    "priced_in_warning": {"active": False, "severity": "low", "en": "Fresh", "ar": "جديد"},
                    "assets_bias_24h": {"XAU": "bullish"},
                    "baseline_prices": {"XAU_OZ": "2600.00"},
                    "evaluation_status": "pending",
                }
            ]
        )

        # Cycle 1 (T+0): No prior eval timestamp -> evaluator MUST run & grade 1 prediction
        res1 = await run_single_cycle(
            settings=cfg,
            target_path=feed_path,
            eval_gate=gate,
            memory_store=store,
            cycle_executor=fake_cycle,
            telegram_broadcaster=fake_tg,
            now_utc=t_start,
        )
        assert res1["status"] == "ok"
        assert res1["evaluator_ran"] is True
        assert res1["graded_count"] == 1
        assert res1["next_eval_in_seconds"] == 3600

        # Cycle 2 (T+15m): Inside 60-min window -> evaluator MUST be skipped
        res2 = await run_single_cycle(
            settings=cfg,
            target_path=feed_path,
            eval_gate=gate,
            memory_store=store,
            cycle_executor=fake_cycle,
            telegram_broadcaster=fake_tg,
            now_utc=t_start + timedelta(minutes=15),
        )
        assert res2["evaluator_ran"] is False
        assert res2["graded_count"] == 0
        assert res2["next_eval_in_seconds"] == 2700

        # Cycle 3 (T+61m): >= 60 minutes elapsed -> evaluator MUST run again
        res3 = await run_single_cycle(
            settings=cfg,
            target_path=feed_path,
            eval_gate=gate,
            memory_store=store,
            cycle_executor=fake_cycle,
            telegram_broadcaster=fake_tg,
            now_utc=t_start + timedelta(minutes=61),
        )
        assert res3["evaluator_ran"] is True

    asyncio.run(_run())


def test_github_actions_workflow_and_gitignore_integrity() -> None:
    """Verify `.github/workflows/ratepath_worker.yml` secrets, schedule, orphan push & `.gitignore`."""
    workflow_path = WORKSPACE_ROOT / ".github" / "workflows" / "ratepath_worker.yml"
    assert workflow_path.exists()
    wf = workflow_path.read_text(encoding="utf-8")

    for required_fragment in (
        "name: RatePath Cloud Worker",
        'cron: "*/5 * * * *"',
        "workflow_dispatch:",
        "contents: write",
        "cancel-in-progress: false",
        'python-version: "3.11"',
        'cache: "pip"',
        "python -m backend.run_cycle",
        "git init -b live-data",
        "git add live_feed.json",
        "git push --force origin live-data",
    ):
        assert required_fragment in wf, f"Missing workflow directive: {required_fragment}"

    required_secrets = (
        "GEMINI_API_KEY_1",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GROQ_API_KEY_1",
        "GROQ_API_KEY_2",
        "GROQ_API_KEY_3",
        "OPENROUTER_API_KEY",
        "SUPABASE_URL",
        "SUPABASE_KEY",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "ADMIN_CHAT_ID",
        "TELEGRAM_PUBLIC_CHANNEL_EN",
        "TELEGRAM_VIP_CHANNEL_EN",
        "TELEGRAM_PUBLIC_CHANNEL_AR",
        "TELEGRAM_VIP_CHANNEL_AR",
    )
    for secret_name in required_secrets:
        assert f"secrets.{secret_name}" in wf, f"Missing secret mapping: {secret_name}"

    gitignore_lines = {
        ln.strip()
        for ln in (WORKSPACE_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    assert ".env" in gitignore_lines
    assert "*.tmp.json" in gitignore_lines
