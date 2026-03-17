#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Manage a codified Speedrift ecosystem hub daemon with optional launchd autostart.

Usage:
  scripts/ecosystem_hub_daemon.sh <command>

Commands:
  start             Start daemonized hub automation
  stop              Stop daemonized hub automation
  restart           Restart daemonized hub automation
  ensure-running    Ensure persistent daemon is installed and healthy
  status            Show hub status and derived URLs
  url               Print current dashboard/api/websocket URLs
  logs              Tail hub daemon log
  run-foreground    Run hub service in foreground (for launchd)
  install-launchd   Install + start launchd agent (persistent)
  uninstall-launchd Remove launchd agent
  launchd-status    Print launchd agent status
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HUB="$ROOT/scripts/ecosystem_hub.sh"

# Ensure PATH includes user tools (claude, cargo/wg, homebrew) — launchd doesn't inherit shell profile
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

PROJECT_DIR="${PROJECT_DIR:-$ROOT}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

HOST="${ECOSYSTEM_HUB_HOST:-0.0.0.0}"
PORT="${ECOSYSTEM_HUB_PORT:-8777}"
INTERVAL_SECONDS="${ECOSYSTEM_HUB_INTERVAL_SECONDS:-60}"
MAX_NEXT="${ECOSYSTEM_HUB_MAX_NEXT:-5}"
TITLE_PREFIX="${ECOSYSTEM_HUB_TITLE_PREFIX:-speedrift}"
CENTRAL_REPO="${ECOSYSTEM_HUB_CENTRAL_REPO:-}"
EXECUTE_DRAFT_PRS="${ECOSYSTEM_HUB_EXECUTE_DRAFT_PRS:-0}"
SKIP_UPDATES="${ECOSYSTEM_HUB_SKIP_UPDATES:-1}"
SUPERVISE_SERVICES="${ECOSYSTEM_HUB_SUPERVISE_SERVICES:-1}"
SUPERVISE_COOLDOWN_SECONDS="${ECOSYSTEM_HUB_SUPERVISE_COOLDOWN_SECONDS:-180}"
SUPERVISE_MAX_STARTS="${ECOSYSTEM_HUB_SUPERVISE_MAX_STARTS:-4}"
PYTHON_BIN="${ECOSYSTEM_HUB_PYTHON:-$(command -v python3)}"

SERVICE_DIR="$PROJECT_DIR/.workgraph/service/ecosystem-hub"
LOG_PATH="$SERVICE_DIR/hub.log"
LAUNCHD_LABEL="${ECOSYSTEM_HUB_LAUNCHD_LABEL:-com.speedrift.ecosystem-hub}"
LAUNCHD_PLIST="${HOME}/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"

tailscale_ipv4() {
  if command -v tailscale >/dev/null 2>&1; then
    tailscale ip -4 2>/dev/null | awk 'NR==1{print; exit}'
  fi
}

print_urls() {
  local ts_ip
  ts_ip="$(tailscale_ipv4 || true)"
  echo "dashboard_local=http://127.0.0.1:${PORT}/"
  echo "api_local=http://127.0.0.1:${PORT}/api/status"
  echo "ws_local=ws://127.0.0.1:${PORT}/ws/status"
  if [[ -n "$ts_ip" ]]; then
    echo "dashboard_tailscale=http://${ts_ip}:${PORT}/"
    echo "api_tailscale=http://${ts_ip}:${PORT}/api/status"
    echo "ws_tailscale=ws://${ts_ip}:${PORT}/ws/status"
  else
    echo "dashboard_tailscale=unavailable"
  fi
}

start_hub() {
  local args
  args=(
    --project-dir "$PROJECT_DIR"
    automate
    --host "$HOST"
    --port "$PORT"
    --interval-seconds "$INTERVAL_SECONDS"
    --max-next "$MAX_NEXT"
    --title-prefix "$TITLE_PREFIX"
    --supervise-cooldown-seconds "$SUPERVISE_COOLDOWN_SECONDS"
    --supervise-max-starts "$SUPERVISE_MAX_STARTS"
  )
  if [[ -n "$CENTRAL_REPO" ]]; then
    args=(--project-dir "$PROJECT_DIR" --central-repo "$CENTRAL_REPO" "${args[@]:2}")
  fi
  if [[ "$SKIP_UPDATES" == "1" ]]; then
    args+=("--skip-updates")
  fi
  if [[ "$EXECUTE_DRAFT_PRS" == "1" ]]; then
    args+=("--execute-draft-prs")
  fi
  if [[ "$SUPERVISE_SERVICES" == "0" ]]; then
    args+=("--no-supervise-services")
  fi
  "$HUB" "${args[@]}"
}

run_foreground() {
  local args
  args=(
    --project-dir "$PROJECT_DIR"
    run-service
    --host "$HOST"
    --port "$PORT"
    --interval-seconds "$INTERVAL_SECONDS"
    --max-next "$MAX_NEXT"
    --title-prefix "$TITLE_PREFIX"
    --supervise-cooldown-seconds "$SUPERVISE_COOLDOWN_SECONDS"
    --supervise-max-starts "$SUPERVISE_MAX_STARTS"
  )
  if [[ -n "$CENTRAL_REPO" ]]; then
    args=(--project-dir "$PROJECT_DIR" --central-repo "$CENTRAL_REPO" "${args[@]:2}")
  fi
  if [[ "$SKIP_UPDATES" == "1" ]]; then
    args+=("--skip-updates")
  fi
  if [[ "$EXECUTE_DRAFT_PRS" == "1" ]]; then
    args+=("--execute-draft-prs")
  fi
  if [[ "$SUPERVISE_SERVICES" == "0" ]]; then
    args+=("--no-supervise-services")
  fi
  exec "$HUB" "${args[@]}"
}

install_launchd() {
  mkdir -p "$(dirname "$LAUNCHD_PLIST")" "$SERVICE_DIR"
  "$HUB" --project-dir "$PROJECT_DIR" stop >/dev/null 2>&1 || true
  cat >"$LAUNCHD_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${ROOT}/scripts/ecosystem_hub_daemon.sh</string>
    <string>run-foreground</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${PROJECT_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PROJECT_DIR</key>
    <string>${PROJECT_DIR}</string>
    <key>ECOSYSTEM_HUB_HOST</key>
    <string>${HOST}</string>
    <key>ECOSYSTEM_HUB_PORT</key>
    <string>${PORT}</string>
    <key>ECOSYSTEM_HUB_INTERVAL_SECONDS</key>
    <string>${INTERVAL_SECONDS}</string>
    <key>ECOSYSTEM_HUB_MAX_NEXT</key>
    <string>${MAX_NEXT}</string>
    <key>ECOSYSTEM_HUB_TITLE_PREFIX</key>
    <string>${TITLE_PREFIX}</string>
    <key>ECOSYSTEM_HUB_EXECUTE_DRAFT_PRS</key>
    <string>${EXECUTE_DRAFT_PRS}</string>
    <key>ECOSYSTEM_HUB_SKIP_UPDATES</key>
    <string>${SKIP_UPDATES}</string>
    <key>ECOSYSTEM_HUB_SUPERVISE_SERVICES</key>
    <string>${SUPERVISE_SERVICES}</string>
    <key>ECOSYSTEM_HUB_SUPERVISE_COOLDOWN_SECONDS</key>
    <string>${SUPERVISE_COOLDOWN_SECONDS}</string>
    <key>ECOSYSTEM_HUB_SUPERVISE_MAX_STARTS</key>
    <string>${SUPERVISE_MAX_STARTS}</string>
    <key>ECOSYSTEM_HUB_CENTRAL_REPO</key>
    <string>${CENTRAL_REPO}</string>
    <key>ECOSYSTEM_HUB_PYTHON</key>
    <string>${PYTHON_BIN}</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${SERVICE_DIR}/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>${SERVICE_DIR}/launchd.err.log</string>
</dict>
</plist>
EOF

  launchctl bootout "gui/${UID}" "$LAUNCHD_PLIST" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/${UID}" "$LAUNCHD_PLIST"
  launchctl kickstart -k "gui/${UID}/${LAUNCHD_LABEL}"
  echo "launchd_installed=${LAUNCHD_PLIST}"
}

uninstall_launchd() {
  launchctl bootout "gui/${UID}" "$LAUNCHD_PLIST" >/dev/null 2>&1 || true
  rm -f "$LAUNCHD_PLIST"
  echo "launchd_removed=${LAUNCHD_PLIST}"
}

ensure_running() {
  local attempt running
  if command -v launchctl >/dev/null 2>&1; then
    mkdir -p "$(dirname "$LAUNCHD_PLIST")" "$SERVICE_DIR"
    if [[ ! -f "$LAUNCHD_PLIST" ]]; then
      install_launchd
    else
      launchctl print "gui/${UID}/${LAUNCHD_LABEL}" >/dev/null 2>&1 || \
        launchctl bootstrap "gui/${UID}" "$LAUNCHD_PLIST"
      launchctl kickstart -k "gui/${UID}/${LAUNCHD_LABEL}" >/dev/null 2>&1 || true
    fi
  else
    start_hub
  fi

  running=0
  for attempt in 1 2 3 4 5; do
    if "$HUB" --project-dir "$PROJECT_DIR" status | "$PYTHON_BIN" -c 'import json,sys; print("1" if json.load(sys.stdin).get("running") else "0")' | grep -q '^1$'; then
      running=1
      break
    fi
    sleep 1
  done
  echo "daemon_ensured=true"
  if [[ "$running" != "1" ]]; then
    echo "warning: hub daemon did not report running after ensure-running" >&2
  fi
}

cmd="${1:-}"
case "$cmd" in
  start)
    start_hub
    print_urls
    ;;
  stop)
    "$HUB" --project-dir "$PROJECT_DIR" stop
    ;;
  restart)
    "$HUB" --project-dir "$PROJECT_DIR" stop >/dev/null 2>&1 || true
    start_hub
    print_urls
    ;;
  ensure-running)
    ensure_running
    "$HUB" --project-dir "$PROJECT_DIR" status
    print_urls
    ;;
  status)
    "$HUB" --project-dir "$PROJECT_DIR" status
    print_urls
    ;;
  url)
    print_urls
    ;;
  logs)
    tail -n 200 -f "$LOG_PATH"
    ;;
  run-foreground)
    run_foreground
    ;;
  install-launchd)
    install_launchd
    print_urls
    ;;
  uninstall-launchd)
    uninstall_launchd
    ;;
  launchd-status)
    launchctl print "gui/${UID}/${LAUNCHD_LABEL}"
    ;;
  ""|--help|-h|help)
    usage
    ;;
  *)
    echo "unknown command: $cmd" >&2
    usage >&2
    exit 2
    ;;
esac
