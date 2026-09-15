'use client'

import * as React from 'react'
import { XCircle, FileSpreadsheet, Store, HelpCircle } from 'lucide-react'
import { DropZone } from '@/components/console/upload-parts'
import { Button } from '@/components/ui/button'
import { WeblateLink } from '@/components/console/primitives'
import { useUni } from '@/components/console/wizard2/ctx'
import { ClarifyDispatcher, AnalysisSheet, StoreFieldsPreview, InfoNote } from '@/components/console/wizard2/clarify'

const EXAMPLES = [
  { label: 'Лок-кит .xlsx', input: 'pirate-ships.xlsx' },
  { label: 'Steam .txt', input: 'steam_short.txt' },
  { label: 'Google Play .zip', input: 'metadata.zip' },
  { label: 'App Store папка', input: 'appstore.zip' },
]

function StopCard({ stop, onRetry }) {
  return (
    <div className="rounded border border-destructive bg-card p-5">
      <div className="flex items-start gap-3">
        <XCircle className="mt-0.5 h-6 w-6 shrink-0 text-destructive" aria-hidden />
        <div>
          <h3 className="text-heading-card text-foreground">{stop.title}</h3>
          <p className="mt-1 text-body-sm text-muted-foreground">{stop.text}</p>
          {stop.reason && <p className="mt-2 text-body-sm text-foreground">Причина: {stop.reason}</p>}
          <p className="mt-2 text-body-sm text-muted-foreground">Частичный импорт «того, что получилось прочитать» не делаем.</p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button className="h-9 rounded-sm" onClick={onRetry}>Загрузить другой архив</Button>
        <WeblateLink url="#" label="Что должно быть в архиве?" />
      </div>
    </div>
  )
}

export function StageFiles() {
  const { analysis, uploading, startUpload, patch, registry, resetUpload } = useUni()
  const [sheet, setSheet] = React.useState(false)

  if (uploading) return <div><h2 className="mb-4 text-heading-card text-foreground">Файлы</h2><DropZone parsing fileName={uploading} /></div>

  if (!analysis) {
    return (
      <section>
        <h2 className="text-heading-card text-foreground">Файлы</h2>
        <p className="mt-1 text-body-sm text-muted-foreground">Загрузите лок-кит (таблицу строк) или тексты для стора (Steam, Google Play, App Store). Тип определим сами — расширение не важно.</p>
        <div className="mt-4">
          <DropZone onPick={startUpload} formats="XLSX · CSV · TSV · TXT · ZIP · папка" label="Перетащите файлы, папку или архив" />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-body-sm text-muted-foreground">Примеры:</span>
          {EXAMPLES.map((e) => <Button key={e.input} variant="secondary" className="h-8 rounded-sm" onClick={() => startUpload(e.input)}>{e.label}</Button>)}
        </div>
        <p className="mt-4 text-body-sm text-muted-foreground">Для лок-кита: колонки «Говорящий» и «Пояснение» — это разные колонки; не объединяйте их. Для стора: загрузите TXT-файлы, папку локали или ZIP с метаданными.</p>
      </section>
    )
  }

  if (analysis.stop) {
    return <section><h2 className="mb-4 text-heading-card text-foreground">Файлы</h2><StopCard stop={analysis.stop} onRetry={resetUpload} /></section>
  }

  const isStore = analysis.kind === 'store'
  const isKit = analysis.kind === 'loc-kit'
  const isAmbiguous = analysis.kind === 'ambiguous'
  const hasFields = Array.isArray(analysis.fields)
  const showPreview = !isAmbiguous && (isKit || (isStore && hasFields))
  const clar = analysis.clarifications || []

  return (
    <section className="space-y-5">
      <h2 className="text-heading-card text-foreground">Файлы</h2>

      {showPreview && (
        <div className="rounded border border-border bg-card">
          <div className="flex items-center justify-between gap-3 border-b border-border bg-accent px-4 py-2.5">
            <div className="flex items-center gap-2">
              {isStore ? <Store className="h-4 w-4 text-primary" /> : <FileSpreadsheet className="h-4 w-4 text-primary" />}
              <h3 className="text-heading-card text-foreground">{isStore ? (registry[analysis.store]?.label || 'Тексты для стора') : 'Лок-кит'}</h3>
            </div>
            <Button variant="ghost" className="h-8 rounded-sm" onClick={() => setSheet(true)}>{isStore ? 'Проверить поля' : 'Посмотреть анализ'}</Button>
          </div>
          <div className="p-4">
            {isKit && (
              <div className="space-y-1 text-body-md text-foreground">
                <div><span className="font-mono">{analysis.file_name}</span> · {analysis.rows.total.toLocaleString('ru-RU')} строк · {analysis.lang_count} языковых колонок</div>
                <div className="text-body-sm text-muted-foreground">{analysis.rows.importable.toLocaleString('ru-RU')} строк готовы · {analysis.rows.quarantine} будут вынесены отдельно</div>
              </div>
            )}
            {isStore && (
              <div className="space-y-3">
                <div className="text-body-md text-foreground">{analysis.fields.length} {plural(analysis.fields.length, 'текстовое поле', 'текстовых поля', 'текстовых полей')} · исходные файлы на {analysis.source_locale || 'русском'}</div>
                <div className="text-body-sm text-muted-foreground">
                  {registry[analysis.store]?.markup ? `Разметка: ${registry[analysis.store].markup} · ` : ''}лимиты: {analysis.fields[0]?.limit_source || 'применятся автоматически'}
                </div>
                {analysis.source?.structure && (
                  <div className="text-body-sm text-muted-foreground">Исходная локаль: {analysis.source_locale}</div>
                )}
                <StoreFieldsPreview fields={analysis.fields} />
                <p className="text-body-sm text-muted-foreground">Лимиты применятся автоматически. Технические детали (маска файлов, пути метаданных) скрыты.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {analysis.split_sets && analysis.split_sets.length > 1 && (
        <InfoNote>Создаём отдельные наборы для {analysis.split_sets.map((s) => registry[s]?.label || s).join(' и ')}. Дальше настроим их вместе.</InfoNote>
      )}

      {isAmbiguous && (
        <div className="flex items-start gap-2 rounded-sm bg-muted p-3 text-body-sm text-muted-foreground"><HelpCircle className="mt-0.5 h-4 w-4 shrink-0" />Определяем содержимое загрузки — ответьте на уточнение ниже.</div>
      )}

      {isStore && !hasFields && !isAmbiguous && (
        <div className="flex items-start gap-2 rounded-sm bg-muted p-3 text-body-sm text-muted-foreground"><HelpCircle className="mt-0.5 h-4 w-4 shrink-0" />В загрузке несколько наборов — выберите, как их разделить, в уточнении ниже.</div>
      )}

      {clar.map((c) => <ClarifyDispatcher key={c.id} c={c} analysis={analysis} patch={patch} registry={registry} />)}

      <AnalysisSheet analysis={analysis} open={sheet} onClose={() => setSheet(false)} />
    </section>
  )
}

function plural(n, one, few, many) {
  const m10 = n % 10, m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few
  return many
}
