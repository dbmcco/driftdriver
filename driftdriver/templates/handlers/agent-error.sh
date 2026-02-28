#!/usr/bin/env bash
# ABOUTME: Handler invoked when the agent encounters an error
# ABOUTME: Records error to Lessons MCP, checks for agentjj rollback point, outputs recovery suggestion

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

TASK_ID="$(current_task_id)"
ERROR_MSG="${WG_ERROR_MESSAGE:-unknown error}"

# Record error event to Lessons MCP
EVENT_JSON="{\"event\":\"agent_error\",\"task_id\":\"$TASK_ID\",\"error\":\"$ERROR_MSG\",\"cli\":\"$CLI_TOOL\"}"
lessons_mcp "record_event" "$EVENT_JSON"

# Check if agentjj checkpoint exists for this task
CHECKPOINT="pre-task-$TASK_ID"
HAS_CHECKPOINT=$(agentjj list-checkpoints 2>/dev/null | grep -c "$CHECKPOINT" || echo "0")

if [[ "$HAS_CHECKPOINT" -gt 0 ]]; then
  echo "RECOVERY: agentjj rollback to checkpoint '$CHECKPOINT' available"
  echo "  Run: agentjj restore $CHECKPOINT"
else
  echo "RECOVERY: no checkpoint found — review git status and retry from last known good state"
fi

wg_log "$TASK_ID" "agent-error: error=$ERROR_MSG checkpoint_available=$HAS_CHECKPOINT"
