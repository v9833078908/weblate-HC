import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import type { ProducerApi, ProducerRun } from "../api/client";
import { AppShell } from "../components/AppShell";
import { AsyncNotice } from "../components/AsyncNotice";
import { QualitySummary } from "../components/QualitySummary";
import { ExistingVerification } from "../components/ExistingVerification";
import { QualityArtifactReport } from "../components/QualityArtifactReport";
import { t } from "../i18n";

interface RunCardProps {
  api: ProducerApi;
}

interface StageRow {
  id: string;
  label: string;
  status: string;
  detail?: string;
}

function stageRows(value: unknown): StageRow[] {
  if (!value || typeof value !== "object") return [];
  const stages = value as Record<string, unknown>;
  const labels: Record<string, string> = {
    components: t("Import components"),
    languages: t("Create languages"),
    glossary: t("Publish glossary"),
    translate: t("Machine translation"),
    checks: t("Translation checks"),
  };
  return Object.entries(labels).map(([id, label]) => {
    const raw = stages[id];
    if (Array.isArray(raw)) {
      const completed = raw.filter(
        (item) =>
          item &&
          typeof item === "object" &&
          (item as { status?: string }).status === "completed",
      ).length;
      return {
        id,
        label,
        status:
          completed === raw.length
            ? "completed"
            : completed
              ? "running"
              : "pending",
        detail: `${completed}/${raw.length}`,
      };
    }
    const item =
      raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    return {
      id,
      label,
      status: typeof item.status === "string" ? item.status : "pending",
      detail:
        item.counts && typeof item.counts === "object"
          ? Object.entries(item.counts as Record<string, unknown>)
              .map(([name, count]) => `${name}: ${String(count)}`)
              .join(" · ")
          : undefined,
    };
  });
}

function runAttempt(run: ProducerRun): number {
  const stages =
    run.stages && typeof run.stages === "object"
      ? (run.stages as Record<string, unknown>)
      : {};
  const execution =
    stages.execution && typeof stages.execution === "object"
      ? (stages.execution as Record<string, unknown>)
      : {};
  return typeof execution.attempt === "number" ? execution.attempt : 1;
}

function MTObservation({ run }: { run: ProducerRun }) {
  const observation = (run.stages as Record<string, unknown> | undefined)?.mt_observation;
  if (!observation || typeof observation !== "object") return null;
  const value = observation as {
    translated: number; total: number;
    scopes: Record<string, { state: string; next_poll_at?: number; last_progress?: number }>;
  };
  if (!value.total || !value.scopes) return null;
  const labels: Record<string, string> = {
    polling: t("Translation accepted; waiting for server progress."),
    reconciling: t("Reconciling after timeout; no new translation is submitted."),
    "rate-limited": t("Server rate limit; waiting before the next check."),
    provider_unknown: t("Provider outcome is unknown; a paid request will not be repeated automatically."),
    partial: t("MT batch needs reconciliation; completed translations are preserved."),
    failed: t("MT batch needs reconciliation; completed translations are preserved."),
    preparing: t("Preparing the next empty rows."),
    completed: t("Translation scope completed."),
  };
  return <section className="run-panel" aria-label={t("Machine translation")}>
    <p>{value.translated} / {value.total}</p>
    <div role="status" aria-live="polite">
      {Object.entries(value.scopes).map(([scope, item]) => <div key={scope}>
        <p>{scope}: <span>{labels[item.state] ?? t(item.state)}</span></p>
        {item.last_progress ? <p>{t("Last progress:")} <time dateTime={new Date(item.last_progress * 1000).toISOString()}>{new Date(item.last_progress * 1000).toLocaleString("ru-RU")}</time></p> : null}
        {item.next_poll_at ? <p>{t("Next check:")} <time dateTime={new Date(item.next_poll_at * 1000).toISOString()}>{new Date(item.next_poll_at * 1000).toLocaleString("ru-RU")}</time></p> : null}
      </div>)}
    </div>
  </section>;
}

export function RunCard({ api }: RunCardProps) {
  const { slug, runId } = useParams<{ slug: string; runId: string }>();
  const [run, setRun] = useState<ProducerRun>();
  const [error, setError] = useState<string>();
  const [resuming, setResuming] = useState(false);

  const load = useCallback(async () => {
    if (!runId) return;
    try {
      setRun(await api.getRun(runId));
      setError(undefined);
    } catch {
      setError(t("The localization run could not be loaded."));
    }
  }, [api, runId]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!run || !["queued", "running", "no-update", "cancel_requested"].includes(run.status))
      return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load, run]);

  const rows = useMemo(() => stageRows(run?.stages), [run?.stages]);
  const resume = async () => {
    if (!run?.resume_allowed) return;
    setResuming(true);
    try {
      setRun(await api.resumeRun(run.id, runAttempt(run)));
    } catch {
      setError(t("The unfinished run could not be resumed."));
    } finally {
      setResuming(false);
    }
  };

  return (
    <AppShell>
      <nav aria-label={t("Breadcrumb")} className="breadcrumbs">
        <Link to="/projects">{t("Projects")}</Link> /{" "}
        <Link to={`/projects/${slug}`}>{slug}</Link> /{" "}
        <span>{t("Localization run")}</span>
      </nav>
      {error ? <AsyncNotice kind="error">{error}</AsyncNotice> : null}
      {!run && !error ? (
        <AsyncNotice>{t("Loading localization run…")}</AsyncNotice>
      ) : null}
      {run ? (
        <article aria-labelledby="run-heading" className="run-card-page">
          <div className="page-heading">
            <div>
              <p className="eyebrow">{t("Localization run")}</p>
              <h1 id="run-heading">{run.requested_mode === "quality" ? t("Quality-only review") : t("First localization")}</h1>
              <p className="lead">{t(run.status)}</p>
              {run.execution_mode === "verify_only" ? <p>{t("Verify only — without changing translations")}</p> : null}
              {run.requested_mode === "quality" ? <p>{t("Machine translation is not run. The original completed job and its file are preserved.")}</p> : null}
            </div>
            {run.resume_allowed ? (
              <button
                className="primary-button"
                disabled={resuming}
                onClick={resume}
                type="button"
              >
                {resuming ? t("Resuming…") : t("Resume unfinished")}
              </button>
            ) : null}
          </div>
          {run.status === "no-update" || run.liveness === "no-update" ? (
            <AsyncNotice kind="error">
              {t(
                "No updates from the worker. The stored stages are safe; resume is available only when the current attempt fails.",
              )}
            </AsyncNotice>
          ) : null}
          {run.failure ? (
            <AsyncNotice kind="error">{t(run.failure)}</AsyncNotice>
          ) : null}
          <MTObservation run={run} />
          <button type="button" className="secondary-button" onClick={() => void load()}>{t("Check status")}</button>
          {run.translation_coverage || run.judge_coverage ? <section className="run-panel" aria-label={t("Quality execution mode")}>
            <dl className="quality-counts">
              {run.translation_coverage ? <div><dt>{t("Translated rows")}</dt><dd>{run.translation_coverage.filled} / {run.translation_coverage.total}</dd></div> : null}
              {run.judge_coverage ? <><div><dt>{t("Rows with both judge responses")}</dt><dd>{run.judge_coverage.both_parsed} / {run.judge_coverage.total}</dd></div>
                <div><dt>{t("Rows with PASS")}</dt><dd>{run.judge_coverage.pass} / {run.judge_coverage.total}</dd></div></> : null}
              {run.payment_outcome ? <div><dt>{t("MT payment outcome unknown")}</dt><dd>{run.payment_outcome.unknown}</dd></div> : null}
            </dl>
          </section> : null}
          <a href={`/api/producer/runs/${encodeURIComponent(run.id)}/diagnostics/`} download>{t("Download diagnostic report")}</a>
          {["queued", "running", "no-update", "unknown"].includes(run.status) ? <div>
            <button type="button" disabled={resuming} onClick={async () => {
              setResuming(true);
              try { setRun(await api.cancelRun(run.id)); } catch { setError(t("Не удалось запросить отмену.")); }
              finally { setResuming(false); }
            }}>{t("Отменить дальнейшую обработку")}</button>
            <p>{t("Отмена прекращает следующие шаги. Уже принятый перевод на сервере может продолжиться.")}</p>
          </div> : null}
          <section className="run-panel" aria-labelledby="run-stages-heading">
            <h2 id="run-stages-heading">{t("Stages")}</h2>
            <ol className="run-stages">
              {rows.filter((row) => run.requested_mode !== "quality" || row.id === "checks").map((row) => (
                <li data-status={row.status} key={row.id}>
                  <span aria-hidden="true" className="stage-dot" />
                  <span>{row.label}</span>
                  {row.detail ? <small>{row.detail}</small> : null}
                </li>
              ))}
            </ol>
          </section>
          <QualitySummary
            runId={run.id}
            value={
              run.stages && typeof run.stages === "object"
                ? (run.stages as Record<string, unknown>).checks
                : undefined
            }
          />
          {run.status === "completed" && run.result_bundle_available ? <QualityArtifactReport runId={run.id} /> : null}
          {run.status === "completed" ? <ExistingVerification api={api} runId={run.id} /> : null}
          {run.warnings &&
          Array.isArray(run.warnings) &&
          run.warnings.length ? (
            <section className="run-panel">
              <h2>{t("Warnings")}</h2>
              <ul>
                {run.warnings.map((warning) => (
                  <li key={String(warning)}>{t(String(warning))}</li>
                ))}
              </ul>
            </section>
          ) : null}
          {run.outcome ? (
            <div className="run-outcome">
              <p>
                {run.outcome === "ready"
                  ? t("Ready for export")
                  : run.outcome === "ready_with_exceptions"
                    ? t("Ready for export with isolated quality exceptions")
                  : t("Needs attention before export")}
              </p>
              {run.result_bundle_available ? null : run.requested_mode === "quality" ? <a href={`/api/producer/runs/${encodeURIComponent(run.id)}/export/?format=xlsx`} download>
                {t("Review and download")}
              </a> : <Link to={`/projects/${slug}/download`}>
                {t("Review and download")}
              </Link>}
            </div>
          ) : null}
        </article>
      ) : null}
    </AppShell>
  );
}
