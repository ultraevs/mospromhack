import { defineConfig, devices } from "@playwright/test";

// e2e ходят в живой backend через vite proxy: перед запуском поднять scripts\dev.ps1
export default defineConfig({
  testDir: "./playwright",
  outputDir: "test-results",
  timeout: 30_000,
  expect: { timeout: 6_000 },
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  use: {
    baseURL: process.env.E2E_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
});
