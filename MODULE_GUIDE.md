# Driftdriver Module Guide

This guide shows how to use each drift module:
- standalone (run one module directly)
- together (run through `driftdriver` orchestration)

## Together (Recommended Default)

Use one command and let fences + policy route modules:

```bash
./.workgraph/drifts check --task <id> --write-log --create-followups
```

Fastest packaging path from this workspace:

```bash
cd driftdriver
scripts/package_app.sh --app /path/to/app --seed-redrift-task
```

`driftdriver` always runs `coredrift`, then optional modules based on:
- tool installed in `./.workgraph/<module>`
- lane strategy (`--lane-strategy auto|fences|all`)
- fenced block present in task description (` ```specdrift`, ` ```redrift`, etc.) when strategy uses fences
- policy order in `./.workgraph/drift-policy.toml`

Defaults:
- `auto`: fence-based routing plus full-suite escalation for complex/rebuild tasks
- `fences`: strict fence-only routing
- `all`: run all installed optional modules

## Standalone Commands

```bash
coredrift --dir . check --task <id> --write-log --create-followups
specdrift --dir . wg check --task <id> --write-log --create-followups
datadrift --dir . wg check --task <id> --write-log --create-followups
archdrift --dir . wg check --task <id> --write-log --create-followups
depsdrift --dir . wg check --task <id> --write-log --create-followups
uxdrift wg --dir . check --task <id> --write-log --create-followups
therapydrift --dir . wg check --task <id> --write-log --create-followups
yagnidrift --dir . wg check --task <id> --write-log --create-followups
redrift --dir . wg check --task <id> --write-log --create-followups
```

## Per-Module Playbook

### coredrift

- Standalone: baseline drift checks on every task.
- Together: always on in `driftdriver`.

### specdrift

- Standalone: docs/contracts must track code.
- Together: combine with `coredrift`; pair with `redrift` in v2 programs.

### datadrift

- Standalone: schema/migration consistency.
- Together: combine with `archdrift`, `depsdrift`, and `redrift`.

### archdrift

- Standalone: architecture intent vs implementation drift.
- Together: combine with `redrift` and `uxdrift` for rebuild and product loops.

### depsdrift

- Standalone: lockfile/manifest consistency.
- Together: combine with `coredrift`, optionally `datadrift`.

### uxdrift

- Standalone: runtime UI evidence (screenshots/network/console).
- Together: combine with `specdrift` and `coredrift`.
- For consistent UX judgment, prefer POV-guided runs:
  - `uxdrift wg --dir . check --task <id> --llm --pov doet-norman-v1 --write-log --create-followups`

### therapydrift

- Standalone: recurring drift recovery and loop safety.
- Together: combine across long-running multi-agent projects.

### yagnidrift

- Standalone: speculative abstraction and overbuild checks.
- Together: combine with `coredrift` and `therapydrift`.

### redrift

- Standalone: brownfield re-spec/rebuild artifact discipline.
- Together: combine with `specdrift`, `datadrift`, `archdrift`, and `therapydrift`.

## Suggested Stacks

- Product feature stack: `coredrift + specdrift + uxdrift`
- Backend migration stack: `coredrift + datadrift + archdrift + depsdrift + redrift`
- Stabilization stack: `coredrift + therapydrift + yagnidrift`
- Brownfield v2 stack: `coredrift + redrift + specdrift + datadrift + archdrift + therapydrift`
