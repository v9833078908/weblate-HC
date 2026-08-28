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
`weblate/checks/judge.py`, prompt — `weblate/trans/judge_prompts/verdict.txt`.

- Удалить `instruction` из wire schema и обязательного набора полей model
  response.
- Строго валидировать только `id`, `verdict`, `errors`, `back_translation`,
  полноту batch и уникальность IDs.
- Новые verdict rows сохраняют `instruction=""`; существующее DB-поле оставить
  для обратной совместимости без миграции данных.
- Repair prompt строить детерминированно: validated error descriptions плюс
  фиксированное требование «исправить все перечисленные ошибки и сохранить
  остальной смысл, placeholders и markup». Regression test доказывает
  эквивалентность нового deterministic wrapper'а существующему
  `describe_latest_verdict()` — repair path больше не видит model-generated
  text.
- Исторические model-generated instructions больше не использовать при repair.
- Не принимать JSON из `reasoning_content`, Markdown fences, bare arrays,
  index-keyed objects или неполных batches.
- Обновить formatting-часть Judge prompt: явно потребовать один JSON object с
  `segments` и четырьмя core fields, не меняя MQM/severity правила.
- Реализовать через TDD; отдельный regression test должен доказать, что
  `instruction=null`, `"None"`, missing или лишний текст больше не превращают
  валидный verdict в `unparsed`.
- TODO после переходного релиза: удалить `JudgeResult.instruction` из
  dataclass и перестать писать `JudgeVerdict.instruction` для новых rows
  (поле БД остаётся для исторических данных).

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
  keyed batch digest. Attempt создаётся в client-слое на каждый HTTP-вызов
  (включая transport failures — usage rows сейчас не пишутся при
  `payload=None`, поэтому attempt, а не usage, является учётной единицей
  billed spend); его id возвращается внутри `JudgeResult`, и `_write_verdict`
  (`weblate/trans/judge_loop.py`) пишет FK из него — общего ключа между
  слоями кроме этого id нет.
- Все observability writes — best effort: их ошибка не меняет verdict и retry
  decision.
- Обновить threat model: attempt storage не должен становиться confirmation
  oracle для переводов.
- Retention: для `JudgeRequestAttempt` и `LLMUsageLog` задаётся bounded
  retention (или archival) и покрывающие индексы до включения deferred queue:
  attempt per HTTP call плюс indefinite retries иначе растят БД без cleanup.
- Задача 4 (SSE reader) зависит от этой задачи: typed `failure_kind` должен
  существовать до того, как streaming transport начнёт классифицировать
  partial JSON, missing final chunk и `finish_reason=length`; сейчас
  `_post_batch` теряет exception class в blanket `except Exception`, а
  deadline возвращается через `buffer is None` без типа.

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
| 2 | `weblate-judge-qwen3.8-max` | native strict `json_schema` | `enable_thinking=false` | true | 0 | 5 |

Stream для Qwen — `true`: §4 объявляет Qwen SSE первичным production
candidate, а non-streaming — NO-GO до отдельного production-envelope gate
после исправлений HCBifrost. Profile resolver берёт значение из этой таблицы,
поэтому production default здесь обязателен, а не только в тексте §4.

- `max_tokens=0` означает «не передавать»; до измерения не вводить truncation
  cap.
- Profile resolver валидирует обе seats до первого платного запроса.
  Hardcoded `_LITELLM_QWEN_THINKING_DISABLED_MODELS` /
  `_LITELLM_THINKING_DISABLED_MODELS` (`weblate/trans/judge.py`) удаляются:
  reasoning payload вычисляется из per-seat profile, один источник истины.
- Cache reuse требует совпадения endpoint, model, response format, reasoning,
  stream mode, temperature, prompt/schema version и project context.
  Prompt/schema version — конкретный механизм: content hash файла
  `verdict.txt` плюс schema revision constant в коде; без этого требование
  «изменение профиля инвалидирует cache» непроверяемо. Тест инвалидации при
  смене версии обязателен.
- Любое изменение профиля инвалидирует старый verdict cache.
- Cache identity материализуется denormalized полями `profile_fingerprint` и
  `prompt_schema_version` на `JudgeVerdict` (snapshot на `JudgeRun`
  недоступен из `_cached_verdict`: `run_id` — standalone UUID, а
  `run_judge_batch` генерирует собственный uuid). Исторические rows имеют
  NULL и потому никогда не reusable — отдельной data-миграции не нужно.
- Fingerprint включает resolved upstream model из `/model/info` и alias
  revision/config hash: retarget alias оператором при неизменных env
  инвалидирует cache.
- Единый canonical request identity (unit, target/context hashes, project
  context, языковая пара, profile fingerprint, prompt/schema version)
  одинаково используется cache, deferral staleness и attempts; текущий
  `compute_context_hash()` project context не включает, identity расширяется.
- Новые настройки объявляются на всех поверхностях:
  `weblate/trans/defaults.py`, `weblate/trans/models/_conf.py`,
  `weblate/settings_docker.py`, `settings_example`, docker-compose env — с
  inherit-семантикой на каждой; `_conf.py` сегодня не имеет даже
  deadline/transport-retries.
- Единая profile-aware call-planning функция заменяет расчёты через
  глобальный `JUDGE_BATCH_SIZE`: `preview_judge_scope`
  (`weblate/trans/autotranslate.py`), `judge_request_upper_bound`
  (`weblate/trans/judge.py`), estimate в views; миграция всех callers и их
  тестов — часть этой задачи, иначе estimates, run cap и progress врут при
  per-seat ceilings 2/5.

Commit: `feat(judge): support per-seat LiteLLM profiles`.

### 4. Исправить streaming и локализовать gateway transport

В Weblate:

- Реализовать общий OpenAI-compatible SSE reader для Qwen и DeepSeek: собирать
  только `delta.content`, отдельно учитывать `delta.reasoning_content`,
  `finish_reason` и final usage.
- Передавать `stream_options.include_usage=true`.
- Сохранять абсолютный deadline и response-size cap. Мигрировать
  `DEFAULT_JUDGE_REQUEST_DEADLINE` 300→120 (`weblate/trans/defaults.py`):
  текущий default позволяет deploy работать с 300s против заявленного 120s
  контракта. Для SSE httpx timeout определяется как absolute deadline плюс
  idle-between-chunks, а не один `timeout=120`: молчащий chunk не должен
  висеть до absolute deadline.
- Partial JSON до `[DONE]`, missing final chunk и `finish_reason=length` считать
  typed failures.
- Qwen streaming — первичный production candidate: только этот режим отдал
  первый байт для того же payload до измеренной 30-секундной границы. Qwen
  non-streaming остаётся control arm и не допускается без отдельного
  production-envelope gate после исправлений HCBifrost.

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
- **Статус 2026-08-28:** администраторы сообщили о применении nginx
  `proxy_read_timeout=130s`, `proxy_send_timeout=130s` и
  `proxy_buffering=off`. Post-change control не прошёл: Qwen non-streaming
  снова reset на 31.5-31.8 секунды без первого байта, streaming завершился
  дважды. Изменение остаётся незачтённым до проверки serving config/reload и
  коррелированного trace через public nginx, internal LiteLLM и upstream.
- Прокинуть correlation ID и сохранить redacted timestamps: ingress,
  upstream dispatch, upstream first byte, downstream first byte, completion и
  reset/error owner. Сопоставить nginx access/error logs с LiteLLM logs.
- Задача 4 (hcbifrost) — external deliverable вне этого репо: manifests,
  nginx vhost и LiteLLM config здесь не коммитятся. В плане фиксируется
  только acceptance contract (этот раздел) и owner; фактическое изменение
  gateway поставляется, ревьюится и откатывается владельцем hcbifrost со
  своими artifacts и версией image.
- Исправить capability metadata aliases: actual upstream ID, provider,
  structured-output и streaming support.
- Redacted wire trace должен подтвердить, что DeepSeek получает
  `thinking.disabled`, а Qwen — `enable_thinking=false`.

Proxy capability smoke, недостаточный для production admission:

- 30 одинаковых safe requests на каждую alias;
- ноль resets, empty responses и malformed envelopes;
- Qwen reasoning tokens = 0;
- DeepSeek reasoning tokens = 0;
- если DeepSeek upstream не исполняет `thinking.disabled`, production switch
  блокируется без импровизированной замены модели.

Provider transport gate:

- для каждой seat interleaved-матрица `stream=true/false`, clean/controlled
  defect, width 1/production ceiling;
- минимум 30 baseline requests в каждой cell, LiteLLM/provider/Weblate retries
  выключены; recovery arm измеряется отдельно;
- точные production prompt, schema, response format и reasoning controls;
- Qwen non-streaming считается NO-GO до новой успешной проверки после
  HCBifrost fix; safe requests не могут изменить этот статус.

### 5. Добавить classified recovery и adaptive batching

App-owned retry policy:

- transport reset — один retry с exponential backoff и full jitter;
- `429` — один retry с `Retry-After`;
- `5xx` — один retry;
- invalid JSON/envelope/segment — один same-seat/same-model retry;
- повторный protocol failure для batch > 1 — одна width-one isolation round;
- deadline и `finish_reason=length` не повторять с той же формой: уменьшить
  batch budget; width-one request с `finish_reason=length` не имеет
  batch/output knob — unit получает terminal operator-visible failure state,
  а не бесконечный deferral (per-seat `max_tokens` — измеряемый эксперимент
  после canary, не default);
- `401/403` — fail-fast configuration error без повторных платных calls;
- никогда не подменять opinion другой моделью или OpenRouter.

Adaptive harness:

- отдельное состояние для `(endpoint, model, seat)`;
- DeepSeek начинает с ceiling 2, Qwen с ceiling 5;
- batch заполняется фиксированным per-seat ceiling (DeepSeek 2, Qwen 5) без
  predictor: ramp только по measured outcome; predicted-output estimator —
  TODO после накопления attempt data;
- transport/deadline делит budget пополам;
- пять последовательных clean attempts увеличивают budget на одну единицу;
- parser failure не меняет batch budget;
- streaming target envelope: first-byte p95 <20 секунд, измерять также
  time-to-first-content, total completion и наличие `[DONE]`;
- non-streaming target envelope: response/first-byte p95 <20 секунд и ноль
  reset-before-first-byte; для обоих режимов измерять parse, truncation, bytes,
  finish reason, reasoning tokens и cost.

Defaults:

```text
JUDGE_TRANSPORT_RETRIES=1
JUDGE_PROTOCOL_RETRIES=1
JUDGE_TRANSIENT_HTTP_RETRIES=1
JUDGE_MAX_UNPARSED_RETRY_ROUNDS=1
JUDGE_RETRY_BUDGET_RATIO=0.2
```

- `JUDGE_RETRY_BUDGET_RATIO` измеряется и резервируется в attempts
  (`JudgeRequestAttempt`, задача 2): cap проверяется до отправки retry, а не
  post-hoc по usage rows; token/cost — отдельный reconciliation report с
  пометкой unknown для reset/billed calls без usage row.

Commit: `feat(judge): add bounded adaptive recovery`.

### 6. Добавить durable deferred queue

Добавить `JudgeDeferral` для units, которые не получили opinion после bounded
recovery.

- Identity включает unit, target/context hashes, project context и seat
  profile — per-seat (missing-seat set), не per-unit: одна seat могла
  ответить, пока другая нет, и unit-level row не различает эти случаи.
- Состояния: `queued`, `slow`, closed.
- Повторная постановка не создаёт duplicate.
- Изменившийся target/context закрывает старую deferral как stale.
- Успешный parsed verdict закрывает запись; proxy failure никогда не меняет
  состояние перевода.
- После пяти последовательных failures запись становится `slow`, но не
  удаляется.
- Периодическая задача группирует units по translation и использует текущий
  валидный profile. Pass chunk'ируется по времени/budget, а не только по
  `MAX_UNITS_PER_PASS`: 200 units × 2 seats × retries — десятки минут одной
  Celery-задачи. Verdicts drain-задачи записываются от system actor
  (`user=None`-эквивалент), не от инициатора исходного run — actor model
  фиксируется при реализации и отражается в audit. Drain строго read-only:
  judge вызывается с `writable_ids=set()` и без `repair_targets`; успешный
  retry сохраняет verdict, но никогда не меняет target/state — system actor
  сам по себе этого не гарантирует.
- Claim drain-задачи транзакционен с lease expiry и idempotent completion:
  два concurrent worker не должны оплатить один unit; stale claim
  восстанавливается по истечении lease.
- Circuit breaker и token bucket — shared backend (DB/Redis), не
  process-local: состояние переживает restart и общее для всех workers;
  семантика open/half-open, пороги и operator stop state фиксируются при
  реализации; тот же per-(endpoint, model, seat) state bucket, что и
  adaptive batching, а не отдельная структура.

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
- для каждой seat и выбранного production mode first-byte p95 <20 секунд,
  ноль reset-before-first-byte, empty/truncated responses и unknown transport
  failures на production-shaped envelope;
- streaming завершается `[DONE]`, накопленный JSON проходит тот же parser, а
  total completion укладывается в absolute deadline;
- любой reset Qwen non-streaming около 30 секунд оставляет этот mode в NO-GO;
- retry multiplier и cost полностью учтены;
- deferred queue опустошается в пределах тестового окна.

## Rollout

1. Закоммитить и запушить каждую задачу отдельным Conventional Commit;
   deployment не выполнять.
2. Задеплоить migrations/code с существующим OpenRouter runtime и всеми новыми
   флагами выключенными — только после явного approval.
3. Применить `hcbifrost` aliases/timeouts и получить коррелированный redacted
   trace через public nginx, internal LiteLLM и, по возможности, direct
   upstream.
4. Выполнить interleaved live transport envelope и quality gates в dev.
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
