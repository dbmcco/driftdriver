#!/usr/bin/env bash
# ABOUTME: Handler for pre-context-compaction events
# ABOUTME: Flushes pending learnings to Lessons MCP before context is lost

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

SESSION_ID="${CLAUDE_SESSION_ID:-${WG_SESSION_ID:-unknown}}"

# Flush learnings before context compaction
FLUSH_JSON=$(jq -n --arg sid "$SESSION_ID" '{session_id: $sid}')
lessons_mcp "flush_learnings" "$FLUSH_JSON"

TASK_ID="$(current_task_id)"
wg_log "$TASK_ID" "pre-compact flush: session=$SESSION_ID cli=$CLI_TOOL"
