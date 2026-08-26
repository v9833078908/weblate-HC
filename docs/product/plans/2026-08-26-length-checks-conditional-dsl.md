# Conditional DSL-aware max-length Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** Awaiting implementation approval. Revised after engineering review on 2026-08-26.

**Goal:** Make the opt-in `max-length:N` and `source-max-length` checks measure a safe worst-case character representation of Hero Craft conditional DSL, and make an over-budget automatic translation in judge mode reach the existing repair loop instead of becoming an unreviewed suggestion.

**Architecture:** Keep the stock check IDs and flag schema. Two custom subclasses replace only `MaxLengthCheck` and `SourceMaxLengthCheck` through `CHECK_LIST`. Their replacement function first applies stock `replacements:` behavior, then collapses each recognized outer conditional to its longest top-level branch and concatenates the selected branches of sequential conditionals. A judge-mode over-budget candidate is stored in `Needs editing`, forced through the existing repair engine with its concrete budget in the failing-check description, and remains non-shippable if the budget still fails after the configured repair attempts.

**Tech Stack:** Python, Django/Weblate checks, `regex`, pytest, shared dev Docker stack.

---

## Problem and evidence

Hero Craft source strings can contain conditional blocks:

```text
{identifier:cond:comparison?branch[|alternate]}
```

The documented grammar is in
`docs/product/plans/2026-08-22-nested-game-placeholder-protection.md:120-128`.
A branch is visible text; the header, outer braces, and top-level `|` are
engine syntax. Stock `max-length` and `source-max-length` count the raw DSL
because they call `len(get_replacement_function(unit)(text))`
(`weblate/checks/chars.py:549-553`, `weblate/checks/source.py:68-81`).

The existing auto-translate gate has a second, independent failure:

```python
max_length = unit.get_max_length()
if self.mode == "suggest" or any(len(item) > max_length for item in target):
    Suggestion.objects.add(...)
```

`weblate/trans/autotranslate.py:400-435` therefore turns a raw conditional
formula with a tight `max-length:N` into a suggestion. In judge mode,
`process_judge()` pre-translates via this path and judges only persisted targets
(`weblate/trans/autotranslate.py:760-790`), so a suggestion has no check row,
never reaches `repair_target()`, and cannot be repaired automatically.

The supplied Need for Greed runtime fixture establishes the required behavior
for one project only:

```text
{hours:cond:>0?{hours}Std. |}{minutes:cond:>0?{minutes}Min. |}{seconds:cond:>=0?{seconds}Sek.|}

hours=0,  minutes=22, seconds=55 -> 22Min. 55Sek.
hours=0,  minutes=22, seconds=0  -> 22Min. 0Sek.
hours=0,  minutes=0,  seconds=23 -> 23Sek.
hours=15, minutes=45, seconds=10 -> 15Std. 45Min. 10Sek.
hours=15, minutes=0,  seconds=10 -> 15Std. 10Sek.
```

The three top-level blocks are independent and concatenate when true. For the
project-specific scenario `hours=minutes=seconds=9`, the German template
renders `9Std. 9Min. 9Sek.` (17 characters). A per-unit flag

```text
max-length:11, replacements:{hours}:9:{minutes}:9:{seconds}:9
```

must therefore fail for that target. It is a detection fixture, not a shared
rule and not a passing German translation. A compact German target or a revised
budget remains that project's content decision.

## Scope decisions

### Included

- `max-length` and `source-max-length` only. Their identifiers, flags, and
  stock documentation anchors stay unchanged.
- Judge-only handling of an over-budget `max-length` candidate: persist,
  repair, and hold it rather than silently creating a suggestion.
- A dynamic `GameMaxLengthCheck` description that tells the repair model the
  concrete limit.
- Scoped check recomputation after an approved project rollout.
- A concise operator guide for project/component/per-unit budget policy.

### Deliberately excluded

- `game-length`, `_visible_length()`, `_LENGTH_TIERS`, and `max-size`. A
  character-longest branch is not necessarily pixel-widest, so `max-size`
  needs a separate branch-aware renderer and an actual font/slot contract.
- A generic runtime evaluator for condition comparisons. This plan computes a
  conservative template bound; it does not simulate the game engine.
- Recursive normalization of nested recognized conditionals. The dev corpus
  has no such source, and plan 05 already documents outermost-only semantics.
- New check IDs, new flag grammar, a project-specific `humanTimer` class, or
  hard-coded values such as `11` in shared Python code.
- Importing budgets, setting any live component flags, restarting any shared
  stack, running `updatechecks` against a live instance, or production work.
- Dependency on
  `docs/product/plans/2026-08-24-05-conditional-dsl-validation.md`. Both
  plans use the same low-level primitives and should land serially to avoid a
  same-file merge conflict, but neither is a functional prerequisite of the
  other.

## Configuration ownership

The implementation is generic; each game owns its data policy.

`Flags` merge from broad to narrow scope: project/category/component,
translation, format/unit flags, source extra flags, then unit extra flags
(`weblate/trans/models/component.py:6186-6198`,
`weblate/trans/models/translation.py:403-406`,
`weblate/trans/models/unit.py:2489-2507`). Later values replace earlier values
with the same name (`weblate/checks/flags.py:305-321`).

Use a project or component `max-length:N` only when that exact static budget
applies to every string in scope. Use a source-file flag or unit extra flag for
a specific UI slot. `replacements:` supplies a declared width scenario, not
live runtime values. For a condition-dependent project rule, select the
worst-case scenario that the rule actually promises. The Need for Greed
`9/9/9` profile above is one example; it must not be copied to another game.

English sources normally need 15% headroom under `source-max-length`
(`weblate/checks/source.py:76-81`). When a particular source intentionally
uses a whole fixed slot, add `ignore-source-max-length` to that unit only. Do
not suppress the source check for every conditional string.

## Fixed behavioral contract

### Conditional representation

```text
stock replacements
        |
        v
conditional worst-case collapse
  +-- no :cond: -> return the same str object
  +-- malformed braces -> leave transformed input unchanged
  +-- each recognized conditional -> longest top-level branch
  +-- consecutive conditionals -> concatenate their selected branches
  +-- nested recognized conditional -> leave transformed input unchanged
        |
        v
len(representation)
```

1. Run the stock replacement function first. With a valid key such as
   `replacements:{hours}:9`, only the nested `{hours}` placeholder changes:

   ```text
   {hours:cond:>0?{hours}Std. |}
   -> {hours:cond:>0?9Std. |}
   ```

   The conditional header remains recognizable. Never use a bare `hours` key
   in a replacement profile.
2. A recognized block splits only at top-level `|`; braces inside nested
   placeholders raise depth and protect their `|` from being a delimiter.
3. Select `max(branches, key=len)` for one recognized block. This is a
   conservative character bound. It can over-count an engine path whose longer
   branch is unreachable in a chosen runtime scenario, but it must never join
   mutually exclusive alternatives.
4. Concatenate the selected branch of every sequential recognized block. This
   matches the supplied timer fixture, where all three true branches display at
   once.
5. If any recognized conditional is nested inside another recognized
   conditional, return the input received by the helper unchanged. It is a
   safe raw fallback, not an implicit recursive DSL interpreter. Add exact
   engine evidence before extending this behavior.
6. A text without `:cond:` returns by identity before parsing. An unrecognized
   `{value:cond:1}` and a malformed brace sequence retain existing stock length
   behavior.
7. `max-lines` is untouched. This plan changes neither line counting nor
   pixel-width rendering.

### Judge-mode repair contract

```text
MT result
  |
  +-- under max-length -> normal write
  |
  +-- over max-length, non-judge mode -> existing Suggestion path
  |
  +-- over max-length, judge mode -> write Needs editing
                                      -> run deterministic checks
                                      -> judge round
                                      -> repair when max-length remains,
                                         including judge PASS
                                      -> re-check and re-judge
                                      -> fixed: normal verdict state
                                      -> still failing: Needs editing
```

- The change is opt-in: only units carrying `max-length:N` can take this path.
- Existing `suggest`, `translate`, `approved`, and `fuzzy` modes retain their
  existing over-budget suggestion behavior.
- A judge `UNPARSED` result remains a transport failure and does not invent a
  repair attempt.
- A repair that remains over budget after `JUDGE_MAX_REPAIR_ATTEMPTS` is not
  silently promoted by a judge PASS. It remains `STATE_FUZZY`/Needs editing.
- `GameMaxLengthCheck.get_description()` supplies the numeric budget to the
  translator. `BaseLLMTranslation._get_failing_checks_context()` already sends
  that description (`weblate/machinery/llm.py:575-583`), so no broad
  `all_flags` exposure is necessary.

## Existing code to reuse

|Existing surface|Reuse|
|---|---|
|`_balanced_brace_blocks()` and `_CONDITIONAL_HEADER` in `weblate_customization/checks.py`|Recognize balanced outer conditional candidates. Do not introduce a second regex grammar.|
|`BaseCheck.get_replacement_function()`|Run its XML/replacement behavior before the conditional collapse.|
|`MaxLengthCheck` and `SourceMaxLengthCheck`|Inherit IDs, flag parsers, skip behavior, and check execution.|
|`CHECKS` registry|Resolve the active max-length implementation in core without importing the customization package.|
|`AutoTranslate.process_judge()`|Reuse its native write, check, judge, and repair pipeline; add no second MT path.|
|`repair_target()` and `JUDGE_MAX_REPAIR_ATTEMPTS`|Reuse the configured project engine and existing bounded retry budget.|
|`updatechecks` command|Recompute persisted rows for one approved rollout component.|

## Task 1: Add conditional worst-case helper and custom check tests

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py`
- Modify: `weblate_customization/tests/test_checks.py`

### Step 1: Add failing pure-function regressions

Import `SimpleTestCase`, `make_check`, `BaseLLMTranslation`, and the two new
check classes. Keep the existing test module's fixtures, then add an explicit
nonempty-alternative fixture and an explicit nested fixture:

```python
ALTERNATE = "{value:cond:>0?{value}x|much-longer}"
SEQUENTIAL = "{hours:cond:>0?{hours}h |}{minutes:cond:>0?{minutes}m |}"
NESTED = "{outer:cond:>0?{inner:cond:>0?x|}y|}"
HUMAN_TIMER_DE = (
    "{hours:cond:>0?{hours}Std. |}"
    "{minutes:cond:>0?{minutes}Min. |}"
    "{seconds:cond:>=0?{seconds}Sek.|}"
)
TIMER_FLAGS = "max-length:11, replacements:{hours}:9:{minutes}:9:{seconds}:9"
```

`ConditionalLengthRepresentationTest(SimpleTestCase)` must assert:

1. Plain text without `:cond:` is returned by identity (`assertIs`).
2. `"{value:cond:1}"` and malformed balanced input are unchanged.
3. `ALTERNATE` becomes `"much-longer"`, not `"{value}xmuch-longer"`.
4. With `replacements:{value}:123`, the first branch of `ALTERNATE` becomes
   longer and is selected. This proves replacement happens before branch
   selection.
5. `SEQUENTIAL` with `hours=9, minutes=9` becomes `"9h 9m "`, proving maxima
   from distinct blocks are concatenated.
6. `HUMAN_TIMER_DE` with `TIMER_FLAGS` becomes `"9Std. 9Min. 9Sek."` and has
   length 17.
7. `NESTED` is unchanged by the conditional helper. This is the deliberate
   safe fallback.

Run before implementation:

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_checks.py::ConditionalLengthRepresentationTest \
  -n 0 -q
```

Expected: collection succeeds and fails because the helper does not exist.

### Step 2: Implement the minimal helper

Add a private helper near `_balanced_brace_blocks()`:

```python
def _conditional_length_text(text: str) -> str:
    """Return a conservative character representation of conditional DSL."""
    if ":cond:" not in text:
        return text

    recognized = []
    for start, end, _children in sorted(
        _balanced_brace_blocks(text), key=lambda block: block[0]
    ):
        header = _CONDITIONAL_HEADER.match(text, start + 1, end - 1)
        if header is not None:
            recognized.append((start, end, header))
    if not recognized:
        return text

    open_ends: list[int] = []
    for start, end, _header in recognized:
        while open_ends and start >= open_ends[-1]:
            open_ends.pop()
        if open_ends:
            return text
        open_ends.append(end)

    result: list[str] = []
    previous = 0
    for start, end, header in recognized:
        assert header is not None
        result.append(text[previous:start])
        branches: list[str] = []
        branch_start = header.end()
        depth = 0
        for position in range(header.end(), end - 1):
            char = text[position]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            elif char == "|" and depth == 0:
                branches.append(text[branch_start:position])
                branch_start = position + 1
        branches.append(text[branch_start : end - 1])
        result.append(max(branches, key=len))
        previous = end
    result.append(text[previous:])
    return "".join(result)
```

### Step 3: Add the two subclasses and numeric repair description

Import `ngettext`, `MaxLengthCheck`, and `SourceMaxLengthCheck`. Do not add a
mixin and do not import `MaxSizeCheck`.

```python
class GameMaxLengthCheck(MaxLengthCheck):
    def get_replacement_function(self, unit):
        replace = super().get_replacement_function(unit)
        return lambda text: _conditional_length_text(replace(text))

    def get_description(self, check_obj):
        try:
            limit = self.get_value(check_obj.unit)
        except ValueError:
            return super().get_description(check_obj)
        return ngettext(
            "Translation must not exceed %(limit)d character.",
            "Translation must not exceed %(limit)d characters.",
            limit,
        ) % {"limit": limit}


class GameSourceMaxLengthCheck(SourceMaxLengthCheck):
    def get_replacement_function(self, unit):
        replace = super().get_replacement_function(unit)
        return lambda text: _conditional_length_text(replace(text))
```

The inherited check IDs remain `max-length` and `source-max-length`.
`ParametrizedCheck.get_description()` keeps its existing invalid-flag error
path because the subclass delegates on `ValueError`.

### Step 4: Add target, source, and prompt-context tests

Use `make_unit(flags=...)`, never an invented `MockUnit`.

- `GameMaxLengthCheck.check_target_params(..., value=11)` returns `True` for
  the Need for Greed German fixture and `False` for an 11-character compact
  fixture. `value` is an integer, not `[11]`.
- The dynamic description of a `make_check(unit, check)` contains
  `11 characters`; an invalid `max-length:*` retains the stock parse-error
  description.
- Build a small fake active Check around `GameMaxLengthCheck.get_description()`
  and call `BaseLLMTranslation._get_failing_checks_context()`. Assert the
  emitted `failing_checks` item has `check_id == "max-length"` and the exact
  numeric English description. This is the repair-prompt contract without
  importing the customization package into a core test module.
- `GameSourceMaxLengthCheck` retains stock English 85% behavior, plural
  handling, `replacements:`, invalid-flag behavior, and an
  `ignore-source-max-length` skip. Follow
  `weblate/checks/tests/test_source_checks.py:55-120` for each regression.

Run:

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_checks.py \
  -n 0 -q
```

Commit only the named files:

```bash
git add weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git commit -m "feat(checks): measure conditional DSL max-length budgets"
```

## Task 2: Make the auto-translate gate use the registered measurement

**Files:**

- Modify: `weblate/trans/autotranslate.py`
- Modify: `weblate/trans/tests/test_autotranslate.py`

### Step 1: Add core-registry regressions

Keep this core test independent of `weblate_customization`. Patch
`CHECKS.data["max-length"]` with a local test stub whose
`get_replacement_function()` returns a known representation.

Test all of these observable contracts:

1. In ordinary `translate` mode, a raw-long target whose stub representation
   fits the unit's `max-length` is stored as a translation, not a suggestion.
2. With no `max-length` check in `CHECKS`, the identity fallback preserves the
   current raw-length suggestion behavior.
3. For plural targets, one over-budget plural form sends the full target list
   to the existing suggestion path.
4. In `judge` mode, an over-budget candidate is stored at `STATE_FUZZY`, not
   created as a suggestion. It is now available to checks and the repair loop.
5. Existing explicit `mode="suggest"` behavior remains a suggestion regardless
   of measurement.

Use direct `AutoTranslate.update()` calls with existing `ViewTestCase` factories
instead of a real machine service.

### Step 2: Implement the judge-only branch

At module level import `CHECKS`. In `AutoTranslate.update()`:

```python
max_length = unit.get_max_length()
max_length_check = CHECKS.get("max-length")
replace = (
    max_length_check.get_replacement_function(unit)
    if max_length_check is not None
    else lambda text: text
)
over_max_length = any(len(replace(item)) > max_length for item in target)

if self.mode == "suggest" or (over_max_length and self.mode != "judge"):
    # existing Suggestion.objects.add() path
    ...
else:
    # existing unit.translate() path
    ...
```

Do not change non-judge over-budget behavior. Judge mode already sets fresh
machine targets to `STATE_FUZZY` in `process_judge()`.

### Step 3: Run and commit

```bash
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate/trans/tests/test_autotranslate.py \
  -n 0 -q

git add weblate/trans/autotranslate.py \
  weblate/trans/tests/test_autotranslate.py
git commit -m "fix(trans): retain over-budget judge candidates for repair"
```

## Task 3: Force deterministic max-length repair in judge mode

**Files:**

- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/autotranslate.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`
- Modify: `weblate/trans/tests/test_judge_autotranslate.py`

### Step 1: Add failing judge-loop regressions

Follow `JudgeLoopTest.run_batch()` in
`weblate/trans/tests/test_judge_loop.py:71-83`.

1. With both seats returning `PASS`, a writable unit whose deterministic checks
   contain `max-length` still calls `repair_target()` once and re-judges both
   seats.
2. An `UNPARSED` pair never calls `repair_target()`, even when `max-length`
   exists.
3. A repair that clears `max-length` may reach the normal PASS state.
4. A repair that leaves `max-length` active exhausts the bounded attempt count
   and remains in a fuzzy/non-shippable state. A judge PASS must not override
   this deterministic failure.
5. A unit without `max-length` retains the existing PASS-does-not-repair path.

Use the existing request and repair mocks; do not call a real LLM.

### Step 2: Implement the narrow repair trigger

In `_process_round_unit()`, run checks before deciding whether PASS is
non-repairable. Preserve the `UNPARSED` behavior, but treat an active
`max-length` deterministic check as repairable even when the judge verdict is
PASS and the unit is writable with attempts remaining. Continue to use
`repair_target()` and `_apply_repair()` unchanged for the actual write and
no-regress protection.

In `AutoTranslate.process_judge()` finalization, if the locked unit still has
an active `max-length` check, preserve `STATE_FUZZY` instead of projecting a
PASS to `STATE_TRANSLATED` or approved. Other judge outcomes and all units
without the check retain `state_for_verdict()` behavior.

Do not generalize this to every deterministic check in this change. A unit has
opted into a hard UI budget by carrying `max-length:N`; extending automatic
repair policy to other checks needs its own design.

### Step 3: Run and commit

```bash
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  -n 0 -q

git add weblate/trans/judge_loop.py \
  weblate/trans/autotranslate.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py
git commit -m "fix(judge): repair over-budget automatic translations"
```

## Task 4: Register checks, document configuration, and define rollout gates

**Files:**

- Modify: `dev-docker/docker-compose.yml`
- Modify: `deploy/environment.example`
- Modify: `docs/changes.rst`
- Modify: `docs/guides/producer-guide-weblate.md`

### Step 1: Replace only the two check classes

In both deployment environments remove only:

```text
weblate.checks.chars.MaxLengthCheck,weblate.checks.source.SourceMaxLengthCheck
```

and add only:

```text
weblate_customization.checks.GameMaxLengthCheck,weblate_customization.checks.GameSourceMaxLengthCheck
```

Do not remove or add `MaxSizeCheck`, `GameLengthCheck`, or any unrelated check.
`modify_env_list()` adds before removing (`weblate/utils/environment.py:182-188`),
so the final registry has one implementation per inherited check ID.

### Step 2: Add user-facing documentation

Add a short Russian subsection immediately after the existing `Флаги перевода`
bullet in `docs/guides/producer-guide-weblate.md`:

- Project/component flags are defaults; a source-file or unit flag overrides
  them for a specific UI slot.
- `max-length:N` is a static character budget. Use it only where the same
  budget really applies.
- `replacements:` describes a selected width scenario; it is not live engine
  evaluation. It must use whole placeholder tokens such as `{hours}`.
- Add `ignore-source-max-length` only to a source intentionally using all of a
  fixed slot.
- For no-human automatic correction, run the judge workflow: a failing
  `max-length` candidate is retried and otherwise remains `Needs editing`.

Do not name Need for Greed, German abbreviations, or `11` in the guide.

### Step 3: Add the unreleased changelog entry

Add one concise bullet in the current `Improvements` section:

```rst
* Hero Craft conditional placeholders now use their worst-case visible branch when evaluating ``max-length`` and ``source-max-length`` budgets. Judge-mode automatic translations that exceed an opted-in ``max-length`` budget are retried with the concrete limit and remain pending when the limit cannot be met.
```

### Step 4: Run scoped checks and commit

```bash
uv run prek run ruff-check ruff-format --files \
  weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py \
  weblate/trans/autotranslate.py \
  weblate/trans/judge_loop.py \
  weblate/trans/tests/test_autotranslate.py \
  weblate/trans/tests/test_judge_loop.py \
  weblate/trans/tests/test_judge_autotranslate.py \
  --skip typos --skip reuse --skip kingfisher-auto
uv run prek run --files \
  docs/changes.rst \
  docs/guides/producer-guide-weblate.md \
  --skip typos --skip reuse --skip kingfisher-auto

git add dev-docker/docker-compose.yml deploy/environment.example \
  docs/changes.rst docs/guides/producer-guide-weblate.md
git commit -m "feat(checks): register conditional max-length budgets"
```

Never use `git commit -am` in the shared checkout.

### Step 5: Restart and recomputation gates require separate approval

The environment block is baked into the running container. Do not restart the
shared dev stack, apply live flags, or run management commands until the user
approves that rollout separately.

After an approved dev restart, verify:

```bash
WEBLATE_PORT=3001 ./rundev.sh check
WEBLATE_PORT=3001 ./rundev.sh exec -T weblate weblate shell -c \
  "from weblate.checks.models import CHECKS; print(type(CHECKS['max-length']).__name__); print(type(CHECKS['source-max-length']).__name__)"
```

Expected output includes `GameMaxLengthCheck` and `GameSourceMaxLengthCheck`.

For one explicitly approved project/component rollout only, record the baseline
number of active `max-length` checks, assign its reviewed flags, then run:

```bash
WEBLATE_PORT=3001 ./rundev.sh exec -T weblate weblate updatechecks project-slug/component-slug
```

`updatechecks` calls `unit.run_checks()` for the selected units
(`weblate/checks/management/commands/updatechecks.py:11-19`). Verify the
post-run count and sample affected strings. Never use `--all` for this feature
rollout.

### Step 6: Push only after all approved verification passes

```bash
git push origin HEAD
```

## Acceptance criteria

1. `GameMaxLengthCheck` and `GameSourceMaxLengthCheck` keep the stock
   `max-length` and `source-max-length` IDs, flags, and invalid-flag behavior.
2. A nonempty `|alternate` chooses one longest top-level branch, never the
   concatenation of both alternatives.
3. Consecutive recognized conditionals concatenate their selected maxima; the
   supplied German timer with `9/9/9` measures as 17 and fails a budget of 11.
4. Stock replacements run before branch selection. `{hours}:9` changes the
   nested value while preserving the `hours:cond:` header.
5. A nested recognized conditional uses the documented safe raw fallback.
6. `GameMaxLengthCheck` exposes the exact numeric limit to the LLM
   `failing_checks` context and retains stock malformed-flag diagnostics.
7. In judge mode, an over-budget result is stored in a non-shippable state,
   repaired despite a judge PASS, and remains non-shippable if the bounded
   repair budget is exhausted. Ordinary auto-translate modes retain suggestion
   fallback behavior.
8. Source headroom, plural handling, replacements, and
   `ignore-source-max-length` remain covered by custom regressions.
9. The dev registry resolves only `GameMaxLengthCheck` and
   `GameSourceMaxLengthCheck` after the separately approved restart; existing
   rows are refreshed only through separately approved, component-scoped
   `updatechecks`.
10. The producer guide documents generic per-project configuration without a
    game-specific hard-coded budget. No live flags, restart, recomputation, or
    production deployment occurs as part of this implementation plan.

## Implementation order and parallelization

Sequential implementation. Tasks 1 and 3 both modify
`weblate_customization/checks.py`/the judge flow dependencies; Task 2 consumes
the check interface; Task 4 registers and documents the final behavior. Do not
split them into parallel worktrees.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope and strategy | 0 | Not run | - |
| Codex Review | `/codex review` | Independent second opinion | 0 | Not run | - |
| Eng Review | `/plan-eng-review` | Architecture and tests | 1 | Revised | 10 findings folded into the plan |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | Not applicable | No UI change |
| DX Review | `/plan-devex-review` | Developer experience | 0 | Not run | Producer guide added by eng review |

**CROSS-MODEL:** Independent architect review agreed with the engineering review: the original plan had to be narrowed and the judge repair path completed.

**VERDICT:** ENG REVIEW REVISIONS APPROVED - plan awaits implementation approval and separate rollout approval.

NO UNRESOLVED DECISIONS
