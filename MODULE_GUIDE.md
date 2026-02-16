# Driftdriver Module Guide

This guide shows how to use each drift module:
- standalone (run one module directly)
- together (run through `driftdriver` orchestration)

## Together (Recommended Default)

Use one command and let fences + policy route modules:

```bash
./.workgraph/drifts check --task <id> --write-log --create-followups
```

`driftdriver` always runs `speedrift`, then optional modules based on:
- tool installed in `./.workgraph/<module>`
- fenced block present in task description (` ```specdrift`, ` ```redrift`, etc.)
- policy order in `./.workgraph/drift-policy.toml`

## Standalone Commands

```bash
speedrift --dir . check --task <id> --write-log --create-followups
specdrift --dir . wg check --task <id> --write-log --create-followups
datadrift --dir . wg check --task <id> --write-log --create-followups
depsdrift --dir . wg check --task <id> --write-log --create-followups
uxdrift wg --dir . check --task <id> --write-log --create-followups
therapydrift --dir . wg check --task <id> --write-log --create-followups
yagnidrift --dir . wg check --task <id> --write-log --create-followups
redrift --dir . wg check --task <id> --write-log --create-followups
```

## Per-Module Playbook

### speedrift

- Standalone: baseline drift checks on every task.
- Together: always on in `driftdriver`.

### specdrift

- Standalone: docs/contracts must track code.
- Together: combine with `speedrift`; pair with `redrift` in v2 programs.

### datadrift

- Standalone: schema/migration consistency.
- Together: combine with `depsdrift` and `redrift`.

### depsdrift

- Standalone: lockfile/manifest consistency.
- Together: combine with `speedrift`, optionally `datadrift`.

### uxdrift

- Standalone: runtime UI evidence (screenshots/network/console).
- Together: combine with `specdrift` and `speedrift`.

### therapydrift

- Standalone: recurring drift recovery and loop safety.
- Together: combine across long-running multi-agent projects.

### yagnidrift

- Standalone: speculative abstraction and overbuild checks.
- Together: combine with `speedrift` and `therapydrift`.

### redrift

- Standalone: brownfield re-spec/rebuild artifact discipline.
- Together: combine with `specdrift`, `datadrift`, and `therapydrift`.

## Suggested Stacks

- Product feature stack: `speedrift + specdrift + uxdrift`
- Backend migration stack: `speedrift + datadrift + depsdrift + redrift`
- Stabilization stack: `speedrift + therapydrift + yagnidrift`
- Brownfield v2 stack: `speedrift + redrift + specdrift + datadrift + therapydrift`
