'use client'

import * as React from 'react'
import { Lock, Check, Info } from 'lucide-react'
import * as api from '@/src/api/client'
import { AppShell } from '@/components/console/AppShell'
import { ConsoleCard, WeblateLink } from '@/components/console/primitives'
import { LANG_PRESET } from '@/components/console/upload-parts'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

const ADV_LABELS = {
  project: 'Проект в Weblate',
  addons: 'Дополнения (add-ons)',
  vcs: 'Система контроля версий',
  checks: 'Снятые проверки',
  machinery: 'Настройки машинного перевода',
}

function LockedField({ label, value }) {
  return (
    <TooltipProvider delayDuration={150}>
      <div className="space-y-1">
        <div className="flex items-center gap-1.5">
          <Label className="text-label-strong text-muted-foreground">{label}</Label>
          <Tooltip>
            <TooltipTrigger asChild><span><Lock className="h-3.5 w-3.5 text-muted-foreground" aria-hidden /></span></TooltipTrigger>
            <TooltipContent>Настраивает администратор AI-инструментов</TooltipContent>
          </Tooltip>
        </div>
        <Input value={value} readOnly disabled className="h-9" />
      </div>
    </TooltipProvider>
  )
}

export function SettingsScreen({ project, navigate }) {
  const slug = project.slug
  const [links, setLinks] = React.useState(null)
  const [email, setEmail] = React.useState('a.producer@studio.example')
  const active = React.useMemo(() => new Set(project.languages.map((l) => l.code)), [project])

  React.useEffect(() => { api.getAdvancedLinks(slug).then(setLinks) }, [slug])

  return (
    <AppShell project={project} active="settings" navigate={navigate}
      breadcrumb={[{ label: 'Проекты', path: '/' }, { label: project.name, path: `/projects/${slug}` }, { label: 'Настройки проекта' }]}>
      <h1 className="mb-6 text-heading-page text-foreground">Настройки проекта</h1>

      <div className="max-w-3xl space-y-6">
        <ConsoleCard title="Языки">
          <p className="mb-3 text-body-sm text-muted-foreground">Набор языков применяется ко всем загрузкам проекта.</p>
          <div className="flex flex-wrap gap-2">
            {LANG_PRESET.map((l) => {
              const on = active.has(l.code)
              return (
                <span key={l.code} className={cn('flex items-center gap-1.5 rounded-sm border px-3 py-1.5 text-body-sm', on ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-card text-muted-foreground')}>
                  {on && <Check className="h-3.5 w-3.5" />}{l.name}
                </span>
              )
            })}
          </div>
        </ConsoleCard>

        <ConsoleCard title="Профиль проекта">
          <div className="space-y-3">
            <WeblateLink url={`https://bdhc.hcgameloc.internal/titles/${slug}`} label="Открыть карточку игры в БДХК" navigate={navigate} />
            <div className="flex items-start gap-2 rounded-sm bg-muted p-3 text-body-sm text-muted-foreground">
              <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              Промпты формируются автоматически; редактируются в Weblate.
            </div>
          </div>
        </ConsoleCard>

        <ConsoleCard title="Сторы">
          <div className="space-y-3">
            {[['Steam', true], ['Google Play', true], ['App Store', false]].map(([name, on]) => (
              <div key={name} className="flex items-center justify-between">
                <span className="text-body-md text-foreground">{name}</span>
                <Switch defaultChecked={on} aria-label={name} />
              </div>
            ))}
          </div>
        </ConsoleCard>

        <ConsoleCard title="Уведомления">
          <div className="space-y-1">
            <Label className="text-label-strong">Email для писем о прогонах</Label>
            <Input value={email} onChange={(e) => setEmail(e.target.value)} className="h-9 max-w-md" />
          </div>
        </ConsoleCard>

        <ConsoleCard title="Служебные параметры">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <LockedField label="Маршрутизация моделей" value="gpt-5.2 · claude-sonnet-4.5" />
            <LockedField label="Месячный бюджет" value="$500.00" />
          </div>
        </ConsoleCard>

        <ConsoleCard title="Advanced">
          <ul className="space-y-2">
            {links ? Object.entries(links).map(([k, url]) => (
              <li key={k}><WeblateLink url={url} label={ADV_LABELS[k] || k} navigate={navigate} /></li>
            )) : <li className="text-body-sm text-muted-foreground">Загрузка…</li>}
          </ul>
        </ConsoleCard>
      </div>
    </AppShell>
  )
}
