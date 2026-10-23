import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

// React Router builds Fetch API requests during memory-router navigation.
// jsdom's AbortSignal belongs to a different realm from Node's Request, so the
// test-only wrapper lets Request create its own compatible signal.
const NativeRequest = Request;
class CompatibleRequest extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const compatibleInit = init ? { ...init, signal: undefined } : init;
    super(input, compatibleInit);
  }
}
vi.stubGlobal("Request", CompatibleRequest);

const translations: Record<string, string> = {
  "Advanced Weblate": "Открыть в Weblate",
  Cancel: "Отмена",
  Close: "Закрыть",
  Create: "Создать",
  "Create project": "Создать проект",
  "Creating project…": "Создание проекта…",
  "Check the project name.": "Проверьте название проекта.",
  "Loading project…": "Загрузка проекта…",
  "Loading projects…": "Загрузка проектов…",
  "Project name": "Название проекта",
  "Project search": "Поиск проекта",
  Projects: "Проекты",
  "You do not have permission to create a project.":
    "Недостаточно прав для создания проекта.",
  "Your session has expired.": "Сессия истекла.",
};

beforeEach(() => {
  vi.stubGlobal(
    "gettext",
    (message: string) => translations[message] ?? message,
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.stubGlobal("Request", CompatibleRequest);
});
