'use client'

import * as React from 'react'
import { Sparkles, Check, Plus, X, Eye, RefreshCw, AlertTriangle, ShieldOff, Pencil } from 'lucide-react'
import * as api from '@/src/api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { langName } from '@/components/console/upload-parts'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'

const CAT_RU = { item: 'Предмет', character: 'Персонаж', place: 'Место', concept: 'Понятие' }
const STAGES = ['Читаем лок-кит', 'Ищем термины и контекст', 'Сравниваем с глоссарием', 'Готовим предложения']

function EvidenceSheet({ term, langs, onClose }) {
  if (!term) return null
  const hl = (text) => {
    const idx = text.indexOf(term.term)
    if (idx < 0) return text
    return (<>{text.slice(0, idx)}<mark className="rounded-sm bg-highlight-glossary px-0.5">{term.term}</mark>{text.slice(idx + term.term.length)}</>)
  }
  return (
    <Sheet open={!!term} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-[560px]">
        <SheetHeader><SheetTitle>Контекст: {term.term}</SheetTitle></SheetHeader>
        <div className="mt-4 space-y-3">
          <div className="text-body-sm text-muted-foreground">{CAT_RU[term.category] || term.category} · встречается в {term.rows} строках</div>
          <ul className="divide-y divide-border rounded-sm border border-border">
            {(term.sample_keys || []).map((k) => (
              <li key={k} className="p-3">
                <div className="font-mono text-body-sm text-muted-foreground">{k} · лист Strings</div>
                <div className="mt-1 code-cell text-foreground">…{hl(`Строка с термином ${term.term} в контексте`)}…</div>
              </li>
            ))}
          </ul>
          <div>
            <div className="mb-1 text-label-caps uppercase text-muted-foreground">Существующие переводы</div>
            <div className="flex flex-wrap gap-2 text-body-sm">
              {langs.map((code) => {
                const t = term.translations?.[code]
                return <span key={code} className={cn('rounded-sm border px-2 py-1', t ? 'border-border bg-muted text-foreground' : 'border-dashed border-border text-muted-foreground')}>{langName(code)}: {t || 'Нет перевода'}</span>
              })}
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}

export function GlossaryWorkspace({ slug, uploadId, mode = 'wizard', targetLanguages = ['en', 'de', 'fr', 'ja'], categories, onStageChange, forceState, stageLabels = STAGES, sourceLabel = 'лок-кит (текущая версия)', beforeTitle = 'Предложить термины из лок-кита', beforeDesc = 'AI найдёт повторяющиеся названия и понятия и предложит их для глоссария. Ничего не добавится без вашего решения.' }) {
  const [phase, setPhase] = React.useState(forceState || 'before') // before|running|ready|empty|failed|model_off
  const [extraction, setExtraction] = React.useState(null)
  const [cand, setCand] = React.useState(forceState && forceState !== 'before' ? null : null)
  const [selected, setSelected] = React.useState({})
  const [rejected, setRejected] = React.useState({})
  const [checked, setChecked] = React.useState({})
  const [disputed, setDisputed] = React.useState({})
  const [questionChoice, setQuestionChoice] = React.useState({})
  const [filter, setFilter] = React.useState('new')
  const [evidence, setEvidence] = React.useState(null)
  const [edit, setEdit] = React.useState({})
  const [publishResult, setPublishResult] = React.useState(null)
  const pollRef = React.useRef(null)

  const loadCandidates = React.useCallback((exId) => {
    api.getCandidatesV2(slug, { upload: uploadId, extraction: exId }).then((c) => {
      const eligible = c.terms.filter((t) => t.eligible !== false)
      if (eligible.length === 0 && c.disputed.length === 0) setPhase('empty')
      else { setCand(c); setPhase('ready') }
    })
  }, [slug, uploadId])

  // for dev/states storyboard: preload candidates for ready/partial
  React.useEffect(() => {
    if (forceState === 'ready' || forceState === 'partial') loadCandidates('demo')
    if (forceState === 'partial') setPublishResult({ added: 10, existing: 2, failed: 1 })
  }, [forceState, loadCandidates])

  const start = () => {
    if (phase === 'running') return
    setPhase('running')
    api.startExtraction(slug, { upload_id: uploadId, scope: [], source_language: 'ru', target_languages: targetLanguages }).then((ex) => {
      setExtraction(ex)
      pollRef.current = setInterval(async () => {
        const st = await api.getExtraction(slug, ex.id)
        setExtraction(st)
        if (st.status === 'done') { clearInterval(pollRef.current); loadCandidates(ex.id) }
        if (st.status === 'error') { clearInterval(pollRef.current); setPhase('failed') }
      }, 700)
    })
  }
  React.useEffect(() => () => pollRef.current && clearInterval(pollRef.current), [])

  const stage = (t) => {
    setSelected((s) => ({ ...s, [t.id]: true }))
    onStageChange && onStageChange({ ...selected, [t.id]: true })
    if (mode === 'page') { api.addGlossaryTerms(slug, [{ source: t.term, targets: t.translations }]).then(() => toast('Добавлено в глоссарий: ' + t.term)) }
  }
  const reject = (t) => setRejected((s) => ({ ...s, [t.id]: true }))

  const eligibleNew = React.useMemo(() => (cand?.terms || []).filter((t) => t.review_status === 'new' && t.eligible !== false && !selected[t.id] && !rejected[t.id]), [cand, selected, rejected])
  const addAll = () => {
    const ids = {}; eligibleNew.forEach((t) => { ids[t.id] = true })
    const next = { ...selected, ...ids }
    setSelected(next); onStageChange && onStageChange(next)
    if (mode === 'page') {
      api.addGlossaryTerms(slug, eligibleNew.map((t) => ({ source: t.term, targets: t.translations }))).then(() => setPublishResult({ added: eligibleNew.length, existing: (cand.terms.filter((t) => t.review_status === 'existing')).length, failed: 0 }))
    }
  }

  // ---------- render states ----------
  if (phase === 'model_off' || forceState === 'model_off') {
    return (
      <div className="flex items-start gap-3 rounded border border-border bg-muted p-4">
        <ShieldOff className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
        <div><div className="text-label-strong text-foreground">Извлечение терминов недоступно</div><div className="text-body-sm text-muted-foreground">Администратор AI-инструментов ещё не настроил модель извлечения (LiteLLM / OpenRouter). Обратитесь к нему.</div></div>
      </div>
    )
  }

  if (phase === 'before') {
    return (
      <div className="rounded border border-border p-4">
        <div className="text-heading-card text-foreground">{beforeTitle}</div>
        <p className="mt-1 text-body-sm text-muted-foreground">{beforeDesc}</p>
        <dl className="mt-3 grid grid-cols-2 gap-y-1 text-body-sm">
          <dt className="text-muted-foreground">Файл</dt><dd className="text-foreground">{sourceLabel}</dd>
          <dt className="text-muted-foreground">Исходный язык</dt><dd className="text-foreground">ru</dd>
          <dt className="text-muted-foreground">Целевые языки</dt><dd className="text-foreground">{targetLanguages.map(langName).join(', ')}</dd>
          <dt className="text-muted-foreground">Категории</dt><dd className="text-foreground">{Object.entries(categories || {}).filter(([, v]) => v).map(([k]) => CAT_RU[k] || k).join(', ') || 'по умолчанию'}</dd>
        </dl>
        <Button className="mt-4 h-9 rounded-sm" onClick={start}><Sparkles className="h-4 w-4" />Извлечь термины</Button>
      </div>
    )
  }

  if (phase === 'running') {
    const stageIdx = extraction?.stage_index != null ? extraction.stage_index : STAGES.indexOf(extraction?.stage)
    return (
      <div className="rounded border border-border bg-muted p-4">
        <div className="text-label-strong text-foreground">{extraction?.status === 'queued' ? 'В очереди…' : 'Извлекаем термины…'}</div>
        <ol className="mt-3 space-y-2">
          {stageLabels.map((st, i) => (
            <li key={st} className="flex items-center gap-2 text-body-sm">
              {i < stageIdx ? <Check className="h-4 w-4 text-success" /> : i === stageIdx ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" /> : <span className="h-4 w-4 rounded-full border border-border" />}
              <span className={i <= stageIdx ? 'text-foreground' : 'text-muted-foreground'}>{st}</span>
            </li>
          ))}
        </ol>
        {extraction?.total_rows ? <div className="mt-3 text-body-sm text-muted-foreground">Обработано {extraction.processed_rows?.toLocaleString('ru-RU')} / {extraction.total_rows.toLocaleString('ru-RU')} строк</div> : null}
      </div>
    )
  }

  if (phase === 'failed') {
    return (
      <div className="flex items-start justify-between gap-3 rounded border border-destructive bg-background p-4">
        <div className="flex items-start gap-3"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" /><div><div className="text-label-strong text-foreground">Не удалось извлечь термины</div><div className="text-body-sm text-muted-foreground">Операция прервалась. Ваши выбранные термины сохранены.</div></div></div>
        <Button className="h-9 rounded-sm" onClick={start}><RefreshCw className="h-4 w-4" />Повторить</Button>
      </div>
    )
  }

  if (phase === 'empty') {
    return (
      <div className="rounded border border-border p-6 text-center">
        <div className="text-heading-card text-foreground">Термины не найдены</div>
        <p className="mt-1 text-body-sm text-muted-foreground">В ките не нашлось повторяющихся названий под выбранные категории.</p>
        <Button variant="secondary" className="mt-3 h-9 rounded-sm" onClick={() => setPhase('before')}>Изменить категории</Button>
      </div>
    )
  }

  // ready
  if (!cand) return <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded" />)}</div>
  const existing = (cand?.terms || []).filter((t) => t.review_status === 'existing')
  const rejectedList = (cand?.terms || []).filter((t) => rejected[t.id])
  const newList = (cand?.terms || []).filter((t) => t.review_status === 'new' && !rejected[t.id])
  const list = filter === 'new' ? newList : filter === 'disputed' ? [] : filter === 'existing' ? existing : rejectedList
  const showDisputed = filter === 'new' || filter === 'disputed'

  return (
    <div className="space-y-4">
      {publishResult && (
        <div className="rounded-sm border border-border bg-muted p-3 text-body-sm text-foreground">
          {mode === 'page' ? 'Публикация: ' : 'Итог: '}Добавлено {publishResult.added} · Уже есть {publishResult.existing}{publishResult.failed ? ` · Не добавлено ${publishResult.failed}` : ''}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-heading-card text-foreground">Предложения для глоссария</div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" className="h-8 rounded-sm" disabled={Object.values(checked).filter(Boolean).length === 0}
            onClick={() => { const next = { ...selected }; Object.keys(checked).forEach((id) => { if (checked[id]) next[id] = true }); setSelected(next); setChecked({}); onStageChange && onStageChange(next) }}>
            Добавить выбранные ({Object.values(checked).filter(Boolean).length})
          </Button>
          <Button className="h-8 rounded-sm" onClick={addAll} disabled={eligibleNew.length === 0}>Добавить все ({eligibleNew.length})</Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {[['new', `Новые ${newList.length}`], ['disputed', `Спорные ${cand.disputed.length}`], ['existing', `Уже в глоссарии ${existing.length}`], ['rejected', `Отклонённые ${rejectedList.length}`]].map(([k, l]) => (
          <button key={k} onClick={() => setFilter(k)} className={cn('rounded-sm border px-3 py-1 text-body-sm', filter === k ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card hover:bg-muted')}>{l}</button>
        ))}
      </div>

      <ul className="space-y-2">
        {list.map((t) => {
          const isSelected = selected[t.id]
          return (
            <li key={t.id} className={cn('rounded border p-3', isSelected ? 'border-primary bg-accent/40' : 'border-border')}>
              <div className="flex items-start gap-3">
                {filter !== 'existing' && filter !== 'rejected' && <Checkbox className="mt-1" checked={!!checked[t.id]} onCheckedChange={(v) => setChecked((c) => ({ ...c, [t.id]: !!v }))} aria-label="Выбрать" disabled={isSelected} />}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-body-md text-foreground">{t.term}</span>
                    <span className="rounded-sm bg-muted px-1.5 py-0.5 text-label-caps uppercase text-muted-foreground">{CAT_RU[t.category] || t.category}</span>
                    <span className="text-body-sm text-muted-foreground">в {t.rows} строках</span>
                    {t.review_status === 'existing' && <span className="rounded-sm bg-info-surface px-1.5 py-0.5 text-label-caps text-info-strong">уже в глоссарии</span>}
                  </div>
                  <div className="mt-1 text-body-sm text-muted-foreground">{t.explanation}</div>
                  <div className="mt-2 flex flex-wrap gap-1.5 text-body-sm">
                    {targetLanguages.map((code) => {
                      const tr = t.translations?.[code]
                      return <span key={code} className={cn('rounded-sm border px-1.5 py-0.5', tr ? 'border-border bg-muted text-foreground' : 'border-dashed border-border text-muted-foreground')}>{code}: {tr || 'Нет перевода'}</span>
                    })}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1.5">
                  <button onClick={() => setEvidence(t)} className="inline-flex items-center gap-1 text-body-sm text-primary hover:underline"><Eye className="h-3.5 w-3.5" />Посмотреть контекст</button>
                  {isSelected ? (
                    <span className="rounded-sm bg-primary px-2 py-1 text-label-caps uppercase text-primary-foreground">{mode === 'wizard' ? 'Выбрано — добавится при запуске' : 'Добавлено в глоссарий'}</span>
                  ) : filter === 'rejected' ? (
                    <Button variant="ghost" className="h-8 rounded-sm" onClick={() => setRejected((s) => { const n = { ...s }; delete n[t.id]; return n })}>Вернуть</Button>
                  ) : filter === 'existing' ? null : (
                    <div className="flex gap-1">
                      <Button className="h-8 rounded-sm" onClick={() => stage(t)}><Plus className="h-4 w-4" />Добавить</Button>
                      <Button variant="ghost" className="h-8 rounded-sm" onClick={() => reject(t)}><X className="h-4 w-4" />Не добавлять</Button>
                    </div>
                  )}
                </div>
              </div>
            </li>
          )
        })}

        {showDisputed && cand.disputed.map((d) => {
          const decided = disputed[d.id]
          return (
            <li key={d.id} className="rounded border border-warning bg-warning-surface p-3">
              <div className="flex items-center gap-2"><span className="text-body-md text-foreground">{d.term}</span><span className="rounded-sm bg-card px-1.5 py-0.5 text-label-caps uppercase text-warning">спорно</span></div>
              <div className="mt-1 text-body-sm text-warning">В ките разные переводы — выберите один, прежде чем добавить.</div>
              <div className="mt-2 flex flex-col gap-1">
                {d.options.map((o) => (
                  <label key={o.translation} className="flex items-center gap-2 text-body-sm text-foreground"><input type="radio" name={`disp-${d.id}`} checked={decided === o.translation} onChange={() => setDisputed((s) => ({ ...s, [d.id]: o.translation }))} /> {o.language}: {o.translation} <span className="text-muted-foreground">({o.rows} строк)</span></label>
                ))}
              </div>
              <Button className="mt-2 h-8 rounded-sm" disabled={!decided || selected[d.id]} onClick={() => stage({ id: d.id, term: d.term, translations: {} })}>{selected[d.id] ? 'Выбрано' : 'Добавить'}</Button>
            </li>
          )
        })}
      </ul>

      <EvidenceSheet term={evidence} langs={targetLanguages} onClose={() => setEvidence(null)} />
    </div>
  )
}
