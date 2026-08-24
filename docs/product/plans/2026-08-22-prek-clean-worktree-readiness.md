# Full pre-commit readiness plan

**Goal:** Make ``uv run prek run --all-files`` reproducibly pass without
changing product behavior, weakening linting globally, or modifying the user's
current uncommitted work.

**Architecture:** Run fix-capable hooks in a detached temporary worktree created
from ``HEAD``. Treat Ruff import placement as code to fix, translation datasets
as data to scope narrowly, and deliberately valid project terms as dictionary
entries. Do not use the dirty main checkout as a full-hook target.

**Tech Stack:** Git worktrees, Prek, Ruff, Codespell, Typos, Django pytest.

---

## Preconditions and boundaries

- Work from the main checkout, but create a temporary detached worktree for all
  ``--all-files`` hook runs. The main checkout contains unrelated user changes
  that must never be formatted, staged, moved, or discarded.
- Record the complete failing-hook output from the clean worktree before
  changing configuration. Classify every finding as code, documentation,
  technical terminology, or translation/research data.
- Do not change deployment configuration, start or restart a development
  stack, or deploy.
- Do not add broad exclusions such as ``docs/**``, ``misc/**``, or all Python
  files. Do not suppress Ruff rules project-wide.
- Do not correct localization data merely to satisfy an English spell checker.
  A data-path exclusion must be restricted to artifacts verified to be
  translation or research data.

## Task 1: Establish a clean baseline

**Files:** None.

1. Create a temporary detached worktree at the current ``HEAD``:

   ```bash
   git worktree add --detach ../weblate-prek-clean HEAD
   ```

2. From that worktree, run:

   ```bash
   uv run prek run --all-files
   ```

3. Save the output outside the repository or retain it in the command log.
   Separate failures into:

   - Ruff violations in maintained Python code;
   - codespell findings in documented technical prose;
   - codespell findings in localization or research artifacts;
   - Typos or other hook failures.

4. Remove the temporary worktree only after the task is complete:

   ```bash
   git worktree remove ../weblate-prek-clean
   ```

**Acceptance:** The cleanup begins from a reproducible baseline and cannot
rewrite unrelated user work.

## Task 2: Fix maintained Python lint findings

**Files:**

- Modify: ``weblate/trans/tests/test_multilingual_spreadsheet.py``

1. Move every import currently nested in a test method to the module import
   section, preserving import grouping and existing test behavior.
2. Do not add an ``import-outside-top-level`` override. The test file is
   maintained Python code, and the correct project-wide convention is to keep
   imports at module scope.
3. Run:

   ```bash
   uv run prek run ruff-check ruff-format --files \
     weblate/trans/tests/test_multilingual_spreadsheet.py
   ./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py -n 0
   ```

**Acceptance:** The file produces no Ruff import-placement errors and its
complete test module passes.

## Task 3: Scope spelling checks to code and prose

**Files, subject to the baseline report:**

- Modify: ``pyproject.toml``
- Modify: ``scripts/codespell.txt``
- Potentially modify: a small number of English documentation files containing
  actual spelling mistakes

1. For each Codespell finding, choose exactly one narrow remedy:

   - fix an actual misspelling in maintained English code or prose;
   - add a valid project-specific technical term to
     ``scripts/codespell.txt``;
   - add the smallest data-only pattern to ``[tool.codespell].skip`` for a
     confirmed translation or generated research artifact.

2. Keep existing language-specific data out of the spelling checker without
   masking documentation or executable scripts. Prefer a concrete extension or
   directory already devoted to data, for example a confirmed ``*.tsv`` export,
   over a broad source directory exclusion.
3. Do not add personal names, ordinary foreign-language vocabulary, or
   misspellings to the dictionary unless their use is a durable project
   identifier.
4. Run Codespell over the complete clean worktree after every configuration
   change.

**Acceptance:** Codespell has no findings, and every new skip or dictionary
entry has a documented, narrow rationale in the commit diff.

## Task 4: Verify the complete hook suite

**Files:** Only files identified in Tasks 2 and 3.

1. In the temporary clean worktree, run:

   ```bash
   uv run prek run --all-files
   uv run prek run --all-files
   ```

   The second successful run proves that no fix-capable hook would alter the
   tree.

2. Run the affected spreadsheet test module serially:

   ```bash
   ./rundev.sh test weblate/trans/tests/test_multilingual_spreadsheet.py -n 0
   ```

3. Inspect ``git diff --check`` and the exact staged diff. Stage only the
   cleanup files, not the main checkout's existing user changes.

**Acceptance:** Both full Prek runs and the spreadsheet tests pass, and the
staged diff contains only the scoped lint-readiness cleanup.

## Task 5: Commit and deliver

1. Commit with a Conventional Commit message, for example:

   ```bash
   git commit -m "ci: make full pre-commit checks pass"
   ```

2. Push the commit to ``origin``.
3. Remove the temporary worktree after confirming the main checkout remains
   unchanged except for the approved cleanup files.

## Out of scope

- Changes to application behavior, checks, automatic translation, or database
  models.
- Broadly disabling Ruff, Codespell, Typos, or Prek hooks.
- Editing translation datasets to turn non-English terms into English words.
- Reformatting, committing, or deleting unrelated changes in the main
  checkout.
- Any deployment, container restart, or production operation.
