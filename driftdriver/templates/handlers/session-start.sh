#!/usr/bin/env bash
# ABOUTME: Handler for agent session start events
# ABOUTME: Ensures driftdriver wrappers, starts wg service, prints project context

set -euo pipefail

HANDLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HANDLER_DIR/common.sh" "$@"

# Ensure driftdriver install wrappers exist (idempotent)
driftdriver install 2>/dev/null || true

# Start workgraph service if not already running
wg service start 2>/dev/null || true

# Query Lessons MCP for project context and print to stdout
CONTEXT_JSON=$(jq -n --arg path "$PROJECT_DIR" '{project_path: $path}')
CONTEXT=$(lessons_mcp "get_project_context" "$CONTEXT_JSON")

if [[ -n "$CONTEXT" ]]; then
  echo "=== Project Knowledge Summary ==="
  echo "$CONTEXT"
  echo "================================="
fi
