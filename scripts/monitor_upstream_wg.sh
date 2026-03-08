#!/usr/bin/env bash
# ABOUTME: Daily monitor for Erik's upstream workgraph repo changes.
# ABOUTME: Fetches origin, reports new commits/branches, flags breaking changes.

set -euo pipefail

WG_DIR="/Users/braydon/projects/experiments/workgraph"
LOG_DIR="$WG_DIR/.workgraph/.upstream-monitor"
mkdir -p "$LOG_DIR"

TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/$TODAY.md"

cd "$WG_DIR"

# Store last known main SHA before fetch
BEFORE_SHA=$(git rev-parse origin/main 2>/dev/null || echo "unknown")

# Fetch upstream
git fetch origin --prune 2>/dev/null

AFTER_SHA=$(git rev-parse origin/main)

# Count new commits on main
if [ "$BEFORE_SHA" = "$AFTER_SHA" ]; then
    NEW_COMMITS=0
else
    NEW_COMMITS=$(git log "$BEFORE_SHA".."$AFTER_SHA" --oneline 2>/dev/null | wc -l | tr -d ' ')
fi

# Count how far our local main is behind
LOCAL_MAIN=$(git rev-parse main 2>/dev/null || echo "unknown")
BEHIND=$(git log main..origin/main --oneline 2>/dev/null | wc -l | tr -d ' ')

# Active branches (updated in last 7 days)
ACTIVE_BRANCHES=""
for branch in $(git branch -r | grep origin/ | grep -v HEAD | grep -v main); do
    last_epoch=$(git log -1 --format='%ct' "$branch" 2>/dev/null || echo 0)
    week_ago=$(date -v-7d +%s 2>/dev/null || date -d '7 days ago' +%s 2>/dev/null || echo 0)
    if [ "$last_epoch" -gt "$week_ago" ]; then
        last_date=$(git log -1 --format='%ar' "$branch" 2>/dev/null)
        count=$(git log origin/main.."$branch" --oneline 2>/dev/null | wc -l | tr -d ' ')
        ACTIVE_BRANCHES="$ACTIVE_BRANCHES\n- $branch ($count commits, $last_date)"
    fi
done

# Check for breaking changes in new commits (if any)
BREAKING=""
if [ "$NEW_COMMITS" -gt 0 ]; then
    # Check for renamed/removed commands we depend on
    for cmd in "add" "done" "show" "log" "service" "ready" "fail" "claim"; do
        deleted=$(git diff "$BEFORE_SHA".."$AFTER_SHA" -- "src/commands/${cmd}.rs" 2>/dev/null | grep '^-pub fn' | wc -l | tr -d ' ')
        if [ "$deleted" -gt 0 ]; then
            BREAKING="$BREAKING\n- ⚠ $cmd.rs: $deleted public functions removed/changed"
        fi
    done

    # Check for CLI flag changes
    cli_changes=$(git diff "$BEFORE_SHA".."$AFTER_SHA" -- src/cli.rs 2>/dev/null | grep -c '^[+-].*#\[arg' | tr -d ' ' || echo 0)
    if [ "$cli_changes" -gt 5 ]; then
        BREAKING="$BREAKING\n- ⚠ CLI flags: $cli_changes arg annotations changed"
    fi
fi

# Write report
{
    echo "# Upstream Workgraph Monitor — $TODAY"
    echo ""
    echo "## Summary"
    echo "- **New commits on main since last fetch:** $NEW_COMMITS"
    echo "- **Local main behind origin/main:** $BEHIND commits"
    echo "- **Origin main SHA:** $AFTER_SHA"
    echo ""

    if [ "$NEW_COMMITS" -gt 0 ]; then
        echo "## New Commits"
        echo '```'
        git log "$BEFORE_SHA".."$AFTER_SHA" --oneline
        echo '```'
        echo ""

        # Categorize
        echo "### By Type"
        feat_count=$(git log "$BEFORE_SHA".."$AFTER_SHA" --oneline | grep -c 'feat:' || true)
        fix_count=$(git log "$BEFORE_SHA".."$AFTER_SHA" --oneline | grep -c 'fix:' || true)
        docs_count=$(git log "$BEFORE_SHA".."$AFTER_SHA" --oneline | grep -c 'docs:' || true)
        echo "- Features: $feat_count"
        echo "- Fixes: $fix_count"
        echo "- Docs: $docs_count"
        echo ""
    fi

    if [ -n "$BREAKING" ]; then
        echo "## ⚠ Potential Breaking Changes"
        echo -e "$BREAKING"
        echo ""
    fi

    if [ -n "$ACTIVE_BRANCHES" ]; then
        echo "## Active Branches (last 7 days)"
        echo -e "$ACTIVE_BRANCHES"
        echo ""
    fi

    echo "## Speedrift Impact Checklist"
    echo "- [ ] Any changes to \`wg add\` / \`wg done\` / \`wg show\` / \`wg log\` interfaces?"
    echo "- [ ] New capabilities that overlap with driftdriver features?"
    echo "- [ ] New commands we should expose through driftdriver?"
    echo "- [ ] Breaking changes to graph.jsonl format?"
} > "$LOG_FILE"

# Output summary to stdout
echo "=== Upstream WG Monitor ($TODAY) ==="
echo "New commits: $NEW_COMMITS | Behind: $BEHIND | Active branches: $(echo -e "$ACTIVE_BRANCHES" | grep -c '^-' || echo 0)"
if [ -n "$BREAKING" ]; then
    echo -e "BREAKING:$BREAKING"
fi
echo "Full report: $LOG_FILE"
