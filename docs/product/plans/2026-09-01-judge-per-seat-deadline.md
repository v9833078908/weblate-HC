# Per-seat judge request deadlines implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bound each paid judge seat by its own measured absolute deadline without changing models, prompts, batch sizes, retry policy, or cached-verdict identity.

**Architecture:** Resolve a positive finite `request_deadline` into every immutable `JudgeSeatProfile`, inheriting the existing global deadline by default. Request readers, HTTP timeouts, retry-delay caps, audit snapshots, and deferred-retry lease margins consume that resolved value. A diagnostic production measurement chooses the explicit seat-2 value before the strict canary is repeated.

**Tech stack:** Python 3.14, Django settings, httpx2 streaming, Celery, pytest, Docker Compose.

**Design:** `docs/llm-first/designs/2026-09-01-01-judge-per-seat-deadline.md`.

**Status:** completed and deployed 2026-09-01; production canary and comparable measurement passed. Evidence: `docs/llm-first/measurements/2026-09-01-02-judge-seat-parallelism-production.md`.

---

## Task 1: Resolve and validate seat deadlines

**Files:**

- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/defaults.py`
- Modify: `weblate/trans/models/_conf.py`
- Modify: `weblate/settings_example.py`
- Modify: `weblate/settings_docker.py`
- Modify: `weblate/trans/judge.py`

### Step 1: Write failing tests

Add focused tests asserting:

```python
first, second = judge_seat_profiles()
self.assertEqual(first.request_deadline, 120)
self.assertEqual(second.request_deadline, 300)
```

Cover explicit per-seat values, `inherit`, numeric strings from Docker settings, and rejection of `0`, negative, infinite, Boolean, and non-numeric values before `http_mock` sees a request. Assert `judge_configuration_snapshot()` records `[120.0, 300.0]` while two profiles that differ only by deadline retain the same `profile_fingerprint`.

### Step 2: Verify RED

Run:

```text
uv run pytest weblate/trans/tests/test_judge_client.py -k "seat_deadline or deadline_snapshot"
```

Expected: failures because `JudgeSeatProfile.request_deadline` and the per-seat settings do not exist.

### Step 3: Implement minimal resolution

- Add `DEFAULT_JUDGE_REQUEST_DEADLINE_SEAT_1/2 = "inherit"`.
- Wire `JUDGE_REQUEST_DEADLINE_SEAT_1/2` through `_conf.py`, `settings_example.py`, and `settings_docker.py` as strings.
- Add `request_deadline: float` to `JudgeSeatProfile`.
- Resolve it with `_profile_value("REQUEST_DEADLINE", ...)`, coerce numeric strings, and reject non-positive/non-finite/Boolean values.
- Keep `request_deadline` out of `profile_fingerprint`; add it to `judge_configuration_snapshot()`.
- Preserve the legacy no-seat profile by resolving the global value.

### Step 4: Verify GREEN

Run the Task 1 selection and the existing profile/configuration tests.

### Step 5: Commit

```text
feat(judge): resolve per-seat request deadlines
```

## Task 2: Enforce the resolved deadline end to end

**Files:**

- Modify: `weblate/trans/tests/test_judge_client.py`
- Modify: `weblate/trans/judge.py`

### Step 1: Write failing tests

Use distinct seat deadlines and a controlled dripping response. Assert seat 1 stops at its short deadline while seat 2 can consume the same response under its longer deadline. Assert `_request_timeout(profile)` and an HTTP 429 `Retry-After` cap use `profile.request_deadline`, not the global setting.

### Step 2: Verify RED

Run the new tests. Expected: both seats still use `settings.JUDGE_REQUEST_DEADLINE`.

### Step 3: Implement minimal enforcement

Thread `profile.request_deadline` through `_read_body`, `_read_sse`, `_decode_non_stream`, `_request_timeout`, and the 429 retry-delay cap. Do not add a second clock or change idle-timeout behavior.

### Step 4: Verify GREEN

Run:

```text
uv run pytest weblate/trans/tests/test_judge_client.py::JudgeRequestDeadlineTest
```

Expected: all deadline tests pass.

### Step 5: Commit

```text
feat(judge): enforce each seat request deadline
```

## Task 3: Preserve deferred-retry lease safety

**Files:**

- Modify: `weblate/trans/tests/test_judge_deferrals.py`
- Modify: `weblate/trans/judge_loop.py`

### Step 1: Write failing tests

Configure seat 1 at 120 seconds and seat 2 at 300 seconds. Assert `_select_drain_requests` skips only a deferral whose own seat cannot fit its resolved deadline. Assert `_drain_seat` reserves that same seat deadline before allowing an in-run retry.

### Step 2: Verify RED

Run:

```text
uv run pytest weblate/trans/tests/test_judge_deferrals.py -k "seat_deadline"
```

Expected: the current global 120-second margin incorrectly admits seat 2.

### Step 3: Implement minimal lease margins

Replace both global deadline reads in the drain path with `profiles[seat].request_deadline` or the already selected `profile.request_deadline`. Do not alter claim durations, token buckets, or retry counts.

### Step 4: Verify GREEN

Run the new tests and the complete deferral test file.

### Step 5: Commit

```text
fix(judge): reserve seat deadlines in deferral leases
```

## Task 4: Publish the operator contract

**Files:**

- Modify: `deploy/environment.example`
- Modify: `dev-docker/docker-compose.yml`
- Modify: `docs/admin/config.rst`
- Modify: `docs/changes.rst`
- Modify: `docs/security/threat-model.rst` only if the documented absolute-bound property changes

### Step 1: Add explicit deployment values

Keep seat 1 at 120 seconds. Leave seat 2 at `inherit` until Task 5 produces evidence; then replace it with the measured value plus the documented margin. Keep the global value at 120 seconds.

### Step 2: Document behavior

Extend `JUDGE_REQUEST_DEADLINE` with the two environment overrides, inheritance, validation, and the fact that the resolved per-seat value remains an absolute bound. Add one concise unreleased changelog entry. The threat model needs no semantic change if it still states that every outbound request is absolutely bounded.

### Step 3: Verify

Run scoped `prek` on all changed Python, YAML, RST, and Markdown files. The existing unrelated `rst-bullet-stop` failure at `docs/changes.rst:43` remains separately reported; no new hook failure is accepted.

### Step 4: Commit

```text
docs(judge): document per-seat request deadlines
```

## Task 5: Measure the censored seat-2 tail

**Files:**

- Create: `analysis/probes/judge-seat-deadline-measurement.py`
- Create: `docs/llm-first/measurements/2026-09-01-judge-seat-deadline.md`

### Step 1: Build a non-persisting probe

Reuse production `build_request`, `_payload`, `_post_batch`, and `_parse_reply`. Select exactly 25 translated `col4/data/fr` units, force only the process-local diagnostic deadline to 300 seconds, send seat 2 one string at a time, and print status, failure kind, elapsed time, parser outcome, and aggregate percentiles. Never print source, target, prompt, response, headers, or credentials; persist no verdict, unit, attempt, or usage row.

### Step 2: Run once in production

Require empty active/reserved/scheduled judge queues. Record start UTC and the exact 25 IDs. Stop on auth, 429, reset, or parser failure rather than changing the probe.

### Step 3: Choose the deadline

Use the slowest successful observed request plus a 25% margin, rounded up to the next 30 seconds, capped at the 300-second diagnostic ceiling. If any request reaches 300 seconds, the measurement does not authorize a deadline; production remains blocked.

### Step 4: Record evidence

Write the exact sample, distribution, selected value, and limitations to the measurement file. Update Task 4 deployment values with the measured seat-2 deadline.

### Step 5: Commit

```text
docs(judge): record the seat deadline measurement
```

## Task 6: Deploy and repeat the strict production canary

**Files:**

- Modify: `docs/llm-first/plans/2026-08-27-judge-seat-parallelism.md`
- Modify: `docs/llm-first/measurements/2026-09-01-judge-seat-deadline.md`

### Step 1: Regression verification

Run the complete judge regression suite and scoped lint. The deadline, profile, deferral, fan-out, audit, and cancellation paths must all pass.

### Step 2: Deploy

Back up production `.env`, set only the two per-seat deadline keys and the already authorized model aliases, deploy the verified main commit, and confirm checkout/image health plus resolved profiles on the running container.

### Step 3: Select a fresh manifest

Choose exactly 25 translated units with no `JudgeVerdict`, record the query, operator, usage marker, and empty Celery task lists. Preview must report 25 matched/processed, zero remaining/writable, and 38 initial calls.

### Step 4: Run and verify

Launch once through the production UI. Require exactly 38 attempts, all HTTP 200 and parsed; 50 verdict rows across 25 units; zero unparsed; both configured models only; zero 403, 429, reset, or deadline failures; monotonic progress; overlapping seat windows; wall time no more than 75% of their summed spans.

### Step 5: Close the records

If the canary passes, record the measurement and unblock the comparable run. If it fails, stop immediately, leave production blocked, and record the exact failure without relaxing the threshold or rerunning opportunistically.

### Step 6: Commit and push

```text
docs(judge): record the production deadline canary
```
