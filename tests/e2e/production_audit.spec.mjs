import { test, expect } from "@playwright/test";

const SEO_ROUTES = [
  "/usd-to-syp",
  "/usd-to-try",
  "/usd-to-egp",
  "/gold-price-today",
  "/btc-to-usdt",
  "/usdt-to-try",
  "/usdt-to-syp",
  "/eth-to-usdt",
  "/eur-to-usd",
  "/silver-price-today",
];

/**
 * Strict Zero-Tolerance Page Telemetry Guard:
 * Fails immediately on ANY console.error, uncaught pageerror, >=400 HTTP response,
 * failed network request, broken image (`naturalWidth === 0`), or horizontal overflow.
 */
function attachStrictGuards(page, { allow404Path = null } = {}) {
  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];
  const badResponses = [];

  page.on("console", (msg) => {
    if (msg.type() === "error") {
      const locUrl = msg.location()?.url || "";
      if (allow404Path && locUrl.includes(allow404Path) && msg.text().includes("404")) return;
      consoleErrors.push(msg.text());
    }
  });
  page.on("pageerror", (err) => {
    pageErrors.push(String(err));
  });
  page.on("requestfailed", (req) => {
    const url = req.url();
    const reason = req.failure()?.errorText || "unknown";
    // Ignore benign browser-aborted requests triggered during intentional route navigation
    if (reason.includes("ERR_ABORTED")) return;
    failedRequests.push(`${req.method()} ${url} (${reason})`);
  });
  page.on("response", (resp) => {
    const status = resp.status();
    const url = resp.url();
    if (allow404Path && url.includes(allow404Path) && status === 404) return;
    if (status >= 400) {
      badResponses.push(`${status} ${resp.request().method()} ${url}`);
    }
  });

  return {
    async assertClean(contextLabel) {
      expect(consoleErrors, `[${contextLabel}] Console errors found`).toEqual([]);
      expect(pageErrors, `[${contextLabel}] Uncaught page errors found`).toEqual([]);
      expect(failedRequests, `[${contextLabel}] Failed network requests found`).toEqual([]);
      expect(badResponses, `[${contextLabel}] HTTP 4xx/5xx responses found`).toEqual([]);

      // Zero broken visible images
      const brokenImgs = await page.evaluate(() => {
        const imgs = Array.from(document.querySelectorAll("img"));
        return imgs
          .filter((img) => {
            const rect = img.getBoundingClientRect();
            const isVisible = rect.width > 0 && rect.height > 0 && getComputedStyle(img).display !== "none";
            return isVisible && (!img.complete || img.naturalWidth === 0);
          })
          .map((img) => img.getAttribute("src") || img.alt || "unknown-img");
      });
      expect(brokenImgs, `[${contextLabel}] Broken images detected`).toEqual([]);

      // Zero horizontal overflow
      const overflow = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
      }));
      expect(
        overflow.scrollWidth,
        `[${contextLabel}] Horizontal overflow detected: scrollWidth=${overflow.scrollWidth} > innerWidth=${overflow.innerWidth}`
      ).toBeLessThanOrEqual(overflow.innerWidth);
    },
  };
}

test.describe("RatePath Strict Production E2E Suite (Desktop 1440x900 & Mobile 390x844)", () => {
  test("1. Main Terminal (/): Live data, Converter, Search, Detail Modal, Quote PNG, VIP Modals & Language Toggle", async ({
    page,
  }, testInfo) => {
    const isMobile = testInfo.project.name.includes("mobile");
    const guard = attachStrictGuards(page);

    await page.goto("/", { waitUntil: "networkidle" });
    await page.waitForTimeout(800);

    // 1. Live UTC clock + Non-empty 18-asset default workspace
    const cardsCount = await page.locator("[data-ws-card]").count();
    expect(cardsCount, "Default workspace must never be empty").toBeGreaterThanOrEqual(18);

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain("Your workspace is empty");
    expect(bodyText).not.toContain("monetization.config.js");
    expect(bodyText).not.toContain("NaN");

    if (!isMobile) {
      const utcText = (await page.locator("#last-updated-time").innerText()).trim();
      expect(utcText).toMatch(/\d{2}:\d{2}/);
    }

    // 2. Converter interaction: enter 250 and switch base currency
    const amtInput = page.locator("#converter-amount-input");
    await amtInput.fill("250");
    await expect(page.locator("#usd-equivalent-readout")).toContainText("250");

    await page.locator("#converter-asset-select").selectOption("BTC");
    await expect(page.locator("#usd-equivalent-readout")).not.toContainText("$250.00 USD");
    await page.locator("#converter-asset-select").selectOption("USD");
    await amtInput.fill("100");

    // 3. Language toggle EN <-> AR
    await page.locator("#btn-lang-ar").click();
    await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
    await expect(page.locator("html")).toHaveAttribute("lang", "ar");
    await page.locator("#btn-lang-en").click();
    await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
    await expect(page.locator("html")).toHaveAttribute("lang", "en");

    // 4. Add Currency Search: XRP dedup (1 hit) + Arabic "سولانا" local hit
    if (isMobile) {
      await page.locator('.mobile-nav-btn[data-tab="rates"]').click();
      await page.waitForTimeout(200);
    }
    await page.locator("#btn-open-asset-picker").click();
    await expect(page.locator("#modal-asset-picker")).toBeVisible();
    const pickerSearch = page.locator('#modal-asset-picker input[type="search"], #modal-asset-picker input[type="text"]').first();
    await pickerSearch.fill("XRP");
    await page.waitForTimeout(250);
    const xrpCount = await page.locator("#asset-picker-results [data-picker-toggle]").filter({ hasText: /./ }).count();
    const xrpRows = await page.locator("#asset-picker-results > div").filter({ hasText: /\bXRP\b/ }).count();
    expect(xrpCount).toBeGreaterThanOrEqual(1);
    expect(xrpRows, "XRP search must return exactly 1 deduplicated result").toBe(1);

    await pickerSearch.fill("سولانا");
    await page.waitForTimeout(250);
    await expect(page.locator("#asset-picker-results")).toContainText("SOL");
    await page.locator("#btn-close-picker-modal").click();
    await expect(page.locator("#modal-asset-picker")).toBeHidden();

    // 5. Asset Detail Modal: BTC -> converter 50 -> EUR -> Swap -> Close
    await page.locator('[data-select-asset="BTC"]').first().click();
    await expect(page.locator("#modal-asset-detail")).toBeVisible();
    await expect(page.locator("#detail-modal-title")).toContainText("BTC");
    const detailAmt = page.locator("#detail-conv-amount");
    await detailAmt.fill("50");
    await page.locator("#detail-conv-target").selectOption("EUR");
    await page.locator("#btn-detail-conv-swap").click();
    await expect(page.locator("#detail-modal-title")).toContainText("BTC");
    await expect(detailAmt).toHaveValue("50");
    await expect(page.locator("#detail-conv-result")).toContainText("50 EUR =");
    await page.locator("#btn-close-detail-modal").click();
    await expect(page.locator("#modal-asset-detail")).toBeHidden();

    // 6. Quote Card PNG modal
    await page.locator("#btn-open-quote-card").click();
    await expect(page.locator("#modal-quote-card")).toBeVisible();
    const canvasDims = await page.locator("#quote-card-canvas").evaluate((c) => ({ w: c.width, h: c.height }));
    expect(canvasDims.w).toBe(1200);
    expect(canvasDims.h).toBe(630);
    await page.locator("#btn-close-quote-modal").click();
    await expect(page.locator("#modal-quote-card")).toBeHidden();

    // 7. Mobile bottom nav tabs OR Desktop VIP Alerts modal
    if (isMobile) {
      await expect(page.locator("#mobile-bottom-nav")).toBeVisible();
      for (const tab of ["rates", "funding", "news", "converter"]) {
        const btn = page.locator(`.mobile-nav-btn[data-tab="${tab}"]`);
        if (await btn.isVisible()) {
          await btn.click();
          await page.waitForTimeout(150);
        }
      }
      await page.locator('.mobile-nav-btn[data-tab="vip"]').click();
      await expect(page.locator("#modal-vip-alerts")).toBeVisible();
      await page.locator("#btn-close-vip-modal").click();
    } else {
      await page.locator("#btn-open-vip-alerts").click();
      await expect(page.locator("#modal-vip-alerts")).toBeVisible();
      await page.locator("#btn-close-vip-modal").click();
    }

    await guard.assertClean(`Main Terminal / (${testInfo.project.name})`);
  });

  test("2. Bilingual Root Routes (/ar & /en) render cleanly with zero errors", async ({ page }, testInfo) => {
    const guard = attachStrictGuards(page);
    for (const route of ["/ar", "/en"]) {
      await page.goto(route, { waitUntil: "networkidle" });
      await page.waitForTimeout(400);
      await guard.assertClean(`Route ${route} (${testInfo.project.name})`);
    }
  });

  test("3. All 10 Programmatic SEO Routes: Zero errors, live hydration, Converter, Swap, Lang Toggle & Zero Overflow", async ({
    page,
  }, testInfo) => {
    const guard = attachStrictGuards(page);

    for (const route of SEO_ROUTES) {
      await page.goto(route, { waitUntil: "networkidle" });
      await page.waitForTimeout(300);

      // Hero rate hydrated (not blank or dash)
      const heroRate = (await page.locator("#seo-hero-rate").innerText()).trim();
      expect(heroRate, `${route} hero rate missing`).toMatch(/\d/);
      expect(heroRate).not.toBe("—");

      // Live UTC clock visible & ticking
      await expect(page.locator("#seo-utc-clock")).toBeVisible();
      const utcTxt = (await page.locator("#seo-utc-clock").innerText()).trim();
      expect(utcTxt).toMatch(/\d{2}:\d{2}:\d{2} UTC/);

      // Telegram CTA visible
      await expect(page.locator('a[href*="RatePathTerminalBot"]').first()).toBeVisible();

      // Zero raw Unicode Syria flag emoji anywhere
      const rawHtml = await page.content();
      expect(rawHtml).not.toContain("\uD83C\uDDF8\uD83C\uDDFE");

      // Interactive converter + Swap + Language toggle on 3 primary routes
      if (route === "/usd-to-syp" || route === "/gold-price-today" || route === "/btc-to-usdt") {
        const beforeOut = (await page.locator("#conv-output").innerText()).trim();
        await page.locator("#conv-amount").fill("100");
        await page.waitForTimeout(150);
        const afterOut = (await page.locator("#conv-output").innerText()).trim();
        expect(afterOut).not.toBe(beforeOut);

        const thFromBefore = (await page.locator("#quick-th-from").innerText()).trim();
        await page.locator("#conv-swap").click();
        const thFromAfter = (await page.locator("#quick-th-from").innerText()).trim();
        expect(thFromAfter).not.toBe(thFromBefore);
        await page.locator("#conv-swap").click();

        const titleBefore = await page.title();
        await page.locator("#seo-lang-toggle").click();
        const titleAfter = await page.title();
        expect(titleAfter).not.toBe(titleBefore);
        await page.locator("#seo-lang-toggle").click();
      }

      if (route === "/usd-to-syp") {
        await expect(page.locator('svg[data-flag="syria-revolution"]')).toBeVisible();
      }
      if (route === "/gold-price-today") {
        expect(await page.locator("body").innerText()).not.toContain("XAU_OZ");
      }

      await guard.assertClean(`SEO ${route} (${testInfo.project.name})`);
    }
  });

  test("4. Real API Endpoints, Sitemap, Robots & 404 Page Verification", async ({ page, request }, testInfo) => {
    // Sitemap & Robots
    const sm = await request.get("/sitemap.xml");
    expect(sm.status()).toBe(200);
    const smBody = await sm.text();
    for (const r of SEO_ROUTES) {
      expect(smBody).toContain(`https://ratepathlive.com${r}`);
    }
    const rb = await request.get("/robots.txt");
    expect(rb.status()).toBe(200);
    expect(await rb.text()).toContain("Sitemap: https://ratepathlive.com/sitemap.xml");

    // Real Edge API endpoints
    const geo = await request.get("/api/geo");
    expect(geo.status()).toBe(200);
    expect((await geo.json()).ok).toBe(true);

    const news = await request.get("/api/live-news?lang=ar");
    expect(news.status()).toBe(200);
    const newsJson = await news.json();
    expect(Array.isArray(newsJson.items || newsJson.news)).toBe(true);

    const signals = await request.get("/api/strategist-signals");
    expect(signals.status()).toBe(200);

    const detail = await request.get("/api/asset-detail?id=BTC&code=BTC&category=crypto&name=Bitcoin&lang=en");
    expect(detail.status()).toBe(200);

    const tgGet = await request.get("/api/telegram-webhook");
    expect(tgGet.status()).toBe(200);
    expect((await tgGet.json()).ok).toBe(true);

    // Security rejection checks: invalid webhook secret & invalid IPN HMAC must return 401
    const tgBadPost = await request.post("/api/telegram-webhook", {
      headers: { "X-Telegram-Bot-Api-Secret-Token": "invalid_secret_test" },
      data: { update_id: 1 },
    });
    expect(tgBadPost.status()).toBe(401);

    const ipnBadPost = await request.post("/api/nowpayments-webhook", {
      headers: { "x-nowpayments-sig": "deadbeef" },
      data: { order_id: "123_vip10_1", payment_status: "finished" },
    });
    expect(ipnBadPost.status()).toBe(401);

    // 404 Page rendering
    const guard404 = attachStrictGuards(page, { allow404Path: "/nonexistent-404-audit" });
    const resp404 = await page.goto("/nonexistent-404-audit", { waitUntil: "networkidle" });
    expect(resp404.status()).toBe(404);
    await expect(page.locator("body")).toContainText("الصفحة غير موجودة");
    await guard404.assertClean(`404 Page (${testInfo.project.name})`);
  });
});
