import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect, type Page, test } from "@playwright/test";

import { assertE2ETarget, E2E_PROJECTS, E2E_USERS } from "./support/seed";

const baseUrl = assertE2ETarget();
const fixture = fileURLToPath(
  new URL(
    "../../weblate/trans/tests/data/producer/ws-localizations-structure.xlsx",
    import.meta.url,
  ),
);
const glossaryFixture = fileURLToPath(
  new URL(
    "../../weblate/trans/tests/data/producer/glossary-en-source.csv",
    import.meta.url,
  ),
);
const currentSheets = [
  "Default",
  "Units",
  "Perks",
  "Arcade",
  "Settings",
  "GDPR",
  "Tutorial",
] as const;

interface UploadSnapshot {
  revision: string;
  ui_mode: "quick" | "detailed";
  answers: unknown;
  errors: unknown[];
}

interface ExportDocument {
  components: Array<{
    id: string;
    columns: Array<{ header: string }>;
    rows: Array<{ values: unknown[] }>;
  }>;
}

function appUrl(path: string): string {
  return new URL(path.replace(/^\/+/, ""), baseUrl).href;
}

async function login(page: Page) {
  await page.locator("#id_username").fill(E2E_USERS.creator.username);
  await page.locator("#id_password").fill(E2E_USERS.creator.password);
  await page.locator('input[type="submit"]').click();
}

async function getUpload(page: Page, token: string): Promise<UploadSnapshot> {
  const response = await page.request.get(appUrl(`api/producer/uploads/${token}/`));
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as UploadSnapshot;
}

async function waitForUploadPatch(page: Page, action: () => Promise<void>) {
  const responsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "PATCH" &&
      new URL(response.url()).pathname.includes("/api/producer/uploads/"),
  );
  await action();
  expect((await responsePromise).ok()).toBeTruthy();
}

async function selectCurrentSheets(page: Page) {
  for (const name of currentSheets) {
    const row = page
      .locator("label.sheet-row")
      .filter({ has: page.getByText(name, { exact: true }) });
    await row.getByRole("checkbox").check();
  }
  const store = page
    .locator("label.sheet-row")
    .filter({ has: page.getByText("Store", { exact: true }) });
  await expect(store.getByRole("checkbox")).toBeDisabled();
  await waitForUploadPatch(page, () =>
    page.getByRole("button", { name: "Save selection" }).click(),
  );
}

async function chooseLanguages(page: Page) {
  await page.getByRole("combobox", { name: "Source language" }).selectOption("en");
  await page.getByRole("checkbox", { name: "ru", exact: true }).check();
  await page.getByRole("checkbox", { name: "es", exact: true }).check();
  await waitForUploadPatch(page, () =>
    page.getByRole("button", { name: "Save languages" }).click(),
  );
}

async function prepareLocalization(
  page: Page,
  slug: string,
  { glossary = false }: { glossary?: boolean } = {},
) {
  await page.goto(appUrl(`console/projects/${slug}/localize`));
  await login(page);
  await page.getByLabel("Loc-kit files").setInputFiles(fixture);
  await expect(page.getByText("Default", { exact: true })).toBeVisible();
  const token = new URL(page.url()).searchParams.get("u");
  expect(token).toBeTruthy();

  await selectCurrentSheets(page);
  await chooseLanguages(page);

  const quick = await getUpload(page, token!);
  expect(quick.errors).toEqual([]);
  const answers = JSON.stringify(quick.answers);

  await waitForUploadPatch(page, () =>
    page.getByRole("button", { name: "Detailed", exact: true }).click(),
  );
  await expect(page.getByRole("heading", { name: "Game context" })).toBeVisible();
  const detailed = await getUpload(page, token!);
  expect(detailed.revision).toBe(quick.revision);
  expect(JSON.stringify(detailed.answers)).toBe(answers);

  await page.reload();
  await expect(page.getByRole("heading", { name: "Game context" })).toBeVisible();
  await page.getByRole("link", { name: slug, exact: true }).click();
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Game context" })).toBeVisible();
  await waitForUploadPatch(page, () =>
    page.getByRole("button", { name: "Quick", exact: true }).click(),
  );
  await expect(
    page.getByRole("heading", { name: "Game context" }),
  ).toBeVisible();

  const restored = await getUpload(page, token!);
  expect(restored.revision).toBe(quick.revision);
  expect(JSON.stringify(restored.answers)).toBe(answers);
  expect(restored.ui_mode).toBe("quick");

  if (glossary) {
    const glossaryResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith(`/projects/${slug}/uploads/`),
    );
    await page.getByLabel("Glossary file").setInputFiles(glossaryFixture);
    expect((await glossaryResponse).ok()).toBeTruthy();
    await expect(page.getByText("Glossary prepared")).toBeVisible();
  }

  await waitForUploadPatch(page, () =>
    page.getByRole("button", { name: "Review and start" }).click(),
  );
  await expect(page.getByText("20", { exact: true })).toBeVisible();
  await expect(page.getByText("18", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Review quarantined rows" }).click();
  await expect(
    page.getByText(/Default!6.*The row is completely empty/),
  ).toBeVisible();
  await expect(
    page.getByText(/Default!7.*The selected source cell is empty/),
  ).toBeVisible();

  let startRequests = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname.endsWith(`/projects/${slug}/localization/`)
    ) {
      startRequests += 1;
    }
  });
  await page.getByRole("button", { name: "Start localization" }).click();
  await expect(page).toHaveURL(new RegExp(`/console/projects/${slug}/runs/`), {
    timeout: 90_000,
  });
  const runUrl = page.url();
  await expect(page.getByText("completed", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Review and download" })).toBeVisible();
  expect(startRequests).toBe(1);
  await page.reload();
  await expect(page).toHaveURL(runUrl);
  await expect(page.getByText("completed", { exact: true })).toBeVisible();

  await page.getByRole("link", { name: "Review and download" }).click();
  await expect(
    page.getByRole("heading", { name: "Download localization results" }),
  ).toBeVisible();
  await page.getByLabel("Format").selectOption("json");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  const download = await downloadPromise;
  const path = await download.path();
  expect(path).not.toBeNull();
  const document = JSON.parse(await readFile(path!, "utf8")) as ExportDocument;
  expect(document.components).toHaveLength(currentSheets.length);
  expect(document.components.map((component) => component.id).sort()).toEqual(
    [...currentSheets].map((name) => name.toLowerCase()).sort(),
  );
  const defaultComponent = document.components.find(
    (component) => component.id === "default",
  );
  expect(defaultComponent?.columns.slice(0, 3).map((column) => column.header)).toEqual([
    "Key",
    "English(en)",
    "Russian(ru)",
  ]);
  const start = defaultComponent?.rows.find((row) => row.values[0] === "ui.start");
  expect(start?.values.slice(1, 4)).toEqual([
    "<b>Start</b>",
    "<b>Начать</b>",
    "<b>Start</b>",
  ]);

  return { runUrl, token: token! };
}

test("localizes the selected multi-sheet kit and preserves one draft across modes", async ({
  page,
}) => {
  await prepareLocalization(page, E2E_PROJECTS.workflow.slug);
});

test("publishes a prepared glossary before offline MT and preserves existing targets", async ({
  page,
}) => {
  const { runUrl } = await prepareLocalization(page, E2E_PROJECTS.glossary.slug, {
    glossary: true,
  });
  const runId = new URL(runUrl).pathname.split("/").filter(Boolean).at(-1);
  const response = await page.request.get(appUrl(`api/producer/runs/${runId}/`));
  expect(response.ok()).toBeTruthy();
  const run = (await response.json()) as {
    summary: { glossary_terms: number };
    stages: { glossary: { status: string } };
  };
  expect(run.stages.glossary.status).toBe("completed");
  expect(run.summary.glossary_terms).toBe(4);
});
