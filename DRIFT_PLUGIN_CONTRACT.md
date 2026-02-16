# Drift Plugin Contract (v1)

This document defines the minimal contract for a **drift** tool so it can be orchestrated by `driftdriver` via `./.workgraph/drifts`.

Goal: consistent agent behavior, low confusion, no hard blocks.

## Task-Level Spec

Plugins are enabled per-task by a fenced TOML block in the Workgraph task `description`:

````md
```<drift_name>
schema = 1
...
```
````

Examples:
- `uxdrift` reads a ` ```uxdrift` block.
- `specdrift` reads a ` ```specdrift` block.
- `datadrift` reads a ` ```datadrift` block.
- `depsdrift` reads a ` ```depsdrift` block.
- `therapydrift` reads a ` ```therapydrift` block.
- `yagnidrift` reads a ` ```yagnidrift` block.
- `redrift` reads a ` ```redrift` block.

## CLI Interface

Required:
- `<drift> wg check --task <id> [--write-log] [--create-followups]`

Behavior:
- Exit codes:
  - `0`: clean (no findings)
  - `3`: findings exist (advisory)
- `--write-log`: write a one-line summary via `wg log <id> "..."`.
- `--create-followups`: create deterministic follow-up tasks (stable IDs) instead of bloating the current task.

Output:
- Text output by default.
- Optional: `--json` for machine consumption (if supported).

## State & Artifacts

- Artifacts should live under `./.workgraph/.<drift>/...` (repo-local, gitignored).
- Do not modify repo code except by creating follow-up tasks and Workgraph logs (unless the tool is explicitly a refactoring tool).

## Orchestration Rules (driftdriver)

- `coredrift` is the baseline check (always-run).
- Optional plugins run only when:
  - the wrapper exists in `./.workgraph/<drift>` (installed), and
  - the task declares ` ```<drift>` in its description.
- Optional plugin failures must be **best-effort** (warn and continue).
- Execution order is controlled by `./.workgraph/drift-policy.toml` (`order = [...]`).
- Automation behavior is controlled by policy mode:
  - `observe`, `advise`, `redirect`, `heal`, `breaker`.
