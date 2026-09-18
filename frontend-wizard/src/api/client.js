// The ONLY module that knows URLs. Every screen consumes typed functions here.
// VITE_API_MODE=mock|live (mapped to NEXT_PUBLIC_API_MODE in this host). Default: mock.
// In live mode, requests go to /api/producer/* with the native Weblate session cookie
// and X-CSRFToken on mutations; a 401 redirects to /accounts/login/?next=<current url>.

import advancedLinksFixture from "./mock/fixtures/advanced_links.json";
import bdhcFixture from "./mock/fixtures/bdhc.json";
import candidatesFixture from "./mock/fixtures/candidates.json";
import costsFixture from "./mock/fixtures/costs.json";
import decisionsFixture from "./mock/fixtures/decisions.json";
import emailsFixture from "./mock/fixtures/emails.json";
import exceptionsFixture from "./mock/fixtures/exceptions.json";
import exportSummaryFixture from "./mock/fixtures/export_summary.json";
import glossaryFixture from "./mock/fixtures/glossary.json";
import candidatesV2Fixture from "./mock/fixtures/glossary_candidates_v2.json";
import meFixture from "./mock/fixtures/me.json";
import profileFixture from "./mock/fixtures/profile.json";
import projectsFixture from "./mock/fixtures/projects.json";
import projectsDetailFixture from "./mock/fixtures/projects_detail.json";
import rowsSetsFixture from "./mock/fixtures/rows_sets.json";
import runsFixture from "./mock/fixtures/runs.json";
import splitSignalsFixture from "./mock/fixtures/split_signals.json";
import storesFixture from "./mock/fixtures/stores.json";
import textEvidenceFixture from "./mock/fixtures/text_evidence.json";
import * as uni from "./mock/universal";
import * as drafts from "./mock/uploads";

const MODE = (process.env.NEXT_PUBLIC_API_MODE || "mock").toLowerCase();
const BASE = "/api/producer";

// realistic latency 300-800ms
const latency = () => 300 + Math.floor(Math.random() * 500);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const clone = (v) => JSON.parse(JSON.stringify(v));

async function mock(value, ms) {
  await wait(ms ?? latency());
  return clone(value);
}

// ---- live-mode fetch (compiles against same types; not exercised in prototype) ----
function getCookie(name) {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp(`(^| )${name}=([^;]+)`));
  return m ? m[2] : null;
}

async function http(path, { method = "GET", body } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (method !== "GET") {
    const csrf = getCookie("csrftoken");
    if (csrf) headers["X-CSRFToken"] = csrf;
  }
  const res = await fetch(`${BASE}/${path}`, {
    method,
    headers,
    credentials: "include",
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && typeof window !== "undefined") {
    const next = encodeURIComponent(
      window.location.pathname + window.location.search,
    );
    window.location.href = `/accounts/login/?next=${next}`;
    return new Promise(() => {});
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw Object.assign(new Error(err.reason || "request_failed"), {
      status: res.status,
      data: err,
    });
  }
  return res.json();
}

// ============================= API surface =============================

export async function getMe() {
  if (MODE === "live") return http("me/");
  return mock(meFixture);
}

export async function getProjects() {
  if (MODE === "live") return http("projects/");
  return mock(projectsFixture);
}

export async function createProject(name) {
  if (MODE === "live")
    return http("projects/", { method: "POST", body: { name } });
  const slug =
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/(^-|-$)/g, "") || "proekt";
  return mock({ slug, name, localized: false });
}

export async function getProject(slug) {
  if (MODE === "live") return http(`projects/${slug}/`);
  const p = projectsDetailFixture[slug];
  if (!p) throw Object.assign(new Error("not_found"), { status: 404 });
  return mock(p);
}

export async function getBdhcTitles(q = "") {
  if (MODE === "live") return http(`bdhc/titles/?q=${encodeURIComponent(q)}`);
  const list = bdhcFixture.titles.filter((t) =>
    t.title.toLowerCase().includes(q.toLowerCase()),
  );
  return mock(list);
}

export async function getBdhcProfile(id) {
  if (MODE === "live") return http(`bdhc/titles/${id}/profile/`);
  return mock(bdhcFixture.profiles[id] || {});
}

export async function getStoreFields(store) {
  if (MODE === "live") return http(`stores/${store}/fields/`);
  return mock(storesFixture[store] || storesFixture.custom);
}

// UploadPreview | 422 {reason}
export async function createUpload(slug, payload) {
  if (MODE === "live")
    return http(`projects/${slug}/uploads/`, { method: "POST", body: payload });
  return mock({
    id: `up-${Math.random().toString(36).slice(2, 8)}`,
    source_language: "ru",
    languages: [
      { code: "ru", name: "Русский", prefilled: 1255 },
      { code: "en", name: "Английский", prefilled: 1255 },
      { code: "fr", name: "Французский", prefilled: 0 },
    ],
    rows: 1258,
    diff: { new_keys: 12, changed_source: 3, unchanged: 1240, removed: 0 },
    quarantine: [
      { key: "debug.placeholder.01", note: "нет текста ни в одном языке" },
      { key: "internal.todo.note", note: "нет текста ни в одном языке" },
      { key: "legacy.unused.key", note: "нет текста ни в одном языке" },
    ],
  });
}

export async function createLocalization(slug, payload) {
  if (MODE === "live")
    return http(`projects/${slug}/localization/`, {
      method: "POST",
      body: payload,
    });
  return mock({
    id: 1,
    kind: "translate",
    status: "queued",
    title: "Прогон #1 · Лок-кит v1 · Перевод",
    started: new Date().toISOString(),
    elapsed: "0 мин",
    stages: [
      { name: "Импорт", status: "done" },
      { name: "Языки", status: "done" },
      { name: "Глоссарий", status: "done" },
      {
        name: "Перевод",
        status: "running",
        detail: `0 / ${payload?.languages?.length || 8} языков`,
      },
      { name: "Проверки", status: "pending" },
    ],
    languages: [],
    cost: [],
  });
}

export async function confirmUpload(slug, id) {
  if (MODE === "live")
    return http(`projects/${slug}/uploads/${id}/confirm/`, { method: "POST" });
  return mock({
    id: 14,
    kind: "translate",
    status: "queued",
    title: "Прогон #14 · Лок-кит v4 · Перевод",
    started: new Date().toISOString(),
    elapsed: "0 мин",
    stages: [
      { name: "Импорт", status: "done" },
      { name: "Языки", status: "done" },
      { name: "Перевод", status: "pending", detail: "0 / 9 языков" },
      { name: "Проверки", status: "pending" },
    ],
    languages: [],
    cost: [],
  });
}

export async function getRuns(slug) {
  if (MODE === "live") return http(`projects/${slug}/runs/`);
  return mock(runsFixture[slug] || []);
}

export async function getRun(slug, id) {
  if (MODE === "live") return http(`runs/${id}/`);
  const all = runsFixture[slug] || [];
  const run = all.find((r) => String(r.id) === String(id)) || all[0];
  return mock(run);
}

export async function estimateRun(slug, scope) {
  if (MODE === "live")
    return http(`projects/${slug}/runs/estimate/`, {
      method: "POST",
      body: { kind: "judge", scope },
    });
  return mock({ strings: 1240, cost_usd: 18, hours_min: 2, hours_max: 3 });
}

export async function createJudgeRun(slug, scope) {
  if (MODE === "live")
    return http(`projects/${slug}/runs/`, {
      method: "POST",
      body: { kind: "judge", scope },
    });
  return mock({
    id: 15,
    kind: "judge",
    status: "queued",
    title: "Прогон #15 · Лок-кит v3 · Оценка качества",
    started: new Date().toISOString(),
    elapsed: "0 мин",
    stages: [
      { name: "Оценка", status: "pending" },
      { name: "Проверка", status: "pending", detail: "0 / 1 240 строк" },
      { name: "Итог", status: "pending" },
    ],
    languages: [],
    cost: [],
  });
}

export async function resumeRun(slug, id) {
  if (MODE === "live") return http(`runs/${id}/resume/`, { method: "POST" });
  const run = (runsFixture[slug] || []).find(
    (r) => String(r.id) === String(id),
  );
  return mock({ ...(run || {}), status: "running" });
}

export async function getDecisions(slug, filters = {}) {
  if (MODE === "live") {
    const qs = new URLSearchParams(filters).toString();
    return http(`projects/${slug}/decisions/?${qs}`);
  }
  let list = decisionsFixture;
  if (slug !== "pirate-ships") list = [];
  if (filters.language)
    list = list.filter((d) => d.language === filters.language);
  if (filters.content) list = list.filter((d) => d.content === filters.content);
  if (filters.kind === "blocking")
    list = list.filter((d) => d.kind === "blocking");
  if (filters.kind === "judge")
    list = list.filter((d) => d.kind !== "blocking");
  if (filters.q)
    list = list.filter((d) =>
      d.key.toLowerCase().includes(String(filters.q).toLowerCase()),
    );
  return mock(list);
}

export async function repairDecision(unitId) {
  if (MODE === "live")
    return http(`decisions/${unitId}/repair/`, { method: "POST" });
  return mock({ run_id: 16 });
}

export async function acceptDecision(unitId, reason) {
  if (MODE === "live")
    return http(`decisions/${unitId}/accept/`, {
      method: "POST",
      body: { reason },
    });
  return mock({ unit_id: unitId, accepted: true, reason });
}

export async function getGlossary(slug) {
  if (MODE === "live") return http(`projects/${slug}/glossary/`);
  return mock(glossaryFixture[slug] || []);
}

export async function getGlossaryCandidates(slug, upload) {
  if (MODE === "live")
    return http(`projects/${slug}/glossary/candidates/?upload=${upload || ""}`);
  return mock(candidatesFixture[slug] || []);
}

export async function addGlossaryTerms(slug, terms) {
  if (MODE === "live")
    return http(`projects/${slug}/glossary/terms/`, {
      method: "POST",
      body: { terms },
    });
  return mock({ added: terms.length, skipped_existing: 0 });
}

export async function getExportSummary(slug, content) {
  if (MODE === "live")
    return http(`projects/${slug}/export/summary/?content=${content || ""}`);
  const s = exportSummaryFixture[slug]?.[content || "whole"] || {
    keys: 0,
    empty: 0,
    by_language: [],
  };
  return mock(s);
}

export async function exportFile(slug, content, format) {
  if (MODE === "live")
    return http(`projects/${slug}/export/?content=${content}&format=${format}`);
  const s = exportSummaryFixture[slug]?.[content || "whole"] || {
    keys: 0,
    empty: 0,
  };
  const ext = format === "json" ? "json" : format === "csv" ? "csv" : "xlsx";
  const filename = `${slug}-${content || "project"}.${ext}`;
  return mock({ filename, keys: s.keys, empty: s.empty });
}

export async function getCosts(slug, period) {
  if (MODE === "live")
    return http(`projects/${slug}/costs/?period=${period || ""}`);
  return mock(
    costsFixture[slug] || {
      total_usd: "0.00",
      by_run: [],
      by_language: [],
      by_model: [],
    },
  );
}

export async function getAdvancedLinks(slug) {
  if (MODE === "live") return http(`projects/${slug}/advanced-links/`);
  return mock(advancedLinksFixture[slug] || {});
}

export function getEmails() {
  return clone(emailsFixture);
}

// ===================== Wizard: uploads & analysis =====================

// POST projects/{slug}/uploads/ → UploadAnalysis
export async function createUploadAnalysis(slug, { file } = {}) {
  if (MODE === "live")
    return http(`projects/${slug}/uploads/`, {
      method: "POST",
      body: { kind: "loc-kit", file },
    });
  await wait(latency() + 1200);
  return drafts.createDraft(file || "pirate-ships.xlsx");
}

// GET uploads/{id}/
export async function getUpload(id) {
  if (MODE === "live") return http(`uploads/${id}/`);
  await wait(200);
  const a = drafts.getDraft(id);
  if (!a) throw Object.assign(new Error("not_found"), { status: 404 });
  return clone(a);
}

// PATCH uploads/{id}/ (re-runs analysis + import gate)
export async function patchUpload(id, patch) {
  if (MODE === "live")
    return http(`uploads/${id}/`, { method: "PATCH", body: patch });
  await wait(latency());
  return clone(drafts.patchDraft(id, patch));
}

// GET uploads/{id}/rows/?set=&value=
export async function getUploadRows(id, set, value) {
  if (MODE === "live")
    return http(`uploads/${id}/rows/?set=${set}&value=${value || ""}`);
  await wait(250);
  if (set === "family") return clone(rowsSetsFixture.family?.[value] || []);
  return clone(rowsSetsFixture[set] || []);
}

// GET uploads/{id}/split-signals/
export async function getSplitSignals(id) {
  if (MODE === "live") return http(`uploads/${id}/split-signals/`);
  await wait(latency());
  const a = drafts.getDraft(id);
  const key = a?.scenario === "positional" ? "positional" : "pirate-ships";
  return clone(splitSignalsFixture[key]);
}

// GET uploads/{id}/text-evidence/
export async function getTextEvidence(id) {
  if (MODE === "live") return http(`uploads/${id}/text-evidence/`);
  await wait(latency());
  return clone(textEvidenceFixture);
}

// GET projects/{slug}/glossary/candidates/?upload=&scope=&extraction=
export async function getCandidatesV2(
  slug,
  { upload, scope, extraction } = {},
) {
  if (MODE === "live") {
    const qs = new URLSearchParams({
      upload: upload || "",
      scope: (scope || []).join(","),
      extraction: extraction || "",
    }).toString();
    return http(`projects/${slug}/glossary/candidates/?${qs}`);
  }
  await wait(latency());
  return clone(candidatesV2Fixture);
}

// POST projects/{slug}/glossary/extractions/ → {id, status}
export async function startExtraction(slug, body) {
  if (MODE === "live")
    return http(`projects/${slug}/glossary/extractions/`, {
      method: "POST",
      body,
    });
  await wait(300);
  return drafts.startExtraction();
}

// GET projects/{slug}/glossary/extractions/{id}/
export async function getExtraction(slug, id) {
  if (MODE === "live")
    return http(`projects/${slug}/glossary/extractions/${id}/`);
  await wait(250);
  return drafts.getExtraction(id);
}

// POST projects/{slug}/glossary/exceptions/preview/ → Exception[]
export async function previewExceptions(slug, body) {
  if (MODE === "live")
    return http(`projects/${slug}/glossary/exceptions/preview/`, {
      method: "POST",
      body,
    });
  await wait(latency());
  return clone(exceptionsFixture);
}

// GET projects/{slug}/profile/
export async function getProfile(slug) {
  if (MODE === "live") return http(`projects/${slug}/profile/`);
  await wait(latency());
  return clone(profileFixture[slug] || profileFixture["pirate-ships"]);
}

// PATCH projects/{slug}/profile/ (invalidates judge cache)
export async function patchProfile(slug, answers) {
  if (MODE === "live")
    return http(`projects/${slug}/profile/`, {
      method: "PATCH",
      body: { answers },
    });
  await wait(latency());
  return {
    answers,
    summary: (profileFixture[slug] || profileFixture["pirate-ships"]).summary,
    judge_cache_invalidated: true,
  };
}

export const API_MODE = MODE;

// ============ Universal 4-stage wizard: uploads, stores registry, launch ============

// GET stores/registry/ → { steam, google_play, app_store, custom }
export async function getStoresRegistry() {
  if (MODE === "live") return http("stores/registry/");
  await wait(150);
  return uni.getStoresRegistry();
}

// POST projects/{slug}/uploads/ → UniversalUploadAnalysis
// `input` is a dropped file name (real drop) or a scenario key (dev/states tiles).
export async function createUniversalUpload(slug, input) {
  if (MODE === "live")
    return http(`projects/${slug}/uploads/`, {
      method: "POST",
      body: { input },
    });
  await wait(latency() + 900);
  return uni.createUniversalDraft(input || "pirate-ships.xlsx");
}

// GET uploads/{id}/ → UniversalUploadAnalysis
export async function getUniversalUpload(id) {
  if (MODE === "live") return http(`uploads/${id}/`);
  await wait(180);
  const a = uni.getUniversalDraft(id);
  if (!a) throw Object.assign(new Error("not_found"), { status: 404 });
  return a;
}

// PATCH uploads/{id}/ (resolve clarifications, replace/remove files, set answers)
export async function patchUniversalUpload(id, patch) {
  if (MODE === "live")
    return http(`uploads/${id}/`, { method: "PATCH", body: patch });
  await wait(latency());
  const a = uni.patchUniversalDraft(id, patch);
  if (!a) throw Object.assign(new Error("not_found"), { status: 404 });
  return a;
}

// POST projects/{slug}/localization/ → Run (universal: kit or store)
export async function createUniversalLocalization(slug, payload) {
  if (MODE === "live")
    return http(`projects/${slug}/localization/`, {
      method: "POST",
      body: payload,
    });
  await wait(latency());
  const isStore = payload?.kind === "store";
  const langs = payload?.languages || [];
  return mock({
    id: 1,
    kind: "translate",
    status: "queued",
    title: isStore
      ? `Прогон #1 · Тексты для стора (${payload?.store || ""}) · Перевод`
      : "Прогон #1 · Лок-кит v1 · Перевод",
    started: new Date().toISOString(),
    elapsed: "0 мин",
    stages: isStore
      ? [
          { name: "Подготовка файлов", status: "done" },
          { name: "Языки", status: "done" },
          { name: "Глоссарий", status: "done" },
          {
            name: "Перевод",
            status: "running",
            detail: `0 / ${langs.length} языков`,
          },
          { name: "Проверка лимитов и разметки", status: "pending" },
          { name: "Сборка ZIP", status: "pending" },
        ]
      : [
          { name: "Импорт", status: "done" },
          { name: "Языки", status: "done" },
          { name: "Глоссарий", status: "done" },
          {
            name: "Перевод",
            status: "running",
            detail: `0 / ${langs.length} языков`,
          },
          { name: "Проверки", status: "pending" },
        ],
    languages: [],
    cost: [],
  });
}
