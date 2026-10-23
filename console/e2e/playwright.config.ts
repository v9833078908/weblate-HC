import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

import { assertE2ETarget } from "./support/seed";

const baseURL = assertE2ETarget().href;
const serverCommand = process.env.PRODUCER_CONSOLE_E2E_SERVER_COMMAND;
const repositoryRoot = fileURLToPath(new URL("../..", import.meta.url));

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 10_000 },
  globalSetup: "./support/seed.ts",
  reporter: [["list"]],
  use: {
    baseURL,
    locale: "ru-RU",
    trace: "retain-on-failure",
  },
  webServer: serverCommand
    ? {
        command: serverCommand,
        cwd: repositoryRoot,
        env: process.env as Record<string, string>,
        reuseExistingServer: false,
        timeout: 120_000,
        url: new URL("console/", baseURL).href,
      }
    : undefined,
});
