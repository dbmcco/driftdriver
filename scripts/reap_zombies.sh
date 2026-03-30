#!/usr/bin/env bash
# ABOUTME: Thin wrapper to invoke the zombie reaper via driftdriver CLI.
# ABOUTME: Intended for cron/launchd execution every 15 minutes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIFTDRIVER_BIN="${SCRIPT_DIR}/../bin/driftdriver"

exec "$DRIFTDRIVER_BIN" reaper run "$@"
