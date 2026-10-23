import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useBlocker, useNavigate, useParams, useSearchParams } from "react-router-dom";

import type {
  ProducerApi,
  ProducerLanguage,
  ProducerApiError,
  ProducerProfile,
  ProducerProfileAnswers,
  ProducerUiMode,
  ProducerUploadAnalysis,
  ProducerUploadAnswers,
  ProducerUploadPage,
  ProducerUploadStep,
} from "../../api/client";
import { AppShell } from "../../components/AppShell";
import { AsyncNotice } from "../../components/AsyncNotice";
import { LanguageSelection } from "./LanguageSelection";
import { codeKey, languageCode } from "./languages";
import { ProfileIntegration } from "./ProfileIntegration";
import { ChangeReview } from "../../components/ChangeReview";
import { QualityModeSelection } from "../../components/QualityModeSelection";
import type { ChangePlan, ProjectConnection } from "../../api/client";
import { t } from "../../i18n";
import {
  analysisBody,
  completeAnswers,
  gateMessages,
  selectedKey,
  type AnalyzedSheet,
} from "./types";

const QUICK_STEPS = [
  "Files",
  "Languages",
  "Context and terms",
  "Review and start",
];
const DETAILED_STEPS = [
  "Loc-kit",
  "Source language",
  "Target languages",
  "Structure",
  "Components",
  "Context",
  "Terms",
  "Review and start",
];

function stepLabel(step: string): string {
  switch (step) {
    case "Files": return t("Files");
    case "Languages": return t("Languages");
    case "Context and terms": return t("Context and terms");
    case "Review and start": return t("Review and start");
    case "Loc-kit": return t("Loc-kit");
    case "Source language": return t("Source language");
    case "Target languages": return t("Target languages");
    case "Structure": return t("Structure");
    case "Components": return t("Components");
    case "Context": return t("Context");
    case "Terms": return t("Terms");
    default: return step;
  }
}

interface LocKitWizardProps {
  api: ProducerApi;
}

function requestError(error: unknown): ProducerApiError | undefined {
  return error instanceof Error && "status" in error
    ? (error as ProducerApiError)
    : undefined;
}

function serverStep(step: number, total: number): ProducerUploadStep {
  if (step === total) return "confirm";
  if (step > 1) return "map-columns";
  return "select-content";
}

function restoredStep(
  currentStep: string,
  mode: ProducerUiMode,
  requestedStep: string | null,
): number {
  const total = mode === "quick" ? QUICK_STEPS.length : DETAILED_STEPS.length;
  const requested = Number(requestedStep);
  if (requestedStep && Number.isFinite(requested)) {
    return Math.max(1, Math.min(requested, total));
  }
  if (currentStep === "confirm") return total;
  return currentStep === "map-columns" ? 2 : 1;
}

function sheetTone(sheet: AnalyzedSheet): string {
  if (sheet.classification === "archive_candidate") return t("Archive candidate");
  if (sheet.classification === "store_candidate") return t("Store content — later");
  if (sheet.classification === "ambiguous") return t("Needs a decision");
  return t("Loc-kit");
}

export function LocKitWizard({ api }: LocKitWizardProps) {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const [draft, setDraft] = useState<ProducerUploadAnalysis>();
  const [glossaryDraft, setGlossaryDraft] = useState<ProducerUploadAnalysis>();
  const [profile, setProfile] = useState<ProducerProfile>();
  const [connection, setConnection] = useState<ProjectConnection | null>(null);
  const [glossaryConfirmed,setGlossaryConfirmed]=useState(false);
  useEffect(()=>setGlossaryConfirmed(false),[glossaryDraft?.revision]);
  const [changePlan, setChangePlan] = useState<ChangePlan | null>(null);
  const [connectionError, setConnectionError] = useState(false);
  const [writesEnabled, setWritesEnabled] = useState(typeof api.getIntegrationPolicy !== "function");
  const [qualityAvailable, setQualityAvailable] = useState(
    typeof api.getIntegrationPolicy !== "function",
  );
  useEffect(() => {
    let active = true;
    if (typeof api.getIntegrationPolicy !== "function") return;
    api.getIntegrationPolicy().then(value => {
      if (active) {
        setWritesEnabled(value.external_writes);
        setQualityAvailable(value.quality_cycle ?? true);
      }
    }).catch(() => {
      if (active) {
        setWritesEnabled(false);
        setQualityAvailable(false);
      }
    });
    return () => {active = false;};
  },[api]);
  useEffect(() => {
    if (!slug || typeof api.getConnection !== "function") return;
    let active = true;
    api.getConnection(slug).then(value => {if (active) setConnection(value);}).catch(() => {if (active) setConnectionError(true);});
    return () => {active = false;};
  }, [api, slug]);
  const [languageCatalog, setLanguageCatalog] = useState<ProducerLanguage[]>([]);
  const [catalogError, setCatalogError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    api.getLanguages(controller.signal).then(value => {
      if (active) {setLanguageCatalog(value); setCatalogError("");}
    }).catch(() => {
      if (active) setCatalogError(t("Справочник языков недоступен. Сохранённый выбор остаётся; добавление новых языков временно недоступно."));
    });
    return () => {active = false; controller.abort();};
  }, [api]);
  const [projectName, setProjectName] = useState("");
  const [profileAnswers, setProfileAnswers] = useState<ProducerProfileAnswers>();
  const [preparationState,setPreparationState] = useState({dirty:false,ready:false,busy:false});
  const profileBlocker = useBlocker(({currentLocation,nextLocation,historyAction})=>preparationState.dirty &&
    (historyAction === "POP" || currentLocation.pathname !== nextLocation.pathname || new URLSearchParams(currentLocation.search).get("u") !== new URLSearchParams(nextLocation.search).get("u")));
  useEffect(()=>{
    if(profileBlocker.state === "blocked") {
      if(window.confirm(t("Есть несохранённые изменения. Отказаться от них и перейти дальше?"))) profileBlocker.proceed();
      else profileBlocker.reset();
    }
  },[profileBlocker]);
  const [quarantine, setQuarantine] = useState<ProducerUploadPage>();
  const [structureOpen, setStructureOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [qualityEnabled, setQualityEnabled] = useState(false);
  const [qualityMode, setQualityMode] = useState<"repair" | "verify_only">("repair");
  const [qualityLanguage, setQualityLanguage] = useState("de");
  const [error, setError] = useState<string>();
  const [preUploadMode, setPreUploadMode] = useState<ProducerUiMode>("quick");
  const stepParam = search.get("step");
  const [step, setStep] = useState(() => Math.max(1, Number(stepParam) || 1));
  const uploadToken = search.get("u");
  const glossaryToken = search.get("g");
  const errorRef = useRef<HTMLDivElement>(null);
  const loadedTokenRef = useRef<string | undefined>(undefined);
  const replacementInputRef = useRef<HTMLInputElement>(null);

  const mode: ProducerUiMode = draft?.ui_mode ?? preUploadMode;
  const steps = mode === "quick" ? QUICK_STEPS : DETAILED_STEPS;
  const activeStep = Math.min(step, steps.length);
  useEffect(() => {
    if (activeStep !== (mode === "quick" ? 3 : 6)) setPreparationState({dirty:false,ready:false,busy:false});
  }, [activeStep, mode]);
  const body = draft ? analysisBody(draft) : undefined;
  const files = body?.loc_kit?.files ?? [];
  const answers = useMemo(() => (draft ? completeAnswers(draft) : undefined), [draft]);
  useEffect(() => {
    const targets = answers?.included_target_languages ?? [];
    if (targets.length && !targets.includes(qualityLanguage)) {
      setQualityLanguage(targets.includes("de") ? "de" : targets[0]);
    }
  }, [answers?.included_target_languages, qualityLanguage]);

  const announceError = useCallback((message: string) => {
    setError(message);
  }, []);

  useEffect(() => {
    if (error) errorRef.current?.focus();
  }, [error]);

  useEffect(() => setQuarantine(undefined), [draft?.revision]);
  useEffect(() => setChangePlan(null), [draft?.revision]);

  useEffect(() => {
    if (!stepParam) return;
    const requested = Number(stepParam);
    if (Number.isFinite(requested)) setStep(Math.max(1, requested));
  }, [stepParam]);

  const rememberDraft = useCallback(
    (next: ProducerUploadAnalysis, nextStep = activeStep, newKit = false) => {
      loadedTokenRef.current = next.token;
      setDraft(next);
      setSearch((current) => {
        current.set("u", next.token);
        current.set("step", String(nextStep));
        if (newKit) current.delete("g");
        return current;
      }, { replace: true });
    },
    [activeStep, setSearch],
  );

  useEffect(() => {
    if (!slug) {
      announceError(t("The project address is invalid."));
      setLoading(false);
      return;
    }
    if (uploadToken && loadedTokenRef.current === uploadToken) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const request = uploadToken
      ? api.getUpload(uploadToken, controller.signal)
      : api.getActiveUpload(slug, controller.signal).catch(reason => {
          if (requestError(reason)?.status === 404 && typeof api.restoreUpload === "function") {
            return api.restoreUpload(slug, controller.signal);
          }
          throw reason;
        });
    request
      .then((restored) => {
        loadedTokenRef.current = restored.token;
        setDraft(restored);
        const nextStep = restoredStep(restored.current_step, restored.ui_mode, stepParam);
        setStep(nextStep);
        if (!uploadToken || !stepParam) {
          setSearch((current) => {
            current.set("u", restored.token);
            current.set("step", String(nextStep));
            return current;
          }, { replace: true });
        }
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        const apiError = requestError(reason);
        if (!uploadToken && apiError?.status === 404) return;
        announceError(
          uploadToken && apiError?.status === 404
            ? t("This draft expired. Upload the files again.")
            : t("The localization draft could not be loaded."),
        );
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [announceError, api, setSearch, slug, stepParam, uploadToken]);

  useEffect(() => {
    if (!slug) return;
    const controller = new AbortController();
    Promise.all([api.getProfile(slug, controller.signal), api.getProject(slug, controller.signal)])
      .then(([next, project]) => {
        setProjectName(project.name);
        setProfile(next);
        setProfileAnswers(next.answers as ProducerProfileAnswers);
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          announceError(t("The project context could not be loaded."));
        }
      });
    return () => controller.abort();
  }, [announceError, api, slug]);

  useEffect(() => {
    if (!glossaryToken) return;
    const controller = new AbortController();
    api.getUpload(glossaryToken, controller.signal)
      .then(setGlossaryDraft)
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          announceError(t("The prepared glossary draft could not be loaded."));
        }
      });
    return () => controller.abort();
  }, [announceError, api, glossaryToken]);

  const upload = async (selected: FileList | File[]) => {
    if (!slug || selected.length === 0) return;
    setBusy(true);
    setError(undefined);
    try {
      const next = await api.createUpload(slug, Array.from(selected), mode);
      setGlossaryDraft(undefined);
      setGlossaryConfirmed(false);
      setChangePlan(null);
      rememberDraft(next, 1, true);
      setStep(1);
    } catch {
      announceError(t("The files could not be analyzed. Check the format and try again."));
    } finally {
      setBusy(false);
    }
  };

  const refreshAfterConflict = async (message: string) => {
    if (!draft) return;
    try {
      setDraft(await api.getUpload(draft.token));
    } finally {
      announceError(message);
    }
  };

  const patchNavigation = async (nextMode: ProducerUiMode, nextStep: number) => {
    const contextStep = mode === "quick" ? 3 : 6;
    if (activeStep === contextStep && nextStep !== contextStep && typeof api.prepareProfile === "function") {
      if (preparationState.busy) {announceError(t("Дождитесь завершения подготовки инструкций"));return;}
      if (preparationState.dirty) {
        if (!window.confirm(t("Есть несохранённые изменения. Отказаться от них и перейти дальше?"))) return;
        if (profile) setProfileAnswers(profile.answers);
      } else if (!preparationState.ready && nextStep > contextStep) {announceError(t("Сначала подготовьте или сохраните инструкции для выбранных языков"));return;}
    }
    if (!draft) {
      setPreUploadMode(nextMode);
      setStep(nextStep);
      return;
    }
    const previousStep = activeStep;
    const total = nextMode === "quick" ? QUICK_STEPS.length : DETAILED_STEPS.length;
    const boundedStep = Math.min(nextStep, total);
    setDraft({
      ...draft,
      ui_mode: nextMode,
      current_step: serverStep(boundedStep, total),
    });
    setStep(boundedStep);
    setSearch((current) => {
      current.set("u", draft.token);
      current.set("step", String(boundedStep));
      return current;
    }, { replace: true });
    setBusy(true);
    try {
      const updated = await api.patchUpload(draft.token, {
        revision: draft.revision,
        ui_mode: nextMode,
        current_step: serverStep(boundedStep, total),
      });
      setDraft(updated);
    } catch (reason: unknown) {
      if (requestError(reason)?.code === "stale-revision") {
        await refreshAfterConflict(t("The draft changed. We loaded the current server version."));
      } else {
        setDraft(draft);
        setStep(previousStep);
        setSearch((current) => {
          current.set("u", draft.token);
          current.set("step", String(previousStep));
          return current;
        }, { replace: true });
        announceError(t("The wizard state could not be saved."));
      }
    } finally {
      setBusy(false);
    }
  };

  const saveAnswers = async (nextAnswers: ProducerUploadAnswers, nextStep = activeStep) => {
    if (!draft) return;
    setBusy(true);
    try {
      const updated = await api.patchUpload(draft.token, {
        revision: draft.revision,
        answers: nextAnswers,
        ui_mode: mode,
        current_step: serverStep(nextStep, steps.length),
      });
      setDraft(updated);
      setStep(nextStep);
      setSearch((current) => {
        current.set("u", updated.token);
        current.set("step", String(nextStep));
        return current;
      }, { replace: true });
      if (gateMessages(updated.errors).length) {
        announceError(t("Some decisions are still required before localization can start."));
      } else {
        setError(undefined);
      }
    } catch (reason: unknown) {
      if (requestError(reason)?.code === "stale-revision") {
        await refreshAfterConflict(t("The draft changed. Review the current server version."));
      } else {
        announceError(t("The draft answers could not be saved."));
      }
    } finally {
      setBusy(false);
    }
  };

  const toggleSheet = (file: string, sheet: AnalyzedSheet, checked: boolean) => {
    if (!answers || sheet.classification === "store_candidate") return;
    const key = selectedKey(file, sheet.name);
    const selected = answers.selected_sheets.filter(
      (item) => selectedKey(item.file, item.sheet) !== key,
    );
    if (checked) selected.push({ file, sheet: sheet.name });
    const selectedFiles = [...new Set(selected.map((item) => item.file))];
    const formulas = [...new Map(
      (body?.formula_cells ?? [])
        .filter((formula) =>
          formula.auto_ignore === true && selected.some(
            (item) => item.file === formula.file && item.sheet === formula.sheet,
          ),
        )
        .map((formula) => [
          `${formula.file}\u0000${formula.sheet}\u0000${formula.column}`,
          {
            file: formula.file,
            sheet: formula.sheet,
            column: formula.column,
            role: "ignored" as const,
          },
        ]),
    ).values()];

    const retainedRoles = answers.column_roles.filter((role) =>
      selected.some((item) => item.file === role.file && item.sheet === role.sheet)
      ,
    );
    setDraft({
      ...draft!,
      answers: {
        ...answers,
        selected_files: selectedFiles,
        selected_sheets: selected,
        column_roles: [...retainedRoles, ...formulas.filter(role => !retainedRoles.some(old => old.file === role.file && old.sheet === role.sheet && old.column === role.column))],
      },
    });
  };

  const languageCandidates = useMemo(() => {
    const selected = new Set(
      (answers?.selected_sheets ?? []).map((item) => selectedKey(item.file, item.sheet)),
    );
    return [...new Set([
      ...files.flatMap((file) =>
        file.sheets
          .filter((sheet) => selected.has(selectedKey(file.path, sheet.name)))
          .flatMap((sheet) => sheet.source_candidates),
      ),
      ...(answers?.column_roles ?? [])
        .filter((role) => role.role === "language" && role.language && selected.has(selectedKey(role.file, role.sheet)))
        .map((role) => role.language!),
    ])].sort();
  }, [answers?.column_roles, answers?.selected_sheets, files]);

  const allStructureColumns = useMemo(() => {
    const selected = new Set(
      (answers?.selected_sheets ?? []).map((item) => selectedKey(item.file, item.sheet)),
    );
    return files.flatMap((file) =>
      file.sheets
        .filter((sheet) => selected.has(selectedKey(file.path, sheet.name)))
        .flatMap((sheet) => sheet.unresolved_columns.map((column) => ({
          ...column,
          file: file.path,
          sheet: sheet.name,
        }))),
    );
  }, [answers?.selected_sheets, files]);

  const unresolvedColumns = allStructureColumns.filter(column => !answers?.column_roles.some(role => role.file === column.file && role.sheet === column.sheet && role.column === column.column && role.role === "ignored"));
  const formulaSummary = [...new Map((body?.formula_cells ?? []).filter(formula => answers?.selected_sheets.some(sheet => sheet.file === formula.file && sheet.sheet === formula.sheet)).map(formula => [`${formula.file}:${formula.sheet}:${formula.column}`, formula])).values()];
  const columnLetter = (column: number) => {let text=""; for(let n=column;n>0;n=Math.floor((n-1)/26)) text=String.fromCharCode(65+(n-1)%26)+text; return text;};
  const setColumnRole = (
    file: string,
    sheet: string,
    column: number,
    value: string,
  ) => {
    if (!answers || !draft) return;
    const roles = answers.column_roles.filter(
      (role) => !(role.file === file && role.sheet === sheet && role.column === column),
    );
    if (value) {
      const [role, language] = value.split(":", 2);
      roles.push({
        file,
        sheet,
        column,
        role: role as (typeof answers.column_roles)[number]["role"],
        ...(role === "language" ? { language } : {}),
      });
    }
    setDraft({ ...draft, answers: { ...answers, column_roles: roles } });
  };

  const start = async () => {
    if (!slug || !draft || !body?.confirmation) return;
    setBusy(true);
    try {
      const run = await api.startLocalization(slug, {
        upload_id: draft.token,
        revision: draft.revision,
        ...(changePlan ? {change_plan_id: changePlan.id, change_plan_revision: changePlan.revision} : {}),
        ...(glossaryDraft ? {
          glossary_upload_id: glossaryDraft.token,
          glossary_revision: glossaryDraft.revision,
          glossary_confirmed: glossaryConfirmed,
        } : {}),
        quality: {
          enabled: qualityEnabled,
          execution_mode: qualityMode,
          language: qualityLanguage,
          max_repair_rounds: qualityMode === "verify_only" ? 0 : 2,
          consistency: ["repeat-drift", "glossary"],
        },
      });
      navigate(`/projects/${encodeURIComponent(slug)}/runs/${run.id}`);
    } catch (reason: unknown) {
      const rejected = requestError(reason);
      announceError(rejected && rejected.status >= 400 && rejected.status < 500
        ? t(rejected.message)
        : t("Localization could not be started. Review the gate and try again."));
    } finally {
      setBusy(false);
    }
  };

  const renderFiles = () => (
    <section aria-labelledby="wizard-files-heading">
      <h2 id="wizard-files-heading">{t("Files and sheets")}</h2>
      {!draft ? (
        <label className="drop-zone">
          <strong>{busy ? t("Analyzing files…") : t("Choose or drop loc-kit files")}</strong>
          <span>{t("XLSX, CSV, TSV, tabular TXT or ZIP")}</span>
          <input
            aria-label={t("Loc-kit files")}
            disabled={busy}
            multiple
            type="file"
            onChange={(event) => event.currentTarget.files && upload(event.currentTarget.files)}
          />
        </label>
      ) : (
        <div className="sheet-list">
          <div>
            <p className="helper-note">{t("Используется загруженный лок-кит. При необходимости можно загрузить новую версию кнопкой ниже.")}</p>
            <button type="button" disabled={busy} onClick={() => replacementInputRef.current?.click()}>
              {busy ? t("Analyzing files…") : t("Загрузить новый лок-кит")}
            </button>
            <input ref={replacementInputRef} hidden multiple type="file" disabled={busy}
              aria-label={t("Новая версия лок-кита")}
              onChange={event => {
                const selected = Array.from(event.currentTarget.files ?? []);
                event.currentTarget.value = "";
                void upload(selected);
              }} />
            <p className="helper-note">{t("Новый файл откроется отдельным черновиком. Для обновления Weblate сравните и подтвердите изменения на шаге «Проверка и запуск». История заданий и существующий глоссарий сохраняются.")}</p>
          </div>
          {files.flatMap((file, fileIndex) =>
            file.sheets.map((sheet, sheetIndex) => {
              const checked = answers?.selected_sheets.some(
                (item) => item.file === file.path && item.sheet === sheet.name,
              );
              const deferred = sheet.classification === "store_candidate";
              const problems = (sheet.issues ?? []).map(issue =>
                issue.code === "loc_kit.no_language_headers"
                  ? t("No recognized language headers were found.")
                  : t(issue.detail));
              if (!problems.length && sheet.classification === "ambiguous") {
                // Existing drafts predate sheet-specific analysis diagnostics.
                problems.push(t("The worksheet structure could not be recognized."));
              }
              if (answers?.source_language && sheet.source_candidates.length
                && !sheet.source_candidates.includes(answers.source_language)
                && !sheet.source_candidates.some(code => ["ru", "en"].includes(code))
                && !answers.column_roles.some(role => role.file === file.path
                  && role.sheet === sheet.name && role.role === "language"
                  && role.language === answers.source_language)) {
                problems.push(`${t("No source-language column was found:")} ${answers.source_language}.`);
              }
              const warningId = `sheet-problem-${fileIndex}-${sheetIndex}`;
              return (
                <label className="sheet-row" key={selectedKey(file.path, sheet.name)}>
                  <input
                    checked={checked ?? false}
                    disabled={deferred || busy}
                    aria-describedby={problems.length ? warningId : undefined}
                    type="checkbox"
                    onChange={(event) => toggleSheet(file.path, sheet, event.target.checked)}
                  />
                  <span>
                    <strong>{sheet.name}</strong>
                    <small>{file.path} · {sheet.rows} · {sheetTone(sheet)}</small>
                    {problems.length ? <span className="sheet-problem" id={warningId}>
                      <strong>{t("Sheet needs attention")}</strong>
                      {problems.map((problem, index) => <span key={index}>{problem}</span>)}
                      <span>{t("Uncheck this sheet if it is not needed for translation, or correct its language headers (for example ru and en).")}</span>
                    </span> : null}
                  </span>
                </label>
              );
            }),
          )}
          {formulaSummary.length > 0 ? <details className="helper-note"><summary>{t("Служебные формулы по колонкам")}</summary><ul>{formulaSummary.map(formula => <li key={`${formula.file}:${formula.sheet}:${formula.column}`}>{formula.file} · {formula.sheet} · {columnLetter(formula.column)} — {formula.auto_ignore ? t("Подсчёт длины или служебная формула; исключено из перевода") : t("Формульная колонка требует подтверждения роли")}</li>)}</ul></details> : null}
          <button disabled={!answers?.selected_sheets.length || busy} onClick={() => saveAnswers(answers!, Math.min(activeStep + 1, steps.length))} type="button">
            {t("Save selection")}
          </button>
        </div>
      )}
    </section>
  );

  const renderLanguages = (showSource = true, showTargets = true) => (
    <section aria-labelledby="wizard-languages-heading">
      <h2 id="wizard-languages-heading">{t("Языки локализации")}</h2>
      {!answers?.selected_sheets.length ? <p>{t("Select at least one loc-kit sheet first.")}</p> : null}
      <LanguageSelection source={answers?.source_language ?? null} targets={answers?.included_target_languages ?? []} kitCodes={languageCandidates} remoteCodes={connection?.snapshot.languages ?? []} catalog={languageCatalog} catalogError={catalogError} busy={busy} showSource={showSource} showTargets={showTargets}
        onSourceChange={source => setDraft(previous => previous ? {...previous, answers: {...completeAnswers(previous), source_language: source, included_target_languages: completeAnswers(previous).included_target_languages.filter(code => codeKey(code) !== codeKey(source ?? ""))}} : previous)}
        onTargetsChange={targets => setDraft(previous => previous ? {...previous, answers: {...completeAnswers(previous), included_target_languages: targets}} : previous)}/>
      <button disabled={busy || !answers?.source_language || !!catalogError || !languageCatalog.length || (showTargets && !answers.included_target_languages.length)} onClick={() => saveAnswers({...answers!, source_language: languageCode(answers!.source_language!,languageCatalog), included_target_languages: answers!.included_target_languages.map(code => languageCode(code,languageCatalog))}, Math.min(activeStep + 1, steps.length))} type="button">
        {showSource && !showTargets ? t("Save source language") : showTargets && !showSource ? t("Save target languages") : t("Save languages")}
      </button>
    </section>
  );

  const renderStructure = () => (
    <section aria-labelledby="wizard-structure-heading">
      <button id="wizard-structure-heading" className="disclosure-button" type="button" aria-expanded={structureOpen} aria-controls="wizard-structure-fields" onClick={() => setStructureOpen(open => !open)}>{t("Column structure")} · {structureOpen ? t("Скрыть") : t("Показать")}</button>
      {unresolvedColumns.some(column => !answers?.column_roles.some(role => role.file === column.file && role.sheet === column.sheet && role.column === column.column)) ? <p role="status">{t("Есть колонки без подтверждённой роли. Откройте структуру и уточните назначение.")}</p> : null}
      <div id="wizard-structure-fields" hidden={!structureOpen}>
      {!unresolvedColumns.length ? (
        <p>{t("Every selected column has a deterministic role.")}</p>
      ) : (
        <div className="field-list">
          {unresolvedColumns.map((column) => {
            const assigned = answers?.column_roles.find(
              (role) => role.file === column.file && role.sheet === column.sheet && role.column === column.column,
            );
            const value = assigned
              ? assigned.role === "language" ? `language:${assigned.language}` : assigned.role
              : "";
            const languageOptions = (column.candidates ?? [])
              .filter((candidate) => candidate.startsWith("language:"))
              .map((candidate) => candidate.slice("language:".length));
            return (
              <label className="field-stack" key={`${column.file}:${column.sheet}:${column.column}`}>
                <span>{column.sheet} · {column.header || `${t("Column")} ${column.column}`}</span>
                <select
                  aria-label={`${column.sheet} ${column.header || column.column}`}
                  disabled={busy}
                  value={value}
                  onChange={(event) => setColumnRole(column.file, column.sheet, column.column, event.target.value)}
                >
                  <option value="">{t("Choose a role")}</option>
                  <option value="reference">{t("Service reference")}</option>
                  <option value="key">{t("String key")}</option>
                  <option value="ignored">{t("Ignore column")}</option>
                  {languageOptions.map((language) => <option key={language} value={`language:${language}`}>{t("Language")} · {language}</option>)}
                </select>
              </label>
            );
          })}
        </div>
      )}
      <button
        disabled={busy || unresolvedColumns.some((column) => !answers?.column_roles.some((role) => role.file === column.file && role.sheet === column.sheet && role.column === column.column))}
        onClick={() => saveAnswers(answers!, Math.min(activeStep + 1, steps.length))}
        type="button"
      >
        {t("Save structure")}
      </button>
      </div>
    </section>
  );

  const renderComponents = () => (
    <section aria-labelledby="wizard-components-heading">
      <h2 id="wizard-components-heading">{t("Components")}</h2>
      <p>{t("Each selected worksheet becomes a separate localization component.")}</p>
      {answers?.selected_sheets.length ? (
        <ul className="content-list">
          {answers.selected_sheets.map((selection) => (
            <li key={selectedKey(selection.file, selection.sheet)}>
              <span><strong>{selection.sheet}</strong><small>{selection.file}</small></span>
              <span>{t("Loc-kit component")}</span>
            </li>
          ))}
        </ul>
      ) : <p>{t("No worksheets selected.")}</p>}
      <button disabled={busy || !answers?.selected_sheets.length} onClick={() => patchNavigation(mode, activeStep + 1)} type="button">{t("Continue to context")}</button>
    </section>
  );

  const saveProfile = async () => {
    if (!slug || !profile || !profileAnswers) return;
    setBusy(true);
    try {
      const updated = await api.patchProfile(slug, profile.revision, profileAnswers);
      setProfile(updated);
      setProfileAnswers(updated.answers as ProducerProfileAnswers);
      setError(undefined);
    } catch (reason: unknown) {
      if (requestError(reason)?.code === "stale-revision") {
        const current = await api.getProfile(slug);
        setProfile(current);
        setProfileAnswers(current.answers as ProducerProfileAnswers);
        announceError(t("The project context changed. We loaded the current version."));
      } else {
        announceError(t("The project context could not be saved."));
      }
    } finally {
      setBusy(false);
    }
  };

  const renderContext = () => (
    <section aria-labelledby="wizard-context-heading">
      <h2 id="wizard-context-heading">{t("Game context")}</h2>
      {profile && slug ? <ProfileIntegration key={slug} api={api} slug={slug} projectName={projectName} source={answers?.source_language ?? null} catalog={languageCatalog} profile={profile} targets={answers?.included_target_languages ?? []} uploadToken={draft?.token} uploadRevision={draft?.revision} answers={profileAnswers} onAnswers={setProfileAnswers} onState={setPreparationState} onApplied={updated => { setProfile(updated); if(typeof api.prepareProfile !== "function") setProfileAnswers(updated.answers as ProducerProfileAnswers); }} /> : null}
      {!profile || !profileAnswers ? <AsyncNotice>{t("Loading project context…")}</AsyncNotice> : (
        <div className="field-list">
          <label className="field-stack"><span>{t("Genre")}</span><input value={profileAnswers.genre ?? ""} onChange={(event) => setProfileAnswers({ ...profileAnswers, genre: event.target.value })} /></label>
          <label className="field-stack"><span>{t("Setting and player role")}</span><textarea value={profileAnswers.setting ?? ""} onChange={(event) => setProfileAnswers({ ...profileAnswers, setting: event.target.value })} /></label>
          <label className="field-stack"><span>{t("Аудитория")}</span><input maxLength={400} value={profileAnswers.audience ?? ""} onChange={e => setProfileAnswers({ ...profileAnswers, audience: e.target.value })} /></label>
          <label className="field-stack"><span>{t("UI register")}</span><input required value={profileAnswers.register_ui ?? ""} onChange={(event) => setProfileAnswers({ ...profileAnswers, register_ui: event.target.value })} /></label>
          <label className="field-stack"><span>{t("Dialogue register")}</span><input required value={profileAnswers.register_dialogue ?? ""} onChange={(event) => setProfileAnswers({ ...profileAnswers, register_dialogue: event.target.value })} /></label>
          <label className="field-stack"><span>{t("Profanity policy")}</span><input required value={profileAnswers.profanity ?? ""} onChange={(event) => setProfileAnswers({ ...profileAnswers, profanity: event.target.value })} /></label>
          <p className="helper-note">{typeof api.prepareProfile === "function" ? t("Изменения контекста сохраняются вместе с инструкциями переводчику.") : t("Сохраните анкету. Настроенная модель подготовит инструкции; иначе используйте ручной ввод инструкций.")}</p>
          {typeof api.prepareProfile !== "function" ? <button disabled={busy || !profileAnswers.register_ui || !profileAnswers.register_dialogue || !profileAnswers.profanity} onClick={saveProfile} type="button">{t("Save context")}</button> : null}
        </div>
      )}
    </section>
  );

  const uploadGlossary = async (selected: FileList) => {
    if (!slug || selected.length === 0) return;
    setBusy(true);
    try {
      const prepared = await api.createUpload(slug, Array.from(selected), mode, "glossary");
      setGlossaryDraft(prepared);
      setSearch((current) => {
        current.set("g", prepared.token);
        return current;
      }, { replace: true });
      setError(undefined);
    } catch {
      announceError(t("The glossary could not be prepared."));
    } finally {
      setBusy(false);
    }
  };

  const renderTerms = () => (
    <section aria-labelledby="wizard-terms-heading">
      <h2 id="wizard-terms-heading">{t("Terminology")}</h2>
      <p>{t("A prepared glossary is optional. It is published before the first machine-translation request.")}</p>
      {connection && Object.values(connection.snapshot.components).some(component=>component.glossary && component.units.length>0) ? <AsyncNotice>{t("Глоссарий уже есть в Weblate. Повторно загружать его для добавления языка не нужно. Загружайте файл только для добавления новых терминов.")}</AsyncNotice> : null}
      <label className="drop-zone compact-drop-zone">
        <strong>{glossaryDraft ? t("Glossary prepared") : t("Choose a glossary table")}</strong>
        <span>{glossaryDraft ? glossaryDraft.token : t("One CSV, TSV or XLSX worksheet")}</span>
        <input aria-label={t("Glossary file")} disabled={busy} type="file" onChange={(event) => event.currentTarget.files && uploadGlossary(event.currentTarget.files)} />
      </label>
    </section>
  );

  const renderReview = () => {
    const totals = body?.confirmation?.totals;
    const errors = gateMessages(draft?.errors);
    const selectedSheets = new Set(
      (answers?.selected_sheets ?? []).map((item) => selectedKey(item.file, item.sheet)),
    );
    const selectedFormulaColumns = [...new Map(
      (body?.formula_cells ?? [])
        .filter((formula) => selectedSheets.has(selectedKey(formula.file, formula.sheet)))
        .map((formula) => [
          `${formula.file}\u0000${formula.sheet}\u0000${formula.column}`,
          formula,
        ]),
    ).values()];
    const formulaRolesComplete = selectedFormulaColumns.every((formula) =>
      answers?.column_roles.some((role) =>
        role.file === formula.file
        && role.sheet === formula.sheet
        && role.column === formula.column,
      ),
    );
    const notes = gateMessages(draft?.attention).filter((item) => {
      if (item.code === "loc_kit.selection_required") {
        return !answers?.selected_sheets.length;
      }
      if (item.code === "loc_kit.source_language_required") {
        return !answers?.source_language;
      }
      if (item.code === "loc_kit.formulas_require_roles") {
        return !formulaRolesComplete;
      }
      return true;
    });
    const loadQuarantine = async (page = 1) => {
      if (!draft) return;
      setBusy(true);
      try {
        setQuarantine(await api.getUploadQuarantine(draft.token, undefined, page));
      } catch {
        announceError(t("Quarantined rows could not be loaded."));
      } finally {
        setBusy(false);
      }
    };
    return (
      <section aria-labelledby="wizard-review-heading">
        <h2 id="wizard-review-heading">{t("Review and start")}</h2>
        {slug && draft && typeof api.getConnection === "function" ? <ChangeReview api={api} slug={slug} uploadId={draft.token} revision={draft.revision} sheets={answers?.selected_sheets ?? []} glossary={glossaryDraft} onPlan={setChangePlan} /> : null}
        {connectionError ? <p role="alert">{t("Connection could not be loaded.")}</p> : null}
        {totals ? (
          <dl className="gate-metrics">
            <div><dt>{t("Строки с данными")}</dt><dd>{totals.input_rows}</dd></div>
            <div><dt>{t("Imported")}</dt><dd>{totals.imported_rows}</dd></div>
            <div><dt>{t("Строки, требующие проверки")}</dt><dd>{totals.quarantined_rows}</dd></div>
            <div><dt>{t("Duplicate collisions")}</dt><dd>{body?.loc_kit?.collisions.length ?? 0}</dd></div>
          </dl>
        ) : <p>{t("Save the required decisions to calculate the exact import gate.")}</p>}
        {Object.keys(body?.confirmation?.fallback_counts ?? {}).length ? (
          <details className="helper-note">
            <summary>{t("Автоматически выбран запасной исходный язык")}: {Object.entries(body!.confirmation!.fallback_counts!).map(([code, count]) => `${code}: ${count}`).join(" · ")}</summary>
            <ul>{body?.confirmation?.fallback_rows?.map((row, i) => <li key={i}>{row.file} · {row.sheet} · {row.row}: {answers?.source_language} → {row.language}</li>)}</ul>
            {body?.confirmation?.fallback_details_truncated ? <p>{t("Показаны первые 500 строк")}</p> : null}
            <p>{t("Глоссарии применяются только при совпадении исходного и целевого языков. Автоматическое обращение глоссария не выполняется.")}</p>
            <ul>{Object.keys(body!.confirmation!.fallback_counts!).flatMap(source => (answers?.included_target_languages ?? []).filter(target => target !== source).map(target => {
              const available = connection && Object.values(connection.snapshot.components).some(component => component.glossary && component.source === source && component.languages.includes(target));
              const prepared = glossaryDraft?.answers.source_language === source && glossaryDraft?.answers.included_target_languages?.includes(target);
              return <li key={`${source}:${target}`}>{source} → {target}: {available || prepared ? t("есть совместимый глоссарий") : t("подходящий глоссарий не найден")}</li>;
            }))}</ul>
          </details>
        ) : null}
        {errors.length ? (
          <ul className="gate-errors">{errors.map((item) => <li key={`${item.code}:${item.scope}`}>{t(item.detail)}</li>)}</ul>
        ) : null}
        {notes.length ? (
          <section aria-labelledby="wizard-notes-heading">
            <h3 id="wizard-notes-heading">{t("Analysis notes")}</h3>
            <ul className="gate-errors">{notes.map((item) => <li key={`${item.code}:${item.scope}`}>{t(item.detail)}</li>)}</ul>
          </section>
        ) : null}
        {totals?.quarantined_rows ? (
          <div className="quarantine-review">
            <button disabled={busy} onClick={() => void loadQuarantine()} type="button">{t("Review quarantined rows")}</button>
            {quarantine ? (
              <div className="problem-table-wrap"><table><caption>{t("Строки, требующие проверки")}</caption><thead><tr><th>{t("Файл")}</th><th>{t("Лист")}</th><th>{t("Строка Excel")}</th><th>{t("Причина")}</th><th>{t("Языки с текстом")}</th><th>{t("Подробности")}</th></tr></thead><tbody>{quarantine.results.map(row=><tr key={`${row.file}:${row.sheet}:${row.row}`}><td>{row.file}</td><td>{row.sheet}</td><td>{row.coordinate}</td><td>{t(row.reason_code ?? row.reason ?? "unknown")}</td><td>{row.available_languages?.join(", ") || "—"}</td><td><details><summary>{t("Подробности")}</summary><ul>{row.original_cells.map((cell,i)=><li key={i}>{columnLetter(i+1)}: {cell || "—"}</li>)}</ul></details></td></tr>)}</tbody></table><div className="button-row"><button type="button" disabled={busy || !quarantine.previous} onClick={() => void loadQuarantine(Number(quarantine.previous))}>{t("Предыдущая страница")}</button><button type="button" disabled={busy || !quarantine.next} onClick={() => void loadQuarantine(Number(quarantine.next))}>{t("Следующая страница")}</button></div></div>
            ) : null}
          </div>
        ) : null}
        {connection && glossaryDraft && changePlan?.glossary_preview ? <label className="check-row"><input type="checkbox" checked={glossaryConfirmed} onChange={event=>setGlossaryConfirmed(event.target.checked)} />Отдельно подтверждаю публикацию показанного глоссария в новый компонент. Прежние глоссарии сохраняются.</label> : null}
        {!writesEnabled ? <p role="status">Запись во внешний Weblate отключена. Подключение, сравнение и скачивание доступны; запуск перевода заблокирован.</p> : null}
        <fieldset className="quality-policy">
          <legend>{t("Automatic quality cycle")}</legend>
          <label className="check-row">
            <input
              checked={qualityEnabled}
              disabled={!qualityAvailable}
              onChange={(event) => setQualityEnabled(event.target.checked)}
              type="checkbox"
            />
            {t("Run deterministic checks, two judges, consistency repair and recheck before export")}
          </label>
          {!qualityAvailable ? (
            <p role="status">{t("Automatic quality cycle is disabled by the operator.")}</p>
          ) : null}
          {qualityEnabled ? (
            <><QualityModeSelection value={qualityMode} onChange={setQualityMode} />
            <p className="helper-note">{t("This mode applies to the quality phase after localization. Existing completed translations can be reviewed separately.")}</p>
            <label className="field-stack">
              <span>{t("Quality check language")}</span>
              <select value={qualityLanguage} onChange={(event) => setQualityLanguage(event.target.value)}>
                {(answers?.included_target_languages ?? []).map((language) => (
                  <option key={language} value={language}>{language}</option>
                ))}
              </select>
            </label></>
          ) : null}
          <p className="helper-note">
            {t("Only candidates verified by both judges are applied. Approved, stale and terminology-conflict strings remain unchanged and are reported as exceptions.")}
          </p>
        </fieldset>
        {totals?.imported_rows === 0 ? <p role="status">{t("Нет строк для перевода. Проверьте исходные тексты и структуру колонок.")}</p> : null}
        <button className="primary-button" disabled={!writesEnabled || busy || !body?.confirmation || totals?.imported_rows === 0 || errors.length > 0 || connectionError || !!connection && (connection.read_only || !changePlan?.confirmed || !!glossaryDraft && !glossaryConfirmed)} onClick={start} type="button">
          {busy ? t("Starting localization…") : t("Start localization")}
        </button>
      </section>
    );
  };

  let panel = renderFiles();
  if (activeStep === steps.length) panel = renderReview();
  else if (mode === "detailed" && activeStep === 4) panel = renderStructure();
  else if (mode === "detailed" && activeStep === 2) panel = renderLanguages(true, false);
  else if (mode === "detailed" && activeStep === 3) panel = renderLanguages(false, true);
  else if (mode === "detailed" && activeStep === 5) panel = renderComponents();
  else if (mode === "quick" && activeStep === 2) panel = <>{renderLanguages()}{unresolvedColumns.length ? renderStructure() : null}</>;
  else if (mode === "quick" && activeStep === 3) panel = <>{renderContext()}{renderTerms()}</>;
  else if (mode === "detailed" && activeStep === 6) panel = renderContext();
  else if (mode === "detailed" && activeStep === 7) panel = renderTerms();
  else if (activeStep > 1) panel = (
    <section><h2>{stepLabel(steps[activeStep - 1])}</h2><p>{t("This step uses the same server draft and keeps every earlier decision.")}</p></section>
  );

  return (
    <AppShell>
      <nav aria-label={t("Breadcrumb")} className="breadcrumbs">
        <Link to="/projects">{t("Projects")}</Link> / <Link to={`/projects/${slug}`}>{slug}</Link> / <span>{t("Localization")}</span>
      </nav>
      <div className="wizard-heading">
        <div><p className="eyebrow">{t("New localization")}</p><h1>{t("Prepare the loc-kit")}</h1></div>
      </div>
      {error ? <div ref={errorRef} tabIndex={-1}><AsyncNotice kind="error">{error}</AsyncNotice></div> : null}
      {loading ? <AsyncNotice>{t("Loading localization draft…")}</AsyncNotice> : (
        <div className="wizard-layout">
          <aside aria-label={t("Wizard steps")}>
            <ol>{steps.map((name, index) => <li key={name}><button aria-current={index + 1 === activeStep ? "step" : undefined} disabled={busy} onClick={() => patchNavigation(mode, index + 1)} type="button"><span>{index + 1}</span>{stepLabel(name)}</button></li>)}</ol>
          </aside>
          <div className="wizard-panel">{panel}</div>
        </div>
      )}
    </AppShell>
  );
}
