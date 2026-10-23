import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

export const E2E_USERS = {
  creator: { username: "producer-e2e", password: "producer-e2e-password" },
  denied: {
    username: "producer-e2e-denied",
    password: "producer-e2e-password",
  },
} as const;

export const E2E_PROJECTS = {
  allowed: { name: "E2E Allowed Project", slug: "e2e-allowed-project" },
  lastPage: {
    name: "E2E Allowed Project 51",
    slug: "e2e-allowed-project-51",
  },
  restricted: {
    name: "E2E Restricted Project",
    slug: "e2e-restricted-project",
  },
  created: { name: "E2E Created Project", slug: "e2e-created-project" },
  workflow: { name: "E2E Allowed Project", slug: "e2e-allowed-project" },
  glossary: {
    name: "E2E Allowed Project 2",
    slug: "e2e-allowed-project-2",
  },
} as const;

const E2E_DATABASE_NAME = "producer_console_e2e";

const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));

export function assertE2ETarget(): URL {
  if (process.env.PRODUCER_CONSOLE_E2E !== "1") {
    throw new Error(
      "Set PRODUCER_CONSOLE_E2E=1 to authorize the isolated E2E run.",
    );
  }
  const rawBaseUrl = process.env.PRODUCER_CONSOLE_E2E_BASE_URL;
  if (!rawBaseUrl) {
    throw new Error("PRODUCER_CONSOLE_E2E_BASE_URL is required.");
  }
  const baseUrl = new URL(rawBaseUrl);
  if (
    baseUrl.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "::1"].includes(baseUrl.hostname)
  ) {
    throw new Error("Producer Console E2E only accepts an HTTP loopback URL.");
  }
  if (!baseUrl.pathname.endsWith("/")) {
    throw new Error("PRODUCER_CONSOLE_E2E_BASE_URL must end with a slash.");
  }
  if (process.env.DJANGO_SETTINGS_MODULE !== "weblate.settings_test") {
    throw new Error("Producer Console E2E requires weblate.settings_test.");
  }
  if (
    !["127.0.0.1", "localhost", "::1"].includes(process.env.CI_DB_HOST ?? "")
  ) {
    throw new Error(
      "Producer Console E2E only accepts a loopback PostgreSQL host.",
    );
  }
  if (process.env.CI_DB_NAME !== E2E_DATABASE_NAME) {
    throw new Error(
      `Producer Console E2E requires the isolated ${E2E_DATABASE_NAME} database.`,
    );
  }
  if (!process.env.PRODUCER_CONSOLE_E2E_PYTHON) {
    throw new Error("PRODUCER_CONSOLE_E2E_PYTHON is required.");
  }
  return baseUrl;
}

function seed(mode: "create" | "cleanup") {
  assertE2ETarget();
  const python = process.env.PRODUCER_CONSOLE_E2E_PYTHON;
  if (!python) {
    throw new Error("PRODUCER_CONSOLE_E2E_PYTHON is required.");
  }
  const result = spawnSync(python, ["console/e2e/support/seed.py", mode], {
    cwd: repositoryRoot,
    env: process.env,
    stdio: "inherit",
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`E2E ${mode} helper exited with status ${result.status}.`);
  }
}

export default function globalSetup() {
  seed("create");
  return () => seed("cleanup");
}
