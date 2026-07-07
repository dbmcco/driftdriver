#!/usr/bin/env bash
# refresh-workgraph-repos.sh
#
# Refresh the .workgraph scaffolding + config in every repo to the current
# pi-default setup. Idempotent and reusable: safe to re-run after Erik ships
# a new wg version, after restoring repos from backup, or on a fresh repo.
#
# What it does, per repo:
#   1. Backs up graph.jsonl + config.toml (timestamped, in .workgraph/backups/)
#   2. Runs `driftdriver install` with the full lane set (lands pi-run.sh/pi.toml,
#      refreshes handlers/hooks/drift-policy). Idempotent — does not touch
#      graph.jsonl or the task graph.
#   3. Flips the dispatch model to pi:zai/glm-5.2 via `wg config --model`.
#   4. Applies safe config cleanups with sed/python (strip deprecated `executor`
#      key, rename [coordinator]->[dispatcher], fix `zai:` -> `pi:zai/` prefix).
#   5. Verifies with `wg config --merged` — flags any repo still showing
#      deprecation warnings, invalid models, or unknown providers.
#
# Repos with warnings after the script are printed at the end for a
# verification subagent to handle (novel stale-model patterns the safe
# transforms don't cover).
#
# Usage:
#   refresh-workgraph-repos.sh                  # auto-discover all .workgraph repos
#   refresh-workgraph-repos.sh repo1 repo2 ...  # specific repos (by name or path)
#   refresh-workgraph-repos.sh --dry-run        # show what would happen, no changes
#   refresh-workgraph-repos.sh --lanes-only     # skip config flip, just refresh scaffolding
#
# Exit codes: 0 all clean | 1 some repos flagged | 2 script error

set -euo pipefail

PI_MODEL="pi:zai/glm-5.2"
PROJECTS_ROOT="${PROJECTS_ROOT:-/Users/braydon/projects}"
LANE_FLAGS="--with-uxdrift --with-therapydrift --with-fixdrift --with-yagnidrift --with-redrift"
DRY_RUN=0
LANES_ONLY=0
REPOS=()

# --- arg parsing ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --lanes-only) LANES_ONLY=1; shift ;;
    --help|-h)
      sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) REPOS+=("$1"); shift ;;
  esac
done

# --- discover repos if none given ---
if [[ ${#REPOS[@]} -eq 0 ]]; then
  while IFS= read -r r; do
    REPOS+=("$r")
  done < <(find "$PROJECTS_ROOT" -maxdepth 3 -type d -name .workgraph 2>/dev/null \
           | sed "s#^${PROJECTS_ROOT}/##; s#/.workgraph##" \
           | grep -vE 'workgraph-fork-archive|workgraph-pr-staging' \
           | sort)
fi

echo "=== refresh-workgraph-repos: ${#REPOS[@]} repo(s) ==="
[[ $DRY_RUN -eq 1 ]] && echo "(dry-run — no changes will be made)"
echo

# --- helpers ---
backup_file() {
  local repo_dir="$1" fname="$2"
  local src="$repo_dir/.workgraph/$fname"
  [[ -f "$src" ]] || return 0
  if [[ $DRY_RUN -eq 0 ]]; then
    mkdir -p "$repo_dir/.workgraph/backups"
    cp "$src" "$repo_dir/.workgraph/backups/${fname}.$(date -u +%Y%m%dT%H%M%SZ)"
  fi
}

# Apply the safe config cleanups. Reads config.toml, writes back cleaned.
# These are all patterns verified safe across 25 repos last session.
clean_config() {
  local cfg="$1"
  [[ -f "$cfg" ]] || return 0
  python3 - "$cfg" <<'PYEOF'
import sys, re
path = sys.argv[1]
with open(path, 'r') as f:
    txt = f.read()
orig = txt
# 1. Strip deprecated `executor = "..."` lines anywhere (handler now derived
#    from model spec provider prefix).
txt = re.sub(r'^(\s*)executor\s*=\s*"[^"]*"\s*$\n', '', txt, flags=re.M)
# 2. Rename [coordinator] table header -> [dispatcher] (deprecated table name)
txt = re.sub(r'^\[coordinator\]', '[dispatcher]', txt, flags=re.M)
# 3. Fix old `zai:` prefix -> `pi:zai/` (old config format the new wg rejects)
txt = re.sub(r'=\s*"zai:([^"]+)"', r'="pi:zai/\1"', txt)
# 4. Collapse 3+ blank lines -> 2
txt = re.sub(r'\n{3,}', '\n\n', txt)
if txt != orig:
    with open(path, 'w') as f:
        f.write(txt)
PYEOF
}

# Check if config is already pi + clean (skip the heavy transforms if so)
config_already_pi() {
  local cfg="$1"
  [[ -f "$cfg" ]] || return 1
  grep -qE '^\s*model\s*=\s*"pi:' "$cfg" && ! grep -qiE '^\s*executor\s*=|^\[coordinator\]|=\s*"zai:' "$cfg"
}

# --- main loop ---
FLAGGED=()
OK=0
SKIP=0
FAIL=0

for r in "${REPOS[@]}"; do
  # resolve to absolute path (accepts name like "experiments/foo" or full path)
  if [[ "$r" == /* ]]; then
    repo_dir="$r"
  else
    repo_dir="$PROJECTS_ROOT/$r"
  fi
  wg_dir="$repo_dir/.workgraph"

  if [[ ! -d "$wg_dir" ]]; then
    echo "✗ $r — no .workgraph dir (skip)"
    SKIP=$((SKIP+1)); continue
  fi

  echo "--- $r ---"

  # 1. backups (graph + config — the things we must not lose)
  backup_file "$repo_dir" "graph.jsonl"
  backup_file "$repo_dir" "config.toml"

  # 2. driftdriver install (refresh scaffolding, land pi-run.sh/pi.toml)
  if [[ $DRY_RUN -eq 0 ]]; then
    if ! (cd "$repo_dir" && driftdriver install $LANE_FLAGS >/dev/null 2>&1); then
      echo "  ✗ driftdriver install FAILED — flagging"
      FLAGGED+=("$r (install failed)")
      FAIL=$((FAIL+1)); continue
    fi
  else
    echo "  [dry-run] would run: driftdriver install $LANE_FLAGS"
  fi

  # 3. config flip + cleanups (skip if --lanes-only or already clean)
  if [[ $LANES_ONLY -eq 0 ]]; then
    cfg="$wg_dir/config.toml"
    if [[ $DRY_RUN -eq 0 ]]; then
      # flip model to pi (creates config.toml if missing)
      (cd "$repo_dir" && wg config --model "$PI_MODEL" --no-reload >/dev/null 2>&1 || true)
      # apply safe cleanups
      clean_config "$cfg"
    else
      echo "  [dry-run] would run: wg config --model $PI_MODEL --no-reload"
      echo "  [dry-run] would apply safe cleanups (strip executor, rename coordinator, fix zai:)"
    fi
  fi

  # 4. verify
  if [[ $DRY_RUN -eq 0 ]]; then
    warn_out=$(cd "$repo_dir" && wg config --merged 2>&1)
    if echo "$warn_out" | grep -qiE 'deprecated|warning:|invalid|unknown provider|error'; then
      echo "  ✗ CONFIG WARNINGS — flagging for verification"
      echo "$warn_out" | grep -iE 'deprecated|warning:|invalid|unknown provider|error' | head -3 | sed 's/^/    /'
      FLAGGED+=("$r")
      FAIL=$((FAIL+1))
    else
      model=$(echo "$warn_out" | grep -E '^\s*model =' | head -1 | xargs)
      echo "  ✓ $model — clean"
      OK=$((OK+1))
    fi
  fi
done

# --- summary ---
echo
echo "=== SUMMARY: $OK ok, $SKIP skipped, $FAIL flagged ==="
if [[ ${#FLAGGED[@]} -gt 0 ]]; then
  echo "--- flagged for verification subagent ---"
  for f in "${FLAGGED[@]}"; do echo "  • $f"; done
  exit 1
fi
echo "✓ all repos clean"
exit 0
