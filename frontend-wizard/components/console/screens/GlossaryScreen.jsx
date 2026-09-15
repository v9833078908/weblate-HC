'use client'

import * as React from 'react'
import { Plus, Check, BookText } from 'lucide-react'
import * as api from '@/src/api/client'
import { AppShell } from '@/components/console/AppShell'
import { ConsoleCard, EmptyState } from '@/components/console/primitives'
import { DropZone } from '@/components/console/upload-parts'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'

const COLS = [
  { code: 'en', name: 'EN' },
  { code: 'fr', name: 'FR' },
  { code: 'ja', name: 'JA' },
  { code: 'de', name: 'DE' },
]

const RULE_CLS = {
  'обязательный': 'bg-info-surface text-info-strong',
  'не переводить': 'bg-muted text-muted-foreground',
  'запрещён': 'bg-background text-destructive border border-destructive',
  'обычный': 'bg-muted text-muted-foreground',
}

export function GlossaryScreen({ project, navigate }) {
  const slug = project.slug
  const [terms, setTerms] = React.useState(null)
  const [candidates, setCandidates] = React.useState([])
  const [added, setAdded] = React.useState({})
  const [newTerm, setNewTerm] = React.useState('')

  React.useEffect(() => {
    api.getGlossary(slug).then(setTerms)
    api.getGlossaryCandidates(slug).then(setCandidates)
  }, [slug])

  const addTerm = async () => {
    if (!newTerm.trim()) return
    await api.addGlossaryTerms(slug, [{ source: newTerm.trim(), rule: 'обычный', targets: {} }])
    setTerms((t) => [{ source: newTerm.trim(), note: '', rule: 'обычный', targets: {} }, ...(t || [])])
    setNewTerm('')
    toast('Термин добавлен')
  }

  const addCandidate = async (c) => {
    await api.addGlossaryTerms(slug, [{ source: c.term, note: c.why, targets: c.suggested }])
    setAdded((a) => ({ ...a, [c.term]: true }))
    setTerms((t) => [{ source: c.term, note: c.why, rule: 'обычный', targets: c.suggested }, ...(t || [])])
  }

  return (
    <AppShell project={project} active="glossary" navigate={navigate}
      breadcrumb={[{ label: 'Проекты', path: '/' }, { label: project.name, path: `/projects/${slug}` }, { label: 'Глоссарий' }]}>
      <h1 className="mb-6 text-heading-page text-foreground">Глоссарий</h1>

      <div className="space-y-6">
        <DropZone onPick={() => toast('Файл принят — добавятся только новые термины')} label="Добавить таблицу терминов (только новые термины добавляются, существующие не меняются)" formats="XLSX · CSV" />

        <ConsoleCard title="Термины">
          {terms === null ? (
            <div className="space-y-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10 rounded-sm" />)}</div>
          ) : terms.length === 0 ? (
            <EmptyState icon={BookText} title="Глоссарий пуст" description="Глоссарий закрепляет имена, названия и валюты — это заметно повышает качество перевода." />
          ) : (
            <>
              <div className="mb-3 flex items-center gap-2">
                <Input value={newTerm} onChange={(e) => setNewTerm(e.target.value)} placeholder="Новый термин…" className="h-9 max-w-xs" onKeyDown={(e) => e.key === 'Enter' && addTerm()} />
                <Button variant="secondary" className="h-9 rounded-sm" onClick={addTerm}><Plus className="h-4 w-4" />Добавить термин</Button>
              </div>
              <div className="overflow-x-auto rounded-sm border border-border">
                <table className="w-full text-body-sm">
                  <thead>
                    <tr className="bg-muted text-label-strong">
                      <th className="px-3 py-2 text-start font-semibold">Термин</th>
                      {COLS.map((c) => <th key={c.code} className="px-3 py-2 text-start font-semibold">{c.name}</th>)}
                      <th className="px-3 py-2 text-start font-semibold">Заметка</th>
                      <th className="px-3 py-2 text-start font-semibold">Правило</th>
                    </tr>
                  </thead>
                  <tbody>
                    {terms.map((t, i) => (
                      <tr key={i} className="h-10 border-t border-border">
                        <td className="px-3 font-mono text-foreground">{t.source}</td>
                        {COLS.map((c) => <td key={c.code} className="px-3 text-muted-foreground">{t.targets?.[c.code] || '—'}</td>)}
                        <td className="px-3 text-muted-foreground">{t.note || '—'}</td>
                        <td className="px-3"><span className={cn('rounded-sm px-2 py-0.5 text-label-caps uppercase', RULE_CLS[t.rule] || RULE_CLS['обычный'])}>{t.rule}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </ConsoleCard>

        {candidates.length > 0 && (
          <ConsoleCard title="Термины, найденные в лок-ките"
            actions={<Button variant="secondary" className="h-8 rounded-sm" onClick={() => candidates.forEach(addCandidate)}>Добавить все</Button>}>
            <ul className="divide-y divide-border">
              {candidates.map((c) => (
                <li key={c.term} className="flex items-start justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2"><span className="text-body-md text-foreground">{c.term}</span><span className="rounded-sm bg-muted px-1.5 py-0.5 text-label-caps text-muted-foreground">{c.why}</span></div>
                    <div className="mt-0.5 truncate text-body-sm text-muted-foreground">{c.context}</div>
                  </div>
                  <Button variant={added[c.term] ? 'ghost' : 'secondary'} className="h-8 shrink-0 rounded-sm" disabled={added[c.term]} onClick={() => addCandidate(c)}>
                    {added[c.term] ? <><Check className="h-4 w-4" />Добавлен</> : <><Plus className="h-4 w-4" />Добавить</>}
                  </Button>
                </li>
              ))}
            </ul>
          </ConsoleCard>
        )}
      </div>
    </AppShell>
  )
}
