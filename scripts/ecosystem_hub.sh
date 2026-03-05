#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${ECOSYSTEM_HUB_PYTHON:-python3}"

cd "$ROOT"
exec "$PYTHON_BIN" -m driftdriver.ecosystem_hub "$@"
