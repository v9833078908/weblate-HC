<!--
Copyright © HCGameLoc

SPDX-License-Identifier: GPL-3.0-or-later
-->

# Limits of a deterministic batch-alignment gate

Date: 2026-08-25. Corpora only, offline. No instance was touched, no provider
call was made.
Tool: `analysis/probes/batch-anchor-threshold.py`.

This measurement answers one question: how much of LLM batch misbinding can be
refused deterministically, before a reply is stored, without an LLM verifier and
without changing `batch_size`. It was run before writing any product code, and it
removed two thirds of the change it was meant to support.

## 1. Corpora

| Corpus | Pairs | Direction |
| --- | --- | --- |
| `analysis/data/heart-abyss-hub-1-units.tsv` | 396 + 396 | ru -> en, ru -> fr |
| `analysis/data/st2-zh-units.jsonl` | 124 | ru -> zh |
| `analysis/data/col4-b0-annotations.jsonl`, `label == "pass"` | 183 | ru -> fr |
| **Total treated as correctly aligned** | **1099** | |
| `analysis/data/col4-b0-annotations.jsonl`, `label == "defect"` | 77 | ru -> fr, human-annotated defects |

The `col4` set carries human MQM labels, so its `pass` records are the only
corpus here where "correctly aligned" is a human judgement rather than an
assumption.

## 2. Candidate gate

Two rules, both meant to run inside `BaseLLMTranslation` next to the placeholder
check that already refuses a batch:

* **numbers** - every ASCII-digit number in the source must survive into the
  target, with the comparison abstaining when either side uses non-ASCII digits,
  grouped thousands, a full date or a quantity spelled without digits;
* **tight `$`** - when the source uses `$` as the engine line separator, the
  target must carry the same count. Tightness follows
  `weblate_customization.checks.separator_is_tight`: a `$` with adjacent
  whitespace, or at either end of the string, is currency or prose and is not
  policed.

Placeholders and engine markup (`{0}`, `%KEY%`, `@@PHn@@`, `<size=14>`) are
masked before reading numbers, because their digits are stated by no
translation and the placeholder multiset is already validated separately.

## 3. Result: the number rule does not survive

| Corpus | Judged | Pass | Refuse | Abstain |
| --- | --- | --- | --- | --- |
| hub-1 ru -> en | 0 | 0 | 0 | 396 |
| hub-1 ru -> fr | 0 | 0 | 0 | 396 |
| st2-zh ru -> zh | 0 | 0 | 0 | 124 |
| col4 ru -> fr (human pass) | 0 | 0 | 0 | 183 |
| **All aligned pairs** | **0 of 1099** | 0 | 0 | 1099 |

The gate abstains on every correctly aligned pair in the repository. The cause
is coverage, not the guards: after masking placeholders and markup, 2 of 396
`heart-abyss/hub-1` sources contain a number at all, 1 of 124 on `st2-zh`, and
5 of 260 across the whole `col4` annotation set.

The only number verdict the probe produced anywhere was **wrong**. On the
human-defect bucket, `col4/data` `EVENT_516_RESULT_977` was refused for
`number_lost`: its source contains the XLSX escape `_x000b_` twice, from which
`\d+` reads the number `000`, while the target correctly renders the control
character as a newline. The annotator's finding on that record is
`newline-count`, not a number defect.

So the number rule is not merely unvalidatable on this corpus - it is
measurably unsafe, in the one case where it spoke. A raw digit comparison also
remains unguarded against localized digits, grouped thousands and dates in
languages these corpora do not contain, and it lacks the markup, date and
decimal normalization that `GameNumberCheck` documents. **Dropped.**

### 3.1 Correction to an earlier claim

An earlier note in this work said `st2-zh` was "48% anchored", implying that
much of it was newly checkable. That was wrong. 60 of 124 `st2-zh` sources
contain a raw digit, but only **1** still does after placeholders and markup are
masked: 59 of those 60 digits live inside `{...}`, `%...%` or `<...>` tokens,
which the existing placeholder gate already validates exactly. The number rule
adds nothing there.

## 4. Result: the tight `$` rule survives, on one string

Across 1099 aligned pairs: **0 false positives**, and no aligned source uses a
tight `$` at all. Every `$` outside the defect bucket is absent, so the rule
abstains by construction rather than by luck.

It fired once, correctly, on the defect bucket:

| Field | Value |
| --- | --- |
| Unit | `col4/data` `EVENT_408` |
| Source | `Очень хорошо сказано!$Сдохни!` |
| Target | `Très bien dit ! Meurs !` |
| Human label | `defect`, severity **critical**, `checks: ["game-line-break"]` |
| Critique | "потерян движковый разделитель $" |

This is the only `critical` record in the 260-record annotation set, and the
rule catches it. Coverage across every corpus is one source string, so this is a
true positive on a real defect and simultaneously a rule that will almost never
speak.

## 5. Result: duplicate targets inside one request

Windows of 10 consecutive units, matching the slicing in
`weblate/trans/autotranslate.py:235-238`. Counted: pairs of *different* sources
sharing one identical stripped target inside a window.

| Corpus | Windows | Collisions, any minimum length 0-60 |
| --- | --- | --- |
| hub-1 ru -> en | 40 | 0 |
| hub-1 ru -> fr | 40 | 0 |
| st2-zh ru -> zh | 13 | 0 |

Zero at every threshold, including no threshold at all - but this window count
walks the corpus in unfiltered unit order, which is **not** how a request is
built, so it must not be read as a safety result. See the correction below.

The probe therefore also enumerates every legitimate convergence in full, with
its target length and the distance between the two units.

| Corpus | Convergent pairs | Max target length | Min positional distance |
| --- | --- | --- | --- |
| hub-1 ru -> en | 1 | 17 | 218 |
| hub-1 ru -> fr | 1 | **27** | 136 |
| st2-zh ru -> zh | 0 | - | - |
| col4 ru -> fr (human pass) | 3 | 8 | 29 |
| **All aligned pairs** | **5** | **27** | **29** |

### 5.1 Correction: positional distance is not a safety margin

An earlier reading of this table claimed that a convergent pair 29 units apart
can never share a request, and therefore that distance rather than length bounds
the rule. **That is wrong.** A request is not a window over the component in
unit order: `AutoTranslate.get_units` (`weblate/trans/autotranslate.py:389-395`)
returns `unit_set` after `exclude(state=STATE_READONLY)`, an optional `unit_ids`
filter, a `suggestion__isnull=True` filter in suggest mode and
`.search(self.q, parser="unit")`, and `fetch_mt` batches *that* list
(`weblate/trans/autotranslate.py:629-659`). Eligibility filtering compresses
positions: if the units between two convergent strings are already translated or
excluded by the query, the two become adjacent in the batch and share one
request. Any pair in the table above can co-occur.

So the in-window collision count and the distance column describe the unfiltered
corpus only. They do not bound the rule.

### 5.2 What actually bounds the rule

Only the position-independent fact: **the longest legitimate convergence
measured is 27 characters.** It is two sources differing solely in a trailing
period - `Знаете ли вы, что его войска….` and `Знаете ли вы, что его войска…`,
both rendering as `Savez-vous que ses troupes…` (`hub1_scrappy_1_6`,
`hub1_scrappy_2_6`). The other four are short UI strings: `Don't even start.`
(17), `Observer` (8), `Écouter` (7) twice.

A minimum length of 40 sits above that 27 and far below the real defect, whose
targets exceed 100 characters. That margin is the rule's *only* protection, it
rests on **five** observed pairs in three language pairs, and it is a bounded
judgement rather than an optimum. All four real convergences are pinned as
regression fixtures the rule must accept.

Consequently the false-positive rate of this rule is **unproven**, not zero.
Setting the threshold too high costs sensitivity only; too low, and correct
strings are refused and re-asked at the price of regenerated output tokens.

## 6. Features rejected outright

| Feature | Why |
| --- | --- |
| terminal punctuation | 19 of 396 ru -> en and **67 of 396 ru -> fr** correctly aligned pairs change it. This is the separate `end_stop` defect class (18/28/63 of 150 strings at batch 20/10/5, `analysis/data/col4-batch-size-eval.json`), so a gate here would refuse roughly one French string in six for a punctuation defect. |
| target length | A zero-false-positive band needs a 3.8-6.6x length ratio. Measured expansion on hub-1: ru -> en p1/p99 = 0.62/1.80, ru -> fr 0.64/2.12. |
| pre-send batch partitioning by fingerprint | Degenerates to single-string requests on the content where it is needed: 394 requests against 40 on hub-1 (1.01 strings per request), 75 against 13 on st2-zh (1.65). |

## 7. What this does not establish

* It says nothing about the paraphrase class. The German rotation in
  `heart-abyss/hub-1` would not be refused by either surviving rule: those four
  sources carry no numbers and no separators, and differ only in terminal
  punctuation, which is disqualified above.
* It cannot bound false positives for languages absent from these corpora. Only
  ru -> en, fr, zh were measured.
* The price of `batch_size = 1`, the only lever that would close the paraphrase
  class structurally, **remains unmeasured**. The historical arms cannot predict
  it: fitting `cost = A * requests + C` on `analysis/data/col4-batch-size-eval.json`
  gives x2.09 through the batch-5 concurrency-4 arm, x3.36 through the
  concurrency-2 arm, and a negative per-request slope through the batch-20 arm,
  which paid for a content filter and two `json_error` retries. Two otherwise
  identical batch-5 arms differ 12.6% in cost purely by cache hits (61448
  against 78726 cached tokens), and the batch-10 baseline arm records no cached
  token count at all.
* Silence from either surviving rule is not evidence that a component is
  aligned.
