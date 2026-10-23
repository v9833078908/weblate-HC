import { useEffect, useState } from "react";
import { t } from "../i18n";

type Issue = {
  file: string; sheet: string; row: number; column: number; key: string; unit_id: number;
  source: string; target: string; category: string; unverified: boolean;
  reason: string; judge_1: string; judge_2: string; action: string;
};
type Report = {
  run_id: string; filename: string; provenance: {scope_revision: string};
  summary: {status: string; processed: number; automatically_corrected: number;
    blocking_cells: number; unverified_cells: number; issue_count: number;
    judge_verified_units: number; quarantined_transport: number;
    project_issues: {kind: string; group_id: string; reason?: string; check_id?: string}[]; judge_seats?: Record<string, Record<string, number>>};
  issues: Issue[]; total: number; page: number; page_size: number;
};
const labels: Record<string, string> = {
  blocking: "Critical issue", unverified: "Verification incomplete", advisory: "Recommendation",
  parsed: "Response parsed (does not mean approval)", missing: "No current response",
  approved: "Approved for the current revision",
  translation_propagation_disabled: "Repeat checks are disabled for this component",
  check_component_repeat_settings: "Check the component repeat settings",
  unparsed: "Response could not be parsed", glossary: "Glossary issue",
  "repeat-drift": "Inconsistent repeated translation", check: "Technical check issue",
  current_revision_not_verified: "No approval from both judges for the current revision",
  judge_major: "Major judge issue for the current revision", judge_critical: "Critical judge issue for the current revision",
  manual_review: "Review and correct manually", complete_verification: "Complete verification",
  review_recommendation: "Consider recommendation",
};
function label(value: string) { return t(labels[value] ?? value); }
function columnName(column: number) {
  let result = "";
  for (let value = column; value > 0; value = Math.floor((value - 1) / 26)) {
    result = String.fromCharCode(65 + (value - 1) % 26) + result;
  }
  return result;
}

export function QualityArtifactReport({runId}: {runId: string}) {
  const [selection, setSelection] = useState({runId, category: "all", page: 1});
  const query = selection.runId === runId ? selection : {category: "all", page: 1};
  const [retry, setRetry] = useState(0);
  const [data, setData] = useState<{runId: string; report: Report}>();
  const [failed, setFailed] = useState(false);
  const base = `/api/producer/runs/${encodeURIComponent(runId)}`;
  const report = data?.runId === runId ? data.report : undefined;
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setData(undefined);
    setFailed(false);
    void (async () => {
      try {
        const response = await fetch(`${base}/quality-report/?format=json&category=${query.category}&page=${query.page}&page_size=50`,
          {credentials: "same-origin", signal: controller.signal});
        if (!response.ok) throw new Error();
        const value = await response.json() as Report;
        if (value.run_id !== runId || !Array.isArray(value.issues) || !value.summary ||
            !value.provenance || !Number.isInteger(value.total) || value.total < 0) throw new Error();
        if (active) setData({runId, report: value});
      } catch {
        if (active) setFailed(true);
      }
    })();
    return () => { active = false; controller.abort(); };
  }, [base, runId, query.category, query.page, retry]);
  return <section className="run-panel quality-artifact-panel" aria-labelledby="quality-artifact-heading">
    <h2 id="quality-artifact-heading">{t("Quality report")}</h2>
    <p>{t("Pink marks critical issues; yellow marks unverified cells. Reasons are listed in the report.")}</p>
    <div className="button-row">
      <a href={`${base}/export/?format=xlsx`} download>{t("Download final file")}</a>
      <a href={`${base}/quality-report/?format=xlsx`} download>{t("Download quality report")}</a>
      <a href={`${base}/result-bundle/`} download>{t("Download all results")}</a>
    </div>
    {failed ? <div role="alert">
      <p>{t("Could not load the saved report")}</p>
      <button type="button" onClick={() => setRetry(value => value + 1)}>{t("Retry loading")}</button>
    </div> : !report ? <p role="status">{t("Loading saved report")}</p> : <>
      <p>{report.filename}</p>
      <p>{t("Result revision")}: {report.provenance.scope_revision}</p>
      <p role="status">{report.summary.status === "completed" ? t("Quality checks finished") :
        t("Quality checks finished with exceptions. See the quality report.")}</p>
      <dl className="quality-counts">
        {([
          ["Processed rows", report.summary.processed], ["Automatically corrected rows", report.summary.automatically_corrected],
          ["Cells with critical issues", report.summary.blocking_cells], ["Unverified cells", report.summary.unverified_cells],
          ["Remaining issues", report.summary.issue_count], ["Rows approved by both judges", report.summary.judge_verified_units],
          ["Isolated service errors", report.summary.quarantined_transport],
        ] as [string, number][]).map(([name, value]) => <div key={name}><dt>{t(name)}</dt><dd>{value}</dd></div>)}
      </dl>
      {Object.entries(report.summary.judge_seats ?? {}).map(([seat, states]) => <p key={seat}>
        {t(seat === "1" ? "Judge 1" : "Judge 2")}: {Object.entries(states).map(([state, count]) => `${label(state)}: ${count}`).join(", ")}
      </p>)}
      {report.summary.project_issues.map((issue, index) => <p key={index}>{label(issue.kind)}: {issue.group_id}. {label(issue.reason ?? issue.kind)}</p>)}
    </>}
    <label htmlFor="quality-artifact-filter">{t("Issue type")}</label>
    <select id="quality-artifact-filter" value={query.category} onChange={event => setSelection({runId, category: event.target.value, page: 1})}>
      <option value="all">{t("All issues")}</option>
      <option value="blocking">{t("Critical issues")}</option>
      <option value="unverified">{t("Unverified cells")}</option>
      <option value="advisory">{t("Recommendations")}</option>
    </select>
    {report ? <>
      {report.issues.length ? <div className="problem-table-wrap" role="region" aria-label={t("Saved quality issues")} tabIndex={0}>
        <table>
          <caption>{t("Saved quality issues")}</caption>
          <thead><tr>{["Cell", "Key", "Source", "Translation", "Status", "Reason", "Judge 1", "Judge 2", "Action"].map(name => <th scope="col" key={name}>{t(name)}</th>)}</tr></thead>
          <tbody>{report.issues.map((issue, index) => <tr key={`${report.page}-${index}`} className={`quality-mark-${issue.category}`}>
            <th scope="row">{issue.file}<br /><span>{issue.sheet}!{columnName(issue.column)}{issue.row}</span></th>
            <td><span>{issue.key}</span><br />ID: {issue.unit_id}</td><td>{issue.source}</td><td>{issue.target}</td>
            <td>{label(issue.category)}{issue.category !== "unverified" && issue.unverified ? <p>{t("Verification incomplete")}</p> : null}</td>
            <td>{label(issue.reason)}</td><td>{label(issue.judge_1)}</td><td>{label(issue.judge_2)}</td><td>{label(issue.action)}</td>
          </tr>)}</tbody>
        </table>
      </div> : <p role="status">{query.category !== "all" ? t("No issues match the filter") :
        report.summary.issue_count === 0 && report.summary.project_issues.length === 0 && !report.summary.quarantined_transport
          ? t("No remaining issues") : t("See project issues and verification status")}</p>}
      <nav aria-label={t("Report pages")} className="button-row">
        <button type="button" disabled={query.page <= 1} onClick={() => setSelection({runId, category: query.category, page: query.page - 1})}>{t("Previous page")}</button>
        <span>{query.page} / {Math.max(1, Math.ceil(report.total / report.page_size))}</span>
        <button type="button" disabled={query.page * report.page_size >= report.total} onClick={() => setSelection({runId, category: query.category, page: query.page + 1})}>{t("Next page")}</button>
      </nav>
    </> : null}
  </section>;
}
