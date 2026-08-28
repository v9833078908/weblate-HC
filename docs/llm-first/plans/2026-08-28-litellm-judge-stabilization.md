# End-to-end стабилизация LiteLLM Judge

**Дата:** 2026-08-28. **Статус:** approved, not started.

## Summary

Цель — перевести обе Judge seats на LiteLLM без автоматического fallback в
OpenRouter и обеспечить измеряемую стабильность: валидный verdict не теряется
из-за repair metadata, gateway не сбрасывает медленные ответы, каждый отказ
классифицирован, а неоценённые строки автоматически дожидаются повторной
обработки.

Архитектура:

- Weblate остаётся владельцем Judge-контракта, semantic validation, retry budget
  и deferred queue.
- `hcbifrost` отвечает за предсказуемый transport, корректную передачу
  provider-параметров и отсутствие скрытых retries.
- Qwen и DeepSeek получают разные immutable request profiles.
- OpenRouter сохраняется только как ручной rollback до завершения production
  canary.

Этот план заменяет противоречащие части
`docs/llm-first/plans/2026-08-27-judge-reliability-hardening.md`, включая тезис
«это не client defect».

## Implementation changes

### 1. Исправить Judge output contract

Основной код: `weblate/trans/judge.py`, `weblate/trans/models/judge.py`,
`weblate/checks/judge.py`.

- Удалить `instruction` из wire schema и обязательного набора полей model
  response.
- Строго валидировать только `id`, `verdict`, `errors`, `back_translation`,
  полноту batch и уникальность IDs.
- Новые verdict rows сохраняют `instruction=""`; существующее DB-поле оставить
  для обратной совместимости без миграции данных.
- Repair prompt строить детерминированно: validated error descriptions плюс
  фиксированное требование «исправить все перечисленные ошибки и сохранить
  остальной смысл, placeholders и markup».
- Исторические model-generated instructions больше не использовать при repair.
- Не принимать JSON из `reasoning_content`, Markdown fences, bare arrays,
  index-keyed objects или неполных batches.
- Обновить formatting-часть Judge prompt: явно потребовать один JSON object с
  `segments` и четырьмя core fields, не меняя MQM/severity правила.
- Реализовать через TDD; отдельный regression test должен доказать, что
  `instruction=null`, `"None"`, missing или лишний текст больше не превращают
  валидный verdict в `unparsed`.

Commit: `fix(judge): decouple repair instructions from verdict parsing`.

### 2. Ввести typed diagnostics и безопасную provenance

Добавить private parse result с закрытым `failure_kind`:

```text
transport
deadline
response-too-large
http-auth
http-rate-limit
http-server
http-other
empty-response
invalid-json
invalid-envelope
segment-count
invalid-segment
finish-length
unknown
```

- `batch ok` писать только после успешного parser; HTTP 200 отдельно обозначать
  как transport success.
- Сохранять `status`, exception class, `finish_reason`, elapsed/first-byte
  milliseconds, response bytes, token usage, reasoning tokens и shape metadata.
- Не сохранять API keys, headers, prompt, source/target, completion или
  reasoning text.
- Добавить `JudgeRequestAttempt`, ссылку из usage/verdict rows, safe
  configuration snapshot на `JudgeRun`, provider/model/profile fingerprints и
  keyed batch digest.
- Все observability writes — best effort: их ошибка не меняет verdict и retry
  decision.
- Обновить threat model: attempt storage не должен становиться confirmation
  oracle для переводов.

Commit: `feat(judge): classify and record judge attempts`.

### 3. Добавить immutable per-seat request profiles

Старые глобальные настройки сохранить как backward-compatible `inherit`. Новые
Docker/settings surfaces:

```text
JUDGE_REASONING_EFFORT_SEAT_1/2
JUDGE_RESPONSE_FORMAT_SEAT_1/2
JUDGE_STREAM_SEAT_1/2
JUDGE_BATCH_SIZE_SEAT_1/2
JUDGE_TEMPERATURE_SEAT_1/2
JUDGE_MAX_TOKENS_SEAT_1/2
```

Production profiles после canary:

| Seat | Model alias | Format | Reasoning | Stream | Temperature | Batch ceiling |
| --- | --- | --- | --- | ---: | ---: | ---: |
| 1 | `weblate-judge-deepseek-v4-pro` | `json_object` + app validation | `thinking.disabled` | true | 0 | 2 |
| 2 | `weblate-judge-qwen3.8-max` | native strict `json_schema` | `enable_thinking=false` | false | 0 | 5 |

- `max_tokens=0` означает «не передавать»; до измерения не вводить truncation
  cap.
- Profile resolver валидирует обе seats до первого платного запроса.
- Cache reuse требует совпадения endpoint, model, response format, reasoning,
  stream mode, temperature, prompt/schema version и project context.
- Любое изменение профиля инвалидирует старый verdict cache.

Commit: `feat(judge): support per-seat LiteLLM profiles`.

### 4. Исправить streaming и gateway transport

В Weblate:

- Реализовать SSE reader для DeepSeek: собирать только `delta.content`, отдельно
  учитывать `delta.reasoning_content`, `finish_reason` и final usage.
- Передавать `stream_options.include_usage=true`.
- Сохранять абсолютный deadline и response-size cap.
- Partial JSON до `[DONE]`, missing final chunk и `finish_reason=length` считать
  typed failures.
- Qwen оставить non-streaming, поскольку `enable_thinking=false` даёт
  достаточный TTFB.

На `hcbifrost`:

- Зафиксировать текущий LiteLLM image digest/version; запретить floating tag.
- Создать два выделенных Weblate Judge aliases, не меняя shared aliases других
  приложений.
- Для aliases установить LiteLLM `num_retries=0` и provider `max_retries=0`:
  скрытые provider calls запрещены, retry принадлежит Weblate.
- Проверить передачу `response_format`, `stream`, `stream_options`,
  `temperature`, `thinking` и `enable_thinking`; `drop_params` не должен молча
  удалять их.
- Установить layered timeouts: upstream/LiteLLM 110 секунд, Weblate absolute
  deadline 120 секунд, nginx read/send timeout 130 секунд.
- Для SSE отключить proxy buffering и подтвердить, что первый chunk доходит до
  клиента.
- Исправить capability metadata aliases: actual upstream ID, provider,
  structured-output и streaming support.
- Redacted wire trace должен подтвердить, что DeepSeek получает
  `thinking.disabled`, а Qwen — `enable_thinking=false`.

Proxy acceptance gate:

- 30 одинаковых safe requests на каждую alias;
- ноль resets, empty responses и malformed envelopes;
- Qwen reasoning tokens = 0;
- DeepSeek reasoning tokens = 0;
- если DeepSeek upstream не исполняет `thinking.disabled`, production switch
  блокируется без импровизированной замены модели.

### 5. Добавить classified recovery и adaptive batching

App-owned retry policy:

- transport reset — один retry с exponential backoff и full jitter;
- `429` — один retry с `Retry-After`;
- `5xx` — один retry;
- invalid JSON/envelope/segment — один same-seat/same-model retry;
- повторный protocol failure для batch > 1 — одна width-one isolation round;
- deadline и `finish_reason=length` не повторять с той же формой: уменьшить
  batch/output budget;
- `401/403` — fail-fast configuration error без повторных платных calls;
- никогда не подменять opinion другой моделью или OpenRouter.

Adaptive harness:

- отдельное состояние для `(endpoint, model, seat)`;
- DeepSeek начинает с ceiling 2, Qwen с ceiling 5;
- batch заполняется до predicted output budget, не только по числу строк;
- transport/deadline делит budget пополам;
- пять последовательных clean attempts увеличивают budget на одну единицу;
- parser failure не меняет batch budget;
- target envelope — 20 секунд до первого байта/ответа в non-streaming режиме.

Defaults:

```text
JUDGE_TRANSPORT_RETRIES=1
JUDGE_PROTOCOL_RETRIES=1
JUDGE_TRANSIENT_HTTP_RETRIES=1
JUDGE_MAX_UNPARSED_RETRY_ROUNDS=1
JUDGE_RETRY_BUDGET_RATIO=0.2
```

Commit: `feat(judge): add bounded adaptive recovery`.

### 6. Добавить durable deferred queue

Добавить `JudgeDeferral` для units, которые не получили opinion после bounded
recovery.

- Identity включает unit, target/context hashes и seat profile.
- Состояния: `queued`, `slow`, closed.
- Повторная постановка не создаёт duplicate.
- Изменившийся target/context закрывает старую deferral как stale.
- Успешный parsed verdict закрывает запись; proxy failure никогда не меняет
  состояние перевода.
- После пяти последовательных failures запись становится `slow`, но не
  удаляется.
- Периодическая задача группирует units по translation и использует текущий
  валидный profile.
- Circuit breaker и token bucket ограничивают стоимость при outage.

Defaults:

```text
JUDGE_DEFERRAL_ENABLED=false
JUDGE_DEFERRAL_MIN_INTERVAL=900
JUDGE_DEFERRAL_MAX_INTERVAL=86400
JUDGE_DEFERRAL_SLOW_AFTER=5
JUDGE_DEFERRAL_MAX_UNITS_PER_PASS=200
```

Флаг включается только после production canary.

Commit: `feat(judge): drain deferred judge units`.

### 7. Закрыть credential incident

Перед переключением production:

- ротировать LiteLLM, OpenRouter, SMTP, PostgreSQL, admin/bootstrap и остальные
  credentials из production `.env`, ранее попавшие в диагностический вывод;
- обновить local secret source и production environment без печати значений;
- отозвать старые внешние keys;
- перезапустить затронутые сервисы только после отдельного deployment approval;
- проверить доступность приложения, БД, SMTP и обоих Judge aliases.

## Test and measurement plan

### Automated tests

- Parser: все failure kinds, duplicate/missing IDs, malformed JSON,
  reasoning-only response и instruction regressions.
- Profiles: точные payloads для Qwen/DeepSeek, inheritance, invalid
  configuration before POST и cache invalidation.
- Streaming: fragmented SSE, reasoning/content separation, usage chunk, reset
  before first byte, truncated JSON, deadline и size cap.
- Recovery: точные call counts, backoff, no retry on auth/length, width-one
  isolation и spend budget.
- Persistence: attempt/usage/verdict links, no raw text/secrets, transaction
  failures do not affect judging.
- Queue: deduplication, stale target, backoff, slow state, circuit breaker и
  eventual drain.

Commands:

```text
./rundev.sh test weblate/trans/tests/test_judge_client.py
./rundev.sh test weblate/trans/tests/test_judge_loop.py
./rundev.sh test weblate/trans/tests/test_judge_autotranslate.py
./rundev.sh test weblate/trans/tests/test_judge_views.py
uv run prek run --files <changed-files>
git diff --check
```

Full Judge suite and relevant machinery/check tests must pass before commit/push.

### Live envelope test

До sealed quality corpus:

- 10 matched units: пять clean и пять controlled defects;
- обе модели, batch widths 1 и production ceiling;
- три повтора без retries;
- recovery arm повторяет только failed requests;
- отдельно считать transport success, protocol parse, terminal coverage,
  latency, first-byte time, reasoning tokens и cost.

Переход к quality gate разрешён только при отсутствии unknown failures и
terminal parse rate не ниже 99% на каждой seat.

### Quality gate

- `ru→zh_Hans`: весь 124-unit ground-truth corpus;
- `en→fr`: 120-unit stratified corpus со всеми critical cases;
- три повтора;
- OpenRouter — измерительный control, не runtime fallback;
- unparsed считается failure, а не false negative или pass.

GO требует одновременно:

- terminal parse rate ≥99% на каждой seat в каждом повторе;
- ноль all-unparsed units после bounded recovery;
- false-flag rate ≤10%;
- union critical recall не хуже OpenRouter;
- Qwen и DeepSeek reasoning controls фактически исполняются;
- DeepSeek first-byte p95 <20 секунд и ни одного reset около 30 секунд;
- retry multiplier и cost полностью учтены;
- deferred queue опустошается в пределах тестового окна.

## Rollout

1. Закоммитить и запушить каждую задачу отдельным Conventional Commit;
   deployment не выполнять.
2. Задеплоить migrations/code с существующим OpenRouter runtime и всеми новыми
   флагами выключенными — только после явного approval.
3. Применить и проверить `hcbifrost` aliases/timeouts.
4. Выполнить live envelope и quality gates в dev.
5. Production canary: одна контролируемая component, максимум 100 units,
   `JUDGE_MAY_APPROVE=false`, deferral выключен.
6. Если canary проходит, включить LiteLLM profiles site-wide; затем включить
   deferred queue.
7. Ручной rollback: восстановить OpenRouter environment и recreate только
   Weblate container. Автоматического OpenRouter fallback в коде нет.
8. Любой unknown failure, all-unparsed unit, auth error, terminal coverage <99%
   или повторный 30-second reset останавливает rollout.

## Assumptions

- Владелец `hcbifrost` доступен для изменения gateway и предоставления redacted
  server-side timing logs.
- Если proxy не может корректно передать DeepSeek `thinking.disabled`, модель не
  допускается в production; другая seat не выбирается без отдельного quality
  evaluation.
- Новые migrations только добавляют nullable/defaulted данные; существующие
  verdicts и run reports остаются читаемыми.
- Все production deployment, restart, secret rotation и gateway mutation
  требуют отдельного явного подтверждения.
