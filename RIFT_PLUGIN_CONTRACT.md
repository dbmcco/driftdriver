# Rift Plugin Contract (v1)

This document defines the minimal contract for a **rift** tool so it can be orchestrated by `driftdriver` via `./.workgraph/rifts`.

Goal: consistent agent behavior, low confusion, no hard blocks.

## Task-Level Spec

Plugins are enabled per-task by a fenced TOML block in the Workgraph task `description`:

````md
```<rift_name>
schema = 1
...
```
````

Examples:
- `uxrift` reads a ` ```uxrift` block.
- `specrift` reads a ` ```specrift` block.

## CLI Interface

Required:
- `<rift> wg check --task <id> [--write-log] [--create-followups]`

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

- Artifacts should live under `./.workgraph/.<rift>/...` (repo-local, gitignored).
- Do not modify repo code except by creating follow-up tasks and Workgraph logs (unless the tool is explicitly a refactoring tool).

## Orchestration Rules (driftdriver)

- `speedrift` is the baseline check (always-run).
- Optional plugins run only when:
  - the wrapper exists in `./.workgraph/<rift>` (installed), and
  - the task declares ` ```<rift>` in its description.
- Optional plugin failures must be **best-effort** (warn and continue).

