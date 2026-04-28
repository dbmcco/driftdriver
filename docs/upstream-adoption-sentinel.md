# Upstream Adoption Sentinel

Driftdriver tracks upstream projects that Speedrift depends on through:

- `.driftdriver/upstream-config.toml`: tracked repos, refs, thresholds, compatibility checks
- `.driftdriver/upstream-pins.toml`: last observed upstream/adopted SHAs and adoption-lag start times
- `.workgraph/service/ecosystem-hub/upstream-adoptions.json`: latest hub-visible adoption cycle

The sentinel exists because Eric/upstream can ship many small coding-agent commits.
Commit count is useful, but not enough by itself. The sentinel combines commit lag,
ancestor checks, divergence age, touched files, and compatibility/API-surface checks
before deciding whether Speedrift needs a WorkGraph-visible follow-up.

## Current Live Setup

The Speedrift ecosystem hub is launchd-managed and runs the sentinel every collector
tick.

- launchd label: `com.speedrift.ecosystem-hub`
- plist: `~/Library/LaunchAgents/com.speedrift.ecosystem-hub.plist`
- hub URL: `http://127.0.0.1:8777/`
- central register: `/Users/braydon/projects/experiments/speedrift-ecosystem/.workgraph/service/ecosystem-central`

Verify the daemon:

```bash
cd /Users/braydon/projects/experiments/driftdriver

scripts/ecosystem_hub_daemon.sh status
scripts/ecosystem_hub_daemon.sh launchd-status
```

Run one sentinel pass:

```bash
driftdriver --dir /Users/braydon/projects/experiments/driftdriver upstream-tracker --json
```

Use `--no-tasks` for a read-only operator check that updates local adoption state
but does not emit WorkGraph tasks:

```bash
uv run driftdriver --dir /Users/braydon/projects/experiments/driftdriver upstream-tracker --json --no-tasks
```

The ecosystem hub also runs this pass every collector tick when `upstream-config.toml`
exists. `scripts/daily_ecosystem_eval.sh` runs the same sentinel before the broader
ecosystem review.

## Alert Rules

The sentinel creates WorkGraph-visible work when any of these hold:

- upstream compatibility checks fail
- a schema or CLI/API surface change appears upstream
- new upstream commits exceed `lag_window_commits`
- upstream commits are not contained in the adopted line for at least `max_lag_days`

For WorkGraph, the current policy is intentionally tight:

- `lag_window_commits = 5`
- `max_lag_days = 3`

This is meant to account for coding-agent commit volume: a large commit count alone
is not the signal; age, touched files, and compatibility checks determine urgency.

## Operator Flow

When the sentinel emits work:

1. Inspect the task it created in WorkGraph.
2. Compare upstream changes against the adopted line.
3. Integrate the smallest compatible slice.
4. Run compatibility checks and focused Speedrift drift checks.
5. Update the adopted SHA in `.driftdriver/upstream-pins.toml`.
6. Push the integration commit so the next sentinel pass sees the new baseline.

If upstream is intentionally not adopted, leave the divergence clock in place and
log the reason on the WorkGraph task. The age threshold will keep the decision visible.
