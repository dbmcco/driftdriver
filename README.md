# Driftdriver

Driftdriver is an orchestrator for **Workgraph-first** agent development.

- **Workgraph is the spine** (tasks, dependencies, loops, agent spawning).
- **Driftdriver coordinates "drift" tools** (code drift, UX drift, data drift, etc.) without hard-blocking work.
- Findings are written back into Workgraph via `wg log` and follow-up tasks, keeping the graph in sync.

Today it supports:
- `speedrift` (baseline, always-run)
- `uxrift` (optional, only when a task declares a ```uxrift block)

## Install Into A Repo

From the repo you want to work in:

```bash
/Users/braydon/projects/experiments/driftdriver/bin/driftdriver install
```

Optional UX integration:

```bash
/Users/braydon/projects/experiments/driftdriver/bin/driftdriver install --with-uxrift
```

This writes:
- `./.workgraph/driftdriver` (pinned wrapper)
- `./.workgraph/rifts` (single per-repo entrypoint used by agents)
- `./.workgraph/speedrift` (pinned wrapper)
- (optional) `./.workgraph/uxrift` (pinned wrapper)
- executor prompt guidance under `./.workgraph/executors/*.toml`

## Per-Task Protocol

Agents should run (at task start and before completion):

```bash
./.workgraph/rifts check --task <id> --write-log --create-followups
```

Exit codes:
- `0`: clean
- `3`: findings exist (advisory; act via follow-ups / contract edits)

## Development

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
scripts/e2e_smoke.sh
```

