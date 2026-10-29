# Подготовка production Weblate и визарда к переносу — 23 октября 2026

Статус: проверенные изменения подготовлены в отдельных рабочих ветках.
Merge, production-развёртывание, сборка production-образа и перезапуск служб
не выполнялись. Установка требует нового явного разрешения пользователя.

План: `docs/operations/plans/2026-10-23-production-git-publication-and-autonomous-rollout.md`.
Пользователь разрешил подготовку без Pull Request: SCM-Manager 1.60 не имеет
доступного механизма PR, администратора подключать не следует.

## Репозиторий и идентичность

Репозиторий: <http://10.39.40.3:8080/scm/git/ai_weblate>.
Проверены приватность и аутентифицированный WRITE-доступ `y.bazenkov`.
Автор новых коммитов: `y.bazenkov <y.bazenkov@herocraft.com>`.
Два независимых дерева кода хранятся в отдельных ветках одного репозитория.
Ветки `main`/`master` не создавались и не изменялись. В интерфейсе SCM следует
выбирать нужную ветку; настройка default branch не изменялась.

| Назначение | Ветка | Контрольный коммит |
| --- | --- | --- |
| Снимок production Weblate | `production/weblate-20261023` | `4c54e0171f0230a86d684bd0b5a93270e7e08a5b` |
| Снимок production Wizard | `production/wizard-20261023` | `de6cd499836c3f23302449dddfbec9c191956b1f` |
| Weblate с изменениями и шаблоном отката | `y.bazenkov/weblate-autonomous-recovery` | `586a4dda3c6defa294fa23f74ccfe4e4ce9d3af9` |
| Wizard с изменениями и совместимым откатом | `y.bazenkov/wizard-autonomous-recovery` | `0e1418ea41428d15d76071efc40080ebc2c5572d` |

Это коммиты проверенного кода до добавления настоящего отчёта и перечней файлов.
Последующие документационные коммиты в этих ветках не меняют проверенный код.
Итоговые HEAD фиксируются отдельно в манифесте публикации и сверяются с сервером.

## Что действительно работает на production

Weblate: `hc-srv15-localizer`, `/srv/hcgameloc`, исходный коммит
`1a852b65ed4e6f5065a8caee403a9f3da5f8d459`, контейнер `hcgameloc-weblate-1`,
образ `sha256:3f0cf69e97d160d2ce7aef3cbb2238bf6380b69dac568721b6530b78a3f0a692`.
1490 общих файлов установленных пакетов совпали с исходниками; дополнительно
контейнер содержит 264 скомпилированных каталога `.mo`. Текстовые production-файлы
не заменены локальной версией. В Git не внесены неотслеживаемые `.env.bak` и архивы БД.

Wizard: `hc-srv20-infobase`, выпуск
`/opt/bdhc/weblate-wizard/releases/release-12c43e2-durable-mt-20261021`.
В установленном каталоге нет `.git`; 271 общий исходный файл после нормализации
LF/CRLF соответствует коммиту `12c43e255c57d1e83167c239a6ea98f16c01423c`.
Снимок сохраняет все 276 полученных файлов, включая пять артефактов интерфейса.
Отсутствующие в установленном комплекте файлы не выдуманы в production-снимке;
в рабочей ветке восстановлены необходимые authoring/licensing-файлы из исходного Git.

Фактический backup-baseline Weblate: 17 проектов, 406794 строки, 396217 непустых
переводов, 39 MT-операций, 5919 producer runs. Защищённый общий digest:
`ebbee303f908442d26b6d57cdcec71cc`.
Прежние 1662 польских перевода и 158 receipts относятся к отдельному локальному
стенду; он не изменялся и его БД не переносилась поверх production.

В Wizard: 9 завершённых заданий, 1 отменённое и 1 `unknown`.
Задание `8add8aa4-f75b-44dd-b46e-e6f071dee4a9` не возобновлялось: сохранены
16 подтверждённых операций и 1 intent. Настройки production, включая действующие
external-write/profile flags, при подготовке не изменялись.

## Содержание изменений

Weblate объединён трёхсторонним сравнением фактического production `1a852b65`
и подготовленного `84955a0e` через общую базу `de468d90`. Сохранены production-
документы и исправление русских терминов с беглой гласной. Реализация
`weblate/checks/morphology.py` и `weblate/glossary/models.py` не изменена относительно
production. Единственный конфликт changelog разрешён сохранением обеих записей.

| Область | Подготовленное поведение |
| --- | --- |
| Weblate Producer API | Совместимые quality v2, MT reconciliation и capabilities для визарда |
| Weblate machinery/Judge | Долговечный журнал ответа до разбора, защита от повторной оплаты в подключённых операциях |
| Weblate quality | Immutable verify-only, проверка актуальности evidence, защита от изменения переводов этим режимом |
| Wizard worker и сервисы | Автоматические quality-этапы, восстановление, независимое продолжение листов, pacing и Retry-After |
| Wizard интерфейс | Режим проверки, GET-only проверка состояния, раздельные счётчики и версии результатов |
| Wizard rollback | Закрытая совместимость схем 0001/0002/0003 → 0004; проверка DDL/индексов и всех durable-журналов |
| Weblate rollback | Шаблон только nginx/Granian, без Celery и стартовых миграций, с отдельной внутренней сетью |

Подготовленный код Weblate: 142 изменённых файлов;
Wizard: 114. Часть diff визарда обусловлена LF/CRLF,
генерацией OpenAPI и заменой собранного интерфейса.
Полные перечни статусов A/M/D, включая тесты и документацию реализации:

- `docs/operations/reports/2026-10-23-production-transfer-weblate-files.tsv`
- `docs/operations/reports/2026-10-23-production-transfer-wizard-files.tsv`

## Проверки

| Проверка | Результат |
| --- | --- |
| Wizard backend после всех исправлений | 477 passed; одно прежнее предупреждение Starlette |
| Wizard UI | 122 passed в 19 файлах |
| Wizard typecheck и production UI build | PASS |
| Chromium E2E с локальным mock API | 1 passed; кнопка состояния не отправляет POST, verify-only сохраняется |
| Weblate recovery/immutable policy/native Judge/repeats/glossary | 311 passed, 43 subtests |
| Weblate Producer API и native autotranslate | 247 passed, 10 subtests |
| Ruff Wizard | PASS; изменённые rollback-файлы отформатированы |
| Настроенный Ruff Weblate | 114 Python-файлов: lint/format PASS |
| Git diff check | PASS в обеих рабочих ветках |
| Migration check Weblate | No changes detected |
| Восстановление реального PostgreSQL dump | PASS на отдельной локальной БД |
| Миграции restored production Weblate | 6 миграций, до `0152_delivery_retention`, PASS |
| Сохранность всех production-переводов и журналов | До/после одинаковые количества и digest |
| Старый фактический Weblate-код на новой БД | ORM чтение PASS; новые provider admissions: 0 |
| Реальный SQLite backup Wizard | Integrity, 0001 → 0004 и старый код через bridge: PASS |
| Данные Wizard после миграции/bridge | 132 records и состояния всех заданий неизменны |
| Online backup archives | SHA256, gzip CRC и tar: PASS |
| Web-only rollback overlay | Compose config и фактический startup-selector PASS; без Celery/миграций/default network |

Тесты Weblate выполнены в отдельной internal Docker-сети без доступа к провайдерам.
Worker на восстановленных production-данных не запускались. Визард тестировался
с синтетическими данными и mock-транспортами; браузерный тест использовал mock API.
Реальные платные запросы ради проверки не отправлялись.

Weblate проверялся на существующем локальном runtime с установленными тестовыми
инструментами. Полная CI, полный mypy и весь набор upstream-тестов этим прогоном
не объявляются пройденными. Production-образ новой версии не собирался.
Полный контейнерный restart/rollback не выполнялся: проверены чтение старым кодом,
схемы, Compose-конфигурация и выбор startup-программ без запуска старых worker.

## Независимое ревью и исправления

Свежий reviewer проверил объединение, безопасность и откат без доступа к production.
Critical замечаний нет. Два Important замечания устранены: определения новых таблиц
проверяются по закрытым fingerprint, а full rollback сравнивает все durable таблицы,
включая пять `quality_*` журналов. Также закрыт список текущих migration-файлов и
обновлено руководство. Воспроизводящие тесты: 7 RED → GREEN; focused-набор 21 PASS;
после исправлений полный backend Wizard 477 PASS. Повторного reviewer не запускали.

Решения и границы: current migration inventory трактуется как обязательный
fail-closed gate; обновление rollback-руководства входит в требуемый план переноса;
старый код разрешён только для чтения с отключёнными worker/платными маршрутами.
Отложенных замечаний к подготовке нет. Live readiness и согласованность файлов
reviewer не оценивал: они отдельно проверены автором на копиях и оговорены ниже.

Проверены исключаемые пути, известные реальные секреты и высокодостоверные
credential-паттерны. Реальных секретов не обнаружено. Шесть срабатываний в Weblate
разобраны как существующие тестовые fixtures и примеры; их пути/хеши записаны
локально. Это не заявление о выполнении полного стороннего SAST.
Базы, `.env`, ключи, данные пользователей, raw LLM responses и private backups
не внесены в Git, включая импортируемую историю. Новые корпоративные ветки
содержат только историю проверенных снимков и подготовленных изменений.

## Бэкапы и ограничение online-снимка

Закрытая локальная папка:
`D:\Weblate\weblate-HC\.superpowers\production-rollout-20261023\private-backup`.
Наследование ACL отключено; полный доступ только текущему Windows-пользователю
и SYSTEM. Шесть принятых архивов — 1862202662 байта:

- `weblate.dump`: согласованный pg_dump PostgreSQL;
- `weblate-data.tar.gz`: data/VCS/uploads/private response-файлы;
- `weblate-config-root.tar.gz`: защищённая конфигурация Weblate/Compose;
- `weblate-runtime-metadata.tar.gz`: фактический nginx vhost и private inspect трёх работающих контейнеров, включая окружение;
- `weblate-image.tar.gz`: точный текущий образ для отката;
- `wizard-online.tar.gz`: согласованная SQLite-копия, файлы, конфигурация и systemd.

Состав и полные SHA256 — в закрытом `manifest.json`. Ошибочная первоначальная
попытка чтения env без sudo не принята в манифест и не заменяет проверенный архив.
Исходники дополнительно сохранены в Git-снимках и локальных source-архивах.

Службы не останавливались. PostgreSQL dump и SQLite backup согласованы каждый
для своей БД; архив файлов снимался при работающем приложении. Это проверенный
online-бэкап, но не финальный согласованный across-services pre-deploy backup.
Перед будущей установкой потребуется свежая копия на безопасной границе.

## Условия до переноса на production

1. Получить новое явное разрешение на конкретные SHA и действия. Сейчас разрешена
   только подготовка, без merge, deployment и restart.
2. Повторно сверить actual HEAD/dirty/runtime/данные. На Weblate свободно около
   4.7 GiB; после потокового экспорта место вернулось к исходному уровню.
   Не начинать сборку на таком диске. Согласовать отдельный builder либо адресное
   освобождение места; общий `docker system prune` и удаление volumes не выполнялись.
3. Установочный preflight Wizard сейчас должен отклонить старое `unknown` задание.
   Его не возобновлять и не обходить gate. До обслуживания необходимо отдельное
   безопасное решение о сохранении/архивации неопределённой операции без повторной
   оплаты; настоящее поручение это состояние не меняет.
4. По отдельному разрешению закрыть admissions новых платных операций, остановить
   только нужные исполнители на безопасной границе и сделать согласованный backup
   с проверкой restore. Принимать уже отправленный запрос за отменённый нельзя.
5. Сначала установить одобренный Weblate и additive миграции, проверить capabilities;
   затем Wizard/SQLite/UI. До smoke держать оплачиваемые операции отключёнными.
6. Бесплатный smoke: login, проекты/глоссарий/готовые переводы, capabilities,
   состояние, права, выгрузки, verify-only preview. Старые jobs не запускать.
   Проверить исходные digest/receipts. Включение новых заданий — только после
   проверки, сначала на отдельном разрешённом проекте.

## Процедура отката после будущего согласованного переноса

Wizard: штатный `deploy/rollback.sh <verified-backup> code`. Скрипт сохраняет
актуальные данные, проверяет закрытую схему, создаёт отдельный migration bridge
и отключает/маскирует оба worker. Старую БД поверх новых журналов не восстанавливать.
Режим `full` обязан отклоняться при любом изменении durable данных.

Weblate: сохранить текущую БД/data/LLM-journal и остановить допуск новых запросов;
вернуть код/образ с запуском только веб-процессов. Подготовлены
`deploy/rollback-web-only.conf` и `deploy/rollback-web-only.compose.yml`.
Ниже — команды будущего согласованного аварийного отката, не выполненные сейчас:

```sh
# Сначала проверить версию, остановку admissions и актуальный согласованный backup.
# Сохранённый образ нельзя удалять до завершения приёмки.
docker tag sha256:3f0cf69e97d160d2ce7aef3cbb2238bf6380b69dac568721b6530b78a3f0a692 hcgameloc:rollback-20261023
docker network create --internal hcgameloc-rollback-isolated
test "$(docker network inspect hcgameloc-rollback-isolated --format '{{.Internal}}')" = true
docker network connect --alias database hcgameloc-rollback-isolated hcgameloc-database-1
docker network connect --alias cache hcgameloc-rollback-isolated hcgameloc-cache-1
# Из deploy/ с проверенными rollback-шаблонами; только сервис weblate.
docker compose -f docker-compose.yml -f rollback-web-only.compose.yml up -d --no-deps --no-build weblate
docker exec hcgameloc-weblate-1 supervisorctl status
```

Сеть создавать только при её отсутствии; уже существующую обязательно проверить
на `Internal=true` и корректные DB/cache aliases. При отсутствии образа сначала
восстановить его из закрытого `weblate-image.tar.gz` и проверить image ID.
`WEBLATE_SERVICE=web-only` выбирает только nginx/Granian и отключает startup migrate.
Отдельная internal-сеть не даёт старому коду обращаться к LLM-провайдерам, включая
синхронные подсказки вне Celery. Существующая основная сеть не подключается к
rollback Weblate; DB/cache не пересоздаются. Проверить отсутствие всех Celery-
программ и внешнего маршрута до открытия доступа, затем чтение/digest/receipts.
Возврат обычной сети и платных worker требует отдельной проверки spend fences.
Откат кода не отменяет запрос, уже принятый внешним провайдером.
