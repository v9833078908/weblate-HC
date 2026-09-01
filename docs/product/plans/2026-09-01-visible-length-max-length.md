# Visible-text character budget for max-length

**Status:** Implemented 2026-09-01: code, tests and documentation are on `main`.
Deployment and the production check recomputation are explicitly deferred and
still need their own approval.

**Goal:** `max-length:N` and the `source-max-length` it triggers measure the
length of the text the player actually reads. Unity rich-text tags render to
zero width and must not spend the budget.

**Architecture:** Keep the stock check IDs, the flag schema and the upstream
documentation anchors. Only the two existing fork subclasses change: their
replacement chain gains one tag-stripping step, placed before the conditional
collapse. No new flag, no new check, no schema change.

**Tech Stack:** Python, Django/Weblate checks, `regex`, pytest.

---

## Problem and evidence

`GameMaxLengthCheck` counted raw stored characters, so Unity rich-text tags
consumed the budget alongside visible text. On production checkout `ffb6693`,
component `need-for-greed/ui` with `max-length:110`:

```text
unit 357661 (ru): raw 136, visible 67, budget 110
'Можно <color=#E3BA59>продать</color> у торговца, использовать в
 <color=#E3BA59>Плавильне</color> или <color=#E3BA59>Супер слоте</color>.'
```

The player reads 67 characters. The string was flagged because three colour
tags spend 69 characters of the 110-character budget.

Measured blast radius, read-only, production:

|Check|Rows before|Stop failing|
|---|---:|---:|
|`max-length`|654|138|
|`source-max-length`|43|13|

All 138 are on `need-for-greed/ui`. The only other component carrying the flag,
`strategy-and-tactics-2/summer-update` (`max-length:100,
ignore-source-max-length`, 23 rows), is unaffected: none of its flagged strings
use tags. No project, category, translation or unit-level `max-length` flag
exists, so the two components are the whole surface.

The change is one-way: stripping only shortens the measured text, so no string
that passes today can begin to fail. It removes false alarms and adds none.

## Decision: tags are invisible, placeholders are not

Only `TAG_PATTERN` is removed (`color`, `link`, `size`, `b`, `i`, `u`, `s`,
`sprite`). Engine placeholders keep counting their own characters.

A tag renders to zero width, so removing it is always correct. A placeholder
renders to a real runtime value - `You have {0} gold` becomes
`You have 1234567 gold` - so treating it as zero characters would let a
genuinely overflowing string pass, and would bypass stock `replacements:`,
which exists precisely to declare that width.

This is why the budget path does not reuse `_visible_length()`. That helper
also drops placeholders, which is correct for `game-length`: that check
compares a target-to-source ratio, where placeholders cancel out on both
sides. An absolute budget has no such cancellation.

Evidence that the stricter choice is nearly free today: of the 654 flagged
rows only 17 contain a placeholder at all, and the two options differ by
exactly one row.

## Ordering inside the replacement chain

```text
stock replace (xml-text, replacements:)  ->  strip tags  ->  conditional collapse
```

- Stock replace stays first, so `replacements:` still expands a declared
  placeholder width before anything is removed.
- Tags are stripped **before** the conditional collapse, not after.
  `_conditional_length_text` keeps the longest top-level branch by `len`
  (`checks.py:355`). With tags counted, `{v:cond:>0?<color=#FFFFFF>hi</color>|hello there}`
  selects the 25-character tagged branch whose visible text is 2 characters,
  understating the worst case; the real worst case is the 11-character plain
  branch. Stripping first also removes any `|` inside a tag attribute, which is
  not a branch separator.

## Implementation

`weblate_customization/src/weblate_customization/checks.py`:

- Import `TAG_PATTERN` from `weblate.trans.protected_tokens`.
- Add `_length_budget_text()`: `_conditional_length_text(TAG_PATTERN.sub("", text))`,
  documenting the ordering rationale and the deliberate difference from
  `_visible_length()`.
- `GameMaxLengthCheck.get_replacement_function` and
  `GameSourceMaxLengthCheck.get_replacement_function` call it instead of
  `_conditional_length_text` directly.

## Tests

`weblate_customization/tests/test_checks.py`, written failing first; the four
behavioral assertions failed on the old code and the three lock-ins passed.

|Test|Locks|
|---|---|
|`test_tags_do_not_consume_the_budget`|Production fixture, 136 raw / 67 visible, budget 110, passes|
|`test_visible_text_over_the_budget_still_fails`|Same fixture at budget 60 still fails|
|`test_placeholders_keep_consuming_the_budget`|A placeholder is not treated as invisible|
|`test_tags_are_removed_before_the_conditional_collapse`|Worst-case branch chosen by visible length|
|`test_replacements_expand_before_tags_are_removed`|Chain order preserves `replacements:`|
|`test_tags_do_not_consume_the_source_budget`|English 85% headroom computed on visible source|
|`test_visible_source_over_the_headroom_still_fails`|Headroom still enforced|

Run:

```sh
PYTHONPATH=weblate_customization/src uv run pytest -n 0 weblate_customization/tests/test_checks.py
uv run pytest -n 0 weblate/checks/tests/
```

Result: 172 passed, 26 skipped, 74 subtests for the customization suite;
1464 passed, 498 skipped for the stock check suite.

## Documentation

- `docs/snippets/checks-autogenerated.rst` - manual text outside the
  autogenerated marker block; the old wording "This only checks for the length
  of translation characters" no longer described the behavior.
- `docs/guides/producer-guide-weblate.md` - the producer-facing definition of
  the budget and the placeholder rule.
- `docs/changes.rst` - unreleased section, user-visible behavior change.

## Out of scope

- Re-tuning the numbers 110 and 100. They now mean visible characters, which is
  the intended reading; changing the values is a producer decision about the
  real UI slots.
- `game-length`, already visible-based.
- `max-size`, the pixel-rendering check.
- Any new flag or check identifier.

## Deferred rollout, approval required

1. **Deploy.** `weblate_customization` reaches production only through the
   image. The dev container additionally needs
   `cp -r weblate_customization/src/weblate_customization dev-docker/data/python/`.
2. **Recompute.** Persisted `Check` rows stay stale until
   `weblate updatechecks need-for-greed/ui` runs. Until then the 138 target
   rows and 13 source rows remain visible to translators. This mutates
   production data and needs its own approval.
