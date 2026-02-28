#!/usr/bin/env bash
# ABOUTME: Shared utilities for driftdriver handler scripts
# ABOUTME: MCP invocation helpers, wg wrappers, CLI detection

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WG_DIR="$(dirname "$HANDLER_DIR")"
PROJECT_DIR="$(dirname "$WG_DIR")"

# Parse --cli flag
CLI_TOOL="unknown"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cli) CLI_TOOL="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# Call Lessons MCP tool via the MCP server
lessons_mcp() {
  local tool_name="$1"
  shift
  # Use node to call the MCP server directly via stdio
  echo "{\"method\":\"tools/call\",\"params\":{\"name\":\"$tool_name\",\"arguments\":$1}}" | \
    node "$PROJECT_DIR/node_modules/.bin/lessons-mcp" 2>/dev/null || true
}

# Log to workgraph
wg_log() {
  local task_id="${1:-}"
  local message="$2"
  if [[ -n "$task_id" ]]; then
    wg log "$task_id" --message "$message" 2>/dev/null || true
  fi
}

# Get current task ID from environment or workgraph
current_task_id() {
  echo "${WG_TASK_ID:-$(wg status --json 2>/dev/null | jq -r '.in_progress[0].id // empty' 2>/dev/null || echo "")}"
}
