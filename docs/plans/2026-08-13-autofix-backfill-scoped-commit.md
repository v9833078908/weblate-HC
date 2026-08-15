# План реализации scoped commit для backfill автофиксов

**Статус (2026-08-15): реализован и проверен.** Exact pending scope,
row locks и один commit на repository root реализованы в команде
`reapply_autofixes` и покрыты тестами.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Цель:** сделать `reapply_autofixes --apply` безопасным относительно правок
переводчиков, ограничить VCS-запись own pending-изменениями и выпускать ровно
один Git commit на repository root.

**Архитектура:** команда хранит exact PK созданных ею `PendingUnitChange` и
удерживает row locks выбранных unit до scoped root commit. Новый внутренний
commit path обновляет storage только по переданным pending PK, собирает files
всех translation и вызывает `Component.commit_files()` один раз. Чужой pending
не читается, не удаляется и не коммитится.

**Стек:** Python 3.13, Django/PostgreSQL transactions, Weblate Unit/
PendingUnitChange/Translation/Component модели, Git backend, pytest.

---

## Задача 1. Зафиксировать безопасный scope и решение по `:`

**Файлы:**

- Modify: `weblate_customization/tests/test_autofixes.py`
- Modify: `weblate_customization/src/weblate_customization/autofixes.py`
- Test: `weblate/trans/tests/test_commands.py`

### Step 1: Написать падающие тесты

Добавить тест `RemoveAddedFinalStopTest.test_keeps_added_colon`, где source не
оканчивается `:`, а fr target оканчивается NBSP + `:`. Ожидание: target
неизменён, `False`.

Добавить в `ReapplyAutofixesTest` source-language unit с исправимым french
spacing defect и проверить dry-run и `--apply`: unit не попал в счётчик, его
`target`, `source`, `Change` и `PendingUnitChange` не изменились.

### Step 2: Убедиться, что тесты красные

Run:
`./rundev.sh test weblate_customization/tests/test_autofixes.py -k added_colon`

Run:
`./rundev.sh test weblate/trans/tests/test_commands.py -k source_translation`

Expected: оба теста FAIL, потому что `:` ещё removable, source translation
ещё попадает в candidate query.

### Step 3: Минимальная реализация

- Изменить `TERMINAL_MARKS` на `".!?"`.
- В `candidate_units()` исключить `translation.is_source` вместе с template.
- В `repair()` вернуть `None`, если `unit.translation.is_source`.

### Step 4: Green

Run те же тесты. Expected: PASS.

### Step 5: Commit

```bash
git add weblate_customization/src/weblate_customization/autofixes.py \
  weblate_customization/tests/test_autofixes.py weblate/trans/tests/test_commands.py
git commit -m "fix(autofixes): keep colons and source translations unchanged"
```

## Задача 2. Выделить scoped pending commit без VCS-коммита

**Файлы:**

- Modify: `weblate/trans/models/translation.py`
- Modify: `weblate/trans/models/component.py`
- Test: `weblate/trans/tests/test_commands.py`

### Step 1: Написать падающие integration-тесты

В `ReapplyAutofixesTest` создать второй target translation в том же repository
root. После `--apply` проверить по Git log/реальной revision, что root получил
ровно один новый commit, хотя изменились два translation files.

Добавить foreign `PendingUnitChange` от другого user на repaired unit после
создания bot pending, но до scoped commit. Ожидание: `CommandError`, VCS commit
не создан и foreign pending существует.

Добавить VCS failure через mock `Component.commit_files(return_value=False)`.
Ожидание: `CommandError`; exact bot pending остаётся, не удалён.

### Step 2: Убедиться, что тесты красные

Run:
`./rundev.sh test weblate/trans/tests/test_commands.py -k "one_root_commit or foreign_pending_same_unit or commit_failure" -n 0`

Expected: FAIL: существующий `Component.commit_pending()` видит pending по
translation/author, смешивает unit-level pending и не проверяет VCS result.

### Step 3: Выделить подготовку одной translation

В `Translation` извлечь из `_commit_pending_with_filename()` внутренний helper,
который принимает exact `list[PendingUnitChange]`, вызывает существующий
`update_units()`, но **не** вызывает `git_commit()` и **не** удаляет pending.
Он возвращает успешные PK, unit timestamps для cleanup и filenames только при
успешном storage update. Сохранить текущий public `commit_pending()` без
изменения поведения, построив его на этом helper и оставив его per-author
commit path.

### Step 4: Добавить root scoped API

В `Component` добавить внутренний `commit_pending_subset(reason, user,
pending_change_ids, *, skip_push) -> bool`.

Он обязан:

1. получить pending строго по `pk__in=pending_change_ids`, с
   `select_for_update()` и `select_related("unit", "author")`;
2. проверить принадлежность всех записей root/linked repository family;
3. подготовить stores каждой translation через helper из Step 3;
4. если хотя бы одна запись storage не записалась, вернуть `False` до VCS
   commit и сохранить все pending;
5. вызвать `root.commit_files(..., files=all_filenames, skip_push=True)` ровно
   один раз с bot author и root commit message;
6. только после true удалить successful pending через существующий
   `delete_successful_pending_changes`, очистить disk state, invalidates caches
   и отправить post-commit signal для затронутых components.

Не передавать в API queryset без PK snapshot и не вызывать
`Component.commit_pending()` из scoped path.

### Step 5: Green

Run command tests из Step 2. Expected: PASS.

### Step 6: Commit

```bash
git add weblate/trans/models/component.py weblate/trans/models/translation.py \
  weblate/trans/tests/test_commands.py
git commit -m "fix(trans): commit scoped autofix pending changes once"
```

## Задача 3. Применить exact pending snapshot под row locks

**Файлы:**

- Modify: `weblate/trans/management/commands/reapply_autofixes.py`
- Test: `weblate/trans/tests/test_commands.py`

### Step 1: Написать падающие тесты

Добавить тест, где `Command.repair()` создал bot pending, а до scoped commit
добавляется pending другого user на **тот же** unit. Ожидание: command raises
`CommandError`; bot and foreign records остаются pending; `commit_files` не
вызывается.

Добавить test, где чужой pending появляется после final foreign check. Ожидание:
scoped commit содержит только bot PK, foreign pending остаётся и не попадает в
Git history.

### Step 2: Red

Run:
`./rundev.sh test weblate/trans/tests/test_commands.py -k "foreign_pending" -n 0`

Expected: FAIL на текущем `.exclude(unit_id__in=written)`.

### Step 3: Реализация

- Заменить `written: list[int]` на exact `pending_change_ids: set[int]`.
- Выполнять repair-loop в одной внешней `transaction.atomic()` внутри
  `root.repository.lock`; получать каждый unit через `select_for_update()` в
  PK-order и удерживать блокировки до завершения scoped API.
- После `Unit.translate()` взять PK `unit.pending_unit_change`; отсутствие PK
  трактовать как internal error.
- `foreign_pending()` сравнивает PK, не `unit_id`.
- Перед commit отказывать при любом foreign pending, но всегда передавать в
  scoped API только own PK. `False` от API преобразовать в `CommandError`.
- В stdout не писать `written` как committed до положительного результата API.

### Step 4: Green

Run тесты из Step 1 и весь Reapply набор:
`./rundev.sh test weblate/trans/tests/test_commands.py -k Reapply -n 0`

Expected: PASS.

### Step 5: Commit

```bash
git add weblate/trans/management/commands/reapply_autofixes.py \
  weblate/trans/tests/test_commands.py
git commit -m "fix(trans): isolate autofix backfill pending changes"
```

## Задача 4. Синхронизировать contract и провести верификацию

**Файлы:**

- Modify: `docs/admin/management.rst`
- Modify: `docs/changes.rst`
- Modify: `docs/misc/autofix-terminal-punctuation.md`
- Modify: `docs/misc/col4-judge-golden.json`
- Modify: `docs/LLM-first/plans/2026-08-11-phase0-schema-and-judge-calibration.md`
- Test: `weblate/trans/tests/test_autofix.py`

### Step 1: Обновить contract

- Документировать `:` как намеренно исключённый.
- Уточнить, что `--apply` commits only its own snapshot once per root and
  returns error without consuming it when storage/VCS commit fails.
- Обновить fingerprint/golden metadata только если ordered registry меняется.
  Не переписывать measurement records.

### Step 2: Тесты и lint

Run:
`./rundev.sh test weblate_customization/tests/test_autofixes.py weblate/trans/tests/test_autofix.py weblate/trans/tests/test_commands.py -k "Reapply or autofix or Autofix" -n 0`

Run:
`uv run prek run --files weblate/trans/models/component.py weblate/trans/models/translation.py weblate/trans/management/commands/reapply_autofixes.py weblate/trans/tests/test_commands.py weblate_customization/src/weblate_customization/autofixes.py weblate_customization/tests/test_autofixes.py docs/admin/management.rst docs/changes.rst`

Run:
`uv run pylint weblate/trans/models/component.py weblate/trans/models/translation.py weblate/trans/management/commands/reapply_autofixes.py`

Run:
`uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh`

Expected: modified modules/tests green; no newly introduced mypy errors.

### Step 3: Commit and push

```bash
git add docs/admin/management.rst docs/changes.rst \
  docs/misc/autofix-terminal-punctuation.md docs/misc/col4-judge-golden.json \
  docs/LLM-first/plans/2026-08-11-phase0-schema-and-judge-calibration.md \
  weblate/trans/tests/test_autofix.py
git commit -m "docs(trans): describe scoped autofix backfill"
/usr/bin/git push
```
