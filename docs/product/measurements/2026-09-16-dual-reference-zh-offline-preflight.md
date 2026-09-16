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

## Screening pilot result

G2 was subsequently closed for screening only: the owner selected explicit
`google/gemini-3.7-flash`; production LiteLLM seats were redacted and frozen;
retry and fallback were disabled. The raw run artifact is local only at
`/Users/eli/Downloads/dual-reference-zh-2026-09-16/study/2026-09-16-screening-v1/model-run.json`.

| Arm | Assigned | Valid translation | Double-rated | Observed unusable | ITT lower--upper |
| --- | ---: | ---: | ---: | ---: | ---: |
| A | 120 | 90 | 85 | 4 | 3.3%--32.5% |
| B | 120 | 100 | 95 | 4 | 3.3%--24.2% |
| C | 120 | 85 | 85 | 3 | 2.5%--31.7% |
| D/E/F | 120 each | 0 | 0 | -- | 0%--100% |

On 65 complete paired proxy records, B--A was -1.54 pp (2 versus 3 unusable)
and C--B was -3.08 pp (2 versus 4). These are not H1/H2 results: missing
outcomes are extensive and the ITT bounds overlap. F--E is unmeasured.

Gemini/OpenRouter returned schema-truncation errors for parts of A--C and then
`403 Forbidden` for every D and E/F editor batch. LiteLLM seat 2 also returned
some `504 Gateway Time-out` responses. The run followed registration: no retry
and no fallback. Successful receipts total `$0.163776`; rejected/timeout calls
may lack receipts. H1--H3 are **not confirmed**.

## Recovery smoke and protocol v2

The incomplete pilot cannot be repeated as-is. Its A--D generation requests were
serial by arm, so a time-varying provider failure is confounded with treatment.
The original result retained only exception strings: it did not retain HTTP
request identifiers, safe error fields, response hashes/lengths, finish reasons
or per-request attempt history. Consequently its `403`, malformed JSON and
`504` observations were not enough to attribute a root cause.

A 2026-09-16 synthetic, no-corpus smoke reproduced the original Gemini request
shape against OpenRouter and identified the `403` as an exhausted monthly limit
on the configured production key. It is a provider-account condition, not an
arm, prompt or batch result. The smoke sent one minimal JSON request to each
production LiteLLM judge seat; both returned HTTP 200 with `finish_reason=stop`
and a request ID. It did not reproduce the historical `504`, so its cause
remains unproven. The old malformed-JSON responses were HTTP 200, but lack their
finish reasons; truncation is likewise unproven.

Protocol v2 (`analysis/probes/dual_reference_zh/remote_screening.py`) now:

- randomizes paired five-record blocks and treatment order for A--D, E/F review,
  and E/F edit with registered seed `20260916`;
- applies one retry rule to every route: at most two attempts, retrying only
  transport/malformed-response and `408`, `409`, `425`, `429`, or `5xx`; a
  `403` is terminal;
- records every attempt with stage/arm/block/seat, HTTP status, request ID,
  content type, body hash, and (where applicable) content hash/length and
  `finish_reason`; it excludes response bodies, prompts and error messages so a
  provider message cannot leak a key identifier.

The replacement 120-record screening pilot is **blocked** until an owner
provides an explicitly registered Gemini route with sufficient available quota
and its safe profile snapshot. After that, run the synthetic smoke again,
register any required batch/schema/limit change as protocol v2, then run all
arms from the beginning; do not combine v1 outcomes with v2.
