import {useEffect, useId, useRef, useState} from "react";
import {t} from "../../i18n";
import {languageLabel, type LanguageOption} from "./languages";
import {useProfilePreparation, type PreparationProps} from "./useProfilePreparation";

export function ProfilePreparationForm(props: PreparationProps & {catalog?:LanguageOption[]}) {
  const state = useProfilePreparation(props);
  const errorRef = useRef<HTMLParagraphElement>(null);
  useEffect(()=>{if(state.error) errorRef.current?.focus();},[state.error]);
  const [query,setQuery] = useState("");
  const [instructionsOpen,setInstructionsOpen] = useState(false);
  const instructionsId = useId();
  const [showChanges,setShowChanges] = useState(false);
  const [replace,setReplace] = useState<string[]>([]);
  const valid = state.fields.persona.trim() && state.fields.style.trim() && props.targets.every(code=>state.fields.language_instructions[code]?.trim());
  const proposal = state.job?.proposal;
  const candidates = proposal ? ["persona","style",...props.targets.map(code=>"language_instructions."+code)] : [];
  const fieldValue = (key:string, value:typeof state.fields)=>key.startsWith("language_instructions.") ? value.language_instructions[key.slice(22)] ?? "" : value[key as "persona"|"style"];
  const fieldLabel = (key:string)=>key === "persona" ? t("Роль переводчика") : key === "style" ? t("Стиль перевода") : `${t("Инструкции для языка")} ${languageLabel(key.slice(22),props.catalog ?? [])} (${key.slice(22)})`;
  return <>
    <fieldset>
      <legend>{t("Данные карточки БДХК")}</legend>
      <label className="field-stack"><span>{t("Название игры или ссылка")}</span><input value={query} maxLength={200} onChange={e=>setQuery(e.target.value)} onKeyDown={e=>{if(e.key === "Enter") {e.preventDefault();if(query.trim() && !state.busy) void state.resolve({query});}}}/></label>
      <button type="button" disabled={!query.trim() || state.busy} onClick={()=>state.resolve({query})}>{t("Найти карточку")}</button>
      {state.resolution?.state === "choice_required" ? <label className="field-stack"><span>{t("Выберите карточку")}</span><select defaultValue="" disabled={state.busy} onChange={e=>{if(e.target.value) void state.resolve({title_id:e.target.value});}}><option value="">{t("Выберите")}</option>{state.resolution.matches.map((item,i)=><option key={item.ref+":"+i} value={item.ref}>{item.name}</option>)}</select></label> : null}
      {state.resolution?.title ? <p>{t("Выбрана игра")}: <strong>{state.resolution.title}</strong></p> : null}
      {state.resolution?.state === "not_found" ? <p>{t("Карточка не найдена. Укажите другое название или заполните контекст вручную.")}</p> : null}
    </fieldset>
    <button type="button" className="instructions-toggle" aria-expanded={instructionsOpen} aria-controls={instructionsId} onClick={()=>setInstructionsOpen(open=>!open)}>
      <span>{t("Готовые инструкции переводчику - если не уверены не меняйте")}</span>
      <span aria-hidden="true">{instructionsOpen ? "▴" : "▾"}</span>
    </button>
    <div id={instructionsId} hidden={!instructionsOpen}>
      <p role="status" aria-live="polite">{state.busy ? t("Подготавливаем инструкции…") : state.dirty ? t("Есть несохранённые изменения") : state.saved ? state.manualSaved ? t("Инструкции сохранены") : t("Инструкции сохранены автоматически") : t("Заполните инструкции или дождитесь подготовки")}</p>
      {state.job?.generation_mode === "template" ? <p>{t("Использован базовый шаблон. Агент LLM не подключён.")}</p> : state.job?.generation_mode === "agent" ? <p>{t("Инструкции подготовлены агентом по данным игры")}</p> : null}
      {state.reviewRequired ? <p role="alert">{t("Сохранённая ручная роль требует проверки для текущей игры и языков.")}</p> : null}
      <label className="field-stack"><span>{t("Роль переводчика")}</span><textarea required maxLength={4000} value={state.fields.persona} onChange={e=>state.edit("persona",e.target.value)}/></label>
      <label className="field-stack"><span>{t("Стиль перевода")}</span><textarea required maxLength={4000} value={state.fields.style} onChange={e=>state.edit("style",e.target.value)}/></label>
      {props.targets.map(code=><label className="field-stack" key={code}><span>{fieldLabel("language_instructions."+code)}</span><textarea required maxLength={1000} value={state.fields.language_instructions[code] ?? ""} onChange={e=>state.edit("language_instructions."+code,e.target.value)}/></label>)}
      <button type="button" disabled={state.busy || !state.dirty || !valid} onClick={()=>state.save()}>{t("Сохранить инструкции")}</button>
      {state.reviewRequired ? <button type="button" disabled={state.busy || !valid} onClick={()=>state.save(true)}>{t("Подтвердить актуальность ручных инструкций")}</button> : null}
      <button type="button" disabled={state.busy} onClick={()=>{setShowChanges(true);setReplace([]);void state.resolve(query.trim() ? {query} : undefined);}}>{t("Подготовить заново")}</button>
      {showChanges && proposal && !state.busy ? <fieldset><legend>{t("Выберите поля для замены")}</legend>{candidates.filter(key=>fieldValue(key,proposal) !== fieldValue(key,state.fields)).map(key=><label key={key} className="field-stack"><span><input type="checkbox" checked={replace.includes(key)} onChange={e=>setReplace(old=>e.target.checked ? [...old,key] : old.filter(v=>v !== key))}/>{fieldLabel(key)}</span><span>{fieldValue(key,state.fields)} → {fieldValue(key,proposal)}</span></label>)}<button type="button" disabled={!replace.length} onClick={()=>{for(const key of replace) state.edit(key,fieldValue(key,proposal));setShowChanges(false);}}>{t("Применить выбранные изменения")}</button></fieldset> : null}
    </div>
    {state.resolution?.warnings.map((warning,i)=><p key={i}>{t(warning)}</p>)}
    {state.job?.warnings.filter(w=>w !== "profile_agent_not_configured").map((warning,i)=><p key={i}>{t(warning)}</p>)}
    {state.error ? <p role="alert" tabIndex={-1} ref={errorRef}>{state.error}</p> : null}
  </>;
}
