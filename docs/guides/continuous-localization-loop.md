# Непрерывная локализация: полный цикл git → Weblate → git

Инструкция по ролям для цикла: разработчик добавил строку в git → она появилась в
Weblate → переводчик перевёл → перевод сам вернулся в git.

Проверено живьём на дев-стенде (проект `pirate-ships`, компонент `localization`):

```text
3f79cb5 chore(l10n): update Russian translation   <- коммит Weblate (автопуш)
c50f83a feat(loc): add cannon_reload_hint string  <- коммит разработчика
```

Источники: `docs/admin/continuous.rst` (этой версии), `docs/devel/integration.rst`,
<https://docs.weblate.org/en/latest/admin/code-hosting.html>, код
`weblate/trans/models/component.py`, `weblate/trans/views/hooks.py`,
`weblate/addons/`.

## Схема цикла

```text
Разработчик                  Weblate                         Переводчик
    |                           |                                |
    | 1. новый ключ в en.json   |                                |
    |    git commit + push      |                                |
    |-------------------------->| 2. webhook /hooks/github/      |
    |        (origin)           |    -> pull + re-parse          |
    |                           | 3. новая строка = untranslated |
    |                           |    во всех языках              |
    |                           |    (+ email-уведомление,       |
    |                           |     + опц. автоперевод MT)     |
    |                           |<-------------------------------| 4. переводит в UI,
    |                           |                                |    Save
    |                           | 5. lazy commit                 |
    |                           |    (commit_pending_age, 24ч    |
    |                           |     по умолч., или вручную)    |
    |<--------------------------| 6. push_on_commit=True         |
    | git pull: перевод в гите  |    -> push в origin (или PR)   |
```

Ключевой принцип: **git — источник истины для исходных строк (шаблона), Weblate —
источник истины для переводов**. Разработчики не редактируют файлы переводов руками,
переводчики не трогают шаблон.

---

## Роль: администратор Weblate (одноразовая настройка)

### 1. Формат файлов

Для игровых строк используем монолингвальный key-value формат (JSON/CSV/XLSX):

- один файл на язык: `loc/en.json`, `loc/ru.json`, …;
- `en` — шаблон (template): **только** его редактируют разработчики;
- ключ записи (`"cannon_reload_hint"`) в Weblate становится полем `context` —
  это идентификатор строки, по нему матчатся все языки. Отдельной колонки
  «игровой Id» Weblate не хранит: лишние колонки отбрасываются при импорте
  (если Id нужен в каждой выгрузке — класть его в `location` при импорте,
  либо сшивать по ключу на выходе).

Первичный сев таких компонентов из лок-кита (CSV/TSV/XLSX) делает
`loc_kit_ingest` (см. `docs/guides/loc-kit-ingest.md`): для строковых
компонентов он выдаёт монолингвальный PO, где игровой Id - это ключ
(`context`), колонка персонажа - developer comment, числовые колонки -
`location`.

### 2. Компонент: поля, которые определяют цикл

UI: `Управление → Настройки` компонента, или API `PATCH /api/components/<proj>/<comp>/`.

| Поле | Значение | Смысл |
|---|---|---|
| `repo` | `https://github.com/org/game.git` | откуда Weblate тянет (`https` + токен или SSH) |
| `push` | `git@github.com:org/game.git` | куда пушит. **Пустое = push выключен** (частая причина «ничего не уезжает») |
| `push_branch` | пусто или `weblate-l10n` | пусто = пуш в ту же ветку; имя = отдельная ветка под PR |
| `branch` | `main` | рабочая ветка |
| `filemask` | `loc/*.json` | маска файлов переводов |
| `template` | `loc/en.json` | шаблон (монолингвальный режим) |
| `new_base` | `loc/en.json` | база для новых языков |
| `new_lang` | `add` | переводчики могут добавлять языки из UI |
| `push_on_commit` | `True` (дефолт) | пушить сразу после каждого коммита Weblate |
| `commit_pending_age` | `24` ч (дефолт) | «ленивый коммит»: сколько держать правки незакоммиченными |
| `merge_style` | `rebase` (дефолт) | как интегрировать upstream-изменения |

### 3. Доступ на запись (варианты push)

| Схема | VCS компонента | `push` | Результат |
|---|---|---|---|
| Прямой push | `Git` | SSH URL | Weblate коммитит прямо в ветку |
| PR-режим | `GitHub` (`GitLab`, `Gitea`, …) | пусто или SSH | Weblate открывает Pull Request; нужен `GITHUB_CREDENTIALS` в настройках инстанса (токен: contents rw + pull requests) |
| Только чтение | `Git` | пусто | переводы забирают вручную/скриптом |

Для прямого push: сгенерировать SSH-ключ Weblate (`Управление → SSH keys` в админке),
добавить его deploy key с write-доступом в репозиторий. Для protected branch —
либо PR-режим, либо исключение для пользователя Weblate.

### 4. Автообновление из git (inbound)

1. **Webhook (рекомендуется)**: в репозитории GitHub → Settings → Webhooks →
   Payload URL `https://<weblate>/hooks/github/`, событие push. Поддерживаются
   также `/hooks/gitlab/`, `/hooks/bitbucket/`, `/hooks/gitea/`, `/hooks/forgejo/`,
   `/hooks/pagure/`, `/hooks/azure/`, `/hooks/gitee/`.
   В настройках **проекта** должно быть включено `Enable hooks`.
2. Альтернативы: `AUTO_UPDATE` (ночной pull всех компонентов; по умолчанию Weblate
   ночью делает только fetch), ручное `Управление → Обслуживание репозитория →
   Обновить`, API `POST .../repository/ {"operation":"pull"}`, `wlc pull`.

### 5. Аддоны (Управление → Аддоны компонента)

| Аддон | Зачем |
|---|---|
| `weblate.autotranslate.autotranslate` | автоперевод **новых** строк при обновлении из репо (событие component update); режим `suggest` — как предложения, `translate` — сразу перевод. Использует настроенный MT-движок (у нас OpenAI-бэкенд → OpenRouter/Gemini) |
| `weblate.git.squash` | схлопывать исходящие коммиты Weblate (per-language/author/file) |
| `weblate.json.customize` | **важно для JSON**: задать отступ/сортировку как в репо, иначе первый коммит Weblate переформатирует весь файл (у нас diff был 3736 строк вместо 1) |
| `weblate.cleanup.generic` | убирать из переводов ключи, удалённые из шаблона |

### 6. Машинный перевод

`Управление (инстанса) → Machine Translation → Add → OpenAI`: API key, base URL
(можно OpenRouter `https://openrouter.ai/api/v1`), модель, persona/style под игру.
Он же используется кнопкой «Автоматический перевод» и аддоном.

### 7. Права

Переводчикам — роль `Translate` в проекте (или `Review` при включённом ревью).
Коммит/пуш/обслуживание репозитория — группа `Managers`/админ.

---

## Роль: разработчик

1. **Новая строка = новый ключ в шаблоне** (`loc/en.json`), в той же ветке, что и код:

   ```json
   "cannon_reload_hint": "Reload the cannons before boarding!"
   ```

   Файлы других языков трогать не надо — Weblate допишет их сам.
2. `git commit && git push` — дальше всё автоматически (webhook).
3. **Не редактировать файлы переводов (`ru.json`, `es.json`, …) руками.**
   Одновременная правка одного файла в git и в Weblate = merge-конфликт.
   Если очень нужно (массовый рефакторинг ключей и т.п.) — процедура из
   `docs/admin/continuous.rst` («Avoiding merge conflicts»):

   ```sh
   wlc lock            # заблокировать перевод в Weblate
   wlc commit && wlc push   # выдавить pending-правки Weblate в git
   # ... правите файлы, git push ...
   wlc pull            # затянуть в Weblate
   wlc unlock
   ```

4. Переименование ключа = удаление + добавление: переводы старого ключа пропадут
   (останутся в Translation Memory). Планировать заранее.
5. Забрать свежие переводы: обычный `git pull` (при `push_on_commit` они уже в
   origin; при PR-режиме — смержить PR от Weblate).

## Роль: переводчик

1. **Узнать о новых строках**: e-mail уведомление «New strings to translate»
   (Настройки профиля → Уведомления → подписаться на проект), или дашборд —
   у компонента появляется счётчик «Непереведённые».
2. Открыть список: компонент → язык → фильтр `Непереведённые строки`
   (поиск: `state:empty`).
3. Перевести в редакторе. Обращать внимание на **проверки** (checks): плейсхолдеры
   `{value}`, теги `<color=...>`, пунктуация. Строка с проваленной проверкой
   помечается — исправить или явно проигнорировать.
4. Если настроен автоперевод в режиме `suggest` — внизу редактора будут
   предложения MT: принять (`Ctrl+1..9`) или поправить.
5. `Сохранить` (`Alt+Enter`). Всё. Коммит/пуш в git — забота Weblate.
6. Офлайн-вариант: `Файлы → Скачать перевод`, перевести локально,
   `Файлы → Отправить перевод` (upload). Форматы: родной файл, CSV, XLIFF.

## Роль: релиз-менеджер / продюсер

- Прогресс: страница проекта (`/projects/<slug>/`) или `Insights → Статистика`;
  API `/api/components/<p>/<c>/statistics/`.
- Перед релизом убедиться, что pending-правки закоммичены и запушены:
  `Управление → Обслуживание репозитория → Commit / Push` (или
  `wlc commit && wlc push`). Условия автокоммита (lazy commit): правка старше
  `commit_pending_age`, чужая правка той же строки, merge из upstream, явный
  Commit, запрос на скачивание файла.
- Выгрузки для движка/билда: ZIP всех языков — страница компонента →
  `Файлы → Скачать файлы перевода в виде ZIP-архива`
  (`/download/<proj>/<comp>/?format=zip`).

---

## Наш дев-стенд (localhost:3001): конкретика

- Инстанс: `http://localhost:3001/` (`admin`/`admin`), API-токен — в
  `weblate-mcp/.env` (`WEBLATE_API_TOKEN`).
- Репозитории — локальные пути вида `/app/data/<repo>` (маунт
  `dev-docker/data/` → `/app/data`). Webhook'ов нет, поэтому pull дёргаем API.
- Чтобы Weblate мог пушить в такой checked-out репозиторий:

  ```sh
  cd dev-docker/data/<repo>
  git config receive.denyCurrentBranch updateInstead
  ```

- Полный цикл руками (то, что в проде делают webhook и lazy commit):

  ```sh
  TOKEN=<api token>
  API=http://localhost:3001/api
  C=$API/components/<project>/<component>/repository/

  # 1. разработчик закоммитил новый ключ в шаблон
  # 2. затянуть в Weblate (в проде — webhook)
  curl -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
       -X POST $C -d '{"operation":"pull"}'
  # 3. перевод в UI (или API PATCH /api/units/<id>/)
  # 4. выдавить в git (в проде — lazy commit + push_on_commit)
  curl -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
       -X POST $C -d '{"operation":"commit"}'
  curl -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
       -X POST $C -d '{"operation":"push"}'
  ```

- Автоперевод новых строк тем же движком, что настроен в инстансе
  (OpenAI-бэкенд → OpenRouter): кнопкой `Операции → Автоматический перевод`
  или аддоном `autotranslate` (см. выше).

## Траблшутинг

| Симптом | Причина / решение |
|---|---|
| Переводы не появляются в git | `push` пуст (push выключен) или нет прав; проверить `needs_push` в `GET .../repository/`, права deploy key |
| Weblate не видит новые ключи | Webhook не настроен / `Enable hooks` выключен в проекте; вручную `operation:pull` |
| Первый коммит Weblate переписал весь JSON | Разное форматирование; аддон `Customize JSON output` (отступ, сортировка) |
| Merge-конфликт | Файлы правили и в git, и в Weblate. `Обслуживание репозитория → Reset and reapply` (сохранит pending-переводы). Не использовать squash-merge для коммитов Weblate |
| LLM-автоперевод: `Mismatching assistant reply` / `Could not parse ... JSON` | Разовые сбои модели на батче; просто повторить автоперевод — он идемпотентен (`state:<translated`) |
| Push отклонён (protected branch) | PR-режим (VCS = GitHub) или исключение для пользователя Weblate |
| В выгрузке нет игрового Id | Weblate его не хранит (см. «Формат файлов»); Id сшивается по ключу из исходника либо кладётся в `location` при импорте |
