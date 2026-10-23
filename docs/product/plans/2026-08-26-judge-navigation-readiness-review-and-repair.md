# Review and repair judge navigation readiness

**Date:** 2026-08-26. **Status:** verified.

## Goal

Independently review the complete `feat/judge-navigation-readiness` change set
against `origin/main`, correct only confirmed implementation defects, and leave
the branch with reproducible evidence for every repair.

## Scope

The review covers the 58 branch-changed files, with focused analysis of:

- verdict persistence, state projection, checks, permissions, query helpers,
  statistics and release readiness;
- automatic-translation launches, background-task phase metadata, repair
  evidence and decision history;
- component, project and workspace templates, JavaScript form behaviour and
  browser accessibility;
- tests, translations, changelog, security model and roadmap claims.

The review uses three independent reviewer agents, named Luna domain, Luna UI,
and Luna security. The available agent inventory has no `luna` type, so each is
a `reviewer`-type subagent with that stable review name.

## Plan

1. Launch the three independent reviews in parallel. Each reviewer must report
   only evidence-backed findings with severity, exact file and line, a
   reproduction or counterexample, and the smallest safe correction. They do
   not edit files or run project-wide validation.
2. Triage every finding in the main session. Re-read the affected code, use
   symbol references for exported Python symbols, and reproduce each plausible
   defect with a targeted test or live isolated-stack scenario. Reject false
   positives and report why.
3. Implement only confirmed defects. Preserve plan-01 behaviour and existing
   interfaces; add or strengthen a regression test for every user-observable
   repaired behaviour. Do not alter production configuration or deploy.
4. Run the narrow regression tests for each repair, then the relevant judge
   suite, lint and type checks for touched files. Ask a fresh reviewer to
   inspect the repair diff. Commit each repair with a Conventional Commit
   message and push the feature branch.

## Acceptance criteria

- Every review finding has a documented disposition: fixed, rejected with
  evidence, or explicitly deferred because it is outside this branch.
- No confirmed critical, high or medium defect remains in the changed code.
- Each repaired observable contract has a test that fails without its repair.
- The changed-file lint/type checks and the relevant judge tests pass.
- No merge, deployment, production judge call, unrelated refactor or change to
  the deferred run-history plan is performed.

## Review disposition

Three independent Luna reviews found and the main session reproduced these
defects. All were repaired with regression coverage:

- Project-level automatic translation could enqueue an invalid task target.
- Capped project/workspace selection had no stable ordering and could
  under-report remaining strings.
- Judge repair evidence always paired attempt 0 with attempt 1.
- Invalid judge cap settings appeared ready and failed only in preview.
- Same-state producer resolutions did not invalidate judge statistics.
- An A-B-A context history hid the current eligible decision.
- The translation-page estimate used a different query than judge execution.
- Persisted form state could override the explicit judge launcher.
- The card contradicted accepted critical overrides and unparsed responses.
- Preview did not refresh for overwrite or engine inputs.
- Empty judge scopes incorrectly reported cap exhaustion and used generic
  completion copy.
- Judge workspace runs incorrectly applied translation-memory source selection.
- A failed selected translation could release its reserved judge cap and allow
  later units outside the previewed scope to run.

The reported context-hash TOCTOU was rejected: the inspected `ContextForm`
mutates explanation, labels and flags, while the hash uses source, source note
and glossary prompt entries. No concrete permitted mutation race was found.
