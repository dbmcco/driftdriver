#!/usr/bin/env bash
# ABOUTME: Handler invoked when an agent is about to complete a task
# ABOUTME: Runs post-drift check, verifies basic changes, records completion to Lessons MCP

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

TASK_ID="$(current_task_id)"

# Run post-task drift check with follow-up creation
if [[ -n "$TASK_ID" ]]; then
  "$WG_DIR/drifts" check --task "$TASK_ID" --write-log --create-followups 2>/dev/null || true
fi

# Basic verification: did any files change?
GIT_DIFF_STAT=$(git -C "$PROJECT_DIR" diff --stat HEAD 2>/dev/null || echo "")
CHANGED_FILES=$(echo "$GIT_DIFF_STAT" | grep -c "changed\|insertion\|deletion" || echo "0")

# Record completion event to Lessons MCP
EVENT_JSON="{\"event\":\"task_completing\",\"task_id\":\"$TASK_ID\",\"cli\":\"$CLI_TOOL\",\"files_changed\":$CHANGED_FILES}"
lessons_mcp "record_event" "$EVENT_JSON"

wg_log "$TASK_ID" "task-completing: cli=$CLI_TOOL files_changed=$CHANGED_FILES"
