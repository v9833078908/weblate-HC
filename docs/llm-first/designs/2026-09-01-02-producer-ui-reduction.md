# LLM-first producer mode: отключение избыточного функционала UI

Дата: 2026-09-01. Статус: дизайн-предложение; P0-часть и реестровые тримы
(formats, machinery) применены на dev-инстансе в тот же день (см. «Применение»),
P1/P2 не реализованы.

Продолжение `docs/llm-first/vision/2026-08-15-producer-first-product-research.md`
(раздел 6 «Что скрыть или сделать неактивным»). Документ проверяет предложения
против текущего кода, помечает источник каждого факта и фиксирует, что
выключается флагом, что режимом, а что не выключается.

Маркировка источников: `[код]` - статический аудит шаблонов и настроек
(подтверждает доступность поверхности при соответствующих правах, а не то, что
её видит конкретный пользователь); `[live]` - read-only замер REST API
локального dev-инстанса `localhost:3001` (не production); `[документ]` -
утверждение из документации, не проверенное вживую.

## Базовые факты

`[live]` На `localhost:3001` 7 проектов: space-arena, need-for-greed,
pirate-ships, heart-abyss, col4, test, judge-repair-probe. У всех пяти
рабочих `translation_review=true`, `source_review=false`. Это локальный набор;
про документный список из восьми проектов на проде см. vision-документ
(обновление 2026-08-22) - `[документ]`, живьём не проверено.

`[код]` Почти все пункты меню - условные по правам
(`{% perm %}`/`user_can_*` в `project.html`, `component.html`,
`translation.html`), поэтому для single-tenant установки главный рычаг -
права роли продюсера, а не шаблонные флаги.

`[код]` `OFFER_HOSTING` по умолчанию `False` (`weblate/trans/defaults.py:11`) -
hosting/billing пункты отсутствуют по умолчанию. `ENABLE_SHARING` и
`REGISTRATION_OPEN` по умолчанию `True` (`weblate/trans/defaults.py:13`,
`weblate/accounts/defaults.py:11`); с 2026-09-01 оба переопределены в
`dev-docker/docker-compose.yml` значением 0 и применены в работающем
контейнере - `[live]`, см. «Применение P0».

`[код]` `suggestion_voting` и `suggestion_autoaccept` по умолчанию выключены
(`weblate/trans/models/component.py:780-788`); autoaccept требует voting
(валидация `component.py:5552`). Форма автоперевода уже централизована на
routed-движках через `ROUTED_ENGINES = ("openrouter", "litellm")` и
`configured_routed_engine` (`weblate/trans/forms.py`).

`[код]` Judge-карточка рендерится в редакторе
(`weblate/templates/translate.html:601`,
`weblate/templates/snippets/judge-verdict.html`), режим judge присутствует в
форме автоперевода. Что видно в браузере на живом инстансе, не проверялось.

## Что выключить сразу (P0)

| Функция | Механизм | Источник и обоснование |
|---|---|---|
| Suggestion voting | настройки компонента (`suggestion_voting=false`, дефолт; `[live]` на dev-инстансе 0 из 21 компонента включен) | `[код]` Механизм голосов людей; в архитектуре (часть 2) записано «рассчитан на голоса людей, не на скоры машин», в части 6 явно отвергнут как gate |
| Suggestion autoaccept | `suggestion_autoaccept=0` (дефолт, невалиден без voting; `[live]` отклонений нет) | `[код]` Порог голосов несовместим с вердикт-контуром судьи |
| Открытая регистрация | `WEBLATE_REGISTRATION_OPEN=0` в compose - применено | `[live]` `settings.REGISTRATION_OPEN=False`; `/accounts/register/` отдаёт «registrations are turned off on this site», формы нет |
| Community/sharing меню | `WEBLATE_ENABLE_SHARING=0` в compose - применено | `[live]` `settings.ENABLE_SHARING=False`; share-меню исчезло со страниц проекта (гейт `{% if enable_sharing %}`, `snippets/share-menu.html:3`) |
| Add-on Automatic translation с дефолтом `auto_source=others` | не устанавливать (не устанавлен) | `[код]`/архитектура: addon без судьи наливает очередь state 20, которую некому разгребать; P1 ресеча отложен до фазы 3 |

Особенность: `Suggestion`-механизм целиком не выключается. План
`docs/llm-first/plans/2026-09-01-judge-producer-triage-embed.md` хранит
repair-кандидаты судьи как нативные `Suggestion`. Выключаются только
voting/autoaccept.

## Что скрыть в Producer mode (P1)

Код не удаляется; функции убираются из основного сценария через права роли
продюсера и режимный рендеринг. Основание - раздел 6 P1 producer-first
документа, сверенное с кодом.

| Функция | Что делать | Обоснование |
|---|---|---|
| Вкладка Automatic suggestions (machinery) в редакторе | скрыть в Producer mode, показывать только в Advanced | `[код]` Ленивый ручной вызов LLM (путь A) - инструмент переводчика; продюсер не переводит, а двойной платный вызов рядом с judge-режимом не нужен |
| Translation memory в Operations | убрать из навигации продюсера | `[код]` Повторы закрывает кэш machinery и полный глоссарий (<=300) в промпте; TM остается backend-fallback. P1 документа |
| Десятки MT-провайдеров в настройках проекта | ~~показывать только routed-движки~~ - применено env-тримом | `[код]` `ROUTED_ENGINES`/`configured_routed_engine` выбирают один движок проектом; остальные дефолтные классы удалены из реестра `WEBLATE_REMOVE_MACHINERY` (см. «Применение»), в реестре 4 сервиса |
| Search and replace, Bulk edit | скрыть | `[код]` Массовая правка текста человеком, который не читает целевой язык, - прямой риск; инструменты Advanced |
| Translator reports (Insights) | скрыть | `[код]` Отчет по вкладу переводчиков без переводчиков - пустой артефакт; место стоимости - LLM usage report (админ) |
| Вкладки Similar keys, Variants, Other occurrences, Other languages | скрыть для продюсера | `[код]` Переводческая навигация; контекст соседей продюсеру дает судья (плечо H2), back-translation - в карточке. P2 документа |
| Screenshots-карточка | скрыть | `[документ]` Архитектура часть 6: контекст уже идет из loc-kit (note/explanation/глоссарий) |
| Announcements, widgets, Data exports, RSS | скрыть | `[код]` Community/hosting обвеска внутреннего инстанса |
| Comments как рабочий канал | скрыть из основного процесса | `[код]`/judge-native design: судья комментарии больше не пишет («комментарий не пишется»), оставить только человеческий аудит-канал |

## Что не выключать

| Функция | Почему |
|---|---|
| `translation_review=True` | `[live]` включен на всех пяти рабочих проектах dev-инстанса; это предусловие state 30 от судьи-бота. Меняется потребитель очереди (`judge:exhausted` вместо `unapproved`), не флаг |
| Suggestion-механизм целиком | см. P0 - хранилище repair-кандидатов |
| Zen-режим, фильтры `judge:*`, Failing checks | `[код]`/judge-native design: «Zen-режим и есть очередь триажа»; детерминированные чеки - единственный блокирующий сигнал |
| Glossary | `[код]` Главный рычаг терминов (producer-first документ, шаг 6) |
| History | Аудит trail; молчаливых переходов нет (инвариант 4.1.8) |
| ZIP download файлов | `[документ]` Шаг 11 producer guide - забор переводов в игру; замена на Create release package - P2 |

## Применение (2026-09-01, dev-инстанс)

`WEBLATE_REGISTRATION_OPEN: 0` и `WEBLATE_ENABLE_SHARING: 0` добавлены в
`dev-docker/docker-compose.yml` (commit `c326243`); контейнер пересоздан
`WEBLATE_PORT=3001 WEBLATE_HOST=localhost:3001 docker compose up -d weblate`
(env вшивается при создании контейнера). Проверено на живом инстансе
`[live]`:

- `settings.REGISTRATION_OPEN=False`, `settings.ENABLE_SHARING=False`
  (`manage.py shell` в контейнере);
- `/accounts/register/` отвечает «registrations are turned off on this site»,
  формы регистрации нет;
- Community/share-меню отсутствует на страницах проекта;
- `Component.objects.filter(suggestion_voting=True).count() == 0` и
  `Component.objects.exclude(suggestion_autoaccept=0).count() == 0` по всем
  21 компонентам - переключений не требовалось.

Инстанс при этом остался на порту 3001 с корректным `WEBLATE_SITE_DOMAIN`
(`web_url` проекта col4 = `http://localhost:3001/projects/col4/`), API отвечает,
health 200. Add-on Automatic translation не установлен ни на одном компоненте
(`Addon.objects.all()` пуст - проверено `manage.py shell` в контейнере).

### Реестровые тримы (парето: один механизм - несколько env-списков)

`WEBLATE_REMOVE_FORMATS` и `WEBLATE_REMOVE_MACHINERY` добавлены в
`dev-docker/docker-compose.yml` рядом с уже используемым `WEBLATE_REMOVE_CHECK`
(тот же `modify_env_list`, exact class path, код не меняется). Контейнер
пересоздан; проверка на живом инстансе `[live]`:

- форматов в реестре было 69, стало 14: `po`, `po-mono`, `tbx`, `xlsx`, `json`,
  `xliff`(plainxliff), `csv`, `txt`, `yaml`, `properties`, `resx`, `strings`,
  `aresource`, `ts` - пять используемых компонентом формата плюс ходовые;
  все используемые форматы компонент остались (`used-ok: True`), страницы
  проектов, компонент и zen-режим открываются с 200;
- сервисов в реестре machinery: ровно 4 - `openrouter`, `litellm`, `weblate`
  (внутренний TM), `weblate-translation-memory` (память переводов); site-wide
  MT-настройки при этом ссылаются только на `weblate`,
  `weblate-translation-memory` и `openrouter`, проектные настройки col4 -
  `litellm` и `openrouter`, выпадение сконфигурированного сервиса не
  произошло.

## Открытые пункты

1. Перед скрытием любого P1-пункта - telemetry-аудит: producer-first документ
   (раздел 9) запрещает считать доказанным, что legacy-функции не
   используются.
2. Скрытие - через права роли и режимный рендеринг, не удаление кода: раздел
   5 producer-first документа требует сохранить аварийный Advanced mode.
3. Оценки прод-инстанса (`l10n.herocraft.com`) этот документ не содержит: любые
   действия против него требуют отдельного одобрения (AGENTS.md, working
   agreement).
