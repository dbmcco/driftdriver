# Claude Code Leak — Patterns Valuable for Speedrift
*2026-04-01. Research output from PlanForge session 20260401-141310.*

These are architectural patterns extracted from the Claude Code CLI source leak (March 2026,
512K lines TypeScript). We are adopting the **patterns only** — no code reuse.

---

## Patterns We Are Taking

### 1. KAIROS Tick Loop — Decide-First Gate

**What it is:** The Claude Code daemon receives a periodic tick. On each tick it runs a
lightweight decide-first gate: should I proactively act, or stay quiet? Only if the gate
says act does it proceed with the more expensive action. Budget enforcement (15-second
blocking per action) is built into the loop, not bolted on.

**Why it's good:** Replaces the always-sweep monolith pattern (scan everything, act on
everything) with a decide-first gate. The expensive sweep happens only when the gate
determines it's warranted. This is how Claude Code stays always-on without burning compute.

**What we're doing with it (Phase 2, not Phase 1):** The speedrift signal gates already
implement this idea at the call-site level (content-hash dedup before LLM invocation). If
Phase 1 data shows the monolithic factory brain sweep is still too costly after signal gates,
the KAIROS architecture is the refactor path: split the brain into a heartbeat loop (cheap,
always-on, decide-first) and a quality loop (expensive, idle-triggered, deep analysis).

**Status:** YAGNI for Phase 1. Implement only if Phase 1 cost data justifies it (trigger:
>$2/hr sustained for 7 consecutive days after signal gates are live).

---

### 2. autoDream Consolidation — Idle-Triggered Memory

**What it is:** During idle periods (no interactive activity, no active tasks), the agent
consolidates its observations into durable facts: merging duplicates, resolving contradictions,
converting soft observations into hard facts in its memory store. Triggered by idle detection,
not a schedule.

**Why it's good:** Avoids the scheduled-batch problem — a nightly job runs whether there's
anything to consolidate or not. Idle-triggered means it fires when the machine has spare
capacity and stops immediately when interactive activity resumes.

**What we're doing with it:** This maps directly onto `northstardrift`'s daily rollup, which
is currently scheduled. If the rollup moves to idle-triggered (session.ended event or
presence heartbeat going stale), it avoids running during interactive sessions and produces
richer context because it has the full session's events.jsonl to work from.

**Status:** Phase 4 refinement. Wire in after northstardrift.daily_rollup is operational.
The session.ended hook is the trigger point — northstardrift consolidation runs when that
event fires and no new session.started arrives within the debounce window.

---

### 3. AGENT_TRIGGERS — Push vs Pull

**What it is:** Instead of polling for new events on a timer, the Claude Code agent registers
push-based webhook/event subscriptions. When a relevant event fires (file change, test failure,
PR opened), the subscription pushes directly to the agent. No polling loop required.

**Why it's good:** Eliminates the latency and waste of a polling cycle. The agent reacts to
real events at event time, not at the next poll interval. This is how the agent stays
responsive without constant sweeping.

**What we're doing with it:** The events.jsonl pipeline (Phase 0 task) is the minimal version
of this: wg task lifecycle events are written to events.jsonl, and the factory brain reads
them. A more complete version would have wg hooks push events directly to the brain process
via a named pipe or Unix socket, eliminating even the file-poll.

**Status:** events.jsonl file-based is Phase 0. Direct push via Unix socket/named pipe is a
Phase 2 refinement if file-poll latency proves to be a problem.

---

## Patterns We Are Not Taking

### ULTRAPLAN — Dedicated Opus Think Budget

**What it is:** Complex planning (architecture decisions, multi-step decomposition) is
offloaded to a dedicated Opus instance with up to 30 minutes of think time. The main loop
doesn't block on planning; it hands the problem off and continues.

**Why we're not using it:** YAGNI. PlanForge already uses Opus via wg tasks with no hard
time budget. The quality of planning outputs is already high. Adding a dedicated 30-minute
Opus invocation would triple planning costs without a demonstrated quality gap to justify it.

---

### Multi-Tier LLM Heartbeat Loop (Haiku → Sonnet → Opus)

**What it is:** Three tiers of LLM, each handling different complexity levels. Haiku for
fast/cheap routine checks, Sonnet for moderate analysis, Opus for complex judgment calls.
The heartbeat loop uses Haiku to avoid cost at high polling frequency.

**Why we're not using it:** Cost math doesn't work for our setup. Haiku at 40+ repos ×
60-90 second poll interval = 27-40 API calls/minute. At ~2K tokens/call, that's ~$3.5/hr —
still above our $5/hr kill threshold and not much cheaper than the signal-gated monolith.
The signal gate approach (hash-based suppression before any LLM call) achieves the same
cost control without adding a polling architecture.

---

## Architecture Insight: Narrow Reconciliation Loops

The deeper pattern in Claude Code is not any single feature but the overall architecture:
**narrow, single-responsibility loops that compose without coupling**.

- Each loop has one job (heartbeat, dispatch, quality)
- Each loop degrades gracefully when peers are missing (partial-boot)
- Each loop has its own cycle time appropriate to its job (seconds vs minutes vs idle)
- No shared state except well-defined handoff points (events.jsonl, task graph, signal gate state)

This is structurally identical to K8s controllers. It's the right long-term architecture for
speedrift. The reason we're not building it in Phase 1 is that we don't have enough operational
data to know which loops the monolithic brain should be split into — and splitting before
validation risks replicating the brain's bugs in smaller containers.

**The monolithic factory brain should be validated first. Narrow loops are the Phase 2
refactor target if and when the monolith proves too costly after signal gates.**

---

## Python Port Reference

A Python port of the Claude Code patterns was available at the time of research. We did not
incorporate its code. The patterns above were extracted from analysis of both the TypeScript
source and the Python port's interpretations. All implementations in speedrift should be
original Python following our existing codebase patterns.

---

## Implementation Tracking

These patterns are tracked in the wg task graph under the `dark-factory-autonomy` root task.
Signal gate fix (hash stability) and events.jsonl wiring are in Phase 0 tasks. KAIROS/autoDream
are deferred to Phase 2 with a cost-threshold trigger defined in the spec.

See: `docs/superpowers/specs/2026-04-01-dark-factory-autonomy-spec.md`
