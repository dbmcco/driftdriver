# CLAUDE.md

<!-- driftdriver-claude:start -->
## Speedrift Ecosystem Protocol

Use Speedrift as an ecosystem, not as disconnected repo-local hooks:

### Session Lifecycle
- At session start, run: `./.workgraph/handlers/session-start.sh --cli claude-code`
- When claiming a task, run: `./.workgraph/handlers/task-claimed.sh --cli claude-code`
- Before completing a task, run: `./.workgraph/handlers/task-completing.sh --cli claude-code`
- On error, run: `./.workgraph/handlers/agent-error.sh --cli claude-code`

### Runtime Authority
- Workgraph is the task and dependency source of truth.
- `speedriftd` is the repo-local runtime supervisor.
- Interactive sessions default to `observe`; they should refresh/report state, not silently take scheduler authority.
- Do not use `wg service start` as the default way to kick off background work.

### Arming This Repo
- Refresh repo runtime state: `driftdriver --dir "$PWD" --json speedriftd status --refresh`
- If the user wants explicit supervision in this repo:
  - `driftdriver --dir "$PWD" speedriftd status --set-mode supervise --lease-owner <agent-name> --reason "explicit repo supervision requested"`
- If the user wants autonomous background execution:
  - `driftdriver --dir "$PWD" speedriftd status --set-mode autonomous --lease-owner <agent-name> --reason "explicit autonomous execution requested"`
- When handing the repo back, return it to passive mode:
  - `driftdriver --dir "$PWD" speedriftd status --set-mode observe --release-lease --reason "return repo to observation"`

### Ecosystem Visibility
- The central Speedrift hub is codified on port `8777`.
- To print current local and Tailscale URLs:
  - `cd /Users/braydon/projects/experiments/driftdriver && scripts/ecosystem_hub_daemon.sh url`
<!-- driftdriver-claude:end -->
