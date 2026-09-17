# Методика и границы доказательств

## Получение данных

В исходном исследовании пользователь отдельно разрешил read-only production inspection. ORM-агрегаты и безопасные поля прочитаны в PostgreSQL-транзакциях `SET TRANSACTION READ ONLY` с `statement_timeout=30s`. Для rate metadata выполнен authenticated GET `/model/info` для двух действующих aliases; ключ остался внутри процесса и не включён в результаты. Chat completion POST, изменение настроек, остановка задач и деплой не выполнялись.

Окно отбора runs: `created >= 2026-09-16T00:00:00Z`, `created < 2026-09-17T06:00:00Z`, `requested_mode=judge`. 19 runs включают15 небольших Pirate Ships и4 больших Anvil/Victory. Attempts и usage выбраны по этим run IDs, включая записи после конца окна создания run. Это не общий расход инстанса за сутки.

## Состав архива

Не копировались полные JSONL или IRC/session records. Экспортированы только итоговые JSON агентских выводов и14 текстовых сообщений об исследовании: без служебных оболочек, transcript metadata, tool outputs и credential records.

Production allowlist: run IDs/times/status/mode/scope label, числовой summary, безопасный configuration snapshot; attempts IDs/seat/model/provider/fingerprints/batch/failure-kind/status/timing/token counts; usage IDs/model/scope/token/cost/batch/outcome; агрегаты verdict/unit/adaptive state; названные non-secret настройки и SHA256 трёх установленных модулей. Произвольный requested_query, исходники функций, prompt, provider error body, actor identity, ключи и тексты переводов исключены. В overlap sample хранятся длины/хэши, но не source/target/context text.

JSON сжат gzip без timestamp в заголовке, содержимое не усечено. `manifest.json` хранит bytes/SHA256 каждого файла кроме себя. Самопроверка архива сетевых вызовов не выполняет.

## Атрибуция

Anvil IDs:

- `4736c44d-67e1-4e0d-8fb6-7f71c80050aa`
- `7567049b-0dae-4bed-8aa8-a0e68b1c208b`

Victory IDs:

- `71ee489d-43cf-404a-bebd-3f8e442eb771`
- `a1d09bd2-0030-450c-9aaf-dba9727e03d3`

Usage привязан через run/request_attempt; translation usage хранится отдельно от judge. HTTP attempt, response с usage, parsed verdict, checked unit и оплаченный запрос — разные единицы. Первичные времена и usage сохранены для самостоятельного пересчёта.

## Расчёт стоимости

Для каждой usage row с известной ставкой:

```text
uncached = prompt_tokens - cached_tokens
estimate = uncached * input_cost_per_token
         + cached_tokens * cache_read_input_token_cost
         + completion_tokens * output_cost_per_token
```

Reasoning — подмножество completion; cached — подмножество prompt. Оба нельзя прибавлять повторно. Ставки в JSON — per token. Qwen metadata также содержит cache creation rate, но usage не содержит количества cache-write tokens: эта часть не реконструируется. `cost_usd=None` остаётся unknown. Вывод estimate не записывается поверх provider cost.

## Время и payload

Attempt `created_at` записывается после ответа. Приближённый interval: `[created_at-elapsed_ms, created_at]`; union intervals не равна точному profiler trace. Суммуelapsed двух параллельных seats нельзя объявлять wall-clock. Около22/44 минут вне union — смесь возможной подготовки, persistence, repair, callbacks и погрешности времён, не установленная одна причина.

Payload sample:24 равномерно выбранных поID rows из объединения двух Anvil scope;19 English, по1 дляfr/ja/ko/zh_Hans/zh_Hant. Получены размеры текущего `_segment(build_request(unit))`, без POST. Это не historical token accounting и не сбалансированная языковая выборка. Исторические target lengths взяты из сохранённых run-unit snapshots.

## Воспроизведение

```sh
python3 docs/operations/measurements/2026-09-17-judge-cost-investigation/evidence/verify.py
```

Скрипт проверяет manifest, полноту агентских результатов, количество rows, subset-отношения tokens, отсутствие Anvil provider prices,38 attempts без usage, overlap291 и согласованность alias revision, затем пересчитывает estimate. Для реального денежного итога потребуется внешний billing export; скрипт его не подменяет.
