import {test,expect} from "@playwright/test";

test("shows durable progress, status-only GET and an immutable verification job", async ({page})=>{
  const posts: Array<{path:string;body:Record<string,unknown>}> = [];
  let reads = 0;
  const run = {id:"old-job",status:"completed",requested_mode:"translate",created:"2026-10-23T00:00:00Z",
    started:null,finished:null,summary:{},failure:"",warnings:[],stages:{},liveness:"completed",
    outcome:"ready",resume_allowed:false,coverage:{},execution_mode:"repair",
    translation_coverage:{filled:1662,total:1662,missing:0},
    judge_coverage:{both_parsed:1647,pass:1594,without_pass:68,total:1662},payment_outcome:{unknown:2}};
  await page.route("**/api/producer/**", async route=>{
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      posts.push({path,body:request.postDataJSON()});
      expect(path).toBe("/api/producer/projects/offline/quality-runs/");
      return route.fulfill({json:{...run,id:"verify-job",requested_mode:"quality",execution_mode:"verify_only"}});
    }
    if (path.includes("verification-preview")) return route.fulfill({json:{project:"offline",languages:["pl"],selected:1662,initial_calls:334,cost:null,
      request:{parent_run_id:"old-job",language:"pl",scope:{unit_ids:[1,2]},glossary_revision:"r1",execution_mode:"verify_only"}}});
    if (path.includes("/quality/")) return route.fulfill({status:404,json:{detail:"No report"}});
    reads++;
    return route.fulfill({json:path.includes("verify-job") ? {...run,id:"verify-job",requested_mode:"quality",execution_mode:"verify_only"} : run});
  });
  await page.goto("/console/projects/offline/runs/old-job");
  await expect(page.getByText("1662 / 1662",{exact:true})).toBeVisible();
  await expect(page.getByText("1594 / 1662",{exact:true})).toBeVisible();
  const before=reads;
  const refresh=page.getByRole("button",{name:"Проверить состояние"});
  await refresh.focus(); await page.keyboard.press("Enter");
  await expect.poll(()=>reads).toBeGreaterThan(before);
  expect(posts).toHaveLength(0);
  await page.getByRole("button",{name:"Проверить готовые переводы без изменений"}).click();
  await expect(page.getByText(/Точная стоимость неизвестна/)).toBeVisible();
  await page.getByRole("button",{name:"Запустить проверку"}).click();
  await expect(page).toHaveURL(/verify-job$/);
  expect(posts).toHaveLength(1);
  expect(posts[0].body.execution_mode).toBe("verify_only");
  await expect(page.getByText("Только проверка — без изменения переводов",{exact:true})).toBeVisible();
  await expect(page.getByText("Машинный перевод",{exact:true})).toHaveCount(0);
});
