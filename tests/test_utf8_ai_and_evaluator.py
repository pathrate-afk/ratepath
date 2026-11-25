"""Tests for AST UTF-8/Decimal Purity, Failover Fetchers, Dual-Pass AI, and 24h Evaluator."""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from backend.ai_strategist import analyze_macro_news_batch
from backend.config import WORKSPACE_ROOT, Settings
from backend.evaluator import (
    MarketMemoryStore,
    grade_prediction_24h,
    normalize_supabase_rest_base,
)
from backend.fetchers import RawNewsItem, fetch_crypto_spot, fetch_fiat_rates
from backend.seed_history import seed_market_memory


def _make_placeholder_settings() -> Settings:
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


def test_backend_ast_enforces_zero_floats_and_explicit_utf8() -> None:
    """Static AST audit of `backend/**/*.py`: ZERO floats, `ensure_ascii=False`, `encoding='utf-8'`."""
    backend_dir = WORKSPACE_ROOT / "backend"
    py_files = sorted(backend_dir.rglob("*.py"))
    assert len(py_files) >= 14

    for py_file in py_files:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))

        for node in ast.walk(tree):
            # 1. Zero float constants anywhere in backend/*.py
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                raise AssertionError(
                    f"Forbidden float literal {node.value!r} in {py_file.name}:{node.lineno}"
                )

            if isinstance(node, ast.Call):
                # 2. Zero float(...) calls
                if isinstance(node.func, ast.Name) and node.func.id == "float":
                    raise AssertionError(
                        f"Forbidden float() call in {py_file.name}:{node.lineno}"
                    )

                # 3. Every json.dumps(...) must specify ensure_ascii=False
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "json"
                    and node.func.attr == "dumps"
                ):
                    kw_map = {kw.arg: kw.value for kw in node.keywords}
                    ensure_val = kw_map.get("ensure_ascii")
                    assert (
                        isinstance(ensure_val, ast.Constant) and ensure_val.value is False
                    ), f"json.dumps missing ensure_ascii=False in {py_file.name}:{node.lineno}"

                # 4. Every open / read_text / write_text must specify encoding='utf-8'
                is_open = isinstance(node.func, ast.Name) and node.func.id == "open"
                is_path_io = isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "read_text",
                    "write_text",
                }
                if is_open or is_path_io:
                    kw_map = {kw.arg: kw.value for kw in node.keywords}
                    enc_val = kw_map.get("encoding")
                    assert (
                        isinstance(enc_val, ast.Constant)
                        and isinstance(enc_val.value, str)
                        and enc_val.value.lower() == "utf-8"
                    ), f"File I/O missing encoding='utf-8' in {py_file.name}:{node.lineno}"


def test_fetcher_primary_to_backup_failover() -> None:
    """Verify primary failure seamlessly falls back to backup feed with pure Decimal results."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "data-api.binance.vision" in url:
            return httpx.Response(503, text="Service Unavailable")
        if "api.bybit.com" in url:
            return httpx.Response(
                200,
                json={
                    "result": {
                        "list": [
                            {"symbol": "BTCUSDT", "lastPrice": "94250.55"},
                            {"symbol": "ETHUSDT", "lastPrice": "3310.20"},
                            {"symbol": "SOLUSDT", "lastPrice": "235.80"},
                        ]
                    }
                },
            )
        if "open.er-api.com" in url:
            return httpx.Response(500, text="Upstream error")
        if "cdn.jsdelivr.net" in url:
            return httpx.Response(
                200,
                json={
                    "usd": {
                        "eur": 0.951,
                        "gbp": 0.792,
                        "try": 34.55,
                        "sar": 3.75,
                        "aed": 3.6725,
                        "egp": 49.60,
                    }
                },
            )
        return httpx.Response(404)

    async def _run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            spot = await fetch_crypto_spot(client)
            assert spot.source == "bybit_v5_spot"
            assert str(spot.prices["BTC"]) == "94250.55"

            fiat = await fetch_fiat_rates(client)
            assert fiat.source == "jsdelivr_currency_api"
            assert str(fiat.units_per_usd["AED"]) == "3.6725"

    asyncio.run(_run())


def test_dual_pass_ai_placeholder_and_multi_llm_failover() -> None:
    """Test deterministic bilingual mode (`placeholder`) AND Gemini -> Groq -> OpenRouter failover."""
    sample_item = RawNewsItem(
        id="rp-testnews00001",
        published_at="2026-11-23T00:00:00Z",
        source_name="Federal Reserve",
        source_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261123a.htm",
        title="Federal Reserve holds rates unchanged as expected amid sticky inflation",
        summary="Policy makers signaled higher for longer rates in line with consensus expectations.",
    )
    ctx = {"XAU_OZ": "2650.00", "BTC": "94000.00"}

    # 1. Placeholder mode -> deterministic bilingual fallback
    placeholder_settings = Settings(
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
    analyzed, mode = asyncio.run(
        analyze_macro_news_batch([sample_item], ctx, settings=placeholder_settings)
    )
    assert mode == "deterministic_fallback"
    assert len(analyzed) == 1
    rec = analyzed[0]
    assert rec.auditor_verified is True
    assert rec.priced_in_warning.active is True
    assert "ريت باث" in rec.ar.title
    assert rec.assets_bias_24h.USD == "bullish"

    # 2. Live Multi-LLM failover: Gemini 503 -> Groq 429 -> OpenRouter 200 (Pass 1 + Pass 2)
    call_counter = {"openrouter": 0}

    def llm_mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "generativelanguage.googleapis.com" in url:
            return httpx.Response(503, text="Overloaded")
        if "api.groq.com" in url:
            return httpx.Response(429, text="Rate limited")
        if "openrouter.ai" in url:
            call_counter["openrouter"] += 1
            if call_counter["openrouter"] == 1:
                content_obj = rec.model_dump(
                    include={"en", "ar", "priced_in_warning", "assets_bias_24h", "confidence_score"}
                )
            else:
                content_obj = {
                    "approved": True,
                    "adjusted_confidence": 82,
                    "priced_in_warning": rec.priced_in_warning.model_dump(),
                    "assets_bias_24h": rec.assets_bias_24h.model_dump(),
                    "en": rec.en.model_dump(),
                    "ar": rec.ar.model_dump(),
                }
            body = json.dumps(
                {"choices": [{"message": {"content": json.dumps(content_obj, ensure_ascii=False)}}]},
                ensure_ascii=False,
            )
            return httpx.Response(200, content=body.encode("utf-8"))
        return httpx.Response(404)

    live_settings = Settings(
        gemini_api_key="live_gem_key",
        groq_api_key="live_groq_key",
        openrouter_api_key="live_or_key",
        supabase_url="placeholder",
        supabase_key="placeholder",
        upstash_redis_rest_url="placeholder",
        upstash_redis_rest_token="placeholder",
        telegram_bot_token="placeholder",
        telegram_public_channel="placeholder",
        telegram_vip_channel="placeholder",
        admin_chat_id="placeholder",
    )

    async def _run_llm() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(llm_mock_handler)) as client:
            items, llm_mode = await analyze_macro_news_batch(
                [sample_item], ctx, settings=live_settings, client=client
            )
            assert llm_mode == "live_llm"
            assert items[0].confidence_score == 82
            assert items[0].auditor_verified is True

    asyncio.run(_run_llm())


def test_24h_self_evaluation_loop_and_history_seeder(tmp_path: Path) -> None:
    """Verify historical macro twin seeding, Supabase URL normalization, and 24h grading loop."""
    assert (
        normalize_supabase_rest_base("https://demo.supabase.co")
        == "https://demo.supabase.co/rest/v1"
    )
    assert (
        normalize_supabase_rest_base("https://demo.supabase.co/rest/v1/")
        == "https://demo.supabase.co/rest/v1"
    )

    ledger_path = tmp_path / "market_memory.tmp.json"
    placeholder_settings = _make_placeholder_settings()

    async def _run() -> None:
        seeded_count = await seed_market_memory(
            settings=placeholder_settings, local_path=ledger_path
        )
        assert seeded_count >= 3

        store = MarketMemoryStore(settings=placeholder_settings, local_path=ledger_path)
        t0 = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")
        await store.upsert_records(
            [
                {
                    "event_key": "pred-due-001",
                    "record_type": "live_prediction",
                    "event_timestamp": t0,
                    "source_name": "CNBC",
                    "source_url": "https://www.cnbc.com/macro",
                    "headline_en": "Fed turns dovish",
                    "headline_ar": "الفيدرالي يتبنى لهجة تيسيرية",
                    "summary_en": "Yields drop on dovish guidance.",
                    "summary_ar": "تراجع العوائد عقب إشارات التيسير.",
                    "priced_in_warning": {"active": False, "severity": "low", "en": "Fresh", "ar": "جديد"},
                    "assets_bias_24h": {"XAU": "bullish", "BTC": "bullish"},
                    "baseline_prices": {"XAU_OZ": "2600.00", "BTC": "90000.00"},
                    "evaluation_status": "pending",
                }
            ]
        )

        graded = await store.evaluate_due_predictions(
            {"XAU_OZ": "2652.00", "BTC": "92700.00"}
        )
        assert len(graded) == 1
        assert graded[0]["accuracy_score"] == "100.00"
        assert graded[0]["evaluation_status"] == "graded"
        assert "ريت باث" in graded[0]["lesson_ar"]

    asyncio.run(_run())

    direct_grade = grade_prediction_24h(
        {"XAU": "bullish", "BTC": "bearish"},
        {"XAU_OZ": "2000.00", "BTC": "80000.00"},
        {"XAU_OZ": "2040.00", "BTC": "82000.00"},
    )
    assert direct_grade["accuracy_score"] == "50.00"
