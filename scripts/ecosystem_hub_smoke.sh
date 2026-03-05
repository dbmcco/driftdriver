#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Smoke test for ecosystem hub daemon + web report + upstream packet generation.

Usage:
  scripts/ecosystem_hub_smoke.sh [project-dir]

Default project-dir is current directory.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HUB="$ROOT/scripts/ecosystem_hub.sh"
PROJECT_DIR="${1:-$(pwd)}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
PORT="${ECOSYSTEM_HUB_PORT:-8877}"
CENTRAL_REPO="${ECOSYSTEM_HUB_CENTRAL_REPO:-$PROJECT_DIR/.workgraph/service/ecosystem-central}"
ACTIVE_PORT="$PORT"

cleanup() {
  "$HUB" --project-dir "$PROJECT_DIR" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[smoke] ensuring previous hub instance is stopped"
"$HUB" --project-dir "$PROJECT_DIR" stop >/dev/null 2>&1 || true

echo "[smoke] starting unattended automation on 127.0.0.1:${PORT}"
"$HUB" --project-dir "$PROJECT_DIR" --central-repo "$CENTRAL_REPO" automate --host 127.0.0.1 --port "$PORT" --interval-seconds 2 --skip-updates --max-next 3 >/dev/null

STATUS_JSON="$("$HUB" --project-dir "$PROJECT_DIR" status)"
ACTIVE_PORT="$(python3 -c 'import json,sys; 
try:
    data=json.loads(sys.stdin.read() or "{}")
except Exception:
    print("0")
    raise SystemExit(0)
print(int(data.get("port") or 0))' <<<"$STATUS_JSON")"
if [[ "$ACTIVE_PORT" -le 0 ]]; then
  ACTIVE_PORT="$PORT"
fi

echo "[smoke] polling status endpoint"
OK=0
for _ in $(seq 1 50); do
  if curl -fsS "http://127.0.0.1:${ACTIVE_PORT}/api/status" >/tmp/ecosystem-hub-status.json 2>/dev/null; then
    if python3 - <<'PY' 2>/dev/null
import json
import sys
with open("/tmp/ecosystem-hub-status.json", "r", encoding="utf-8") as f:
    data = json.load(f)
if data.get("generated_at"):
    sys.exit(0)
sys.exit(1)
PY
    then
      OK=1
      break
    fi
  fi
  sleep 0.2
done

if [[ "$OK" -ne 1 ]]; then
  echo "[smoke] FAIL: could not get a generated snapshot from /api/status"
  exit 1
fi

python3 - <<'PY'
import json, sys
path = "/tmp/ecosystem-hub-status.json"
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
if "repos" not in data or "next_work" not in data:
    print("[smoke] FAIL: status payload missing keys")
    sys.exit(1)
print(f"[smoke] status ok: repos={len(data.get('repos', []))} next_work={len(data.get('next_work', []))}")
PY

echo "[smoke] validating websocket stream"
ECOSYSTEM_HUB_PORT="$ACTIVE_PORT" python3 - <<'PY'
import base64
import json
import os
import socket
import struct
import sys

port = int(os.environ["ECOSYSTEM_HUB_PORT"])

def recv_exact(sock_obj: socket.socket, count: int) -> bytes:
    buf = bytearray()
    while len(buf) < count:
        chunk = sock_obj.recv(count - len(buf))
        if not chunk:
            raise RuntimeError("socket_closed")
        buf.extend(chunk)
    return bytes(buf)

key = base64.b64encode(os.urandom(16)).decode("ascii")
req = (
    "GET /ws/status HTTP/1.1\r\n"
    f"Host: 127.0.0.1:{port}\r\n"
    "Upgrade: websocket\r\n"
    "Connection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    "Sec-WebSocket-Version: 13\r\n\r\n"
)

with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock_obj:
    sock_obj.sendall(req.encode("utf-8"))
    header = b""
    while b"\r\n\r\n" not in header:
        part = sock_obj.recv(4096)
        if not part:
            raise RuntimeError("no_handshake_response")
        header += part
    if b"101 Switching Protocols" not in header:
        print("[smoke] FAIL: websocket handshake failed")
        sys.exit(1)

    frame = recv_exact(sock_obj, 2)
    size = frame[1] & 0x7F
    if size == 126:
        size = struct.unpack("!H", recv_exact(sock_obj, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", recv_exact(sock_obj, 8))[0]
    payload = recv_exact(sock_obj, size) if size else b""
    data = json.loads(payload.decode("utf-8"))
    if "schema" not in data or "repos" not in data:
        print("[smoke] FAIL: websocket payload missing expected keys")
        sys.exit(1)
print("[smoke] websocket stream ok")
PY

OUT="$PROJECT_DIR/.workgraph/service/ecosystem-hub/upstream-candidates.md"
"$HUB" --project-dir "$PROJECT_DIR" upstream-report --output "$OUT"
if [[ ! -s "$OUT" ]]; then
  echo "[smoke] FAIL: upstream report not written: $OUT"
  exit 1
fi

echo "[smoke] dry-run draft PR command generation"
"$HUB" --project-dir "$PROJECT_DIR" open-draft-pr >/tmp/ecosystem-hub-pr-open.json
python3 - <<'PY'
import json, sys
with open("/tmp/ecosystem-hub-pr-open.json", "r", encoding="utf-8") as f:
    data = json.load(f)
if "request_count" not in data:
    print("[smoke] FAIL: open-draft-pr output missing request_count")
    sys.exit(1)
print(f"[smoke] draft-pr request_count={data.get('request_count')}")
PY

echo "[smoke] upstream report written: $OUT"
if [[ ! -s "$CENTRAL_REPO/ecosystem-hub/register/$(basename "$PROJECT_DIR").json" ]]; then
  echo "[smoke] FAIL: central register not written at $CENTRAL_REPO"
  exit 1
fi
echo "[smoke] central register written: $CENTRAL_REPO/ecosystem-hub/register/$(basename "$PROJECT_DIR").json"
echo "[smoke] PASS"
