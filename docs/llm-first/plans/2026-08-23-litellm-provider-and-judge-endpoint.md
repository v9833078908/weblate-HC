# LiteLLM machinery provider and configurable judge endpoint

**Date:** 2026-08-23. **Status:** Tasks 1-6 implemented, committed, and pushed
on `feat/litellm-provider-and-judge-endpoint` (not yet merged to `main`).
Task 7 (development deployment and live proxy preflight) and the production
rollout remain pending separate explicit approval.
**Realizes:** the phase-4 roadmap item "Переезд судьи на корпоративный
LiteLLM-прокси" (`docs/llm-first/vision/llm-first-product-architecture.md:660-676`)
plus a second routed machinery provider so machine translation can also run
through the corporate proxy.

## Goal

Both LLM surfaces of this fork — the `RoutedLLMTranslation` machinery and the
LLM judge — can be pointed at the corporate LiteLLM proxy
(`https://hcbifrost.herocraft.com/litellm/v1`) instead of OpenRouter:

1. A new **LiteLLM** automatic-suggestion service (slug `litellm`) appears on
   `/machinery/<project>/` next to **OpenRouter** and is configurable per
   project, with its own dedicated key.
2. The judge endpoint becomes operator configuration
   (`WEBLATE_JUDGE_BASE_URL`, default OpenRouter) instead of a hardcoded URL.
3. The judge repair loop, the judge prompt's project context, and the
   `AutoForm` preselection resolve the routed engine per project instead of
   hardcoding `"openrouter"`, so a project moved fully to LiteLLM keeps its
   repair contour and persona/style context.

Changing the judge **seat models** is explicitly out of scope: rule R3
(prompt or model change voids the measurement) gates that behind a separate
eval on the S&T2 corpus.

Moving the endpoint proceeds only after a compatibility preflight with the
dedicated Weblate key proves that both unchanged seat model IDs accept the
exact LiteLLM payload and return a locally parseable verdict. The same route
string is not evidence that a proxy resolves to the same deployed model; record
the provider-returned model identity when available. If either preflight
fails, stop and schedule an R3 eval instead of substituting a model.

## Context (verified)

- `weblate_customization/src/weblate_customization/machinery.py` —
  `RoutedLLMTranslation` (`name = "OpenRouter"`, slug `openrouter`): OpenAI
  chat-completions payload, one key + `base_url`
  (default `https://openrouter.ai/api/v1`), per-language `routing` JSON with
  `"*"` fallback, `trusted_error_hosts = {"openrouter.ai"}`.
  `get_chat_payload` adds a strict `json_schema` `response_format` **and the
  OpenRouter-only `provider.require_parameters: true` block**.
- `weblate/machinery/llm.py:316-341` — `BaseLLMTranslation`:
  `request_timeout = 120`, `batch_size = 10`, `batch_concurrency = 2`.
- `weblate/trans/judge.py:39,382-398` — the judge POSTs to the constant
  `OPENROUTER_CHAT_COMPLETIONS_URL` with `settings.JUDGE_OPENROUTER_KEY`;
  `JUDGE_REQUEST_TIMEOUT = 120`.
- `weblate/trans/judge_loop.py:97-100` (repair engine), `judge_loop.py:129`
  (persona/style for the judge prompt), `weblate/trans/judge_workflow.py:35`
  (`repair_route`) — all key on `AutoForm.DEFAULT_ENGINE = "openrouter"`
  (`weblate/trans/forms.py:1194`, preselection at `forms.py:1329-1334`).
  If a project drops its `openrouter` configuration, repair silently
  disables and the project context disappears from the judge prompt.
- `weblate/trans/models/project.py:1332-1351` — `get_machinery_settings()`
  merges site-wide `Setting` rows (category MT, keyed by service slug) with
  project `machinery_settings` field by field; a project-level `None`
  removes a globally installed service for that project.
- Corporate proxy contract, probed live in game_pulse
  (`game_pulse_saas/docs/LiteLLM/00-architecture-and-roadmap.md` §1.4,
  `backend/app/ai_pipeline/llm_client.py:619-651`):
  - hard ~60 s gateway timeout (nginx 504);
  - `response_format` is passed through to the model, **not enforced**;
  - budget/quota failures are string markers across varying HTTP statuses:
    `no healthy deployments`, `ExceededTokenBudget`, `budget has been
    exceeded`, `exceeded budget`, `allocated quota exceeded`;
  - reasoning is on by default for most roster models and arrives in
    `reasoning_content` (the `content` field stays clean);
  - the model roster churns — route values must be checked against the live
    proxy, never a table;
  - the wire payload `usage: {"include": true}` causes HTTP 500; the current
    judge still sends it, so this change removes it. OpenRouter now returns
    usage automatically, so no accounting data is lost;
  - provider identity is resolved from the parsed hostname, never a URL
    substring; OpenRouter-only request fields are omitted for LiteLLM;
  - a non-empty `JUDGE_REASONING_EFFORT` sends a `reasoning` field today, but
    that field is not universally supported by the corporate roster. LiteLLM
    migration therefore requires it to remain empty until an R3 eval defines a
    measured compatible contract.
- A dedicated LiteLLM key for Weblate will be provided by the operator
  (separate from the game_pulse prod key `LLM_API_KEY`).

## Design

### 1. MT provider `RoutedLiteLLMTranslation`

New class in `weblate_customization/src/weblate_customization/machinery.py`,
subclassing `RoutedLLMTranslation`:

- `name = "LiteLLM"` → slug `litellm`, its own `Setting` row and its own
  per-project override, fully independent from the `openrouter` service.
- Default `base_url`: `https://hcbifrost.herocraft.com/litellm/v1`
  (module constant `LITELLM_DEFAULT_BASE_URL`); overriding
  `get_runtime_base_url()`.
- `trusted_error_hosts = {"hcbifrost.herocraft.com"}` — proxy error details
  (`no healthy deployments`, `ExceededTokenBudget`, …) stay visible in the
  UI as safe error messages.
- `request_timeout = 55` — below the proxy's hard 60 s gateway 504
  (the OpenRouter class keeps 120). This caps one transport attempt, not the
  whole translation job: inherited retries remain unchanged and their
  429/503 behavior is measured before rollout rather than retuned here.
- `get_chat_payload()` keeps the `json_schema` `response_format` (the proxy
  passes it through; a model that ignores it produces a reply the existing
  parser rejects — a visible failure, not silent corruption) and removes the
  OpenRouter-only `provider` key with `pop("provider", None)`.
- `RoutedLiteLLMMachineryForm(RoutedLLMMachineryForm)` only overrides the
  `routing` help text ("model IDs available on the LiteLLM proxy" instead of
  "OpenRouter model IDs"). `validate_settings()` tests one selected route; it
  is a connectivity check, not proof that every map entry works. Before a
  project is enabled, the development preflight tests every unique route model
  actually used by that project's target languages with the final
  `temperature = 0` and schema payload.
- `batch_size = 10` and `batch_concurrency = 2` are inherited; tuning for
  reasoning-heavy models happens later, from data only.

Rejected alternatives: reusing the `openrouter` service with a per-project
`base_url` override (misleading name, wrong trusted host, 120 s timeout over
a 60 s gateway, OpenRouter `provider` block sent to LiteLLM, no
coexistence); a provider-profile switch inside one service (breaks the
one-slug-one-Setting model).

### 2. Judge: configurable endpoint

Exactly the phase-4 minimal increment:

- New setting `JUDGE_BASE_URL`, default `https://openrouter.ai/api/v1`:
  `weblate/trans/defaults.py`, `weblate/trans/models/_conf.py`,
  `weblate/settings_example.py`, `weblate/settings_docker.py`
  (`WEBLATE_JUDGE_BASE_URL`).
- A small `judge.py` helper validates that the base URL is a non-empty absolute
  HTTPS URL, builds `{JUDGE_BASE_URL}/chat/completions` with the existing
  rstrip-join, and is used by both the readiness gate and `_post_batch`.
  Invalid configuration raises `JudgeError` before a network call.
  `_post_batch` keeps `stream_validated_url` and `follow_redirects=False`, so
  runtime public-target validation is not weakened.
- The common judge payload contains only `model`, `stream`, `response_format`,
  and `messages`. `usage` is removed for every endpoint: OpenRouter returns
  usage automatically and LiteLLM rejects the field. The existing response
  usage logger remains unchanged.
- A parsed-hostname provider profile adds `provider.require_parameters` only
  for `openrouter.ai` (and its subdomains). LiteLLM and unknown operator
  endpoints receive no OpenRouter-only field; hostname matching never uses a
  free substring.
- LiteLLM with non-empty `JUDGE_REASONING_EFFORT` fails the configuration gate
  before a paid request. The implementation must not silently drop or
  translate the setting, because that would change a measured judge contract.
- **Key rename** `JUDGE_OPENROUTER_KEY` → `JUDGE_API_KEY`
  (`WEBLATE_JUDGE_OPENROUTER_KEY` → `WEBLATE_JUDGE_API_KEY`): clean cutover,
  no fallback shim. The old name lies as soon as the endpoint is not
  OpenRouter. All code, test, docs, and environment-template references are
  updated in the same change; the production env var is renamed at deploy
  time (deployment is gated separately anyway).
- `JUDGE_REQUEST_TIMEOUT` stays 120. A LiteLLM gateway 504 remains an
  existing unparsed batch; the observed ~60 s gateway limit is a deployment
  acceptance criterion, not a new application timeout contract.
- The endpoint remains **operator** configuration (env), never user-facing —
  matching the threat-model stance.

### 3. Routed-engine resolution for the judge and AutoForm

The hardcoded `"openrouter"` becomes an ordered policy
`ROUTED_ENGINES = ("openrouter", "litellm")`.

- Two narrow helpers live next to `AutoForm` in `weblate/trans/forms.py`:
  one chooses the first engine with a non-empty project settings mapping, and
  one chooses the first engine present in a registry-filtered engine-id set.
  They share the tuple but do not treat unlike input shapes as interchangeable.
- `judge_loop.py:97-100` (repair candidate) and `judge_loop.py:129`
  (persona/style in the judge prompt) use the configured-engine helper.
  `judge_workflow.py:repair_route` uses it too and keeps naming the chosen
  engine in errors. `AutoForm` uses the available-engine helper, then retains
  its `weblate` TM fallback.
- This is an explicit **project-wide** preference, not per-language failover.
  If the preferred engine lacks a key, target-language route, or registration,
  repair follows its existing no-repair/error path and never silently tries the
  other provider. Moving a project to LiteLLM therefore means configuring
  `litellm` and disabling `openrouter` at project level (`None`).

When both engines are configured, `openrouter` wins: behavior of the eight
production projects does not change.

### 4. Registration and deploy wiring

- `WEBLATE_ADD_MACHINERY` gains
  `,weblate_customization.machinery.RoutedLiteLLMTranslation` in
  `dev-docker/docker-compose.yml` and `deploy/environment.example`.
- Copying `weblate_customization` into `dev-docker/data/python/`, rebuilding
  the shared dev stack, and live proxy requests are a separate development
  deployment, not ordinary implementation work; see Task 7.
- Production enablement on col4 is a separate, explicitly approved deploy:
  env changes + site-wide `litellm` configuration + `/machinery/col4/`.

### 5. Errors and constraints

- No special budget/quota handling is added: `MachineTranslationError`
  surfaces the proxy message (trusted host), and a failed judge batch is
  already recorded as unparsed. The game_pulse string markers above are the
  operator's diagnostic vocabulary, recorded here.
- `docs/security/threat-model.rst` currently describes the judge request as
  going to "the fixed provider host" (`threat-model.rst:241-251`, also
  114-121, 920-925, 980-984). It is updated to describe an
  operator-configured provider host (:setting:`JUDGE_BASE_URL`, never
  user-configurable), while preserving the documented runtime public-target
  validation and disabled redirects.
- The loc-kit OpenRouter client (`weblate/trans/loc_kit.py`) is untouched.

## Out of scope

- Changing judge seat models or any model choice (eval-gated, R3).
- Failover/spend telemetry à la game_pulse plans 03/04 — Weblate's
  machinery retry/rate-limit semantics and the judge's unparsed-batch
  semantics already cover the failure surface we need.
- `batch_size`/concurrency tuning for the corporate proxy.
- Compatibility beyond OpenRouter and the corporate LiteLLM proxy; a new
  provider-specific payload profile requires its own design and measurement.
- Production deployment and col4 configuration (separate approval).

## Tasks

### Task 1: `RoutedLiteLLMTranslation`

`weblate_customization/src/weblate_customization/machinery.py`:

1. Add `LITELLM_DEFAULT_BASE_URL = "https://hcbifrost.herocraft.com/litellm/v1"`.
2. Add `RoutedLiteLLMMachineryForm(RoutedLLMMachineryForm)` overriding only
   the `routing` field help text.
3. Add `RoutedLiteLLMTranslation(RoutedLLMTranslation)` with `name`,
   `settings_form`, `trusted_error_hosts`, `request_timeout = 55`,
   `get_runtime_base_url()`, and `get_chat_payload()` that calls `super()` and
   removes `provider` with `pop("provider", None)`.

### Task 2: machinery tests

`weblate_customization/tests/test_machinery.py`, mirroring the existing
structure (mock URL becomes the hcbifrost chat-completions URL for the new
class): slug/registration, default `base_url`, payload carries
`response_format` without `provider`, malformed content does not make the
safe `pop` fail, `request_timeout == 55`, and one inherited routing smoke.

### Task 3: judge endpoint, payload profile, and key rename

1. `defaults.py`: add `DEFAULT_JUDGE_BASE_URL`; rename
   `DEFAULT_JUDGE_OPENROUTER_KEY` → `DEFAULT_JUDGE_API_KEY`.
2. `models/_conf.py`, `settings_example.py`, `settings_docker.py`: add
   `JUDGE_BASE_URL` / `WEBLATE_JUDGE_BASE_URL`; rename the key setting and
   env var.
3. `judge.py`: add the validated chat-completions URL helper and parsed-hostname
   provider profile; rename every `JUDGE_OPENROUTER_KEY` use. Remove `usage`
   from every request, retain `provider.require_parameters` only for
   OpenRouter, and reject non-empty `JUDGE_REASONING_EFFORT` for LiteLLM before
   a request. Drop the old endpoint constant.
4. Update `test_judge_client.py`, `test_judge_loop.py`,
   `test_judge_form.py`, and `test_judge_autotranslate.py`: rename overrides;
   assert the default OpenRouter URL and profile; assert the LiteLLM URL has
   neither `usage` nor `provider`; assert response usage is still logged;
   cover trailing slashes, blank/malformed base URLs with zero HTTP calls, and
   the LiteLLM reasoning-effort gate.
5. Update `deploy/environment.example`, `dev-docker/environment.example`, and
   docs: `docs/admin/config.rst` (rename the setting section, add
   `JUDGE_BASE_URL` with `.. versionadded::`), `docs/admin/install/docker.rst`
   (envvar list), `docs/admin/checks.rst` mention, and
   `docs/security/threat-model.rst` per design §5. Rename ignored local
   `dev-docker/environment` and compose-override variables manually when
   testing; never print, add, or commit their credentials.

### Task 4: routed-engine resolution

1. `weblate/trans/forms.py`: replace `AutoForm.DEFAULT_ENGINE` with
   `ROUTED_ENGINES = ("openrouter", "litellm")` and two explicit helpers for
   configured settings maps and available engine-id sets. Use the latter for
   preselection at `forms.py:1329-1334`.
2. `judge_loop.py:97-100,129` and `judge_workflow.py:32-57`: use the
   configured-engine helper. Do not add per-language fallback from OpenRouter
   to LiteLLM.
3. Tests: repair and prompt-context resolution for openrouter-only /
   litellm-only / both (OpenRouter wins) / neither; a preferred engine without
   a key, target-language route, or registration must not fall through to
   LiteLLM. Cover `check_judge_repair_routes` with a litellm-only project
   (`weblate/trans/tests/test_commands.py:471-495` pattern) and AutoForm
   preselection for the four ordinary cases.
4. Regression discipline: after writing each new test, revert the code
   change once and confirm the test fails (memory: two of three earlier
   judge-UI tests passed against the bug on first write).

### Task 5: source wiring and documentation

1. `dev-docker/docker-compose.yml` and `deploy/environment.example`: extend
   `WEBLATE_ADD_MACHINERY`.
2. `docs/changes.rst` (top, unreleased): one entry for the LiteLLM suggestion
   service and configurable judge endpoint.
3. `AGENTS.md`: extend the `weblate_customization/` description and the
   "Deploying custom checks and machinery" registration line.
4. Do not alter dated historical plans merely because they retain the old
   setting name. Update the phase-4 vision only after Task 7 succeeds.

### Task 6: automated verification

1. Run `uv run pytest weblate_customization/tests/test_machinery.py` and the
   judge/forms suites (`weblate/trans/tests/test_judge_client.py`,
   `test_judge_loop.py`, `test_judge_form.py`, `test_judge_autotranslate.py`,
   `test_commands.py`) — host or `./rundev.sh test`, whichever the current
   container memory pressure allows (check `docker stats --no-stream` before
   blaming failures).
2. Run `uv run prek run --all-files`; run mypy with the repository command.
3. After successful automated verification, commit and push the implementation
   and its documentation with a Conventional Commit. Do not mark the endpoint
   increment implemented in the phase-4 vision until Task 7 succeeds.

### Task 7: development deployment and proxy preflight (separate approval)

Prerequisite: explicit approval to change the shared dev instance. Without it,
stop after Task 6.

1. Copy `weblate_customization` into `dev-docker/data/python/`, then rebuild
   with `WEBLATE_PORT=3001 ./rundev.sh`; do not use a bare restart for the new
   machinery environment.
2. Configure the site-wide `litellm` service with the dedicated key. For every
   unique route model used by the target project's enabled languages, send a
   real suggestion request and record whether it accepts `temperature = 0`,
   the schema payload, and the observed latency.
3. Point `WEBLATE_JUDGE_BASE_URL` at LiteLLM with the dev judge key. Execute
   the exact LiteLLM judge payload once for each configured seat model, then
   run one end-to-end two-seat judge batch. Both replies must pass local strict
   parsing; record latency, proxy status on failure, and returned model
   identity when present. `JUDGE_REASONING_EFFORT` remains empty.
4. If any route or seat fails, stop. Do not substitute a model or weaken the
   schema; record the result and start the R3 eval path.
5. Only after success, mark the phase-4 endpoint increment implemented in
   `docs/llm-first/vision/llm-first-product-architecture.md` and commit/push
   that status update separately. Task 7 never holds the implementation commit
   from Task 6 hostage.

### Deployment (separate approval, not part of this plan's execution)

After the Task 7 preflight succeeds and production deployment is explicitly
approved, rename `WEBLATE_JUDGE_OPENROUTER_KEY` →
`WEBLATE_JUDGE_API_KEY` in the production environment, add the machinery
registration, deploy via `deploy/vps.sh`, configure `litellm` site-wide with
the dedicated key, then configure `/machinery/col4/` (enable `litellm` and
disable `openrouter` per project to switch the repair contour). Repeat the
two-seat compatibility check if production credentials or routing differ from
development.
