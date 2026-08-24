# Batch-misalignment radius scan across production

Date: 2026-08-24. Instance: `l10n.herocraft.com`.
Tool: `.omp/skills/weblate-lqa/scripts/detect_misalignment.py` (commit `b37bb2d`).

This scan answers one question raised by the German rotation found in
`docs/llm-first/measurements/2026-08-24-heart-abyss-hub-1-full-lqa.md` §5.1: how
much more of production holds the same defect. It is read-only.

## 1. Radius

Automatic translation is recorded as `ActionEvents.AUTO` (`6`) with the author
`mt:openrouter`, so the radius is measurable rather than assumed:

| Measure | Count |
| --- | --- |
| Components on the instance | 23 |
| Translatable components (not glossaries) | 15 |
| Components with any automatic translation | 14 |
| Components touched by the LLM batch path (`mt:openrouter`) | 14 |
| Units written by the LLM batch path | 27436 |
| Of those, already reviewed on `heart-abyss/hub-1` | 3564 |
| Unreviewed before this scan | 23872 |

`pirate-ships/localization-json` is the only translatable component with no
automatic translation at all.

## 2. Method

Two independent detectors over source and target fetched from the API:

* **Offset runs.** Compare the language-independent shape of a target - engine
  placeholders, ASCII numbers, the `$` separator and terminal punctuation -
  against its own source and its neighbours. Report maximal runs of consecutive
  units explained by one shift offset.
* **Duplicate targets.** One identical target stored against two different
  sources, which is the other half of a shift: one reply item repeated, one
  source's content lost.

Detection is restricted to the units a batch actually translated. This is not a
refinement, it is a correctness condition. On `space-arena/lockit` the batch
touched 45 units per language inside 4864; scanning the whole component made
unrelated strings adjacent and produced **285** offset runs, all of them
artifacts. The same scan restricted to the batch produces **zero**.

## 3. False-positive classes removed before trusting the output

Both were found by reading candidates, not by assuming the tool was right.

| Class | Cause | Effect | Fix |
| --- | --- | --- | --- |
| CJK numerals | `10 тысяч` and `一万` are one quantity sharing no digit | 2 aligned guard lines per Chinese language reported as a shift | digit comparison dropped when either side spells a quantity without ASCII digits |
| Translatable bracket tokens | `[shift]` matched the placeholder pattern, its translation `[Umschalttaste]` did not | `victory-banner/common` keybinding help reported as a shift | square-bracket tokens removed from the placeholder feature |

Both are locked by `tests/misalignment_regression.json`, which holds real
pre-repair production units. Synthetic stand-ins were written first and passed
with the defects still in place, so they were replaced.

## 4. Result

129 (component, language) pairs, 27025 units in batch scope.

| Detector | Findings | Real defects |
| --- | --- | --- |
| Offset runs | 0 | 0 |
| Duplicate targets | 57 | 1 |

Of the 57 duplicate findings, 55 are sources that are identical or synonymous
and legitimately converge on one target in a short UI string. One is a
terminology collapse, not a shift, and one is the same defect class as the
German rotation.

### 4.1 Confirmed content loss: `col4/data`, Indonesian

| Unit | Context | Russian source | Indonesian target |
| --- | --- | --- | --- |
| 88213 | `EVENT_360_RESULT_671` | `Кроме вас посетителей не было и вы спокойно справили нужду. Правда, вместо бумаги пришлось использовать страницы устава` | `Ada sesuatu yang tak terlukiskan duduk di toilet. …` |
| 88214 | `EVENT_360_RESULT_672` | `На унитазе сидело нечто неописуемое. Вы даже не помните, как сбежали оттуда. Да и туалет для вас больше не актуален` | identical to the row above |

Unit 88213 holds the translation of 88214's source. The content of its own
source is absent from the component. French renders both distinctly and
correctly, which fixes the reading of the two sources. Repair needs a
translation of 671 written from scratch; it does not exist anywhere in the
component.

### 4.2 Terminology collapse, reported for completeness

`victory-banner/common` German renders both `пт ежи` (anti-tank hedgehogs,
units 345217 and 345227) and `Надолбы` (dragon's teeth, units 345222 and 345232)
as `Panzersperren`, so two distinct fortifications share one name. This is a
translation choice, not a misalignment, and is out of this scan's scope.

## 5. What this scan does not establish

* The offset-run detector did **not** find the one real defect in §4.1: the two
  sources have the same shape, so nothing discriminates them. The duplicate
  detector caught it. Neither detector would catch a shift whose strings share a
  shape *and* whose duplicated content was paraphrased differently each time -
  which is exactly the German case, where `Меня сейчас стошнит` came back as both
  `Mir wird schlecht` and `Ich muss kotzen`.
* It confirms the earlier miss: `heart-abyss/hub-1` zh_Hant units 372096 and
  372097 exchanged clause content across a segment boundary with the shape
  preserved, and remain undetected.
* Silence on a component is therefore not proof that it is aligned. The honest
  claim is narrower: across 27025 machine-translated units, the two shapes of
  this defect that these detectors can see occur once outside `heart-abyss/hub-1`.

## 6. Verification of the `heart-abyss/hub-1` repair

`heart-abyss/hub-1` German was re-fetched after the nine target repairs of
2026-08-24 and reports zero offset runs, against two runs covering units
370314-370317 on the pre-repair snapshot. The repair is confirmed by the same
tool that found the defect.
