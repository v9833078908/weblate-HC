# HCGameLoc: стоимость и производительность LLM-судей

Исследование 2026-09-17. Никаких изменений продукта/прода и новых платных LLM-запросов.

**Финансовая оговорка:** все 5997 Anvil usage rows имеют `cost_usd=None`. Денежные суммы ниже — сценарные расчёты по сохранённым ответам с usage и ставкам `/model/info`, не invoice и не доказанная экономия. 38 HTTP-попыток без usage не оценены. Экстраполяция на 57996 строк не является стоимостью текущего остатка: актуальный уникальный eligible scope не пересчитывался.

**Статус рекомендаций:** устранение повторного исполнителя — исправление подтверждённого дефекта; изменения batch/model/reasoning/cascade — только кандидаты на отдельно согласованный A/B с проверкой качества, parse-rate и timeout-rate.

## 1. Область и доказательства

Прочитаны разговоры omp 01a0ae1c-371a-777c-8434-a2c70796cd59 и 01a0ae23-c8cc-73d5-937f-9a933ab1ce7d из локальных JSONL. Пять параллельных агентов исследовали исполнение, payload/accounting, валидацию качества, внешние best practices и ведущие TMS. С отдельного разрешения пользователя выполнены read-only SQL-пробы на production (SET TRANSACTION READ ONLY, statement_timeout=30s) и GET /model/info на корпоративном LiteLLM. Ключи и тексты пользовательских переводов в выгрузки не включены; payload-проба возвращала только размеры полей. Никаких POST к LLM, остановок задач, правок переводов, настроек, рестартов, деплоев.

SHA256 установленных weblate.trans.judge, judge_loop и autotranslate совпали с локальными файлами. Поэтому проверенный execution/payload code относится к установленному сейчас продукту; конфигурация вчерашних прогонов подтверждается configuration_snapshot. Текущие alias_revision из /model/info совпали с revision в обоих Anvil run. Это не превращает расчёт по метаданным в фактический счёт провайдера.

Артефакты этой сессии:

- docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/production-usage.json.gz — 19 run за заданное окно, 12329 attempts, 12070 usage rows, безопасные настройки, prompt/source hashes.
- docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/overlap-payload.json.gz — пересечение Anvil, metadata вердиктов, размеры исторических targets и текущих payload.
- docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/proxy-rate-metadata.json — ставки из /model/info, без credentials.
- docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/run-aggregates.json — четыре крупных прогона двух проектов.
- `docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/methodology.md` — методика, границы доступа и воспроизведение расчёта без production-команд.

Ранее сохранённый контекст: docs/operations/measurements/2026-09-17-anvil-saga-judge-investigation.md. Его неизвестные ниже частично закрыты; прежние выводы о 3000 запросах, кэше и сравнении длительности нельзя повторять без новых данных.

## 2. Главный вывод

Нет одного token leak. Есть разные множители:

- Два обязательных судьи на каждый выбранный перевод.
- Реальные batch widths 2 и 1, а не глобальные 5: 3000 initial HTTP calls на 2000 строк — ожидаемая база.
- Длинный повторяющийся system prompt и schema на короткие строки; provider prompt cache уже работает.
- DeepSeek с reasoning — основной источник задержки. Qwen уже без reasoning.
- Повторное исполнение одной задачи приводит к реальным повторным оценкам; completed-verdict cache не исключает одновременно выполняемую работу.
- Денежная наблюдаемость неполна: Anvil usage cost_usd=None, а не $0.
- Повторное оценивание пустого target в Victory Banner было отдельной подтверждённой ошибкой, не нормальной работой судьи.

## 3. Anvil Saga: фактические метрики

Первый run 4736c44d-67e1-4e0d-8fb6-7f71c80050aa:

- started 2026-09-16 14:32:26.550 UTC; finished 19:53:06.510; wall 19239.96s.
- 2000 checked; 3004 HTTP attempts; 3000 usage rows.
- prompt 6,979,330; cached input 5,426,176; completion 927,148; reasoning 743,755; total 7,906,478.
- DeepSeek: 1001 usage rows, prompt 2,468,842, cached 1,346,560, completion 843,259, reasoning 743,755.
- Qwen: 1999 usage rows, prompt 4,510,488, cached 4,079,616, completion 83,889, reasoning 0.
- DeepSeek recorded request elapsed sum 15,628.385s, median 10.347s, p95 44.107s.
- Qwen elapsed sum 4,588.969s, median 2.013s, p95 3.844s.

Второй run 7567049b-0dae-4bed-8aa8-a0e68b1c208b:

- started 2026-09-16 18:32:33.723 UTC; finished 2026-09-17 03:44:16.261; wall 33102.538s.
- 2000 checked = 1990 evaluated + 10 unparsed; 3031 HTTP attempts; 2997 usage rows.
- prompt 6,999,063; cached input 5,440,256; completion 1,668,743; reasoning 1,378,195; total 8,667,806.
- DeepSeek: 1009 usage rows, prompt 2,504,469, cached 1,368,832, completion 1,534,925, reasoning 1,378,195.
- Qwen: 1988 usage rows, prompt 4,494,594, cached 4,071,424, completion 133,818, reasoning 0.
- DeepSeek elapsed sum 27,640.492s, median 21.979s, p95 67.292s.
- Qwen elapsed sum 5,663.948s, median 2.179s, p95 5.192s.

Сумма двух Anvil: 16,574,284 tokens, из них input 13,978,393, cached input 10,866,432, output 2,595,891, reasoning 2,121,950. Reasoning уже входит в completion; повторно его складывать нельзя. Cached input уже входит в prompt; это также не добавочная категория.

Отсутствуют usage rows для 38 Anvil attempts: 19 LiteLLM deadline + 19 OpenRouter auth403. Это неизвестная возможная оплата таймаутов, а не доказанный нулевой расход. Непарсящиеся ответы могут иметь usage и расход; parsing success не условие оплаты.

В четырёх крупных run Anvil+Victory Banner сохранено 34,736,177 токенов и 12040 usage rows. Из них лишь 55 имеют provider-reported cost, суммарно $0.34817529152. Эта сумма НЕ является общей стоимостью. На Victory Banner зарегистрированы также translation/repair usage: 22 и 10 applied OpenRouter rows соответственно. Их нельзя потерять, считая только JudgeRequestAttempt.

## 4. Разложение уравнения

Количество основных запросов:
Q_initial = sum по component/language группам g и seats s ceil(N_g,s / B_g,s).
Далее добавляются транспортные/протокольные повторы, fallback, isolation, повторные раунды, judge candidate, повторное исполнение задачи. Repair MT учитывается отдельно.

Для Anvil B1=2, B2=1: 1000+2000=3000, до мелкой адаптации/ошибок. Наблюдаемые 3004/3031 не подтверждают гипотезу массовой retry explosion. Глобальный JUDGE_BATCH_SIZE=5 переопределён JUDGE_BATCH_SIZE_SEAT_1='2', SEAT_2='1'.

Токены запроса:
I = stable rules + project context + schema + per-row source/target/context/glossary/checks + framing.
O = structured verdict/errors/back_translation + reasoning (если провайдер включает его в completion, отдельно не добавлять).

Деньги:

```text
C = sum [(I_cached * rate_cache) + ((I_total-I_cached) * rate_input) + O_total * rate_output] + отдельно применимые cache-write fees/прочие начисления.
```

Ставки ниже per 1M tokens; unknown usage, иная тарификация cache creation, надбавки/скидки контракта остаются вне подтверждённой суммы.

DeepSeek alias: input $0.619962; cache read $0.0789815; output $1.239924.
Qwen alias: input $2; cache read $0.25; output $6; cache creation metadata $2.5 (число созданных cache tokens журналом отдельно не сохраняется).

Расчёт по сохранённому usage и cache-read ставкам:

- Anvil first $4.2327 = DeepSeek $1.8477 + Qwen $2.3850.
- Anvil second $5.3825 = DeepSeek $2.7154 + Qwen $2.6671.
- Anvil total около $9.62 (расчёт до округления по run: $9.6151).
- Сценарий без cache discount: $12.1005 и $13.2480. Это альтернативный расчёт, НЕ доверительный интервал и НЕ гарантированная верхняя граница счёта.
- Четыре больших run: около $20.61 = оценка unpriced основных aliases + $0.3482 reported OpenRouter costs.

В Anvil суммарно по статьям: DeepSeek uncached input $1.3998, cached input $0.2145, reasoning output $2.6311, visible output $0.3177; Qwen uncached input $1.7081, cached input $2.0378, visible output $1.3062. Поэтому главный источник задержки (DeepSeek reasoning) НЕ равен единственному источнику денег. Qwen при нулевом reasoning стоит немного больше двух DeepSeek totals из-за ставки и большого числа повторных промптов.

Порядок величины $2.12–2.69 / 1000 processed target-language rows не выглядит сам по себе экстремальным для двух оценок, но экономическая окупаемость не установлена: нужны подтверждённые человеком полезные находки и время их разбора. 1000 строк не равно 1000 слов. Сценарий 57996 строк с прежней средней ценой даёт $122.74–156.08, но это не смета: язык, длина, cache hit, repairs и контракты меняются. Текущие две выборки не репрезентативны для всего проекта.

## 5. Почему долго

Оба судьи запускаются параллельно, но каждый последовательно отправляет свои батчи. _run_seats синхронизирует сохранённое покрытие по end_offset, не по одинаковому номеру батча. B1=2/B2=1 требует двух Qwen запросов на один DeepSeek batch; быстрый seat ждёт peer coverage. Нельзя складывать elapsed обоих seats и объявлять это wall-clock.

DeepSeek output на 89.23% состоит из reasoning. В обоих Anvil run correlation reasoning_tokens vs elapsed_ms на parsed DeepSeek attempts: 0.980 и 0.986. Это сильное свидетельство доминирования объёма generation, а не только ожидания очереди. Median first byte ~1.67 и 1.62s при median полной latency 10.35 и 21.98s.

Два run имеют РАЗНЫЙ состав: первый содержит 1104 glossary rows по шести языкам и 896 lockit rows; второй — 2000 lockit rows одного языка. Историческая длина target: median 7 vs 46 characters, mean 20.76 vs 57.83. Поэтому 5h20 vs 9h11 нельзя трактовать как benchmark одинаковой нагрузки или доказательство деградации провайдера. Более длинный контент сопровождается почти удвоенным reasoning.

Приближённая union длительностей HTTP requests (created_at фиксируется после ответа, start восстановлен как created_at-elapsed): 17935.9s из wall19240 и 30452.8s из wall33103. Вне этих окон остаётся ~21.7 и ~44.2 минуты. Это не чистое время БД: там preparation, persistence, MT repair, callbacks и смещение timestamps. Полного profiler trace этих периодов нет. Не приписывать residual одному механизму.

В Anvil все verdict mutation attempt=0, subject=live: успешного repair+candidate rejudge цикла в этих данных нет. Однако repair_status=no-candidate у 310 и 581 units и код repair_targets вызывается для таких кандидатов. Отсутствие MT usage НЕ доказывает отсутствие отказавших MT HTTP calls (403 до usage). Их полное количество/время по нынешнему ledger не восстанавливается. Включать отдельную unknown ветку, не называть весь residual repair.

## 6. Кэш и повторное исполнение

Provider prompt cache работает: input hit tokens суммарно 77.74%; DeepSeek 54.60%, Qwen90.52%. Утверждение «кэш выключен из-за random boundary» неверно. Boundary стоит после стабильного system prefix; он может мешать reuse изменяющейся части, но не уничтожает уже измеренные hits. Нельзя ослаблять защиту untrusted data ради предполагаемой экономии.

Application verdict cache — другой слой. Он требует complete parsed evidence обеих seats с теми же target/context/profile/subject. Текущий run проверяет cache при подготовке request set; он не резервирует in-flight право на оценку для других workers.

Проверено 291 пересекающаяся строка двух Anvil run:

- у всех 291 совпали input_target_hash и context_hash;
- у всех 582 пар unit/seat timestamp первого run позже старта второго;
- у 575/582 совпала request_identity и profile; остальные 7 имеют отличающийся profile;
- 575 пар parsed у обоих; второй seat verdict иногда даже записан раньше первого (215/582).

Это реальная повторная оценка совпадающего контента при перекрывающихся выполнениях, а не только гипотеза инвалидирования кэша человеком. Сценарий совместим с lease redelivery: одинаковый task_id и start gap14407s при lease14400s. Прямого broker receipt log в этой сессии не получено; конкретный механизм запуска второго исполнителя не доказан таким логом.

У второго run 441 referenced attempts на пересекающихся units, 437 usage rows, 1,236,382 recorded tokens, оценка ~$0.7357. 436 этих usage batches целиком пересекаются, один mixed batch. Это цена связанных с overlap запросов, не точный ущерб: некоторые verdicts восстанавливают ранее unparsed, mixed request включает другую строку, timeout billing неизвестен. Нельзя объявлять весь второй run впустую: 1709 его rows новые.

## 7. Payload и качество

System prompt (нейтральная подстановка Russian→English): 6920 characters; JSON schema batch2:871 characters. Текущая равномерная по ID диагностическая выборка24 units: сегмент в среднем171.875 chars, median134, max394. Это character measurement текущей выборки, НЕ исторический token breakdown. 19/24 sample rows English; не языково-сбалансированная выборка.

Средние сериализованные части сегмента: source25.4 chars, target23.2, glossary33.8, explanation14.2, key18.5. Полного глоссария проекта в каждый запрос не передают: get_matched_glossary_prompt_entries берёт совпадающие термины/варианты с пояснениями. Соседние реплики вообще не передаются данным builder. Нет основания лечить расход удалением несуществующих neighbor blocks или всего глоссария.

Каждый ответ обязан включать back_translation для каждой строки. Error descriptions не имеют собственного жёсткого ограничения; max_tokens обоих seats сейчас0 (поле не отправляется). Это возможности измеренного A/B, не безопасные однострочные оптимизации. Слишком низкий output cap создаст truncated JSON и повторную оплату.

Исторические проверки не позволяют просто удалить DeepSeek:

- docs/product/measurements/2026-08-13-phase0-measurements.md: held-out433, old model pair. DeepSeek H recall90.6%, union97.4%; critical→pass3/120 vs0/120. Union false flag11.4%. Эти числа НЕ валидируют сегодняшние aliases и шесть языков Anvil.
- Там же старые каскады с пересмотром вердикта снижали recall: best87.1% против94.0% single control на train/dev. Это не доказательство невозможности любого cascade; это запрет повторять тот конкретный каскад как якобы уже безопасный.
- docs/product/measurements/2026-09-03-judge-thinking-passthrough.md: Qwen no-think уже внедрён и текущие reasoning0 это подтверждают. У DeepSeek no-think в маленьком эксперименте исчезли6 прежних detections/20. Agreement со stored verdict не human recall; всё равно без нового human-gold сравнения выключать reasoning небезопасно.
- Ground truth преимущественно constructed/LLM-labeled, ограниченное языковое покрытие; strong independent human anchor только для одного ru→zh набора. Repair success, полезность major/critical в текущем Anvil, стоимость человеческого разбора и production ROI не измерены.

## 8. Что делают ведущие TMS (публично документировано, не их закрытая архитектура)

Phrase: QPS/AI Checks для направления переводов в нужный review workflow, TM/NT прежде платного MT/QA, анализ unlocked scope. Текущая AI Unit Calculation: первый AI check 1 AIU на10 unlocked SOURCE words; последующий+25% первого. Не сравнивать AIU с нашими raw tokens или выдумывать долларовую ставку. Старые Auto LQA/Quality Profiles deprecated/меняются; не копировать устаревающий UI.
<https://support.phrase.com/hc/en-us/articles/5709672289180-Phrase-QPS-Overview>
<https://support.phrase.com/hc/en-us/articles/14032731809052-AI-Unit-Calculation>
<https://support.phrase.com/hc/en-us/articles/13872357395228-Localization-Platform-Pricing>

Smartling: LQE High/Medium/Low маршрутизирует объём человеческой постредактуры; доступны LLM и custom XLMR варианты. LQA/AI post-edit — разные услуги, shared prepaid AI budget и per-word rates по договору. Это НЕ доказательство каскада из двух конкретных LLM или тарифа за наш эквивалентный judge.
<https://help.smartling.com/hc/en-us/articles/25058582212507-Language-Quality-Estimation-Agent-for-Machine-Translation>
<https://help.smartling.com/hc/en-us/articles/46279014244763-Smartling-s-AI-Subscription>

Lokalise: TM100% не оцениваются повторно в AI task scoring; низкие и non-scored отправляются на review. Scoring в текущих docs не расходует дополнительную AI words quota; отдельный AI LQA — другая тарифицируемая поверхность. Нельзя из этого вывести нулевую себестоимость их inference. Видимый0–100 score не предлагаем переносить в HCGameLoc.
<https://docs.lokalise.com/en/articles/11631905-scoring-translation-quality>
<https://docs.lokalise.com/en/articles/7945761-ai-lqa>

Crowdin: configurable AI QA, context/glossary/TM/style, BYOK/managed providers и daily/monthly/per-user spending controls; auto-retry QA опционален и способен увеличить расход. При достижении лимита поддержанные AI requests ставятся на паузу. Важная оговорка docs: custom providers не оцениваются и не входят в денежный лимит; стандартные ставки дают estimate, не точный invoice. AI Pipeline — отдельное приложение, не свидетельство внутренней архитектуры всех Crowdin QA.
<https://support.crowdin.com/crowdin-ai/>
<https://support.crowdin.com/crowdin-ai/#ai-usage-limits>
<https://support.crowdin.com/crowdin-credits/>

memoQ / RWS: QE отдельным слоем с providers TAUS/ModelFront/Language Weaver, применение TM перед MT, направление сомнительных сегментов людям. Тарифы часто договорные. Auto-approval по score не подходит advisory-инварианту HCGameLoc.
<https://docs.memoq.com/12-0/en/Workspace/mt-settings.html>
<https://www.rws.com/language-weaver/quality-estimation/>

Общий подтверждённый паттерн: reuse, отдельные cheap checks/QE и дорогая review, прозрачная квота, приоритизация человеческого внимания. Не установлено, что все крупные TMS оценивают только sample или обязательно используют один вместо двух LLM. Sampling оценивает качество партии; оно не означает проверку каждой непосмотренной строки.

## 9. Best practices и применимость

- Считать фактические cache-read/input/output rates, reasoning не удваивать. Стабильный prefix сохранять. <https://developers.openai.com/api/docs/guides/prompt-caching> (provider-specific; OpenAI правила нельзя автоматически переносить на Atlas).
- Отличать microbatch в одном chat request и asynchronous Batch API. Публичная OpenAI Batch скидка относится к поддержанному сервису, не нашему LiteLLM endpoint. <https://developers.openai.com/api/docs/guides/batch>
- QE вроде CometKiwi/xCOMET позволяет дешёвый предварительный сигнал/спаны без генерации длинного рассуждения. Это кандидат на отдельный benchmark, не готовая замена: short strings/game context/языки требуют human-gold, а GPU hosting не бесплатен. <https://aclanthology.org/2022.wmt-1.60/> ; <https://aclanthology.org/2024.tacl-1.54/>
- Риск-ориентированный cascade может экономить, но сильный судья должен искать пропуски cheap screen, а не только подтверждать уже найденное. Если проверять лишь flagged, false negatives cheap screen так и остаются невидимы. Нужны random audit среди pass и явная политика coverage. Исторический локальный cascade был другим и показал ухудшение.
- Детерминированные проверки не заменяют semantic review. Их разумно выполнить до оплаченной проверки ещё неготового текста, затем пересудить окончательную версию; пропуск semantic review из-за успешного regex недопустим.
- Окупаемость = inference + infra + human review + expected escape cost. Доля pass/major/critical без adjudication не даёт count полезных находок.

## 10. Приоритеты оптимизации и проверки

P0, без снижения качества: один активный исполнитель одной задачи, повторное использование run и сохранённых границ; не считать mutex простую строку/idempotency_key в БД. Проверить двойную доставку и crash recovery с fake provider. Exactly-once billing после смерти между ответом и commit без idempotency провайдера не обещать. Согласованный MT prerequisite перед judge сохраняется: существующий текст не затирать, при неготовности выбранных языков судей не запускать. Это отдельный уже написанный план, не разрешение исполнять его.

P0, финансовая ясность: cost_usd=None показывать как unknown; отдельно actual/estimated, prompt/cached/output/reasoning, source of rate и unmetered timeouts. Прогноз и бюджет на весь user intent, не только число units в child task. Включить failed repair/fallback attempts. Денежный бюджет пока лишь рекомендация к проектированию; действующий cap2000 не меняется.

P1, самый простой стоимостной эксперимент: увеличить Qwen microbatch1→2/5, не менять prompt/model/quality policy одновременно. При 2000 строк clean counts2/1=3000;2/5=1400 (−53.3% HTTP);5/5=800 (−73.3%). Это арифметика количества HTTP, НЕ процент экономии денег или гарантированного ускорения. Проверять parsed/cardinality, recall/severity на одинаковых frozen data, max latency/deadline, token/unit, стоимость с cache. DeepSeek ширину повышать отдельно: reasoning и decode могут упереться в deadline.

P1, главный latency experiment: кандидат вместо DeepSeek или ограниченный reasoning budget, поддерживаемый РЕАЛЬНЫМ upstream. Gold human examples по языкам и классам строк; paired inputs, несколько повторов; критические пропуски, false flags, сохранность терминологии. Простое disable reasoning сегодняшнего seat1 не рекомендовано. Qwen reasoning-off уже сделано, повторной экономии там нет.

P2: компактный prompt/output без потери safety/rubric; отдельно испытать back_translation только для findings/по запросу, а не дважды для каждого pass. Валидация нужна: intermediate back translation может влиять на reasoning/recall. Ограничивать output после p95/p99, не наугад. Матченный glossary/context не удалять без свидетельства избыточности.

P2: после нового качества-сравнения выбирать full two-seat vs selective second seat/QE. У cheaper screen должны контролироваться false negatives и реальные critical, в том числе случайная проверка его pass. Ни статистическую выборку, ни low-cost pass не выдавать за полное двухсудейское покрытие.

Latency-only: bounded parallelism нескольких независимых microbatches/коротких durable chunks может ускорить; само по себе токены не уменьшает и ускоряет расход при неисправности. Вводить только после single-executor/budget/лимитов провайдера, сохраняя fail-fast/cancellation и DB-safe persistence. Не превращать cap10000 в один непрерывный сутки работающий task.

Критерии решения: стоимость/1000 уникальных target rows и /1000 source words, unpriced coverage, p50/p95 latency, total wall for fixed scope; per-language human-labeled critical recall + confidence intervals, false positive review time, repeat drift, unparsed/cardinality. Не подбирать prompt на старом sealed test: нужен новый held-out набор для выбора и отдельный контроль финальной конфигурации.

Не рекомендовано сейчас: уменьшать cap вместо оптимизации; автоматически повышать до10000; считать весь второй run потерей; отключить DeepSeek без ground truth; обещать10x от batching; строить крупную новую QE инфраструктуру до простых A/B; считать миллион cached input tokens по обычной input ставке; переносить TMS marketing90%/80% как наши показатели.

## 11. Остаточная неопределённость

Фактический invoice Atlas/LiteLLM, cached-write accounting и биллинг оборванных запросов не доступны в текущем ledger. Расчёт по /model/info — best available estimate, не invoice. Точный residual времени за пределами HTTP windows, число отказавших repair calls и причина broker redelivery без worker receipts не измерены полностью. Экономия новых конфигураций и качество на шести языках без отдельно разрешённого платного A/B не доказаны. Эти ограничения не мешают установленному диагнозу: microbatch2/1, reasoning-heavy serial DeepSeek, working prefix cache, incomplete monetary accounting и real concurrent repeated judgements.

Итог: текущая inference цена по доступным ставкам не выглядит катастрофической; UX/throughput и ненадёжность длинных задач — неприемлемое место. Первые оптимизации — устранить повторное исполнение, сделать деньги видимыми, измерить более широкий Qwen batch и более быстрый/ограниченный DeepSeek-профиль с human quality gate. Не снижать качество или объём под видом оптимизации.
