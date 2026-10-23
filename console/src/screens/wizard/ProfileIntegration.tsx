import { useEffect, useId, useRef, useState } from "react";
import type { ProducerApi, ProducerProfile } from "../../api/client";
import { languageLabel, type LanguageOption } from "./languages";
import { t } from "../../i18n";
import { BdhcRuleValue, bdhcFieldLabel } from "./BdhcRulePreview";
import { ProfilePreparationForm } from "./ProfilePreparationForm";
import type { PreparationProps } from "./useProfilePreparation";

type ProfileIntegrationProps = {
  api: ProducerApi; slug: string; projectName: string; source: string | null; profile: ProducerProfile;
  targets: string[]; catalog?: LanguageOption[]; onApplied: (profile: ProducerProfile) => void;
} & Partial<Pick<PreparationProps,"uploadToken"|"uploadRevision"|"answers"|"onAnswers"|"onState">>;

export function ProfileIntegration(props: ProfileIntegrationProps) {
  if (props.uploadToken && props.uploadRevision && props.onAnswers && props.answers && typeof props.api.prepareProfile === "function") {
    return <ProfilePreparationForm {...props} uploadToken={props.uploadToken} uploadRevision={props.uploadRevision} onAnswers={props.onAnswers} answers={props.answers}/>;
  }
  return <LegacyProfileIntegration {...props}/>;
}

function instructionDefaults(projectName: string, source: string | null, targets: string[], catalog: LanguageOption[]) {
  const languageName = (code: string) => (name => name[0].toLocaleLowerCase("ru") + name.slice(1))(languageLabel(code, catalog));
  const sourceName = source ? languageName(source) : "";
  const from = /[иы]й$/.test(sourceName) ? sourceName.replace(/[иы]й$/, "ого") : `${t("языка")} ${sourceName}`;
  return {
    persona: projectName && source && targets.length
      ? t("Ты профессиональный локализатор видеоигр. Переводи тексты {project} с {source} на {targets}: интерфейс, диалоги и игровые описания. Учитывай контекст приключенческой игры с головоломками.")
        .replace(/\{(project|source|targets)\}/g, (_, key: "project" | "source" | "targets") => ({ project: projectName, source: from, targets: targets.map(languageName).join(", ") })[key])
      : "",
    style: t("Сохраняй атмосферу игры, юмор и драматические оттенки оригинала. Интерфейс переводи кратко и понятно, диалоги — естественно, с учётом характера персонажа. Соблюдай единообразие терминов. Не добавляй новых фактов. Сохраняй игровые теги, переменные, числа и разделители строк."),
  };
}

function refreshTemplateInserts(persona: string, defaultPersona: string) {
  // Older saved profiles have no template metadata. Recognize only the
  // template's translation sentence; leave the surrounding manual prose intact.
  const translationSentence = /Переводи тексты [^\r\n]+? с [^\r\n]+? на [^\r\n]+?: интерфейс, диалоги и игровые описания\./;
  const updated = defaultPersona.match(translationSentence)?.[0];
  return updated ? persona.replace(translationSentence, () => updated) : persona;
}

function LegacyProfileIntegration({ api, slug, projectName, source, profile, targets, catalog = [], onApplied }: {
  api: ProducerApi; slug: string; projectName: string; source: string | null; profile: ProducerProfile; targets: string[];
  catalog?: LanguageOption[];
  onApplied: (profile: ProducerProfile) => void;
}) {
  const personaLabel = useId();
  const styleLabel = useId();
  const [query, setQuery] = useState("");
  const [titles, setTitles] = useState<Array<{ name: string; ref: string }>>([]);
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState<Awaited<ReturnType<ProducerApi["previewBdhc"]>>>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const defaults = instructionDefaults(projectName, source, targets, catalog);
  const customPersona = useRef(Boolean(profile.generated?.persona));
  const customStyle = useRef(Boolean(profile.generated?.style));
  const [persona, setPersona] = useState(() => refreshTemplateInserts(profile.generated?.persona ?? defaults.persona, defaults.persona));
  const [style, setStyle] = useState(profile.generated?.style ?? defaults.style);
  const [instructions, setInstructions] = useState<Record<string, string>>(profile.generated?.language_instructions ?? {});
  useEffect(() => {
    setPersona(previous => refreshTemplateInserts(customPersona.current ? previous : profile.generated?.persona ?? defaults.persona, defaults.persona));
    if (!customStyle.current) setStyle(profile.generated?.style ?? defaults.style);
  }, [defaults.persona, defaults.style, profile.generated]);
  const [manualChecked,setManualChecked]=useState(false);
  const isManual=!!persona.trim() && refreshTemplateInserts(persona,defaults.persona)===persona && !/Переводи тексты [^\r\n]+? с [^\r\n]+? на [^\r\n]+?: интерфейс, диалоги и игровые описания\./.test(persona);
  useEffect(()=>{setManualChecked(false);},[defaults.persona,persona]);
  const perform = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (reason) {
      setError(reason instanceof Error ? t(reason.message) : t("Не удалось выполнить запрос. Можно продолжить с ручной анкетой."));
    } finally { setBusy(false); }
  };
  return <>
    <fieldset disabled={busy}>
      <legend>{t("Данные карточки БДХК")}</legend>
      <label className="field-stack"><span>{t("Название игры или ссылка")}</span><input value={query} maxLength={200} onChange={e => setQuery(e.target.value)} /></label>
      <button type="button" disabled={!query.trim()} onClick={() => perform(async () => {
        const response = await api.searchBdhc(query); setTitles(response.results); setSelected(""); setPreview(undefined);
        if (!response.results.length) setError(t("Карточка не найдена. Заполните анкету вручную."));
      })}>{t("Найти карточку")}</button>
      {titles.length ? <label className="field-stack"><span>{t("Выберите карточку")}</span><select value={selected} onChange={e => { setSelected(e.target.value); setPreview(undefined); }}><option value="">{t("Выберите")}</option>{titles.map(title => <option key={title.ref} value={title.ref}>{title.name}</option>)}</select></label> : null}
      <button type="button" disabled={!selected} onClick={() => perform(async () => setPreview(await api.previewBdhc(slug, selected, profile.revision)))}>{t("Показать предлагаемые изменения")}</button>
      {preview ? <div className="bdhc-preview">
        <ul>{preview.diff.map(item => <li key={item.field}><strong>{bdhcFieldLabel(item.field)}</strong>: <BdhcRuleValue field={item.field} value={item.before || "—"} /> → <BdhcRuleValue field={item.field} value={item.after} /></li>)}</ul>
        {preview.locale_rules_diff.map(item => <section className="bdhc-locale-rules" key={item.language} aria-label={`${t("Языковые правила")} ${item.language}`}>
          <h3>{t("Языковые правила")} {item.language}</h3>
          <h4>{t("До подтверждения")}</h4><BdhcRuleValue value={item.before} />
          <h4>{t("После подтверждения")}</h4><BdhcRuleValue value={item.after} />
        </section>)}
        <details className="bdhc-technical">
          <summary>{t("Служебные сведения карточки")}</summary>
          <dl className="bdhc-rule-fields">
            <div><dt>{t("Источник")}</dt><dd>{preview.provenance.title_id}</dd></div>
            <div><dt>{t("Версия схемы")}</dt><dd>{preview.provenance.schema_version ?? "—"}</dd></div>
            <div><dt>{t("Версия контекста")}</dt><dd>{preview.provenance.context_hash ?? "—"}</dd></div>
          </dl>
        </details>
        {preview.warnings.map((warning, i) => <p key={i}>{t(warning)}</p>)}
        <p>{t("Подтверждение заменит перечисленные поля сохранённой анкеты. Сохраните ручные правки перед загрузкой карточки.")}</p>
        <button type="button" onClick={() => perform(async () => { onApplied(await api.applyBdhc(slug, preview.preview_id, profile.revision)); setPreview(undefined); })}>{t("Подтвердить автозаполнение")}</button>
      </div> : null}
    </fieldset>
    <details>
      <summary>{t("Готовые инструкции переводчику — ручной ввод")}</summary>
      <p>{t("Роль и стиль заполнены шаблоном с названием проекта и выбранными языками. При необходимости измените текст. Сначала сохраните анкету, затем сохраните инструкции.")}</p>
      <fieldset disabled={busy}>
        <label className="field-stack"><span id={personaLabel}>{t("Роль переводчика")}</span><textarea aria-labelledby={personaLabel} required value={persona} maxLength={4000} onChange={e => { customPersona.current = true; setPersona(e.target.value); }} /></label>
        <label className="field-stack"><span id={styleLabel}>{t("Стиль перевода")}</span><textarea aria-labelledby={styleLabel} required value={style} maxLength={4000} onChange={e => { customStyle.current = true; setStyle(e.target.value); }} /></label>
        {targets.map(code => <label className="field-stack" key={code}><span>{t("Инструкции для языка")} {languageLabel(code, catalog)} ({code})</span><textarea maxLength={1000} value={instructions[code] ?? ""} onChange={e => setInstructions({ ...instructions, [code]: e.target.value })} /></label>)}
        {isManual ? <label className="check-row"><input type="checkbox" checked={manualChecked} onChange={event=>setManualChecked(event.target.checked)} />Проверил ручные инструкции: проект {projectName}, исходный язык {source}, языки перевода {targets.join(", ")}. Текст подходит этим языкам.</label> : null}
        <button type="button" disabled={isManual && !manualChecked || !persona.trim() || !style.trim() || profile.missing_required.length > 0} onClick={() => perform(async () => {
          onApplied(await api.setManualProfile(slug, profile.revision, { persona, style, language_instructions: Object.fromEntries(targets.filter(code => instructions[code]?.trim()).map(code => [code, instructions[code]])) }));
        })}>{t("Сохранить инструкции")}</button>
      </fieldset>
    </details>
    {error ? <p role="alert">{error}</p> : null}
    <p role="status">{profile.profile_state === "configured" ? t("Профиль перевода готов") : t("Профиль перевода ещё не готов")}</p>
  </>;
}
