"""24/7 Autonomous Cloud Worker & CORS HTTP Server for RatePath (ريت باث).

Designed for Hugging Face Spaces (Docker Port 7860):
- Exposes `GET /health` JSON keep-alive endpoint (for `cron-job.org`).
- Exposes `GET /live_feed.json` with CORS (`Access-Control-Allow-Origin: *`) for Cloudflare Pages.
- Runs two concurrent background `asyncio` loops:
  1. Market + Macro News + Telegram Publisher cycle every 5 minutes (`300` seconds).
  2. 24-hour Self-Training Accuracy Evaluator (`evaluator.py`) every 60 minutes (`3600` seconds).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from backend.config import (
    CONTRACT_SCHEMA_PATH,
    LIVE_FEED_TARGET_PATH,
    Settings,
    enforce_utf8_runtime,
)
from backend.evaluator import MarketMemoryStore, extract_price_snapshot
from backend.math_engine import loads_decimal_json
from backend.publisher import execute_live_cycle
from backend.seed_history import seed_market_memory
from backend.telegram_bot import send_admin_watchdog_alert

DEFAULT_WORKER_PORT: int = 7860
MARKET_CYCLE_INTERVAL_SECONDS: int = 300   # 5 minutes
EVALUATOR_INTERVAL_SECONDS: int = 3600     # 60 minutes

CORS_HEADERS: tuple[tuple[str, str], ...] = (
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type, Authorization, Cache-Control"),
    ("Access-Control-Max-Age", "86400"),
    ("X-Content-Type-Options", "nosniff"),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(dt: datetime | None = None) -> str:
    target = dt or _utc_now()
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class WorkerState:
    """In-memory telemetry state exposed via `/health` (pure integer counters, zero floats)."""

    started_at: datetime = field(default_factory=_utc_now)
    port: int = DEFAULT_WORKER_PORT
    market_cycles_completed: int = 0
    evaluations_completed: int = 0
    graded_predictions_total: int = 0
    last_feed_generated_at: str | None = None
    last_evaluation_at: str | None = None
    last_error: str | None = None

    def uptime_seconds(self) -> int:
        delta = _utc_now() - self.started_at
        return max(0, delta.days * 86400 + delta.seconds)

    def to_health_dict(self, feed_path: Path = LIVE_FEED_TARGET_PATH) -> dict[str, Any]:
        generated_at = self.last_feed_generated_at
        if generated_at is None and feed_path.exists():
            try:
                raw = feed_path.read_text(encoding="utf-8")
                parsed = json.loads(raw)
                generated_at = parsed.get("meta", {}).get("generated_at")
            except Exception:
                generated_at = None

        return {
            "status": "ok" if self.last_error is None else "degraded",
            "platform": "RatePath",
            "platform_ar": "ريت باث",
            "service": "huggingface_cloud_worker",
            "port": self.port,
            "uptime_seconds": self.uptime_seconds(),
            "market_cycles_completed": self.market_cycles_completed,
            "evaluations_completed": self.evaluations_completed,
            "graded_predictions_total": self.graded_predictions_total,
            "last_feed_generated_at": generated_at,
            "last_evaluation_at": self.last_evaluation_at,
            "last_error": self.last_error,
            "timestamp": _utc_iso(),
        }


def build_http_response(
    status_code: int,
    reason: str,
    body_bytes: bytes,
    *,
    content_type: str = "application/json; charset=utf-8",
    cache_control: str = "no-store",
    head_only: bool = False,
) -> bytes:
    """Construct raw HTTP/1.1 response bytes with CORS & security headers."""
    header_lines = [
        f"HTTP/1.1 {status_code} {reason}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body_bytes)}",
        f"Cache-Control: {cache_control}",
        "Connection: close",
    ]
    for k, v in CORS_HEADERS:
        header_lines.append(f"{k}: {v}")
    raw_head = ("\r\n".join(header_lines) + "\r\n\r\n").encode("utf-8")
    return raw_head if head_only else raw_head + body_bytes


def route_worker_http_request(
    method: str,
    raw_target: str,
    worker_state: WorkerState,
    *,
    feed_path: Path = LIVE_FEED_TARGET_PATH,
) -> bytes:
    """Pure deterministic router for `/health`, `/live_feed.json`, and CORS `OPTIONS`."""
    upper_method = method.upper().strip()
    clean_path = urlsplit(raw_target).path or "/"

    if upper_method == "OPTIONS":
        return build_http_response(204, "No Content", b"", cache_control="public, max-age=86400")

    head_only = upper_method == "HEAD"
    if upper_method not in ("GET", "HEAD"):
        err_body = json.dumps(
            {"error": "Method Not Allowed", "allowed": ["GET", "HEAD", "OPTIONS"]},
            ensure_ascii=False,
        ).encode("utf-8")
        return build_http_response(405, "Method Not Allowed", err_body)

    if clean_path in ("/", "/health", "/healthz"):
        health_payload = worker_state.to_health_dict(feed_path=feed_path)
        body = (json.dumps(health_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        return build_http_response(200, "OK", body, head_only=head_only)

    if clean_path in (
        "/live_feed.json",
        "/public/live_feed.json",
        "/frontend/public/live_feed.json",
    ):
        if not feed_path.exists():
            warming_body = json.dumps(
                {
                    "status": "warming_up",
                    "message": "Initial live market cycle is in progress.",
                },
                ensure_ascii=False,
            ).encode("utf-8")
            return build_http_response(503, "Service Unavailable", warming_body, head_only=head_only)

        feed_bytes = feed_path.read_text(encoding="utf-8").encode("utf-8")
        return build_http_response(
            200,
            "OK",
            feed_bytes,
            cache_control="public, max-age=60, stale-while-revalidate=240",
            head_only=head_only,
        )

    not_found_body = json.dumps(
        {
            "error": "Not Found",
            "endpoints": ["/health", "/live_feed.json"],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    return build_http_response(404, "Not Found", not_found_body, head_only=head_only)


async def _handle_client_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    worker_state: WorkerState,
    feed_path: Path,
) -> None:
    try:
        request_line_bytes = await asyncio.wait_for(reader.readline(), timeout=10)
        if not request_line_bytes:
            return
        request_line = request_line_bytes.decode("utf-8", errors="replace").strip()
        parts = request_line.split(" ")
        method = parts[0] if len(parts) >= 1 else "GET"
        target = parts[1] if len(parts) >= 2 else "/"

        # Drain HTTP headers up to empty line (bounded to prevent memory abuse)
        for _ in range(64):
            hline = await asyncio.wait_for(reader.readline(), timeout=5)
            if not hline or hline in (b"\r\n", b"\n"):
                break

        resp_bytes = route_worker_http_request(
            method, target, worker_state, feed_path=feed_path
        )
        writer.write(resp_bytes)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Background Autonomous Loops (5-Minute Market Cycle & 60-Minute Evaluator)
# ---------------------------------------------------------------------------


async def run_single_market_iteration(
    worker_state: WorkerState,
    *,
    settings: Settings | None = None,
    feed_path: Path = LIVE_FEED_TARGET_PATH,
    schema_path: Path = CONTRACT_SCHEMA_PATH,
) -> dict[str, Any] | None:
    """Execute one 5-minute market + news + Telegram cycle with watchdog protection."""
    cfg = settings or Settings.load()
    try:
        payload = await execute_live_cycle(
            target_path=feed_path,
            schema_path=schema_path,
            settings=cfg,
        )
        worker_state.market_cycles_completed += 1
        worker_state.last_feed_generated_at = payload["meta"]["generated_at"]
        worker_state.last_error = None
        return payload
    except Exception as exc:
        err_msg = f"Market cycle error: {exc}"
        worker_state.last_error = err_msg
        await send_admin_watchdog_alert("Cloud Worker Market Cycle Failure", err_msg, settings=cfg)
        return None


async def run_single_evaluator_iteration(
    worker_state: WorkerState,
    *,
    settings: Settings | None = None,
    feed_path: Path = LIVE_FEED_TARGET_PATH,
) -> int:
    """Execute one 60-minute 24h self-evaluation pass against current `live_feed.json`."""
    cfg = settings or Settings.load()
    try:
        if not feed_path.exists():
            return 0
        raw_feed = loads_decimal_json(feed_path.read_text(encoding="utf-8"))
        snapshot = extract_price_snapshot(raw_feed)
        store = MarketMemoryStore(settings=cfg)
        graded = await store.evaluate_due_predictions(snapshot)
        worker_state.evaluations_completed += 1
        worker_state.graded_predictions_total += len(graded)
        worker_state.last_evaluation_at = _utc_iso()
        return len(graded)
    except Exception as exc:
        err_msg = f"Evaluator loop error: {exc}"
        worker_state.last_error = err_msg
        await send_admin_watchdog_alert("Cloud Worker 24h Evaluator Failure", err_msg, settings=cfg)
        return 0


async def market_and_news_loop(
    worker_state: WorkerState,
    stop_event: asyncio.Event,
    *,
    interval_seconds: int = MARKET_CYCLE_INTERVAL_SECONDS,
    settings: Settings | None = None,
    feed_path: Path = LIVE_FEED_TARGET_PATH,
) -> None:
    """Autonomous 5-minute market + news cycle loop."""
    while not stop_event.is_set():
        await run_single_market_iteration(
            worker_state, settings=settings, feed_path=feed_path
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue


async def evaluator_accuracy_loop(
    worker_state: WorkerState,
    stop_event: asyncio.Event,
    *,
    interval_seconds: int = EVALUATOR_INTERVAL_SECONDS,
    settings: Settings | None = None,
    feed_path: Path = LIVE_FEED_TARGET_PATH,
) -> None:
    """Autonomous 60-minute 24h self-training accuracy evaluation loop."""
    while not stop_event.is_set():
        await run_single_evaluator_iteration(
            worker_state, settings=settings, feed_path=feed_path
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue


async def run_cloud_worker(
    *,
    host: str = "0.0.0.0",
    port: int = DEFAULT_WORKER_PORT,
    feed_path: Path = LIVE_FEED_TARGET_PATH,
) -> None:
    """Start HTTP server on `port` (`7860`) and run both autonomous loops forever."""
    enforce_utf8_runtime()
    cfg = Settings.load()
    worker_state = WorkerState(port=port)
    stop_event = asyncio.Event()

    # Ensure historical macro twins are seeded on container startup
    try:
        await seed_market_memory(settings=cfg)
    except Exception:
        pass

    server = await asyncio.start_server(
        lambda r, w: _handle_client_stream(r, w, worker_state, feed_path),
        host=host,
        port=port,
    )

    print(
        f"[RatePath | ريت باث] 24/7 Cloud Worker listening on http://{host}:{port} "
        f"(/health & /live_feed.json)",
        flush=True,
    )

    market_task = asyncio.create_task(
        market_and_news_loop(worker_state, stop_event, settings=cfg, feed_path=feed_path)
    )
    eval_task = asyncio.create_task(
        evaluator_accuracy_loop(worker_state, stop_event, settings=cfg, feed_path=feed_path)
    )

    try:
        async with server:
            await server.serve_forever()
    finally:
        stop_event.set()
        market_task.cancel()
        eval_task.cancel()
        await asyncio.gather(market_task, eval_task, return_exceptions=True)


if __name__ == "__main__":
    enforce_utf8_runtime()
    target_port = int(os.environ.get("PORT", str(DEFAULT_WORKER_PORT)))
    asyncio.run(run_cloud_worker(host="0.0.0.0", port=target_port))
