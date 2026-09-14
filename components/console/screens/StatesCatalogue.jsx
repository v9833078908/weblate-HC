'use client'

import * as React from 'react'
import { AlertTriangle, XCircle, Radio, CheckCircle2, ShieldOff, FolderPlus } from 'lucide-react'
import { AppShell } from '@/components/console/AppShell'
import { StatusBadge, KindBadge, EmptyState } from '@/components/console/primitives'
import { DropZone } from '@/components/console/upload-parts'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

function Tile({ title, children }) {
  return (
    <div className="rounded border border-border bg-card">
      <div className="border-b border-border bg-accent px-4 py-2 text-label-caps uppercase text-muted-foreground">{title}</div>
      <div className="p-4">{children}</div>
    </div>
  )
}

export function StatesCatalogueScreen({ navigate }) {
  return (
    <AppShell navigate={navigate} breadcrumb={[{ label: 'Проекты', path: '/' }, { label: '/dev/states' }]}>
      <h1 className="mb-1 text-heading-page text-foreground">Каталог состояний</h1>
      <p className="mb-6 text-body-sm text-muted-foreground">Все пустые, загрузочные и ошибочные состояния рядом для ревью.</p>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Tile title="Статусы прогонов и языков">
          <div className="flex flex-wrap gap-2">
            <StatusBadge status="ready" /><StatusBadge status="completed" /><StatusBadge status="translating" />
            <StatusBadge status="running" /><StatusBadge status="queued" /><StatusBadge status="decisions" />
            <StatusBadge status="stalled" /><StatusBadge status="error" />
          </div>
        </Tile>

        <Tile title="Типы решений">
          <div className="flex flex-wrap gap-2">
            <KindBadge kind="blocking" /><KindBadge kind="judge_critical" /><KindBadge kind="judge_note" />
          </div>
        </Tile>

        <Tile title="Пусто: поиск проектов">
          <EmptyState icon={FolderPlus} title="Ничего не найдено" description="По вашему запросу проектов нет." />
        </Tile>

        <Tile title="Пусто: всё решено">
          <EmptyState icon={CheckCircle2} title="Всё решено" description="9 из 9 языков готовы к выгрузке." />
        </Tile>

        <Tile title="Загрузка: скелетон таблицы">
          <div className="space-y-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10 rounded-sm" />)}</div>
        </Tile>

        <Tile title="Загрузка: разбор файла">
          <DropZone onPick={() => {}} parsing fileName="pirate-ships-loc-kit.xlsx" />
        </Tile>

        <Tile title="Ошибка: прогон остановлен">
          <div className="flex items-start gap-3 rounded border border-destructive bg-background p-4">
            <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden />
            <div>
              <div className="text-label-strong text-foreground">Прогон остановлен с ошибкой</div>
              <div className="text-body-sm text-muted-foreground">Модель вернула ошибку аутентификации (401). Незавершённые строки не потеряны.</div>
              <Button className="mt-3 h-9 rounded-sm">Перезапустить незавершённое</Button>
            </div>
          </div>
        </Tile>

        <Tile title="Нет обновлений">
          <div className="flex items-start gap-3 rounded border border-input bg-background p-4">
            <Radio className="mt-0.5 h-5 w-5 shrink-0 animate-pulse text-muted-foreground" aria-hidden />
            <div>
              <div className="text-label-strong text-foreground">Нет обновлений</div>
              <div className="text-body-sm text-muted-foreground">Обработчик не отвечает 4 мин; незавершённые строки не потеряны.</div>
            </div>
          </div>
        </Tile>

        <Tile title="Судья не настроен">
          <div className="flex items-start gap-3 rounded border border-border bg-muted p-4">
            <ShieldOff className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
            <div>
              <div className="text-label-strong text-foreground">Проверка качества недоступна</div>
              <div className="text-body-sm text-muted-foreground">Администратор AI-инструментов ещё не настроил судью.</div>
            </div>
          </div>
        </Tile>

        <Tile title="Ошибка выгрузки">
          <div className="flex items-start gap-3 rounded border border-destructive bg-background p-4">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden />
            <div>
              <div className="text-label-strong text-foreground">Не удалось собрать файл</div>
              <div className="text-body-sm text-muted-foreground">Попробуйте ещё раз через несколько минут.</div>
            </div>
          </div>
        </Tile>
      </div>

      <div className="mt-8 flex flex-wrap gap-3">
        <Button variant="secondary" className="h-9 rounded-sm" onClick={() => navigate('/emails/completion')}>Письмо: завершение</Button>
        <Button variant="secondary" className="h-9 rounded-sm" onClick={() => navigate('/emails/failure')}>Письмо: ошибка</Button>
      </div>
    </AppShell>
  )
}
