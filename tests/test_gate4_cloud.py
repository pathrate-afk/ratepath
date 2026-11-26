"""Gate 4 Verification Suite: Cloud Worker, CORS, Telegram Publisher, Dedup & Monetization."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx

from backend.cloud_worker import (
    WorkerState,
    _handle_client_stream,
    run_single_evaluator_iteration,
)
from backend.config import WORKSPACE_ROOT, Settings
from backend.publisher import publish_live_feed_atomic
from backend.telegram_bot import (
    NewsDeduplicator,
    broadcast_feed_to_telegram,
    build_admin_watchdog_payload,
    build_lang_mini_app_url,
    build_public_channel_payload,
    build_vip_channel_payload,
    send_admin_watchdog_alert,
)
from tests.test_contract_and_publisher import make_valid_feed_payload

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def _placeholder_settings() -> Settings:
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


def test_cloud_worker_health_and_live_feed_cors_endpoints(tmp_path: Path) -> None:
    """Verify `/health`, `/live_feed.json`, and `OPTIONS` CORS headers over real async TCP."""
    feed_file = tmp_path / "live_feed.json"
    sample_feed = make_valid_feed_payload()
    publish_live_feed_atomic(sample_feed, target_path=feed_file)

    worker_state = WorkerState(port=7860)

    async def _run() -> None:
        graded_count = await run_single_evaluator_iteration(
            worker_state, settings=_placeholder_settings(), feed_path=feed_file
        )
        assert graded_count >= 0
        assert worker_state.evaluations_completed == 1

        server = await asyncio.start_server(
            lambda r, w: _handle_client_stream(r, w, worker_state, feed_file),
            host="127.0.0.1",
            port=0,
        )
        port = server.sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"

        try:
            async with httpx.AsyncClient() as client:
                # 1. GET /health
                health_resp = await client.get(f"{base_url}/health")
                assert health_resp.status_code == 200
                assert health_resp.headers["access-control-allow-origin"] == "*"
                health_json = health_resp.json()
                assert health_json["status"] == "ok"
                assert health_json["platform"] == "RatePath"
                assert health_json["platform_ar"] == "ريت باث"
                assert health_json["port"] == 7860
                assert isinstance(health_json["uptime_seconds"], int)

                # 2. OPTIONS /live_feed.json preflight
                opt_resp = await client.options(f"{base_url}/live_feed.json")
                assert opt_resp.status_code == 204
                assert opt_resp.headers["access-control-allow-origin"] == "*"
                assert "GET" in opt_resp.headers["access-control-allow-methods"]

                # 3. GET /live_feed.json CORS real-time endpoint
                feed_resp = await client.get(f"{base_url}/live_feed.json")
                assert feed_resp.status_code == 200
                assert feed_resp.headers["access-control-allow-origin"] == "*"
                assert "max-age=60" in feed_resp.headers["cache-control"]
                body = feed_resp.json()
                assert body["meta"]["platform_ar"] == "ريت باث"
                assert body["gold"]["prices"]["XAU_OZ"] == "2655.40"
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(_run())


def test_telegram_payload_formatting_and_upstash_deduplication(tmp_path: Path) -> None:
    """Test pure-EN vs pure-AR 4-channel payloads, legacy fallback, Watchdog, and Redis dedup."""
    feed = make_valid_feed_payload()
    news_item = feed["news"][0]

    assert build_lang_mini_app_url("https://ratepath.io/", "en") == "https://ratepath.io/?lang=en"
    assert build_lang_mini_app_url("https://ratepath.io/", "ar") == "https://ratepath.io/?lang=ar"

    # 1. Pure-English Public & VIP Payloads (ZERO Arabic characters + `?lang=en`)
    pub_en = build_public_channel_payload(
        news_item,
        feed,
        chat_id="@RatePathEN",
        mini_app_url="https://ratepath.io/",
        lang="en",
    )
    assert pub_en["chat_id"] == "@RatePathEN"
    assert pub_en["parse_mode"] == "HTML"
    assert not _ARABIC_RE.search(pub_en["text"])
    assert pub_en["text"].count("\n\n") >= 2
    assert "🔥 <b>" in pub_en["text"]
    assert "📖 <b>What Happened:</b>" in pub_en["text"]
    assert "💡 <b>Bottom Line:</b>" in pub_en["text"]
    assert "2655.40" not in pub_en["text"]  # ZERO inline price tickers in news alerts
    assert "reply_markup" not in pub_en      # Link-free standard post (website lives in single pinned header)
    assert "http" not in pub_en["text"].lower()

    vip_en = build_vip_channel_payload(
        news_item,
        feed,
        chat_id="@RatePathVIP_EN",
        mini_app_url="https://ratepath.io/",
        lang="en",
    )
    assert not _ARABIC_RE.search(vip_en["text"])
    assert vip_en["text"].count("\n\n") >= 2
    assert "🔥 <b>[VIP]" in vip_en["text"]
    assert "📖 <b>What Happened:</b>" in vip_en["text"]
    assert "💡 <b>Bottom Line:</b>" in vip_en["text"]
    assert "reply_markup" not in vip_en
    assert "http" not in vip_en["text"].lower()

    # 2. Pure-Arabic Public & VIP Payloads (Authentic Arabic + ZERO EN chrome + Link-Free Standard)
    pub_ar = build_public_channel_payload(
        news_item,
        feed,
        chat_id="@RatePathAR",
        mini_app_url="https://ratepath.io/",
        lang="ar",
    )
    assert pub_ar["chat_id"] == "@RatePathAR"
    assert _ARABIC_RE.search(pub_ar["text"])
    assert pub_ar["text"].count("\n\n") >= 2
    assert "🔥 <b>" in pub_ar["text"]
    assert "📖 <b>ما القصة؟</b>" in pub_ar["text"]
    assert "💡 <b>الخلاصة ومسار السيولة:</b>" in pub_ar["text"]
    assert "What Happened" not in pub_ar["text"]
    assert "2655.40" not in pub_ar["text"]  # ZERO inline price tickers in news alerts
    assert "reply_markup" not in pub_ar
    assert "http" not in pub_ar["text"].lower()

    vip_ar = build_vip_channel_payload(
        news_item,
        feed,
        chat_id="@RatePathVIP_AR",
        mini_app_url="https://ratepath.io/",
        lang="ar",
    )
    assert _ARABIC_RE.search(vip_ar["text"])
    assert vip_ar["text"].count("\n\n") >= 2
    assert "📖 <b>ما القصة؟</b>" in vip_ar["text"]
    assert "💡 <b>الخلاصة ومسار السيولة:</b>" in vip_ar["text"]
    assert "Bottom Line" not in vip_ar["text"]
    assert "reply_markup" not in vip_ar
    assert "http" not in vip_ar["text"].lower()

    # 3. Silent Admin Watchdog formatting & placeholder graceful skip
    watchdog_payload = build_admin_watchdog_payload(
        "Primary Feed Timeout", "Failover to Bybit succeeded", admin_chat_id="123456"
    )
    assert watchdog_payload["disable_notification"] is True

    placeholder_cfg = _placeholder_settings()
    skip_res = asyncio.run(
        send_admin_watchdog_alert("Test", "Placeholder skip", settings=placeholder_cfg)
    )
    assert skip_res["status"] == "skipped_placeholder"

    # 4. 4-Channel Broadcast (EN Public/VIP + AR Public/VIP) + Upstash Redis Deduplication
    redis_keys: set[str] = set()
    sent_messages: list[dict[str, object]] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "upstash.io" in url:
            cmd = json.loads(request.content.decode("utf-8"))
            assert cmd[0] == "SET" and cmd[-1] == "NX"
            key = cmd[1]
            if key in redis_keys:
                return httpx.Response(200, json={"result": None})
            redis_keys.add(key)
            return httpx.Response(200, json={"result": "OK"})
        if "api.telegram.org" in url:
            sent_messages.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404)

    four_channel_cfg = Settings(
        gemini_api_key="placeholder",
        groq_api_key="placeholder",
        openrouter_api_key="placeholder",
        supabase_url="placeholder",
        supabase_key="placeholder",
        upstash_redis_rest_url="https://mock-redis.upstash.io",
        upstash_redis_rest_token="live_upstash_token",
        telegram_bot_token="123456:LIVE_BOT_TOKEN",
        telegram_public_channel="placeholder",
        telegram_vip_channel="placeholder",
        telegram_public_channel_en="@RatePathEN",
        telegram_vip_channel_en="@RatePathVIP_EN",
        telegram_public_channel_ar="@RatePathAR",
        telegram_vip_channel_ar="@RatePathVIP_AR",
        admin_chat_id="999888777",
        mini_app_url="https://ratepath.io/",
    )

    dedup_file = tmp_path / "telegram_dedup.tmp.json"
    dedup = NewsDeduplicator(settings=four_channel_cfg, local_cache_path=dedup_file)

    async def _run_broadcast() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
            first_run = await broadcast_feed_to_telegram(
                feed, settings=four_channel_cfg, deduplicator=dedup, client=client
            )
            assert first_run["public_en_sent"] == 1
            assert first_run["vip_en_sent"] == 1
            assert first_run["public_ar_sent"] == 1
            assert first_run["vip_ar_sent"] == 1
            assert first_run["public_sent"] == 2
            assert first_run["vip_sent"] == 2
            assert first_run["duplicates_skipped"] == 0

            # Second run with identical news URL must be 100% deduplicated
            second_run = await broadcast_feed_to_telegram(
                feed, settings=four_channel_cfg, deduplicator=dedup, client=client
            )
            assert second_run["public_sent"] == 0
            assert second_run["vip_sent"] == 0
            assert second_run["duplicates_skipped"] == 1

    asyncio.run(_run_broadcast())
    assert len(sent_messages) == 4
    by_chat = {str(m["chat_id"]): m for m in sent_messages}
    assert not _ARABIC_RE.search(str(by_chat["@RatePathEN"]["text"]))
    assert not _ARABIC_RE.search(str(by_chat["@RatePathVIP_EN"]["text"]))
    assert _ARABIC_RE.search(str(by_chat["@RatePathAR"]["text"]))
    assert _ARABIC_RE.search(str(by_chat["@RatePathVIP_AR"]["text"]))

    # 5. Backward compatibility when only legacy single-channel vars are populated
    legacy_cfg = Settings(
        gemini_api_key="placeholder",
        groq_api_key="placeholder",
        openrouter_api_key="placeholder",
        supabase_url="placeholder",
        supabase_key="placeholder",
        upstash_redis_rest_url="placeholder",
        upstash_redis_rest_token="placeholder",
        telegram_bot_token="123456:LIVE_BOT_TOKEN",
        telegram_public_channel="@LegacyPublic",
        telegram_vip_channel="@LegacyVIP",
        admin_chat_id="placeholder",
    )
    assert legacy_cfg.resolve_telegram_targets() == {
        "public_legacy": "@LegacyPublic",
        "vip_legacy": "@LegacyVIP",
    }


def test_monetization_config_100_usd_default_and_cloud_files() -> None:
    """Verify `monetization.config.js`, `100 USD` initial UX state, Dockerfile & Cloudflare SEO."""
    frontend_dir = WORKSPACE_ROOT / "frontend"
    mon_cfg = (frontend_dir / "monetization.config.js").read_text(encoding="utf-8")
    index_html = (frontend_dir / "index.html").read_text(encoding="utf-8")
    app_js = (frontend_dir / "js" / "app.js").read_text(encoding="utf-8")
    headers_cfg = (frontend_dir / "_headers").read_text(encoding="utf-8")
    redirects_cfg = (frontend_dir / "_redirects").read_text(encoding="utf-8")
    sitemap_xml = (frontend_dir / "sitemap.xml").read_text(encoding="utf-8")
    robots_txt = (frontend_dir / "robots.txt").read_text(encoding="utf-8")
    dockerfile = (WORKSPACE_ROOT / "Dockerfile").read_text(encoding="utf-8")

    # 1. Centralized monetization.config.js keys (LIVE_FEED_URL, PUBLIC_SITE_URL, 4-channel EN/AR)
    for required_token in (
        "LIVE_FEED_URL",
        "PUBLIC_SITE_URL",
        "MONETIZATION_CONFIG",
        "wise:",
        "remitly:",
        "bybit:",
        "binance:",
        "aAdsUnitId:",
        "nowPaymentsVipUrl:",
        "publicChannelEn:",
        "vipChannelEn:",
        "publicChannelAr:",
        "vipChannelAr:",
        "botUrl:",
    ):
        assert required_token in mon_cfg

    # Verify port 8765 server isolation, localhost SW bypass, and frontend/logo.png wiring
    sw_js = (frontend_dir / "sw.js").read_text(encoding="utf-8")
    server_py = (frontend_dir / "server.py").read_text(encoding="utf-8")
    assert "IS_LOCALHOST" in sw_js and "if (IS_LOCALHOST) return;" in sw_js
    assert "8765" in server_py
    if (frontend_dir / "logo.png").exists():
        assert "./logo.png" in index_html
        manifest_text = (frontend_dir / "manifest.json").read_text(encoding="utf-8")
        assert "./logo.png" in manifest_text

    # 2. Wired into app.js & index.html + strict `100 USD` initial default + dynamic EN/AR binding
    assert 'from "../monetization.config.js"' in app_js
    assert 'amount: "100"' in app_js
    assert 'baseAssetId: "USD"' in app_js
    assert "applyLanguageAwareTelegramLinks" in app_js
    assert "publicChannelAr" in app_js and "vipChannelAr" in app_js
    assert "publicChannelEn" in app_js and "vipChannelEn" in app_js
    assert 'urlLang === "ar"' in app_js and 'path === "/ar"' in app_js
    assert 'value="100"' in index_html
    assert "$100.00 USD" in index_html
    assert "nowpayments-vip-link" in index_html
    assert 'data-tg-channel="vip"' in index_html
    assert 'data-tg-channel="public"' in index_html

    # Check `.env.example` documents all 4 separate channel variables
    env_example = (WORKSPACE_ROOT / ".env.example").read_text(encoding="utf-8")
    for env_key in (
        "TELEGRAM_PUBLIC_CHANNEL_EN",
        "TELEGRAM_VIP_CHANNEL_EN",
        "TELEGRAM_PUBLIC_CHANNEL_AR",
        "TELEGRAM_VIP_CHANNEL_AR",
    ):
        assert env_key in env_example

    # 3. Dockerfile & Cloudflare Pages SEO / Routing
    assert "EXPOSE 7860" in dockerfile
    assert "backend.cloud_worker" in dockerfile
    assert "Access-Control-Allow-Origin: *" in headers_cfg
    assert "/ar" in redirects_cfg and "/index.html?lang=ar" in redirects_cfg
    assert "https://ratepathlive.com/ar" in sitemap_xml
    assert "Sitemap: https://ratepathlive.com/sitemap.xml" in robots_txt


def test_gitignore_and_dockerignore_strictly_exclude_env_and_caches() -> None:
    """Verify `.env`, `.venv/`, and `*.tmp.json` are strictly shielded from Git & Docker."""
    gitignore_lines = {
        ln.strip()
        for ln in (WORKSPACE_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    dockerignore_lines = {
        ln.strip()
        for ln in (WORKSPACE_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    required_exclusions = {".env", ".venv/", "*.tmp.json", "__pycache__/", ".pytest_cache/"}
    assert required_exclusions.issubset(gitignore_lines)
    assert required_exclusions.issubset(dockerignore_lines)
    assert (WORKSPACE_ROOT / "LAUNCH_CHECKLIST_AR.md").exists()


def test_six_key_rotator_round_robin_and_60s_cooldown_on_429_timeout() -> None:
    """Verify AsyncKeyPool round-robin rotation and 60-second cooldown on 429 & timeouts."""
    from datetime import datetime, timedelta, timezone
    from backend.ai_strategist import AsyncKeyPool, MultiProviderRotator, probe_provider_via_rotator

    async def _run() -> None:
        base_time = datetime(2026, 11, 25, 0, 0, 0, tzinfo=timezone.utc)
        pool = AsyncKeyPool(
            "gemini",
            ("gem_k1", "placeholder", "gem_k2", "", "gem_k3"),
            cooldown_seconds=60,
        )
        assert pool.size == 3

        # Round-robin order: slot 1 -> slot 2 -> slot 3 -> slot 1
        assert await pool.acquire(now=base_time) == (1, "gem_k1")
        assert await pool.acquire(now=base_time) == (2, "gem_k2")
        assert await pool.acquire(now=base_time) == (3, "gem_k3")

        # Put key 1 into 60s cooldown -> skipped at +30s, restored at +61s
        await pool.mark_cooldown("gem_k1", now=base_time)
        assert pool.is_in_cooldown("gem_k1", now=base_time + timedelta(seconds=30))
        assert await pool.acquire(now=base_time + timedelta(seconds=30)) == (2, "gem_k2")
        assert not pool.is_in_cooldown("gem_k1", now=base_time + timedelta(seconds=61))

        # Integration test: key #1 returns 429, key #2 times out, key #3 succeeds
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            key_used = request.headers.get("Authorization", "").replace("Bearer ", "")
            calls.append(key_used)
            if key_used == "groq_k1":
                return httpx.Response(429, text="Rate limit")
            if key_used == "groq_k2":
                raise httpx.ReadTimeout("Simulated timeout")
            body = json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {"status": "ok", "platform_ar": "ريت باث"},
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
                ensure_ascii=False,
            )
            return httpx.Response(200, content=body.encode("utf-8"))

        cfg = Settings(
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
            groq_api_keys=("groq_k1", "groq_k2", "groq_k3"),
        )
        assert cfg.active_ai_key_counts["groq"] == 3
        rotator = MultiProviderRotator(cfg, cooldown_seconds=60)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            res = await probe_provider_via_rotator(
                "groq", settings=cfg, rotator=rotator, client=client
            )
            assert res["ok"] is True
            assert res["slot"] == 3
            assert res["platform_ar"] == "ريت باث"
            assert calls == ["groq_k1", "groq_k2", "groq_k3"]
            assert rotator.groq_pool.is_in_cooldown("groq_k1") is True
            assert rotator.groq_pool.is_in_cooldown("groq_k2") is True
            assert rotator.groq_pool.is_in_cooldown("groq_k3") is False

    asyncio.run(_run())

