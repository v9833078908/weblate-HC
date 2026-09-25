# PRODUCT.md — HCGameLoc

Status: inferred from the repository on 2026-09-21 (no interview was run; the user
told the agent to proceed). Every claim below cites an existing repo document; the
assumptions section marks what would need confirmation before a durable commitment.

## What this is

HCGameLoc is an independent Weblate-derived TMS (`AGENTS.md`, "Project-specific
setup") used to localize Hero Craft games. It is not tracking upstream; `weblate/`
is edited directly. The product direction is an LLM-first translation pipeline:
routed MT machinery, an AI judge with verdicts and repair, deterministic game
checks, loc-kit intake, and glossary tooling — see
`docs/product/vision/llm-first-product-architecture.md` and
`docs/product/vision/producer-console-design-and-roadmap.md`.

Its own console (Producer Console) is a second view over the same data and the same
permissions, built as `weblate/api/producer/` plus a separate frontend; the native
Weblate UI remains the primary surface today.

## Who it serves

- **Producer** — the person the fork is optimized for: a Russian-speaking game
  producer who does not read the target language and does not translate. Their job
  is to decide whether to trust the localization and to release it
  (`docs/product/vision/2026-08-15-producer-first-product-research.md`, §5;
  roadmap decision 21: producer = Weblate project administrator).
- **Translator / reviewer** — works in the dense editor, Zen mode, suggestions.
- **AI Tools administrator** — a superuser wiring models, keys, and costs.
- **Engineer** — owns components, VCS, and the game repository contract
  (`docs/product/guides/game-repo-integration-contract.md`).

## Surfaces and modes

- Weblate project/language pages, editor, checks, reports — **Operate** (dense tooling,
  scanability first; `DESIGN.md` Overview: "рабочий инструмент, а не витрина").
- Producer Console screens — **Operate** (task completion, no marketing surface).
- Fork documentation under `docs/product/` — **Read**.
- The public landing page of the instance — **Persuade** (not covered here).

## Product truths that designs must respect

- **Two paid triggers, and only two, for repeat recommendations**: an explicit
  "prepare recommendations" command and an opt-in on the MT form. The judge
  check linked from the repeat queue opens the standard judge launch form with
  queue places prefilled and verdict-only mode selected; it generates no repair
  candidates or pretranslations, and the queue itself starts no paid run.
  Opening a page, filtering, and preview are free; an unknown price is never
  displayed as `$0`
  (`docs/product/plans/2026-09-17-repeat-drift-reconciliation-and-managed-reuse.md`, §1;
  `docs/product/plans/2026-09-24-repeat-queue-producer-ux.md`).
- **Nothing is written without preview**: approved targets are never overwritten.
  The repeat queue preselects the best available choice for every single-form
  group (D17: a passed variant first, never a flagged one); a preselected
  choice still goes through preview and confirmation. Every
  mass write is preceded by an explicit confirmation screen
  (`docs/product/plans/2026-09-24-repeat-queue-producer-ux.md`).
- **Permissions are the existing ones** (`project.edit`, `unit.edit`,
  `upload.perform`, `unit.review`, `translation.auto`); the console and the native
  UI check the same `has_perm` (`producer-console-design-and-roadmap.md`, §4.2).
- **Repeat semantics**: detection, sharing, and approval are separate decisions;
  an identical source string does not prove an identical meaning; technical keys
  and length prove nothing (`docs/product/research/2026-09-17-repeat-drift-tms-practices-and-session-context.md`, §7).
- **No raw prompts, JSON schemas, or internal state codes in producer-facing UI**
  (`2026-08-15-producer-first-product-research.md`).
- Costs, runs, and verdicts are recorded in `LLMUsageLog` / `ProducerRun`; an
  unknown cost stays unknown rather than being rounded to zero.

## Assumptions to confirm

- That the native Weblate surface remains the default home for repeat
  reconciliation until Producer Console wave 2 item 2.6 ships.
- That the repeat queue's audience is project administrators, not translators.
- That a prototype lives under `.impeccable/` and never becomes product code.
