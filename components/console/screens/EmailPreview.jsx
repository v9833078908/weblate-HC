'use client'
import * as React from 'react'
import { getEmails } from '@/src/api/client'
import { AppShell } from '@/components/console/AppShell'
import { Button } from '@/components/ui/button'
export function EmailPreviewScreen({ kind = 'completion', navigate }) {
  const emails = getEmails()
  const email = emails[kind] || emails.completion
  return (
    <AppShell navigate={navigate} breadcrumb={[{ label: 'Проекты', path: '/' }, { label: 'Письмо' }]}>
      <div className="mx-auto max-w-2xl">
        <div className="mb-4 flex gap-2">
          <Button variant={kind === 'completion' ? 'default' : 'secondary'} className="h-8 rounded-sm" onClick={() => navigate('/emails/completion')}>Завершение</Button>
          <Button variant={kind === 'failure' ? 'default' : 'secondary'} className="h-8 rounded-sm" onClick={() => navigate('/emails/failure')}>Ошибка</Button>
        </div>
        <div className="overflow-hidden rounded border border-border">
          <div className="border-b border-border bg-muted px-4 py-3 text-body-sm">
            <div className="text-muted-foreground">От: <span className="text-foreground">{email.from}</span></div>
            <div className="text-muted-foreground">Кому: <span className="text-foreground">{email.to}</span></div>
            <div className="mt-1 text-heading-card text-foreground">{email.subject}</div>
          </div>
          <div className="space-y-4 bg-card p-6">
            <h2 className="text-heading-card text-foreground">{email.heading}</h2>
            <p className="text-body-md text-foreground">{email.body}</p>
            <p className="text-body-sm text-muted-foreground">{email.cost}</p>
            <Button className="h-9 rounded-sm" onClick={() => navigate(email.cta_url)}>{email.cta}</Button>
          </div>
        </div>
      </div>
    </AppShell>
  )
}
