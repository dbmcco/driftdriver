#!/usr/bin/env bash
# ABOUTME: Handler invoked when an agent claims a workgraph task
# ABOUTME: Creates checkpoint, runs pre-task drift check, primes agent with relevant knowledge

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

# Prime agent with relevant knowledge from distilled learnings
# This reads knowledge.jsonl (populated by distill_drift_knowledge) and
# logs the most relevant facts to the task so the agent can see them.
if command -v driftdriver >/dev/null 2>&1; then
  PRIMED=$(driftdriver --dir "$PROJECT_DIR" prime 2>/dev/null || echo "")
  if [[ -n "$PRIMED" ]]; then
    wg_log "$TASK_ID" "prior-knowledge: $PRIMED"
  fi
fi

# Also query Lessons MCP for broader project context (async, non-blocking)
TASK_DESC="${WG_TASK_DESCRIPTION:-}"
QUERY_JSON=$(jq -n --arg query "$TASK_DESC" --argjson limit 5 \
  '{query: $query, limit: $limit}')
lessons_mcp "search_knowledge" "$QUERY_JSON"

wg_log "$TASK_ID" "task-claimed: cli=$CLI_TOOL checkpoint=pre-task-$TASK_ID"
