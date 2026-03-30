#!/usr/bin/env bash
# ABOUTME: Installs driftdriver launchd agents for spend-watchdog and zombie-reaper.
# ABOUTME: Copies plists to ~/Library/LaunchAgents/, loads them via launchctl. Idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHD_DIR="${SCRIPT_DIR}/../launchd"
TARGET_DIR="$HOME/Library/LaunchAgents"

AGENTS=(
  "com.driftdriver.spend-watchdog"
  "com.driftdriver.zombie-reaper"
)

mkdir -p "$TARGET_DIR"

for agent in "${AGENTS[@]}"; do
  src="${LAUNCHD_DIR}/${agent}.plist"
  dst="${TARGET_DIR}/${agent}.plist"

  if [ ! -f "$src" ]; then
    echo "ERROR: Missing plist: $src" >&2
    exit 1
  fi

  # Unload existing agent if loaded (ignore errors if not loaded)
  launchctl bootout "gui/$(id -u)/${agent}" 2>/dev/null || true

  cp "$src" "$dst"
  echo "Installed ${dst}"

  launchctl bootstrap "gui/$(id -u)" "$dst"
  echo "Loaded ${agent}"
done

echo ""
echo "Verify with: launchctl list | grep driftdriver"
