---
name: weblate-docs
description: Answer a question from the Weblate documentation for this repo (HCGameLoc fork). Use whenever a question is about how Weblate works, what a setting/check/add-on/format/API endpoint does, where something is documented, or when a claim about Weblate behavior needs a source. Triggers on "что говорит документация", "где это в доках", "как работает <настройка>", "what does <setting> do", "where is X documented", "weblate docs", "найди в документации".
---

# Weblate docs lookup

Goal: a sourced answer in 1-3 tool calls. Never answer Weblate behavior from
memory, and never guess a doc URL.

## Precedence (non-negotiable)

1. **Code under `weblate/`** - defines actual behavior. Wins over any prose.
2. **Local `docs/`** - the only place fork divergence is described.
3. **Upstream `.md` pages** - authoritative for stock Weblate.

Prose that contradicts the code is stale prose. Say so and cite the file:line.

## Fork context

This repo is HCGameLoc, an independent Weblate-derived repo, not a tracking
fork. Upstream docs do NOT cover:

- `weblate_customization/` - `GameMarkupCheck`, `RoutedLLMTranslation`
  (service slug `openrouter`, display name `OpenRouter`)
- `loc_kit_ingest/` and `weblate/utils/views.py:create_component_from_kit`
- `WEBLATE_ADD_CHECK` / `WEBLATE_ADD_MACHINERY` env registration
  (`weblate/utils/environment.py`, folded into `CHECK_LIST` by
  `settings_docker.py`) instead of editing settings lists
- `docs/llm-first/`, `docs/product/`, `docs/operations/`, `docs/guides/`
  (Russian design docs; layout rule in `AGENTS.md`, "Documentation layout")

Any question touching these: read local files, not upstream.

## Step 1 - local first

```text
grep -rn "<term>" docs/ --include=*.rst
```

`docs/admin/` holds the admin-facing pages, `docs/api.rst` the REST API,
`docs/changes.rst` the current unreleased section. Hit found -> `read` that
`.rst` and answer. Question is about fork-specific behavior -> stop here.

## Step 2 - upstream markdown

The docs build enables `sphinx_llm.txt` (see `docs/conf.py`), so every page has
a markdown twin plus an index. Fetch the index once per session and cache it:

```sh
curl -s https://docs.weblate.org/en/latest/llms.txt > /tmp/weblate-llms.txt
```

212 pages, each line `- [Title](URL.md): first sentence`. Route by grepping the
index rather than guessing:

```sh
grep -i "glossary" /tmp/weblate-llms.txt
```

Then read the page. Pages are large (`admin/machine.md` is 72 KB) - read in
ranges and widen only if needed:

```text
read https://docs.weblate.org/en/latest/admin/machine.md:1-200
```

Hot paths, already verified:

| Question | Page |
|---|---|
| automatic suggestions, MT/LLM services, priority, scores | `admin/machine.md` |
| quality checks, flags, writing a check | `admin/checks.md` |
| custom check/machinery/add-on/autofix registration | `admin/customize.md` |
| settings reference (`CHECK_LIST`, rate limits, ...) | `admin/config.md` |
| add-ons and their events | `admin/addons.md` |
| management commands (`updatechecks`, `install_machinery`) | `admin/management.md` |
| project/component fields, source & secondary language | `admin/projects.md` |
| permissions, roles, groups | `admin/access.md` |
| REST API endpoints, unit state PATCH rules | `api.md` |
| file formats | `formats.md` |
| VCS backends | `vcs.md` |
| Docker env vars | `admin/install/docker.md` |
| running tests, frontend conventions, internals | `contributing/tests.md`, `contributing/frontend.md`, `contributing/internals.md` |

## Version check

`llms.txt` line 3 states the documented version. It must match
`weblate/utils/version.py` (`VERSION`). Mismatch -> the answer may describe a
different release; pin the version explicitly:

```text
https://docs.weblate.org/en/weblate-5.9/admin/machine.md
```

## Cross-references

`.rst` uses `:ref:`, `:setting:`, `:doc:`. The rendered `.md` keeps
`<a id="target"></a>` anchor lines, so a `:ref:` target is greppable in the
markdown. To resolve a target across the whole upstream corpus:

```sh
uv run python -m sphinx.ext.intersphinx https://docs.weblate.org/en/latest/objects.inv | grep -i "<target>"
```

## Rules

- Fetch the `.md` URL, never the HTML page. It is already clean markdown;
  reader-mode/extraction middlemen add nothing and strip the `<a id=...>`
  anchors needed for cross-references. Character-capped fetchers silently
  truncate a 72 KB page - an absent section is not proof of absence.
- Cite `path:line` for local files, the `.md` URL plus section heading for
  upstream.
- Doc says one thing, code another: report the code, name both.
- Answer not in the docs: say so, then read the implementing module under
  `weblate/` and answer from it, marked as read from code.

## Local render (only when the whole fork corpus must be searched)

```sh
uv sync --all-extras --dev && cd docs && uv run make html
```

Produces `docs/_build/html/llms.txt` for this fork - the same index workflow as
upstream, but over local `.rst`.
