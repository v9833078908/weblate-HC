'use client'
import { AppShell } from '@/components/console/AppShell'
import { Button } from '@/components/ui/button'
import { ExternalLink, ArrowLeft } from 'lucide-react'
export function AdvancedPlaceholderScreen({ url, navigate }) {
  return (
    <AppShell navigate={navigate} breadcrumb={[{ label: 'Проекты', path: '/' }, { label: 'Advanced → Weblate' }]}>
      <div className="mx-auto max-w-2xl py-10 text-center">
        <ExternalLink className="mx-auto mb-4 h-10 w-10 text-muted-foreground" aria-hidden />
        <h1 className="text-heading-page text-foreground">Открывается в Weblate</h1>
        <p className="mt-2 text-body-md text-muted-foreground">В рабочей системе здесь откроется классический интерфейс Weblate. Это прототип — показываем адрес назначения:</p>
        <div className="mt-4 break-all rounded-sm border border-border bg-muted p-3 font-mono text-body-sm text-foreground">{url || '—'}</div>
        <Button variant="secondary" className="mt-6 h-9 rounded-sm" onClick={() => window.history.back()}><ArrowLeft className="h-4 w-4" />Назад</Button>
      </div>
    </AppShell>
  )
}
