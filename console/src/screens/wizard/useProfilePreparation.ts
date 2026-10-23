import {useCallback, useEffect, useRef, useState} from "react";
import type {ContextResolution, ProducerApi, ProducerProfile, ProducerProfileAnswers, ProfilePreparation} from "../../api/client";
import {t} from "../../i18n";

type Instructions = NonNullable<ProducerProfile["generated"]>;
export interface PreparationProps {
  api: ProducerApi; slug: string; projectName: string; profile: ProducerProfile;
  uploadToken: string; uploadRevision: string; source: string | null; targets: string[];
  answers: ProducerProfileAnswers;
  onAnswers: (answers: ProducerProfileAnswers)=>void;
  onApplied: (profile: ProducerProfile)=>void;
  onState?: (state: {dirty:boolean;ready:boolean;busy:boolean})=>void;
}
const empty: Instructions = {persona:"",style:"",language_instructions:{}};
function answerChanges(value: ProducerProfileAnswers, baseline: ProducerProfileAnswers) {
  return Object.keys(value).filter(key=>(value[key as keyof ProducerProfileAnswers] ?? "") !== (baseline[key as keyof ProducerProfileAnswers] ?? ""));
}
function jobError(job: ProfilePreparation) {
  if (job.state === "unknown") return t("Результат запроса неизвестен. Повторный платный запрос автоматически не отправляется.");
  if (job.state === "stale") return t("Данные изменились во время подготовки. Проверьте текущую версию и подготовьте инструкции снова.");
  return t("Не удалось подготовить инструкции. Сохранённые тексты оставлены; можно заполнить поля вручную.");
}

export function useProfilePreparation(props: PreparationProps) {
  const latest = useRef(props); latest.current = props;
  const currentProfile = useRef(props.profile);
  const baselineAnswers = useRef(props.profile.answers);
  const dirtyFields = useRef(new Set<string>());
  const [fields,setFields] = useState<Instructions>(props.profile.generated ?? empty);
  const fieldsRef = useRef(fields); fieldsRef.current = fields;
  const [resolution,setResolution] = useState<ContextResolution>();
  const [job,setJob] = useState<ProfilePreparation>();
  const [busy,setBusy] = useState(false);
  const [saved,setSaved] = useState(false);
  const [manualSaved,setManualSaved] = useState(false);
  const [error,setError] = useState("");
  const epoch = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const pending = useRef(false);
  const snapshot = useRef<string | null>(null);
  const dirtyAnswers = answerChanges(props.answers,baselineAnswers.current);
  const dirty = dirtyFields.current.size > 0 || dirtyAnswers.length > 0;
  const reviewRequired = (currentProfile.current.needs_review_fields ?? []).length > 0;
  useEffect(()=>{latest.current.onState?.({dirty,ready:saved && !reviewRequired,busy});},[dirty,saved,busy,reviewRequired]);
  useEffect(()=>{
    const before = (event: BeforeUnloadEvent)=>{if (dirty) {event.preventDefault();event.returnValue="";}};
    window.addEventListener("beforeunload",before);
    return ()=>window.removeEventListener("beforeunload",before);
  },[dirty]);

  const edit = (field: string, value: string) => {
    dirtyFields.current.add(field);
    setFields(previous=>field.startsWith("language_instructions.")
      ? {...previous,language_instructions:{...previous.language_instructions,[field.slice(22)]:value}}
      : {...previous,[field]:value});
  };

  const accept = async (prepared: ProfilePreparation, token: number) => {
    if (token !== epoch.current) return;
    setJob(prepared);
    if (prepared.state === "queued" || prepared.state === "running") {
      timer.current = setTimeout(()=>{
        const p = latest.current;
        p.api.getProfilePreparation(p.slug,prepared.job_id).then(value=>accept(value,token)).catch(reason=>{
          if (token === epoch.current) {setError(reason instanceof Error ? t(reason.message) : t("Не удалось прочитать состояние подготовки"));setBusy(false);pending.current=false;}
        });
      },1000);
      return;
    }
    if (prepared.state !== "ready" || !prepared.auto_saved) {
      setError(jobError(prepared));setBusy(false);pending.current=false;return;
    }
    const p = latest.current;
    const profile = await p.api.getProfile(p.slug);
    if (token !== epoch.current) return;
    if (profile.revision !== prepared.profile_revision || !profile.generated) {
      setError(t("Сохранённый профиль уже изменился. Откройте текущую версию перед продолжением."));setBusy(false);pending.current=false;return;
    }
    const edits = answerChanges(latest.current.answers,baselineAnswers.current);
    const mergedAnswers = {...profile.answers};
    for (const key of edits) Object.assign(mergedAnswers,{[key]:latest.current.answers[key as keyof ProducerProfileAnswers]});
    baselineAnswers.current = profile.answers;
    currentProfile.current = profile;
    const next = profile.generated;
    setFields(previous=>({
      persona:dirtyFields.current.has("persona") ? previous.persona : next.persona,
      style:dirtyFields.current.has("style") ? previous.style : next.style,
      language_instructions:{...previous.language_instructions,...Object.fromEntries(Object.entries(next.language_instructions).filter(([code])=>!dirtyFields.current.has("language_instructions."+code)))},
    }));
    p.onAnswers(mergedAnswers);p.onApplied(profile);setSaved(true);setManualSaved(false);setBusy(false);pending.current=false;
  };

  const resolve = useCallback(async (selection?:{query?:string;title_id?:string})=>{
    const p = latest.current;
    const token = ++epoch.current;
    if (timer.current) clearTimeout(timer.current);
    setBusy(true);pending.current=true;setSaved(false);setError("");snapshot.current=null;
    try {
      const profile = await p.api.getProfile(p.slug);
      if (token !== epoch.current) return;
      currentProfile.current = profile;
      let context: ContextResolution;
      try {
        context = await p.api.resolveContext(p.slug,{revision:profile.revision,upload_token:p.uploadToken,...selection});
      } catch (reason) {
        if (token !== epoch.current) return;
        setError(reason instanceof Error ? t(reason.message) : t("Карточка недоступна. Используются базовые правила."));
        context = {state:"not_found",matches:[],snapshot_id:null,title:null,answers:{},locale_rules:{},warnings:[]};
      }
      if (token !== epoch.current) return;
      setResolution(context);
      if (context.state === "choice_required") {setBusy(false);pending.current=false;return;}
      snapshot.current = context.snapshot_id;
      if (!p.source || !p.targets.length) {setBusy(false);pending.current=false;return;}
      const prepared = await p.api.prepareProfile(p.slug,{revision:profile.revision,upload_token:p.uploadToken,
        upload_revision:p.uploadRevision,context_snapshot_id:context.snapshot_id,answers:latest.current.answers,
        edited_fields:answerChanges(latest.current.answers,baselineAnswers.current).map(key=>"answers."+key)});
      await accept(prepared,token);
    } catch (reason) {
      if (token === epoch.current) {setError(reason instanceof Error ? t(reason.message) : t("Не удалось подготовить инструкции"));setBusy(false);pending.current=false;}
    }
  },[]);

  const targetKey = props.targets.join(",");
  useEffect(()=>{
    void resolve();
    return ()=>{++epoch.current;if(timer.current) clearTimeout(timer.current);};
  },[resolve,props.slug,props.uploadToken,props.uploadRevision,props.source,targetKey,props.projectName]);

  const save = async (confirmReview=false)=>{
    const p = latest.current;
    const token = epoch.current;
    setBusy(true);setError("");
    const edited = [...dirtyFields.current,...answerChanges(p.answers,baselineAnswers.current).map(key=>"answers."+key),...(confirmReview ? ["persona"] : [])];
    try {
      const response = await p.api.commitProfile(p.slug,{revision:currentProfile.current.revision,upload_token:p.uploadToken,
        upload_revision:p.uploadRevision,context_snapshot_id:snapshot.current,job_id:job?.job_id,
        answers:p.answers,generated:{...fieldsRef.current,language_instructions:Object.fromEntries(p.targets.map(code=>[code,fieldsRef.current.language_instructions[code] ?? ""]))},edited_fields:edited});
      if (token !== epoch.current) return;
      dirtyFields.current.clear();baselineAnswers.current=response.answers;currentProfile.current=response;
      setFields(response.generated ?? fieldsRef.current);p.onApplied(response);p.onAnswers(response.answers);setSaved(true);setManualSaved(true);
    } catch(reason) {
      if(token === epoch.current) setError(reason instanceof Error ? t(reason.message) : t("Не удалось сохранить инструкции"));
    } finally {if(token === epoch.current) setBusy(false);}
  };

  return {fields,edit,resolution,job,busy,dirty,saved,manualSaved,error,resolve,save,reviewRequired};
}
