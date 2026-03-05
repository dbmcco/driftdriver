# Driftdriver

Driftdriver is an orchestrator for **Workgraph-first** agent development.

- **Workgraph is the spine** (tasks, dependencies, loops, agent spawning).
- **Driftdriver coordinates "drift" tools** (code drift, UX drift, data drift, etc.) without hard-blocking work.
- Findings are written back into Workgraph via `wg log` and follow-up tasks, keeping the graph in sync.

Today it supports:
- `coredrift` (baseline, always-run)
- `specdrift` (optional)
- `datadrift` (optional)
- `archdrift` (optional)
- `depsdrift` (optional)
- `uxdrift` (optional)
- `therapydrift` (optional)
- `fixdrift` (optional)
- `yagnidrift` (optional)
- `redrift` (optional)

Plugin interface: see `DRIFT_PLUGIN_CONTRACT.md`.

## Evolution & External Integration

Driftdriver started as a thin orchestrator routing `coredrift` checks. It has grown into a full ecosystem coordinator:

- **10 specialized drift lanes** from baseline contract checks (coredrift) through brownfield rebuilds (redrift)
- **Configurable ecosystem monitoring** — a daily scanner tracks 23+ repos, 7 GitHub users/orgs, and watched report URLs for upstream changes
- **Runtime-agnostic integration** — works with Amplifier (Microsoft), Claude Code, and any CLI agent runtime
- **Automatic update detection** — surfaces upstream changes as Workgraph eval tasks for human decision; no auto-updates applied

Driftdriver is intentionally reliant on external projects rather than reimplementing their capabilities:

| Dependency | Role |
|---|---|
| [Workgraph](https://github.com/graphwork/workgraph) | Task graph spine — all tasks, deps, loops, and contracts flow through `wg` |
| [Amplifier](https://github.com/microsoft/amplifier) | Agent runtime — session management, executor dispatch, hook-based auto-bootstrap |
| [claude-session-driver](https://github.com/obra/claude-session-driver) | Multi-agent orchestration — fan-out workers, supervised pipelines |
| [superpowers](https://github.com/obra/superpowers) | Skills/workflow plugin — brainstorming, planning, TDD, code review patterns |
| [mira-OSS](https://github.com/taylorsatula/mira-OSS) | Memory decay patterns — activity-weighted scoring ported into ecosystem findings |
| [lessons-mcp](https://github.com/dbmcco/lessons-mcp) | Cross-session knowledge with evolutionary signal classification |
| [beads](https://github.com/steveyegge/beads) | Git-backed task tracking via `bd` CLI |

### Automated Ecosystem Monitoring

The daily ecosystem scanner (`scripts/daily_ecosystem_eval.sh`) runs on a configurable schedule:

1. Queries GitHub for HEAD commits on all core and configured external repos
2. Scans watched GitHub users for new or updated repositories
3. Fetches watched report URLs and detects content changes via keyword matching
4. Creates Workgraph eval tasks when actionable findings are detected
5. Retains the last 30 review snapshots (JSON + Markdown) under `.workgraph/.driftdriver/reviews/`

Configure monitoring via `.workgraph/.driftdriver/ecosystem-review.json` (template: `docs/ecosystem-review.example.json`).

Findings are advisory — humans decide via the generated eval tasks whether to update, defer, or investigate.

### Activity-Weighted Decay

Ecosystem findings are scored using MIRA-style activity-weighted decay:

```
importance = 0.95^(days_since_last_reference) * newness_boost
```

- **Newness boost (1.3x)** applies to findings less than 7 days old
- **Reference resets decay** — when a finding is acted on (eval task created, review mention), `mark_finding_referenced()` resets the timer
- `score_all_findings()` returns all state entries sorted by importance

This prevents stale findings from cluttering reviews while keeping recently-acted-on findings prominent.

### Workgraph Evaluation Scores

When Workgraph's agency layer provides evaluation scores (individual quality, org impact), `build_review_prompt()` incorporates them as additional evidence for milestone review. Scores are read from:
- `.workgraph/output/{task_id}` files (avg_score, evaluation lines)
- `wg show {task_id}` output (evaluation score/grade entries)

This is a read-only integration — autopilot does not control Workgraph's evaluation cascade.

## Ecosystem Map

This project is part of the Speedrift suite for Workgraph-first drift control.

### Core Suite

- Suite home: [speedrift-ecosystem](https://github.com/dbmcco/speedrift-ecosystem)
- Spine: [Workgraph](https://github.com/graphwork/workgraph) ([docs](https://graphwork.github.io/))
- Orchestrator: [driftdriver](https://github.com/dbmcco/driftdriver)
- Baseline lane: [coredrift](https://github.com/dbmcco/coredrift)
- Optional lanes: [specdrift](https://github.com/dbmcco/specdrift), [datadrift](https://github.com/dbmcco/datadrift), [archdrift](https://github.com/dbmcco/archdrift), [depsdrift](https://github.com/dbmcco/depsdrift), [uxdrift](https://github.com/dbmcco/uxdrift), [therapydrift](https://github.com/dbmcco/therapydrift), `fixdrift`, [yagnidrift](https://github.com/dbmcco/yagnidrift), [redrift](https://github.com/dbmcco/redrift)
- Amplifier integration: [amplifier-bundle-speedrift](https://github.com/dbmcco/amplifier-bundle-speedrift)

### External Dependencies

- [Amplifier](https://github.com/microsoft/amplifier) — Agent runtime (Microsoft): [core](https://github.com/microsoft/amplifier-core), [app-cli](https://github.com/microsoft/amplifier-app-cli), [foundation](https://github.com/microsoft/amplifier-foundation) — [guide](https://ramparte.github.io/amplifier-stories/)
- [Workgraph](https://github.com/graphwork/workgraph) — Task graph spine, `wg` CLI (Erik Garrison / [graphwork](https://graphwork.github.io))
- [superpowers](https://github.com/obra/superpowers) — Core skills/workflow plugin (Jesse Vincent / obra)
- [superpowers-chrome](https://github.com/obra/superpowers-chrome) — Chrome DevTools browser control (Jesse Vincent / obra)
- [claude-session-driver](https://github.com/obra/claude-session-driver) — Worker session orchestration (Jesse Vincent / obra)
- [freshell](https://github.com/danshapiro/freshell) — Shell framework (Dan Shapiro)
- [mira-OSS](https://github.com/taylorsatula/mira-OSS) — Discrete memory decay + modular system prompt (Taylor Satula)
- [beads](https://github.com/steveyegge/beads) — Git-backed task tracking, `bd` CLI (Steve Yegge)

### Watched GitHub Users / Orgs

The daily ecosystem scanner monitors these for new repos and activity:

- [@obra](https://github.com/obra) (superpowers, claude-session-driver, superpowers-chrome)
- [@2389](https://github.com/2389) (2389-research)
- [@danshapiro](https://github.com/danshapiro) (Freshell)
- [@taylorsatula](https://github.com/taylorsatula) (MIRA)
- [@steveyegge](https://github.com/steveyegge) (Beads)
- [@ramparte](https://github.com/ramparte) (Amplifier bundles/extensions)
- [@ekg](https://github.com/ekg) (Erik Garrison — Workgraph / [graphwork](https://github.com/graphwork))
- [@dsifry](https://github.com/dsifry) (Metaswarm — multi-agent orchestration)
- [@Joi](https://github.com/Joi) (AI agent learnings)

The vibez community (WhatsApp "code code code" group) is an additional intelligence source — members actively share repos and tools relevant to agentic development. Community discoveries are triaged via [vibez-monitor](https://github.com/dbmcco/vibez-monitor) and added to the scanner config when relevant.

## Install (CLI)

You need `wg` (Workgraph) installed first.

Fastest install today is `pipx` from GitHub:

```bash
pipx install git+https://github.com/dbmcco/driftdriver.git
pipx install git+https://github.com/dbmcco/coredrift.git

# Optional plugins:
pipx install git+https://github.com/dbmcco/specdrift.git
pipx install git+https://github.com/dbmcco/datadrift.git
pipx install git+https://github.com/dbmcco/archdrift.git
pipx install git+https://github.com/dbmcco/depsdrift.git
pipx install git+https://github.com/dbmcco/uxdrift.git
pipx install git+https://github.com/dbmcco/therapydrift.git
# fixdrift (local checkout path while repository publishing is in progress)
# pipx install /path/to/fixdrift
pipx install git+https://github.com/dbmcco/yagnidrift.git
pipx install git+https://github.com/dbmcco/redrift.git
```

## Install Into A Repo

From the repo you want to work in:

```bash
driftdriver install
```

Note: baseline lane was renamed from `speedrift` to `coredrift`.

Optional UX + therapy + fix-quality + YAGNI + redrift integration:

```bash
driftdriver install --with-uxdrift --with-therapydrift --with-fixdrift --with-yagnidrift --with-redrift
```

Optional Amplifier executor + autostart hook integration:

```bash
driftdriver install --with-amplifier-executor
```

This writes:
- `.workgraph/executors/amplifier.toml`
- `.workgraph/executors/amplifier-run.sh`
- `.amplifier/hooks/speedrift-autostart/hooks.json`
- `.amplifier/hooks/speedrift-autostart/session-start.sh`

Use this when you want Workgraph to spawn Amplifier sessions and auto-bootstrap Speedrift on Amplifier session start.
In current Amplifier runtime paths, bootstrap is triggered on first prompt submit (plus SessionStart compatibility hooks).
The generated autostart hook now also:
- keeps `wg service` running (prefers `--executor amplifier`)
- starts a background Speedrift autopilot loop that runs `./.workgraph/drifts orchestrate --write-log --create-followups` every 90 seconds
- writes monitor state under `.workgraph/service/` (`speedrift-autopilot.pid`, `speedrift-autopilot.log`)

## Package Any App (Self-Serve)

If you're working from this mono-workspace and want one command to package an app with local drift tool bins:

```bash
cd driftdriver
scripts/package_app.sh --app /path/to/app --seed-redrift-task
```

What it does:
- installs `driftdriver` wrappers into the target app's `.workgraph/`
- wires all locally available modules (`coredrift`, `specdrift`, `datadrift`, `archdrift`, `depsdrift`, `uxdrift`, `therapydrift`, `fixdrift`, `yagnidrift`, `redrift`)
- optionally seeds a starter redrift task with a full-suite fence set

Common flags:
- `--wrapper-mode auto|pinned|portable` (default: `auto`)
- `--skip-contracts` (skip `ensure-contracts` during install)
- `--seed-redrift-task` (create a starter migration/rebuild task)

### Wrapper Modes (Portable vs Pinned)

By default `driftdriver install` chooses wrapper style automatically:

- `--wrapper-mode auto`: portable only when core tools are on PATH (default)
- `--wrapper-mode pinned`: `.workgraph/*` wrappers exec absolute paths (best for local checkouts)
- `--wrapper-mode portable`: `.workgraph/*` wrappers exec tools on PATH (commit-safe)

If you want to commit `./.workgraph/drifts` (and wrappers) into the repo, use:

```bash
driftdriver install --wrapper-mode portable --with-uxdrift --with-therapydrift --with-fixdrift --with-yagnidrift --with-redrift
```

If Amplifier is your primary CLI runtime, add:

```bash
driftdriver install --wrapper-mode portable --with-uxdrift --with-therapydrift --with-fixdrift --with-yagnidrift --with-redrift --with-amplifier-executor
```

This writes:
- `./.workgraph/driftdriver` (wrapper)
- `./.workgraph/drifts` (single per-repo entrypoint used by agents)
- `./.workgraph/coredrift` (wrapper)
- (optional) `./.workgraph/archdrift` (wrapper)
- (optional) `./.workgraph/uxdrift` (wrapper)
- (optional) `./.workgraph/therapydrift` (wrapper)
- (optional) `./.workgraph/fixdrift` (wrapper)
- (optional) `./.workgraph/yagnidrift` (wrapper)
- (optional) `./.workgraph/redrift` (wrapper)
- (optional) `./.workgraph/executors/amplifier.toml` (Workgraph -> Amplifier executor)
- (optional) `./.workgraph/executors/amplifier-run.sh` (executor wrapper)
- (optional) `.amplifier/hooks/speedrift-autostart/hooks.json` (UserPromptSubmit + SessionStart triggers)
- (optional) `.amplifier/hooks/speedrift-autostart/session-start.sh` (auto-bootstrap + daemon/monitor startup script)
- `./.workgraph/drift-policy.toml` (mode/order/recursion defaults)
- executor prompt guidance under `./.workgraph/executors/*.toml`

## Drift Policy

`driftdriver install` creates `./.workgraph/drift-policy.toml`:

```toml
schema = 1
mode = "redirect"
order = ["coredrift", "specdrift", "datadrift", "archdrift", "depsdrift", "uxdrift", "therapydrift", "fixdrift", "yagnidrift", "redrift"]

[recursion]
cooldown_seconds = 1800
max_auto_actions_per_hour = 2
require_new_evidence = true
max_auto_depth = 2

[contracts]
auto_ensure = true

[updates]
enabled = true
check_interval_seconds = 21600
create_followup = false

[loop_safety]
max_redrift_depth = 2
max_ready_drift_followups = 20
block_followup_creation = true
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
- Contract hygiene: if `[contracts].auto_ensure = true`, driftdriver runs `coredrift ensure-contracts --apply` before checks.
- Update preflight runs before `check` when `[updates].enabled = true`.
  - If ecosystem repo heads (or configured discovery sources) changed, driftdriver prints a decision prompt:
    - `Decision needed: should the model/toolchain self-update now?`
  - Update checks are cached by `check_interval_seconds` in `./.workgraph/.driftdriver/update-state.json`.
  - If `create_followup = true`, driftdriver creates a deterministic follow-up task per origin task.
  - Optional discovery config lives at `./.workgraph/.driftdriver/ecosystem-review.json`:
    - add `extra_repos` for borrowed repos you want monitored
    - add `github_users` (for example `jesse`, `2389`) to scan for new/updated repos
    - add `reports` URLs (for example your Bibez report) and `report_keywords` to surface useful lines
- Loop safety: if `[loop_safety]` thresholds are exceeded, driftdriver downgrades the run to advisory mode (logs yes, new follow-ups no).
- Lane routing is controlled per run with `--lane-strategy`:
  - `auto` (default): respects task fences and escalates to full-suite for complex/rebuild tasks.
  - `fences`: only run optional lanes explicitly fenced in task description.
  - `all`: run every installed optional lane.

Manual update check:

```bash
driftdriver updates
driftdriver updates --force
driftdriver updates --json --force
driftdriver updates --watch-user jesse --watch-user 2389 --watch-report bibez=https://example.com/report --report-keyword workgraph --force
driftdriver updates --config ./.workgraph/.driftdriver/ecosystem-review.json --write-review ./.workgraph/.driftdriver/reviews/latest.md --force

# health + queue controls
driftdriver doctor
driftdriver doctor --fix
driftdriver queue --limit 10
driftdriver compact
driftdriver compact --apply

# one-shot flow: check + normalized actions + next queued drift tasks
driftdriver run --task <id>
```

Scheduled review helper:

```bash
# writes timestamped JSON + Markdown into .workgraph/.driftdriver/reviews/
scripts/run_ecosystem_review.sh --app .
```

Config template:

```bash
cp docs/ecosystem-review.example.json ./.workgraph/.driftdriver/ecosystem-review.json
```

## Project Autopilot

Full-loop autonomous execution: goal → task decomposition → parallel worker dispatch → drift checks → milestone review → report.

```bash
# Decompose a goal and execute it end-to-end
driftdriver autopilot --goal "Build user authentication system"

# Skip decomposition, use existing workgraph tasks
driftdriver autopilot --goal "Complete remaining tasks" --skip-decompose

# Dry run — show what would be dispatched without executing
driftdriver autopilot --goal "Add API pagination" --dry-run

# Control parallelism and timeouts
driftdriver autopilot --goal "Refactor data layer" --max-parallel 2 --worker-timeout 3600

# Skip the post-loop milestone review
driftdriver autopilot --goal "Quick fix" --skip-decompose --skip-review
```

The autopilot:
1. **Decomposes** the goal into Workgraph tasks (via claude-session-driver worker or direct CLI)
2. **Dispatches** workers for each ready task, respecting dependencies
3. **Drift-checks** after each task completes; creates follow-up tasks on findings
4. **Escalates** to human only when drift failures exceed threshold (default: 3)
5. **Reviews** the milestone with an evidence-based verification worker, incorporating Workgraph agency evaluation scores when available
6. **Reports** results to `.workgraph/.autopilot/latest-report.md`

State is persisted to `.workgraph/.autopilot/` (run-state.json, workers.jsonl).

Shell wrapper with SIGTERM handling and PID tracking:

```bash
scripts/project_autopilot.sh /path/to/project "Your goal here" 4
```

## Use Tools Separately

You can run each tool directly without `driftdriver`:

```bash
coredrift --dir . check --task <id> --write-log --create-followups
specdrift --dir . wg check --task <id> --write-log --create-followups
datadrift --dir . wg check --task <id> --write-log --create-followups
archdrift --dir . wg check --task <id> --write-log --create-followups
depsdrift --dir . wg check --task <id> --write-log --create-followups
uxdrift wg --dir . check --task <id> --write-log --create-followups
therapydrift --dir . wg check --task <id> --write-log --create-followups
fixdrift --dir . wg check --task <id> --write-log --create-followups
yagnidrift --dir . wg check --task <id> --write-log --create-followups
redrift --dir . wg check --task <id> --write-log --create-followups
```

Use `driftdriver` when you want one command (`./.workgraph/drifts check`) that routes by lane strategy + policy.

Detailed per-module standalone vs combined playbooks:
- `MODULE_GUIDE.md`

## Per-Task Protocol

Agents should run (at task start and before completion):

```bash
./.workgraph/drifts check --task <id> --write-log --create-followups
```

For UX-heavy tasks, define an `uxdrift` fence on the task and keep using the same command:

````md
```uxdrift
schema = 1
url = "http://localhost:3000"
pages = ["/", "/checkout"]
llm = true
pov = "doet-norman-v1"
pov_focus = ["discoverability", "feedback", "error_prevention_recovery"]
```
````

For complex apps or rebuild programs, use:

```bash
./.workgraph/drifts check --task <id> --lane-strategy all --write-log --create-followups
```

Exit codes:
- `0`: clean
- `3`: findings exist (advisory; act via follow-ups / contract edits)

## Continuous "Pit Wall" Mode

If you want drift telemetry running continuously while work happens:

```bash
./.workgraph/drifts orchestrate --write-log --create-followups
```

## Ecosystem Hub Daemon + Web Report

Driftdriver now includes an ecosystem hub service that centralizes status across suite repos and serves a local web report.

Start unattended automation (daemon + web report + upstream dry-run actions):

```bash
scripts/ecosystem_hub.sh --project-dir . automate --host 127.0.0.1 --port 8777 --interval-seconds 60
```

Equivalent explicit daemon start command:

```bash
scripts/ecosystem_hub.sh --project-dir . start --host 127.0.0.1 --port 8777 --interval-seconds 60
```

Check status:

```bash
scripts/ecosystem_hub.sh --project-dir . status
```

Stop service:

```bash
scripts/ecosystem_hub.sh --project-dir . stop
```

Web report:
- Dashboard: `http://127.0.0.1:8777/`
- APIs: `/api/status`, `/api/repos`, `/api/next-work`, `/api/updates`, `/api/upstream`, `/api/overview`, `/api/graph`, `/api/repo-dependencies`
- Live stream (WebSocket): `/ws/status` (dashboard auto-switches to live mode when connected)
- Tailscale access (when daemon is bound to `0.0.0.0`): `http://<tailscale-ip>:8777/`

Dashboard includes:
- Narrated operations summary (model-style status text)
- Operational overview cards
- By-repo health cards with sortable/filterable controls (priority, dirtiness, blocked/behind, service, health)
- Repo dependency overview map (cross-repo signals, top inbound/outbound dependency pressure, click-to-focus repo graph)
- Task dependency graph visualization (`all repos` / per-repo, focus chain / active+blocked / full modes, zoom/pan, node path inspector, cycle edge highlighting)
- Action center with repo/sort/priority/dirtiness filters, per-queue counts, and structured `what/why/prompt` rows (attention queue + aging/dependencies + upstream candidates + planned next work)
- Queue items include Claude/Codex-ready prompts (copy button) and repo graph focus links
- Central supervisor metrics (service restarts/failures) in overview cards
- Auto-discovery of additional active speedrift repos in workspace (recent `.workgraph/graph.jsonl` + speedrift drift-policy), beyond `ecosystem.toml`

Central register integration:
- If `reporting.central_repo` is set in `.workgraph/drift-policy.toml`, each snapshot is mirrored to:
  - `<central_repo>/ecosystem-hub/register/<project>.json` (latest)
  - `<central_repo>/ecosystem-hub/history/<project>/<timestamp>.json` (history)
- Hub snapshot also includes `central_reports` summary from `<central_repo>/reports/*`.

One-shot snapshot (non-daemon):

```bash
scripts/ecosystem_hub.sh --project-dir . once --skip-updates
```

Generate upstream contribution packets:

```bash
scripts/ecosystem_hub.sh --project-dir . upstream-report --output .workgraph/service/ecosystem-hub/upstream-candidates.md
```

Prepare draft PR commands (dry-run):

```bash
scripts/ecosystem_hub.sh --project-dir . open-draft-pr
```

Execute draft PR creation (explicit opt-in):

```bash
scripts/ecosystem_hub.sh --project-dir . open-draft-pr --repo driftdriver --execute
```

Execute draft PR creation continuously during automation loop (use with care):

```bash
scripts/ecosystem_hub.sh --project-dir . automate --execute-draft-prs
```

Codified daemon runner (stable host/port + optional launchd persistence):

```bash
# start daemon with defaults host=0.0.0.0 port=8777
scripts/ecosystem_hub_daemon.sh start

# print current local/tailscale URLs
scripts/ecosystem_hub_daemon.sh url

# show daemon status plus URLs
scripts/ecosystem_hub_daemon.sh status

# install persistent launchd service (auto-start + keepalive)
scripts/ecosystem_hub_daemon.sh install-launchd

# install launchd with an explicit shared central register path
ECOSYSTEM_HUB_CENTRAL_REPO=/Users/braydon/projects/experiments/speedrift-ecosystem/.workgraph/service/ecosystem-central \
  scripts/ecosystem_hub_daemon.sh install-launchd

# ensure daemon is persistent and healthy (safe to run repeatedly)
scripts/ecosystem_hub_daemon.sh ensure-running
```

Daemon configuration knobs (environment variables):
- `ECOSYSTEM_HUB_HOST` (default `0.0.0.0`)
- `ECOSYSTEM_HUB_PORT` (default `8777`)
- `ECOSYSTEM_HUB_INTERVAL_SECONDS` (default `60`)
- `ECOSYSTEM_HUB_MAX_NEXT` (default `5`)
- `ECOSYSTEM_HUB_CENTRAL_REPO` (default empty: auto-resolve from drift-policy)
- `ECOSYSTEM_HUB_EXECUTE_DRAFT_PRS` (`0`/`1`, default `0`)
- `ECOSYSTEM_HUB_SKIP_UPDATES` (`0`/`1`, default `1`)
- `ECOSYSTEM_HUB_SUPERVISE_SERVICES` (`0`/`1`, default `1`)
- `ECOSYSTEM_HUB_SUPERVISE_COOLDOWN_SECONDS` (default `180`)
- `ECOSYSTEM_HUB_SUPERVISE_MAX_STARTS` (default `4` per cycle)
- `ECOSYSTEM_HUB_PYTHON` (default `python3` on PATH; set explicitly for launchd if needed)

When `ECOSYSTEM_HUB_SUPERVISE_SERVICES=1`, the central daemon supervises repos in the registry/discovery set:
- if repo has work underway (`in-progress` or `ready`) and workgraph service is stopped, it attempts restart
- restart attempts are cooldown-limited per repo and max-limited per cycle

Each candidate packet includes:
- repo and branch context
- ahead/behind vs upstream base
- changed files
- suggested PR category (`bugfix`, `docs`, `tooling`, `feature`)

Smoke test:

```bash
scripts/ecosystem_hub_smoke.sh .
```

## Autonomous Dark Factory Plan (Model-Mediated)

Design docs for the next-stage ecosystem control plane:

- `docs/plans/factorydrift-model-mediated-contract.md`
- `docs/plans/drift-policy-factory-extensions.md`
- `docs/plans/dark-factory-operating-model.md`
- `docs/plans/secdrift-qadrift-model-mediated-design.md`

These documents define:
- model-vs-code decision ownership
- new ecosystem drift modules (`sourcedrift`, `syncdrift`, `stalledrift`, `servicedrift`, `federatedrift`, `secdrift`, `qadrift`, `factorydrift`)
- policy extensions for autonomy tiers and safety budgets
- phased rollout toward autonomous dark-factory operation
  with upstream pull-down integration and verifiable guardrails
  across growing repo counts.

Phase 0 implementation is now available:

```bash
# run one policy-bounded factory cycle (plan + prompts + decision ledger)
driftdriver factory --json

# force a cycle even if [factory].enabled=false
driftdriver factory --force --json

# execute safe automated handlers for this cycle
driftdriver factory --execute --json

# force local corrective task emission (writes to each repo's workgraph)
driftdriver factory --emit-followups --json

# run without writing local/central ledger files
driftdriver factory --no-write-ledger --json
```

Factory cycle writes:
- local ledger: `.workgraph/service/factoryd/latest.json` and `.workgraph/service/factoryd/history/<timestamp>.json`
- central ledger (when configured): `<central_repo>/ecosystem-hub/factory/register/<project>.json`
  and `<central_repo>/ecosystem-hub/factory/history/<project>/<timestamp>.json`

When `[factory].enabled = true`, the ecosystem hub daemon emits a factory cycle each tick,
runs safe automated handlers when `[factory].plan_only = false`, and persists decision ledger + execution results.

To have the auditor create corrective tasks in local repos for agents to pick up, set:
- `[factory].emit_followups = true`
- `[factory].max_followups_per_repo = 2` (or your preferred cap)

To move out of plan-only mode:
- `[factory].plan_only = false`
- keep `[factory].hard_stop_on_failed_verification = true` for safety

Current automated handlers include:
- restart stopped repo workgraph services
- run `git fetch --all --prune` for sync signals
- run driftdriver checks for active tasks
- run upstream update checks
- run `secdrift` scans and emit policy-bounded security review tasks
- run program-level `qadrift` scans and emit policy-bounded quality review tasks
- delegate risky corrective actions to explicit local follow-up tasks

Optional security/quality policy sections:

```toml
[secdrift]
enabled = true
interval_seconds = 14400
run_pentest = false
allow_network_scans = false
target_urls = []
emit_review_tasks = true
max_review_tasks_per_repo = 3
hard_stop_on_critical = false

[qadrift]
enabled = true
interval_seconds = 21600
emit_review_tasks = true
max_review_tasks_per_repo = 3
include_playwright = true
include_test_health = true
include_workgraph_health = true
```

## Development

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
scripts/e2e_smoke.sh
python3 -m unittest tests.test_ecosystem_hub
scripts/ecosystem_hub_smoke.sh .
```
