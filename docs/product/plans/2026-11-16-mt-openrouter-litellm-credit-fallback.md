# MT fallback: OpenRouter/Gemini → LiteLLM/Qwen

Дата: 2026-11-16. **Phase 2: полный legacy + durable план для реализации.**
Направление и реализация одобрены владельцем; финансовое уточнение D1 также
**разрешено**, см. `docs/product/plans/2026-11-16-mt-openrouter-litellm-credit-fallback-requirements.md`
и `docs/product/plans/2026-11-16-mt-openrouter-litellm-credit-fallback-authorization.md`.
Дополнительного подтверждения на каждый подходящий запрос не требуется.
Это не разрешение на production deployment, конфигурацию, активацию,
платные generation calls или запись переводов в production.

Правила: `AGENTS.md`. Только абсолютный worktree
`.worktrees/mt-credit-fallback`, ветка `feat/mt-credit-fallback`, исходная база
`778f287b`; основной checkout не трогать. Corporate reconciliation и итоговые
commit/push/PR принадлежат основному агенту. В planning-сессии изменён только
этот план; код, тесты, линтеры, сборки и production не выполнялись.

## Цель, scope и разрешение второй оплаты

Один MT-прогон сохраняет пригодные primary-ответы OpenRouter/Gemini. После
подтверждённого `insufficient-credit` он прекращает новые primary submissions,
дренирует уже запущенные запросы и переводит допустимый остаток через отдельный
бюджет LiteLLM: **`QWEN3.7-plus`, top-level `reasoning_effort: "none"`**.
Успешный primary не вызывает fallback.

Владелец разрешил ровно одну отдельную fallback paid attempt на подходящую
неразрешённую unit: подтверждённо refused members и никогда не отправленный
credit-stopped tail **того же запуска/операции**. Это standing authorization,
не утверждение, что primary отказ был бесплатным. Она материализуется отдельным
аудируемым stage journal до отправки, а не другим semantic identity.

- Полный deliverable включает обычный legacy UI/API autotranslate и durable
  Producer MT через public `process_mt_operation`; legacy-only не считается done.
- Primary routing, `ROUTED_ENGINES`, engine preference, judge profiles/budget,
  judge repair и обязательная pre-judge MT preparation не меняются.
- Несколько выбранных engines остаются сравнением результатов на всём scope.
  Pilot применяется только при единственном primary `openrouter`, не к прямым
  editor machinery suggestions; `overwrite_existing=True` не включает fallback.
- Fallback не перезаписывает непустой target. Сохраняются выбранные обычные
  translate/suggest/fuzzy/approved semantics, protected slots, glossary и prompt.
- Timeout/lost/paid-unknown, parsing/partial reply, generic/provider error,
  auth/permission, rate limit и другая quota **не** разрешают fallback.
  Поздний credit не легализует ранее неизвестную/invalid оплату.
- Нет третьей модели, автоматического повторения fallback, возврата к primary,
  retries/split/rescue на неизвестном результате. Существующие финансовые
  fences остаются; новые проверки добавляют отказы, не снимают старые.

### Входные свидетельства, не новая production-проверка

Исходный план/requirements фиксируют ранее разрешённые probes: `QWEN3.7-plus`
и `qwen3.7-plus` ведут на `dashscope/qwen3.7-plus`; `reasoning_effort=none` дал
HTTP 200 и zero reasoning, `low` reasoning не отключил. Anvil prompt со strict
schema/protected slots прошёл parser на двух длинных строках. Это не оценка
качества, SLA или стабильности 10-string batches. Выбор владельца — только
`QWEN3.7-plus`, без второго fallback DeepSeek. Project LiteLLM согласно
requirements отсутствовал; здесь effective production config не перепроверялся.

## Source map: текущие интерфейсы и ограничения

Пути и диапазоны относятся к исходникам worktree до реализации. Новые имена
ниже явно обозначены как proposed.

| Граница | Наблюдаемое поведение / источник |
|---|---|
| Legacy | `weblate/trans/autotranslate.py:833–977`: `AutoTranslate.fetch_mt(engines_list, threshold, on_batch=None, on_failure=None) -> dict[int, UnitMemoryResultDict]`; `fetch_mt_checked` возвращает `(translations, failures)`. Repeat reuse происходит до batches. |
| Scheduler | `weblate/trans/machinery.py:38–79,164–356`: `MachineryBatchOutcome(status, service, unit_ids, reason_code, error)`; `fetch_machinery_matches(*, units, user, services, threshold, set_progress=None, log_translation=None, on_batch=None, on_failure=None)`. Каждый service получает весь scope; при >1 `on_batch` выключен. Failures сообщаются после drain, skipped не сообщаются через `on_failure`. |
| Stop defect | `_ServiceRun.observe:74–78` запоминает первый любой reason; ранний nonterminal `provider-unavailable` маскирует поздний credit. Нужна первая терминальная stop reason при сохранённой истории всех outcomes. |
| Внутренние запросы | `weblate/machinery/base.py:462–533,1099–1130,1251–1286,1325–1370,1430–1494`; `weblate/machinery/llm.py:2878–2950,3358–3372`: transport retries, parser rescue/split, несколько HTTP внутри scheduler batch; equal source text разделяется в pending groups, результаты группируются по text. `batch_translate` публикует `unit.machinery` после полного `_translate_sources`. |
| Store/status | `autotranslate.py:979–1151`: fatal primary refusal безусловно задаёт failure; обычный store не использует empty/source guard. `written` не доказывает изменение target. `BatchAutoTranslate._finish_translation:2801–2818` агрегирует результаты, `_finish_producer_run:2578–2784` заменяет summary. Summary не финансовый journal. |
| Redelivery | `weblate/trans/tasks.py:1193–1202` и `autotranslate.py:2526–2575`: late-ack task использует тот же run после worker loss. Недостаточно выключить retries только внутри одного вызова. |
| Settings | `weblate/trans/models/project.py:1331–1350`: field-by-field inheritance, `None` отключает service. `weblate/trans/forms.py:1298–1317,1459–1476`: OpenRouter project-wide предпочтителен; это не language failover. |
| Public settings | `weblate/machinery/views.py:338–400`: project form, inherited initial и diff-only persistence. `weblate/machinery/models.py:37–97`: API validator использует `settings_form`, не project form, возвращает исходную configuration. `weblate/api/views.py:2430–2518`: POST/PATCH service, PUT заменяет карту. |
| Validation | `weblate/machinery/forms.py:89–123,486–537`: prompt-only changes пропускают live validation; unchanged save намеренно проверяет service. API validator сейчас не передаёт initial. Toggle fallback не должен требовать платного успешного primary probe при пустых кредитах. |
| Routed wire | `weblate_customization/src/weblate_customization/machinery.py:48–140,167–226,354–381,612–640`: скрытый OpenRouter project key; отдельные LiteLLM key/base_url; LiteLLM удаляет `provider`. Frozen v1/v2/v3 выбираются до rendering. |
| Durable entry | `weblate/api/producer/mt.py:155–304` → `weblate/trans/tasks.py:294–300` → `weblate/trans/mt_operations.py:302–516`, public `process_mt_operation(run_id: str | UUID) -> dict`. Admission допускает только single-source/empty units. |
| Logical fence | `models/producer_mt.py:60–81`, migration `0147_producer_mt_operations`: одна non-released `ProducerMTUnitAttempt` на `(unit, revision)`, даже между keys. |
| Financial fence | `models/llm_delivery.py:55–63`, migration `0150_llm_delivery_journal`: unique semantic_key → один exchange. `llm.py:2997–3011`: canonical `mt:{unit}:{language}:{source_decision_revision}`, без модели/run/policy. `llm_delivery.reserve_exchange:100–197` требует прежние scope/digest/codec. |
| Capture/usage | `machinery/openai.py:40–85`: request digest из URL/method/payload, usage lookup через `exchange_for_work(work)`. `base.py:519–528` сохраняет HTTP до classification. `llm_delivery.py:200–279,358–457`: checksummed private receipts, usage-once, replay. Fallback usage нельзя искать по primary work FK. |
| Recovery | `mt_operations.py:63–172,193–297,519–677`: broad catch часто превращает полученный 402 в provider_unknown; recovery ищет primary work и только status 200. Reconcile на `590–594` releases pending как never_sent — это небезопасно при уже существующей fallback reservation. |
| Разрешение | `llm_delivery.reconcile_exchange:367–375` запрещает allow_new_charge без отдельной audited authorization. Judge-specific `302–339` разрешает captured 429/5xx, не MT credit. D1 разрешает добавить отдельный MT stage interface, но не менять эти правила. |

## Общий config и frozen Qwen profile

Proposed поле существующей `Project.machinery_settings`:

```json
{"openrouter": {"mt_fallback": {
  "engine": "litellm", "routing": {"*": "QWEN3.7-plus"}
}}}
```

Это не новый batch API input. Отсутствие/`{}` = off; enabled object принимается
только в точности как выше. Other engines/models, лишние ключи, credentials,
endpoint/reasoning внутри metadata отклоняются. `{}` отключает inherited enabled
value. Не превращать fixed pilot route в общую routing API.

- Объявить поле/clean в `RoutedLLMMachineryForm`, унаследовать в
  `RoutedProjectOpenRouterMachineryForm`; LiteLLM form поля не имеет. Проверить
  новый ключ и на API raw configuration: неизвестные form fields сейчас не
  удаляются из возвращаемого API validator configuration. Новый ключ у другого
  engine отклонять узко, без переделки всей конфигурационной API.
- Добавить type в `weblate/machinery/types.py::SettingsDict`. Runtime повторяет
  structural validation для raw/historical JSON: malformed → warning и zero
  fallback calls, не неожиданный маршрут.
- Metadata-only web save использует существующий skip-service-check механизм,
  не объявляя metadata prompt text. Proposed optional
  `initial_configuration: SettingsDict | None = None` у
  `validate_service_configuration` задаёт form initial отдельно от site-wide
  merge base. API project callers передают предыдущее **effective** service
  configuration, включая project overrides, для сравнения POST/PATCH/PUT.
  Metadata-only exception требует уже usable configured primary; key/URL/routing
  changes и unchanged save сохраняют существующую validation. Сетевые проверки
  в тестах mocked, не новая платная генерация при toggle.
- Fallback instance берёт effective LiteLLM key/base_url/transport, fixed route
  и effective primary `persona`, `style`, `language_instructions`, включая
  явные пустые значения. `_project`, units/glossary/context те же. Не копировать
  primary key/URL; не мутировать сохранённые settings. Missing/disabled service,
  key или language pair дают явную недоступность, не смену credentials.

`RoutedLiteLLMTranslation.get_chat_payload`: none только для точного
`QWEN3.7-plus`; other aliases/routes и OpenRouter не получают `reasoning_effort`.
Сохранить `temperature=0`, strict schema/parser/protected slots; не добавлять
caps. Новые requests используют proposed **`routed-slots-v4`**: Gemini medium
сохраняется, Qwen получает none. Frozen v3/v2/v1 не получают нового параметра.

Изменить routed default/selection, deterministic example IDs, Gemini condition,
`reserve_exchange` codec allowlist и модель choices. Proposed additive migration
`0157_routed_qwen_reasoning_codec.py`, dependency `0156_judge_delivery_attempt`
(при изменении head пересогласовать номер). Исторические rows/digests/receipts
не backfill. Это wire metadata, не разрешение второй оплаты.

## Аудируемая stage-модель: D1 реализуется, не обходится

Следующие модели/поля **proposed**. Новый файл
`weblate/trans/models/mt_fallback.py`, exports в `models/__init__.py`; additive
`0158_mt_credit_fallback_stages.py` после 0157. Исторические exchanges/attempts
без новых связей сохраняют прежние semantics, не получают автоматическое
разрешение fallback задним числом.

### Schema contract

1. **`ProducerMTFallbackAuthorization`**: UUID, `run` FK PROTECT,
   `translation` FK PROTECT, optional durable `operation` FK PROTECT, actor FK SET_NULL
   плюс actor-id snapshot, project snapshot, immutable scope digest, policy
   `openrouter-credit-to-litellm-qwen37-none-v1`, primary/fallback config digests,
   fixed fallback engine/model/profile, timestamps, state
   `observing/authorized/closed`. Unique **`(run, translation)`**; changed scope
   under той же авторизацией отвергается, не создаёт запись с новым digest.
   Immutable enrolled scope берётся из frozen run scope по translation;
   legacy без scope_snapshot фиксирует полный выбранный translation scope
   перед первым provider call, не только текущий chunk. Chunk continuation
   использует подмножество того же scope, не расширяет его.
2. **`ProducerMTFallbackUnit`**: FK authorization PROTECT, original canonical
   `identity`, globally unique **`semantic_key`** (тот же SHA-256 canonical
   identity, не suffix/policy/run), unit FK SET_NULL плюс immutable ID,
   optional original `ProducerMTUnitAttempt` и `ProducerLLMWork` FKs PROTECT,
   source/target/audit revisions, frozen ordered source slots и per-slot primary
   dispositions/пригодные ответы, eligibility reason, nullable
   `fallback_exchange` FK PROTECT, fallback state/приватный полученный answer.
   Одна такая financial slot на canonical work навсегда; policy version или
   release/replacement unit-attempt не открывают другую fallback slot.
3. **`ProducerMTStageMember`**: FK unit-stage row PROTECT, FK
   `ProducerLLMExchange` PROTECT, stage `primary/fallback`, original source-slot
   index, request position. Unique **`(unit_stage, stage, source_slot)`** и
   **`(exchange, position)`**. Один fallback exchange для всех fallback slots
   одной unit (FK на unit-stage row); mapping immutable после reservation.
   Stage source slots — mapping данных, **не новые financial identities**.
4. Additive nullable/defaulted поля на `ProducerLLMExchange`:
   `mt_authorization` FK PROTECT, `mt_stage`, private `request_snapshot` JSON.
   Snapshot содержит фактически отправляемые method/URL/payload, codec и
   occurrence mapping, материал локального parser/restoration; **без auth
   headers/key**. Это закрытые данные того же класса, что raw replies, не
   публичный serializer. Saved request digest проверяется перед replay.

Новые stage rows не подменяют существующий `ProducerLLMWork.exchange`.
Для refused durable work он продолжает указывать на primary exchange.
Для member, у которого canonical claim ещё отсутствует (never-sent tail или
legacy primary без такого claim), **при fallback reservation** создать
`ProducerLLMWork` с прежней canonical identity и FK на **реальный fallback
exchange**. Не создавать фиктивный primary request/receipt. Реальные legacy
primary receipts доступны через `ProducerMTStageMember(stage=primary)`.
Обычный primary lookup не должен интерпретировать такой tail claim как
сохранённый primary response: stage-aware recovery проверяется первым.

### Общая атомарная arbitration, не две независимые uniqueness проверки

Legacy enrollment, MT stage reservation и существующий `reserve_exchange` для
canonical `mt:` work используют общий lock boundary: **Unit rows по sorted PK
→ unit-stage rows → existing ProducerLLMWork rows → exchange rows**. Никаких
row locks через provider I/O. Проверки нового source revision делаются под этим
lock. Missing Unit допускает лишь локальное чтение существующей истории, не
новую оплату. Judge/repair identities сохраняют прежний путь.

- Enrollment проверяет существующие primary claims/owners/receipts. Чужой или
  unresolved primary exchange не становится fallback-authorized из-за нового
  legacy run. Current durable operation может связать собственные неизменные
  claims с новой stage row; чужой overlap возвращает conflict без send.
- Обычный MT `reserve_exchange` под теми же locks также проверяет существующий
  legacy primary/stage claim и отказывает при conflicting/unknown ownership.
  Это дополнительный deny, а не разрешение изменённого digest/membership.
- Primary/fallback source slots резервируются атомарно до HTTP. Одновременная
  legacy enrollment и durable reservation не могут обе пройти проверку
  отсутствия claim. API admission (`api/producer/mt.py`) проверяет stage claim
  перед новым unit intent; canonical uniqueness остаётся последней защитой.
- New fallback reservation и tail canonical claims создаются одной транзакцией;
  конфликт любого member откатывает **весь** request, без частичной оплаты.

## Proposed public/internal interfaces и transport lifecycle

Новый узкий модуль `weblate/trans/mt_fallback.py` — общая config/policy/stage
граница, не второй task scheduler или provider retry framework:

```text
resolve_mt_credit_fallback(project, *, primary_engine, target_language) -> FallbackProfile | None
enroll_mt_credit_scope(*, run, translation, units, operation=None) -> ProducerMTFallbackAuthorization
authorize_mt_credit_fallback(authorization_id) -> ProducerMTFallbackAuthorization
reserve_mt_fallback_exchange(authorization_id, ordered_slots, request_digest, *, request_snapshot, request_codec, replay_only=False) -> ExchangeRef
dispatch_mt_fallback_exchange(authorization_id, ordered_slots, request_digest, send, *, request_snapshot, request_codec, replay_only=False, before_send=None) -> MTStageDispatchResult
recover_mt_stage_answers(authorization_id) -> StageRecoveryResult
```

`FallbackProfile` — fixed engine/model/reasoning + resolved secret-free identity;
credentials остаются runtime engine settings. `ordered_slots` — закрытый ordered
список `(unit_stage_id, source_slot)`, не произвольные unit IDs или suffix identity.
`MTStageDispatchResult` содержит **`exchange_id` и captured receipt**. Recovery
возвращает persisted stage dispositions/answers, никогда не отправляет запрос.
`request_snapshot` — immutable prepared secret-free actual wire request вместе
с occurrence mapping; его digest проверяется, snapshot commits **в той же
reservation transaction до sending/transport**. Аналогичный аргумент обязателен
у primary-stage dispatch. Send callable не является единственным носителем
snapshot: после crash его уже нет.

Для opted-in legacy primary нужен аналогичный **`dispatch_mt_primary_exchange`**
с тем же exact slot mapping и `MTStageDispatchResult`: он сохраняет attribution,
полный receipt и frozen request каждого внутреннего HTTP до classifier.
Это наблюдение первичной попытки, не fallback authorization. Durable primary
продолжает существующий `dispatch_exchange` и связывает его настоящий exchange
с exact stage members. Исторический primary reservation policy не ослабляется.

`BaseOpenAITranslation.request` получает явный stage context от LLM batch:
primary/fallback, authorization ID, ordered source slots. Он не выводится из
`delivery_purpose` или presence run_id. Для fallback использует новый dispatch;
`llm_delivery_usage`/`record_usage_once` получают **возвращённый stage exchange**,
никогда `exchange_for_work(primary_identity)` после fallback. Saved-codec lookup
тоже stage-specific. ContextVar tokens сбрасываются в finally.

Stage context/mapping устанавливается **до rendering/string-ID generation**;
primary legacy также получает deterministic occurrence wire IDs для exact replay.
В `BatchMachineTranslation.request` (`base.py:519–528`) расширить условие capture
на активный opted-in MT stage: сейчас оно проверяет только
`durable_single_attempt`. Иначе legacy 402 выбросит exception раньше возврата
`send()` и receipt потеряется. Не включать ради этого durable identity mode.
На local recovery использовать frozen request/mapping/codec, а не новый prompt
или заново случайно сгенерированные IDs.

Общую низкоуровневую reserved/sending/capture/replay lifecycle
`llm_delivery.py::dispatch_exchange` переиспользовать через небольшой выделенный
внутренний dispatch-reservation helper, а не копировать другой retry loop:

1. Guard current run/SSO/permissions/revisions и policy до новой reservation.
2. Атомарно exact ordered slots + digest + codec, получить `ExchangeRef`.
3. Только provably unsent `reserved` можно reclaim после lease; permission,
   cancellation, current revisions проверяются снова **до transport**.
4. `reserved → sending` commits до send. Capture полного response до
   classification/parser сохраняет checksummed private receipt.
5. Exact replay возвращает тот же exchange/body без сети/повторного usage.
   `sending/unknown` без intact receipt остаётся unknown; lease expiry не unpaid.
6. Изменение membership/order/digest/codec/model/config не создаёт новый exchange;
   никаких release/rekey/suffix. Однократно занятый fallback slot не открывается
   при failure/timeout/partial или новом request key.

## Legacy: доказательство refusal, drain и применение

### Точные seams

- Сохранить return types и старые callbacks `fetch_mt`/`fetch_mt_checked` и
  `fetch_machinery_matches`. Добавить optional keyword-only `on_outcome` в
  scheduler: финальные success/failed/skipped outcomes на caller thread после
  drain. Добавить `skip_cause: Literal["terminal-refusal", "rate-limit"] | None`
  и stop reason у terminal skips. `on_failure` продолжает сообщать errors один раз.
- `_ServiceRun.observe` запоминает первую терминальную причину, а не первый
  любой reason. In-flight результаты дренируются до fallback, worker DB
  connections закрываются как сейчас; multi-service behavior не менять.
- Proposed opt-in `credit_fallback_single_attempt` на участвующих instances
  задаёт `retry_attempts=0` и no LLM split/rescue; default false для остальных.
  Не использовать `durable_single_attempt=True` как legacy retry toggle:
  он включает другой identity/journal contract и не поддерживает plurals.
- В pre-judge caller явно передать proposed keyword-only
  `allow_credit_fallback=False` через `fetch_mt_checked`/`fetch_mt`.
- Legacy с сохранённым `producer_run` enrolls scope **перед первым primary
  call**. Direct `AutoTranslate` без stable run остаётся primary-only с явной
  diagnostic: нельзя silently делать незажурналированную вторую оплату.
  Пользовательские UI/API/task callers уже передают/создают ProducerRun;
  тест fallback через прямой fetch также обязан передать persisted run.

### Internal-request evidence, не batch-wide угадывание

Stage mapping/receipt сохраняются на `_fetch_llm_batch`/HTTP seam для каждого
реального internal request. В момент primary send exact slots получают sending;
при crash незавершённый witness означает unknown, не never_sent. Auth вычисляется
из сохранённых receipt + slot dispositions после scheduler drain:

- `credit_refused` — **собственный** request slot получил полный qualifying
  response, классифицированный существующим `MachineTranslationServiceError`
  как `REASON_INSUFFICIENT_CREDIT` (402, подтверждённый 403 или upstream 402 в 200).
- `never_sent_due_credit` — slot не имеет отправленной primary reservation и
  входил в enrolled scope, остановленный именно credit. Rate-limit skipped
  или неизвестный sending не подходят. Зарезервированный, но provably unsent
  primary закрывается без отправки и сохраняется как unsent history.
- Пригодные primary slots/повторное reuse сохраняются и вычитаются. Partial,
  invalid, generic, auth, quota или unknown slots не переклассифицируются из-за
  credit в другом request. У unit с таким неразрешённым slot fallback не вызывается.

Перед повторным raise typed refusal опубликовать уже заполненный
`TranslationDownloadPlan.output` через существующую plural/result mapping,
не повторять download и не зависеть от cache. Captured успешные ответы доступны
для local replay после crash, в том числе prefix внутри failed scheduler batch.

### Occurrence-safe fallback adapter, включая plurals

Proposed `BaseLLMTranslation.translate_mt_fallback_slots(...)` принимает frozen
ordered `(unit_stage_id, source_slot, source_text, unit)` и возвращает aligned
slot results. Он использует существующие prompt/schema/protected-token builder
и parser, но **не** text-keyed `_get_pending_batch`/`_build_translation_results`
для финансового grouping/result pairing. Wire IDs детерминированы по
member+occurrence, не являются financial identity.

Каждый request включает **все допустимые unresolved source slots каждой
включённой unit**, даже при равном source text; unit не разбивается на два
fallback HTTP requests. Можно группировать несколько целых units в engine-sized
batch. В одном unit-target объединяются только пригодные сохранённые primary
slots и новые fallback slots. Уже успешный plural slot не переводится заново.
В opted-in primary callback **не передавать неполный plural vector** в обычный
`store_batch`/`store_results`: они сейчас пишут unit уже при `any(quality)`.
Пока отдельные успешные primary slots ждут допустимый credit fallback, они
хранятся приватно в stage journal, target остаётся целиком пустым. Fully covered
primary units сохраняются incremental как прежде. Полный объединённый vector
применяется один раз под empty-target guard; human write в промежутке запрещает
apply, а не разрешает overwrite. Это отдельные успешные internal requests,
не разрешение fallback после partial provider response.
Это fallback-only adapter; обычное primary grouping и durable single-source
admission не переопределяются. Проверить equal-text plural slots и одинаковый
text у units с разными contexts на реальном parser, не словарном mock.

### Оркестрация, guards, progress/report

В `AutoTranslate.fetch_mt` primary и fallback — **два последовательных
single-service passes**, не `services=[primary, fallback]`. После drain загрузить
persisted eligibility; candidates не равны `all_units - results`. Перед каждым
fallback send и apply перечитать target empty, source/context/audit revisions,
unit readonly, translation/component lock/readonly, текущего actor/mode rights
через `check_auto_translate_permission`, cancellation/run state.

Apply под row locks с scoped fallback guard; обычный `store_results` сам этих
проверок не даёт. Не расширять глобально pre-judge preparation behavior.
Проверять реальный target/suggestion outcome после `update`, не просто `written`.
In-flight primary success и human edit не перезаписываются.

Opted-in orchestration направляет **scheduler и store progress** в один coverage
reporter. `store_results:1151` не должен независимо публиковать `len(written)`;
`process_mt:1043–1044` не уменьшает denominator до числа ответов. Множество
terminal original IDs монотонно растёт, denominator фиксируется до первого
fetch (включая reuse/guard-skips); credit remainder ждёт fallback. Component/
chunk aggregation использует union исходного run scope, без второго нуля.
В реальной Celery meta `progress` считается из coverage, proposed
`coverage_done/coverage_total` публикуют эти числа. Существующие `done/total`
(`set_progress:458–460`) **остаются mutation counters** updated/eligible,
не маскируются coverage. Requested/usable/applied/suggested — отдельные counts;
не opted-in поведение не менять. DB callbacks остаются caller-thread.

Primary credit остаётся audit/warning, но не безусловный failure при полном
coverage через fallback. Standalone `process_mt` finalization и
`BatchAutoTranslate._finish_translation/_finish_producer_run` агрегируют стадии,
не стирая provenance. Pre-judge early-return остаётся прежним. Unresolved →
partial/failed, не completed; cancellation сохраняет свои semantics.
Proposed summary keys: `primary_engine`, `fallback_engine`, `fallback_model`,
`fallback_unit_ids` и requested/usable/applied counts. Bot `mt:litellm`/origin
обеспечивают per-unit историю; stage journal — финансовую. Usage одного run
содержит разные provider/model/exchange, NULL cost = unknown. Нет row на 402
не означает бесплатность. Warnings не содержат key/raw provider messages.

## Durable: полная интеграция через process_mt_operation

`build_engine` оставляет primary configured engine, `retry_attempts=0` и
`durable_single_attempt=True`. В `process_mt_operation` enroll/link canonical
scope до запроса, текущие paid claims не release. `_receive_pending` различает
captured typed credit и unknown, но не считает broad Exception разрешением.
SSO refusal до transport остаётся отдельным auth pause как сейчас.

После каждой primary reply сохранить пригодный prefix/usage; после qualifying
credit остановить primary tail и materialize standing authorization из exact
receipt. Shared stage API резервирует один Qwen exchange на eligible member,
включая never-sent tail. Ответ fallback сохраняется до apply; original logical
unit attempt остаётся тем же, aggregate answer/state получает stage provenance,
primary receipt/work FK неизменен. Повторная task delivery загружает прежний
stage exchange, не создаёт новую попытку. `operation_result` и его
`MTReceiptSerializer` (`api/producer/mt.py:76–84,108–128`) получают additive
read-only `fallback` summary: engine/model, requested/usable/applied counts и
unit_ids в исходном scope. Старые fields не менять; prompt/answer/raw receipts
не сериализуются. API engine selection не меняется.

### Recovery/disposition contract

Проверять stage journal **до** любой `pending → released`, новой primary
submission или выбора exchange через primary identity:

| Состояние | Допустимое recovery без новой оплаты |
|---|---|
| Нет primary/stage claim, pending | Подтверждённо never_sent по текущим guards; существующая explicit reconciliation может release. |
| Reserved, нет sending, request/body не отправлялся | Тот же reservation может быть продолжен обычным worker после current guards; reconciliation сам сеть не вызывает и не выдаёт новую авторизацию. |
| Sending/unknown, нет intact receipt | paid_unknown, постоянный fence; нельзя release/retry/создать другой stage/key. |
| Captured primary credit | Local replay classifier + прежняя immutable scope → authorize один fallback, только если policy сейчас enabled и guards действуют. |
| Captured invalid/partial/other refusal | Сохранить пригодный prefix, remainder paid_invalid/failed; не открывать fallback. |
| Captured usable primary/fallback | Parse frozen request/codec и сохранённый occurrence mapping, потом current apply guards; ни primary, ни fallback network не нужны. |
| Applied/nonempty/stale/forbidden/cancelled | Не писать/не платить; receipt и stage financial witnesses сохраняются. |

`_recover_saved_bodies`, `reconcile_mt_operation`, expired-operation recovery
перечисляют оба stage exchanges. Tail с pending unit attempt, но fallback
reserved/sending, **не** становится never_sent/released. Current config digest
не заменяет frozen request; сохранённый request_snapshot/digest/parser mapping
позволяет локальную обработку при выключенном fallback без новых credentials.

`resolve_run_deliveries` и cleanup учитывают оба exchanges того же run: body
retention закрывается только после обработанного terminal результата; unresolved
receipts не удаляются. После разрешённого pruning сохраняются canonical work,
stage uniqueness, digests и audit links. Missing/corrupt body остаётся fenced,
не превращается в разрешение нового charge. No account/run cleanup cascade
не должен стирать финансовые witnesses.
Private `request_snapshot`, frozen source texts и stage answers подчиняются той
же resolved 30-day retention, а не остаются вечной второй копией raw body в БД.
Cleanup удаляет текстовый материал, но оставляет occurrence indices, hashes,
membership/authorization и canonical financial claims. После pruning локальный
replay текста недоступен и не разрешает новую оплату; unresolved не purged.

## Один связный implementation slice для Kimi 2.8

**K1: public settings → v4 profile → shared audited stage dispatch → legacy и
public durable orchestration → recovery/reporting.** Все части одной financial
boundary интегрирует один implementation owner. Не отдавать отдельно legacy
как завершённый deliverable и не проектировать journal по ходу тестов.
D1 закрыт; блокирующего решения владельца по scope/оплате нет.

### Файлы, outcome, зависимости

| Файлы | Ответственность |
|---|---|
| `weblate_customization/src/weblate_customization/machinery.py`; `weblate/machinery/types.py`, `forms.py`, `models.py`, `views.py`; `weblate/api/views.py` | public config, inheritance/metadata-only validation, Qwen/frozen codec |
| proposed `weblate/trans/models/mt_fallback.py`, `models/__init__.py`; `models/llm_delivery.py`; proposed migrations 0157/0158 | permanent audited authorization/membership/exchange snapshots и constraints |
| proposed `weblate/trans/mt_fallback.py`; `weblate/trans/llm_delivery.py`; `weblate/machinery/openai.py`, `llm.py`, `base.py` | common arbitration, stage-aware dispatch/capture/usage, single-attempt/occurrence-safe adapter, primary prefix |
| `weblate/trans/machinery.py`, `autotranslate.py` | terminal fix, drain, exact outcome reports, eligibility/store/progress/status, persisted-run redelivery |
| `weblate/trans/mt_operations.py`, `weblate/api/producer/mt.py`, `weblate/trans/tasks.py` | positive durable fallback, admission/status, existing outbox/recovery, no new queue |
| guides `producer-guide-weblate.md`, `run-isolation-and-offline-release.md`; `docs/changes.rst`; `docs/security/threat-model.rst:554–571,1280–1292` | user-facing route/provenance, stage recovery/codec, security-property/authorization update |

- [x] Сначала red behavioral tests у перечисленных ниже public seams; schema
  constraints/lock order/immutable mapping являются контрактом, не optional hardening.
- [x] Public config + frozen wire v4; затем additive stage schema и reservation
  lifecycle с deterministic conflict/replay tests. Старые fences/codec tests не ослаблять.
- [x] Internal-request membership/capture и occurrence adapter + usage exchange;
  затем scheduler terminal/drain/prefix и legacy orchestration. Не подключать
  fallback до готовности persistent evidence/reservation.
- [x] Positive durable `process_mt_operation`, admission, stage-aware
  reconciliation/retention и crash/redelivery/concurrency; затем полный
  aggregation/report/docs. Ни negative-only durable test, ни hidden skip не done.
- [ ] Основной агент проверяет весь K1 после изменений, выполняет final review,
  corporate reconciliation, commit/push/PR. Никакого production activation.

### TDD seams: setup и ожидаемое наблюдаемое поведение

1. **Config/public form/payload.** `weblate_customization/tests/test_machinery.py`:
   `RoutedLLMFormTest`, `RoutedSettingsValidationTest`, `RoutedLiteLLMTest`,
   `RoutedDownloadTest`, `RoutedDeliveryTest`; `weblate/machinery/tests.py`:
   `MachineryViewTest`, `ProjectMachinerySettingsTest`; `weblate/api/tests.py`:
   `ProjectAPITest` machinery cases (`6165–6462`). Реальный web/API round-trip
   POST/PATCH/PUT/GET, inherited/off/invalid metadata, hidden OpenRouter key,
   previous project overrides initial, disabled LiteLLM, соседний project не
   меняется. Metadata-only save без successful primary; key/URL/routing checks
   остаются. Qwen sync/async top-level none/schema/no provider; другие routes
   без none. Frozen Qwen v3/Gemini v2/v3 остаются точными; новая v4 replay
   fresh engine без второго HTTP/usage; changed scope/model/style/source fenced.
2. **Financial public seams.** `test_llm_delivery.py::LLMDeliveryTest` и proposed
   `weblate/trans/tests/test_mt_fallback.py`: реальные DB constraints и shared
   stage interfaces, no live providers. Captured eligible receipt → one
   authorization/stage; canonical key неизменен, primary FK неизменен, tail
   claim указывает на реальный fallback. Exact replay = тот же exchange;
   changed order/scope/digest/codec/policy/new operation не дают второй charge.
   Missing/corrupt receipt/unknown/invalid/partial не открывают fallback.
   Snapshot-before-send crash оставляет frozen wire/mapping в reserved/sending
   row и допускает exact local recovery без текущей configuration. Private
   retention очищает все текстовые копии, не открывая financial slots.
3. **Public scheduler/fetch/full legacy consumer.**
   `test_autotranslate.py::MachineryBatchFetchTest`, `RecordingTranslation`,
   `AutoTranslationMtTest`, `ProducerRunCreationTest`,
   `ProducerRunRefusalAggregationTest`. Controlled Events/Barrier, не sleeps:
   primary success → zero fallback; prefix + credit + поздний in-flight success
   → только exact unresolved credit/never-sent slots после drain. Generic перед
   credit не маскирует stop и не переоплачивается. Rate-limit skipped,
   timeout/auth/quota/permission/partial не становятся eligible. Compound batch
   с ранним partial и поздним credit сохраняет partial remainder заблокированным;
   successful internal prefix сохраняется. Real occurrence parser: equal-text
   plural slots и equal text/different contexts получают разные slot ответы,
   unit не разделён на два fallback HTTP. Multi-engine/overwrite/pre-judge и
   no-run direct caller не получают новый платный маршрут.
   Mixed-stage plurals: primary заполнил один slot, credit отказал другому —
   никакого intermediate partial target, один финальный combined apply;
   human write между ними запрещает apply. Проверить actual Celery meta при
   interleaving scheduler/store, fallback success/failure и guard-skips:
   progress монотонен, coverage denominator стабилен, done/total — mutations.
4. **Persisted legacy Celery path.** Extend
   `PersistedProducerRunRecoveryTest`/реальный `auto_translate` task entry, не
   только helper mocks. Crash после primary sending до outcome → unknown/no
   fallback. Crash после fallback reservation, sending, capture, parse, до/после
   apply; redelivery использует тот же run/members/exchange, never second send
   для sending/unknown. Captured prefix локально восстанавливается. Target edit,
   source revision, locks, permission revoke/cancel между send/apply защищены;
   проверить реальные targets/suggestions/Change origin, progress и ProducerRun.
5. **Positive public durable path.**
   `test_mt_operations.py::ProducerMTTest`: существующий acceptance fixture,
   настоящий routed engine с HTTP mocks, **`process_mt_operation(run_id)`**.
   Captured primary credit + prefix + never-sent tail → ровно один authorized
   Qwen request для допустимых IDs; две stage receipts и отдельный usage-once
   при одном run. Primary success не вызывает fallback. Duplicate task/new key,
   repeated reservation/changed source/config, unknown/invalid/partial,
   fallback timeout, cancel/logout/revoke не дают ещё charge/неправильный apply.
   `test_mt_reconciliation.py`: captured replies recovery без send, в том числе
   policy disabled; pending unit с fallback reservation не released как unpaid.
6. **Cross-worker/cross-path integration.** DB-backed `TransactionTestCase`
   с thread-local connections cleanup: concurrent legacy enrollment vs durable
   `reserve_exchange` и two fallback workers — только один claim/dispatch.
   Existing durable unknown блокирует legacy enrollment; legacy unknown блокирует
   новый durable primary. Crash между authorization/member/exchange commit и
   send-state/receipt/apply не создаёт другой slot. Проверить actual fallback
   exchange ID в usage, а не только суммарный row count. Retention/body pruning
   не удаляют spend/stage fences. Account deletion сохраняет witnesses.

## Проверки, approval gates и откат

**Эти команды не запускались при планировании.** Все external provider responses
в tests — mocks. Основной агент выполняет из абсолютного worktree после host
prerequisites AGENTS.md: isolated PostgreSQL, test settings, collectstatic,
установленный customization package. Не переподнимать shared dev stack.

```sh
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest \
  weblate_customization/tests/test_machinery.py \
  weblate/trans/tests/test_autotranslate.py \
  weblate/trans/tests/test_mt_operations.py \
  weblate/trans/tests/test_mt_reconciliation.py \
  weblate/trans/tests/test_llm_delivery.py \
  weblate/trans/tests/test_mt_fallback.py
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run pytest \
  weblate/machinery/tests.py weblate/api/tests.py
DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check --dry-run
uv run prek run --all-files
```

Также: isolated migration-from-current-head/replay fixture, local UI/API smoke
с mocked primary 402 и mocked fallback, real local persistence exact remainder,
truthful report/cost provenance. Никаких paid verification calls. Приёмка всего
K1 требует и legacy, и положительный public durable сценарий.

Gates не смешивать:

1. **Design/implementation + standing authorization D1: одобрены.** Нового
   блокирующего owner decision нет; schema/interfaces выше входят в реализацию.
   Итоговый независимый architect review: **approve**, blocking issues none.
   Включены cross-journal arbitration, immutable internal-request evidence,
   actual-stage usage, occurrence-safe plurals, private partial-vector
   persistence, единый task progress и snapshot-before-send. Review одобряет
   план, не непроверенную реализацию и не deployment.
2. **Integration:** основной агент повторяет corporate fetch/reconciliation,
   проверяет миграции/регрессии, проходит code-owner review. Недоступный corporate
   origin — внешний integration blocker; GitHub не подменяет source of truth.
3. **Production deploy/config:** требуется отдельное явное разрешение,
   актуальные runtime/Compose chain/backup; fallback default off. Не менять
   primary routing и не записывать production secrets в план/логи.
4. **Активация/платная canary/сохранение production targets:** отдельное разрешение
   на точный budget/scope. Исторические probes не разрешают новых вызовов.

Выключение config запрещает **новые** fallback reservations/authorizations,
но оставляет локальное чтение принятых receipts/answers и финансовые fences.
Provably unsent reservation при disabled policy не отправляется; sending/unknown
не переоплачивается. Уже сохранённые targets не откатываются.
Старый образ не понимает v4/stage journal; rollback требует остановить новых
writers, не запускать старые workers поверх незавершённых stage intents и
сохранить additive schema/private receipts. Не обещать seamless downgrade и
не удалять journal ради возврата старого образа.

## Ход реализации 2026-11-16

Возобновление с существующими незакоммиченными изменениями на базе `778f287b`.
Исполнители используют `bifrost-litellm/stepfun/step-5-preview`; shared financial,
legacy и durable boundary закреплены за одним владельцем, public settings и
frozen codec — за отдельным владельцем с непересекающимся набором файлов.

Изолированная проверка: Python 3.13, native PostgreSQL 16 на локальном порту
5432, отдельная тестовая БД, editable customization package, `collectstatic`.
Общий dev-docker стек не пересоздавался. Исходный прогон пяти regression files:
**25 failed, 326 passed, 74 subtests passed**. Это исходное свидетельство
незавершённой реализации, не итоговая приёмка K1. После изменения имён
неопубликованных миграций тестовая БД пересоздаётся, а не подгоняется через
`--fake`.

Corporate `git fetch origin` не прошёл через настроенный SOCKS-прокси;
прямой запрос также завершился timeout. GitHub `main` и cached `origin/main`
совпадали с `778f287b`, но cached ref не доказывает актуальность corporate.
Публикация и интеграционная приёмка требуют повторной живой сверки.
Production deployment, activation и платные проверки не выполнялись.

Первый handoff не принят: isolated WSGI/browser smoke обычного legacy API
записал два резервных target без единого `ProducerLLMExchange`, а запуск оставил
`failed` при полном результате. Положительный durable HTTP-сценарий
`POST 202 → GET 200/completed` применил две строки и сохранил fallback exchanges,
но primary snapshot и stage provenance в summary отсутствовали.
Независимый review подтвердил пробелы capture, occurrence pairing, guards,
arbitration и coverage. Эти пункты остаются частью K1, переданы на исправление;
helper-level green suite не считается приёмкой. Платных запросов не было.

### Проверенная реализация

По просьбе владельца оставшиеся Step-процессы остановлены; дальнейшие
исполнители и независимый reviewer использовали `openai-codex/gpt-6.1-terra`.
Неполные handoffs не принимались. Итоговую интеграцию и исправления выполнял
основной агент.

- Основной полный набор: **463 passed, 1 skipped, 89 subtests passed**.
  Помимо шести файлов команды выше включены
  `weblate/trans/tests/test_mt_fallback_replay.py` и
  `weblate/trans/tests/test_mt_fallback_boundaries.py`.
- `uv run prek run --files <изменённые файлы>`: все применимые hooks прошли,
  включая Ruff, format, REUSE, doccmd, Sphinx Lint и Markdown checks.
- `makemigrations --check --dry-run`: **No changes detected**. Окружение
  предупреждает об отсутствующем `git svn`; это не ошибка миграций.
- Отдельная migration-from-0156 проверка сохранила исторический unknown exchange,
  codec, digest и canonical work; ретроактивных fallback authorizations нет.
- Реальный изолированный WSGI/browser smoke, все provider responses подменены:
  legacy `POST 200`, два target сохранены, run `completed`; public durable
  `POST 202 → GET 200/completed`, два target применены, `fallback` summary
  содержит `requested=usable=applied=2`.
- В smoke БД оба запуска имеют исходные primary receipts и отдельные fallback
  exchanges; четыре fallback usage rows привязаны к двум исходным run.
  Это синтетический usage, не реальные расходы. Report page показывает
  `Completed`, две записанные строки и провайдера/модель LiteLLM/QWEN.
- Пять blocking findings независимого Terra-review исправлены: разные primary
  plural slots больше не конфликтуют; перед fallback transport проверяется
  delegation; сохраняется выбранный suggest mode; audit revision вычисляется
  из настоящего Unit; unused enrollment text также истекает после retention.
  Регрессии проверяют отдельные plural receipts и replay, отзыв delegation,
  изменение audit при пустом target и очистку enrollment без exchange.

Corporate reconciliation остаётся отдельным integration gate: повторный fetch
снова завершился `Can't complete SOCKS5 connection ... (5)`. Проверены также
прямое соединение и VPN gateway; cached `origin/main` не считается живой
сверкой. Последний checkbox не закрывается до публикации и сверки corporate.
Production deploy/config/activation и платных переводных probes не было.

Полный public machinery/API прогон: **2188 passed, 53 skipped,
131 subtests passed**; два subtests одного repair-refusal теста обнаружили
ошибку fixture: после разделения judge и repair endpoints ответ 403 был
зарегистрирован на judge URL вместо OpenRouter repair URL. Исправлен только
URL этого mock; отдельный повтор исходного теста: **1 passed, 2 subtests passed**.
Весь большой набор после этого локального изменения fixture повторно не запускался.
Также обновлены offline judge fixtures: поддерживаемый LiteLLM endpoint,
mock alias discovery и настоящие profile/request identity поля candidate evidence.
Runtime judge и его verification guards ради тестов не ослаблялись.
