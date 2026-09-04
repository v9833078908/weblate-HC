# Snowball glossary morphology against `repeat-drift`: no collision, three real ones next door

**Date:** 2026-09-04. **Status:** measured, read-only against production.
**Probe:** `analysis/probes/glossary-morphology-vs-repeat-drift.py`.
**Data:** `analysis/data/glossary-drift-interaction-2026-09-04/col4.json`.
**Scope:** project `col4` (ru source, 16 090 comparable target units, 95
glossary terms), plus a live inventory query over all eight production
projects. No writes, no machinery, no LLM calls, $0.

## Verdict

The hypothesis - Snowball morphology in the glossary check and the new
`repeat-drift` check will fight each other - **is refuted, and it cannot be
true by construction**, not merely "was not observed".

`repeat-drift` groups units by a **byte-identical source** and
`translation__plural_id` (`weblate/trans/models/unit.py:2626-2642`, and the
`groups[unit.source]` grouping in `weblate/checks/consistency.py:409-411`).
The glossary evaluation of a unit is a function of
`(source, target, language, glossary state, unit flags)`
(`weblate/checks/glossary.py:52-99`). Inside a repeat group the source, the
language and the glossary are identical by definition, so the glossary verdict
degenerates to a function of the target alone. Therefore **any group member
whose target is glossary-green is a canonical choice that satisfies both
checks at once**. Drift can never demand a target the glossary refuses, unless
*every* candidate in the group already fails the glossary - which is a
pre-existing glossary defect on both variants, not a conflict.

Measured on `col4`: a conflict-free canonical candidate exists in
**73 of 76** French diverging groups and **57 of 57** Indonesian ones. The
three French exceptions are one term, `Хриплый` -> `Râpeux`, which no
translation of the group uses (`rauque`, `enroué`); picking either variant
leaves the same advisory. Drift is neutral there.

The same argument covers the caps question below: a rule keyed on the source
form is constant inside a repeat group.

## What the two checks actually see on `col4`

Neither check is enabled on `col4` today (`check_flags` empty on the project
and on all three components). `check_glossary` is enabled on **no** production
project at all (0 rows), and `repeat-drift` only on `need-for-greed`
(253 rows). The numbers below are therefore what enabling them *would*
produce.

| | fr | en_US | tr_TR | id |
|---|---|---|---|---|
| diverging groups (`repeat-drift` fires) | 76 | 51 | 61 | 57 |
| units flagged | 181 | 140 | 167 | 133 |
| source carries a glossary term | 28 | 12 | - | 20 |
| glossary green on every member | 71 | 51 | - | 56 |
| glossary advisory on some member | 5 | 0 | - | 1 |
| conflict-free canonical candidate | 73 | 51 | - | 57 |
| divergence is case-only | 1 | 1 | 0 | 2 |
| source carries an all-caps term | 3 | 3 | 3 | 3 |

The two detectors are near-disjoint: of 245 diverging groups, 172 have no
glossary term in the source at all (counted against the Russian term list,
independent of target-language coverage), and only 6 carry a glossary advisory
anywhere. Where they do overlap they agree - the group that renders
`Чествование Знамён Хозяйств` three ways (`Drapeaux Ménagers`,
`Drapeaux des Ménages`, `Drapeaux des Exploitations Agricoles`) is both a
drift and a terminology finding, and the candidate with the fewest advisories
is the one to canonicalise on.

`tr_TR` shows no glossary column because it **has no glossary at all**: the
glossary component `all-glossary` carries translations for `en`, `fr`, `tr`,
`id`, `en_US`, while the content components use `en_US` and `tr_TR`.
`get_glossary_units()` filters `translation__language=<exact language>`
(`weblate/glossary/models.py:311-316`), so `tr_TR` units match zero terms.
`en_US` is worse than it looks: its 95 term rows exist but are **untranslated**
(`translated_terms: 0`), and an empty `term.target` compiles to the
zero-width pattern `\b\b`, which `re.search` finds in any target
(`weblate/checks/glossary.py:69-72`). So all 12 `en_US` groups whose source
carries a term are reported green **only because the term is untranslated**.
For two of four `col4` languages, `repeat-drift` and the judge are the only
term-consistency signals that exist.

## `ГИГАХРУЩ`: where morphology carries the weight

Term: `ГИГАХРУЩ` -> `Gigastructure` (en, fr, tr), `GIGAKHRUSHCH` (id),
empty (en_US). Flags: `terminology` only - **no `exact`, `read-only` or
`forbidden` anywhere in the `col4` glossary**. 85 units per language mention
it, in 13 source forms:

```text
Гигахруща 32  Гигахрущ 25  ГИГАХРУЩА 11  Гигахрущу 6  Гигахруще 4
ГИГАХРУЩ 4    Гигахрущёвке 3  Гигахрущём 2  ГИГАХРУЩЕВКИ 2
ГИГАХРУЩЁВКЕ 1  Гигахрущестроения 1  ГИГАХРУЩУ 1  ГИГАХРУЩЕМ 1
```

(per language; the probe reports the sum over the three glossary-covered
languages.)

Source-side match path, per language: **26 units exact, 52 stem-only, 7 not
matched at all**. Two thirds of the term's reach exists only because of
Snowball. Project-wide the share is the same order: 1 159 exact against
1 249 stem-only term-unit matches in French, i.e. **52% of all glossary
matching on `col4` depends on the stem path**. The most stem-dependent terms
are `Ячейка` (97% stem-only), `Смена` (94%), `Игорь` (100%),
`Ликвидатор` (82%), `ГИГАХРУЩ` (67%).

The 7 unmatched units are the "гигахрущёвка" class the hypothesis named:
`Гигахрущёвке`, `ГИГАХРУЩЕВКИ`, `ГИГАХРУЩЁВКЕ`, `Гигахрущестроения`.
Russian Snowball stems them to `гигахрущевк` and `гигахрущестроен`, which are
not the term's stem `гигахрущ`, so the source-side matcher drops them: no
check, no advisory, and no glossary entry in the MT or judge prompt. The
consequence is visible in Indonesian, where those very units carry
`Gigakhrushchyovka` and `GIGAKHRUSHCHEVKA` - two renderings that exist
nowhere else in the corpus. French happens to have written `Gigastructure`
anyway.

**The `exact` trap.** Marking `ГИГАХРУЩ` as `exact` - the obvious reading of
"fixed term" - would not tighten the term, it would delete two thirds of it.
The stem index skips any term carrying `exact`, `read-only`, `forbidden` or
`not-applicable` (`weblate/glossary/models.py:229-230`), so coverage would
drop from 78 matched units to 26, and the term would vanish from the prompt
and the judge for every inflected mention. This is the one place where a fixed
term and Snowball genuinely conflict - inside the glossary layer, with no
`repeat-drift` involved. On a Russian source, `exact` is only safe for a term
whose every occurrence is nominative.

## Caps: a hole both checks share, not a fight between them

The claim "morphology does not preserve caps word forms" is confirmed, and it
is not a morphology bug - the whole glossary layer is case-blind by
construction:

* the source-side automaton matches against `unit.source.lower()` and stores
  the hit as `source[start:end].lower()`
  (`weblate/glossary/models.py:506, 534`);
* the stem index lowercases both the term and every word it compares
  (`weblate/checks/morphology.py:84`, `weblate/glossary/models.py:237, 243`);
* the target-side exact test and the forbidden test both pass
  `re.IGNORECASE` (`weblate/checks/glossary.py:63, 70`).

So the case of the matched source occurrence is discarded before any consumer
sees it. Nothing downstream can know that a unit shouted `ГИГАХРУЩЕВКИ`, and
`GIGASTRUCTURE`, `Gigastructure` and `gigastructure` are all equally
"matched". Production shows exactly the drift this permits, per source case:

| language | caps source | other source |
|---|---|---|
| en_US | CAPS 16, Title 5 | Title 68, CAPS 4 |
| fr | Title 20 | Title 73 |
| id | CAPS 20 | CAPS 68, Title 5 |

English mirrors the source caps 16 times out of 21 and shouts 4 times when
the source did not; French never mirrors (0 of 20 - a normalising MT run);
Indonesian shouts 68 times where the source did not, because its own glossary
target `GIGAKHRUSHCH` is itself all-caps. The golden set already treats the
non-mirrored caps case as a defect (`analysis/data/col4-judge-golden.json`,
`expected_rendering: GIGASTRUCTURE` for a caps source), i.e. today the judge
is the only layer that can see it.

`repeat-drift` cannot fill that hole and cannot be hurt by filling it:

* the case decision would be a function of the source form, which is constant
  inside a repeat group - a caps-mirroring rule and byte-equality cannot
  disagree;
* the 12 diverging groups whose source carries an all-caps term (3 sources ×
  4 languages) render the term **identically** in every member
  (`GIGASTRUCTURE`/`GIGASTRUCTURE`, `SAMOSBOR`/`SAMOSBOR`); the divergence is
  paraphrase elsewhere in the sentence;
* conversely, the case defect that both checks miss is inside a *single*
  target: `ATTENTION. SAMOSBOR EN COURS. TOUT LE MONDE DOIT SE METTRE À
  COUVERT DERRIÈRE LES Portes étanches.` - a shouted announcement with the
  glossary form `Portes étanches` pasted in glossary case. Both members of that
  repeat group share the defect, so drift is green; `re.IGNORECASE` makes the
  glossary green too.

What `repeat-drift` does own is the target-side case canon: 4 of 245 diverging
groups differ by case only, and 3 of those 4 sit on a glossary term
(`Старейшина` -> `The Elder`/`The elder`, `Пионер` -> `Pionnier`/`pionnier`,
`Ячейка` -> `Sel`/`sel`). The glossary check is green on all of them. That is
a division of labour, not a contradiction - but see the next section for why
that flag currently has no repair path.

## The three real collisions found while looking

1. **`repeat-drift` leaks into the judge prompt.** The MT/repair path
   deliberately drops it (`weblate/machinery/llm.py:613-616`: "every member of
   a repeat group is flagged, including the correct one; a mandatory repair
   would chase a sibling"), and the plan's decisions D13/D14 keep it out of
   `enforced_checks`. The judge path has no such filter:
   `weblate/trans/judge_loop.py:128` sends
   `sorted(unit.all_checks_names - JUDGE_CHECKS)`, which lands in
   `segment["checks"]` (`weblate/trans/judge.py:904-905`), and the prompt says
   *"Do not re-report anything already listed under `checks`: code has proven
   those"* (`weblate/trans/judge_prompts/verdict.txt:12-13`). So on every
   member of a drift group - including the correct one - the judge is told a
   defect it cannot see has been proven, and is instructed to stay silent
   about it. The risk is suppression of a real finding the seat attributes to
   the opaque name, and it is live: `need-for-greed` carries 253 `repeat-drift`
   rows today, 5 of them on units that also carry `judge-flag`. Any future
   judge round there ships the flag to both seats. Decision needed: either
   exclude `repeat-drift` in `build_request` the way `llm.py` does, or give the
   judge the sibling renderings so the name means something.
2. **Cross-component `max-length` against byte-equality.** `repeat-drift` is
   `batch_project_wide` and groups across components, while `max-length` is a
   component flag. On `need-for-greed` the components disagree by an order of
   magnitude (`buyers` 21, `survey` 39, `characterdialogue` 50, `ui` 120,
   `orders` 152, `tutorial` 155), and 3 of the 119 live groups already span
   `survey` (39) and `ui` (120) on the source `Начать`. Today the targets are
   5-7 characters, so nothing breaks; the class is real and the escape hatch
   is `ignore-repeat-drift` on the tighter unit. Nothing in the code detects
   the case.
3. **Glossary language coverage holes make drift the only detector.**
   `tr_TR` and `en_US` on `col4` (previous section) have no usable glossary,
   yet carry 112 of the 245 diverging groups. Fixing the glossary language
   codes is worth more than either check.

## Reproduction

```bash
PROD_WEBLATE_API_TOKEN=... uv run python \
    analysis/probes/glossary-morphology-vs-repeat-drift.py \
    --project col4 --captured-at 2026-09-04
```

Snowball 3.1.1; source stemming on (`ru` in `SOURCE_STEM_LANGUAGES`); target
algorithms `fr` -> french, `tr` -> turkish, `en`/`en_US`/`id` -> none, so
Indonesian and English targets have no morphological tolerance at all and
`id`'s 0 morphology lifts are expected, not a measurement gap.

## Limits

* The probe re-derives the source-side exact matcher and the target-side
  evaluation from `weblate/glossary/models.py` and
  `weblate/checks/glossary.py`; only the stem comparison is imported from
  `weblate.checks.morphology`. Variants are out of scope, which matches the
  check's own `include_variants=False`.
* Neither check is enabled on `col4`, so the group counts are predictions of
  enablement, not observed `Check` rows. The `need-for-greed` numbers (253
  rows, 119 groups, 3 cross-component) are observed.
* The judge suppression risk in finding 1 is a mechanism traced through code
  and prompt, not a measured verdict delta. Measuring it needs a paired judge
  run with and without the flag in `checks`.
