#!/usr/bin/env bash
# ABOUTME: WorkGraph Claude executor wrapper for current Claude CLI prompt contract
# ABOUTME: Converts piped prompt input into a positional prompt argument and forwards extra CLI args

set -euo pipefail

unset CLAUDECODE
unset CLAUDE_CODE_ENTRYPOINT

detect_manual_owner_assist() {
  local task_id="${WG_TASK_ID:-}"
  local project_dir="${PWD}"
  local policy_path="${project_dir}/.workgraph/drift-policy.toml"
  if [[ -z "$task_id" || ! -f "$policy_path" ]]; then
    return 1
  fi

  python3 - "$task_id" "$policy_path" <<'PY'
import json
import subprocess
import sys
import tomllib
from pathlib import Path

task_id = sys.argv[1]
policy_path = Path(sys.argv[2])

try:
    data = tomllib.loads(policy_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

mode = str(((data.get("speedriftd") or {}).get("manual_owner_policy") or "hold")).strip().lower()
if mode != "assist":
    raise SystemExit(1)

try:
    result = subprocess.run(
        ["wg", "show", task_id, "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    task = json.loads(result.stdout)
except Exception:
    raise SystemExit(1)

owner = str(task.get("agent") or "").strip()
if not owner:
    raise SystemExit(1)

owner_tag = f"agent:{owner}"
executors = ((data.get("routing") or {}).get("executors") or {})
for cfg in executors.values():
    pattern = str((cfg or {}).get("tag_match") or "")
    if pattern.endswith(":*") and owner_tag.startswith(pattern[:-1]):
        raise SystemExit(1)
    if pattern == owner_tag:
        raise SystemExit(1)

print(owner)
PY
}

manual_owner_release() {
  if [[ -z "${WG_MANUAL_OWNER_ASSIST:-}" || -z "${WG_TASK_ID:-}" ]]; then
    return
  fi
  local status
  status="$(wg show "$WG_TASK_ID" --json 2>/dev/null | grep -o '"status": *"[^"]*"' | head -1 | sed 's/.*"status": *"//;s/"//' || echo "unknown")"
  if [[ "$status" != "in-progress" ]]; then
    return
  fi
  if [[ -n "${MANUAL_OWNER_LOG_MESSAGE:-}" ]]; then
    wg log "$WG_TASK_ID" "$MANUAL_OWNER_LOG_MESSAGE" >/dev/null 2>&1 || true
  fi
  wg unclaim "$WG_TASK_ID" >/dev/null 2>&1 || true
}

# Resolve claude binary — the wg daemon may not inherit the user's full PATH.
CLAUDE_BIN="${CLAUDE_BIN:-}"
if [[ -z "$CLAUDE_BIN" ]]; then
  for candidate in "$HOME/.local/bin/claude" /usr/local/bin/claude; do
    if [[ -x "$candidate" ]]; then
      CLAUDE_BIN="$candidate"
      break
    fi
  done
fi
CLAUDE_BIN="${CLAUDE_BIN:-claude}"

if [[ -d "$PWD/.workgraph/bin" ]]; then
  export PATH="$PWD/.workgraph/bin:$PATH"
fi

PROMPT="$(cat)"
if [[ -z "${PROMPT//[[:space:]]/}" ]]; then
  echo "error: empty workgraph prompt" >&2
  exit 1
fi

# ── Agency enrichment (graceful fallback) ──
# If Agency is reachable, compose an agent identity for this task.
# If not, the original speedrift prompt is used unchanged.
EXECUTORS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVENTS_FILE="$PWD/.workgraph/service/runtime/events.jsonl"

_agency_emit() {
  local kind="$1" detail="$2"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"
  mkdir -p "$(dirname "$EVENTS_FILE")" 2>/dev/null || true
  echo "{\"kind\":\"$kind\",\"ts\":\"$ts\",\"task\":\"${WG_TASK_ID:-}\",\"detail\":\"$detail\"}" \
    >> "$EVENTS_FILE" 2>/dev/null || true
}

if [[ -n "${WG_TASK_ID:-}" && "${WG_SKIP_AGENCY:-}" != "1" ]]; then
  ASSIGN_SCRIPT="$EXECUTORS_DIR/agency-assign-workgraph"
  WRAP_SCRIPT="$EXECUTORS_DIR/agency-speedrift-wrap.py"

  if [[ -x "$ASSIGN_SCRIPT" && -f "$WRAP_SCRIPT" ]]; then
    COMPOSED=""
    COMPOSED=$("$ASSIGN_SCRIPT" "$WG_TASK_ID" "" 2>/dev/null) || true

    if [[ -n "$COMPOSED" ]]; then
      # Write original prompt to a temp file for the wrap script
      ORIG_PROMPT_TMP="$(mktemp)"
      trap 'rm -f "$ORIG_PROMPT_TMP"' EXIT
      printf '%s' "$PROMPT" > "$ORIG_PROMPT_TMP"

      ENRICHED=$(printf '%s' "$COMPOSED" | python3 "$WRAP_SCRIPT" "$ORIG_PROMPT_TMP" 2>/dev/null) || true
      rm -f "$ORIG_PROMPT_TMP"
      trap - EXIT

      if [[ -n "$ENRICHED" ]]; then
        PROMPT="$ENRICHED"
        _agency_emit "agency.enrichment.applied" "task=${WG_TASK_ID}"
      else
        _agency_emit "agency.enrichment.skipped" "reason=wrap_failed"
      fi
    else
      _agency_emit "agency.enrichment.skipped" "reason=agency_unavailable"
    fi
  fi
fi

MANUAL_OWNER_ID=""
if MANUAL_OWNER_ID="$(detect_manual_owner_assist 2>/dev/null)"; then
  export WG_MANUAL_OWNER_ASSIST=1
  export WG_MANUAL_OWNER_ID="$MANUAL_OWNER_ID"
  MANUAL_OWNER_LOG_MESSAGE="Advisory worker session finished; leaving this task open for ${MANUAL_OWNER_ID} review."
  trap manual_owner_release EXIT
  PROMPT=$'## Manual Owner Assist Mode\n- This task remains owned by '"${MANUAL_OWNER_ID}"$'; you may investigate and make progress, but do not close it.\n- If you need owner input or believe the work is ready for review, record that with `wg log '"${WG_TASK_ID:-task}"$' "..."`.\n- Before you stop, leave the task open with `wg unclaim '"${WG_TASK_ID:-task}"$'` unless the owner explicitly delegated terminal authority.\n- Do not run `wg done` or `wg fail` in this mode.\n\n'"$PROMPT"
fi

set +e
"$CLAUDE_BIN" \
  --print \
  --dangerously-skip-permissions \
  --no-session-persistence \
  "$@" \
  "$PROMPT"
EXIT_CODE=$?
set -e

exit $EXIT_CODE
