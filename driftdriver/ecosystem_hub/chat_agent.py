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
        history_path: Path | str | None,
        freshell_base: str = "http://localhost:3550",
    ) -> None:
        self._snapshot_path = Path(snapshot_path) if snapshot_path else None
        self._history_path = Path(history_path) if history_path else None
        self._freshell_base = freshell_base
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

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

    def _tool_launch_freshell(self, repo_name: str) -> dict:
        path = self._find_repo_path(repo_name)
        payload = json.dumps({"repo": path or repo_name, "agent_type": "claude-code"}).encode()
        try:
            req = urllib.request.Request(
                f"{self._freshell_base}/api/sessions",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                session_id = data.get("id") or data.get("session_id", "")
                url = (
                    f"{self._freshell_base}/session/{session_id}"
                    if session_id
                    else self._freshell_base
                )
                return {"ok": True, "session_url": url, "session_id": session_id}
        except Exception as e:
            return {
                "ok": False,
                "error": f"Freshell unavailable: {e}",
                "freshell_url": self._freshell_base,
            }

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
        if not snap:
            return "No ecosystem snapshot available."
        repos = snap.get("repos", [])
        attention = sorted(
            [r for r in repos if isinstance(r, dict) and r.get("drift_score", 0) >= 60],
            key=lambda x: x.get("drift_score", 0),
            reverse=True,
        )
        lines = [
            f"Ecosystem: {len(repos)} repos | Generated: {snap.get('generated_at', 'unknown')}",
            f"Needs attention ({len(attention)}): "
            + ", ".join(r.get("name", "") for r in attention[:8]),
        ]
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

                    elif event_type == "RawContentBlockStopEvent":
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
