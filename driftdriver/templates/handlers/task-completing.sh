#!/usr/bin/env bash
# ABOUTME: Handler invoked when an agent is about to complete a task
# ABOUTME: Runs post-drift check, compares against pre-check snapshot, records per-finding outcomes

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

TASK_ID="$(current_task_id)"

# Run post-task drift check in JSON mode and capture output for outcome comparison
POST_CHECK_JSON=""
if [[ -n "$TASK_ID" ]]; then
  POST_CHECK_JSON=$("$WG_DIR/drifts" check --task "$TASK_ID" --write-log --create-followups --json 2>/dev/null || echo "")
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

# Record drift outcomes by comparing pre-check snapshot against post-check findings.
# This replaces the naive blanket "resolved" with per-finding outcome classification.
if command -v driftdriver >/dev/null 2>&1 && [[ -n "$TASK_ID" ]] && [[ -n "$POST_CHECK_JSON" ]]; then
  OUTCOME_RESULT=$(printf '%s' "$POST_CHECK_JSON" | \
    driftdriver --dir "$PROJECT_DIR" outcome-from-check --task-id "$TASK_ID" 2>/dev/null || echo "")
  if [[ -n "$OUTCOME_RESULT" ]]; then
    RECORDED=$(printf '%s' "$OUTCOME_RESULT" | jq -r '.recorded // empty' 2>/dev/null) || RECORDED=0
    wg_log "$TASK_ID" "outcome-feedback: recorded=${RECORDED:-0} outcomes from check comparison"
  fi
fi

wg_log "$TASK_ID" "task-completing: cli=$CLI_TOOL files_changed=$CHANGED_FILES"
