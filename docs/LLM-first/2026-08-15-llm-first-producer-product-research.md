# LLM-first HCGameLoc: исследование producer-first продукта

Дата: 2026-08-15.

Статус: продуктовый research и backlog, не план немедленной реализации.

Исследование выполнено read-only аудитом кодовой базы несколькими
субагентами в Luna-контексте. Файлы приложения в рамках исследования не
изменялись.

## Краткий вывод

Сейчас HCGameLoc - это **Weblate + LLM**, а не LLM-first продукт.
Переводческий контур уже сильный, но пользовательский сценарий по-прежнему
предполагает человека-переводчика:

- LLM запускается вручную;
- результат выглядит как suggestion или обычный перевод;
- review предполагает отдельного лингвиста;
- нет production-судьи и единого pipeline;
- нет экрана «можно ли выпускать язык»;
- нет доказательств качества, понятных продюсеру, который не читает целевой
  язык.

Целевая роль - русскоязычный продюсер игры, знающий английский, но не
читающий французский, китайский, японский и другие целевые языки. Его задача
не в том, чтобы переводить строки. Его задача - принять решение о доверии к
локализации и выпустить её без найма переводчиков.

Главная рекомендация: **не строить ещё один Weblate-модуль и не удалять
доменные возможности физически. Нужно сделать один producer-oriented pipeline
поверх существующих механизмов, а переводческий функционал скрыть в
Linguist/Advanced mode.**

## 1. Целевая постановка

### Пользователь

- русский язык интерфейса и рабочих инструкций;
- знает английский как исходный или рабочий язык;
- не может проверить целевой текст чтением;
- не должен знать внутреннюю терминологию Weblate;
- не должен разбираться в PO, XLIFF, `filemask`, `translation state`,
  `suggestion voting` и маршрутизации моделей.

### JTBD

> Когда выходит обновление игры, локализация должна автоматически
> перевестись, пройти проверку и показать продюсеру понятные доказательства
> качества. Всё, чему нельзя доверять, должно быть явно остановлено или
> вынесено на решение.

Продюсеру нужны не «инструменты переводчика», а:

- состояние pipeline;
- прогресс по языкам;
- стоимость и бюджет;
- список блокеров;
- доказательства качества;
- безопасный повторный запуск;
- готовый release package или Pull Request.

## 2. Что уже есть в коде

### LLM-перевод

Основной контур уже реализован:

- `RoutedLLMTranslation` с OpenRouter и маршрутизацией модели по языку:
  `weblate_customization/src/weblate_customization/machinery.py`;
- контекстный промпт с glossary, explanation, note, placeholders, plural
  metadata и failing checks:
  `weblate/machinery/llm.py`;
- structured JSON-ответы, retry, разбиение батчей и восстановление
  частичных ответов;
- project-level настройки, наследующие глобальные credentials и routing
  field-by-field:
  `weblate/trans/models/project.py:1332-1351`;
- `AutoForm.DEFAULT_ENGINE = "openrouter"`:
  `weblate/trans/forms.py:1177-1332`;
- инкрементальная запись батчей в
  `weblate/trans/autotranslate.py`.

### Детерминированный quality floor

В `weblate_customization` уже есть:

- `GameMarkupCheck` для Unity rich-text tags и placeholders;
- `GameLineBreakCheck` для `$`-разделителей;
- `CyrillicLeakCheck`;
- `LineSeparatorSpacing`;
- исправления финальной пунктуации;
- французский punctuation spacing.

Файлы:

- `weblate_customization/src/weblate_customization/checks.py`;
- `weblate_customization/src/weblate_customization/autofixes.py`.

Эти проверки следует считать фундаментом LLM-first контура: всё, что
проверяется детерминированно, не нужно отдавать на оценку модели.

### Loc-kit и glossary

`loc_kit_ingest` и Weblate-side glossary workflow уже поддерживают:

- CSV/TSV/XLSX;
- строгий профиль структуры;
- deterministic inference;
- optional OpenRouter fallback для профиля;
- preview и local validation;
- TBX render и parse-back validation;
- append-only обновление существующего glossary;
- collision detection;
- частичный успех по языкам;
- сохранение существующих targets, explanations и flags.

Это уже подходящий вход для producer onboarding. Его нужно упростить в UI,
а не заменять новым импортёром.

### Учёт стоимости

Есть `LLMUsageLog` и команда:

```text
weblate/trans/models/llm_usage.py
weblate/trans/management/commands/llm_usage_report.py
```

Учитываются prompt/completion/total tokens, cached/reasoning tokens, модель,
проект и стоимость OpenRouter. Пока это административный отчёт, а не
producer-facing dashboard. Нет полноценной атрибуции к localization run и
отдельной строке.

### Release primitives

В `Project` уже есть:

```text
CommitPolicyChoices.APPROVED_ONLY
```

и связанная фильтрация pending changes. Это полезный primitive, но он не
заменяет LLM pipeline: текущая state machine не знает, был ли target проверен
semantic judge и почему он допущен.

## 3. Что сейчас мешает LLM-first сценарию

### 3.1. LLM не является автоматическим конвейером

Сейчас существуют три отдельных пути:

1. ленивые per-unit automatic suggestions;
2. ручная bulk-форма Automatic translation;
3. add-on Automatic translation.

`AutoTranslateAddon` по умолчанию использует:

- `auto_source = "others"`;
- `mode = "suggest"`;
- query `state:<translated`.

Это означает, что новая строка после Git не проходит автоматически путь:

```text
Git update
  -> LLM translation
  -> deterministic QA
  -> semantic judge
  -> release decision
```

Источники:

- `weblate/addons/autotranslate.py:40-165`;
- `weblate/trans/autotranslate.py:334-383, 656-714`.

### 3.2. Review предполагает человека, которого нет

`Project.translation_review` и `source_review` рассчитаны на dedicated
reviewers. При включённом review перевод попадает в состояние ожидания
подтверждения. Для студии без переводчиков это создаёт очередь, которую
никому разгребать.

Нужно разделить:

- translation state;
- deterministic QA result;
- semantic evaluation;
- release verdict.

Человеческий approval не должен быть единственным способом перевести строку
в состояние поставки.

### 3.3. Нет отдельного quality/verdict слоя

`Unit.automatically_translated` хранит только provenance-флаг. Сейчас нет
первоклассного хранения:

- модели и версии prompt;
- версии localization run;
- source/target hash;
- deterministic verdict;
- judge verdict;
- MQM errors;
- back-translation/evidence;
- retry и idempotency key;
- причины автоматического approve или reject.

### 3.4. Массовый запуск небезопасен

`AutoForm` содержит технические параметры mode, query, source, component,
engines и threshold. При этом нет полноценного:

- dry-run;
- sample preview;
- оценки количества строк;
- прогноза стоимости;
- лимита бюджета;
- rollback batch;
- понятного run ID;
- повторного запуска только незавершённого хвоста.

Предупреждение формы о возможной потере существующих переводов не заменяет
безопасный workflow.

### 3.5. Интерфейс построен вокруг архитектуры Weblate

Основная навигация и страницы показывают:

- Projects;
- Languages;
- Checks;
- Components;
- Operations;
- Translation memory;
- Add-ons;
- Files;
- Reports;
- Suggestions;
- Comments;
- Variants;
- History.

Продюсер должен видеть:

- Progress;
- Tasks;
- Quality;
- Automation;
- Glossary;
- Release.

Источники:

- `weblate/templates/base.html`;
- `weblate/templates/project.html`;
- `weblate/templates/component.html`;
- `weblate/templates/translate.html`.

### 3.6. Контекст есть, но разбросан

В редакторе отдельно отображаются context, note, explanation, labels и
screenshots. Нет единой producer-facing карточки контекста, где можно задать:

- экран;
- персонажа;
- speaker;
- жанровую роль;
- ограничение длины;
- платформу;
- gameplay state;
- обязательный термин;
- запрет на определённую форму.

Для LLM-first качество контекста важнее большей части переводческой
навигации.

### 3.7. Glossary workflow слишком технический

Строгий профиль, TBX, режимы `forbidden`, `read-only`, `exact` и
`not-applicable` полезны для движка, но не должны быть основным языком
продюсерского UI. Нужен простой mapping:

```text
Source | Target | Context | Note | Rule
```

Существующие append-only и collision guarantees нужно сохранить.

## 4. Главный незакрытый компонент: semantic judge

В production-коде отсутствуют:

- `JudgeVerdict`;
- `judge-flag` и `judge-reject`;
- judge client;
- Celery orchestration;
- `mode="judge"` в `AutoForm`;
- UI evidence card;
- API для verdict/evidence.

Есть только calibration scripts и design docs:

- `docs/LLM-first/2026-08-13-judge-native-ui-design.md`;
- `docs/LLM-first/2026-08-14-st2-judge-run.md`;
- `docs/LLM-first/plans/2026-08-13-01-judge-verdict-core.md`;
- `docs/misc/col4-judge-eval.py`;
- `docs/misc/st2-judge-experiment.py`.

### Требования к judge

Judge должен:

- получать deterministic check results и glossary context;
- выдавать structured output;
- перечислять MQM errors с severity;
- обязательно писать понятное описание ошибки;
- объяснять проблему на русском или английском, а не только показывать
  target-language span;
- оставлять audit trail;
- быть отдельным от translation model family;
- поддерживать dry-run и повторную калибровку;
- не выдавать молчаливые state transitions.

Back-translation следует использовать как display-only evidence. Она помогает
продюсеру сравнить source и приблизительную реконструкцию, но не должна быть
единственным quality score.

### Политика безопасности

Нельзя сразу включать blind auto-approve. Судья должен пройти:

1. golden-set calibration;
2. повторные прогоны для измерения нестабильности;
3. canary на одном production-компоненте;
4. dry-run;
5. проверку false pass для критических ошибок.

Deterministic errors остаются главным blocking-сигналом. Семантическая
политика должна включаться постепенно: сначала advisory verdict, затем
отдельно согласованный policy для critical cases и низкоресурсных языков.

## 5. Producer mode вместо удаления ядра

Нужны два интерфейсных режима.

### Producer mode

Основная навигация:

1. **Progress** - состояние игр и языков.
2. **Tasks** - незавершённые runs и решения.
3. **Quality** - checks, verdicts, evidence.
4. **Automation** - profile, модели, расписание.
5. **Glossary** - термины, конфликты, coverage.
6. **Release** - готовность, PR, package, blockers.
7. **Advanced** - технические настройки.

Основной экран строки:

- source;
- target;
- context;
- screenshot;
- deterministic checks;
- verdict;
- обратный перевод;
- объяснение проблемы;
- `Retry`;
- `Accept exception`;
- `Block release`.

### Linguist/Advanced mode

Оставить полноценный Weblate editor и административные возможности для:

- ручного перевода;
- plural editing;
- suggestions;
- comments;
- variants;
- translation memory;
- VCS;
- file formats;
- labels;
- сложных glossary flags;
- API и интеграций.

Это уменьшает риск поломать upstream-модель и оставляет аварийный escape
hatch.

## 6. Что скрыть или сделать неактивным

Удалять код и модели на первом этапе не следует. Рекомендуется скрытие в
Producer mode и отключение дефолтов.

### P0

| Функциональность | Решение |
| --- | --- |
| Suggestion voting | Выключить из основного сценария. Оставить для альтернатив и исключений. |
| Suggestion autoaccept | Не использовать как quality gate. |
| Ручной per-string editor | Заменить producer review mode; полный editor оставить в Advanced. |
| Человеческая review-очередь | Заменить очередью verdict/evidence. |
| Ручной bulk AutoTranslate | Заменить одной операцией `Translate and validate`. |

### P1

| Функциональность | Решение |
| --- | --- |
| Translation Memory | Оставить backend fallback, убрать из главной навигации и дефолтного пути. |
| Десятки MT-провайдеров | В Producer UI показывать только OpenRouter. Остальные не удалять до telemetry-аудита. |
| VCS, filemask, branch, template | Перенести в Advanced/Admin. |
| Commit messages и language aliases | Скрыть от продюсера. |
| Comments и translator-work reports | Скрыть из основного рабочего процесса, аудит сохранить. |
| Сложные glossary flags | Спрятать за простым mapping UI. |
| Технические CSV/XLIFF/XLSX/JSON export | Заменить Create release package; форматы оставить в Advanced. |

### P2

| Функциональность | Решение |
| --- | --- |
| Workspaces | Не показывать для single-tenant сценария. |
| Billing, Discover, hosting | Убрать из основной навигации внутренней установки feature flags. |
| Categories, labels, variants | Оставить в полном режиме, скрыть для продюсера. |
| Announcements и community workflow | Не включать в основной localization workflow. |

## 7. Приоритетный backlog развития

### P0: вертикальный LLM-first slice

1. Producer onboarding:
   `загрузить файлы -> выбрать языки -> задать profile -> запустить`.
2. `LocalizationRun`:
   один run на все target languages, прогресс, cost, retry и idempotency.
3. `UnitEvaluation`:
   source/target hashes, model, prompt version, checks, verdict и evidence.
4. Единый pipeline:

   ```text
   source update
     -> LLM translation
     -> autofix
     -> deterministic checks
     -> semantic judge
     -> release decision
   ```

5. Dry-run и sample preview до массовой записи.
6. Producer dashboard с blocked/uncertain/ready status.

### P1: доказательства и управление

1. Production judge после завершения calibration.
2. Review queue по языку, batch и модели, а не по username.
3. Back-translation и описания ошибок в UI.
4. Cost dashboard:
   requests, tokens, cost, failures, model, language, run и budget limit.
5. `Localization profile` вместо технических persona/style/routing JSON.
6. Glossary mapping UI с coverage и conflict report.
7. Автоматический retry/retranslation для rejected строк.

### P2: поставка

1. Release readiness page.
2. GitHub PR или release branch.
3. Package с checksum и QA report.
4. CI gate для deterministic defects.
5. Удаление или окончательное отключение неиспользуемых функций только после
   telemetry-аудита.

## 8. Предлагаемая минимальная модель данных

### `LocalizationRun`

Минимальные поля:

- project;
- source revision;
- target languages;
- component scope;
- profile version;
- translator model/routing;
- judge policy version;
- status;
- started/finished timestamps;
- estimated/actual cost;
- retry count;
- idempotency key;
- release decision.

### `UnitEvaluation`

Минимальные поля:

- unit;
- run;
- source hash;
- target hash;
- context/glossary hash;
- translation model;
- judge model;
- prompt version;
- deterministic checks;
- verdict;
- maximum severity;
- structured errors;
- back-translation;
- attempt;
- resolution;
- resolution reason;
- resolved by;
- timestamp.

`LLMUsageLog` следует сохранить как низкоуровневый billing log, но связать с
run и языком. Не следует использовать его как замену `UnitEvaluation`.

## 9. Что не делать

- Не добавлять ещё один десяток MT/LLM-провайдеров.
- Не строить новый переводческий редактор вместо producer review.
- Не использовать suggestion voting как quality gate.
- Не считать числовой score 0-100 достаточным доказательством качества.
- Не делать back-translation единственным judge-сигналом.
- Не удалять сразу Weblate domain models и полноценный editor.
- Не смешивать site-wide loc-kit profile analysis с translation machinery.
- Не включать auto-approve до golden-set calibration и canary.
- Не показывать продюсеру raw prompt, JSON schema и внутренние state codes.

## 10. Критерии успеха

Целевой сценарий должен позволять продюсеру:

1. загрузить локализационные файлы или подключить Git;
2. выбрать целевые языки;
3. задать профиль игры и glossary;
4. запустить одну операцию для всех языков;
5. увидеть прогресс и стоимость;
6. получить список проблем с понятными объяснениями;
7. повторно запустить только проблемные строки;
8. принять исключение с причиной;
9. получить release package или Pull Request;
10. не читать целевой язык и не знать внутреннюю модель Weblate.

Каждая строка, которая не прошла автоматический контур, должна иметь:

- понятную причину;
- evidence;
- текущую версию target;
- историю попыток;
- однозначное решение: retry, exception или block.

## 11. Проверенные точки в коде и документации

### Основные исходники

- `weblate/trans/forms.py:1177-1332` - ручная форма Automatic translation.
- `weblate/trans/autotranslate.py:334-383, 656-714` - режимы и запись
  автоматического перевода.
- `weblate/addons/autotranslate.py:40-165` - add-on и его defaults.
- `weblate/trans/models/project.py:61-95` - commit policy.
- `weblate/trans/models/project.py:327-345` - review и commit policy fields.
- `weblate/trans/models/project.py:1332-1351` - project machinery inheritance.
- `weblate/trans/models/unit.py:693-701, 2363-2475` - provenance и lifecycle.
- `weblate/machinery/llm.py` - LLM prompt, context, retry и parsing.
- `weblate_customization/src/weblate_customization/machinery.py` - OpenRouter.
- `weblate_customization/src/weblate_customization/checks.py` - game checks.
- `weblate_customization/src/weblate_customization/autofixes.py` - game
  autofixes.
- `weblate/trans/models/llm_usage.py` - usage model.
- `weblate/trans/management/commands/llm_usage_report.py` - usage report.
- `weblate/templates/base.html` - глобальная навигация.
- `weblate/templates/project.html` - project navigation.
- `weblate/templates/translate.html` - translator-first editor.

### Связанные исследования и дизайны

- `docs/LLM-first/llm-first-product-architecture.md`;
- `docs/LLM-first/llm-first-product-research.md`;
- `docs/LLM-first/2026-08-13-judge-native-ui-design.md`;
- `docs/LLM-first/2026-08-13-phase0-measurements.md`;
- `docs/LLM-first/2026-08-14-st2-judge-run.md`;
- `docs/specs/2026-08-11-llm-first-prompt-and-pipeline-review.md`;
- `docs/specs/producer-guide.md`;
- `docs/specs/continuous-localization-loop.md`;
- `docs/specs/loc-kit-ingest.md`.

## 12. Проверка выводов

Подтверждено по текущему коду:

- OpenRouter machinery, game checks/autofixes, loc-kit и usage accounting
  существуют;
- `CommitPolicyChoices.APPROVED_ONLY` существует;
- `AutoForm` остаётся ручной технической формой;
- add-on automatic translation по умолчанию не является LLM-first pipeline;
- production `JudgeVerdict`, judge API и judge orchestration не найдены;
- glossary check остаётся отдельным включаемым quality mechanism, а не
  полноценным semantic verdict.

Не следует считать доказанным до реализации и calibration:

- что один judge безопасно заменяет человеческое review;
- что auto-approve будет корректен на всех языках;
- что judge должен использовать один фиксированный model family;
- что все legacy-функции действительно не используются.

Перед изменением кода требуется отдельный approved plan с контрактом run,
verdict, release policy и Producer mode.
