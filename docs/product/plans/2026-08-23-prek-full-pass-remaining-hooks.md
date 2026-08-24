# Full pre-commit readiness: remaining hook categories

**Goal:** Finish what `docs/product/plans/2026-08-22-prek-clean-worktree-readiness.md`
started - make `uv run prek run --all-files` pass with zero exceptions - by
covering the four hook categories that plan's Task 2/3 did not name. Two
follow-up commits already landed the mechanical slice (import placement,
codespell scoping, shellcheck, changelog punctuation, one doccmd file, reuse
headers, and a bulk whitespace/EOF/JSON pass). This plan covers what is left,
verified against a full `uv run prek run --all-files` baseline taken
2026-08-23 in a clean worktree.

**Architecture:** Same isolation model as the parent plan - do all work in a
detached worktree created from `HEAD`, never in the main checkout's dirty
working tree. Each task below is independently committable and pushable; do
not block one on another.

**Tech Stack:** Git worktrees, Prek, Ruff, Typos, rumdl, doccmd.

---

## Preconditions and boundaries

- Work in a detached worktree, not the main checkout. Record a fresh
  `uv run prek run --all-files` baseline before starting each task, since the
  file set drifts between sessions (this plan's own baseline already
  disagrees with the parent plan's on file counts).
- Do not change deployment configuration, start or restart the shared
  dev-docker stack, or deploy. Running `weblate_customization`'s or
  `loc_kit_ingest`'s own standalone test suites (`uv run pytest`, no Docker)
  is in scope for verification; a Docker-based Weblate test run is not,
  unless separately approved.
- Do not correct localization or research data merely to satisfy an English
  spell checker or linter; classify each finding (code vs. data) before
  fixing it, as the parent plan's Task 3 did for codespell.

## Task 1: Ruff findings outside product code that are genuinely mechanical

**Files (from the 2026-08-23 baseline, 45 files / 719 findings total):**

- `.claude/skills/weblate-lqa/scripts/audit_component.py` and its `.omp/`
  duplicate (36 findings each, identical file content in both trees)
- `analysis/probes/*.py` (24 files, 1-16 findings each): `st2-judge-experiment.py`,
  `st2-zh-recalibration.py`, `st2-zh-annotate.py`, `st2-zh-score.py`,
  `col4-id-*.py` (7 files), `autofix-backfill-scan.py`,
  `game-number-probe.py`, `glossary-or-probe.py`, `openrouter-usage.py`,
  `col4-cost-probe.py`, `col4-glossary-probe.py`, `col4-prompt-probe.py`

1. For each file, run `uv run prek run ruff-check ruff-format --files <file>`
   and read every finding. The `--fix` flag already ran during baseline
   collection; classify the remainder into:
   - fixable by the same kind of change as the parent plan's Task 2 (import
     placement, `try`/`except`/`continue` logging, merged comparisons,
     unconventional import alias) - fix directly;
   - a rule that would require behavior-affecting judgment (see Task 2) -
     leave it and note it in the commit message; do not silence the rule.
2. Do not add a blanket `per-file-ignores` entry for these paths; fix the
   findings.
3. Re-run `ruff-check`/`ruff-format` on the touched files until clean.

**Acceptance:** These files produce no Ruff findings, or only findings
explicitly deferred to Task 2 with a one-line reason each.

## Task 2: Ruff findings in `weblate/` that need a real decision

**Files:**

- `weblate/trans/models/multilingual_spreadsheet.py` (1 finding)
- `weblate/trans/multilingual_spreadsheet.py` (8 findings)
- `weblate/trans/tests/test_files.py` (13 findings)

These are not mechanical - each needs a product decision, not a style pass:

1. `django-model-without-dunder-str` on `ComponentSpreadsheetImportDraft`:
   decide and add a `__str__` (likely component + owner + token prefix).
2. `suspicious-xml-etree-import` /
   `unconventional-import-alias` on `import xml.etree.ElementTree` in
   `multilingual_spreadsheet.py`: decide whether the existing usage is safe
   (parsing Weblate's own generated XLSX XML, not untrusted input) and either
   add a scoped `# noqa`-equivalent Ruff suppression with that justification,
   or switch to `defusedxml`. Alias to `ET` either way.
3. `assert` in production code (`multilingual_spreadsheet.py:126`): replace
   with an explicit exception or justify why the module already treats
   `assert` as acceptable (e.g., co-located with other asserts under the same
   convention - check first).
4. `repeated-equality-comparison` (`multilingual_spreadsheet.py:289`): pure
   mechanical merge, fix directly.
5. Remaining `import-outside-top-level` in `test_files.py` and
   `multilingual_spreadsheet.py`: same treatment as the parent plan's Task 2.

**Acceptance:** `ruff-check`/`ruff-format` pass on these three files; the
`weblate/trans/tests/test_files.py` module and, if Docker is available,
`weblate/trans/tests/test_multilingual_spreadsheet.py` still pass.

## Task 3: Ruff findings in `loc_kit_ingest/`

**Files:** `loc_kit_ingest/*.py` and `loc_kit_ingest/tests/*.py` (18 files,
~450 findings - the largest single bucket).

1. This package has its own standalone test suite with no Django/DB
   dependency (`cd loc_kit_ingest && uv run pytest`, per `AGENTS.md`) - use it
   as the safety net for every fix in this task, not the Weblate suite.
2. Triage findings by rule first (`ruff check loc_kit_ingest/ --statistics`
   equivalent), since 18 files with hundreds of findings almost certainly
   share a handful of repeated patterns (e.g., test files with function-local
   imports or fixture-shadowing patterns) rather than 450 unique problems.
3. Fix mechanically-safe rules directly; for anything that would change
   `loc_kit_ingest`'s public API or CLI behavior, stop and get that increment
   approved separately - this package is depended on by
   `weblate/utils/views.py:create_component_from_kit` and by the copy under
   `dev-docker/data/python/loc_kit_ingest/` (see `AGENTS.md`), so a signature
   change has to be synchronized in both places.

**Acceptance:** `ruff-check`/`ruff-format` pass on `loc_kit_ingest/`, and
`cd loc_kit_ingest && uv run pytest` is green.

## Task 4: `typos` tool - separate config from codespell, same data files

**Files, subject to a fresh baseline:**

- Modify: `pyproject.toml` (`[tool.typos.files]`, `[tool.typos.default.*]`)

The parent plan's Task 3 scoped **codespell** (`[tool.codespell]`) to the
confirmed Heart Abyss/COL4 data files. The `typos` tool (crate-ci/typos) is a
separate binary with separate configuration and does not read codespell's
`skip`/`ignore-words-list` - it still fails on the same files (~90 findings in
`misc/heart-abyss-hub-1-units.tsv` and `misc/heart-abyss-hub-1-translation-qa.md`,
`analysis/data/col4-id-defects.tsv`, `analysis/data/col4-glossary-append-2026-08-14.csv`,
French/Indonesian/Turkish loc data) plus a handful of code-level false
positives already fixed for codespell but not for typos: `nd`/`ein` in
`docs/operations/plans/2026-08-19-space-arena-game-number-and-same-noise.md`, `recal` (an <!-- # codespell:ignore -->
intentional abbreviation, `st2-zh-recal`) in three `analysis/data/`/`docs/llm-first/`
files, `criticals` (plural noun, intentional) in
`docs/llm-first/plans/2026-08-13-01-judge-verdict-core.md`, `seconde` (a French
in-game duration string, not a typo of "second") in
`docs/product/plans/2026-08-17-game-number-check.md`.

1. Add the confirmed data files/directories to `[tool.typos.files].extend-exclude`
   (mirrors the `misc/**.tsv` etc. reasoning already applied to codespell).
2. Add the confirmed code-level words to `[tool.typos.default.extend-words]`
   or `extend-identifiers`, matching the existing entries in that section
   (`ba`, `billling`, `hasttr`, etc.) - one entry per word with a comment.
3. Run `uv run prek run typos --all-files` after every change.

**Acceptance:** `typos` has no findings, and every new exclude/word entry has
a one-line rationale in the commit diff (same bar as the parent plan's Task
3).

## Task 5: `rumdl` markdown lint

**Files, subject to a fresh baseline:** 30 files, 424 findings. Rule
breakdown from the 2026-08-23 baseline: `MD036` (emphasis used instead of a
heading) 303, `MD032` (list needs blank line) 66, `MD022` (heading needs
blank line) 18, `MD040` (fenced code needs a language) 11, `MD031` (fenced
code needs blank line) 8, `MD026` (trailing punctuation in heading) 8,
`MD028` (blank line inside blockquote) 6, plus one each of `MD057` (broken
relative link), `MD001` (heading level skip), `MD034` (bare URL), `MD058`.
Heaviest files: `docs/llm-first/archive/2026-08-07-project-scoped-llm-context.md`
(63), `docs/product/plans/2026-08-12-loc-kit-glossary-smarter-inference.md` (61),
`docs/llm-first/archive/2026-08-05-routed-llm-machinery.md` (44),
`docs/llm-first/plans/2026-08-14-llm-usage-tracking.md` (37).

1. `rumdl-fmt` (the autofix hook) safely resolves `MD022`/`MD032`/`MD031`/
   `MD028` on its own - run it and accept the result for those rules.
2. `MD036` is 71% of the findings and is NOT a safe blind autofix: it means
   rewriting a `**Bold label:**` paragraph opener into a real
   `#### Bold label` heading, which changes the document's heading hierarchy
   and its table of contents. AGENTS.md's documentation guidance is explicit
   that the fork's `docs/**` structure should not be rewritten casually. Two
   options, needs a decision before starting:
   - **(a)** Accept the structural rewrite across the ~15 affected files
     (mostly closed/historical plan docs) as a one-time formatting pass.
   - **(b)** Add a `[tool.rumdl.overrides]` (or equivalent) MD036 exclusion
     scoped to the fork's `docs/**` markdown - these are internal
     process records, not published documentation, and the plan's own
     precedent (`.pre-commit-config.yaml`'s existing exclusions) already
     treats several doc trees as exempt from some hooks.
3. Fix the one `MD057` broken relative link
   (`.github/comments/issue-newbie.md:3` -> `docs/contributing/index.rst`) by
   checking whether that target moved and pointing at the current one.
4. Fix the one-off `MD001`/`MD034`/`MD058` findings directly - each is a
   single occurrence.

**Acceptance:** `rumdl check`/`rumdl fmt` pass, and the MD036 decision (3a or
3b) is recorded in the commit message.

## Task 6: `doccmd` - remaining indented code excerpts

**Files:** 17 files, 61 blocks (one file/block pair, in
`docs/llm-first/reviews/2026-08-11-llm-prompt-and-pipeline-review.md`, is already
fixed). Full list from the 2026-08-23 scan:
`docs/llm-first/plans/2026-08-11-layer0-autofix-quick-wins.md` (6 blocks),
`docs/llm-first/plans/2026-08-13-01-judge-verdict-core.md` (7),
`docs/llm-first/archive/2026-08-05-routed-llm-machinery.md` (1),
`docs/product/plans/2026-08-10-loc-kit-glossary-deterministic-infer.md` (4),
`docs/product/plans/2026-08-10-loc-kit-glossary-note-column.md` (6),
`docs/product/plans/2026-08-10-loc-kit-glossary-update-existing.md` (3),
`docs/product/plans/2026-08-12-glossary-terminology-flag.md` (3),
`docs/product/plans/2026-08-12-loc-kit-glossary-smarter-inference.md` (13),
`docs/llm-first/plans/2026-08-14-llm-usage-tracking.md` (5),
`docs/product/plans/2026-08-17-microsoft-clarity-session-recordings.md` (7),
`docs/operations/plans/2026-08-17-outgoing-mail-webnotify.md` (2),
`docs/product/plans/2026-08-22-multilingual-spreadsheet-review-fixes.md` (2),
`docs/product/plans/2026-08-22-nested-game-placeholder-protection.md` (1),
`docs/llm-first/measurements/2026-08-11-col4-fr-autotranslate-report.md` (2).

1. Same pattern as the already-fixed file: each block quotes a real code
   excerpt with its original (non-zero) indentation preserved, and several
   use a bare `...` where the excerpt elides code - a syntax error inside
   Python, not just a formatting issue.
2. For each block: dedent to the excerpt's own minimum indentation, replace a
   bare `...` with `# ...`, then run
   `uvx ruff@0.16.1 format --diff <tmpfile>` (or
   `uv run prek run doccmd --files <that .md file>`) to confirm it is already
   in ruff's canonical form before moving to the next block - `doccmd`
   rewrites the source doc in place if the block needed reformatting beyond
   the syntax fix, so check the resulting diff is a clean dedent, not a
   content change.
3. Batch this per file (fix every block in one file, verify, move to the
   next) rather than per block, since `doccmd` stops at the first parse error
   per invocation and does not report every failure in one pass.

**Acceptance:** `uv run prek run doccmd --all-files` passes.

## Verification (all tasks)

1. In the worktree: `uv run prek run --all-files` twice - the second run
   changing nothing proves no fix-capable hook would still alter the tree.
2. `cd loc_kit_ingest && uv run pytest` (Task 3's package, no DB needed).
3. If the dev-docker stack is available and its use is approved for this
   session: `./rundev.sh test weblate/trans/tests/test_files.py
   weblate/trans/tests/test_multilingual_spreadsheet.py -n 0`.
4. `git diff --check` and the exact staged diff match only the files listed
   above per task; commit each task separately (matches how the parent plan
   landed as two independent commits).

## Out of scope

- Any change to `weblate_customization/`'s registered checks, autofixes, or
  machinery behavior.
- Broadly disabling Ruff, `typos`, `rumdl`, or `doccmd`.
- Rewriting the fork's `docs/**` content beyond what Task 5's
  MD036 decision and Task 6's dedents require.
- Any deployment, container restart, or production operation.
