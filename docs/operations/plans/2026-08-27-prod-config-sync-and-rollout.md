# Синхронизация прод-конфигурации и выкатка слитой работы

Дата: 2026-08-27. Статус: ожидает одобрения.

## Проблема

Прод (`l10n.herocraft.com`, `/srv/hcgameloc`) стоит на коммите `6bfdf40` и
отстал от `origin/main` (`9834f3c`) на 99 коммитов. Деплой сам по себе выкатку
не завершит: файл `/srv/hcgameloc/deploy/.env` разошёлся с
`deploy/environment.example`, и слитый код прочитает не то, что там лежит.
Четыре расхождения, все одного класса - переменные окружения не обновлялись
вместе с кодом.

1. **Судья выключится молча.** В `.env:89` стоит `WEBLATE_JUDGE_OPENROUTER_KEY`.
   Развёрнутый `6bfdf40` читает именно это имя (`settings_docker.py:1694`), а
   `origin/main` читает `WEBLATE_JUDGE_API_KEY` (`settings_docker.py:1693`).
   Фолбэка на старое имя в новом коде нет. Ключ станет пустым,
   `judge_configuration_ready()` вернёт `False`, полоса судьи исчезнет со всех
   проектов - без ошибки в логах. Локально это уже произошло и подтверждено:
   подстановка одного только ключа переводила гейт в `True`.
2. **Условные длины не включатся.** В `.env:60` (`WEBLATE_ADD_CHECK`) нет
   `GameLengthCheck`, `GameMaxLengthCheck`, `GameSourceMaxLengthCheck`.
3. **Штатные проверки длины останутся активными.** Строки
   `WEBLATE_REMOVE_CHECK` в проде нет вообще, то есть
   `weblate.checks.chars.MaxLengthCheck` и
   `weblate.checks.source.SourceMaxLengthCheck` продолжат считать длину без
   разбора условного DSL. Вместе с пунктом 2 это значит, что слитая фича не
   просто не заработает - сохранится прежнее неверное поведение.
4. **LiteLLM не зарегистрирован.** В `.env:59` (`WEBLATE_ADD_MACHINERY`) только
   `RoutedLLMTranslation`.

`WEBLATE_ADD_AUTOFIX` совпадает с эталоном полностью.

## Что проверено и в порядке

Проверено чтением прода, без записи.

- **Миграционного конфликта нет.** На прод-базе применены `0100_llmusagelog`,
  `0101_judge_verdict`, `0102_component_spreadsheet_import_draft`. Ни
  `0103_judge_target_storage_hash`, ни `0104_judge_verdict_target_storage_hash`
  не применялись, и колонки `target_storage_hash` в `trans_judgeverdict` нет.
  Пять миграций (`0103_judge_target_storage_hash`,
  `0103_llmusagelog_batch_size_llmusagelog_outcome`, `0104_llm_usage_operation`,
  `0105_alter_change_action`, `0106_merge_0103_llmusagelog_batch_size`) пройдут
  вперёд. Ранее заявленная мной угроза «двух голов» опровергнута.
- **Review-workflow на проде уже включён** у всех восьми проектов
  (`translation_review = t`, `commit_policy = 20`). Второй гейт судьи
  (`unit.review`) там закрывать не нужно - в отличие от dev, где его пришлось
  включать.
- **Классы существуют** в `origin/main`: `GameLengthCheck`,
  `GameMaxLengthCheck`, `GameSourceMaxLengthCheck`, `RoutedLiteLLMTranslation`.
- **Прод-образ ставит кастомизацию** внутрь `site-packages`
  (`deploy/Dockerfile:47`) и проверяет импорт на этапе сборки
  (`deploy/Dockerfile:55`), поэтому новые имена в `WEBLATE_ADD_CHECK` не уронят
  старт по `ImportError`.
- `6bfdf40` - прямой предок `origin/main`, выкатка идёт fast-forward.

## Решение

### 1. Правка `/srv/hcgameloc/deploy/.env`

Перед правкой - копия рядом (`deploy/.env.bak-2026-08-27`), чтобы откат не
зависел от моей памяти. Правки вносятся `sed` по конкретным именам переменных,
значения не печатаются в вывод.

| Строка | Действие |
|---|---|
| 89 | `WEBLATE_JUDGE_OPENROUTER_KEY=` -> `WEBLATE_JUDGE_API_KEY=`, значение не меняется |
| 60 | в `WEBLATE_ADD_CHECK` добавить `GameLengthCheck`, `GameMaxLengthCheck`, `GameSourceMaxLengthCheck` |
| новая | `WEBLATE_REMOVE_CHECK=weblate.checks.chars.MaxLengthCheck,weblate.checks.source.SourceMaxLengthCheck` |
| 59 | в `WEBLATE_ADD_MACHINERY` добавить `weblate_customization.machinery.RoutedLiteLLMTranslation` |

Итоговые списки берутся из `deploy/environment.example` - он и есть эталон,
расхождение с которым породило проблему.

### 2. Выкатка

`./deploy/vps.sh deploy` из чистой рабочей копии на `main` = `origin/main` =
`9834f3c`. Скрипт пушит HEAD, пересобирает образ и ждёт здоровья; `migrate` и
`collectstatic` выполняет entrypoint при каждом старте, отдельного вызова
миграций не требуется.

### 3. Пересчёт проверок

Новые проверки длины не появятся на уже существующих строках сами: штатный
`daily_update_checks` (`weblate/trans/tasks.py:1206-1220`) при
`BACKGROUND_TASKS = "monthly"` (дефолт, в проде переменная не задана) берёт
компонент раз в месяц - при совпадении `id % 30` с числом и `id % 24` с часом.
То есть без ручного шага фича будет «включена», но невидима недели.

Пересчёт делается `weblate updatechecks <project/component>` покомпонентно, а
не `--all`: команда прогоняет `run_checks()` по каждому юниту, это тяжёлая
операция на живом инстансе. Порядок: сначала один компонент с реальными
бюджетами длины, замер времени, потом остальные по одному.

### 4. LiteLLM

Только регистрация класса (пункт 1). Конфигурация не создаётся: строки
`Setting(category=2, name='litellm')` в БД нет, сервис будет виден в
`/manage/machinery/` как ненастроенный и не будет использоваться. Это
осознанное состояние, а не недоделка - ключа для него нет.

## Вне охвата

- **Настройка LiteLLM** - по решению владельца, позже.
- **Ротация ключа OpenRouter.** Старый ключ попал в расшифровку сессии и
  сейчас работает на проде. Выпустить новый может только владелец аккаунта
  OpenRouter - у меня нет ни доступа, ни provisioning-ключа. Пока ключ не
  сменён, деплой сохраняет текущий (скомпрометированный) ключ: это
  зафиксированный принятый риск, а не забытый пункт.
- **LLM-анализ профиля loc-kit** (`LOC_KIT_PROFILE_ANALYSIS_ENABLED`) - на
  проде остаётся выключенным, как и в dev; это отдельное решение об исходящей
  передаче загруженных таблиц.

## Риски и откат

- **Отката одной командой нет.** В `deploy/vps.sh` нет подкоманды `rollback`:
  сценарий возврата - `git -C /srv/hcgameloc checkout 6bfdf40` и пересборка.
  Обратных миграций для пяти новых нет, но все пять - аддитивные (новые поля и
  таблицы), поэтому старый код на новой схеме поднимается: лишние колонки его
  не ломают.
- **Простой.** Пересборка образа плюс рестарт; здоровье проверяет сам скрипт по
  логину, отдающему 200. Ожидаемо минуты, но это живой инстанс с работающими
  переводчиками.
- **Снятие штатных проверок длины** обнулит существующие срабатывания
  `max-length` до пересчёта (пункт 3). Данные строк не затрагиваются.

## Проверка

1. `./deploy/vps.sh deploy` завершается `DEPLOY-OK`, страница логина отдаёт 200.
2. `git -C /srv/hcgameloc rev-parse HEAD` = `9834f3c`.
3. `SELECT name FROM django_migrations WHERE app='trans' AND name LIKE '010%'`
   содержит все пять новых записей.
4. В контейнере: `judge_configuration_ready()` = `True`; `CHECK_LIST` содержит
   `GameLengthCheck`, `GameMaxLengthCheck`, `GameSourceMaxLengthCheck` и **не**
   содержит штатных `MaxLengthCheck`/`SourceMaxLengthCheck`; в списке машинерии
   присутствует `litellm` со статусом «не настроен».
5. В браузере на странице прод-проекта видна полоса `AI judge`, обе ссылки
   отдают 200.
6. После пункта 3 плана - на выбранном компоненте появляются срабатывания
   `max-length`, посчитанные с разбором условного DSL.
