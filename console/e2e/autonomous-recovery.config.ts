import {defineConfig} from "@playwright/test";
export default defineConfig({
  testDir:".", testMatch:"autonomous-recovery.spec.ts", workers:1,
  use:{baseURL:"http://127.0.0.1:18786",locale:"ru-RU",trace:"retain-on-failure"},
  webServer:{command:"node autonomous-server.mjs",url:"http://127.0.0.1:18786/console/",reuseExistingServer:false},
});
