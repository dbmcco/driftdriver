#!/usr/bin/env bash
# ABOUTME: Evaluation chain for upstream workgraph changes.
# ABOUTME: Runs after monitor, produces impact assessment, creates wg eval task.

set -euo pipefail

WG_DIR="/Users/braydon/projects/experiments/workgraph"
DD_DIR="/Users/braydon/projects/experiments/driftdriver"
LOG_DIR="$WG_DIR/.workgraph/.upstream-monitor"
TODAY=$(date +%Y-%m-%d)
EVAL_FILE="$LOG_DIR/$TODAY-eval.md"

cd "$WG_DIR"

LOCAL_MAIN_SHA=$(git rev-parse main 2>/dev/null || echo "unknown")
ORIGIN_MAIN_SHA=$(git rev-parse origin/main 2>/dev/null || echo "unknown")

if [ "$LOCAL_MAIN_SHA" = "$ORIGIN_MAIN_SHA" ]; then
    echo "Local main is up to date with origin. No eval needed."
    exit 0
fi

BEHIND=$(git log main..origin/main --oneline 2>/dev/null | wc -l | tr -d ' ')
if [ "$BEHIND" -eq 0 ]; then
    echo "No new commits to evaluate."
    exit 0
fi

# ── Commands driftdriver depends on ──
WATCHED_COMMANDS="add done show log service ready fail claim"

# ── 1. Categorize new commits ──
FEAT_LIST=$(git log main..origin/main --oneline | grep 'feat:' || true)
FIX_LIST=$(git log main..origin/main --oneline | grep 'fix:' || true)
DOCS_LIST=$(git log main..origin/main --oneline | grep 'docs:' || true)
REFACTOR_LIST=$(git log main..origin/main --oneline | grep 'refactor:' || true)
FEAT_COUNT=$(echo "$FEAT_LIST" | grep -c '.' || echo 0)
FIX_COUNT=$(echo "$FIX_LIST" | grep -c '.' || echo 0)

# ── 2. Check command interface stability ──
INTERFACE_CHANGES=""
for cmd in $WATCHED_COMMANDS; do
    diff_output=$(git diff main..origin/main -- "src/commands/${cmd}.rs" 2>/dev/null || true)
    if [ -n "$diff_output" ]; then
        added=$(echo "$diff_output" | grep -c '^+pub fn' 2>/dev/null || true)
        added=${added:-0}; added=$(echo "$added" | head -1 | tr -d ' ')
        removed=$(echo "$diff_output" | grep -c '^-pub fn' 2>/dev/null || true)
        removed=${removed:-0}; removed=$(echo "$removed" | head -1 | tr -d ' ')
        if [ "$added" -gt 0 ] 2>/dev/null || [ "$removed" -gt 0 ] 2>/dev/null; then
            INTERFACE_CHANGES="$INTERFACE_CHANGES\n- **$cmd**: +$added/-$removed public functions"
        fi
    fi
done

# ── 3. Check CLI flag changes ──
CLI_DIFF=$(git diff main..origin/main -- src/cli.rs 2>/dev/null || true)
NEW_SUBCOMMANDS=""
REMOVED_FLAGS=""
if [ -n "$CLI_DIFF" ]; then
    NEW_SUBCOMMANDS=$(echo "$CLI_DIFF" | grep '^+.*Subcommand\|^+.*Commands' | head -10 || true)
    REMOVED_FLAGS=$(echo "$CLI_DIFF" | grep '^-.*#\[arg' | head -10 || true)
    ADDED_FLAGS=$(echo "$CLI_DIFF" | grep '^+.*#\[arg' | head -10 || true)
fi

# ── 4. Check for graph.jsonl format changes ──
GRAPH_FORMAT_CHANGES=$(git diff main..origin/main -- src/graph.rs src/parser.rs 2>/dev/null | grep -c '^[+-].*pub ' || echo 0)

# ── 5. Detect new files (new modules/capabilities) ──
NEW_SRC_FILES=$(git diff --diff-filter=A --name-only main origin/main | grep -E '^src/.*\.rs$' | grep -v test || true)
NEW_SRC_COUNT=$(echo "$NEW_SRC_FILES" | grep -c '.' || echo 0)

# ── 6. Theme detection ──
THEMES=""

# Agency / agent management
agency_hits=$(git log main..origin/main --oneline | grep -ciE 'agency|agent|eval|flip|assigner' || echo 0)
[ "$agency_hits" -gt 3 ] && THEMES="$THEMES\n- **Agency/Agent Management** ($agency_hits commits): Agent evaluation, FLIP scoring, federation"

# Task lifecycle
lifecycle_hits=$(git log main..origin/main --oneline | grep -ciE 'draft|paused|verify|wait|resume|publish|abandon' || echo 0)
[ "$lifecycle_hits" -gt 2 ] && THEMES="$THEMES\n- **Task Lifecycle** ($lifecycle_hits commits): Draft/paused, verify gates, wait/resume"

# Notifications
notify_hits=$(git log main..origin/main --oneline | grep -ciE 'notif|slack|discord|telegram|email|webhook|sms' || echo 0)
[ "$notify_hits" -gt 2 ] && THEMES="$THEMES\n- **Notifications** ($notify_hits commits): Multi-channel notification system"

# TUI
tui_hits=$(git log main..origin/main --oneline | grep -ciE 'tui|viz|editor|scroll|fade|inspector' || echo 0)
[ "$tui_hits" -gt 3 ] && THEMES="$THEMES\n- **TUI** ($tui_hits commits): Visualization, editor, interaction"

# Safety
safety_hits=$(git log main..origin/main --oneline | grep -ciE 'safety|retract|self-heal|toctou|circuit.break|validation' || echo 0)
[ "$safety_hits" -gt 2 ] && THEMES="$THEMES\n- **Safety/Resilience** ($safety_hits commits): Retract, self-healing, validation"

# Provider/model routing
provider_hits=$(git log main..origin/main --oneline | grep -ciE 'provider|openrouter|model.*route|resolve_model' || echo 0)
[ "$provider_hits" -gt 2 ] && THEMES="$THEMES\n- **Model/Provider Routing** ($provider_hits commits): Multi-provider, per-role routing"

# ── 7. Overlap detection with driftdriver ──
OVERLAPS=""

# Notifications overlap
[ "$notify_hits" -gt 2 ] && OVERLAPS="$OVERLAPS\n- **Notifications**: wg now has NotificationChannel trait with 8+ backends. driftdriver has its own notification code. Potential consolidation."

# Self-healing overlap with therapydrift
if git log main..origin/main --oneline | grep -qi 'self-heal'; then
    OVERLAPS="$OVERLAPS\n- **Self-healing**: wg has self-healing pipeline for failed tasks. Overlaps with therapydrift's purpose."
fi

# Prompt evolution overlap
if git log main..origin/main --oneline | grep -qi 'evolve'; then
    OVERLAPS="$OVERLAPS\n- **Prompt evolution**: wg has wg evolve. driftdriver triggers it but should verify interface compatibility."
fi

# ── 8. Determine severity ──
SEVERITY="info"
if [ -n "$INTERFACE_CHANGES" ]; then
    SEVERITY="warning"
fi
if [ -n "$REMOVED_FLAGS" ]; then
    SEVERITY="error"
fi

# ── Write evaluation report ──
{
    echo "# Upstream WG Evaluation — $TODAY"
    echo ""
    echo "**Severity:** $SEVERITY"
    echo "**Behind:** $BEHIND commits"
    echo "**Features:** $FEAT_COUNT | **Fixes:** $FIX_COUNT | **New source files:** $NEW_SRC_COUNT"
    echo ""

    if [ -n "$THEMES" ]; then
        echo "## Themes Detected"
        echo -e "$THEMES"
        echo ""
    fi

    if [ -n "$INTERFACE_CHANGES" ]; then
        echo "## Interface Changes (Commands We Depend On)"
        echo -e "$INTERFACE_CHANGES"
        echo ""
    else
        echo "## Interface Changes"
        echo "None detected in watched commands ($WATCHED_COMMANDS)."
        echo ""
    fi

    if [ -n "$OVERLAPS" ]; then
        echo "## Feature Overlaps With Driftdriver"
        echo -e "$OVERLAPS"
        echo ""
    fi

    echo "## Recommendation"
    if [ "$SEVERITY" = "error" ]; then
        echo "**BREAKING CHANGES DETECTED.** Review interface changes before merging."
        echo "Do NOT pull until compatibility is verified."
    elif [ "$SEVERITY" = "warning" ]; then
        echo "**Interface changes detected.** Review command changes, verify driftdriver"
        echo "compatibility, then pull."
    else
        echo "**Safe to pull.** No breaking changes to commands driftdriver depends on."
        echo "Run \`cd /Users/braydon/projects/experiments/workgraph && git merge origin/main\`"
        echo "then verify: \`cd $DD_DIR && python3 -m pytest tests/ -x -q\`"
    fi

    echo ""
    echo "## Decision Required"
    echo "- [ ] Pull origin/main into local main"
    echo "- [ ] Run driftdriver test suite against new wg"
    echo "- [ ] Review overlapping features for consolidation opportunities"
    echo "- [ ] Update driftdriver if any interface changes affect us"
} > "$EVAL_FILE"

# ── Create wg evaluation task (if wg available and changes are significant) ──
if command -v wg >/dev/null 2>&1; then
    TASK_ID="eval-upstream-wg-$TODAY"

    # Check if task already exists
    if wg --dir "$DD_DIR/.workgraph" show "$TASK_ID" --json >/dev/null 2>&1; then
        echo "Task $TASK_ID already exists, skipping creation."
    else
        DESC="Evaluate $BEHIND new upstream workgraph commits ($TODAY).

\`\`\`wg-contract
schema = 1
mode = \"core\"
objective = \"Evaluate upstream workgraph changes for impact on Speedrift\"
non_goals = [\"No auto-merge\", \"No code changes without review\"]
touch = []
acceptance = [\"Impact assessed\", \"Decision recorded\"]
max_files = 0
max_loc = 0
auto_followups = false
\`\`\`

**Severity:** $SEVERITY
**Commits behind:** $BEHIND (features: $FEAT_COUNT, fixes: $FIX_COUNT, new files: $NEW_SRC_COUNT)

**Evaluation report:** $EVAL_FILE

## Action Items
1. Read evaluation report: \`cat $EVAL_FILE\`
2. Review interface changes to commands we depend on (add/done/show/log/service)
3. Decide: pull now, defer, or investigate further
4. If pulling: \`cd $WG_DIR && git merge origin/main\`
5. Verify: \`cd $DD_DIR && python3 -m pytest tests/ -x -q\`
6. Log decision: \`wg log $TASK_ID \"Decision: <pull|defer|skip> — <reasoning>\"\`"

        wg --dir "$DD_DIR/.workgraph" add "$TASK_ID" --id "$TASK_ID" --immediate \
            -d "$DESC" -t "upstream" -t "evaluation" 2>/dev/null \
            && echo "Created evaluation task: $TASK_ID" \
            || echo "warning: failed to create evaluation task"
    fi
fi

# ── Output summary ──
echo "=== Upstream WG Eval ($TODAY) ==="
echo "Severity: $SEVERITY | Behind: $BEHIND | Features: $FEAT_COUNT | Fixes: $FIX_COUNT"
if [ -n "$INTERFACE_CHANGES" ]; then
    echo -e "Interface changes:$INTERFACE_CHANGES"
fi
if [ -n "$OVERLAPS" ]; then
    echo -e "Overlaps:$OVERLAPS"
fi
echo "Report: $EVAL_FILE"
