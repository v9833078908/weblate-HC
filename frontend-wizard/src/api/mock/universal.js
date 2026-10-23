// Mock engine for the universal 4-stage localization wizard.
// One typed surface behind src/api/client.js. A scenario key (chosen by dev/states
// tiles or inferred from a dropped file name) produces a UniversalUploadAnalysis.
// Draft state persists in sessionStorage so a reload / back-nav returns to the step.

import registry from "./fixtures/stores_registry.json";

const KEY = "hcgl:uwiz";
const uid = (p) => p + Math.random().toString(36).slice(2, 8);
const load = () => {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
};
const save = (v) => {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(v));
  } catch {}
};
const clone = (v) => JSON.parse(JSON.stringify(v));

export function getStoresRegistry() {
  return clone(registry);
}

// ---------------------------------------------------------------- sample source texts
const FILES = {
  steam: {
    short: "steam_short.txt",
    about: "steam_about.txt",
    legal: "steam_legal.txt",
  },
  google_play: {
    title: "title.txt",
    short: "short_description.txt",
    full: "full_description.txt",
  },
  app_store: {
    name: "name.txt",
    subtitle: "subtitle.txt",
    description: "description.txt",
    keywords: "keywords.txt",
    promotional: "promotional_text.txt",
  },
};

const SAMPLE = {
  steam: {
    short:
      "Пиратский рогалик про корабли, штормы и проклятые сокровища. Соберите команду, прокачайте судно и бросьте вызов Мёртвой оболочке.",
    about:
      "[b]Пиратские корабли[/b] — морской рогалик о капитане, что бросает вызов проклятию Мёртвой оболочки.\n\nСобирайте команду, улучшайте корабль и исследуйте штормовые воды. Каждый рейс уникален.\n\n[b]Особенности[/b]\n[list]\n[*]Десятки кораблей и модулей\n[*]Живые морские сражения\n[*]Проклятые сокровища и боссы\n[/list]",
    legal:
      "© 2026 Студия. Все права защищены. Указанные товарные знаки принадлежат их владельцам.",
  },
  google_play: {
    title: "Пиратские корабли",
    short: "Морской рогалик про пиратов, штормы и сражения на проклятых водах.",
    full: "Пиратские корабли — морской рогалик о капитане и его команде.\n\nСобирайте экипаж, улучшайте судно, исследуйте штормовые воды и бросьте вызов Мёртвой оболочке. Каждый рейс уникален: маршруты, находки и боссы меняются.",
  },
  app_store: {
    name: "Пиратские корабли",
    subtitle: "Морской рогалик",
    description:
      "Пиратские корабли — морской рогалик о капитане и его команде. Собирайте экипаж, улучшайте судно и бросьте вызов Мёртвой оболочке.",
    keywords: "пираты,корабли,рогалик,море,сокровища,шторм,бой",
    promotional:
      "Новый сезон: проклятые воды и Мёртвая оболочка ждут капитанов.",
  },
};

// build a field row with computed character state
function field(store, id, over = {}) {
  const reg = registry[store];
  const meta = (reg.fields || []).find((f) => f.id === id) || {
    id,
    label: id,
    limit: 0,
    required: true,
  };
  const value =
    over.value !== undefined ? over.value : (SAMPLE[store]?.[id] ?? "");
  const chars = over.chars !== undefined ? over.chars : value.length;
  const limit = over.limit !== undefined ? over.limit : meta.limit;
  let status = "ok";
  let over_by = 0;
  if (over.status) status = over.status;
  else if (!String(value).trim()) status = "empty";
  else if (limit > 0 && chars > limit) {
    status = "over";
    over_by = chars - limit;
  }
  return {
    id,
    label: over.label || meta.label,
    file: over.file || FILES[store]?.[id] || `${id}.txt`,
    limit,
    limit_source: reg.limits_source,
    markup: reg.markup || null,
    value,
    chars,
    status,
    over_by,
    required: meta.required !== false,
    ...(over.extra || {}),
  };
}

function fieldsFor(store, only) {
  const ids = only || (registry[store].fields || []).map((f) => f.id);
  return ids.map((id) => field(store, id));
}

// ---------------------------------------------------------------- scenario builders
function build(scenario) {
  const id = uid("uw-");
  const base = {
    id,
    scenario,
    created: Date.now(),
    kind: null,
    file_name: "",
    source: null,
    stop: null,
    note: null,
    store: null,
    store_confidence: null,
    source_locale: null,
    source_language: "ru",
    fields: null,
    columns: null,
    rows: null,
    format: null,
    languages_found: [],
    lang_count: null,
    source_options: null,
    clarifications: [],
    answers: {
      source_language: "ru",
      languages: null,
      profile: null,
      glossary: null,
    },
  };

  switch (scenario) {
    // 1 — loc-kit success
    case "kit_ok":
      return {
        ...base,
        kind: "loc-kit",
        file_name: "pirate-ships.xlsx",
        source: { kind: "file", label: "файл" },
        format: { kind: "XLSX", sheet: "Strings", encoding: "UTF-8" },
        rows: {
          total: 3864,
          importable: 3830,
          quarantine: 34,
          by_reason: {
            empty_all: 17,
            duplicate_exact: 9,
            duplicate_conflict: 3,
            empty_key: 5,
          },
        },
        columns: [
          { header: "key", role: "Ключ" },
          { header: "Character", role: "Говорящий" },
          { header: "Explanation", role: "Пояснение" },
          { header: "Id", role: "Служебная" },
        ],
        lang_count: 11,
        languages_found: [
          { code: "ru", name: "Русский", fill: 100, source: "file" },
          { code: "en", name: "Английский", fill: 97, source: "file" },
          { code: "de", name: "Немецкий", fill: 96, source: "file" },
          { code: "fr", name: "Французский", fill: 95, source: "file" },
          { code: "es", name: "Испанский", fill: 95, source: "file" },
          {
            code: "pt_BR",
            name: "Португальский (Бразилия)",
            fill: 96,
            source: "file",
          },
          { code: "tr", name: "Турецкий", fill: 90, source: "file" },
          { code: "ja", name: "Японский", fill: 95, source: "file" },
          { code: "ko", name: "Корейский", fill: 95, source: "file" },
          {
            code: "zh_Hans",
            name: "Китайский (упр.)",
            fill: 94,
            source: "file",
          },
          { code: "it", name: "Итальянский", fill: 88, source: "file" },
        ],
      };

    // 2 — Steam: 3 txt recognized
    case "steam_txt":
      return {
        ...base,
        kind: "store",
        store: "steam",
        store_confidence: "high",
        source_locale: "русский",
        file_name: "3 файла",
        source: { kind: "files", label: "3 текстовых файла" },
        fields: fieldsFor("steam"),
      };

    // 3 — Google Play: zip
    case "gplay_zip":
      return {
        ...base,
        kind: "store",
        store: "google_play",
        store_confidence: "high",
        source_locale: "русский, ru-RU",
        file_name: "metadata.zip",
        source: {
          kind: "zip",
          label: "архив",
          structure: [
            "ru-RU/title.txt",
            "ru-RU/short_description.txt",
            "ru-RU/full_description.txt",
          ],
        },
        fields: fieldsFor("google_play"),
      };

    // 4 — App Store: folder
    case "appstore_folder":
      return {
        ...base,
        kind: "store",
        store: "app_store",
        store_confidence: "high",
        source_locale: "русский",
        file_name: "appstore/ru",
        source: { kind: "folder", label: "папка" },
        fields: fieldsFor("app_store"),
      };

    // 5 — unknown txt files → custom store
    case "custom_unknown":
      return {
        ...base,
        kind: "store",
        store: "custom",
        store_confidence: "low",
        file_name: "2 файла",
        source: { kind: "files", label: "2 текстовых файла" },
        fields: [],
        clarifications: [
          {
            code: "unknown_files",
            id: "unk",
            resolved: false,
            files: [
              {
                name: "about_project.txt",
                label: "",
                limit: null,
                no_limit: false,
                limit_source: "",
                markup: "none",
              },
              {
                name: "promo_main.txt",
                label: "",
                limit: null,
                no_limit: false,
                limit_source: "",
                markup: "none",
              },
            ],
          },
        ],
      };

    // 6 — single ambiguous description.txt
    case "ambiguous_one":
      return {
        ...base,
        kind: "ambiguous",
        file_name: "description.txt",
        source: { kind: "file", label: "файл" },
        clarifications: [
          {
            code: "ambiguous_content",
            id: "amb",
            resolved: false,
            file: "description.txt",
            choice: null,
            store: null,
            field: null,
          },
        ],
      };

    // 7 — Steam + Google Play together
    case "two_stores":
      return {
        ...base,
        kind: "store",
        store: null,
        file_name: "stores.zip",
        source: { kind: "zip", label: "архив" },
        clarifications: [
          {
            code: "two_stores",
            id: "two",
            resolved: false,
            choice: null,
            stores: [
              { store: "steam", label: "Steam", field_count: 3 },
              { store: "google_play", label: "Google Play", field_count: 3 },
            ],
          },
        ],
      };

    // 11 — source text over store limit
    case "over_limit_source": {
      const flds = fieldsFor("google_play");
      flds[0] = field("google_play", "title", {
        value: "Пиратские корабли: проклятые сокровища",
        status: "over",
      });
      return {
        ...base,
        kind: "store",
        store: "google_play",
        store_confidence: "high",
        source_locale: "русский, ru-RU",
        file_name: "metadata.zip",
        source: { kind: "zip", label: "архив" },
        fields: flds,
        clarifications: [
          {
            code: "over_limit_source",
            id: "ovl",
            resolved: false,
            field: "title",
          },
        ],
      };
    }

    // 12 — broken Steam BBCode
    case "broken_bbcode": {
      const flds = fieldsFor("steam");
      flds[1] = field("steam", "about", {
        value:
          "Пиратские корабли — [b]морской рогалик о капитане, что бросает вызов проклятию Мёртвой оболочки. Собирайте команду и покоряйте штормовые воды.",
        extra: {
          bbcode_error: {
            tag: "b",
            fragment: "рогалик о капитане, [b]что бросает вызов",
          },
        },
      });
      flds[1].status = "bbcode";
      return {
        ...base,
        kind: "store",
        store: "steam",
        store_confidence: "high",
        source_locale: "русский",
        file_name: "3 файла",
        source: { kind: "files", label: "3 текстовых файла" },
        fields: flds,
        clarifications: [
          { code: "broken_bbcode", id: "bb", resolved: false, field: "about" },
        ],
      };
    }

    // 13 — empty txt file (optional field → can continue without)
    case "empty_txt": {
      const flds = fieldsFor("steam");
      flds[2] = field("steam", "legal", { value: "   " });
      return {
        ...base,
        kind: "store",
        store: "steam",
        store_confidence: "high",
        source_locale: "русский",
        file_name: "3 файла",
        source: { kind: "files", label: "3 текстовых файла" },
        fields: flds,
        clarifications: [
          {
            code: "empty_file",
            id: "emp",
            resolved: false,
            field: "legal",
            required: false,
          },
        ],
      };
    }

    // 14 — unknown file inside a known set
    case "unknown_in_known":
      return {
        ...base,
        kind: "store",
        store: "google_play",
        store_confidence: "high",
        source_locale: "русский, ru-RU",
        file_name: "metadata.zip",
        source: { kind: "zip", label: "архив" },
        fields: fieldsFor("google_play"),
        clarifications: [
          {
            code: "unknown_in_known",
            id: "uik",
            resolved: false,
            store: "google_play",
            file: "promo_extra.txt",
            choice: null,
            extra: {
              label: "",
              limit: null,
              no_limit: false,
              limit_source: "",
              markup: "none",
            },
          },
        ],
      };

    // 15 — two files → one field
    case "duplicate_field": {
      const flds = fieldsFor("steam");
      return {
        ...base,
        kind: "store",
        store: "steam",
        store_confidence: "high",
        source_locale: "русский",
        file_name: "4 файла",
        source: { kind: "files", label: "4 текстовых файла" },
        fields: flds,
        clarifications: [
          {
            code: "duplicate_field",
            id: "dup",
            resolved: false,
            store: "steam",
            field: "about",
            field_label: "Об игре",
            choice: null,
            candidates: [
              {
                file: "steam_about.txt",
                path: "/",
                chars: 812,
                markup: "BBCode",
                modified: "2026-05-01",
                preview: [
                  "[b]Пиратские корабли[/b] — морской рогалик",
                  "Собирайте команду и покоряйте штормовые воды.",
                  "Каждый рейс уникален.",
                ],
              },
              {
                file: "about.txt",
                path: "/legacy/",
                chars: 640,
                markup: "BBCode",
                modified: "2026-03-14",
                preview: [
                  "Пиратский рогалик о капитане Мёртвой оболочки.",
                  "Старый черновик описания.",
                ],
              },
            ],
          },
        ],
      };
    }

    // 16 — archive unreadable / unsafe (hard stop)
    case "unreadable_zip":
      return {
        ...base,
        kind: null,
        file_name: "broken.zip",
        source: { kind: "zip", label: "архив" },
        stop: {
          code: "unreadable",
          title: "Не удалось безопасно прочитать архив",
          text: "Файлы не импортированы. Остальные данные проекта не изменились.",
          reason: "Архив повреждён",
        },
      };

    // 8 — archive already has fr + de translations (stage 2 focus)
    case "existing_translations":
      return {
        ...base,
        kind: "store",
        store: "steam",
        store_confidence: "high",
        source_locale: "русский",
        file_name: "metadata.zip",
        source: { kind: "zip", label: "архив" },
        fields: fieldsFor("steam"),
        languages_found: [
          { code: "ru", name: "Русский", source: "file", is_source: true },
          {
            code: "fr",
            name: "Французский",
            source: "file",
            has_translation: true,
          },
          {
            code: "de",
            name: "Немецкий",
            source: "file",
            has_translation: true,
          },
        ],
        _seed_languages: ["en", "de", "fr", "es", "pt_BR", "tr", "ja", "ko"],
        _seed_translated: ["fr", "de"],
      };

    // 9 — English chosen as source but incomplete
    case "source_incomplete":
      return {
        ...base,
        kind: "store",
        store: "steam",
        store_confidence: "high",
        source_locale: "русский",
        file_name: "metadata.zip",
        source: { kind: "zip", label: "архив" },
        fields: fieldsFor("steam"),
        source_language: "en",
        source_options: [
          {
            code: "ru",
            name: "Русский",
            have: 3,
            need: 3,
            complete: true,
            missing: [],
          },
          {
            code: "en",
            name: "Английский",
            have: 2,
            need: 3,
            complete: false,
            missing: ["Правовая информация"],
          },
        ],
      };

    // 10 — ambiguous regional locales (stage 2 focus)
    case "ambiguous_locales":
      return {
        ...base,
        kind: "store",
        store: "steam",
        store_confidence: "high",
        source_locale: "русский",
        file_name: "3 файла",
        source: { kind: "files", label: "3 текстовых файла" },
        fields: fieldsFor("steam"),
        _seed_languages: ["en", "de", "pt", "es", "zh"],
      };

    // 19 — glossary extraction from store texts (stage 3 focus)
    case "glossary_store":
      return {
        ...base,
        kind: "store",
        store: "steam",
        store_confidence: "high",
        source_locale: "русский",
        file_name: "3 файла",
        source: { kind: "files", label: "3 текстовых файла" },
        fields: fieldsFor("steam"),
        _seed_languages: ["en", "de", "fr", "ja"],
      };

    default:
      return build("kit_ok");
  }
}

// ---------------------------------------------------------------- clarification resolution
function isClarResolved(c) {
  switch (c.code) {
    case "unknown_files":
      return c.files.every(
        (f) => f.label && (f.no_limit || f.limit == null || f.limit_source),
      );
    case "ambiguous_content":
      return (
        !!c.choice && (c.choice !== "store_field" || (!!c.store && !!c.field))
      );
    case "two_stores":
      return !!c.choice;
    case "unknown_in_known":
      if (!c.choice) return false;
      if (c.choice === "additional")
        return (
          !!c.extra.label &&
          (c.extra.no_limit || c.extra.limit == null || c.extra.limit_source)
        );
      return true;
    case "duplicate_field":
      return !!c.choice;
    case "over_limit_source":
    case "broken_bbcode":
    case "empty_file":
      return !!c.resolved;
    default:
      return !!c.resolved;
  }
}

function recompute(a) {
  // field-level blockers
  const flds = a.fields || [];
  const fieldBlock = flds.some(
    (f) =>
      f.status === "over" ||
      f.status === "bbcode" ||
      (f.status === "empty" && f.required),
  );
  a.clarifications = (a.clarifications || []).map((c) => ({
    ...c,
    resolved: isClarResolved(c),
  }));
  const clarBlock = a.clarifications.some((c) => !c.resolved);
  a.stage1_ready =
    !a.stop && !fieldBlock && !clarBlock && a.kind && a.kind !== "ambiguous";
  return a;
}

// ---------------------------------------------------------------- draft store
export function createUniversalDraft(scenarioOrFile) {
  const scenario = SCENARIOS[scenarioOrFile]
    ? scenarioOrFile
    : inferScenario(scenarioOrFile);
  const drafts = load();
  const a = recompute(build(scenario));
  drafts[a.id] = a;
  save(drafts);
  return clone(a);
}

export function getUniversalDraft(id) {
  const a = load()[id];
  return a ? clone(a) : null;
}

export function patchUniversalDraft(id, patch) {
  const drafts = load();
  const a = drafts[id];
  if (!a) return null;

  if (patch.answers) a.answers = { ...a.answers, ...patch.answers };
  if (patch.source_language !== undefined) {
    a.source_language = patch.source_language;
    a.answers.source_language = patch.source_language;
  }

  // update one field (replace file / remove / edit source text)
  if (patch.field) {
    const { id: fid, remove, ...changes } = patch.field;
    if (remove) {
      a.fields = (a.fields || []).filter((f) => f.id !== fid);
    } else {
      a.fields = (a.fields || []).map((f) => {
        if (f.id !== fid) return f;
        const nf = { ...f, ...changes };
        if (changes.value !== undefined) {
          nf.chars = changes.value.length;
          nf.status = !changes.value.trim()
            ? "empty"
            : nf.limit > 0 && nf.chars > nf.limit
              ? "over"
              : "ok";
          nf.over_by = nf.status === "over" ? nf.chars - nf.limit : 0;
          if (nf.status !== "bbcode") delete nf.bbcode_error;
        }
        return nf;
      });
    }
  }

  // update one clarification
  if (patch.clarify) {
    const { id: cid, ...changes } = patch.clarify;
    a.clarifications = (a.clarifications || []).map((c) =>
      c.id === cid ? { ...c, ...changes } : c,
    );
    // resolving an ambiguous_content into a store field materialises the store fields
    const amb = a.clarifications.find(
      (c) => c.id === cid && c.code === "ambiguous_content",
    );
    if (amb) {
      if (amb.choice === "table") {
        a.kind = "loc-kit";
        a.file_name = amb.file;
        Object.assign(a, tableFromKit());
      } else if (amb.choice === "store_field" && amb.store && amb.field) {
        a.kind = "store";
        a.store = amb.store;
        a.store_confidence = "high";
        a.source_locale = registry[amb.store].source_locale;
        a.fields = fieldsFor(amb.store, [amb.field]);
      } else if (amb.choice === "other") {
        a.kind = "store";
        a.store = "custom";
        a.fields = [
          field("custom", amb.file.replace(/\.txt$/, ""), {
            label: amb.file,
            limit: 0,
          }),
        ];
      }
    }
    // resolving two_stores → materialise the primary set (first store) as fields
    const two = a.clarifications.find(
      (c) => c.id === cid && c.code === "two_stores",
    );
    if (two?.choice) {
      a.store = two.stores[0].store;
      a.store_confidence = "high";
      a.source_locale = registry[a.store].source_locale;
      a.fields = fieldsFor(a.store);
      a.split_sets = two.stores.map((s) => s.store);
    }
    // resolving unknown_in_known → drop the extra file (skip) or keep as additional field
    const uik = a.clarifications.find(
      (c) => c.id === cid && c.code === "unknown_in_known",
    );
    if (uik && uik.choice === "additional" && uik.extra.label) {
      a.fields = [
        ...(a.fields || []),
        field(a.store, uik.file.replace(/\.txt$/, ""), {
          label: uik.extra.label,
          limit: uik.extra.no_limit ? 0 : uik.extra.limit || 0,
        }),
      ];
    }
  }

  recompute(a);
  drafts[id] = a;
  save(drafts);
  return clone(a);
}

function tableFromKit() {
  return {
    format: { kind: "CSV", encoding: "UTF-8" },
    rows: { total: 1512, importable: 1512, quarantine: 0, by_reason: {} },
    columns: [{ header: "key", role: "Ключ" }],
    lang_count: 2,
    languages_found: [
      { code: "ru", name: "Русский", fill: 100, source: "file" },
      { code: "en", name: "Английский", fill: 100, source: "file" },
    ],
    store: null,
    fields: null,
  };
}

// ---------------------------------------------------------------- scenario registry / inference
export const SCENARIOS = {
  kit_ok: { title: "Лок-кит: успешное распознавание", stage: 1 },
  steam_txt: { title: "Steam: три TXT-файла", stage: 1 },
  gplay_zip: { title: "Google Play: архив", stage: 1 },
  appstore_folder: { title: "App Store: папка", stage: 1 },
  custom_unknown: { title: "Другая площадка: неизвестные TXT", stage: 1 },
  ambiguous_one: { title: "Один неоднозначный description.txt", stage: 1 },
  two_stores: { title: "Steam + Google Play в одной загрузке", stage: 1 },
  existing_translations: { title: "В архиве уже есть fr и de", stage: 2 },
  source_incomplete: { title: "Исходный комплект неполный (en)", stage: 2 },
  ambiguous_locales: { title: "Неоднозначные региональные локали", stage: 2 },
  over_limit_source: {
    title: "Исходный текст превышает лимит стора",
    stage: 1,
  },
  broken_bbcode: { title: "Сломанная Steam BBCode-разметка", stage: 1 },
  empty_txt: { title: "Пустой TXT-файл", stage: 1 },
  unknown_in_known: { title: "Неизвестный файл в известном наборе", stage: 1 },
  duplicate_field: { title: "Два файла назначены одному полю", stage: 1 },
  unreadable_zip: { title: "Архив не читается или небезопасен", stage: 1 },
  store_run_done: { title: "Перевод стор-текстов завершён", stage: "run" },
  target_over_limit: { title: "Target превышает лимит → пусто", stage: "run" },
  glossary_store: { title: "Извлечение терминов из стор-текстов", stage: 3 },
};

function inferScenario(fileName = "") {
  const f = String(fileName).toLowerCase();
  if (f.endsWith(".zip")) {
    if (f.includes("broken") || f.includes("corrupt")) return "unreadable_zip";
    if (f.includes("appstore") || f.includes("app-store"))
      return "appstore_folder";
    return "gplay_zip";
  }
  if (f.includes("steam")) return "steam_txt";
  if (f.includes("gplay") || f.includes("google")) return "gplay_zip";
  if (f.includes("appstore") || f.includes("app-store"))
    return "appstore_folder";
  if (/\.(xlsx|csv|tsv)$/.test(f) || f.includes("kit") || f.includes("pirate"))
    return "kit_ok";
  if (f.includes("description")) return "ambiguous_one";
  return "kit_ok";
}
