import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type AxeResults, run as axe } from "axe-core";
import { RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { ProducerApi, ProducerUploadAnalysis } from "../../api/client";
import { createConsoleMemoryRouter } from "../../router";
import { ProfileIntegration } from "./ProfileIntegration";
import { LanguageSelection } from "./LanguageSelection";

it("explains glossary availability for fallback sources inside closed details", async () => {
  const ready = {...structuredClone(draft), analysis:{...draft.analysis, confirmation:{
    totals:{input_rows:2,imported_rows:2,quarantined_rows:0},components:[],quarantine_reasons:{},
    fallback_counts:{ru:1}, fallback_rows:[{file:"kit.xlsx",sheet:"Units",row:3,language:"ru"}],
  }}, answers:{...draft.answers,selected_sheets:[{file:"kit.xlsx",sheet:"Units"}],source_language:"en",included_target_languages:["pl"]}};
  renderWizard(apiMock({getUpload:vi.fn().mockResolvedValue(ready)}), `/projects/pirate-ships/localize?u=${draft.token}&step=4`);
  const summary = await screen.findByText(/Автоматически выбран запасной/);
  await userEvent.setup().click(summary);
  expect(screen.getByText(/ru → pl: подходящий глоссарий не найден/)).toBeVisible();
});

it("declared_source_without_column_uses_confirmed_fallback", async () => {
  const change = vi.fn();
  render(<LanguageSelection source="en" targets={["pl"]} kitCodes={["en", "ru"]} remoteCodes={[]} catalog={[{code:"en",name:"English"},{code:"ru",name:"Russian"},{code:"de",name:"German"},{code:"pl",name:"Polish"}]} busy={false} onSourceChange={change} onTargetsChange={vi.fn()} />);
  const user = userEvent.setup();
  await user.selectOptions(screen.getByRole("combobox"), "de");
  expect(change).toHaveBeenCalledWith("de");
  expect(screen.getByText(/При отсутствии текста/)).toBeVisible();
});

it("keeps structure closed and toggles it with keyboard", async () => {
  const ready = {...structuredClone(draft), answers:{...draft.answers,selected_sheets:[{file:"kit.xlsx",sheet:"Units"}],source_language:"en",included_target_languages:["ru"]}};
  renderWizard(apiMock({getUpload:vi.fn().mockResolvedValue(ready)}), `/projects/pirate-ships/localize?u=${draft.token}&step=2`);
  const button = await screen.findByRole("button", {name:/Структура колонок/});
  expect(button).toHaveAttribute("aria-expanded", "false");
  const user = userEvent.setup();
  button.focus();
  await user.keyboard(" ");
  expect(button).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByLabelText("Units Id")).toBeVisible();
  await user.keyboard("{Enter}");
  expect(button).toHaveAttribute("aria-expanded", "false");
});

it("shows locale rule changes and their BDHC provenance before confirmation", async () => {
  const user = userEvent.setup();
  const api = apiMock({
    searchBdhc: vi.fn().mockResolvedValue({ results: [{ name: "Dead Shell", ref: "dead-shell" }] }),
    previewBdhc: vi.fn().mockResolvedValue({
      preview_id: "preview", diff: [], warnings: [],
      locale_rules_diff: [{ language: "ja", before: { locale_notes: "Old Japanese" }, after: {
        locale_notes: "Polite Japanese", tone: "Fairytale atmosphere", register: "informal",
        player_address_note: null, has_character_voices: true,
        forbidden_rules: [{ rule: "Error messages", applies_to: ["humor"], source: "producer_form" }],
        voices: [{ name: "Blu", register: "formal", voice_id: 68,
          example_lines: ["You will find <f>death</f>."] }],
        future_rule: "Keep this new rule visible",
      } }],
      provenance: { title_id: "dead-shell-id", schema_version: "12.7", context_hash: "confirmed-hash" },
    }),
    applyBdhc: vi.fn(),
  });
  render(<ProfileIntegration api={api} slug="local" projectName="Dead Shell" source="ru" targets={["ja"]} onApplied={vi.fn()} profile={{
    revision: "revision", answers: {}, summary: "", missing_required: [], profile_state: "missing", engine: "openrouter", judge_context_changed: false,
  }} />);
  await user.type(screen.getByLabelText("Название игры или ссылка"), "Dead Shell");
  await user.click(screen.getByRole("button", { name: "Найти карточку" }));
  await user.selectOptions(await screen.findByRole("combobox"), "dead-shell");
  await user.click(screen.getByRole("button", { name: "Показать предлагаемые изменения" }));
  expect(await screen.findByText(/Polite Japanese/)).toBeVisible();
  expect(screen.getByText(/Old Japanese/)).toBeVisible();
  expect(screen.getByText("Тон и атмосфера")).toBeVisible();
  expect(screen.getByText("Fairytale atmosphere")).toBeVisible();
  expect(screen.getByText("Голоса персонажей")).toBeVisible();
  expect(screen.getByText("Blu")).toBeVisible();
  expect(screen.getByText("You will find <f>death</f>.")).not.toBeVisible();
  await user.click(screen.getByText("Blu"));
  expect(screen.getByText("You will find <f>death</f>.")).toBeVisible();
  expect(screen.getByText("Keep this new rule visible")).toBeVisible();
  expect(screen.getByText("Есть голоса персонажей").nextElementSibling).toHaveTextContent("Да");
  expect(document.querySelector("pre")).toBeNull();
  expect(document.querySelector("f")).toBeNull();
  const provenance = screen.getByText("Служебные сведения карточки").closest("details");
  expect(provenance).not.toHaveAttribute("open");
  await user.click(screen.getByText("Служебные сведения карточки"));
  expect(screen.getByText(/confirmed-hash/)).toBeVisible();
  expect(api.applyBdhc).not.toHaveBeenCalled();
});

const draft: ProducerUploadAnalysis = {
  token: "11111111-1111-1111-1111-111111111111",
  revision: "a".repeat(64),
  kind: "string",
  ui_mode: "quick",
  current_step: "select-content",
  answers: {
    selected_files: [],
    selected_sheets: [],
    source_language: null,
  },
  analysis: {
    detected_kind: "ambiguous",
    loc_kit: {
      files: [
        {
          path: "kit.xlsx",
          format: "xlsx",
          selected: false,
          sheets: [
            {
              name: "Units",
              classification: "loc_kit_candidate",
              selected: false,
              rows: 20,
              columns: ["Key", "English(en)", "Russian(ru)", "Length", "Id"],
              languages: [],
              source_language: null,
              source_candidates: ["en", "ru"],
              unresolved_columns: [
                {
                  column: 5,
                  header: "Id",
                  candidates: ["service", "language:id"],
                },
              ],
              ignored_columns: [],
              formula_cells: [
                {
                  file: "kit.xlsx",
                  sheet: "Units",
                  cell: "D2",
                  column: 4,
                  formula: "=LEN(B2)", auto_ignore: true,
                },
                {
                  file: "kit.xlsx",
                  sheet: "Units",
                  cell: "D3",
                  column: 4,
                  formula: "=LEN(B3)", auto_ignore: true,
                },
              ],
            },
            {
              name: "Default (Old)",
              classification: "archive_candidate",
              selected: false,
              rows: 4,
              columns: ["Key", "English(en)"],
              languages: [],
              source_language: null,
              source_candidates: ["en"],
              unresolved_columns: [],
              ignored_columns: [],
              formula_cells: [],
            },
            {
              name: "Store",
              classification: "store_candidate",
              selected: false,
              rows: 3,
              columns: ["Title", "Description"],
              languages: [],
              source_language: null,
              source_candidates: [],
              unresolved_columns: [],
              ignored_columns: [],
              formula_cells: [],
            },
          ],
        },
      ],
      collisions: [{
        key: "duplicate.key",
        occurrences: [
          { file: "kit.xlsx", sheet: "Units", row: 2 },
          { file: "kit.xlsx", sheet: "Default (Old)", row: 2 },
        ],
      }],
    },
    formula_cells: [
      {
        file: "kit.xlsx",
        sheet: "Units",
        cell: "D2",
        column: 4,
        formula: "=LEN(B2)", auto_ignore: true,
      },
      {
        file: "kit.xlsx",
        sheet: "Units",
        cell: "D3",
        column: 4,
        formula: "=LEN(B3)", auto_ignore: true,
      },
    ],
  },
  attention: [],
  errors: [],
};

it("explains problematic sheets beside editable checkboxes and saves their exclusion", async () => {
  const user = userEvent.setup();
  const file = draft.analysis.loc_kit!.files[0];
  const sheets = [file.sheets[0], ...["Questions_Answers", "General"].map(name => ({
    ...structuredClone(file.sheets[0]), name, classification: "ambiguous" as const,
    source_candidates: [], unresolved_columns: [], formula_cells: [],
    issues: name === "Questions_Answers" ? [{ code: "loc_kit.no_language_headers",
      scope: "sheet", detail: "No recognized language headers were found." }] : undefined,
  }))];
  const problemDraft: ProducerUploadAnalysis = { ...structuredClone(draft),
    analysis: { ...draft.analysis, loc_kit: { ...draft.analysis.loc_kit!, files: [{ ...file, sheets }] } },
    answers: { ...draft.answers,
      selected_files: ["kit.xlsx"],
      selected_sheets: sheets.map(sheet => ({ file: "kit.xlsx", sheet: sheet.name })),
    },
  };
  const api = apiMock({ getUpload: vi.fn().mockResolvedValue(problemDraft) });
  renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}`);
  for (const name of ["Questions_Answers", "General"]) {
    const checkbox = await screen.findByRole("checkbox", { name: new RegExp(name) });
    expect(checkbox).toBeEnabled();
    expect(checkbox).toBeChecked();
    expect(checkbox).toHaveAccessibleDescription(/Снимите галочку/);
    const row = screen.getByText(name).closest("label")!;
    const reason = name === "General" ? /распознать структуру/ : /распознанными кодами языков/;
    expect(within(row).getByText(reason)).toBeVisible();
    await user.click(checkbox);
    expect(checkbox).not.toBeChecked();
    expect(within(row).getByText(reason)).toBeVisible();
  }
  await user.click(screen.getByRole("button", { name: "Сохранить выбор" }));
  await waitFor(() => expect(api.patchUpload).toHaveBeenCalled());
  const body = vi.mocked(api.patchUpload).mock.calls.at(-1)![1];
  expect(body.answers?.selected_sheets).toEqual([{ file: "kit.xlsx", sheet: "Units" }]);
  expect(api.startLocalization).not.toHaveBeenCalled();
});

function apiMock(overrides: Partial<ProducerApi> = {}): ProducerApi {
  const notFound = Object.assign(new Error("Данные не найдены."), { status: 404 });
  let current = structuredClone(draft);
  const profile = {
    revision: "p".repeat(64),
    answers: {},
    summary: "",
    missing_required: ["register_ui", "register_dialogue", "profanity"],
    profile_state: "missing" as const,
    engine: "openrouter",
    judge_context_changed: false,
  };
  return {
    getLanguages: vi.fn().mockResolvedValue(["ru", "en", "fr", "it", "de", "zh_Hans", "ko", "ja", "pt_BR", "es_MX", "pl", "cs", "nl"].map(code => ({code, name: code}))),
    getActiveUpload: vi.fn().mockRejectedValue(notFound),
    getUpload: vi.fn().mockImplementation(async () => current),
    createUpload: vi.fn().mockImplementation(async () => current),
    patchUpload: vi.fn().mockImplementation(async (_token, body) => {
      current = {
        ...current,
        ui_mode: body.ui_mode,
        current_step: body.current_step,
        revision: "b".repeat(64),
        answers: body.answers ?? current.answers,
      };
      return current;
    }),
    getProfile: vi.fn().mockResolvedValue(profile),
    getProject: vi.fn().mockResolvedValue({ name: "Pirate Ships", slug: "pirate-ships" }),
    patchProfile: vi.fn().mockImplementation(async (_slug, _revision, answers) => ({
      ...profile,
      answers,
      profile_state: "configured" as const,
      missing_required: [],
    })),
    startLocalization: vi.fn(),
    ...overrides,
  } as unknown as ProducerApi;
}

function renderWizard(api: ProducerApi, entry = "/projects/pirate-ships/localize") {
  const router = createConsoleMemoryRouter(api, [entry]);
  return { router, ...render(<RouterProvider router={router} />) };
}

it("automatically restores the saved kit when the temporary draft expired", async () => {
  const restored = {...draft, answers:{...draft.answers, selected_files:["kit.xlsx"],selected_sheets:[{file:"kit.xlsx",sheet:"Units"}],source_language:"ru",included_target_languages:["en"]}};
  const api = apiMock({restoreUpload:vi.fn().mockResolvedValue(restored),patchUpload:vi.fn().mockResolvedValue({...restored,current_step:"map-columns"})});
  const {router}=renderWizard(api);
  expect(await screen.findByText("Units")).toBeVisible();
  expect(screen.getByRole("checkbox",{name:/Units/})).toBeChecked();
  expect(api.restoreUpload).toHaveBeenCalledWith("pirate-ships",expect.anything());
  expect(screen.getByText("Используется загруженный лок-кит. При необходимости можно загрузить новую версию кнопкой ниже.")).toBeVisible();
  expect(screen.getByRole("button",{name:"Загрузить новый лок-кит"})).toBeEnabled();
  expect(new URLSearchParams(router.state.location.search).get("u")).toBe(draft.token);
  await userEvent.click(screen.getByRole("button",{name:/2.*Языки/}));
  expect(screen.getByRole("combobox",{name:"Язык, с которого переводить"})).toHaveValue("ru");
  expect(api.startLocalization).not.toHaveBeenCalled();
});

it("uploads a replacement kit as a separate draft and clears the previous glossary selection", async () => {
  const user = userEvent.setup();
  const fileBody = structuredClone(draft.analysis.loc_kit!.files[0]);
  const replacement: ProducerUploadAnalysis = {
    ...structuredClone(draft), token: "22222222-2222-2222-2222-222222222222",
    analysis: { ...draft.analysis, loc_kit: { ...draft.analysis.loc_kit!, files: [{
      ...fileBody, sheets: [{ ...fileBody.sheets[0], name: "New units" }, ...fileBody.sheets.slice(1)],
    }] } },
  };
  const glossary = { ...structuredClone(draft), token: "old-glossary", kind: "glossary" as const };
  const api = apiMock({
    getUpload: vi.fn().mockImplementation(async token => token === glossary.token ? glossary : draft),
    createUpload: vi.fn().mockResolvedValue(replacement),
  });
  const { router } = renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}&g=${glossary.token}&step=1`);
  await screen.findByText("Units");
  expect(screen.getByRole("button", { name: "Загрузить новый лок-кит" })).toBeEnabled();
  const file = new File(["new"], "updated.xlsx");
  await user.upload(screen.getByLabelText("Новая версия лок-кита"), file);
  expect(await screen.findByText("New units")).toBeVisible();
  expect(api.createUpload).toHaveBeenCalledWith("pirate-ships", [file], "quick");
  const query = new URLSearchParams(router.state.location.search);
  expect(query.get("u")).toBe(replacement.token);
  expect(query.get("g")).toBeNull();
  expect(query.get("step")).toBe("1");
  expect(api.getUpload).not.toHaveBeenCalledWith(replacement.token, expect.anything());
  expect(api.patchUpload).not.toHaveBeenCalled();
  expect(api.startLocalization).not.toHaveBeenCalled();
});

it("keeps the old kit and URL if replacement analysis fails and allows retry", async () => {
  const user = userEvent.setup();
  const api = apiMock({ createUpload: vi.fn().mockRejectedValue(new Error("bad workbook")) });
  const { router } = renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}&g=old-glossary&step=1`);
  await screen.findByText("Units");
  const before = router.state.location.search;
  await user.upload(screen.getByLabelText("Новая версия лок-кита"), new File(["broken"], "bad.xlsx"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось проанализировать файлы");
  expect(screen.getByText("Units")).toBeVisible();
  expect(router.state.location.search).toBe(before);
  expect(screen.getByRole("button", { name: "Загрузить новый лок-кит" })).toBeEnabled();
  expect(api.patchUpload).not.toHaveBeenCalled();
  expect(api.startLocalization).not.toHaveBeenCalled();
  await user.upload(screen.getByLabelText("Новая версия лок-кита"), new File(["broken"], "bad.xlsx"));
  await waitFor(() => expect(api.createUpload).toHaveBeenCalledTimes(2));
});

it.each([
  {glossary:true,units:[{key:"term",source:"Термин",targets:{}}],notice:true},
  {glossary:true,units:[],notice:false},
  {glossary:false,units:[{key:"unit",source:"Строка",targets:{}}],notice:false},
])("shows existing glossary guidance above upload only for populated glossaries: $notice",async({glossary,units,notice})=>{
  const api=apiMock({getConnection:vi.fn().mockResolvedValue({read_only:true,component_map:{},snapshot:{languages:[],components:{terms:{glossary,units}}}})});
  renderWizard(api,`/projects/pirate-ships/localize?u=${draft.token}&step=3`);
  const file=await screen.findByLabelText("Файл глоссария");
  await waitFor(()=>expect(api.getConnection).toHaveBeenCalled());
  const message="Глоссарий уже есть в Weblate. Повторно загружать его для добавления языка не нужно. Загружайте файл только для добавления новых терминов.";
  if(notice) {
    const note=await screen.findByText(message);
    expect(note).toBeVisible();
    expect(note).toHaveAttribute("role","status");
    expect(note.compareDocumentPosition(file) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  } else {
    expect(screen.queryByText(message)).not.toBeInTheDocument();
  }
  expect(file).toBeEnabled();
});

it("autofills and saves the game form without the old context buttons", async()=>{
  const instructions = {persona:"Роль по карточке",style:"Стиль игры",language_instructions:{en:"English"}};
  const saved = {revision:"saved",answers:{genre:"RPG",register_ui:"clear",register_dialogue:"natural",profanity:"preserve"},generated:instructions,summary:"",missing_required:[],profile_state:"configured" as const,engine:"openrouter",judge_context_changed:false};
  const ready = {...structuredClone(draft),answers:{...draft.answers,source_language:"ru",included_target_languages:["en"]}};
  const api = apiMock({getUpload:vi.fn().mockResolvedValue(ready),getProfile:vi.fn().mockResolvedValue(saved),
    resolveContext:vi.fn().mockResolvedValue({state:"matched",snapshot_id:"snapshot",title:"Pirate Ships",answers:saved.answers,matches:[],warnings:[],locale_rules:{}}),
    prepareProfile:vi.fn().mockResolvedValue({job_id:"job",input_key:"key",state:"ready",result:instructions,auto_saved:true,profile_revision:"saved",warnings:[]}),commitProfile:vi.fn()});
  renderWizard(api,`/projects/pirate-ships/localize?u=${draft.token}&step=3`);
  await screen.findByText("Инструкции сохранены автоматически");
  expect(screen.queryByLabelText("Роль игрока")).not.toBeInTheDocument();
  expect(screen.queryByRole("button",{name:"Сохранить контекст"})).not.toBeInTheDocument();
  expect(screen.getByLabelText("Жанр")).toHaveValue("RPG");
  expect(screen.getByRole("button",{name:"Сохранить инструкции",hidden:true})).toBeDisabled();
  await userEvent.click(screen.getByRole("button",{name:/4.*Проверка и запуск/}));
  expect(await screen.findByRole("heading",{name:"Проверка и запуск"})).toBeVisible();
  expect(api.commitProfile).not.toHaveBeenCalled();
});

it("loads named language cards for selected sheets and saves canonical new targets on reopening", async () => {
  const user = userEvent.setup();
  const ready = {...structuredClone(draft), answers: {...draft.answers, selected_sheets: [{file:"kit.xlsx",sheet:"Units"}], source_language:"ru", included_target_languages:["en"], column_roles:[{file:"kit.xlsx",sheet:"Default (Old)",column:2,role:"language" as const,language:"ja"}]}};
  const api = apiMock({getUpload: vi.fn().mockResolvedValue(ready)});
  renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}&step=2`);
  const source = await screen.findByRole("combobox", {name:"Язык, с которого переводить"});
  expect(within(source).getByRole("option",{name:"Русский (ru)"})).toBeVisible();
  const kit = screen.getByRole("group",{name:"Языки, уже присутствующие в лок-ките"});
  expect(within(kit).queryByText(/Японский/)).toBeNull();
  await user.click(screen.getByRole("checkbox",{name:/Польский \(pl\)/}));
  await user.type(screen.getByRole("textbox",{name:"Добавить свой язык"}), "испанский мексиканский{Enter}");
  await user.click(screen.getByRole("button",{name:"Сохранить языки"}));
  expect(api.patchUpload).toHaveBeenLastCalledWith(draft.token, expect.objectContaining({answers:expect.objectContaining({source_language:"ru",included_target_languages:["en","pl","es_MX"]})}));
  await user.click(screen.getByRole("button",{name:/2.*Языки/}));
  expect(screen.getByRole("checkbox",{name:/\(es_MX\)/})).toBeChecked();
});

describe("LocKitWizard", () => {
  it.each([
    [Object.assign(new Error("Update instructions for the selected languages"), { status: 400, code: "profile_languages" }), "Языки сохранённых инструкций не совпадают с выбранными. Вернитесь на шаг «Контекст и термины» и нажмите «Сохранить инструкции»."],
    [new Error("Network unavailable"), "Не удалось запустить локализацию. Проверьте условия запуска и повторите попытку."],
  ])("shows actionable API launch failures and retains a fallback for network errors", async (failure, message) => {
    const user = userEvent.setup();
    const ready = { ...draft, current_step: "confirm", analysis: { ...draft.analysis, confirmation: {
      totals: { input_rows: 20, imported_rows: 20, quarantined_rows: 0, skipped_rows: 0 }, components: [], quarantine_reasons: {},
    } } } satisfies ProducerUploadAnalysis;
    const api = apiMock({ getUpload: vi.fn().mockResolvedValue(ready), startLocalization: vi.fn().mockRejectedValue(failure) });
    renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}&step=4`);
    await user.click(await screen.findByRole("button", { name: "Запустить локализацию" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(api.startLocalization).toHaveBeenCalledOnce();
    expect(screen.getByRole("alert").parentElement).toHaveFocus();
    expect(screen.getByRole("button", { name: "Запустить локализацию" })).toBeEnabled();
  });

  it("supports keyboard file selection and shows every classified sheet", async () => {
    const user = userEvent.setup();
    const api = apiMock();
    renderWizard(api);
    const input = await screen.findByLabelText("Файлы лок-кита");
    expect(screen.queryByRole("button", { name: "Быстрый" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подробный" })).not.toBeInTheDocument();
    const file = new File(["fake"], "kit.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });

    await user.upload(input, file);

    expect(api.createUpload).toHaveBeenCalledWith(
      "pirate-ships",
      [file],
      "quick",
    );
    expect(await screen.findByText("Units")).toBeVisible();
    expect(screen.getByText("Default (Old)")).toBeVisible();
    expect(screen.getByText("Store")).toBeVisible();
    expect(screen.getByRole("checkbox", { name: /Store/ })).toBeDisabled();
    expect(screen.queryByText(/D2/)).not.toBeInTheDocument();
  });

  it("does not reload a newly uploaded draft through its new URL", async () => {
    const user = userEvent.setup();
    const api = apiMock();
    renderWizard(api);
    const input = await screen.findByLabelText("Файлы лок-кита");
    expect(screen.queryByRole("button", { name: "Быстрый" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подробный" })).not.toBeInTheDocument();
    const file = new File(["fake"], "kit.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });

    await user.upload(input, file);
    await screen.findByText("Units");

    await waitFor(() => expect(api.createUpload).toHaveBeenCalledOnce());
    expect(api.getUpload).not.toHaveBeenCalled();
  });

  it("restores detailed drafts without showing mode switches", async () => {
    const api = apiMock({ getUpload: vi.fn().mockResolvedValue({ ...draft, ui_mode: "detailed" }) });
    renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}`);
    await screen.findByText("Units");
    expect(screen.getByRole("button", { name: /8.*Проверка и запуск/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Быстрый" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Подробный" })).not.toBeInTheDocument();
    expect(api.patchUpload).not.toHaveBeenCalled();
  });
  it("shows the selected step before navigation persistence resolves", async () => {
    const user = userEvent.setup();
    let resolvePatch: (value: ProducerUploadAnalysis) => void = () => undefined;
    const pendingPatch = new Promise<ProducerUploadAnalysis>((resolve) => {
      resolvePatch = resolve;
    });
    const api = apiMock({
      patchUpload: vi.fn().mockReturnValue(pendingPatch),
    });
    renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}`);
    await screen.findByText("Units");

    await user.click(screen.getByRole("button", { name: /2.*Языки/ }));

    expect(screen.getByRole("button", { name: /2.*Языки/ })).toHaveAttribute("aria-current", "step");

    resolvePatch({ ...draft, current_step: "map-columns" });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /2.*Языки/ })).toBeEnabled(),
    );
  });

  it("reloads stale server state and moves focus to the announced error", async () => {
    const user = userEvent.setup();
    const stale = Object.assign(new Error("Reload"), {
      status: 409,
      code: "stale-revision",
    });
    const api = apiMock({ patchUpload: vi.fn().mockRejectedValue(stale) });
    renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}`);
    await screen.findByText("Units");

    await user.click(screen.getByRole("button", { name: /2.*Языки/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Черновик изменился",
    );
    expect(api.getUpload).toHaveBeenCalledTimes(2);
    await waitFor(() =>
      expect(screen.getByRole("alert").parentElement).toHaveFocus(),
    );
  });

  it("announces an expired deep-linked draft", async () => {
    const expired = Object.assign(new Error("Gone"), { status: 404 });
    renderWizard(
      apiMock({ getUpload: vi.fn().mockRejectedValue(expired) }),
      `/projects/pirate-ships/localize?u=${draft.token}`,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Срок действия черновика истёк");
    expect(screen.getByRole("alert").parentElement).toHaveFocus();
  });

  it("renders exact gate blockers and keeps start disabled", async () => {
    const user = userEvent.setup();
    const blocked = {
      ...draft,
      current_step: "confirm",
      attention: [{
        code: "store.unsupported_in_this_slice",
        scope: "upload",
        detail: "Store candidates require the later store-package workflow.",
      }],
      errors: [{
        code: "loc_kit.source_required",
        scope: "upload",
        detail: "Choose the source language before confirmation.",
      }],
      analysis: {
        ...draft.analysis,
        confirmation: {
          totals: {
            input_rows: 20,
            imported_rows: 18,
            quarantined_rows: 2,
            skipped_rows: 0,
          },
          components: [],
          quarantine_reasons: { missing_source: 2 },
        },
      },
    } satisfies ProducerUploadAnalysis;
    const quarantine = {
      count: 1,
      next: null,
      previous: null,
      results: [{
        file: "kit.xlsx",
        sheet: "Units",
        row: 2,
        coordinate: "Units!2",
        status: "quarantined" as const,
        reason_code: "missing_source",
        reason: "The selected source cell is empty.",
        original_cells: ["unit.one", ""],
        normalized: null,
      }],
    };
    renderWizard(
      apiMock({
        getUpload: vi.fn().mockResolvedValue(blocked),
        getUploadQuarantine: vi.fn().mockResolvedValue(quarantine),
      }),
      `/projects/pirate-ships/localize?u=${draft.token}&step=4`,
    );

    expect(await screen.findByText("Choose the source language before confirmation.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Запустить локализацию" })).toBeDisabled();
    expect(screen.getByText("18")).toBeVisible();
    expect(screen.queryByText("Пропуски исходного текста")).not.toBeInTheDocument();
    expect(screen.getByText("Store candidates require the later store-package workflow.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Посмотреть строки, требующие проверки" }));
    expect(await screen.findByText("Units!2")).toBeVisible();
  });

  it("hides formula attention when every selected formula column has a role", async () => {
    const blocked = {
      ...draft,
      current_step: "confirm",
      answers: {
        ...draft.answers,
        selected_files: ["kit.xlsx"],
        selected_sheets: [{ file: "kit.xlsx", sheet: "Units" }],
        source_language: null,
        column_roles: [{
          file: "kit.xlsx",
          sheet: "Units",
          column: 4,
          role: "ignored" as const,
        }],
      },
      attention: [{
        code: "loc_kit.formulas_require_roles",
        scope: "upload",
        detail: "Formula columns require explicit roles.",
      }],
      errors: [{
        code: "loc_kit.source_required",
        scope: "upload",
        detail: "Choose the source language before confirmation.",
      }],
    } satisfies ProducerUploadAnalysis;
    renderWizard(
      apiMock({ getUpload: vi.fn().mockResolvedValue(blocked) }),
      `/projects/pirate-ships/localize?u=${draft.token}&step=4`,
    );

    expect(await screen.findByText("Choose the source language before confirmation.")).toBeVisible();
    expect(screen.queryByText("Formula columns require explicit roles.")).not.toBeInTheDocument();
  });

  it("persists explicit sheet, source, target, and ambiguous-column roles", async () => {
    const user = userEvent.setup();
    const api = apiMock();
    renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}`);

    await user.click(await screen.findByRole("checkbox", { name: /Units/ }));
    await user.click(screen.getByRole("button", { name: "Сохранить выбор" }));
    const selectionAnswers = vi.mocked(api.patchUpload).mock.calls.at(-1)?.[1].answers;
    expect(selectionAnswers?.column_roles.filter((role) => role.column === 4)).toHaveLength(1);
    await user.selectOptions(screen.getByLabelText("Язык, с которого переводить"), "en");
    await user.click(screen.getByRole("checkbox", { name: "Русский (ru)" }));
    await user.click(screen.getByRole("button", { name: "Сохранить языки" }));
    await user.click(screen.getByRole("button", { name: /2.*Языки/ }));

    await user.click(screen.getByRole("button", {name:/Структура колонок/}));
    await user.selectOptions(screen.getByLabelText("Units Id"), "reference");
    await user.click(screen.getByRole("button", { name: "Сохранить структуру" }));

    expect(api.patchUpload).toHaveBeenLastCalledWith(
      draft.token,
      expect.objectContaining({
        answers: expect.objectContaining({
          selected_sheets: [{ file: "kit.xlsx", sheet: "Units" }],
          source_language: "en",
          included_target_languages: ["ru"],
          column_roles: expect.arrayContaining([
            expect.objectContaining({ column: 4, role: "ignored" }),
            expect.objectContaining({ column: 5, role: "reference" }),
          ]),
        }),
      }),
    );

    await user.click(screen.getByRole("button", { name: /1.*Файлы/ }));
    await user.click(screen.getByRole("checkbox", { name: /Default \(Old\)/ }));
    await user.click(screen.getByRole("button", { name: "Сохранить выбор" }));
    expect(api.patchUpload).toHaveBeenLastCalledWith(
      draft.token,
      expect.objectContaining({
        answers: expect.objectContaining({
          column_roles: expect.arrayContaining([
            expect.objectContaining({ column: 5, role: "reference" }),
          ]),
        }),
      }),
    );
  });

  it("follows browser history between wizard steps", async () => {
    const api = apiMock();
    const { router } = renderWizard(
      api,
      `/projects/pirate-ships/localize?u=${draft.token}&step=4`,
    );
    expect(await screen.findByRole("heading", { name: "Проверка и запуск" })).toBeVisible();

    expect(screen.getByRole("button", { name: /4.*Проверка и запуск/ })).toHaveAttribute("aria-current", "step");
    await act(async () => router.navigate(`/projects/pirate-ships/localize?u=${draft.token}&step=2`));
    expect(await screen.findByRole("heading", { name: "Языки локализации" })).toBeVisible();
    await act(async () => router.navigate(`/projects/pirate-ships/localize?u=${draft.token}&step=3`));
    expect(await screen.findByRole("heading", { name: "Контекст игры" })).toBeVisible();
    await act(async () => router.navigate(-1));
    expect(await screen.findByRole("heading", { name: "Языки локализации" })).toBeVisible();
  });

  it("has no serious automated accessibility violations", async () => {
    const { container } = renderWizard(
      apiMock(),
      `/projects/pirate-ships/localize?u=${draft.token}`,
    );
    await screen.findByText("Units");

    const results: AxeResults = await axe(container);
    expect(
      results.violations.filter(({ impact }) =>
        ["serious", "critical"].includes(impact ?? ""),
      ),
    ).toEqual([]);
  });

  it("saves explicit project context and prepares an optional glossary", async () => {
    const user = userEvent.setup();
    const glossary = {
      ...draft,
      token: "22222222-2222-2222-2222-222222222222",
      kind: "glossary" as const,
      revision: "g".repeat(64),
    };
    const api = apiMock({ createUpload: vi.fn().mockResolvedValue(glossary) });
    renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}`);
    await screen.findByText("Units");

    await user.click(screen.getByRole("button", { name: /Контекст и термины/ }));
    await user.type(screen.getByLabelText("Стиль интерфейса"), "concise");
    await user.type(screen.getByLabelText("Стиль диалогов"), "natural");
    await user.type(screen.getByLabelText("Правила ненормативной лексики"), "preserve");
    await user.click(screen.getByRole("button", { name: "Сохранить контекст" }));
    const glossaryFile = new File(["term,en\nship,Ship\n"], "terms.csv", {
      type: "text/csv",
    });
    await user.upload(screen.getByLabelText("Файл глоссария"), glossaryFile);

    expect(api.patchProfile).toHaveBeenCalledWith(
      "pirate-ships",
      "p".repeat(64),
      expect.objectContaining({
        register_ui: "concise",
        register_dialogue: "natural",
        profanity: "preserve",
      }),
    );
    expect(api.createUpload).toHaveBeenCalledWith(
      "pirate-ships",
      [glossaryFile],
      "quick",
      "glossary",
    );
    expect(await screen.findByText("Глоссарий подготовлен")).toBeVisible();
  });
});


it("blocks publication with no admitted rows and explains how to continue", async () => {
  const ready = {...draft, current_step: "confirm", analysis: {...draft.analysis, confirmation: {totals: {input_rows: 1, imported_rows: 0, quarantined_rows: 1}, components: [], quarantine_reasons: {missing_source: 1}}}} satisfies ProducerUploadAnalysis;
  const api = apiMock({getUpload: vi.fn().mockResolvedValue(ready), getIntegrationPolicy: vi.fn().mockResolvedValue({external_writes: true})});
  renderWizard(api, `/projects/pirate-ships/localize?u=${draft.token}&step=4`);
  expect(await screen.findByRole("button", {name: "Запустить локализацию"})).toBeDisabled();
  expect(screen.getByText("Нет строк для перевода. Проверьте исходные тексты и структуру колонок.")).toBeVisible();
  expect(api.startLocalization).not.toHaveBeenCalled();
});

it("passes the confirmed incremental plan to start, never to navigation", async () => {
  const user=userEvent.setup();
  const ready={...draft,current_step:"confirm",answers:{...draft.answers,selected_sheets:[{file:"kit.xlsx",sheet:"Units"}]},analysis:{...draft.analysis,confirmation:{totals:{input_rows:20,imported_rows:20,quarantined_rows:0,skipped_rows:0},components:[],quarantine_reasons:{}}}} satisfies ProducerUploadAnalysis;
  const plan={id:"change-plan:1",revision:"plan-rev",read_only:false,components:[],blocked:[]};
  const api=apiMock({getUpload:vi.fn().mockResolvedValue(ready),getIntegrationPolicy:vi.fn().mockResolvedValue({external_writes:true}),getConnection:vi.fn().mockResolvedValue({read_only:false,component_map:{},snapshot:{languages:[],components:{}}}),previewChanges:vi.fn().mockResolvedValue(plan),confirmChanges:vi.fn().mockResolvedValue({...plan,confirmed:true}),startLocalization:vi.fn().mockRejectedValue(new Error("test stop"))});
  renderWizard(api,`/projects/pirate-ships/localize?u=${draft.token}&step=4`);
  await user.click(await screen.findByRole("button",{name:"Рассчитать изменения"}));
  await user.click(await screen.findByRole("button",{name:"Подтвердить перечисленные изменения"}));
  await user.click(await screen.findByRole("button",{name:"Запустить локализацию"}));
  expect(api.startLocalization).toHaveBeenCalledWith("pirate-ships",expect.objectContaining({change_plan_id:"change-plan:1",change_plan_revision:"plan-rev"}));
  await user.click(screen.getByRole("button",{name:/Контекст и термины/}));
  await waitFor(()=>expect(api.patchUpload).toHaveBeenCalled());
  expect(vi.mocked(api.patchUpload).mock.calls.at(-1)?.[1]).not.toHaveProperty("change_plan_id");
});

it("requires acknowledgement of arbitrary manual instructions before another language", async () => {
  const user=userEvent.setup();
  const api=apiMock({setManualProfile:vi.fn().mockResolvedValue({})});
  render(<ProfileIntegration api={api} slug="local" projectName="Game" source="ru" targets={["en"]} onApplied={vi.fn()} profile={{revision:"p",answers:{},summary:"",missing_required:[],profile_state:"configured",engine:"openrouter",judge_context_changed:false,generated:{persona:"Перевод только на немецкий",style:"Manual style",language_instructions:{de:"German"}}}} />);
  await user.click(screen.getByText("Готовые инструкции переводчику — ручной ввод"));
  expect(screen.getByRole("button",{name:"Сохранить инструкции",hidden:true})).toBeDisabled();
  await user.click(screen.getByRole("checkbox",{name:/Проверил ручные инструкции/}));
  expect(screen.getByRole("button",{name:"Сохранить инструкции"})).toBeEnabled();
});


it("reviews glossary terms and requires separate consent on a connected wizard project",async()=>{
 const user=userEvent.setup();
 const ready={...draft,current_step:"confirm",answers:{...draft.answers,selected_sheets:[{file:"kit.xlsx",sheet:"Units"}]},analysis:{...draft.analysis,confirmation:{totals:{input_rows:20,imported_rows:20,quarantined_rows:0,skipped_rows:0},components:[],quarantine_reasons:{}}}} satisfies ProducerUploadAnalysis;
 const glossary={...draft,token:"22222222-2222-2222-2222-222222222222",kind:"glossary" as const};
 const plan={id:"p",revision:"pr",read_only:false,blocked:[],components:[],glossary_preview:{source:"ru",targets:["de"],components:[{sheet:"Terms",units:[{key:"term",values:{ru:"Source term",de:"Ready term"}}]}]}};
 const api=apiMock({getUpload:vi.fn().mockImplementation(async token=>token===glossary.token?glossary:ready),getIntegrationPolicy:vi.fn().mockResolvedValue({external_writes:true}),getConnection:vi.fn().mockResolvedValue({read_only:false,component_map:{},snapshot:{languages:[],components:{}}}),previewChanges:vi.fn().mockResolvedValue(plan),confirmChanges:vi.fn().mockResolvedValue({...plan,confirmed:true}),startLocalization:vi.fn().mockRejectedValue(new Error("stop"))});
 renderWizard(api,`/projects/pirate-ships/localize?u=${draft.token}&g=${glossary.token}&step=4`);
 await user.click(await screen.findByRole("button",{name:"Рассчитать изменения"}));
 await user.click(await screen.findByText("Подробный список изменений"));
 expect(await screen.findByText(/Ready term/)).toBeVisible();
 await user.click(screen.getByRole("button",{name:"Подтвердить перечисленные изменения"}));
 expect(screen.getByRole("button",{name:"Запустить локализацию"})).toBeDisabled();
 await user.click(screen.getByRole("checkbox",{name:/Отдельно подтверждаю публикацию/}));
 await user.click(screen.getByRole("button",{name:"Запустить локализацию"}));
 expect(api.startLocalization).toHaveBeenCalledWith("pirate-ships",expect.objectContaining({glossary_confirmed:true,glossary_upload_id:glossary.token}));
 expect(api.previewChanges).toHaveBeenCalledWith("pirate-ships",draft.token,draft.revision,{},false,expect.objectContaining({token:glossary.token}));
});
