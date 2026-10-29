<!--
Copyright © HCGameLoc
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Снижение расходов Gemini при сохранении `medium`

**Статус:** исследование для решения человеком; изменения не предлагаются как уже утверждённые. Исходники изучены в production-base worktree `judge-hotfix-20261029`, соответствующем предоставленному source anchor `017464487bfaf46fa0fd013526670614f1ba535c`. Внешние первичные источники и публичные метаданные проверены 29 октября 2026 года через Exa и HTTP GET.

Production, настройки, credentials и платные inference-запросы не затрагивались. Игровые строки, prompts и provider responses не воспроизводятся.

## 1. Проблема

Нужно снизить стоимость переводчика **`google/gemini-3.7-flash` через OpenRouter**, сохранив `reasoning.effort=medium`, смысл, контекст, glossary и защищённые slots. Наиболее важное различие: prompt caching снижает стоимость части **входа**, тогда как предотвращение ненужного вызова или более дешёвый service tier могут затронуть стоимость **всего вызова**, включая reasoning.

`off`, `minimal` и замена модели исключены. Прямой Gemini API или Vertex рассматриваются только для понимания документации, не как предложение несанкционированной миграции.

### Текущий source map

| Механизм | Текущее поведение |
|---|---|
| Стек | Python/Django Weblate, собственный routed machinery, OpenRouter Chat Completions и durable-журнал платных обменов. |
| Модель и reasoning | При точном hostname OpenRouter, точном ID `google/gemini-3.7-flash` и codec `routed-slots-v3` payload получает `reasoning: {"effort": "medium"}`. Старые codecs сохраняются для replay. `weblate_customization/src/weblate_customization/machinery.py:167–205,354–381`. |
| Сообщения | System с правилами, persona, style и языковыми инструкциями; user/assistant с few-shot; user с текущим батчем. `weblate/machinery/openai.py:305–331`; `weblate/machinery/llm.py:1281–1287,1492–1545`. |
| Glossary | Уже отправляются термины, выбранные для строк батча, а не автоматически вся база. `weblate/machinery/llm.py:325–330,705–718,756–778`. |
| Идентичность и разметка | Структурированный output требует идентификаторы и source-owned protected slots; server восстанавливает неизменяемые части из источника. `weblate_customization/src/weblate_customization/machinery.py:149–158,372–380`. |
| Батч и concurrency | LLM batch size **10**, concurrency **2**. Одинаковые source texts в одном pending batch разводятся по запросам; одиночный запрос не доказывает дефект. `weblate/machinery/llm.py:417–435`; `weblate/machinery/base.py:1324–1338`. |
| Translation cache | Перед платным вызовом проверяется локальный cache. Его identity учитывает протокол, glossary, контекст и plural; одинаковый source в разных контекстах не является достаточным доказательством эквивалентности. `weblate/machinery/base.py:1173–1239`; `weblate/machinery/llm.py:1165–1225`. |
| Durable replay | Work identity, digest и codec защищают платный обмен; неоднозначно оплаченный или изменившийся запрос требует reconciliation, а не автоматического второго POST. Это не универсальный cache для разных контекстов. `weblate/machinery/openai.py:40–76`; `weblate/trans/llm_delivery.py:78–79,101–161,230–247,303–322`. |
| Учёт | Записываются provider-reported cost и counts prompt/completion/cached/reasoning. Отсутствующие либо невалидные cached/reasoning counts в `LLMUsageLog` нормализуются в `0`; такой ноль не доказывает, что провайдер сообщил ноль. `weblate/machinery/openai.py:240–276`. |

**Уточнение по IDs и cache churn.** Базовая реализация создаёт случайные few-shot IDs, но `RoutedLLMTranslation` для durable exchanges с `routed-slots-v2/v3` переопределяет их стабильными `example{index}` именно для точного digest replay. Сами durable batch IDs отдельно выводятся из semantic identity. Поэтому случайный churn первого user-сообщения относится к базовому/недолговечному пути, **не установлен для текущего durable v3**. Даже при стабильных IDs проектные примеры могут различаться; эффект на cache hits не измерен. `weblate_customization/src/weblate_customization/machinery.py:219–227`; `weblate/machinery/llm.py:1360–1436,1492–1545,2995–3030`.

### Сохранённые исторические измерения

- План сообщает за **28 октября, московское время**: 1 636 запросов в журнале приложения, **$14.67463843926**, из них Gemini **$13.704178725**. Его **2 319 180 reasoning tokens входят в 2 744 874 completion tokens**, то есть около **84,5%** выходных токенов. Это app ledger, **не полная выписка OpenRouter key**. `docs/product/plans/2026-10-29-judge-pair-fallback-and-cost-controls.md:297–316`.
- В исходных данных задания дополнительно переданы Gemini **1 248 вызовов**, **4 683 357 prompt** и **150 543 cached**. Эти детализации не найдены независимо в прочитанном разделе плана; ниже они используются только как **условия расчёта**, не как сверенная provider statement. Переданная общая сумма `$14.674686` отличается от суммы сохранённого плана; здесь приведена точная сумма плана.
- Августовский пример с cached tokens относится к **Gemini 2.5 Flash**, не к текущей модели. Два старых batch-5 arms отличались стоимостью на **12,6%** при различном количестве cached tokens — конкретное историческое наблюдение, а не прогноз. `docs/product/measurements/2026-08-11-col4-fr-autotranslate-report.md:190–202`; `docs/product/measurements/2026-08-25-deterministic-alignment-limits.md:187–195`.
- Исследование batch size зафиксировало truncation при 20 и ухудшение пунктуации при 5. Детерминистический alignment gate не доказал покрытие всех классов ошибок. Самостоятельно менять размер ради экономии не обосновано. `weblate/machinery/llm.py:423–429`; `docs/product/measurements/2026-08-25-deterministic-alignment-limits.md:161–195`.
- **Устаревшие выводы:** августовское предложение выключить thinking противоречит текущему требованию `medium`. Старые описания полного glossary и неразделённых glossary warnings также не описывают текущий код: выбор терминов и разделение hard/advisory уже существуют. Отбор терминов имеет известные ограничения, поэтому дальнейшее сокращение контекста без проверки качества не является бесплатной оптимизацией. `docs/product/measurements/2026-08-11-col4-fr-autotranslate-report.md:295–305`; `docs/product/measurements/2026-09-10-mt-glossary-selection-projection.md:34–60,85–90`; `weblate/machinery/llm.py:644–675,756–770`.

### Публичные цены, prompt cache и неопределённости

**Точный ID публично существует.** GET `/endpoints` возвращает endpoints Google AI Studio и Google Vertex с поддержкой `reasoning` и structured output. На момент чтения:

| Endpoint | Input / 1M | Output и internal reasoning / 1M | Cache read / 1M |
|---|---:|---:|---:|
| AI Studio / Vertex global standard | $0.75 | $3.75 | $0.075 |
| AI Studio flex | $0.375 | $1.875 | $0.0375 |
| Vertex global batch | $0.375 | $1.875 | $0.0375 |

Это публичные endpoint metadata на момент GET, не подтверждение того, что конкретный аккаунт может выполнить запрос, что endpoint будет доступен при inference, или что исторические вызовы оплачивались по этим ставкам. Цены и скидки могут измениться. [verified: https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints; https://openrouter.ai/api/v1/models/google/gemini-3.7-flash:batch/endpoints]

- **Implicit cache.** Общий OpenRouter guide описывает автоматический cache Gemini 2.5 и новее, отсутствие отдельной платы за запись/хранение и примерный TTL **3–5 минут**. Однако metadata конкретных Gemini 3.7 endpoints возвращает `supports_implicit_caching: false`; общий guide также приводит для Gemini коэффициент read 0,25×, тогда как metadata точного standard endpoint показывает 0,10×. Следовательно, ни доступность implicit caching на текущем route, ни его точный порог нельзя объявить установленными. [verified: https://openrouter.ai/docs/guides/best-practices/prompt-caching; https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints]
- **Explicit cache через OpenRouter.** Для Gemini guide описывает `cache_control` на content blocks, использование последнего breakpoint, фиксированный **5-минутный TTL без продления** и стоимость записи как input плюс пять минут хранения. Первый system message нормализуется в единый Gemini `systemInstruction`: нельзя считать переменный хвост внутри него независимым от cached части. Metadata показывает `input_cache_write`, но не объясняет достаточно, является ли поле полной ценой записи или только компонентом хранения; не складывать ставки без подтверждения провайдера. Поддержка именно текущего route не проверена. [verified: https://openrouter.ai/docs/guides/best-practices/prompt-caching; https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints]
- **Sticky routing.** OpenRouter описывает account/model/conversation affinity; default key зависит от первого system и первого non-system сообщения. С `session_id` stickiness активируется после успешного запроса; без него — после обнаруженного cache hit. Routing session истекает после **10 минут бездействия** — это не TTL provider cache. `provider.order` отключает sticky routing; failover возможен. Прочитанный payload не добавляет `session_id`, `cache_control` или `provider.order`; он добавляет `provider.require_parameters`. Наличие `session_id` не гарантирует cache hit. [verified: https://openrouter.ai/docs/guides/best-practices/prompt-caching; `weblate/machinery/openai.py:305–331`; `weblate_customization/src/weblate_customization/machinery.py:354–381`]
- **Usage.** OpenRouter определяет `prompt_tokens_details.cached_tokens` как reads и `cache_write_tokens` как writes; документирует также `cache_discount`. Текущий рассмотренный ledger сохраняет cached count и cost, но не показывает сохранения write count, discount или различия между отсутствующим count и нулём. [verified: https://openrouter.ai/docs/cookbook/administration/usage-accounting; https://openrouter.ai/docs/guides/best-practices/prompt-caching; `weblate/machinery/openai.py:240–276`]
- **Прямые API — только сравнение.** Gemini Developer API документирует implicit caching и отдельный explicit cache с настраиваемым TTL и оплатой хранения; точный порог 3.7 в прочитанной Developer API таблице не установлен. Google Cloud перечисляет Gemini 3.7 Flash и указывает **6 144 токена для implicit**; Cloud TTL и правила нельзя автоматически переносить на OpenRouter. [verified: https://ai.google.dev/gemini-api/docs/generate-content/caching; https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-overview]

### Экономические границы

При **условных** переданных объёмах Gemini и текущих публичных standard global rates:

1. Предельно оптимистичная дополнительная экономия, если все ещё не cached input tokens стали бы cache hits:

   `((4 683 357 − 150 543) / 1 000 000) × ($0.75 − $0.075) ≈ $3.06`.

   Это около **22,3%** исторических `$13.704`. Реальный потенциал ниже: уникальный ввод, минимальный prefix, misses, routing и неподтверждённая поддержка implicit cache. Это не пересчёт счёта и не обещанная экономия. [inference; verified rates: https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints]

2. При тех же предположениях `2 744 874 × $3.75/M ≈ $10.29` output; reasoning уже включён. Prompt cache сам по себе не сокращает сгенерированные reasoning tokens. [inference; исторический состав: `docs/product/plans/2026-10-29-judge-pair-fallback-and-cost-controls.md:299–303`]

3. Для prefix `P` токенов, `H` дополнительных подтверждённых hits и дополнительных расходов на writes/storage `W`:

   `экономия = H × P/1 000 000 × (input_rate − read_rate) − W`.

   При standard rates каждый hit экономит `$0.675` на миллион cached prefix tokens до write/storage; break-even: `H > W / (P/1 000 000 × 0.675)`. Для implicit без отдельной платы `W=0`, но первый запрос и misses остаются платными. Для explicit `W` нужно подтвердить по выбранному endpoint. [inference; verified: https://openrouter.ai/docs/guides/best-practices/prompt-caching]

4. **Flex против standard:** если одинаковый объём input/output действительно обслужен по указанным тарифам, разница — `$0.375/M input` и `$1.875/M output`. При условии идентичного набора платных токенов это номинально **50%** до различий cache, повторных попыток и других компонентов. Это не оценка прошлой экономии: фактический tier, маршруты и стоимость дополнительных попыток не установлены. [inference; verified rates: https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints]

5. При exact reuse экономия равна стоимости **реально предотвращённых** вызовов за вычетом расходов на безопасный reuse; она может включать и input, и output/reasoning. Семантическую эквивалентность нужно доказать. [inference]

## 2. Варианты

### 1. Безопасный exact reuse и предотвращение лишней оплаченной работы

**Что это.** Определить, где уже полученный допустимый результат — локальный translation cache, durable replay или надёжный exact TM match — может предотвратить новый вызов для той же полной identity.

- **Соответствие стеку:** использует существующие `weblate/machinery/base.py:1173–1239`, `llm.py:1165–1225` и `weblate/trans/llm_delivery.py:101–161`. Историческое исследование обнаружило, что тогдашний batch path мог вызвать следующий движок даже при выбранном высоком TM match; это не доказательство поведения текущего runtime. `docs/product/research/2026-09-17-repeat-drift-tms-practices-and-session-context.md:220–258`.
- **Плюсы:** потенциально устраняет стоимость целого ненужного вызова, включая reasoning, не меняя модель и `medium`. [inference]
- **Риски:** совпадение source не доказывает совпадение context, языка, plural, glossary, clarification, protected slots и overwrite policy; TM score не равен качеству. Нельзя обходить digest или `paid_unknown`. [inference]
- **Неплатные проверки:** по сохранённым агрегатам и source определить повторяемость полной identity, порядок выбора движков и долю повторов, уже покрытых cache/replay, не раскрывая строки или ответы.
- **Усилие / обратимость:** **M / средняя**: политика reuse меняет результат и требует строгого scope.
- **Источники:** локальные диапазоны выше; различие полного response cache и provider prompt cache — [verified: https://openrouter.ai/docs/guides/features/response-caching].

### 2. Синхронный Flex service tier для того же ID и `medium`

**Что это.** Рассмотреть OpenRouter `service_tier: "flex"` для подходящих задержкоустойчивых запросов; это синхронный tier, а не асинхронный Batch API. Публичный каталог показывает flex endpoint с тем же model ID и параметром `reasoning`, но GET не доказывает доступность исполнения или реальное сохранение `medium` на route. [verified: https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints; https://openrouter.ai/docs/guides/features/service-tiers]

- **Соответствие стеку:** существующий Chat Completions остаётся синхронным, но прочитанный payload не задаёт `service_tier`; добавление поля изменило бы digest и потребовало бы сохранения frozen replay. Текущий `request_timeout=120` секунд; Google для Flex рекомендует клиентский timeout **10 минут и более**, а целевая задержка может составлять 1–15 минут. `weblate/machinery/llm.py:417–422`; `weblate/machinery/openai.py:305–331`. [verified: https://ai.google.dev/gemini-api/docs/generate-content/flex-inference]
- **Плюсы:** опубликованная flex ставка вдвое ниже standard для input и output, включая `internal_reasoning`; объём reasoning не нужно уменьшать. Синхронная форма проще по lifecycle, чем Batch. [verified: https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints; inference]
- **Риски и маршрутизация:** Flex использует best-effort/sheddable capacity; возможны 429/503 и длинная очередь. OpenRouter описывает для `service_tier: "flex"` приоритет flex и fallback только на endpoints, чьи **и input, и output цены** не выше flex; при отсутствии подходящей capacity может вернуться ошибка. Если flex endpoints вообще отсутствуют в eligible pool, документ допускает обычный standard routing. Поэтому фактический `service_tier` и стоимость нужно учитывать, а не считать цену гарантированной. [verified: https://openrouter.ai/docs/guides/features/service-tiers; https://ai.google.dev/gemini-api/docs/generate-content/flex-inference]
- **Важный риск `:floor`:** OpenRouter документирует `:floor` как сортировку и допуск flex с возможным fallback на стандартную цену. Но текущий код выставляет `medium` только при **точном** сравнении модели с `google/gemini-3.7-flash`; передача ID с `:floor` не удовлетворяет этому условию. Поэтому `:floor` нельзя считать безопасным переключателем, сохраняющим текущий `medium`, без изменения и проверки контракта. [verified: https://openrouter.ai/docs/guides/routing/model-variants/floor; `weblate_customization/src/weblate_customization/machinery.py:354–371`]
- **Неплатные проверки:** подтвердить для точного endpoint совместимость `service_tier`, `reasoning=medium`, schema и `require_parameters`; изучить timeout, financial fence, usage и response tier без paid calls. Google Developer API в прочитанном списке Flex не перечисляет 3.7, что является дополнительной неопределённостью, а не опровержением каталога OpenRouter. [verified: https://ai.google.dev/gemini-api/docs/generate-content/flex-inference]
- **Усилие / обратимость:** **M / средняя**: sync форма совместима, но timeout, состояние неопределённой оплаты и frozen codecs важны.
- **Источники:** [verified: https://openrouter.ai/docs/guides/features/service-tiers; https://openrouter.ai/docs/guides/routing/model-variants/floor; https://ai.google.dev/gemini-api/docs/generate-content/flex-inference].

### 3. Стабильный prefix и временная locality

**Что это.** Оценить общий безопасный prefix и возможность группировать запросы допустимого scope близко по времени, не удаляя контекст и не изменяя смысл примеров.

- **Соответствие стеку:** system уже предшествует динамическому батчу. В durable routed v2/v3 демонстрационные IDs **стабильны**, но содержимое project examples может различаться; случайные IDs относятся к базовому/недолговечному пути. `weblate/machinery/openai.py:313–323`; `weblate/machinery/llm.py:1360–1436,1492–1545`; `weblate_customization/src/weblate_customization/machinery.py:219–227`.
- **Плюсы:** может улучшить input cache hits без изменения `medium` и размера batch. [inference; verified: https://openrouter.ai/docs/guides/best-practices/prompt-caching]
- **Риски:** prefix может быть ниже порога; exact endpoint metadata сообщает `supports_implicit_caching: false`; affinity не гарантирует hit. Изменение примеров или сортировки может повлиять на качество и digest replay. Реальные ID строк нельзя заменять позиционными ради cache. [inference]
- **Неплатные проверки:** сравнить безопасные hashes/длины prefixes и временные интервалы в сохранённых данных; отдельно определить применимость durable и non-durable путей. Не предполагать, что direct Vertex threshold автоматически применим к OR.
- **Усилие / обратимость:** **M / средняя** из-за версионирования wire contract.
- **Источники:** [verified: https://openrouter.ai/docs/guides/best-practices/prompt-caching; https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-overview].

### 4. Explicit prompt cache через OpenRouter

**Что это.** Условно рассмотреть `cache_control` breakpoint на действительно общем и достаточно длинном prefix после подтверждения поддержки точного endpoint и окупаемости.

- **Соответствие стеку:** OpenRouter описывает Gemini content-block breakpoints; текущий код передаёт string content и breakpoint не задаёт. Новые bytes затрагивают digest и frozen codec, поэтому старые платные exchanges должны воспроизводиться по сохранённому контракту. `weblate/machinery/openai.py:305–331`; `weblate_customization/src/weblate_customization/machinery.py:180–205`.
- **Плюсы:** явная граница повторяемого контекста; подтверждённые cache reads могут снизить стоимость входа. [verified: https://openrouter.ai/docs/guides/best-practices/prompt-caching]
- **Риски:** 5-минутный TTL, write/storage cost, порог и поддержка route не установлены. Нельзя добавлять лишний glossary ради достижения порога: это может увеличить и стоимость, и терминологический риск. [inference; `docs/product/measurements/2026-09-10-mt-glossary-selection-projection.md:34–60`]
- **Неплатные проверки:** уточнить у OpenRouter support для Gemini 3.7, threshold, billing `input_cache_write` и сочетание breakpoint с `reasoning`, schema и `require_parameters`; оценить break-even.
- **Усилие / обратимость:** **M / средняя**.
- **Источники:** [verified: https://openrouter.ai/docs/guides/best-practices/prompt-caching; https://openrouter.ai/api/v1/models/google/gemini-3.7-flash/endpoints].

### 5. OpenRouter response cache для полностью идентичных запросов

**Что это.** Рассмотреть opt-in cache полного успешного ответа OpenRouter, отдельно от prompt cache и локального durable replay.

- **Соответствие стеку:** документирован `X-OpenRouter-Cache: true`; рассмотренный POST его не добавляет. Совпадение всего запроса, включая examples, IDs и параметры, требует проверки. `weblate/machinery/openai.py:174–187,305–331`; `weblate/machinery/llm.py:1492–1545`.
- **Плюсы:** документированный hit бесплатен и не вызывает provider, поэтому может избежать стоимости всего повторного generation. [verified: https://openrouter.ai/docs/guides/features/response-caching]
- **Риски:** параллельные идентичные запросы до заполнения cache оба могут быть MISS; TTL по умолчанию 300 секунд, eviction возможна раньше, account-level ZDR несовместим. HTTP 200 не означает, что ответ прошёл локальные slot/semantic проверки. Cache не заменяет financial fence. [verified: https://openrouter.ai/docs/guides/features/response-caching; inference]
- **Неплатные проверки:** оценить долю byte-identical запросов по безопасным fingerprints, retention/ZDR и влияние нулевых usage counts cache HIT на учёт.
- **Усилие / обратимость:** **M / высокая** для нового opt-in, при отдельном review privacy и accounting.
- **Источник:** [verified: https://openrouter.ai/docs/guides/features/response-caching].

### 6. Асинхронный OpenRouter Batch API

**Что это.** Для неинтерактивной работы рассмотреть отдельный asynchronous lifecycle с тем же base model и `medium`. Это не увеличение числа строк в синхронном LLM batch.

- **Соответствие стеку:** публичный `google/gemini-3.7-flash:batch` endpoint перечисляет `reasoning` и structured output; OpenRouter Batch API принимает base model и разрешает batch variant. Текущий код ожидает синхронный ответ и использует собственный durable journal, поэтому это не простой параметр существующего вызова. [verified: https://openrouter.ai/api/v1/models/google/gemini-3.7-flash:batch/endpoints; https://openrouter.ai/docs/guides/routing/model-variants/overview; `weblate/machinery/openai.py:174–187`]
- **Плюсы:** опубликованные batch input/output rates совпадали с опубликованными flex rates и составляли половину standard global; потенциальная скидка охватывает output/reasoning без его снижения. Фактическая совместимость `medium`, cache и счёт требуют подтверждения. [verified: https://openrouter.ai/api/v1/models/google/gemini-3.7-flash:batch/endpoints; inference]
- **Риски:** результаты неупорядочены, элементы могут завершаться ошибками; Vertex может занимать до **96 часов**; cancellation не поддерживается; данные сохраняются до 30 дней; ограничения ZDR и residency. Потребуется другой recovery contract. [verified: https://openrouter.ai/docs/guides/features/batch-api]
- **Неплатные проверки:** установить допустимость latency, retention, residency, параметров и journal semantics без отправки batch.
- **Усилие / обратимость:** **L / средняя**.
- **Сравнение с Flex:** обе публичные ставки сейчас номинально 50% standard для указанных input/output; Flex сохраняет sync интерфейс, но имеет sheddable capacity и потенциально минуты ожидания, тогда как Batch имеет отдельный lifecycle и может ждать значительно дольше. Ни одна экономия не гарантирована одним GET. [verified: https://openrouter.ai/docs/guides/features/service-tiers; https://openrouter.ai/docs/guides/features/batch-api]

## 3. Сравнение

| Вариант | Усилие | Риск | Соответствие стеку | Сопровождение | Главный компромисс |
|---|---:|---|---|---|---|
| 1. Exact reuse | M | Неверная identity | Высокое: cache, TM, journal уже существуют | Среднее | Экономит весь предотвращённый вызов, но разные контексты нельзя сливать |
| 2. Синхронный Flex | M | Timeout, capacity и фактический tier | Хорошее для sync формы; требует wire/replay discipline | Среднее | Номинальная скидка включает reasoning; latency и доступность хуже |
| 3. Prefix/locality | M | Неизвестный hit rate и поддержка | Хорошее, но возможна смена wire contract | Среднее | Сохраняет генерацию; экономия ограничена input |
| 4. Explicit prompt cache | M | Write cost, threshold, поддержка route | Условное: content blocks и frozen codecs | Среднее | Явный prefix против риска платных writes без reads |
| 5. OR response cache | M | Retention, невалидный HTTP 200, concurrent MISS | Условное; opt-in и проверка identity | Среднее | Бесплатный полный hit, но требует идентичного допустимого запроса |
| 6. OR Batch API | L | Долгое ожидание, privacy, partial failure | Низкое для текущего sync lifecycle | Высокое | Номинальная скидка включает output, но нужен отдельный durable lifecycle |

## 4. Рекомендация — на утверждение человека

**Рекомендую приоритизировать неплатное исследование варианта 1 и одновременно оценить применимость синхронного Flex из варианта 2.** Exact reuse потенциально устраняет весь ненужный вызов, включая преобладающую reasoning-часть, но только при полной семантической identity. Flex заслуживает отдельного рассмотрения: публичная ставка точной модели показывает номинальное уменьшение input и output цены вдвое без снижения `medium`, однако это не доказательство доступности inference, допустимой задержки или фактической тарификации. Prefix caching остаётся полезным кандидатом, но его эффект ограничен входом и поддержка exact endpoint противоречиво отражена в документации и metadata.

**Любой платный canary требует отдельного разрешения:** проверка фактического `medium` и schema на Flex, cache hits, `cache_control`, session affinity, стоимости, качества или Batch API. До него доступны только публичные GET, анализ сохранённых агрегатов и offline fingerprints. Canary не должен менять переводы, запускать массовые задачи, ослаблять protected slots или обходить `paid_unknown` и replay fences.
