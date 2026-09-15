'use client'

import * as React from 'react'
import { X, CheckCircle2, Lock, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { langName } from '@/components/console/upload-parts'
import { useUni } from '@/components/console/wizard2/ctx'
import { PRESET, REGION, regionLabel, initLanguages } from '@/components/console/wizard2/ctx'
import { Chip, Radio } from '@/components/console/wizard2/clarify'
import { cn } from '@/lib/utils'

export function StageLanguages() {
  const { analysis, answers, setAnswers, setAnalysis } = useUni()

  React.useEffect(() => {
    if (!answers.languages) {
      setAnswers({ languages: initLanguages(analysis), source_language: answers.source_language || analysis.source_language })
    }
  }, []) // eslint-disable-line

  const langs = answers.languages || []
  const source = answers.source_language || analysis.source_language
  const setLangs = (next) => setAnswers({ languages: next })
  const update = (key, patch) => setLangs(langs.map((l) => (l.key === key ? { ...l, ...patch } : l)))
  const addPreset = (p) => {
    const e = langs.find((l) => l.key === p.code)
    if (e) update(p.code, { include: true })
    else setLangs([...langs, { key: p.code, group: p.group ? p.code : null, code: p.group ? null : p.code, name: p.name, source: 'preset', include: true, has_translation: false }])
  }
  const addAll = () => {
    let next = [...langs]
    PRESET.forEach((p) => {
      const i = next.findIndex((l) => l.key === p.code)
      if (i >= 0) next[i] = { ...next[i], include: true }
      else next.push({ key: p.code, group: p.group ? p.code : null, code: p.group ? null : p.code, name: p.name, source: 'preset', include: true, has_translation: false })
    })
    setLangs(next)
  }

  const included = langs.filter((l) => l.include)
  const unresolved = included.filter((l) => l.group && !l.code)
  const targets = included.filter((l) => l.code)
  const saved = targets.filter((l) => l.has_translation)
  const translated = targets.filter((l) => !l.has_translation)
  const presetAvailable = PRESET.filter((p) => { const e = langs.find((l) => l.key === p.code); return !(e && e.include) })

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-heading-card text-foreground">Языки</h2>
        <p className="mt-1 text-body-sm text-muted-foreground">Один набор языков на весь проект. Существующие переводы не перезаписываются — прогон заполняет только пустые или изменившиеся строки.</p>
      </div>

      {/* Source completeness (scenario 9) */}
      {analysis.source_options && <SourceCard analysis={analysis} source={source} setSource={(c) => setAnswers({ source_language: c })} setAnalysis={setAnalysis} />}

      {/* Source language lock line */}
      <div className="flex items-center gap-2 rounded-sm border border-border bg-muted px-3 py-2 text-body-sm text-foreground">
        <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
        Исходный язык: <span className="font-medium">{langName(source)}</span> — не изменится после настройки.
      </div>

      {/* Target languages */}
      <div className="rounded border border-border bg-card p-4">
        <div className="mb-3 text-label-caps uppercase text-muted-foreground">Целевые языки</div>
        {included.length === 0 ? (
          <p className="text-body-sm text-muted-foreground">Добавьте языки из пресета студии ниже.</p>
        ) : (
          <ul className="space-y-2">
            {included.map((l) => (
              <li key={l.key} className={cn('rounded-sm border p-3', l.group && !l.code ? 'border-warning bg-warning-surface' : 'border-border')}>
                <div className="flex items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-body-md text-foreground">{l.name}</span>
                    {l.code && <span className="font-mono text-body-sm text-muted-foreground">{regionLabel(l.code)}</span>}
                    {l.source === 'file' && !l.has_translation && <span className="rounded-sm bg-muted px-1.5 py-0.5 text-label-caps uppercase text-muted-foreground">из файла</span>}
                    {l.has_translation && <span className="rounded-sm bg-info-surface px-1.5 py-0.5 text-label-caps uppercase text-info-strong">в файлах уже есть перевод</span>}
                    {l.code && !l.has_translation && <span className="text-body-sm text-muted-foreground">будет переведено</span>}
                  </div>
                  <button onClick={() => update(l.key, { include: false })} className="rounded-sm p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={`Убрать ${l.name}`}><X className="h-4 w-4" /></button>
                </div>
                {l.group && !l.code && (
                  <div className="mt-2">
                    <div className="mb-1 text-body-sm text-warning">Выберите вариант — это разные локали:</div>
                    <div className="flex flex-col gap-1">
                      {REGION[l.group].map((o) => <Radio key={o.code} name={'reg-' + l.key} checked={l.code === o.code} onChange={() => update(l.key, { code: o.code })}>{o.label}</Radio>)}
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Add from preset */}
      {presetAvailable.length > 0 && (
        <div className="rounded border border-border bg-card p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-label-caps uppercase text-muted-foreground">Пресет студии</div>
            <Button variant="secondary" className="h-8 rounded-sm" onClick={addAll}>Добавить все</Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {presetAvailable.map((p) => <Chip key={p.code} onClick={() => addPreset(p)}>+ {p.name}{p.group && ' *'}</Chip>)}
          </div>
          <p className="mt-2 text-body-sm text-muted-foreground">* потребуется выбрать региональный вариант.</p>
        </div>
      )}

      {/* Resolution completed note (scenario 10) */}
      {unresolved.length === 0 && targets.some((l) => l.group) && (
        <div className="flex items-start gap-2 rounded-sm border border-success bg-success/10 p-3 text-body-sm text-foreground">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
          <span>Уточнения завершены: {targets.filter((l) => l.group).map((l) => `${l.name.toLowerCase()} ${regionLabel(l.code)}`).join(' · ')}</span>
        </div>
      )}

      {/* Summary */}
      <div className="rounded border border-border bg-muted p-4">
        <div className="text-metric-lg tabular-nums text-foreground">{targets.length} {plural(targets.length, 'целевой язык', 'целевых языка', 'целевых языков')}</div>
        <div className="mt-1 space-y-0.5 text-body-sm text-muted-foreground">
          {saved.length > 0 && <div>{saved.length} {plural(saved.length, 'перевод сохраним', 'перевода сохраним', 'переводов сохраним')} (не перезапишем)</div>}
          <div>{translated.length} {plural(translated.length, 'язык переведём', 'языка переведём', 'языков переведём')}</div>
        </div>
        {unresolved.length > 0 && <div className="mt-2 flex items-center gap-1.5 text-body-sm text-warning"><AlertTriangle className="h-4 w-4" />Выберите региональный вариант для: {unresolved.map((l) => l.name).join(', ')}</div>}
      </div>
    </section>
  )
}

function SourceCard({ analysis, source, setSource, setAnalysis }) {
  const opts = analysis.source_options
  const addMissing = (code) => {
    setAnalysis({ ...analysis, source_options: opts.map((o) => (o.code === code ? { ...o, have: o.need, complete: true, missing: [] } : o)) })
    setSource(code)
  }
  return (
    <div className="rounded border border-warning bg-card">
      <div className="flex items-center gap-2 border-b border-warning px-4 py-2.5"><AlertTriangle className="h-4 w-4 text-warning" /><h3 className="text-label-strong text-foreground">Исходный язык: комплект неполный</h3></div>
      <div className="space-y-2 p-4">
        {opts.map((o) => (
          <div key={o.code} className={cn('rounded-sm border p-3', o.complete ? 'border-border' : 'border-warning bg-warning-surface', source === o.code && o.complete && 'border-primary bg-accent/40')}>
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-body-md text-foreground">{o.name} <span className="font-mono text-muted-foreground">({o.code})</span></div>
                <div className="text-body-sm text-muted-foreground">Доступно {o.have} из {o.need} исходных полей{o.missing.length > 0 && ` · не хватает: ${o.missing.join(', ')}`}</div>
              </div>
              {o.complete
                ? <Button className="h-8 rounded-sm" variant={source === o.code ? 'default' : 'secondary'} onClick={() => setSource(o.code)}>{source === o.code ? 'Выбран' : 'Выбрать'}</Button>
                : <Button className="h-8 rounded-sm" variant="secondary" onClick={() => addMissing(o.code)}>Добавить недостающий файл</Button>}
            </div>
          </div>
        ))}
        <p className="text-body-sm text-muted-foreground">«Далее» заблокировано, пока не выбран полный исходный комплект. Русский текст не подставляется вместо отсутствующего английского автоматически.</p>
      </div>
    </div>
  )
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few
  return many
}
