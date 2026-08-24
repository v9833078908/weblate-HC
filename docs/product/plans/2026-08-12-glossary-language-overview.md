# Glossary language overview Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the unhelpful "Unfinished characters" column from the language overview of glossary components and present all zero-valued glossary statistics compactly.

**Architecture:** `component.html` already passes `is_glossary` to the shared `snippets/list-objects.html` table. Use that existing template context to omit the character-statistics header and cell only for glossary views. Stop opting out of the shared `list_objects_number` zero behavior for the glossary `Translated` cell, so every zero is visually hidden but remains available to assistive technology. Leave ordinary component tables unchanged.

**Tech Stack:** Django templates, Django test client, pytest.

**Status (2026-08-15): implemented and verified.** Glossary-only statistics
rendering and the regression coverage are implemented.

---

## Task 1: Add a glossary overview regression test

**Files:**

- Modify: `weblate/trans/tests/test_views.py` in `BasicViewTest`

### Step 1: Write the failing test

Add a test that creates the project's glossary and loads its component overview. Assert that the glossary table:

- does not include the `Unfinished characters` heading;
- still includes `Unfinished words`;
- uses a visually hidden `0` in every zero-valued `Translated` cell.

In the same test, load the ordinary component overview and assert that it still includes `Unfinished characters`.

Add `from lxml import html` with the other third-party imports, then use this test:

```python
class BasicViewTest(ViewTestCase):
    def test_glossary_component_hides_unfinished_character_statistics(self) -> None:
        self.component.create_glossary()
        glossary = Component.objects.get(project=self.project, is_glossary=True)

        response = self.client.get(glossary.get_absolute_url())

        self.assertNotContains(response, "Unfinished characters")
        self.assertContains(response, "Unfinished words")
        tree = html.fromstring(response.content)
        translated_cells = tree.xpath(
            '//div[@id="translations"]//tbody/tr[th]/td[@data-value][1]'
        )
        self.assertTrue(translated_cells)
        for cell in translated_cells:
            self.assertTrue(
                cell.xpath('.//span[@class="visually-hidden" and text()="0"]')
            )

        response = self.client.get(self.component.get_absolute_url())

        self.assertContains(response, "Unfinished characters")
```

### Step 2: Run the test to verify it fails

Run:

```bash
DJANGO_SETTINGS_MODULE=weblate.settings_test \
CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434 \
CI_DB_USER=weblate CI_DB_PASSWORD=weblate CI_DB_NAME=weblate_glossary_overview \
uv run pytest weblate/trans/tests/test_views.py::BasicViewTest::test_glossary_component_hides_unfinished_character_statistics -q
```

Expected: FAIL because the shared template currently renders `Unfinished characters` for glossaries.

## Task 2: Hide non-actionable glossary statistics

**Files:**

- Modify: `weblate/templates/snippets/list-objects.html:116-124,331-347`

### Step 1: Conditionally render the header

Wrap the existing `Unfinished characters` `<th>` in `{% if not is_glossary %}` / `{% endif %}`. Keep its sort link, CSS class, and translation unchanged for non-glossary tables.

### Step 2: Conditionally render the value cell

Wrap the matching `object.stats.todo_chars` `{% list_objects_number %}` call in the same `{% if not is_glossary %}` condition. Do not change the `todo`, `todo_words`, or `nottranslated` cells.

### Step 3: Use the shared zero display for glossary translations

Remove `show_zero=True` from the glossary `object.stats.translated` call. This lets `list_objects_number` use its existing accessible, visually hidden zero rendering, matching the rest of the glossary metrics.

### Step 4: Run the regression test

Run the Task 1 command again.

Expected: PASS.

### Step 5: Run the focused view-test class

Run:

```bash
DJANGO_SETTINGS_MODULE=weblate.settings_test \
CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434 \
CI_DB_USER=weblate CI_DB_PASSWORD=weblate CI_DB_NAME=weblate_glossary_overview \
uv run pytest weblate/trans/tests/test_views.py::BasicViewTest -q
```

Expected: PASS.

## Task 3: Verify the rendered UI and commit

**Files:**

- Verify: `weblate/templates/snippets/list-objects.html`
- Verify: `weblate/trans/tests/test_views.py`

### Step 1: Check formatting and linting only for touched files

Run:

```bash
uv run prek run --files weblate/templates/snippets/list-objects.html weblate/trans/tests/test_views.py docs/product/plans/2026-08-12-glossary-language-overview.md
```

Expected: PASS.

### Step 2: Browser-smoke the glossary overview

Use the existing local Weblate instance without restarting or rebuilding it. Open a glossary component overview and confirm that `Unfinished characters` is absent while zero-valued metrics remain blank rather than visibly rendered as `0`.

### Step 3: Commit and push the scoped change

```bash
git add docs/product/plans/2026-08-12-glossary-language-overview.md \
  weblate/templates/snippets/list-objects.html \
  weblate/trans/tests/test_views.py
git commit -m "fix(glossary): simplify language overview statistics"
git push -u origin fix/glossary-language-overview
```

## Out of scope

- Changing how `todo_chars` is calculated or stored.
- Changing language statistics for normal translation components.
- Restoring visually rendered zero values in glossary statistics.
- Any data migration, automatic translation, deployment, container rebuild, or restart.
