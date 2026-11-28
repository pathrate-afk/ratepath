/**
 * RatePath Phase 4 — Real Headless Lighthouse Performance, Accessibility, Best Practices & SEO Audit.
 * Uses Playwright's installed Chromium binary against the local production server (zero mocks).
 */

import { chromium } from "@playwright/test";
import lighthouse from "lighthouse";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 8792;
const BASE = `http://127.0.0.1:${PORT}`;
const CDP_PORT = 9229;

async function waitForUrl(url, maxMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {
      // Wait for boot
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`Timeout waiting for ${url}`);
}

const serverProc = spawn(process.execPath, [path.join(ROOT, "scripts", "local_prod_server.mjs")], {
  env: { ...process.env, PORT: String(PORT), HOST: "127.0.0.1" },
  stdio: ["ignore", "pipe", "pipe"],
});

const chromePath = chromium.executablePath();
const chromeProc = spawn(
  chromePath,
  [
    `--remote-debugging-port=${CDP_PORT}`,
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
  ],
  { stdio: "ignore" }
);

try {
  await Promise.all([
    waitForUrl(`${BASE}/api/geo`),
    waitForUrl(`http://127.0.0.1:${CDP_PORT}/json/version`),
  ]);
  await new Promise((r) => setTimeout(r, 500));

  const targets = [
    { label: "Homepage (Mobile)", url: `${BASE}/`, preset: "mobile" },
    { label: "Homepage (Desktop)", url: `${BASE}/`, preset: "desktop" },
    { label: "SEO /usd-to-syp (Mobile)", url: `${BASE}/usd-to-syp`, preset: "mobile" },
    { label: "SEO /usd-to-syp (Desktop)", url: `${BASE}/usd-to-syp`, preset: "desktop" },
  ];

  const results = [];
  for (const t of targets) {
    const flags = {
      port: CDP_PORT,
      output: "json",
      logLevel: "error",
      onlyCategories: ["performance", "accessibility", "best-practices", "seo"],
      formFactor: t.preset,
      screenEmulation:
        t.preset === "desktop"
          ? { mobile: false, width: 1440, height: 900, deviceScaleFactor: 1, disabled: false }
          : { mobile: true, width: 390, height: 844, deviceScaleFactor: 2, disabled: false },
    };
    let runnerRes = await lighthouse(t.url, flags);
    if (runnerRes.lhr.runtimeError || runnerRes.lhr.categories.performance?.score === null) {
      await new Promise((r) => setTimeout(r, 600));
      runnerRes = await lighthouse(t.url, flags);
    }
    const lhr = runnerRes.lhr;
    const scoreOf = (id) => Math.round((lhr.categories[id]?.score ?? 0) * 100);
    const failedAudits = Object.values(lhr.audits)
      .filter((a) => a.score !== null && a.score < 0.9 && a.scoreDisplayMode !== "informative" && a.scoreDisplayMode !== "notApplicable")
      .map((a) => ({ id: a.id, title: a.title, score: a.score, display: a.displayValue || "" }))
      .slice(0, 12);

    const summary = {
      label: t.label,
      scores: {
        performance: scoreOf("performance"),
        accessibility: scoreOf("accessibility"),
        best_practices: scoreOf("best-practices"),
        seo: scoreOf("seo"),
      },
      vitals: {
        fcp: lhr.audits["first-contentful-paint"]?.displayValue || "",
        lcp: lhr.audits["largest-contentful-paint"]?.displayValue || "",
        tbt: lhr.audits["total-blocking-time"]?.displayValue || "",
        cls: lhr.audits["cumulative-layout-shift"]?.displayValue || "",
      },
      notable_audits: failedAudits,
    };
    results.push(summary);
    console.log(JSON.stringify(summary));
  }
} finally {
  chromeProc.kill("SIGTERM");
  serverProc.kill("SIGTERM");
}
