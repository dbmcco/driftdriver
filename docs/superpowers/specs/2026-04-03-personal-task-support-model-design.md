# Personal Task Support Model Design

Date: 2026-04-03
Status: Draft approved in conversation; written for review

## Summary

PAIA needs a simpler and more reliable task model for human-owned work that can be supported by agents without letting agents or daemon automation take over the human's task list. The core requirement is that Braydon's tasks remain Braydon's tasks, while Sam can see them, support them, and autonomously execute subordinate work. Restarts must not mutate canonical task state or silently change what appears on the board.

This design defines a single canonical personal-work task system with:

- Braydon-owned parent tasks
- agent-owned support child tasks
- one write path through a task daemon/API
- append-only task history
- read-only startup and explicit recovery
- board projections derived from canonical state rather than treated as truth

## Problem

The current task experience is fragile because task visibility can change after a restart or repair event. In practice, that means a board can appear to lose tasks even when the user's intent and ownership have not changed. The root issue is not that agents exist; it is that ownership, authority, and canonical storage boundaries are not strict enough.

PAIA also needs a model where:

- Sam can support Braydon's tasks autonomously
- the Dark Factory can wake Sam or trigger support flows
- Braydon's top-level tasks are never completed by Sam or the daemon
- support work is visible without cluttering the main board

## Goals

- Preserve human ownership of top-level personal tasks
- Allow Sam to autonomously create and complete support work
- Show support activity without replacing the user's main task list
- Make restart behavior deterministic and non-destructive
- Make every mutation attributable and auditable
- Keep the model simple enough for basic task management

## Non-Goals

- Shared co-ownership of top-level personal tasks
- Automatic completion of Braydon-owned parent tasks by Sam or the daemon
- Using repo-local execution tasks as the canonical home for personal tasks
- Maintaining a second mutable board-state store alongside canonical task state

## Core Model

The system uses one task model with two task kinds.

### Parent Task

A parent task is the user's real task.

Properties:

- `task_kind = parent`
- `owner_user_id = braydon`
- appears in the main board by default
- only Braydon can mark it `done`
- Sam and the daemon may support it but may not take ownership or complete it

### Support Child Task

A support child task is subordinate work created to help move a parent task forward.

Properties:

- `task_kind = support`
- `parent_task_id = <parent>`
- `agent_owner_id = sam` or another authorized agent
- may be autonomously created, claimed, failed, retried, and completed by its agent owner
- never auto-completes the parent task

## Permission and Authority Rules

### Sam Can

- read Braydon-owned parent tasks she is allowed to support
- create support child tasks under a parent autonomously
- claim, update, fail, retry, and complete her support child tasks
- add notes, summaries, evidence, and recommendations to the parent task activity stream
- mark a parent as `ready_for_review` or `proposed_done`

### Sam Cannot

- change the owner of a Braydon-owned parent task
- mark a Braydon-owned parent task `done`
- reassign the parent task to herself
- archive or hide the parent task

### Daemon Can

- wake Sam when a parent becomes stale, blocked, overdue, or newly created
- create support child tasks under a parent
- send reminders, alerts, and suggested next steps
- compute board indicators from support child state

### Daemon Cannot

- edit the core fields of Braydon-owned parent tasks
- claim Braydon-owned parent tasks
- complete Braydon-owned parent tasks
- reassign Braydon-owned parent tasks

### Parent Completion Rule

If a parent task is owned by Braydon, only Braydon can set it to `done`. Even if every support child task is completed successfully, the parent remains open until Braydon confirms completion.

## Minimal Schema

Use one canonical task store with one task table or collection and one task-events table or collection.

### Shared Task Fields

- `id`
- `task_kind = parent | support`
- `title`
- `status = open | in_progress | blocked | done | failed | cancelled`
- `created_at`
- `updated_at`
- `created_by`
- `description`

### Parent-Only Fields

- `owner_user_id`
- `support_agents[]`
- `support_mode = assist_only`
- `due_at` (optional)
- `priority` (optional)
- `project` (optional)

### Support-Only Fields

- `parent_task_id`
- `agent_owner_id`
- `support_kind = research | prep | reminder | execution | followup`
- `result_summary`

### Derived Fields

The following values should be computed from canonical tasks/events rather than stored as mutable state:

- `subtask_count`
- `active_subtask_count`
- `blocked_subtask_count`
- `ready_for_review`
- `last_support_update_at`

## Activity Log

Every meaningful change appends a task event. At minimum:

- task created
- task updated
- status changed
- note added
- child task created
- child task completed
- ready-for-review proposed
- recovery action performed

Each event must record:

- actor identity
- actor type (`user`, `agent`, `daemon`)
- task id
- timestamp
- change payload

This makes every meaningful mutation attributable and provides the audit trail needed for recovery and debugging.

## Consistency Model

### One Write Path

The board UI, Sam, and the Dark Factory do not write task storage directly. All writes go through one task daemon/API. That daemon is the sole writer for canonical task state and task events.

### Same Canonical Store

Parent tasks and support child tasks live in the same canonical personal-work store. The board is only a view over that store. There is no second mutable board-state database.

### Event-First Mutation

Every mutation writes an event first, then updates current task state transactionally with that event or derives current state from the event stream. The system must always be able to answer:

- who changed the task
- what changed
- when it changed
- whether the actor was Braydon, Sam, or daemon automation

### Read-Only Startup

Service startup must validate the canonical store and then serve it. Startup must not repair, migrate, trim, or rewrite live task state automatically.

If validation fails, the daemon enters degraded read-only mode and surfaces:

- the last good canonical state
- a visible integrity or recovery warning
- no silent mutation of task counts, ownership, or status

### Authority Enforcement in the Write Path

Ownership and permission rules must be enforced by the canonical write path, not only by the UI. If a request violates the parent-task ownership rules, the daemon rejects it even if the caller is Sam or the Dark Factory.

## Board and UI Model

### Default View

The main board shows Braydon-owned parent tasks by default.

Each parent row may show derived indicators such as:

- number of support subtasks
- active support subtasks
- blocked support subtasks
- ready for review
- last support update time

### Expanded View

Opening a parent task reveals:

- support child tasks
- support notes and evidence
- recent task activity

### Agent Filter

Filtering for Sam shows support child tasks directly. This gives visibility into Sam's work without cluttering the main Braydon task list.

## Integration with Repo Work

Canonical personal tasks do not move into repo-local graphs just because support work touches code.

If Sam needs to perform repo-local execution work:

- she may create or link a repo-local execution task in the relevant repo
- that repo task is secondary, not canonical for the personal task
- the canonical parent task and support child remain in the central personal-work store
- repo execution outcomes flow back as events, notes, or summaries on the support child task

This keeps personal task ownership simple while still allowing repo-level execution when needed.

## Failure and Recovery Model

### Restart Behavior

On restart, the daemon:

- reloads canonical task state
- rebuilds derived indicators if needed
- serves the same parent/child ownership model as before restart

Restart does not:

- rewrite ownership
- change parent/child relationships
- complete tasks
- trim older tasks
- reassign tasks

### Explicit Recovery Only

Repairs are explicit administrative operations. A recovery action must include:

- selected snapshot or backup source
- diff preview against current state
- explicit restore confirmation
- audit event describing what was restored and why

No automatic backup restore, graph trimming, or silent migration should run during normal boot.

## Verification Requirements

The system should have tests or checks for these hard invariants:

- Sam can create and complete support child tasks
- Sam cannot complete Braydon-owned parent tasks
- the daemon cannot edit Braydon-owned parent-task core fields
- restart does not change canonical parent-task counts without explicit writes
- derived board indicators can be rebuilt from canonical tasks/events
- recovery operations append auditable recovery events

Operationally, the system should also be able to compare before/after restart values for:

- parent task count
- open parent task count
- support child task count
- last event id or timestamp

If those values change without explicit writes, the system should surface corruption or integrity failure rather than silently serving a different board.

## Recommended Architecture

The simplest architecture that satisfies the approved constraints is:

1. One canonical personal-work daemon/store
2. One task model with `parent` and `support` kinds
3. One canonical write path through the daemon/API
4. Append-only task event history
5. Board projections derived from canonical task state
6. Repo-local execution tasks treated as linked secondary work

This is intentionally simpler than a multi-repo personal-task ownership model and safer than a design where a central daemon or board can rewrite live task state on startup.

## Why This Model

This design preserves the product behavior Braydon wants:

- "These are my tasks."
- "Sam can support me and act autonomously on support work."
- "The daemon can wake Sam and route support work."
- "Sam and the daemon do not complete my top-level tasks for me."
- "Support work is visible when needed but does not clutter the main board."

It also addresses the operational failure mode that motivated the redesign:

- restart reloads state
- restart does not mutate state
- visibility is derived from canonical records rather than from fragile projections

## Open Implementation Notes

This document defines the behavior and data model, not the storage engine migration plan. The canonical store may remain Workgraph-backed if Workgraph is used as the authoritative personal-work graph, but the approved design requires:

- one true write path
- append-only auditability
- read-only startup unless an explicit recovery action is invoked

Any implementation that cannot enforce those properties does not satisfy this design.
