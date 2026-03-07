#!/usr/bin/env bash
# ABOUTME: Handler for agent session start events
# ABOUTME: Ensures driftdriver wrappers, starts wg service, prints project context

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

# Ensure driftdriver install wrappers exist (idempotent)
driftdriver --dir "$PROJECT_DIR" install 2>/dev/null || true

# Provide repo-local shims needed by Workgraph-generated agent wrappers.
export PATH="$PROJECT_DIR/.workgraph/bin:$PATH"

# Refresh runtime control/status before deciding whether this session may start services.
SPEEDRIFT_STATUS="$(driftdriver --dir "$PROJECT_DIR" --json speedriftd status --refresh 2>/dev/null || echo '{}')"
CONTROL_MODE="$(printf '%s\n' "$SPEEDRIFT_STATUS" | jq -r '.control.mode // "observe"' 2>/dev/null || echo "observe")"

# Interactive sessions only auto-start services when repo control mode explicitly allows it.
if [[ "$CONTROL_MODE" == "supervise" || "$CONTROL_MODE" == "autonomous" ]]; then
  wg service start 2>/dev/null || true
fi

# Ensure ecosystem hub automation runs through the shared driftdriver CLI.
if command -v driftdriver >/dev/null 2>&1; then
  HUB_ARGS=(ecosystem-hub --project-dir "$PROJECT_DIR" automate --host "${ECOSYSTEM_HUB_HOST:-0.0.0.0}" --port "${ECOSYSTEM_HUB_PORT:-8777}")
  if [[ -n "${ECOSYSTEM_HUB_CENTRAL_REPO:-}" ]]; then
    HUB_ARGS+=(--central-repo "$ECOSYSTEM_HUB_CENTRAL_REPO")
  fi
  driftdriver "${HUB_ARGS[@]}" >/dev/null 2>&1 || true
fi

# Prime agent with project knowledge from lessons.db (real-time path)
if command -v driftdriver >/dev/null 2>&1; then
  CONTEXT=$(driftdriver --dir "$PROJECT_DIR" prime 2>/dev/null || echo "")
  if [[ -n "$CONTEXT" ]]; then
    echo "=== Project Knowledge Summary ==="
    echo "$CONTEXT"
    echo "================================="
  fi

  # Record session start event immediately
  driftdriver --dir "$PROJECT_DIR" record-event \
    --event-type "session_start" \
    --content "Session started for $(basename "$PROJECT_DIR") via $CLI_TOOL" \
    --session-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-}}" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || true
fi
