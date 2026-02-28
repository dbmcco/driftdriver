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

AUTOPILOT_DIR=".workgraph/service"
AUTOPILOT_PID="$AUTOPILOT_DIR/speedrift-autopilot.pid"
AUTOPILOT_LOG="$AUTOPILOT_DIR/speedrift-autopilot.log"
mkdir -p "$AUTOPILOT_DIR" >/dev/null 2>&1 || true

is_pid_running() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

if [[ -f "$AUTOPILOT_PID" ]]; then
  EXISTING_PID="$(cat "$AUTOPILOT_PID" 2>/dev/null || true)"
  if is_pid_running "$EXISTING_PID"; then
    exit 0
  fi
fi

export PROJECT_DIR
nohup bash -lc '
  set -euo pipefail
  cd "$PROJECT_DIR" >/dev/null 2>&1 || exit 0
  while true; do
    if command -v wg >/dev/null 2>&1; then
      if ! wg --dir "$PROJECT_DIR/.workgraph" service status 2>/dev/null | grep -Eq "^Service:[[:space:]]+running"; then
        wg --dir "$PROJECT_DIR/.workgraph" service start --executor amplifier >/dev/null 2>&1 || \
          wg --dir "$PROJECT_DIR/.workgraph" service start >/dev/null 2>&1 || true
      fi
    fi
    if [[ -x "$PROJECT_DIR/.workgraph/drifts" ]]; then
      "$PROJECT_DIR/.workgraph/drifts" orchestrate --write-log --create-followups >/dev/null 2>&1 || true
    fi
    sleep 90
  done
' >>"$AUTOPILOT_LOG" 2>&1 &
echo "$!" > "$AUTOPILOT_PID" 2>/dev/null || true

exit 0
