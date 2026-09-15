'use client'

import * as React from 'react'
import { Check, Search, Plus } from 'lucide-react'
import { useWizard } from '@/components/console/wizard/WizardShell'
import { langName, LANG_NAMES } from '@/components/console/upload-parts'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export function Step3Languages() {
  const { s, set, upload } = useWizard()
  const [q, setQ] = React.useState('')
  const langs = s.languages || []
  const source = s.source_language

  const update = (idx, patch) => set((p) => {
    const arr = [...p.languages]
    arr[idx] = { ...arr[idx], ...patch }
    return { languages: arr }
  })

  const kit = langs.map((l, i) => ({ ...l, i })).filter((l) => l.source === 'kit')
  const preset = langs.map((l, i) => ({ ...l, i })).filter((l) => l.source === 'preset')
  const hasId = upload?.columns.some((c) => c.header === 'Id')

  return (
    <section className="space-y-6">
      <div>
        <h2 className="text-heading-card text-foreground">На какие языки делаем переводы?</h2>
      </div>

      {/* in kit */}
      <div>
        <div className="mb-2 text-label-caps uppercase text-muted-foreground">Есть в ките</div>
        <div className="flex flex-wrap gap-2">
          {kit.filter((l) => !l.ambiguous && !l.low_fill).map((l) => {
            const code = l.resolved_as || l.code
            const isSource = code === source
            return (
              <button key={l.header} disabled={isSource} onClick={() => update(l.i, { include: !l.include })}
                className={cn('flex items-center gap-1.5 rounded-sm border px-3 py-1.5 text-body-sm', l.include ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-foreground hover:bg-muted', isSource && 'opacity-90')}>
                {l.include && <Check className="h-3.5 w-3.5" />}{langName(code)} <span className="opacity-70">{l.fill}%</span>{isSource && <span className="rounded-sm bg-primary-foreground/20 px-1.5 text-label-caps">исходный</span>}
              </button>
            )
          })}
        </div>
      </div>

      {/* preset */}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-label-caps uppercase text-muted-foreground">Пресет студии</span>
          <button onClick={() => set((p) => ({ languages: p.languages.map((l) => l.source === 'preset' ? { ...l, include: true } : l) }))} className="text-body-sm text-primary hover:underline">Добавить все</button>
        </div>
        <div className="flex flex-wrap gap-2">
          {preset.map((l) => (
            <button key={l.code} onClick={() => update(l.i, { include: !l.include })}
              className={cn('flex items-center gap-1.5 rounded-sm border px-3 py-1.5 text-body-sm', l.include ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-foreground hover:bg-muted')}>
              {l.include && <Check className="h-3.5 w-3.5" />}{langName(l.code)}
            </button>
          ))}
        </div>
      </div>

      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Другой язык…" className="h-9 ps-9" />
      </div>

      {/* resolution cards */}
      {kit.filter((l) => l.ambiguous).map((l) => (
        <div key={l.header} className="rounded border border-warning bg-warning-surface p-4">
          <div className="text-label-strong text-warning">Колонка {l.header}: уточните вариант языка</div>
          <div className="mt-3 flex flex-col gap-2">
            {l.ambiguous.map((code) => (
              <label key={code} className="flex items-center gap-2 text-body-sm text-foreground">
                <input type="radio" name={`amb-${l.header}`} checked={l.resolved_as === code} onChange={() => update(l.i, { resolved_as: code, include: true })} />
                {langName(code)} ({code})
              </label>
            ))}
          </div>
          <p className="mt-2 text-body-sm text-warning">Смешанная лексика внутри колонки — признак неровного перевода, а не варианта; мы не угадываем по словарю.</p>
        </div>
      ))}

      {kit.filter((l) => l.low_fill).map((l) => (
        <div key={l.header} className="rounded border border-border p-4">
          <div className="text-label-strong text-foreground">Колонка {l.code} заполнена в {l.fill}% строк. Это язык или остатки вставки?</div>
          <div className="mt-3 flex flex-col gap-2">
            <label className="flex items-center gap-2 text-body-sm"><input type="radio" name={`rare-${l.header}`} checked={l._decided && l.include} onChange={() => update(l.i, { include: true, _decided: true })} /> Язык — импортировать как есть</label>
            <label className="flex items-center gap-2 text-body-sm"><input type="radio" name={`rare-${l.header}`} checked={l._decided && !l.include} onChange={() => update(l.i, { include: false, _decided: true })} /> Не язык — не импортировать</label>
          </div>
          <p className="mt-2 text-body-sm text-muted-foreground">Колонки, заполненные меньше чем на 5%, по умолчанию не импортируются.</p>
        </div>
      ))}

      {hasId && (
        <div className="rounded border border-border p-4">
          <div className="text-label-strong text-foreground">Колонка Id: это индонезийский язык или служебный идентификатор?</div>
          <div className="mt-3 flex flex-col gap-2">
            <label className="flex items-center gap-2 text-body-sm"><input type="radio" name="idlang" checked={s.idIsLanguage === true} onChange={() => set({ idIsLanguage: true })} /> Индонезийский (id)</label>
            <label className="flex items-center gap-2 text-body-sm"><input type="radio" name="idlang" checked={s.idIsLanguage === false} onChange={() => set({ idIsLanguage: false })} /> Служебная колонка (настроим на шаге «Структура кита»)</label>
          </div>
        </div>
      )}

      <p className="text-body-sm text-muted-foreground">Каждая загрузка в этот проект будет переводиться на выбранные языки; добавить язык позже можно в настройках.</p>
    </section>
  )
}
