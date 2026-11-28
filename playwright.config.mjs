import { defineConfig } from "@playwright/test";

const PORT = Number(process.env.QA_PORT || 8788);
const BASE_URL = process.env.QA_BASE_URL || `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop-1440x900",
      use: {
        browserName: "chromium",
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "mobile-390x844",
      use: {
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: process.env.QA_BASE_URL
    ? undefined
    : {
        command: "node scripts/local_prod_server.mjs",
        url: `http://127.0.0.1:${PORT}/api/geo`,
        reuseExistingServer: true,
        timeout: 20_000,
        env: { PORT: String(PORT), HOST: "127.0.0.1" },
      },
});
