#!/usr/bin/env bash
# ABOUTME: Daily ecosystem scanner that feeds signals into the intelligence sync
# ABOUTME: pipeline (Postgres). Falls back to legacy wg task creation if Postgres is down.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DRIVER_BIN="$ROOT/bin/driftdriver"
APP_DIR="${1:-$ROOT}"
APP_DIR="$(cd "$APP_DIR" && pwd)"

CONFIG="$APP_DIR/.workgraph/.driftdriver/ecosystem-review.json"
OUTPUT_DIR="$APP_DIR/.workgraph/.driftdriver/reviews"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DATE_HUMAN="$(date -u +%Y-%m-%d)"
JSON_OUT="$OUTPUT_DIR/review-$STAMP.json"

mkdir -p "$OUTPUT_DIR"

if [[ ! -x "$DRIVER_BIN" ]]; then
  echo "error: driftdriver not found at $DRIVER_BIN" >&2
  exit 2
fi

# ── 0. Run upstream workgraph monitor + eval chain ──
if [[ -x "$SCRIPT_DIR/monitor_upstream_wg.sh" ]]; then
  echo "Running upstream workgraph monitor..."
  "$SCRIPT_DIR/monitor_upstream_wg.sh" || true
fi
if [[ -x "$SCRIPT_DIR/eval_upstream_wg.sh" ]]; then
  echo "Running upstream workgraph eval chain..."
  "$SCRIPT_DIR/eval_upstream_wg.sh" || true
fi

# ── 1. Run the ecosystem scan ──
set +e
"$DRIVER_BIN" --dir "$APP_DIR" updates --json --config "$CONFIG" --force >"$JSON_OUT"
rc=$?
set -e

if [[ ! -f "$JSON_OUT" ]]; then
  echo "error: no output produced" >&2
  exit 1
fi

# ── 2. Parse findings with Python ──
EVAL_RESULT=$("$DRIVER_BIN" --dir "$APP_DIR" updates --json --config "$CONFIG" --force 2>/dev/null || true)

# Use the already-written JSON file instead
HAS_UPDATES=$(python3 -c "import json,sys; d=json.load(open('$JSON_OUT')); print('yes' if d.get('has_updates') or d.get('has_discoveries') else 'no')")
SUMMARY=$(python3 -c "
import json, sys
d = json.load(open('$JSON_OUT'))
parts = []
updates = d.get('updates', [])
user_findings = d.get('user_findings', [])
report_findings = d.get('report_findings', [])
errors = d.get('errors', [])

if updates:
    for u in updates:
        parts.append(f\"REPO UPDATE: {u['tool']} ({u['repo']}) — new commit {u['current_sha'][:8]}\")
if user_findings:
    for f in user_findings:
        kind = f.get('kind', '?')
        if kind == 'new_repo':
            parts.append(f\"NEW REPO: {f['user']}/{f['repo']} — {f.get('description', 'no description')}\")
        elif kind == 'repo_pushed':
            parts.append(f\"REPO ACTIVITY: {f['repo']} — pushed since last check\")
if report_findings:
    for f in report_findings:
        parts.append(f\"REPORT: {f.get('name', '?')} — keyword matches found\")
if errors:
    for e in errors:
        parts.append(f\"ERROR: {e}\")

if not parts:
    print('CLEAN: no updates, no new repos, no report matches')
else:
    print('\n'.join(parts))
")

echo "[$STAMP] $SUMMARY"

# ── 3. If no actionable findings, done ──
if [[ "$HAS_UPDATES" == "no" ]]; then
  echo "No actionable findings. Done."
  # Retention
  COUNT=$(find "$OUTPUT_DIR" -name 'review-*.json' -type f 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$COUNT" -gt 30 ]]; then
    PRUNE=$((COUNT - 30))
    find "$OUTPUT_DIR" -name 'review-*.json' -type f | sort | head -n "$PRUNE" | while read -r f; do
      rm -f "$f" "${f%.json}.md"
    done
  fi
  exit 0
fi

# ── 4. Feed signals into the intelligence sync pipeline ──
# Try the Postgres-backed intelligence pipeline first; fall back to legacy wg
# task creation if Postgres is unreachable.

SYNC_OK=0
set +e
SYNC_OUT=$(python3 -m driftdriver.intelligence.sync --json 2>&1)
SYNC_RC=$?
set -e

if [[ "$SYNC_RC" -eq 0 ]]; then
  SYNC_OK=1
  echo "Intelligence sync succeeded:"
  echo "$SYNC_OUT"
else
  echo "Intelligence sync failed (rc=$SYNC_RC), falling back to legacy wg tasks:" >&2
  echo "$SYNC_OUT" >&2

  # ── Legacy fallback: create workgraph tasks (no CLAUDE.md injection) ──
  if command -v wg >/dev/null 2>&1; then
    TASK_IDS=()

    REPO_UPDATES=$(python3 -c "
import json
d = json.load(open('$JSON_OUT'))
for u in d.get('updates', []):
    print(f\"{u['tool']}:{u['repo']}:{u['current_sha'][:8]}\")
")
    if [[ -n "$REPO_UPDATES" ]]; then
      TASK_ID="eval-ecosystem-$DATE_HUMAN-repo-updates"
      DESC="Evaluate ecosystem repo updates from $DATE_HUMAN scan (legacy fallback).

Updates detected:
$REPO_UPDATES

Review JSON: $JSON_OUT"
      wg add "$TASK_ID" --id "$TASK_ID" --immediate -d "$DESC" 2>/dev/null && TASK_IDS+=("$TASK_ID") || true
    fi

    NEW_REPOS=$(python3 -c "
import json
d = json.load(open('$JSON_OUT'))
for f in d.get('user_findings', []):
    if f.get('kind') == 'new_repo':
        print(f\"{f.get('user','?')}/{f.get('repo','?')}: {f.get('description','no description')}\")
")
    if [[ -n "$NEW_REPOS" ]]; then
      TASK_ID="eval-ecosystem-$DATE_HUMAN-new-repos"
      DESC="Evaluate new repos discovered on $DATE_HUMAN (legacy fallback).

New repos:
$NEW_REPOS

Review JSON: $JSON_OUT"
      wg add "$TASK_ID" --id "$TASK_ID" --immediate -d "$DESC" 2>/dev/null && TASK_IDS+=("$TASK_ID") || true
    fi

    REPO_ACTIVITY=$(python3 -c "
import json
d = json.load(open('$JSON_OUT'))
for f in d.get('user_findings', []):
    if f.get('kind') == 'repo_pushed':
        print(f\"{f.get('repo','?')}\")
")
    if [[ -n "$REPO_ACTIVITY" ]]; then
      TASK_ID="eval-ecosystem-$DATE_HUMAN-repo-activity"
      DESC="Evaluate repo activity detected on $DATE_HUMAN (legacy fallback).

Active repos:
$REPO_ACTIVITY

Review JSON: $JSON_OUT"
      wg add "$TASK_ID" --id "$TASK_ID" --immediate -d "$DESC" 2>/dev/null && TASK_IDS+=("$TASK_ID") || true
    fi

    echo "Legacy fallback created ${#TASK_IDS[@]} evaluation task(s)."
  else
    echo "warning: wg not on PATH, skipping legacy fallback" >&2
  fi
fi

# Retention
COUNT=$(find "$OUTPUT_DIR" -name 'review-*.json' -type f 2>/dev/null | wc -l | tr -d ' ')
if [[ "$COUNT" -gt 30 ]]; then
  PRUNE=$((COUNT - 30))
  find "$OUTPUT_DIR" -name 'review-*.json' -type f | sort | head -n "$PRUNE" | while read -r f; do
    rm -f "$f" "${f%.json}.md"
  done
fi

if [[ "$SYNC_OK" -eq 1 ]]; then
  echo "Done. Signals routed to intelligence pipeline."
else
  echo "Done. Used legacy fallback."
fi
