import { defineConfig, devices } from "@playwright/test";

const host = "http://127.0.0.1:4173";
const artifactDir = "/tmp/mospromhack-playwright";

export default defineConfig({
  testDir: "./playwright",
  outputDir: `${artifactDir}/test-results`,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  reporter: [
    ["list"],
    ["html", { outputFolder: `${artifactDir}/playwright-report`, open: "never" }],
  ],
  use: {
    baseURL: host,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: {
        ...devices["Desktop Chrome"],
        browserName: "chromium",
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "mobile-chromium",
      use: {
        ...devices["iPhone 13"],
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 1,
        isMobile: true,
      },
    },
    {
      name: "tablet-chromium",
      use: {
        ...devices["Desktop Chrome"],
        browserName: "chromium",
        viewport: { width: 768, height: 1024 },
      },
    },
  ],
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4173",
    url: host,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
