# Upstream Workgraph Branch Tracking

Tracking Erik's active branches for impact on driftdriver when they merge to main.
Local main is 457 commits behind origin/main as of 2026-03-12.

## Critical Branches (Direct Driftdriver Impact)

### `infra-fix-toctou` (31 commits)

**What:** Replaces file-based graph.lock with flock-based `mutate_graph` — atomic
read-modify-write cycle prevents TOCTOU races entirely.

**Driftdriver impact:**
- Our `_clear_stale_graph_locks()` becomes unnecessary once this lands — zero-byte
  lock files stop being created. Keep it as a safety net for transition period.
- The `wg show` timeout early-return in `drift_task_guard.py` becomes less critical
  but remains good defensive code.
- `timeout=10.0` on dedup `wg show` calls should stay regardless.

**Monitor for:** Changes to graph.lock semantics. If flock replaces file-creation,
our sweep function won't find anything to clean (which is fine).

### `fix-toctou-race` (59 commits, superset branch)

**What:** Mega-branch merging TOCTOU fix + several features:
- **Self-healing** — automatic failure diagnosis wired into coordinator tick loop
  (`safety-self-healing`). Overlaps with our `stalledrift` lane.
- **Compactor MVP** — `wg compact` generates `context.md` from graph state.
  Overlaps with our context-generation in drift checks.
- **`wg retract`** — provenance-based undo of task side effects. New capability
  we should expose through factory actions.
- **Liveness detection** — sleep-aware stuck agent handling. Overlaps with our
  worker monitor (`worker_monitor.py`).
- **Atomic task addition** — spawn-time dep re-check + dispatch grace period.
  May change `wg add` timing behavior our factory relies on.
- **`wg decompose`** — task self-decomposition. New capability.
- **`wg cascade-stop/hold/unhold`** — subtree control. New safety operations
  the factory brain could issue.
- **Live dependency enforcement** — pauses in-progress tasks with unmet deps.
  Could affect our stall detection logic.

**Monitor for:**
- Self-healing overlap with stalledrift — may want to disable stalledrift when
  wg coordinator handles it natively.
- `wg add` interface changes from atomic task addition.
- New commands to add to factory brain directive vocabulary.

### `fix-auto-task-edges` (4 commits)

**What:**
- `wg service restart` command (log caller identity on stop)
- Fix bidirectional edges for auto-created system tasks

**Driftdriver impact:**
- Factory brain currently kills + restarts daemons manually. `wg service restart`
  gives us a clean single command for this.
- Auto-task edge fix may change how dependency chains resolve in our factory cycles.

**Monitor for:** `wg service restart` availability — update factory brain to use it.

## Lower Priority Branches

### `fix-before-edges` (11 commits)
Before-edge normalization into after-edges. Internal graph representation change.
Shouldn't affect driftdriver unless we parse graph.jsonl directly (we don't — we use `wg` CLI).

### `fix-output-section` (30 commits)
TUI detail view improvements (collapse toggle, tail preview). No driftdriver impact.

### `show-live-token` (10 commits)
Live token count in TUI. No driftdriver impact.

### `tui-disable-fade` / `tui-pink-lifecycle` (3 commits total)
TUI visual polish. No driftdriver impact.

## Action Items When Merging

1. **Rebase local main** — we're 457 commits behind, rebase before next feature work
2. **Test `_clear_stale_graph_locks()`** — verify it's a no-op when flock is in place
3. **Evaluate stalledrift vs self-healing** — if wg coordinator handles failure
   diagnosis natively, stalledrift may become redundant for enrolled repos
4. **Add `wg service restart` to factory brain** — replace kill+start pattern
5. **Add `wg retract` / `wg cascade-stop` to directive vocabulary** — new safety
   operations the brain should be able to issue
6. **Test `wg add` timing** — atomic task addition + grace period may affect factory
   cycle timing assumptions
7. **Evaluate compactor vs context generation** — `wg compact` may replace some of
   our drift-check context assembly
