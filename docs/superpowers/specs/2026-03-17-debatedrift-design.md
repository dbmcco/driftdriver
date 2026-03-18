# debatedrift — Design Spec
**Date:** 2026-03-17
**Status:** Approved for implementation

---

## Overview

`debatedrift` is a new speedrift lane that runs opinionated multi-model debate sessions for tasks where design quality matters most — planning, troubleshooting, and use case development. Two debater agents challenge each other to think harder, while a third proxy agent listens and calls the final decision using Braydon's distilled judgment principles.

The primary goal is task completion. Learnings captured in the process are a bonus.

---

## Architecture

### Three-Agent Triad

| Role | Behavior |
|------|----------|
| **Debater A** | Attacks the problem from one angle |
| **Debater B** | Diverges — contrarian views, corner cases, alternative approaches |
| **Proxy** | Listens to both, holds judgment constitution, calls the final decision |

The debaters know the proxy is present and listening. This changes their behavior: they argue *for* the proxy, not just at each other. The proxy speaks when it has enough — no formal convergence signal required.

### tmux Layout

Four panes in a single session:

```
┌─────────────────┬─────────────────┐
│   Debater A     │   Debater B     │
│   (pane-a)      │   (pane-b)      │
├─────────────────┼─────────────────┤
│   Proxy         │   debate.log    │
│   (pane-c)      │   (pane-d, RO)  │
└─────────────────┴─────────────────┘
```

Pane D is read-only — the aggregated debate log for Braydon to observe. He can watch without interacting, or inject into any pane via normal tmux interaction.

---

## Communication

Agents run as standard Claude Code sessions in their tmux panes. Output is captured via `pipe-pane` — no agent ever writes JSON or manages files directly.

```
pane-a output → pipe-pane → ts wrapper → pane-a.log  (timestamped lines)
pane-b output → pipe-pane → ts wrapper → pane-b.log  (timestamped lines)
aggregator (thin script) → merges by timestamp → debate.log
```

`ts` (from `moreutils`) prefixes each line with a precise timestamp, enabling clean chronological merge. If `ts` is unavailable, the aggregator falls back to interleave-by-mtime and logs a warning.

**Watchdog:** Monitors `[ROUND:END]` sentinel frequency in `pane-a.log` and `pane-b.log`. If N seconds pass without a new sentinel (default: 90s), it sends a `wg msg` nudge to the stalled agent. Debater agent prompts must include an explicit instruction to check `wg msg list` at the end of each turn, before writing `[ROUND:END]`.

**Human injection:** Braydon can interject at any time via tmux interaction with any pane, or via `wg msg send <task-id> "..."`. Agents acknowledge and incorporate human messages before continuing.

---

## Lane Activation

Tasks activate `debatedrift` via a fenced TOML block in their description (consistent with how all speedrift lanes are selected):

```
```debatedrift
schema = 1
type = "planning"          # planning | troubleshoot | usecase
context_files = []         # paths to relevant files the agents should read
max_rounds = 5             # hard cap, default 5
watchdog_timeout = 90      # seconds before nudge, default 90
```
```

The lane runner reads this block, launches the tmux session, wires up `pipe-pane` for each pane, and starts the aggregator. The `type` key informs the proxy which judgment overlay to apply.

Future task types can be added without changing the core lane — only the proxy constitution needs a new overlay section.

---

## Termination

The proxy terminates the debate when it has sufficient information. It signals completion by writing a sentinel line to its pane output:

```
DEBATE:CONCLUDED
```

The aggregator watches for this sentinel and triggers clean teardown: flushing logs, writing `summary.md`, emitting a `wg log` completion entry, and closing the tmux session.

Termination priority order:
1. Proxy writes `DEBATE:CONCLUDED` — aggregator tears down
2. Hard round cap hit — aggregator activates proxy immediately
3. Human issues `/conclude` via `wg msg` — aggregator activates proxy immediately
4. Genuine deadlock after cap — proxy activates, writes `DEBATE:DEADLOCK` sentinel, escalates to real Braydon

**Round definition:** One round = one complete A-speaks → B-speaks cycle. The aggregator counts rounds by tracking alternating pane-a / pane-b output segments. Debaters write `[ROUND:END]` at the end of their turn to mark a clean boundary; the aggregator uses this as the round delimiter and the watchdog resets its timer on each one.

---

## Proxy Constitution

The proxy is **not** fed CLAUDE.md. It has a dedicated judgment document:

**Location:** `driftdriver/driftdriver/debatedrift/proxy-constitution.md` (inside the package, alongside the lane implementation)

This is a hand-authored, versioned artifact. Structure:

### Sections

**Decision Principles**
YAGNI, simplicity-first, what "good enough" looks like per task type. When to pick the simpler answer even if the clever one is technically sound.

**Judgment Heuristics**
How to break ties. When a contrarian view is genuinely worth pursuing vs noise. How to recognize when the debate has circled back to the same ground.

**Task-Type Overlays**
Planning calls feel different from troubleshooting calls — different risk tolerances, different definitions of "done."

**Escalation Rules**
What the proxy never decides alone. Hard lines that always go to real Braydon.

**Refinement Protocol**
When the proxy makes a call Braydon would have made differently, the constitution gets updated. It sharpens with use.

Writing the initial constitution is explicitly in scope for the implementation work.

---

## Learnings Capture

After the proxy renders a decision, the aggregator produces `summary.md` alongside `debate.log`:

```
.workgraph/.debatedrift/<task-id>/
  pane-a.log
  pane-b.log
  debate.log
  summary.md       ← proxy-authored, optional
```

`summary.md` contains:
- What was decided
- Key tensions that surfaced
- Dissenting angles worth revisiting later

This is generated by the proxy as part of its closing statement — no separate step required.

---

## Workgraph Integration

`debatedrift` is a standard speedrift lane. It integrates with the existing lane SDK:

- Lane detection via fenced TOML block scan in task description (same pattern as `specdrift`, `uxdrift`)
- Debate artifacts stored under `.workgraph/.debatedrift/<task-id>/`
- Task log entries written via `wg log` at session start, each round, and close
- Follow-up tasks created for escalated decisions or deferred angles

---

## Factory Brain Interaction

The driftdriver factory brain suppresses action directives when an interactive session is active. Each debater pane and the proxy pane must emit a `session.started` presence event at launch so the brain registers three active sessions and stays passive for the debate duration. The lane runner emits these events before handing off to the tmux panes. On teardown, corresponding `session.ended` events are emitted.

---

## Non-Goals

- No mandatory gates — debatedrift is advisory, same as other speedrift lanes
- No fully automated runs without tmux — human observation is a first-class requirement
- No cross-agent LLM API calls — agents communicate through the log, not directly
- No forced consensus — proxy can call a deadlock and escalate cleanly

---

## Open Questions (deferred)

- Which model runs the proxy? (Opus for judgment weight, or same as debaters?)
- Session persistence: can a debate be paused and resumed?
- Federation: can debate logs be referenced across repos via `wg peer`?
