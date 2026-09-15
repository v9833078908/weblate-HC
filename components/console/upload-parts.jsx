'use client'

import * as React from 'react'
import { UploadCloud, FileSpreadsheet, ChevronDown, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'

export function DropZone({ onPick, parsing, fileName, formats = 'XLSX · CSV · TSV', label = 'Перетащите файл сюда или выберите на диске' }) {
  const inputRef = React.useRef(null)
  const [drag, setDrag] = React.useState(false)
  if (parsing) {
    return (
      <div className="flex flex-col items-center justify-center rounded border border-border bg-muted p-10 text-center">
        <div className="mb-3 h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        <div className="text-body-md text-foreground">Разбираем файл…</div>
        {fileName && <div className="mt-1 font-mono text-body-sm text-muted-foreground">{fileName}</div>}
      </div>
    )
  }
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); onPick(e.dataTransfer.files?.[0]?.name || 'loc-kit.xlsx') }}
      className={cn('flex flex-col items-center justify-center rounded border-2 border-dashed p-10 text-center transition-colors', drag ? 'border-primary bg-muted' : 'border-input bg-card')}
    >
      <UploadCloud className="mb-3 h-8 w-8 text-muted-foreground" aria-hidden />
      <div className="text-body-md text-foreground">{label}</div>
      <div className="mt-1 text-body-sm text-muted-foreground">Принимаются: {formats}</div>
      <div className="mt-4 flex items-center gap-2">
        <Button variant="secondary" className="h-9 rounded-sm" onClick={() => inputRef.current?.click()}>
          <FileSpreadsheet className="h-4 w-4" /> Выбрать файл
        </Button>
        <Button variant="ghost" className="h-9 rounded-sm" onClick={() => onPick('pirate-ships-loc-kit.xlsx')}>Использовать пример</Button>
      </div>
      <input ref={inputRef} type="file" className="hidden" onChange={(e) => onPick(e.target.files?.[0]?.name || 'loc-kit.xlsx')} />
    </div>
  )
}

export function LocKitPreview({ preview, showDiff }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="rounded border border-border p-4">
          <div className="text-label-caps uppercase text-muted-foreground">Исходный язык</div>
          <div className="mt-1 text-body-md text-foreground">{preview.source_language} — первая заполненная колонка</div>
          <div className="mt-1 text-body-sm text-muted-foreground">Исходный язык нельзя изменить после настройки локализации.</div>
        </div>
        <div className="rounded border border-border p-4">
          <div className="text-label-caps uppercase text-muted-foreground">Строк в файле</div>
          <div className="mt-1 text-metric-lg tabular-nums text-foreground">{preview.rows.toLocaleString('ru-RU')}</div>
        </div>
      </div>

      <div className="rounded border border-border p-4">
        <div className="mb-2 text-label-caps uppercase text-muted-foreground">Найденные языки</div>
        <div className="flex flex-wrap gap-2">
          {preview.languages.map((l) => (
            <span key={l.code} className="inline-flex items-center gap-1.5 rounded-sm border border-border bg-muted px-2 py-1 text-body-sm">
              {l.name} <span className="font-mono text-muted-foreground">{l.code}</span>
              <span className="text-muted-foreground">· {l.prefilled > 0 ? `${l.prefilled.toLocaleString('ru-RU')} переводов` : 'пусто'}</span>
            </span>
          ))}
        </div>
      </div>

      {showDiff && preview.diff && (
        <div className="rounded border border-border bg-muted p-4">
          <div className="mb-2 text-label-caps uppercase text-muted-foreground">Изменения относительно прошлой версии</div>
          <div className="grid grid-cols-2 gap-y-1 text-body-sm md:grid-cols-4">
            <div>Новых ключей <span className="font-semibold text-foreground">{preview.diff.new_keys}</span></div>
            <div>Изменённый источник <span className="font-semibold text-foreground">{preview.diff.changed_source}</span></div>
            <div>Без изменений <span className="font-semibold text-foreground">{preview.diff.unchanged.toLocaleString('ru-RU')}</span></div>
            <div>Удалённых <span className="font-semibold text-foreground">{preview.diff.removed}</span></div>
          </div>
          <p className="mt-2 text-body-sm text-muted-foreground">Существующие переводы не перезаписываются — переводятся только новые ключи и строки с изменённым источником.</p>
        </div>
      )}

      {preview.quarantine && preview.quarantine.length > 0 && (
        <Collapsible className="rounded border border-border">
          <CollapsibleTrigger className="flex w-full items-center justify-between gap-2 p-4 text-start">
            <span className="flex items-center gap-2 text-body-md text-foreground">
              <AlertTriangle className="h-4 w-4 text-warning" aria-hidden />
              Строки без текста ({preview.quarantine.length}) — импортированы, но отложены
            </span>
            <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden />
          </CollapsibleTrigger>
          <CollapsibleContent className="border-t border-border">
            <ul className="divide-y divide-border">
              {preview.quarantine.map((r) => (
                <li key={r.key} className="flex items-center justify-between gap-3 px-4 py-2 text-body-sm">
                  <span className="font-mono text-foreground">{r.key}</span>
                  <span className="text-muted-foreground">{r.note}</span>
                </li>
              ))}
            </ul>
          </CollapsibleContent>
        </Collapsible>
      )}

      <div className="rounded border border-border p-4">
        <div className="mb-2 text-label-caps uppercase text-muted-foreground">Сопоставление колонок (только чтение)</div>
        <div className="flex flex-wrap gap-2 text-body-sm">
          <span className="rounded-sm bg-muted px-2 py-1 font-mono">key → ключ</span>
          {preview.languages.map((l) => <span key={l.code} className="rounded-sm bg-muted px-2 py-1 font-mono">{l.code} → {l.code}</span>)}
          <span className="rounded-sm bg-muted px-2 py-1 font-mono">explanation → пояснение</span>
        </div>
      </div>
    </div>
  )
}

export const LANG_PRESET = [
  { code: 'en', name: 'Английский' },
  { code: 'de', name: 'Немецкий' },
  { code: 'fr', name: 'Французский' },
  { code: 'es', name: 'Испанский' },
  { code: 'pt_BR', name: 'Португальский (Бразилия)' },
  { code: 'tr', name: 'Турецкий' },
  { code: 'ja', name: 'Японский' },
  { code: 'ko', name: 'Корейский' },
  { code: 'zh_Hans', name: 'Китайский (упр.)' },
]

export const LANG_NAMES = {
  ru: 'Русский', en: 'Английский', de: 'Немецкий', fr: 'Французский', es: 'Испанский',
  pt: 'Португальский (Португалия)', pt_BR: 'Португальский (Бразилия)', tr: 'Турецкий',
  ja: 'Японский', ko: 'Корейский', zh_Hans: 'Китайский (упр.)', zh_Hant: 'Китайский (трад.)', id: 'Индонезийский',
}
export const langName = (code) => LANG_NAMES[code] || code
