# ABOUTME: Python Unix socket client for workgraph daemon IPC
# ABOUTME: Sends JSON line-delimited commands and parses responses

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass, field


class IpcError(Exception):
    """Raised when IPC communication fails."""


@dataclass
class IpcResponse:
    ok: bool
    data: dict = field(default_factory=dict)
    error: str = ""


def send_ipc(socket_path: str, request: dict, timeout: float = 5.0) -> dict:
    """Send a JSON request over Unix socket and return parsed response.

    Protocol: send a single JSON line, receive a single JSON line back.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
    except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
        raise IpcError(f"connection failed: {exc}") from exc

    try:
        payload = json.dumps(request) + "\n"
        sock.sendall(payload.encode("utf-8"))

        # Read response until newline
        chunks: list[bytes] = []
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout as exc:
                raise IpcError("timeout waiting for response") from exc
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break

        raw = b"".join(chunks).decode("utf-8").strip()
        if not raw:
            raise IpcError("empty response from daemon")

        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IpcError(f"invalid JSON response: {exc}") from exc
    finally:
        sock.close()


def query_task(socket_path: str, task_id: str) -> dict:
    """Query a task by ID via IPC."""
    return send_ipc(socket_path, {"cmd": "query_task", "task_id": task_id})


def add_task(
    socket_path: str,
    title: str,
    description: str = "",
    after: list[str] | None = None,
    tags: list[str] | None = None,
    origin: str = "",
) -> str:
    """Add a task via IPC. Returns the new task ID."""
    request: dict = {
        "cmd": "add_task",
        "title": title,
        "description": description,
    }
    if after:
        request["after"] = after
    if tags:
        request["tags"] = tags
    if origin:
        request["origin"] = origin

    response = send_ipc(socket_path, request)
    return response.get("task_id", "")


def send_heartbeat(socket_path: str, agent_id: str) -> bool:
    """Send a heartbeat for an agent. Returns True on success."""
    response = send_ipc(socket_path, {"cmd": "heartbeat", "agent_id": agent_id})
    return response.get("ok", False)


def notify_graph_changed(socket_path: str) -> bool:
    """Notify the daemon that the task graph has changed. Returns True on success."""
    response = send_ipc(socket_path, {"cmd": "graph_changed"})
    return response.get("ok", False)


def get_service_status(socket_path: str) -> dict:
    """Query daemon service status."""
    return send_ipc(socket_path, {"cmd": "status"})
