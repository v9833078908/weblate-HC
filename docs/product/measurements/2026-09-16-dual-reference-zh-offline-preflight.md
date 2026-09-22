# RU/EN → zh-Hans: offline preflight screening

Дата: 2026-09-16.

**Обновление 2026-09-17:** этот документ сохраняет историю preflight, v1 и
утраченного v2. Владелец разрешил малый сквозной прогон v3, затем полный
screening-пилот при его успехе. Актуальное исполнение описано в
`docs/product/measurements/2026-09-17-dual-reference-zh-v3.md`.

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

## Intermediate v2 run status (2026-09-17)

This is a progress record, not a results table. No conclusion for H1, H2 or H3
may be drawn from it.

After the OpenRouter monthly limit was raised, a synthetic Gemini smoke against
the effective production OpenRouter configuration returned HTTP 200 with
`finish_reason=stop`. The v2 runner then revalidated all 120 frozen RU/EN Unit
pairs in production before inference; the result was `validated_records: 120`
and `inference: false`.

The single full v2 run was started at 2026-09-17 10:09 (server time, UTC+02:00).
At this report's snapshot its host wrapper, `docker exec`, and Python process
were still active for about 18 minutes. The runner has not written its final
raw JSON artifact and its stderr log is empty. That is expected from this
version of the runner, which serializes one artifact at completion; it does not
prove that any particular arm or batch has completed.

The planned first-attempt volume remains 144 Gemini/OpenRouter requests (720
generation/edit segment operations) and 384 LiteLLM requests (480 review plus
1,440 blind proxy-rating segment operations), or 528 requests in total. The
registered retry policy can at most add one eligible retry per request; it does
not retry a `403`.

### Registered runtime deviation

The direct v2 runner sends `stream: false` to LiteLLM even though the frozen
production judge profile resolves `stream: true`. It still uses the two frozen
LiteLLM aliases, temperature zero, the same response envelope and identical
behavior for E and F, so this does not expose an arm-specific treatment.
Nevertheless it is a profile deviation and must be reported with the final
screening result. It must not be silently described as a byte-for-byte replay
of the production judge client.

The run reads Units and configuration only. It does not create or update
Weblate translations, suggestions, changes, or judge-run records. Raw results
remain temporary inside the production container until the completed artifact
can be transferred to the local study directory without entering Git.

## Stopped-run synthesis (2026-09-17)

The owner stopped further inference. There is no active v2 runner and no v2
final artifact. The process terminated while serializing its in-memory result:
the retry journal held a circular reference between a failure metadata object
and its `attempts` list. This is a runner defect, not a model outcome. Its
in-memory responses are unavailable after process exit, so v2 contributes no
usable outcomes, costs or H1--H3 observations.

### What the experiment established

| Question | Evidence | Result |
| --- | --- | --- |
| Can the approved corpus be frozen and matched safely? | Offline checks and the v2 production preflight matched 120/120 RU/EN pairs. | Yes, for screening. |
| Did OpenRouter `403` identify a method failure? | Synthetic smoke identified an exhausted monthly key limit; later smoke returned HTTP 200 after the limit was raised. | No; it was a provider-account condition. |
| Can the two LiteLLM judge routes answer a minimal contract? | Both seats returned HTTP 200 with `finish_reason=stop` in synthetic smoke. | Yes, but this does not explain the historical batch `504`. |
| Does reviewed EN beat RU, or does RU reference improve ZH? | v1 has incomplete, time-confounded proxy outputs only. | Not established. |

### What can be reported from v1

The sole preserved model artifact is v1. It generated valid translations for
90/120 A records, 100/120 B records and 85/120 C records; D, E and F had no
valid outputs. On 65 complete paired, double-rated proxy records, B--A was
-1.54 percentage points (2 versus 3 unusable) and C--B was -3.08 percentage
points (2 versus 4 unusable). The observed unusable-rate ITT bounds were
3.3%--32.5% for A, 3.3%--24.2% for B and 2.5%--31.7% for C. H3 is unmeasured.

These differences are directional complete-case proxy observations, not H1 or
H2 results: missingness is extensive, provider failures were confounded with
the serial arm order, and the intervals overlap. They must not guide a source
language change or a product rollout.

### Final status and decision boundary

G0 and G1 are closed for the 120-record screening corpus. G2 was satisfied for
the attempted screening runs only. G3 is **open**: there is no complete,
reproducible pilot. G4 and G5 are also open. The corpus remains ineligible for
confirmatory analysis because all 120 records lack established RU/EN human
review provenance and independent Chinese LQA.

No further model calls are planned under this stopped run. A future v3 would
need a separately reviewed runner that writes durable, append-only output after
each batch, a fresh G2 registration, and a complete rerun from the beginning.
It must not pool v1 partial outcomes or lost v2 outputs with its results.

### Exploratory self-review of preserved outputs

At the owner's request, the agent performed an additional screening review of
the only preserved complete v1 subset. The sample consists of the first four
complete A/B/C records in stable `(project, component, record_id)` order from
each approved stratum: Need For Greed UI, Tutorial and Loot, and Heart Abyss
hub-1. It therefore covers 16 records and 48 candidate forms. Sampling was
independent of the prior proxy verdicts, but candidate arm labels were visible
to the reviewer; this is not a blinded or independent LQA sample.

Against both available RU and EN sources, the self-review found no major or
critical defect in the 48 forms. It noted only minor-risk observations:
alternative transliterations/levels of specificity for a character name,
dialogue-register variation, and two forms that render ``mare`` as the more
general ``马儿``. The UI string describing ``defense`` versus ``resistance``
also demonstrates a source-language difference rather than a translation
failure: A follows RU's ``защиту`` while B/C follow EN's ``resistance``.

The self-review supports only the narrow statement that this 48-form coverage
sample contains no obvious unusable output to this reviewer. It cannot estimate
an arm rate, resolve whether a source choice is preferable, verify terminology
consistency without game materials, measure H1/H2, or measure H3: no D/E/F
output exists. It is an agent self-review, not independent Chinese LQA and not
a replacement for the registered LiteLLM panel or human assessment.
