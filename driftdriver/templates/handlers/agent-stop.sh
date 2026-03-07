#!/usr/bin/env bash
# ABOUTME: Handler invoked when the agent signals a stop event
# ABOUTME: Evaluates task completeness and outputs CONTINUE, STOP, or ESCALATE

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

TASK_ID="$(current_task_id)"
DECISION="STOP"
REASON="no active task"

if [[ -n "$TASK_ID" ]]; then
  # Read task contract from workgraph
  CONTRACT=$(wg task show "$TASK_ID" --json 2>/dev/null || echo "{}")

  STATUS=$(echo "$CONTRACT" | jq -r '.status // "unknown"' 2>/dev/null || echo "unknown")
  CHECKLIST_DONE=$(echo "$CONTRACT" | jq -r '.checklist_complete // false' 2>/dev/null || echo "false")

  if [[ "$STATUS" == "in_progress" && "$CHECKLIST_DONE" != "true" ]]; then
    DECISION="CONTINUE"
    REASON="task in_progress and checklist incomplete"
  elif [[ "$STATUS" == "blocked" ]]; then
    DECISION="ESCALATE"
    REASON="task is blocked"
  else
    DECISION="STOP"
    REASON="task status=$STATUS"
  fi
fi

# Record stop decision immediately to lessons.db
if command -v driftdriver >/dev/null 2>&1; then
  driftdriver --dir "$PROJECT_DIR" record-event \
    --event-type "agent_stop" \
    --content "Agent stop: decision=$DECISION reason=$REASON task=$TASK_ID" \
    --session-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-}}" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || true
fi

wg_log "$TASK_ID" "agent-stop: decision=$DECISION reason=$REASON"
echo "$DECISION"
