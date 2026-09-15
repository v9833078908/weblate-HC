'use client'

import * as React from 'react'
import { Check, ArrowRight, ArrowLeft, Search, Info, Plus, BookText } from 'lucide-react'
import * as api from '@/src/api/client'
import { AppShell } from '@/components/console/AppShell'
import { WeblateLink } from '@/components/console/primitives'
import { DropZone, LocKitPreview, LANG_PRESET } from '@/components/console/upload-parts'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'

const STEPS = ['Лок-кит', 'Языки', 'Профиль проекта', 'Глоссарий', 'Проверка']

function Rail({ step }) {
  return (
    <ol className="space-y-1">
      {STEPS.map((s, i) => {
        const n = i + 1
        const done = n < step
        const cur = n === step
        return (
          <li key={s} className={cn('flex items-center gap-3 rounded-sm px-3 py-2 text-body-sm', cur ? 'bg-accent font-semibold text-foreground' : 'text-muted-foreground')}>
            <span className={cn('inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-label-caps', done ? 'bg-success text-success-foreground' : cur ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground')}>
              {done ? <Check className="h-3.5 w-3.5" /> : n}
            </span>
            {s}
          </li>
        )
      })}
    </ol>
  )
}

const QUESTIONS = [
  { id: 'genre', label: 'Жанр' },
  { id: 'setting', label: 'Сеттинг' },
  { id: 'tone', label: 'Тон' },
  { id: 'audience', label: 'Аудитория / возраст' },
  { id: 'address', label: 'Обращение к игроку («ты» / «вы»)' },
  { id: 'lang_notes', label: 'Особенности по языкам' },
  { id: 'banned', label: 'Запрещённые слова' },
]

export function WizardScreen({ project, navigate }) {
  const slug = project.slug
  const [step, setStep] = React.useState(1)

  // step 1
  const [parsing, setParsing] = React.useState(false)
  const [fileName, setFileName] = React.useState('')
  const [preview, setPreview] = React.useState(null)

  // step 2
  const [langs, setLangs] = React.useState(() => Object.fromEntries(LANG_PRESET.map((l) => [l.code, true])))

  // step 3
  const [q, setQ] = React.useState('')
  const [titles, setTitles] = React.useState([])
  const [profileMode, setProfileMode] = React.useState(null) // 'bdhc'|'questionnaire'|'default'
  const [bdhc, setBdhc] = React.useState(null)
  const [answers, setAnswers] = React.useState({})

  // step 4
  const [candidates, setCandidates] = React.useState([])
  const [added, setAdded] = React.useState({})

  const [committing, setCommitting] = React.useState(false)

  const pick = async (name) => {
    setFileName(name); setParsing(true)
    const p = await api.createUpload(slug, { kind: 'loc-kit' })
    setPreview(p); setParsing(false)
    // pre-select languages present in the kit
    setLangs((prev) => { const n = { ...prev }; p.languages.forEach((l) => { if (n[l.code] !== undefined) n[l.code] = true }); return n })
  }

  React.useEffect(() => { if (step === 3) api.getBdhcTitles(q).then(setTitles) }, [step, q])
  React.useEffect(() => { if (step === 4 && preview) api.getGlossaryCandidates(slug, preview.id).then(setCandidates) }, [step, preview, slug])

  const inKit = React.useMemo(() => new Set((preview?.languages || []).map((l) => l.code)), [preview])

  const selectBdhc = async (t) => {
    setBdhc(t); setProfileMode('bdhc')
    const prof = await api.getBdhcProfile(t.id)
    setBdhc({ ...t, ...prof })
    if (!prof.brief || !prof.voice) setProfileMode('questionnaire')
  }

  const commit = async () => {
    setCommitting(true)
    const chosen = Object.keys(langs).filter((k) => langs[k])
    const profile = profileMode === 'bdhc' ? { bdhc_title_id: bdhc.id } : profileMode === 'questionnaire' ? { questionnaire: answers } : { default: true }
    const terms = candidates.filter((c) => added[c.term]).map((c) => ({ source: c.term, note: c.why, targets: c.suggested }))
    const run = await api.createLocalization(slug, { languages: chosen, profile, kit_upload_id: preview?.id, glossary_terms: terms })
    setCommitting(false)
    navigate(`/projects/${slug}/runs/${run.id}`)
  }

  const canNext = step === 1 ? !!preview : true

  return (
    <AppShell project={project} active="overview" navigate={navigate}
      breadcrumb={[{ label: 'Проекты', path: '/' }, { label: project.name, path: `/projects/${slug}` }, { label: 'Сделать локализацию' }]}>
      <div className="mb-6 flex items-center justify-between gap-3">
        <h1 className="text-heading-page text-foreground">Сделать локализацию</h1>
        <Button variant="ghost" className="h-9 rounded-sm" onClick={() => navigate(`/projects/${slug}`)}>Отмена</Button>
      </div>

      <div className="grid grid-cols-1 gap-8 md:grid-cols-[220px_1fr]">
        <aside className="md:sticky md:top-16 md:self-start"><Rail step={step} /></aside>

        <div className="min-w-0">
          {/* STEP 1 */}
          {step === 1 && (
            <section className="space-y-5">
              <div>
                <h2 className="text-heading-card text-foreground">Загрузите лок-кит</h2>
                <p className="mt-1 text-body-sm text-muted-foreground">Таблица с колонками ключ / язык(и) / пояснение. Исходный язык определится автоматически по первой заполненной колонке.</p>
              </div>
              {preview ? <LocKitPreview preview={preview} /> : <DropZone onPick={pick} parsing={parsing} fileName={fileName} />}
              {preview && <Button variant="ghost" className="h-8 rounded-sm" onClick={() => { setPreview(null); setFileName('') }}>Загрузить другой файл</Button>}
            </section>
          )}

          {/* STEP 2 */}
          {step === 2 && (
            <section className="space-y-5">
              <div>
                <h2 className="text-heading-card text-foreground">Языки проекта</h2>
                <p className="mt-1 text-body-sm text-muted-foreground">Набор языков задаётся один раз. На них будет переводиться каждая будущая загрузка в этом проекте.</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {LANG_PRESET.map((l) => {
                  const on = !!langs[l.code]
                  const present = inKit.has(l.code)
                  return (
                    <button key={l.code} onClick={() => setLangs((s) => ({ ...s, [l.code]: !s[l.code] }))}
                      className={cn('flex items-center gap-2 rounded-sm border px-3 py-1.5 text-body-sm transition-colors', on ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-foreground hover:bg-muted')}>
                      {on && <Check className="h-3.5 w-3.5" />}
                      {l.name}
                      {present && <span className={cn('rounded-sm px-1.5 py-0.5 text-label-caps', on ? 'bg-primary-foreground/20' : 'bg-muted text-muted-foreground')}>в ките есть переводы</span>}
                    </button>
                  )
                })}
              </div>
            </section>
          )}

          {/* STEP 3 */}
          {step === 3 && (
            <section className="space-y-5">
              <div>
                <h2 className="text-heading-card text-foreground">Профиль проекта</h2>
                <p className="mt-1 text-body-sm text-muted-foreground">Профиль помогает переводить в нужном тоне. Найдите карточку игры в БДХК или заполните короткую анкету.</p>
              </div>

              <div className="relative">
                <Search className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Карточка игры в БДХК" className="h-9 ps-9" />
              </div>
              <ul className="divide-y divide-border rounded border border-border">
                {titles.map((t) => (
                  <li key={t.id}>
                    <button onClick={() => selectBdhc(t)} className={cn('flex w-full items-center justify-between gap-3 p-3 text-start hover:bg-muted', bdhc?.id === t.id && 'bg-accent')}>
                      <span className="text-body-sm text-foreground">{t.title}</span>
                      <span className="text-body-sm text-muted-foreground">{t.has_brief ? 'бриф' : 'нет брифа'}{t.has_voice ? ' · стиль' : ''}</span>
                    </button>
                  </li>
                ))}
              </ul>

              {profileMode === 'bdhc' && bdhc?.brief && bdhc?.voice && (
                <div className="flex items-start gap-3 rounded border border-info-surface bg-info-surface p-4 text-info-strong">
                  <Info className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
                  <div className="text-body-sm">Профиль проекта импортирован из карточки игры в БДХК: жанр, сеттинг, тон, обращение к игроку.</div>
                </div>
              )}

              {profileMode === 'questionnaire' && (
                <div className="space-y-3">
                  <p className="text-body-sm text-muted-foreground">В карточке нет нужных блоков — заполните анкету. Промпты сформирует платформа, вы их не увидите.</p>
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                    {QUESTIONS.map((qq) => (
                      <div key={qq.id} className="space-y-1">
                        <Label className="text-label-strong">{qq.label}</Label>
                        <Input value={answers[qq.id] || ''} onChange={(e) => setAnswers((a) => ({ ...a, [qq.id]: e.target.value }))} className="h-9" />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>
          )}

          {/* STEP 4 */}
          {step === 4 && (
            <section className="space-y-5">
              <div>
                <h2 className="text-heading-card text-foreground">Глоссарий</h2>
              </div>
              <div className="flex items-start gap-3 rounded border border-info-surface bg-info-surface p-4 text-info-strong">
                <Info className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
                <div className="text-body-sm">Глоссарий заметно повышает качество перевода — рекомендуем загрузить.</div>
              </div>
              <DropZone onPick={() => {}} label="Перетащите таблицу терминов или выберите на диске" formats="XLSX · CSV" />

              <div className="rounded border border-border">
                <div className="flex items-center justify-between gap-2 border-b border-border bg-accent px-4 py-2.5">
                  <h3 className="flex items-center gap-2 text-heading-card"><BookText className="h-4 w-4" />Термины, найденные в лок-ките</h3>
                  <Button variant="secondary" className="h-8 rounded-sm" onClick={() => setAdded(Object.fromEntries(candidates.map((c) => [c.term, true])))}>Добавить все</Button>
                </div>
                <ul className="divide-y divide-border">
                  {candidates.map((c) => (
                    <li key={c.term} className="flex items-start justify-between gap-3 p-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2"><span className="text-body-md text-foreground">{c.term}</span><span className="rounded-sm bg-muted px-1.5 py-0.5 text-label-caps text-muted-foreground">{c.why}</span></div>
                        <div className="mt-0.5 truncate text-body-sm text-muted-foreground">{c.context}</div>
                      </div>
                      <Button variant={added[c.term] ? 'ghost' : 'secondary'} className="h-8 shrink-0 rounded-sm" disabled={added[c.term]} onClick={() => setAdded((a) => ({ ...a, [c.term]: true }))}>
                        {added[c.term] ? <><Check className="h-4 w-4" />Добавлен</> : <><Plus className="h-4 w-4" />Добавить</>}
                      </Button>
                    </li>
                  ))}
                </ul>
              </div>
            </section>
          )}

          {/* STEP 5 */}
          {step === 5 && (
            <section className="space-y-5">
              <h2 className="text-heading-card text-foreground">Проверка</h2>
              <dl className="divide-y divide-border rounded border border-border">
                <div className="flex justify-between gap-3 p-3"><dt className="text-muted-foreground">Лок-кит</dt><dd className="text-foreground">{fileName || 'пример'} · {preview?.rows?.toLocaleString('ru-RU')} строк · источник {preview?.source_language}</dd></div>
                <div className="flex justify-between gap-3 p-3"><dt className="text-muted-foreground">Языки</dt><dd className="text-end text-foreground">{Object.keys(langs).filter((k) => langs[k]).length} языков</dd></div>
                <div className="flex justify-between gap-3 p-3"><dt className="text-muted-foreground">Профиль</dt><dd className="text-foreground">{profileMode === 'bdhc' ? 'из карточки БДХК' : profileMode === 'questionnaire' ? 'анкета' : 'по умолчанию'}</dd></div>
                <div className="flex justify-between gap-3 p-3"><dt className="text-muted-foreground">Глоссарий</dt><dd className="text-foreground">{Object.values(added).filter(Boolean).length} терминов добавлено</dd></div>
              </dl>
              <p className="text-body-sm text-muted-foreground">После запуска платформа создаст языки, лок-кит и глоссарий и начнёт прогон #1.</p>
            </section>
          )}

          {/* nav */}
          <div className="mt-8 flex items-center justify-between gap-3 border-t border-border pt-4">
            <div>
              {step > 1 && <Button variant="secondary" className="h-9 rounded-sm" onClick={() => setStep((s) => s - 1)}><ArrowLeft className="h-4 w-4" />Назад</Button>}
            </div>
            <div className="flex items-center gap-2">
              {step === 3 && <Button variant="ghost" className="h-9 rounded-sm" onClick={() => { setProfileMode('default'); setStep(4) }}>Пропустить</Button>}
              {step === 4 && <Button variant="ghost" className="h-9 rounded-sm" onClick={() => setStep(5)}>Пропустить</Button>}
              {step < 5 && <Button className="h-9 rounded-sm" disabled={!canNext} onClick={() => setStep((s) => s + 1)}>Далее <ArrowRight className="h-4 w-4" /></Button>}
              {step === 5 && <Button className="h-9 rounded-sm" disabled={committing} onClick={commit}>{committing ? 'Запуск…' : 'Сделать локализацию'}</Button>}
            </div>
          </div>

          {step === 1 && !preview && (
            <div className="mt-4"><WeblateLink url={`https://weblate.hcgameloc.internal/projects/${slug}/`} navigate={navigate} /></div>
          )}
        </div>
      </div>
    </AppShell>
  )
}
