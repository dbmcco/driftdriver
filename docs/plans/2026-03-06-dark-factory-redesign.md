# Dark Factory Redesign — Speedrift Ecosystem v2

**Date:** 2026-03-06
**Status:** Approved design
**Approach:** Clean-room redesign preserving lessons learned

## North Star

An autonomous, self-improving dark factory for managing agents. Human sets direction, system handles everything else. Human does UAT on significant application changes only. Runtime-agnostic — any agent (Claude Code, Codex, Amplifier, custom) is just a worker.

## Design Decisions

- **Task graph is the spine** — workgraph is the single source of truth for all state
- **Six services** — Planner, Graph, Dispatch, Quality, Learning, Gate
- **Advisory not blocking** — findings create follow-ups, never hard-stop work
- **Runtime-agnostic dispatch** — workers register capabilities, dispatch matches requirements
- **Tiered learning** — outcome > quality > process > environmental signals
- **Clean-room design** — but ~85% of existing LOC (workgraph + lane tools + templates) carries forward

## Architecture

```
  Human
    |
  [Gate] ← dashboard, UAT, notifications
    |
  [Planner] → decomposes goals into task subgraphs
    |
  [Graph] ← single source of truth (workgraph)
    |
  [Dispatch] → assigns tasks to agent runtimes
    |
  [Quality] → runs drift checks, creates follow-ups
    |
  [Learning] → records outcomes, distills knowledge, feeds back
    ↺ (back to Planner, Dispatch, Quality)
```

All six services read from and write to the Graph. The arrows show primary data flow, but every service can query any other through the Graph.

---

## Service 1: Graph

**Responsibility:** Owns all tasks, dependencies, state transitions, messaging. The only place state lives.

**Current code:**
- `workgraph/` (129K lines Rust) — keeps as-is, already the most mature piece
- `driftdriver/wg_ipc.py` (107 lines) — keeps, Python IPC client

**Changes:**
- Parallel state stores get eliminated. Currently speedriftd maintains snapshot.json, worker.ledger.jsonl, control.json, plus autopilot_state.py and execution_state.py. All of this becomes **task metadata on graph nodes**.
- Graph exposes a service API via IPC — other services talk to it through the socket, not by shelling out to `wg`.

**Deletes:**
- `autopilot_state.py` (106 lines) — state moves to graph nodes
- `execution_state.py` (107 lines) — state moves to graph nodes

---

## Service 2: Planner

**Responsibility:** Decomposes goals into task subgraphs with contracts, dependencies, and lane hints. Pure planning — writes tasks to Graph, does not dispatch or check quality.

**Current code:**
- `project_autopilot.py` (932 lines) — extract ~300 lines of decomposition logic, discard dispatch/quality
- `contract_enrichment.py` (142 lines) — keeps, wire into automatic invocation on task creation
- `plandrift.py` (604 lines) — keeps as planning-time validation lane

**Changes:**
- `project_autopilot.py` currently tries to be Planner + Dispatch + Quality. Extract decomposition, discard the rest.
- Contract enrichment gets called automatically when Planner creates tasks — currently dead code nobody invokes.

**Deletes:**
- `pm_coordination.py` (195 lines) — dispatch responsibility moves to Dispatch service

---

## Service 3: Dispatch

**Responsibility:** Assigns ready tasks to available agent runtimes. Runtime-agnostic — workers register capabilities, Dispatch matches task requirements.

**Current code:**
- `speedriftd.py` (645 lines) — extract ~200 lines of dispatch logic, drop state management
- `worker_monitor.py` (206 lines) — keeps, dead agent detection
- `continuation.py` (111 lines) — keeps, CONTINUE/STOP/ESCALATE decisions
- `loop_detection.py` (73 lines) — keeps, repeated action fingerprinting
- All adapter templates (claude-code, codex, amplifier, opencode) — keep

**Changes:**
- `speedriftd.py` currently mixes dispatch with state management and supervision. Extract dispatch logic only.
- Workers register capabilities (languages, tools, autonomy level). Dispatch matches requirements.
- Separate state files (snapshot.json, control.json, worker.ledger.jsonl) become graph metadata.

---

## Service 4: Quality

**Responsibility:** Runs drift checks against work in progress. Routes to lanes based on evidence. Creates follow-up tasks for findings.

**Current code:**
- `cli/check.py` (731 lines) — keeps, simplify
- `smart_routing.py` (212 lines) — keeps
- `routing_models.py` (127 lines) — keeps, decide: wire to real model or simplify to rule-based
- `policy.py` (1,088 lines) — split into routing policy + enforcement policy
- `scope_enforcement.py` (103 lines) — keeps
- `verification.py` (160 lines) — keeps
- All 10 external lane tools — keep as-is
- 6 internal lanes (factorydrift, northstardrift, qadrift, secdrift, plandrift, contrariandrift)

**Changes:**
- Internal lanes (qadrift, secdrift, factorydrift, etc.) follow the same plugin contract as external lanes. Currently they bypass the standard interface.
- `smart_routing.py` calls `parse_routing_response("")` with empty string — either wire to real model or drop to rule-based.
- `policy.py` split: routing decisions vs. enforcement actions.

**Deletes:**
- `adversarial_review.py` (143 lines) — dead code, nothing imports it
- `contrariandrift.py` (251 lines) — dead code, nothing imports it
- `tool_approval.py` (184 lines) — dead code, nothing imports it

---

## Service 5: Learning

**Responsibility:** Closes the feedback loop. Records outcomes, distills patterns, feeds knowledge back into Planner (contract enrichment), Dispatch (lane weights), and Quality (routing evidence).

**Current code:**
- `reporting.py` (566 lines) — keeps, the distillation logic is solid
- `outcome.py` (96 lines) — keeps
- `knowledge_priming.py` (141 lines) — keeps
- `cold_distillation.py` (109 lines) — rewrite, add decay
- `event_bridge.py` (109 lines) — keeps
- `self_reflect.py` (152 lines) — rewrite, integrate into graph event listener

**Changes:**
- Learning is always-on, not just at session end. Every task completion and drift finding records immediately.
- Knowledge gets **decay** — stale findings that haven't been confirmed recently lose confidence.
- Cross-repo knowledge sharing needs quality gates — high-confidence in one repo may be irrelevant in another.
- Current pending.jsonl queue (nothing reads until session end) replaced by real-time graph event listening.

**Deletes:**
- `project_profiles.py` (115 lines) — subsume into Learning analytics

**Learning tiers (by predictive value):**
1. **Outcome signals** (highest) — what happened after a recommendation
2. **Quality signals** — drift findings, test results, verification
3. **Process signals** — how long tasks take, which lanes fire, dispatch patterns
4. **Environmental signals** — repo structure, tech stack, team patterns

---

## Service 6: Gate

**Responsibility:** Human touchpoint. Surfaces significant changes for UAT, aggregates findings, provides dashboard and notifications.

**Current code:**
- `ecosystem_hub/` (5,953 lines, 7 modules) — keeps as presentation layer
- `health.py` (247 lines) — keeps
- `updates.py` (840 lines) — simplify, aggregation logic moves to Learning

**Changes:**
- Hub becomes a thin presentation layer over Graph + Learning data, not its own aggregation engine.
- Add notification channels (Slack/Matrix/email) for proactive alerts when UAT is needed.
- "Significant change" threshold learned from outcome history — which reviews actually resulted in changes vs. rubber stamps.

---

## What Carries Forward from Speedrift

1. **Task contracts as source of truth** — embedded in task descriptions, survive transitions
2. **Advisory-not-blocking** — findings create follow-ups, never hard-stop
3. **Policy-driven via TOML** — modes, lane ordering, enforcement levels
4. **Lane plugin architecture** — external tools with standard interface
5. **Evidence-based routing** — file classifications, change context, pattern hints
6. **Outcome tracking → lane weights** — compute_lane_weights() mechanism
7. **Adapter pattern for runtimes** — different CLI tools get different hook configs
8. **Stall/loop detection** — agents get stuck; catching it early matters

## What Doesn't Work (Anti-Patterns to Fix)

1. **Parallel state stores** — speedriftd, autopilot_state, execution_state all competing with the graph
2. **Dead code accumulation** — 4 modules (703 lines) with tests but no production callers
3. **Aspirational wiring** — "model-mediated" routing that calls parse_routing_response(""), contract_enrichment never called
4. **Session-end-only learning** — pending.jsonl queue that nothing reads until the session ends
5. **Monolithic policy** — 1,088 lines mixing routing, autonomy, intervals, and enforcement
6. **Internal lanes bypassing plugin contract** — qadrift/secdrift/factorydrift don't follow standard interface

---

## Impact Summary

| Decision | Lines | Description |
|----------|-------|-------------|
| Keep as-is | ~140K | workgraph (Rust), 10 lane tools, templates, adapters |
| Keep + rewire | ~3K | reporting, outcome, knowledge_priming, smart_routing, scope_enforcement, verification, event_bridge, contract_enrichment |
| Rewrite | ~4K | policy (split), project_autopilot (extract), speedriftd (extract), internal lanes (standardize), cli (simplify) |
| Delete | ~1K | adversarial_review, contrariandrift, tool_approval, autopilot_state, execution_state, pm_coordination, project_profiles |
| Shrink | ~6K | ecosystem_hub (thin out aggregation), updates |

**~85% of LOC survives.** This is a reorganization with targeted rewrites, not a ground-up rebuild.

---

## Open Questions

1. **Model-mediated routing:** Wire to a real model call (adds latency + cost) or stay rule-based (works fine today)?
2. **Knowledge decay rate:** Linear decay? Exponential? Confirmation-based reset?
3. **Notification channels:** Start with terminal notifications or go straight to Slack/Matrix?
4. **Internal lane standardization:** Wrap existing code in plugin interface or rewrite as standalone tools?
