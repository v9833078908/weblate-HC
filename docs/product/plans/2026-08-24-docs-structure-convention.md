# Конвенция файлов документации: единая структура docs/

Дата: 2026-08-24. Статус: **согласовано, выполнено.**

## Проблема

Замер по текущему состоянию репозитория (99 markdown-файлов форка в четырёх
каталогах, 462 перекрёстные ссылки между ними):

1. **Две параллельные папки планов без правила.** `docs/plans/` (47 файлов) и
   `docs/LLM-first/plans/` (12 + `old/` 3). План судьи может лежать в любой:
   `docs/plans/2026-08-22-03-judge-review-gate.md` и
   `docs/LLM-first/plans/2026-08-22-02-judge-navigation-readiness.md` — соседние
   задачи одной фазы роадмапа в разных деревьях.
2. **Роадмап уже ссылается неправильно.** `llm-first-product-architecture.md`
   пишет `plans/2026-08-11-glossary-morphological-enforcement.md` (фаза 1) и
   `plans/2026-08-14-intra-component-consistency-check.md`, но первый файл лежит
   в `docs/plans/`, а не в `docs/LLM-first/plans/`. Из 462 ссылок **32 битые**.
3. **Жанр закодирован суффиксом, а не местом.** Семь пар «дизайн + план»
   различаются только суффиксом `-design` внутри `docs/plans/`, поэтому
   «plans» — не жанр, а свалка.
4. **`docs/specs/` держит четыре разных жанра одновременно:** вечнозелёные
   контракты и гайды (4), датированные отчёты замеров (3), заметки со встреч (2)
   и апстримовые артефакты API (`openapi.yaml`, `schemas/*.json`), которые
   участвуют в сборке Sphinx (`docs/contributing/schemas.rst`), в CI
   (`.github/workflows/api.yml`) и в `REUSE.toml`.
5. **`docs/misc/` — свалка из 180 файлов:** 12 md-отчётов, 37 одноразовых
   скриптов, 125 файлов данных (json/jsonl/tsv/csv/log) и незакоммиченный
   `__pycache__` (6 `.pyc`).
6. **Имена без правила.** Единственный каталог в CamelCase (`LLM-first`),
   единственный snake_case (`agent_researches`), `plans/old` вместо архива, дата
   то в начале (`2026-08-20-judge-...`), то в конце
   (`judge-first-dev-run-2026-08-20.md`), то отсутствует у датированного
   документа (`autofix-terminal-punctuation.md` — замер от 2026-08-12).
7. **Продуктовые документы стоят рядом с одноразовыми прод-задачами.**
   `docs/plans/2026-08-12-st2-summer-ru-glossary.md` (13 строк, «выгрузить CSV из
   ~/Downloads») лежит рядом с
   `docs/plans/2026-08-10-git-localization-quality-gate.md` (1778 строк,
   релизный гейт из фазы 3 роадмапа).

## Правило

Один путь, две оси, никаких исключений:

```
docs/<область>/<жанр>/<ГГГГ-ММ-ДД>-<слаг>.md
```

**Область** (верхний уровень) — кому и зачем документ принадлежит:

| Область | Что внутри | Критерий |
| --- | --- | --- |
| `llm-first/` | LLM-first TMS: видение, роадмап, судья, MT-machinery, слой автофиксов, гейты качества | документ меняет или исполняет фазу роадмапа `llm-first/vision/llm-first-product-architecture.md` |
| `product/` | функции самого форка Weblate: loc-kit, глоссарные UI, чеки, выгрузки, dev-окружение | продуктовая работа над инструментом, роадмап её не считает |
| `operations/` | работа с живыми инстансами и конкретными играми: прод-задачи, LQA-аудиты, отчёты, встречи | привязан к проекту/инстансу, а не к коду |
| `guides/` | вечнозелёные контракты и инструкции для людей | читается снаружи, не датируется, обновляется на месте |

**Жанр** (второй уровень) — закрытый список:

| Жанр | Что это |
| --- | --- |
| `plans/` | согласованный план реализации по задачам, со статусом |
| `designs/` | дизайн-решение без списка задач |
| `measurements/` | числа с прогона: воспроизводимо, датировано |
| `research/` | синтез внешних источников или чужой системы |
| `reviews/` | ревизия плана/дизайна/кода другим агентом |
| `audits/` | LQA-аудит живого компонента |
| `reports/` | статус или сводка для человека |
| `meetings/` | материалы встречи с командой игры |
| `archive/` | устаревшее, оставлено для истории (заменяет `plans/old`) |
| `vision/` | видение и роадмап (только в `llm-first/`) |

Правила имён:

- каталоги — строчными через дефис: `llm-first` (было `LLM-first`), `research`
  (было `agent_researches`);
- датированный снимок (план, замер, аудит, встреча) — всегда `ГГГГ-ММ-ДД-слаг.md`,
  дата **в начале**;
- живой документ, который правится на месте (роадмап, гайд, контракт), — **без
  даты**;
- несколько документов за один день нумеруются `ГГГГ-ММ-ДД-NN-слаг.md`
  (как уже сделано в `2026-08-13-01-...`, `2026-08-22-02-...`);
- жанр из имени не убираем там, где он уже есть (`-design`): пара
  «дизайн + план» с одинаковым слагом внутри разных каталогов читается
  двусмысленно в 462 текстовых ссылках.

## Целевое дерево

```
docs/
  llm-first/
    vision/         3   роадмап (без даты) + два продуктовых исследования
    plans/         16   планы фаз 0-4, включая 4 переехавших из docs/plans/
    designs/        2
    measurements/  14
    research/       5
    reviews/        2
    archive/        3   было plans/old/
  product/
    plans/         23
    designs/        3
    research/       1
  operations/
    plans/         17
    audits/         3
    reports/        1
    meetings/       2
  guides/           4   вечнозелёные, без дат
  specs/                ТОЛЬКО апстрим: openapi.yaml + schemas/*.json
  <остальное>           апстримовое дерево Sphinx (.rst) не трогаем
```

Скрипты и данные уходят из `docs/` в новый корневой каталог:

```
analysis/
  probes/    37   было docs/misc/*.py, docs/misc/col4-fr-monitor.sh
  data/      30   было docs/misc/*.{json,jsonl,tsv,csv,log}
    st2-zh-recal/     85
    st2-zh-verdicts/  10
```

Почему данные едут вместе со скриптами, а не остаются в `docs/`: это входы и
выходы харнессов (`col4-judge-golden.json` читается
`col4-judge-eval.py`), они не документация и уже исключены из линтеров и REUSE
теми же правилами, что и скрипты. `docs/misc/__pycache__` (6 `.pyc`, не в
индексе) удаляется.

## Полная карта переноса markdown

| Было | Станет |
| --- | --- |
| `docs/specs/continuous-localization-loop.md` | `docs/guides/continuous-localization-loop.md` |
| `docs/specs/game-repo-integration-contract.md` | `docs/guides/game-repo-integration-contract.md` |
| `docs/specs/loc-kit-ingest.md` | `docs/guides/loc-kit-ingest.md` |
| `docs/specs/producer-guide-weblate.md` | `docs/guides/producer-guide.md` |
| `docs/LLM-first/plans/old/2026-08-05-routed-llm-machinery.md` | `docs/llm-first/archive/2026-08-05-routed-llm-machinery.md` |
| `docs/LLM-first/plans/old/2026-08-07-project-scoped-llm-context.md` | `docs/llm-first/archive/2026-08-07-project-scoped-llm-context.md` |
| `docs/LLM-first/plans/old/llm-judge-external-pipeline.md` | `docs/llm-first/archive/llm-judge-external-pipeline.md` |
| `docs/LLM-first/2026-08-05-routed-llm-machinery-design.md` | `docs/llm-first/designs/2026-08-05-routed-llm-machinery-design.md` |
| `docs/LLM-first/2026-08-13-judge-native-ui-design.md` | `docs/llm-first/designs/2026-08-13-judge-native-ui-design.md` |
| `docs/specs/2026-08-11-col4-fr-autotranslate-report.md` | `docs/llm-first/measurements/2026-08-11-col4-fr-autotranslate-report.md` |
| `docs/specs/2026-08-11-glossary-enforcement-analysis.md` | `docs/llm-first/measurements/2026-08-11-glossary-enforcement-analysis.md` |
| `docs/misc/autofix-terminal-punctuation.md` | `docs/llm-first/measurements/2026-08-12-autofix-terminal-punctuation.md` |
| `docs/misc/col4-judge-annotation.md` | `docs/llm-first/measurements/2026-08-12-col4-judge-annotation.md` |
| `docs/LLM-first/2026-08-13-phase0-measurements.md` | `docs/llm-first/measurements/2026-08-13-phase0-measurements.md` |
| `docs/misc/2026-08-14-st2-zh-judge-run.md` | `docs/llm-first/measurements/2026-08-14-st2-zh-judge-run.md` |
| `docs/misc/2026-08-14-minto-summary.md` | `docs/llm-first/measurements/2026-08-14-st2-zh-judge-summary.md` |
| `docs/LLM-first/2026-08-18-severity-recalibration-measurements.md` | `docs/llm-first/measurements/2026-08-18-severity-recalibration-partial.md` |
| `docs/LLM-first/2026-08-18-severity-recalibration-status.md` | `docs/llm-first/measurements/2026-08-18-severity-recalibration-status.md` |
| `docs/LLM-first/2026-08-19-severity-recalibration-final.md` | `docs/llm-first/measurements/2026-08-19-severity-recalibration-final.md` |
| `docs/misc/2026-08-20-judge-dev-test-scenario-col4.md` | `docs/llm-first/measurements/2026-08-20-judge-dev-test-scenario-col4.md` |
| `docs/misc/judge-first-dev-run-2026-08-20.md` | `docs/llm-first/measurements/2026-08-20-judge-first-dev-run.md` |
| `docs/misc/2026-08-20-judge-prompt-universalization-run.md` | `docs/llm-first/measurements/2026-08-20-judge-prompt-universalization-run.md` |
| `docs/LLM-first/agent_researches/measurements-report.md` | `docs/llm-first/measurements/judge-measurements-index.md` |
| `docs/plans/2026-08-10-git-localization-quality-gate.md` | `docs/llm-first/plans/2026-08-10-git-localization-quality-gate.md` |
| `docs/plans/2026-08-11-glossary-morphological-enforcement.md` | `docs/llm-first/plans/2026-08-11-glossary-morphological-enforcement.md` |
| `docs/LLM-first/plans/2026-08-11-layer0-autofix-quick-wins.md` | `docs/llm-first/plans/2026-08-11-layer0-autofix-quick-wins.md` |
| `docs/LLM-first/plans/2026-08-11-phase0-schema-and-judge-calibration.md` | `docs/llm-first/plans/2026-08-11-phase0-schema-and-judge-calibration.md` |
| `docs/LLM-first/plans/2026-08-12-phase0-implementation.md` | `docs/llm-first/plans/2026-08-12-phase0-implementation.md` |
| `docs/LLM-first/plans/2026-08-13-01-judge-verdict-core.md` | `docs/llm-first/plans/2026-08-13-01-judge-verdict-core.md` |
| `docs/LLM-first/plans/2026-08-14-intra-component-consistency-check.md` | `docs/llm-first/plans/2026-08-14-intra-component-consistency-check.md` |
| `docs/LLM-first/plans/2026-08-14-judge-severity-recalibration.md` | `docs/llm-first/plans/2026-08-14-judge-severity-recalibration.md` |
| `docs/plans/2026-08-14-llm-usage-tracking.md` | `docs/llm-first/plans/2026-08-14-llm-usage-tracking.md` |
| `docs/LLM-first/plans/2026-08-15-llm-first-auto-translation-rollout.md` | `docs/llm-first/plans/2026-08-15-llm-first-auto-translation-rollout.md` |
| `docs/LLM-first/plans/2026-08-17-session-canon.md` | `docs/llm-first/plans/2026-08-17-session-canon.md` |
| `docs/LLM-first/plans/2026-08-20-judge-dialog-context.md` | `docs/llm-first/plans/2026-08-20-judge-dialog-context.md` |
| `docs/LLM-first/plans/2026-08-20-judge-prompt-universalization.md` | `docs/llm-first/plans/2026-08-20-judge-prompt-universalization.md` |
| `docs/LLM-first/plans/2026-08-22-02-judge-navigation-readiness.md` | `docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md` |
| `docs/plans/2026-08-22-03-judge-review-gate.md` | `docs/llm-first/plans/2026-08-22-03-judge-review-gate.md` |
| `docs/LLM-first/plans/2026-08-23-litellm-provider-and-judge-endpoint.md` | `docs/llm-first/plans/2026-08-23-litellm-provider-and-judge-endpoint.md` |
| `docs/LLM-first/2026-08-11-cathedral-localizer-analysis.md` | `docs/llm-first/research/2026-08-11-cathedral-localizer-analysis.md` |
| `docs/LLM-first/agent_researches/2026-08-11-judge-ux-competitor-research.md` | `docs/llm-first/research/2026-08-11-judge-ux-competitor-research.md` |
| `docs/LLM-first/agent_researches/2026-08-11-judge-weblate-ui-integration.md` | `docs/llm-first/research/2026-08-11-judge-weblate-ui-integration.md` |
| `docs/LLM-first/agent_researches/2026-08-11-llm-judge-design-research.md` | `docs/llm-first/research/2026-08-11-llm-judge-design-research.md` |
| `docs/LLM-first/2026-08-20-judge-prompt-best-practices.md` | `docs/llm-first/research/2026-08-20-judge-prompt-best-practices.md` |
| `docs/specs/2026-08-11-llm-first-prompt-and-pipeline-review.md` | `docs/llm-first/reviews/2026-08-11-llm-prompt-and-pipeline-review.md` |
| `docs/LLM-first/plans/2026-08-13-01-judge-verdict-core-review-archdoc.md` | `docs/llm-first/reviews/2026-08-13-judge-verdict-core-archdoc-review.md` |
| `docs/LLM-first/llm-first-product-research.md` | `docs/llm-first/vision/2026-08-10-llm-first-product-research.md` |
| `docs/LLM-first/2026-08-15-llm-first-producer-product-research.md` | `docs/llm-first/vision/2026-08-15-producer-first-product-research.md` |
| `docs/LLM-first/llm-first-product-architecture.md` | `docs/llm-first/vision/llm-first-product-architecture.md` |
| `docs/misc/heart-abyss-hub-1-translation-qa.md` | `docs/operations/audits/2026-08-20-heart-abyss-hub-1-translation-qa.md` |
| `docs/misc/2026-08-22-ui-multilingual-lqa-de-fr.md` | `docs/operations/audits/2026-08-22-ui-multilingual-lqa-de-fr.md` |
| `docs/misc/2026-08-22-victory-banner-common-de-lqa-audit.md` | `docs/operations/audits/2026-08-22-victory-banner-common-de-lqa.md` |
| `docs/specs/2026-08-07-space-arena-dev-onboarding.md` | `docs/operations/meetings/2026-08-07-space-arena-dev-onboarding.md` |
| `docs/specs/2026-08-14-pirate-ships-production-rollout.md` | `docs/operations/meetings/2026-08-14-pirate-ships-production-rollout.md` |
| `docs/plans/2026-08-12-st2-summer-ru-glossary.md` | `docs/operations/plans/2026-08-12-st2-summer-ru-glossary.md` |
| `docs/plans/2026-08-12-st2-terminology-glossary.md` | `docs/operations/plans/2026-08-12-st2-terminology-glossary.md` |
| `docs/misc/st2-zh-corrected-export-2026-08-14.md` | `docs/operations/plans/2026-08-14-st2-zh-corrected-export.md` |
| `docs/plans/2026-08-15-pirate-ships-local-json-component.md` | `docs/operations/plans/2026-08-15-pirate-ships-local-json-component.md` |
| `docs/plans/2026-08-15-pirate-ships-production-cleanup-json-migration.md` | `docs/operations/plans/2026-08-15-pirate-ships-production-cleanup-json-migration.md` |
| `docs/plans/2026-08-16-pirate-ships-json-zero-checks.md` | `docs/operations/plans/2026-08-16-pirate-ships-json-zero-checks.md` |
| `docs/plans/2026-08-17-nfg-dev-to-prod-components.md` | `docs/operations/plans/2026-08-17-nfg-dev-to-prod-components.md` |
| `docs/plans/2026-08-17-outgoing-mail-webnotify.md` | `docs/operations/plans/2026-08-17-outgoing-mail-webnotify.md` |
| `docs/plans/2026-08-18-space-arena-language-aliases-and-sourceless-keys.md` | `docs/operations/plans/2026-08-18-space-arena-language-aliases-and-sourceless-keys.md` |
| `docs/plans/2026-08-18-space-arena-lockit-english-terms-and-residual-fixes.md` | `docs/operations/plans/2026-08-18-space-arena-lockit-english-terms-and-residual-fixes.md` |
| `docs/plans/2026-08-18-space-arena-lockit-zero-checks.md` | `docs/operations/plans/2026-08-18-space-arena-lockit-zero-checks.md` |
| `docs/plans/2026-08-18-spaceship-battles-glossary.md` | `docs/operations/plans/2026-08-18-spaceship-battles-glossary.md` |
| `docs/plans/2026-08-19-space-arena-game-number-and-same-noise.md` | `docs/operations/plans/2026-08-19-space-arena-game-number-and-same-noise.md` |
| `docs/plans/2026-08-19-space-arena-lockit-producer-view-squadrons.md` | `docs/operations/plans/2026-08-19-space-arena-lockit-producer-view-squadrons.md` |
| `docs/plans/2026-08-19-space-arena-source-language-to-english.md` | `docs/operations/plans/2026-08-19-space-arena-source-language-to-english.md` |
| `docs/plans/2026-08-19-space-arena-terminal-punctuation.md` | `docs/operations/plans/2026-08-19-space-arena-terminal-punctuation.md` |
| `docs/plans/2026-08-24-vpn-gateway-self-healing.md` | `docs/operations/plans/2026-08-24-vpn-gateway-self-healing.md` |
| `docs/misc/2026-08-21-token-report.md` | `docs/operations/reports/2026-08-21-agent-token-report.md` |
| `docs/plans/2026-08-10-loc-kit-glossary-deterministic-infer-design.md` | `docs/product/designs/2026-08-10-loc-kit-glossary-deterministic-infer-design.md` |
| `docs/plans/2026-08-10-loc-kit-glossary-note-column-design.md` | `docs/product/designs/2026-08-10-loc-kit-glossary-note-column-design.md` |
| `docs/plans/2026-08-13-autofix-backfill-scoped-commit-design.md` | `docs/product/designs/2026-08-13-autofix-backfill-scoped-commit-design.md` |
| `docs/plans/2026-08-06-loc-kit-ingest.md` | `docs/product/plans/2026-08-06-loc-kit-ingest.md` |
| `docs/plans/2026-08-07-loc-kit-keyless-and-glossary-ui.md` | `docs/product/plans/2026-08-07-loc-kit-keyless-and-glossary-ui.md` |
| `docs/plans/2026-08-10-game-line-break-rule.md` | `docs/product/plans/2026-08-10-game-line-break-rule.md` |
| `docs/plans/2026-08-10-loc-kit-glossary-deterministic-infer.md` | `docs/product/plans/2026-08-10-loc-kit-glossary-deterministic-infer.md` |
| `docs/plans/2026-08-10-loc-kit-glossary-note-column.md` | `docs/product/plans/2026-08-10-loc-kit-glossary-note-column.md` |
| `docs/plans/2026-08-10-loc-kit-glossary-term-description-shape.md` | `docs/product/plans/2026-08-10-loc-kit-glossary-term-description-shape.md` |
| `docs/plans/2026-08-10-loc-kit-glossary-update-existing.md` | `docs/product/plans/2026-08-10-loc-kit-glossary-update-existing.md` |
| `docs/plans/2026-08-12-glossary-language-overview.md` | `docs/product/plans/2026-08-12-glossary-language-overview.md` |
| `docs/plans/2026-08-12-glossary-terminology-flag.md` | `docs/product/plans/2026-08-12-glossary-terminology-flag.md` |
| `docs/plans/2026-08-12-loc-kit-glossary-smarter-inference.md` | `docs/product/plans/2026-08-12-loc-kit-glossary-smarter-inference.md` |
| `docs/plans/2026-08-13-autofix-backfill-scoped-commit.md` | `docs/product/plans/2026-08-13-autofix-backfill-scoped-commit.md` |
| `docs/plans/2026-08-15-json-zip-download-button.md` | `docs/product/plans/2026-08-15-json-zip-download-button.md` |
| `docs/plans/2026-08-17-game-number-check.md` | `docs/product/plans/2026-08-17-game-number-check.md` |
| `docs/plans/2026-08-17-microsoft-clarity-session-recordings.md` | `docs/product/plans/2026-08-17-microsoft-clarity-session-recordings.md` |
| `docs/plans/2026-08-18-loc-kit-table-add-strings.md` | `docs/product/plans/2026-08-18-loc-kit-table-add-strings.md` |
| `docs/plans/2026-08-20-persistent-task-progress.md` | `docs/product/plans/2026-08-20-persistent-task-progress.md` |
| `docs/plans/2026-08-22-02-weblate-lqa-review-scope.md` | `docs/product/plans/2026-08-22-02-weblate-lqa-review-scope.md` |
| `docs/plans/2026-08-22-multilingual-spreadsheet-exchange.md` | `docs/product/plans/2026-08-22-multilingual-spreadsheet-exchange.md` |
| `docs/plans/2026-08-22-multilingual-spreadsheet-review-fixes.md` | `docs/product/plans/2026-08-22-multilingual-spreadsheet-review-fixes.md` |
| `docs/plans/2026-08-22-nested-game-placeholder-protection.md` | `docs/product/plans/2026-08-22-nested-game-placeholder-protection.md` |
| `docs/plans/2026-08-22-prek-clean-worktree-readiness.md` | `docs/product/plans/2026-08-22-prek-clean-worktree-readiness.md` |
| `docs/plans/2026-08-22-weblate-lqa-skill.md` | `docs/product/plans/2026-08-22-weblate-lqa-skill.md` |
| `docs/plans/2026-08-23-prek-full-pass-remaining-hooks.md` | `docs/product/plans/2026-08-23-prek-full-pass-remaining-hooks.md` |
| `docs/specs/google-sheets-to-weblate-migration.md` | `docs/product/research/google-sheets-to-weblate-migration.md` |

## Что остаётся на месте

- Всё апстримовое дерево Sphinx: `docs/admin/`, `docs/user/`, `docs/devel/`,
  `docs/contributing/`, `docs/formats/`, `docs/security/`, `docs/snippets/`,
  `docs/changes/`, `docs/*.rst`, `docs/conf.py`, `docs/_ext/`, `docs/images/`,
  `docs/screenshots/`, `docs/locales/`.
- `docs/specs/openapi.yaml` и `docs/specs/schemas/*.json` — путь зашит в
  `docs/contributing/schemas.rst`, `.github/workflows/api.yml`,
  `.github/workflows/apply-maintenance-patch.yml` (regex allowlist),
  `REUSE.toml` и `pyproject.toml`. После переезда md-файлов `docs/specs/`
  становится каталогом только этих артефактов.

## Обновление ссылок

462 ссылки в 99 файлах перезаписываются скриптом по карте выше (точное
совпадение старого пути; проверка «в тексте не осталось ни одного старого
пути» — обязательная приёмка). В том же проходе закрываются 32 битые ссылки,
из них механически исправимые:

- `docs/specs/producer-guide.md` → `docs/guides/producer-guide.md`
  (10 ссылок; файл всё время звался `producer-guide-weblate.md`);
- `plans/...` относительно `docs/LLM-first/` там, где файл лежит в
  `docs/plans/` (6 ссылок, включая фазу 1 роадмапа);
- `docs/misc/st2-zh-units.json` → `.jsonl`, `col4-b0-annotations.json` → `.jsonl`
  (3 ссылки);
- `docs/LLM-first/2026-08-14-st2-zh-judge-run.md` → фактический путь в
  `llm-first/measurements/` (2 ссылки).

`plans/2026-08-13-02-judge-navigation-and-readiness.md` указывает на файл,
который в итоге вышел как
`docs/llm-first/plans/2026-08-22-02-judge-navigation-readiness.md`, — ссылка
переписана на него.

Остальные ведут на артефакты, которых в репозитории нет: они получают путь по
новой конвенции (там, где файл лежал бы), а проверка ссылок держит их в явном
списке исключений:

- `analysis/probes/col4-b0-dump.py` — дамп фазы 0, не закоммичен;
- `analysis/data/col4-schema-eval.json` — вывод плеча A2, прогон остановлен;
- `docs/llm-first/measurements/2026-08-XX-judge-calibration.md` — сводный отчёт
  фазы 0, вместо него вышли три документа замеров;
- `docs/llm-first/measurements/2026-08-20-judge-dialog-context-run.md` — замер
  плеча H2, план ещё не одобрен;
- `docs/llm-first/archive/2026-08-10-llm-first-p1-setup.md` — удалён;
- `docs/llm-first/plans/2026-08-13-03-judge-decisions-and-whitebox.md` — третий
  план нарезки судьи, не написан.

## Обновление кода и конфигов

| Файл | Что меняется |
| --- | --- |
| `AGENTS.md` (`CLAUDE.md` — симлинк) | строка 16 (куда кладём план), 214-217 (описание дерева), 233 (`docs/specs/loc-kit-ingest.md`), 404 (`openapi.yaml` — без изменений) |
| `README.rst:38` | секция ``docs/plans/`` → новое дерево |
| `.claude/skills/weblate-docs/SKILL.md:30` | список путей |
| `pyproject.toml` | `[tool.check-manifest] ignore`: добавить `analysis/*`, `analysis/*/*`, `analysis/*/*/*`; `[tool.codespell] skip`: два пути; `[tool.ruff.lint.per-file-ignores]`: `docs/misc/*.py` → `analysis/probes/*.py`; `[tool.typos.files] extend-exclude`: 4 записи `docs/misc/*` → `analysis/data/*` |
| `REUSE.toml` | шесть `docs/misc/**.*` → `analysis/**`; заодно убрать мёртвые `misc/**.json`, `misc/**.md`, `misc/**.tsv` (корневого `misc/` в репозитории нет) |
| `weblate/checks/morphology.py:23,100` | `docs/specs/2026-08-11-glossary-enforcement-analysis.md` → `docs/llm-first/measurements/...` |
| `weblate/checks/tests/test_glossary_checks.py:271`, `weblate/glossary/tests.py:1233` | путь плана морфологии |
| `weblate/glossary/tests.py:1363` | «the probes in `docs/misc`» → `analysis/probes` |
| `weblate/machinery/llm.py:321`, `weblate/machinery/tests.py:4156` | `docs/misc/col4-batch-size-eval.json` → `analysis/data/...` |
| `weblate/trans/judge.py:146,159` | два пути в `analysis/probes` и `docs/llm-first/measurements` |
| `weblate_customization/tests/test_machinery.py:189` | `docs/LLM-first/plans/2026-08-12-phase0-implementation.md` |
| `analysis/probes/col4-fr-monitor.sh:15` | дефолт `OUT=docs/misc/col4-fr-monitor.log` → `analysis/data/...`; `cd "$(dirname "$0")/../.."` остаётся корректным (глубина та же) |
| `analysis/probes/*.py` | пути в docstring'ах и примерах запуска (13 файлов) |

## Порядок работ

1. `git mv` по карте (markdown), затем `git mv` скриптов и данных в `analysis/`;
   переименование `docs/LLM-first` делается через промежуточное имя — на APFS
   регистр не различается.
2. Скрипт перезаписи ссылок по карте + список из «битых» выше.
3. Правки конфигов и кода из таблицы.
4. Этот план переезжает вместе с остальными:
   `docs/product/plans/2026-08-24-docs-structure-convention.md`.
5. Приёмка (ниже).
6. Один коммит `docs: single convention for fork documentation` + пуш.

## Приёмка — результат

- `docs/plans/`, `docs/misc/`, `docs/LLM-first/` больше не существуют;
  `docs/specs/` содержит только `openapi.yaml` и `schemas/`. **OK**
- Старые пути (`docs/plans/`, `docs/misc/`, `docs/LLM-first`,
  `agent_researches`) не встречаются ни в одном файле, кроме этого плана, где
  колонка «Было» — исторический факт. **OK**
- Все 611 ссылок вида `docs/...`/`analysis/...` из markdown и probe-скриптов
  указывают на существующий файл, кроме шести артефактов из списка исключений
  выше. **OK** (было 462 ссылки и 32 битые).
- Содержимое перенесённых файлов побайтово равно версии из `HEAD` с наложенной
  заменой путей: пересчитано программно для всех 262 файлов, расхождений нет.
  Это же вернуло пять markdown-файлов, которые `rumdl fmt` успел
  переформатировать во время первого прогона линтеров. **OK**
- `uv run prek run --all-files` на `HEAD` **не зелёный** (падают `ruff-check`,
  `ruff-format`, `doccmd`, `reuse`, `codespell`, `typos`, `rumdl`,
  `rumdl-fmt`), поэтому критерий — «не добавить ни одной новой находки».
  Сравнение по каждому хуку против чистого worktree на `HEAD`: новых находок
  нет ни в одном (`ruff-check` 719 ошибок в обоих, `typos` и `rumdl` — тот же
  набор, `reuse` — тот же единственный файл `weblate_customization/uv.lock`,
  `doccmd` — тот же parse error на `2026-08-13-01-judge-verdict-core.md:641`,
  проверено на `HEAD` отдельным прогоном). `codespell` из падающего стал
  зелёным: его `skip` указывал на несуществующий путь
  `misc/heart-abyss-hub-1-translation-qa.md`. **OK**
- `./rundev.sh test weblate/checks/tests/test_glossary_checks.py
  weblate_customization/tests/test_machinery.py` — 60 passed. **OK**
- Ни один `.rst` не ссылается на перенесённые файлы (проверено grep'ом), так
  что сборка Sphinx не затронута: форковый markdown в неё не входил и не
  входит — `myst_parser` не подключён. **OK**

## Согласованные решения

1. **Области**: `llm-first` / `product` / `operations` / `guides`
   (выбрано `product`, не `platform`).
2. **Каталог вне docs**: новый корневой `analysis/{probes,data}`.
3. **Данные плоско**: `analysis/data/*` плюс два существующих подкаталога
   прогонов `st2-zh-recal/` и `st2-zh-verdicts/`.
4. Суффикс `-design` сохраняем: иначе пара «дизайн/план» получает одинаковые
   имена в разных каталогах, а в тексте 462 ссылки читаются двусмысленно.
5. `producer-guide-weblate.md` → `guides/producer-guide.md`: это чинит 10
   существующих битых ссылок, которые уже пишут `producer-guide.md`.
6. `docs/specs/` остаётся апстримовым (только `openapi.yaml` + `schemas/`);
   11 форковых md уезжают в `guides/`, `llm-first/` и `operations/`.

## Вне объёма

- Язык документов (сейчас смесь русского и английского) — не меняется.
- Содержимое документов не редактируется: только пути, ссылки и имена файлов.
- Апстримовое дерево Sphinx и `docs/changes.rst` не трогаются.
- Новые документы не создаются, старые не удаляются (кроме незакоммиченного
  `docs/misc/__pycache__`).
