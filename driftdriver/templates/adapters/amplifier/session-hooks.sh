#!/usr/bin/env bash
# ABOUTME: Amplifier session lifecycle adapter for driftdriver shared handlers.
# ABOUTME: Routes Amplifier session events through .workgraph/handlers/ scripts.
set -euo pipefail

PROJECT_DIR="${AMPLIFIER_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_DIR" 2>/dev/null || exit 0

EVENT="${1:-session-start}"

case "$EVENT" in
  session-start)
    if [[ -x ".workgraph/handlers/session-start.sh" ]]; then
      .workgraph/handlers/session-start.sh --cli amplifier
    fi
    ;;
  pre-compact)
    if [[ -x ".workgraph/handlers/pre-compact.sh" ]]; then
      .workgraph/handlers/pre-compact.sh --cli amplifier
    fi
    ;;
  progress-check)
    if [[ -x ".workgraph/handlers/progress-check.sh" ]]; then
      .workgraph/handlers/progress-check.sh --cli amplifier
    fi
    ;;
  agent-stop)
    if [[ -x ".workgraph/handlers/agent-stop.sh" ]]; then
      .workgraph/handlers/agent-stop.sh --cli amplifier
    fi
    ;;
  *)
    echo "Unknown event: $EVENT" >&2
    exit 1
    ;;
esac
