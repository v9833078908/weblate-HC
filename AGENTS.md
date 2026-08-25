# Agents guidance for Weblate

This file captures agent-specific guidance for working in the Weblate codebase.
For application-developer workflows and broader product integration guidance, use
`docs/devel/` instead of repeating that material here.

"Working agreement" and every section from "Project-specific setup" onwards
describe this repository only; the sections in between are Weblate conventions
inherited from the original codebase.

## Working agreement

- Start every task with a plan and wait for approval. Before editing any file,
  state what will change, in which files, how it will be verified, and what is
  deliberately out of scope. Do not implement until the user approves it. For
  multi-step work, put the plan in the matching `docs/<area>/plans/` directory
  (see "Documentation layout" below) following the existing files there.
  Answering questions, reading code, and research need no plan.
- Implement only what the approved plan covers. When the work turns out to
  need something the plan does not mention, stop and get that increment
  approved instead of widening the change silently.
- Never deploy without explicit approval. Deployment is anything that changes a
  running instance: `deploy/vps.sh`, any command against `l10n.herocraft.com`
  or the `hcgameloc-*` containers, production image builds, management commands
  or `weblate shell` against production, and rebuilding or restarting the
  shared `dev-docker` stack through `./rundev.sh`.
- Committing and pushing is not deployment. The "Always finish by committing
  and pushing" rule under "Code expectations" applies once an approved
  implementation is complete and verified; it never authorises starting work or
  deploying.

## Project overview

- Weblate is a Django-based web translation platform with Celery background
  tasks.
- The primary stack is Python, Django, JavaScript, and HTML/CSS/Bootstrap.

## Code expectations

- Follow existing Django patterns and project conventions.
- Prefer the repository's configured Ruff-based formatting and linting rules.
- Use human-readable Ruff rule names in overrides, such as
  `# ruff: ignore[assert]`; do not use or rewrite them to cryptic codes such as
  `# noqa: S102`, `# ruff: N801`, or `# ruff: noqa: F841`.
- Prefer type hints and use `from __future__ import annotations` in Python
  modules.
- Use `TYPE_CHECKING` imports for type-only dependencies when that avoids
  runtime import cycles.
- All user-facing strings must be translatable using Django i18n helpers, except
  messages used in the API or persisted storage, such as the audit log, add-on
  log, or changes; these messages should not be localized.
- In templates, use `{% translate %}` / `{% blocktranslate %}` for translatable
  text.
- When a template supplies its own `<form>` element and submit button, the
  form class must set `self.helper = FormHelper(self)` and
  `self.helper.form_tag = False`. The `{% crispy %}` tag otherwise renders a
  second `<form>`; the source stays balanced, but a browser closes the outer
  form at crispy's `</form>` and every later control, including the submit
  button, ends up outside any form. The page renders correctly and no click
  can submit it. Use the `{{ form|crispy }}` filter when only the fields are
  wanted.
- Preserve accessibility and the existing Bootstrap/jQuery-based frontend
  patterns. For user-facing HTML, CSS, or JavaScript changes, follow
  `ACCESSIBILITY.md` and `docs/contributing/frontend.rst`, including keyboard
  navigation, visible focus, semantic controls, labels/errors, and
  non-color-only state.
- Write commit messages using the Conventional Commits format
  `<type>(<optional scope>): <description>`. Common types include `feat`,
  `fix`, `docs`, `refactor`, `test`, `ci`, and `chore`. Example:
  `fix(translations): handle empty component slug`.
- Always finish by committing and pushing. When implementation is complete
  and verified, commit the changes and push to the remote; do not stop with
  uncommitted or unpushed work.
- Keep new project code under GPL-3.0-or-later and include the repository's
  usual copyright and SPDX license header in new Python files.

## Documentation expectations

- Match the style of the surrounding page in `docs/`; prefer clear, direct,
  instructional prose with short paragraphs over marketing language or large
  rewrites.
- Preserve the existing structure and heading hierarchy. Prefer extending an
  existing section over creating a new one, and keep headings in sentence case
  to match the current documentation.
- Use Sphinx and reStructuredText conventions already present in the docs:
  prefer semantic cross-references such as `:ref:`, `:doc:`, `:guilabel:`,
  `:setting:`, `:wladmin:`, `:file:`, and `:program:` instead of raw links,
  repeated explanations, or ad-hoc formatting.
- Keep documentation changes scoped and additive when possible. Avoid
  unnecessary rewrites or structure changes, especially because the
  documentation is translated.
- Use admonitions, screenshots, and code blocks only when they add concrete
  value and match the style of the surrounding page.
- Keep manually maintained explanations in the main documentation pages. In
  `docs/snippets/`, do not hand-edit content inside autogenerated marker
  blocks; manual explanatory text outside those blocks is preserved by the
  generator and can be edited when appropriate.

## Weblate-specific guardrails

- Be careful with repository, webhook, and file-handling code; validate inputs
  and avoid introducing path traversal, command injection, or script injection
  risks.
- Handle VCS operations defensively and surface failures cleanly.
- Mock external VCS operations and API calls in tests.
- Check `docs/security/threat-model.rst` when changing public endpoints,
  authentication or token modes, deployment modes, backup or import formats, VCS
  execution paths, outbound integration classes, add-on execution capabilities,
  or security-relevant defaults for hooks, HTTPS, rate limits, CSP,
  private-network access, or backup import limits.
- Update `docs/security/threat-model.rst` in the same change when the threat
  model's "Conditions that change this model" apply, including when unsupported
  components become supported product surface, claimed security properties
  change, or a vulnerability report exposes a model gap.
- For user-visible changes, add or update a changelog entry in the top section
  of `docs/changes.rst` for the upcoming release.
- Do not alter changelog sections for already released versions; put follow-up
  entries in the current unreleased section instead.
- Keep changelog entries concise and link to the relevant documentation for the
  feature instead of embedding long explanations in the changelog itself.
- Minor fixes and fixes for features that have not been released yet do not
  need a changelog entry.

## GitHub discussions

- GitHub organization discussion URLs such as
  `https://github.com/orgs/WeblateOrg/discussions/19794` can still belong to the
  `WeblateOrg/weblate` repository. When working with these URLs, resolve the
  discussion through `WeblateOrg/weblate` repository discussions instead of
  treating the URL as an issue, pull request, or organization-only object.
- Use GitHub discussion-aware tooling, such as `gh api graphql` against
  `repository(owner: "WeblateOrg", name: "weblate") { discussion(number: ...) }`,
  when the regular GitHub issue or pull request connectors do not expose the
  discussion.

## Testing and linting instructions

- Install the development dependencies first using
  `uv sync --all-extras --dev`.
- After syncing, prefer `uv run ...` for subsequent commands so they use the
  virtual environment created in `.venv`. If needed, you can also activate it
  with `source .venv/bin/activate` or invoke tools from `.venv/bin/`.
- Prefer `uv run prek run --all-files` as the primary linting/formatting command because
  it runs the repository's configured pre-commit framework checks.
- `prek` is a third-party reimplementation of the `pre-commit` tool.
- Prefer `prek` for Ruff checks and formatting; `uv run ruff ...` is not
  guaranteed to work in this environment because Ruff can be provided only
  through the pre-commit hook environment.
- Use `pytest` to run the test suite: `uv run pytest`. On a fresh checkout,
  first follow the local test setup in `docs/contributing/tests.rst`
  (`DJANGO_SETTINGS_MODULE=weblate.settings_test`, `collectstatic`, and test
  database prerequisites). `scripts/test-database.sh` can be sourced to set up
  the database connection variables such as `CI_DB_USER`, `CI_DB_PASSWORD`,
  `CI_DB_HOST`, and `CI_DB_PORT`.
- Use `pylint` to lint the Python code: `uv run pylint weblate/ scripts/`
- Use `mypy` to type check with the same command as CI:
  `uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh`.
- New or changed code should not introduce new mypy failures where current
  Django typing support makes that practical. Existing non-enforced mypy
  findings should not be worsened.

## Project-specific setup

HCGameLoc is an independent Weblate-derived repository (`origin` =
v9833078908/weblate-HC) used to localize Hero Craft games. It is no longer
tracking WeblateOrg/weblate: there is no upstream remote, no rebase workflow,
and editing files under `weblate/` directly is a normal way to change product
behavior. Keep the code GPL-3.0-or-later and keep upstream conventions from the
sections above, but treat divergence from WeblateOrg as expected rather than as
debt.

Repository-specific parts:

- `weblate_customization/` - a custom-check, custom-autofix, and
  custom-machinery package following `docs/admin/customize.rst`. `checks.py`
  ships `GameMarkupCheck` (`check_id: game-markup`), which asserts that Unity
  rich-text tags (`<color=#RRGGBB>`, `<link>`, `<size=N>`, `<b>`,
  `<sprite name="fire">`) and engine placeholders (`{0}`, `%KEY%`) in the
  target match the source multiset, and
  `GameLineBreakCheck` (`check_id: game-line-break`), which asserts that the
  Hero Craft engine line separator `$` is neither lost nor added, and that no
  whitespace hugs it, whenever the source uses `$` tightly as a separator, and
  `GameNumberCheck` (`check_id: game-number`), which asserts that every number
  the source states is present in the target after markup, placeholders and
  full dates are removed and the decimal separator is normalized; a number the
  target adds is accepted, an English ordinal is dropped from the source only
  (the target renders "24 декабря" as "December 24th"), and the flag is
  `ignore-game-number`. `GameTokenCheck` (`check_id: game-token`) asserts that
  every engine substitution identifier the source uses survives into the
  target: the mission DSL writes `item_type[|{0}]` and
  `skirmish_league_id[gen|в {0}|в любой лиге]`, whose bracketed bodies are
  translated while the identifier in front of the bracket is a lookup key, so
  a translated one (`element_type[`) resolves to nothing at runtime. A bracket
  without a `|` is ordinary prose, not a substitution. The flag is
  `ignore-game-token`.
  `autofixes.py` ships `LineSeparatorSpacing`, which deterministically strips
  whitespace hugging a tight `$` separator before the target is stored,
  importing the shared separator regexes from `checks.py` (mirroring how
  `weblate/trans/autofixes/chars.py` imports from `weblate/checks/chars.py`);
  both `GameLineBreakCheck` and `LineSeparatorSpacing` honour the same
  `ignore-game-line-break` flag. `machinery.py` ships `RoutedLLMTranslation`,
  an OpenRouter-backed automatic suggestion service (display name and service
  slug: `OpenRouter` / `openrouter`) that resolves the model ID per target
  language from a `routing` JSON map, and `RoutedLiteLLMTranslation`, the same
  routing model against the corporate LiteLLM proxy (`LiteLLM` / `litellm`,
  default base URL `https://hcbifrost.herocraft.com/litellm/v1`, no
  OpenRouter-only `provider` field). Each service uses its own key; a
  project-level configuration overrides the global one field by field, so a
  project stores only what it changes (persona, style,
  `language_instructions`) and inherits `key`, `base_url` and `routing`.
  `weblate/trans/forms.py` resolves which routed engine a project uses
  through `ROUTED_ENGINES = ("openrouter", "litellm")` and the
  `configured_routed_engine`/`available_routed_engine` helpers: the first
  entry with usable project configuration wins project-wide (`openrouter`
  when both are configured), never a per-language fallback to the other one;
  `AutoForm` uses the same helpers to preselect the "Machine translation"
  source, and `judge_loop.py`/`judge_workflow.py` use them for the judge's
  repair engine and project context. See "Deploying custom checks and
  machinery" below for how these modules reach the dev container.
- `weblate-mcp/` (gitignored, its own git repo) - vendored `@mmntm/weblate-mcp`,
  a NestJS MCP server that talks to the local Weblate REST API. Its `.env` points
  at `http://localhost:3001/api/`.
- `docs/llm-first/`, `docs/product/`, `docs/operations/`, `docs/guides/` -
  fork documentation (mostly in Russian) for game-localization workflows; see
  "Documentation layout" below for the rule that decides where a file goes.
- `analysis/probes/`, `analysis/data/` - one-off measurement scripts and the
  corpora, golden sets and run outputs they read and write. Not documentation:
  nothing here is part of the product, and both directories are excluded from
  packaging and from the `typos`/`codespell` hooks.
- `loc_kit_ingest/` (tracked) - standalone loc-kit importer package (no Django
  imports): `reader.py` (CSV/TSV/XLSX), `infer.py` (derives a strict profile
  from the kit's own header row), `profile.py` (closed schema with two
  versions: v1 `term-description-pairs` and v2 `record-map` for TBX glossary
  tables), `parser.py`, `writer.py` (monolingual PO + bilingual TBX with
  parse-back validation), `pipeline.py`/`cli.py` (atomic
  `python -m loc_kit_ingest`). The CLI stays deterministic and offline: no
  `--terms`, no `--suggest-profile`, no OpenRouter. Context identity is a
  Unicode-safe serialization of `(section, source term)`, not an ASCII slug.
  The component-creation UI consumes it through
  `weblate/utils/views.py:create_component_from_kit`: the "Upload translation
  files" tab accepts CSV/TSV/XLSX kits directly (converted to po-mono,
  discovery skipped) as well as ZIP. When :guilabel:`Use as glossary` is
  checked, a CSV/TSV/XLSX table instead enters the glossary workflow (sheet
  selection, deterministic inference first, optional OpenRouter fallback,
  local validation, TBX component) documented in `docs/guides/loc-kit-ingest.md`
  and the plan.
  Standalone tests: `cd loc_kit_ingest && uv run pytest` (no DB).
  Weblate-level tests: `weblate/trans/tests/test_loc_kit_ingest_contract.py`.
  The running container imports the package from `/app/data/python`, so after
  editing it run `cp loc_kit_ingest/*.py dev-docker/data/python/loc_kit_ingest/`.
- `weblate/trans/loc_kit.py` + `weblate/trans/models/loc_kit.py` - the
  Weblate-side glossary analysis surface. `loc_kit.py` owns the bounded
  deterministic structural sampler, the fixed-host OpenRouter profile proposal
  client (`https://openrouter.ai/api/v1/chat/completions`, strict JSON Schema,
  120 s timeout, `provider.require_parameters: true`), and the local
  publication gate (`validate_glossary_profile`: schema, exact headers, full
  parse, render, parse-back). `models/loc_kit.py` adds
  `LocKitImportDraft` (migration `0098_loc_kit_import_draft.py`): an
  unguessable-token, owner- and session-bound temporary upload draft under
  private `FileSystemStorage` in `DATA_DIR`, capped at one hour, cleaned by the
  Celery `cleanup_loc_kit_drafts` task every 900 s. This is a **separate,
  site-wide OpenRouter configuration** keyed by
  `LOC_KIT_PROFILE_OPENROUTER_KEY` / `LOC_KIT_PROFILE_OPENROUTER_MODEL` and
  gated by `LOC_KIT_PROFILE_ANALYSIS_ENABLED` (all off by default); it does
  **not** reuse `RoutedLLMTranslation` or its machine-translation
  configuration, and users cannot supply an endpoint, key, or model.
  `append_glossary_terms(request, component, preview) -> GlossaryAppendResult`
  is the append-only counterpart: it applies a validated preview to an
  *existing* glossary component and never overwrites anything already
  there. Identity is `(context, source)`; a match is the old term, and its
  target, explanations, and flags stay untouched no matter what the table
  carries for it. A blank target cell or an absent language column is a
  per-language partial-success skip (`blank`/`absent` on
  `GlossaryLanguageAppendResult`), not an apply-wide failure; apply is
  blocked entirely only on a source-language mismatch or a same-source/
  different-context conflict (`GlossaryAppendCollisionError`). A missing
  target language is created automatically when a new term needs it and
  the actor holds `translation.add`/`glossary.add`, otherwise that
  language is `unavailable` while the rest of the table still applies. New
  terms keep their source/target explanations
  (`Unit.update_explanation`) and are flagged `terminology`, so background
  `sync_terminology` later mirrors them into languages added afterwards.
  `LocKitImportDraft.target_component` distinguishes an update draft,
  bound to one existing glossary and gated by `upload.perform` on it
  rather than the wizard's project-creation permission;
  `LocKitGlossaryConfirmView` explicitly refuses a draft with
  `target_component` set, so that weaker permission can never reach
  component creation.

Local modifications to `dev-docker/docker-compose.yml`: Postgres published on
`5434` (5433 is taken by another project) and `WEBLATE_VCS_ALLOW_SCHEMES` extended
with `file` so local git repos can be used as translation sources.

## Documentation layout

Every fork document lives at `docs/<area>/<genre>/<YYYY-MM-DD>-<slug>.md`. The
area answers "whose document is this", the genre answers "what kind". Both
vocabularies are closed; if a document does not fit, discuss it rather than
inventing a directory.

| Area | What belongs there |
| --- | --- |
| `docs/llm-first/` | the LLM-first TMS: anything that changes or executes a phase of the roadmap in `docs/llm-first/vision/llm-first-product-architecture.md` - judge, MT machinery, autofix layer, quality gates |
| `docs/product/` | features of the Weblate fork itself that the roadmap does not own - loc-kit intake, glossary UI, checks, exports, dev-environment work |
| `docs/operations/` | work bound to a live instance or one game - production tasks, LQA audits, reports, team meetings |
| `docs/guides/` | evergreen contracts and instructions read from outside; updated in place, never dated |

| Genre | What belongs there |
| --- | --- |
| `plans/` | an approved implementation plan, task by task, carrying a status |
| `designs/` | a design decision without a task list |
| `measurements/` | numbers from a run: reproducible, dated |
| `research/` | a synthesis of external sources or of another system |
| `reviews/` | a review of a plan, design or change |
| `audits/` | an LQA audit of a live component |
| `reports/` | a status or summary written for a human |
| `meetings/` | material from a meeting with a game team |
| `archive/` | superseded, kept for history |
| `vision/` | vision and roadmap (only under `docs/llm-first/`) |

Naming: directories are lowercase and hyphenated; a dated snapshot always
carries its date first (`2026-08-24-slug.md`), a living document carries none;
several documents from one day are numbered `YYYY-MM-DD-NN-slug.md`. Reference
another document by its full repository-relative path, never a path relative to
the referring file - documents move, and relative links silently rot.

`docs/specs/` holds upstream API artifacts only (`openapi.yaml` and
`schemas/*.json`, wired into the Sphinx build, two GitHub workflows and
`REUSE.toml`). Do not put fork documents there. Fork markdown is not part of
the Sphinx build - `myst_parser` is not enabled.

## Development environment

The dev instance runs in Docker (`dev-docker/`), started via `./rundev.sh` from
the repo root. `rundev.sh` defaults `WEBLATE_PORT=8080`, but the currently running
container publishes **3001** (`WEBLATE_PORT=3001 ./rundev.sh`) - that is what the
MCP server and any API scripts expect. Login `admin`/`admin`. Mail goes to maildev
on <http://localhost:1080/>.

```sh
./rundev.sh                 # build + start (recreates containers)
./rundev.sh logs -f weblate # follow logs
./rundev.sh stop
./rundev.sh test weblate/checks/tests/test_markup.py   # pytest inside container
./rundev.sh check           # django `weblate check`
```

The repo root is bind-mounted to `/app/src` and Granian reloads on changes under
`/app/src/weblate`, so Python edits in `weblate/` are live. `dev-docker/data/` is
mounted at `/app/data`.

### Host-side (uv) commands

```sh
uv sync --all-extras --dev
uv run pytest weblate/utils/tests/test_search.py          # single file
uv run pytest weblate/trans/tests/test_views.py -k slug   # single test
uv run prek run --all-files                               # lint/format (Ruff lives here)
uv run pylint weblate/ scripts/
uv run mypy --show-column-numbers weblate scripts/*.py ./*.py | ./scripts/filter-mypy.sh
```

pytest needs `DJANGO_SETTINGS_MODULE=weblate.settings_test`, a PostgreSQL server
(`source scripts/test-database.sh` sets `CI_DB_*`), and a prior
`DJANGO_SETTINGS_MODULE=weblate.settings_test uv run ./manage.py collectstatic --noinput`.
Running tests through `./rundev.sh test` avoids all of that setup.

The container shares the host's Docker memory allocation with every other
running compose project. When a suite is green in isolation but returns mass
setup errors whose count changes between identical runs, or a test suddenly
takes twenty times longer, check `docker stats --no-stream` before suspecting
the code or `pytest-xdist`: the usual cause is the container sitting at its
memory ceiling. Each xdist worker already gets its own temporary `DATA_DIR`
(`weblate/settings_test.py`), so worker file collisions are not the cause.

### weblate-mcp

For any operation against the running Weblate instance (projects, components,
languages, units, statistics), prefer the vendored MCP server
<https://github.com/mmntm/weblate-mcp> when it is connected to the agent
session, instead of hand-rolled REST calls or Django shell one-liners.

If the MCP server is not connected in the current session, fall back to direct
REST calls against `http://localhost:3001/api/` using the token already stored
in `weblate-mcp/.env` (`WEBLATE_API_TOKEN`) - do not mint a second token.

```sh
cd weblate-mcp && pnpm install && pnpm build   # dist/main.js is the stdio entrypoint
pnpm dev                                       # nest start --watch
pnpm test
```

## Deploying custom checks and machinery

`weblate_customization/` is a `uv_build` package, but the dev container does not
install it - the module is **copied** into `dev-docker/data/python/`, which is on
the container's `sys.path` via `/app/data/python`. After editing
`weblate_customization/src/weblate_customization/checks.py`,
`weblate_customization/src/weblate_customization/autofixes.py`, or
`weblate_customization/src/weblate_customization/machinery.py`:

```sh
cp -r weblate_customization/src/weblate_customization dev-docker/data/python/
```

`GameMarkupCheck`, `GameLineBreakCheck`, `CyrillicLeakCheck`,
`GameNumberCheck` and `GameTokenCheck` are registered through
`WEBLATE_ADD_CHECK: weblate_customization.checks.GameMarkupCheck,weblate_customization.checks.GameLineBreakCheck,weblate_customization.checks.CyrillicLeakCheck,weblate_customization.checks.GameNumberCheck,weblate_customization.checks.GameTokenCheck`,
and `LineSeparatorSpacing` through
`WEBLATE_ADD_AUTOFIX: weblate_customization.autofixes.LineSeparatorSpacing`,
both in the `weblate` service environment in `dev-docker/docker-compose.yml`
and (as `WEBLATE_ADD_CHECK=` / `WEBLATE_ADD_AUTOFIX=`) in
`deploy/environment.example`; `settings_docker.py` folds `WEBLATE_ADD_CHECK` /
`WEBLATE_REMOVE_CHECK` into `CHECK_LIST`, and `WEBLATE_ADD_AUTOFIX` /
`WEBLATE_REMOVE_AUTOFIX` into `AUTOFIX_LIST`, through `modify_env_list`
(`weblate/utils/environment.py:182`). `RoutedLLMTranslation` and
`RoutedLiteLLMTranslation` are registered the same way through
`WEBLATE_ADD_MACHINERY:
weblate_customization.machinery.RoutedLLMTranslation,weblate_customization.machinery.RoutedLiteLLMTranslation`,
already present in `dev-docker/docker-compose.yml`. The same mechanism
exists for
`WEBLATE_ADD_ADDONS`, `WEBLATE_ADD_APPS`, etc. A restart after editing
`dev-docker/docker-compose.yml` needs a full `./rundev.sh` (rebuild + start),
not just a container restart, because the environment block is baked in at
container creation.

`dev-docker/data/python/customize/` is a separate, older customization module -
unrelated to `weblate_customization/`.

## Upstream architecture, briefly

Django project + Celery. Apps under `weblate/`, wired in `weblate/settings_*.py`
(`settings_docker.py` is what the dev container uses; `settings_test.py` for
pytest).

The data model is a chain: `Project` -> `Component` (one VCS repo + file format)
-> `Translation` (one language of a component) -> `Unit` (a single string, holding
`source`, `target`, `state`, and computed `failing_checks`). `Suggestion`,
`Comment`, `Change`, and `Label` hang off units. All in `weblate/trans/models/`.

Cross-cutting subsystems, each a registry of pluggable classes:

- `weblate/checks/` - quality checks (`TargetCheck`/`SourceCheck` subclasses,
  registered in `CHECK_LIST`). Custom checks subclass these.
- `weblate/formats/` - translation file formats via translate-toolkit.
- `weblate/vcs/` - git/mercurial/subversion backends; all repo work funnels
  through here.
- `weblate/machinery/` - machine-translation and LLM services
  (`weblate/machinery/llm.py` passes a unit's `failing_checks` into the
  translation prompt, so a failing custom check feeds back into the next LLM
  suggestion).
- `weblate/addons/` - event-driven add-ons that react to component/translation
  changes.
- `weblate/api/` - Django REST Framework API (`views.py` is where unit state
  transitions and permission checks live; the OpenAPI spec is
  `docs/specs/openapi.yaml`).

Unit states are defined in `weblate/utils/state.py`: `10` needs editing, `11`
needs rewriting, `12` needs checking, `20` translated, `30` approved. The API
permits setting only `0/10/20/30` via PATCH - `11`/`12` are set by Weblate itself.
A PATCH that changes `state` must also send `target` as the full list of plural
forms.
