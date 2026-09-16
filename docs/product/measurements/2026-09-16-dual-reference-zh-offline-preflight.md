# RU/EN → zh-Hans: offline preflight screening

Дата: 2026-09-16.

## Scope

Владелец утвердил screening-корпус из 120 строк: Need For Greed UI, Tutorial и
Loot — по 30; Heart Abyss hub-1 dialogue — 30. Этот замер не является
model-run, LQA или выводом о качестве китайского перевода.

## Вход и воспроизводимость

Пакет: `/Users/eli/Downloads/dual-reference-zh-2026-09-16/`.

- `pilot-120.jsonl`:
  `649eee0cfd0079eca92e0cf1d08252f5c3af06de7a88564695483aca7ad0a474`.
- `candidate-pool.jsonl`:
  `58596b54b63f7932542bc9aa61e693d7a9793bb3f3c5cdf7be9c91359f352500`.
- Офлайн-валидатор: 29 совпавших хешей, 120 уникальных pilot rows, 4 квоты по
  30, 1113 выровненных кандидатов, две полные диалоговые группы.

Study-artifacts хранятся только локально, вне Git:
`/Users/eli/Downloads/dual-reference-zh-2026-09-16/study/2026-09-16-screening-v1/`.
Они содержат raw строки и не должны коммититься или направляться во внешние
сервисы до G2.

## Результат dry-run

Offline-инструмент из `analysis/probes/dual_reference_zh/` создал 120 inputs,
120 eligibility records и 1 200 jobs. Распределение работ: 480 generation
(A–D), 480 review (E/F × два seat) и 240 edit (E/F). `dry-run.json` записал
`network_requests: 0`.

Тесты закрепляют treatment roles, общий B для E/F, отсутствие неявного
сопоставления по порядку, no-network dry-run, ITT bounds для missing ratings и
synthetic null/planted paired effect. Они не импортируют Django, Celery или
product machinery.

## Eligibility и ограничения

Все 120 строк включены в screening и исключены из confirmatory stratum.
Локальный metadata audit выявил:

- `human_review_status=unverified` у 120 строк;
- `authoring_language=unknown` у 120 строк;
- `semantic_alignment_review=pending` у 120 строк;
- `en_automatically_translated=true` у 47 строк;
- отсутствующие RU/EN формы и дубликаты нормализованного RU внутри компонента:
  0 и 0 соответственно.

Следовательно, screening способен проверить технический протокол и дать лишь
диагностические наблюдения. Он не проверяет гипотезу о превосходстве
вычитанного EN и не подтверждает профессиональное качество zh-Hans.

## Gates

G0 закрыт выбором корпуса и screening-режима. G1 структурно закрыт для
screening: snapshot воспроизводим, eligibility явный, confirmatory stratum
пуст. G2 открыт: отсутствуют точные безопасно полученные profiles Gemini и
LiteLLM, расходный лимит и зарегистрированное разрешение конкретного pilot
inference. Поэтому этот preflight не сделал model calls и не обращался к
production.
