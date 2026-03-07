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
CHANGED_FILES=$(printf '%s\n' "$GIT_DIFF_STAT" | grep -cE "changed|insertion|deletion") || CHANGED_FILES=0

# Record completion event to Lessons MCP
EVENT_JSON=$(jq -n --arg event "task_completing" --arg tid "$TASK_ID" --arg cli "$CLI_TOOL" \
  --argjson changed "${CHANGED_FILES:-0}" \
  '{event: $event, task_id: $tid, cli: $cli, files_changed: $changed}')
lessons_mcp "record_event" "$EVENT_JSON"

# Record task completion event immediately to lessons.db
if command -v driftdriver >/dev/null 2>&1; then
  driftdriver --dir "$PROJECT_DIR" record-event \
    --event-type "task_completed" \
    --content "Task $TASK_ID completed" \
    --session-id "${WG_SESSION_ID:-}" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || true
fi

# Extract learnings from task execution
if command -v driftdriver >/dev/null 2>&1; then
  LEARNINGS=$(driftdriver --dir "$PROJECT_DIR" wire reflect 2>/dev/null || echo "")
  if [[ -n "$LEARNINGS" && "$LEARNINGS" != *"No learnings"* ]]; then
    wg_log "$TASK_ID" "self-reflect: $LEARNINGS"
  fi
fi

wg_log "$TASK_ID" "task-completing: cli=$CLI_TOOL files_changed=$CHANGED_FILES"
