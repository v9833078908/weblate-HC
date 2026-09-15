'use client'

import * as React from 'react'
import { Check, ArrowRight, ArrowLeft } from 'lucide-react'
import * as api from '@/src/api/client'
import { AppShell } from '@/components/console/AppShell'
import { LANG_PRESET, langName } from '@/components/console/upload-parts'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { RowsSheet } from '@/components/console/wizard/RowsSheet'
import { Step1Kit } from '@/components/console/wizard/Step1Kit'
import { Step2Source } from '@/components/console/wizard/Step2Source'
import { Step3Languages } from '@/components/console/wizard/Step3Languages'
import { Step4Structure } from '@/components/console/wizard/Step4Structure'
import { Step5Components } from '@/components/console/wizard/Step5Components'
import { Step6Profile } from '@/components/console/wizard/Step6Profile'
import { Step7Glossary } from '@/components/console/wizard/Step7Glossary'
import { Step8Review } from '@/components/console/wizard/Step8Review'

const STEP_NAMES = ['Лок-кит', 'Исходный язык', 'Языки', 'Структура кита', 'Компоненты', 'Профиль проекта', 'Глоссарий', 'Проверка и запуск']

const WizardCtx = React.createContext(null)
export const useWizard = () => React.useContext(WizardCtx)

function buildLanguages(upload) {
  const inKit = upload.columns.filter((c) => c.role === 'language').map((c) => ({
    header: c.header, code: c.language, fill: c.fill, ambiguous: c.ambiguous || null, low_fill: !!c.low_fill,
    include: !c.low_fill && !c.ambiguous ? true : !c.ambiguous, resolved_as: c.ambiguous ? null : c.language, source: 'kit',
  }))
  const kitCodes = new Set(inKit.map((l) => l.resolved_as || l.code))
  const preset = LANG_PRESET.filter((p) => !kitCodes.has(p.code)).map((p) => ({ header: null, code: p.code, fill: null, ambiguous: null, low_fill: false, include: false, resolved_as: p.code, source: 'preset' }))
  return [...inKit, ...preset]
}

export function WizardShell({ project, navigate }) {
  const slug = project.slug
  const [step, setStep] = React.useState(1)
  const [maxReached, setMaxReached] = React.useState(1)
  const [upload, setUpload] = React.useState(null)
  const [rows, setRows] = React.useState(null) // {set,value,title}
  const [submitting, setSubmitting] = React.useState(false)

  const [s, setS] = React.useState({
    fileName: '', uploadId: null,
    source_language: null, explanation_language: 'source', explanation_other: '',
    languages: null,
    idIsLanguage: null,
    duplicates_policy: 'quarantine',
    splitMode: 'single', split: { rule: [], residue: '', ambiguity: {} },
    profile: { bdhc_title_id: null, store_url: '', questionnaire: null, register_ui: null, register_dialogue: null, profanity: null, profanity_level: null, cjk_politeness: null, skipped_q1: false },
    glossary: { skipped: false, extractionId: null, selected: {}, disputed: {}, questions: {}, categories: { item: true, character: true, place: true, concept: true, button: false, phrase: false }, exceptionsApproved: {}, exceptionsRejected: {}, materials: { read_only: '', exact: '', forbidden: '' } },
  })
  const set = React.useCallback((patch) => setS((p) => ({ ...p, ...(typeof patch === 'function' ? patch(p) : patch) })), [])

  // restore draft from ?u=
  React.useEffect(() => {
    const u = new URLSearchParams(window.location.search).get('u')
    const st = new URLSearchParams(window.location.search).get('step')
    if (u) {
      api.getUpload(u).then((a) => {
        setUpload(a)
        set({ uploadId: a.id, fileName: a.file_name || '', source_language: a.source_language, languages: buildLanguages(a) })
        if (st) { setStep(Number(st)); setMaxReached(Number(st)) }
      }).catch(() => {})
    }
  }, []) // eslint-disable-line

  const patch = React.useCallback(async (body) => {
    const a = await api.patchUpload(s.uploadId, body)
    setUpload(a)
    return a
  }, [s.uploadId])

  const onParsed = (a) => {
    setUpload(a)
    set({ uploadId: a.id, languages: buildLanguages(a) })
    window.history.replaceState({}, '', `/projects/${slug}/localize?u=${a.id}&step=1`)
  }

  const goto = (n) => {
    setStep(n); setMaxReached((m) => Math.max(m, n))
    if (s.uploadId) window.history.replaceState({}, '', `/projects/${slug}/localize?u=${s.uploadId}&step=${n}`)
  }

  const cancel = () => {
    if (s.uploadId) api.getUpload(s.uploadId).then(() => {}).catch(() => {})
    navigate(`/projects/${slug}`)
  }

  const submit = async () => {
    setSubmitting(true)
    const langs = (s.languages || []).filter((l) => l.include).map((l) => l.resolved_as || l.code).filter((c) => c !== s.source_language)
    const run = await api.createLocalization(slug, {
      upload_id: s.uploadId, source_language: s.source_language, explanation_language: s.explanation_language,
      languages: langs, columns: upload?.columns, duplicates_policy: s.duplicates_policy,
      split: s.splitMode === 'split' ? s.split : undefined, profile: s.profile, glossary: { terms: s.glossary.selected },
    })
    setSubmitting(false)
    navigate(`/projects/${slug}/runs/${run.id}`)
  }

  // ---- can proceed ----
  const canProceed = React.useMemo(() => {
    if (step === 1) return !!upload && !upload.stop
    if (step === 2) return !!s.source_language && !upload?.source_conflict
    if (step === 3) {
      const langs = s.languages || []
      const ambiguousResolved = langs.filter((l) => l.ambiguous).every((l) => !!l.resolved_as || l.include === false)
      const idDecided = !upload?.columns.some((c) => c.header === 'Id') || s.idIsLanguage !== null
      const hasTarget = langs.some((l) => l.include && (l.resolved_as || l.code) !== s.source_language)
      const rareDecided = langs.filter((l) => l.low_fill).every((l) => l._decided === true || l.include === false || l.include === true)
      return ambiguousResolved && idDecided && hasTarget && rareDecided
    }
    if (step === 4) return upload?.gate?.ready
    if (step === 5) {
      if (s.splitMode === 'single') return true
      if (!s.split.residue) return false
      const undecided = Object.values(s.split.ambiguity).some((v) => !v)
      return !undecided && s.split.rule.length > 0
    }
    if (step === 6) {
      const cjkNeeded = (s.languages || []).some((l) => l.include && ['ja', 'ko'].includes(l.resolved_as || l.code))
      return s.profile.register_ui && s.profile.register_dialogue && s.profile.profanity && (!cjkNeeded || s.profile.cjk_politeness)
    }
    if (step === 7) return true // skippable
    return true
  }, [step, upload, s])

  const skipped = { 5: s.splitMode === 'single' && step > 5, 7: s.glossary.skipped }

  const ctx = { slug, navigate, s, set, upload, setUpload, patch, onParsed, openRows: setRows, goto }

  const StepComp = [null, Step1Kit, Step2Source, Step3Languages, Step4Structure, Step5Components, Step6Profile, Step7Glossary, Step8Review][step]

  return (
    <WizardCtx.Provider value={ctx}>
      <AppShell project={project} active="overview" navigate={navigate}
        breadcrumb={[{ label: 'Проекты', path: '/' }, { label: project.name, path: `/projects/${slug}` }, { label: 'Сделать локализацию' }]}>
        <div className="mb-6 flex items-center justify-between gap-3">
          <h1 className="text-heading-page text-foreground">Сделать локализацию</h1>
          <Button variant="ghost" className="h-9 rounded-sm" onClick={cancel}>Отмена</Button>
        </div>

        <div className="grid grid-cols-1 gap-8 md:grid-cols-[240px_1fr]">
          <aside className="md:sticky md:top-16 md:self-start">
            <ol className="space-y-1">
              {STEP_NAMES.map((name, i) => {
                const n = i + 1
                const done = n < step
                const cur = n === step
                const reachable = n <= maxReached
                const wasSkipped = (n === 5 && skipped[5]) || (n === 7 && skipped[7])
                return (
                  <li key={name}>
                    <button disabled={!reachable} onClick={() => reachable && goto(n)}
                      className={cn('flex w-full items-center gap-3 rounded-sm px-3 py-2 text-start text-body-sm transition-colors',
                        cur ? 'bg-accent font-semibold text-foreground' : reachable ? 'text-foreground hover:bg-muted' : 'cursor-not-allowed text-muted-foreground')}>
                      <span className={cn('inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-label-caps',
                        done ? 'bg-success text-success-foreground' : cur ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground')}>
                        {done ? <Check className="h-3.5 w-3.5" /> : n}
                      </span>
                      <span className="flex-1">{name}</span>
                      {wasSkipped && <span className="text-label-caps uppercase text-muted-foreground">пропущено</span>}
                    </button>
                  </li>
                )
              })}
            </ol>
          </aside>

          <div className="min-w-0">
            <StepComp />

            <div className="mt-8 flex items-center justify-between gap-3 border-t border-border pt-4">
              <div>{step > 1 && <Button variant="secondary" className="h-9 rounded-sm" onClick={() => goto(step - 1)}><ArrowLeft className="h-4 w-4" />Назад</Button>}</div>
              <div className="flex items-center gap-2">
                {step === 5 && <Button variant="ghost" className="h-9 rounded-sm" onClick={() => { set({ splitMode: 'single' }); goto(6) }}>Оставить один компонент</Button>}
                {step === 7 && <Button variant="ghost" className="h-9 rounded-sm" onClick={() => { set((p) => ({ glossary: { ...p.glossary, skipped: true } })); goto(8) }}>Пропустить</Button>}
                {step < 8 && <Button className="h-9 rounded-sm" disabled={!canProceed} onClick={() => goto(step + 1)}>Далее <ArrowRight className="h-4 w-4" /></Button>}
                {step === 8 && <Button className="h-9 rounded-sm" disabled={submitting} onClick={submit}>{submitting ? 'Запуск…' : 'Сделать локализацию'}</Button>}
              </div>
            </div>
          </div>
        </div>
      </AppShell>

      <RowsSheet uploadId={s.uploadId} req={rows} onClose={() => setRows(null)} />
    </WizardCtx.Provider>
  )
}
