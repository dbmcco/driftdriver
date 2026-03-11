#!/usr/bin/env bash
# ABOUTME: Factory Brain launcher — replaces dark-factory.sh with brain-managed orchestration.
# ABOUTME: Starts ecosystem hub if needed, shows roster status, and prints monitoring commands.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DRIFTDRIVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { echo "[factory-brain] $(date +%H:%M:%S) $*"; }

# ── 1. Check if ecosystem hub is already running ──
if curl -sf http://localhost:8777/ >/dev/null 2>&1; then
  log "Ecosystem hub already running on port 8777"
else
  log "Starting ecosystem hub on port 8777..."
  (cd "$DRIFTDRIVER_DIR" && python -m driftdriver.ecosystem_hub.server run-service &)
  sleep 2

  if curl -sf http://localhost:8777/ >/dev/null 2>&1; then
    log "Ecosystem hub started successfully"
  else
    log "WARN: Ecosystem hub may not have started — check logs"
  fi
fi

# ── 2. Show current roster ──
log "Current brain roster:"
driftdriver brain-roster 2>/dev/null || echo "  (no repos enrolled yet — use: driftdriver brain-enroll <repo-path>)"

# ── 3. Print status info ──
echo ""
echo "================================================================"
echo "  Factory Brain — Online"
echo "================================================================"
echo ""
echo "  The factory brain manages:"
echo "    - Dispatch loop lifecycle (start/stop/restart)"
echo "    - Repo enrollment and discovery"
echo "    - Tiered healing (Haiku → Sonnet → Opus escalation)"
echo "    - Heartbeat monitoring and crash recovery"
echo ""
echo "  Monitor:"
echo "    driftdriver brain-status          # brain state overview"
echo "    driftdriver brain-roster          # enrolled repos"
echo "    curl -s localhost:8777/           # ecosystem hub dashboard"
echo "    tail -f <hub-data>/brain-logs/brain-log.md   # live brain log"
echo ""
echo "  Kill switch:"
echo "    driftdriver brain-unenroll <repo> # remove repo from brain"
echo "    pkill -f 'ecosystem_hub.server'   # stop ecosystem hub"
echo ""
echo "================================================================"
