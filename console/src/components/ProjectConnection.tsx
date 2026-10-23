import { useEffect, useState } from "react";
import type { ProducerApi, ProjectConnection as Connection } from "../api/client";
import { t } from "../i18n";

export function ProjectConnection({api, slug}: {api: ProducerApi; slug: string}) {
  const [value, setValue] = useState<Connection | null>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  useEffect(() => {
    let active = true;
    if (typeof api.getConnection !== "function") return;
    api.getConnection(slug).then(v => {if (active) setValue(v);}).catch(() => {if (active) setError(t("Connection could not be loaded."));});
    return () => {active = false;};
  }, [api, slug]);
  if (!value && !error) return null;
  return <section aria-labelledby="connection-heading" className="overview-card">
    <h2 id="connection-heading">{t("Weblate connection")}</h2>
    {error ? <p role="alert">{error}</p> : null}
    {value ? <>
      <p>{value.remote_slug} · {t(value.read_only ? "Read-only project. Writes are blocked." : "Wizard-owned project")}</p>
      <p>{t("Snapshot updated")}: {new Date(value.observed * 1000).toLocaleString("ru-RU")}</p>
      <p>{t("Reconstructed snapshot is available for download. Original workbook layout is not recovered.")}</p>
      <p>{t("Languages")}: {value.snapshot.languages.join(", ")}</p>
      <p>Настройки переводчика: {Object.keys(value.snapshot.profile ?? {}).length ? Object.keys(value.snapshot.profile ?? {}).join(", ") : "в проекте не заданы или недоступны через API"}.</p>
      <ul>{Object.values(value.snapshot.components).map(c => <li key={c.slug}>{c.name}: {c.source} → {c.languages.join(", ")} · {c.units.length} строк</li>)}</ul>
      {Object.values(value.snapshot.components).filter(c => c.glossary).map(c => <details key={c.slug}><summary>{t("Glossary")}: {c.name} · {c.units.length}</summary><ul>{c.units.slice(0,20).map((u,i) => <li key={i}>{u.source} — {Object.entries(u.targets).map(([code,text]) => `${code}: ${text}`).join("; ")}</li>)}</ul></details>)}
      {value.snapshot.issues.map((issue,i) => <p key={i} role="status">{issue.component}: {issue.detail}</p>)}
      <button type="button" disabled={pending} onClick={async () => {setPending(true); setError(""); try {setValue(await api.refreshConnection(slug));} catch {setError(t("Connection could not be loaded."));} finally {setPending(false);}}}>{t("Refresh remote snapshot")}</button>
    </> : null}
  </section>;
}
