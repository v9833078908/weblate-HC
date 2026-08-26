# Review of the seven zh critical labels

**Date:** 2026-08-26.
**Reviews:** the `critical` severities in `analysis/data/st2-zh-groundtruth.json`
(sealed 2026-08-18, S&T2 summer-update zh_Hans, 124 units).
**Outcome:** one label confirmed critical, five downgraded to major, one removed
from the recall gate for lack of evidence. Every defect stands; only the rubric
step that promoted all seven to `critical` is revised. The sealed file is
unchanged; the result is `analysis/data/st2-zh-critical-revision.json`.

## Why the labels were re-read

Stage 3 of the seat pair search disqualified `qwen3.8-max` on
`missed_crit = 6/7` while it returned no unparsed batch on either corpus. Its
stored error descriptions show it located a defect in five of the seven units
and named the mechanism, including the placeholder binding in `24221`. The
disagreement was largely about severity, not about existence. That is a
different fault from not seeing the defect, and it is worth separating before a
candidate is rejected on it.

The ground truth is human. `_truth_source` records a 14-defect anchor
(`analysis/data/st2-zh-judge-annotations.json`) plus the corrected-export defect
list (`docs/operations/plans/2026-08-14-st2-zh-corrected-export.md`), with
severities assigned afterwards by a player-consequence rubric. The anchor is not
in question. The rubric step is.

## Method, and a correction to an earlier reading

A first pass judged the seven from the source/target pairs alone. That pass was
wrong on two units, and the corrected-export review is the reason: it records
*rendered* examples, which settle questions the raw strings leave open. The
rendered examples are used here in preference to any inference from the string
pair.

One limit carries over. That review states plainly that placeholder roles in
`24207`, `24208`, `24221` and `24230` were inferred from the source text and
sibling strings, not read out of game code. The inference is documented and
reproducible, but it is not runtime verification.

## Unit by unit

### 24130 `ID_AGITATION_CAMPAIGN_PURCHASE_QUESTION` - removed from the gate

```text
RU: ...получать {[PARAM0]}% к доходу за {[PARAM1]}?
ZH: ...并支付{[PARAM1]}以获得{[PARAM0]}%收入加成吗？
```

The target introduces 支付, "to pay". Whether that is an invention depends on
what `{[PARAM1]}` holds: Russian `за {[PARAM1]}` carries both a price and a
duration reading, and the neighbouring `24131` uses `PARAM1` as a turn count.

The human reviewer tried to settle it against other languages of the same key
and could not: the production API answered 401 without a token, and the local
`strategy-and-tactics-2` copy does not contain the key. The sealed record
carries `disputed: true`.

Excluded from the gate because the evidence is missing, not because the target
is clean. If the parameter is ever resolved this unit should come back.

### 24180, 24181, 24182 - downgraded to major

```text
RU: Результаты провальной обороны укреплений в провинции {0}
ZH: 省份{0}防御工事失败防御结果
```

The semantic content is right in each: 失败防御 for "провальная оборона",
失败进攻 for "провальный штурм", 成功防御 for "удачная оборона". The annotator's
own reason for `24180` is «набор существительных без связей», and `24181` and
`24182` are recorded as the same defect.

A connector-less noun pile is a fluency defect. The player reads a broken
caption and still reads the correct event out of it. Reserving `critical` for
this leaves nothing to distinguish it from a string that states the wrong fact.
Major.

`qwen3.8-max` labelled all three `major fluency` and described the missing
grammatical connectors. Under this review that answer is right.

### 24207 `ID_AI_PLAYER_RANK_STATUS_NOTIFICATION_DESC` - downgraded to major

```text
RU: Статус {0} cтран {1} изменился: {2} до {3}!
ZH: {0} 个国家 {1} 的状态已变更: {2} 至 {3}！
```

The first pass called this a placeholder-role inversion. It is not. The
reviewer's rendered example is «3 个国家 盟国 的状态»: `{0}` is a count and `{1}`
is a group name, so the measure-word construction `{0} 个国家` is the correct
binding. The Russian agrees - genitive plural `стран` after `{0}` requires a
numeral - and the sibling `24208` uses singular `страны {0}` for an identity.

The real defect is in the rendered word order, where the count phrase and `{1}`
end up juxtaposed with no connector. Fluency, not meaning. Major.

`qwen3.8-max` returned `critical mistranslation` here, arguing `{0}` was being
forced into a count. That reading is wrong, so this is an over-severity call on
a real defect rather than a match.

### 24208 `ID_AI_PLAYER_HEGEMON_STATUS_NOTIFICATION_DESC` - downgraded to major

```text
RU: Статус страны {0} изменился: {1} статус {2}!
ZH: 国家 {0} 的状态已变更: {1} 状态 {2}！
```

The first pass called this a faithful mirror of a broken source, and excluded
it. That was wrong. The reviewer's rendered example is «失去 状态 霸主», where
Chinese requires 失去霸主状态. The translator carried Russian modifier order
into Chinese, so the target is broken on its own terms, independently of the
source fragment being awkward. The meaning is still decodable from the three
tokens, so major rather than critical.

Both Qwen models passed it. Under this review that is a genuine recall miss.

### 24221 `ID_DIVISION_WITH_EXP_LVL_DIED_DESC` - confirmed critical

```text
RU: ...погибла дивизия {0} уровня {1}...
ZH: ...损失了一支{0}级别的{1}师...
```

The Russian is ambiguous in isolation, and the first pass leaned on the key,
which is weak evidence. The corrected-export review settles it directly:
«роли плейсхолдеров перевёрнуты: {0}=имя дивизии, {1}=уровень». The target binds
`{0}` to the level, so the rendered string is wrong for every pair of values and
the reader cannot recover the intended one. Confirmed critical.

`QWEN3.7-plus` returned `critical` here. `qwen3.8-max` found the same inversion
and described it correctly but graded it `major` - the one genuine
under-calibration in its run.

## Consequence for the gate

In the gate: `24180`, `24181`, `24182`, `24207`, `24208` at major, and `24221`
at critical. Excluded: `24130`.

Six of the seven defects are real and none of them disappears under review. What
changes is that only one of them carries a consequence severe enough that a
judge grading it `major` should be treated as having failed.

The existing `missed_crit` counter charges a candidate both for failing to see a
defect and for seeing it and grading it lower. Those are separate faults and
only the first should end a candidate's run. The split gate is task 2 of
`docs/llm-first/plans/2026-08-26-zh-critical-label-revision-and-split-gate.md`.

This review does not re-open Stage 4. Whether any candidate deserves further
paid runs is decided after the split gate is computed, and needs its own
approval.
