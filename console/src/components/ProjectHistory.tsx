import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { ProducerApi, ProducerRun } from "../api/client";
import { t } from "../i18n";

export function ProjectHistory({api, slug}: {api: ProducerApi; slug: string}) {
  const [runs, setRuns] = useState<ProducerRun[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    if (typeof api.getHistory !== "function") return;
    api.getHistory(slug).then(v => {if (active) setRuns(v.results);}).catch(() => {if (active) setError("Не удалось загрузить историю заданий.");});
    return () => {active = false;};
  },[api,slug]);
  return <section className="overview-card" aria-labelledby="history-heading">
    <h2 id="history-heading">История заданий и выгрузок</h2>
    {error ? <p role="alert">{error}</p> : null}
    {!runs.length && !error ? <p>Заданий пока нет.</p> : null}
    <ul>{runs.map(run => <li key={run.id}>
      <Link to={`/projects/${encodeURIComponent(slug)}/runs/${encodeURIComponent(run.id)}`}>{new Date(run.created).toLocaleString("ru-RU")} · {t(run.status)}</Link>
      {run.status === "completed" ? <> · <a href={`/api/producer/runs/${encodeURIComponent(run.id)}/export/?format=xlsx`}>Скачать эту версию</a></> : null}
    </li>)}</ul>
  </section>;
}
