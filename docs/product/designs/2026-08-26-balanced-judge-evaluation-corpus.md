# A balanced judge evaluation corpus, drawn from production

**Date:** 2026-08-26. **Status:** proposed, needs a decision on scope and cost.
**Supplies:** Stage A of
`docs/llm-first/plans/2026-08-26-judge-provider-failover.md`, which cannot name
a seat pair until a scored instrument exists.
**Rule:** R3 - changing the prompt or the model invalidates the measurement.

## The Fable and ChatGPT annotations: already taken, in their only defensible role

They exist and they are already in the golden set - as the *clean* side of it,
never as the defect side.

`analysis/data/col4-b0-annotations.jsonl` and
`analysis/data/col4-b0-annotations-topup-20260812.jsonl`, both on col4 `data`
(ru->fr, dev mirror of the production run of 2026-08-11):

| annotator | rows | pass | defect | critical |
|---|---:|---:|---:|---:|
| `fable-anthropic-frontier-2026-08-12` | 260 | 183 | 77 | 1 |
| `openai-codex-gpt-5.6-terra-2026-08-12` | 300 | 253 | 47 | 1 |

`analysis/data/col4-judge-golden.json` then consumed them: of its 919 records,
419 carry `label_origin: annotation` and come from these two files, while all
500 defects carry `label_origin: construction`.

Three reasons their defect labels were not promoted to ground truth, all
visible in the data above:

1. **The two annotators disagree by a factor of two on prevalence** - 29.6%
   against 15.7% defect rate. Pooling them would make the defect rate an
   artefact of which annotator drew which unit.
2. **Their unit sets do not overlap at all.** Zero shared `unit_id`, so
   inter-annotator agreement cannot be computed even in principle. There is no
   way to know which calibration is right.
3. **Two criticals in 560 rows.** Recall on criticals is the number that
   decides a seat, and this set cannot measure it.

There is also a measured precedent against trusting model annotators here. The
zh ground truth records it in `analysis/data/st2-zh-judge-annotations.json`:
`claude-sonnet-5` and `gpt-5.4-mini` "reproduced the judges' blind spots: they
labelled 9/14 hand-confirmed defects below major", so they were "used only as a
completeness cross-check" and the human inventory became the truth.

**So: yes, take them, exactly as they are already taken.** They are a good
source of verified-clean strings, which is half of any recall instrument and
the half that is tedious to produce. Promoting their defect labels would
measure how well a judge agrees with a 2026-08-12 annotator, not how well it
catches defects.

## What the current instruments cover

| instrument | pair | units | defect labels | criticals |
|---|---|---:|---|---:|
| `st2-zh-groundtruth.json` | ru->zh_Hans | 124 | **human inventory** | 7 (6 in gate after `aae1397`) |
| `nfg-ui-fr-golden.json` | en->fr | 236 | construction | 27 |
| `col4-judge-golden.json` | ru->fr | 919 | construction | 182 |

Two targets, and one of them is French twice. The single human-labelled set is
the zh one, which is the set you do not want the decision to rest on. That is
the actual imbalance: not too much Chinese, but **one real-defect instrument,
and it happens to be Chinese**.

## Production inventory

Read-only, `https://l10n.herocraft.com/api`, 2026-08-26.

| project | source | languages | strings | failing checks |
|---|---|---:|---:|---:|
| space-arena | en | 15 | 78 045 | 11 |
| pirate-ships | ru | 16 | 62 576 | 23 |
| need-for-greed | en | 18 | 21 510 | 862 |
| col4 | ru | 6 | 20 685 | 687 |
| heart-abyss | ru | 11 | 8 455 | 277 |
| victory-banner | ru | 8 | 3 856 | 86 |
| strategy-and-tactics-2 | en, ru | 3 | 1 965 | 0 |

Genre is carried by component, and `need-for-greed` is the only project that
separates genres explicitly:

| component | source units | genre |
|---|---:|---|
| `ui` | 466 | short labels, placeholder-dense |
| `loot` | 154 | item names and stat lines, number-dense |
| `tutorial` | 102 | instructional imperative |
| `orders` | 102 | mission objectives, conditional DSL |
| `characterdialogue` | 25 | register and idiom |
| `buyers` | 10 | short, 116 failing checks - the densest defect pocket |

`space-arena/lockit` carries 4 864 translated units in **every** one of ja, ko,
zh_Hans, fa, de, tr, th, hi - the only slice with that script spread.
`heart-abyss/hub-1` carries ~394 in ja, ko, zh_Hans, de from a Russian source.
`col4/data` carries 3 941 in tr, id, fr, also Russian source.

Note `heart-abyss/temple` has zero units in ja/ko/zh: those languages live only
in `hub-1`.

## Proposal

### Six pairs, chosen for script and typology rather than volume

| pair | donor | why this pair |
|---|---|---|
| en->de | `need-for-greed`, all 6 genres | Latin, compounding, worst case for length budgets |
| en->tr | `need-for-greed`, all 6 genres | agglutinative, placeholder order moves |
| en->ja | `space-arena/lockit` | no word spacing, counter words - where `game-number` reasoning lives |
| en->fa | `space-arena/lockit` | RTL and bidi, a failure surface nothing else covers |
| ru->ko | `heart-abyss/hub-1` | honorific register from a Russian source |
| ru->zh_Hans | `strategy-and-tactics-2`, existing | **retained as the control**, the only human-labelled set |

Two source languages, six scripts, and the incumbent zh anchor kept so the new
instrument can be compared against a measurement we already trust. Dropping it
would leave nothing to calibrate the new sets against.

French is deliberately absent: two French instruments already exist, and a
third would add cost without adding coverage.

### 150 units per pair, 90 clean and 60 defective

Mirrors the shape of `nfg-ui-fr-golden.json`, which is already validated:
per pair, 90 clean passes and 60 constructed defects, of which roughly 25
critical and 35 major. Six pairs is 900 units and 360 constructed defects, with
150 criticals - enough that a recall difference of ten points is not noise.

Defects are constructed, not annotated, for the reason the Fable set
demonstrates: construction is provable, cheap, and its prevalence is a
parameter rather than an artefact of who labelled it.

The per-language mutators must be written per language, not translated from the
existing ones. `analysis/probes/col4-judge-goldenset-build.py` carries Russian
stemmers and Cyrillic donors; `nfg-ui-fr-goldenset-build.py` carries French
articles and negation. Neither transfers to Japanese or Persian. Mutation
classes that *do* transfer unchanged: number loss, placeholder corruption,
sentence dropped, markup dropped.

### Two tiers, so screening does not cost confirmation

- **Screen** on en->de and en->ja - one Latin, one non-Latin, 300 units. Every
  candidate model runs this.
- **Confirm** the two or three survivors on all six pairs, 900 units.

At `JUDGE_BATCH_SIZE = 5`, screening is 60 requests per candidate per seat;
confirmation is 180. A six-candidate screen plus a three-candidate confirmation
is roughly 1 100 requests, against roughly 2 200 if every candidate ran
everything.

## The risk this design cannot remove

The research picked DeepSeek V4 and Qwen 3.7 as the best Chinese models **for
translation**, and the same families are the judge candidates. If the model
that produced a translation also judges it, it cannot see its own blind spot,
and its recall on its own output will read higher than it is.

This is not speculation about these models: it is the effect already measured
on this project, where `claude-sonnet-5` and `gpt-5.4-mini` as annotators
"reproduced the judges' blind spots" on 9 of 14 hand-confirmed defects.

Two consequences for how this corpus is built:

1. **Defects must not be produced by a candidate model.** Construction is
   mechanical, so this holds by design - one more reason to prefer it over
   model annotation.
2. **The translator and the judge seats should not be the same model.** If
   DeepSeek V4 is the production translator, a DeepSeek seat measures itself.
   This corpus cannot detect that; only a seat-versus-translator matrix can,
   and that is a separate measurement.

## What this is not

- Not a measure of real MT error recall. Constructed defects measure detection
  of injected faults. The zh human anchor is the only instrument that measures
  real ones, which is exactly why it stays in the set.
- Not a translation-quality benchmark. It scores judges, not translators.
- Not a production write of any kind. Every unit is read, none is modified.

## Open decisions

1. **Six pairs or four?** Dropping en->fa and ru->ko halves the construction
   work and removes the two scripts nobody on the team can proofread. Keeping
   them is the only way to know whether a seat is competent outside Latin and
   Han. My recommendation is to keep both and accept that their *clean* side is
   verified structurally rather than read.
2. **Is a human-labelled slice wanted per pair?** It is the only way to measure
   real-defect recall outside zh, and it is the expensive part. A 40-unit
   human pass per language would roughly triple the calendar cost.
3. **Which model may annotate the clean side?** Fable and GPT-5.6 did it for
   col4 and their clean labels held up. Reusing that pattern is cheap; the
   alternative is structural verification only, with no reader.
4. `need-for-greed/buyers` has 10 source units and 6 to 9 failing checks in
   every language (de 7, tr 6, it 7, pl 9, fr 8). That is not a defect pocket,
   it is one string family failing the same check everywhere - almost certainly
   a false positive or a source-side fault. Diagnose it before sampling; a
   stratum built on it would measure the check, not the judge.
