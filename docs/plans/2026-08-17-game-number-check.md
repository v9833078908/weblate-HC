# Game number check implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `game-number` quality check that reports a number stated by the
source string and missing from the translation, so a wrong damage value, radius
or duration can never reach a player unnoticed.

**Architecture:** One `TargetCheck` subclass in the existing
`weblate_customization` package, next to `GameMarkupCheck` and
`GameLineBreakCheck`. The rule is deliberately **asymmetric**: every number in
the source must appear in the target, while a number the target adds is
accepted. Symmetric comparison was measured and rejected - it fires on 439
correct strings, because Japanese writes `3回に1回` where the source says
"каждый третий", and a date is rendered per locale. Containment fires on 50
target strings out of 55 875, which collapse into 9 source keys.

**Tech Stack:** Python 3.12+, Django, the `regex` module, Weblate's
`weblate.checks.base.TargetCheck`, `weblate.checks.tests.test_checks.CheckTestCase`.

---

## Read before starting

- `weblate_customization/src/weblate_customization/checks.py` - the file being
  changed. Three checks already live there; copy their shape, do not invent a
  new one.
- `weblate_customization/tests/test_checks.py` - the test file. Every check has
  a `CheckTestCase` subclass plus a handful of named boundary tests.
- `docs/plans/2026-08-10-game-line-break-rule.md` - the previous check added to
  this same file. Same task shape, same rollout path.
- `AGENTS.md`, sections "Deploying custom checks and machinery" and
  "Development environment".

## Ground rules for this repository

1. **Work in the main checkout, on a branch. Do not create a git worktree.**
   This change touches `dev-docker/docker-compose.yml`, and `dev-docker/`
   publishes fixed host ports (5434, 1080) with a compose project name derived
   from the directory basename. A worktree copy collides with the main
   checkout's containers instead of isolating from them.
2. **Absolute or repo-relative paths only, and never mix the two.** Every path
   in this plan is relative to the repository root
   `/Users/eli/Documents/PythonProjects/gamedev tools/weblate`.
3. **Do not run `./rundev.sh` (build, start, stop, restart).** Restarting the
   shared dev stack counts as deployment under `AGENTS.md` and needs explicit
   approval from the user. Task 6 is the gate; everything before it runs on the
   host.
4. **Ignore-flag spelling.** A check's ignore flag is derived by
   `weblate/checks/base.py:88` as `"ignore-" + check_id`, and `check_id` already
   uses dashes. The flag is `ignore-game-number`. An underscore variant saves
   cleanly, renders in `all_flags`, and never matches - the check keeps firing
   silently.

## Test command

```sh
PYTHONPATH="$PWD/weblate_customization/src" \
DJANGO_SETTINGS_MODULE=weblate.settings_test \
uv run pytest weblate_customization/tests/test_checks.py -q
```

Verified on the host: 44 passed, 16 skipped, 0.51 s. No database, no
`collectstatic`, no container. `PYTHONPATH` is required because
`weblate_customization` is a `uv_build` package that is copied into the
container rather than installed into `.venv`.

Export it once for the session:

```sh
export PYTHONPATH="$PWD/weblate_customization/src"
export DJANGO_SETTINGS_MODULE=weblate.settings_test
```

---

## Task 1: The check

**Files:**

- Modify: `weblate_customization/src/weblate_customization/checks.py:12-25` (imports and shared regex)
- Modify: `weblate_customization/src/weblate_customization/checks.py:71-78` (`_tokens` reads the hoisted constant)
- Modify: `weblate_customization/src/weblate_customization/checks.py:146` (append the new class at end of file)
- Test: `weblate_customization/tests/test_checks.py:9-13` (import) and end of file (new test class)

### Step 1: Write the failing test class

Append to `weblate_customization/tests/test_checks.py`:

```python
class GameNumberCheckTest(CheckTestCase):
    check = GameNumberCheck()

    def setUp(self) -> None:
        super().setUp()
        # The decimal separator is a locale choice, not a different number.
        self.test_good_matching = (
            "Radius 0.5 m for 1.5 seconds",
            "Rayon 0,5 m pendant 1,5 seconde",
            "",
        )
        # A rebalanced value that never reached the source, or a stale source:
        # either way the two strings promise the player different things.
        self.test_failure_1 = (
            "Shield with 200 durability",
            "Bouclier avec 1200 de durabilite",
            "",
        )
        # A dropped clause takes its number with it.
        self.test_failure_2 = ("Deals 200 damage over 3 s", "Infliger des degats", "")
        # Two values, both wrong, and neither is absent - only the set differs.
        self.test_failure_3 = ("Deals 20% and 30%", "Infligge 30% e 10%", "")

    def test_a_number_the_target_adds_is_accepted(self) -> None:
        # Japanese counts what Russian words: "каждый третий" -> "3回に1回".
        self.assertFalse(
            self.check.check_single(
                "Every third repair is faster", "3回に1回の修理は速くなります", None
            )
        )

    def test_a_full_date_is_not_a_quantity(self) -> None:
        self.assertFalse(
            self.check.check_single(
                "Starts on 14.04.2025", "Beginnt am 14. April 2025", None
            )
        )

    def test_a_placeholder_is_not_a_number(self) -> None:
        self.assertFalse(self.check.check_single("Value {0}", "Wert {0}", None))

    def test_a_lost_placeholder_is_not_this_checks_business(self) -> None:
        # game-markup owns placeholder integrity; two checks reporting one
        # defect would double every failing-check count.
        self.assertFalse(self.check.check_single("Value {0}", "Wert", None))

    def test_a_number_inside_markup_is_not_counted(self) -> None:
        self.assertFalse(
            self.check.check_single(
                "<size=14>Text</size>", "<size=14>Texte</size>", None
            )
        )

    def test_a_source_without_numbers_passes(self) -> None:
        self.assertFalse(self.check.check_single("Plain text", "Texte 42", None))

    def test_an_empty_target_passes(self) -> None:
        self.assertFalse(self.check.check_single("Damage 200", "", None))
```

Add `GameNumberCheck` to the existing import at
`weblate_customization/tests/test_checks.py:9-13`, keeping alphabetical order:

```python
from weblate_customization.checks import (
    CyrillicLeakCheck,
    GameLineBreakCheck,
    GameMarkupCheck,
    GameNumberCheck,
)
```

### Step 2: Run the test to verify it fails

```sh
uv run pytest weblate_customization/tests/test_checks.py -q
```

Expected: collection error, `ImportError: cannot import name 'GameNumberCheck'`.

### Step 3: Hoist the shared markup regex

`_tokens` at `weblate_customization/src/weblate_customization/checks.py:71-78`
builds its combined pattern inline. The new helper needs the same pattern to
strip markup, so lift it to a module constant instead of writing a second copy.

After `PLACEHOLDER_PATTERN` (line 25), insert:

```python
# Tags and placeholders together: `_tokens` compares them, `_numbers` removes
# them so that `<size=14>` and `{0}` are never read as quantities.
MARKUP = regex.compile(
    rf"(?:{TAG_PATTERN.pattern})|(?:{PLACEHOLDER_PATTERN.pattern})"
)
```

**Do not add `regex.IGNORECASE` to this constant.** `TAG_PATTERN` carries the
flag, but `TAG_PATTERN.pattern` is only the pattern string and drops it, so the
inline composition inside `_tokens` has always been case-sensitive. Verified:
`TAG_PATTERN.findall("<COLOR=#fff>x</COLOR>")` returns two matches while the
composed pattern returns none. Adding the flag here would silently widen
`GameMarkupCheck` to compare uppercase tags it has never compared. The
component has zero non-lowercase tags today (measured across all 16 files), so
the quirk is inert - preserve it rather than fix it in a plan about numbers.

Then rewrite `_tokens` to read it:

```python
def _tokens(text: str) -> list[str]:
    """Extract ordered markup tokens (tags with attrs + placeholders)."""
    return [match.group() for match in MARKUP.finditer(text)]
```

### Step 4: Run the whole file to prove the refactor changed nothing

```sh
uv run pytest weblate_customization/tests/test_checks.py -q
```

Expected: the same collection error as Step 2, and no new failure once it is
fixed. If `GameMarkupCheckTest` starts failing here, the hoisted pattern is not
equivalent - fix that before going on.

### Step 5: Add the number vocabulary

After the `MARKUP` constant, insert:

```python
# A number in the source is a fact the player acts on: damage, radius, seconds.
# Losing or altering it is a defect in every language. A number the target adds
# is not: Japanese counts "3回に1回" where the source says "каждый третий", and a
# full date is written per locale. So the rule is containment, not equality.
NUMBER = regex.compile(r"\d+(?:[.,]\d+)?")
# A full date is not a quantity, and its rendering belongs to the locale.
FULL_DATE = regex.compile(r"\b\d{1,2}[.,]\d{1,2}[.,]\d{4}\b")
```

Add `Counter` to the imports at line 12-17:

```python
from __future__ import annotations

from collections import Counter

import regex
from django.utils.translation import gettext_lazy

from weblate.checks.base import TargetCheck
```

### Step 6: Add the helper and the check

Append to the end of the file:

```python
def _numbers(text: str) -> Counter[str]:
    """Quantities outside markup, with the decimal separator normalized."""
    body = FULL_DATE.sub(" ", MARKUP.sub(" ", text))
    return Counter(match.group().replace(",", ".") for match in NUMBER.finditer(body))


class GameNumberCheck(TargetCheck):
    """Every quantity the source states must survive into the translation."""

    check_id = "game-number"
    name = gettext_lazy("Game number")
    description = gettext_lazy(
        "A number from the source string is missing from the translation."
    )
    # Always on: a wrong damage or radius value is a defect, not a preference.
    default_disabled = False

    def check_single(self, source: str, target: str, unit) -> bool:
        source_numbers = _numbers(source)
        if not source_numbers or not target:
            return False
        return bool(source_numbers - _numbers(target))
```

`Counter.__sub__` keeps only positive counts, so the asymmetry is free: numbers
the target adds never appear in the difference.

### Step 7: Run the tests to verify they pass

```sh
uv run pytest weblate_customization/tests/test_checks.py -q
```

Expected: **zero failures**. The new class contributes the `CheckTestCase` rows
plus the 8 named tests; rows left as `None` (`test_good_ignore`,
`test_good_flag`, `test_highlight`) skip, so both the passed and the skipped
count rise from the 44 passed / 16 skipped baseline. Do not treat a changed
skip count as a problem - treat any failure, and any drop in the passed count,
as one.

### Step 8: Prove the tests would catch a broken check

A test that passes against the bug is not a test. Break the check three ways
and confirm a failure each time; revert after each.

| Mutation | Test that must fail |
|---|---|
| `return source_numbers != _numbers(target)` (symmetric) | `test_a_number_the_target_adds_is_accepted` |
| Drop `FULL_DATE.sub(" ", ...)` | `test_a_full_date_is_not_a_quantity` |
| Drop `.replace(",", ".")` | `test_single_good_matching` |

```sh
uv run pytest weblate_customization/tests/test_checks.py -q
```

Expected after each mutation: exactly the named test fails. After the third
revert, back to 60 passed.

### Step 9: Lint

```sh
uv run prek run --all-files
```

Expected: pass. Ruff lives in the pre-commit hook environment, so
`uv run ruff` is not a substitute. `mypy` does not cover
`weblate_customization/` (`AGENTS.md` runs it over `weblate scripts/*.py
./*.py`), so there is nothing to run there.

### Step 10: Commit

```sh
git add weblate_customization/src/weblate_customization/checks.py \
        weblate_customization/tests/test_checks.py
git commit -m "feat(checks): report a source number missing from the translation"
```

---

## Task 2: Verify against the real component

Unit tests prove the rule. This task proves the **rate**: that turning the
check on does not bury the producer in false positives. Read-only, no writes.

**Files:**

- Create: `docs/misc/game-number-probe.py`

### Step 1: Write the probe

The four existing `docs/misc/*-probe.py` scripts import the real
implementation instead of reimplementing it. Do the same: a probe that
re-derives the rule proves nothing about the check.

```python
# Report game-number firings for a live component, per language and per key.
# Read-only. Usage:
#   PYTHONPATH=weblate_customization/src python3 docs/misc/game-number-probe.py
from __future__ import annotations

import collections
import json
import os
import urllib.request

from weblate_customization.checks import _numbers

DOMAIN = os.environ["PROBE_DOMAIN"]
TOKEN = os.environ["PROBE_TOKEN"]
COMPONENT = os.environ.get("PROBE_COMPONENT", "pirate-ships/localization-json")
SOURCE = os.environ.get("PROBE_SOURCE", "ru")
LANGUAGES = os.environ["PROBE_LANGUAGES"].split(",")


def get_file(language: str) -> dict[str, str]:
    url = f"https://{DOMAIN}/api/translations/{COMPONENT}/{language}/file/"
    request = urllib.request.Request(url, headers={"Authorization": f"Token {TOKEN}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


files = {language: get_file(language) for language in LANGUAGES}
source_file = files[SOURCE]

per_language: collections.Counter[str] = collections.Counter()
per_key: collections.Counter[str] = collections.Counter()
pairs = 0
for language, strings in files.items():
    if language == SOURCE:
        continue
    for key, target in strings.items():
        if not target:
            continue
        pairs += 1
        if _numbers(source_file.get(key, "")) - _numbers(target):
            per_language[language] += 1
            per_key[key] += 1

print(f"firings: {sum(per_language.values())} / {pairs} pairs")
print(f"per language: {dict(per_language)}")
print("per key:")
for key, count in per_key.most_common():
    print(f"  {count:2} languages  {key}")
```

`_numbers` is module-private on purpose: the probe is diagnostics, not product
code, and importing it keeps the probe honest about which rule is shipping.

### Step 2: Run it against production, read-only

The token is already in `deploy/.env.local` as `PROD_WEBLATE_API_TOKEN`; do not
mint a second one.

```sh
set -a; . deploy/.env.local; set +a
PROBE_DOMAIN="$PROD_DOMAIN" \
PROBE_TOKEN="$PROD_WEBLATE_API_TOKEN" \
PROBE_LANGUAGES=ru,en,es,fr,de,it,pl,pt_BR,nl,tr,vi,id,th,ja,ko,zh_Hans \
PYTHONPATH="$PWD/weblate_customization/src" \
python3 docs/misc/game-number-probe.py
```

Expected output, measured 2026-08-17 after the 195 manual fixes:

```text
firings: 50 / 55875 pairs
per language: {'en': 2, 'es': 2, 'fr': 2, 'de': 2, 'it': 2, 'pl': 2,
               'pt_BR': 2, 'nl': 7, 'tr': 5, 'vi': 5, 'id': 5, 'th': 2,
               'ja': 4, 'ko': 4, 'zh_Hans': 4}
per key:
  15 languages  ability_name_summon_damage_venom
  15 languages  ability_description_crew_captain_campaign_0_2
   6 languages  ability_description_boarding_hooks3
   6 languages  ability_description_boarding_hooks4
   2 languages  booster_description_battle_frost
   2 languages  booster_description_battle_fog
   2 languages  booster_description_battle_rain
   1 languages  ability_description_shield_hard_reflect_bullets_aoe
   1 languages  ability_description_ship_collision_explosion2
```

**If the numbers differ, stop and diff before continuing.** More firings means
the check is broader than designed; fewer means a mutation slipped in. The nine
keys are the deliverable of this task: they are what the producer gets handed.

### Step 3: Commit

```sh
git add docs/misc/game-number-probe.py
git commit -m "test(checks): add a read-only game-number probe for live components"
```

---

## Task 3: Registration

Registration is two text edits. It takes effect only when the container is
recreated, which is Task 6.

**Files:**

- Modify: `dev-docker/docker-compose.yml:63`
- Modify: `deploy/environment.example:94`

### Step 1: Append the class to both lists

`dev-docker/docker-compose.yml:63`, one line, comma-separated, no spaces:

```yaml
      WEBLATE_ADD_CHECK: weblate_customization.checks.GameMarkupCheck,weblate_customization.checks.GameLineBreakCheck,weblate_customization.checks.CyrillicLeakCheck,weblate_customization.checks.GameNumberCheck
```

`deploy/environment.example:94`:

```ini
WEBLATE_ADD_CHECK=weblate_customization.checks.GameMarkupCheck,weblate_customization.checks.GameLineBreakCheck,weblate_customization.checks.CyrillicLeakCheck,weblate_customization.checks.GameNumberCheck
```

`weblate/utils/environment.py:182` folds the variable into `CHECK_LIST` through
`modify_env_list`, and `weblate/checks/flags.py:166` derives
`IGNORE_CHECK_FLAGS` from the registry, so `ignore-game-number` becomes a valid
flag the moment the check is registered. No flag plumbing to write.

### Step 2: Verify the two lines are identical apart from the separator

```sh
grep -h WEBLATE_ADD_CHECK dev-docker/docker-compose.yml deploy/environment.example
```

Expected: both lines end with `...CyrillicLeakCheck,weblate_customization.checks.GameNumberCheck`.

### Step 3: Commit

```sh
git add dev-docker/docker-compose.yml deploy/environment.example
git commit -m "chore(checks): register GameNumberCheck in both environments"
```

---

## Task 4: Documentation

**Files:**

- Modify: `docs/changes.rst` (after line 26, inside the `2026.8.1` Improvements block)
- Modify: `AGENTS.md:177-186` and `AGENTS.md:342-343`
- Modify: `docs/specs/producer-guide.md:408` (add a table row)
- Modify: `docs/specs/game-repo-integration-contract.md:97-125` (part 1.3)

### Step 1: Changelog

A new check is user-visible, so it needs an entry in the top unreleased
section. Match the surrounding one-sentence style; do not restate the design.

Insert after `docs/changes.rst:26`:

```rst
* A number stated by the source string is now reported by the ``game-number`` check when it is missing from the translation, so a wrong damage value, radius or duration is caught. A number the translation adds on its own is accepted, because a counter word in the source is often a digit in the target.
```

### Step 2: AGENTS.md, the checks paragraph

`AGENTS.md:177-186` enumerates the shipped checks. Extend the sentence that
lists them, keeping the existing pattern `` `Name` (`check_id: id`), which
asserts that ... ``:

```markdown
  `GameNumberCheck` (`check_id: game-number`), which asserts that every number
  the source states is present in the target after markup, placeholders and
  full dates are removed and the decimal separator is normalized; a number the
  target adds is accepted, and the flag is `ignore-game-number`.
```

### Step 3: AGENTS.md, the registration paragraph

`AGENTS.md:342-343` quotes the `WEBLATE_ADD_CHECK` value. Update the quoted
value to include `weblate_customization.checks.GameNumberCheck` and the
sentence that names the registered classes.

### Step 4: Producer guide

Add one row after `docs/specs/producer-guide.md:408`, matching the terse
symptom style of the table:

```markdown
| `game-number` | В переводе нет числа, которое есть в исходнике: другой урон, радиус или длительность |
```

### Step 5: The contract requirement

This is the half of the problem code cannot fix. Measured on the live
component: 28 Russian source strings write a decimal with a dot (`0.5м`,
`1.5 секунды`) against the Russian comma convention, while all 15 translations
are clean; and 95 source keys state a quantity as a word ("в течение трёх
секунд"), of which 70 are stat descriptions rather than prose. Both are edits
in the developer's own table, and both are invisible to any check.

Add three rows to the table at `docs/specs/game-repo-integration-contract.md:101-109`:

```markdown
| Числовые характеристики | Цифрой или плейсхолдером из данных, не словом: «в течение 3 секунд», не «в течение трёх секунд» |
| Десятичный разделитель | Запятая, как требует русская типографика: `0,5м`, не `0.5м` |
| Балансные числа в новых ключах | Плейсхолдером: `"Ускоряет ремонт в {0} раза"`, значение подставляет движок |
```

Then one short paragraph after the existing `json` example (line 116),
explaining why - the contract is read by the developer, so the reason has to be
about their cost, not ours:

```markdown
Почему числа: значение, записанное словом, невозможно сверить машинно, и
16 копий одного числа расходятся после каждого ребаланса - на живом файле это
уже случилось дважды. Число в плейсхолдере существует в одном месте, правка
баланса вообще не касается локализации, а сохранность `{0}` во всех языках
проверяет `game-markup`. Требование распространяется на новые ключи; переписывать
существующие не нужно.
```

### Step 6: Verify the docs build and the RST is valid

```sh
uv run prek run --all-files
```

Expected: pass. The hooks cover RST and Markdown formatting; a malformed
`changes.rst` entry fails here.

### Step 7: Commit

```sh
git add docs/changes.rst AGENTS.md docs/specs/producer-guide.md \
        docs/specs/game-repo-integration-contract.md
git commit -m "docs(checks): document game-number and the numeric source contract"
```

---

## Task 5: Push

```sh
git push
```

`AGENTS.md` requires finishing verified work by committing and pushing. Tasks 1
to 4 are host-only: nothing is deployed and no running instance changed.

---

## Task 6: Rollout - REQUIRES EXPLICIT USER APPROVAL

Everything below changes a running instance. Under `AGENTS.md` that is
deployment, and it needs the user to say so. Do not start this task on your own
initiative; report Tasks 1-5 and ask.

### Step 1: Copy the package into the dev container's path

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
```

The container imports from `/app/data/python`; the package is copied, not
installed. Editing the source alone is not enough.

### Step 2: Recreate the dev container

```sh
WEBLATE_PORT=3001 ./rundev.sh
```

A plain restart is not enough: the `environment` block is baked in at container
creation, so a changed `WEBLATE_ADD_CHECK` needs a full rebuild and start. Note
the port: `rundev.sh` defaults to 8080, but the instance everything else
expects is 3001.

### Step 3: Confirm the check is registered

```sh
./rundev.sh check 2>&1 | tail -5
docker compose -f dev-docker/docker-compose.yml exec -T weblate \
  weblate shell -c "from weblate.checks.models import CHECKS; print('game-number' in CHECKS)"
```

Expected: `True`.

### Step 4: Recompute checks for stored strings

New checks do not appear on existing units until they are re-run. There is no
`updatechecks` management command in this checkout; the entry point is
`Component.schedule_update_checks` (`weblate/trans/models/component.py:6723`),
which queues the `update_checks` Celery task.

```sh
docker compose -f dev-docker/docker-compose.yml exec -T weblate weblate shell -c "
from weblate.trans.models import Component
component = Component.objects.get(project__slug='pirate-ships', slug='localization')
component.schedule_update_checks(update_state=True)
"
```

Then wait for the Celery worker and read the count back:

```sh
docker compose -f dev-docker/docker-compose.yml exec -T weblate weblate shell -c "
from weblate.checks.models import Check
print(Check.objects.filter(name='game-number').count())
"
```

### Step 5: Verify in the browser, by clicking

Open the component's failing-checks page and click through to a
`game-number` row. Confirm the check name and description render, and that the
:guilabel:`Ignore` control writes `ignore-game-number` into the unit flags.
Drive the real control; a direct POST bypasses the DOM relationship being
tested.

### Step 6: Production

Only after the dev instance is confirmed. Production registration goes through
`deploy/environment.example`'s real counterpart on the host, then
`deploy/vps.sh`. Re-run the Task 2 probe against production afterwards and
expect the same nine keys.

---

## Out of scope, and why

| Not doing | Reason |
|---|---|
| A separate `game-decimal` check for separator conventions | Measured: all 15 translations are already clean; the 28 violations are in the Russian source, which regeneration from the developer's table overwrites. A check would report what Weblate cannot hold. One contract row instead (Task 4, Step 5) |
| Symmetric multiset comparison | Fires on 439 correct strings: Japanese counters, localized dates, "полцены" -> `50% off` |
| Grouped thousands (`1 000` / `1,000`) normalization | Zero occurrences measured in the component. If one appears, the token splits and the check false-positives; the fix is `ignore-game-number` on that unit, or extending `NUMBER` then |
| CJK numerals (`三倍` where the source says `3`) | 45 strings use them, all prose where the source also uses a word, so the check stays silent. If it fires, the flag handles it |
| Rewriting the 95 worded-quantity source keys | The developer's table, not ours. Task 4 Step 5 states the requirement; the check works without it |
| Moving balance numbers into placeholders | The developer's engine. Contract requirement for new keys only |

## Risks

| Risk | Mitigation | Verified by |
|---|---|---|
| The check double-reports what `game-markup` already reports | `_numbers` strips markup and placeholders before counting | `test_a_lost_placeholder_is_not_this_checks_business` |
| A localized date reads as a lost number | `FULL_DATE` removed from both sides | `test_a_full_date_is_not_a_quantity`, and the probe's 50 firings contain no date key |
| Correct target-side explicitation is punished | Containment, not equality | `test_a_number_the_target_adds_is_accepted`, probe rate 0.089% |
| An untranslated-but-fuzzy unit lights up | `not target` returns early, on top of the framework's `ignore_state` | `test_an_empty_target_passes` |
| The refactor of `_tokens` changes `game-markup` behaviour | The hoisted constant is compiled without flags, exactly like the inline composition it replaces; `GameMarkupCheckTest` runs unchanged before the new class exists | Task 1, Step 4 |
| The producer cannot silence a genuine exception | `ignore-game-number`, dash form, valid as soon as the check is registered | Task 6, Step 5 |
