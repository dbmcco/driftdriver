# Ecosystem Intelligence Design

**Date:** 2026-03-17
**Status:** Draft
**Scope:** Driftdriver ecosystem intelligence signal store, evaluation loop, and dashboard
**Repo:** driftdriver

> Restored from the 2026-03-17 Workgraph task chain after context loss so the task graph has a checked-in source of truth again.

---

## 1. Problem Statement

The current ecosystem review flow discovers useful upstream and community signals, but it treats them as short-lived task prompts:

- GitHub dependency updates and repo activity are discovered by `driftdriver/updates.py`.
- Community best-practice signals live outside that path, especially in `vibez-monitor`.
- Daily review output is pushed directly into Workgraph tasks and `CLAUDE.md`, which means signal history, vetoes, and pattern learning are hard to retain.

We need a durable intelligence layer that behaves like a best-practices wiki backed by a signal database:

- collect signals from upstream repos plus broader community sources
- evaluate them with model assistance
- expose a daily briefing, inbox, and decision log
- retain rationale, vetoes, and learning over time

This is not a conventional content-authored wiki. It is a structured signal store plus decision workflow that continuously builds the ecosystem knowledge base.

---

## 2. Architecture Overview

```text
GitHub / Vibez / future sources
            |
      SourceAdapters
            |
            v
  ecosystem_intelligence Postgres
    - signals
    - evaluation_runs
    - source_configs
            |
            v
      Sync orchestrator
            |
            v
   LLM evaluation pipeline
            |
   +--------+---------+
   |                  |
   v                  v
Dashboard tab     Telegram / n8n
Briefing          notifications
Inbox
Decision Log
            |
            v
daily scanner integration
```

### Goals

- unify upstream dependency intelligence and community best-practice intelligence
- preserve the full decision trail instead of emitting disposable task text
- support both automation and human veto/override
- plug into the existing ecosystem hub instead of replacing it

### Non-goals

- no automatic code merges
- no hard dependency on any one external source
- no rewrite of the existing hub operational views

---

## 3. Data Model

The first implementation task creates a local Postgres database named `ecosystem_intelligence` on port `5432`.

### `signals`

One row per normalized external signal.

Columns:

- `id uuid primary key`
- `source_type text not null`
- `source_id text not null`
- `signal_type text not null`
- `title text not null`
- `raw_payload jsonb not null`
- `detected_at timestamptz not null`
- `evaluated_at timestamptz null`
- `decision text null`
- `decision_reason text null`
- `decision_confidence double precision null`
- `decided_by text null`
- `acted_on boolean not null default false`
- `action_log jsonb not null`
- `vetoed_at timestamptz null`
- `veto_reason text null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Constraints and indexes:

- primary key on `id`
- unique key on `(source_type, source_id, signal_type)` so repeated syncs are idempotent
- index on `(source_type, evaluated_at)` for source health and backlog views
- index on `(decision, acted_on)` for inbox and decision-log filtering
- `decision_confidence` must be `0.0 <= value <= 1.0` when present

Semantics:

- `raw_payload` stores the original source record for debugging and drill-down
- `action_log` stores actions taken from a decision, including auto-actions and vetoes
- `updated_at` is application-maintained for now

### `evaluation_runs`

One row per sync/evaluation pass.

Columns:

- `id uuid primary key`
- `run_at timestamptz not null`
- `source_types text[] not null`
- `signals_created integer not null`
- `signals_evaluated integer not null`
- `auto_decisions jsonb not null`
- `escalated integer not null`
- `llm_model text null`
- `llm_tokens_used integer not null`
- `duration_ms integer not null`

Semantics:

- `source_types` records which adapters participated in the run
- `auto_decisions` stores per-decision counts or structured metadata about automation outcomes
- counts must be non-negative

### `source_configs`

One row per source adapter configuration.

Columns:

- `id uuid primary key`
- `source_type text not null`
- `config jsonb not null`
- `enabled boolean not null`
- `last_synced_at timestamptz null`
- `sync_interval_minutes integer not null`

Constraints:

- unique key on `source_type`
- `sync_interval_minutes > 0`

Initial source types:

- `github`
- `vibez`

`source_configs` replaces the current flat-file source configuration as the canonical source registry.

---

## 4. Source Adapter Architecture

Location: `driftdriver/intelligence/`

Base interface:

```python
class SourceAdapter(Protocol):
    def sync(self, config: dict[str, Any], last_synced_at: datetime | None) -> list[Signal]: ...
    def health_check(self) -> dict[str, Any]: ...
```

Normalized signal object:

```python
@dataclass
class Signal:
    id: UUID
    source_type: str
    source_id: str
    signal_type: str
    title: str
    raw_payload: dict[str, Any]
    detected_at: datetime
```

### GitHubAdapter

- wraps the existing `driftdriver/updates.py` review flow
- produces `repo_update`, `new_repo`, and `activity` signal types
- migrates data currently expressed through `ecosystem-review.json` into `source_configs`

### VibezAdapter

- reads Vibez monitor data from port `3100`
- ingests daily briefing summaries, hot alerts, and contribution opportunities
- filters by configurable keywords such as `workgraph`, `amplifier`, `speedrift`, and `freshell`
- emits `community_mention`, `trend`, and `hot_alert` signal types
- if Vibez is unavailable, logs the issue and returns an empty result set

---

## 5. Sync Orchestration

Entry point: `python -m driftdriver.intelligence.sync`

Responsibilities:

- load enabled `source_configs`
- run adapters in parallel with `asyncio.gather`
- isolate adapter failures so one source outage does not block the rest of the run
- insert only new normalized signals into `signals`
- record a row in `evaluation_runs` with timing, counts, and error summary
- hand off unevaluated signals to the LLM evaluation pipeline

The sync layer is the single integration point the daily scanner should call once the intelligence path is live.

---

## 6. LLM Evaluation Pipeline

Location: `driftdriver/intelligence/evaluator.py`

The evaluator processes unevaluated rows from `signals` and writes decisions back to the same table.

### Batching

- `repo_update`: up to 10 signals per model call
- `new_repo`: 1 signal per call
- community signals (`community_mention`, `trend`, `hot_alert`): up to 5 per call

### Context

Each evaluation pass injects:

- last 30 logged decisions
- current watch list
- recent veto reasons and veto patterns
- source metadata from `source_configs`

### Decision Envelope

Each evaluated signal produces:

- `decision`
- `confidence`
- `rationale`
- `recommended_actions`
- `relevance_to_stack`
- `urgency`

### Models

- `claude-haiku-4-5-20251001` via the Anthropic Messages API for current classification and adoption review
- model choice remains configurable per source type via `source_configs`
- a GPT Nano/OpenAI route remains deferred until a supported direct API integration is wired into the evaluator

### Confidence Matrix

Auto-execution thresholds:

- `skip`: auto at `>= 0.80`
- `watch`: auto at `>= 0.80`
- `defer`: auto at `>= 0.85`
- `adopt`: auto at `>= 0.90`

Anything below threshold goes to the inbox for human review. Low-confidence `adopt` and `defer` decisions must always be escalated.

### Action Rules

Auto-executed actions:

- `skip`: log only
- `watch`: add/update watchlist state
- `defer`: log with reason and revisit later
- `adopt`: create a Workgraph follow-up task

All auto-decisions enter a 48-hour veto window:

- human override before expiry updates `vetoed_at` and `veto_reason`
- no veto finalizes the decision and leaves a learning trail for future prompts

---

## 7. Dashboard UI

Location: `driftdriver/ecosystem_hub/`

Add a new top-level `Intelligence` tab to the existing hub on port `8777`.

### Views

- `Briefing`
  - today’s headline stats
  - key actions taken
  - stack impact summary
  - source health
  - 7-day history
- `Inbox`
  - signals awaiting a human decision
  - LLM recommendation, confidence, rationale, urgency, veto timer
  - approve, override, snooze, and batch-approve actions
- `Decision Log`
  - searchable history of evaluated signals
  - filters by source, decision, date, confidence, and vetoed state
  - trend summaries over time

### API

- `GET /intelligence/briefing`
- `GET /intelligence/briefing/history`
- `GET /intelligence/inbox`
- `POST /intelligence/inbox/{signal_id}/approve`
- `POST /intelligence/inbox/{signal_id}/override`
- `POST /intelligence/inbox/{signal_id}/snooze`
- `POST /intelligence/inbox/batch-approve`
- `GET /intelligence/decisions`
- `GET /intelligence/decisions/trends`

The dashboard reads directly from Postgres and must not disturb existing operational tabs.

---

## 8. Notification Layer

Location: `driftdriver/intelligence/notifications.py` plus n8n workflow

Notifications are delivered through the existing n8n/Telegram path.

Payload schema:

- `notification_type`
- `summary`
- `deep_link_url`
- `urgency`
- `signal_ids`

Notification types:

- daily briefing push
- escalation alerts for inbox items
- veto reminders 24 hours before a 48-hour auto-decision window closes
- weekly digest rollups

Notification failure is non-blocking: log it, keep the pipeline moving.

---

## 9. Integration Points

### Daily scanner

`scripts/daily_ecosystem_eval.sh` should eventually call:

```bash
python -m driftdriver.intelligence.sync
```

instead of directly emitting evaluation tasks into Workgraph.

### Graceful degradation

If Postgres is unavailable, the daily scanner may temporarily fall back to the current task-creation flow so the review loop does not go dark.

### CLAUDE.md cleanup

The intelligence pipeline replaces manual `CLAUDE.md` task injection. Existing stale `Ecosystem Updates Pending Evaluation` sections should be removed once the sync path is cut over.

### Migration path

- seed `source_configs` from the current ecosystem-review config
- keep the current launchd schedule
- switch the scanner only after sync, evaluation, dashboard, and notifications are working end to end
