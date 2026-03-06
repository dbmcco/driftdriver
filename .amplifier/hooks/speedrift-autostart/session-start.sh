#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${AMPLIFIER_PROJECT_DIR:-$(pwd)}"
PROJECT_KEY="$(printf '%s' "$PROJECT_DIR" | cksum | awk '{print $1}')"
SESSION_KEY="${AMPLIFIER_SESSION_ID:-unknown}"
STAMP_DIR="${TMPDIR:-/tmp}/speedrift-autostart"
STAMP_FILE="$STAMP_DIR/${PROJECT_KEY}-${SESSION_KEY}"
mkdir -p "$STAMP_DIR" 2>/dev/null || true
if [[ -f "$STAMP_FILE" ]]; then
  exit 0
fi
touch "$STAMP_FILE" 2>/dev/null || true
cd "$PROJECT_DIR" 2>/dev/null || exit 0

if [[ ! -d ".workgraph" ]]; then
  if command -v wg >/dev/null 2>&1; then
    wg init >/dev/null 2>&1 || true
  else
    exit 0
  fi
fi

if [[ ! -x ".workgraph/drifts" || ! -x ".workgraph/coredrift" || ! -x ".workgraph/executors/amplifier-run.sh" ]]; then
  if command -v driftdriver >/dev/null 2>&1; then
    driftdriver --dir "$PROJECT_DIR" install --wrapper-mode portable --with-fixdrift --with-amplifier-executor --no-ensure-contracts >/dev/null 2>&1 || \
      driftdriver --dir "$PROJECT_DIR" install --wrapper-mode portable --no-ensure-contracts >/dev/null 2>&1 || true
  fi
fi

if [[ -x ".workgraph/coredrift" ]]; then
  ./.workgraph/coredrift --dir "$PROJECT_DIR" ensure-contracts --apply >/dev/null 2>&1 || true
fi

if command -v driftdriver >/dev/null 2>&1; then
  SPEEDRIFT_STATUS="$(driftdriver --dir "$PROJECT_DIR" --json speedriftd status --refresh 2>/dev/null || echo '{}')"
  CONTROL_MODE="$(printf '%s\n' "$SPEEDRIFT_STATUS" | jq -r '.control.mode // "observe"' 2>/dev/null || echo 'observe')"
else
  CONTROL_MODE="observe"
fi

if [[ "$CONTROL_MODE" == "supervise" || "$CONTROL_MODE" == "autonomous" ]]; then
  if command -v wg >/dev/null 2>&1; then
    wg --dir "$PROJECT_DIR/.workgraph" service start --executor amplifier >/dev/null 2>&1 || \
      wg --dir "$PROJECT_DIR/.workgraph" service start >/dev/null 2>&1 || true
  fi
fi

exit 0
