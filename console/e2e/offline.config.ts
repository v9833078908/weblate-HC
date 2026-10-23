import { fileURLToPath } from "node:url";
import { defineConfig } from "@playwright/test";

const consoleRoot = fileURLToPath(new URL("..", import.meta.url));
const baseURL = "http://127.0.0.1:19538/";

export default defineConfig({
  testDir: ".",
  testMatch: "isolation.spec.ts",
  workers: 1,
  timeout: 30_000,
  reporter: "list",
  use: { baseURL, locale: "ru-RU", trace: "retain-on-failure" },
  webServer: {
    command: "node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 19538 --strictPort",
    cwd: consoleRoot,
    url: new URL("console/", baseURL).href,
    reuseExistingServer: false,
  },
});
