'use client'

import * as React from 'react'
import { AlertTriangle, XCircle, CheckCircle2, ChevronDown, Info, FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { WeblateLink } from '@/components/console/primitives'
import { cn } from '@/lib/utils'

const MARKUP_OPTS = [['none', 'Нет'], ['bbcode', 'BBCode'], ['html', 'HTML']]

// ---------------------------------------------------------------- «Нужно уточнить» wrapper
export function ClarifyCard({ resolved, summary, title, tone = 'warning', children }) {
  if (resolved) {
    return (
      <div className="flex items-start gap-2 rounded-sm border border-success bg-success/10 p-3 text-body-sm text-foreground">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" aria-hidden />
        <span>{summary || 'Уточнения завершены'}</span>
      </div>
    )
  }
  const border = tone === 'destructive' ? 'border-destructive' : 'border-warning'
  const Icon = tone === 'destructive' ? XCircle : AlertTriangle
  const icTone = tone === 'destructive' ? 'text-destructive' : 'text-warning'
  return (
    <section className={cn('rounded border bg-card', border)}>
      <header className="flex items-center gap-2 border-b px-4 py-2.5" style={{ borderColor: 'inherit' }}>
        <Icon className={cn('h-4 w-4 shrink-0', icTone)} aria-hidden />
        <h3 className="text-label-strong text-foreground">Нужно уточнить</h3>
      </header>
      <div className="p-4">
        {title && <div className="mb-3 text-body-md text-foreground">{title}</div>}
        {children}
      </div>
    </section>
  )
}

// ---------------------------------------------------------------- store field character counter
export function FieldCounter({ f }) {
  const near = f.limit > 0 && f.chars >= f.limit * 0.9 && f.chars <= f.limit
  const tone = f.status === 'over' ? 'text-destructive' : near ? 'text-warning' : 'text-muted-foreground'
  return (
    <span className={cn('tabular-nums', tone)}>
      {f.limit > 0 ? `${f.chars.toLocaleString('ru-RU')} / ${f.limit.toLocaleString('ru-RU')}` : `${f.chars.toLocaleString('ru-RU')} симв.`}
    </span>
  )
}

function StatusMark({ f }) {
  if (f.status === 'ok') return <span className="inline-flex items-center gap-1 text-body-sm text-success"><CheckCircle2 className="h-3.5 w-3.5" />готово</span>
  if (f.status === 'over') return <span className="inline-flex items-center gap-1 text-body-sm text-destructive"><XCircle className="h-3.5 w-3.5" />превышен лимит</span>
  if (f.status === 'bbcode') return <span className="inline-flex items-center gap-1 text-body-sm text-destructive"><XCircle className="h-3.5 w-3.5" />ошибка разметки</span>
  if (f.status === 'empty') return <span className="inline-flex items-center gap-1 text-body-sm text-warning"><AlertTriangle className="h-3.5 w-3.5" />пусто</span>
  return null
}

// ---------------------------------------------------------------- analysis / fields side sheet
export function AnalysisSheet({ analysis, open, onClose }) {
  if (!analysis) return null
  const isStore = analysis.kind === 'store'
  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-[560px]">
        <SheetHeader><SheetTitle>{isStore ? 'Проверка полей' : 'Анализ лок-кита'}</SheetTitle></SheetHeader>
        <div className="mt-4 space-y-4">
          {isStore ? (
            <>
              <div className="rounded-sm border border-border p-3 text-body-sm">
                <div className="text-label-caps uppercase text-muted-foreground">Площадка</div>
                <div className="mt-1 text-foreground">{analysis.store} · лимиты: {analysis.fields?.[0]?.limit_source}</div>
              </div>
              <ul className="space-y-2">
                {(analysis.fields || []).map((f) => (
                  <li key={f.id} className="rounded-sm border border-border p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-label-strong text-foreground">{f.label}</span>
                      <FieldCounter f={f} />
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-body-sm text-muted-foreground">
                      <FileText className="h-3.5 w-3.5" /> <span className="font-mono">{f.file}</span>{f.markup && <span>· {f.markup}</span>}
                    </div>
                    <div className="mt-2 whitespace-pre-wrap rounded-sm bg-muted p-2 text-body-sm text-foreground">{f.value || <span className="text-muted-foreground">пусто</span>}</div>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <>
              <div className="rounded-sm border border-border p-3 text-body-sm">
                <div className="text-label-caps uppercase text-muted-foreground">Формат</div>
                <div className="mt-1 text-foreground">{analysis.format?.kind} · {analysis.format?.encoding}{analysis.format?.sheet ? ` · лист ${analysis.format.sheet}` : ''}</div>
              </div>
              <div className="rounded-sm border border-border p-3">
                <div className="mb-1 text-label-caps uppercase text-muted-foreground">Строки</div>
                <div className="text-body-sm text-foreground">Всего {analysis.rows?.total?.toLocaleString('ru-RU')} · готовы {analysis.rows?.importable?.toLocaleString('ru-RU')} · карантин {analysis.rows?.quarantine}</div>
                {analysis.rows?.by_reason && Object.keys(analysis.rows.by_reason).length > 0 && (
                  <ul className="mt-2 space-y-0.5 text-body-sm text-muted-foreground">
                    {Object.entries(analysis.rows.by_reason).map(([k, v]) => <li key={k}>{REASON_RU[k] || k}: {v}</li>)}
                  </ul>
                )}
              </div>
              <div className="rounded-sm border border-border p-3">
                <div className="mb-1 text-label-caps uppercase text-muted-foreground">Колонки</div>
                <ul className="space-y-1 text-body-sm">
                  {(analysis.columns || []).map((c) => <li key={c.header} className="flex justify-between"><span className="font-mono text-foreground">{c.header}</span><span className="text-muted-foreground">{c.role}</span></li>)}
                </ul>
              </div>
              <div className="rounded-sm border border-border p-3">
                <div className="mb-1 text-label-caps uppercase text-muted-foreground">Языки в файле</div>
                <div className="flex flex-wrap gap-1.5 text-body-sm">
                  {(analysis.languages_found || []).map((l) => <span key={l.code} className="rounded-sm bg-muted px-2 py-0.5">{l.name} <span className="font-mono text-muted-foreground">{l.code}</span>{l.fill != null && ` · ${l.fill}%`}</span>)}
                </div>
              </div>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

const REASON_RU = { empty_all: 'пусто во всех языках', duplicate_exact: 'точный дубликат', duplicate_conflict: 'конфликт дубликатов', empty_key: 'пустой ключ' }

// ================================================================ specific clarify cards
export function ClarifyDispatcher({ c, analysis, patch, registry }) {
  const onClarify = (changes) => patch({ clarify: { id: c.id, ...changes } })
  const combined = (body) => patch(body)
  switch (c.code) {
    case 'unknown_files': return <UnknownFilesCard c={c} onClarify={onClarify} resolved={c.resolved} />
    case 'ambiguous_content': return <AmbiguousContentCard c={c} onClarify={onClarify} registry={registry} resolved={c.resolved} />
    case 'two_stores': return <TwoStoresCard c={c} onClarify={onClarify} resolved={c.resolved} />
    case 'unknown_in_known': return <UnknownInKnownCard c={c} onClarify={onClarify} registry={registry} resolved={c.resolved} />
    case 'duplicate_field': return <DuplicateFieldCard c={c} onClarify={onClarify} resolved={c.resolved} />
    case 'over_limit_source': return <OverLimitCard c={c} analysis={analysis} onFix={combined} resolved={c.resolved} />
    case 'broken_bbcode': return <BrokenBBCodeCard c={c} analysis={analysis} onFix={combined} resolved={c.resolved} />
    case 'empty_file': return <EmptyFileCard c={c} analysis={analysis} onFix={combined} resolved={c.resolved} />
    default: return null
  }
}

// 5 — unknown txt files (custom store)
function UnknownFilesCard({ c, onClarify, resolved }) {
  const setFile = (i, patch) => { const files = c.files.map((f, j) => (j === i ? { ...f, ...patch } : f)); onClarify({ files }) }
  return (
    <ClarifyCard resolved={resolved} title={`Не узнали ${c.files.length} имени файла. Укажите, какие это поля.`}
      summary={`Уточнения завершены: ${c.files.map((f) => f.label).join(' · ')}`}>
      <div className="space-y-4">
        {c.files.map((f, i) => (
          <div key={f.name} className="rounded-sm border border-border p-3">
            <div className="mb-2 flex items-center gap-2 text-body-sm text-muted-foreground"><FileText className="h-3.5 w-3.5" /><span className="font-mono">{f.name}</span></div>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <div><Label className="text-body-sm">Название поля</Label><Input className="mt-1 h-9" value={f.label} onChange={(e) => setFile(i, { label: e.target.value })} placeholder="Напр. Описание проекта" /></div>
              <div>
                <Label className="text-body-sm">Лимит символов</Label>
                <div className="mt-1 flex items-center gap-2">
                  <Input className="h-9 w-24" type="number" disabled={f.no_limit} value={f.limit ?? ''} onChange={(e) => setFile(i, { limit: e.target.value ? Number(e.target.value) : null })} placeholder="—" />
                  <label className="flex items-center gap-1.5 text-body-sm text-foreground"><input type="checkbox" checked={f.no_limit} onChange={(e) => setFile(i, { no_limit: e.target.checked, limit: null, limit_source: '' })} />Лимита нет</label>
                </div>
              </div>
              {!f.no_limit && f.limit != null && (
                <div className="md:col-span-2"><Label className="text-body-sm">Источник лимита <span className="text-destructive">*</span></Label><Input className="mt-1 h-9" value={f.limit_source} onChange={(e) => setFile(i, { limit_source: e.target.value })} placeholder="Откуда лимит (справочник, документация)" /></div>
              )}
              <div className="md:col-span-2">
                <Label className="text-body-sm">Разметка</Label>
                <div className="mt-1 flex gap-2">{MARKUP_OPTS.map(([v, l]) => <Chip key={v} active={f.markup === v} onClick={() => setFile(i, { markup: v })}>{l}</Chip>)}</div>
              </div>
            </div>
          </div>
        ))}
        <p className="text-body-sm text-muted-foreground">Неизвестный лимит не придумываем. Если указываете лимит — укажите источник.</p>
      </div>
    </ClarifyCard>
  )
}

// 6 — single ambiguous file
function AmbiguousContentCard({ c, onClarify, registry, resolved }) {
  const stores = Object.values(registry || {}).filter((s) => s.id !== 'custom')
  const storeFields = c.store ? (registry[c.store]?.fields || []) : []
  return (
    <ClarifyCard resolved={resolved} title={`Что находится в файле ${c.file}?`}
      summary={`Уточнения завершены: ${c.file} → ${OPT_RU[c.choice] || c.choice}${c.field ? ' · ' + (registry[c.store]?.fields.find((f) => f.id === c.field)?.label || c.field) : ''}`}>
      <div className="space-y-2">
        {[['table', 'Таблица строк лок-кита'], ['store_field', 'Одно поле страницы стора'], ['other', 'Другое']].map(([v, l]) => (
          <Radio key={v} name={'amb-' + c.id} checked={c.choice === v} onChange={() => onClarify({ choice: v, store: null, field: null })}>{l}</Radio>
        ))}
        {c.choice === 'store_field' && (
          <div className="ms-6 mt-2 space-y-3 rounded-sm border border-border p-3">
            <div>
              <Label className="text-body-sm">Для какой площадки?</Label>
              <div className="mt-1 flex flex-wrap gap-2">{stores.map((s) => <Chip key={s.id} active={c.store === s.id} onClick={() => onClarify({ store: s.id, field: null })}>{s.label}</Chip>)}</div>
            </div>
            {c.store && (
              <div>
                <Label className="text-body-sm">Какое это поле?</Label>
                <div className="mt-1 flex flex-wrap gap-2">{storeFields.map((f) => <Chip key={f.id} active={c.field === f.id} onClick={() => onClarify({ field: f.id })}>{f.label}</Chip>)}</div>
              </div>
            )}
          </div>
        )}
      </div>
    </ClarifyCard>
  )
}
const OPT_RU = { table: 'Таблица строк', store_field: 'Поле стора', other: 'Другое' }

// 7 — two stores in one upload
function TwoStoresCard({ c, onClarify, resolved }) {
  return (
    <ClarifyCard resolved={resolved} title="Нашли тексты для двух площадок"
      summary={`Уточнения завершены: ${c.choice === 'split' ? 'создаём два набора — ' + c.stores.map((s) => s.label).join(' · ') : 'выбрано вручную'}`}>
      <ul className="mb-3 space-y-1.5">
        {c.stores.map((s) => <li key={s.store} className="flex items-center justify-between rounded-sm border border-border px-3 py-2 text-body-sm"><span className="text-foreground">{s.label}</span><span className="text-muted-foreground">{s.field_count} поля</span></li>)}
      </ul>
      <p className="mb-3 text-body-sm text-foreground">Создать два отдельных набора: Steam и Google Play?</p>
      <div className="flex flex-wrap gap-2">
        <Button className="h-9 rounded-sm" onClick={() => onClarify({ choice: 'split' })}>Создать два набора</Button>
        <Button variant="secondary" className="h-9 rounded-sm" onClick={() => onClarify({ choice: 'manual' })}>Выбрать файлы вручную</Button>
        <Button variant="ghost" className="h-9 rounded-sm" onClick={() => onClarify({ choice: null })}>Загрузить другой архив</Button>
      </div>
    </ClarifyCard>
  )
}

// 14 — unknown file in a known set
function UnknownInKnownCard({ c, onClarify, registry, resolved }) {
  const fields = registry[c.store]?.fields || []
  const setExtra = (patch) => onClarify({ extra: { ...c.extra, ...patch } })
  return (
    <ClarifyCard resolved={resolved} title={`Не узнали поле ${c.file}`}
      summary={`Уточнения завершены: ${c.file} → ${c.choice === 'additional' ? c.extra.label : c.choice === 'skip' ? 'не импортировать' : (fields.find((f) => f.id === c.choice)?.label || c.choice)}`}>
      <div className="space-y-2">
        {fields.map((f) => <Radio key={f.id} name={'uik-' + c.id} checked={c.choice === f.id} onChange={() => onClarify({ choice: f.id })}>{f.label}</Radio>)}
        <Radio name={'uik-' + c.id} checked={c.choice === 'additional'} onChange={() => onClarify({ choice: 'additional' })}>Дополнительное поле</Radio>
        <Radio name={'uik-' + c.id} checked={c.choice === 'skip'} onChange={() => onClarify({ choice: 'skip' })}>Не импортировать</Radio>
        {c.choice === 'additional' && (
          <div className="ms-6 mt-2 grid grid-cols-1 gap-3 rounded-sm border border-border p-3 md:grid-cols-2">
            <div><Label className="text-body-sm">Русское название</Label><Input className="mt-1 h-9" value={c.extra.label} onChange={(e) => setExtra({ label: e.target.value })} /></div>
            <div><Label className="text-body-sm">Лимит</Label>
              <div className="mt-1 flex items-center gap-2">
                <Input className="h-9 w-24" type="number" disabled={c.extra.no_limit} value={c.extra.limit ?? ''} onChange={(e) => setExtra({ limit: e.target.value ? Number(e.target.value) : null })} />
                <label className="flex items-center gap-1.5 text-body-sm"><input type="checkbox" checked={c.extra.no_limit} onChange={(e) => setExtra({ no_limit: e.target.checked, limit: null, limit_source: '' })} />Лимита нет</label>
              </div>
            </div>
            {!c.extra.no_limit && c.extra.limit != null && <div className="md:col-span-2"><Label className="text-body-sm">Источник лимита <span className="text-destructive">*</span></Label><Input className="mt-1 h-9" value={c.extra.limit_source} onChange={(e) => setExtra({ limit_source: e.target.value })} /></div>}
            <div className="md:col-span-2"><Label className="text-body-sm">Разметка</Label><div className="mt-1 flex gap-2">{MARKUP_OPTS.map(([v, l]) => <Chip key={v} active={c.extra.markup === v} onClick={() => setExtra({ markup: v })}>{l}</Chip>)}</div></div>
          </div>
        )}
      </div>
    </ClarifyCard>
  )
}

// 15 — two files → one field
function DuplicateFieldCard({ c, onClarify, resolved }) {
  return (
    <ClarifyCard resolved={resolved} title={`Для поля «${c.field_label}» найдено два файла. Выберите один.`}
      summary={`Уточнения завершены: поле «${c.field_label}» → ${c.choice}`}>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {c.candidates.map((f) => (
          <div key={f.file} className={cn('rounded-sm border p-3', c.choice === f.file ? 'border-primary bg-accent/40' : 'border-border')}>
            <div className="flex items-center gap-2 text-body-sm text-foreground"><FileText className="h-3.5 w-3.5 text-muted-foreground" /><span className="font-mono">{f.path}{f.file}</span></div>
            <div className="mt-1 text-body-sm text-muted-foreground">{f.chars} симв. · {f.markup} · изменён {f.modified}</div>
            <ul className="mt-2 space-y-0.5 rounded-sm bg-muted p-2 text-body-sm text-foreground">{f.preview.map((p, i) => <li key={i} className="truncate">{p}</li>)}</ul>
            <Button className="mt-2 h-8 w-full rounded-sm" variant={c.choice === f.file ? 'default' : 'secondary'} onClick={() => onClarify({ choice: f.file })}>Использовать этот файл</Button>
          </div>
        ))}
      </div>
      <p className="mt-2 text-body-sm text-muted-foreground">Файлы нельзя объединять автоматически.</p>
    </ClarifyCard>
  )
}

// 11 — source over limit
function OverLimitCard({ c, analysis, onFix, resolved }) {
  const [showSrc, setShowSrc] = React.useState(false)
  const f = (analysis.fields || []).find((x) => x.id === c.field)
  if (!f) return null
  const over = f.value.slice(f.limit)
  const good = f.value.slice(0, f.limit)
  const replace = () => onFix({ field: { id: f.id, value: good.replace(/[\s:—-]+$/, '') }, clarify: { id: c.id, resolved: true } })
  return (
    <ClarifyCard resolved={resolved} tone="destructive" title={`${f.label} превышает лимит`} summary={`Уточнения завершены: «${f.label}» в пределах лимита`}>
      <div className="text-body-md tabular-nums text-destructive">{f.chars} из {f.limit} символов</div>
      <div className="mt-1 text-body-sm text-muted-foreground">Лимит {analysis.store} · источник: {f.limit_source}</div>
      <div className="mt-3 whitespace-pre-wrap rounded-sm border border-border bg-muted p-3 text-body-sm text-foreground">{good}<mark className="rounded-sm bg-destructive/20 px-0.5 text-destructive line-through">{over}</mark></div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button className="h-9 rounded-sm" onClick={replace}>Заменить файл</Button>
        <Button variant="ghost" className="h-9 rounded-sm" onClick={() => setShowSrc((v) => !v)}>Посмотреть источник лимита</Button>
      </div>
      {showSrc && <div className="mt-2 rounded-sm bg-info-surface p-2 text-body-sm text-info-strong">Лимит {f.limit} символов для поля «{f.label}» — {f.limit_source}.</div>}
      <p className="mt-2 text-body-sm text-muted-foreground">Текст не обрезается автоматически. Остальные файлы и ответы сохраняются.</p>
    </ClarifyCard>
  )
}

// 12 — broken BBCode
function BrokenBBCodeCard({ c, analysis, onFix, resolved }) {
  const f = (analysis.fields || []).find((x) => x.id === c.field)
  if (!f) return null
  const err = f.bbcode_error || { tag: 'b', fragment: f.value.slice(0, 80) }
  const fixed = 'Пиратские корабли — [b]морской рогалик[/b] о капитане, что бросает вызов проклятию Мёртвой оболочки. Собирайте команду и покоряйте штормовые воды.'
  return (
    <ClarifyCard resolved={resolved} tone="destructive" title={`В поле «${f.label}» не закрыт тег [${err.tag}]`} summary={`Уточнения завершены: разметка поля «${f.label}» исправлена`}>
      <div className="rounded-sm border border-border bg-muted p-3 text-body-sm text-foreground">
        {err.fragment.split(`[${err.tag}]`).map((part, i, arr) => <React.Fragment key={i}>{part}{i < arr.length - 1 && <mark className="rounded-sm bg-destructive/20 px-0.5 font-mono text-destructive">[{err.tag}]</mark>}</React.Fragment>)}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button className="h-9 rounded-sm" onClick={() => onFix({ field: { id: f.id, value: fixed }, clarify: { id: c.id, resolved: true } })}>Заменить файл</Button>
        <Button variant="secondary" className="h-9 rounded-sm">Скачать исходный файл</Button>
        <WeblateLink url="#" label="Открыть технические детали" />
      </div>
      <p className="mt-2 text-body-sm text-muted-foreground">Разметку не исправляем автоматически. Остальные файлы сохраняются.</p>
    </ClarifyCard>
  )
}

// 13 — empty file
function EmptyFileCard({ c, analysis, onFix, resolved }) {
  const f = (analysis.fields || []).find((x) => x.id === c.field)
  const label = f?.label || c.field
  const sample = 'Правовая информация: © 2026 Студия. Все права защищены.'
  return (
    <ClarifyCard resolved={resolved} title={`В файле ${f?.file || c.field} нет текста`} summary={`Уточнения завершены: поле «${label}» ${f ? 'заполнено' : 'удалено из набора'}`}>
      <p className="text-body-sm text-muted-foreground">Пустое исходное поле невозможно перевести.{c.required ? ' Это поле обязательно для набора.' : ' Поле необязательно — набор может продолжить без него.'}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {!c.required && <Button variant="secondary" className="h-9 rounded-sm" onClick={() => onFix({ field: { id: c.field, remove: true }, clarify: { id: c.id, resolved: true } })}>Удалить файл из набора</Button>}
        <Button className="h-9 rounded-sm" onClick={() => onFix({ field: { id: c.field, value: sample }, clarify: { id: c.id, resolved: true } })}>Заменить файл</Button>
      </div>
    </ClarifyCard>
  )
}

// ---------------------------------------------------------------- small shared bits
export function Chip({ active, onClick, children, disabled }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick}
      className={cn('rounded-sm border px-3 py-1 text-body-sm transition-colors',
        active ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-foreground hover:bg-muted',
        disabled && 'cursor-not-allowed opacity-50')}>
      {children}
    </button>
  )
}

export function Radio({ name, checked, onChange, children }) {
  return <label className="flex items-center gap-2 py-1 text-body-sm text-foreground"><input type="radio" name={name} checked={checked} onChange={onChange} />{children}</label>
}

export function InfoNote({ children }) {
  return <div className="flex items-start gap-2 rounded-sm bg-info-surface p-3 text-body-sm text-info-strong"><Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />{children}</div>
}

export function StoreFieldsPreview({ fields }) {
  return (
    <ul className="divide-y divide-border rounded-sm border border-border">
      {fields.map((f) => (
        <li key={f.id} className="flex items-center justify-between gap-3 px-3 py-2">
          <div className="min-w-0">
            <div className="text-body-sm text-foreground">{f.label} {!f.required && <span className="text-muted-foreground">· необязательно</span>}</div>
            <div className="truncate font-mono text-body-sm text-muted-foreground">{f.file}</div>
          </div>
          <div className="flex shrink-0 items-center gap-3"><FieldCounter f={f} /><StatusMark f={f} /></div>
        </li>
      ))}
    </ul>
  )
}
