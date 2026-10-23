import {useState} from "react";
import {useNavigate} from "react-router-dom";
import type {ProducerApi, VerificationPreview} from "../api/client";
import {t} from "../i18n";

export function ExistingVerification({api, runId}: {api:ProducerApi; runId:string}) {
  const [preview, setPreview] = useState<VerificationPreview>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  async function load(language?: string) {
    setBusy(true); setError("");
    try {setPreview(await api.previewVerification(runId, language));}
    catch {setPreview(undefined); setError(t("Verification requires a completed, fully translated accessible project and server support."));}
    finally {setBusy(false);}
  }
  async function start() {
    if (!preview) return;
    setBusy(true); setError("");
    try {const run = await api.startVerification(preview.project, preview);
      navigate(`/projects/${encodeURIComponent(preview.project)}/runs/${encodeURIComponent(run.id)}`);}
    catch {setError(t("Verification could not start. Refresh the estimate and try again."));}
    finally {setBusy(false);}
  }
  return <section className="run-panel" aria-label={t("Verify only — without changing translations")}>
    <button type="button" disabled={busy} onClick={() => void load()}>{t("Review completed translations without changes")}</button>
    {error ? <p role="alert">{error}</p> : null}
    {preview ? <>
      <label className="field-stack">{t("Quality check language")}<select value={preview.request.language} disabled={busy}
        onChange={event => void load(event.target.value)}>{preview.languages.map(code=><option key={code} value={code}>{code}</option>)}</select></label>
      <p>{t("Selected rows")}: {preview.selected}. {t("Initial judge calls")}: {preview.initial_calls ?? t("Unknown")}</p>
      <p>{t("Two-judge evaluation can have a separate cost. Verification generates no repair candidates and does not change translations, states or glossary.")}</p>
      <p>{t("Exact monetary cost is unknown. Saved responses are reused; unknown paid requests are not repeated.")}</p>
      <button className="primary-button" type="button" disabled={busy} onClick={() => void start()}>{t("Start verification")}</button>
    </> : null}
  </section>;
}
