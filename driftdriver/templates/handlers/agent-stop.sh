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

# Log reasoning to Lessons MCP
EVENT_JSON="{\"event\":\"agent_stop\",\"task_id\":\"$TASK_ID\",\"decision\":\"$DECISION\",\"reason\":\"$REASON\",\"cli\":\"$CLI_TOOL\"}"
lessons_mcp "record_decision" "$EVENT_JSON"

wg_log "$TASK_ID" "agent-stop: decision=$DECISION reason=$REASON"
echo "$DECISION"
