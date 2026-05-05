# ABOUTME: JSON API handlers for the ecosystem hub HTTP server.
# ABOUTME: Routes /api/status, /api/repos, /api/next-work, /api/security, /api/quality, etc.
from __future__ import annotations

import json
import logging
import os
import socket
import struct
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from uuid import UUID

from driftdriver.control_plane import (
    build_chain_payload,
    build_pressure_payload,
    repos_from_snapshot,
)

from .activity_cache import read_activity_digest
from .agent_history import build_agent_history as _build_agent_history
from .operator_home import build_operator_home
from .dashboard import render_dashboard_html
from .discovery import _read_json
from .model_registry_dashboard import render_model_registry_dashboard_html
from .model_registry_status import build_model_registry_status
from .services import detect_services as _detect_services, _validate_plist_path
from .session_launcher import (
    FreshellUnavailableError as _FreshellUnavailableError,
    launch_session as _launch_session,
)
from .intelligence_api import (
    approve_signal,
    batch_approve_signals,
    build_briefing,
    build_briefing_history,
    build_decision_log,
    build_decision_trends,
    build_inbox,
    build_tracking,
    override_signal,
    snooze_signal,
    trigger_sync,
)
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
    activity_path: Path | None = None
    live_hub: LiveStreamHub | None = None
    workspace_root: Path | None = None

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

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _load_chat_roster(self) -> dict[str, Any] | None:
        """Load factory brain roster for chat context."""
        roster_path = Path.home() / ".config" / "workgraph" / "factory-brain" / "roster.json"
        return _read_json(roster_path) if roster_path.exists() else None

    def _load_chat_decisions(self, snapshot: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Load pending decisions across all repos for chat context."""
        from driftdriver.decision_queue import read_pending_decisions, _record_to_dict
        from driftdriver.paia_agent_health.queue import load_pending_agent_health_decisions

        repos = snapshot.get("repos") or []
        all_decisions: list[dict[str, Any]] = []
        for repo_row in repos:
            if not isinstance(repo_row, dict):
                continue
            repo_path = str(repo_row.get("path") or "")
            if not repo_path or not Path(repo_path).is_dir():
                continue
            for dec in read_pending_decisions(Path(repo_path)):
                all_decisions.append(_record_to_dict(dec))
        for dec in load_pending_agent_health_decisions(workspace_root=self.workspace_root):
            all_decisions.append(_record_to_dict(dec))
        return all_decisions or None

    def _load_notification_ledger(self) -> list[dict[str, Any]]:
        """Load sent-decision provenance from the factory notification ledger."""
        ledger_path = Path.home() / ".config" / "workgraph" / "factory-brain" / "notification-ledger.jsonl"
        if not ledger_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                rows.append(entry)
        return rows

    def _find_repo_path(self, repo_name: str) -> str | None:
        """Resolve a repo name to its filesystem path via snapshot."""
        snapshot = self._read_snapshot()
        for r in snapshot.get("repos") or []:
            if isinstance(r, dict) and str(r.get("name") or "") == repo_name:
                return str(r.get("path") or "")
        return None

    def _pg_config(self) -> "PostgresConfig":
        from driftdriver.intelligence.db import PostgresConfig as _PgConfig
        return _PgConfig()

    def _agent_chat_session_dir(self) -> Path:
        return self.snapshot_path.parent / "agent_chat"

    def _handle_get_sessions(self) -> None:
        import urllib.request as urlreq
        freshell_local = "http://localhost:3550"
        freshell_base = os.environ.get("FRESHELL_BASE_URL", freshell_local)
        token = os.environ.get("FRESHELL_AUTH_TOKEN", "")

        def _freshell_get(path: str) -> dict:
            req = urlreq.Request(f"{freshell_local}{path}")
            if token:
                req.add_header("X-Auth-Token", token)
            with urlreq.urlopen(req, timeout=2) as resp:
                return json.loads(resp.read())

        try:
            tabs = (_freshell_get("/api/tabs").get("data") or {}).get("tabs") or []
        except Exception:
            tabs = []

        # Build a map of pane_id → tab for joining
        pane_to_tab: dict = {}
        for tab in tabs:
            active_pane = tab.get("activePaneId")
            if active_pane:
                pane_to_tab[active_pane] = tab

        try:
            panes = (_freshell_get("/api/panes").get("data") or {}).get("panes") or []
        except Exception:
            panes = []

        # Only include real terminal panes (not picker/browser)
        sessions = []
        for p in panes:
            kind = p.get("kind", "")
            if kind != "terminal":
                continue
            pane_id = p.get("id", "")
            tab = pane_to_tab.get(pane_id, {})
            tab_id = tab.get("id", "")
            title = tab.get("title") or pane_id
            token_qs = f"?token={token}" if token else ""
            tab_qs = f"&tab={tab_id}" if tab_id else ""
            sessions.append({
                "session_id": pane_id,
                "tab_id": tab_id,
                "repo": title,
                "url": f"{freshell_base}/{token_qs}{tab_qs}",
                "started_at": p.get("createdAt") or p.get("started_at"),
            })

        self._send_json({"sessions": sessions, "freshell_url": freshell_base})

    def _handle_post_session_select(self) -> None:
        """Tell Freshell to focus a specific tab (broadcasts via WebSocket to connected clients)."""
        import urllib.request as urlreq
        body = self._read_body()
        try:
            tab_id = json.loads(body).get("tab_id", "")
        except Exception:
            tab_id = ""
        if not tab_id:
            self._send_json({"error": "tab_id required"}, status=HTTPStatus.BAD_REQUEST)
            return
        freshell_local = "http://localhost:3550"
        token = os.environ.get("FRESHELL_AUTH_TOKEN", "")
        try:
            req = urlreq.Request(
                f"{freshell_local}/api/tabs/{tab_id}/select",
                data=b"{}",
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("X-Auth-Token", token)
            with urlreq.urlopen(req, timeout=2) as resp:
                result = json.loads(resp.read())
            self._send_json({"ok": True, "tab_id": tab_id, "response": result})
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)})

    def _handle_get_agent_chat_history(self) -> None:
        from driftdriver.ecosystem_hub.chat_history import ChatSessionManager
        mgr = ChatSessionManager(self._agent_chat_session_dir())
        self._send_json({"history": mgr.active().load(limit=100)})

    def _handle_clear_agent_chat_history(self) -> None:
        from driftdriver.ecosystem_hub.chat_history import ChatSessionManager
        mgr = ChatSessionManager(self._agent_chat_session_dir())
        mgr.active().clear()
        self._send_json({"ok": True})

    def _handle_get_chat_sessions(self) -> None:
        from driftdriver.ecosystem_hub.chat_history import ChatSessionManager
        mgr = ChatSessionManager(self._agent_chat_session_dir())
        self._send_json({"sessions": mgr.list_sessions()})

    def _handle_post_new_chat_session(self) -> None:
        from driftdriver.ecosystem_hub.chat_history import ChatSessionManager
        mgr = ChatSessionManager(self._agent_chat_session_dir())
        mgr.new_session()
        self._send_json({"ok": True})

    def _handle_get_chat_session(self, session_id: str) -> None:
        from driftdriver.ecosystem_hub.chat_history import ChatSessionManager
        mgr = ChatSessionManager(self._agent_chat_session_dir())
        h = mgr.get_session(session_id)
        if h is None:
            self._send_json({"error": "session_not_found", "id": session_id}, status=HTTPStatus.NOT_FOUND)
            return
        self._send_json({"history": h.load(limit=100)})

    def _handle_post_agent_chat(self) -> None:
        body = self._read_body()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
            return

        message = str(data.get("message", "")).strip()
        if not message:
            self._send_json({"error": "empty message"}, status=HTTPStatus.BAD_REQUEST)
            return

        from driftdriver.ecosystem_hub.chat_agent import EcosystemAgent
        from driftdriver.ecosystem_hub.chat_history import ChatSessionManager

        mgr = ChatSessionManager(self._agent_chat_session_dir())
        history = mgr.active()
        agent = EcosystemAgent(
            snapshot_path=self.snapshot_path,
            history_path=None,
            session_dir=self._agent_chat_session_dir(),
        )
        history_messages = history.to_anthropic_messages(limit=20)

        # SSE streaming response.
        # Force HTTP/1.0: browsers buffer HTTP/1.1 responses without Content-Length
        # or Transfer-Encoding: chunked.  HTTP/1.0 reads until connection close so
        # each flushed write is delivered to the client immediately.
        self.protocol_version = "HTTP/1.0"
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        full_response = ""
        try:
            for chunk in agent.stream_response(message, history_messages):
                event_data = json.dumps({"type": "text", "text": chunk})
                self.wfile.write(f"data: {event_data}\n\n".encode())
                self.wfile.flush()
                full_response += chunk
        except Exception as exc:
            logging.getLogger(__name__).debug("agent chat stream failed", exc_info=True)
            err_data = json.dumps({"type": "error", "text": str(exc)[:300]})
            try:
                self.wfile.write(f"data: {err_data}\n\n".encode())
                self.wfile.flush()
            except Exception:
                pass
        finally:
            if full_response:
                history.append(message, full_response)
            try:
                done_data = json.dumps({"type": "done"})
                self.wfile.write(f"data: {done_data}\n\n".encode())
                self.wfile.flush()
            except Exception:
                pass

    def do_POST(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]

        # --- Intelligence inbox actions ---
        if route == "/intelligence/inbox/batch-approve":
            try:
                result = batch_approve_signals(self._pg_config())
                self._send_json(result)
            except Exception as exc:
                logging.getLogger(__name__).debug("intelligence batch-approve failed", exc_info=True)
                self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if route.startswith("/intelligence/inbox/") and route.endswith("/approve"):
            signal_id_str = route[len("/intelligence/inbox/"):-len("/approve")]
            try:
                sid = UUID(signal_id_str)
            except ValueError:
                self._send_json({"error": "invalid_signal_id"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                result = approve_signal(self._pg_config(), signal_id=sid)
                status_code = HTTPStatus.OK if "error" not in result else HTTPStatus.NOT_FOUND
                self._send_json(result, status=status_code)
            except Exception as exc:
                logging.getLogger(__name__).debug("intelligence approve failed", exc_info=True)
                self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if route.startswith("/intelligence/inbox/") and route.endswith("/override"):
            signal_id_str = route[len("/intelligence/inbox/"):-len("/override")]
            try:
                sid = UUID(signal_id_str)
            except ValueError:
                self._send_json({"error": "invalid_signal_id"}, status=HTTPStatus.BAD_REQUEST)
                return
            body = self._read_body()
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                return
            new_decision = data.get("decision", "")
            reason = data.get("reason", "")
            if not new_decision or not reason:
                self._send_json(
                    {"error": "missing_fields", "required": ["decision", "reason"]},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                result = override_signal(self._pg_config(), signal_id=sid, new_decision=new_decision, reason=reason)
                status_code = HTTPStatus.OK if "error" not in result else HTTPStatus.NOT_FOUND
                self._send_json(result, status=status_code)
            except Exception as exc:
                logging.getLogger(__name__).debug("intelligence override failed", exc_info=True)
                self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if route.startswith("/intelligence/inbox/") and route.endswith("/snooze"):
            signal_id_str = route[len("/intelligence/inbox/"):-len("/snooze")]
            try:
                sid = UUID(signal_id_str)
            except ValueError:
                self._send_json({"error": "invalid_signal_id"}, status=HTTPStatus.BAD_REQUEST)
                return
            try:
                result = snooze_signal(self._pg_config(), signal_id=sid)
                status_code = HTTPStatus.OK if "error" not in result else HTTPStatus.NOT_FOUND
                self._send_json(result, status=status_code)
            except Exception as exc:
                logging.getLogger(__name__).debug("intelligence snooze failed", exc_info=True)
                self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if route == "/intelligence/sync":
            try:
                pg = self._pg_config()
                result = trigger_sync(pg)
                self._send_json(result)
            except Exception as exc:
                logging.getLogger(__name__).debug("intelligence sync failed", exc_info=True)
                self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if route == "/api/decisions/answer":
            body = self._read_body()
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                return
            decision_id = data.get("decision_id")
            answer = data.get("answer")
            answered_via = data.get("answered_via", "api")
            if not decision_id or not answer:
                self._send_json(
                    {"error": "missing_fields", "required": ["decision_id", "answer"]},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            # Search all enrolled repos for this decision
            from driftdriver.cli.decisions_cmd import handle_decisions_answer
            from driftdriver.paia_agent_health.queue import answer_agent_health_decision
            snapshot = self._read_snapshot()
            for r in snapshot.get("repos") or []:
                if not isinstance(r, dict):
                    continue
                repo_path = str(r.get("path") or "")
                if not repo_path or not Path(repo_path).is_dir():
                    continue
                project_dir = Path(repo_path)
                result = handle_decisions_answer(
                    project_dir,
                    decision_id=decision_id,
                    answer=answer,
                    answered_via=answered_via,
                )
                if "error" not in result:
                    self._send_json(result)
                    return
            agent_health = answer_agent_health_decision(
                decision_id=decision_id,
                answer=answer,
                answered_via=answered_via,
                workspace_root=self.workspace_root,
            )
            if agent_health is not None:
                self._send_json(
                    {
                        "decision_id": agent_health.id,
                        "repo": agent_health.repo,
                        "answer": agent_health.answer,
                        "answered_via": agent_health.answered_via,
                        "answered_at": agent_health.answered_at,
                    }
                )
                return
            self._send_json(
                {"error": "decision_not_found", "decision_id": decision_id},
                status=HTTPStatus.NOT_FOUND,
            )
            return

        if route == "/api/chat":
            body = self._read_body()
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                return
            message = data.get("message")
            chat_id = data.get("chat_id")
            user_name = data.get("user_name", "")
            if not message or not chat_id:
                self._send_json(
                    {"error": "missing_fields", "required": ["message", "chat_id"]},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            chat_id = str(chat_id)
            # Auth check — reject unknown chat_ids before any LLM call
            from driftdriver.factory_brain.chat import (
                handle_chat_message,
                load_authorized_chat_ids,
            )

            authorized = load_authorized_chat_ids()
            if authorized and chat_id not in authorized:
                self._send_json(
                    {"error": "unauthorized", "chat_id": chat_id},
                    status=HTTPStatus.FORBIDDEN,
                )
                return
            # Gather context from hub state
            snapshot = self._read_snapshot()
            roster = self._load_chat_roster()
            pending_decisions = self._load_chat_decisions(snapshot)
            try:
                result = handle_chat_message(
                    message=message,
                    chat_id=chat_id,
                    user_name=user_name,
                    snapshot=snapshot,
                    roster=roster,
                    pending_decisions=pending_decisions,
                )
                self._send_json(result)
            except Exception as exc:
                self._send_json(
                    {"error": "internal_error", "detail": str(exc)[:200]},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if route.startswith("/api/repo/") and route.endswith("/start"):
            repo_name = route[len("/api/repo/"):-len("/start")]
            if not repo_name:
                self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
                return
            snapshot = self._read_snapshot()
            repos = snapshot.get("repos") or []
            repo = None
            for r in repos:
                if isinstance(r, dict) and str(r.get("name") or "") == repo_name:
                    repo = r
                    break
            if not repo:
                self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
                return
            repo_path = str(repo.get("path") or "")
            if not repo_path or not Path(repo_path).is_dir():
                self._send_json({"error": "repo_path_invalid", "repo": repo_name}, status=HTTPStatus.BAD_REQUEST)
                return
            wg_dir = Path(repo_path) / ".workgraph"
            if not wg_dir.is_dir():
                self._send_json({"error": "no_workgraph", "repo": repo_name}, status=HTTPStatus.BAD_REQUEST)
                return
            import subprocess as _sp
            try:
                result = _sp.run(  # noqa: S603
                    ["wg", "service", "start"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self._send_json({
                    "repo": repo_name,
                    "action": "start",
                    "returncode": result.returncode,
                    "stdout": result.stdout[:500],
                    "stderr": result.stderr[:500],
                })
            except Exception as exc:
                self._send_json({"error": str(exc), "repo": repo_name}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # --- Workgraph service start (new route alias) ---
        if route.startswith("/api/repo/") and route.endswith("/service/workgraph/start"):
            repo_name = route[len("/api/repo/"):-len("/service/workgraph/start")]
            if not repo_name:
                self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
                return
            repo_path = self._find_repo_path(repo_name)
            if not repo_path or not Path(repo_path).is_dir():
                self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
                return
            if not (Path(repo_path) / ".workgraph").is_dir():
                self._send_json({"error": "no_workgraph", "repo": repo_name}, status=HTTPStatus.BAD_REQUEST)
                return
            import subprocess as _sp
            try:
                result = _sp.run(  # noqa: S603
                    ["wg", "service", "start"],
                    cwd=repo_path, capture_output=True, text=True, timeout=15,
                )
                self._send_json({
                    "repo": repo_name, "action": "workgraph/start",
                    "returncode": result.returncode,
                    "stdout": result.stdout[:500], "stderr": result.stderr[:500],
                })
            except Exception as exc:
                self._send_json({"error": str(exc), "repo": repo_name}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # --- Workgraph service stop ---
        if route.startswith("/api/repo/") and route.endswith("/service/workgraph/stop"):
            repo_name = route[len("/api/repo/"):-len("/service/workgraph/stop")]
            if not repo_name:
                self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
                return
            repo_path = self._find_repo_path(repo_name)
            if not repo_path or not Path(repo_path).is_dir():
                self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
                return
            if not (Path(repo_path) / ".workgraph").is_dir():
                self._send_json({"error": "no_workgraph", "repo": repo_name}, status=HTTPStatus.BAD_REQUEST)
                return
            import subprocess as _sp
            try:
                result = _sp.run(  # noqa: S603
                    ["wg", "service", "stop"],
                    cwd=repo_path, capture_output=True, text=True, timeout=15,
                )
                self._send_json({
                    "repo": repo_name, "action": "workgraph/stop",
                    "returncode": result.returncode,
                    "stdout": result.stdout[:500], "stderr": result.stderr[:500],
                })
            except Exception as exc:
                self._send_json({"error": str(exc), "repo": repo_name}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # --- launchd start ---
        if route.startswith("/api/repo/") and route.endswith("/service/launchd/start"):
            repo_name = route[len("/api/repo/"):-len("/service/launchd/start")]
            body = self._read_body()
            try:
                body_data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                return
            plist_path = str(body_data.get("plist_path") or "")
            if not plist_path or not _validate_plist_path(plist_path):
                self._send_json({"error": "invalid_plist_path"}, status=HTTPStatus.BAD_REQUEST)
                return
            import subprocess as _sp
            try:
                result = _sp.run(  # noqa: S603
                    ["launchctl", "load", plist_path],
                    capture_output=True, text=True, timeout=10,
                )
                self._send_json({
                    "repo": repo_name, "action": "launchd/start",
                    "plist_path": plist_path,
                    "returncode": result.returncode,
                    "stdout": result.stdout[:500], "stderr": result.stderr[:500],
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # --- launchd stop ---
        if route.startswith("/api/repo/") and route.endswith("/service/launchd/stop"):
            repo_name = route[len("/api/repo/"):-len("/service/launchd/stop")]
            body = self._read_body()
            try:
                body_data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                return
            plist_path = str(body_data.get("plist_path") or "")
            if not plist_path or not _validate_plist_path(plist_path):
                self._send_json({"error": "invalid_plist_path"}, status=HTTPStatus.BAD_REQUEST)
                return
            import subprocess as _sp
            try:
                result = _sp.run(  # noqa: S603
                    ["launchctl", "unload", plist_path],
                    capture_output=True, text=True, timeout=10,
                )
                self._send_json({
                    "repo": repo_name, "action": "launchd/stop",
                    "plist_path": plist_path,
                    "returncode": result.returncode,
                    "stdout": result.stdout[:500], "stderr": result.stderr[:500],
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # --- launchd restart ---
        if route.startswith("/api/repo/") and route.endswith("/service/launchd/restart"):
            repo_name = route[len("/api/repo/"):-len("/service/launchd/restart")]
            body = self._read_body()
            try:
                body_data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                return
            plist_path = str(body_data.get("plist_path") or "")
            if not plist_path or not _validate_plist_path(plist_path):
                self._send_json({"error": "invalid_plist_path"}, status=HTTPStatus.BAD_REQUEST)
                return
            import subprocess as _sp
            try:
                unload = _sp.run(  # noqa: S603
                    ["launchctl", "unload", plist_path],
                    capture_output=True, text=True, timeout=10,
                )
                if unload.returncode != 0:
                    self._send_json({
                        "repo": repo_name, "action": "launchd/restart",
                        "plist_path": plist_path,
                        "unload": {"returncode": unload.returncode,
                                   "stdout": unload.stdout[:500],
                                   "stderr": unload.stderr[:500]},
                        "load": None,
                        "error": "unload_failed",
                    }, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                load = _sp.run(  # noqa: S603
                    ["launchctl", "load", plist_path],
                    capture_output=True, text=True, timeout=10,
                )
                self._send_json({
                    "repo": repo_name, "action": "launchd/restart",
                    "plist_path": plist_path,
                    "unload": {"returncode": unload.returncode,
                               "stdout": unload.stdout[:500],
                               "stderr": unload.stderr[:500]},
                    "load": {"returncode": load.returncode,
                             "stdout": load.stdout[:500],
                             "stderr": load.stderr[:500]},
                })
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        # --- Agent session launch ---
        if route.startswith("/api/repo/") and route.endswith("/launch"):
            repo_name = route[len("/api/repo/"):-len("/launch")]
            if not repo_name:
                self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
                return
            body = self._read_body()
            try:
                body_data = json.loads(body) if body else {}
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
                return
            mode = str(body_data.get("mode") or "fresh")
            agent_type = str(body_data.get("agent_type") or "claude-code")
            valid_modes = {"fresh", "seeded", "continuation", "resume"}
            valid_agents = {"claude-code", "codex", "shell"}
            if mode not in valid_modes:
                self._send_json(
                    {"error": "invalid_mode", "valid": sorted(valid_modes)},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            if agent_type not in valid_agents:
                self._send_json(
                    {"error": "invalid_agent_type", "valid": sorted(valid_agents)},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            snapshot = self._read_snapshot()
            repo = None
            for r in (snapshot.get("repos") or []):
                if isinstance(r, dict) and str(r.get("name") or "") == repo_name:
                    repo = r
                    break
            if not repo:
                self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
                return
            activity_digest: dict | None = None
            activity_path = getattr(self.__class__, "activity_path", None)
            if activity_path and activity_path.exists():
                activity_digest = read_activity_digest(activity_path)
            try:
                result = _launch_session(
                    repo,
                    mode=mode,
                    agent_type=agent_type,
                    activity_digest=activity_digest,
                )
                self._send_json(result)
            except _FreshellUnavailableError as exc:
                self._send_json(
                    {"error": "freshell_unavailable", "message": str(exc)},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
            except Exception as exc:
                logging.getLogger(__name__).debug("launch_session failed", exc_info=True)
                self._send_json(
                    {"error": "launch_failed", "message": str(exc)[:200]},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if route == "/api/agent/chat":
            self._handle_post_agent_chat()
            return

        if route == "/api/agent/chat/clear":
            self._handle_clear_agent_chat_history()
            return

        if route == "/api/agent/chat/new":
            self._handle_post_new_chat_session()
            return

        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._send_html(render_dashboard_html())
            return
        if route in ("/models", "/model-registry"):
            self._send_html(render_model_registry_dashboard_html())
            return
        if route in ("/ws", "/ws/status"):
            self._serve_websocket()
            return
        snapshot = self._read_snapshot()
        if route == "/api/status":
            self._send_json(snapshot)
            return
        if route == "/api/model-registry":
            try:
                self._send_json(build_model_registry_status(workspace_root=self.workspace_root))
            except Exception as exc:
                logging.getLogger(__name__).debug("model registry status failed", exc_info=True)
                self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if route == "/api/repos":
            self._send_json(snapshot.get("repos") or [])
            return
        if route == "/api/operator/home":
            self._send_json(
                build_operator_home(
                    snapshot=snapshot,
                    decisions=self._load_chat_decisions(snapshot) or [],
                    notification_ledger=self._load_notification_ledger(),
                )
            )
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
        if route == "/api/conformance":
            self._send_json(extract_conformance_response(snapshot))
            return
        if route == "/api/upstream-tracker":
            payload = snapshot.get("upstream_tracker")
            if not isinstance(payload, dict) or "adoption_cycle" not in payload:
                from driftdriver.upstream_tracker import build_snapshot_entry as _build_upstream_entry

                project_dir_raw = snapshot.get("project_dir")
                project_dir = (
                    Path(str(project_dir_raw)).resolve()
                    if project_dir_raw
                    else self.snapshot_path.parents[3]
                )
                payload = _build_upstream_entry(
                    snapshot.get("repos") if isinstance(snapshot.get("repos"), list) else [],
                    state_dir=project_dir / ".driftdriver",
                )
            self._send_json(payload or {
                "pass1_last_run": None,
                "pass1_results": [],
                "pass2_findings": [],
                "adoption_cycle": {},
            })
            return
        if route == "/api/creation-pipeline":
            self._send_json(snapshot.get("creation_pipeline") or [])
            return
        if route == "/api/planforge":
            self._send_json(
                snapshot.get("planforge_sessions")
                or {"sessions": [], "total": 0}
            )
            return
        if route == "/api/convergence":
            self._send_json(
                snapshot.get("convergence")
                or {
                    "total_repos": 0,
                    "configured": 0,
                    "converged": 0,
                    "progress_pct": 0.0,
                    "by_status": {},
                    "by_target": {},
                    "repos": [],
                }
            )
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
        if route == "/api/activity":
            params = self.path.split("?", 1)
            window = "48h"
            if len(params) > 1:
                for part in params[1].split("&"):
                    if part.startswith("window="):
                        window = part[len("window="):]
            activity_path = getattr(self.__class__, "activity_path", None)
            if activity_path is None:
                self._send_json({"generated_at": None, "window": window, "timeline": [], "repos": []})
            else:
                self._send_json(_build_activity_payload(activity_path, window))
            return
        if route in ("/api/decisions", "/api/decisions/pending"):
            all_decisions = self._load_chat_decisions(snapshot) or []
            # /api/decisions/pending returns flat list (used by telegram poller)
            # /api/decisions returns wrapped object (used by dashboard)
            if route == "/api/decisions/pending":
                self._send_json(all_decisions)
            else:
                self._send_json({"decisions": all_decisions, "count": len(all_decisions)})
            return

        # --- Intelligence endpoints ---
        if route.startswith("/intelligence/"):
            try:
                pg = self._pg_config()
                if route == "/intelligence/briefing":
                    self._send_json(build_briefing(pg))
                    return
                if route == "/intelligence/briefing/history":
                    self._send_json(build_briefing_history(pg))
                    return
                if route == "/intelligence/inbox":
                    self._send_json(build_inbox(pg))
                    return
                if route == "/intelligence/decisions/trends":
                    self._send_json(build_decision_trends(pg))
                    return
                if route == "/intelligence/tracking":
                    # snapshot_path is .workgraph/service/ecosystem-hub/snapshot.json
                    wg_dir = self.snapshot_path.parent.parent.parent
                    self._send_json(build_tracking(pg, wg_dir=wg_dir))
                    return
                if route == "/intelligence/decisions":
                    qs = self.path.split("?", 1)[1] if "?" in self.path else ""
                    params: dict[str, str] = {}
                    for pair in qs.split("&"):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            params[k] = v
                    kwargs: dict[str, Any] = {}
                    if params.get("source_type"):
                        kwargs["source_type"] = params["source_type"]
                    if params.get("decision"):
                        kwargs["decision"] = params["decision"]
                    if params.get("search"):
                        kwargs["search"] = params["search"]
                    self._send_json(build_decision_log(pg, **kwargs))
                    return
            except Exception as exc:
                logging.getLogger(__name__).debug("intelligence GET failed", exc_info=True)
                self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

        # --- GET /api/repo/:name/services ---
        if route.startswith("/api/repo/") and route.endswith("/services"):
            repo_name = route[len("/api/repo/"):-len("/services")]
            if not repo_name:
                self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
                return
            repo_path = self._find_repo_path(repo_name)
            if not repo_path or not Path(repo_path).is_dir():
                self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
                return
            try:
                services_payload = _detect_services(repo_name, repo_path)
                self._send_json(services_payload)
            except Exception as exc:
                logging.getLogger(__name__).debug("services detect failed", exc_info=True)
                self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if route.startswith("/api/repo/") and not route.endswith("/start"):
            repo_name = route[len("/api/repo/"):].strip("/")
            if not repo_name:
                self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
                return
            activity_path = getattr(self.__class__, "activity_path", None)
            payload = _build_repo_detail_payload(repo_name, self.snapshot_path, activity_path)
            if payload is None:
                self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(payload)
            return

        if route == "/api/factory-daily":
            from driftdriver.factory_report import list_factory_daily_reports
            project_dir = self.snapshot_path.parent.parent
            try:
                reports = list_factory_daily_reports(project_dir)
                self._send_json({"reports": reports})
            except Exception as exc:
                logging.getLogger(__name__).debug("factory-daily failed", exc_info=True)
                self._send_json({"error": str(exc)[:200], "reports": []})
            return

        if route.startswith("/api/factory-daily/"):
            report_date = route[len("/api/factory-daily/"):].strip("/")
            from driftdriver.factory_report import list_factory_daily_reports
            import json as _json
            project_dir = self.snapshot_path.parent.parent
            wg_dir = project_dir / ".workgraph"
            report_path = wg_dir / f"factory-daily-{report_date}.json"
            if report_path.exists():
                try:
                    self._send_json(_json.loads(report_path.read_text(encoding="utf-8")))
                except Exception as exc:
                    self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return

        if route == "/api/sessions":
            self._handle_get_sessions()
            return

        if route == "/api/sessions/select":
            self._handle_post_session_select()
            return

        if route == "/api/services/manifest":
            from driftdriver.ecosystem_hub.services_manifest import build_services_manifest
            try:
                self._send_json(build_services_manifest(snapshot))
            except Exception as exc:
                logging.getLogger(__name__).debug("services manifest failed", exc_info=True)
                self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if route == "/api/agent/chat/history":
            self._handle_get_agent_chat_history()
            return

        if route == "/api/agent/chat/sessions":
            self._handle_get_chat_sessions()
            return

        if route.startswith("/api/agent/chat/session/"):
            session_id = route[len("/api/agent/chat/session/"):]
            if session_id:
                self._handle_get_chat_session(session_id)
            else:
                self._send_json({"error": "missing_session_id"}, status=HTTPStatus.BAD_REQUEST)
            return

        if not route.startswith("/api/") and not route.startswith("/ws"):
            self._send_html(render_dashboard_html())
            return
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep daemon logs clean and structured in our own files.
        return


def extract_conformance_response(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Extract conformance findings from a snapshot dict for the /api/conformance response."""
    findings = snapshot.get("conformance_findings", [])
    return {"findings": findings, "count": len(findings)}


def _build_activity_payload(activity_path: Path, window: str = "48h") -> dict[str, Any]:
    """Build the /api/activity response from the cached digest file."""
    from datetime import datetime, timedelta, timezone

    valid_windows = {"24h": 1, "48h": 2, "72h": 3, "7d": 7}
    days = valid_windows.get(window, 2)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    digest = read_activity_digest(activity_path)
    all_repos = digest.get("repos") or []

    # Build flat timeline filtered to window
    timeline: list[dict[str, Any]] = []
    for repo_entry in all_repos:
        for commit in repo_entry.get("timeline", []):
            try:
                ts = datetime.fromisoformat(commit["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    timeline.append(commit)
            except (ValueError, KeyError):
                continue
    timeline.sort(key=lambda c: c["timestamp"], reverse=True)

    # Build per-repo summary filtered to window
    repos_out: list[dict[str, Any]] = []
    for repo_entry in all_repos:
        window_data = (repo_entry.get("windows") or {}).get(window, {})
        count = window_data.get("count", 0)
        if count == 0 and not repo_entry.get("last_commit_at"):
            continue
        repos_out.append({
            "name": repo_entry.get("name"),
            "last_commit_at": repo_entry.get("last_commit_at"),
            "summary": repo_entry.get("summary"),
            "window_count": count,
        })

    # Sort repos by last_commit_at descending
    def _ts_key(r: dict[str, Any]) -> str:
        return r.get("last_commit_at") or ""

    repos_out.sort(key=_ts_key, reverse=True)

    return {
        "generated_at": digest.get("generated_at"),
        "window": window,
        "timeline": timeline,
        "repos": repos_out,
    }


def _build_repo_detail_payload(
    repo_name: str,
    snapshot_path: Path,
    activity_path: Path | None,
) -> dict[str, Any] | None:
    """Assemble all per-repo signals for GET /api/repo/:name.

    Returns None if snapshot is missing or repo_name is not found.
    """
    import glob as _glob

    if not snapshot_path.exists():
        return None

    data = _read_json(snapshot_path)
    if not data:
        return None

    repos = data.get("repos") or []
    repo: dict[str, Any] | None = None
    all_repos = [r for r in repos if isinstance(r, dict)]

    for r in all_repos:
        if str(r.get("name") or "") == repo_name:
            repo = r
            break

    if repo is None:
        return None

    # --- git ---
    git: dict[str, Any] = {
        "branch": str(repo.get("git_branch") or ""),
        "dirty": bool(repo.get("git_dirty")),
        "dirty_file_count": int(repo.get("dirty_file_count") or 0),
        "untracked_file_count": int(repo.get("untracked_file_count") or 0),
        "ahead": int(repo.get("ahead") or 0),
        "behind": int(repo.get("behind") or 0),
    }

    # --- services ---
    plist_pattern = str(
        Path.home() / "Library" / "LaunchAgents" / f"*{repo_name}*"
    )
    launchd_loaded = bool(_glob.glob(plist_pattern))
    services: dict[str, Any] = {
        "workgraph_service_running": bool(repo.get("service_running")),
        "launchd_plist_loaded": launchd_loaded,
        "cron_jobs": [],
    }

    # --- workgraph ---
    workgraph: dict[str, Any] = {
        "exists": bool(repo.get("workgraph_exists")),
        "task_counts": dict(repo.get("task_counts") or {}),
        "in_progress": list(repo.get("in_progress") or []),
        "ready": list(repo.get("ready") or []),
    }

    # --- presence actors ---
    presence_actors: list[dict[str, Any]] = list(repo.get("presence_actors") or [])

    # --- dependencies ---
    raw_deps = [r for r in (repo.get("cross_repo_dependencies") or []) if isinstance(r, dict)]
    depends_on: list[str] = [
        str(d.get("repo") or "") for d in raw_deps if str(d.get("repo") or "")
    ]
    depended_on_by: list[str] = []
    for other in all_repos:
        if str(other.get("name") or "") == repo_name:
            continue
        other_deps = [
            str(d.get("repo") or "")
            for d in (other.get("cross_repo_dependencies") or [])
            if isinstance(d, dict)
        ]
        if repo_name in other_deps:
            depended_on_by.append(str(other.get("name") or ""))

    dependencies: dict[str, Any] = {
        "depends_on": depends_on,
        "depended_on_by": sorted(set(depended_on_by)),
    }

    # --- health ---
    northstar = repo.get("northstar") or repo.get("repo_north_star") or {}
    health: dict[str, Any] = {
        "drift_score": northstar.get("score"),
        "drift_tier": str(northstar.get("tier") or ""),
        "security_findings": list(repo.get("security_findings") or []),
        "quality_findings": list(repo.get("quality_findings") or []),
        "stalled": bool(repo.get("stalled")),
        "stall_reasons": list(repo.get("stall_reasons") or []),
        "narrative": str(repo.get("narrative") or ""),
    }

    # --- activity (from digest, falls back to empty) ---
    activity: dict[str, Any] = {
        "last_commit_at": None,
        "summary": None,
        "timeline": [],
    }
    if activity_path and activity_path.exists():
        digest = read_activity_digest(activity_path)
        for entry in digest.get("repos") or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("name") or "") == repo_name:
                activity["last_commit_at"] = entry.get("last_commit_at")
                activity["summary"] = entry.get("summary")
                activity["timeline"] = list(entry.get("timeline") or [])
                break

    # --- agent history ---
    repo_fs_path = str(repo.get("path") or "")
    agent_history: dict[str, Any] = {"sessions": [], "total_sessions_in_file": 0, "history_since": None}
    if repo_fs_path and Path(repo_fs_path).is_dir():
        agent_history = _build_agent_history(Path(repo_fs_path))

    return {
        "name": repo_name,
        "path": repo_fs_path,
        "exists": bool(repo.get("exists")),
        "source": str(repo.get("source") or ""),
        "tags": list(repo.get("tags") or []),
        "ecosystem_role": str(repo.get("ecosystem_role") or ""),
        "git": git,
        "services": services,
        "workgraph": workgraph,
        "presence_actors": presence_actors,
        "dependencies": dependencies,
        "health": health,
        "activity": activity,
        "agent_history": agent_history,
    }


def _handler_factory(
    snapshot_path: Path,
    state_path: Path,
    live_hub: LiveStreamHub,
    activity_path: Path | None = None,
    workspace_root: Path | None = None,
) -> type[_HubHandler]:
    class Handler(_HubHandler):
        pass

    Handler.snapshot_path = snapshot_path
    Handler.state_path = state_path
    Handler.live_hub = live_hub
    Handler.activity_path = activity_path
    Handler.workspace_root = workspace_root
    return Handler
