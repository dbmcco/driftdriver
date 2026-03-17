#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${ECOSYSTEM_HUB_PYTHON:-python3}"

# Ensure cargo/wg and homebrew are on PATH (launchd doesn't inherit shell profile)
export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

cd "$ROOT"

# Self-update: pull latest code before launching.
# Fast-forward only — never creates merge commits.
# Fails silently if offline or repo is dirty.
if git -C "$ROOT" diff --quiet HEAD 2>/dev/null; then
  git -C "$ROOT" pull --ff-only --quiet 2>/dev/null || true
fi

exec "$PYTHON_BIN" -m driftdriver.ecosystem_hub "$@"
