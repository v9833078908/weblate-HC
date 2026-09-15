'use client'

import * as React from 'react'
import { FileSpreadsheet, Store, Pencil } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { langName } from '@/components/console/upload-parts'
import { Money } from '@/components/console/primitives'
import { useUni, finalTargetCodes, regionLabel } from '@/components/console/wizard2/ctx'

const REG_UI = { 'ты': 'на «ты»', 'вы': 'на «вы»', formal: 'формально' }

function Row({ title, children, onEdit }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border py-3 last:border-0">
      <div className="min-w-0">
        <div className="text-label-caps uppercase text-muted-foreground">{title}</div>
        <div className="mt-1 text-body-sm text-foreground">{children}</div>
      </div>
      {onEdit && <Button variant="ghost" className="h-8 shrink-0 rounded-sm" onClick={onEdit}><Pencil className="h-3.5 w-3.5" />Изменить</Button>}
    </div>
  )
}

export function StageReview() {
  const { analysis, answers, goto, registry } = useUni()
  const isStore = analysis.kind === 'store'
  const source = answers.source_language || analysis.source_language
  const targets = finalTargetCodes(answers.languages, source)
  const saved = (answers.languages || []).filter((l) => l.include && l.code && l.has_translation)
  const p = answers.profile || {}
  const g = answers.glossary || {}
  const selectedCount = Object.values(g.selected || {}).filter(Boolean).length

  // translate-only estimate
  const units = isStore ? (analysis.fields?.length || 0) * targets.length : (analysis.rows?.importable || 0) * targets.length
  const cost = isStore ? Math.max(1, Math.round(units * 0.35)) : Math.round((analysis.rows?.importable || 0) * targets.length * 0.006)
  const hMin = isStore ? 1 : 2
  const hMax = isStore ? 2 : 4

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-heading-card text-foreground">Проверка и запуск</h2>
        <p className="mt-1 text-body-sm text-muted-foreground">Проверьте настройки. Оценка ниже — только за перевод; проверка качества запускается отдельным прогоном.</p>
      </div>

      <div className="rounded border border-border bg-card p-4">
        <Row title="Файлы" onEdit={() => goto(1)}>
          <span className="inline-flex items-center gap-1.5">
            {isStore ? <Store className="h-3.5 w-3.5 text-muted-foreground" /> : <FileSpreadsheet className="h-3.5 w-3.5 text-muted-foreground" />}
            {isStore
              ? `${registry[analysis.store]?.label || 'Стор'} · ${analysis.fields?.length} полей · исходные на ${analysis.source_locale || 'русском'}`
              : `Лок-кит · ${analysis.file_name} · ${analysis.rows?.importable?.toLocaleString('ru-RU')} строк готовы`}
          </span>
        </Row>
        <Row title="Исходный язык" onEdit={() => goto(2)}>{langName(source)} <span className="text-muted-foreground">· не изменится</span></Row>
        <Row title="Целевые языки" onEdit={() => goto(2)}>
          {targets.length === 0 ? <span className="text-warning">не выбраны</span> : targets.map((c) => regionLabel(c)).join(' · ')}
          {saved.length > 0 && <div className="mt-0.5 text-muted-foreground">{saved.length} перевода сохраним (не перезапишем)</div>}
        </Row>
        <Row title="Контекст" onEdit={() => goto(3)}>
          Обращение: {REG_UI[p.register_ui] || '—'} · Мат: {p.profanity === 'keep' ? 'сохраняем' : p.profanity === 'soften' ? `смягчаем (${p.profanity_level === 'none' ? 'без мата' : 'до лёгкой'})` : '—'}
          {p.cjk_politeness && ` · CJK: ${p.cjk_politeness}`}
        </Row>
        <Row title="Глоссарий" onEdit={() => goto(3)}>
          {g.skipped ? 'пропущен' : selectedCount > 0 ? `${selectedCount} терминов выбрано — добавится при запуске` : 'без новых терминов'}
        </Row>
      </div>

      <div className="rounded border border-border bg-muted p-4">
        <div className="text-label-caps uppercase text-muted-foreground">Перевод</div>
        <div className="mt-1 text-body-md text-foreground">
          {isStore
            ? `≈ ${analysis.fields?.length} полей × ${targets.length} языков = ${units} значений`
            : `≈ ${analysis.rows?.importable?.toLocaleString('ru-RU')} строк × ${targets.length} языков`}
        </div>
        <div className="mt-1 text-body-sm text-muted-foreground">≈ <Money value={cost} /> · ≈ {hMin}–{hMax} часа · письмо придёт по завершении</div>
      </div>
    </section>
  )
}
