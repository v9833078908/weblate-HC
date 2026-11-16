# Owner-approved direction (2026-11-16)

- Implement primary OpenRouter Gemini to fallback LiteLLM QWEN3.7-plus on confirmed OpenRouter insufficient-credit.
- QWEN3.7-plus profile uses reasoning_effort=none. Live probes confirmed none produces zero reasoning and actual Anvil prompt parsed; low does not disable reasoning. Separate proxy budget established by owner.
- Existing multiple-engine selection means best-result comparison, NOT fallback; do not silently repurpose it globally.
- Primary success must not call fallback. Preserve in-flight and applied primary results; fallback receives only unresolved units after terminal credit refusal and drain.
- Do not automatically fallback on timeout, lost/paid-unknown response, parsing/partial errors, permission, auth, generic provider errors or other quota without explicit design. No automatic third paid model.
- Existing durable paid work/digest/unit revision fences MUST NOT be weakened, bypassed or identity-substituted. New charges need explicit audit authorization. See docs/product/plans/2026-11-16-mt-openrouter-litellm-credit-fallback.md (must be copied to branch).
- Current project effective LiteLLM absent; production deployment/config mutation NOT authorized by this implementation request.
- Implementation allowed; tests, commits, pushes, PR expected after verified completion. No production changes.
- Work only in absolute worktree /Users/eli/Documents/PythonProjects/gamedev tools/weblate/.worktrees/mt-credit-fallback based github/main 778f287b. Corporate origin currently unreachable; must retry and reconcile before merge/push completion. Preserve all unrelated main checkout changes.
