# Engine line separator as an instance-wide rule - implementation plan

**Goal:** `$`, the Hero Craft engine line separator, becomes machine-enforced for
every string whose source uses it tightly: whitespace hugging a separator is
repaired deterministically before the text is stored, and a lost or added
separator is reported as a failing check for a human. Strings whose source does
not identify `$` as an engine separator are deliberately outside this rule.

**Architecture:** Two new classes in the existing `weblate_customization`
package, registered through the existing `WEBLATE_ADD_CHECK` /
`WEBLATE_ADD_AUTOFIX` environment mechanism. Nothing under `weblate/` changes.
The check owns the shared regexes; the autofix imports them, mirroring how
`weblate/trans/autofixes/chars.py` imports from `weblate/checks/chars.py`.

**Tech Stack:** Python, `regex`, `weblate.checks.base.TargetCheck`,
`weblate.trans.autofixes.base.AutoFix`, DB-free `SimpleTestCase` factories.

## Why this shape

Two defects were measured on prod in `col4/localizecommon/fr`, both from the
same cause: the model treated `$` as punctuation rather than a line break.

| Key | Source | Machine output | Defect |
|---|---|---|---|
| `DEMO_THANKS` | `...игры$Choice of Life: Samosbor` | `...à la démo de Choice of Life: Samosbor` | separator dropped, two lines became one |
| `DATA_LOADING` | `...данных$Пожалуйста...` | `...en cours.$\u00a0Veuillez...patienter.\u00a0$Cela...` <!-- # codespell:ignore --> | NBSP hugging the separator |
| `BUG_TEXT` | `...почту:$blazing...` | `...e-mail\u00a0:$blazing...` plus `.$\u00a0Veuillez` | same |

The two defects need different mechanisms, and that split is the whole design:

- **Hugging whitespace is unambiguous.** There is exactly one correct repair, so
  it belongs in an autofix, which runs before storage and needs no human.
- **A lost separator is not repairable.** Where the break belonged cannot be
  recovered from the translation, so it belongs in a check.

Verified code facts the plan depends on:

- `weblate/trans/models/suggestion.py:75` calls `fix_target` when a suggestion
  is created, so the autofix covers machine output, not only human saves.
- `weblate/trans/models/unit.py:2405` calls `fix_target` on `translate()`, and
  `Suggestion.accept()` goes through `translate()`
  (`weblate/trans/models/suggestion.py:212`). Autofixes therefore run again at
  acceptance: installing the autofix **before** the pending 82 suggestions are
  accepted repairs `DATA_LOADING` and `BUG_TEXT` without hand edits.
- `weblate/checks/flags.py:164` derives `IGNORE_CHECK_FLAGS` from the check
  registry, so `ignore-game-line-break` becomes a valid flag as soon as the
  check is registered. No flag plumbing needed.
- `modify_env_list` (`weblate/utils/environment.py:184`) inserts at index 0, so
  the new autofix runs first in `AUTOFIX_LIST`. Order is immaterial here:
  `PunctuationSpacing` only rewrites the space preceding `: ; ? !`
  (`weblate/checks/chars.py:33-38`) and never touches `$`.
- `Suggestion.accept()` runs `translate()` only when the suggestion differs from
  the current target or the unit is not yet translated
  (`weblate/trans/models/suggestion.py:206-218`). The rollout preflight must
  reject a pending suggestion that would take the no-op branch; that branch is
  deleted without another autofix pass.

## Scope locked

- **No changes under `weblate/`.** This is a customization-package change. A
  need to edit core checks or autofixes is a design regression.
- **Source-gated.** The check first establishes that the source itself uses `$`
  tightly, then compares counts and looks for target whitespace. A source that
  spaces its dollar signs is using currency, and a blanket trim would turn French
  `5 $` into `5$` - a new typographic defect. A target-only `$` is likewise out
  of scope because the source provides no evidence that it is engine syntax.
- **One operator switch.** The autofix honours the check's own
  `ignore-game-line-break` flag, so a component or string opts out of both at
  once.
- **Separate check id.** `game-line-break`, not an extension of
  `game-markup`: merged, they could only be disabled together, and markup and
  line breaks fail for unrelated reasons.
- **Count comparison is exact.** Once source-gated,
  `source.count("$") != target.count("$")`, not a set comparison. The built-in
  `placeholders:$` flag was considered and rejected for exactly this:
  `weblate/checks/placeholders.py:83` compares sets, so it catches 1 lost out of
  1 but not 1 lost out of 2, which leaves `BUG_TEXT` (`$$`) unguarded.
- **Prompt wording is out of this plan.** A rule in the project style field is
  cheap and worth adding, but it is not a guarantee and does not replace either
  mechanism here.

## What already exists

| Existing layer | Reuse |
|---|---|
| `weblate_customization` package | `uv_build` package with `checks.py`, `machinery.py`, `tests/`. Add `autofixes.py` beside them. |
| `GameMarkupCheck` | Same `TargetCheck` shape, same `default_disabled = False` posture, same multiset comparison idea. |
| `WEBLATE_ADD_CHECK` / `WEBLATE_ADD_AUTOFIX` | Registration without touching settings files. `ADD_CHECK` already carries `GameMarkupCheck` on prod. |
| `weblate.trans.tests.factories.make_unit` | Unsaved units, no database, used by the existing custom check tests. |
| `weblate.checks.tests.test_checks.CheckTestCase` | Runs the standard good/failure/ignore matrix for a check. |
| `deploy/vps.sh deploy` | `weblate_customization/` is in `IMAGE_PATHS`, so the rollout picks a rebuild by itself. |

## Failure modes and guards

| Failure | Guard | Evidence |
|---|---|---|
| Currency `5 $` gets its space eaten | `separator_is_tight` gate on the source | Autofix test with a spaced source |
| A legitimate `$`-free project starts failing | Return before both count and whitespace checks unless the source is tight | Check test with source-free and target-only `$` |
| Operator cannot silence a false positive | `ignore-game-line-break` honoured by check and autofix | Autofix flag test, `CheckTestCase` ignore row |
| Non-breaking spaces slip past the trim | Space class covers `U+0020 U+0009 U+00A0 U+2009 U+202F` | Autofix test with each character |
| Autofix fights the French punctuation autofix | Disjoint targets: one rewrites space before `: ; ? !`, the other around `$` | Live `DATA_LOADING` verification after both run |
| Already-stored strings keep a stale check state | `weblate updatechecks` after rollout | Failing-check count on prod before/after |
| Dev and prod diverge again | Register in `dev-docker/docker-compose.yml` in the same task as prod | Container settings dump |

## NOT in scope

- Dropping the `$`-splitting into a file format or a plural handler: the
  separator is engine text, and Weblate must keep it verbatim.
- Autorepair of a lost separator, including "put it back where the source has
  it": the target's word order is not the source's, and a wrong break is worse
  than a reported one.
- The added-final-period defect (`DATA_LOADING`): grammatically plausible, no
  mechanical signature, stays a prompt and review matter.
- Mixed `U+00A0`/`U+202F` before punctuation: that is Weblate's
  `PunctuationSpacing` following the French norm, and it is correct.
- `enforced_checks`: blocking a save also blocks accepting a suggestion. Offered
  as an operator option after the rule has run for one cycle, not now.

---

### Task 1: check that a separator was neither lost nor hugged

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py`
- Test: `weblate_customization/tests/test_checks.py`

**Step 1: write the failing test** - append to `tests/test_checks.py`:

```python
class GameLineBreakCheckTest(CheckTestCase):
    check = GameLineBreakCheck()

    def setUp(self) -> None:
        super().setUp()
        self.test_good_matching = ("Line$Next", "Ligne$Suivante", "")
        # A dropped separator merges two lines on screen.
        self.test_failure_1 = ("Line$Next", "Ligne Suivante", "")
        # Two separators in, one out: a set comparison would miss this.
        self.test_failure_2 = ("Warning!$$Body", "Attention !$Corps", "")
        # Whitespace beside a separator renders as a stray indent.
        self.test_failure_3 = ("Line$Next", "Ligne$\u00a0Suivante", "")

    def test_currency_source_is_not_policed(self) -> None:
        self.assertFalse(self.check.check_single("Price 5 $", "Prix 5 $", None))

    def test_target_only_separator_is_not_policed(self) -> None:
        self.assertFalse(self.check.check_single("One line", "Une$ligne", None))

    def test_separator_free_strings_pass(self) -> None:
        self.assertFalse(self.check.check_single("Plain", "Simple", None))
```

Add `GameLineBreakCheck` to the existing import from `weblate_customization.checks`.

#### Step 2: verify red

Run: `./rundev.sh test weblate_customization/tests/test_checks.py -n0 -q`

Expected: FAIL with `ImportError` on `GameLineBreakCheck`.

**Step 3: add the shared vocabulary** - after `PLACEHOLDER_PATTERN`:

```python
# `$` is the engine's line separator, not a character. Whitespace beside one
# renders as a stray indent; a lost one merges two lines.
SEPARATOR_SPACE = r"[ \t\u00a0\u2009\u202f]"
SEPARATOR_HUGGED = regex.compile(rf"{SEPARATOR_SPACE}\$|\${SEPARATOR_SPACE}")
# A separator sits tight between two lines. A source that spaces its dollar
# signs is using them for something else - currency, most likely - and its
# spacing is not ours to police.
SEPARATOR_LOOSE_IN_SOURCE = regex.compile(
    rf"{SEPARATOR_SPACE}\$|\${SEPARATOR_SPACE}|^\$|\$$"
)


def separator_is_tight(source: str) -> bool:
    """Whether the source uses `$` as a line separator we can reason about."""
    return "$" in source and not SEPARATOR_LOOSE_IN_SOURCE.search(source)
```

**Step 4: add the check** - after `GameMarkupCheck`:

```python
class GameLineBreakCheck(TargetCheck):
    """`$` is a line break: the count must match and nothing may hug it."""

    check_id = "game-line-break"
    name = gettext_lazy("Game line break")
    description = gettext_lazy(
        "The number of $ line separators does not match the source, or "
        "whitespace sits next to a separator."
    )
    # Always on: a separator is engine syntax, not a per-component preference.
    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        if not separator_is_tight(source):
            return False
        return source.count("$") != target.count("$") or bool(
            SEPARATOR_HUGGED.search(target)
        )
```

#### Step 5: verify green

Run: `./rundev.sh test weblate_customization/tests/test_checks.py -n0 -q`

**Acceptance:** every new row passes, and the existing `GameMarkupCheckTest`
still passes unchanged.

---

### Task 2: autofix that strips whitespace around a separator

**Files:**

- Create: `weblate_customization/src/weblate_customization/autofixes.py`
- Test: `weblate_customization/tests/test_autofixes.py`

**Step 1: write the failing test** - new file `tests/test_autofixes.py`:

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the engine line separator autofix."""

from __future__ import annotations

from django.test import SimpleTestCase

from weblate_customization.autofixes import LineSeparatorSpacing

from weblate.trans.tests.factories import make_unit


class LineSeparatorSpacingTest(SimpleTestCase):
    fix = LineSeparatorSpacing()

    def test_strips_every_space_kind(self) -> None:
        unit = make_unit(source="Line$Next", code="fr")
        for space in (" ", "\t", "\u00a0", "\u2009", "\u202f"):
            self.assertEqual(
                self.fix.fix_target([f"Ligne{space}${space}Suivante"], unit),
                (["Ligne$Suivante"], True),
            )

    def test_clean_target_is_untouched(self) -> None:
        unit = make_unit(source="Line$Next", code="fr")
        self.assertEqual(
            self.fix.fix_target(["Ligne$Suivante"], unit),
            (["Ligne$Suivante"], False),
        )

    def test_currency_source_is_left_alone(self) -> None:
        unit = make_unit(source="Price 5 $", code="fr")
        self.assertEqual(self.fix.fix_target(["Prix 5 $"], unit), (["Prix 5 $"], False))

    def test_separator_free_source_is_left_alone(self) -> None:
        unit = make_unit(source="Plain", code="fr")
        self.assertEqual(self.fix.fix_target(["5 $"], unit), (["5 $"], False))

    def test_ignore_flag_disables_the_fix(self) -> None:
        unit = make_unit(source="Line$Next", code="fr", flags="ignore-game-line-break")
        self.assertEqual(
            self.fix.fix_target(["Ligne \u00a0$ Suivante"], unit),
            (["Ligne \u00a0$ Suivante"], False),
        )

    def test_real_prod_regression(self) -> None:
        unit = make_unit(source="Идет скачивание$Пожалуйста$В зависимости", code="fr")
        target = (
            "Téléchargement en cours.$\u00a0Veuillez patienter.\u00a0$"  # codespell:ignore
            "Cela peut prendre plusieurs minutes."
        )
        expected = (
            "Téléchargement en cours.$Veuillez patienter.$"  # codespell:ignore
            "Cela peut prendre plusieurs minutes."
        )
        self.assertEqual(self.fix.fix_target([target], unit), ([expected], True))
```

#### Step 2: verify red

Run: `./rundev.sh test weblate_customization/tests/test_autofixes.py -n0 -q`

Expected: FAIL with `ModuleNotFoundError` on `weblate_customization.autofixes`.

#### Step 3: implement

```python
# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic repair of Hero Craft engine line separators."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from django.utils.translation import gettext_lazy

from weblate_customization.checks import (
    SEPARATOR_SPACE,
    GameLineBreakCheck,
    separator_is_tight,
)

from weblate.trans.autofixes.base import AutoFix

if TYPE_CHECKING:
    from weblate.trans.models import Unit

HUGGING_SEPARATOR = re.compile(rf"{SEPARATOR_SPACE}*\${SEPARATOR_SPACE}*")


class LineSeparatorSpacing(AutoFix):
    """`$` is a line break: whitespace beside it renders as a stray indent."""

    fix_id = "line-separator-spacing"
    name = gettext_lazy("Line separator spacing")

    @staticmethod
    def get_related_checks():
        return [GameLineBreakCheck()]

    def fix_single_target(
        self, target: str, source: str, unit: Unit
    ) -> tuple[str, bool]:
        # One switch for the pair: the check's own ignore flag.
        if (
            not separator_is_tight(source)
            or GameLineBreakCheck().ignore_string in unit.all_flags
        ):
            return target, False
        new_target = HUGGING_SEPARATOR.sub("$", target)
        return new_target, new_target != target
```

#### Step 4: verify green

Run: `./rundev.sh test weblate_customization/tests/ -n0 -q`

**Acceptance:** all custom tests pass; the prod regression row reproduces the
exact `DATA_LOADING` string and repairs it.

---

### Task 3: register both on dev and confirm the loaded settings

**Files:**

- Modify: `dev-docker/docker-compose.yml:60`
- Modify: `deploy/environment.example:82`

Dev currently registers only the machinery, so `GameMarkupCheck` has never been
active there while prod runs it. Close that gap in the same edit.

```yaml
      WEBLATE_ADD_MACHINERY: weblate_customization.machinery.RoutedLLMTranslation
      WEBLATE_ADD_CHECK: weblate_customization.checks.GameMarkupCheck,weblate_customization.checks.GameLineBreakCheck
      WEBLATE_ADD_AUTOFIX: weblate_customization.autofixes.LineSeparatorSpacing
```

```ini
WEBLATE_ADD_CHECK=weblate_customization.checks.GameMarkupCheck,weblate_customization.checks.GameLineBreakCheck
WEBLATE_ADD_AUTOFIX=weblate_customization.autofixes.LineSeparatorSpacing
```

Deploy into the running dev container and recreate it, because the module is
copied rather than installed and the environment changed:

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
WEBLATE_PORT=3001 ./rundev.sh
```

**Verify** - the loaded settings, not the file:

```sh
docker compose -f dev-docker/docker-compose.yml exec -T weblate weblate shell -c "
from django.conf import settings
print([a.rsplit('.', 1)[-1] for a in settings.AUTOFIX_LIST])
print([c for c in settings.CHECK_LIST if 'weblate.checks' not in c])
"
```

**Acceptance:** `LineSeparatorSpacing` appears in `AUTOFIX_LIST` and both custom
checks in `CHECK_LIST`.

---

### Task 4: prove the rule on the dev instance end to end

**Files:** none - live verification.

Three things the unit tests cannot show: that a machine suggestion is repaired
before storage, that a legacy suggestion is repaired at acceptance, and that the
check reports the real corpus honestly.

```sh
docker compose -f dev-docker/docker-compose.yml exec -T weblate weblate shell -c "
from weblate.auth.models import AuthenticatedHttpRequest, User
from weblate.trans.models import Component, Suggestion
from weblate.trans.models.suggestion import SuggestionAddResult

comp = Component.objects.get(project__slug='col4', slug='common')
unit = comp.translation_set.get(language__code='fr').unit_set.get(context='DATA_LOADING')
user = User.objects.get(username='admin')
dirty = 'Ligne.\$\u00a0Deux.\u00a0\$Trois.'
clean = 'Ligne.\$Deux.\$Trois.'

# Standard creation repairs before storage. Delete only the exact probe that
# this command created; never delete every pending suggestion on the unit.
created, result = Suggestion.objects.add(unit, [dirty], request=None, user=user)
if result is not SuggestionAddResult.CREATED or created is None:
    raise RuntimeError(f'Creation probe was not created: {result}')
try:
    if created.target != clean:
        raise RuntimeError(repr(created.target))
finally:
    created.delete()

# Bypass the manager to simulate a suggestion saved before the autofix existed.
# Acceptance must invoke Unit.translate() and repair it. Restore the dev fixture
# before the corpus scan so the probe cannot become a false-positive hit.
old_target, old_state = unit.target, unit.state
legacy = Suggestion.objects.create(unit=unit, target=dirty, user=user)
request = AuthenticatedHttpRequest()
request.user = user
try:
    legacy.accept(request)
    unit.refresh_from_db()
    if unit.target != clean:
        raise RuntimeError(repr(unit.target))
finally:
    if Suggestion.objects.filter(pk=legacy.pk).exists():
        legacy.delete()
    unit.refresh_from_db()
    if (unit.target, unit.state) != (old_target, old_state):
        unit.translate(user, old_target, old_state)
print('creation and acceptance repaired:', repr(clean))
"
```

**Acceptance:** both probes pass. The first proves the standard machine
suggestion path stores `Ligne.$Deux.$Trois.`; the second proves an already stored
legacy suggestion is repaired when accepted. The probe restores `DATA_LOADING`
before the corpus scan.

Then measure the corpus:

```sh
docker compose -f dev-docker/docker-compose.yml exec -T weblate weblate shell -c "
from weblate_customization.checks import GameLineBreakCheck
from weblate.trans.models import Component

check = GameLineBreakCheck()
for slug in ('common', 'data'):
    comp = Component.objects.get(project__slug='col4', slug=slug)
    for tr in comp.translation_set.exclude(language=comp.source_language):
        hits = [u.context for u in tr.unit_set.all()
                if check.check_target(
                    u.get_source_plurals(), u.get_target_plurals(), u
                )]
        print(slug, tr.language.code, len(hits), hits[:5])
"
```

**Acceptance:** the French `common` row lists `DEMO_THANKS` among its hits, and
no language shows a hit count that looks like a false-positive wave. Any
unexpected mass is investigated before Task 5, not silenced.

---

### Task 5: roll out to prod, then accept the scoped pending suggestions

**Files:** `/srv/hcgameloc/deploy/.env` on the VPS (gitignored, edited in place).

The only ordering requirement for the free repair is that the autofix is live
*before* acceptance. `updatechecks` does not repair text; it makes the new check
visible on already stored units. Run it before acceptance to expose
`DEMO_THANKS`, then again after the manual correction as the final verification.

1. Append the two variables, matching `deploy/environment.example` from Task 3.
2. `./deploy/vps.sh deploy` - `weblate_customization/` is in `IMAGE_PATHS`, so
   the script selects a rebuild.
3. Confirm the loaded settings with the same shell dump as Task 3, against
   `hcgameloc-weblate-1`.
4. Recalculate only the target translation:

```sh
docker exec -T hcgameloc-weblate-1 weblate updatechecks \
  col4/localizecommon --lang fr
```

5. Print a read-only preflight for the one translation. Confirm the component
   slug, author, and expected 82 rows before running the next command. Do
   **not** use `/api/suggestions/`: that endpoint lists every suggestion the
   token can see and offers no project/component/language filter.

```sh
docker exec -T hcgameloc-weblate-1 weblate shell -c "
from weblate.trans.models import Suggestion, Translation
from weblate.utils.state import STATE_TRANSLATED

translation = Translation.objects.get(
    component__project__slug='col4',
    component__slug='localizecommon',
    language__code='fr',
)
suggestions = list(
    Suggestion.objects.filter(unit__translation=translation)
    .select_related('unit', 'user')
    .order_by('pk')
)
authors = sorted({s.user.username if s.user else '<anonymous>' for s in suggestions})
no_op = [s.pk for s in suggestions if s.unit.target == s.target and s.unit.state >= STATE_TRANSLATED]
print('count:', len(suggestions))
print('authors:', authors)
print('ids:', [s.pk for s in suggestions])
print('contexts:', [s.unit.context for s in suggestions])
print('no-op accepts:', no_op)
if len(suggestions) != 82 or authors != ['mt:openrouter'] or no_op:
    raise SystemExit('Refusing to accept an unexpected batch')
"
```

6. After the preflight has been reviewed, paste its printed `ids` list below and
   accept exactly that immutable snapshot. This deliberately uses
   `Suggestion.accept()` rather than the UI bulk-accept task: the UI skips
   suggestions with failing checks, whereas this first rollout accepts
   `DEMO_THANKS`, leaves its check visible, and fixes it by hand.

```sh
docker exec -T hcgameloc-weblate-1 weblate shell -c "
from weblate.auth.models import AuthenticatedHttpRequest, User
from weblate.trans.models import Suggestion, Translation
from weblate.utils.state import STATE_TRANSLATED

translation = Translation.objects.get(
    component__project__slug='col4',
    component__slug='localizecommon',
    language__code='fr',
)
ids = []  # Paste the exact list printed by the reviewed preflight.
if len(ids) != 82 or len(ids) != len(set(ids)):
    raise SystemExit('Refusing an incomplete or duplicate ID snapshot')
suggestions = list(
    Suggestion.objects.filter(pk__in=ids, unit__translation=translation)
    .select_related('unit', 'user')
    .order_by('pk')
)
authors = sorted({s.user.username if s.user else '<anonymous>' for s in suggestions})
no_op = [s.pk for s in suggestions if s.unit.target == s.target and s.unit.state >= STATE_TRANSLATED]
if len(suggestions) != len(ids) or authors != ['mt:openrouter'] or no_op:
    raise SystemExit('Refusing to accept an unexpected batch')

user = User.objects.get(username='admin')
request = AuthenticatedHttpRequest()
request.user = user

blocked = [
    suggestion.pk
    for suggestion in suggestions
    if not user.has_perm('suggestion.accept', suggestion.unit)
]
if blocked:
    raise RuntimeError(f'Admin cannot accept suggestions: {blocked}')

for suggestion in suggestions:
    suggestion.accept(request)

remaining = list(Suggestion.objects.filter(pk__in=ids).values_list('pk', flat=True))
if remaining:
    raise RuntimeError(f'Suggestions were not accepted: {remaining}')

print('accepted:', len(suggestions))
"
```

7. Re-run the scoped `updatechecks` command after manually correcting
   `DEMO_THANKS`; it is the final persisted-check verification.

**Acceptance:**

- `col4/localizecommon/fr` reports `translated = 82`.
- `DATA_LOADING` and `BUG_TEXT` contain no whitespace beside a `$`.
- `DEMO_THANKS` shows a failing `game-line-break` check and is fixed by hand -
  the one defect this rule can only report.
- Change history still attributes the text to `mt:openrouter`
  (`weblate/trans/models/suggestion.py:208-211`).

---

### Task 6: document the rule where it is looked up

**Files:**

- Modify: `docs/changes.rst` (unreleased section, Improvements)
- Modify: `docs/specs/producer-guide.md:402-407, 424-426`
- Modify: `AGENTS.md` (customization section)

Changelog, one entry:

```rst
* The engine line separator ``$`` is now enforced where it is used tightly in
  the source: whitespace around a separator is removed automatically, and a
  separator lost or added by a translation is reported by the
  ``game-line-break`` check.
```

Producer guide, a row in the failing-checks table and a mention in the
instance-wide rules list, in the surrounding Russian prose:

```markdown
| `game-line-break` | В строке с разделителем `$` в исходнике он потерян, добавлен или рядом с ним появился пробел |
```

`AGENTS.md`: name both new classes beside `GameMarkupCheck` and record that
registration now also covers `WEBLATE_ADD_AUTOFIX`. The existing sentence
claiming `GameMarkupCheck` is "importable but not registered" is stale for prod
and becomes stale for dev after Task 3.

**Acceptance:** `uv run prek run --all-files` passes on the changed files.

---

## Task checklist

- [x] **T1 (P1)** - `weblate_customization/src/weblate_customization/checks.py` - shared separator vocabulary and `GameLineBreakCheck`, red-green in one task.
  - Verify: `./rundev.sh test weblate_customization/tests/test_checks.py -n0 -q`
- [x] **T2 (P1)** - `weblate_customization/src/weblate_customization/autofixes.py` - `LineSeparatorSpacing`, including the real `DATA_LOADING` regression.
  - Verify: `./rundev.sh test weblate_customization/tests/ -n0 -q`
- [x] **T3 (P1)** - `dev-docker/docker-compose.yml`, `deploy/environment.example` - register both, close the dev/prod gap.
  - Verify: container settings dump lists both.
- [x] **T4 (P1)** - live dev instance - suggestion creation *and legacy acceptance* are repaired; corpus check count is sane.
  - Verify: `DATA_LOADING` is repaired in both flows and restored after the probe; `DEMO_THANKS` is reported.
- [~] **T5 (P1)** - prod `.env`, `./deploy/vps.sh deploy`, scoped `updatechecks`, preflight, and scoped accept.
  - Production runtime rollout is confirmed read-only on 2026-08-15: `hcgameloc-weblate-1` is healthy at revision `02f8d2f`, and both custom checks and autofixes are loaded.
  - Production data confirms `82` suggestion-creation records by `mt:openrouter` and `translated_units = 82`; however, `Suggestion.accept()` records are `0`, `79` suggestions were removed by cleanup, and `3` remain pending (`28`, `38`, `53`). The exact scoped acceptance operation from this task was not run.
  - The French `DEMO_THANKS`, `DATA_LOADING`, and `BUG_TEXT` targets are clean; `DEMO_THANKS` was corrected by a user edit, not by the planned suggestion-acceptance batch. Persisted `game-line-break` failures remain for the expected real defects.
- [~] **T6 (P2)** - `docs/changes.rst`, `docs/specs/producer-guide.md`, `AGENTS.md`.
  - Documentation changes are present and the relevant formatting, Ruff, YAML, Sphinx, and codespell hooks pass.
  - The full `prek` run is not green because of unrelated existing REUSE/typos errors in `docs/misc/col4-glossary-append-2026-08-14.csv` and `docs/misc/col4-id-defects.tsv`, plus the pre-existing `docs/changes.rst:12` bullet-stop failure.

## Parallelization

Sequential. T2 imports from the module T1 creates; T3 registers what T1 and T2
define; T4 exercises T3's wiring; T5 depends on T4's verdict. T6 may be written
at any point after T2 but is cheapest to land with the verification pass.

## Open decisions

- **`enforced_checks`.** Deferred by design: it would block accepting a
  suggestion whose only fault is a lost separator. Revisit after one full
  narrative run, when the false-positive rate on `LocalizeData` is known.
- **Currency projects.** The source gate assumes `$` is a separator only when
  tight. If a future project uses `$5` as currency inside a kit that also uses
  `$` as a separator, the gate is wrong for that project and the component needs
  `ignore-game-line-break` plus a different separator in the kit.
