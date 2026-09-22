<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Переименование ключа и изменение порядка строк

**Дата:** 2026-09-22
**Статус:** дизайн согласован; план готов к отдельному утверждению реализации. Код, миграция, изменения данных и деплой этим документом не разрешаются.

## Цель и основания

После первоначального импорта Weblate является источником истины для состава и
порядка строк. Project admin получает две точечные операции над уже существующей
source-строкой:

1. **Rename key** — изменить внешний ключ, сохранив внутреннюю identity строки,
   переводы и связанные данные.
2. **Move string** — поставить строку перед или после выбранного ключа и записать
   этот порядок в source/template и файлы переводов.

Согласованный продуктовый бриф получен в разговоре 2026-09-22. Исследовательское
основание и внешние паттерны находятся в
`docs/product/research/2026-09-21-string-identity-order-and-authoritative-sync.md`.
Правила репозитория, проверки и отдельный гейт production deployment задаёт
`AGENTS.md`.

### Проверенные факты, не будущие результаты

Read-only проверка production 2026-09-22 показала:

- `space-arena/lockit` — `JSONFormat`, template `en.json`, source language `en`;
- `has_template=True`, `manage_units=True`, `edit_template=True`, компонент не
  glossary и не locked;
- 15 translations, 4 876 source units, у всех priority `100`;
- `file_format_params == {"json_indent": 2}`: принудительная алфавитная
  `json_sort_keys` не включена;
- `%BETA_LABEL%` имеет position `4864`,
  `%SQUADRONS_SHIP_SELECTION_POPUP_VIP_EXPIRED%` — `4875`;
- существующая сортировка `timestamp` уже отображается как :guilabel:`String
  age` / «Возраст строки» (`weblate/utils/views.py:SORT_CHOICES`), поэтому
  новую сортировку создавать не нужно.

Read-only production probe загрузил и сериализовал все 15 JSON stores
(73 140 units, 6 957 937 bytes) за 0,775 с без записи. Это подтверждает
приемлемость parse/render части synchronous design; полный local
write/DB/commit smoke остаётся обязательной проверкой задачи 2. Production
reverse proxies дают HTTP read/send timeout 300 с
(`deploy/Caddyfile`, `deploy/nginx-l10n.conf`).

Текущие интерфейсы, на которых строится write path, проверены в коде:

- `Component.locked_for_update()` держит repository lock и Component row в
  порядке repository → transaction/row;
- `Component.commit_pending(..., skip_push=...)` и
  `Component.commit_files(files=..., signals=..., skip_push=...)` существуют;
- `TranslationFormat.save_atomic()` атомарно заменяет один файл;
- `Translation._validate_new_unit_context()` применяет format validation;
- `ActionEvents` сейчас заканчивается значением 107, migration head —
  `0137_producer_run_full_scope.py`;
- `translate.html` содержит `unit_tools_dropdown`, а
  `embed-units.html` уже поддерживает optional `actions_template`;
- роль `Administration` включает permission codename `component.edit`.

Для template-driven JSON `TranslationFormat._get_all_monolingual_units()`
итерирует template units, а `Translation.update_units_from_store()` присваивает
`pos + 1`. Поэтому **template order владеет `Unit.position` во всех
translations**, даже когда пустой target key физически отсутствует в своём
файле.

Текущий код не предоставляет Rename или Move. `Unit.context` и `Unit.id_hash`
участвуют в идентичности, а unique constraint равен `(translation, id_hash)`
(`weblate/trans/models/unit.py:Unit`). Для template-driven форматов hash ключа
считается из context (`weblate/formats/base.py:TranslationUnit.calculate_id_hash`).
DB-only изменение `context` или `position` неверно: следующий parse вернёт
identity/order из файла.

## Объём

Входит:

- одиночный Rename из меню :guilabel:`Tools` / «Сервис» на странице фактической
  source-строки;
- одиночный Move из Browse-списка source-строк;
- preview и повторная проверка перед apply;
- project-admin permission, component/repository lock и один структурный commit;
- сохранение Unit PK и всех FK/M2M-связей;
- отдельные audited history events для Rename и Move;
- обязательная поддержка плоского `JSONFormat`, доказанная на
  production-подобном компоненте Space Arena;
- capability-gated интерфейс формата: другие форматы получают UI только после
  собственной parse-back проверенной реализации.

Не входит:

- authoritative sync или повторное применение Google Sheets/loc-kit;
- `previous_key`, bulk Rename/Move, REST API или management command;
- drag-and-drop, ввод числовой позиции, отдельная административная страница;
- glossary, bilingual, nested JSON/I18Next/RESJSON и другие не доказанные
  форматы;
- изменение ключевых ссылок в исходном коде игры;
- dedicated Undo; обратные Rename/Move выполняются теми же действиями;
- изменение default sorting или существующей сортировки «Возраст строки»;
- переписывание translation-memory records: они text-level и не принадлежат
  Unit по FK; source/targets и Unit identity сохраняются;
- автоматические уведомления переводчикам;
- production data mutation и deployment.

## Продуктовый контракт

### Доступность

Операция доступна, только если одновременно:

- `unit.is_source`;
- `component.has_template()`;
- `component.manage_units` и `component.edit_template` включены;
- компонент не glossary и не locked;
- формат объявляет соответствующую capability для текущих
  `file_format_params`;
- actor имеет `component.edit` на фактическом компоненте unit. Это соответствует
  согласованному project-admin уровню: роль `Administration` включает это право,
  а одного `source.edit` недостаточно.

Проверка выполняется и при render, и заново на preview/apply. В агрегированном
URL вроде `/translate/space-arena/-/en/` она относится к конкретному Unit и его
Component, а не к коду языка страницы.

`read-only` не запрещает структурную операцию и не снимается ею. Component lock
запрещает обе операции.

### Rename key

Вход: source Unit PK и `new_context`. Preview возвращает old/new key, число
затронутых translations/files и предупреждение, что код игры не изменяется.
Apply:

- отклоняет blank/control characters, форматно невалидный key и collision;
- Rename в тот же key — no-op: preview сообщает, что изменений нет, apply,
  commit и history entry недоступны;
- не объединяет и не удаляет строки;
- сохраняет PK source и target Units, `source_unit_id`, source/targets, states,
  priority, position, timestamp/last_updated, labels, flags/read-only,
  Explanation, comments, suggestions, screenshots, checks, judge rows и history;
- меняет context и пересчитанный id_hash во всех translations;
- меняет ключ в template и в каждом target-файле, где unit физически существует;
  отсутствие пустого target key в конкретном файле не создаёт новую строку;
- сохраняет место ключа в порядке файла;
- создаёт одну source history entry `Key renamed: OLD → NEW` и один VCS commit,
  но не 15 target events.

Collision проверяется по новому hash во всех translations после получения lock.
Существующая строка возвращается как ссылка в ошибке; merge отсутствует.

### Move string

Вход: source Unit PK, anchor source Unit PK и placement `before|after`. Anchor
должен принадлежать тому же component, отличаться от moving unit и оставаться
source Unit.

Preview возвращает old/new position и соседей до/после. Apply:

- меняет только position/order, не priority и не content;
- строит canonical source context order без gaps/duplicates;
- записывает его в template;
- приводит физически существующие ключи каждого target JSON к тому же
  относительному порядку; отсутствующие target keys не создаются;
- оставляет неизвестные/format-owned raw units на их прежних slots и меняет
  только относительный порядок известных component keys;
- присваивает всем sibling Units canonical `Unit.position` из template order;
  unit, физически отсутствующий в target JSON, всё равно получает эту позицию,
  как и при штатном monolingual parse;
- no-op, если unit уже находится в запрошенном месте: apply отключён, commit и
  history event не создаются;
- создаёт одну source history entry
  `String moved: OLD_POSITION → before|after ANCHOR (NEW_POSITION)`.

После успеха сервер возвращает URL фактической source translation с
`sort_by=position`, который текущий `SORT_CHOICES` подписывает «Position»,
нужной browse page и highlight/anchor перемещённого Unit. Priority сохраняется;
комбинированный `-priority,position` («Position and priority») для демонстрации
результата не используется.

### Согласованность и сбои

Обе операции используют один и тот же structural-write порядок:

1. Взять repository lock; внутри него открыть `transaction.atomic()`, получить
   свежий Component row и необходимые source/sibling rows через
   `select_for_update`. Для Move блокируется component Unit set, позиции
   которого меняются; Rename блокирует выбранную identity во всех translations
   и collision candidates.
2. Повторить capability/permission/stale/collision validation на свежих rows.
3. `component.commit_pending(..., skip_push=True)` до изменения identity/order.
   Если после flush остались любые pending changes компонента, структурная
   операция отказывается продолжать: будущий pending writer не должен искать
   старый key.
4. После flush рабочее дерево обязано быть clean; сохранить `previous_revision`.
   Неизвестный dirty state блокирует операцию и направляет в штатное repository
   recovery. Пока тот же repository lock удерживается, clean preflight
   гарантирует, что staged/worktree изменения после этого принадлежат только
   текущей structural operation.
5. Загрузить все stores и классифицировать **текущее clean-HEAD состояние** до
   мутации:
   - `BEFORE`: template содержит old key / исходный order, все **физически
     присутствующие** target keys согласованы с ним;
   - `RECONCILE`: clean HEAD целиком совпадает с **полным desired structure
     fingerprint** подтверждённого preview — для Rename это new key, исходный
     slot и прежний per-file presence bitmap; для Move это полный canonical
     ordered source-key list и относительный порядок всех присутствующих target
     keys;
   - `MIXED`: old/new одновременно, template/targets расходятся, совпадает
     только anchor relation, но не полный order fingerprint, либо состояние
     нельзя однозначно доказать.
   Отсутствующий в target-файле key согласован в `BEFORE`/`RECONCILE` и не
   создаётся классификатором. `MIXED` → 409/fail-closed.
   `RECONCILE` не утверждает, что текущий actor создал существующий file commit:
   его новый confirmed POST явно разрешает привести DB к уже существующему
   clean HEAD. Файловую фазу пропустить; audit detail фиксирует
   `reconciled_existing_file_state=true`, текущий revision и actor, который
   подтвердил reconciliation. Полный успех, где DB уже совпадает, — no-op.
   Порядок классификации обязателен: сначала сравнить clean HEAD с signed
   `desired` fingerprint; при совпадении DB desired → no-op, DB expected →
   `RECONCILE`, любая третья DB state → 409. Только если HEAD не desired,
   сравнить его с signed `expected`: совпадение → `BEFORE`, иначе `MIXED`.
   Поэтому повтор confirmed POST после успеха не отбрасывается как stale.
6. Только для `BEFORE`: target stores обрабатываются до template, пока old key
   остаётся lookup identity. Сформировать content каждого файла в памяти,
   parse-back проверить весь набор до первой записи и сохранить original bytes.
   Записать файлы через `save_atomic`, затем сделать **локальный** structural
   commit одним `Component.commit_files(..., signals=False, skip_push=True)` со
   всем deduplicated filename set.
7. Для `BEFORE` и `RECONCILE` DB rows и одна audit entry фиксируются в
   transaction. После DB commit отправить post-commit signal и
   `push_if_needed`; reconciliation использует уже существующий clean HEAD и
   не создаёт новый content commit.
8. Явная таблица исходов:
   - validation/render failure до записи → ни file, ни DB diff;
   - обработанное write/commit failure при неизменившемся HEAD → вызвать
     существующий `repository.reset_to_revision(previous_revision)` **только**
     при всё ещё удерживаемом lock и доказанном clean preflight. Это очищает и
     Git index, и working tree; затем DB rollback. Raw `git reset` из
     application code не добавляется;
   - локальный commit создан, но Unit/audit/cache update упал до DB commit →
     старые bytes поверх commit не восстанавливать; после rollback открыть
     новую DB transaction под тем же repository lock и немедленно выполнить
     `RECONCILE` по signed desired fingerprint. Если и reconciliation падает,
     вернуть recovery-required 409; clean HEAD остаётся desired, следующий
     confirmed POST повторяет только reconciliation;
   - локальный commit и DB commit завершены, а последующий signal/push упал →
     локальная операция считается применённой, UI показывает repository
     warning, штатный alert/retry владеет push;
   - process loss между несколькими file replacements и commit → следующий
     запуск видит dirty tree и fail-closed до штатного repository recovery;
     DB/history не утверждают успех;
   - process loss после локального commit, но до DB commit → повторный confirmed
     POST с валидным desired fingerprint получает `RECONCILE`, завершает
     DB/audit без второго content commit и честно помечает reconciliation;
   - повтор после полного успеха сначала совпадает с desired и становится
     no-op без duplicate event.
9. Не обещать multi-resource ACID. Контракт: handled pre-commit failures
   откатываются; post-commit DB failures немедленно converged отдельной
   transaction либо остаются честным recovery-required состоянием; uncommitted
   crash debris блокирует новые writes и никогда не маскируется как успех.

После успеха сбрасываются source/template/store caches, обновляются store hashes,
component/unit search caches и variants по новому id_hash. Текст не меняется,
поэтому translation states, last author и judge context freshness не
инвалидируются. Unit PK остаётся прежним: FK-зависимые comments, suggestions,
screenshots, judge verdicts/deferrals/applications и history не мигрируются и не
пересоздаются.

## Глубокие интерфейсы

Названия ниже — предлагаемый внешний seam; implementation helpers остаются
приватными.

### Format seam

`weblate/formats/base.py:TranslationFormat`:

    @classmethod
    def supports_key_rename(cls, params: FileFormatParams) -> bool: ...


    @classmethod
    def supports_key_order(cls, params: FileFormatParams) -> bool: ...


    def rename_key(self, old_context: str, new_context: str) -> bool: ...


    def apply_key_order(self, contexts: Sequence[str]) -> bool: ...

Default capabilities — `False`; mutation methods refuse unsupported calls.
`bool` indicates whether serialized content changed. UI never infers support
from `can_add_unit/can_delete_unit`: those capabilities недостаточны для
identity-preserving rename/order.

`JSONFormat` opts in only when `format_id == "json"`; inherited nested/i18next
formats remain out until separately proven. Обе capabilities возвращают false
при включённом `JSONOutputSortKeys`: Rename тогда сам изменил бы сортированную
позицию ключа, а Move serializer уничтожил бы сразу после записи.

JSON adapter updates translate-toolkit indexes around `setid`, invalidates
Weblate format indexes, preserves unit object/value and list slot on Rename, and
reorders existing raw units stably on Move. A local probe already demonstrated
that remove-from-index → `setid` → add-to-index and raw list reordering survive
JSON save/parse-back; permanent format tests own this invariant.

### Translation inventory seam

`weblate/trans/models/translation.py:Translation` remains the owner beside
existing `add_unit`/`delete_unit`:

    @dataclass(frozen=True)
    class ConfirmedRename:
        unit_id: int
        old_context: str
        new_context: str
        expected_fingerprint: str
        desired_fingerprint: str


    @dataclass(frozen=True)
    class ConfirmedMove:
        unit_id: int
        anchor_id: int
        placement: Literal["before", "after"]
        expected_fingerprint: str
        desired_fingerprint: str


    @dataclass(frozen=True)
    class UnitMoveResult:
        changed: bool
        unit_id: int
        old_position: int
        new_position: int
        anchor_id: int
        anchor_context: str
        placement: Literal["before", "after"]


    def rename_unit_key(self, *, change: ConfirmedRename, user: User) -> Unit: ...


    def move_unit(self, *, change: ConfirmedMove, user: User) -> UnitMoveResult: ...

Both require `self.is_source`, validate ownership/capability themselves and hide
store iteration, lock order, hash changes, commit, rollback, cache invalidation
and audit from views. Views/forms do not manipulate files or Unit rows.

View проверяет Django signature/expiry и десериализует immutable confirmed
payload, но **не** решает stale state. Inventory method принимает payload,
заново загружает Unit/anchor и сравнивает оба fingerprints уже под
repository/DB locks; так signed preview проходит через deep interface без
TOCTOU.

Preview computation reuses the same validation/order helpers but performs no
write. Server выдаёт Django-signed preview token с operation kind, Unit/anchor
IDs, old/new или placement, expected structure fingerprint и **полным desired
structure fingerprint**; Move fingerprint включает весь canonical ordered
source-key list, не только двух соседей. Apply проверяет подпись, срок, fresh
permission и fingerprints под lock в порядке `desired → expected → conflict`,
описанном выше. Только состояние, не совпавшее ни с одним signed fingerprint,
возвращает stale HTTP 409; preview никогда не является авторизацией сам по себе.

### Internal web protocol

Два session-authenticated, CSRF-protected UI endpoints; это не REST API:

- `POST source/<pk>/rename-key/` — `preview` или `confirmed`;
- `GET|POST source/<pk>/move/` — GET anchor autocomplete, POST `preview` или
  `confirmed`.

JSON responses:

- `200`: preview or completed result with server-computed text/redirect URL;
- `400`: invalid form/key/anchor/no-op;
- `403`: revoked/missing `component.edit`;
- `404`: inaccessible/non-source/cross-component object;
- `409`: locked component, repository lock timeout, stale preview, collision or
  residual pending changes.

Autocomplete searches only source units of the moving unit’s component,
excludes the moving unit, limits results, returns Unit PK + escaped key + bounded
source preview + position, and never accepts project/component from the client.
Final POST sends anchor PK, not display text. Dynamic content is assigned with
`textContent`, never raw HTML.

## Затрагиваемые файлы

| Путь | Ответственность |
|---|---|
| `weblate/formats/base.py` | Default structural capabilities and deep format interface. |
| `weblate/formats/ttkit.py` | Flat `JSONFormat` rename/order adapter; exact-format and `json_sort_keys` guards. |
| `weblate/trans/models/component.py` | Side-effect-free capability properties shared by templates/views. |
| `weblate/trans/models/translation.py` | Component-wide Rename/Move orchestration beside add/delete; locks, store writes, DB convergence, commit. |
| `weblate/trans/forms.py` | Rename and Move preview/apply validation forms; hidden expected values. |
| `weblate/trans/views/source.py` | Permission-checked autocomplete, preview and synchronous apply responses. |
| `weblate/urls.py` | Two internal UI routes. |
| `weblate/trans/actions.py` | `RENAME_STRING=108`, `MOVE_STRING=109`; neither content/revertable/notification action. |
| `weblate/trans/change_display.py` | Safe old/new key and old/new position/anchor rendering. |
| `weblate/trans/migrations/0138_alter_change_action.py` | `AlterField(Change.action)` for the two new enum choices; no data migration. If migration head changes before execution, renumber/rebase rather than branch the graph accidentally. |
| `weblate/templates/translate.html` | Source-only :guilabel:`Rename key` item in existing Tools menu and accessible modal. |
| `weblate/templates/browse.html` | Move modal and action-template wiring. |
| `weblate/templates/snippets/embed-units.html` | Optional compact actions header, stable row anchor/highlight; existing consumers unchanged. |
| `weblate/templates/snippets/unit-move-action.html` | Per-row source/capability/permission-gated button. |
| `weblate/static/editor/source-unit-structure.js` | Two-step modals, anchor autocomplete, stale/error display, focus management and redirect. |
| `weblate/static/styles/main.css` | Narrow actions column and non-color-only temporary highlight. |
| `weblate/static/icons/swap-vertical.svg` | Material Design move icon, optimized with repository script; accessible name remains textual. |
| `weblate/formats/tests/test_formats.py` | Flat JSON mutation/parse-back/capability regressions. |
| `weblate/trans/tests/test_edit.py` | Model + view + rendered UI behavior on JSON components and aggregate/source/permission cases. |
| `weblate/trans/tests/test_changes.py` | History details and single-event behavior. |
| `docs/product/guides/producer-guide-weblate.md` | Replace delete-and-recreate workaround; document Rename, Move and existing age sorting. |
| `docs/changes.rst` | Upcoming-release feature entry. |
| `docs/security/threat-model.rst` | New authenticated POST mutation surface, permission/stale/lock/file-path properties; existing VCS integration reused. |

Не редактировать locale catalogs вручную; все новые user-facing strings проходят
Django/JS gettext extraction обычным release process.

## Задачи реализации

### Задача 1. Structural format seam и JSON-персистентность

**Outcome.** Flat JSON store умеет безопасно переименовать key и применить
canonical key order без изменения values. Unsupported formats и JSON с
`json_sort_keys=True` честно не заявляют capability. Save + parse-back сохраняет
новое имя/порядок; неизвестные и отсутствующие target keys не теряются.

**Files and interfaces.** `weblate/formats/base.py`,
`weblate/formats/ttkit.py`, `weblate/trans/models/component.py`; format seam
выше.

**Actions.**

- [ ] Добавить default-false capability methods и refusing mutation interface.
- [ ] Реализовать exact flat-JSON adapter: безопасно обновить raw id/index,
      invalidировать cached indexes; стабильная перестановка известных keys.
- [ ] Учесть `JSONOutputSortKeys`: обе capabilities false, без исключений.
- [ ] Добавить component capability properties без открытия файлов и без
      permission logic.
- [ ] Не включать наследников `JSONFormat` автоматически.

**Verification.** В `JSONFormatTest`:

- rename `b → x` сохраняет value и slot, old lookup исчезает, new lookup есть;
- collision/blank не мутируют store;
- reorder `c` after `a` сериализует ожидаемый порядок и повторный parse его
  сохраняет;
- target subset без anchor/одного key сохраняет остальные values и следует
  canonical relative order;
- unknown raw unit остаётся на исходном slot;
- `json_sort_keys=True`, nested и i18next не заявляют обе capabilities.

Команда:

    ./rundev.sh test weblate/formats/tests/test_formats.py -k "JSONFormat and (rename or order)" -n 0

### Задача 2. Deep Translation operations, audit и failure consistency

**Outcome.** Один synchronous Rename/Move меняет все применимые JSON files и
Units, сохраняет Unit PK/metadata, делает один structural commit и одну source
history entry. Collision/stale/pending/lock/parse failures откатываются до
исходного состояния; post-commit push failure оставляет честный локальный
успех с repository warning; commit/DB crash replay идемпотентен.

**Files and interfaces.** `weblate/trans/models/translation.py`,
`weblate/trans/actions.py`, `weblate/trans/change_display.py`, migration
`0138...`, `weblate/trans/tests/test_edit.py`, `test_changes.py`; Translation
inventory seam и consistency order выше.

**Actions.**

- [ ] Ввести immutable `ConfirmedRename`/`ConfirmedMove`, source-only
      preview/order helpers и два public inventory methods; переиспользовать
      `_validate_new_unit_context`, но collision и оба fingerprints проверять
      по свежему состоянию во всех translations под lock.
- [ ] Flush ambient pending с `skip_push=True`; refuse residual pending.
- [ ] Добавить `BEFORE|RECONCILE|MIXED` classifier по template и всем
      физически присутствующим target keys/order. Сравнивать полный signed
      desired structure fingerprint; одного совпадения unit/anchor
      недостаточно. `MIXED` и неизвестный dirty tree fail-closed.
- [ ] Только для `BEFORE`: stage/render/parse-back all stores до write,
      snapshot original bytes, targets before template и один local commit без
      signals/push. Для `RECONCILE` skip file/commit, выполнить DB convergence и
      записать honest reconciliation audit details с current revision/actor.
- [ ] После transaction выполнить обычные post-commit signals/push с outcome
      table выше.
- [ ] Rename: bulk-update context/id_hash только выбранной identity во всех
      sibling Units, не меняя PK/timestamps/content; обновить component source
      cache и variants.
- [ ] Move: вычислить canonical template order, применить adapter каждому
      store и присвоить его contiguous positions всем sibling Units;
      priority/timestamps/content неизменны.
- [ ] Добавить path-safe rollback через
      `repository.reset_to_revision(previous_revision)` только при
      unchanged HEAD + clean preflight + непрерывно удерживаемом lock; этим
      очищаются и index, и working tree. Добавить dirty-tree fail-closed,
      immediate second-transaction reconciliation после post-commit DB error и
      signed-fingerprint recovery после process loss; duplicate apply не
      создаёт второй audit/commit.
- [ ] Добавить ActionEvents 108/109, choices migration и detail renderer;
      исключить их из `ACTIONS_CONTENT`, `ACTIONS_REVERTABLE` и notification
      families.
- [ ] Инвалидировать caches/store hashes и запустить только необходимые
      обычные post-commit hooks; не запускать MT/judge и не создавать target
      history entries.

**Verification.** На `EditJSONTest`/model seam:

- Rename сохраняет source/target Unit PK, targets/states, labels, flags,
  Explanation, comments, Suggestion, screenshot relation, read-only,
  priority/position/timestamp и связанные judge rows; context/id_hash и JSON
  key меняются во всех 15-like translations. Derived variant membership
  пересчитывается штатным `update_variants`, а не сохраняется вопреки новому key;
- target JSON без физического old key не получает new key после Rename, а его
  DB Unit сохраняет PK/context/hash и переживает parse-back;
- reparse/update component не создаёт old Unit и не откатывает key;
- collision, invalid/no-op key, non-source, glossary, locked, unsupported
  format, dirty tree и residual pending дают отказ без принятого file/DB diff;
- pre-commit exception при unchanged HEAD очищает Git index/worktree через
  guarded `reset_to_revision(previous_revision)` и откатывает DB;
  post-commit DB exception либо завершается immediate reconciliation во второй
  transaction, либо оставляет recovery-required clean desired HEAD; post-DB
  push failure сохраняет local commit/DB и выдаёт warning;
- simulated clean-HEAD `RECONCILE` state с точным desired fingerprint
  converges DB без duplicate content commit и помечает audit как reconciliation;
  несовпадающий full-order fingerprint, `MIXED` и partial uncommitted dirty
  state fail-closed;
- Move shifts both directions, preserves all
  content/metadata/priority/timestamps, assigns canonical template positions
  всем translations, writes template/targets and survives reparse;
- missing target key remains absent, other target keys keep relative canonical
  order;
- Rename и Move no-op не commit/audit;
- ровно один `RENAME_STRING`/`MOVE_STRING` Change принадлежит source Unit;
  target translations не получают структурных Change rows; history renders
  escaped old/new/anchor details;
- rename variant member/base сохраняет Unit/FK records, а variant membership
  совпадает с результатом штатного `update_variants` для нового key;
- migration state matches `ActionEvents.choices`.

Команды:

    ./rundev.sh test weblate/trans/tests/test_edit.py -k "EditJSONTest and (rename or move)" -n 0
    ./rundev.sh test weblate/trans/tests/test_changes.py -k "rename_string or move_string" -n 0
    DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py makemigrations --check --dry-run

До задачи 3 выполнить production-size local smoke: 15 JSON stores × не менее
4 876 keys, включая target subset, Rename и Move в обе стороны, parse-back и
Git commit. Записать отдельно parse/render, DB и commit wall time. Если total
превышает 30 с или не оставляет десятикратный запас к production 300-секундному
proxy timeout, synchronous контракт считается неподтверждённым и план
возвращается на продуктовое решение до написания UI; постоянный flaky timing
assert в test suite не добавлять.

### Задача 3. Source-only Rename/Move UI

**Outcome.** Project admin видит Rename в Tools только на фактической
source-строке и компактную Move-кнопку только в source Browse rows. Оба действия
имеют keyboard-accessible двухшаговый preview; permission revocation, stale
preview и lock failure отображаются без перезагрузочного race. Filter/age sort
не мешают Move; success открывает pure Position и подсвечивает результат.

**Files and interfaces.** `weblate/trans/forms.py`,
`weblate/trans/views/source.py`, `weblate/urls.py`, templates/static/icon/CSS из
таблицы, `weblate/trans/tests/test_edit.py`; internal web protocol выше.
Новый script подключается обычным `{% static %}` на обеих страницах как IIFE,
аналогично существующим `editor/browse.js`/`editor/full.js`; webpack entry и
новая third-party dependency не нужны.

**Actions.**

- [ ] Добавить формы с `helper.form_tag=False`; сервер владеет Unit/component,
      client передаёт new key либо anchor PK/placement и только server-signed
      preview token, содержащий expected/desired structure fingerprints.
- [ ] Добавить GET autocomplete и POST preview/confirmed endpoints с точными
      status codes, fresh permission/capability checks и bounded escaped output.
- [ ] Добавить :guilabel:`Rename key` в `unit_tools_dropdown`; modal показывает
      old/new, files/languages и обязательное предупреждение про игровой код.
- [ ] Подключить optional row actions template в Browse; узкая постоянная
      icon-button колонка только там, где на странице есть допустимые source
      rows. Остальные `embed-units.html` consumers не получают пустую колонку.
- [ ] Реализовать move autocomplete, before/after, old/new neighbors, preview,
      confirm, loading/error states; no-op disabled.
- [ ] После success очистить stale search-session ordering, redirect на actual
      source translation `sort_by=position`, вычисленную page и row anchor;
      highlight содержит текст/aria-live announcement, не только цвет.
- [ ] Выполнить WCAG 2.2 AA требования: native buttons/inputs, labels/errors,
      initial focus, focus trap/return, Escape/Cancel, visible focus, keyboard
      selection, reduced motion, localized strings.

**Verification.** View/UI regressions:

- Administration actor видит actions; `source.edit` без `component.edit`, target
  Unit, glossary, locked/unsupported component и `json_sort_keys=True` не
  видят их; direct POST/GET повторяет 403/404/409 contract;
- aggregate `/translate/space-arena/-/en/` показывает action только строкам,
  где `unit.is_source`;
- autocomplete не возвращает cross-component/private/inaccessible/target Units,
  исключает moving Unit и ограничивает результат;
- preview не пишет; confirmed без валидного signed preview token отклонён;
  desired fingerprint проверяется до expected (идемпотентный повтор/no-op);
  состояние вне обоих fingerprints требует новый preview;
- Browse action остаётся при `sort_by=-timestamp` и фильтре; success redirect
  содержит `sort_by=position`, правильную page и row highlight;
- HTML имеет одну сбалансированную форму на modal, уникальные IDs, accessible
  names и не вкладывает `<form>` через crispy.

Команда:

    ./rundev.sh test weblate/trans/tests/test_edit.py -k "rename_key or move_string or browse" -n 0

После зелёных regressions — browser smoke на уже запущенном локальном dev
instance (не перезапускать shared stack без отдельного разрешения):

1. Войти `admin/admin`, открыть source translation JSON-компонента.
2. Rename: Tools → Rename key → invalid/collision → valid preview → confirm;
   проверить warning, focus, success и тот же Unit URL/PK.
3. Browse: сортировать «Возраст строки» newest-first, открыть Move у новой
   строки, keyboard-only найти anchor, preview neighbors, confirm; проверить
   redirect в «Позиция» и highlight.
4. Скачать/прочитать template и два target JSON: new key/value и relative order
   совпадают; refresh/component reparse порядок не откатывает.
5. Повторить viewport desktop и узкий mobile, keyboard Tab/Shift+Tab/Escape;
   сохранить визуальное доказательство только локального стенда.

### Задача 4. Документация, security contract и интеграционная приёмка

**Outcome.** Руководство больше не предлагает delete+create workaround;
changelog и threat model честно описывают новую permissioned VCS mutation.
Таргетированные regressions, docs и lint проходят вместе; production не
затронут.

**Files and interfaces.** Три документа из таблицы; весь изменённый file set.

**Actions.**

- [ ] В producer guide заменить текущий раздел «Поменять ключ, не потеряв
      переводы» инструкцией Tools → Rename key; рядом описать Browse → Move,
      anchor search, «Возраст строки» и предупреждение об обновлении кода игры.
- [ ] Добавить concise upcoming-release entry в `docs/changes.rst`.
- [ ] Дополнить `docs/security/threat-model.rst`: два новых authenticated,
      CSRF-protected POST; `component.edit` на фактическом component;
      apply-time recheck; no REST/token surface; выбран один source Unit и один
      anchor, но Move сдвигает промежуточные positions и переписывает все файлы
      компонента; key не становится filename/shell input; используется
      существующий repository commit/push path, не новая integration class.
- [ ] Проверить migration graph, gettext wrapping, JS/template formatting и
      отсутствие ручных locale edits.
- [ ] Запустить объединённую targeted suite, docs build и `prek` один раз после
      интеграции; провести self-review по brief и threat model.

**Verification.** Команды из корня, кроме явно указанного cwd:

    ./rundev.sh test weblate/formats/tests/test_formats.py -k "JSONFormat and (rename or order)" -n 0
    ./rundev.sh test weblate/trans/tests/test_edit.py -k "rename_key or move_string or browse" -n 0
    ./rundev.sh test weblate/trans/tests/test_changes.py -k "rename_string or move_string" -n 0
    ./rundev.sh check
    uv run prek run --all-files
    # cwd docs/
    uv run make html

Не утверждать passing до фактического вывода. Если host pytest блокируется
неустановленным `weblate_customization`, использовать штатный container test
path; не вносить product dependency только ради host collection.

## Приёмочные сценарии

### Space Arena move

Исходно:

- `%BETA_LABEL%`: position 4864;
- `%SQUADRONS_SHIP_SELECTION_POPUP_VIP_EXPIRED%`: position 4875.

Project admin выбирает Move → after `%BETA_LABEL%`, проверяет neighbors и
подтверждает. Ожидается position 4865; прежние 4865–4874 сдвинуты на один;
priority остаётся 100. Template `en.json` и существующие target JSON имеют тот
же relative order. Переводы/flags/history/timestamps неизменны. Повторный parse
не откатывает порядок.

Это локальный/dev regression scenario. На production ничего не перемещать без
отдельного явного разрешения.

### Identity-preserving rename

Source Unit с targets, label, `read-only`, Explanation, Comment, Suggestion,
screenshot и judge verdict переименовывается `OLD → NEW`. До/после совпадают
все PK и связанные records; old context/hash отсутствует, new context/hash один
на translation; файлы parse-back валидны. Collision оставляет exact before
snapshot.

### Permission and scope

- project Administration: UI + apply доступны;
- actor только с `source.edit`: UI отсутствует, direct endpoint 403;
- target page, glossary, nested JSON, sorted-key JSON: UI отсутствует,
  mutation отклонена;
- aggregate project-language page не расширяет actor scope и не принимает
  cross-component anchor.

## Порядок выполнения и handoff

Задачи строго `1 → 2 → 3 → 4`: format seam нужен inventory methods; inventory
methods нужны endpoints/UI; документация описывает окончательно проверенный
контракт. Не распараллеливать правки `translation.py`, `source.py`,
`test_edit.py` нескольким агентам. Format adapter и history renderer можно
делегировать параллельно только после фиксации interfaces выше и с одним
integration owner.

Перед изменением экспортируемых `TranslationFormat`/`Translation` symbols
выполнить LSP references и обновить всех callers чистым cutover. После
реализации запросить code review; миграцию не применять и production не
деплоить без отдельного разрешения. Завершённую реализацию коммитить/пушить по
`AGENTS.md`, staging только явных paths — никогда `git add -A`.

## Проверка полноты плана

План покрывает все согласованные решения:

- Weblate, а не Google Sheet, является source of truth;
- UI-only single Rename/Move, project admin, source units only;
- Tools modal для Rename; Browse right-column action и searchable anchor для
  Move; два confirm stages;
- existing age sorting reused; filtered/age view поддержан;
- collision full stop, read-only preserved, no dedicated Undo/API/bulk;
- physical file order, stable Unit PK, all language data/metadata, one audit
  event, no translator notifications;
- flat JSON production case, priority semantics, stale/lock/pending/failure
  handling, accessibility, docs, security and exact verification.

Открытых продуктовых решений нет. Реализация может начаться только после
отдельного одобрения этого плана; deployment остаётся ещё одним отдельным
гейтом.
