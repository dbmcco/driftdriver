# Dark Factory — Level 5 Autonomy Rollout

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable continuous autonomous improvement across 4 repos (training-assistant, news-briefing, vibez-monitor, lodestar) with full notification stack and kill switch.

**Architecture:** Ecosystem hub ranks repos by pressure, speedriftd arms them for autonomous operation, attractor loop drives convergence toward `production-ready`, dispatch-loop.sh spawns agents (workaround for wg#4). Four notification channels: terminal, hub dashboard, macOS, Telegram.

**Tech Stack:** driftdriver (Python), workgraph (Rust CLI), bash scripts, osascript, Telegram Bot API

---

### Task 1: Declare production-ready attractor in lodestar

**Files:**
- Modify: `/Users/braydon/projects/experiments/lodestar/.workgraph/drift-policy.toml`

**Step 1: Add attractor section to drift-policy.toml**

Open the file and add this block after the `[recursion]` section (before any lane-specific sections):

```toml
[attractor]
target = "production-ready"

[attractor.breakers]
max_passes = 3
max_tasks_per_cycle = 30
max_dispatches_per_cycle = 10
plateau_threshold = 2
pass_timeout_seconds = 1800
```

**Step 2: Verify the attractor is recognized**

Run: `cd /Users/braydon/projects/experiments/lodestar && driftdriver attractor status --json`
Expected: JSON output showing target = "production-ready" and current finding counts per lane.

**Step 3: Commit**

```bash
cd /Users/braydon/projects/experiments/lodestar
git add .workgraph/drift-policy.toml
git commit -m "feat: declare production-ready attractor target"
```

---

### Task 2: Declare production-ready attractor in training-assistant

**Files:**
- Modify: `/Users/braydon/projects/experiments/training-assistant/.workgraph/drift-policy.toml`

**Step 1: Update attractor section**

training-assistant already has `attractor = "production-ready"` at root level (line 4). Replace it with the full block:

```toml
[attractor]
target = "production-ready"

[attractor.breakers]
max_passes = 3
max_tasks_per_cycle = 30
max_dispatches_per_cycle = 10
plateau_threshold = 2
pass_timeout_seconds = 1800
```

**Step 2: Verify**

Run: `cd /Users/braydon/projects/experiments/training-assistant && driftdriver attractor status --json`
Expected: JSON showing target = "production-ready"

**Step 3: Commit**

```bash
cd /Users/braydon/projects/experiments/training-assistant
git add .workgraph/drift-policy.toml
git commit -m "feat: declare production-ready attractor target"
```

---

### Task 3: Declare production-ready attractor in news-briefing

**Files:**
- Modify: `/Users/braydon/projects/experiments/news-briefing/.workgraph/drift-policy.toml`

**Step 1: Add attractor section**

Add after the existing `[recursion]` section:

```toml
[attractor]
target = "production-ready"

[attractor.breakers]
max_passes = 3
max_tasks_per_cycle = 30
max_dispatches_per_cycle = 10
plateau_threshold = 2
pass_timeout_seconds = 1800
```

**Step 2: Verify**

Run: `cd /Users/braydon/projects/experiments/news-briefing && driftdriver attractor status --json`
Expected: JSON showing target = "production-ready"

**Step 3: Commit**

```bash
cd /Users/braydon/projects/experiments/news-briefing
git add .workgraph/drift-policy.toml
git commit -m "feat: declare production-ready attractor target"
```

---

### Task 4: Declare production-ready attractor in vibez-monitor

**Files:**
- Modify: `/Users/braydon/projects/personal/vibez-monitor/.workgraph/drift-policy.toml`

**Step 1: Add attractor section**

The current file is minimal. Add:

```toml
[attractor]
target = "production-ready"

[attractor.breakers]
max_passes = 3
max_tasks_per_cycle = 30
max_dispatches_per_cycle = 10
plateau_threshold = 2
pass_timeout_seconds = 1800
```

**Step 2: Verify**

Run: `cd /Users/braydon/projects/personal/vibez-monitor && driftdriver attractor status --json`
Expected: JSON showing target = "production-ready"

**Step 3: Commit**

```bash
cd /Users/braydon/projects/personal/vibez-monitor
git add .workgraph/drift-policy.toml
git commit -m "feat: declare production-ready attractor target"
```

---

### Task 5: Configure Telegram notifications

**Files:**
- Create: `/Users/braydon/.config/workgraph/notify.toml`

**Step 1: Get Telegram bot token and chat ID**

Ask Braydon for the Telegram bot token and chat ID from the paia Telegram bot. These are NOT in the codebase (correctly).

**Step 2: Create global notify.toml**

Global config so all repos get notifications without per-repo setup:

```toml
[routing]
default = ["telegram"]
urgent = ["telegram"]
approval = ["telegram"]

[telegram]
bot_token = "<BOT_TOKEN>"
chat_id = "<CHAT_ID>"
```

**Step 3: Verify wg can read the config**

Run: `wg notify test "Dark Factory notify test" 2>&1`
Expected: Message arrives in Telegram chat.

**Step 4: No commit** — this file contains credentials and lives outside any repo.

---

### Task 6: Deploy dispatch-loop.sh to all repos

**Files:**
- Copy to: `/Users/braydon/projects/experiments/training-assistant/.workgraph/executors/dispatch-loop.sh`
- Copy to: `/Users/braydon/projects/experiments/news-briefing/.workgraph/executors/dispatch-loop.sh`
- Copy to: `/Users/braydon/projects/personal/vibez-monitor/.workgraph/executors/dispatch-loop.sh`

**Step 1: Ensure executor directories exist**

```bash
mkdir -p /Users/braydon/projects/experiments/training-assistant/.workgraph/executors
mkdir -p /Users/braydon/projects/experiments/news-briefing/.workgraph/executors
mkdir -p /Users/braydon/projects/personal/vibez-monitor/.workgraph/executors
```

**Step 2: Copy dispatch-loop.sh from lodestar**

```bash
for repo in \
  /Users/braydon/projects/experiments/training-assistant \
  /Users/braydon/projects/experiments/news-briefing \
  /Users/braydon/projects/personal/vibez-monitor; do
  cp /Users/braydon/projects/experiments/lodestar/.workgraph/executors/dispatch-loop.sh \
     "$repo/.workgraph/executors/dispatch-loop.sh"
  chmod +x "$repo/.workgraph/executors/dispatch-loop.sh"
done
```

**Step 3: Ensure claude-run.sh exists with API key fix in each repo**

Check each repo has `.workgraph/executors/claude-run.sh` with `unset ANTHROPIC_API_KEY`. If missing, copy from lodestar:

```bash
for repo in \
  /Users/braydon/projects/experiments/training-assistant \
  /Users/braydon/projects/experiments/news-briefing \
  /Users/braydon/projects/personal/vibez-monitor; do
  if [ ! -f "$repo/.workgraph/executors/claude-run.sh" ]; then
    cp /Users/braydon/projects/experiments/lodestar/.workgraph/executors/claude-run.sh \
       "$repo/.workgraph/executors/claude-run.sh"
    chmod +x "$repo/.workgraph/executors/claude-run.sh"
  fi
done
```

**Step 4: Verify dispatch-loop works in each repo**

For each repo, run:
```bash
cd <repo> && .workgraph/executors/dispatch-loop.sh &
sleep 5 && kill %1
```
Expected: Sees "Starting dispatch loop" message, no errors.

**Step 5: Commit in each repo**

```bash
for repo in \
  /Users/braydon/projects/experiments/training-assistant \
  /Users/braydon/projects/experiments/news-briefing \
  /Users/braydon/projects/personal/vibez-monitor; do
  cd "$repo"
  git add .workgraph/executors/
  git commit -m "feat: add dispatch-loop workaround for wg#4"
done
```

---

### Task 7: Create macOS notification wrapper

**Files:**
- Create: `/Users/braydon/projects/experiments/driftdriver/scripts/notify-macos.sh`

**Step 1: Write the notification script**

```bash
#!/usr/bin/env bash
# ABOUTME: macOS notification center wrapper for dark factory events.
# ABOUTME: Called by dispatch-loop and speedriftd for significant events.

set -euo pipefail

TITLE="${1:-Speedrift}"
MESSAGE="${2:-No message}"
SOUND="${3:-default}"

osascript -e "display notification \"$MESSAGE\" with title \"$TITLE\" sound name \"$SOUND\"" 2>/dev/null || true
```

**Step 2: Make executable**

```bash
chmod +x /Users/braydon/projects/experiments/driftdriver/scripts/notify-macos.sh
```

**Step 3: Test it**

Run: `/Users/braydon/projects/experiments/driftdriver/scripts/notify-macos.sh "Dark Factory" "Test notification"`
Expected: macOS notification appears.

**Step 4: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add scripts/notify-macos.sh
git commit -m "feat: add macOS notification wrapper for dark factory"
```

---

### Task 8: Upgrade dispatch-loop with notifications

**Files:**
- Modify: `/Users/braydon/projects/experiments/lodestar/.workgraph/executors/dispatch-loop.sh`

**Step 1: Add notification hooks to dispatch-loop.sh**

Replace the current dispatch-loop.sh with this enhanced version that calls notifications on significant events:

```bash
#!/usr/bin/env bash
# ABOUTME: Workaround for graphwork/workgraph#4 — daemon auto-dispatch silently fails.
# ABOUTME: Polls `wg ready`, spawns agents via `wg spawn`, respects max_agents concurrency.

set -euo pipefail

MAX_AGENTS="${WG_MAX_AGENTS:-2}"
POLL_INTERVAL="${WG_POLL_INTERVAL:-30}"
EXECUTOR="${WG_EXECUTOR:-claude}"
REPO_NAME="$(basename "$(pwd)")"
NOTIFY_SCRIPT="${WG_NOTIFY_SCRIPT:-/Users/braydon/projects/experiments/driftdriver/scripts/notify-macos.sh}"

log() { echo "[dispatch-loop] $(date +%H:%M:%S) $*"; }

notify() {
  local title="$1" msg="$2"
  # macOS notification
  [ -x "$NOTIFY_SCRIPT" ] && "$NOTIFY_SCRIPT" "$title" "$msg" &
  # wg notify (routes to Telegram via notify.toml)
  wg notify "$title: $msg" 2>/dev/null &
}

alive_count() {
  wg agents 2>/dev/null \
    | grep -c 'alive' \
    || echo 0
}

ready_tasks() {
  wg ready 2>/dev/null \
    | grep -E '^\s+\S+' \
    | awk '{print $1}' \
    | head -n "$((MAX_AGENTS - $(alive_count)))"
}

log "Starting dispatch loop (max_agents=$MAX_AGENTS, poll=${POLL_INTERVAL}s, executor=$EXECUTOR)"
log "Workaround for graphwork/workgraph#4"
notify "$REPO_NAME" "Dispatch loop started (max=$MAX_AGENTS)"

CYCLE=0
while true; do
  ALIVE=$(alive_count)

  if [ "$ALIVE" -ge "$MAX_AGENTS" ]; then
    sleep "$POLL_INTERVAL"
    continue
  fi

  SLOTS=$((MAX_AGENTS - ALIVE))
  TASKS=$(ready_tasks)

  if [ -z "$TASKS" ]; then
    # Check if all tasks are done
    OPEN=$(wg list --status open 2>/dev/null | grep -c '^\s' || echo 0)
    if [ "$OPEN" -eq 0 ] && [ "$ALIVE" -eq 0 ]; then
      CYCLE=$((CYCLE + 1))
      if [ "$CYCLE" -ge 3 ]; then
        log "All tasks complete. Exiting."
        notify "$REPO_NAME" "All tasks complete — factory idle"
        exit 0
      fi
    fi
    sleep "$POLL_INTERVAL"
    continue
  fi

  CYCLE=0
  for TASK_ID in $TASKS; do
    log "Spawning agent for: $TASK_ID"
    if wg spawn --executor "$EXECUTOR" "$TASK_ID" 2>&1; then
      log "Spawned successfully: $TASK_ID"
    else
      log "ERROR: Failed to spawn: $TASK_ID"
      notify "$REPO_NAME" "FAILED to spawn: $TASK_ID"
    fi

    SLOTS=$((SLOTS - 1))
    [ "$SLOTS" -le 0 ] && break
  done

  sleep "$POLL_INTERVAL"
done
```

**Step 2: Copy the updated version to all repos**

```bash
for repo in \
  /Users/braydon/projects/experiments/training-assistant \
  /Users/braydon/projects/experiments/news-briefing \
  /Users/braydon/projects/personal/vibez-monitor; do
  cp /Users/braydon/projects/experiments/lodestar/.workgraph/executors/dispatch-loop.sh \
     "$repo/.workgraph/executors/dispatch-loop.sh"
done
```

**Step 3: Commit in all repos**

```bash
for repo in \
  /Users/braydon/projects/experiments/lodestar \
  /Users/braydon/projects/experiments/training-assistant \
  /Users/braydon/projects/experiments/news-briefing \
  /Users/braydon/projects/personal/vibez-monitor; do
  cd "$repo"
  git add .workgraph/executors/dispatch-loop.sh
  git commit -m "feat: dispatch-loop with macOS + Telegram notifications"
done
```

---

### Task 9: Add repos to ecosystem.toml

**Files:**
- Modify: `/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml`

**Step 1: Read the current ecosystem.toml**

```bash
cat /Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml
```

**Step 2: Add the 4 dark factory repos**

Add these entries (preserve existing repos):

```toml
[repos.lodestar]
role = "product"
path = "../lodestar"

[repos.training-assistant]
role = "product"
path = "../training-assistant"

[repos.news-briefing]
role = "product"
path = "../news-briefing"

[repos.vibez-monitor]
role = "product"
path = "../../personal/vibez-monitor"
```

**Step 3: Verify hub can discover them**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -c "from driftdriver.ecosystem_hub.discovery import load_ecosystem_config; c = load_ecosystem_config(); print([r for r in c.get('repos', {})])"`
Expected: All 4 repos appear in the list.

**Step 4: Commit**

```bash
cd /Users/braydon/projects/experiments/speedrift-ecosystem
git add ecosystem.toml
git commit -m "feat: add dark factory repos to ecosystem manifest"
```

---

### Task 10: Enable factory in driftdriver

**Files:**
- Modify: `/Users/braydon/projects/experiments/driftdriver/.workgraph/drift-policy.toml` (or global driftdriver config)

**Step 1: Check where factory config lives**

```bash
grep -r "factory" /Users/braydon/projects/experiments/driftdriver/.workgraph/drift-policy.toml 2>/dev/null
```

**Step 2: Enable factory with live settings**

Update or add the factory section:

```toml
[factory]
enabled = true
cycle_seconds = 90
plan_only = false
max_repos_per_cycle = 4
max_actions_per_cycle = 12
emit_followups = true
max_followups_per_repo = 3
write_decision_ledger = true
hard_stop_on_failed_verification = true
```

Key changes from defaults:
- `enabled = true` — turn it on
- `plan_only = false` — actually execute, don't just plan
- `emit_followups = true` — create follow-up tasks from findings
- `max_followups_per_repo = 3` — cap follow-ups per repo per cycle

**Step 3: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add .workgraph/drift-policy.toml
git commit -m "feat: enable dark factory — plan_only=false, emit_followups=true"
```

---

### Task 11: Create dark-factory launcher script

**Files:**
- Create: `/Users/braydon/projects/experiments/driftdriver/scripts/dark-factory.sh`

**Step 1: Write the launcher**

This is the master script that starts the whole system — dispatch loops for all repos, ecosystem hub, and the factory cycle:

```bash
#!/usr/bin/env bash
# ABOUTME: Dark Factory launcher — starts autonomous improvement across all enrolled repos.
# ABOUTME: Manages dispatch loops, ecosystem hub, and continuous attractor convergence.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DRIFTDRIVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
NOTIFY="$SCRIPT_DIR/notify-macos.sh"
PIDS=()

cleanup() {
  echo "[dark-factory] Shutting down..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  [ -x "$NOTIFY" ] && "$NOTIFY" "Dark Factory" "Shut down"
  wait 2>/dev/null
  echo "[dark-factory] Done."
}
trap cleanup EXIT INT TERM

log() { echo "[dark-factory] $(date +%H:%M:%S) $*"; }

# ── Repos enrolled in the dark factory ──
REPOS=(
  "/Users/braydon/projects/experiments/lodestar"
  "/Users/braydon/projects/experiments/training-assistant"
  "/Users/braydon/projects/experiments/news-briefing"
  "/Users/braydon/projects/personal/vibez-monitor"
)

# ── 1. Start dispatch loops per repo ──
for repo in "${REPOS[@]}"; do
  name="$(basename "$repo")"
  dispatch="$repo/.workgraph/executors/dispatch-loop.sh"

  if [ ! -x "$dispatch" ]; then
    log "WARN: No dispatch-loop.sh in $name, skipping"
    continue
  fi

  log "Starting dispatch loop for $name"
  (cd "$repo" && exec "$dispatch") &
  PIDS+=($!)
done

# ── 2. Arm speedriftd in all repos ──
for repo in "${REPOS[@]}"; do
  name="$(basename "$repo")"
  log "Arming $name → autonomous mode"
  driftdriver --dir "$repo" speedriftd status \
    --set-mode autonomous \
    --lease-owner "dark-factory" \
    --reason "Dark Factory Level 5 rollout" 2>&1 || true
done

# ── 3. Start ecosystem hub ──
log "Starting ecosystem hub on port 8777"
(cd "$DRIFTDRIVER_DIR" && python -m driftdriver.ecosystem_hub.server) &
PIDS+=($!)

[ -x "$NOTIFY" ] && "$NOTIFY" "Dark Factory" "Online — ${#REPOS[@]} repos armed"
log "Dark Factory online. ${#REPOS[@]} repos, ${#PIDS[@]} processes."
log "Kill switch: driftdriver --dir <repo> speedriftd status --set-mode observe --release-lease --reason 'kill'"
log "Full stop: Ctrl+C or kill $$"

# ── 4. Continuous attractor convergence loop ──
CYCLE_INTERVAL=90

while true; do
  for repo in "${REPOS[@]}"; do
    name="$(basename "$repo")"

    # Check if repo is still armed
    MODE=$(driftdriver --dir "$repo" --json speedriftd status 2>/dev/null \
      | python3 -c "import sys,json; print(json.load(sys.stdin).get('mode','observe'))" 2>/dev/null \
      || echo "observe")

    if [ "$MODE" != "autonomous" ] && [ "$MODE" != "supervise" ]; then
      continue
    fi

    log "Running attractor loop for $name"
    driftdriver --dir "$repo" attractor run --json 2>&1 | while IFS= read -r line; do
      echo "[attractor:$name] $line"
    done || true
  done

  sleep "$CYCLE_INTERVAL"
done
```

**Step 2: Make executable**

```bash
chmod +x /Users/braydon/projects/experiments/driftdriver/scripts/dark-factory.sh
```

**Step 3: Dry run test**

Run: `/Users/braydon/projects/experiments/driftdriver/scripts/dark-factory.sh`
Expected: Dispatch loops start for all 4 repos, speedriftd armed, ecosystem hub starts on 8777, attractor loop begins cycling. Ctrl+C cleanly shuts everything down.

**Step 4: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add scripts/dark-factory.sh
git commit -m "feat: dark factory launcher — Level 5 autonomy for 4 repos"
```

---

### Task 12: Create kill-switch convenience script

**Files:**
- Create: `/Users/braydon/projects/experiments/driftdriver/scripts/dark-factory-stop.sh`

**Step 1: Write the kill switch**

```bash
#!/usr/bin/env bash
# ABOUTME: Emergency stop for the Dark Factory — disarms all repos immediately.
# ABOUTME: Usage: dark-factory-stop.sh [repo-name] (omit repo to stop all)

set -euo pipefail

REPOS=(
  "/Users/braydon/projects/experiments/lodestar"
  "/Users/braydon/projects/experiments/training-assistant"
  "/Users/braydon/projects/experiments/news-briefing"
  "/Users/braydon/projects/personal/vibez-monitor"
)

REASON="${1:-manual kill switch}"

if [ -n "${1:-}" ]; then
  # Stop specific repo by name
  for repo in "${REPOS[@]}"; do
    if [ "$(basename "$repo")" = "$1" ]; then
      echo "Disarming $1..."
      driftdriver --dir "$repo" speedriftd status \
        --set-mode observe --release-lease --reason "$REASON"
      echo "Done. $1 is now in observe mode."
      exit 0
    fi
  done
  echo "Unknown repo: $1"
  echo "Available: ${REPOS[*]##*/}"
  exit 1
fi

# Stop all repos
for repo in "${REPOS[@]}"; do
  name="$(basename "$repo")"
  echo "Disarming $name..."
  driftdriver --dir "$repo" speedriftd status \
    --set-mode observe --release-lease --reason "$REASON" 2>&1 || true
done

echo "All repos disarmed. Dark Factory is idle."

NOTIFY="/Users/braydon/projects/experiments/driftdriver/scripts/notify-macos.sh"
[ -x "$NOTIFY" ] && "$NOTIFY" "Dark Factory" "STOPPED — all repos disarmed"
```

**Step 2: Make executable and commit**

```bash
chmod +x /Users/braydon/projects/experiments/driftdriver/scripts/dark-factory-stop.sh
cd /Users/braydon/projects/experiments/driftdriver
git add scripts/dark-factory-stop.sh
git commit -m "feat: dark factory kill switch — disarm one or all repos"
```

---

### Task 13: Smoke test — bring the factory online

**Step 1: Start the dark factory**

```bash
/Users/braydon/projects/experiments/driftdriver/scripts/dark-factory.sh
```

**Step 2: Verify in a separate terminal**

```bash
# Check ecosystem hub
curl -s http://127.0.0.1:8777/api/status | python3 -m json.tool

# Check each repo's speedriftd mode
for repo in lodestar training-assistant news-briefing; do
  echo "=== $repo ==="
  driftdriver --dir /Users/braydon/projects/experiments/$repo --json speedriftd status 2>&1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'mode={d[\"mode\"]}')" 2>/dev/null
done
driftdriver --dir /Users/braydon/projects/personal/vibez-monitor --json speedriftd status 2>&1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'mode={d[\"mode\"]}')" 2>/dev/null

# Check dispatch loops are spawning
wg --dir /Users/braydon/projects/experiments/lodestar/.workgraph agents 2>&1 | grep alive
```

**Step 3: Verify notifications arrive**

- macOS notification: Should see "Online — 4 repos armed"
- Telegram: Should see startup message (if notify.toml configured)
- Ecosystem hub: http://127.0.0.1:8777/ should show dashboard

**Step 4: Test the kill switch**

```bash
# Stop one repo
/Users/braydon/projects/experiments/driftdriver/scripts/dark-factory-stop.sh lodestar

# Verify it's disarmed
driftdriver --dir /Users/braydon/projects/experiments/lodestar --json speedriftd status

# Re-arm it
driftdriver --dir /Users/braydon/projects/experiments/lodestar speedriftd status \
  --set-mode autonomous --lease-owner dark-factory --reason "re-arm after kill switch test"
```

**Step 5: Watch the first attractor cycle complete**

Wait 90 seconds. Check the attractor output in the terminal:
```
[attractor:lodestar] {"pass": 1, "findings": {...}, "status": "..."}
```

If findings are created as tasks and agents pick them up — the dark factory is live.
