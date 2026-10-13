# Lab 25B — Game Day Report

**Họ tên / nhóm:** Dinh Van Binh / 2A202602830  
**Commit:** điền sau khi commit  
**Điểm tự động:** 84/88

## 1. Kết quả Game Day

| Băng | Client | Availability | p95 (s) | Amplification | Retry ratio | Cost/turn | Recovery (s) | PASS |
|---|---|---:|---:|---:|---:|---:|---:|:---:|
| G1 | naive | 0.7194 | 7.157 | 7.738 | 6.738 | 2.163 | 4.0 | No |
| G1 | resilient | 1.0000 | 4.764 | 1.061 | 0.001 | 2.090 | 0.0 | Yes |
| G2 | naive | 0.4560 | 15.034 | 9.904 | 8.904 | 4.627 | 155.0 | No |
| G2 | resilient | 1.0000 | 10.822 | 1.041 | 0.007 | 2.111 | 0.0 | Yes |
| G3 | naive | 1.0000 | 4.768 | 1.164 | 0.164 | 2.328 | 0.0 | No |
| G3 | resilient | 1.0000 | 4.668 | 1.059 | 0.030 | 2.143 | 0.0 | Yes |
| G4 | naive | 1.0000 | 13.764 | 1.004 | 0.004 | 2.008 | 0.0 | No |
| G4 | resilient | 1.0000 | 5.626 | 1.014 | 0.002 | 2.072 | 0.0 | Yes |
| G5 | naive | 0.3699 | 5.359 | 17.030 | 16.030 | — | — | No |
| G5 | resilient | 0.8179 | 5.477 | 1.003 | 0.002 | 2.504 | — | Yes |
| G6 | naive | — | — | — | — | — | — | No |
| G6 | resilient | 0.9572 | 5.882 | 1.028 | 0.012 | 2.232 | 0.0 | Yes |

The resilient client passed all six tapes. It uses capability-aware routing,
full jitter, shared cooldowns, retry budget and a sliding-window breaker.

## 2. Configuration and rationale

| Parameter | Value | Rationale |
|---|---:|---|
| attempt_timeout_s | 8.0 | Leaves time in the 30-second user deadline for failover. |
| max_attempts_per_provider | 2 | Bounds amplification; G2 measured 1.041. |
| backoff_base_s / cap_s | 0.25 / 4.0 | Jitter spreads retries while keeping recovery short. |
| retry_budget_ratio | 0.1 | Limits retry traffic during overload and brownout. |
| breaker window/min_calls | 20 / 10 | Detects sustained degradation without opening on a few calls. |
| failure threshold | 0.5 | Opens on a majority of health-relevant failures. |
| slow_call_s / threshold | 5.0 / 0.6 | Detects G4 slow calls before timeout amplification. |
| open_duration_s | 10.0 | Allows recovery probes without a thundering herd. |
| half-open probes/successes | 2 / 2 | Small controlled recovery sample. |

Decision summary: client errors are released and given up; quota and rate
limit errors release the permit and fail over with cooldown; overload, server
errors and timeout count toward the breaker and retry only when budget and
deadline permit.

## 3. Experiments

1. The retry budget is a last line of defence if breaker configuration is
   wrong. With the normal configuration G2 amplification was 1.041 and G3
   retry ratio was 0.030; removing the budget allows transient errors to
   multiply even when a breaker eventually opens. Both mechanisms are needed:
   the breaker protects provider health, while the budget protects the system
   during delayed or misconfigured breaker decisions.
2. A 30-second attempt timeout consumes the whole user deadline and leaves no
   useful time for fallback. Availability can decrease while successful-call
   p95 appears better because failed calls are excluded (survivorship bias).
   G4 is mostly unchanged because it has no provider errors; slow-call
   detection and a bounded timeout are the relevant controls.
3. Removing tool capability filtering would route tool calls to `gamma-mini`,
   which cannot execute tools. For a product manager, this means the system
   would choose a cheaper model that cannot complete the requested action,
   producing avoidable failed customer turns.

## 4. Workflow

| Executor | Completion | Crashes | Duplicate refunds | Duplicate emails | Lost refunds | Overpaid | Wasted LLM/crash |
|---|---:|---:|---:|---:|---:|---:|---:|
| naive | 1.000 | 81 | 128 | 81 | 0 | 1,496,700 | 1.123 |
| yours | 1.000 | 66 | 0 | 0 | 0 | 0 | 0.303 |

Idempotency alone does not avoid recomputing completed read-only steps, while
checkpointing alone cannot deduplicate a side effect whose response was lost.
The side effect must execute with a stable key, then its result must be
checkpointed. Writing a checkpoint before payment could skip the payment
after a crash and create a lost refund.

## 5. Alerting and error budget

The burn-rate alerter produced 3 pages, 1 ticket and 0 false pages. It detected
checkout_bug, provider_outage and degraded_evening as pages, slow_leak as a
ticket, and correctly did not page for deploy_blip, night_spike or small_bump.
The 30-day report contains 25,901,755 requests, 55,618 bad requests,
availability 0.997853, 42.95% budget consumed and 57.05% remaining, so release
freeze is not required.

## 6. Postmortem — G2

The trigger was a 75% GPU capacity loss on alpha at virtual t=120. The root
cause was the naive three-layer retry design: fixed delays, no jitter, no
budget and no breaker amplified load and filled the provider queue.

| Time | Event | Evidence |
|---:|---|---|
| 120 | Alpha loses GPU capacity | overload begins |
| 150 | Retry storm grows | naive amplification reaches high single digits |
| 180 | Queueing causes timeouts | 2,737 client timeouts in the tape |
| 240 | Retries continue against overloaded alpha | 34,475 attempts total |
| 300 | Provider capacity is restored | recovery point in scenario |
| 455 | User impact persists | naive recovery is 155 seconds |

At t=300 the provider was healthy, but requests abandoned by clients were
still occupying queue capacity and new retries kept arrival rate above service
capacity. This is the metastable feedback loop: timeout → retry → queue growth
→ more timeout. The resilient client limits amplification to 1.041, fails over
and recovers without that loop.

Action items:

| # | Action | Type | Owner | Verification |
|---:|---|---|---|---|
| 1 | Enforce one retry layer with full jitter and retry budget | prevent | Platform | G2 amplification <= 1.2 in CI |
| 2 | Alert on amplification, timeout rate and queue recovery | detect | SRE | Synthetic G2 page within the burn-rate window |
| 3 | Use capability-aware fallback and breaker probes | mitigate | Agent team | G6 has zero incompatible routes |

## 7. Remaining weakness

G2 successful-call p95 is still 10.822 seconds. A future improvement is
adaptive per-provider timeouts based on normal p99 latency and remaining user
deadline, while preserving enough time for one compatible failover.

## 8. Cache

| Client | Availability | FAQ availability | Hit ratio | Stale served | Wrong | Outdated | Cost/turn | PASS |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| resilient | 0.8380 | 0.8430 | 0.000 | 0 | 0 | 0 | 1.386 | No |
| cached | 0.9113 | 0.9634 | 0.580 | 153 | 0 | 4 | 0.965 | Yes |

The final cache configuration is `similarity_threshold=0.97`,
`ttl_s=60`, `max_stale_s=300`, `max_entries=500`. The high similarity
threshold prevents semantically close but different questions from sharing an
answer. The short TTL limits silent shipping-document changes; the stale
window preserves FAQ availability during the provider outage. Personal
questions are never cached because even a per-user cache can return stale or
incorrect account state. During stale serving, alerting should use provider
error rate, stale-served count and cache-miss/freshness metrics, not only user
availability.
