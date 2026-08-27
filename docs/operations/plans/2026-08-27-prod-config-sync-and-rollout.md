# Синхронизация прод-конфигурации и выкатка слитой работы

Дата: 2026-08-27. Статус: одобрен, исполняется в отдельной сессии.

Решение владельца от 2026-08-27: новый ключ OpenRouter не выпускается и не
добавляется. Прод сохраняет текущий ключ, LiteLLM регистрируется без
конфигурации. План написан так, чтобы его выполнила другая сессия по шагам
ниже, без повторного исследования.

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

Скрипт правки. Пишется локально в `deploy/fix-env.sh`, выполняется на VPS от
root; значения ключей он не печатает - сравнение идёт по именам переменных.

```sh
set -e
cd /srv/hcgameloc/deploy
cp -p .env .env.bak-2026-08-27

sed -i 's/^WEBLATE_JUDGE_OPENROUTER_KEY=/WEBLATE_JUDGE_API_KEY=/' .env

sed -i '/^WEBLATE_ADD_MACHINERY=/c\
WEBLATE_ADD_MACHINERY=weblate_customization.machinery.RoutedLLMTranslation,weblate_customization.machinery.RoutedLiteLLMTranslation' .env

sed -i '/^WEBLATE_ADD_CHECK=/c\
WEBLATE_ADD_CHECK=weblate_customization.checks.GameMarkupCheck,weblate_customization.checks.GameLineBreakCheck,weblate_customization.checks.CyrillicLeakCheck,weblate_customization.checks.GameNumberCheck,weblate_customization.checks.GameTokenCheck,weblate_customization.checks.GameLengthCheck,weblate_customization.checks.GameMaxLengthCheck,weblate_customization.checks.GameSourceMaxLengthCheck' .env

grep -q '^WEBLATE_REMOVE_CHECK=' .env || sed -i '/^WEBLATE_ADD_CHECK=/a\
WEBLATE_REMOVE_CHECK=weblate.checks.chars.MaxLengthCheck,weblate.checks.source.SourceMaxLengthCheck' .env

# контроль: только имена переменных, без значений
diff <(sed -E 's/=.*//' .env.bak-2026-08-27) <(sed -E 's/=.*//' .env) || true
```

Доставка на сервер (шлюз должен быть поднят, `./deploy/vps.sh status`):

```sh
cd deploy && . ./.env.local
B64=$(base64 < fix-env.sh | tr -d '\n')
./vps.sh ssh "echo $B64 | base64 -d > /tmp/fix-env.sh; \
  echo '$VPS_PASSWORD' | sudo -S bash /tmp/fix-env.sh; rm -f /tmp/fix-env.sh"
```

`docker cp` в `hcgameloc-weblate-1` не использовать: это запись в работающий
прод-контейнер. Всё, что нужно прочитать, читается через `weblate shell -c` и
`psql` без переноса файлов.

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
операция на живом инстансе.

Радиус мал и известен точно. На прод-данных бюджеты длины есть ровно у одного
компонента - `strategy-and-tactics-2/summer-update` (флаг `max-length` на
уровне компонента); юнитов с собственным `max-length` во всей базе нет. То же
ограничивает и последствия снятия штатных проверок из пункта 1: обнулиться и
пересчитаться может только этот компонент.

```sh
docker exec hcgameloc-weblate-1 \
  time weblate updatechecks strategy-and-tactics-2/summer-update
```

Остальные компоненты пересчитывать не требуется: без флагов длины ни одна из
трёх новых проверок на них не срабатывает.

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
6. Пересчёт из пункта 3 отработал: команда завершилась без ошибки, а состав
   сработок на `strategy-and-tactics-2/summer-update` сверен с базовой
   цифрой, снятой до деплоя (см. ниже).
7. Поведение подтверждено на условной строке: при бюджете 40 проверка
   `max-length` молчит, потому что DSL-длина 36, а не 53 (см. «Поведенческая
   проверка условного DSL»). Это единственный пункт, который отличает игровую
   реализацию от штатной по результату, а не по регистрации.

Пункт 4 одной командой:

```sh
docker exec hcgameloc-weblate-1 weblate shell -c '
from django.conf import settings
from weblate.trans import judge
from weblate.machinery.models import MACHINERY
from weblate.checks.models import CHECKS
print("judge_ready:", judge.judge_configuration_ready())
print("length checks:", [c.split(".")[-1] for c in settings.CHECK_LIST if "ength" in c])
print("max-length class:", type(CHECKS["max-length"]).__name__)
print("source-max-length class:", type(CHECKS["source-max-length"]).__name__)
print("litellm registered:", "litellm" in MACHINERY)
'
```

`judge_ready` должен быть `True`, в списке проверок - три класса `Game*`, без
штатных `MaxLengthCheck` и `SourceMaxLengthCheck`. Классы под идентификаторами
`max-length` и `source-max-length` должны быть `GameMaxLengthCheck` и
`GameSourceMaxLengthCheck`: `GameMaxLengthCheck` наследует штатный
`MaxLengthCheck` и **не переопределяет** `check_id`, поэтому подмена видна
только по классу, а не по имени проверки.

Снипет проверен 2026-08-27 на dev-стеке, где уже стоит код `origin/main` с той
же регистрацией - то есть на эквиваленте пост-деплойного состояния. Ожидаемый
вывод на проде дословно такой, за исключением `litellm registered`, который
останется `True` (класс регистрируется) при отсутствующей конфигурации:

```text
judge_ready: True
length checks: ['GameLengthCheck', 'GameMaxLengthCheck', 'GameSourceMaxLengthCheck']
max-length class: GameMaxLengthCheck
source-max-length class: GameSourceMaxLengthCheck
litellm registered: True
```

### Базовая цифра для пункта 6

Снято на проде 2026-08-27, до деплоя, компонент
`strategy-and-tactics-2/summer-update`: 254 юнита, сработки -
`game-markup` = 6, и **ни одной** строки `max-length`, `source-max-length`
или `game-length`.

```sh
docker exec -i hcgameloc-database-1 psql -U weblate -d weblate -At -F"|" <<'SQL'
SELECT ch.name, count(*)
FROM checks_check ch
JOIN trans_unit u ON ch.unit_id = u.id
JOIN trans_translation t ON u.translation_id = t.id
JOIN trans_component c ON t.component_id = c.id
JOIN trans_project p ON c.project_id = p.id
WHERE p.slug = 'strategy-and-tactics-2' AND c.slug = 'summer-update'
GROUP BY 1 ORDER BY 2 DESC;
SQL
```

Ожидание после пересчёта: `game-markup` остаётся `6` - изменения не должны
выходить за проверки длины. Ноль новых сработок `max-length` - допустимый
результат: он означает, что ни одна строка не выходит за бюджет.

Этот запрос показывает **радиус изменений**, а не подмену реализации: строки
`max-length` в БД одинаковы для штатного и игрового класса, поэтому по ним
нельзя отличить старую реализацию от новой. Живость фичи доказывают два
следующих пункта - регистрация и поведение.

### Поведенческая проверка условного DSL

Считает то же, что и проверка длины на реальной строке, но без БД и без записи:
объект-заглушка несёт только флаги. Строка - `TIMER` из
`weblate_customization/tests/test_checks.py:33`.

```sh
docker exec hcgameloc-weblate-1 weblate shell -c '
from weblate.checks.flags import Flags
from weblate.checks.models import CHECKS
TIMER = "{hours:cond:>0?{hours:00}:|}{minutes:00}:{seconds:00}"
class Stub:
    all_flags = Flags("max-length:40")
check = CHECKS["max-length"]
print("class      :", type(check).__name__)
print("raw len    :", len(TIMER))
print("dsl len    :", len(check.get_replacement_function(Stub())(TIMER)))
print("fires at 40:", check.check_target_params([TIMER], [TIMER], Stub(), 40))
'
```

Проверено 2026-08-27 на dev-стеке с кодом `origin/main`; ожидаемый вывод:

```text
class      : GameMaxLengthCheck
raw len    : 53
dsl len    : 36
fires at 40: False
```

Смысл: при бюджете 40 наивная длина 53 дала бы срабатывание, а длина с разбором
условного DSL - 36, поэтому проверка молчит. Со штатным `MaxLengthCheck`
последняя строка была бы `True`. Это и есть отличие реализаций, наблюдаемое
напрямую.
