#!/usr/bin/env bash
# ABOUTME: Main orchestration entry point for project autopilot
# ABOUTME: Wraps driftdriver autopilot CLI with SIGTERM handling and logging

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${1:-.}"
GOAL="${2:-}"
MAX_PARALLEL="${3:-4}"
DRY_RUN="${4:-}"

if [ -z "$GOAL" ]; then
    echo "Usage: $0 <project-dir> <goal> [max-parallel] [--dry-run]"
    echo ""
    echo "Examples:"
    echo "  $0 . 'Build authentication system' 4"
    echo "  $0 /path/to/project 'Add API pagination' 2 --dry-run"
    exit 1
fi

# Resolve project dir
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

# Verify workgraph exists
if [ ! -d "$PROJECT_DIR/.workgraph" ]; then
    echo "[autopilot] Error: no .workgraph in $PROJECT_DIR. Run 'wg init' first."
    exit 1
fi

# State tracking
AUTOPILOT_DIR="$PROJECT_DIR/.workgraph/.autopilot"
PID_FILE="$AUTOPILOT_DIR/autopilot.pid"
LOG_FILE="$AUTOPILOT_DIR/autopilot.log"

mkdir -p "$AUTOPILOT_DIR"

# Prevent duplicate runs
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[autopilot] Already running (PID $OLD_PID). Stop it first or remove $PID_FILE."
        exit 1
    fi
    rm -f "$PID_FILE"
fi

# Write our PID
echo $$ > "$PID_FILE"

# Graceful shutdown on SIGTERM/SIGINT
cleanup() {
    echo "[autopilot] Shutting down (signal received)..." | tee -a "$LOG_FILE"
    rm -f "$PID_FILE"
    exit 0
}
trap cleanup SIGTERM SIGINT

echo "[autopilot] Starting: goal='$GOAL' project=$PROJECT_DIR parallel=$MAX_PARALLEL" | tee -a "$LOG_FILE"
echo "[autopilot] PID: $$" | tee -a "$LOG_FILE"
echo "[autopilot] Log: $LOG_FILE" | tee -a "$LOG_FILE"

# Build CLI args
ARGS=(
    "autopilot"
    "--goal" "$GOAL"
    "--max-parallel" "$MAX_PARALLEL"
)

if [ "$DRY_RUN" = "--dry-run" ]; then
    ARGS+=("--dry-run")
fi

# Run driftdriver autopilot
set +e
driftdriver "${ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=$?
set -e

# Cleanup PID file
rm -f "$PID_FILE"

echo "[autopilot] Finished with exit code $EXIT_CODE" | tee -a "$LOG_FILE"
exit "$EXIT_CODE"
