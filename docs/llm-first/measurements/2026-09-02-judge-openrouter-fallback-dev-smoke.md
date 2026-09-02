# Forced OpenRouter fallback smoke on the dev container

Date: 2026-09-02
Scope: `dev-docker` container, translation 182 (component `data`, language `fr`),
first two translated units, both seats.
Probe: `analysis/probes/judge-fallback-forced-smoke.py`, run through
`docker compose exec -T ... weblate shell`.
Code: `main` at `0191a7c` (fallback implementation merged as `382fd51`).

This is Task 7 evidence for
`docs/llm-first/plans/2026-09-01-02-judge-openrouter-availability-fallback.md`.
It is a dev-container measurement only. No Rollout step was performed: nothing
in the running dev environment block was changed, and production was not
touched beyond a read-only fetch of two existing credentials.

## Configuration

| Role | Endpoint | Seat 1 model | Seat 2 model |
|---|---|---|---|
| Primary | `https://hcbifrost.herocraft.com/litellm/v1` | `deepseek-v4-pro` | `atlas/qwen3.8-max` |
| Fallback | `https://openrouter.ai/api/v1` | `deepseek/deepseek-v4-pro` | `qwen/qwen3-235b-a22b-2507` |

The primary is the configuration production currently runs. The fallback is the
historical OpenRouter endpoint, key and pair the plan's Rollout designates,
recovered from `deploy/.env.bak-20260901T055524Z-judge-seat-canary` on the
production host. Both credentials were passed to the probe process through
`docker compose exec -e` and never written to disk, and the fallback settings
exist only for the duration of the probe: the dev environment block still
carries eight blank `WEBLATE_JUDGE_FALLBACK_*` entries.

## Results

| Arm | Expected | Observed | Verdict |
|---|---|---|---|
| 3. Healthy control, no fallback configured | both seats parsed on the primary, zero fallback attempts | seat 1 and seat 2 parsed on `litellm`, zero `openrouter` attempts | pass |
| 1. Forced primary `http-auth` | one primary attempt then exactly one fallback attempt per batch per seat, parsed | seat 1: `litellm` 401 then `openrouter` 200 parsed. Seat 2 (two batches): each batch `litellm` 401 then `openrouter` 200 parsed. Result provenance `openrouter` on both seats | pass |
| 3b. Fallback configured but idle | a configured fallback must not change a healthy run | both seats parsed on `litellm`, zero `openrouter` attempts | pass |
| 2. Forced primary non-availability failure, must not fail over | one attempt, no fallback | the proxy answered the unknown model with **403**, not a 4xx outside 401/403/429, so the failure classified as `http-auth` and correctly did fail over | inconclusive, see below |

## Arm 2 did not test what it intended

The arm forces a non-availability failure by pointing seat 1 at
`model-that-does-not-exist-on-this-endpoint`. Against this LiteLLM proxy an
unknown model returns **HTTP 403**, which `_failure_for_http` maps to
`http-auth` - a deliberately permitted failover trigger, since an entitlement
refusal at one provider is exactly the case a second provider is meant to
cover. The observed failover is therefore correct behaviour, not a defect, but
the arm produced no evidence about non-availability kinds.

The probe's premise is what is wrong: it assumes an unknown model yields
`http-other`. That assumption came from the 2026-09-01 incident, where LiteLLM
model names reached the default OpenRouter endpoint and produced `http-other`.
The reverse direction, an unknown name on the LiteLLM proxy, answers 403
instead.

The negative contract is covered by unit tests rather than this arm:
`JudgeFailoverTest.test_every_failure_kind_fails_over_only_when_it_is_an_availability_kind`
enumerates every member of the closed `FAILURE_KINDS` set and asserts one
fallback attempt for exactly the five permitted kinds and zero for all others,
including `http-other` and `http-request-invalid`. To measure it live, the arm
needs a primary that returns a 4xx outside 401/403/429 - for example a
deliberately malformed request body - which is a probe change, not a code
change.

## Ledger invariants

Attempt rows over the probe window, by provider and outcome:

| Provider | Failure kind | Status | Parsed | Rows |
|---|---|---:|---|---:|
| `litellm` | - | 200 | yes | 6 |
| `litellm` | `http-auth` | 401 | no | 6 |
| `litellm` | `http-auth` | 403 | no | 7 |
| `openrouter` | - | 200 | yes | 10 |
| `openrouter` | `http-request-invalid` | 400 | no | 1 |

Usage rows over the same window: `litellm` 6, `openrouter` 10 - exactly one per
delivered response, matching the parsed attempt counts, with no row for any of
the 13 failed attempts. This confirms the plan's no-double-counting
requirement: the fallback attempt is recorded once, against the provider that
actually served it, and never billed twice.

The single `openrouter` `http-request-invalid` row is from the discarded first
run described below.

Side effects, all verified after the run:

- `JudgeVerdict` rows written in the window: **0**.
- `JudgeDeferral` rows created in the window: **0**.
- The two judged units: `last_updated` unchanged, **0** `Change` rows.
- All 650 pre-existing verdicts still carry a blank `judge_provider`,
  consistent with "blank means written before the field existed".

## A first run was discarded

The first attempt supplied `LITELLM_API_KEY` from `deploy/.env.local` as the
primary credential. The proxy answered 401/403 for every primary request, so
the healthy control was not healthy and arms 2, 3 and 3b were invalid. Only
arm 1's seat 1 was meaningful there, and it already showed the failover
working.

The cause is that the judge uses its own LiteLLM virtual key
(`WEBLATE_JUDGE_API_KEY` in the production environment), not the general
`LITELLM_API_KEY`. The run above uses the correct key. The discarded run's rows
are included in the ledger counts above because both runs fall inside the same
window.

## What this does and does not establish

Established live, against real endpoints:

- A primary availability failure is followed by exactly one fallback attempt
  per batch per seat, and the fallback completes the batch.
- Provenance is recorded on the result: a fallback-served result carries
  `served_provider=openrouter` and the fallback model.
- Configuring a fallback does not change a healthy run's call count or
  provider.
- Usage accounting attributes each delivered response to the provider that
  served it, exactly once.
- The probe does not mutate translation content, verdicts or the deferral
  queue.

Not established live:

- The non-availability negative case (arm 2), for the reason above. Covered by
  the unit-test matrix.
- Anything about production. Production still runs with all eight
  `JUDGE_FALLBACK_*` settings empty, and every Rollout step continues to need
  its own explicit approval per `AGENTS.md`.
