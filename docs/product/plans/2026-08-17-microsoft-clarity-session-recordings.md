# Microsoft Clarity: записи сессий — план реализации

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** включаемая одной настройкой интеграция Microsoft Clarity (записи сессий, тепловые карты, клики) по существующему паттерну Matomo/Google Analytics; по умолчанию выключена.

**Architecture:** настройка `CLARITY_PROJECT_ID` (`None` = выключено) доезжает до шаблона через контекст-процессор; блок в `footer.html` подключает внешний загрузчик `static/js/clarity.js`, который через `data-*`-атрибуты получает конфигурацию и ставит официальный тег Clarity; CSP расширяется **только** при заданной настройке. Inline-JS не используется — в `script-src` форка нет `'unsafe-inline'`.

**Tech Stack:** Django settings + django-appconf, Django templates, ванильный JS без сборки (в корне нет `package.json`), pytest через Django test client, Sphinx/reStructuredText для доков.

---

## Ограничение провайдера — прочитать до начала работы

**Clarity никогда не записывает содержимое `input`, `select` и `textarea` — ни в одном режиме, ни одним атрибутом.** Это не настройка и не обходится кодом.

Проверено по исходникам (`clarity-js` 0.8.68 с npm; живой тег отдаёт 0.8.69):

- `packages/clarity-js/src/layout/constants.ts:10` — `MaskTagsList = ["INPUT", "SELECT", "TEXTAREA"]`;
- `packages/clarity-js/src/layout/dom.ts:239-252` — `maskTags.includes(tag)` проверяется **раньше** ветки `Constant.UnmaskData in attributes`, поэтому `data-clarity-unmask="true"` до этих тегов не доходит;
- `dom.ts:23,51` — `maskTags` присваивается один раз из константы, переопределить нечем.

Официально: FAQ, «Can I unmask input text boxes?» → «Content in the input boxes is masked in all modes and can't be customized».

Для Weblate это значит: редактор перевода — `PluralTextarea(forms.Textarea)` (`weblate/trans/forms.py:393`), то есть **набираемый переводчиком текст в записи будет замаскирован**. Исходная строка маскирована не будет (рендерится разметкой, а не полем ввода). Видны навигация, клики, скроллинг, глоссарий, проверки.

Поэтому план **не добавляет** ни `data-clarity-mask`, ни `data-clarity-unmask`, ни списков селекторов: маскирование остаётся тем, что Clarity навязывает сам. Режим `Relaxed` («No content is masked») переключается только в дашборде проекта, применяется к новым записям, с задержкой до часа.

## Правки, внесённые по итогам ревью

| № | Что изменено против первой версии плана | Основание (проверено) |
|---|---|---|
| 1 | Якорь `docs/admin/config.rst`: секция вставляется **перед `.. setting:: GOOGLE_ANALYTICS_ID` (строка 1206)**, а не между `CHECK_LIST` (485) и `COMMIT_PENDING_HOURS` (531) | `grep -n "^\.\. setting:: "`: GA на 1206, `MATOMO_SITE_ID` на 1730; файл не алфавитный, настройки сгруппированы тематически |
| 2 | `docs/changes.rst`: целевая секция названа явно — **`Weblate 2026.8.1`** | `sed -n '1,2p' docs/changes.rst` → `Weblate 2026.8.1` + подчёркивание |
| 3 | CSP-ассерты в тесте переписаны на паттерн `next(...)` вместо словаря директив | конвенция репозитория: `test_basic_views.py:40-44` (GA) и `:85-89` (Matomo) |
| 4 | В тест добавлены ассерты `preconnect` | `base.html:37` — `{% for domain in preconnect_list %}<link rel="preconnect" href="https://{{ domain }}">` |
| 5 | Реализация разбита на два коммита (рендеринг → CSP), тесты пишутся до кода | TDD-требование скилла |
| 6 | Запись в `_conf.py` **оставлена** вопреки замечанию «выбросить как мёртвую» | `appconf/base.py:60` — дефолт ставится на settings-holder только если его там нет; `MATOMO_SITE_ID`/`MATOMO_URL`/`GOOGLE_ANALYTICS_ID` в `_conf.py:59-64` находятся в точно таком же состоянии. Это конвенция «настройка живёт в четырёх файлах», ломать её ради двух строк дороже |
| 7 | Ассерт `assertNotContains(response, "clarity.ms/tag")` **оставлен** вопреки замечанию «упадёт» | `django/test/testcases.py:635-644` — `assertNotContains` падает при `real_count != 0`, то есть когда подстрока **найдена**; её отсутствие в HTML — это PASS. Прямой аналог: `assertNotContains(response, "GoogleAnalyticsObject")`, `test_basic_views.py:39` |

Дополнительно подтверждено перед началом работы (в план внесено как факты, а не предположения):

- `weblate/settings_test.py:14` — `from weblate.settings_example import *`, значит в тестах `CLARITY_PROJECT_ID` будет `None` по умолчанию, отдельная правка тестовых настроек не нужна;
- `FixtureTestCase.setUp` (`weblate/trans/tests/test_views.py:480-483`) вызывает `set_up_authenticated_view()` → `client.login(username="testuser", ...)` (`:287-290`), поэтому ассерт `data-username` отработает;
- `add_settings_context` (`weblate/trans/context_processors.py:66-68`) делает `context[name.lower()]`, то есть в шаблоне переменная называется `clarity_project_id`;
- пустая строка в env безопасна: `""` ложна во всех проверках `if settings.CLARITY_PROJECT_ID:` и в `{% if clarity_project_id %}`.

## Решения, ожидающие заказчика

Ни одно не блокирует реализацию и тесты — только живой smoke (Task 5) и два вызова в `clarity.js`, каждый снимается удалением одной строки.

| Решение | Если «да» | Если «нет» |
|---|---|---|
| `consentv2` | сессии посетителей из ЕЭЗ/UK/CH сшиваются | с 31.10.2025 такой посетитель получает новый ID на каждый просмотр, записи рассыпаются |
| `identify` | в дашборде видно имя пользователя Weblate | безымянные сессии; имя пользователя не уходит в Microsoft |
| Страница уведомления о cookie | выполняются Terms of use Clarity | на проде `weblate.legal` не настроен, `PRIVACY_URL` пуст — сообщать негде |
| Project ID (`abc12345`) | выполним Task 5 | Tasks 1-4 выполняются и проверяются полностью, Task 5 откладывается |

---

## Задачи

### Task 1: Рендеринг хука Clarity

**Files:**

- Modify: `weblate/trans/tests/test_basic_views.py` (вставка после строки 91, перед `def test_keys` на 93)
- Modify: `weblate/trans/defaults.py:34`
- Modify: `weblate/trans/models/_conf.py:64`
- Modify: `weblate/settings_example.py:997`
- Modify: `weblate/settings_docker.py:1602`
- Modify: `weblate/trans/context_processors.py:42,84`
- Modify: `weblate/templates/footer.html:79`
- Create: `weblate/static/js/clarity.js`
- Modify: `deploy/environment.example:55`
- Modify: `dev-docker/docker-compose.yml:52`

**Step 1: Написать падающие тесты**

В `weblate/trans/tests/test_basic_views.py`, сразу после `test_matomo` (заканчивается строкой 91):

```python
    @override_settings(CLARITY_PROJECT_ID="abc12345")
    def test_clarity(self) -> None:
        response = self.client.get(self.project_url)
        self.assertContains(response, static("js/clarity.js"))
        self.assertContains(response, 'data-project-id="abc12345"')
        self.assertContains(response, 'data-language="en"')
        self.assertContains(response, f'data-project="{self.project.name}"')
        self.assertContains(response, f'data-username="{self.user.username}"')
        self.assertContains(response, 'href="https://www.clarity.ms"')
        self.assertContains(response, 'href="https://scripts.clarity.ms"')
        # The official snippet is inline, this integration must not be
        self.assertNotContains(response, "clarity.ms/tag")

    def test_clarity_disabled(self) -> None:
        response = self.client.get(self.project_url)
        self.assertNotContains(response, static("js/clarity.js"))
        self.assertNotContains(response, "clarity-tracker")
        self.assertNotIn("clarity.ms", response["Content-Security-Policy"])
```

`test_clarity_disabled` проходит и до реализации — это сторож на будущее: он ловит регрессию «CSP или загрузчик подключаются безусловно». Драйвер задачи — `test_clarity`.

**Step 2: Убедиться, что тест падает**

Run: `./rundev.sh test weblate/trans/tests/test_basic_views.py -k clarity`
Expected: `test_clarity` — FAIL, `AssertionError: 0 != 1 : Couldn't find '/static/js/clarity.js' in response`; `test_clarity_disabled` — PASS.

**Step 3: Дефолт настройки**

`weblate/trans/defaults.py`, после `DEFAULT_GOOGLE_ANALYTICS_ID = None` (строка 34):

```python
DEFAULT_CLARITY_PROJECT_ID = None
```

**Step 4: Запись в AppConf**

`weblate/trans/models/_conf.py`, после блока Google Analytics (строка 64):

```python
    # Microsoft Clarity
    CLARITY_PROJECT_ID = defaults.DEFAULT_CLARITY_PROJECT_ID
```

**Step 5: Настройка в примере настроек**

`weblate/settings_example.py`, после `GOOGLE_ANALYTICS_ID = None` (строка 997):

```python
CLARITY_PROJECT_ID = None
```

**Step 6: Настройка в docker-настройках**

`weblate/settings_docker.py`, после блока `GOOGLE_ANALYTICS_ID` (заканчивается строкой 1602):

```python
CLARITY_PROJECT_ID = get_env_str(
    "WEBLATE_CLARITY_PROJECT_ID", trans_defaults.DEFAULT_CLARITY_PROJECT_ID
)
```

**Step 7: Контекст-процессор и preconnect**

`weblate/trans/context_processors.py`: в `CONTEXT_SETTINGS` после `"GOOGLE_ANALYTICS_ID"` (строка 42) добавить

```python
    "CLARITY_PROJECT_ID",
```

и в `get_preconnect_list` после блока Google Analytics (строка 84):

```python
    if settings.CLARITY_PROJECT_ID:
        result.append("www.clarity.ms")
        result.append("scripts.clarity.ms")
```

Тег грузится с `www.clarity.ms`, библиотека — со `scripts.clarity.ms`; оба хоста стоят прогреть заранее, потому что скрипт стартует из подвала.

**Step 8: Загрузчик**

Create `weblate/static/js/clarity.js` — по образцу `google-analytics.js:5-33` (та же очередь-заглушка, тот же `document.head.append`):

```js
// Copyright © Michal Čihař <michal@weblate.org>
//
// SPDX-License-Identifier: GPL-3.0-or-later

const clarityTracker = document.getElementById("clarity-tracker");

if (typeof window.clarity !== "function") {
  const queue = [];
  const clarity = (...args) => {
    queue.push(args);
  };
  clarity.q = queue;
  window.clarity = clarity;
}

const clarityScript = document.createElement("script");
clarityScript.async = true;
clarityScript.src = `https://www.clarity.ms/tag/${clarityTracker.dataset.projectId}`;
document.head.append(clarityScript);

window.clarity("consentv2", {
  ad_Storage: "denied",
  analytics_Storage: "granted",
});
window.clarity("set", "language", clarityTracker.dataset.language);
if (clarityTracker.dataset.project !== undefined) {
  window.clarity("set", "project", clarityTracker.dataset.project);
}
if (clarityTracker.dataset.username !== undefined) {
  window.clarity("identify", clarityTracker.dataset.username);
}
```

Почему это работает при загрузке тега файлом, а не inline-сниппетом: тег (`https://www.clarity.ms/tag/<id>`) использует уже существующий `window.clarity`, дописывает в его очередь свои обработчики и `("start", config)`, затем делает `q.unshift(q.pop())` — то есть `start` встаёт первым, а наши `consentv2`/`set`/`identify` обрабатываются после инициализации. Проверено на живом теге: `curl https://www.clarity.ms/tag/3t0wlogvdz`.

`ad_Storage: "denied"` отключает обмен рекламным MUID с Bing: в теге `muidsync()` (пиксель `https://c.clarity.ms/c.gif`) вызывается только при `granted`. Поэтому `img-src` в CSP не расширяется (см. Task 2).

Если заказчик откажется от `consentv2` или `identify` — удалить соответствующий вызов, остальное не меняется.

**Step 9: Блок в подвале**

`weblate/templates/footer.html`, после блока Google Analytics (`{% endif %}` на строке 79):

```html
{% if clarity_project_id %}
  <script src="{% static 'js/clarity.js' %}"
          async
          defer
          id="clarity-tracker"
          data-project-id="{{ clarity_project_id }}"
          data-language="{{ LANGUAGE_CODE }}"
          {% if project %}data-project="{{ project.name }}"{% endif %}
          {% if user.is_authenticated %}data-username="{{ user.username }}"{% endif %}></script>
{% endif %}
```

Clarity рекомендует `<head>`, но здесь берётся конвенция репозитория (Matomo и GA в подвале). Тег снимает полный снапшот DOM при старте, из-за позднего старта теряются только самые ранние клики.

**Step 10: Окружение**

`deploy/environment.example`, после `WEBLATE_ENABLE_AVATARS=0` (строка 55):

```text
# Microsoft Clarity session recordings; empty value disables the integration
WEBLATE_CLARITY_PROJECT_ID=
```

`dev-docker/docker-compose.yml`, в блок `environment` сервиса `weblate` после `WEBLATE_SITE_DOMAIN` (строка 52):

```yaml
      WEBLATE_CLARITY_PROJECT_ID: ${WEBLATE_CLARITY_PROJECT_ID:-}
```

**Step 11: Убедиться, что тесты проходят**

Run: `./rundev.sh test weblate/trans/tests/test_basic_views.py -k clarity`
Expected: 2 passed.

Правка `docker-compose.yml` вступает в силу только после полного `./rundev.sh` (rebuild + start) — блок окружения фиксируется при создании контейнера. Для этих тестов она не нужна: они используют `override_settings`.

**Step 12: Коммит**

```bash
git add weblate/trans/defaults.py weblate/trans/models/_conf.py \
        weblate/settings_example.py weblate/settings_docker.py \
        weblate/trans/context_processors.py weblate/templates/footer.html \
        weblate/static/js/clarity.js weblate/trans/tests/test_basic_views.py \
        deploy/environment.example dev-docker/docker-compose.yml
git commit -m "feat(trans): add optional Microsoft Clarity tracking hook"
```

---

### Task 2: CSP для Clarity

**Files:**

- Modify: `weblate/trans/tests/test_basic_views.py` (дополнить `test_clarity`)
- Modify: `weblate/middleware.py:317,401`

**Step 1: Дописать падающие ассерты**

В конец `test_clarity`:

```python
        script_src = next(
            directive
            for directive in response["Content-Security-Policy"].split(";")
            if directive.strip().startswith("script-src ")
        )
        self.assertIn("www.clarity.ms", script_src)
        self.assertIn("scripts.clarity.ms", script_src)
        self.assertNotIn("'unsafe-inline'", script_src)
        connect_src = next(
            directive
            for directive in response["Content-Security-Policy"].split(";")
            if directive.strip().startswith("connect-src ")
        )
        self.assertIn("*.clarity.ms", connect_src)
```

**Step 2: Убедиться, что тест падает**

Run: `./rundev.sh test weblate/trans/tests/test_basic_views.py -k test_clarity`
Expected: FAIL, `AssertionError: 'www.clarity.ms' not found in " script-src 'self' 'wasm-unsafe-eval'"`.

**Step 3: Реализовать директивы**

`weblate/middleware.py`, новый метод после `build_csp_google_analytics` (заканчивается строкой 401):

```python
    def build_csp_clarity(self) -> None:
        # Microsoft Clarity
        if settings.CLARITY_PROJECT_ID:
            # The tag bootstrap loads the library from a separate host
            self.directives["script-src"].add("www.clarity.ms")
            self.directives["script-src"].add("scripts.clarity.ms")
            # Uploads are load balanced across a.clarity.ms … z.clarity.ms
            self.directives["connect-src"].add("*.clarity.ms")
```

и вызов в `CSPBuilder.__init__` после `self.build_csp_google_analytics()` (строка 317):

```python
        self.build_csp_clarity()
```

Хосты добавляются литералами, а не через `add_csp_host`: тот берёт `urlparse(...).hostname` и подстановочную маску построить не может.

Состав директив снят с живого тега, а не с документации: тег подставляет `https://scripts.clarity.ms/0.8.69/clarity.js` и выгружает на `https://<буква>.clarity.ms/collect` (буква зависит от проекта — отсюда маска). `new Worker`, `createObjectURL`, `eval` и `new Function` в теге и в собранной библиотеке отсутствуют, выгрузка идёт `sendBeacon`/XHR — поэтому `worker-src`, `'unsafe-eval'` и `img-src` не трогаем. Если браузерный smoke (Task 5) покажет в консоли заблокированную картинку — добавить `c.clarity.ms` и `c.bing.com` в `img-src` и дописать ассерт в тест.

**Step 4: Убедиться, что тесты проходят**

Run: `./rundev.sh test weblate/trans/tests/test_basic_views.py -k clarity`
Expected: 2 passed.

Run: `./rundev.sh test weblate/trans/tests/test_basic_views.py`
Expected: без регрессий (в файле есть тесты Matomo, GA и Sentry, которые тоже разбирают CSP).

**Step 5: Коммит**

```bash
git add weblate/middleware.py weblate/trans/tests/test_basic_views.py
git commit -m "feat(trans): extend Content-Security-Policy for Microsoft Clarity"
```

---

### Task 3: Линт и формат

**Step 1: Прогнать проверки**

Run: `uv run prek run --all-files`
Expected: правки проходят biome (новый JS), djlint (шаблон), ruff (Python).

**Step 2: Исправить только свои находки**

Часть проверок падает на предсуществующих файлах (`reuse lint` на `loc_kit_ingest/`) — это не наша регрессия, трогать не нужно. Исправляем только замечания к файлам из Tasks 1-2.

**Step 3: Коммит, если что-то изменилось**

```bash
git add -u && git commit -m "style: apply prek formatting to Clarity integration"
```

---

### Task 4: Документация

**Files:**

- Modify: `docs/admin/config.rst` (перед строкой 1206)
- Modify: `docs/admin/install/docker.rst` (рядом с `WEBLATE_GOOGLE_ANALYTICS_ID`, строка ~1004)
- Modify: `docs/changes.rst` (секция `Weblate 2026.8.1`)
- Modify: `docs/security/threat-model.rst` (~930-934)

**Step 1: Настройка в config.rst**

Вставить **перед** `.. setting:: GOOGLE_ANALYTICS_ID` (строка 1206), то есть после секции `AZURE_DEVOPS_CREDENTIALS`:

```rst
.. setting:: CLARITY_PROJECT_ID

CLARITY_PROJECT_ID
------------------

`Microsoft Clarity <https://clarity.microsoft.com/>`_ project ID to turn on
session recordings and heatmaps.

.. note::

   Clarity always masks the content of input fields (``input``, ``select`` and
   ``textarea``) in the recordings, this can not be changed from Weblate.
```

**Step 2: Переменная окружения в docker.rst**

После блока `.. envvar:: WEBLATE_GOOGLE_ANALYTICS_ID` (строка ~1004), формулировка по образцу соседа:

```rst
.. envvar:: WEBLATE_CLARITY_PROJECT_ID

   Configures ID for Microsoft Clarity by changing
   :setting:`CLARITY_PROJECT_ID`.
```

**Step 3: Запись в changelog**

В список *Improvements* верхней, ещё не выпущенной секции `Weblate 2026.8.1` (строка 1) — одна строка со ссылкой на настройку, без длинных объяснений:

```rst
* Added optional session recording using :setting:`CLARITY_PROJECT_ID`.
```

**Step 4: Threat model**

Это новый исходящий интеграционный класс — прямой триггер из «Conditions that change this model» (`docs/security/threat-model.rst:916-923`). Абзац рядом с абзацем про loc-kit (~930-934): при заданном `CLARITY_PROJECT_ID` разметка страниц, URL и имя вошедшего пользователя уходят в Microsoft; включается только настройкой, по умолчанию выключено; CSP расширяется исключительно при заданной настройке; содержимое полей ввода провайдер не записывает.

**Step 5: Коммит**

```bash
git add docs/admin/config.rst docs/admin/install/docker.rst \
        docs/changes.rst docs/security/threat-model.rst
git commit -m "docs: document Microsoft Clarity integration"
```

---

### Task 5: Браузерный smoke (нужен Project ID и решения заказчика)

**Step 1: Поднять стенд с настройкой**

```bash
WEBLATE_CLARITY_PROJECT_ID=<project-id> WEBLATE_PORT=3001 ./rundev.sh
```

Полный `./rundev.sh`, не `restart`: переменные окружения фиксируются при создании контейнера.

**Step 2: Проверить страницу перевода**

Открыть `http://localhost:3001` (admin/admin), зайти на страницу перевода и убедиться:

- `scripts.clarity.ms/*/clarity.js` загружается (200);
- POST на `*.clarity.ms/collect` отвечает 200;
- в консоли нет ни одного нарушения CSP;
- в `<head>` присутствуют `preconnect` на `www.clarity.ms` и `scripts.clarity.ms`.

Взаимодействовать реальными кликами по контролам, не `form.submit()` и не прямым POST: иначе проверка обходит ровно тот путь, который тестируется.

**Step 3: Проверить запись в дашборде**

В проекте Clarity выставить `Settings → Masking → Relaxed`, подождать до часа, затем в новой записи убедиться: исходная строка читается, набранный перевод замаскирован. Второе — подтверждение ограничения провайдера, а не дефект.

**Step 4: Если консоль показала блокировку картинки**

Добавить `c.clarity.ms` и `c.bing.com` в `img-src` (`build_csp_clarity`), дописать ассерт в `test_clarity`, прогнать тесты, закоммитить отдельным коммитом.

---

## Критерии готовности

- `./rundev.sh test weblate/trans/tests/test_basic_views.py` — зелёный, включая оба новых теста;
- `uv run prek run --all-files` — без новых замечаний;
- настройка не задана → в HTML нет загрузчика, в CSP нет `clarity.ms` (закреплено `test_clarity_disabled`);
- настройка задана → загрузчик, `data-*`, `preconnect` и обе CSP-директивы на месте, inline-скрипта нет;
- документация: настройка, переменная окружения, changelog, threat model;
- Task 5 выполнен либо явно отложен из-за отсутствия Project ID.

## Вне скоупа

- серверная (Python) отправка событий, воронки, smart events, Clarity MCP;
- деплой на `l10n.herocraft.com` — отдельное одобрение по AGENTS.md;
- удаление или переделка существующих хуков Matomo/GA;
- любые `data-clarity-mask`/`data-clarity-unmask` и списки селекторов — маскирование не настраивается по решению заказчика;
- русский перевод новых строк документации.
