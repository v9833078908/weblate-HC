# Локальное согласование Weblate и Wizard с main a554cebe

Статус: реализация подготовлена; **локальная реализация, проверки и финальная сборка завершены; production не обновлён**.
Production не обновлялся и в ходе реализации не опрашивался. Службы production
не перезапускались, старое задание не возобновлялось, платных запросов нет.

## База и сохранность исходного решения

Weblate подготовлен в новой ветке
`y.bazenkov/weblate-main-reconciliation-20261026` от настоящего main
`a554cebea17c390da027ec7e2b8c67ac65397df3`. Собственное изменение перенесено
из `19cc555a60e63047d55ba7253b1074d04fff4700` относительно точного e752.
Wizard — ветка `y.bazenkov/wizard-main-compatibility-20261026` от `803f6a4`.
Основная ветка и прежние опубликованные ветки не переписываются.
Перед работой сохранены локальные Git bundles обеих исходных копий.

Все 168 отличающихся путей кандидата сверены с incoming. Перечень и исходные
blob IDs: `docs/operations/reports/2026-10-26-main-reconciliation-files.tsv`.
Число конфликтных блоков в TSV относится к первичной пробе: три блока в двух
файлах. Они разрешены с сохранением новых provider errors и нашей immutable policy.
Исторический production manifest перенесён в
`docs/operations/reports/2026-10-23-imported-production-snapshot.json`;
это свидетельство прежнего снимка, не описание установленного кандидата.

## Изменения поведения

- Новые routed-запросы используют source-owned slots из свежего main.
  Codec хранится вместе с semantic spend fence; metadata не меняет request digest.
- Старый parts-контракт отделён в replay-only модуль. Миграция 0154 сохраняет
  старые записи неидентифицированными. Только доказанное совпадение запроса и
  полного набора строк разрешает прочесть старый ответ; отправить legacy запрос нельзя.
- Идентификаторы примеров в новых durable prompts стабильны. Старые случайные
  идентификаторы намеренно не угадываются: если точный запрос невосстановим,
  сохраняется блокировка без повторной оплаты.
- Producer reconciliation проходит тот же digest/codec gate, а не передаёт
  произвольное сохранённое тело непосредственно в parser. Восстановленный HTTP
  response содержит request metadata, необходимую для штатной проверки статуса.
- Partial/error semantics свежего Weblate объединены с восстановлением и
  запретом mutating operations в verify_only, включая входной overwrite=true.
- В Wizard runtime-переписывание не потребовалось: partial уже поддерживался.
  Добавлена проверка HTTP-клиента; существующий регресс оркестрации сохранён.
- Deployment pin использует ID реального контейнера и прекращает rebuild при
  ошибке pin. Автоматический prune образов/кэша удалён; остаётся отчёт о месте.

## Проверки и пределы результата

| Проверка | Результат на этапе подготовки |
| --- | --- |
| Исходный journal + routed baseline | 69 passed |
| Новые journal codec guards | 9 passed |
| V1 golden contract, V1/V2 replay, invalid reply, OpenRouter/LiteLLM | 5 passed |
| Replay, journal и подготовленная русская локализация | 22 passed |
| Producer/native API, verify_only overwrite, MT и migration contracts | 176 passed |
| Docker/SSH stubs и capacity tools | 29 passed; никаких реальных deploy/prune |
| Wizard compatibility/recovery/deployment | 124 passed; новый client test отдельно PASS после уточнения `_http_status` |
| Общий связанный regression после исправлений | 1971 passed, 53 skipped (related-regression-final.log) |
| Ruff затронутого кода | PASS; executable-бит Windows bind mount исключён только в CLI lint |
| Migration check на локальном восстановлении | No changes detected; восемь forward migrations прошли |

Предыдущий большой прогон: 1966 passed, 53 skipped и три failures. Один выявил
прямой parser path reconciliation; два требовали compilemessages в новом стенде.
Исправления проверены целевым прогоном; финальный общий результат фиксируется
отдельно, а старый прогон не объявляется полностью зелёным.

Восстановлен существующий разрешённый backup 5e27fcc, SHA-256
`17ee41fe46b206f19f25b7e8c03e887c401579a83d3e06e9cfa219509ae12c97`.
Проверено равенство количеств и хешей всех прежних полей в защищаемых таблицах
до/после миграций, включая Unit, run, usage, MT receipts и judge history.
Локальная восстановленная БД не использует production Redis, credentials или
workers и не переносится обратно поверх живой БД. Историческая область 1662
переводов не монтировалась в изменяемые тесты и не заменялась этим восстановлением.

Точный последний production image 03ef в этой реализации не получался с сервера.
Поэтому web-only rollback именно этого image на новой схеме остаётся незакрытым
gate переноса. Также нужны свежие capacity/health/refs, пригодный image archive,
согласованные B0/B1, изоляция без resume и отдельное разрешение на установку.
Локальная реализация сама по себе не означает готовность к production.

## Итоговое ревью и исправление

Независимое read-only ревью b34fdea: Critical 0, Important 1, Minor 0.
Замечание: replay должен повторять реальный producer path с cleanup/context
ordering и обратной подстановкой protected tokens. Исправлен именно этот путь,
без ослабления digest/codec gates. Новый тест использует реальный producer,
игровой markup и python-brace flags, валидный mock-ответ и прерывание после его
сохранения/разбора. Проверяются равенство исходного/replay payload, точный target,
неизменность witness digests, один transport и один usage record.
Контрольная подстановка прежнего helper в отдельном процессе воспроизводит
ошибку; исправленный связанный набор: 60 passed (review-fix-green.log).
Ранее собранный b34fdea image не является финальным кандидатом после исправления.

Историческая инвентаризация не сверялась повторно с сервером; она остаётся
датированным свидетельством. Текущие production health/capacity/image behavior
не проверяются в локальном этапе. Нормализация Wizard archival JSON отдельно
проверена на структурное равенство, runtime Wizard не меняется.

## Финальный локальный выпуск

- Runtime source commit: `ebd0333b1fb256343c0e7039a85d34acb7e7e62d`.
- Image: `sha256:1903e9260b27350eee2cb83df567ee983bef4a2f3c549162ac5b12f6c517510a` (linux/amd64).
- Архив image.tar: 592133632 байт; SHA-256 `e14801d04139269c2b02391d2dda8dc3068d2b76689018b607e575df8a8eb89e`.
- Артефакты: `D:/Weblate/weblate-HC/.superpowers/production-rollout-20261023/artifacts/main-reconciliation-image-ebd0333-20261026/`.
- Установленные файлы: 15 хешей совпали с Git; NumPy 2.3.5; migration leaf 0154.
- Изолированный image smoke: модели без drift, 525 миграций на новой синтетической
  БД, login 200, анонимный/неверный token 401, верный token 200; capabilities
  mt_async_batches / mt_reconciliation / verify_only_immutable = 1.
- Journal smoke в самом образе: один synthetic transport, один usage callback,
  повторное чтение без новой отправки, новый legacy transport запрещён.
- Временные контейнеры и сеть image-проверки удалены с проверкой их ID/labels.

Связанный регресс 1971 PASS относится к исходному интегрированному состоянию;
последующее исправление recovery покрыто 60 PASS затронутых MT/journal/routed
сценариев. Неизменённые проверки повторно не запускались. Дополнительно Wizard
full-cycle/partial suite: 12 PASS; сначала восстановлена только локальная
tracking-ссылка на прежний опубликованный production snapshot, необходимая
rollback fixture. Первый сбой этого набора был отсутствием ref, не runtime.
Контроль старого helper без активных highlights проходил; значимый RED использует
python-brace flags и зарегистрированный GameMarkupCheck, реально меняющие source
при cleanup. Этот RED подтверждён до итогового PASS, исходные логи сохранены.

Декларация выпуска в репозитории и внешний manifest разделены: сам image ссылается
на точный runtime commit выше; последующие документационные коммиты описывают
его результаты, не подменяя image revision текущим HEAD отчётной ветки.
Архив и секретные/приватные backup в Git не отправляются.
