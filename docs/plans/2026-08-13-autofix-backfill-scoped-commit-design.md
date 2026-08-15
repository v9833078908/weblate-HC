# Scoped commit для backfill автофиксов

Дата: 2026-08-13. Статус: дизайн реализован и проверен 2026-08-15.

Продолжение `docs/LLM-first/plans/2026-08-11-layer0-autofix-quick-wins.md`.

## Проблема

`reapply_autofixes --apply` создаёт pending-изменения от bot-пользователя, а
затем передаёт управление `Component.commit_pending()`. Этот API собирает все
pending-изменения семейства репозитория и коммитит их по translation/author
группам. Проверка «чужой pending» по `unit_id` не различает две pending-записи
одного юнита; правка переводчика после backfill могла быть принята за свою и
попасть в Git commit. Repository lock не блокирует `Unit.translate()`.

Одновременно команда обещает один Git commit на repository root, но штатный
`Component.commit_pending()` выпускает commit на каждую translation/author
группу. Candidate query также не исключал source translation, хотя измеренная
популяция backfill их исключает.

## Решение

### Изолированный набор pending-изменений

`apply_group()` под repository lock начинает одну внешнюю `transaction.atomic()`.
Она повторно получает кандидаты с `select_for_update()` в порядке PK,
пересчитывает автофиксы на свежем target и вызывает `Unit.translate()` только
для действительно дефектных unit. Блокировки строк удерживаются до конца
commit path, поэтому переводчик не может подменить только что исправленный
unit между repair и фиксацией.

После каждого успешного `translate()` команда сохраняет PK созданного
`PendingUnitChange`. Перед VCS-записью она проверяет, что в repository family
нет pending-записей вне этого точного множества. Если есть, run завершается
ошибкой без VCS commit. Если новая чужая запись появится после этой проверки,
она всё равно не попадёт в commit: commit API принимает только exact PK set.

### Один commit на repository root

Новый внутренний scoped commit API принимает root component, exact pending PK
set, bot-пользователя и `skip_push=True`. Он:

1. выбирает только заданные pending-записи и группирует их по translation;
2. обновляет storage каждой translation через существующий
   `Translation.update_units()`;
3. собирает все изменённые filenames;
4. вызывает `root.commit_files()` один раз;
5. удаляет только успешно записанные pending-записи и отправляет обычные
   post-commit hooks только после удачного VCS commit.

Ошибочный storage/VCS шаг оставляет pending-записи для диагностики и возвращает
неуспех команде. Чужие pending-записи не выбираются, не удаляются и не
попадают в commit.

### Scope и пунктуация

Candidate query исключает glossary components, templates, source translations
и read-only units. Эти условия повторяются под row lock.

Терминальный автофикс обрабатывает только `.?!`. `:` исключён: измерение в
`docs/misc/autofix-terminal-punctuation.md` показало риск снятия турецкого
маркера прямой речи.

## Альтернативы

1. Оставить `Component.commit_pending()` и расширить проверки. Отклонено:
   API всё равно читает чужой pending-набор и не даёт один root commit.
2. Оставлять pending-изменения scheduler без VCS commit. Отклонено: меняет
   контракт `--apply` и не даёт оператору атомарный backfill.

## Проверка

TDD-регрессии обязаны доказать:

- source translation не попадает в dry-run и `--apply`;
- pending правка переводчика того же unit не маскируется собственным pending;
- scoped commit не включает чужой pending, в том числе появившийся после
  финальной проверки;
- две translation одного root дают ровно один Git commit;
- storage/VCS failure оставляет own pending и возвращает `CommandError`;
- `:` остаётся без изменения, а `.`, `!` и `?` сохраняют текущие гарантии.
