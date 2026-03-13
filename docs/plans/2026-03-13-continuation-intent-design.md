# Continuation Intent — Design

## Problem

When Braydon closes a Claude Code session, repos go quiet. With 40+ repos, he comes back to find 21 stalled and can't tell which ones stopped intentionally vs. accidentally. The system doesn't track whether a repo *should* keep working after a session ends.

## Approach

Flip the default: repos keep working autonomously when a session ends. The brain self-heals technical problems and only escalates genuine human decisions (aesthetics, feature direction, business judgment). A centralized decision queue ensures questions and answers are visible across all channels — Telegram, terminal, dashboard — without duplication.

---

## 1. Continuation Intent Record

When a session ends, the system writes a continuation intent to the repo's control state.

```json
{
  "continuation_intent": {
    "intent": "continue | parked | needs_human",
    "reason": "session ended, work continues autonomously",
    "set_by": "agent | brain | human",
    "set_at": "2026-03-13T...",
    "decision_id": null
  }
}
```

**Default behavior:** Session ends without explicit park → `intent: "continue"`. Repo stays in its current mode, agents keep dispatching.

**Explicit park:** User tells the agent "hold off on this" or "I'll come back to this" → agent writes `intent: "parked"` before session closes.

**Brain auto-pause:** Brain detects a problem requiring human input → writes `intent: "needs_human"` with a reference to the decision queue entry.

---

## 2. Brain Self-Healing Before Escalation

Two-phase response to stalls. The brain handles technical problems itself and only escalates what it can't resolve.

### Phase 1: Brain Self-Heals

| Stall Category | Brain's First Move |
|----------------|-------------------|
| **Blocked cascade** — open tasks with unmet deps | Diagnose the failing task. Create a fix plan (new workgraph tasks). Execute. |
| **Awaiting validation** — tasks done but unverified | Run verify commands or spin up Playwright tests automatically. Report pass/fail. |
| **Lane boundary** — current lane done, next lane exists | Start the next lane (it's ready work, just needs dispatch). |
| **Agent failure** — workers died or timed out | Restart the worker. If it fails again, create a diagnostic task to investigate. |
| **Task loop** — same task failed 3+ times | Analyze failure pattern. Create a new approach task. |
| **Drift plateau** — attractor ran 2+ passes, no improvement | Re-diagnose from scratch. Adjust strategy. |

### Phase 2: Escalate to Human

The brain escalates only when Phase 1 fails or the decision is inherently human:

- **Self-heal failed** — "Tried to fix `setup-db-schema` twice, both failed. Here's what I found: [context]. Options: (A) rebuild migrations, (B) skip and move to API layer."
- **Aesthetic/UX judgment** — "Auth flow works but redirect takes 3 seconds. Acceptable or should I optimize?"
- **Feature/business decision** — "Scoring algorithm weights all criteria equally. Should some be weighted higher?"
- **External dependency** — "Need Stripe test API keys to proceed with payment integration."

Every escalation includes:
1. What happened
2. What the brain tried
3. Why it couldn't resolve it
4. A specific question with options when possible

---

## 3. Centralized Decision Queue

Single source of truth for pending human decisions, shared across all channels.

**Storage:** `.workgraph/service/runtime/decisions.jsonl` per repo, plus a central index at the hub level.

**Decision record:**

```json
{
  "id": "dec-20260313-001",
  "repo": "lfw-interview",
  "status": "pending | answered | expired",
  "question": "Should interview scoring weight technical depth higher?",
  "context": {
    "task_id": "scoring-algorithm",
    "what_brain_tried": "Ran tests with equal weights, all pass but results feel flat",
    "options": ["A: Weight technical depth 2x", "B: Keep equal weights"],
    "findings": "..."
  },
  "category": "aesthetic | feature | business | external_dep",
  "created_at": "2026-03-13T14:30:00Z",
  "notified_via": ["telegram", "dashboard"],
  "answered_at": null,
  "answered_via": null,
  "answer": null,
  "resolution_task": null
}
```

**Channel behavior:**
- All channels read from the same record
- Answering in any channel writes `answer`, sets `status: "answered"`, records `answered_via`
- All other channels immediately see it's resolved — no stale prompts
- Brain picks up the answer, creates a task from it, flips repo intent back to `continue`

---

## 4. Notification Channels

Alerts go out simultaneously through all available channels.

### Telegram (dedicated bot)

- New bot specifically for factory decision queue (separate from existing alerts)
- Bidirectional: brain sends question, Braydon replies with answer
- Messages tagged with repo name + decision ID for reply routing
- Bot checks decision queue before re-alerting (no duplicates)
- If no reply, repo stays paused — one alert, no nagging

### Active Terminal

- If a Claude Code session is open anywhere, surface the alert there
- Agent checks decision queue on session start — pending decisions for any repo shown immediately

### Dashboard

- `needs_human` badge on repo row with the question visible
- Decision history viewable per repo
- Answered decisions show resolution

---

## 5. Session Lifecycle (New Behavior)

### Session Starts
- Presence registered (unchanged)
- Brain suppresses Tier 1 events for this repo (unchanged)
- Agent checks decision queue — surfaces any pending `needs_human` decisions

### During Session
- Normal work. If user says "park this" / "hold off" → agent writes `intent: "parked"`
- If user says nothing about continuation → no action yet

### Session Ends
- No explicit park → `intent: "continue"` written (NEW DEFAULT)
- Repo stays in current mode (supervise/autonomous)
- Brain resumes monitoring but does NOT revert to observe
- Agents keep dispatching work

### Brain Monitoring (Ongoing)
- Phase 1: Self-heal technical problems
- Phase 2: Escalate genuine human decisions to decision queue
- Notifications go to all channels
- Answer flows back from any channel, work resumes

---

## What Changes from Today

| Aspect | Today | New |
|--------|-------|-----|
| Session ends default | Brain backs off, repo goes quiet | Repo keeps going (`intent: "continue"`) |
| Stall response | Dashboard shows "stalled" with no context | Brain self-heals first, then escalates with specific question |
| Human notification | None (you discover stalls manually) | Telegram + terminal + dashboard with actionable questions |
| Reply mechanism | Re-open a session, investigate, fix | Reply in Telegram or any terminal, work resumes |
| State tracking | No record of why a repo is in its state | Continuation intent + decision queue — full audit trail |
| Park a repo | CLI command or mode change | Tell the agent conversationally |

---

## What's NOT in This Design

- **Dev/QA/Prod lifecycle modes** — deferred to a separate design
- **Dashboard chat panel (Approach C)** — future work, but decision queue is a stepping stone
- **Auto-escalation between brain tiers based on decision category** — the brain already has tier routing; this design uses it as-is
