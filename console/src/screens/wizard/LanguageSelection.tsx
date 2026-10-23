import { useEffect, useId, useRef, useState } from "react";
import { t } from "../../i18n";
import { codeKey, languageCode, languageLabel, recommendedLanguages, resolveLanguageInput, supportedLanguage, uniqueLanguageCodes, type LanguageOption } from "./languages";

interface LanguageSelectionProps {
  source: string | null;
  targets: string[];
  kitCodes: string[];
  remoteCodes: string[];
  catalog: LanguageOption[];
  catalogError?: string;
  busy: boolean;
  showSource?: boolean;
  showTargets?: boolean;
  onSourceChange: (code: string | null) => void;
  onTargetsChange: (codes: string[]) => void;
}

export function LanguageSelection({source, targets, kitCodes, remoteCodes, catalog, catalogError, busy, showSource = true, showTargets = true, onSourceChange, onTargetsChange}: LanguageSelectionProps) {
  const id = useId();
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [candidates, setCandidates] = useState<LanguageOption[]>([]);
  const feedback = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error || notice || candidates.length) feedback.current?.scrollIntoView?.({block: "nearest"});
  }, [error, notice, candidates]);
  const kit = uniqueLanguageCodes(kitCodes, catalog);
  const selected = uniqueLanguageCodes(targets, catalog).filter(code => codeKey(code) !== codeKey(source ?? ""));
  const kitSet = new Set(kit.map(codeKey));
  const remoteSet = new Set(remoteCodes.map(codeKey));
  const extra = uniqueLanguageCodes([...recommendedLanguages.map(row => row.code), ...remoteCodes, ...targets], catalog)
    .filter(code => !kitSet.has(codeKey(code)) && codeKey(code) !== codeKey(source ?? ""));
  const choose = (code: string) => {
    if (codeKey(code) === codeKey(source ?? "")) {setError(t("Этот язык выбран как язык, с которого переводить.")); return;}
    if (!supportedLanguage(code)) {setError(t("Этот вариант языка пока не поддерживается форматом лок-кита.")); return;}
    onTargetsChange(uniqueLanguageCodes([...selected, code], catalog));
    const alreadySelected = selected.some(row => codeKey(row) === codeKey(code));
    setNotice(`${alreadySelected ? t("Язык уже выбран:") : t("Добавлен и выбран:")} ${languageLabel(code, catalog)} (${code}).`);
    setInput(""); setError(""); setCandidates([]);
  };
  const add = () => {
    setNotice("");
    const matches = resolveLanguageInput(input, catalog);
    setCandidates(matches.length > 1 ? matches : []);
    if (matches.length === 1) choose(matches[0].code);
    else setError(matches.length ? t("Найдено несколько вариантов. Выберите нужный язык.") : t("Не удалось распознать язык. Введите полное название или код Weblate."));
  };
  const cards = (codes: string[], prefix: string) => codes.map((code, index) => {
    const isSource = codeKey(code) === codeKey(source ?? "");
    const exists = remoteSet.has(codeKey(code));
    const verified = catalog.some(row => codeKey(row.code) === codeKey(code));
    const checked = selected.some(row => codeKey(row) === codeKey(code));
    const descriptionId = `${id}-${prefix}-${index}`;
    return <label className={`language-choice${checked ? " selected" : ""}`} key={codeKey(code)}>
      <input type="checkbox" checked={checked} disabled={busy || !source || isSource || (!verified && !kitSet.has(codeKey(code)) && !checked) || !supportedLanguage(code)} aria-labelledby={`${descriptionId}-label`} aria-describedby={descriptionId} onChange={event => onTargetsChange(event.target.checked ? [...selected, code] : selected.filter(row => codeKey(row) !== codeKey(code)))}/>
      <span><span id={`${descriptionId}-label`} className="language-choice-name">{languageLabel(code, catalog)} <span className="language-code">({code})</span></span>
        <small id={descriptionId}>{isSource ? t("Язык, с которого переводить") : exists ? t("Уже есть в проекте Weblate") : kitSet.has(codeKey(code)) ? t("Уже есть в лок-ките") : t("Новый язык перевода")}</small>
      </span>
    </label>;
  });
  return <>
    {showSource ? <label className="field-stack"><span>{t("Язык, с которого переводить")}</span><select disabled={busy || !kit.length} value={source ? languageCode(source, catalog) : ""} onChange={event => {
      const value = event.target.value || null;
      onSourceChange(value);
      onTargetsChange(selected.filter(code => codeKey(code) !== codeKey(value ?? "")));
      setError(""); setNotice(""); setCandidates([]);
    }}>
      <option value="">{t("Выберите язык, с которого переводить")}</option>
      {uniqueLanguageCodes([...kit, ...(kit.some(code => ["ru", "en"].includes(code)) ? catalog.filter(row => supportedLanguage(row.code)).map(row => row.code) : []), ...(source ? [source] : [])], catalog).map(code => <option key={codeKey(code)} value={code}>{languageLabel(code, catalog)} ({code})</option>)}
    </select></label> : null}
    {showSource && kit.some(code => ["ru", "en"].includes(code)) ? <p className="helper-note">{t("При отсутствии текста на выбранном языке в этой строке используется русский, затем английский. Заявленный язык остаётся выбранным.")}</p> : null}
    {showTargets ? <>
      <div className="language-columns">
        <fieldset aria-describedby={`${id}-kit-help`}><legend>{t("Языки, уже присутствующие в лок-ките")}</legend><p id={`${id}-kit-help`}>{t("Эти языки уже присутствуют в выбранных листах лок-кита. Наличие языка не означает, что все строки переведены. Выберите галочками языки для обработки.")}</p><div className="language-choices">{cards(kit, "kit")}</div></fieldset>
        <fieldset><legend>{t("Добавить языки перевода")}</legend><p>{t("Выберите языки, на которые нужно перевести. Языки лок-кита здесь не повторяются.")}</p><div className="language-choices">{cards(extra, "extra")}</div></fieldset>
      </div>
      <div ref={feedback}>
        {catalogError || error ? <p role="alert">{catalogError || error}</p> : null}
        {notice ? <p role="status" aria-label={t("Добавление языка")}>{notice}</p> : null}
        {candidates.length ? <div className="button-row">{candidates.map(row => <button key={row.code} type="button" disabled={busy} onClick={() => choose(row.code)}>{languageLabel(row.code, catalog)} ({row.code})</button>)}</div> : null}
      </div>
      <div className="custom-language">
        <label className="field-stack"><span id={`${id}-input-label`}>{t("Добавить свой язык")}</span><input disabled={busy || !source || !catalog.length} value={input} maxLength={200} aria-labelledby={`${id}-input-label`} aria-describedby={`${id}-input-help`} onChange={event => {setInput(event.target.value); setError(""); setNotice(""); setCandidates([]);}} onKeyDown={event => {if (event.key === "Enter") {event.preventDefault(); add();}}}/><small id={`${id}-input-help`}>{t("Введите название по-русски, по-английски или код языка. Нажмите Enter или «Добавить».")}</small></label>
        <button type="button" disabled={busy || !source || !catalog.length || !input.trim()} onClick={add}>{t("Добавить")}</button>
      </div>
    </> : null}
    {!showTargets && catalogError ? <p role="alert">{catalogError}</p> : null}
  </>;
}
