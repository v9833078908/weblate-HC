import { afterEach, expect, it, vi } from "vitest";
import { t } from "./i18n";
import russian from "./locales/ru.json";
import { readFileSync } from "node:fs";

afterEach(() => vi.unstubAllGlobals());

it("provides Russian captions without a Django gettext runtime", () => {
  vi.stubGlobal("gettext", undefined);
  expect(t("Projects")).toBe("Проекты");
  expect(t("Prepare the loc-kit")).toBe("Подготовить лок-кит");
  expect(t("Start localization")).toBe("Запустить локализацию");
  expect(t("queued")).toBe("В очереди");
  expect(t("missing_source")).toBe("Нет текста на заявленном, русском или английском языке");
  expect(t("External Weblate returned HTTP 403")).toBe("Внешний Weblate вернул код HTTP 403.");
  expect(t("German(de)")).toBe("German(de)");
});

it("keeps Russian captions when an external gettext returns untranslated keys", () => {
  vi.stubGlobal("gettext", (message: string) => message);
  expect(t("Projects")).toBe("Проекты");
});

it("covers every English caption in the maintained UI catalog", () => {
  const source = readFileSync("src/gettext-catalog.js", "utf8");
  const messages = [...source.matchAll(/gettext\(("(?:\\.|[^"\\])*")\)/g)].map(match => JSON.parse(match[1]) as string);
  expect(messages.length).toBeGreaterThan(100);
  for (const message of messages.filter(item => /^[A-Za-z]/.test(item))) {
    expect((russian as Record<string, string>)[message], message).toMatch(/[А-Яа-яЁё]/);
  }
});
