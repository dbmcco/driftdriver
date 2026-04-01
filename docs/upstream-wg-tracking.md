# Upstream Workgraph Branch Tracking

Tracking Erik's active branches for impact on driftdriver when they merge to main.
**Last sync: 2026-03-30** — rebased `dbmcco/workgraph:sync-upstream-2026-03-25` onto
`graphwork/workgraph:main` @ `0742071`. Fork is now 1 commit ahead (coordinator guards patch).

## Landed in Main (2026-03-25 → 2026-03-30)

The following branches from the previous tracking doc have merged to upstream main:

### `infra-fix-toctou` / `fix-toctou-race` — **LANDED**
TOCTOU fix (`modify_graph`), self-healing coordinator, compactor, `wg retract`,
`wg decompose`, `wg cascade-stop/hold/unhold`, atomic task addition, liveness detection.
All features now in upstream main.

**Driftdriver follow-up (still pending):**
- `_clear_stale_graph_locks()` — now a no-op, keep as safety net
- stalledrift vs coordinator self-healing — evaluate overlap
- Add `wg retract` / `wg cascade-stop` / `wg decompose` to factory brain directive vocabulary
- Evaluate `wg compact` vs our drift-check context assembly

### `fix-auto-task-edges` — **LANDED**
`wg service restart` and bidirectional auto-task edges are in upstream main.
**Pending:** update factory brain to use `wg service restart` instead of kill+start.

## What's In Current Upstream (0742071, 2026-03-30)

Notable features now available in upstream main vs our last sync point:
- **User boards** (`.user-NAME`) — new primitive, auto-created, TUI integration
- **`flip_enabled` defaults to true** — UX/coordinator behavior change
- **Cycle-aware readiness** (back-edge detection) — may affect stall detection logic
- **`user-board` tag in DAEMON_MANAGED_TAGS** — tasks tagged user-board skip agent spawn
- **`design_experiment` signature change** — now takes `&source_id` param
- **`save_graph` replaced with `modify_graph`** everywhere in coordinator
- **Docs sync** — COMMANDS.md, SKILL.md, AGENCY.md, README all updated (March 28)
- **Coordinator chat labels** — use actual task ID numbers, not indexes
- Inspector/TUI polish (border drag snap fix, archived cycle suppression)

## Active Upstream Branches to Watch

### `research-agency-primitive` (docs only)
Design doc for agency primitive sync model. Monitor for API changes when this lands.

### `mu-l-toasts` (new branch)
UI toasts. No driftdriver impact expected.

## PAIA Multi-User Evaluation: Workgraph User Board System

**Date:** 2026-04-01 | **Task:** upstream-wg-multiuser

### What It Is

The `.user-NAME` system is a per-human presence primitive that mirrors the `.coordinator-N` pattern.
A user board is a regular `Task` node with:
- ID format `.user-{handle}-{N}` (e.g., `.user-sam-0`, `.user-braydon-0`)
- `user-board` tag (which puts it in `DAEMON_MANAGED_TAGS` — coordinators skip agent spawn for these)
- `InProgress` status while active; `wg done` archives it and auto-creates `N+1`
- Messages stored in the standard `.workgraph/messages/` JSONL infrastructure
- Identity sourced from `WG_USER` env var → `USER` → `"unknown"` fallback chain

**CLI interface:**
```bash
wg user init [NAME]       # explicit creation (lazy-created on first msg send)
wg user list              # show all boards
wg user archive [NAME]    # done + create successor
wg msg send .user-sam "..." # alias resolves to active .user-sam-N
```

**Coordinator integration (as of 2026-03-28 → 2026-03-31):**
- Coordinator startup auto-creates a board for `current_user()` if none exists
- Chat messages forwarded to the user board as audit trail (routing context: `user [coord:N]: ...`)
- Context injection into coordinator was added then **reverted** (commit ca0e102) — it caused
  message leakage and feedback loops. Current state: audit trail only, no injection.

### PAIA Applicability

**The question:** Does `.user-NAME` enable Sam/Derek/Ingrid to have isolated task namespaces
in the same workgraph? Does it clean up the `board:*` pattern from paia-work?

**Findings:**

1. **Not a task namespace.** User boards are audit/presence surfaces, not task containers.
   Tasks are not scoped *under* a user board — they remain in the flat graph. There is no
   "assign task to user board" or "user board owns tasks" mechanism. The design doc is explicit:
   user boards are "human presence markers," not task queues.

2. **Agent-identity use (Sam/Derek/Ingrid) is possible but tangential.** Each agent could
   run with `WG_USER=sam` and get a `.user-sam-0` board auto-created. That board would capture
   chat messages sent by Sam's coordinator — but it would not isolate Sam's tasks from Derek's.
   You'd still need the `board:sam` tag pattern or explicit dependency edges for task separation.

3. **Cleaner alternative to `board:*` tags — maybe, in a future phase.** The user board gives
   each agent a persistent, named message surface without custom tag conventions. For the PAIA
   use case (Sam, Derek, Ingrid as named agents coordinating in a shared graph), you could
   replace `board:sam` task tags with `wg msg send .user-sam "task result"` for agent-to-agent
   communication. But this is a different communication pattern, not a drop-in replacement.

4. **`DAEMON_MANAGED_TAGS` impact.** The `user-board` tag prevents coordinators from dispatching
   agents to user board tasks. This is correct and desired — but means if you ever tag a task
   `user-board` accidentally, it becomes invisible to the dispatch loop. Low risk, worth noting.

5. **Multi-user server work is separate.** The `mu-*` commits add multi-user TUI and per-coordinator
   state, but the server-side multi-user architecture (separate auth, isolated graphs) is still
   in planning (`docs/plans/`). The `.user-NAME` primitive is client-side, single-graph.

### Recommendation: **Adopt in Phase 2 — defer active use, watch the `mu-*` track**

**Phase 1 (now):** No action required. The `user-board` tag is now in `DAEMON_MANAGED_TAGS`
— just don't accidentally tag regular tasks with it. The board auto-creation on coordinator
startup is benign.

**Phase 2 (next upstream sync, likely ~1 month):** Once the multi-user server work lands,
revisit. The combination of named user boards + `WG_USER` propagation to spawned agents
(already in `spawn/execution.rs`) gives PAIA agents a real identity layer. At that point,
route inter-agent messages through `.user-NAME` boards instead of custom `board:*` tags.

**Don't adopt now because:** The context injection was reverted (still unstable), the server-side
multi-user design is in flux, and the current `board:*` pattern in paia-work works fine.

## Action Items

1. **Add `wg service restart` to factory brain** — replace kill+start pattern
2. **Evaluate stalledrift vs self-healing** — coordinator now handles failure diagnosis natively
3. **Add `wg retract` / `wg cascade-stop` / `wg decompose` to directive vocabulary**
4. **Evaluate `wg compact` vs context generation** — may replace drift-check context assembly
5. **User board tag** — if we ever create board-tagged tasks, they'll be daemon-managed
6. **Test `_clear_stale_graph_locks()`** — verify no-op with flock-based writes
7. **[Phase 2] PAIA agent identity via `.user-NAME`** — when mu-* multi-user server lands,
   route inter-agent messages through user boards instead of `board:*` tags
