# ABOUTME: Streaming ecosystem agent for the hub. Direct Anthropic SDK, tool dispatch.
# ABOUTME: Tools: ecosystem status, repo detail, arm/disarm, attractor, wg tasks, Freshell sessions.

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Generator

import anthropic

_SYSTEM_PROMPT = """You are the Ecosystem Agent — an autonomous operator for the Speedrift dark factory.

You have full visibility into all enrolled repos and can take direct action:
- Read ecosystem status and per-repo details
- Arm repos for supervised or autonomous operation via speedriftd
- Run the attractor convergence loop on repos
- Create and list workgraph tasks in any repo
- Launch Freshell sessions and run commands in repos

When asked to do work, do it. Don't ask for confirmation on routine operations.
Be concise. Prefer action over explanation. When you spawn Freshell sessions or
run commands, report what happened and what you found.

IMPORTANT: Never spawn duplicate Freshell terminals. Always call list_freshell_sessions
first to check if a session for that repo already exists. When finished with a terminal
session, close it with close_freshell_session to avoid accumulation.

The ecosystem uses:
- Workgraph (wg) for task management in each repo
- Driftdriver for drift checking and attractor convergence
- speedriftd for repo mode control (observe -> supervise -> autonomous)
- Freshell at port 3550 for terminal sessions
"""

TOOL_DEFINITIONS = [
    {
        "name": "get_ecosystem_status",
        "description": "Get status summary of all enrolled repos — drift scores, trends, attention items.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_repo_detail",
        "description": "Get detailed status for a specific repo including tasks, drift findings, and services.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {"type": "string", "description": "Repo name as shown in the hub"},
            },
            "required": ["repo_name"],
        },
    },
    {
        "name": "arm_repo",
        "description": "Arm a repo for supervised or autonomous operation via speedriftd.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["supervise", "autonomous"],
                    "description": "supervise or autonomous",
                },
                "reason": {"type": "string"},
            },
            "required": ["repo_name", "mode", "reason"],
        },
    },
    {
        "name": "disarm_repo",
        "description": "Return a repo to observe mode (passive) and release the lease.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["repo_name", "reason"],
        },
    },
    {
        "name": "run_attractor",
        "description": "Run the attractor convergence loop for a repo (diagnose -> plan -> execute -> re-diagnose).",
        "input_schema": {
            "type": "object",
            "properties": {"repo_name": {"type": "string"}},
            "required": ["repo_name"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List workgraph tasks for a repo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {"type": "string"},
                "status": {
                    "type": "string",
                    "description": "Filter: open, in-progress, done, all (default: open)",
                },
            },
            "required": ["repo_name"],
        },
    },
    {
        "name": "create_task",
        "description": "Create a workgraph task in a repo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {"type": "string"},
                "title": {"type": "string", "description": "Task title"},
                "description": {"type": "string", "description": "Optional detailed description"},
            },
            "required": ["repo_name", "title"],
        },
    },
    {
        "name": "launch_freshell_session",
        "description": "Launch or resume a Freshell terminal session for a repo. Returns the session URL.",
        "input_schema": {
            "type": "object",
            "properties": {"repo_name": {"type": "string"}},
            "required": ["repo_name"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command in a repo's directory. Use for wg, driftdriver, git, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {"type": "string"},
                "command": {"type": "string", "description": "Shell command to run"},
            },
            "required": ["repo_name", "command"],
        },
    },
    {
        "name": "audit_services",
        "description": (
            "Audit all LaunchAgent services across the ecosystem. Returns orphaned plists, "
            "duplicate working dirs, stopped keep-alive services, unloaded services, and "
            "enrolled repos with no plist. Use to recommend consolidation or cleanup."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "send_keys_to_session",
        "description": (
            "Send text or keystrokes to a Freshell terminal pane (spawned via launch_freshell_session). "
            "Use to run commands in the terminal, e.g. send 'wg status\\r' to run it. "
            "IMPORTANT: Use \\r (carriage return) for Enter, NOT \\n — PTY terminals require \\r."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pane_id": {"type": "string", "description": "Pane ID returned by launch_freshell_session"},
                "keys": {"type": "string", "description": "Text/keys to send. Use \\r for Enter (NOT \\n)."},
            },
            "required": ["pane_id", "keys"],
        },
    },
    {
        "name": "capture_pane_output",
        "description": "Read the current terminal output from a Freshell pane.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pane_id": {"type": "string", "description": "Pane ID returned by launch_freshell_session"},
            },
            "required": ["pane_id"],
        },
    },
    {
        "name": "close_freshell_session",
        "description": "Close a Freshell terminal tab by tab_id. Use to clean up sessions when done.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tab_id": {"type": "string", "description": "Tab ID returned by launch_freshell_session"},
            },
            "required": ["tab_id"],
        },
    },
    {
        "name": "list_freshell_sessions",
        "description": "List all currently open Freshell terminal tabs.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


def _run_subprocess(cmd: list[str], cwd: str, timeout: int = 30) -> dict:
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return {
            "ok": result.returncode == 0,
            "output": (result.stdout + result.stderr).strip(),
            "returncode": result.returncode,
        }
    except FileNotFoundError as e:
        return {"ok": False, "output": str(e), "returncode": -1}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "command timed out", "returncode": -1}
    except Exception as e:
        return {"ok": False, "output": str(e), "returncode": -1}


class EcosystemAgent:
    def __init__(
        self,
        snapshot_path: Path | str | None,
        history_path: Path | str | None = None,
        freshell_base: str | None = None,
        session_dir: Path | str | None = None,
    ) -> None:
        self._snapshot_path = Path(snapshot_path) if snapshot_path else None
        self._history_path = Path(history_path) if history_path else None
        self._session_dir = Path(session_dir) if session_dir else None
        self._freshell_base = freshell_base or os.environ.get("FRESHELL_BASE_URL", "http://localhost:3550")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key and self._snapshot_path:
            _key_file = self._snapshot_path.parent / "anthropic_api_key"
            if _key_file.exists():
                api_key = _key_file.read_text().strip()
        self._client = anthropic.Anthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict]:
        return TOOL_DEFINITIONS

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_snapshot(self) -> dict:
        if not self._snapshot_path or not self._snapshot_path.exists():
            return {}
        try:
            return json.loads(self._snapshot_path.read_text())
        except Exception:
            return {}

    def _find_repo_path(self, repo_name: str) -> str | None:
        snap = self._get_snapshot()
        for r in snap.get("repos", []):
            if isinstance(r, dict) and r.get("name") == repo_name:
                return r.get("path") or r.get("root")
        return None

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def execute_tool(self, name: str, inputs: dict) -> Any:
        if name == "get_ecosystem_status":
            return self._tool_ecosystem_status()
        if name == "get_repo_detail":
            return self._tool_repo_detail(inputs.get("repo_name", ""))
        if name == "arm_repo":
            return self._tool_arm_repo(inputs["repo_name"], inputs["mode"], inputs["reason"])
        if name == "disarm_repo":
            return self._tool_disarm_repo(inputs["repo_name"], inputs["reason"])
        if name == "run_attractor":
            return self._tool_run_attractor(inputs["repo_name"])
        if name == "list_tasks":
            return self._tool_list_tasks(inputs["repo_name"], inputs.get("status", "open"))
        if name == "create_task":
            return self._tool_create_task(
                inputs["repo_name"], inputs["title"], inputs.get("description", "")
            )
        if name == "launch_freshell_session":
            return self._tool_launch_freshell(inputs["repo_name"])
        if name == "run_command":
            return self._tool_run_command(inputs["repo_name"], inputs["command"])
        if name == "audit_services":
            return self._tool_audit_services()
        if name == "send_keys_to_session":
            return self._tool_send_keys(inputs["pane_id"], inputs["keys"])
        if name == "capture_pane_output":
            return self._tool_capture_pane(inputs["pane_id"])
        if name == "close_freshell_session":
            return self._tool_close_freshell_tab(inputs["tab_id"])
        if name == "list_freshell_sessions":
            return self._tool_list_freshell_sessions()
        return {"error": f"unknown tool: {name}"}

    def _tool_ecosystem_status(self) -> dict:
        snap = self._get_snapshot()
        if not snap:
            return {"status": "no snapshot available"}
        repos = snap.get("repos", [])
        attention = [r for r in repos if isinstance(r, dict) and r.get("drift_score", 0) >= 60]
        return {
            "total_repos": len(repos),
            "needs_attention": len(attention),
            "top_attention": [
                {"name": r.get("name"), "drift": r.get("drift_score"), "trend": r.get("trend")}
                for r in sorted(attention, key=lambda x: x.get("drift_score", 0), reverse=True)[:5]
            ],
            "generated_at": snap.get("generated_at"),
        }

    def _tool_repo_detail(self, repo_name: str) -> dict:
        snap = self._get_snapshot()
        for r in snap.get("repos", []):
            if isinstance(r, dict) and r.get("name") == repo_name:
                return {k: v for k, v in r.items()}
        return {"error": f"repo not found: {repo_name}"}

    def _tool_arm_repo(self, repo_name: str, mode: str, reason: str) -> dict:
        path = self._find_repo_path(repo_name)
        if not path:
            return {"error": f"repo path not found for {repo_name}"}
        result = _run_subprocess(
            [
                "driftdriver", "--dir", path, "speedriftd", "status",
                "--set-mode", mode, "--lease-owner", "ecosystem-agent", "--reason", reason,
            ],
            cwd=path,
        )
        return {"repo": repo_name, "mode": mode, **result}

    def _tool_disarm_repo(self, repo_name: str, reason: str) -> dict:
        path = self._find_repo_path(repo_name)
        if not path:
            return {"error": f"repo path not found for {repo_name}"}
        result = _run_subprocess(
            [
                "driftdriver", "--dir", path, "speedriftd", "status",
                "--set-mode", "observe", "--release-lease", "--reason", reason,
            ],
            cwd=path,
        )
        return {"repo": repo_name, "mode": "observe", **result}

    def _tool_run_attractor(self, repo_name: str) -> dict:
        path = self._find_repo_path(repo_name)
        if not path:
            return {"error": f"repo path not found for {repo_name}"}
        result = _run_subprocess(
            ["driftdriver", "--dir", path, "attractor", "run", "--json"],
            cwd=path,
            timeout=120,
        )
        return {"repo": repo_name, **result}

    def _tool_list_tasks(self, repo_name: str, status: str) -> dict:
        path = self._find_repo_path(repo_name)
        if not path:
            return {"error": f"repo path not found for {repo_name}"}
        cmd = ["wg", "list"]
        if status and status != "all":
            cmd += ["--status", status]
        result = _run_subprocess(cmd, cwd=path)
        return {"repo": repo_name, **result}

    def _tool_create_task(self, repo_name: str, title: str, description: str) -> dict:
        path = self._find_repo_path(repo_name)
        if not path:
            return {"error": f"repo path not found for {repo_name}"}
        cmd = ["wg", "add", title]
        if description:
            cmd += ["--description", description]
        result = _run_subprocess(cmd, cwd=path)
        return {"repo": repo_name, **result}

    def _freshell_request(self, method: str, path: str, body: dict | None = None) -> dict:
        """Make an authenticated request to the local Freshell agent API."""
        token = os.environ.get("FRESHELL_AUTH_TOKEN", "")
        url = f"http://localhost:3550{path}"
        payload = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=payload, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Auth-Token", token)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _tool_launch_freshell(self, repo_name: str) -> dict:
        path = self._find_repo_path(repo_name)
        cwd = path or repo_name
        token = os.environ.get("FRESHELL_AUTH_TOKEN", "")
        token_suffix = f"?token={token}" if token else ""
        session_url = f"{self._freshell_base}/{token_suffix}"
        try:
            # Check for an existing terminal tab with matching name — avoid duplicates
            tabs_data = self._freshell_request("GET", "/api/tabs")
            for tab in (tabs_data.get("data") or {}).get("tabs") or []:
                tab_title = (tab.get("title") or "").strip()
                if tab_title == repo_name:
                    return {
                        "ok": True,
                        "session_url": session_url,
                        "tab_id": tab.get("id", ""),
                        "pane_id": tab.get("activePaneId", ""),
                        "cwd": cwd,
                        "note": "Reusing existing Freshell terminal for this repo.",
                    }

            data = self._freshell_request("POST", "/api/tabs", {
                "name": repo_name, "cwd": cwd, "mode": "shell"
            })
            result = data.get("data", {})
            tab_id = result.get("tabId", "")
            pane_id = result.get("paneId", "")
            return {
                "ok": True,
                "session_url": session_url,
                "tab_id": tab_id,
                "pane_id": pane_id,
                "cwd": cwd,
                "note": "Terminal spawned in Freshell. Use send_keys to interact with it.",
            }
        except Exception as e:
            return {"ok": False, "error": f"Freshell unavailable: {e}"}

    def _tool_close_freshell_tab(self, tab_id: str) -> dict:
        try:
            result = self._freshell_request("DELETE", f"/api/tabs/{tab_id}")
            return {"ok": True, "tab_id": tab_id, "response": result}
        except Exception as e:
            return {"ok": False, "tab_id": tab_id, "error": str(e)}

    def _tool_list_freshell_sessions(self) -> dict:
        try:
            data = self._freshell_request("GET", "/api/tabs")
            tabs = (data.get("data") or {}).get("tabs") or []
            return {"ok": True, "sessions": tabs, "count": len(tabs)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _tool_send_keys(self, pane_id: str, keys: str) -> dict:
        try:
            # PTY terminals require \r (carriage return) for Enter, not \n
            keys = keys.replace("\n", "\r")
            result = self._freshell_request("POST", f"/api/panes/{pane_id}/send-keys", {"keys": keys})
            return {"ok": True, "pane_id": pane_id, "sent": keys, "response": result}
        except Exception as e:
            return {"ok": False, "pane_id": pane_id, "error": str(e)}

    def _tool_capture_pane(self, pane_id: str) -> dict:
        try:
            token = os.environ.get("FRESHELL_AUTH_TOKEN", "")
            url = f"http://localhost:3550/api/panes/{pane_id}/capture"
            req = urllib.request.Request(url)
            if token:
                req.add_header("X-Auth-Token", token)
            with urllib.request.urlopen(req, timeout=10) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read()
                if "application/json" in content_type:
                    data = json.loads(raw)
                    output = (data.get("data") or {}).get("content") or data.get("content") or raw.decode()
                else:
                    output = raw.decode("utf-8", errors="replace")
            return {"ok": True, "pane_id": pane_id, "output": output}
        except Exception as e:
            return {"ok": False, "pane_id": pane_id, "error": str(e)}

    def _tool_audit_services(self) -> dict:
        from driftdriver.ecosystem_hub.services_manifest import audit_services
        snap = self._get_snapshot()
        return audit_services(snap)

    def _tool_run_command(self, repo_name: str, command: str) -> dict:
        path = self._find_repo_path(repo_name)
        if not path:
            return {"error": f"repo path not found for {repo_name}"}
        result = _run_subprocess(["bash", "-c", command], cwd=path, timeout=60)
        return {"repo": repo_name, "command": command, **result}

    # ------------------------------------------------------------------
    # Context + streaming
    # ------------------------------------------------------------------

    def _build_context(self) -> str:
        snap = self._get_snapshot()
        lines = []
        if not snap:
            lines.append("No ecosystem snapshot available.")
        else:
            repos = snap.get("repos", [])
            attention = sorted(
                [r for r in repos if isinstance(r, dict) and r.get("drift_score", 0) >= 60],
                key=lambda x: x.get("drift_score", 0),
                reverse=True,
            )
            lines.append(
                f"Ecosystem: {len(repos)} repos | Generated: {snap.get('generated_at', 'unknown')}"
            )
            lines.append(
                f"Needs attention ({len(attention)}): "
                + ", ".join(r.get("name", "") for r in attention[:8])
            )

        if self._session_dir:
            try:
                from driftdriver.ecosystem_hub.chat_history import ChatSessionManager
                mgr = ChatSessionManager(self._session_dir)
                summary = mgr.recent_context_summary(max_sessions=3)
                lines.append("")
                lines.append(summary)
            except Exception:
                pass

        return "\n".join(lines)

    def stream_response(
        self, message: str, history_messages: list[dict]
    ) -> Generator[str, None, None]:
        """Stream response chunks. Yields text strings. Handles tool calls inline."""
        context = self._build_context()
        system = f"{_SYSTEM_PROMPT}\n\n## Current Ecosystem State\n{context}"
        messages = list(history_messages) + [{"role": "user", "content": message}]

        while True:
            tool_calls: list[dict] = []
            current_tool: dict | None = None

            with self._client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system,
                messages=messages,
                tools=self.get_tool_definitions(),  # type: ignore[arg-type]
            ) as stream:
                for event in stream:
                    event_type = type(event).__name__

                    if event_type == "RawContentBlockStartEvent":
                        block = event.content_block
                        if hasattr(block, "type") and block.type == "tool_use":
                            current_tool = {"id": block.id, "name": block.name, "input_json": ""}
                        else:
                            current_tool = None

                    elif event_type == "RawContentBlockDeltaEvent":
                        delta = event.delta
                        if hasattr(delta, "text"):
                            yield delta.text
                        elif hasattr(delta, "partial_json") and current_tool is not None:
                            current_tool["input_json"] += delta.partial_json

                    elif "ContentBlockStop" in event_type:
                        if current_tool is not None:
                            tool_calls.append(current_tool)
                            current_tool = None

                final_msg = stream.get_final_message()

            if final_msg.stop_reason != "tool_use" or not tool_calls:
                break

            # Execute tools, yield progress, continue
            tool_use_blocks = []
            tool_results = []
            for tc in tool_calls:
                try:
                    inputs = json.loads(tc["input_json"]) if tc["input_json"] else {}
                except json.JSONDecodeError:
                    inputs = {}

                yield f"\n[{tc['name']}] "
                result = self.execute_tool(tc["name"], inputs)
                result_text = json.dumps(result, default=str)

                tool_use_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": inputs,
                })
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_text,
                })

            messages = messages + [
                {"role": "assistant", "content": tool_use_blocks},
                {"role": "user", "content": tool_results},
            ]
