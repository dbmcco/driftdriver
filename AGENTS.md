<!-- driftdriver-codex:start -->
## Driftdriver Integration Protocol

When working on tasks in this project, follow this protocol:

### At Session Start
Run: `./.workgraph/handlers/session-start.sh --cli codex`

### When Claiming a Task
Run: `./.workgraph/handlers/task-claimed.sh --cli codex`

### Before Completing a Task
Run: `./.workgraph/handlers/task-completing.sh --cli codex`

### On Error
Run: `./.workgraph/handlers/agent-error.sh --cli codex`

### Drift Protocol
- Pre-check: `./.workgraph/drifts check --task <TASK_ID> --write-log`
- Post-check: `./.workgraph/drifts check --task <TASK_ID> --write-log --create-followups`

## Speedrift Ecosystem Protocol

- Workgraph is the source of truth for tasks and dependencies.
- `speedriftd` is the repo-local runtime supervisor. Interactive sessions do not own dispatch by default.
- Default posture is `observe`. Do not use `wg service start` as a generic way to kick off autonomous work.
- Refresh repo runtime state before acting: `driftdriver --dir "$PWD" --json speedriftd status --refresh`
- If the user wants background execution in this repo, arm it explicitly:
  - `driftdriver --dir "$PWD" speedriftd status --set-mode supervise --lease-owner <agent-name> --reason "explicit repo supervision requested"`
  - `driftdriver --dir "$PWD" speedriftd status --set-mode autonomous --lease-owner <agent-name> --reason "explicit autonomous execution requested"`
- When the task is complete or the repo should stop self-dispatching, return it to passive mode:
  - `driftdriver --dir "$PWD" speedriftd status --set-mode observe --release-lease --reason "return repo to observation"`
- To see the broader ecosystem hub and current port 8777 URLs:
  - `cd /Users/braydon/projects/experiments/driftdriver && scripts/ecosystem_hub_daemon.sh url`

## tmux Agent Monitor

A heartbeat daemon watches all tmux panes and tracks running coding agents.

### Check What Agents Are Running

```bash
# Agents relevant to current repo (default: uses cwd)
driftdriver tmux-monitor status

# Agents in a specific repo
driftdriver tmux-monitor status --repo driftdriver

# All agents (including unrelated)
driftdriver tmux-monitor status --all

# JSON output for programmatic consumption
driftdriver tmux-monitor status --json
driftdriver tmux-monitor status --repo paia-program --json
```

### Output

Each agent shows: session, pane, type (codex/claude-code/opencode), title, current task, summary, cwd, relevance (same_repo/related/unrelated), and the `tmux send-keys` target for control.

Relevant agents (same repo) are marked with `***`. Use `controllable: true` and the `pane_id` field to send commands:
```bash
tmux send-keys -t %272 "your command here" Enter
```

### When to Use

- Before starting work on a repo: check if another agent is already working there
- Before sending commands to another agent: verify it's running and get its pane_id
- To coordinate: summarize what other agents are doing and avoid conflicts
<!-- driftdriver-codex:end -->
