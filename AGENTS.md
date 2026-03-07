<!-- driftdriver-codex:start -->
## Speedrift Ecosystem

**Speedrift** is the development quality system across this workspace. It combines
[Workgraph](https://github.com/graphwork/workgraph) (task spine) with
[Driftdriver](https://github.com/dbmcco/driftdriver) (drift orchestrator) to keep
code, specs, and intent in sync without hard-blocking work.

### Quick Reference

```bash
# Drift-check a task (run at start + before completion)
./.workgraph/drifts check --task <id> --write-log --create-followups

# Ecosystem dashboard
# Local:     http://127.0.0.1:8777/
# Tailscale: http://100.77.214.44:8777/

# Create tasks with current wg flags
wg add "Title" --after <dep-id> --immediate --verify "test command"
```

### Lifecycle Hooks
- Session start: `./.workgraph/handlers/session-start.sh --cli codex`
- Task claimed: `./.workgraph/handlers/task-claimed.sh --cli codex`
- Before completion: `./.workgraph/handlers/task-completing.sh --cli codex`
- On error: `./.workgraph/handlers/agent-error.sh --cli codex`

### Runtime Authority
- Workgraph is the task/dependency source of truth. `speedriftd` is the repo-local supervisor.
- Sessions default to `observe`. Do not use `wg service start` as a generic kickoff.
- Refresh state: `driftdriver --dir "$PWD" --json speedriftd status --refresh`
- Arm repo: `driftdriver --dir "$PWD" speedriftd status --set-mode supervise --lease-owner <agent> --reason "reason"`
- Disarm: `driftdriver --dir "$PWD" speedriftd status --set-mode observe --release-lease --reason "done"`

### What Happens Automatically
- **Drift task guard**: follow-up tasks are deduped + capped at 3 per lane per repo
- **Notifications**: significant findings alert via terminal/webhook/wg-notify
- **Prompt evolution**: recurring drift patterns trigger `wg evolve` to teach agents
- **Outcome learning**: resolution rates feed back into notification significance scoring
<!-- driftdriver-codex:end -->

