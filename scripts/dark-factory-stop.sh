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
