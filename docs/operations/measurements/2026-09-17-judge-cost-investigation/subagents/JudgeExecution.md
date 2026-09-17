# JudgeExecution

Полный итоговый вывод субагента. Не все исходные гипотезы подтверждены: см. `docs/operations/measurements/2026-09-17-judge-cost-investigation/corrections.md`.

## summary

Production overrides supersede the inherited-batch assumption: B1=2 and B2=1 make exactly 3,000 clean initial judge POSTs for 2,000 units (1,000 + 2,000). Current local scheduler uses coverage end_offset, not same batch-index barriers.

## files

```json
[
  {
    "path": "weblate/trans/judge_loop.py",
    "description": "_run_seats coverage/end_offset acknowledgement barrier around lines 1160-1238."
  },
  {
    "path": "weblate/trans/judge.py",
    "description": "Per-seat batch override resolution around lines 450-550 and initial request count around lines 815-830."
  }
]
```

## architecture

Seat 1 and seat 2 receive the same ordered request sequence, but each uses its own configured width. The coverage barrier releases an event only after every peer has persisted through that event's end offset; it is a bounded coverage barrier, not a strict equal batch-index barrier.

## report

With B1=2/B2=1, seat 1 emits [0,2], [2,4], ... while seat 2 emits [0,1], [1,2], [2,3], [3,4], ... . Seat 2's first event waits until seat 1 coverage reaches offset 1; once seat 1's [0,2] is persisted, seat 2 can advance through [1,2]. Seat 1's [0,2] is released only once seat 2 reaches offset 2. In general, a pending event waits for peer coverage >= its end_offset, so the narrower seat can issue multiple requests while the wider event is pending, but cannot pass the wider seat's current persisted coverage boundary. Fatal errors release pending/future events and abort workers. This explains 3,000 clean initial calls without retries or adaptive collapse. Equalizing widths would reduce fixed prompt/request overhead (B2=1 doubles seat-2 requests), but should be evaluated against model quality, latency, provider limits, and approval gates; no settings change is recommended from this investigation alone.

## Дополнительные сообщения в ходе исследования

Сохранены только тексты исследовательских выводов, без служебных записей сессии. Порядок хронологический; ранние предположения могут быть опровергнуты следующими сообщениями.

### Сообщение 1

Execution trace: AutoTranslate.preview_judge_scope selects first N per translation (autotranslate.py:741-773), BatchAutoTranslate globally decrements remaining cap across translations sorted component/language/pk (autotranslate.py:1240-1270). For each translation process_judge optionally MT-pretranslates writable IDs, then one run_judge_batch (autotranslate.py:832-902). run_judge_batch resolves both profiles, selected seats default (1,2), computes initial_paid_calls=sum(ceil(len(pending)/profile[seat].batch_size)); validates/caches each unit; _run_seats launches one thread per seat with same ordered requests, and barrier persists each batch before releasing corresponding seat (judge_loop.py:1320-1430, 1119-1238). Static preview count = sum ceil(strings/batch_size) (judge.py:815-830); defaults batch=5, seat overrides inherit global (defaults.py:61-70,87-89; settings_docker.py:1745-1797). Thus 2000 units, both seats batch 5 => 400+400=800 clean initial POSTs, not ~3000.

Actual outbound attempts per batch are not 1: judge.py:_run_batch (1770-1948) sends primary, retries transport/http transient/protocol according to JUDGE_*_RETRIES (defaults each 1), auth 401/403 adds exactly one fallback POST (fallback never retried), failover kinds can fallback once; multi-item parser failures trigger width-1 isolation, each unit recursively POSTed (fallback not multiplied in isolation); shared RetryBudget=max ceil(initial_paid_calls*JUDGE_RETRY_BUDGET_RATIO), default ratio .2, consumed by retry and isolation. request_verdicts re-reads adaptive seat batch budget before each batch; adaptive state can shrink profile batch after transport/deadline and persist across runs (judge.py:1944-2085; adaptive funcs ~1590-1640). Therefore effective requests = sum over rounds/seats/batches of primary POST + permitted retries + fallback + parser isolation, not simply ceil(N/5).

Rounds: run_judge_batch has unparsed retry rounds (default 1) only when every selected seat has unparsed rows, potentially another 2-seat pass; repair attempt loop max JUDGE_MAX_REPAIR_ATTEMPTS (default 1). For writable defect, repair_targets calls project routed MT (batch engine fetch_machinery_matches or per-unit if engine.batch_size==1; judge_loop.py:184-267), then changed units are rejudged in next pending round. Candidate/proposal path: each candidate is stored, then recursively run_judge_batch([unit], writable_ids=set(), candidate_targets=..., same selected seats), causing up to 2 more judge POSTs/unit; proposal-only disables MT/state-mutating repair (autotranslate.py:832-900). Producer proposal/recheck callers explicitly set proposal_only=True, pretranslate=False, mutating_repairs=False (api/producer/views.py:164-167,261-265; tasks.py:1711-1739). Deferral drain uses single selected seat, use_cache=False, bounded seat capacity (judge_loop.py:1950-1985).

Cache key is request_identity built from unit id, target hash, context hash (source/note/explanation/glossary/clarification), project_context hash, source/target languages, profile_fingerprint, prompt schema; _cached_verdict requires complete parsed rows for BOTH seats and exact model, and cache only applies when selected seats are both seats (judge_loop.py around _cached_verdict; run batch 1360-1410). Any target/context/profile/alias revision/prompt schema change invalidates. Concurrent redelivery: process_judge excludes already_done only non-PENDING JudgeRunUnit; simultaneous workers can both see PENDING and pay. _adopt_producer_run row lock does not mutex already RUNNING same task ID; saved investigation confirms duplicate runs/overlap hypothesis.

Exact formula for one invocation: Let R be repair/rejudge rounds; for each round r and seat s, E_rs=ceil(U_rs / B_eff_rs), where B_eff is adaptive current budget (<= configured batch), and each batch b has A=1 primary + T transport retries + H transient HTTP retries + P protocol retries, bounded by shared retry budget/deadline; + F fallback (at most 1 on auth/failover); + I parser isolation unit calls (only failed multi-item batches, budgeted). Add U_unparsed_retry extra rounds (only all-seat unparsed), and recursive candidate rejudge calls (each 2-seat, usually width 1). Repair MT calls are separate provider calls, not JudgeRequestAttempt; JudgeRequestAttempt/LLMUsageLog only count judge HTTP attempts. 3004/3031 vs 800 implies ~3.75 attempts/clean initial slot; plausible contributors are adaptive B_eff collapse + retries/fallback/isolation and duplicated/redelivered execution, but cannot attribute without per-attempt batch_size/attempt ordinal/failure_kind/round/subject and timing. Recorded Anvil data says repaired=0, but this does NOT rule out failed/empty repair MT; 10 unparsed second run supports retry/deferred paths only as possibility, not proof.

### Сообщение 2

Exact aggregation fields: JudgeRequestAttempt model (trans/models/judge.py:434-530): run FK, created_at, seat, attempt ordinal, provider, endpoint_fingerprint, model/model_fingerprint/profile_fingerprint/prompt_schema_version, batch_digest, batch_size, transport_succeeded, parsed, failure_kind, http_status, exception_class, finish_reason, response_shape, response_segment_count, elapsed_ms, first_byte_ms, response_bytes, prompt/completion/total/reasoning tokens, response_id. Group by run+seat+attempt ordinal+failure_kind+batch_size; count distinct batch_digest and sum elapsed only for attempts (never wall-clock sum). Join LLMUsageLog (trans/models/llm_usage.py:31-130) via request_attempt/run: created_at, service/model, project/component snapshots, target_language_code, prompt/completion/total/reasoning/cached tokens, cost_usd (null=unpriced), unit_count, batch_size, outcome/refusal_reason. JudgeRunUnit (judge.py model lines 690-794): run/unit snapshot, translation/component/project, outcome/skip_reason, verdict, repair_status, initial/final severity, attempt_count, cached, candidate hashes/ids, projection_succeeded. JudgeVerdict lines 795-930: unit, seat, attempt (repair cycle), request_round (transport recovery), subject live/candidate, target/context/profile/prompt hashes, request_attempt FK, provider/model, unparsed. These fields permit exact decomposition by round, subject, seat, provider, batch width, parsed/failure, tokens/cost. Also aggregate JudgeAdaptiveState (judge.py model ~510-560): endpoint/model/seat batch_budget, clean streak, failure streak/circuit state; it explains adaptive effective-width changes.

### Сообщение 3

Arithmetic nuance: 3000 JudgeRequestAttempt rows/run over 2000 units is 3000 POSTs, versus 800 clean static batches (400 per seat). If every selected unit got one call per seat, observed avg effective batch width = (2*2000)/3000 ≈1.33 requests' worth (before extra retries/fallback); adaptive seat budget collapse from 5→2→1 is a code-supported mechanism (transport/deadline halves shared JudgeAdaptiveState budget, never exceeds profile; judge.py adaptive funcs). But this is not proof adaptive caused it: concurrent redelivery can duplicate work while PENDING, and retries/fallback/unparsed rounds add attempts. Need production query timeline by attempt created_at and batch_size + adaptive state history (only current state stored) to separate.

### Сообщение 4

Fresh B1=2/B2=1 fully explains ~3000 clean initial POSTs: E_initial=ceil(2000/2)+ceil(2000/1)=1000+2000=3000, exactly matching attempts; prior 800 baseline assumed inherited B=5 and is superseded. Seat override names are JUDGE_BATCH_SIZE_SEAT_1/2 and config snapshots/attempts prove production values. No need invoke adaptive/retry hypotheses for Anvil clean-count; still report retries separately but clean 3000 is expected.

_barrier current code uses coverage end_offset (judge_loop.py _run_seats ~1160-1238), not same batch index. With B1=2 and B2=1: seat1 event [0,2] and seat2 [0,1] persist; seat2 cannot ack until peer coverage>=1; once seat1 [0,2] is persisted, seat2 [0,1] releases, then seat2 [1,2] may run; seat1 [0,2] releases only when seat2 coverage>=2. This lets seat2 finish requests covering up to seat1's current end offset (not race ahead beyond it), while seat1 waits. Then seat1 [2,4] can be pending while seat2 [2,3],[3,4] catch up; bounded look-ahead is <= peer event end_offset, but unequal widths create multiple seat2 POSTs per seat1 event. Fatal peer errors release all pending/future events via _SeatAbortedError. Recommendation: do not claim a strict same-batch-index barrier; describe coverage/end-offset barrier. For cost optimization, equalize seat batch widths only if quality/latency permits (e.g. both 2 or 5), because B2=1 deterministically doubles seat2 request count and fixed prompt overhead; however changing overrides needs evaluation/approval and may alter provider behavior. Keep independent seat models if quality requires, and measure tokens/latency before widening.
