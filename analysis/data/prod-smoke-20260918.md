Smoke: producer judge run on prod after deploy a1ccdafb
=======================================================
Scope: pirate-ships / localization-json / fr, 6 units (1 empty + 5 translated fr).
Estimate -> POST runs/ -> REST poll -> JudgeRunUnit/LLMUsageLog audit.

What was exercised (all correct on prod):
1. Estimate   : preparation payload {missing:1, per_language:{fr:1}, engine:openrouter, blockers:[]}.
2. Mandatory MT barrier: run d1aefe9d started 18:41:00; OpenRouter REFUSED the
   MT call ("The provider refused the request"); preparation phase=blocked,
   written=0; run FAILED 4.3s later with
   "Judges were not started: 1 strings still have no translation";
   all 6 JudgeRunUnit rows outcome=skipped skip_reason=mt-prerequisite;
   summary.reason_code=mt-prerequisite. No silent success, judges not started.
3. Retry run 1bf0b076 (18:41:15): MT retried -> applied (written=1);
   preparation ready -> judge evaluated all 6 units -> all passed (none/pass),
   run completed 18:41:48. summary: written=1 evaluated=6 nothing_blocking=6.
4. Third run 0567ae89: missing_initial=0 (barrier saw filled string),
   judge 6/6 passed, completed.
5. Unit 411118 '+{0}/ch' -> target '+{0}/h', state=20 (translated). Correct.

Cost (LLMUsageLog, project_slug=pirate-ships, window 18:41:04-18:41:53):
- 2 MT calls op=translation openrouter google/gemini-3.7-flash:
  1 refused (cost 0.0043 reported) + 1 applied (cost 0.0037) = $0.0081 total.
- 17 judge calls op=judge via litellm (seat1 qwen3.8-max batch=1 reasoning off,
  seat2 deepseek-v4-pro batch=2); cost_usd=None (LiteLLM proxy reports none).
  cached tokens present (2-3k per qwen call).
- TOTAL: 20 requests, 58932 in / 2780 out tokens, cached 30848.

Note: 3 runs instead of 1 — ./deploy/vps.sh ssh has ssh_retry (3 attempts on
nonzero exit) and my driver script asserted on 201; every failed attempt
re-launched a run. Runs 2-3 duplicated run 1's small scope. Not a product
bug; harness lesson recorded (scripts must be idempotent and exit 0).

No config touched, no workers restarted, no cleanup needed: the failed run is
a normal durable record, the empty-string translation was real work (+{0}/h).
