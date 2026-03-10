#!/usr/bin/env bash
# ABOUTME: macOS notification center wrapper for dark factory events.
# ABOUTME: Called by dispatch-loop and speedriftd for significant events.

set -euo pipefail

TITLE="${1:-Speedrift}"
MESSAGE="${2:-No message}"
SOUND="${3:-default}"

osascript -e "display notification \"$MESSAGE\" with title \"$TITLE\" sound name \"$SOUND\"" 2>/dev/null || true
