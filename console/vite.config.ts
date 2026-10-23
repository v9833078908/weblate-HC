import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/console/",
  plugins: [react()],
  build: {outDir: "dist", emptyOutDir: true, manifest: true},
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["./src/test/setup.ts"],
    environmentOptions: {jsdom: {url: "https://weblate.test/weblate/console/projects/pirate-ships"}},
  },
});
