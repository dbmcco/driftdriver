# ABOUTME: JSON API handlers for the ecosystem hub HTTP server.
# ABOUTME: Routes /api/status, /api/repos, /api/next-work, /api/security, /api/quality, etc.
from __future__ import annotations

import json
import socket
import struct
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from driftdriver.control_plane import (
    build_chain_payload,
    build_pressure_payload,
    repos_from_snapshot,
)

from .dashboard import render_dashboard_html
from .discovery import _read_json
from .websocket import (
    LiveStreamHub,
    _encode_ws_frame,
    _read_ws_frame,
    _ws_accept_key,
)


class _HubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    snapshot_path: Path
    state_path: Path
    live_hub: LiveStreamHub | None = None

    def _read_snapshot(self) -> dict[str, Any]:
        if not self.snapshot_path.exists():
            return {
                "schema": 1,
                "generated_at": "",
                "repos": [],
                "next_work": [],
                "updates": {"summary": "No snapshot yet"},
                "upstream_candidates": [],
                "central_reports": [],
                "repo_sources": {},
                "overview": {},
                "repo_dependency_overview": {"nodes": [], "edges": [], "summary": {}},
                "secdrift": {"summary": {}, "repos": []},
                "qadrift": {"summary": {}, "repos": []},
                "northstardrift": {
                    "summary": {
                        "overall_score": 0,
                        "overall_tier": "watch",
                        "overall_trend": "flat",
                        "overall_delta": 0,
                        "narrative": "",
                    },
                    "axes": {},
                    "repo_scores": [],
                    "counts": {},
                    "regressions": [],
                    "improvements": [],
                    "operator_prompts": [],
                    "recommended_reviews": [],
                    "targets": {"overall": {}, "axes": {}, "summary": {}, "priority_gaps": []},
                    "history": {
                        "points": [],
                        "daily_points": [],
                        "weekly_points": [],
                        "windows": {},
                        "summary": {"count": 0, "daily_count": 0, "weekly_count": 0, "window": "recent"},
                    },
                    "task_emit": {"enabled": False, "attempted": 0, "created": 0, "existing": 0, "skipped": 0, "errors": [], "tasks": []},
                },
                "supervisor": {},
                "narrative": "",
            }
        data = _read_json(self.snapshot_path)
        return data if data else {"repos": []}

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        blob = json.dumps(payload, indent=2, sort_keys=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _send_html(self, body: str, status: int = HTTPStatus.OK) -> None:
        blob = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _serve_websocket(self) -> None:
        hub = self.live_hub
        if hub is None:
            self._send_json({"error": "ws_not_configured"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        upgrade = str(self.headers.get("Upgrade") or "").lower()
        conn = str(self.headers.get("Connection") or "").lower()
        client_key = str(self.headers.get("Sec-WebSocket-Key") or "").strip()
        if upgrade != "websocket" or "upgrade" not in conn or not client_key:
            self._send_json({"error": "invalid_websocket_upgrade"}, status=HTTPStatus.BAD_REQUEST)
            return

        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _ws_accept_key(client_key))
        self.end_headers()

        client = self.connection
        client.settimeout(1.0)
        self.close_connection = False
        hub.register(client)

        initial = hub.latest_payload()
        if not initial:
            initial = json.dumps(self._read_snapshot(), sort_keys=False)
        if not hub.send_payload(client, initial):
            hub.unregister(client)
            return

        try:
            while not hub.stop_event.is_set():
                try:
                    opcode, payload = _read_ws_frame(client)
                except socket.timeout:
                    continue
                except (ConnectionError, OSError, struct.error, ValueError):
                    break

                if opcode == 0x8:  # close
                    break
                if opcode == 0x9:  # ping
                    try:
                        client.sendall(_encode_ws_frame(payload, opcode=0xA))
                    except OSError:
                        break
        finally:
            hub.unregister(client)

    def do_GET(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._send_html(render_dashboard_html())
            return
        if route in ("/ws", "/ws/status"):
            self._serve_websocket()
            return
        snapshot = self._read_snapshot()
        if route == "/api/status":
            self._send_json(snapshot)
            return
        if route == "/api/repos":
            self._send_json(snapshot.get("repos") or [])
            return
        if route == "/api/next-work":
            self._send_json(snapshot.get("next_work") or [])
            return
        if route == "/api/updates":
            self._send_json(snapshot.get("updates") or {})
            return
        if route == "/api/upstream":
            self._send_json(snapshot.get("upstream_candidates") or [])
            return
        if route == "/api/security":
            self._send_json(snapshot.get("secdrift") or {"summary": {}, "repos": []})
            return
        if route == "/api/quality":
            self._send_json(snapshot.get("qadrift") or {"summary": {}, "repos": []})
            return
        if route == "/api/effectiveness":
            self._send_json(
                snapshot.get("northstardrift")
                or {
                    "summary": {
                        "overall_score": 0,
                        "overall_tier": "watch",
                        "overall_trend": "flat",
                        "overall_delta": 0,
                        "narrative": "",
                    },
                    "axes": {},
                    "repo_scores": [],
                    "counts": {},
                    "regressions": [],
                    "improvements": [],
                    "operator_prompts": [],
                    "recommended_reviews": [],
                    "targets": {"overall": {}, "axes": {}, "summary": {}, "priority_gaps": []},
                    "history": {
                        "points": [],
                        "daily_points": [],
                        "weekly_points": [],
                        "windows": {},
                        "summary": {"count": 0, "daily_count": 0, "weekly_count": 0, "window": "recent"},
                    },
                    "task_emit": {"enabled": False, "attempted": 0, "created": 0, "existing": 0, "skipped": 0, "errors": [], "tasks": []},
                }
            )
            return
        if route == "/api/effectiveness-history":
            northstar = snapshot.get("northstardrift") if isinstance(snapshot.get("northstardrift"), dict) else {}
            self._send_json(
                northstar.get("history")
                if isinstance(northstar.get("history"), dict)
                else {
                    "points": [],
                    "daily_points": [],
                    "weekly_points": [],
                    "windows": {},
                    "summary": {"count": 0, "daily_count": 0, "weekly_count": 0, "window": "recent"},
                }
            )
            return
        if route == "/api/overview":
            self._send_json(
                {
                    "overview": snapshot.get("overview") or {},
                    "narrative": snapshot.get("narrative") or "",
                }
            )
            return
        if route == "/api/graph":
            repos = snapshot.get("repos") or []
            payload: list[dict[str, Any]] = []
            if isinstance(repos, list):
                for row in repos:
                    if not isinstance(row, dict):
                        continue
                    payload.append(
                        {
                            "repo": str(row.get("name") or ""),
                            "nodes": row.get("task_graph_nodes") or [],
                            "edges": row.get("task_graph_edges") or [],
                        }
                    )
            self._send_json(payload)
            return
        if route == "/api/repo-dependencies":
            self._send_json(snapshot.get("repo_dependency_overview") or {"nodes": [], "edges": [], "summary": {}})
            return
        if route == "/api/pressure":
            repo_objects = repos_from_snapshot(snapshot)
            self._send_json(build_pressure_payload(repo_objects))
            return
        if route.startswith("/api/pressure/chain/"):
            target_repo = route[len("/api/pressure/chain/"):].strip("/")
            if not target_repo:
                self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
                return
            repo_objects = repos_from_snapshot(snapshot)
            self._send_json(build_chain_payload(target_repo, repo_objects))
            return
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep daemon logs clean and structured in our own files.
        return


def _handler_factory(snapshot_path: Path, state_path: Path, live_hub: LiveStreamHub) -> type[_HubHandler]:
    class Handler(_HubHandler):
        pass

    Handler.snapshot_path = snapshot_path
    Handler.state_path = state_path
    Handler.live_hub = live_hub
    return Handler
