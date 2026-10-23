import { useEffect, useState, useRef } from "react";
import type { ProducerApi, ChangePlan, ProjectConnection } from "../api/client";
import { t } from "../i18n";

export function ChangeReview({api, slug, uploadId, revision, sheets, glossary, onPlan}: {api: ProducerApi; slug: string; uploadId: string; revision: string; sheets: Array<{file: string; sheet: string}>; glossary?: {token:string;revision:string}; onPlan: (plan: ChangePlan | null) => void}) {
  const generation=useRef(0);
  const [link, setLink] = useState<ProjectConnection | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<ChangePlan | null>(null);
  const [oldEmpty, setOldEmpty] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    if (typeof api.getConnection !== "function") return;
    api.getConnection(slug).then(value => {if (active) {setLink(value); setMapping(value?.component_map ?? {});}}).catch(() => {if (active) setError(t("Connection could not be loaded."));});
    return () => {active = false;};
  }, [api, slug]);
  useEffect(() => {generation.current++; setPlan(null); onPlan(null);}, [revision, uploadId, glossary?.revision, onPlan]);
  if (!link) return error ? <p role="alert">{error}</p> : null;
  const invalidate = () => {generation.current++; setPlan(null); onPlan(null);};
  return <section aria-labelledby="changes-heading">
    <h3 id="changes-heading">{t("Compare with Weblate")}</h3>
    {link.read_only ? <p role="status">{t("Existing project is protected. Comparison does not change Weblate.")}</p> : null}
    <p>{t("Languages already in Weblate")}: {link.snapshot.languages?.join(", ")}</p>
    {sheets.map(sheet => {const key = sheet.file + "\u001f" + sheet.sheet; return <label className="field-stack" key={key}><span>{sheet.sheet} · {sheet.file}</span><select value={mapping[key] ?? ""} onChange={event => {setMapping({...mapping, [key]: event.target.value}); invalidate();}}>
      <option value="">{t("Select Weblate component")}</option>
      {Object.entries(link.snapshot.components).filter(([, c]) => !c.glossary).map(([id, c]) => <option key={id} value={id}>{c.name} · {id}</option>)}
    </select></label>;})}
    <label className="check-row"><input type="checkbox" checked={oldEmpty} onChange={event => {setOldEmpty(event.target.checked); invalidate();}} />{t("Include old empty strings explicitly")}</label>
    <button type="button" disabled={pending || !sheets.length} onClick={async () => {setPending(true); setError(""); invalidate(); try {const at=generation.current; const result=await api.previewChanges(slug, uploadId, revision, mapping, oldEmpty, glossary); if(at===generation.current) setPlan(result); } catch (reason) {setError(reason instanceof Error ? reason.message : t("Comparison failed."));} finally {setPending(false);}}}>{t("Calculate changes")}</button>
    {error ? <p role="alert">{error}</p> : null}
    {plan ? <>
      {plan.components.map((c,i) => <div key={i}><h4>{c.sheet}</h4>{c.create_component ? <p>{t("Новая группа исходного языка")}: {c.source} · {c.remote_id}</p> : null}
        <p>{t("New strings")}: {c.new.length} · {t("Unchanged strings")}: {c.unchanged.length} · {t("Changed source")}: {c.changed.length} · {t("Retained in Weblate")}: {c.retained.length}</p>
        {Object.entries(c.translate).map(([code,keys])=><p key={code}>{code}: {t("машинный перевод")} ({keys.length}) · {t("Готовые переводы для загрузки")} ({Object.keys(c.seed?.[code] ?? {}).length})</p>)}
      </div>)}
      {plan.glossary_preview ? <p>{t("Новый глоссарий")}: {plan.glossary_preview.components.reduce((count,table)=>count+table.units.length,0)} {t("терминов")}</p> : null}
      <details key={`${plan.id}:${plan.revision}`}>
        <summary>{t("Подробный список изменений")}</summary>
      {plan.profile_preview ? <details><summary>Инструкции переводчику: до и после подтверждения</summary>
        <p>Сервис: {plan.profile_preview.engine}. Инструкции невыбранных языков сохраняются.</p>
        <p style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>До: {JSON.stringify(plan.profile_preview.before,null,2)}</p>
        <p style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>После: {JSON.stringify(plan.profile_preview.after,null,2)}</p>
      </details> : null}
      {plan.components.map((c,i) => <div key={i}><h4>{c.sheet}</h4>{c.create_component ? <p>{t("Новая группа исходного языка")}: {c.source} · {c.remote_id}</p> : null}
        <p>{t("New strings")}: {c.new.length} · {t("Unchanged strings")}: {c.unchanged.length} · {t("Changed source")}: {c.changed.length} · {t("Retained in Weblate")}: {c.retained.length}</p>
        <ul>{c.new.map(unit=><li key={unit.key}>Добавление: {unit.key} — {unit.source}{unit.explanation ? ` · Контекст: ${unit.explanation}` : ""}</li>)}</ul>
        <p>Сохраняются без удаления: {c.retained.join(", ") || "—"}</p>
        <p>Без изменения исходного текста: {c.unchanged.join(", ") || "—"}</p>
        <p>Изменённый исходный текст (заблокировано): {c.changed.join(", ") || "—"}</p>
        {Object.entries(c.translate).map(([code,keys])=><div key={code}><p>{code}: машинный перевод ({keys.length}): {keys.join(", ") || "—"}</p>
          <p>Старые пустые строки вне выбранного объёма: {c.old_empty[code]?.join(", ") || "—"}</p>
          <p>Готовые переводы для загрузки ({Object.keys(c.seed?.[code] ?? {}).length}):</p><ul>{Object.entries(c.seed?.[code] ?? {}).map(([key,target])=><li key={key}>{key} → {target}</li>)}</ul>
        </div>)}
      </div>)}
      {plan.glossary_preview ? <section aria-label="Новый глоссарий"><h4>Новый глоссарий — отдельная версия компонента</h4><p>Существующие глоссарии остаются без изменений. Исходный язык: {plan.glossary_preview.source}.</p>{plan.glossary_preview.components.map(table=><div key={table.sheet}><p>{table.sheet}: {table.units.length} терминов</p><ul>{table.units.map(unit=><li key={unit.key}>{unit.key}: {Object.entries(unit.values).map(([code,text])=>`${code}: ${text}`).join("; ")}</li>)}</ul></div>)}</section> : null}
      </details>
      {plan.blocked.length ? <><p>{t("Uncheck the affected sheets on the Files step and calculate again.")}</p><ul>{plan.blocked.map((b,i) => <li key={i}>{b.sheet}: {b.reason}</li>)}</ul></> : null}
      <button type="button" disabled={pending || !!plan.blocked.length || plan.read_only || plan.confirmed} onClick={async () => {setPending(true); try {const at=generation.current; const confirmed = await api.confirmChanges(slug, plan); if(at===generation.current) {setPlan(confirmed); onPlan(confirmed);}} catch (reason) {setError(reason instanceof Error ? reason.message : t("Comparison failed."));} finally {setPending(false);}}}>{t(plan.confirmed ? "Changes confirmed" : "Confirm exact changes")}</button>
    </> : null}
  </section>;
}
