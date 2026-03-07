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

# Record task completion event immediately to lessons.db (real-time learning)
if command -v driftdriver >/dev/null 2>&1; then
  driftdriver --dir "$PROJECT_DIR" record-event \
    --event-type "task_completed" \
    --content "Task $TASK_ID completed (files_changed=$CHANGED_FILES, cli=$CLI_TOOL)" \
    --session-id "${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-}}" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || true
fi

# Extract learnings from task execution
if command -v driftdriver >/dev/null 2>&1; then
  LEARNINGS=$(driftdriver --dir "$PROJECT_DIR" wire reflect 2>/dev/null || echo "")
  if [[ -n "$LEARNINGS" && "$LEARNINGS" != *"No learnings"* ]]; then
    wg_log "$TASK_ID" "self-reflect: $LEARNINGS"
  fi
fi

# Record drift outcomes for any findings from the pre-task check
if command -v driftdriver >/dev/null 2>&1 && [[ -n "$TASK_ID" ]]; then
  driftdriver --dir "$PROJECT_DIR" outcome \
    --task-id "$TASK_ID" \
    --lane "coredrift" \
    --finding-key "auto" \
    --recommendation "pre-task drift check" \
    --action-taken "task-completed" \
    --outcome "resolved" 2>/dev/null || true
fi

wg_log "$TASK_ID" "task-completing: cli=$CLI_TOOL files_changed=$CHANGED_FILES"
