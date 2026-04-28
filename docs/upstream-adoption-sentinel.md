# Upstream Adoption Sentinel

Driftdriver tracks upstream projects that Speedrift depends on through:

- `.driftdriver/upstream-config.toml`: tracked repos, refs, thresholds, compatibility checks
- `.driftdriver/upstream-pins.toml`: last observed upstream/adopted SHAs and adoption-lag start times
- `.workgraph/service/ecosystem-hub/upstream-adoptions.json`: latest hub-visible adoption cycle

Run one sentinel pass:

```bash
driftdriver --dir /Users/braydon/projects/experiments/driftdriver upstream-tracker --json
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
