import { useState } from "react";
import { t } from "../i18n";

type UnitResult = {
  unit_id: number;
  outcome: string;
  application_id?: number | null;
  seat_results?: Record<string, string>;
  final_reasons?: string[];
};
type QualityReport = {
  units: UnitResult[];
  groups: { group_id: string; outcome: string }[];
  weblate_project_url?: string;
};

const labels: Record<string, string> = {
  clean: "Verified", residual_issues: "Outstanding issues", not_verified: "Not verified",
  parsed: "Response received", unparsed: "Response could not be parsed", missing: "Response missing",
  "repeat-drift": "Inconsistent repeated translation", glossary: "Glossary issue",
  check: "Technical check issue", needs_review: "Needs review", ready: "Ready",
  applied: "Applied", no_change: "Unchanged", forbidden: "Permission denied",
  stale: "Changed after check", approved_conflict: "Conflicting approvals",
  independent: "Independent translation", failed: "Failed", unrepairable: "Could not repair",
};
function label(value: string) { return t(labels[value] ?? "Unknown quality result"); }
function safeProjectUrl(value?: string) {
  try {
    const url = new URL(value ?? "");
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch { return undefined; }
}

export function QualityDetails({ runId }: { runId: string }) {
  const [report, setReport] = useState<QualityReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  async function load() {
    setLoading(true);
    setFailed(false);
    try {
      const response = await fetch(`/api/producer/runs/${encodeURIComponent(runId)}/quality/`, { credentials: "same-origin" });
      if (!response.ok) throw new Error();
      const value = await response.json() as QualityReport;
      if (!Array.isArray(value.units) || !Array.isArray(value.groups)) throw new Error();
      setReport(value);
    } catch { setFailed(true); }
    finally { setLoading(false); }
  }
  const projectUrl = safeProjectUrl(report?.weblate_project_url);
  return <div>
    <button type="button" disabled={loading} onClick={() => void load()}>
      {t(report ? "Refresh row results" : "Show row results")}
    </button>
    {failed ? <p role="alert">{t("Quality report could not be loaded. Try again.")}</p> : null}
    {report ? <>
      <table>
        <caption>{t("Translation quality by row")}</caption>
        <thead><tr>{["Unit ID", "Final result", "Judge 1", "Judge 2", "Remaining issues", "Weblate application"].map(name => <th scope="col" key={name}>{t(name)}</th>)}</tr></thead>
        <tbody>{report.units.map(unit => <tr key={unit.unit_id}>
          <th scope="row">{unit.unit_id}</th><td>{label(unit.outcome)}</td>
          {["1", "2"].map(seat => <td key={seat}>{label(unit.seat_results?.[seat] ?? "missing")}</td>)}
          <td>{unit.final_reasons?.length ? unit.final_reasons.map(label).join(", ") : t("None")}</td>
          <td>{unit.application_id ? projectUrl
            ? <a href={projectUrl} target="_blank" rel="noopener noreferrer">{unit.application_id}</a>
            : unit.application_id : "—"}</td>
        </tr>)}</tbody>
      </table>
      {report.groups.length ? <table>
        <caption>{t("Repeated translation groups")}</caption>
        <thead><tr><th scope="col">{t("Group ID")}</th><th scope="col">{t("Final result")}</th></tr></thead>
        <tbody>{report.groups.map(group => <tr key={group.group_id}><th scope="row">{group.group_id}</th><td>{label(group.outcome)}</td></tr>)}</tbody>
      </table> : null}
    </> : null}
  </div>;
}
