
# Weblate UI Integration Surfaces for LLM-Judge Verdict

## Scope

Read-only scout of `weblate_customization/`, `weblate/checks/`, `weblate/trans/`,
`weblate/templates/`, `weblate/api/`, `weblate/utils/`, and `weblate/static/`
for the cheapest integration points to surface an external LLM-judge verdict
(pass/flag/reject + reason + back-translation) inside the existing Weblate UI.

No edits. All references are file:line.

---

## 1. Checks UI: failing checks on unit page and in unit lists

### How a failing check renders

**Sidebar "Things to check" card** on the unit detail page:

- Template: `weblate/templates/translate.html:596-666`
- Iterates `unit.all_checks` — each `<div class="list-group-item check check-item">`
- Shows `check.get_name` (from `check.check_obj.name`), `check.get_description`
  (returns `check_obj.get_description(check)` — arbitrary text), a
  "Dismiss" button (POST to `js-ignore-check`), and a "Reset" button.
- Display-only checks (always_display=True, not failing) render at
  `translate.html:668-676` with green checkmark.

**Unit list badge**: `weblate/trans/templatetags/translations.py:934-936`

- `{% unit_state_class unit %}` returns `"unit-state-bad"` CSS class when
  `unit.has_failing_check` is True. The CSS class renders a red dot in
  the `unit-state-cell` column (`weblate/templates/snippets/embed-units.html:93`).

**Tooltip**: `unit_state_title` (`translations.py:946-964`) concatenates
"Failing checks: check_name, check_name" plus dismissed checks, comments,
suggestions into a semicolon-separated tooltip.

### Can a check carry arbitrary text?

Yes. `Check.get_description()` (`weblate/checks/models.py:99-101`) delegates to
`check_obj.get_description(self)` which returns the `description` attribute of
the BaseCheck subclass (`weblate/checks/base.py:222-223`). Any subclass can
override `get_description` to return dynamic text — but the description is
**static per check type**, not per-unit-instance. The `Check` model has only
`unit`, `name`, and `dismissed` fields — no per-check-instance metadata.
`models.py:75-77`.

### Dismiss action

- UI: `translate.html:643-654` — POST to `js-ignore-check`, with "For all languages"
  checkbox that sets `ignore-<check_id>` flag on the source unit.
- Model: `Check.set_dismiss()` (`models.py:122-131`) — sets `dismissed=True`,
  propagates to propagated_units.
- Filter: dismissed checks excluded by `check__dismissed=False` in
  `active_checks` queryset, `has:check` search filter.

### ignore-* flags

- `weblate/checks/base.py:82-83`: `self.ignore_string = f"ignore-{id_dash}"`
- `weblate/checks/flags.py:179`: `IGNORE_CHECK_FLAGS = {check.ignore_string for check in CHECKS.values()}`
- Checks are skipped when flag is present via `is_ignored()` -> `should_skip()`.
- Also `ignore-all-checks` flat flag disables all checks (`flags.py:107`).

### Check storage (`weblate/checks/models.py`)

- `Check(model)`: `unit` FK, `name` CharField (choice from CHECKS loader),
  `dismissed` BooleanField.
- Unique: `(unit, name)` — one row per check type per unit.
- `get_display_checks()` (`models.py:136-143`): yields Check objects for all
  registered `CHECKS.target` that `should_display(unit)`. Creates synthetic
  unsaved `Check(unit=unit, dismissed=False, name=check)` if no row exists but
  the check should display.

### Can an external process create Check rows directly?

**Yes.** `Check.objects.create(unit=unit_obj, name="some-check-id", dismissed=False)`.
The row will:

- Appear in `unit.all_checks` (prefetched via `prefetch_all_checks()`)
- Render in the sidebar "Things to check" card
- Contribute to `has_failing_check` → red unit-state-bad badge
- Be filterable via `q=check:some-check-id` and `q=has:check`
- Be counted in `stats.allchecks` / `stats.allchecks_percent`
- Appear in the checks overview at `/checks/` (`weblate/checks/views.py`)

The caveat: the check name must be registered in `CHECKS` (via `CHECK_LIST`
setting + `WEBLATE_ADD_CHECK`), or `check_obj` will be `None` and
`get_description()` / `get_name()` will fall back to the raw name string
(`models.py:100-104`). An external process can create rows with **any**
name — they just won't have a doc URL or localized description unless
registered.

### Search/filter

- `has:check` → `Q(check__dismissed=False)` (`weblate/utils/search.py:767-768`)
- `check:<name>` → Exists subquery on Check with name matching
  (`weblate/utils/search.py:884-901`)
- `dismissed_check:<name>` → same with `dismissed=True`
- Pre-defined filter names: `allchecks` ("has:check"), `translated_checks`
  ("has:check AND state:>=translated"), `dismissed_checks`
  ("has:dismissed-check") — `weblate/trans/filter.py:45-55`
- Each check gets `check:<id>` filter entry automatically
  (`filter.py:75-83`).

---

## 2. Labels

### Model: `weblate/trans/models/label.py`

- `Label`: `project` FK, `name` (max 190), `color` (ColorChoices),
  `description` (max 250, blank=True).
- Unique: `(project, name)`.
- `__str__` returns HTML span with `badge label-<color>`.
- `filter_name` property: `f"label:{Flags.format_value(self.name)}"`.

### Labels in UI

- **Unit detail sidebar**: `weblate/templates/translate.html:899-907` —
  renders `unit.all_labels` as clickable `<span class="badge label-{color}">`
  linking to `?q=label:<name>`. Editable via modal ("context-edit-form").
- **Unit list**: labels shown via `has:label` filter in stats
  (`weblate/templates/snippets/list-objects.html:202` — though that's a
  count, not a per-unit display).

### API

- Labels are set on units via `PATCH /api/units/{id}/` with `"labels": [id, ...]`
  — `weblate/api/views.py:3851-3852`: `unit.save_labels(data["labels"], user)`.
- `LabelSerializer` exists at `weblate/api/serializers.py:3350-3353` with
  fields `(id, name, description, color)`.
- Labels are created through the Django admin or project management UI;
  there is no dedicated label CRUD API endpoint — labels are read via
  the unit serializer's `labels` field (serializer fields listed at
  `serializers.py:1440-1460` area).

### Filterability

- `label:<name>` filter: `weblate/utils/search.py:876-882` —
  Exists subquery: `Label.objects.filter(name__icontains=text, unit=OuterRef("source_unit_id"))`
- `has:label` → `Q(source_unit__labels__isnull=False)` (`search.py:785`)
- `NOT has:label` → unlabeled strings (`filter.py:72`)
- Labels appear in stats: `label:<name>` key in `TranslationStats` / `AggregatingStats`
  (`weblate/utils/stats.py:940-960`).

### Verdict: Labels are project-scoped, not per-unit-verdict-shaped

Labels are designed for classification ("Source needs review", "Priority").
They have a flat name+color+description structure, no per-unit metadata,
no verdict grade, no nested data. Writing a judge verdict as a label would
require one label per verdict outcome (e.g., "judge:pass", "judge:reject")
per project, which is coarse and loses reason/back-translation context.

---

## 3. Comments

### Model: `weblate/trans/models/comment.py`

- `Comment`: `unit` FK, `comment` TextField, `user` FK (nullable),
  `timestamp`, `resolved` bool, `userdetails` JSON.
- `comment` field is plain text but rendered through `|markdown` filter
  in templates (`weblate/templates/list-comments.html:46`).

### API endpoint

- `POST/GET /api/units/{id}/comments/` — `weblate/api/views.py:3909-3931`
- `CommentSerializer` (`weblate/api/serializers.py:1312`): fields
  `scope` ("report"/"global"/"translation"), `comment` (max 1000),
  `timestamp` (admin-only), `user_email` (admin-only).
- The serializer docstring says: "You can use Markdown and mention users by @username."

### UI rendering

- `weblate/templates/list-comments.html`: renders each comment in
  `comment-content` div with `lang`/`dir` attributes and
  `{{ comment.comment|markdown }}`.
- Supports Markdown, user mentions, resolved/delete actions.
- Comments tab on unit page: `translate.html:560-587`.

### Is this suitable for judge back-translation?

Yes, but as **human-facing audit trail** only. A comment can carry:

```text
**Judge verdict: REJECT**
**Reason:** The back-translation "He walked" does not match source "She ran".
**Back-translation:** Il a marché.
```

Comments are filterable via `has:comment` (`search.py:763-764`) and appear in
the unit page sidebar "Things to check" when unresolved
(`translate.html:679-694` — `comments_to_check`). They contribute to
`stats.comments` count.

A `"report"` scope comment auto-labels the unit as "Source needs review"
and flips state to needs-checking/needs-rewriting
(`comment.py:92-98`). This is a useful built-in for "reject" verdicts.

---

## 4. Review States in UI

### States: `weblate/utils/state.py`

- 0 = Empty, 10 = Needs editing, 11 = Needs rewriting, 12 = Needs checking,
  20 = Translated (waiting for review), 30 = Approved, 100 = Read-only.
- `get_state_label(20, enable_review=True)` returns "Waiting for review".

### Display

- **Unit detail**: card header class:
  - `text-bg-success` when `unit.approved` (state 30)
  - `text-bg-warning` when `unit.is_source`
  - Default otherwise — `translate.html:79`
- **Unit list**: `{% unit_state_class unit %}` (`translations.py:934-942`):
  - `unit-state-bad` for failing checks
  - `unit-state-todo` for untranslated
  - `unit-state-approved` for approved
  - `unit-state-translated` for translated (state 20)
- CSS classes in `weblate/static/styles/` render a colored dot.

### Review queue

- Filter: `state:translated` returns strings at state 20 ("waiting for review"
  when `enable_review=True`).
- Pre-defined filter `unapproved` = `state:translated` (`filter.py:64-68`).
- The "unapproved" filter is the review queue.

### enable_review

- Project property: `weblate/trans/models/project.py:1354`:
  `enable_review = self.translation_review or self.source_review`
- Controls whether state 20 means "Waiting for review" or just "Translated"
  (`state.py:62`).

### What a reviewer sees

- Permission: `unit.review` — checked in `translate.html:39`:
  `{% perm 'unit.review' unit as user_can_review %}`
- "Accept and approve" button on suggestions (`suggestions.html:62-68`).
- Reviewers can approve strings (set state to 30).

### Verdict integration with review states

- **pass** → leave state unchanged (or approve if `enable_review` and state is 20)
- **flag** → could set state to 12 (needs checking)
- **reject** → could set state to 10 (needs editing) or 11 (needs rewriting)

---

## 5. Unit Detail Page Layout (Sidebar Zones)

### Template structure: `weblate/templates/translate.html`

**Left column** (col-sm-9): translation form, then tabbed content:

- Nearby strings (`.tab-pane#nearby`, line 353)
- Similar keys (`.tab-pane#keys`, line 358)
- Variants (`.tab-pane#variants`, line 309)
- **Suggestions** (`.tab-pane#suggestions`, line 376-382)
- Other occurrences (`.tab-pane#others`, line 387)
- **Comments** (`.tab-pane#comments`, line 560)
- **Automatic suggestions / Machinery** (`.tab-pane#machinery`, line 573)
  - Loaded via JS: `data-load="machinery"` — fetches `/js/translate/{unit_id}/{service}/`
  - Contains translation memory search form and results table
- **Other languages** (`.tab-pane#translations`, line 544)
- **History** (`.tab-pane#history`, line 548)

**Right sidebar** (col-sm-3, class `source-info`, line 588):

1. **"Things to check" card** (lines 596-744):
   - Suggestions count + link
   - `unit.all_checks` loop (failing checks with dismiss/reset)
   - `display_checks` loop (always-display checks, green checkmark)
   - Comments to check + link
   - Variants count + link
   - Automatically translated indicator
   - Translation quality filter indicator

2. **Glossary card** (lines 747-790): glossary terms table + add term button.

3. **Screenshots card** (lines 792-820): screenshot thumbnails + add/manage buttons.

4. **String information card** (lines 822+):
   - Explanation, context, source description
   - **Labels** (lines 899-907) — `unit.all_labels` as clickable colored badges
   - **Flags** (lines 909-929)
   - Details accordion (unit id, location, timestamps)

### Best placement for "Judge verdict" zone

**Option A — "Things to check" card** (line 596): Add a new `<div class="list-group-item check">`
between suggestions and failing checks, or after automatically-translated.
This zone already carries "something to review" semantics.

**Option B — New card in sidebar**: After screenshot card, before string info.
Rarely-visible widgets go lower; judge verdict is high-priority and should be
in the top half.

**Option C — Tab in left column**: Like machinery/suggestions. A `#judge` tab
with full back-translation + verdict explanation. Pros: spacious, full JSON
display. Cons: adds cognitive load, not visible by default.

**Option D — Inside the translation form card**: A colored banner above/below
the target input. Highest visibility but most invasive to the core editing flow.

---

## 6. Dashboards / Reports

### Component/project overview

- Template: `weblate/templates/snippets/info.html` — stats table with rows for
  failing checks, suggestions, comments, etc.
  - Failing checks: `stats.allchecks_percent`, `stats.allchecks` at line 367-372.
  - Each row is percent + absolute number.
- Unit lists: `weblate/templates/snippets/list-objects.html` — per-language
  progress bars with summary numbers:
  - `{% list_objects_number value=category.stats.allchecks ... %}` at line 202
  - Links to `q=has:check` for drill-down.

### Stats system: `weblate/utils/stats.py`

- `TranslationStats.calculate_checks()` (lines 911-935): counts per-check-name
  via `unit_set.filter(check__dismissed=False).values("check__name").annotate_stats()`.
  Stores keys like `check:<name>`, `check:<name>_words`, `check:<name>_chars`.
- `TranslationStats.calculate_labels()` (lines 937-960): same for labels.
- `AggregatingStats` (lines 967+): aggregates child stats (translation→component→project).

### API stats endpoints

- `/api/projects/{slug}/statistics/` → `ProjectStats`
- `/api/components/{project}/{slug}/statistics/` → `ComponentStats`
- `/api/translations/{project}/{component}/{lang}/statistics/` → `TranslationStats`
- All include `failing_checks`, `failing_checks_percent` fields
  (`weblate/api/serializers.py:2549-2555`).
- `/api/metrics/` exposes Prometheus metrics including
  `weblate_failing` (`weblate/api/views.py:324-326`).

### Where to surface judge aggregate numbers

- A new column/row in `info.html` table: "Judge coverage" (how many strings
  have judge verdict) and "Judge pass-rate" (what fraction pass).
- A new list-objects number in `list-objects.html` alongside existing
  `allchecks`, `suggestions`, `comments` counts (line 202 area).
- A new stats key `judge:pass` / `judge:flag` / `judge:reject` following the
  existing `check:<name>` / `label:<name>` stats pattern. If the judge writes
  Check rows, stats are automatic. If it uses labels or a custom model, stats
  need a `calculate_judge()` method mirroring `calculate_checks()`.

---

## 7. Automatic Suggestions Tab (Machinery UI)

### Architecture

- Tab: `weblate/templates/translate.html:573-595` — `<div class="tab-pane" id="machinery">`
  with `data-load="machinery"`.
- JS: `weblate/static/editor/full.js:275-304` — `Machinery` class at line 765.
  On tab activation, creates `new Machinery()`, fetches from
  `/js/translate/{unit_id}/{service}/` for each service in
  `machinery_services` list, renders into `#machinery-translations` table.
- Each machinery result renders as a table row with:
  - Translation text, suggested change, source text, origin name, similarity %,
    action buttons (copy, copy+save, delete).
- The `Machinery` class at `full.js:765` is a simple state+render component:

  ```js
  class Machinery {
    constructor() { this.state = { translations: [] }; }
    setState(newState) { ... }
    render(translations) { ... }  // builds DOM rows
  }
  ```

### Could judge back-translation reuse this?

**Structurally yes, but conceptually different.**

- Machinery is **"here are options to pick from"** (proactive suggestions).
- Judge verdict is **"here is an evaluation of what's already there"** (retrospective).

A judge result could be rendered **inside the same tab** or as a sibling tab:

- If rendered as machinery-like service rows: each row would show the
  back-translation as text, with "Copy to target" perhaps swapping the
  reviewed translation. A new row CSS class (e.g., `judge-pass`/`judge-fail`)
  would distinguish verdicts from suggestions.
- **Simpler alternative**: A dedicated `#judge` tab (pattern already exists —
  `#nearby`, `#keys`, `#history`, etc.) with its own JS fetch to
  `/js/judge/{unit_id}/` returning a verdict object. This avoids conflating
  machinery semantics.

---

## 8. Custom Check Registration Path

### Fork's registration mechanism

- `weblate_customization/src/weblate_customization/checks.py` contains three
  custom checks: `GameMarkupCheck`, `GameLineBreakCheck`, `CyrillicLeakCheck`.
- Registration via `WEBLATE_ADD_CHECK` env var in `dev-docker/docker-compose.yml:61`
  and `deploy/environment.example:82`:

  ```text
  WEBLATE_ADD_CHECK=weblate_customization.checks.GameMarkupCheck,weblate_customization.checks.GameLineBreakCheck,weblate_customization.checks.CyrillicLeakCheck
  ```

- `weblate/utils/environment.py:182` — `modify_env_list` folds
  `WEBLATE_ADD_CHECK` into the `CHECK_LIST` setting.
- `weblate/settings_docker.py` — applies it at startup.
- `weblate/checks/models.py:25-27` — `ChecksLoader` reads `CHECK_LIST`
  into `CHECKS` dict.
- All three are `TargetCheck` subclasses (`weblate/checks/base.py`) with
  `default_disabled = False`.

### Is check_single computed on demand or stored?

Both. Standard flow:

1. `check_single()` is called during translation save (edit flow) to
   **compute** whether the check fires.
2. If True, `Check.objects.get_or_create(unit=unit, name=check_id)` creates a
   **persisted row** that records the failure.
3. On subsequent renders, `unit.all_checks` reads from the persisted Check
   rows (via `prefetch_all_checks()`), NOT by re-running check_single.
4. Checks are re-run on translation save and stats recalculation.

### Can an external process create Check rows directly?

**Yes, and they will render + filter correctly.** See section 1 above.

The key difference from real-time checks: external-created Check rows are
**stale if the translation changes**. A save will re-run all target checks
and may create/delete rows. If the judge creates a `Check(name="judge-fail")`
row and the human edits + saves, the check framework will run `check_single`
on the registered `JudgeFailCheck` class. If that class returns `True` or
the check is designed as external-only, it must handle this correctly.

### Design pattern for external judge check

```python
class JudgeVerdictCheck(TargetCheck):
    check_id = "judge-flag"
    name = gettext_lazy("LLM Judge Flag")
    description = gettext_lazy("The translation was flagged by the LLM judge.")
    default_disabled = False
    ignore_untranslated = False  # always check

    def check_single(self, source, target, unit):
        # Never compute — this check is externally populated.
        # If a Check row exists, it's already visible via all_checks.
        # We return False here so the framework doesn't auto-delete it.
        return False
```

Registered via `WEBLATE_ADD_CHECK`. External process creates:

```python
Check.objects.get_or_create(unit=unit, name="judge-flag", defaults={"dismissed": False})
```

**Limitation**: The `Check` model has no per-instance metadata field. The
back-translation and reason can't be stored in the Check row itself. They'd
need to go in a Comment attached to the same unit (linked by timestamp or
a `comment.comment` marker).

---

## Ranked Integration Shortlist (Cheapest First)

### 1. Comment-as-verdict (lowest effort)

- **What**: POST to `/api/units/{id}/comments/` with Markdown-formatted verdict.
- **Tradeoffs**: No structured filtering (can't filter "show rejected strings"
  without parsing comment text), no aggregate stats, relies on human reading
  the comment tab. But zero code changes: the API exists, Markdown renders,
  comments appear in "Things to check" sidebar when unresolved.
- **Effort**: External API call only.

### 2. Check rows from external process (medium-low effort)

- **What**: Create `Check(name="judge-fail", unit=unit)` rows via Django ORM
  or a custom API endpoint. Register a `JudgeFailCheck` class via
  `WEBLATE_ADD_CHECK` that always returns `False` from `check_single`.
- **Tradeoffs**: Immediate integration with:
  - `has:check` / `check:judge-fail` search filters
  - Red badge in unit list sidebar
  - Sidebar "Things to check" card with dismiss button
  - Stats aggregation (`stats.allchecks`)
  - Checks overview page (`/checks/`)
  But: no back-translation display, no reason display in check description
  (static per check type), no per-verdict metadata. Need a Comment alongside
  for the audit trail.
- **Effort**: Register a check class (5 lines), create rows externally.

### 3. Label-as-verdict (medium-low effort)

- **What**: One label per verdict ("judge:pass", "judge:flag", "judge:reject").
  External process sets labels via `PATCH /api/units/{id}/` with `"labels": [id]`.
- **Tradeoffs**: Visible as colored badges in sidebar string info and filterable
  via `q=label:judge:reject`. Stats available via `label:<name>` keys. But:
  - Labels must be pre-created per project.
  - No back-translation or reason visible in label badge.
  - One label per verdict is coarse; "judge:flag" can't distinguish
    "minor issue" from "severe concern" without label granularity explosion.
  - Labels are on source_unit, shared across all translations — a verdict
    on fr translation would set the label on the source, confusing non-French
    translators.
- **Effort**: Create labels via admin, PATCH units via API.

### 4. Custom "judge" sidebar card (medium effort)

- **What**: New `<div class="card">` in the sidebar (after Things to check,
  before Glossary) rendered by a template include. External API call from
  JS fetches verdict JSON, renders pass/flag/reject with back-translation
  diff and reason text.
- **Tradeoffs**: Fully custom UI, minimal coupling to existing mechanisms.
  No filtering, no stats, no dismiss. But: complete control over display
  (color-coded verdict badge, back-translation with diff highlighting,
  reason text, "re-judge" button). Best UX for the non-translator stakeholder
  who just wants to see "did the LLM approve this?".
- **Effort**: New template include + JS fetch + CSS + URL route for verdict endpoint.

### 5. Judge as machinery service (medium effort)

- **What**: Add "judge" as a pseudo-machinery service. JS fetches
  `/js/translate/{unit_id}/judge/` which returns machinery-shaped result
  with back-translation as the "suggestion" and verdict as "similarity %".
- **Tradeoffs**: Reuses existing machinery tab, table, and copy buttons.
  But: machinery results are suggestions to use; judge results are evaluations
  of existing work. The "Copy" action on a back-translation is confusing.
  Similarity % would need to be repurposed (e.g., 0 = reject, 50 = flag, 100 = pass).
- **Effort**: Implement `IJudgeMachinery` class in `weblate/machinery/`,
  register via `WEBLATE_ADD_MACHINERY`.

### 6. Custom stats + dashboard widget (medium-high effort)

- **What**: Extend stats system with `judge:*` keys, new widget on component
  overview showing "Judge coverage: 87% | Pass rate: 94%". New filter
  `has:judge` / `has:judge-fail`.
- **Tradeoffs**: Requires custom stats calculation, template changes,
  filter parser extension. Useful for PM dashboard but secondary to
  per-unit verdict display.
- **Effort**: Extend `calculate_by_name`, add new filter keys, template widget.

### 7. Custom review state extension (high effort)

- **What**: New state 13 ("Judge flagged") or 14 ("Judge rejected") in the
  state machine. Judge sets state; review workflow includes judge states.
- **Tradeoffs**: Deep coupling with Weblate's state machine, review flow,
  VCS export, and all state-dependent UI. Overkill for a pre-human-review
  gate.
- **Effort**: State enum, state transitions, VCS integration, UI everywhere.

---

## Summary Table

| Integration surface | Filterable? | Carries reason? | Carries back-trans? | Stats? | Effort |
|---|---|---|---|---|---|
| Comments | via `has:comment` | Yes (Markdown) | Yes (Markdown) | comment count | Minimal |
| Check rows | `check:<name>`, `has:check` | Static per type only | No | `allchecks` | Low |
| Labels | `label:<name>`, `has:label` | 250-char description | No | `label:<name>` | Low |
| Sidebar card | Custom JS/API | Yes | Yes | No (custom) | Medium |
| Machinery tab | Built-in | Repurposed fields | Repurposed | No | Medium |
| Stats + dashboard | Custom filter | No | No | Yes | Medium-High |
| Review states | `state:judge:flag` | No | No | Yes | High |

**Recommended starting combo**: Check rows for filtering/stats/badging + Comment
for audit trail (back-translation, reason). Total effort: one check class
registration + REST API calls to create Check + Comment rows. No template
changes needed for the MVP; everything renders through existing mechanisms.
