"use client";

import { ArrowLeft, ArrowRight, Check, Rocket } from "lucide-react";
import * as React from "react";
import { AppShell } from "@/components/console/AppShell";
import {
  finalTargetCodes,
  initLanguages,
  UniCtx,
} from "@/components/console/wizard2/ctx";
import { StageContext } from "@/components/console/wizard2/StageContext";
import { StageFiles } from "@/components/console/wizard2/StageFiles";
import { StageLanguages } from "@/components/console/wizard2/StageLanguages";
import { StageReview } from "@/components/console/wizard2/StageReview";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import * as api from "@/src/api/client";
import { SCENARIOS } from "@/src/api/mock/universal";

const STAGE_NAMES = [
  "Файлы",
  "Языки",
  "Контекст и термины",
  "Проверка и запуск",
];
const STAGES = [StageFiles, StageLanguages, StageContext, StageReview];

export function UniversalWizard({ project, navigate }) {
  const slug = project.slug;
  const [step, setStep] = React.useState(1);
  const [maxReached, setMaxReached] = React.useState(1);
  const [analysis, setAnalysis] = React.useState(null);
  const [answers, setAnswersState] = React.useState({
    source_language: null,
    languages: null,
    profile: null,
    glossary: { skipped: false, selected: {} },
  });
  const [uploading, setUploading] = React.useState(false);
  const [registry, setRegistry] = React.useState({});
  const [submitting, setSubmitting] = React.useState(false);

  const setAnswers = React.useCallback(
    (patch) =>
      setAnswersState((p) => ({
        ...p,
        ...(typeof patch === "function" ? patch(p) : patch),
      })),
    [],
  );

  React.useEffect(() => {
    api
      .getStoresRegistry()
      .then(setRegistry)
      .catch(() => {});
  }, []);

  // restore / deep-link
  // biome-ignore lint/correctness/useExhaustiveDependencies: mount-once deep-link restore; startUpload intentionally captured from first render (existing eslint-disable).
  React.useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const u = q.get("u");
    const scenario = q.get("scenario");
    const stg = Number(q.get("stage") || 0);
    if (u) {
      api
        .getUniversalUpload(u)
        .then((a) => {
          setAnalysis(a);
          setAnswersState((p) => ({
            ...p,
            languages: initLanguages(a),
            source_language: a.source_language,
          }));
          if (stg) {
            setStep(stg);
            setMaxReached(stg);
          }
        })
        .catch(() => {});
    } else if (scenario) {
      const jump =
        stg ||
        (typeof SCENARIOS[scenario]?.stage === "number"
          ? SCENARIOS[scenario].stage
          : 1);
      startUpload(scenario, jump);
    }
  }, []); // eslint-disable-line

  const startUpload = React.useCallback(
    (input, jumpTo) => {
      setUploading(input);
      api
        .createUniversalUpload(slug, input)
        .then((a) => {
          setUploading(false);
          setAnalysis(a);
          setAnswersState({
            languages: initLanguages(a),
            source_language: a.source_language,
            profile: null,
            glossary: { skipped: false, selected: {} },
          });
          const sp = new URLSearchParams();
          sp.set("u", a.id);
          window.history.replaceState(
            {},
            "",
            `/projects/${slug}/localize?${sp.toString()}`,
          );
          if (jumpTo && jumpTo > 1) {
            setStep(jumpTo);
            setMaxReached((m) => Math.max(m, jumpTo));
          }
        })
        .catch(() => setUploading(false));
    },
    [slug],
  );

  const resetUpload = React.useCallback(() => {
    setAnalysis(null);
    setStep(1);
    setMaxReached(1);
    window.history.replaceState({}, "", `/projects/${slug}/localize`);
  }, [slug]);

  const patch = React.useCallback(
    async (body) => {
      if (!analysis) return null;
      const a = await api.patchUniversalUpload(analysis.id, body);
      setAnalysis(a);
      return a;
    },
    [analysis],
  );

  const goto = React.useCallback((n) => {
    setStep(n);
    setMaxReached((m) => Math.max(m, n));
  }, []);

  const source = answers.source_language || analysis?.source_language;
  const targets = finalTargetCodes(answers.languages, source);
  const cjkNeeded = targets.some((c) => ["ja", "ko"].includes(c));
  const isStore = analysis?.kind === "store";

  const canProceed = React.useMemo(() => {
    if (step === 1) return !!analysis && !!analysis.stage1_ready;
    if (step === 2) {
      const included = (answers.languages || []).filter((l) => l.include);
      const tgt = included.filter((l) => l.code);
      const unresolved = included.filter((l) => l.group && !l.code);
      const sourceOk =
        !analysis?.source_options ||
        !!analysis.source_options.find((o) => o.code === source)?.complete;
      return tgt.length > 0 && unresolved.length === 0 && sourceOk;
    }
    if (step === 3) {
      const p = answers.profile || {};
      const dialogueOk = isStore ? true : !!p.register_dialogue;
      return (
        !!p.register_ui &&
        !!p.profanity &&
        dialogueOk &&
        (!cjkNeeded || !!p.cjk_politeness)
      );
    }
    return true;
  }, [step, analysis, answers, source, cjkNeeded, isStore]);

  const submit = async () => {
    setSubmitting(true);
    const run = await api.createUniversalLocalization(slug, {
      kind: analysis.kind,
      store: analysis.store,
      upload_id: analysis.id,
      source_language: source,
      languages: targets,
      profile: answers.profile,
      glossary: { selected: answers.glossary?.selected || {} },
    });
    setSubmitting(false);
    navigate(`/projects/${slug}/runs/${run.id}`);
  };

  const ctx = {
    slug,
    navigate,
    analysis,
    setAnalysis,
    answers,
    setAnswers,
    patch,
    registry,
    goto,
    step,
    uploading,
    startUpload,
    resetUpload,
  };
  const StageComp = STAGES[step - 1];

  return (
    <UniCtx.Provider value={ctx}>
      <AppShell
        project={project}
        active="overview"
        navigate={navigate}
        breadcrumb={[
          { label: "Проекты", path: "/" },
          { label: project.name, path: `/projects/${slug}` },
          { label: "Сделать локализацию" },
        ]}
      >
        <div className="mb-6 flex items-center justify-between gap-3">
          <h1 className="text-heading-page text-foreground">
            Сделать локализацию
          </h1>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              className="h-9 rounded-sm"
              onClick={() => navigate(`/projects/${slug}/localize-legacy`)}
            >
              Старый мастер
            </Button>
            <Button
              variant="ghost"
              className="h-9 rounded-sm"
              onClick={() => navigate(`/projects/${slug}`)}
            >
              Отмена
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-8 md:grid-cols-[240px_1fr]">
          <aside className="md:sticky md:top-16 md:self-start">
            <ol className="space-y-1">
              {STAGE_NAMES.map((name, i) => {
                const n = i + 1;
                const done = n < step;
                const cur = n === step;
                const reachable = n <= maxReached;
                return (
                  <li key={name}>
                    <button
                      type="button"
                      disabled={!reachable}
                      onClick={() => reachable && goto(n)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-sm px-3 py-2 text-start text-body-sm transition-colors",
                        cur
                          ? "bg-accent font-semibold text-foreground"
                          : reachable
                            ? "text-foreground hover:bg-muted"
                            : "cursor-not-allowed text-muted-foreground",
                      )}
                    >
                      <span
                        className={cn(
                          "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-label-caps",
                          done
                            ? "bg-success text-success-foreground"
                            : cur
                              ? "bg-primary text-primary-foreground"
                              : "bg-muted text-muted-foreground",
                        )}
                      >
                        {done ? <Check className="h-3.5 w-3.5" /> : n}
                      </span>
                      <span className="flex-1">{name}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </aside>

          <div className="min-w-0">
            <StageComp />

            <div className="mt-8 flex items-center justify-between gap-3 border-t border-border pt-4">
              <div>
                {step > 1 && (
                  <Button
                    variant="secondary"
                    className="h-9 rounded-sm"
                    onClick={() => goto(step - 1)}
                  >
                    <ArrowLeft className="h-4 w-4" />
                    Назад
                  </Button>
                )}
              </div>
              <div className="flex items-center gap-2">
                {step < 4 && (
                  <Button
                    className="h-9 rounded-sm"
                    disabled={!canProceed}
                    onClick={() => goto(step + 1)}
                  >
                    Далее <ArrowRight className="h-4 w-4" />
                  </Button>
                )}
                {step === 4 && (
                  <Button
                    className="h-9 rounded-sm"
                    disabled={submitting || targets.length === 0}
                    onClick={submit}
                  >
                    <Rocket className="h-4 w-4" />
                    {submitting ? "Запуск…" : "Сделать локализацию"}
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      </AppShell>
    </UniCtx.Provider>
  );
}
