import { expect, type Page, test } from "@playwright/test";

const controlSelectors = ["button", "input", "select", "textarea"];

async function expectHealthyPage(page: Page) {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];

  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(error.message));

  // The app intentionally keeps a WebSocket open for live events, so networkidle
  // is never reached when the backend is healthy. DOM readiness is the right gate.
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveTitle("Контроль безопасности");
  await expect(page.locator("html")).toHaveAttribute("lang", "ru");
  await expect(page.locator("body")).toBeAttached();
  expect(await page.evaluate(() => document.visibilityState)).toBe("visible");

  expect(consoleErrors, "browser console errors").toEqual([]);
  expect(pageErrors, "uncaught page errors").toEqual([]);
}

test("app loads and captures a responsive screenshot", async ({ page }, testInfo) => {
  await expectHealthyPage(page);
  await page.screenshot({
    path: testInfo.outputPath(`${testInfo.project.name}-shell.png`),
    fullPage: true,
  });
});

test("buttons, forms, and navigation are inspectable when present", async ({ page }) => {
  await expectHealthyPage(page);

  for (const selector of controlSelectors) {
    const controls = page.locator(selector);
    for (let index = 0; index < await controls.count(); index += 1) {
      const control = controls.nth(index);
      if (!await control.isVisible()) continue;
      await expect(control).toBeVisible();
      await expect(control).toBeEnabled();
    }
  }

  const links = page.locator("a");
  for (let index = 0; index < await links.count(); index += 1) {
    const link = links.nth(index);
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", /.+/);
  }
});

test("navigation shell renders semantic controls", async ({ page }) => {
  await expectHealthyPage(page);
  await expect(page.locator("nav")).toHaveCount(1);
  expect(await page.locator("button").count()).toBeGreaterThan(4);
  await expect(page.locator("main")).toBeAttached();
});
