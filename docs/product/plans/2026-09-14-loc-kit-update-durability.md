<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Закрытие durability-пробелов полного loc-kit обновления

Дата: 2026-09-14.
Статус: реализовано на ветке рабочего каталога; deploy и применение
миграций на живом инстансе не выполнялись и требуют отдельного
согласования. Все четыре задачи (durable dispatch ledger, атомарная
порция, repository-locked finalizer с no-diff recovery, admission/docs)
реализованы и покрыты регрессионными тестами; строго failing-first (red
перед implementation) подтверждён явно только для Task 4 (несоответствие
лимитов загрузки: тест сначала проверенно упал на текущем коде, затем
форма исправлена и тест стал зелёным) и для отдельных находок,
обнаруженных при обзоре (drain `skip_locked` без `.filter().first()`,
PO-Revision-Date-детерминизм для no-diff replay); остальные тесты Tasks
1-3 писались вместе с реализацией и подтверждают поведение постфактум, не
строгим red-green циклом.

Точный результат обязательной команды верификации (`CI_DB_PORT=5434 uv run
pytest --create-db weblate/trans/tests/test_loc_kit_ingest_contract.py
weblate/trans/tests/test_loc_kit_drafts.py weblate/trans/tests/test_tasks.py
weblate/formats/tests/test_formats.py`), четыре независимых
последовательных прогона на чистой БД: `1 failed, 794 passed, 58 skipped,
60 subtests passed` в каждом из четырёх прогонов - не 0 сбоев. Сбой каждый
раз один и тот же по сигнатуре
(`weblate.vcs.base.RepositoryCommandError: fatal: Invalid revision range
..<sha>`) и каждый раз в `LocKitGlossaryUploadUITest` (glossary upload UI,
код не затронут этим документом), но каждый раз на РАЗНОМ конкретном тесте
класса; тот же тест зелёный при изолированном перезапуске. Это
воспроизводимо похоже на предсуществующую нестабильность общего VCS test
fixture между тестовыми классами этого файла, а не на регрессию этой
работы, но обязательная команда как есть не даёт чистого нулевого results:
сбой зафиксирован как есть, не подавлен и не объявлен решённым. Каждый тест
в объёме задач 1-4 (включая новые `LocKitAtomicPortionConcurrencyTest`,
`LocKitFinalizerRecoveryTest`, `LocKitDispatchDrainConcurrencyTest`)
проходит стабильно во всех четырёх прогонах.

Новая миграция `trans.0127_loc_kit_dispatch_ledger` отдельно проверена
`manage.py migrate --noinput` на изолированной чистой БД
(`weblate_migration_check`, вне test-раннера) - применяется без ошибок.

`uv run prek run --files <изменённые файлы>`: `ruff-check` заканчивается 20
находками в изменённых файлах (`weblate/trans/loc_kit.py`,
`weblate/trans/models/translation.py`,
`weblate/trans/tests/test_loc_kit_ingest_contract.py`,
`weblate/trans/views/create.py`) - не 0. Каждая из 20 сверена построчно с
diff hunks этой работы и подтверждена вне изменённого диапазона (пример:
`too-many-arguments` на `_add_unit_locked`, не тронутый этой работой;
`literal-membership` на `draft.state in (...)`, четыре идентичных
вхождения в файле, из которых только одно дословно, без изменения логики,
оказалось внутри переписанного `_retry` при реструктуризации - оставлено
как есть, чтобы не расходиться стилем с тремя нетронутыми соседними
вхождениями того же паттерна в том же файле). Находки, изначально введённые
этой работой, устранены до финальной проверки: сложность 19>16 на
`_append_translation_strings_locked` - извлечением
`_tag_cascade_pending_changes`; `private-member-access` на прямой доступ
теста к `_apply_kit_explanations_locked` - `# ruff:
ignore[private-member-access]`; 13 `missing-blank-line-after-summary` на
docstring новых тестовых методов/классов (`LocKitAtomicPortionConcurrencyTest`,
`LocKitFinalizerRecoveryTest`, новые методы в существующих классах) и на
docstring `_tag_cascade_pending_changes` - переписаны в формат
one-line-summary + blank line + description; после этого повторный
функциональный прогон (18 тестов, затронутых переформулировкой) зелёный.
`rumdl`/`ruff-format` зелёные для изменённых файлов. `reuse lint` и
`typos` сообщают о файлах, не тронутых этой работой (`.omp/lsp.json`,
`DESIGN.md`, `analysis/data/anvil-saga-fr-lqa-golden-2026-09-11.json`) -
записано, не подавлено. `mypy` не вносит новых находок ни в один из семи
изменённых модулей (все существующие находки лежат вне изменённых
диапазонов diff, подтверждено построчной сверкой).

## Цель и основания

Закрыть оставшиеся P1-находки технического review после merge полного
loc-kit-обновления существующего строкового компонента. После работы одна
подтверждённая таблица остаётся безопасной при потерянном `on_commit` callback,
дублированной доставке Celery, замене fencing UUID, исключении в Explanation,
сбое между VCS commit и удалением pending rows, и конкурентном repository commit.
Новые строки сохраняют input `extra_flags` также в backing-файле. Upload
отклоняется на том же byte limit, который применяет background parser.

Основания:

- действующий пользовательский контракт:
  `docs/product/guides/loc-kit-ingest.md`, раздел
  ``loc-kit-strings-update``;
- исходный реализованный план:
  `docs/product/plans/2026-09-11-loc-kit-full-table-update.md`;
- review findings, зафиксированные при merge: раздельные commit порции и
  Explanation оставляют owned ``PendingUnitChange`` при исключении; token
  проверяется до mutation boundary; continuation зависит от недолговечного
  callback; finalizer не держит repository lock и не восстанавливает commit,
  произошедший до DB rollback; pending writer не содержит `extra_flags`;
  форма и worker используют разные upload limits; threat-model неверно
  исключает VCS push;
- наблюдаемые текущие границы:
  `weblate/trans/tasks.py:_chain_loc_kit_apply_continuation`,
  `_flush_loc_kit_pending_changes`, `apply_loc_kit_string_update_draft`;
  `weblate/trans/loc_kit.py:append_translation_strings`,
  `apply_kit_explanations`, `apply_loc_kit_string_update`;
  `weblate/trans/models/component.py:Component.locked_for_update`,
  `commit_pending_subset`; и
  `weblate/trans/models/translation.py:find_or_add_pending_store_unit`.

## Объём и не-цели

Входит:

- durable dispatch только для `LocKitImportDraft.Kind.STRING` prepare/apply
  generations, включая self-chain continuation;
- один atomic mutation boundary на row portion, в котором fencing UUID,
  cursor, counters, units, ownership metadata и Explanation согласованы;
- idempotent subset finalization под repository lock, в том числе crash после
  успешного VCS commit;
- persistence `extra_flags` новых loc-kit units, согласование upload limit и
  точность threat-model/руководств.

Не входит:

- изменение glossary append flow, штатных component upload flows, source/target
  overwrite существующих ключей, reverse sync или новый общий Celery framework;
- новый общий outbox для всего продукта: у этого потока один owner (`draft`),
  один возможный active generation и один destination task, поэтому draft сам
  является узким durable outbox record;
- изменение очередей, priority, broker visibility timeout, worker shutdown
  ordering, VCS provider configuration либо push policy;
- production deployment, migration application или paid/external LLM work.

## Выбранный подход

### Пер-драфтовый dispatch ledger, не best-effort callback

Добавить на `LocKitImportDraft` одну durable запись текущей публикации:
предлагаемые поля `dispatch_task_id`, `dispatch_phase` (`prepare`/`apply`),
`dispatch_requested_at`, `dispatch_published_at`, `dispatch_attempts` и
sanitized `dispatch_error`. В той же DB transaction, которая резервирует
`prepare_task_id` или `apply_task_id`, сохранять dispatch intent; intent
обязан ссылаться ровно на task ID текущей фазы.

`transaction.on_commit` остаётся fast path, но вызывает общий dispatcher.
Новый периодический drain забирает intent с row lock, публикует
`apply_async(..., task_id=dispatch_task_id, priority=INTERACTIVE_TASK_PRIORITY)`
и только затем отмечает intent published. Crash после broker publish, но до DB
mark, может дать повторную доставку того же UUID; это допустимо только благодаря
atomic portion contract ниже. Crash до publish оставляет intent доступным drain.
После конечного числа persistently recorded broker failures generation переходит
в retryable `FAILED` с существующей `retry_phase`; payload, cursor и counters не
стираются. Никакой callback не публикует задачу минуя ledger.

### Atomic row portion и lock order

Ввести request-free private coordinator порции в `weblate/trans/loc_kit.py`.
Он берёт locks исключительно в порядке `repository -> Component row -> draft
row` через `Component.locked_for_update()`, затем
`LocKitImportDraft.objects.select_for_update()`. Он повторно проверяет
`state == APPLYING`, `apply_task_id`, expected `next_row`, актуальные права,
source language и component eligibility **до первой unit mutation**. В той же
outer transaction он выполняет add/Explanation, tag `PendingUnitChange`,
`store_update_changes`, cursor/counters/heartbeat update.

`append_translation_strings` и `apply_kit_explanations` разделить на public
wrappers и private ``*_locked`` primitives, чтобы coordinator не берёт тот же
repository lock дважды. Независимость прав сохраняется: нет `unit.add` —
Explanation всё ещё применяется; нет `source.edit` — новые строки всё ещё
добавляются. Но operational exception любой оси делает rollback всей порции:
не остаётся новых unit, owned pending rows или cursor advance без своего парного
outcome. Это заменяет прежний «append уже committed, Explanation упал» replay.

Перед `store_update_changes` snapshot input `extra_flags` в metadata именно
новых owned pending rows. Runtime pending writer применяет этот snapshot к
freshly added store unit перед file serialization. Existing-key flags остаются
неизменными; metadata без snapshot сохраняет нынешнюю семантику всех иных
pending writers.

### Recovery-aware finalization под тем же lock order

Finalization начинает с `Component.locked_for_update()`, затем locks draft,
проверяет token/current permission и формирует owned set только по
`metadata["loc_kit_draft_id"]`. Он никогда не вызывает `commit_pending_subset`
без repository lock. После каждой flush attempt он перечитывает owned rows, а
не считает старый `pending_change_ids` source of truth.

Расширить `Component.commit_pending_subset` так, чтобы после успешного
`update_units` отсутствие VCS diff означало ``already applied``: удалить только
проверенные pending rows и вернуть success, не публикуя второй commit/push.
Это закрывает crash window: если VCS commit успел, но DB transaction не успела
удалить rows, retry видит identical on-disk content, не создаёт пустой/второй
commit и завершает deletion. Неуспешные update statuses, foreign IDs и реальная
VCS error остаются failure: pending rows сохраняются и draft остаётся
retryable `FAILED`. Успешная новая flush продолжает существующий commit/push
путь ровно один раз.

## Карта файлов

| Файл | Ответственность в этом инкременте |
| --- | --- |
| `weblate/trans/models/loc_kit.py` | Proposed dispatch-ledger fields/choices и model-level lifecycle invariants. |
| `weblate/trans/migrations/` | Migration после текущего head для dispatch ledger; не создавать параллельную ветвь миграций. |
| `weblate/trans/views/create.py` | Резервировать dispatch intent вместе с prepare/apply UUID; retry/confirm используют один dispatcher. |
| `weblate/trans/tasks.py` | Durable dispatcher/drain, continuation, atomic-portion orchestration и repository-locked finalizer. |
| `weblate/trans/loc_kit.py` | Private locked primitives, transactional portion coordinator, durable pending flag snapshot. |
| `weblate/trans/models/component.py` | No-diff successful recovery contract в `commit_pending_subset`. |
| `weblate/trans/models/translation.py` | Применить optional flag snapshot нового pending unit при runtime-store write. |
| `weblate/trans/forms.py` | `LocKitStringsUpdateForm` использует `validate_translation_upload_size`. |
| `weblate/trans/tests/test_loc_kit_ingest_contract.py` | Delivery/fence/portion/finalization/UI-limit regressions. |
| `weblate/trans/tests/test_loc_kit_drafts.py` | Dispatch-ledger expiry/cleanup tests, если новые fields требуют lifecycle coverage. |
| `weblate/trans/tests/test_tasks.py` | Periodic dispatcher task and repository-finalize error/recovery tests, если сложившиеся test helpers подходят. |
| `weblate/formats/tests/test_formats.py` | Reparse proof для stored `extra_flags`, если format-level behavior затронут. |
| `docs/product/guides/loc-kit-ingest.md` | Уточнить atomic retry/finalization guarantee без ложного обещания exactly-once delivery. |
| `docs/security/threat-model.rst` | Исправить outbound/VCS boundary. |
| `docs/product/plans/2026-09-11-loc-kit-full-table-update.md` | Отметить реализованное merge и ссылку на этот follow-up вместо устаревшего статуса ветки. |

## Задачи

### 1. Сделать task reservation durable до broker publish

**Outcome:** prepare, confirm, retry и self-chain continuation не могут оставить
`PREPARING`/`APPLYING` без устойчиво обнаружимого dispatch intent. Повторное
сообщение с тем же UUID допустимо и не создаёт новую generation. Existing
component tasks не используют новый ledger.

**Files and interfaces:** `LocKitImportDraft`, its migration,
`_dispatch_loc_kit_task`, `_chain_loc_kit_apply_continuation`,
`apply_loc_kit_string_update_draft`, `setup_periodic_tasks` и preview context/
template только для правдивой queued/failure state. Proposed shared contract —
dispatch ledger выше; no caller publishes prepare/apply directly outside the
common dispatcher.

**Actions:**

- [ ] Сначала добавить failing HTTP/task tests для commit-without-callback,
  callback broker failure, drain recovery, and duplicate same-UUID delivery.
- [ ] Добавить ledger fields и generated migration after the migration head
  visible at implementation time; use a model choice rather than unvalidated
  JSON phase values.
- [ ] Вынести claim/publish/mark-published в one task-local dispatcher;
  `on_commit` calls it immediately and a short periodic drain reclaims every
  unpublished current intent with `select_for_update(skip_locked=True)`.
- [ ] Перевести view start/confirm/retry и continuation на reserve-intent then
  common dispatcher. Match intent UUID to the current phase UUID before every
  publish/failed transition; preserve explicit interactive priority.
- [ ] Record bounded dispatch attempts/error without traceback or broker details
  exposed to the owner. Exhaustion enters existing retryable FAILED path; a
  user retry reserves a new UUID and intent, never mutates cursor/payload.

**Verification:** With `CELERY_TASK_ALWAYS_EAGER=False`, commit a confirm while
not executing callbacks; run the drain and assert one task publication with the
reserved UUID and no unit writes before worker execution. Simulate `apply_async`
failure until the cap and assert retryable `dispatch-failed`, unchanged cursor
and private payload. Simulate broker success followed by process loss before
`dispatch_published_at`; drain republishes the same UUID, and the two actual
workers are covered by task 2's single-portion result. Verify ordinary
prepare/apply confirmation still gets `INTERACTIVE_TASK_PRIORITY` and the
component discovery link remains owner/session bound.

### 2. Commit every row portion under its fencing token

**Depends:** task 1 defines how a generation is reserved and republished.

**Outcome:** a stale/replaced task cannot mutate units or roll back progress;
duplicate deliveries of one UUID commit at most one portion; an exception in
Explanation rolls back additions, owned pending rows and cursor together.
Permission-axis partial success and existing-key protection stay unchanged.

**Files and interfaces:** `weblate/trans/loc_kit.py` owns the new private
locked primitives and portion result. `weblate/trans/tasks.py` becomes a loop
that loads immutable packet data and delegates each durable write boundary.
`LocKitImportDraft.next_row`, `progress`, `pending_change_ids`, `apply_task_id`
and `PendingUnitChange.metadata["loc_kit_draft_id"]` are written together.
Before changing public helpers, run LSP references for
`append_translation_strings`, `apply_kit_explanations` and
`apply_loc_kit_string_update`; migrate every current caller deliberately.

**Actions:**

- [ ] Write failing `TransactionTestCase` regressions with independent DB
  connections: stale UUID is replaced while its worker is paused before the
  draft lock; two workers deliver the same UUID at one cursor; Explanation
  raises after additions would previously have committed.
- [ ] Introduce the private coordinator and ``*_locked`` helpers under one
  `repository -> Component -> draft` transaction. Recheck token, cursor,
  permissions and component eligibility in this transaction before writes.
- [ ] Move pending tagging/`store_update_changes`, aggregate counters,
  `next_row`, `pending_change_ids`, heartbeat and expiry update into the same
  transaction. A stale cursor or token returns without an externally visible
  mutation.
- [ ] Preserve baseline/source-drift and permission-axis classification inside
  the coordinator. Treat operational error as a full portion rollback, then
  use current `mark_loc_kit_draft_failed`/lock retry policy outside it.
- [ ] Snapshot new unit `extra_flags` into only the corresponding pending
  metadata and teach the runtime pending writer to apply it before the new unit
  reaches the backing store. Do not source a retry's flags from a possibly
  later user edit; do not change existing-key flags.

**Verification:** A two-thread `TransactionTestCase` proves old UUID creates
zero units after retry installs a new UUID, while a pair of same-UUID deliveries
leaves one key, one cursor advance and one counter increment. Inject an
Explanation error after the add path begins; before the fix the key and owned
pending row survive, after the fix both are absent and retry applies the whole
portion once. Apply a new `read-only`/other loc-kit flag, finalize, reparse the
PO and verify the persisted flag; verify an existing key with a conflicting
input flag remains unchanged. Re-run all existing source-drift, baseline,
missing-right and redelivery scenarios in `LocKitStringsUpdateServiceTest` and
`LocKitStringsUpdateViewTest`.

### 3. Finalize owned pending changes atomically with repository recovery

**Depends:** task 2 supplies one owned, all-or-nothing portion and exact
`pending_change_ids` durability.

**Outcome:** finalization serializes against every component writer, commits
only this draft's pending rows, and survives a crash after the VCS commit with
no second commit or terminal `finalize-failed`. Foreign pending rows stay
untouched; permission loss stays a retryable authorization failure.

**Files and interfaces:** `_flush_loc_kit_pending_changes` in `tasks.py`,
`Component.commit_pending_subset` in `models/component.py`, and its existing
callers in `reapply_autofixes.py` and loc-kit tests. Preserve the public bool
contract for current callers: a proven no-diff replay returns `True`; an
unapplied/error result remains `False`.

**Actions:**

- [ ] Add failing regression tests for: finalizer holds `Component.locked_for_update`;
  a foreign pending row is excluded; permission revoked after a portion causes
  no VCS commit; and a crash after VCS commit/before pending deletion resumes
  without another commit.
- [ ] Reorder finalizer to enter `Component.locked_for_update()` then draft row
  lock, validate token/state/owner authorization, and derive the owned set
  from metadata each attempt. Retain `pending_change_ids` as audit/recovery
  data, never ownership authority.
- [ ] In `commit_pending_subset`, distinguish a real update/write failure from
  the replay where `update_units` has applied every pending change but the
  repository has no diff. In the latter, clear those exact pending rows and
  caches as successful already-applied work without a new commit/push.
- [ ] After every finalization attempt re-query owned rows under the same
  fencing lock. Only empty owned set can transition `COMPLETED`; otherwise
  preserve retry phase and all recovery data.
- [ ] Keep the generic autofix caller behavior covered; do not make its
  foreign-pending guard or commit scope broader.

**Verification:** In a repository test fixture, force an exception after a
successful VCS commit and before DB pending deletion; rerun finalization and
assert the source/target files contain exactly one change, outgoing revision
count grows once, owned rows are deleted and draft completes. Force a genuine
`update_units`/commit failure and assert rows survive with `retry_phase="finalize"`.
Run a simultaneous manual pending edit plus two loc-kit drafts on the component:
each finalizer commits only its own metadata-tagged IDs. Verify permission
revocation after row mutation stops finalization before `commit_pending_subset`.

### 4. Align admission and truth-bearing documentation

**Depends:** tasks 1–3 settle the observed recovery behavior and VCS boundary.

**Outcome:** an upload cannot pass HTTP validation then fail asynchronously only
because two size settings disagree. Operator and threat-model text accurately
distinguish direct provider traffic from existing VCS push behavior.

**Files and interfaces:** `LocKitStringsUpdateForm`,
`validate_translation_upload_size`, prepare task's existing
`TRANSLATION_UPLOAD_MAX_SIZE` checks, loc-kit guides, threat model and parent
plan status. No template or API route changes beyond any task-1 truthful queued
state.

**Actions:**

- [ ] Write the failing form/view regression with
  `TRANSLATION_UPLOAD_MAX_SIZE` smaller than `COMPONENT_ZIP_UPLOAD_MAX_SIZE`;
  a table over the former is rejected before `LocKitImportDraft` or task
  reservation exists.
- [ ] Replace only `LocKitStringsUpdateForm`'s mismatched ZIP validator with
  the existing translation upload validator. Do not alter glossary or ZIP
  component flows.
- [ ] Update the loc-kit guide with bounded at-least-once dispatch, atomic
  portions and idempotent VCS-finalization recovery; retain the established
  no-overwrite/delete guarantees.
- [ ] Correct `docs/security/threat-model.rst`: the importer makes no direct
  provider/LLM request, but its finalization can use the pre-existing configured
  VCS commit/push path, so table-derived translation content can be outbound to
  that configured repository. Preserve the fixed-host glossary-analysis claim.
- [ ] Replace the parent plan's stale “awaiting final verification, commit and
  push on feature branch” status with its merge result and a link to this
  follow-up. Record actual verification evidence and remaining UI-tool limits
  only after implementation, not as assumed results here.

**Verification:** POST an oversized CSV under conflicting settings and assert
form error, no draft row, no storage object and no `apply_async`. Validate
threat-model RST with the repository documentation check used for this area.
Review the rendered loc-kit preview status manually on the available dev
surface; retain the known Lightpanda mobile/Tab limitation as blocked rather
than calling a non-exercised visual scenario verified.

## Integrated verification and handoff

Run tests sequentially; this repository shares its test DB/VCS fixture state,
so do not overlap pytest sessions:

```sh
source scripts/test-database.sh && CI_DB_PORT=5434 uv run pytest --create-db \
  weblate/trans/tests/test_loc_kit_ingest_contract.py \
  weblate/trans/tests/test_loc_kit_drafts.py \
  weblate/trans/tests/test_tasks.py \
  weblate/formats/tests/test_formats.py
```

Before the changed public symbols or format-pending interfaces are edited, use
LSP references; run the relevant existing callsites after the clean cutover.
After targeted tests, run the repository command from `AGENTS.md`:

```sh
uv run prek run --all-files
```

If unrelated existing hooks fail, record their exact output and ensure the
changed files pass their configured hooks; do not suppress unrelated failures.
Run the configured mypy command from `AGENTS.md` and ensure changed modules do
not add findings. Generate a migration against the then-current migration head;
verify it applies under the isolated test DB. Do not restart the shared dev
stack, run a production mutation, deploy, or apply migrations outside the test
suite without separate authorization.

The tasks are intentionally ordered 1 → 2 → 3 → 4: the dispatcher contract
settles UUID ownership, the portion establishes its mutation boundary, and
finalization consumes the resulting durable owned set. `tasks.py`,
`loc_kit.py`, `models/component.py`, and the main contract test file are shared
mutation boundaries and require one integration owner. The size-validator
regression can be prepared independently, but it must land with task 4 after
the final behavior and docs wording are known.
