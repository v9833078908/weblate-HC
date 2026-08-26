# LiteLLM judge seats in production, with OpenRouter as the fallback

**Date:** 2026-08-26. **Status:** approved in direction, not started. Stage A
and Stage D each need their own explicit go before they run.
**Rule:** R3 - changing the prompt or the model invalidates the measurement
(`docs/llm-first/vision/llm-first-product-architecture.md:674`).

## Goal

Run the judge seats on the corporate LiteLLM proxy, and fall back to the
current OpenRouter seats when LiteLLM cannot serve a batch. The fallback is an
availability mechanism, never a quality one: it must never be able to turn a
verdict we dislike into a second opinion.

## Why this is possible now

Three measurements, all on 2026-08-26:

- `docs/llm-first/measurements/2026-08-26-litellm-transport-reset-rate.md` -
  Stage 3's disqualification of every DeepSeek route was proxy load at that
  hour. `deepseek-v4-pro` answered today's judge payload, strict schema
  included, in 8.9 s, and reset 0 of 12 times.
- The same file - on the production path `deepseek-v4-pro` holds 12 of 12
  batches with reasoning **on** at 17-23 s, while `qwen3.8-max` fails 8 of 8
  with reasoning on and holds with reasoning off at 5.6 s.
- `9f081cd` - a dropped connection is now repeated once
  (:setting:`JUDGE_TRANSPORT_RETRIES`), so an unlucky batch no longer reads as
  a model failure.

The neighbouring `cathedral` localizer (`/home/dev01/localization` on
`hc-srv15-localizer`) has run DeepSeek + Qwen against the same host in
production since at least August, which is the existence proof that the proxy
can carry this workload.

## What is still unknown, and why Stage A exists

**No LiteLLM model has ever been scored against ground truth.** Every number
above is latency and parseability. Stage 3 never produced a scored candidate,
and its gate output is void for the reasons recorded in the measurement.

So this plan cannot name the seats. It selects them first, then promotes them.
A plan that hard-codes "DeepSeek + Qwen because cathedral uses them" would be
choosing by analogy, which R3 exists to prevent.

## Design decisions

### D1. Two complete endpoint configurations, not a list of URLs

An endpoint is not just a URL. It carries a key, its own seat models, and its
own reasoning value. Bolting a second URL onto the existing flat settings and
reusing everything else produces the bug in D6.

Primary keeps today's names, so no deployment has to be rewritten:
`JUDGE_BASE_URL`, `JUDGE_API_KEY`, `JUDGE_MODEL_SEAT_1`, `JUDGE_MODEL_SEAT_2`,
`JUDGE_REASONING_EFFORT`.

Fallback adds five, each with a `WEBLATE_` env twin in `settings_docker.py` and
a default in `weblate/trans/defaults.py`:

| setting | meaning |
|---|---|
| `JUDGE_FALLBACK_BASE_URL` | empty disables the fallback entirely |
| `JUDGE_FALLBACK_API_KEY` | separate credential, never shared with primary |
| `JUDGE_FALLBACK_MODEL_SEAT_1` | today's OpenRouter seat 1 |
| `JUDGE_FALLBACK_MODEL_SEAT_2` | today's OpenRouter seat 2 |
| `JUDGE_FALLBACK_REASONING_EFFORT` | the OpenRouter value, independent of D6 |

Empty `JUDGE_FALLBACK_BASE_URL` MUST reproduce today's behaviour exactly. That
is the migration story: an existing deployment that sets nothing keeps running
unchanged.

### D2. The fallback is per seat and per batch, and a verdict is never a blend

A batch that fails on the primary for seat 1 is re-sent to the fallback for
seat 1 only. Seat 2 is unaffected and keeps whichever endpoint served it.

Rejected alternatives:

- *Run-level failover.* An outage mid-run still loses the run, and the health
  probe that would decide it is one more thing that can be wrong.
- *Whole-collegium failover.* A single failing seat would discard a good
  verdict from the other seat and pay for it twice.

The invariant that must hold: one `JudgeVerdict` row is produced by exactly one
(endpoint, model) pair. A row is never assembled from two providers. The
collegium may mix providers across its two seats, and that is recorded.

### D3. Only unavailability triggers the fallback

Fall back on:

- a dropped connection after `JUDGE_TRANSPORT_RETRIES` is spent;
- `500`, `502`, `503`, `504`, `524`;
- `401`, `403` - for us an entitlement failure is unavailability. Our key
  returns `403` on `deepseek-ai/deepseek-v4-flash`, the route cathedral uses;
- `429` after its existing single retry.

Never fall back on:

- a `200` whose body fails `_parse_reply`. That is the model's behaviour, and
  retrying it elsewhere would launder a bad seat into a good verdict. It stays
  `unparsed`, exactly as today.
- a parsed verdict of any severity.

This line is the whole safety argument for the feature. It must be a test, not
a comment.

### D4. Provenance is recorded per verdict

`JudgeVerdict` stores `judge_model` but not the endpoint. Two providers can
expose the same model string, and then a stored verdict cannot be attributed.
Add `judge_provider` (`CharField`, blank allowed) with a migration, written
from `_judge_provider(base_url)`, and set it on every new row. Existing rows
keep it blank, which reads correctly as "before failover existed".

Without this, no post-hoc report can answer "which provider produced the
verdicts in this run", and R3 cannot be enforced on any future measurement.

### D5. Cache reuse is already safe; do not widen it

`_cached_verdict` (`weblate/trans/judge_loop.py:175-205`) requires both stored
rows to carry exactly the configured pair of model names. A fallback run writes
fallback model names, so it can never be reused while the primary pair is
configured, and vice versa. That is correct by construction.

The cost is that a fallback run's work is not reusable afterwards. Accept it.
Widening the match to "either pair" would let an OpenRouter verdict satisfy a
LiteLLM-configured run, which is precisely the R3 violation this plan is
trying to avoid.

### D6. The reasoning value is per endpoint, and today's code has a live bug here

`weblate/trans/judge.py:608-618` reads:

```python
if _judge_provider(base_url) == "litellm" and effort.strip() == "none":
    payload.update(_litellm_reasoning_disable_payload(model))
elif isinstance(effort, str) and effort.strip():
    payload["reasoning"] = {"effort": effort.strip(), "exclude": True}
```

With LiteLLM primary the operator must set `JUDGE_REASONING_EFFORT` to `""` or
`"none"`; `validate_request_settings` refuses anything else for a LiteLLM host.
If it is `"none"` and a batch then goes to an OpenRouter fallback, the `elif`
fires and sends `reasoning: {"effort": "none"}` to OpenRouter, which is not one
of its levels (`minimal`, `low`, `medium`, `high`).

**The fallback would send a malformed request on its first use.** Hence
`JUDGE_FALLBACK_REASONING_EFFORT`, and hence the effort must be resolved from
the endpoint being called, not from one global.

### D7. Per-seat reasoning is conditional, not planned

The measurement says DeepSeek wants reasoning on and Qwen wants it off. If
Stage A selects a pair that mixes the two modes, one global effort per endpoint
is not enough and per-seat effort is required.

Do not build it now. Build it only if Stage A returns a mixed pair, and if it
does, extend the seat vocabulary already in use rather than inventing a second
one: `JUDGE_REASONING_EFFORT_SEAT_1` / `_SEAT_2`, each falling back to the
endpoint's value when empty.

## Stages

### Stage A: select the LiteLLM seats - needs an explicit go, spends money

Nothing is promoted before this. Score candidates against the sealed corpora
with the existing harness, not with new probes.

1. Candidate pool: the routes that held the production prompt, which today
   means `deepseek-v4-pro` with reasoning on, `qwen3.8-max` with reasoning off,
   and any route from
   `docs/llm-first/measurements/2026-08-26-litellm-stage0-compatibility.md`
   that a fresh compatibility pass still admits. Re-run compatibility first:
   the proxy's behaviour is load-dependent and the Stage 0 sample is old.
2. Corpora: the sealed `analysis/data/st2-zh-*` set and the frozen
   `analysis/data/nfg-ui-fr-golden.json` built at `c7b59c0`.
3. Score with `analysis/probes/st2-zh-score.py`, using the split gate added in
   `aae1397`: `missed_defect` and `severity_miscal` are separate numbers, and a
   candidate is judged on recall first.
4. Search the pair offline from stored verdicts. The objective is union recall
   across the two seats, not the best individual score.
5. Record the run in `docs/llm-first/measurements/`, dated, with the model IDs,
   the reasoning mode per model, and the batch width.

**Acceptance:** a named pair with a recall number per corpus, and an explicit
comparison against the incumbent OpenRouter pair measured under the same gate.
If no LiteLLM pair matches the incumbent, stop here and report that: the
fallback work below is then not worth doing, because the primary would be
worse than the thing it displaces.

### Stage B: implement the failover

Only after Stage A names a pair.

**B1. Endpoint resolution.** Introduce a small value object - an endpoint
carrying `base_url`, `api_key`, `reasoning_effort`, and its two seat models -
built from settings. `get_judge_base_url`, `_judge_provider`,
`_judge_provider_profile` and the reasoning branch all take the endpoint
instead of reading globals. This is the change that fixes D6.

**B2. Validation.** `judge_configuration_ready` and
`validate_request_settings` validate the primary as today, and validate the
fallback only when `JUDGE_FALLBACK_BASE_URL` is non-empty. A malformed fallback
is a configuration error surfaced before any paid request, exactly like a
malformed primary. An empty fallback is not an error.

**B3. Failover in `request_verdicts`.** After the primary's existing retry
budget is exhausted with a D3 trigger, re-send the same batch once to the
fallback endpoint with the fallback's seat model for that seat. One fallback
attempt per batch per seat; no budget beyond that. Log the switch with the
model, the trigger, and the batch position.

**B4. Provenance.** Thread the serving endpoint's provider into the
`JudgeVerdict` write (D4), with the migration.

**B5. Reporting.** Extend `check_judge_repair_routes` - or add a sibling
command if that one's scope does not fit - to preflight both endpoints and
print, per endpoint: reachable, key accepted, each seat model admitted by
`_parse_reply` on a one-batch probe. This is what an operator runs before a
production run, and what diagnoses a failover storm afterwards.

### Stage C: verification

**C1. Tests**, in `weblate/trans/tests/test_judge_client.py` alongside the
transport-retry tests:

- an empty fallback reproduces today's behaviour, including call counts;
- each D3 trigger causes exactly one fallback call with the fallback model;
- a `200` that fails `_parse_reply` causes **no** fallback call and yields
  `unparsed` - the D3 safety line;
- the fallback's reasoning effort is used, and `"none"` never reaches an
  OpenRouter payload - the D6 regression;
- a fallback failure after a primary failure yields `unparsed`, not an
  exception, and does not abort the run (invariant D5 in the judge docstring);
- provenance is stored for both the primary and the fallback path.

Every test must be shown to fail against the unmodified code before it is
trusted. Two of the four transport-retry tests were written as guards and pass
in both states; that is fine when it is deliberate and stated.

**C2. Live smoke**, with `analysis/probes/litellm-retry-smoke.py` extended, or a
sibling probe: one run against a deliberately wrong `JUDGE_MODEL_SEAT_1` on the
primary so the fallback fires for real, and one healthy run where it must not
fire. The control matters as much as the test.

**C3. Full judge suites.** Note the four pre-existing
`TransactionManagementError` failures in `JudgeUsageLogTest` and
`JudgeLiteLLMPayloadTest`; they fail identically on a clean checkout and are
not caused by this work.

### Stage D: production rollout - separate approval, per AGENTS.md

Deployment is out of scope for the implementation session. When it is
approved:

1. Set the fallback settings to today's OpenRouter values **first**, deploy,
   and confirm nothing changed: the primary is still OpenRouter, the fallback
   is configured but idle.
2. Only then repoint the primary to LiteLLM with the Stage A pair.
3. Watch the failover log rate for a full run. A high rate means the primary is
   not ready and the correct action is to revert the primary, not to widen the
   retry budgets.

Sequencing it this way means the risky change is one setting, and reverting it
is one setting.

## Out of scope

- Streaming. Cathedral does not stream either; the ceiling is reachable without
  it, and adding it would invalidate every measurement under R3.
- Changing `JUDGE_BATCH_SIZE`. It is a measured parameter; moving it voids the
  incumbent numbers this plan compares against.
- Any change to the prompt, the schema, or the severity rubric.
- A third endpoint, or provider weighting, or load balancing. This is a
  fallback, not a router.
- Asking for entitlement to `deepseek-ai/deepseek-v4-flash`. Record the `403`
  and move on unless Stage A wants that specific route.

## Open questions

1. **Does the fallback need its own batch width?** If the OpenRouter pair was
   measured at a different `JUDGE_BATCH_SIZE` than the LiteLLM pair needs, the
   fallback silently changes a measured parameter. Check during Stage A; if the
   widths differ, the fallback needs its own setting and D1 grows by one row.
2. **Should a failover be visible to the user, or only to the operator?** The
   verdict is equally valid, so the argument for surfacing it is auditability
   rather than trust. Defer until Stage B has the provenance field.
3. `weblate/trans/models/_conf.py:103-112` lists most `JUDGE_*` defaults but
   not `JUDGE_REQUEST_DEADLINE`, `JUDGE_REASONING_EFFORT`, or the new
   `JUDGE_TRANSPORT_RETRIES`. Harmless today because the code reads them
   defensively, but the file is drifting. Worth one tidy-up commit, separate
   from this work.

## Files this will touch

- `weblate/trans/judge.py` - endpoint object, effort resolution, failover.
- `weblate/trans/judge_loop.py` - provenance on the verdict write.
- `weblate/trans/models/judge.py` + a migration - `judge_provider`.
- `weblate/trans/defaults.py`, `weblate/settings_example.py`,
  `weblate/settings_docker.py` - the five fallback settings.
- `weblate/trans/management/commands/` - the preflight command.
- `weblate/trans/tests/test_judge_client.py` - Stage C1.
- `docs/admin/config.rst`, `docs/changes.rst` - settings and changelog.
- `deploy/environment.example` - the new `WEBLATE_JUDGE_FALLBACK_*` names.
