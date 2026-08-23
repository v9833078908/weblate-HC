# LiteLLM machinery provider and configurable judge endpoint

**Date:** 2026-08-23. **Status:** approved design, implementation pending.
**Realizes:** the phase-4 roadmap item "Переезд судьи на корпоративный
LiteLLM-прокси" (`docs/LLM-first/llm-first-product-architecture.md:660-676`)
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
  - `extra_body.usage.include` causes HTTP 500 (we never send it);
  - provider identity must be resolved from the parsed hostname.
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
  (the OpenRouter class keeps 120).
- `get_chat_payload()` keeps the `json_schema` `response_format` (the proxy
  passes it through; a model that ignores it produces a reply the existing
  parser rejects — a visible failure, not silent corruption) and **removes
  the OpenRouter-only `provider` block**.
- `RoutedLiteLLMMachineryForm(RoutedLLMMachineryForm)` only overrides the
  `routing` help text ("model IDs available on the LiteLLM proxy" instead of
  "OpenRouter model IDs"). Routing values must exist on the live proxy;
  `validate_settings()` already performs a live test chat request, which is
  exactly that check.
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
- `judge.py:_post_batch` builds `{JUDGE_BASE_URL}/chat/completions` (same
  rstrip-join as `weblate/machinery/openai.py:join_api_url`) instead of the
  hardcoded constant.
- **Key rename** `JUDGE_OPENROUTER_KEY` → `JUDGE_API_KEY`
  (`WEBLATE_JUDGE_OPENROUTER_KEY` → `WEBLATE_JUDGE_API_KEY`): clean cutover,
  no fallback shim. The old name lies as soon as the endpoint is not
  OpenRouter. All code, test, docs, and environment-template references are
  updated in the same change; the production env var is renamed at deploy
  time (deployment is gated separately anyway).
- `JUDGE_REQUEST_TIMEOUT` stays 120 — behind the corporate gateway the
  effective ceiling is 60 s; no extra knob (YAGNI). A 504 lands in the
  existing "batch unparsed" semantics.
- The endpoint remains **operator** configuration (env), never user-facing —
  matching the threat-model stance.

### 3. Routed-engine resolution for the judge and AutoForm

The hardcoded `"openrouter"` in three places becomes a deterministic pick
from an ordered tuple `("openrouter", "litellm")` — the first engine that
has a configuration:

- `judge_loop.py:97-100` (repair candidate) and `judge_loop.py:129`
  (persona/style in the judge prompt) — resolved against
  `project.get_machinery_settings()`.
- `judge_workflow.py:repair_route` — same resolution; error messages keep
  naming the engine actually chosen (or state that none is configured).
- `AutoForm` — preselects the first tuple engine present in the available
  engine ids, then the existing fallback to the `weblate` TM.

The tuple and a small resolver helper live next to `AutoForm` in
`weblate/trans/forms.py` (both judge modules already import `AutoForm`).
When both engines are configured, `openrouter` wins: behavior of the eight
production projects does not change. Moving a project to LiteLLM =
configure `litellm` + disable `openrouter` at project level (`None`) on
`/machinery/<project>/` — an explicit operator action.

### 4. Registration and deploy wiring

- `WEBLATE_ADD_MACHINERY` gains
  `,weblate_customization.machinery.RoutedLiteLLMTranslation` in
  `dev-docker/docker-compose.yml:62` and `deploy/environment.example:93`.
- `cp -r weblate_customization/src/weblate_customization
  dev-docker/data/python/` after editing; a full `./rundev.sh` (not a bare
  restart) because the environment block is baked at container creation.
- Production enablement on col4 is a separate, explicitly approved deploy:
  env changes + site-wide `litellm` configuration + `/machinery/col4/`.

### 5. Errors and constraints

- No special budget/quota handling is added: `MachineTranslationError`
  surfaces the proxy message (trusted host), and a failed judge batch is
  already recorded as unparsed. The game_pulse string markers above are the
  operator's diagnostic vocabulary, recorded here.
- `docs/security/threat-model.rst` currently describes the judge request as
  going to "the fixed provider host" (`threat-model.rst:241-251`, also
  114-121, 920-925, 980-984); this wording is updated to "the
  operator-configured provider host (:setting:`JUDGE_BASE_URL`, never
  user-configurable)". The machinery `base_url` keeps the existing
  `WeblateServiceURLField` private-target protection.
- The loc-kit OpenRouter client (`weblate/trans/loc_kit.py`) is untouched.

## Out of scope

- Changing judge seat models or any model choice (eval-gated, R3).
- Failover/spend telemetry à la game_pulse plans 03/04 — Weblate's
  machinery retry/rate-limit semantics and the judge's unparsed-batch
  semantics already cover the failure surface we need.
- `batch_size`/concurrency tuning for the corporate proxy.
- Production deployment and col4 configuration (separate approval).

## Tasks

### Task 1: `RoutedLiteLLMTranslation`

`weblate_customization/src/weblate_customization/machinery.py`:

1. Add `LITELLM_DEFAULT_BASE_URL = "https://hcbifrost.herocraft.com/litellm/v1"`.
2. Add `RoutedLiteLLMMachineryForm(RoutedLLMMachineryForm)` overriding only
   the `routing` field help text.
3. Add `RoutedLiteLLMTranslation(RoutedLLMTranslation)` with `name`,
   `settings_form`, `trusted_error_hosts`, `request_timeout = 55`,
   `get_runtime_base_url()`, and `get_chat_payload()` that calls `super()`
   and pops the `provider` key.

### Task 2: machinery tests

`weblate_customization/tests/test_machinery.py`, mirroring the existing
structure (mock URL becomes the hcbifrost chat-completions URL for the new
class): slug/registration, default `base_url`, payload carries
`response_format` **without** a `provider` block, `request_timeout == 55`,
routing resolution inherited (one smoke, no duplication of the full matrix).

### Task 3: judge endpoint + key rename

1. `defaults.py`: add `DEFAULT_JUDGE_BASE_URL`; rename
   `DEFAULT_JUDGE_OPENROUTER_KEY` → `DEFAULT_JUDGE_API_KEY`.
2. `models/_conf.py`, `settings_example.py`, `settings_docker.py`: add
   `JUDGE_BASE_URL` / `WEBLATE_JUDGE_BASE_URL`; rename the key setting and
   env var.
3. `judge.py`: build the URL from `settings.JUDGE_BASE_URL`; rename every
   `JUDGE_OPENROUTER_KEY` use. Drop the now-unused module constant.
4. Update tests: `test_judge_client.py`, `test_judge_loop.py`,
   `test_judge_form.py`, `test_judge_autotranslate.py` (rename overrides;
   add one test that `JUDGE_BASE_URL` override reaches the request URL via
   the HTTP mock).
5. Update `deploy/environment.example:112` and docs:
   `docs/admin/config.rst` (rename the setting section, add
   `JUDGE_BASE_URL` with `.. versionadded::`), `docs/admin/install/docker.rst`
   (envvar list), `docs/admin/checks.rst:143` mention,
   `docs/security/threat-model.rst` wording per design §5.
   Note: the gitignored `dev-docker/environment` carries the dev judge key
   under the old name — rename it locally when testing.

### Task 4: routed-engine resolution

1. `weblate/trans/forms.py`: replace `AutoForm.DEFAULT_ENGINE` with an
   ordered `ROUTED_ENGINES = ("openrouter", "litellm")` plus a resolver
   (first engine present in a settings map / engine-id list); use it for the
   preselection at `forms.py:1329-1334`.
2. `judge_loop.py:97-100,129` and `judge_workflow.py:32-57`: resolve via the
   helper; a project with neither engine behaves exactly like today's
   missing-`openrouter` case.
3. Tests: repair and prompt-context resolution for openrouter-only /
   litellm-only / both (openrouter wins) / neither;
   `check_judge_repair_routes` with a litellm-only project
   (`weblate/trans/tests/test_commands.py:471-495` pattern); AutoForm
   preselection for the same four cases.
4. Regression discipline: after writing each new test, revert the code
   change once and confirm the test fails (memory: two of three earlier
   judge-UI tests passed against the bug on first write).

### Task 5: wiring and docs

1. `dev-docker/docker-compose.yml` + `deploy/environment.example`:
   extend `WEBLATE_ADD_MACHINERY`.
2. `cp -r weblate_customization/src/weblate_customization
   dev-docker/data/python/`; full `./rundev.sh` with `WEBLATE_PORT=3001`.
3. `docs/changes.rst` (top, unreleased): one entry for the LiteLLM
   suggestion service and the configurable judge endpoint.
4. `AGENTS.md`: extend the `weblate_customization/` description and the
   "Deploying custom checks and machinery" registration line.
5. `docs/LLM-first/llm-first-product-architecture.md` phase 4: mark the
   endpoint increment as implemented by this plan.

### Task 6: verification

1. `uv run pytest weblate_customization/tests/test_machinery.py` and the
   judge/forms suites (`weblate/trans/tests/test_judge_client.py`,
   `test_judge_loop.py`, `test_judge_form.py`, `test_judge_autotranslate.py`,
   `test_commands.py`) — host or `./rundev.sh test`, whichever the current
   container memory pressure allows (check `docker stats --no-stream`
   before blaming failures).
2. `uv run prek run --all-files`; mypy per repo command.
3. Live smoke on dev :3001 once the dedicated key arrives:
   - configure the `litellm` service site-wide (`/manage/machinery/`):
     `validate_settings()` fires a real chat request against hcbifrost;
   - fetch a suggestion on a real component with a `routing` entry for a
     live proxy model;
   - point `WEBLATE_JUDGE_BASE_URL` at the proxy with the dev judge key and
     run one judge batch; record contract observations (latency vs 60 s,
     response_format behavior of the chosen model).
4. Commit and push (Conventional Commits), per the working agreement.

### Deployment (separate approval, not part of this plan's execution)

Rename `WEBLATE_JUDGE_OPENROUTER_KEY` → `WEBLATE_JUDGE_API_KEY` in the prod
environment, add the machinery registration, deploy via `deploy/vps.sh`
(worktree recipe if the checkout is dirty), configure `litellm` site-wide
with the dedicated key, then configure `/machinery/col4/` (enable `litellm`,
optionally disable `openrouter` per project to switch the repair contour).
