#!/usr/bin/env bash
# ABOUTME: Dark Factory launcher — starts autonomous improvement across all enrolled repos.
# ABOUTME: Manages dispatch loops, ecosystem hub, and continuous attractor convergence.

echo "WARNING: dark-factory.sh is deprecated. Use: scripts/factory-brain-start.sh"
echo "The factory brain manages dispatch loops, enrollment, and healing autonomously."
echo ""
echo "Continuing with legacy dark factory in 5 seconds... (Ctrl+C to abort)"
sleep 5

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
