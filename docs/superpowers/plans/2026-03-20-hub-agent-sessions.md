# Hub Agent + Sessions Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a streaming ecosystem chat agent (side panel) and a Sessions tab (active Freshell/CLI sessions) to the ecosystem hub at port 8777.

**Architecture:**
- `chat_history.py` — JSONL-backed persistent conversation history
- `chat_agent.py` — direct Anthropic SDK agent with ecosystem tools + Freshell execution
- New API routes in `api.py`: SSE `/api/chat`, `/api/chat/history`, `/api/sessions`
- `dashboard.py` — Sessions tab + persistent right-side chat panel with SSE streaming

**Tech Stack:** Python stdlib HTTP server, anthropic SDK (streaming), Freshell HTTP API (port 3550), subprocess for wg/driftdriver commands

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `driftdriver/ecosystem_hub/chat_history.py` | Create | JSONL chat history persistence |
| `driftdriver/ecosystem_hub/chat_agent.py` | Create | Streaming agent, tools, ecosystem context |
| `driftdriver/ecosystem_hub/api.py` | Modify | Add /api/chat (SSE), /api/chat/history, /api/sessions |
| `driftdriver/ecosystem_hub/dashboard.py` | Modify | Sessions tab + chat side panel |
| `tests/test_chat_history.py` | Create | Unit tests for history module |
| `tests/test_chat_agent.py` | Create | Unit tests for agent + tools |
| `tests/test_chat_api.py` | Create | Integration tests for new API routes |

---

## Task 1: chat_history.py

**Files:**
- Create: `driftdriver/ecosystem_hub/chat_history.py`
- Create: `tests/test_chat_history.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_history.py
import pytest, json, pathlib
from driftdriver.ecosystem_hub.chat_history import ChatHistory

def test_append_and_load(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    h.append("hello", "hi there")
    turns = h.load()
    assert len(turns) == 1
    assert turns[0]["user"] == "hello"
    assert turns[0]["assistant"] == "hi there"
    assert "timestamp" in turns[0]

def test_load_empty(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    assert h.load() == []

def test_clear(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    h.append("a", "b")
    h.clear()
    assert h.load() == []

def test_limit(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    for i in range(10):
        h.append(f"u{i}", f"a{i}")
    turns = h.load(limit=3)
    assert len(turns) == 3
    assert turns[-1]["user"] == "u9"  # most recent last

def test_to_anthropic_messages(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    h.append("what is the status?", "here is the status...")
    msgs = h.to_anthropic_messages(limit=10)
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m pytest tests/test_chat_history.py -v 2>&1 | head -20
```

- [ ] **Step 3: Implement chat_history.py**

```python
# ABOUTME: Persistent JSONL-backed chat history for the ecosystem hub agent.
# ABOUTME: Append turns, load with limit, clear, convert to Anthropic message format.

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class ChatHistory:
    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, user: str, assistant: str) -> None:
        turn = {
            "timestamp": datetime.now(UTC).isoformat(),
            "user": user,
            "assistant": assistant,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as f:
            f.write(json.dumps(turn) + "\n")

    def load(self, limit: int = 50) -> list[dict]:
        if not self._path.exists():
            return []
        lines = self._path.read_text().splitlines()
        turns = []
        for line in lines:
            line = line.strip()
            if line:
                try:
                    turns.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return turns[-limit:]

    def clear(self) -> None:
        if self._path.exists():
            self._path.write_text("")

    def to_anthropic_messages(self, limit: int = 20) -> list[dict]:
        turns = self.load(limit=limit)
        msgs = []
        for t in turns:
            msgs.append({"role": "user", "content": t["user"]})
            msgs.append({"role": "assistant", "content": t["assistant"]})
        return msgs
```

- [ ] **Step 4: Run tests and verify pass**

```bash
python -m pytest tests/test_chat_history.py -v
```

- [ ] **Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/chat_history.py tests/test_chat_history.py
git commit -m "feat: add ChatHistory JSONL persistence for hub agent"
```

---

## Task 2: chat_agent.py

**Files:**
- Create: `driftdriver/ecosystem_hub/chat_agent.py`
- Create: `tests/test_chat_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_agent.py
import pytest, json
from unittest.mock import MagicMock, patch
from driftdriver.ecosystem_hub.chat_agent import EcosystemAgent, _run_subprocess

def test_run_subprocess_success(tmp_path):
    result = _run_subprocess(["echo", "hello"], cwd=str(tmp_path))
    assert result["ok"] is True
    assert "hello" in result["output"]

def test_run_subprocess_bad_cmd(tmp_path):
    result = _run_subprocess(["false"], cwd=str(tmp_path))
    assert result["ok"] is False

def test_run_subprocess_missing_dir():
    result = _run_subprocess(["echo", "x"], cwd="/nonexistent/path")
    assert result["ok"] is False

def test_agent_tool_list():
    agent = EcosystemAgent(snapshot_path=None, history_path=None)
    tools = agent.get_tool_definitions()
    names = [t["name"] for t in tools]
    assert "get_ecosystem_status" in names
    assert "get_repo_detail" in names
    assert "arm_repo" in names
    assert "disarm_repo" in names
    assert "run_attractor" in names
    assert "list_tasks" in names
    assert "create_task" in names
    assert "launch_freshell_session" in names
    assert "run_command" in names

def test_execute_get_ecosystem_status_no_snapshot():
    agent = EcosystemAgent(snapshot_path=None, history_path=None)
    result = agent.execute_tool("get_ecosystem_status", {})
    assert "error" in result or "repos" in result or "status" in result

def test_execute_unknown_tool():
    agent = EcosystemAgent(snapshot_path=None, history_path=None)
    result = agent.execute_tool("nonexistent_tool", {})
    assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_chat_agent.py -v 2>&1 | head -20
```

- [ ] **Step 3: Implement chat_agent.py**

```python
# ABOUTME: Streaming ecosystem agent for the hub. Uses Anthropic SDK with tool dispatch.
# ABOUTME: Tools: ecosystem status, repo detail, arm/disarm, attractor, wg tasks, Freshell sessions.

from __future__ import annotations

import json
import os
import subprocess
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
- speedriftd for repo mode control (observe → supervise → autonomous)
- Freshell at port 3550 for terminal sessions
"""


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
            "properties": {"repo_name": {"type": "string", "description": "Repo name as shown in the hub"}},
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
                "mode": {"type": "string", "enum": ["supervise", "autonomous"], "description": "supervise or autonomous"},
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
        "description": "Run the attractor convergence loop for a repo (diagnose → plan → execute → re-diagnose).",
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
                "status": {"type": "string", "description": "Filter: open, in-progress, done, all (default: open)"},
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


class EcosystemAgent:
    def __init__(
        self,
        snapshot_path: Path | None,
        history_path: Path | None,
        freshell_base: str = "http://localhost:3550",
    ) -> None:
        self._snapshot_path = snapshot_path
        self._history_path = history_path
        self._freshell_base = freshell_base
        self._client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict]:
        return TOOL_DEFINITIONS

    # ------------------------------------------------------------------
    # Tool execution
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
            return self._tool_create_task(inputs["repo_name"], inputs["title"], inputs.get("description", ""))
        if name == "launch_freshell_session":
            return self._tool_launch_freshell(inputs["repo_name"])
        if name == "run_command":
            return self._tool_run_command(inputs["repo_name"], inputs["command"])
        return {"error": f"unknown tool: {name}"}

    def _get_snapshot(self) -> dict:
        if not self._snapshot_path or not Path(self._snapshot_path).exists():
            return {}
        try:
            return json.loads(Path(self._snapshot_path).read_text())
        except Exception:
            return {}

    def _find_repo_path(self, repo_name: str) -> str | None:
        snap = self._get_snapshot()
        for r in snap.get("repos", []):
            if isinstance(r, dict) and r.get("name") == repo_name:
                return r.get("path") or r.get("root")
        return None

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
                return {k: v for k, v in r.items() if k not in ("_raw",)}
        return {"error": f"repo not found: {repo_name}"}

    def _tool_arm_repo(self, repo_name: str, mode: str, reason: str) -> dict:
        path = self._find_repo_path(repo_name)
        if not path:
            return {"error": f"repo path not found for {repo_name}"}
        result = _run_subprocess(
            ["driftdriver", "--dir", path, "speedriftd", "status",
             "--set-mode", mode, "--lease-owner", "ecosystem-agent", "--reason", reason],
            cwd=path,
        )
        return {"repo": repo_name, "mode": mode, **result}

    def _tool_disarm_repo(self, repo_name: str, reason: str) -> dict:
        path = self._find_repo_path(repo_name)
        if not path:
            return {"error": f"repo path not found for {repo_name}"}
        result = _run_subprocess(
            ["driftdriver", "--dir", path, "speedriftd", "status",
             "--set-mode", "observe", "--release-lease", "--reason", reason],
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
        import urllib.request
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
                url = f"{self._freshell_base}/session/{session_id}" if session_id else self._freshell_base
                return {"ok": True, "session_url": url, "session_id": session_id}
        except Exception as e:
            return {"ok": False, "error": f"Freshell unavailable: {e}", "freshell_url": self._freshell_base}

    def _tool_run_command(self, repo_name: str, command: str) -> dict:
        path = self._find_repo_path(repo_name)
        if not path:
            return {"error": f"repo path not found for {repo_name}"}
        result = _run_subprocess(["bash", "-c", command], cwd=path, timeout=60)
        return {"repo": repo_name, "command": command, **result}

    # ------------------------------------------------------------------
    # Streaming response
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
            f"Needs attention ({len(attention)}): " + ", ".join(r.get("name", "") for r in attention[:8]),
        ]
        return "\n".join(lines)

    def stream_response(
        self, message: str, history_messages: list[dict]
    ) -> Generator[str, None, str]:
        """Stream response chunks. Yields text chunks, handles tool calls inline.
        Returns final full response text."""
        context = self._build_context()
        system = f"{_SYSTEM_PROMPT}\n\n## Current Ecosystem State\n{context}"

        messages = history_messages + [{"role": "user", "content": message}]
        full_response = ""

        while True:
            with self._client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=system,
                messages=messages,
                tools=self.get_tool_definitions(),
            ) as stream:
                tool_calls = []
                current_tool: dict | None = None

                for event in stream:
                    event_type = type(event).__name__

                    if event_type == "RawContentBlockStartEvent":
                        block = event.content_block
                        if hasattr(block, "type") and block.type == "tool_use":
                            current_tool = {"id": block.id, "name": block.name, "input_json": ""}
                        elif hasattr(block, "type") and block.type == "text":
                            current_tool = None

                    elif event_type == "RawContentBlockDeltaEvent":
                        delta = event.delta
                        if hasattr(delta, "text"):
                            chunk = delta.text
                            full_response += chunk
                            yield chunk
                        elif hasattr(delta, "partial_json") and current_tool is not None:
                            current_tool["input_json"] += delta.partial_json

                    elif event_type == "RawContentBlockStopEvent":
                        if current_tool is not None:
                            tool_calls.append(current_tool)
                            current_tool = None

                final_msg = stream.get_final_message()

            if final_msg.stop_reason != "tool_use" or not tool_calls:
                break

            # Execute tool calls and continue
            tool_results = []
            for tc in tool_calls:
                try:
                    inputs = json.loads(tc["input_json"]) if tc["input_json"] else {}
                except json.JSONDecodeError:
                    inputs = {}
                result = self.execute_tool(tc["name"], inputs)
                result_text = json.dumps(result, default=str)
                yield f"\n[tool: {tc['name']}] "
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_text,
                })

            # Build tool_use content block for the assistant turn
            tool_use_blocks = []
            for tc in tool_calls:
                try:
                    inp = json.loads(tc["input_json"]) if tc["input_json"] else {}
                except json.JSONDecodeError:
                    inp = {}
                tool_use_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": inp,
                })

            messages = messages + [
                {"role": "assistant", "content": tool_use_blocks},
                {"role": "user", "content": tool_results},
            ]

        return full_response
```

- [ ] **Step 4: Run tests and verify pass**

```bash
python -m pytest tests/test_chat_agent.py -v
```

- [ ] **Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/chat_agent.py tests/test_chat_agent.py
git commit -m "feat: add EcosystemAgent streaming agent with ecosystem tools"
```

---

## Task 3: New API routes

**Files:**
- Modify: `driftdriver/ecosystem_hub/api.py`
- Create: `tests/test_chat_api.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_chat_api.py
import pytest, json
from unittest.mock import patch, MagicMock
from tests.test_repo_detail_smoke import HubTestServer  # reuse test server

def test_sessions_endpoint_freshell_down(hub_server):
    """When Freshell is down, /api/sessions returns empty list not error."""
    import urllib.request
    with urllib.request.urlopen(f"{hub_server}/api/sessions") as r:
        data = json.loads(r.read())
    assert "sessions" in data
    assert isinstance(data["sessions"], list)

def test_chat_history_empty(hub_server):
    import urllib.request
    with urllib.request.urlopen(f"{hub_server}/api/chat/history") as r:
        data = json.loads(r.read())
    assert "history" in data
    assert isinstance(data["history"], list)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_chat_api.py -v 2>&1 | head -20
```

- [ ] **Step 3: Add routes to api.py**

Find the section in `api.py` where routes are dispatched (the `do_POST` and `do_GET` methods). Add:

**In `do_GET`**, add before the final 404:
```python
if route == "/api/sessions":
    self._handle_get_sessions()
    return
if route == "/api/chat/history":
    self._handle_get_chat_history()
    return
```

**In `do_POST`**, add before the final 404:
```python
if route == "/api/chat":
    self._handle_post_chat()
    return
if route == "/api/chat/history/clear":
    self._handle_clear_chat_history()
    return
```

**New handler methods** (add to the handler class):

```python
def _get_chat_history_path(self) -> Path:
    from pathlib import Path
    service_dir = getattr(self.__class__, "service_dir", None)
    if service_dir:
        return Path(service_dir) / "chat_history.jsonl"
    return Path(".workgraph/service/ecosystem-hub/chat_history.jsonl")

def _handle_get_sessions(self) -> None:
    import urllib.request as urlreq
    freshell_base = "http://localhost:3550"
    try:
        with urlreq.urlopen(f"{freshell_base}/api/sessions", timeout=2) as resp:
            data = json.loads(resp.read())
            sessions = data if isinstance(data, list) else data.get("sessions", [])
    except Exception:
        sessions = []
    self._send_json({"sessions": sessions, "freshell_url": freshell_base})

def _handle_get_chat_history(self) -> None:
    from driftdriver.ecosystem_hub.chat_history import ChatHistory
    h = ChatHistory(self._get_chat_history_path())
    self._send_json({"history": h.load(limit=100)})

def _handle_clear_chat_history(self) -> None:
    from driftdriver.ecosystem_hub.chat_history import ChatHistory
    h = ChatHistory(self._get_chat_history_path())
    h.clear()
    self._send_json({"ok": True})

def _handle_post_chat(self) -> None:
    from driftdriver.ecosystem_hub.chat_agent import EcosystemAgent
    from driftdriver.ecosystem_hub.chat_history import ChatHistory

    length = int(self.headers.get("Content-Length", 0))
    body = self.rfile.read(length) if length else b"{}"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
        return

    message = str(data.get("message", "")).strip()
    if not message:
        self._send_json({"error": "empty message"}, status=HTTPStatus.BAD_REQUEST)
        return

    history_path = self._get_chat_history_path()
    history = ChatHistory(history_path)
    snapshot_path = getattr(self.__class__, "snapshot_path", None)

    agent = EcosystemAgent(snapshot_path=snapshot_path, history_path=history_path)
    history_messages = history.to_anthropic_messages(limit=20)

    # SSE response
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream")
    self.send_header("Cache-Control", "no-cache")
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()

    full_response = ""
    try:
        for chunk in agent.stream_response(message, history_messages):
            event_data = json.dumps({"type": "text", "text": chunk})
            self.wfile.write(f"data: {event_data}\n\n".encode())
            self.wfile.flush()
            full_response += chunk
    except Exception as e:
        err_data = json.dumps({"type": "error", "text": str(e)})
        self.wfile.write(f"data: {err_data}\n\n".encode())
        self.wfile.flush()
    finally:
        if full_response:
            history.append(message, full_response)
        done_data = json.dumps({"type": "done"})
        self.wfile.write(f"data: {done_data}\n\n".encode())
        self.wfile.flush()
```

- [ ] **Step 4: Run tests and verify pass**

```bash
python -m pytest tests/test_chat_api.py -v
```

- [ ] **Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/api.py tests/test_chat_api.py
git commit -m "feat: add /api/sessions, /api/chat (SSE), /api/chat/history routes"
```

---

## Task 4: Sessions tab in dashboard.py

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py`

- [ ] **Step 1: Add "Sessions" to the nav**

Find the nav tab buttons section (5 tabs: Operations, Intelligence, Conformance, Convergence, Factory). Add a sixth tab:

```html
<button class="tab-btn" data-tab="sessions" onclick="switchTab('sessions')">Sessions</button>
```

- [ ] **Step 2: Add sessions view div**

After the existing `view-*` divs, add:

```html
<div id="view-sessions" class="tab-view" style="display:none">
  <div class="section-card">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem">
      <h2 style="margin:0;font-size:1rem;font-weight:700;letter-spacing:0.04em">ACTIVE SESSIONS</h2>
      <button onclick="refreshSessions()" style="font-size:0.8rem;padding:0.25rem 0.75rem;border-radius:6px;border:1px solid var(--line);background:var(--panel);cursor:pointer">Refresh</button>
    </div>
    <div id="sessions-list"><p style="color:var(--muted);font-size:0.88rem">Loading sessions...</p></div>
    <p style="color:var(--muted);font-size:0.78rem;margin-top:0.75rem">Freshell: <a id="freshell-link" href="http://localhost:3550" target="_blank" style="color:var(--accent)">http://localhost:3550</a></p>
  </div>
</div>
```

- [ ] **Step 3: Add JS functions for sessions**

```javascript
function refreshSessions() {
  var container = el('sessions-list');
  if (!container) return;
  fetch('/api/sessions')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var sessions = data.sessions || [];
      if (sessions.length === 0) {
        container.innerHTML = '<p style="color:var(--muted);font-size:0.88rem">No active sessions. Launch one from a repo detail page.</p>';
        return;
      }
      var rows = sessions.map(function(s) {
        var url = s.url || (data.freshell_url + '/session/' + (s.id || s.session_id || ''));
        var repo = esc(s.repo || s.repo_name || 'unknown');
        var agent = esc(s.agent_type || 'claude-code');
        var started = s.started_at ? relativeTimeIso(s.started_at) : '—';
        var active = s.last_active_at ? relativeTimeIso(s.last_active_at) : '—';
        return '<tr>'
          + '<td style="font-weight:600">' + repo + '</td>'
          + '<td>' + agent + '</td>'
          + '<td>' + started + '</td>'
          + '<td>' + active + '</td>'
          + '<td><a href="' + esc(url) + '" target="_blank" style="color:var(--accent);font-weight:600">Open →</a></td>'
          + '</tr>';
      }).join('');
      container.innerHTML = '<table style="width:100%;border-collapse:collapse;font-size:0.85rem">'
        + '<thead><tr style="border-bottom:1px solid var(--line)">'
        + '<th style="text-align:left;padding:0.4rem 0.5rem;color:var(--muted)">Repo</th>'
        + '<th style="text-align:left;padding:0.4rem 0.5rem;color:var(--muted)">Agent</th>'
        + '<th style="text-align:left;padding:0.4rem 0.5rem;color:var(--muted)">Started</th>'
        + '<th style="text-align:left;padding:0.4rem 0.5rem;color:var(--muted)">Last Active</th>'
        + '<th style="text-align:left;padding:0.4rem 0.5rem;color:var(--muted)">Open</th>'
        + '</tr></thead><tbody>' + rows + '</tbody></table>';
    })
    .catch(function() {
      container.innerHTML = '<p style="color:var(--muted);font-size:0.88rem">Could not reach sessions API.</p>';
    });
}

// Auto-refresh sessions tab every 15s when visible
setInterval(function() {
  if (currentTab === 'sessions') refreshSessions();
}, 15000);
```

- [ ] **Step 4: Hook into tab switching**

In the `switchTab` function, add:
```javascript
if (tab === 'sessions') refreshSessions();
```

- [ ] **Step 5: Restart hub, verify Sessions tab renders**

```bash
lsof -ti:8777 | xargs kill -9 2>/dev/null; sleep 1
scripts/ecosystem_hub_daemon.sh start
sleep 3
curl -s http://127.0.0.1:8777/api/sessions | python3 -m json.tool
```

- [ ] **Step 6: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat: add Sessions tab to hub dashboard with Freshell session list"
```

---

## Task 5: Chat side panel in dashboard.py

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py`

- [ ] **Step 1: Add CSS for the chat panel**

Add to the `<style>` block:

```css
#chat-panel {
  position: fixed; top: 0; right: -420px; width: 420px; height: 100vh;
  background: var(--panel); border-left: 1px solid var(--line);
  display: flex; flex-direction: column; z-index: 1000;
  transition: right 0.25s ease; box-shadow: -4px 0 20px rgba(0,0,0,0.08);
}
#chat-panel.open { right: 0; }
#chat-toggle {
  position: fixed; right: 1rem; bottom: 1.5rem; z-index: 1001;
  background: var(--accent); color: #fff; border: none; border-radius: 24px;
  padding: 0.6rem 1.2rem; font-size: 0.85rem; font-weight: 700;
  cursor: pointer; box-shadow: 0 2px 12px rgba(0,0,0,0.18);
  letter-spacing: 0.03em;
}
#chat-toggle:hover { opacity: 0.9; }
#chat-messages {
  flex: 1; overflow-y: auto; padding: 1rem;
  display: flex; flex-direction: column; gap: 0.75rem;
}
.chat-msg-user {
  align-self: flex-end; background: var(--accent); color: #fff;
  border-radius: 12px 12px 2px 12px; padding: 0.5rem 0.85rem;
  max-width: 85%; font-size: 0.875rem; line-height: 1.45;
}
.chat-msg-agent {
  align-self: flex-start; background: var(--bg); border: 1px solid var(--line);
  border-radius: 12px 12px 12px 2px; padding: 0.5rem 0.85rem;
  max-width: 92%; font-size: 0.875rem; line-height: 1.5; white-space: pre-wrap;
}
.chat-msg-agent.streaming { border-color: var(--accent-soft); }
#chat-input-row {
  display: flex; gap: 0.5rem; padding: 0.75rem;
  border-top: 1px solid var(--line);
}
#chat-input {
  flex: 1; border: 1px solid var(--line); border-radius: 8px;
  padding: 0.5rem 0.75rem; font-size: 0.875rem; resize: none;
  background: var(--bg); color: var(--ink); font-family: inherit;
  min-height: 40px; max-height: 120px;
}
#chat-input:focus { outline: none; border-color: var(--accent); }
#chat-send {
  background: var(--accent); color: #fff; border: none; border-radius: 8px;
  padding: 0.5rem 1rem; font-weight: 700; cursor: pointer; font-size: 0.85rem;
}
#chat-send:disabled { opacity: 0.5; cursor: default; }
#chat-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.85rem 1rem; border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}
```

- [ ] **Step 2: Add chat panel HTML**

Add before the closing `</body>` tag:

```html
<!-- Chat side panel -->
<div id="chat-panel">
  <div id="chat-header">
    <span style="font-weight:700;font-size:0.9rem;letter-spacing:0.04em">ECOSYSTEM AGENT</span>
    <div style="display:flex;gap:0.5rem;align-items:center">
      <button onclick="clearChatHistory()" style="font-size:0.75rem;color:var(--muted);background:none;border:none;cursor:pointer;padding:0.2rem 0.4rem">Clear</button>
      <button onclick="toggleChat()" style="background:none;border:none;cursor:pointer;font-size:1.2rem;color:var(--muted);line-height:1">&#x2715;</button>
    </div>
  </div>
  <div id="chat-messages"></div>
  <div id="chat-input-row">
    <textarea id="chat-input" rows="1" placeholder="Ask the agent anything or give it work to do..."></textarea>
    <button id="chat-send" onclick="sendChatMessage()">Send</button>
  </div>
</div>
<button id="chat-toggle" onclick="toggleChat()">Agent &#x25BA;</button>
```

- [ ] **Step 3: Add JS for chat panel**

```javascript
var chatOpen = false;
var chatStreaming = false;

function toggleChat() {
  chatOpen = !chatOpen;
  var panel = document.getElementById('chat-panel');
  var toggle = document.getElementById('chat-toggle');
  if (chatOpen) {
    panel.classList.add('open');
    toggle.textContent = 'Agent \u25C4';
    if (document.getElementById('chat-messages').children.length === 0) {
      loadChatHistory();
    }
  } else {
    panel.classList.remove('open');
    toggle.textContent = 'Agent \u25BA';
  }
}

function loadChatHistory() {
  fetch('/api/chat/history')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var history = data.history || [];
      var container = document.getElementById('chat-messages');
      container.innerHTML = '';
      history.forEach(function(turn) {
        appendChatMsg(turn.user, 'user');
        appendChatMsg(turn.assistant, 'agent');
      });
      scrollChatToBottom();
    })
    .catch(function() {});
}

function appendChatMsg(text, role) {
  var container = document.getElementById('chat-messages');
  var div = document.createElement('div');
  div.className = role === 'user' ? 'chat-msg-user' : 'chat-msg-agent';
  div.textContent = text;
  container.appendChild(div);
  return div;
}

function scrollChatToBottom() {
  var container = document.getElementById('chat-messages');
  container.scrollTop = container.scrollHeight;
}

function clearChatHistory() {
  fetch('/api/chat/history/clear', {method: 'POST'})
    .then(function() {
      document.getElementById('chat-messages').innerHTML = '';
    });
}

function sendChatMessage() {
  if (chatStreaming) return;
  var input = document.getElementById('chat-input');
  var message = input.value.trim();
  if (!message) return;

  input.value = '';
  input.style.height = 'auto';
  appendChatMsg(message, 'user');
  scrollChatToBottom();

  chatStreaming = true;
  document.getElementById('chat-send').disabled = true;

  var agentDiv = appendChatMsg('', 'agent');
  agentDiv.classList.add('streaming');
  scrollChatToBottom();

  var es = new EventSource('/api/chat');

  // POST via fetch, but SSE needs EventSource — use a workaround:
  // POST first, then EventSource reads from a session token
  // Simpler: use fetch with streaming body reader
  agentDiv.textContent = '';

  fetch('/api/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: message}),
  }).then(function(response) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';

    function pump() {
      return reader.read().then(function(result) {
        if (result.done) {
          chatStreaming = false;
          document.getElementById('chat-send').disabled = false;
          agentDiv.classList.remove('streaming');
          scrollChatToBottom();
          return;
        }
        buffer += decoder.decode(result.value, {stream: true});
        var lines = buffer.split('\n');
        buffer = lines.pop();
        lines.forEach(function(line) {
          if (!line.startsWith('data: ')) return;
          try {
            var evt = JSON.parse(line.slice(6));
            if (evt.type === 'text') {
              agentDiv.textContent += evt.text;
              scrollChatToBottom();
            }
          } catch(e) {}
        });
        return pump();
      });
    }
    return pump();
  }).catch(function(e) {
    agentDiv.textContent = 'Error: ' + e.message;
    chatStreaming = false;
    document.getElementById('chat-send').disabled = false;
    agentDiv.classList.remove('streaming');
  });
}

// Enter to send (Shift+Enter for newline)
document.addEventListener('DOMContentLoaded', function() {
  var input = document.getElementById('chat-input');
  if (input) {
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChatMessage();
      }
    });
    // Auto-resize textarea
    input.addEventListener('input', function() {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
  }
});
```

- [ ] **Step 4: Restart hub and verify panel**

```bash
lsof -ti:8777 | xargs kill -9 2>/dev/null; sleep 1
scripts/ecosystem_hub_daemon.sh start
sleep 3
```

Open `http://127.0.0.1:8777/` and verify:
- "Agent ▶" button appears bottom-right
- Clicking opens the panel from the right
- Sessions tab exists in nav
- Chat history loads on open

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat: add streaming chat side panel and Sessions tab to hub"
```

---

## Completion

After all tasks:
- Hub at `http://127.0.0.1:8777/` has a Sessions tab and chat panel
- Agent streams responses, executes tools (arm repo, run attractor, create tasks, Freshell sessions)
- Chat history persists in `.workgraph/service/ecosystem-hub/chat_history.jsonl`
- Sessions from the agent appear in the Sessions tab
