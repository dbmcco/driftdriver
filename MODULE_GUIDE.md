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
- fenced block present in task description (` ```specdrift`, ` ```redrift`, etc.)
- policy order in `./.workgraph/drift-policy.toml`

## Standalone Commands

```bash
coredrift --dir . check --task <id> --write-log --create-followups
specdrift --dir . wg check --task <id> --write-log --create-followups
datadrift --dir . wg check --task <id> --write-log --create-followups
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
- Together: combine with `depsdrift` and `redrift`.

### depsdrift

- Standalone: lockfile/manifest consistency.
- Together: combine with `coredrift`, optionally `datadrift`.

### uxdrift

- Standalone: runtime UI evidence (screenshots/network/console).
- Together: combine with `specdrift` and `coredrift`.

### therapydrift

- Standalone: recurring drift recovery and loop safety.
- Together: combine across long-running multi-agent projects.

### yagnidrift

- Standalone: speculative abstraction and overbuild checks.
- Together: combine with `coredrift` and `therapydrift`.

### redrift

- Standalone: brownfield re-spec/rebuild artifact discipline.
- Together: combine with `specdrift`, `datadrift`, and `therapydrift`.

## Suggested Stacks

- Product feature stack: `coredrift + specdrift + uxdrift`
- Backend migration stack: `coredrift + datadrift + depsdrift + redrift`
- Stabilization stack: `coredrift + therapydrift + yagnidrift`
- Brownfield v2 stack: `coredrift + redrift + specdrift + datadrift + therapydrift`
