'use client'

import * as React from 'react'
import { Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { GlossaryWorkspace } from '@/components/console/wizard/GlossaryWorkspace'
import { langName } from '@/components/console/upload-parts'
import { useUni, finalTargetCodes } from '@/components/console/wizard2/ctx'
import { Radio } from '@/components/console/wizard2/clarify'
import { cn } from '@/lib/utils'

const STORE_STAGES = ['Читаем тексты площадки', 'Ищем названия и игровые понятия', 'Сравниваем с глоссарием', 'Готовим предложения']

function Q({ n, title, hint, children }) {
  return (
    <div className="rounded border border-border p-4">
      <div className="mb-1 flex items-center gap-2"><span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-muted text-label-caps text-muted-foreground">{n}</span><h3 className="text-label-strong text-foreground">{title}</h3></div>
      {hint && <p className="mb-2 ms-8 text-body-sm text-muted-foreground">{hint}</p>}
      <div className="ms-8">{children}</div>
    </div>
  )
}

export function StageContext() {
  const { analysis, answers, setAnswers } = useUni()
  const isStore = analysis.kind === 'store'
  const source = answers.source_language || analysis.source_language
  const targets = finalTargetCodes(answers.languages, source)
  const cjkNeeded = targets.some((c) => ['ja', 'ko'].includes(c))

  const p = answers.profile || {}
  const setP = (patch) => setAnswers({ profile: { ...(answers.profile || {}), ...patch } })

  const g = answers.glossary || { skipped: false, selected: {} }
  const setG = (patch) => setAnswers({ glossary: { ...(answers.glossary || { skipped: false, selected: {} }), ...patch } })

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-heading-card text-foreground">Контекст и термины</h2>
        <p className="mt-1 text-body-sm text-muted-foreground">Ответы задают тон перевода и служат опорой для проверки качества. Промпты формируются автоматически — ничего не выдумываем.</p>
      </div>

      <div className="space-y-4">
        <Q n={1} title={isStore ? 'Тон текстов для стора' : 'Обращение к игроку в интерфейсе'}>
          <Radio name="rui" checked={p.register_ui === 'ты'} onChange={() => setP({ register_ui: 'ты' })}>На «ты»</Radio>
          <Radio name="rui" checked={p.register_ui === 'вы'} onChange={() => setP({ register_ui: 'вы' })}>На «вы»</Radio>
          <Radio name="rui" checked={p.register_ui === 'formal'} onChange={() => setP({ register_ui: 'formal' })}>Формально</Radio>
        </Q>

        {!isStore && (
          <Q n={2} title="Обращение в диалогах">
            <Radio name="rd" checked={p.register_dialogue === 'by_speaker'} onChange={() => setP({ register_dialogue: 'by_speaker' })}>Как в оригинале (по говорящему)</Radio>
            <Radio name="rd" checked={p.register_dialogue === 'ty'} onChange={() => setP({ register_dialogue: 'ty' })}>Всегда на «ты»</Radio>
            <Radio name="rd" checked={p.register_dialogue === 'vy'} onChange={() => setP({ register_dialogue: 'vy' })}>Всегда на «вы»</Radio>
          </Q>
        )}

        <Q n={isStore ? 2 : 3} title="Мат и грубость сохраняем в силе источника?">
          <Radio name="prof" checked={p.profanity === 'keep'} onChange={() => setP({ profanity: 'keep' })}>Сохранять в силе источника — не смягчать и не добавлять</Radio>
          <Radio name="prof" checked={p.profanity === 'soften'} onChange={() => setP({ profanity: 'soften', profanity_level: p.profanity_level || 'light' })}>Смягчать</Radio>
          {p.profanity === 'soften' && (
            <div className="mt-1 flex gap-2">
              <button onClick={() => setP({ profanity_level: 'light' })} className={cn('rounded-sm border px-2 py-1 text-body-sm', p.profanity_level === 'light' ? 'border-primary bg-primary text-primary-foreground' : 'border-border')}>до лёгкой грубости</button>
              <button onClick={() => setP({ profanity_level: 'none' })} className={cn('rounded-sm border px-2 py-1 text-body-sm', p.profanity_level === 'none' ? 'border-primary bg-primary text-primary-foreground' : 'border-border')}>без мата</button>
            </div>
          )}
        </Q>

        {cjkNeeded && (
          <Q n={isStore ? 3 : 4} title="Вежливость для японского и корейского">
            <Radio name="cjk" checked={p.cjk_politeness === 'neutral'} onChange={() => setP({ cjk_politeness: 'neutral' })}>Нейтрально-вежливая речь везде</Radio>
            <Radio name="cjk" checked={p.cjk_politeness === 'as_shipped'} onChange={() => setP({ cjk_politeness: 'as_shipped' })}>Как в существующих переводах</Radio>
            <Radio name="cjk" checked={p.cjk_politeness === 'mixed'} onChange={() => setP({ cjk_politeness: 'mixed' })}>Интерфейс нейтрально, диалоги по говорящему</Radio>
          </Q>
        )}
      </div>

      {/* Glossary — recommended, skippable */}
      <div className="rounded border border-border">
        <div className="flex items-center justify-between gap-3 border-b border-border bg-accent px-4 py-2.5">
          <div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-primary" /><h3 className="text-heading-card text-foreground">Термины и глоссарий</h3><span className="rounded-sm bg-muted px-1.5 py-0.5 text-label-caps uppercase text-muted-foreground">рекомендуем</span></div>
          {!g.skipped
            ? <Button variant="ghost" className="h-8 rounded-sm" onClick={() => setG({ skipped: true })}>Пропустить</Button>
            : <Button variant="ghost" className="h-8 rounded-sm" onClick={() => setG({ skipped: false })}>Вернуть</Button>}
        </div>
        <div className="p-4">
          {g.skipped ? (
            <p className="text-body-sm text-muted-foreground">Глоссарий пропущен. Можно добавить термины позже на странице «Глоссарий» — прогон запустится и без него.</p>
          ) : (
            <GlossaryWorkspace
              slug={analysis.store ? analysis.store : 'pirate-ships'}
              uploadId={analysis.id}
              mode="wizard"
              targetLanguages={targets.length ? targets : ['en', 'de', 'fr', 'ja']}
              categories={{ item: true, character: true, place: true, concept: true }}
              onStageChange={(sel) => setG({ selected: sel })}
              stageLabels={isStore ? STORE_STAGES : undefined}
              sourceLabel={isStore ? 'тексты площадки (текущая версия)' : undefined}
              beforeTitle={isStore ? 'Предложить термины из загруженных файлов' : undefined}
              beforeDesc={isStore ? 'AI найдёт названия и повторяющиеся игровые понятия. Ничего не добавится без вашего решения.' : undefined}
            />
          )}
        </div>
      </div>
    </section>
  )
}
