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

## Action Items

1. **Add `wg service restart` to factory brain** — replace kill+start pattern
2. **Evaluate stalledrift vs self-healing** — coordinator now handles failure diagnosis natively
3. **Add `wg retract` / `wg cascade-stop` / `wg decompose` to directive vocabulary**
4. **Evaluate `wg compact` vs context generation** — may replace drift-check context assembly
5. **User board tag** — if we ever create board-tagged tasks, they'll be daemon-managed
6. **Test `_clear_stale_graph_locks()`** — verify no-op with flock-based writes
