import { t } from "../i18n";
import { QualityDetails } from "./QualityDetails";

interface QualitySummaryProps {
  value: unknown;
  runId: string;
}

const phases: Record<string, string> = {
  preflight: "Checking server support", "initial-scan": "Checking glossary and repeats",
  "full-judge-start": "Two judges are checking translations", "full-judge-poll": "Two judges are checking translations",
  "individual-apply": "Applying judge corrections", "affected-recheck": "Rechecking changed translations",
  "repeat-plan": "Choosing consistent translations", "repeat-apply": "Unifying repeated translations",
  "recipient-recheck": "Checking updated repeats", "final-scan": "Final quality checks",
  completed: "Quality checks finished",
};
const countLabels: Record<string, string> = {
  processed: "Processed rows", automatically_corrected: "Automatically corrected rows",
  consistency_issues: "Remaining required issues", glossary_hard: "Required glossary issues",
  repeat_groups: "Unresolved repeat groups", advisory: "Advisory issues",
  not_verified: "Rows without verified judgment", quarantined_transport: "Isolated service errors",
  both_parsed: "Rows with both judge responses", pass: "Rows with PASS",
};

export function QualitySummary({ value, runId }: QualitySummaryProps) {
  const quality =
    value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : {};
  const counts =
    quality.counts && typeof quality.counts === "object"
      ? (quality.counts as Record<string, unknown>)
      : {};
  const rows = Object.entries(counts).filter(([, count]) =>
    Number.isFinite(count),
  );
  if (!quality.phase && rows.length === 0) return null;
  return (
    <section aria-labelledby="quality-summary-heading" aria-live="polite" className="run-panel">
      <h2 id="quality-summary-heading">{t("Automatic quality cycle")}</h2>
      <p>
        {quality.status === "waiting_for_configuration"
          ? t("Waiting for the Weblate full quality API. Processing has not started.")
          : quality.status === "completed_with_exceptions"
          ? t("Quality checks finished with exceptions. See the quality report.")
          : quality.status === "completed"
            ? t("Both judges and consistency checks completed.")
            : quality.execution_mode === "verify_only" ? t("Translations are checked without changes.")
            : t("Errors are being checked and repaired automatically.")}
      </p>
      {typeof quality.phase === "string" ? (
        <p>{t("Current quality phase")}: {t(phases[quality.phase] ?? quality.phase)}</p>
      ) : null}
      {rows.length ? (
        <dl className="quality-counts">
          {rows.map(([name, count]) => (
            <div key={name}><dt>{t(countLabels[name] ?? name)}</dt><dd>{String(count)}</dd></div>
          ))}
        </dl>
      ) : null}
      <a href={`/api/producer/runs/${encodeURIComponent(runId)}/quality/`}>
        {t("Open machine-readable quality report")}
      </a>
      <QualityDetails runId={runId} />
    </section>
  );
}
