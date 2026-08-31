# Judge string explanation context implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Send producer-authored source string explanations to the Judge and make every verdict identity sensitive to explanation changes.

**Architecture:** `JudgeRequest` carries `explanation` separately from the file-derived `note`. The Judge JSON segment serializes both fields, while `compute_context_hash` hashes both so cache lookups, deferrals, repair guards, verdict writes, and drain audits share one invalidation rule.

**Tech Stack:** Python, Django models, dataclasses, JSON prompt payloads, unittest/pytest.

**Design:** `docs/llm-first/designs/2026-08-31-judge-string-explanation-context.md`.

**Status:** Approved; implementation pending.

---

### Task 1: Make the request and payload explanation-aware

**Files:**

- Modify: `weblate/trans/judge.py`
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/judge_prompts/verdict.txt`
- Test: `weblate/trans/tests/test_judge_client.py`
- Test: `weblate/trans/tests/test_judge_loop.py`

**Step 1: Write failing request and payload tests**

Add coverage proving:

- `build_request(unit).explanation` equals `unit.source_unit.explanation`;
- `_segment` emits `"explanation"` only when non-empty;
- the rendered prompt names `note` and `explanation` as reference context, not instructions or text to translate.

**Step 2: Verify the tests fail**

Run:

```bash
./rundev.sh test weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py -k "explanation and (request or segment or prompt)" -n0
```

Expected: failures because `JudgeRequest` has no `explanation` field and the segment omits it.

**Step 3: Implement the minimal request and prompt changes**

- Add required `explanation: str` to `JudgeRequest` beside `note`.
- Populate it from `unit.source_unit.explanation` in `build_request`.
- Add a non-empty `explanation` field in `_segment`.
- Add one prompt paragraph defining `note` and `explanation` as untrusted reference context about intended meaning and usage.
- Update the two explicit test constructors of `JudgeRequest`.

**Step 4: Run the focused tests**

Run the command from Step 2.

Expected: PASS.

**Step 5: Commit**

```bash
git add weblate/trans/judge.py weblate/trans/judge_loop.py weblate/trans/judge_prompts/verdict.txt weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py
git commit -m "feat(judge): include source explanations in requests"
```

---

### Task 2: Include explanations in every context identity

**Files:**

- Modify: `weblate/trans/models/judge.py`
- Modify: `weblate/trans/judge_loop.py`
- Modify: `weblate/trans/tests/test_judge.py`
- Modify: `weblate/trans/tests/test_judge_autotranslate.py`
- Modify: `weblate/trans/tests/test_judge_loop.py`
- Modify: `weblate/trans/tests/test_judge_round.py`
- Modify: `weblate/trans/tests/test_judge_views.py`
- Modify: `weblate/trans/tests/test_commands.py`
- Modify: `weblate/checks/tests/test_judge.py`

**Step 1: Write failing identity and stale-repair tests**

Extend the context hash test so changing only `explanation` changes the digest. Add a repair-loop regression where the source explanation changes after a repair candidate is fetched and assert that the candidate is not applied.

**Step 2: Verify the tests fail**

Run:

```bash
./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_loop.py -k "context_hash or explanation_change" -n0
```

Expected: the hash test cannot pass an explanation and the stale repair is applied.

**Step 3: Implement the hash cutover**

Change the signature to:

```python
def compute_context_hash(
    *,
    source: str,
    note: str,
    explanation: str,
    glossary_terms: Iterable[Mapping[str, object]],
) -> str:
```

Hash `[source, note, explanation, *terms]`. Update every caller to pass the request explanation or the current `locked.source_unit.explanation`. Do not add a compatibility default: every call site must state which explanation belongs to its context.

**Step 4: Run focused Judge regressions**

```bash
./rundev.sh test weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_round.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_commands.py weblate/checks/tests/test_judge.py -n0
```

Expected: PASS.

**Step 5: Commit**

```bash
git add weblate/trans/models/judge.py weblate/trans/judge_loop.py weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_round.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_commands.py weblate/checks/tests/test_judge.py
git commit -m "fix(judge): invalidate verdicts on explanation changes"
```

---

### Task 3: Document and verify the Judge contract

**Files:**

- Modify: `docs/changes.rst`
- Modify: `docs/guides/continuous-localization-loop.md`

**Step 1: Update documentation**

Document that the Judge receives both file-derived developer notes and producer-authored source explanations, and that editing either invalidates prior verdict identity. Add a concise entry to the current unreleased changelog section.

**Step 2: Run scoped lint and focused tests**

```bash
uv run prek run --files weblate/trans/judge.py weblate/trans/judge_loop.py weblate/trans/models/judge.py weblate/trans/judge_prompts/verdict.txt weblate/trans/tests/test_judge.py weblate/trans/tests/test_judge_client.py weblate/trans/tests/test_judge_loop.py weblate/trans/tests/test_judge_round.py weblate/trans/tests/test_judge_views.py weblate/trans/tests/test_judge_autotranslate.py weblate/trans/tests/test_commands.py weblate/checks/tests/test_judge.py docs/changes.rst docs/guides/continuous-localization-loop.md
```

Expected: PASS.

**Step 3: Commit**

```bash
git add docs/changes.rst docs/guides/continuous-localization-loop.md
git commit -m "docs(judge): document string explanation context"
```

NO UNRESOLVED DECISIONS
