# Length checks and conditional DSL Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Status:** Awaiting implementation approval.

**Goal:** Make every length measurement — `game-length`, `max-length`,
`source-max-length`, `max-size`, and the auto-translate length gate — count the
text a player reads instead of the Hero Craft conditional DSL scaffold, so
per-key length budgets become meaningful for formula strings such as
`humanTimer`, and so a `max-length:N` flag cannot silently divert all machine
output for a formula string into suggestions.

**Architecture:** One new pure function in `weblate_customization/checks.py`
strips the immutable conditional scaffold (outer braces,
`identifier:cond:comparison?` header, top-level `|`) from a text while keeping
branch text, nested placeholders, and everything else byte-identical.
`_visible_length()` consumes it for `game-length`. Three thin subclasses of the
stock length checks override `get_replacement_function()` to strip the scaffold
*after* `replacements:` expansion, and replace the stock classes in
`CHECK_LIST` through the existing `WEBLATE_REMOVE_CHECK`/`WEBLATE_ADD_CHECK`
mechanism, keeping every `check_id`, flag, and documentation anchor unchanged.
The auto-translate length gate stops measuring raw text and instead measures
through the *registered* `max-length` check's replacement function, so core
never imports `weblate_customization` and the gate always agrees with the check
that will judge the stored translation.

**Tech Stack:** Python, `regex`, Django/Weblate checks, pytest in the shared
dev Docker stack.

---

## Context

The developer's question that exposed this (2026-08-26): the `humanTimer`
formula renders `1ч. 2м. 53с.` in Russian but `1Std. 2Min. 53Sek.` in German,
and the German does not fit its slot. The same applies to Latvian and Turkish.
The team plans to import per-key `max-length:N` budgets in bulk, and the team
has no human translators: everything must survive a fully automated
translate → check → judge → repair pipeline.

### Root cause, verified 2026-08-26

`conditional_dsl_syntax_spans()`
(`weblate_customization/src/weblate_customization/checks.py:208-254`) already
parses this DSL, but it feeds only `GameMarkupCheck.check_highlight()` — editor
highlighting, autofix protection, and LLM placeholder protection. No length
consumer uses it. Each one measures the raw scaffold:

|Consumer|Where|What it measures today|
|---|---|---|
|`game-length`|`_visible_length()`, `checks.py:682-684`|`MARKUP.sub("", text)` strips `{hours}` but keeps `{hours:cond:>0?`, `\|`, `}`|
|`max-length`|`weblate/checks/chars.py:552-553`|`len(replace(target))` — `replacements:`-aware, scaffold included|
|`source-max-length`|`weblate/checks/source.py:76-81`|same, plus the 85 % rule for English sources|
|`max-size`|`weblate/checks/render.py:87-90`|renders the scaffold as glyphs, so the measurement itself is invalid; whether it fires depends on the configured pixel limit|
|auto-translate gate|`weblate/trans/autotranslate.py:405-406`|`len(item)` — raw, not even `replacements:`-aware, against `unit.get_max_length()`|

Measured with byte-identical copies of the installed regexes and check logic:

```text
RU source {hours:cond:>0?{hours}ч. |}…   _visible_length = 64, player reads 12 ("1ч. 2м. 53с.")
DE target {hours:cond:>0?{hours}Std. |}… _visible_length = 70, player reads 18 ("1Std. 2Min. 53Sek.")
game-length ratio today: 70/64 = 1.09 (scaffold noise) — true ratio 18/12 = 1.50
```

The scaffold is near-identical in every language, so it dominates both sides of
the ratio and dilutes the real signal. `game-length` stays silent by
coincidence, not by verification. Any `max-length:N` flag set on a formula
string is nonsense today: the scaffold alone eats ~64 characters, so a budget
of 15 visible characters cannot be expressed at all.

### The auto-translate gate is the automation blocker

`weblate/trans/autotranslate.py:405-406`:

```python
max_length = unit.get_max_length()
if self.mode == "suggest" or any(len(item) > max_length for item in target):
    _, result = Suggestion.objects.add(...)
```

`Unit.get_max_length()` (`weblate/trans/models/unit.py:2588-2606`) returns the
`max-length` flag value when the flag exists (`:2597-2598`); without a flag the
source-derived fallback `max(100, len(source) * 10)` is far too big to matter
for formula strings. Today the gate never fires on them. The moment a
`max-length:16` flag lands on `humanTimer`, the raw ~90-character machine
output is always `> 16`, and **every** machine translation of that string is
diverted into a `Suggestion` — which, in a pipeline with no humans, nobody will
ever accept. The gate must measure the same text the `max-length` check will
measure, or the flag import kills auto-translation for exactly the strings it
is meant to protect.

The other `get_max_length()` consumers were audited and need no change:

- `weblate/trans/forms.py:544` puts it in `data-max`; the editor JS
  (`weblate/static/editor/base.js:157-170`) only styles a character counter
  (`has-warning`/`has-error`), saving is never blocked. A formula string with a
  tight budget shows a red counter to a human — cosmetic, accepted.
- `weblate/trans/validators.py:29-33` allows `10 * (max_length + 100)` — with
  a budget of 16 that is 1160 characters of headroom, no formula string comes
  close.

### What this plan does *not* fix, on purpose

- Even with honest counting, `game-length` will not fire on `humanTimer`
  (12 → 18 sits under the `_LENGTH_TIERS` floor at `checks.py:692-698`, by
  design — short labels get slack). The enforcement lever for tight slots is a
  per-key `max-length:N` flag, which this plan makes meaningful. Retuning the
  tiers is out of scope.
- Structural tampering with the DSL (`>0` → `<0`, a lost `|`, whitespace inside
  a header) is the subject of
  `docs/product/plans/2026-08-24-05-conditional-dsl-validation.md`
  (awaiting approval). This plan does not validate anything; it only measures.
  Land that plan first: it touches the same file, and its signature parser is
  the natural future home for the scaffold walk added here.

### How the repair pipeline picks these checks up — no extra wiring

`weblate/machinery/llm.py:1035-1039` already feeds a unit's failing checks,
with descriptions, into every translation request, and prompt rule 23
(`llm.py:199`) requires the model to change the translation so every listed
check passes. The judge repair loop hands them to the judge as evidence
(`weblate/trans/judge_loop.py:79`) and rolls back any repair that introduces a
new deterministic failure (`judge_loop.py:260-269`). A DSL-aware `max-length`
failure therefore automatically becomes a repair instruction on the next
translation round and a no-regress gate on every repair.

### Worked example the plan must enable

With this plan implemented, a formula string can carry:

```text
max-length:16, replacements:{hours}:8:{minutes}:59:{seconds}:59
```

`replacements:` expands the value placeholders first (stock behavior,
`weblate/checks/base.py:283-301`), then the scaffold is stripped:

```text
RU "8ч. 59м. 59с."      = 13 ≤ 16  pass
DE "8Std. 59Min. 59Sek." = 19 > 16  max-length fires — the defect the developer asked about
TR "8 sa. 59 dk. 59 sn." = 19 > 16  fires
```

Without `replacements:`, nested placeholders keep counting at their literal
width (`{hours}` = 7), exactly as stock `max-length` counts them today — the
convention does not change, only the scaffold stops counting.

### Fixed contract

1. Only the scaffold of a *recognized outermost* conditional is stripped: the
   outer `{` and `}`, the exact `identifier:cond:comparison?` header, and each
   top-level `|`. Recognition is `_CONDITIONAL_HEADER` (`checks.py:185-187`),
   unchanged.
2. Nested placeholders (`{hours}`) are **kept**: they count like every other
   placeholder unless `replacements:` expands them.
3. The `:` between two adjacent placeholders (`{minutes:00}:{seconds:00}`) is
   **kept**: it renders (`12:30`) even though it is protected from editing.
   Protection and visibility are different properties.
4. A text without `:cond:` passes through byte-identical, guarded by a
   C-level substring test before any parsing.
5. An unrecognized conditional (`{value:cond:1}` — no `?`) passes through
   byte-identical.
6. Scaffold stripping happens *after* `replacements:` expansion, so
   `replacements:{hours}:8` sees `{hours}` intact.
7. Every `check_id` (`max-length`, `source-max-length`, `max-size`,
   `game-length`), every flag (`max-length:N`, `ignore-max-length`,
   `replacements:`), and every documentation anchor stays unchanged.
8. `max-lines` is not touched: the scaffold contains no newline, so stripping
   cannot change a line count.
9. The auto-translate gate measures through the **registered** `max-length`
   check (`weblate.checks.models.CHECKS`), never by importing
   `weblate_customization`: on a stock deployment the gate becomes
   `replacements:`-aware (it finally agrees with the check), on this fork's
   deployment it resolves to the DSL-aware subclass for free.

### CHECK_LIST replacement is safe — demonstrated

- The registry is keyed by `check_id`, not class path
  (`weblate/utils/classloader.py:102`:
  `result[obj.get_identifier()] = obj`), so a subclass with an inherited
  `check_id` is a drop-in.
- `Check` DB rows store the `check_id` string
  (`weblate/checks/models.py:86`), so existing failing-check rows and
  dismissals survive the swap.
- `modify_env_list` folds add-then-remove
  (`weblate/utils/environment.py:184-187`), so no duplicate id ever sits in
  `CHECK_LIST`.
- The exact removal strings exist in `DEFAULT_CHECK_LIST`
  (`weblate/checks/defaults.py:22,63,81`).
- The mechanism is the one already carrying the Game* checks
  (`dev-docker/docker-compose.yml:64`).

### Implementation boundaries

- Work in the existing checkout and the shared `dev-docker` stack; copy
  `weblate_customization` into `dev-docker/data/python/` after every edit.
- No edits to stock check classes under `weblate/checks/`: they stay intact and
  are swapped per-deployment, so a plain Weblate installation is unaffected.
  The single core edit is the auto-translate gate (Task 4), and it stays
  generic.
- Do not run anything against production. The compose restart in Task 5
  recreates the shared dev containers and needs its own explicit go-ahead.
- `weblate/trans/tests/test_autotranslate.py` is flaky under xdist in the dev
  container: run it with `-n 0` and compare against a baseline run before
  blaming a change.
- Use @superpowers:test-driven-development for Tasks 1-4.

---

### Task 1: The scaffold stripper and its contract tests

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py`
- Modify: `weblate_customization/tests/test_checks.py`

#### Step 1: Add the failing tests

`StripConditionalScaffoldTest(SimpleTestCase)` with fixtures already present
in the test module (`HUMAN_TIMER_EN`, `HUMAN_TIMER_DE`, `TIMER`,
`AMOUNT_FORMATTED`):

- `HUMAN_TIMER_EN` → `"{hours}h. {minutes}m. {seconds}s."` (scaffold gone,
  placeholders and branch text kept, including branch-trailing spaces).
- `HUMAN_TIMER_DE` → `"{hours}Std. {minutes}Min. {seconds}Sek."`.
- `TIMER` (`{hours:cond:>0?{hours:00}:|}{minutes:00}:{seconds:00}`) →
  `"{hours:00}:{minutes:00}:{seconds:00}"` — the adjacent-placeholder colons
  survive, both inside and outside the branch.
- `AMOUNT_FORMATTED` → `"{value:amount()}{value:N0}"`.
- `"{value:cond:1}"` → unchanged (unrecognized, no `?`).
- `"Text without conditionals {0} <b>x</b>"` → returned by identity
  (`assertIs`), proving the `":cond:"` guard short-circuits.

Run (expected: FAIL — function does not exist):

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh test \
  weblate_customization/tests/test_checks.py::StripConditionalScaffoldTest -n 0 -q
```

#### Step 2: Implement

Add below `conditional_dsl_syntax_spans()`:

```python
def strip_conditional_scaffold(text: str) -> str:
    """
    Text with the immutable conditional scaffold removed.

    What remains is what the player can read: branch text and nested
    placeholders. Placeholders keep their literal width, as they do for
    every stock length check, unless `replacements:` expanded them first.
    """
    if ":cond:" not in text:
        return text
    spans: list[tuple[int, int]] = []
    for start, end, _children in _balanced_brace_blocks(text):
        header = _CONDITIONAL_HEADER.match(text, start + 1, end - 1)
        if header is None:
            continue
        spans.extend(((start, header.end()), (end - 1, end)))
        depth = 0
        for position in range(header.end(), end - 1):
            char = text[position]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            elif char == "|" and depth == 0:
                spans.append((position, position + 1))
    if not spans:
        return text
    pieces = []
    previous = 0
    for start, end in sorted(spans):
        pieces.append(text[previous:start])
        previous = max(previous, end)
    pieces.append(text[previous:])
    return "".join(pieces)
```

Note the deliberate differences from `conditional_dsl_syntax_spans()`: child
placeholder spans and the adjacent-colon rule are *not* collected — those are
protected from editing but rendered to the player. If plan 2026-08-24-05 has
landed by implementation time, derive the spans from `_parse_conditional_dsl()`
instead of repeating the walk; the tests above are the contract either way.

#### Step 3: Run the tests, then commit

Same command as Step 1, expected: all new tests pass. Then:

```bash
git add weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py
git commit -m "feat(checks): strip conditional DSL scaffold for length measurement"
```

---

### Task 2: Make `game-length` measure the player-visible text

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py:682-684`
- Modify: `weblate_customization/tests/test_checks.py`

#### Step 1: Failing test

In `GameLengthCheckTest`:

```python
    def test_visible_length_ignores_conditional_scaffold(self) -> None:
        self.assertEqual(_visible_length(HUMAN_TIMER_EN), len("h. m. s."))
        self.assertEqual(_visible_length(HUMAN_TIMER_DE), len("Std. Min. Sek."))
```

`check_single(HUMAN_TIMER_EN, HUMAN_TIMER_DE, None)` must stay `False` — the
pair sits under the tier floor by design.

#### Step 2: Implement

```python
def _visible_length(text: str) -> int:
    """Length of what the player reads: markup, placeholders and DSL scaffold removed."""
    return len(MARKUP.sub("", strip_conditional_scaffold(text)))
```

#### Step 3: Run the whole custom check module, commit

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh test weblate_customization/tests/test_checks.py -n 0 -q
```

Expected: all pass; no existing `game-length` expectation changes, because no
existing fixture pairs a conditional source with a non-conditional target.

```bash
git commit -am "feat(checks): game-length measures branch text, not DSL scaffold"
```

---

### Task 3: DSL-aware `max-length`, `source-max-length`, `max-size`

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py`
- Modify: `weblate_customization/tests/test_checks.py`

#### Step 1: Failing tests

```python
class GameMaxLengthCheckTest(SimpleTestCase):
    def test_budget_applies_to_visible_text(self) -> None:
        check = GameMaxLengthCheck()
        replace = check.get_replacement_function(MockUnit(
            flags='max-length:16, replacements:{hours}:8:{minutes}:59:{seconds}:59'
        ))
        self.assertEqual(replace(HUMAN_TIMER_EN), "8h. 59m. 59s.")
        self.assertEqual(replace(HUMAN_TIMER_DE), "8Std. 59Min. 59Sek.")
```

Plus one end-to-end row: `check_target_params(..., value=[16])` is `False` for
the EN text (13) and `True` for the DE text (19). Reuse the existing mock-unit
helper the module already uses for flag-carrying tests; if none exists, follow
`weblate/checks/tests/test_chars.py` for constructing one.

#### Step 2: Implement

```python
class _StripScaffoldMixin:
    """Length checks measure visible text: expand replacements, then drop DSL scaffold."""

    def get_replacement_function(self, unit):
        replace = super().get_replacement_function(unit)
        return lambda text: strip_conditional_scaffold(replace(text))


class GameMaxLengthCheck(_StripScaffoldMixin, MaxLengthCheck): ...
class GameSourceMaxLengthCheck(_StripScaffoldMixin, SourceMaxLengthCheck): ...
class GameMaxSizeCheck(_StripScaffoldMixin, MaxSizeCheck): ...
```

Imports: `from weblate.checks.chars import MaxLengthCheck`,
`from weblate.checks.source import SourceMaxLengthCheck`,
`from weblate.checks.render import MaxSizeCheck`. No other body: `check_id`,
name, description, params are inherited, so `./manage.py list_checks` output
and every docs anchor stay identical.

#### Step 3: Run, commit

```bash
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh test weblate_customization/tests/test_checks.py -n 0 -q
git commit -am "feat(checks): DSL-aware max-length, source-max-length, max-size"
```

---

### Task 4: The auto-translate gate measures like the `max-length` check

Without this task, a `max-length:N` flag on a formula string silently diverts
every machine translation into a suggestion that no one will accept in a
pipeline without humans.

**Files:**

- Modify: `weblate/trans/autotranslate.py:405-406`
- Modify: `weblate/trans/tests/test_autotranslate.py`

#### Step 1: Failing test

In `weblate/trans/tests/test_autotranslate.py`, on a unit whose source is a
conditional formula and whose flags are
`max-length:16, replacements:{hours}:8:{minutes}:59:{seconds}:59`, a mocked MT
result that is a formula string (raw length ~90, visible 13) must be written as
a **translation**, not a suggestion. Control case: a plain (non-formula)
result whose measured length exceeds the budget still becomes a suggestion.
Follow the module's existing mock-machinery pattern.

Run first against the unmodified module to record the baseline (the file is
flaky under xdist; always `-n 0`):

```bash
WEBLATE_PORT=3001 ./rundev.sh test weblate/trans/tests/test_autotranslate.py -n 0 -q
```

#### Step 2: Implement

In `AutoTranslate.update()` (`weblate/trans/autotranslate.py:405-406`), measure
through the registered `max-length` check:

```python
from weblate.checks.models import CHECKS  # module-level import

max_length = unit.get_max_length()
max_length_check = CHECKS.get("max-length")
measure = (
    max_length_check.get_replacement_function(unit)
    if max_length_check is not None
    else lambda text: text
)
if self.mode == "suggest" or any(len(measure(item)) > max_length for item in target):
```

This is deliberately generic core behavior: the gate now measures exactly what
the registered `max-length` check will measure. On stock class lists that adds
`replacements:` awareness (gate and check finally agree); after Task 5's swap
it resolves to `GameMaxLengthCheck` and becomes DSL-aware, with no core import
of `weblate_customization`. A deployment that removed the `max-length` check
entirely keeps today's raw behavior.

Boundary: formats that import a physical width limit as a `max-length` flag
(for example XLIFF `maxwidth`) would now measure replaced text against it.
This fork's formats are monolingual PO/CSV game kits; none import such flags.

#### Step 3: Run, compare to baseline, commit

```bash
WEBLATE_PORT=3001 ./rundev.sh test weblate/trans/tests/test_autotranslate.py -n 0 -q
git add weblate/trans/autotranslate.py weblate/trans/tests/test_autotranslate.py
git commit -m "fix(trans): auto-translate length gate measures like max-length check"
```

---

### Task 5: Registration, changelog, and the restart gate

**Files:**

- Modify: `dev-docker/docker-compose.yml:64` (and add `WEBLATE_REMOVE_CHECK`)
- Modify: `deploy/environment.example`
- Modify: `docs/changes.rst` (top unreleased section)

#### Step 1: Swap the classes per-deployment

In `dev-docker/docker-compose.yml`, extend the `weblate` service environment:

```yaml
WEBLATE_REMOVE_CHECK: weblate.checks.chars.MaxLengthCheck,weblate.checks.source.SourceMaxLengthCheck,weblate.checks.render.MaxSizeCheck
```

and append to the existing `WEBLATE_ADD_CHECK` line:

```text
,weblate_customization.checks.GameMaxLengthCheck,weblate_customization.checks.GameSourceMaxLengthCheck,weblate_customization.checks.GameMaxSizeCheck
```

Mirror both (as `WEBLATE_REMOVE_CHECK=` / additions to `WEBLATE_ADD_CHECK=`) in
`deploy/environment.example`.

#### Step 2: Changelog

One bullet in the unreleased `Improvements` section of `docs/changes.rst`:

```rst
* The ``max-length``, ``source-max-length``, ``max-size`` and ``game-length`` checks, and the automatic translation length gate, now measure the player-visible text of Hero Craft conditional placeholders such as ``{hours:cond:>0?{hours}h. |}``: the conditional scaffold no longer counts toward a length budget, so ``max-length:N`` flags become meaningful for formula strings and no longer divert machine translations into suggestions.
```

#### Step 3: Scoped hooks

```bash
uv run prek run ruff-check ruff-format --files \
  weblate_customization/src/weblate_customization/checks.py \
  weblate_customization/tests/test_checks.py \
  weblate/trans/autotranslate.py \
  weblate/trans/tests/test_autotranslate.py
uv run prek run trailing-whitespace end-of-file-fixer rst-double-space \
  sphinx-lint codespell --files docs/changes.rst
```

Do not run `--all-files`; `typos` and `reuse` scan the whole tree regardless
and report pre-existing findings tracked in
`docs/product/plans/2026-08-23-prek-full-pass-remaining-hooks.md`.

#### Step 4: Restart gate — explicit approval required

The environment block is baked at container creation: the swap takes effect
only after a full `./rundev.sh` (rebuild + start) of the shared dev stack.
Stop and ask before running it. After the restart, verify in the container:

```bash
WEBLATE_PORT=3001 ./rundev.sh check
docker compose -f dev-docker/docker-compose.yml exec weblate weblate shell -c \
  "from weblate.checks.models import CHECKS; print(type(CHECKS['max-length']))"
```

Expected: `GameMaxLengthCheck`. Production deployment is out of scope and
needs its own approval per `AGENTS.md`.

#### Step 5: Commit and push

```bash
git add dev-docker/docker-compose.yml deploy/environment.example docs/changes.rst \
  docs/product/plans/2026-08-26-length-checks-conditional-dsl.md
git commit -m "feat(checks): register DSL-aware length checks"
git push origin HEAD
```

---

## Out of scope

- **`game-number` and scaffold digits.** `_prepare()` (`checks.py:417-423`)
  reads `>0`/`>99999` comparison digits as quantities. Today this is harmless
  (the scaffold contributes equal counts to both sides) and even accidentally
  reports some comparison tampering; plan 2026-08-24-05 reports that tampering
  properly. Strip the scaffold there only if real false positives appear, and
  only after 2026-08-24-05 lands — otherwise the accidental protection is
  removed with nothing replacing it.
- Retuning `_LENGTH_TIERS` or `_LENGTH_MAX_RATIO`.
- Choosing per-key `max-length:N` budgets and importing them in bulk — that is
  the developer's flag-import task, a separate plan. It must not start before
  this plan lands (Task 4 is its prerequisite).
- Font upload, font groups, and actual `max-size` limits — separate adoption
  work (`docs/llm-first/plans/2026-08-10-git-localization-quality-gate.md`
  covers the COL4 variant).
- The editor character counter (`data-max`): it shows raw length and will show
  red on formula strings with tight budgets. Advisory styling only
  (`weblate/static/editor/base.js:157-170`), saving is not blocked.
- Repairing stored translations, suggestions, or translation memory.
- Moving the DSL scanner into `weblate/trans/protected_tokens.py`.

## Acceptance criteria

1. `strip_conditional_scaffold(HUMAN_TIMER_EN)` == `"{hours}h. {minutes}m. {seconds}s."`;
   the TIMER adjacent-colons and `{value:cond:1}` cases hold as specified.
2. `_visible_length(HUMAN_TIMER_DE)` == 14 (`"Std. Min. Sek."`), not 70.
3. With `max-length:16, replacements:{hours}:8:{minutes}:59:{seconds}:59`,
   the EN/RU text passes and the DE/TR text fails `max-length`.
4. With the same flags, auto-translate writes a formula MT result as a
   translation, not a suggestion; an over-budget plain result still becomes a
   suggestion.
5. A text without `:cond:` is returned by identity from the stripper, and no
   stock check behavior changes for it.
6. `CHECKS["max-length"]`, `["source-max-length"]`, `["max-size"]` resolve to
   the Game* subclasses in the dev container after the approved restart;
   `check_id`s, flags, and docs anchors are unchanged.
7. `weblate_customization/tests/test_checks.py` fully passes;
   `weblate/trans/tests/test_autotranslate.py` matches its pre-change baseline
   plus the new tests; no stock test under `weblate/checks/tests/` is modified.
8. Only the files named in the tasks are committed; nothing is deployed to
   production.
