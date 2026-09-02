# Judge test fixture-base migration plan

**Status:** proposed 2026-09-02; not started.

**Goal:** Cut the wall time of the judge test suites by moving every
VCS-independent judge test class from ``ViewTestCase`` (a fresh git clone plus
repository scan on every test method) to ``FixtureTestCase`` (the
``simple-project.json`` fixture loaded once per class), without changing what a
single test asserts.

**Architecture:** Reuse the existing ``FixtureComponentTestCase`` /
``FixtureTestCase`` base classes in ``weblate/trans/tests/test_views.py``. They
override ``clone_test_repos`` to a no-op and build the component from the loaded
fixture instead of a per-test checkout. The canonical strings the judge tests
depend on -- the ``Hello, world!`` source unit and its ``cs`` translation --
are present in the fixture. The migration is a base-class swap only: change the
parent class, run the class in isolation, keep it if it is green, revert it if
it turns out to need a live repository. Test bodies, verdict fixtures, mocks,
and the code under test are never touched.

**Tech Stack:** Django pytest, ``FixtureComponentTestCase``, pytest-xdist,
PostgreSQL test database.

**Motivation (measured 2026-09-02, this worktree):** Two classes of twelve
identical tests, single worker, reused DB (worker DB creation excluded):

| base | total (12 tests) | per test | one-time |
| --- | --- | --- | --- |
| ``ViewTestCase`` (git clone per test) | 12.69 s | ~1.06 s | 0 |
| ``FixtureTestCase`` (fixture per class) | 4.10 s | ~0.15 s | ~2.4 s |

The git-based component is rebuilt on every method (~1.06 s each, flat); the
fixture front-loads ~2.4 s once, then ~0.15 s per method. Break-even is about
three tests; beyond that the win grows with class size (3.0x at N=12, 3.9x at
N=20, 4.6x at N=30). The fat judge classes (``JudgeLoopTest``,
``JudgeRoundTest``, ``JudgeQueueStripViewTest``, ``JudgeRunReportViewTest``,
``JudgeProducerTriageViewTest``) are where most of the saving lands.

---

## Preconditions and boundaries

- Base-class change only. Never alter a test's assertions, its verdict/round
  fixtures, its mocks, or the production code under test. A migrated class must
  keep the same test count and the same per-test outcomes.
- The per-class oracle is the class itself: run it in isolation with
  ``-p no:cacheprovider`` before and after. If any test fails only because it
  needs a live VCS repository (a commit, a repository scan, a real language
  addition), revert that one class to the git base and record why.
- Leave already-cheap classes alone. ``SimpleTestCase`` and
  ``TransactionTestCase`` judge classes do not build a component and gain
  nothing from the fixture.
- Do not migrate glossary or concurrency classes (see the inventory). They stay
  on the git base by design.
- No deployment, no container restart, no change to ``pyproject.toml`` test
  configuration.

## Inventory

Every judge test class, its current base, and its disposition. Only ``ViewTestCase``
classes are migration candidates.

**Migrate now -- pure verdict/render/resolution/form logic (Task 2):**

| class | file |
| --- | --- |
| ``JudgeResolutionTest`` | ``trans/tests/test_judge.py`` |
| ``JudgeCandidateAcceptanceTest`` | ``trans/tests/test_judge.py`` |
| ``JudgeCheckVisibilityTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeVerdictCardTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeRepairEvidenceTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeBackTranslationTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeResolutionViewTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeQueueStripViewTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeRunReportViewTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeProducerTriageViewTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeVerdictCardRenderTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeRoundTest`` | ``trans/tests/test_judge_round.py`` |
| ``JudgeAutoFormTest`` | ``trans/tests/test_judge_form.py`` |
| ``JudgeDeferralTest`` | ``trans/tests/test_judge_deferrals.py`` |
| ``JudgeCheckTest`` | ``checks/tests/test_judge.py`` |
| ``JudgeCheckDismissalTest`` | ``checks/tests/test_judge.py`` |

**Migrate with verification -- exercises ``run_judge_batch`` / ``auto_translate``
with mocked LLM (Task 3).** These write unit targets to the DB; confirm none of
them forces a repository commit on the fixture component:

| class | file |
| --- | --- |
| ``JudgeLoopTest`` | ``trans/tests/test_judge_loop.py`` |
| ``JudgeUnparsedRetryRoundTest`` | ``trans/tests/test_judge_loop.py`` |
| ``JudgeIncrementalPersistenceTest`` | ``trans/tests/test_judge_loop.py`` |
| ``JudgeMaxLengthRepairTest`` | ``trans/tests/test_judge_loop.py`` |
| ``JudgeAutoTranslateViewTest`` | ``trans/tests/test_judge_views.py`` |
| ``JudgeAutoTranslateTest`` | ``trans/tests/test_judge_autotranslate.py`` |

**Keep on the git base -- ineligible (Task 4, no code change):**

| class | reason |
| --- | --- |
| ``JudgeGlossaryRepairLockTest`` | ``CREATE_GLOSSARIES = True`` builds a second VCS component |
| ``JudgeGlossaryContextTest`` | ``CREATE_GLOSSARIES = True`` builds a second VCS component |
| ``JudgeResolutionRealConcurrencyTest`` | ``RepoTestMixin`` + ``TransactionTestCase``: real two-connection race |
| ``JudgeSeatConnectionCleanupTest`` | ``TransactionTestCase``: real worker DB-connection cleanup |

**No change -- already cheap (``SimpleTestCase``):** ``JudgeSeverityGateTest``,
``JudgeHashTest``, ``JudgeResolutionLocalizationTest``,
``JudgeRequestEstimateTest``, ``JudgeCandidateMetadataTest``,
``JudgeRetryBudgetTest``, ``JudgeSeverityVocabularyLocalizationTest``.

## Task 1: Record the baseline

**Files:** none (produces a measurement note).

1. With the test database already created (reused), time each judge file
   single-worker so per-class cost is not hidden by xdist:

   ```bash
   env CI_DB_HOST=127.0.0.1 CI_DB_PORT=5434 CI_DB_USER=weblate \
     CI_DB_PASSWORD=weblate CI_DB_NAME=weblate_producer_triage \
     DJANGO_SETTINGS_MODULE=weblate.settings_test \
     uv run pytest weblate/trans/tests/test_judge.py \
     weblate/trans/tests/test_judge_loop.py \
     weblate/trans/tests/test_judge_views.py \
     weblate/trans/tests/test_judge_round.py \
     weblate/trans/tests/test_judge_form.py \
     weblate/trans/tests/test_judge_deferrals.py \
     weblate/trans/tests/test_judge_autotranslate.py \
     weblate/checks/tests/test_judge.py \
     -q --no-header --no-cov -n 0 -p no:cacheprovider --durations=0 --durations-min=0
   ```

2. Save the total wall time and the per-class setup/call sums to
   ``docs/product/measurements/2026-09-02-judge-suite-fixture-baseline.md``.

**Acceptance:** A reproducible before number exists for the same file set the
migration will re-time in Task 5.

## Task 2: Migrate the pure-logic classes

**Files:** ``trans/tests/test_judge.py``, ``trans/tests/test_judge_views.py``,
``trans/tests/test_judge_round.py``, ``trans/tests/test_judge_form.py``,
``trans/tests/test_judge_deferrals.py``, ``checks/tests/test_judge.py``.

1. For each class in the "Migrate now" table, change only the parent:
   ``ViewTestCase`` becomes ``FixtureTestCase``. Add ``FixtureTestCase`` to the
   existing ``from weblate.trans.tests.test_views import ...`` line in each file;
   do not add a second import statement.
2. Leave every ``setUp``, ``enable_review``, helper, fixture, and assertion
   exactly as-is. ``FixtureTestCase.setUp`` already calls
   ``set_up_authenticated_view`` like ``ViewTestCase``.
3. Run each migrated class in isolation and confirm the same test count and all
   green:

   ```bash
   ... uv run pytest weblate/trans/tests/test_judge_views.py::JudgeRoundTest \
     -q --no-header --no-cov -p no:cacheprovider
   ```

4. If a class fails only on a VCS dependency, revert that single class to
   ``ViewTestCase`` and note it in the measurement doc.

**Acceptance:** All sixteen listed classes pass unchanged on the fixture base,
or any exception is reverted and documented.

## Task 3: Migrate the batch classes with verification

**Files:** ``trans/tests/test_judge_loop.py``,
``trans/tests/test_judge_views.py``, ``trans/tests/test_judge_autotranslate.py``.

1. Migrate the six classes in the "Migrate with verification" table one at a
   time, ``ViewTestCase`` to ``FixtureTestCase``.
2. After each, run the class in isolation. These run ``run_judge_batch`` /
   ``auto_translate`` with a mocked LLM and write unit targets; verify no test
   triggers a repository commit that the fixture component cannot satisfy.
3. Revert any class whose failure traces to a live repository requirement and
   record it. A partial result is acceptable: some of these six may stay on the
   git base.

**Acceptance:** Each of the six is either green on the fixture base or reverted
with a recorded reason; no test's outcome changed.

## Task 4: Confirm the ineligible set is untouched

**Files:** none.

1. Verify the four "Keep on the git base" classes and the seven
   ``SimpleTestCase`` classes were not modified.

**Acceptance:** ``git diff`` shows changes only to base-class lines and imports
of migrated classes; glossary, concurrency, and ``SimpleTestCase`` classes are
byte-identical.

## Task 5: Re-measure, regress, and land

**Files:** ``docs/product/measurements/2026-09-02-judge-suite-fixture-baseline.md``.

1. Re-run the Task 1 command; record the after number and the delta.
2. Run the full judge regression across all migrated files under xdist to prove
   nothing broke:

   ```bash
   ... uv run pytest weblate/trans/tests/test_judge.py \
     weblate/trans/tests/test_judge_loop.py \
     weblate/trans/tests/test_judge_views.py \
     weblate/trans/tests/test_judge_round.py \
     weblate/trans/tests/test_judge_form.py \
     weblate/trans/tests/test_judge_deferrals.py \
     weblate/trans/tests/test_judge_autotranslate.py \
     weblate/checks/tests/test_judge.py \
     -q --no-header --no-cov -n auto -p no:cacheprovider
   ```

3. Run ``uv run prek run ruff-check ruff-format --files <changed test files>``.
4. Commit and push.

**Acceptance:** The full judge suite is green, the measurement doc shows a
concrete wall-time reduction, and the change is committed.

## Out of scope

- Any change to production code, judge behavior, or what a test asserts.
- Migrating non-judge test classes (a separate, larger sweep if this pays off).
- Changing ``pyproject.toml`` test configuration or the xdist worker count.
- Introducing a new base class or a shared judge fixture; this plan only reuses
  the existing ``FixtureTestCase``.
- Any deployment, container restart, or production operation.
