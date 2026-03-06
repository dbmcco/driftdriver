#!/usr/bin/env bash
# ABOUTME: WorkGraph executor bridge to claude-session-driver
# ABOUTME: Launches a worker, sends task prompt, waits for completion

set -euo pipefail

TASK_ID="${WG_TASK_ID:?WG_TASK_ID must be set}"
PROJECT_DIR="${WG_PROJECT_DIR:?WG_PROJECT_DIR must be set}"
PROMPT="${WG_PROMPT:?WG_PROMPT must be set}"
TIMEOUT="${WG_TIMEOUT:-1800}"

# Discover session-driver scripts
CSD_SCRIPTS="${CLAUDE_SESSION_DRIVER_SCRIPTS:-}"
if [[ -z "$CSD_SCRIPTS" ]]; then
  CSD_SCRIPTS=$(find ~/.claude/plugins/cache -path "*/claude-session-driver/*/scripts" -type d 2>/dev/null | head -1)
fi

if [[ -z "$CSD_SCRIPTS" || ! -d "$CSD_SCRIPTS" ]]; then
  echo "error: claude-session-driver scripts not found" >&2
  exit 1
fi

WORKER_NAME="wg-task-${TASK_ID}"

# Launch worker
RESULT=$("$CSD_SCRIPTS/launch-worker.sh" "$WORKER_NAME" "$PROJECT_DIR" 2>&1)
SESSION_ID=$(echo "$RESULT" | jq -r '.session_id')

if [[ -z "$SESSION_ID" || "$SESSION_ID" == "null" ]]; then
  echo "error: failed to launch worker" >&2
  echo "$RESULT" >&2
  exit 1
fi

EVENT_FILE="/tmp/claude-workers/${SESSION_ID}.events.jsonl"
META_FILE="/tmp/claude-workers/${SESSION_ID}.meta"
LOG_FILE=""

if [[ -f "$META_FILE" ]]; then
  CWD=$(jq -r '.cwd // empty' "$META_FILE" 2>/dev/null || true)
  if [[ -n "$CWD" && -d "$CWD" ]]; then
    CWD=$(cd "$CWD" && pwd -P)
    ENCODED_PATH="${CWD//\//-}"
    LOG_FILE="$HOME/.claude/projects/${ENCODED_PATH}/${SESSION_ID}.jsonl"
  fi
fi

count_text_messages() {
  if [[ -z "$LOG_FILE" || ! -f "$LOG_FILE" ]]; then
    echo 0
    return
  fi
  jq -s '[.[] | select(.type == "assistant" and ((.message.content // []) | any(.type == "text")))] | length' "$LOG_FILE" 2>/dev/null || echo 0
}

last_text_response() {
  if [[ -z "$LOG_FILE" || ! -f "$LOG_FILE" ]]; then
    return
  fi
  jq -rs 'map(select(.type == "assistant" and ((.message.content // []) | any(.type == "text")))) | if length == 0 then "" else (last | [(.message.content // [])[] | select(.type == "text") | .text] | join("\n")) end' "$LOG_FILE" 2>/dev/null
}

BEFORE_COUNT=$(count_text_messages)
AFTER_LINE=0
if [[ -f "$EVENT_FILE" ]]; then
  AFTER_LINE=$(wc -l < "$EVENT_FILE" | tr -d ' ')
fi

if ! "$CSD_SCRIPTS/send-prompt.sh" "$WORKER_NAME" "$PROMPT" >/dev/null 2>&1; then
  echo "error: failed to send prompt to worker" >&2
  "$CSD_SCRIPTS/stop-worker.sh" "$WORKER_NAME" "$SESSION_ID" 2>/dev/null || true
  exit 1
fi

if ! "$CSD_SCRIPTS/wait-for-event.sh" "$SESSION_ID" stop "$TIMEOUT" --after-line "$AFTER_LINE" >/dev/null 2>&1; then
  echo "error: worker did not finish within ${TIMEOUT}s" >&2
  "$CSD_SCRIPTS/stop-worker.sh" "$WORKER_NAME" "$SESSION_ID" 2>/dev/null || true
  exit 1
fi

RESPONSE=""
for _ in $(seq 1 50); do
  AFTER_COUNT=$(count_text_messages)
  if [[ "$AFTER_COUNT" =~ ^[0-9]+$ && "$BEFORE_COUNT" =~ ^[0-9]+$ && "$AFTER_COUNT" -gt "$BEFORE_COUNT" ]]; then
    RESPONSE=$(last_text_response)
    if [[ -n "$RESPONSE" && "$RESPONSE" != "null" ]]; then
      break
    fi
  fi
  sleep 0.1
done

if [[ -z "$RESPONSE" || "$RESPONSE" == "null" ]]; then
  echo "error: timed out waiting for assistant response in session log" >&2
  "$CSD_SCRIPTS/stop-worker.sh" "$WORKER_NAME" "$SESSION_ID" 2>/dev/null || true
  exit 1
fi

echo "$RESPONSE"

# Cleanup
"$CSD_SCRIPTS/stop-worker.sh" "$WORKER_NAME" "$SESSION_ID" 2>/dev/null || true
