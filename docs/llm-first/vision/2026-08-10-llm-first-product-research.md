# LLM-first HCGameLoc: исследование и продуктовое предложение

Дата: 2026-08-10. Подготовлено тремя параллельными исследованиями: аудит
текущего продукта, инвентаризация встроенных механизмов Weblate, внешнее
исследование топовых TMS (Lokalise, Crowdin, Phrase, Smartling, Transifex,
XTM, memoQ) через exa.

Цель: сделать HCGameLoc LLM-first — автоперевод во главе сценария, человек
ревьюит только low-confidence строки.

> Заменено 2026-08-11: актуальные архитектура и роадмап — в
> `llm-first-product-architecture.md` (части 1-3 перенесены туда без
> изменений, часть 4 пересмотрена под вводную «переводчиков нет,
> валидирует LLM-judge»). Этот файл сохранён ради ссылок с номерами
> строк из ревизии 2026-08-11.

---

## Часть 1. Текущее состояние продукта (аудит кода)

### Карта LLM-возможностей

```text
RoutedLLMTranslation (weblate_customization/machinery.py)
  name='OpenRouter', per-language routing JSON ({ja: model, *: fallback}),
  ContextVar -> resolve_model(), наследование настроек project -> global
      | наследует
OpenAITranslation (weblate/machinery/openai.py)
  chat completions payload, parse_chat_response
      | наследует
BaseLLMTranslation (weblate/machinery/llm.py, ~2600 строк)
  PROMPT-шаблон (27 правил) + persona/style/language_instructions
  _build_string_payload(): source, context/key, explanation, note,
    secondary language, plural metadata, failing_checks[], placeholders map
  _get_glossary_entries(), batch_size=20, max_score=90
      | наследует
BatchMachineTranslation (weblate/machinery/base.py)
  кэш переводов 30 дней, rate limiting (1800s), batch_translate,
  TranslationDownloadPlan, quality в TranslationResultDict
```

### Три пути к LLM-переводу сегодня

**Путь A — вкладка Automatic suggestions (per-unit, lazy).**
Переводчик открывает страницу перевода, JS (`weblate/static/editor/full.js:275`)
лениво грузит suggestions по AJAX (`weblate/machinery/views.py:571`).
LLM не вызывается, пока человек не открыл вкладку. Кнопки
Clone / Accept / Accept+Approve, хоткеи Ctrl+M.

**Путь B — форма Automatic translation (bulk, Celery).**
Operations -> Automatic translation (`AutoForm`,
`weblate/trans/forms.py:1177`, `DEFAULT_ENGINE='openrouter'`).
Режимы: suggest / translate / fuzzy / approved; фильтр по умолчанию
`state:<translated`. Задача `auto_translate`
(`weblate/trans/tasks.py:971`) -> `AutoTranslate.process_mt()`
(`weblate/trans/autotranslate.py`) -> `fetch_machinery_matches` ->
`batch_translate`. Запускается человеком, отдельно на каждый язык.

**Путь C — addon Automatic translation (`weblate/addons/autotranslate.py`).**
Триггеры: EVENT_COMPONENT_UPDATE, EVENT_DAILY (шахматка BACKGROUND_TASKS),
EVENT_CHANGE. Дефолты: `mode=suggest`, `auto_source=others` (память
переводов, НЕ MT/LLM!), `q=state:<translated`, `threshold=80`.

### Точки трения (ручные шаги)

1. Новая строка из git НЕ переводится LLM автоматически: addon по умолчанию
   смотрит в TM, не в machinery; на компонентах он не установлен.
2. Bulk-автоперевод запускается вручную и per-language.
3. Нет auto-approve: даже принятый LLM-перевод ждёт человека.
4. Нет оценки качества (QE): все LLM-результаты равнозначны, очередь ревью
   не приоритизирована.
5. `enforced_checks` (механизм STATE_NEEDS_REWRITING при failing check) не
   подключён к игровым чекам.
6. LLM виден в UI только как вкладка suggestions — опция, не дефолтный поток.

### Архивные планы (docs/llm-first/archive/)

- `llm-judge-external-pipeline.md` — доархитектурный черновик внешнего
  LLM-judge; не реализован и заменён архитектурой
  `llm-first-product-architecture.md`, частью 4, и фазой 0 B2.
- `2026-08-07-project-scoped-llm-context.md` — field-by-field наследование
  проектных настроек, developer note в промпте, команда glossary coverage.
  НЕ реализован.
- `2026-08-05-routed-llm-machinery.md` (+ design) — RoutedLLMTranslation.
  РЕАЛИЗОВАН.
- `docs/guides/continuous-localization-loop.md` — полный цикл
  git -> Weblate -> git с рекомендациями по addon'ам.

---

## Часть 2. Встроенные механизмы Weblate (на что опираться)

| Механизм | Файл | Что даёт | Ограничение |
|---|---|---|---|
| BatchMachineTranslation | `weblate/machinery/base.py` | batch_translate, кэш 30 дней, rate limiting, `quality` в результате, ранжирование сервисов (max_score + rank_boost) | quality — статическая константа сервиса, не оценка конкретного перевода |
| Реестр MACHINERY | `weblate/machinery/models.py` | регистрация кастомных сервисов через WEBLATE_ADD_MACHINERY | — |
| State machine | `weblate/utils/state.py` | 0 empty, 10 fuzzy, 11 needs rewriting (enforced checks), 12 needs checking, 20 translated, 30 approved | API запрещает вручную ставить 11/12 |
| Review workflow | `weblate/trans/models/project.py` | `translation_review=True`: state 20 = "Waiting for review", 30 требует права unit.review | — |
| Suggestion + autoaccept | `weblate/trans/models/suggestion.py`, `component.py` | `suggestion_voting` + `suggestion_autoaccept` (порог голосов) | рассчитан на голоса людей, не на скоры машин |
| AutoTranslate | `weblate/trans/autotranslate.py` | режимы suggest/fuzzy/translate/approved, фильтр q | всегда per-translation (один язык), нет multi-language batch |
| Addon autotranslate | `weblate/addons/autotranslate.py` | триггеры component_update / daily / change | дефолт auto_source=others (TM) |
| enforced_checks | `weblate/trans/models/unit.py` (Unit.translate) | failing enforced check -> STATE_NEEDS_REWRITING, блокирует «переведено» | применяется после сохранения, не до |
| Checks на suggestion | `suggestion.get_checks()` | прогон checks на suggestion | не влияет на autoaccept |
| API | `weblate/api/views.py` | PATCH unit (target+state, 30 требует unit.review), POST translations/{id}/autotranslate, SuggestionViewSet.accept, ProjectViewSet.machinery_settings | нет batch-translate endpoint |
| Настройки MT per-project | `weblate/configuration/models.py` (Setting, category=2) | field-by-field merge поверх глобальных, `_project` в ключе кэша | — |
| Celery | `weblate/trans/tasks.py`, `weblate/utils/celery.py` | auto_translate с retry, периодические задачи, /api/tasks/ прогресс | — |

Архитектурные пробелы для LLM-first: нет QE-поля на unit'е, нет порога
auto-approve, quality у suggestion не хранится, нет batch API.

---

## Часть 3. Как это делают топовые TMS (2025-2026)

Единый паттерн рынка: **AI переводит всё при загрузке -> QE-скор 0-100 на
каждую строку -> auto-approve выше порога -> человек ревьюит только
low-confidence**.

### Сравнительная таблица

| Вендор | Флагманский LLM-сценарий | QE-метод | Auto-approve | Роль человека | Killer feature |
|---|---|---|---|---|---|
| Lokalise | AI translation + Workflows | Real-time MQM (0-100) | порог (default 80) | ревью < порога | Thompson Sampling мульти-модельный роутинг |
| Crowdin | AI Pipeline + Copilot | флаги неоднозначности + QA checks | ручной/Copilot | разрешает неоднозначности | модульный pipeline + ambiguity-first Copilot |
| Phrase | AI Translation Agent + MT Autoselect | QPS (MQM-based, 0-100) | порог auto-confirm + lock (default 100%) | ревью незалоченных | явный порог auto-confirm + Analytics для тюнинга |
| Smartling | AIT (managed) / AI Toolkit | LQE Agent + LQA Agent (MQM) | Tier 1 = без ревью; Tier 2 = light post-edit | тиры по языкам | zero-review для high-resource языков |
| Transifex | AI Fill-up + TQI | TQI (3 компонента, real-time) | Auto-Review по порогу TQI | ревью < порога | разбивка TQI: consistency / structural / semantic |
| XTM | AI Translate + Intelligent Workflow | консенсус 2-3 LLM (TQI) | auto-close шага, если все Good | ревью < порога | LLM-консенсус как QE (без отдельной модели) + auto-close |
| memoQ | AGT (RAG) | нет (человек обязателен) | нет | полное ревью | RAG по своим TM/TB/LiveDocs без дообучения |

### Детали по вендорам

**Lokalise.** Upload -> Workflows -> AI translation (роутинг Thompson
Sampling между 5+ LLM) -> real-time MQM per string -> auto-approve >= 80 ->
только <80 в ревью. AI Profiles с RAG по прошлым переводам/терминологии,
скриншоты и метаданные автоматически в контексте. MCP-сервер для внешних
копилотов. Маркетинг: "human-level translations at scale", 90%+ acceptance.
Источники: <https://lokalise.com/ai/>,
<https://docs.lokalise.com/en/articles/11631905-scoring-translation-quality>.

**Crowdin.** Auto-Translation при загрузке -> AI Pipeline (модульные шаги:
извлечение контекста -> терминология -> перевод -> contextual appraisal ->
QA) -> Copilot флагует неоднозначности и задаёт уточняющие вопросы вместо
угадывания. Полностью кастомизируемые промпты; контекст: глоссарий, TM,
style guide, соседние строки, файл, проект, код/репозиторий, скриншоты.
Источники: <https://support.crowdin.com/crowdin-ai/>,
<https://crowdin.com/blog/mastering-ai-localization-pipelines>,
<https://crowdin.com/blog/game-localization>.

**Phrase.** Pre-translation (TM -> MT/AI) -> MT Autoselect per job -> QPS
скорит каждый сегмент (0-100, предсказывает MQM) -> сегменты выше порога
auto-confirm и/или lock (default 100%, понижают до ~90%) -> остальное в
ревью/LQA через Orchestrator. AI Translation Agent: translate -> error
correct -> terminology -> fluency refine без конфигурации. Style Guides
per-locale, Rules как источник AI-чеков. Источники:
<https://support.phrase.com/hc/en-us/articles/5709672289180-Phrase-QPS-Overview>,
<https://support.phrase.com/hc/en-us/articles/20660272640284-AI-Translation-Agent>.

**Smartling.** Два режима: AIT (managed, Tier 1 языки = ноль человеческого
ревью, near-instant; Tier 2 = AI + Light Post-Edit) и self-serve AI Toolkit
(LQE Agent предсказывает качество, AI Post-Editing Agent правит грамматику/
тон/brand voice, LQA Agent — MQM-based QA). Visual Context: скриншоты, OCR,
JS/API. Маркетинг: "MTPE-quality at half the cost". Источники:
<https://help.smartling.com/hc/en-us/articles/4417496876827-Smartling-Language-Services-Workflows>,
<https://help.smartling.com/hc/en-us/articles/25058582212507-Language-Quality-Estimation-Agent-for-Machine-Translation>.

**Transifex.** TM Fill-up -> AI & MT Fill-up (AI/MT/hybrid per language) ->
TQI в реальном времени (Consistency = согласие LLM-выводов, Structural
Integrity = форматирование/глоссарий/теги, Semantic = MQM-точность) ->
Auto-Review по пользовательскому порогу TQI -> ниже порога — в ревью.
Thumbs up/down улучшает AI и TQI. Smart Tag protection для notranslate.
Источники: <https://help.transifex.com/en/articles/9465000-translation-quality-index-tqi>,
<https://community.transifex.com/t/now-live-supercharge-your-localization-with-auto-review-based-on-tqi/4762>.

**XTM.** AI Translate шлёт каждый сегмент 2-3 LLM (с source + контекст +
до 3 TM-совпадений + терминология) -> TQI = сходство ответов моделей
(высокое согласие = высокий скор) -> Intelligent Workflow auto-close шага,
если все сегменты Good (порог Acceptable default 70%, Good 95-100%,
конфигурируется global/customer/project) -> ниже порога — лингвисту.
IPE (Intelligent Post-Editing) — автоматический LLM-шаг доводки перед
человеком. Источники:
<https://help.xtm.ai/en/xtm-cloud/26.2/en/global-ai-translate---tqi-settings.html>,
<https://help.xtm.ai/en/xtm-cloud/26.2/en/global-intelligent-workflow-settings.html>.

**memoQ.** AGT: pre-translate LLM'ом с RAG по своим TM/TB/LiveDocs, без
дообучения. Человеческое ревью обязательно. Источники:
<https://docs.memoq.com/current/en/Workspace/pre-translate-with-agt.html>,
<https://www.memoq.com/product/memoq-agt/>.

### Quality estimation: что реально гейтит в проде

1. **TMS-нативные скоры** (Phrase QPS, Lokalise MQM, Transifex TQI, XTM
   TQI, Smartling LQE) — проприетарные смеси COMET-производных +
   LLM-as-judge. Именно они, а не сырой COMET, выполняют auto-approve.
2. **COMET / CometKiwi** (Unbabel) — открытые модели; CometKiwi —
   reference-free QE, практичный вариант для гейтинга.
   <https://github.com/Unbabel/COMET>,
   <https://aclanthology.org/2022.wmt-1.60/>.
3. **GEMBA-MQM** — GPT-4 как MQM-судья, SOTA для system-level ranking
   (WMT). <https://aclanthology.org/2023.wmt-1.64/>. Академическая база
   того, что вендоры продают как real-time скоринг.
4. **Мульти-LLM консенсус** (XTM, компонент Consistency у Transifex) —
   качество = согласие моделей; не нужна отдельная QE-модель.
5. **MQM** — общий каркас всех скоров: accuracy, fluency, terminology,
   style + severity (minor/major/critical).

### Контекст-инъекция для игровой локализации (зрелые практики)

- Глоссарий/termbase в промпте — универсально; рекомендация 500-1000
  терминов до включения AI (<https://loxily.com/en/blog/complete-guide-game-localization-ai>).
- Профили персонажей / тон / style guide в промпте (Gridly character
  prompt library, Crowdin style guides).
- Скриншоты / visual context против переполнения UI (Smartling, Crowdin).
- Лимиты длины строк per-language (Gridly length limit).
- Сохранение плейсхолдеров/маркапа как детерминированные проверки — по
  Loxily большинство провалов игровой локализации это
  engineering/formatting, не качество перевода
  (<https://loxily.com/en/blog/game-localization-qa-checklist>).
- Код/репозиторий как контекст (Crowdin MCP, сканирование репо).

### Агентные / pipeline-подходы

- **Translate -> Estimate -> Refine** (TEaR, NAACL 2025;
  <https://aclanthology.org/2025.findings-naacl.218/>) — self-review цикл;
  экспериментально, но сильные результаты.
- **Каскадный cost-quality роутинг** ("Translate Smart, not Hard",
  EMNLP 2025; <https://aclanthology.org/2025.emnlp-main.1358.pdf>) —
  дешёвая модель первой, эскалация низкоскорных X% на дорогую в рамках
  бюджета. Прямо ложится на OpenRouter-роутинг.
- **Sample-N + re-rank по QE** (Unbabel QUARTZ, MBR;
  <https://unbabel.com/quality-aware-machine-translation/>) — ~40%
  снижение серьёзных ошибок.
- **Multi-agent translate -> postedit -> proofread** (WMT25;
  <https://aclanthology.org/2025.wmt-1.32/>) — коммерческий аналог:
  Crowdin AI Pipeline.

### Роль человека (консенсус рынка)

- Review-only: человек не переводит первый драфт нигде.
- Порог QE решает, что человек вообще увидит.
- Тиры доверия по языкам (Smartling): high-resource — без ревью.
- Человек остаётся для транскреации / лора / эмоционально нагруженного
  контента; AI стартует с low-risk (UI, patch notes).
- Петля обратной связи: правки человека -> TM/RAG -> будущие переводы.

---

## Часть 4. Продуктовое предложение: 4 инкремента

### P1 — «Строка приходит переведённой» (конфигурация, ~дни)

Ноль нового кода в ядре:

- Addon autotranslate на каждом компоненте: `auto_source=mt`,
  `engines=[openrouter]`, триггеры component_update + change,
  `mode=translate`.
- Включить `Project.translation_review=True`: LLM-перевод попадает в
  state 20 = "Waiting for review" — готовая очередь ревью; человек
  становится ревьюером.
- `Component.enforced_checks = [game-markup, game-line-break]`: брак по
  маркапу автоматически падает в STATE_NEEDS_REWRITING (11) и не доходит
  до релиза (механизм уже есть в `Unit.translate()`).

Эффект: сценарий переворачивается сразу — git push -> всё переведено ->
очередь ревью. 80% «LLM-first» за 5% усилий.

### P2 — QE-скор + порог auto-approve (killer feature, ~2-3 недели)

Паритет с Phrase QPS / Transifex TQI, которого нет в стоковом Weblate:

- **Консенсус-QE по образцу XTM**: та же строка -> 2 дешёвые модели через
  существующий OpenRouter-роутинг -> скор = семантическое согласие +
  прогон существующих checks. Отдельная QE-модель не нужна; batch_translate,
  кэш и роутинг уже есть.
- Скор в `TranslationResultDict.quality` + label юнита
  (`q=label:llm-low-confidence` для фильтрации очереди).
- Порог per-project (та же `Setting category=MT`): `score >= T` и нет
  failing checks -> `STATE_APPROVED`; иначе -> 20 (ревью).
- Минимальный дашборд: фильтр по скору в очереди ревью.

Эффект: человек видит только 10-30% строк.

### P3 — Cost-quality каскад (~1-2 недели)

- Первый проход дешёвой моделью -> низкий QE-скор из P2 -> автоэскалация
  на дорогую модель. Расширение `routing` JSON:
  `{"ja": {"primary": "...", "escalation": "..."}}`.
- Тиры языков по Smartling: для en/ja порог auto-approve выше, для редких
  языков — всё в ревью.

Академическое обоснование: EMNLP 2025 cascaded deferral (см. Часть 3).

### P4 — историческое предложение, заменено архитектурой

- Калибровка судьи выполняется по
  `docs/llm-first/plans/2026-08-11-phase0-schema-and-judge-calibration.md`;
  реализация судьи следует части 4
  `llm-first-product-architecture.md`, а не архивному ночному
  Celery-проходу.
- `2026-08-07-project-scoped-llm-context.md` остаётся невыполненным
  архивным планом: project-scoped persona / glossary coverage — контекст
  per-игра; для геймдева контекст-инъекция важнее выбора модели
  (консенсус Crowdin/Gridly/Loxily).

### Что сознательно НЕ делать

- Свою COMET/ML-QE модель: консенсус LLM даёт ~90% ценности без MLOps.
- Скриншоты/visual context: дорого; контекст уже идёт из loc-kit
  (note/explanation/глоссарий).
- `suggestion_voting`/`suggestion_autoaccept` как gate: механизм рассчитан
  на голоса людей; правильный рычаг — `state` + `enforced_checks`.

### Риски

- P2 удваивает-утраивает токен-косты. Митигация: консенсус только там, где
  он нужен (короткие строки — одной моделью), кэш 30 дней уже есть.
- Auto-approve галлюцинаций. Митигация: enforced game-checks как жёсткий
  детерминированный пол + LLM-judge как сэмплированный аудит (P4).

### Порядок

P1 немедленно (конфигурация, обратимо), параллельно спека P2; P3 после
данных по распределению скоров из P2; P4 — по существующим планам.
