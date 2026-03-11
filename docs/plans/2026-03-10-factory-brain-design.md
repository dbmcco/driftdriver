# Self-Healing Model-Mediated Dark Factory — Design

**Goal:** Replace the heuristic dark factory with a model-mediated brain that autonomously manages repo enrollment, agent dispatch, failure recovery, and resource allocation across the Speedrift ecosystem.

**Architecture:** The factory brain lives inside driftdriver's ecosystem hub as a new module. The existing collector loop provides snapshots. The brain replaces heuristic decision-making with three-tier model calls.

**Key Principle:** Model-mediated, not heuristic. The brain *reasons* about what's happening rather than following if/then rules. Existing heuristics become advisory inputs, not decision-makers.

---

## Three-Tier Intelligence

### Tier 1 — Reflexes (Haiku)
- **Scope:** Health — crashes, stuck processes, deadlocked daemons, hung commands
- **Trigger:** Events (loop.crashed, agent.died, spawn.failed, daemon.killed) + 60s heartbeat safety net
- **Input:** Triggering event, repo snapshot, last 10 events, heuristic recommendation
- **Output:** Structured JSON directives, executed immediately
- **Cost model:** Cheap, frequent, narrow context

### Tier 2 — Strategy (Sonnet)
- **Scope:** Resource allocation, repo prioritization, enrollment/unenrollment, concurrency adjustments
- **Trigger:** Events (tasks.exhausted, repo.discovered, attractor.converged, attractor.plateaued, Tier 1 escalation) + 10min sweep
- **Input:** All Tier 1 context + cross-repo view, recent decision history (last 20 directives), roster, attractor distances
- **Output:** Directives + reasoning trace to log
- **Cost model:** Moderate, less frequent, broader context

### Tier 3 — Judgment (Opus)
- **Scope:** Strategic pivots, attractor target changes, "is this repo done?", multi-repo stuck situations
- **Trigger:** Event-only — Tier 2 escalation, Tier 2 flagging uncertainty, same issue escalated twice
- **Input:** Everything from Tier 1 and 2 + full reasoning traces + escalation reason
- **Output:** Directives + reasoning + Telegram notification (for significant decisions)
- **Cost model:** Expensive, rare, full context

### Timer Model
- Timers are safety nets, not decision cycles
- 60s heartbeat: checks dispatch loop heartbeat files, daemon liveness. No model call if nothing wrong.
- 10min sweep: scans workspace for new repos, checks overall factory health. Fires Tier 2 only if something needs attention.
- Opus: no timer. Event-triggered only.

---

## Event System

### Per-Repo Event Files
Location: `.workgraph/service/runtime/factory-events.jsonl`

Each repo maintains its own append-only event log. The hub aggregates across all repos.

### Dispatch Loop Events
- `loop.started` — dispatch loop process began
- `loop.exited` — clean exit (all tasks done)
- `loop.crashed` — unclean exit with error
- `agent.spawned` — agent created for task
- `agent.died` — agent process exited unexpectedly
- `agent.completed` — agent finished task successfully
- `spawn.failed` — `wg spawn` returned error
- `tasks.exhausted` — no more ready tasks
- `daemon.killed` — dispatch loop killed a stuck daemon

### Hub Events
- `repo.discovered` — new `.workgraph/` directory found
- `repo.enrolled` / `repo.unenrolled` — brain changed roster
- `attractor.converged` / `attractor.plateaued` — convergence loop result
- `snapshot.collected` — heartbeat (60s)
- `heartbeat.stale` — dispatch loop heartbeat file is stale (loop probably dead)

### Event Routing
Events route to the appropriate tier based on type. Tier 1 events can escalate to Tier 2. Tier 2 can escalate to Tier 3. Timer-generated synthetic events flow through the same pipeline.

---

## Brain Identity — The Factory Adversary

```
You are the Factory Adversary. Your job is to find what's broken, what's about
to break, and what everyone is pretending is fine. You distrust stability —
silence means something failed quietly. Healthy metrics mean something isn't
being measured.

When you see a snapshot, your first question is: "What's wrong that I can't
see?" When an agent reports success, you ask: "Did it actually work, or did it
just exit clean?" When a repo is idle, you ask: "Is it done, or is it stuck
and nobody noticed?"

You have heuristic recommendations from a rules-based system. Treat them as a
naive first guess. They follow playbooks. You think.

Act decisively. Log your reasoning. When you're wrong, say so — then fix it
harder.
```

Tier-specific additions:
- **Tier 1:** "You handle reflexes. Fix what's broken. Don't strategize — act."
- **Tier 2:** "You allocate resources and shape the factory. Think across repos, not within them."
- **Tier 3:** "You make the calls nobody else can. Enrollment, targets, strategic pivots. Be right."

---

## Directive Vocabulary

The brain outputs structured JSON. All tiers share the same directive format:

```json
{
  "reasoning": "Free-text adversarial reasoning trace",
  "directives": [
    {"action": "<action>", ...params}
  ],
  "telegram": "Optional message for kill alerts",
  "escalate": false
}
```

### Available Actions
| Action | Params | Tier | Description |
|--------|--------|------|-------------|
| `kill_process` | `pid` | 1+ | Kill a specific process |
| `kill_daemon` | `repo` | 1+ | Kill wg daemon in repo |
| `clear_locks` | `repo` | 1+ | Remove stale lock/socket files |
| `start_dispatch_loop` | `repo` | 1+ | Start dispatch-loop.sh |
| `stop_dispatch_loop` | `repo` | 1+ | Stop dispatch-loop.sh |
| `spawn_agent` | `repo, task_id` | 1+ | Directly spawn an agent |
| `set_mode` | `repo, mode` | 2+ | Set speedriftd mode |
| `adjust_concurrency` | `repo, max_agents` | 2+ | Change max concurrent agents |
| `enroll` | `repo` | 2+ | Add repo to factory roster |
| `unenroll` | `repo` | 2+ | Remove repo from factory roster |
| `set_attractor_target` | `repo, target` | 3 | Change attractor target |
| `send_telegram` | `message` | 2+ | Send Telegram notification |
| `escalate` | `reason` | 1-2 | Bump to next tier |
| `noop` | `reason` | 1+ | Explicitly do nothing |

---

## Autonomous Enrollment

### Discovery
- 10-min Tier 2 sweep scans workspace for `.workgraph/` directories
- Compares against current roster in `factory-roster.json`
- New repos trigger `repo.discovered` event → Tier 2 evaluates

### Enrollment Process
1. Brain decides repo is worth enrolling (has tasks, is active, matches factory goals)
2. Copies dispatch-loop.sh into `.workgraph/executors/`
3. Ensures `drift-policy.toml` has attractor section (brain picks target)
4. Adds entry to `ecosystem.toml`
5. Starts dispatch loop
6. Emits `repo.enrolled` event → Tier 3 → Telegram notification

### Unenrollment Process
1. Brain decides repo is done (converged, idle, no value in continued work)
2. Stops dispatch loop
3. Sets speedriftd to observe, releases lease
4. Marks inactive in roster (preserves history)
5. Emits `repo.unenrolled` → Telegram notification

### Roster Persistence
- `factory-roster.json` in the hub's data directory
- Contains: repo path, enrolled_at, status (active/inactive), attractor target, current distance, last event timestamp

---

## Notifications

### Reasoning Log (Primary Audit Trail)
- Every brain invocation writes to `.workgraph/service/runtime/brain-log.md`
- Includes: timestamp, tier, trigger event, reasoning, directives issued, outcome
- Rotated daily

### Telegram (Kill Alerts Only)
Sent for:
- **Catastrophic:** Factory brain crashed, all dispatch loops dead, unrecoverable state
- **Significant decisions:** Repo enrolled/unenrolled, attractor target changed, major resource shift

Not sent for: routine health fixes, agent spawns, normal operations.

---

## Dispatch Loop Hardening

Changes to dispatch-loop.sh (pre-brain, but brain-compatible):

1. **Kill daemon on startup** — `wg service stop 2>/dev/null` prevents deadlock
2. **Event emission** — append JSONL to `factory-events.jsonl` for every action
3. **Hung command watchdog** — `timeout 15` on `wg ready` and `wg spawn`. If hung, kill daemon, retry once.
4. **Crash trap** — `trap` on EXIT emits `loop.crashed` event with exit code
5. **Heartbeat file** — touch `.workgraph/service/runtime/dispatch-loop.heartbeat` each cycle. Brain's 60s timer checks staleness.

---

## Heuristic Relationship

- Existing `build_factory_cycle()`, `supervise_repo_services()`, attractor loop, bundle matching all stay
- Their output is serialized and passed to the brain as "heuristic recommendation"
- Brain prompt: "Treat these as suggestions from a junior operator"
- Over time, heuristic logic migrates into prompt context as guidelines rather than code
- No heuristic code deleted in v1

---

## Implementation Sequence

1. Harden dispatch-loop.sh (event emission, watchdog, heartbeat, daemon kill)
2. Build event aggregation in ecosystem hub
3. Build directive executor (takes JSON directives, runs them)
4. Build Tier 1 brain (Haiku reflexes)
5. Build Tier 2 brain (Sonnet strategy)
6. Build Tier 3 brain (Opus judgment)
7. Build autonomous enrollment
8. Wire Telegram for kill alerts
9. Replace dark-factory.sh with brain-managed startup
