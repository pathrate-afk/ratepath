/**
 * RatePath Phase 3 — Real Autocannon Stress & Load Test Suite (Zero Mocks).
 * Runs 50 concurrent connections x 20s AND 100 concurrent connections x 20s across:
 *   1. Homepage (`/`)
 *   2. Programmatic SEO Page (`/usd-to-syp`)
 *   3. Real Edge API: `/api/strategist-signals`
 *   4. Real Edge API: `/api/live-news?lang=ar`
 *   5. Uncached Edge API (`no-store`): `/api/telegram-webhook`
 */

import autocannon from "autocannon";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 8791;
const BASE = `http://127.0.0.1:${PORT}`;

function runBench(url, connections, duration) {
  return new Promise((resolve, reject) => {
    autocannon(
      {
        url,
        connections,
        duration,
        pipelining: 1,
        timeout: 15,
      },
      (err, res) => {
        if (err) reject(err);
        else resolve(res);
      }
    );
  });
}

async function waitForServer(url, maxMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {
      // Wait for server startup
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("Local prod server failed to start in time");
}

const serverProc = spawn(process.execPath, [path.join(ROOT, "scripts", "local_prod_server.mjs")], {
  env: { ...process.env, PORT: String(PORT), HOST: "127.0.0.1" },
  stdio: ["ignore", "pipe", "pipe"],
});

try {
  await waitForServer(`${BASE}/api/geo`);

  // Real warm-up of heaviest API endpoints (populates real s-maxage=300 edge cache just like Cloudflare Pages)
  for (const warmPath of ["/api/strategist-signals", "/api/live-news?lang=ar", "/api/telegram-webhook"]) {
    const r = await fetch(`${BASE}${warmPath}`);
    if (!r.ok) throw new Error(`Warm-up failed for ${warmPath}: HTTP ${r.status}`);
  }

  const targets = [
    { name: "Homepage (/)", path: "/" },
    { name: "SEO Page (/usd-to-syp)", path: "/usd-to-syp" },
    { name: "API Quant Signals (/api/strategist-signals)", path: "/api/strategist-signals" },
    { name: "API Live News (/api/live-news?lang=ar)", path: "/api/live-news?lang=ar" },
    { name: "API Webhook Uncached (/api/telegram-webhook)", path: "/api/telegram-webhook" },
  ];

  const report = [];
  for (const t of targets) {
    for (const c of [50, 100]) {
      const res = await runBench(`${BASE}${t.path}`, c, 20);
      const row = {
        target: t.name,
        connections: c,
        duration_s: 20,
        total_requests: res.requests.total,
        rps_avg: Math.round(res.requests.average),
        latency_p50_ms: res.latency.p50,
        latency_p97_5_ms: res.latency.p97_5 ?? res.latency.p99,
        latency_p99_ms: res.latency.p99,
        status_2xx: res["2xx"],
        non_2xx: res.non2xx,
        errors: res.errors,
        timeouts: res.timeouts,
        throughput_mb_s: Number((res.throughput.average / (1024 * 1024)).toFixed(2)),
      };
      report.push(row);
      console.log(JSON.stringify(row));
    }
  }

  const totalErrors = report.reduce((s, r) => s + r.errors + r.timeouts + r.non_2xx, 0);
  console.log("STRESS_SUMMARY_TOTAL_ERRORS=" + totalErrors);
  if (totalErrors > 0) process.exitCode = 1;
} finally {
  serverProc.kill("SIGTERM");
}
