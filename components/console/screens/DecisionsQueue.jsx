'use client'

import * as React from 'react'
import { Search, ExternalLink, Sparkles, Check, Keyboard, CheckCircle2, MoreHorizontal, Copy } from 'lucide-react'
import * as api from '@/src/api/client'
import { AppShell } from '@/components/console/AppShell'
import { KindBadge, EmptyState } from '@/components/console/primitives'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Checkbox } from '@/components/ui/checkbox'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { Sheet, SheetContent } from '@/components/ui/sheet'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { toast } from 'sonner'

const KIND_LABEL = { blocking: 'Блокирует выгрузку', judge_critical: 'Судья: критично', judge_note: 'Судья: замечание' }

function HighlightedSource({ text, ranges }) {
  if (!ranges || !ranges.length) return <>{text}</>
  const r = ranges.find((x) => x.lang === 'source')
  if (!r) return <>{text}</>
  return (
    <>
      {text.slice(0, r.from)}
      <mark className="rounded-sm bg-highlight-glossary px-0.5 text-foreground">{text.slice(r.from, r.to)}</mark>
      {text.slice(r.to)}
    </>
  )
}

function StringSheet({ decision, open, onOpenChange, onRepair, onAccept, repairing, navigate }) {
  const [acceptOpen, setAcceptOpen] = React.useState(false)
  const [reason, setReason] = React.useState('')
  if (!decision) return null

  const isBlocking = decision.kind === 'blocking'
  const actions = [
    { key: 'fix', label: 'Исправить с помощью AI', primary: true, onClick: () => onRepair(decision) },
    { key: 'weblate', label: 'Открыть в Weblate', onClick: () => navigate('/advanced?url=' + encodeURIComponent(decision.advanced_url)) },
  ]
  if (!isBlocking) actions.push({ key: 'accept', label: 'Принять как есть', onClick: () => { setReason(''); setAcceptOpen(true) } })

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-[560px]">
        {/* Z-pattern: key+lang top-left, actions top-right */}
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="code-cell text-foreground">{decision.key}</div>
            <div className="mt-1 text-body-sm text-muted-foreground">{decision.language_name} · {decision.language}</div>
          </div>
          <div className="flex items-center gap-2">
            <KindBadge kind={decision.kind} />
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" className="h-8 w-8 rounded-sm" aria-label="Ещё"><MoreHorizontal className="h-4 w-4" /></Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => { navigator.clipboard?.writeText(decision.key); toast('Ключ скопирован') }}><Copy className="h-4 w-4" />Скопировать ключ</DropdownMenuItem>
                <DropdownMenuItem onClick={() => navigate('/advanced?url=' + encodeURIComponent(decision.advanced_url))}><ExternalLink className="h-4 w-4" />Открыть в Weblate</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        <div className="mt-6 space-y-4">
          <div>
            <div className="mb-1 text-label-caps uppercase text-muted-foreground">Исходный текст</div>
            <div className="code-cell rounded-sm border border-border bg-muted p-3 text-foreground">
              <HighlightedSource text={decision.source} ranges={decision.glossary} />
            </div>
          </div>
          <div>
            <div className="mb-1 text-label-caps uppercase text-muted-foreground">Перевод · {decision.language}</div>
            <div className="code-cell rounded-sm border border-border bg-card p-3 text-foreground">{decision.target}</div>
          </div>
          <div>
            <div className="mb-1 text-label-caps uppercase text-muted-foreground">Обратный перевод</div>
            {decision.back_translation ? (
              <div className="code-cell rounded-sm border border-border bg-card p-3 text-foreground">{decision.back_translation}</div>
            ) : (
              <div className="rounded-sm border border-dashed border-border p-3 text-body-sm text-muted-foreground">нет — появится после проверки судьёй</div>
            )}
          </div>

          <div className="rounded border border-border bg-accent/40 p-3">
            <div className="mb-1 text-label-caps uppercase text-muted-foreground">Причина</div>
            <div className="text-body-md text-foreground">{decision.reason}</div>
          </div>

          {repairing && (
            <div className="rounded-sm border border-info-surface bg-info-surface p-3 text-body-sm text-info-strong">
              Строка отправлена на исправление. Результат появится после прогона — статус можно отслеживать в прогоне.
            </div>
          )}

          <div>
            <div className="mb-2 text-label-caps uppercase text-muted-foreground">История</div>
            <ol className="space-y-2">
              {decision.history.map((h) => (
                <li key={h.attempt} className="flex items-start gap-2 text-body-sm">
                  <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-muted text-label-caps text-muted-foreground">{h.attempt}</span>
                  <span className="text-muted-foreground"><span className="font-mono">{h.kind}</span> — {h.note}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>

        {/* action bar */}
        <div className="mt-8 flex flex-wrap items-center gap-2 border-t border-border pt-4">
          {actions.map((a, i) => (
            <Button
              key={a.key}
              variant={a.primary ? 'default' : 'secondary'}
              className="h-9 rounded-sm"
              disabled={a.key === 'fix' && repairing}
              onClick={a.onClick}
            >
              <span className="me-1 inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-black/10 px-1 text-[10px]">{i + 1}</span>
              {a.key === 'fix' && <Sparkles className="h-4 w-4" />}
              {a.key === 'weblate' && <ExternalLink className="h-4 w-4" />}
              {a.key === 'accept' && <Check className="h-4 w-4" />}
              {a.key === 'fix' && repairing ? 'В очереди на исправление' : a.label}
            </Button>
          ))}
        </div>

        <Dialog open={acceptOpen} onOpenChange={setAcceptOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Принять как есть</DialogTitle>
              <DialogDescription>Укажите короткую причину — она сохранится в истории строки.</DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="accept-reason" className="text-label-strong">Причина</Label>
              <Textarea id="accept-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Например: осознанная стилистическая правка" rows={3} autoFocus />
            </div>
            <DialogFooter>
              <Button variant="secondary" className="h-9 rounded-sm" onClick={() => setAcceptOpen(false)}>Отмена</Button>
              <Button className="h-9 rounded-sm" disabled={!reason.trim()} onClick={() => { setAcceptOpen(false); onAccept(decision, reason.trim()) }}>Принять</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </SheetContent>
    </Sheet>
  )
}

export function DecisionsQueueScreen({ project, initialQuery = {}, navigate }) {
  const slug = project.slug
  const [all, setAll] = React.useState(null)
  const [lang, setLang] = React.useState(initialQuery.lang || '')
  const [kind, setKind] = React.useState(initialQuery.kind || '')
  const [content, setContent] = React.useState(initialQuery.content || '')
  const [q, setQ] = React.useState(initialQuery.q || '')
  const [activeIdx, setActiveIdx] = React.useState(-1)
  const [sheetOpen, setSheetOpen] = React.useState(false)
  const [selected, setSelected] = React.useState({})
  const [repairing, setRepairing] = React.useState({})
  const [removed, setRemoved] = React.useState({})
  const [cheat, setCheat] = React.useState(false)
  const searchRef = React.useRef(null)
  const rowsRef = React.useRef([])

  React.useEffect(() => { api.getDecisions(slug, {}).then(setAll) }, [slug])

  const pushUrl = React.useCallback((next) => {
    const params = new URLSearchParams()
    if (next.lang) params.set('lang', next.lang)
    if (next.kind) params.set('kind', next.kind)
    if (next.content) params.set('content', next.content)
    if (next.q) params.set('q', next.q)
    const qs = params.toString()
    window.history.replaceState({}, '', `/projects/${slug}/decisions${qs ? '?' + qs : ''}`)
  }, [slug])

  React.useEffect(() => { pushUrl({ lang, kind, content, q }) }, [lang, kind, content, q, pushUrl])

  const matchKind = (d) => !kind || (kind === 'blocking' ? d.kind === 'blocking' : d.kind !== 'blocking')
  const matchContent = (d) => !content || (content === 'stores' ? d.content.startsWith('store') : d.content === 'loc-kit')
  const matchQ = (d) => !q || d.key.toLowerCase().includes(q.toLowerCase())

  const visible = React.useMemo(() => {
    if (!all) return null
    return all.filter((d) => !removed[d.unit_id] && matchKind(d) && matchContent(d) && matchQ(d) && (!lang || d.language === lang))
  }, [all, lang, kind, content, q, removed])

  const langCounts = React.useMemo(() => {
    if (!all) return {}
    const base = all.filter((d) => !removed[d.unit_id] && matchKind(d) && matchContent(d) && matchQ(d))
    const m = {}
    base.forEach((d) => { m[d.language] = (m[d.language] || 0) + 1 })
    return m
  }, [all, kind, content, q, removed])

  const active = activeIdx >= 0 && visible ? visible[activeIdx] : null

  const doRepair = async (d) => {
    setRepairing((r) => ({ ...r, [d.unit_id]: true }))
    await api.repairDecision(d.unit_id)
    toast(`Отправлено на исправление: ${d.key} · ${d.language}`, { duration: 6000 })
  }

  const doAccept = async (d, reason) => {
    await api.acceptDecision(d.unit_id, reason)
    setRemoved((r) => ({ ...r, [d.unit_id]: true }))
    setSheetOpen(false)
    const restore = () => setRemoved((r) => { const n = { ...r }; delete n[d.unit_id]; return n })
    toast(`Принято: ${d.key} · ${d.language}`, { duration: 6000, action: { label: 'Отменить', onClick: restore } })
  }

  const bulkFix = async () => {
    const ids = Object.keys(selected).filter((k) => selected[k])
    for (const id of ids) { setRepairing((r) => ({ ...r, [id]: true })); await api.repairDecision(id) }
    toast(`Отправлено на исправление: ${ids.length} строк`, { duration: 6000 })
    setSelected({})
  }

  // keyboard
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.key === '?') { setCheat(true); return }
      if (e.key === '/' && !sheetOpen) { e.preventDefault(); searchRef.current?.focus(); return }
      if (!visible || visible.length === 0) return
      const tag = document.activeElement?.tagName
      const typing = tag === 'INPUT' || tag === 'TEXTAREA'
      if (sheetOpen && active) {
        if (e.key === 'Escape') { setSheetOpen(false); return }
        if (['1', '2', '3'].includes(e.key) && !typing) {
          e.preventDefault()
          const isBlocking = active.kind === 'blocking'
          if (e.key === '1') doRepair(active)
          else if (e.key === '2') navigate('/advanced?url=' + encodeURIComponent(active.advanced_url))
          else if (e.key === '3' && !isBlocking) { /* accept handled via dialog */ }
        }
        return
      }
      if (typing) return
      if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIdx((i) => Math.min((i < 0 ? -1 : i) + 1, visible.length - 1)) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); setActiveIdx((i) => Math.max((i < 0 ? 0 : i) - 1, 0)) }
      else if (e.key === 'Enter' && activeIdx >= 0) { e.preventDefault(); setSheetOpen(true) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [visible, activeIdx, sheetOpen, active]) // eslint-disable-line

  React.useEffect(() => {
    if (activeIdx >= 0 && rowsRef.current[activeIdx]) rowsRef.current[activeIdx].focus()
  }, [activeIdx])

  const selectedCount = Object.values(selected).filter(Boolean).length

  const Chip = ({ on, onClick, children }) => (
    <button onClick={onClick} className={cn('rounded-sm border px-3 py-1.5 text-body-sm transition-colors', on ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-foreground hover:bg-muted')}>{children}</button>
  )

  return (
    <AppShell
      project={project}
      active="decisions"
      navigate={navigate}
      breadcrumb={[{ label: 'Проекты', path: '/' }, { label: project.name, path: `/projects/${slug}` }, { label: 'Требуют решения' }]}
    >
      <div className="mb-4 flex items-center justify-between gap-3">
        <h1 className="text-heading-page text-foreground">Требуют решения</h1>
        <Button variant="ghost" className="h-9 rounded-sm" onClick={() => setCheat(true)}><Keyboard className="h-4 w-4" />Горячие клавиши</Button>
      </div>

      {/* filters */}
      <div className="mb-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-label-caps uppercase text-muted-foreground">Язык</span>
          <Chip on={!lang} onClick={() => setLang('')}>Все</Chip>
          {project.languages.filter((l) => langCounts[l.code]).map((l) => (
            <Chip key={l.code} on={lang === l.code} onClick={() => setLang(l.code)}>{l.name} <span className="opacity-70">{langCounts[l.code]}</span></Chip>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <span className="text-label-caps uppercase text-muted-foreground">Тип</span>
            <Chip on={!kind} onClick={() => setKind('')}>Все</Chip>
            <Chip on={kind === 'blocking'} onClick={() => setKind('blocking')}>Блокируют выгрузку</Chip>
            <Chip on={kind === 'judge'} onClick={() => setKind('judge')}>Судья</Chip>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-label-caps uppercase text-muted-foreground">Содержимое</span>
            <Chip on={!content} onClick={() => setContent('')}>Всё</Chip>
            <Chip on={content === 'loc-kit'} onClick={() => setContent('loc-kit')}>Лок-кит</Chip>
            <Chip on={content === 'stores'} onClick={() => setContent('stores')}>Сторы</Chip>
          </div>
          <div className="relative ms-auto">
            <Search className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск по ключу  (/)" aria-label="Поиск по ключу" className="h-9 w-64 ps-9" />
          </div>
        </div>
      </div>

      {selectedCount > 0 && (
        <div className="mb-3 flex items-center justify-between rounded-sm border border-border bg-muted px-3 py-2">
          <span className="text-body-sm text-foreground">Выбрано строк: {selectedCount}</span>
          <Button className="h-8 rounded-sm" onClick={bulkFix}><Sparkles className="h-4 w-4" />Исправить с помощью AI ({selectedCount})</Button>
        </div>
      )}

      {visible === null ? (
        <div className="space-y-2">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-10 rounded-sm" />)}</div>
      ) : visible.length === 0 ? (
        <EmptyState icon={CheckCircle2} title="Всё решено" description={`${project.ready_count} из ${project.language_count} языков готовы к выгрузке.`}
          action={<Button className="h-9 rounded-sm" onClick={() => navigate(`/projects/${slug}/download`)}>Скачать результат</Button>} />
      ) : (
        <div className="overflow-hidden rounded border border-border">
          <table className="w-full border-collapse text-body-sm">
            <thead>
              <tr className="bg-muted text-label-strong">
                <th className="w-10 px-3 py-2"></th>
                <th className="px-3 py-2 text-start font-semibold">Ключ</th>
                <th className="px-3 py-2 text-start font-semibold">Язык</th>
                <th className="px-3 py-2 text-start font-semibold">Исходный текст</th>
                <th className="px-3 py-2 text-start font-semibold">Причина</th>
                <th className="px-3 py-2 text-start font-semibold">Тип</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((d, i) => (
                <tr
                  key={d.unit_id}
                  ref={(el) => (rowsRef.current[i] = el)}
                  tabIndex={0}
                  aria-label={`строка ${i + 1} из ${visible.length}, ${d.language_name}, ${KIND_LABEL[d.kind]}`}
                  onFocus={() => setActiveIdx(i)}
                  onClick={() => { setActiveIdx(i); setSheetOpen(true) }}
                  onKeyDown={(e) => { if (e.key === 'Enter') { setActiveIdx(i); setSheetOpen(true) } }}
                  className={cn('h-10 cursor-pointer border-t border-border align-middle hover:bg-accent', activeIdx === i && 'bg-accent')}
                >
                  <td className="px-3" onClick={(e) => e.stopPropagation()}>
                    <Checkbox checked={!!selected[d.unit_id]} onCheckedChange={(v) => setSelected((s) => ({ ...s, [d.unit_id]: !!v }))} aria-label="Выбрать строку" />
                  </td>
                  <td className="max-w-[180px] truncate px-3"><span className="code-cell text-foreground">{d.key}</span></td>
                  <td className="whitespace-nowrap px-3 text-muted-foreground">{d.language}</td>
                  <td className="max-w-[260px] truncate px-3 text-muted-foreground"><span className="code-cell">{d.source}</span></td>
                  <td className="max-w-[280px] truncate px-3 text-foreground">
                    {repairing[d.unit_id] ? <span className="text-info-strong">В очереди на исправление</span> : d.reason}
                  </td>
                  <td className="px-3"><KindBadge kind={d.kind} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <StringSheet
        decision={active}
        open={sheetOpen}
        onOpenChange={(v) => { setSheetOpen(v); if (!v && active) rowsRef.current[activeIdx]?.focus() }}
        onRepair={doRepair}
        onAccept={doAccept}
        repairing={active ? !!repairing[active.unit_id] : false}
        navigate={navigate}
      />

      <Dialog open={cheat} onOpenChange={setCheat}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Горячие клавиши</DialogTitle>
            <DialogDescription>Очередь решений управляется с клавиатуры.</DialogDescription>
          </DialogHeader>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-body-sm">
            <dt className="font-mono text-muted-foreground">↑ / ↓</dt><dd>Перемещение по строкам</dd>
            <dt className="font-mono text-muted-foreground">Enter</dt><dd>Открыть строку</dd>
            <dt className="font-mono text-muted-foreground">Esc</dt><dd>Закрыть панель</dd>
            <dt className="font-mono text-muted-foreground">1 / 2 / 3</dt><dd>Действия по порядку</dd>
            <dt className="font-mono text-muted-foreground">/</dt><dd>Фокус на поиск</dd>
          </dl>
        </DialogContent>
      </Dialog>
    </AppShell>
  )
}
