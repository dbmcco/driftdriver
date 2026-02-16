#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Package any app repo with the full Speedrift suite (local checkout bins).

Usage:
  scripts/package_app.sh --app /path/to/repo [options]

Options:
  --app <path>               Target app repository directory (required)
  --wrapper-mode <mode>      auto|pinned|portable (default: auto)
  --skip-contracts           Do not run ensure-contracts during install
  --seed-redrift-task        Create a starter redrift task in workgraph
  --task-id <id>             Task id for seeded redrift task
  --task-title <title>       Task title for seeded redrift task
  --help                     Show this help

Examples:
  scripts/package_app.sh --app ~/code/my-app
  scripts/package_app.sh --app ~/code/my-app --seed-redrift-task
  scripts/package_app.sh --app ~/code/my-app --wrapper-mode auto --skip-contracts
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIFTDRIVER_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXPERIMENTS_ROOT="$(cd "$DRIFTDRIVER_ROOT/.." && pwd)"

APP_DIR=""
WRAPPER_MODE="auto"
SKIP_CONTRACTS=0
SEED_REDRIFT_TASK=0
TASK_ID=""
TASK_TITLE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)
      APP_DIR="${2:-}"
      shift 2
      ;;
    --wrapper-mode)
      WRAPPER_MODE="${2:-}"
      shift 2
      ;;
    --skip-contracts)
      SKIP_CONTRACTS=1
      shift
      ;;
    --seed-redrift-task)
      SEED_REDRIFT_TASK=1
      shift
      ;;
    --task-id)
      TASK_ID="${2:-}"
      shift 2
      ;;
    --task-title)
      TASK_TITLE="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$APP_DIR" ]]; then
  echo "error: --app is required" >&2
  usage >&2
  exit 2
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "error: app directory not found: $APP_DIR" >&2
  exit 2
fi

APP_DIR="$(cd "$APP_DIR" && pwd)"
APP_NAME="$(basename "$APP_DIR")"
WG_DIR="$APP_DIR/.workgraph"

if [[ ! -x "$DRIFTDRIVER_ROOT/bin/driftdriver" ]]; then
  echo "error: driftdriver bin not found: $DRIFTDRIVER_ROOT/bin/driftdriver" >&2
  exit 2
fi

if ! command -v wg >/dev/null 2>&1; then
  echo "error: wg not found on PATH" >&2
  exit 2
fi

resolve_local_bin() {
  local repo="$1"
  local tool="$2"
  local p="$EXPERIMENTS_ROOT/$repo/bin/$tool"
  if [[ -x "$p" ]]; then
    printf '%s\n' "$p"
    return 0
  fi
  return 1
}

SPEEDRIFT_BIN="$(resolve_local_bin speedrift speedrift || true)"
SPECDRIFT_BIN="$(resolve_local_bin specdrift specdrift || true)"
DATADRIFT_BIN="$(resolve_local_bin datadrift datadrift || true)"
DEPSDRIFT_BIN="$(resolve_local_bin depsdrift depsdrift || true)"
UXDRIFT_BIN="$(resolve_local_bin uxdrift uxdrift || true)"
THERAPYDRIFT_BIN="$(resolve_local_bin therapydrift therapydrift || true)"
YAGNIDRIFT_BIN="$(resolve_local_bin yagnidrift yagnidrift || true)"
REDRIFT_BIN="$(resolve_local_bin redrift redrift || true)"

if [[ -z "$SPEEDRIFT_BIN" ]]; then
  echo "error: could not find speedrift bin at $EXPERIMENTS_ROOT/speedrift/bin/speedrift" >&2
  exit 2
fi

cmd=(
  "$DRIFTDRIVER_ROOT/bin/driftdriver"
  --dir "$APP_DIR"
  install
  --wrapper-mode "$WRAPPER_MODE"
  --speedrift-bin "$SPEEDRIFT_BIN"
)

[[ -n "$SPECDRIFT_BIN" ]] && cmd+=(--specdrift-bin "$SPECDRIFT_BIN")
[[ -n "$DATADRIFT_BIN" ]] && cmd+=(--datadrift-bin "$DATADRIFT_BIN")
[[ -n "$DEPSDRIFT_BIN" ]] && cmd+=(--depsdrift-bin "$DEPSDRIFT_BIN")
[[ -n "$UXDRIFT_BIN" ]] && cmd+=(--uxdrift-bin "$UXDRIFT_BIN")
[[ -n "$THERAPYDRIFT_BIN" ]] && cmd+=(--therapydrift-bin "$THERAPYDRIFT_BIN")
[[ -n "$YAGNIDRIFT_BIN" ]] && cmd+=(--yagnidrift-bin "$YAGNIDRIFT_BIN")
[[ -n "$REDRIFT_BIN" ]] && cmd+=(--redrift-bin "$REDRIFT_BIN")
[[ "$SKIP_CONTRACTS" -eq 1 ]] && cmd+=(--no-ensure-contracts)

echo "==> Packaging app: $APP_DIR"
"${cmd[@]}"

if [[ "$SEED_REDRIFT_TASK" -eq 1 ]]; then
  if [[ -z "$TASK_ID" ]]; then
    TASK_ID="redrift-${APP_NAME}-v2"
  fi
  if [[ -z "$TASK_TITLE" ]]; then
    TASK_TITLE="Redrift ${APP_NAME} to v2"
  fi

  if wg --dir "$WG_DIR" show "$TASK_ID" --json >/dev/null 2>&1; then
    echo "==> Seed task already exists: $TASK_ID"
  else
    DESC_FILE="$(mktemp)"
    trap 'rm -f "$DESC_FILE"' EXIT
    cat > "$DESC_FILE" <<'EOF'
```wg-contract
schema = 1
mode = "explore"
objective = "Analyze v1 and define controlled v2 rebuild lane"
non_goals = ["No fallback layering in core rebuild tasks"]
touch = ["src/**", "docs/**", "README.md", ".workgraph/**"]
acceptance = [
  "Document v1 inventory and constraints",
  "Define explicit v2 spec and architecture artifacts",
  "Create migration plan tasks in Workgraph",
]
max_files = 40
max_loc = 1200
auto_followups = true
```

```specdrift
schema = 1
spec = ["README.md", "docs/**"]
require_spec_update_when_code_changes = true
```

```therapydrift
schema = 1
min_signal_count = 2
followup_prefixes = ["drift-", "speedrift-pit-", "redrift-"]
require_recovery_plan = true
ignore_signal_prefixes = ["Therapydrift"]
cooldown_seconds = 1800
max_auto_actions_per_hour = 2
min_new_signals = 1
circuit_breaker_after = 3
```

```yagnidrift
schema = 1
max_new_files = 10
max_new_dirs = 3
enforce_no_speculative_abstractions = true
abstraction_keywords = ["factory", "adapter", "manager", "engine", "framework", "orchestrator"]
allow_paths = ["tests/**", "docs/**"]
```

```redrift
schema = 1
artifact_root = ".workgraph/.redrift"
required_artifacts = [
  "analyze/inventory.md",
  "analyze/constraints.md",
  "respec/v2-spec.md",
  "design/v2-architecture.md",
  "design/adr.md",
  "build/migration-plan.md",
]
create_phase_followups = true
```

Dogfood full-suite redrift lane.
EOF

    # Add data/dependency drift blocks only when common manifests are present.
    if [[ -f "$APP_DIR/src/db/schema.sql" || -d "$APP_DIR/src/db/migrations" || -d "$APP_DIR/db/migrations" ]]; then
      cat >> "$DESC_FILE" <<'EOF'

```datadrift
schema = 1
migrations = ["src/db/migrations/**", "db/migrations/**"]
schema_files = ["src/db/schema.sql", "db/schema.sql", "docs/**"]
require_schema_update_when_code_changes = true
```
EOF
    fi

    if [[ -f "$APP_DIR/package.json" || -f "$APP_DIR/pyproject.toml" || -f "$APP_DIR/go.mod" ]]; then
      cat >> "$DESC_FILE" <<'EOF'

```depsdrift
schema = 1
manifests = ["package.json", "pyproject.toml", "go.mod"]
locks = ["package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock", "go.sum"]
require_lock_update_when_manifest_changes = true
```
EOF
    fi

    wg --dir "$WG_DIR" add "$TASK_TITLE" --id "$TASK_ID" -d "$(cat "$DESC_FILE")" >/dev/null
    echo "==> Created redrift starter task: $TASK_ID"
  fi
fi

echo ""
echo "Next steps:"
echo "  cd \"$APP_DIR\""
echo "  ./.workgraph/drifts check --task <id> --write-log --create-followups"
if [[ "$SEED_REDRIFT_TASK" -eq 1 ]]; then
  echo "  ./.workgraph/drifts check --task \"$TASK_ID\" --write-log --create-followups"
fi
