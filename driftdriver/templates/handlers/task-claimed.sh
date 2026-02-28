#!/usr/bin/env bash
# ABOUTME: Handler invoked when an agent claims a workgraph task
# ABOUTME: Creates agentjj checkpoint, runs pre-task drift check, queries relevant knowledge

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

TASK_ID="$(current_task_id)"

# Create agentjj checkpoint for potential rollback
agentjj checkpoint "pre-task-$TASK_ID" 2>/dev/null || true

# Run pre-task drift check
if [[ -n "$TASK_ID" ]]; then
  "$WG_DIR/drifts" check --task "$TASK_ID" --write-log 2>/dev/null || true
fi

# Query Lessons MCP for relevant knowledge about this task type
TASK_DESC="${WG_TASK_DESCRIPTION:-}"
QUERY_JSON="{\"query\":\"$TASK_DESC\",\"limit\":5}"
lessons_mcp "search_knowledge" "$QUERY_JSON"

wg_log "$TASK_ID" "task-claimed: cli=$CLI_TOOL checkpoint=pre-task-$TASK_ID"
