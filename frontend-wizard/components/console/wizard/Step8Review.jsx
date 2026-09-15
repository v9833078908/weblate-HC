'use client'

import * as React from 'react'
import { Lock } from 'lucide-react'
import { useWizard } from '@/components/console/wizard/WizardShell'
import { langName } from '@/components/console/upload-parts'

function Row({ label, children, onEdit }) {
  return (
    <div className="flex items-start justify-between gap-3 p-3">
      <div className="min-w-0"><div className="text-label-caps uppercase text-muted-foreground">{label}</div><div className="mt-1 text-body-sm text-foreground">{children}</div></div>
      {onEdit && <button onClick={onEdit} className="shrink-0 text-body-sm text-primary hover:underline">Изменить</button>}
    </div>
  )
}

export function Step8Review() {
  const { s, upload, goto } = useWizard()
  const langs = (s.languages || []).filter((l) => l.include).map((l) => l.resolved_as || l.code)
  const targets = langs.filter((c) => c !== s.source_language)
  const gate = upload?.gate
  const qTotal = Object.values(upload?.rows?.quarantined_by_reason || {}).reduce((a, b) => a + b, 0)
  const selectedTerms = Object.values(s.glossary.selected).filter(Boolean).length
  const estStrings = (upload?.rows?.importable || 3830)
  const estCost = Math.max(1, Math.round((estStrings * targets.length) / 2000 * 8))

  const P = s.profile
  const regUi = { 'ты': 'на «ты»', 'вы': 'на «вы»', formal: 'формально' }[P.register_ui] || '—'
  const regD = { by_speaker: 'по говорящему', ty: 'всегда «ты»', vy: 'всегда «вы»' }[P.register_dialogue] || '—'

  return (
    <section className="space-y-5">
      <h2 className="text-heading-card text-foreground">Проверка и запуск</h2>

      <div className="divide-y divide-border rounded border border-border">
        <Row label="Лок-кит" onEdit={() => goto(1)}>{s.fileName || 'файл'} · импортировано {gate?.imported?.toLocaleString('ru-RU')} · в карантин {qTotal} · проверка импортом: {gate?.ready ? 'готово' : 'не готово'}</Row>
        <Row label="Исходный язык" onEdit={() => goto(2)}><span className="inline-flex items-center gap-1.5">{langName(s.source_language)} <Lock className="h-3.5 w-3.5 text-muted-foreground" /> не изменится после запуска</span></Row>
        <Row label="Языки" onEdit={() => goto(3)}>{targets.map(langName).join(', ')}</Row>
        <Row label="Структура" onEdit={() => goto(4)}>роли колонок настроены · дубликаты: {s.duplicates_policy === 'stop' ? 'остановиться' : 'в карантин'}</Row>
        <Row label="Компоненты" onEdit={() => goto(5)}>{s.splitMode === 'split' ? `разделено на ${s.split.rule.length + 1}` : 'один компонент'}</Row>
        <Row label="Профиль" onEdit={() => goto(6)}>интерфейс {regUi}, диалоги {regD}, мат: {P.profanity === 'keep' ? 'в силе источника' : 'смягчать'}</Row>
        <Row label="Глоссарий" onEdit={() => goto(7)}>{s.glossary.skipped ? 'пропущен — заполните позже на странице Глоссарий' : `${selectedTerms} терминов выбрано`}</Row>
      </div>

      <div className="rounded border border-border bg-muted p-4">
        <div className="text-label-caps uppercase text-muted-foreground">Оценка</div>
        <div className="mt-1 text-body-md text-foreground">Перевод ≈ {estStrings.toLocaleString('ru-RU')} строк × {targets.length} языков · ≈ ${estCost} · ≈ 20 мин</div>
        <p className="mt-1 text-body-sm text-muted-foreground">Проверка качества судьёй в этот прогон не входит — запустите её отдельно после перевода.</p>
      </div>
    </section>
  )
}
