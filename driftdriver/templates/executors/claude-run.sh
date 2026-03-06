#!/usr/bin/env bash
# ABOUTME: WorkGraph Claude executor wrapper for current Claude CLI prompt contract
# ABOUTME: Converts piped prompt input into a positional prompt argument and forwards extra CLI args

set -euo pipefail

unset CLAUDECODE
unset CLAUDE_CODE_ENTRYPOINT

PROMPT="$(cat)"
if [[ -z "${PROMPT//[[:space:]]/}" ]]; then
  echo "error: empty workgraph prompt" >&2
  exit 1
fi

exec claude \
  --print \
  --dangerously-skip-permissions \
  --no-session-persistence \
  "$@" \
  "$PROMPT"
