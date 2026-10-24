import { expect, type Page, test } from "@playwright/test";

const ADMIN = { login: process.env.E2E_ADMIN ?? "admin", password: process.env.E2E_ADMIN_PASSWORD ?? "admin1234" };
const VIEWER = { login: process.env.E2E_VIEWER ?? "viewer", password: process.env.E2E_VIEWER_PASSWORD ?? "viewer1234" };

async function login(page: Page, who: { login: string; password: string }) {
  await page.goto("/");
  await page.getByLabel("Логин").fill(who.login);
  await page.getByLabel("Пароль", { exact: true }).fill(who.password);
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.locator(".sidebar")).toBeVisible();
}

function openMenu(page: Page) {
  return page.viewportSize()!.width < 760 ? page.getByRole("button", { name: "Меню" }).click() : Promise.resolve();
}

test("неверный пароль не пускает", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Логин").fill(ADMIN.login);
  await page.getByLabel("Пароль", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "Войти" }).click();
  await expect(page.getByRole("alert")).toHaveText("Неверный логин или пароль");
});

test("админ проходит по всем разделам без ошибок в консоли", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page, ADMIN);
  const sections: [string, string][] = [
    ["Камеры", "Камеры"], ["Журнал", "Журнал нарушений"], ["Аналитика", "Аналитика"],
    ["Производительность", "Производительность"], ["Настройки", "Настройки"], ["Пользователи", "Пользователи"], ["Обзор", "Обзор смены"],
  ];
  for (const [link, title] of sections) {
    await openMenu(page);
    await page.locator(".sidebar nav").getByRole("link", { name: link }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  expect(errors).toEqual([]);
});

test("журнал: строка открывает карточку и ссылка на нарушение живёт в адресе", async ({ page }) => {
  await login(page, ADMIN);
  await page.goto("/events");
  const rows = page.locator(".events-table tbody tr");
  test.skip(await rows.count() === 0, "в базе нет нарушений");
  await rows.first().click();
  await expect(page).toHaveURL(/event=/);
  await expect(page.locator(".detail")).toBeVisible();
  await page.reload();
  await expect(page.locator(".detail")).toBeVisible();
});

test("камеры: переключение и видеостена", async ({ page }) => {
  await login(page, ADMIN);
  await page.goto("/cameras");
  const chips = page.locator(".switcher button");
  test.skip(await chips.count() === 0, "камер нет");
  await chips.last().click();
  await expect(chips.last()).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "Видеостена" }).click();
  await expect(page).toHaveURL(/mode=wall/);
  await expect(page.locator(".sidebar")).toBeVisible();
});

test("оператор видит только камеры и журнал", async ({ page }) => {
  await login(page, VIEWER);
  await openMenu(page);
  await expect(page.locator(".sidebar nav a")).toHaveText([/Камеры/, /Журнал/]);
  await page.goto("/settings");
  await expect(page).toHaveURL(/\/cameras/);
});
