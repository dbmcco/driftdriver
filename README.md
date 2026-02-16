# Driftdriver

Driftdriver is an orchestrator for **Workgraph-first** agent development.

- **Workgraph is the spine** (tasks, dependencies, loops, agent spawning).
- **Driftdriver coordinates "drift" tools** (code drift, UX drift, data drift, etc.) without hard-blocking work.
- Findings are written back into Workgraph via `wg log` and follow-up tasks, keeping the graph in sync.

Today it supports:
- `speedrift` (baseline, always-run)
- `specdrift` (optional, only when a task declares a ```specdrift block)
- `datadrift` (optional, only when a task declares a ```datadrift block)
- `depsdrift` (optional, only when a task declares a ```depsdrift block)
- `uxdrift` (optional, only when a task declares a ```uxdrift block)
- `therapydrift` (optional, only when a task declares a ```therapydrift block)
- `yagnidrift` (optional, only when a task declares a ```yagnidrift block)

Plugin interface: see `DRIFT_PLUGIN_CONTRACT.md`.

## Install (CLI)

You need `wg` (Workgraph) installed first.

Fastest install today is `pipx` from GitHub:

```bash
pipx install git+https://github.com/dbmcco/driftdriver.git
pipx install git+https://github.com/dbmcco/speedrift.git

# Optional plugins:
pipx install git+https://github.com/dbmcco/specdrift.git
pipx install git+https://github.com/dbmcco/uxdrift.git
pipx install git+https://github.com/dbmcco/therapydrift.git
pipx install git+https://github.com/dbmcco/yagnidrift.git
```

## Install Into A Repo

From the repo you want to work in:

```bash
driftdriver install
```

Optional UX + therapy + YAGNI integration:

```bash
driftdriver install --with-uxdrift --with-therapydrift --with-yagnidrift
```

### Wrapper Modes (Portable vs Pinned)

By default `driftdriver install` chooses wrapper style automatically:

- `--wrapper-mode auto`: portable only when core tools are on PATH (default)
- `--wrapper-mode pinned`: `.workgraph/*` wrappers exec absolute paths (best for local checkouts)
- `--wrapper-mode portable`: `.workgraph/*` wrappers exec tools on PATH (commit-safe)

If you want to commit `./.workgraph/drifts` (and wrappers) into the repo, use:

```bash
driftdriver install --wrapper-mode portable --with-uxdrift --with-therapydrift --with-yagnidrift
```

This writes:
- `./.workgraph/driftdriver` (wrapper)
- `./.workgraph/drifts` (single per-repo entrypoint used by agents)
- `./.workgraph/speedrift` (wrapper)
- (optional) `./.workgraph/uxdrift` (wrapper)
- (optional) `./.workgraph/therapydrift` (wrapper)
- (optional) `./.workgraph/yagnidrift` (wrapper)
- `./.workgraph/drift-policy.toml` (mode/order/recursion defaults)
- executor prompt guidance under `./.workgraph/executors/*.toml`

## Drift Policy

`driftdriver install` creates `./.workgraph/drift-policy.toml`:

```toml
schema = 1
mode = "redirect"
order = ["speedrift", "specdrift", "datadrift", "depsdrift", "uxdrift", "therapydrift", "yagnidrift"]

[recursion]
cooldown_seconds = 1800
max_auto_actions_per_hour = 2
require_new_evidence = true
max_auto_depth = 2
```

Modes:
- `observe`: no logs, no follow-ups
- `advise`: logs only
- `redirect`: logs + follow-ups
- `heal`: only `therapydrift` auto-followups; others log only
- `breaker`: logs only + creates `drift-breaker-<task_id>` if findings persist

Notes:
- `order` controls optional plugin execution order under `./.workgraph/drifts check`.
- CLI flags still force behavior per run: `--write-log` and `--create-followups`.

## Per-Task Protocol

Agents should run (at task start and before completion):

```bash
./.workgraph/drifts check --task <id> --write-log --create-followups
```

Exit codes:
- `0`: clean
- `3`: findings exist (advisory; act via follow-ups / contract edits)

## Continuous "Pit Wall" Mode

If you want drift telemetry running continuously while work happens:

```bash
./.workgraph/drifts orchestrate --write-log --create-followups
```

## Development

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
scripts/e2e_smoke.sh
```
