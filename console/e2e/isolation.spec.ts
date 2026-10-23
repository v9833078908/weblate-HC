import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";

const require = createRequire(import.meta.url);

for (const width of [1440, 390]) {
  test(`isolated run remains readable without paid actions at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 });
    let reads = 0;
    const writes: string[] = [];
    const run = { id: "archived", status: "archived_unknown", requested_mode: "translate",
      created: "2026-10-23T00:00:00Z", started: null, finished: null, summary: {},
      failure: "", warnings: [], stages: {}, liveness: "archived_unknown", outcome: null,
      resume_allowed: true, resume_reason: null, preparation_phase: "translate", coverage: {} };
    await page.route("**/*", async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.hostname !== "127.0.0.1") return route.abort();
      if (!url.pathname.startsWith("/api/producer/")) {
        if (request.isNavigationRequest()) {
          const response = await route.fetch();
          const metadata = { "console-base": "/console/", "producer-console-root": "/console/",
            "producer-api-root": "/api/producer/", "producer-login-url": "/auth/login/", "csrf-token": "offline-fixture" };
          const tags = Object.entries(metadata).map(([name, content]) => `<meta name="${name}" content="${content}">`).join("");
          return route.fulfill({ response, body: (await response.text()).replace("<!--WIZARD_META-->", tags) });
        }
        return route.continue();
      }
      if (request.method() !== "GET") {
        writes.push(url.pathname);
        return route.fulfill({ status: 405 });
      }
      reads++;
      return route.fulfill({ json: run });
    });
    await page.goto("/console/projects/offline/runs/archived");
    await expect(page.getByText("Изолировано", { exact: true })).toBeVisible();
    await expect(page.getByText(/История сохранена; результат одного запроса неизвестен/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Продолжить незавершённое задание" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Отменить дальнейшую обработку" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Скачать диагностический отчёт" })).toBeVisible();
    const before = reads;
    await page.getByRole("button", { name: "Проверить состояние" }).focus();
    await page.keyboard.press("Enter");
    await expect.poll(() => reads).toBeGreaterThan(before);
    expect(writes).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
    const violations = await page.evaluate(async () => {
      const results = await (window as unknown as { axe: { run(): Promise<{violations: Array<{impact: string; id: string}>}> } }).axe.run();
      return results.violations.filter(item => ["serious", "critical"].includes(item.impact));
    });
    expect(violations).toEqual([]);
    await page.screenshot({ path: info.outputPath(`archived-${width}.png`), fullPage: true });
  });
}
