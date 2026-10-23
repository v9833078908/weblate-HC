import { expect, type Page, test } from "@playwright/test";

import { assertE2ETarget, E2E_PROJECTS, E2E_USERS } from "./support/seed";

const baseUrl = assertE2ETarget();

function appUrl(path: string): string {
  return new URL(path.replace(/^\/+/, ""), baseUrl).href;
}

function appPath(path: string): string {
  return new URL(path.replace(/^\/+/, ""), baseUrl).pathname;
}

async function login(
  page: Page,
  user: (typeof E2E_USERS)[keyof typeof E2E_USERS],
) {
  await page.locator("#id_username").fill(user.username);
  await page.locator("#id_password").fill(user.password);
  await page.locator('input[type="submit"]').click();
}

async function openCreateDialog(page: Page) {
  const create = page.getByRole("button", { name: "Создать проект" });
  await create.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
}

async function submitProject(page: Page, name: string) {
  await page.getByRole("textbox", { name: "Название проекта" }).fill(name);
  await page.getByRole("button", { name: "Создать", exact: true }).click();
}

test("first Wave 0 slice creates a persisted project through native auth", async ({
  context,
  page,
}) => {
  const deepLink = appPath(`console/projects/${E2E_PROJECTS.allowed.slug}`);
  await page.goto(appUrl(`console/projects/${E2E_PROJECTS.allowed.slug}`));
  expect(new URL(page.url()).pathname).toBe(appPath("accounts/login/"));
  expect(new URL(page.url()).searchParams.get("next")).toBe(deepLink);

  await login(page, E2E_USERS.creator);
  await expect(page).toHaveURL(
    appUrl(`console/projects/${E2E_PROJECTS.allowed.slug}`),
  );
  await expect(
    page.getByRole("heading", { name: E2E_PROJECTS.allowed.name }),
  ).toBeVisible();

  await page.goto(appUrl("console/projects"));
  await expect(
    page.getByRole("link", { name: E2E_PROJECTS.allowed.name, exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: E2E_PROJECTS.restricted.name, exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: E2E_PROJECTS.lastPage.name, exact: true }),
  ).toBeVisible();

  let createRequests = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === appPath("api/producer/projects/")
    ) {
      createRequests += 1;
    }
  });
  await openCreateDialog(page);
  await submitProject(page, E2E_PROJECTS.created.name);
  await expect(page).toHaveURL(
    appUrl(`console/projects/${E2E_PROJECTS.created.slug}`),
  );
  expect(createRequests).toBe(1);
  await page.reload();
  const advanced = page.getByRole("link", { name: "Открыть в Weblate" });
  const expectedAdvanced = new URL(
    `projects/${E2E_PROJECTS.created.slug}/`,
    baseUrl,
  ).href;
  await expect(advanced).toHaveAttribute("href", expectedAdvanced);
  await advanced.click();
  await expect(page).toHaveURL(expectedAdvanced);

  await context.clearCookies();
  await page.goto(appUrl("console/projects"));
  await login(page, E2E_USERS.denied);
  await expect(page).toHaveURL(appUrl("console/projects"));
  await openCreateDialog(page);
  await submitProject(page, "E2E Forbidden Project");
  await expect(page.getByRole("alert")).toContainText("нет прав");
  await expect(page).toHaveURL(appUrl("console/projects"));

  await context.clearCookies();
  await page.goto(appUrl("console/projects"));
  await login(page, E2E_USERS.creator);
  await expect(
    page.getByRole("link", { name: E2E_PROJECTS.lastPage.name, exact: true }),
  ).toBeVisible();
  await openCreateDialog(page);
  await page
    .getByRole("textbox", { name: "Название проекта" })
    .fill("E2E Expired Session");
  await context.clearCookies();
  await page
    .getByRole("button", { name: "Создать", exact: true })
    .click({ noWaitAfter: true });
  await page.waitForURL((url) => url.pathname === appPath("accounts/login/"));
  expect(new URL(page.url()).pathname).toBe(appPath("accounts/login/"));
  await login(page, E2E_USERS.creator);
  await expect(
    page.getByRole("link", { name: "E2E Expired Session" }),
  ).toHaveCount(0);

  const projectsApiUrl = appUrl("api/producer/projects/");
  await page.route(projectsApiUrl, async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    const headers = { ...route.request().headers() };
    delete headers["x-csrftoken"];
    await route.continue({ headers });
  });
  await openCreateDialog(page);
  await submitProject(page, "E2E Missing CSRF");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(appUrl("console/projects"));
  await page.unroute(projectsApiUrl);
  await page.reload();
  await expect(
    page.getByRole("link", { name: "E2E Missing CSRF" }),
  ).toHaveCount(0);

  await page.setViewportSize({ width: 320, height: 640 });
  const createButton = page.getByRole("button", { name: "Создать проект" });
  await expect(createButton).toBeVisible();
  expect((await createButton.boundingBox())?.x).toBeGreaterThanOrEqual(0);
  expect((await createButton.boundingBox())?.width).toBeLessThanOrEqual(320);
  await page.goto(appUrl(`console/projects/${E2E_PROJECTS.created.slug}`));
  await expect(advanced).toBeVisible();
  const advancedBox = await advanced.boundingBox();
  expect(
    (advancedBox?.x ?? -1) + (advancedBox?.width ?? 321),
  ).toBeLessThanOrEqual(320);
});
