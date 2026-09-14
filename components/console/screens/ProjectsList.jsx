'use client'

import * as React from 'react'
import { Search, Plus, FolderPlus } from 'lucide-react'
import * as api from '@/src/api/client'
import { AppShell } from '@/components/console/AppShell'
import { StatusBadge, Money, EmptyState } from '@/components/console/primitives'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger,
} from '@/components/ui/dialog'

function fmt(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const months = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сент', 'окт', 'нояб', 'дек']
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${d.getDate()} ${months[d.getMonth()]}, ${hh}:${mm}`
}

function ProjectCard({ p, navigate }) {
  return (
    <button
      onClick={() => navigate(`/projects/${p.slug}`)}
      className="group flex flex-col rounded border border-border bg-card p-4 text-start transition-colors hover:border-primary hover:bg-muted"
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-heading-card text-foreground group-hover:text-primary">{p.name}</h3>
        {p.decisions_count > 0 && (
          <span className="inline-flex items-center rounded-sm bg-warning-surface px-2 py-0.5 text-label-caps uppercase text-warning">
            {p.decisions_count} на решение
          </span>
        )}
      </div>

      {p.localized ? (
        <>
          <dl className="mt-4 grid grid-cols-2 gap-y-3 text-body-sm">
            <dt className="text-muted-foreground">Готово к выгрузке</dt>
            <dd className="text-end tabular-nums text-foreground">{p.ready_count} / {p.language_count}</dd>
            <dt className="text-muted-foreground">Потрачено в {p.month_label}</dt>
            <dd className="text-end text-foreground"><Money value={p.spend_month} /></dd>
          </dl>
          <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
            {p.last_run ? (
              <StatusBadge status={p.last_run.status} />
            ) : <span className="text-body-sm text-muted-foreground">Прогонов нет</span>}
            {p.last_run && <span className="text-body-sm text-muted-foreground">{fmt(p.last_run.when)}</span>}
          </div>
        </>
      ) : (
        <div className="mt-4 flex flex-1 items-end">
          <p className="text-body-sm text-muted-foreground">Локализация не настроена</p>
        </div>
      )}
    </button>
  )
}

export function ProjectsListScreen({ navigate }) {
  const [projects, setProjects] = React.useState(null)
  const [q, setQ] = React.useState('')
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState('')
  const [creating, setCreating] = React.useState(false)

  React.useEffect(() => { api.getProjects().then(setProjects) }, [])

  const sorted = React.useMemo(() => {
    if (!projects) return null
    return [...projects]
      .filter((p) => p.name.toLowerCase().includes(q.toLowerCase()))
      .sort((a, b) => (b.blocking_count - a.blocking_count) || (b.decisions_count - a.decisions_count))
  }, [projects, q])

  const create = async () => {
    if (!name.trim()) return
    setCreating(true)
    const p = await api.createProject(name)
    setCreating(false)
    setOpen(false)
    navigate(`/projects/${p.slug}`)
  }

  return (
    <AppShell navigate={navigate}>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-heading-page text-foreground">Проекты</h1>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Поиск проекта" aria-label="Поиск проекта" className="h-9 w-64 ps-9" />
          </div>
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button className="h-9 rounded-sm"><Plus className="h-4 w-4" />Создать проект</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Создать проект</DialogTitle>
                <DialogDescription>
                  Будет создан пустой проект. Локализацию вы настроите на следующем шаге, загрузив лок-кит.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-2">
                <Label htmlFor="pname" className="text-label-strong">Название проекта</Label>
                <Input id="pname" value={name} onChange={(e) => setName(e.target.value)} placeholder="Например, Pirate Ships" className="h-9" autoFocus
                  onKeyDown={(e) => e.key === 'Enter' && create()} />
                {name.trim() && (
                  <p className="text-body-sm text-muted-foreground">
                    Адрес: <span className="font-mono">{name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || 'proekt'}</span>
                  </p>
                )}
              </div>
              <DialogFooter>
                <Button variant="secondary" className="h-9 rounded-sm" onClick={() => setOpen(false)}>Отмена</Button>
                <Button className="h-9 rounded-sm" disabled={!name.trim() || creating} onClick={create}>
                  {creating ? 'Создание…' : 'Создать'}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {sorted === null ? (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-44 rounded" />)}
        </div>
      ) : sorted.length === 0 ? (
        <EmptyState icon={FolderPlus} title="Ничего не найдено" description="По вашему запросу проектов нет. Измените запрос или создайте новый проект." />
      ) : (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {sorted.map((p) => <ProjectCard key={p.slug} p={p} navigate={navigate} />)}
        </div>
      )}
    </AppShell>
  )
}
