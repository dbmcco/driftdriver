#!/usr/bin/env bash
# ABOUTME: Daily ecosystem scanner that creates workgraph tasks and CLAUDE.md
# ABOUTME: notices when actionable updates or new repos are discovered.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DRIVER_BIN="$ROOT/bin/driftdriver"
APP_DIR="${1:-$ROOT}"
APP_DIR="$(cd "$APP_DIR" && pwd)"

CONFIG="$APP_DIR/.workgraph/.driftdriver/ecosystem-review.json"
OUTPUT_DIR="$APP_DIR/.workgraph/.driftdriver/reviews"
CLAUDE_MD="/Users/braydon/projects/.claude/CLAUDE.md"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DATE_HUMAN="$(date -u +%Y-%m-%d)"
JSON_OUT="$OUTPUT_DIR/review-$STAMP.json"

mkdir -p "$OUTPUT_DIR"

if [[ ! -x "$DRIVER_BIN" ]]; then
  echo "error: driftdriver not found at $DRIVER_BIN" >&2
  exit 2
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

# ── 4. Create workgraph evaluation tasks ──
if ! command -v wg >/dev/null 2>&1; then
  echo "warning: wg not on PATH, skipping task creation" >&2
else
  # Create one eval task per finding type
  TASK_IDS=()

  # Repo updates → eval task
  REPO_UPDATES=$(python3 -c "
import json
d = json.load(open('$JSON_OUT'))
for u in d.get('updates', []):
    print(f\"{u['tool']}:{u['repo']}:{u['current_sha'][:8]}\")
")
  if [[ -n "$REPO_UPDATES" ]]; then
    TASK_ID="eval-ecosystem-$DATE_HUMAN-repo-updates"
    DESC="Evaluate ecosystem repo updates from $DATE_HUMAN scan.

\`\`\`wg-contract
schema = 1
mode = \"core\"
objective = \"Evaluate and decide on ecosystem repo updates\"
non_goals = [\"No auto-merge\"]
touch = []
acceptance = []
max_files = 0
max_loc = 0
auto_followups = false
\`\`\`

Updates detected:
$REPO_UPDATES

Review JSON: $JSON_OUT

Decision needed: for each update, decide adopt/defer/skip and log reasoning."

    wg add "$TASK_ID" --id "$TASK_ID" -d "$DESC" 2>/dev/null && TASK_IDS+=("$TASK_ID") || true
  fi

  # New repos → eval task
  NEW_REPOS=$(python3 -c "
import json
d = json.load(open('$JSON_OUT'))
for f in d.get('user_findings', []):
    if f.get('kind') == 'new_repo':
        print(f\"{f.get('user','?')}/{f.get('repo','?')}: {f.get('description','no description')}\")
")
  if [[ -n "$NEW_REPOS" ]]; then
    TASK_ID="eval-ecosystem-$DATE_HUMAN-new-repos"
    DESC="Evaluate new repos discovered on $DATE_HUMAN.

\`\`\`wg-contract
schema = 1
mode = \"core\"
objective = \"Evaluate newly discovered repos for potential value\"
non_goals = [\"No auto-adoption\"]
touch = []
acceptance = []
max_files = 0
max_loc = 0
auto_followups = false
\`\`\`

New repos:
$NEW_REPOS

Review JSON: $JSON_OUT

Decision needed: for each repo, decide explore/watch/skip and log reasoning."

    wg add "$TASK_ID" --id "$TASK_ID" -d "$DESC" 2>/dev/null && TASK_IDS+=("$TASK_ID") || true
  fi

  # Repo activity → eval task
  REPO_ACTIVITY=$(python3 -c "
import json
d = json.load(open('$JSON_OUT'))
for f in d.get('user_findings', []):
    if f.get('kind') == 'repo_pushed':
        print(f\"{f.get('repo','?')}\")
")
  if [[ -n "$REPO_ACTIVITY" ]]; then
    TASK_ID="eval-ecosystem-$DATE_HUMAN-repo-activity"
    DESC="Evaluate repo activity detected on $DATE_HUMAN.

\`\`\`wg-contract
schema = 1
mode = \"core\"
objective = \"Review active repos for relevant changes\"
non_goals = [\"No deep audit\"]
touch = []
acceptance = []
max_files = 0
max_loc = 0
auto_followups = false
\`\`\`

Active repos:
$REPO_ACTIVITY

Review JSON: $JSON_OUT

Decision needed: check what changed, decide if relevant to our work."

    wg add "$TASK_ID" --id "$TASK_ID" -d "$DESC" 2>/dev/null && TASK_IDS+=("$TASK_ID") || true
  fi

  # ── 5. Append notice to CLAUDE.md ──
  if [[ ${#TASK_IDS[@]} -gt 0 ]]; then
    # Build the notice block
    NOTICE_BLOCK="
## Ecosystem Updates Pending Evaluation ($DATE_HUMAN)

The daily ecosystem scanner found actionable items. Evaluate these before other work:
"
    for tid in "${TASK_IDS[@]}"; do
      NOTICE_BLOCK+="- \`wg show $tid\` — review and decide (adopt/defer/skip), then \`wg done $tid\`
"
    done
    NOTICE_BLOCK+="
After evaluating all items, remove this section from CLAUDE.md.
<!-- ecosystem-eval-marker:$DATE_HUMAN -->"

    # Only append if this date's marker isn't already present
    if ! grep -q "ecosystem-eval-marker:$DATE_HUMAN" "$CLAUDE_MD" 2>/dev/null; then
      # Insert after the first line (# header) so it's prominent
      {
        head -1 "$CLAUDE_MD"
        echo "$NOTICE_BLOCK"
        tail -n +2 "$CLAUDE_MD"
      } > "$CLAUDE_MD.tmp"
      mv "$CLAUDE_MD.tmp" "$CLAUDE_MD"
      echo "Added evaluation notice to $CLAUDE_MD"
    fi
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

echo "Done. Created ${#TASK_IDS[@]} evaluation task(s)."
