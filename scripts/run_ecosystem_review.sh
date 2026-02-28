#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run a scheduled ecosystem review and write JSON + Markdown artifacts.

Usage:
  scripts/run_ecosystem_review.sh [options]

Options:
  --app <path>          Target repo directory (default: current directory)
  --config <path>       Review config JSON (default: <app>/.workgraph/.driftdriver/ecosystem-review.json)
  --output-dir <path>   Directory for review artifacts (default: <app>/.workgraph/.driftdriver/reviews)
  --no-force            Respect policy interval instead of forcing a check
  --strict-exit         Preserve driftdriver exit code 3 when findings are detected
  --help                Show this help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DRIVER_BIN="$ROOT/bin/driftdriver"

APP_DIR="$(pwd)"
CONFIG_PATH=""
OUTPUT_DIR=""
FORCE=1
STRICT_EXIT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app)
      APP_DIR="${2:-}"
      shift 2
      ;;
    --config)
      CONFIG_PATH="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --no-force)
      FORCE=0
      shift
      ;;
    --strict-exit)
      STRICT_EXIT=1
      shift
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

if [[ ! -x "$DRIVER_BIN" ]]; then
  echo "error: driftdriver executable not found: $DRIVER_BIN" >&2
  exit 2
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "error: app directory not found: $APP_DIR" >&2
  exit 2
fi
APP_DIR="$(cd "$APP_DIR" && pwd)"

if [[ -z "$CONFIG_PATH" ]]; then
  CONFIG_PATH="$APP_DIR/.workgraph/.driftdriver/ecosystem-review.json"
fi
if [[ -z "$OUTPUT_DIR" ]]; then
  OUTPUT_DIR="$APP_DIR/.workgraph/.driftdriver/reviews"
fi
mkdir -p "$OUTPUT_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
JSON_OUT="$OUTPUT_DIR/review-$STAMP.json"
MD_OUT="$OUTPUT_DIR/review-$STAMP.md"

cmd=(
  "$DRIVER_BIN"
  --dir "$APP_DIR"
  updates
  --json
  --config "$CONFIG_PATH"
  --write-review "$MD_OUT"
)
if [[ "$FORCE" -eq 1 ]]; then
  cmd+=(--force)
fi

set +e
"${cmd[@]}" >"$JSON_OUT"
rc=$?
set -e

echo "Review JSON: $JSON_OUT"
echo "Review Markdown: $MD_OUT"

if [[ "$rc" -eq 3 && "$STRICT_EXIT" -eq 0 ]]; then
  exit 0
fi
exit "$rc"
