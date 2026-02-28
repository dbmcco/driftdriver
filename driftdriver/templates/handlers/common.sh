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

# Write event to local JSONL file for later batch processing
lessons_mcp() {
  local tool_name="$1"
  shift
  local args="$1"
  local events_dir="${PROJECT_DIR}/.workgraph/.lessons-events"
  mkdir -p "$events_dir"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "{\"ts\":\"$ts\",\"tool\":\"$tool_name\",\"args\":$args}" >> "$events_dir/pending.jsonl"
}

# Log to workgraph
wg_log() {
  local task_id="${1:-}"
  local message="$2"
  if [[ -n "$task_id" ]]; then
    wg log "$task_id" "$message" 2>/dev/null || true
  fi
}

# Get current task ID from environment or workgraph
current_task_id() {
  echo "${WG_TASK_ID:-$(wg status --json 2>/dev/null | jq -r '.in_progress[0].id // empty' 2>/dev/null || echo "")}"
}
