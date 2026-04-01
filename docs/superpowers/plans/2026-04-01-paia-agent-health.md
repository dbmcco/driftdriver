# PAIA Agent Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a retrospective self-healing lane to the Factory Brain that detects PAIA agent quality failures, auto-applies small fixes, and proposes large fixes via Dark Factory Telegram.

**Architecture:** New `paia_agent_health/` module in driftdriver collects signals from paia-memory/paia-events/paia-shell/workgraph, runs two-pass LLM analysis (Haiku pattern detection → Sonnet fix design), and integrates into the existing Factory Brain router tick. Approval flow reuses the existing `create_decision` / `extract_decision_id` Telegram chat pattern.

**Tech Stack:** Python 3.12, httpx (async HTTP), driftdriver factory_brain infrastructure, Claude CLI (Haiku + Sonnet via subprocess), existing `send_telegram()` and `handle_chat_message()` from `factory_brain/`

---

## File Map

**New files (driftdriver):**
- `driftdriver/paia_agent_health/__init__.py` — empty package marker
- `driftdriver/paia_agent_health/collector.py` — `SignalBundle`, `AgentSignals`, `collect_signals()`
- `driftdriver/paia_agent_health/analyzer.py` — `Finding`, `FixProposal`, `run_analysis()`
- `driftdriver/paia_agent_health/fixes.py` — `apply_fix()`, `send_proposal()`, `handle_agent_fix_decision()`
- `driftdriver/paia_agent_health/fix_history.py` — `FixRecord`, load/save/add/update JSON
- `tests/test_paia_agent_health_collector.py`
- `tests/test_paia_agent_health_analyzer.py`
- `tests/test_paia_agent_health_fixes.py`
- `tests/test_paia_agent_health_fix_history.py`

**Modified files (driftdriver):**
- `driftdriver/factory_brain/directives.py:14-32` — add 3 new actions to `DIRECTIVE_SCHEMA`, add `restart_paia_service` to `DESTRUCTIVE_ACTIONS`, add executor branches
- `driftdriver/factory_brain/router.py:23-31` — add `last_agent_health_check` to `BrainState`; add health tick lane to `run_brain_tick()`
- `driftdriver/ecosystem_hub/api.py:546-558` — add `decision_route` hook for agent fix approvals

**Modified files (paia-agent-runtime):**
- `paia_agent_runtime/tools/__init__.py:198-232` — fire-and-forget `agent.tool.called` event after each tool execution

---

## Task 1: Fix History Module

**Files:**
- Create: `driftdriver/driftdriver/paia_agent_health/__init__.py`
- Create: `driftdriver/driftdriver/paia_agent_health/fix_history.py`
- Create: `driftdriver/tests/test_paia_agent_health_fix_history.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paia_agent_health_fix_history.py
# ABOUTME: Tests for FixRecord persistence, add, update, and due-check logic.

from __future__ import annotations
import json
import tempfile
from pathlib import Path
from driftdriver.paia_agent_health.fix_history import (
    FixRecord, add_fix, update_outcome, load_history, save_history, pending_checks
)

def test_add_and_load_roundtrip():
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        path = Path(f.name)
        record = FixRecord(
            fix_id="abc123",
            applied_at="2026-04-01T03:00:00+00:00",
            agent="samantha",
            component="skills/scheduling_tactics.md",
            finding_pattern="tool_failure",
            change_summary="Added retry logic",
            diff="--- a\n+++ b\n@@ -1 +1,2 @@\n line\n+retry",
            auto_applied=True,
            check_after="2026-04-08T03:00:00+00:00",
            outcome=None,
        )
        add_fix(path, record)
        history = load_history(path)
        assert len(history) == 1
        assert history[0].fix_id == "abc123"
        assert history[0].outcome is None

def test_update_outcome():
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        path = Path(f.name)
        record = FixRecord(
            fix_id="xyz789",
            applied_at="2026-04-01T03:00:00+00:00",
            agent="caroline",
            component="skills/outreach_templates.md",
            finding_pattern="behavioral_loop",
            change_summary="Fallback search protocol",
            diff="+fallback",
            auto_applied=False,
            check_after="2026-04-08T03:00:00+00:00",
            outcome=None,
        )
        add_fix(path, record)
        update_outcome(path, "xyz789", "resolved")
        history = load_history(path)
        assert history[0].outcome == "resolved"

def test_pending_checks_returns_due_records():
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        path = Path(f.name)
        due = FixRecord(
            fix_id="due1",
            applied_at="2026-03-01T00:00:00+00:00",
            agent="samantha",
            component="skills/scheduling_tactics.md",
            finding_pattern="tool_failure",
            change_summary="old fix",
            diff="+x",
            auto_applied=True,
            check_after="2026-03-08T00:00:00+00:00",  # past
            outcome=None,
        )
        not_due = FixRecord(
            fix_id="notdue1",
            applied_at="2026-04-01T00:00:00+00:00",
            agent="derek",
            component="skills/architecture_patterns.md",
            finding_pattern="task_stall",
            change_summary="future fix",
            diff="+y",
            auto_applied=True,
            check_after="2099-01-01T00:00:00+00:00",  # future
            outcome=None,
        )
        add_fix(path, due)
        add_fix(path, not_due)
        due_records = pending_checks(path)
        assert len(due_records) == 1
        assert due_records[0].fix_id == "due1"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_paia_agent_health_fix_history.py -v
```
Expected: `ModuleNotFoundError: No module named 'driftdriver.paia_agent_health'`

- [ ] **Step 3: Create package marker**

```python
# driftdriver/paia_agent_health/__init__.py
# ABOUTME: PAIA agent health self-healing lane for the Factory Brain.
# ABOUTME: Collects agent quality signals, analyzes patterns, proposes and applies fixes.
```

- [ ] **Step 4: Implement fix_history.py**

```python
# driftdriver/paia_agent_health/fix_history.py
# ABOUTME: FixRecord persistence — stores applied and proposed fixes with 7-day outcome tracking.
# ABOUTME: Persists to ~/.config/workgraph/agent_health_fixes.json.

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "workgraph" / "agent_health_fixes.json"


@dataclass
class FixRecord:
    fix_id: str
    applied_at: str           # ISO 8601
    agent: str
    component: str            # e.g. "skills/outreach_templates.md"
    finding_pattern: str      # e.g. "tool_failure"
    change_summary: str
    diff: str
    auto_applied: bool
    check_after: str          # ISO 8601, applied_at + 7 days
    outcome: str | None       # None | "resolved" | "persists" | "unknown"


def load_history(path: Path = DEFAULT_PATH) -> list[FixRecord]:
    """Load all fix records from disk. Returns empty list if file missing."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [FixRecord(**r) for r in data]
    except (json.JSONDecodeError, TypeError, KeyError):
        return []


def save_history(records: list[FixRecord], path: Path = DEFAULT_PATH) -> None:
    """Write all fix records to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(r) for r in records], indent=2))


def add_fix(path: Path, record: FixRecord) -> None:
    """Append a new fix record. Does not deduplicate."""
    records = load_history(path)
    records.append(record)
    save_history(records, path)


def update_outcome(path: Path, fix_id: str, outcome: str) -> None:
    """Set the outcome field on the record with the given fix_id."""
    records = load_history(path)
    for r in records:
        if r.fix_id == fix_id:
            r.outcome = outcome
    save_history(records, path)


def pending_checks(path: Path = DEFAULT_PATH) -> list[FixRecord]:
    """Return records where check_after has passed and outcome is still None."""
    now = datetime.now(timezone.utc)
    due: list[FixRecord] = []
    for r in load_history(path):
        if r.outcome is not None:
            continue
        try:
            check_dt = datetime.fromisoformat(r.check_after)
            if check_dt.tzinfo is None:
                check_dt = check_dt.replace(tzinfo=timezone.utc)
            if now >= check_dt:
                due.append(r)
        except ValueError:
            continue
    return due


def is_duplicate_pending(
    path: Path,
    agent: str,
    component: str,
    pattern: str,
    max_age_hours: int = 48,
) -> bool:
    """True if a pending fix for (agent, component, pattern) exists within max_age_hours."""
    now = datetime.now(timezone.utc)
    for r in load_history(path):
        if r.outcome is not None:
            continue
        if r.agent != agent or r.component != component or r.finding_pattern != pattern:
            continue
        try:
            applied = datetime.fromisoformat(r.applied_at)
            if applied.tzinfo is None:
                applied = applied.replace(tzinfo=timezone.utc)
            age_hours = (now - applied).total_seconds() / 3600
            if age_hours < max_age_hours:
                return True
        except ValueError:
            continue
    return False
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_paia_agent_health_fix_history.py -v
```
Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add driftdriver/paia_agent_health/__init__.py driftdriver/paia_agent_health/fix_history.py tests/test_paia_agent_health_fix_history.py
git commit -m "feat: add paia_agent_health fix_history module"
```

---

## Task 2: Signal Collector

**Files:**
- Create: `driftdriver/driftdriver/paia_agent_health/collector.py`
- Create: `driftdriver/tests/test_paia_agent_health_collector.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paia_agent_health_collector.py
# ABOUTME: Tests for SignalBundle collection — mocks all HTTP endpoints.

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from driftdriver.paia_agent_health.collector import (
    AgentSignals, SignalBundle, AGENT_CONFIGS, collect_signals
)


def test_agent_configs_covers_all_agents():
    names = {a["name"] for a in AGENT_CONFIGS}
    assert names == {"samantha", "derek", "ingrid", "caroline"}


def test_agent_configs_tenant_ids():
    # Tenant IDs are capitalized per paia-memory convention
    tenants = {a["name"]: a["tenant_id"] for a in AGENT_CONFIGS}
    assert tenants["samantha"] == "Samantha"
    assert tenants["caroline"] == "Caroline"


@pytest.mark.asyncio
async def test_collect_signals_returns_bundle_with_all_agents():
    mock_atoms_response = {"atoms": [{"content": "user said: that didn't work", "event_type": "conversation.turn"}]}
    mock_events_response = {"events": [{"event_type": "agent.task.failed", "data": {"agent": "samantha"}}]}
    mock_shell_response = {"metrics": []}

    async def mock_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "retrieve/atoms" in url:
            resp.json.return_value = mock_atoms_response
        elif "v1/events" in url:
            resp.json.return_value = mock_events_response
        elif "health/metrics" in url:
            resp.json.return_value = mock_shell_response
        return resp

    with patch("driftdriver.paia_agent_health.collector.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("driftdriver.paia_agent_health.collector._run_wg_list", return_value=[]):
            bundle = await collect_signals()

    assert isinstance(bundle, SignalBundle)
    assert set(bundle.agents.keys()) == {"samantha", "derek", "ingrid", "caroline"}
    assert bundle.collected_at is not None


@pytest.mark.asyncio
async def test_collect_signals_tolerates_http_failure():
    """Partial failures should not prevent bundle creation."""
    async def mock_get_fail(url, **kwargs):
        raise Exception("connection refused")

    with patch("driftdriver.paia_agent_health.collector.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.get = mock_get_fail
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("driftdriver.paia_agent_health.collector._run_wg_list", return_value=[]):
            bundle = await collect_signals()

    # Bundle still created, agents present but with empty signals
    assert set(bundle.agents.keys()) == {"samantha", "derek", "ingrid", "caroline"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_paia_agent_health_collector.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement collector.py**

```python
# driftdriver/paia_agent_health/collector.py
# ABOUTME: Collects quality signals from paia-memory, paia-events, paia-shell, and workgraph.
# ABOUTME: Returns a SignalBundle used by the analyzer. Stateless — tolerates partial failures.

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

MEMORY_URL = os.environ.get("PAIA_MEMORY_URL", "http://localhost:3510")
EVENTS_URL = os.environ.get("PAIA_EVENTS_URL", "http://localhost:3511")
SHELL_URL = os.environ.get("PAIA_SHELL_URL", "http://localhost:3540")

AGENT_CONFIGS = [
    {"name": "samantha", "tenant_id": "Samantha", "repo": "samantha"},
    {"name": "derek",    "tenant_id": "Derek",    "repo": "derek"},
    {"name": "ingrid",   "tenant_id": "Ingrid",   "repo": "ingrid"},
    {"name": "caroline", "tenant_id": "Caroline", "repo": "caroline"},
]

EXPERIMENTS = os.environ.get("EXPERIMENTS_DIR", os.path.expanduser("~/projects/experiments"))


@dataclass
class AgentSignals:
    name: str
    tenant_id: str
    conversation_turns: list[dict] = field(default_factory=list)
    session_extracts: list[dict] = field(default_factory=list)
    task_events: list[dict] = field(default_factory=list)
    tool_events: list[dict] = field(default_factory=list)
    failed_tasks: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class SignalBundle:
    agents: dict[str, AgentSignals]
    shell_metrics: list[dict]
    collected_at: str


async def _fetch_atoms(client: httpx.AsyncClient, tenant_id: str, event_type: str, limit: int = 50) -> list[dict]:
    url = f"{MEMORY_URL}/retrieve/atoms"
    params = {"tenant_id": tenant_id, "event_type": event_type, "limit": limit}
    resp = await client.get(url, params=params, timeout=10)
    return resp.json().get("atoms", [])


async def _fetch_events(client: httpx.AsyncClient, prefix: str, limit: int = 200) -> list[dict]:
    url = f"{EVENTS_URL}/v1/events"
    params = {"event_type_prefix": prefix, "limit": limit}
    resp = await client.get(url, params=params, timeout=10)
    return resp.json().get("events", [])


def _run_wg_list(repo: str, status: str) -> list[dict]:
    """Run wg list --status <status> --json in the agent repo. Returns empty list on failure."""
    repo_path = os.path.join(EXPERIMENTS, repo)
    try:
        result = subprocess.run(
            ["wg", "list", "--status", status, "--json"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            return json.loads(result.stdout)
        return []
    except Exception as exc:
        logger.debug("wg list failed for %s/%s: %s", repo, status, exc)
        return []


async def _collect_agent(client: httpx.AsyncClient, cfg: dict) -> AgentSignals:
    signals = AgentSignals(name=cfg["name"], tenant_id=cfg["tenant_id"])
    try:
        signals.conversation_turns = await _fetch_atoms(client, cfg["tenant_id"], "conversation.turn")
    except Exception as exc:
        signals.errors.append(f"conversation_turns: {exc}")

    try:
        signals.session_extracts = await _fetch_atoms(client, cfg["tenant_id"], "session.extract", limit=10)
    except Exception as exc:
        signals.errors.append(f"session_extracts: {exc}")

    try:
        all_task_events = await _fetch_events(client, "agent.task")
        agent_name = cfg["name"]
        signals.task_events = [
            e for e in all_task_events
            if e.get("data", {}).get("agent") == agent_name
            or agent_name in str(e.get("data", {}))
        ]
    except Exception as exc:
        signals.errors.append(f"task_events: {exc}")

    try:
        all_tool_events = await _fetch_events(client, "agent.tool.called")
        agent_name = cfg["name"]
        signals.tool_events = [
            e for e in all_tool_events
            if e.get("data", {}).get("agent") == agent_name
        ]
    except Exception as exc:
        signals.errors.append(f"tool_events: {exc}")

    signals.failed_tasks = _run_wg_list(cfg["repo"], "failed")
    return signals


async def collect_signals(window_hours: int = 24) -> SignalBundle:
    """Collect all agent quality signals. Tolerates partial failures."""
    agents: dict[str, AgentSignals] = {}
    shell_metrics: list[dict] = []

    async with httpx.AsyncClient() as client:
        for cfg in AGENT_CONFIGS:
            agents[cfg["name"]] = await _collect_agent(client, cfg)

        try:
            resp = await client.get(f"{SHELL_URL}/api/health/metrics", timeout=10)
            shell_metrics = resp.json() if isinstance(resp.json(), list) else []
        except Exception as exc:
            logger.debug("shell metrics fetch failed: %s", exc)

    return SignalBundle(
        agents=agents,
        shell_metrics=shell_metrics,
        collected_at=datetime.now(timezone.utc).isoformat(),
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_paia_agent_health_collector.py -v
```
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add driftdriver/paia_agent_health/collector.py tests/test_paia_agent_health_collector.py
git commit -m "feat: add paia_agent_health signal collector"
```

---

## Task 3: Analyzer

**Files:**
- Create: `driftdriver/driftdriver/paia_agent_health/analyzer.py`
- Create: `driftdriver/tests/test_paia_agent_health_analyzer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paia_agent_health_analyzer.py
# ABOUTME: Tests for two-pass LLM analysis — monkeypatches subprocess to avoid real Claude calls.

from __future__ import annotations

import json
from unittest.mock import patch
from driftdriver.paia_agent_health.collector import AgentSignals, SignalBundle
from driftdriver.paia_agent_health.analyzer import Finding, FixProposal, run_analysis


def _make_bundle() -> SignalBundle:
    signals = AgentSignals(
        name="caroline",
        tenant_id="Caroline",
        conversation_turns=[
            {"content": "user: that search didn't work again"},
            {"content": "user: you missed the contact lookup"},
            {"content": "user: try again, same issue"},
        ],
        tool_events=[
            {"data": {"tool": "load_skill", "success": False, "error": "search failed"}} for _ in range(4)
        ],
    )
    return SignalBundle(
        agents={"caroline": signals, "samantha": AgentSignals(name="samantha", tenant_id="Samantha"),
                "derek": AgentSignals(name="derek", tenant_id="Derek"),
                "ingrid": AgentSignals(name="ingrid", tenant_id="Ingrid")},
        shell_metrics=[],
        collected_at="2026-04-01T03:00:00+00:00",
    )


def _pass1_output() -> dict:
    return {
        "findings": [{
            "agent": "caroline",
            "pattern_type": "tool_failure",
            "evidence": ["user: that search didn't work again", "user: you missed the contact lookup"],
            "evidence_count": 4,
            "affected_component": "skills/outreach_templates.md",
            "severity": "high",
            "confidence": 0.85,
        }]
    }


def _pass2_output() -> dict:
    return {
        "change_summary": "Add fallback search protocol to outreach_templates.md",
        "diff": "--- a/outreach_templates.md\n+++ b/outreach_templates.md\n@@ -10 +10,3 @@\n+## Fallback\n+Retry with last name only.",
        "auto_apply": False,
        "risk": "low",
    }


def _mock_run(pass1_out, pass2_out):
    call_count = [0]
    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        out = pass1_out if call_count[0] == 1 else pass2_out
        return type("R", (), {"returncode": 0, "stdout": json.dumps({"structured_output": out}), "stderr": ""})()
    return fake_run


def test_run_analysis_returns_proposals():
    bundle = _make_bundle()
    with patch("subprocess.run", side_effect=_mock_run(_pass1_output(), _pass2_output())):
        proposals = run_analysis(bundle)
    assert len(proposals) == 1
    p = proposals[0]
    assert isinstance(p, FixProposal)
    assert p.finding.agent == "caroline"
    assert p.auto_apply is False
    assert "Fallback" in p.diff


def test_low_confidence_findings_are_filtered():
    bundle = _make_bundle()
    low_conf = {"findings": [{"agent": "caroline", "pattern_type": "tool_failure",
                               "evidence": ["x"], "evidence_count": 1,
                               "affected_component": "skills/outreach_templates.md",
                               "severity": "low", "confidence": 0.3}]}
    with patch("subprocess.run", side_effect=_mock_run(low_conf, _pass2_output())):
        proposals = run_analysis(bundle)
    assert proposals == []


def test_empty_bundle_returns_no_proposals():
    empty = SignalBundle(
        agents={"caroline": AgentSignals(name="caroline", tenant_id="Caroline")},
        shell_metrics=[],
        collected_at="2026-04-01T00:00:00+00:00",
    )
    with patch("subprocess.run", side_effect=_mock_run({"findings": []}, {})):
        proposals = run_analysis(empty)
    assert proposals == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_paia_agent_health_analyzer.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement analyzer.py**

```python
# driftdriver/paia_agent_health/analyzer.py
# ABOUTME: Two-pass LLM analysis — Haiku detects patterns, Sonnet designs fixes.
# ABOUTME: Uses subprocess Claude CLI directly (same pattern as factory_brain/chat.py).

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from driftdriver.paia_agent_health.collector import SignalBundle

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6
SMALL_FIX_MAX_LINES = 20
SMALL_FIX_MIN_EVIDENCE = 3

_EXPERIMENTS = os.environ.get("EXPERIMENTS_DIR", os.path.expanduser("~/projects/experiments"))

_STRIPPED_ENV = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    path = env.get("PATH", "")
    for extra in [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin"]:
        if extra not in path:
            path = f"{extra}:{path}"
    env["PATH"] = path
    return env


@dataclass
class Finding:
    agent: str
    pattern_type: str       # "tool_failure" | "behavioral_loop" | "task_stall" | "conversation_correction"
    evidence: list[str]
    evidence_count: int
    affected_component: str # skill file path, tool name, or config key
    severity: str           # "low" | "medium" | "high"
    confidence: float       # 0.0–1.0


@dataclass
class FixProposal:
    finding: Finding
    change_summary: str
    diff: str
    auto_apply: bool
    risk: str


_PASS1_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["agent", "pattern_type", "evidence", "evidence_count",
                             "affected_component", "severity", "confidence"],
                "properties": {
                    "agent": {"type": "string"},
                    "pattern_type": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "evidence_count": {"type": "integer"},
                    "affected_component": {"type": "string"},
                    "severity": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        }
    },
}

_PASS2_SCHEMA = {
    "type": "object",
    "required": ["change_summary", "diff", "auto_apply", "risk"],
    "properties": {
        "change_summary": {"type": "string"},
        "diff": {"type": "string"},
        "auto_apply": {"type": "boolean"},
        "risk": {"type": "string"},
    },
}


def _invoke_claude(prompt: str, schema: dict, model: str) -> dict:
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--max-budget-usd", "1.00",
    ]
    result = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, timeout=120, env=_clean_env()
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exit {result.returncode}: {result.stderr[:200]}")
    cli_out = json.loads(result.stdout)
    return cli_out.get("structured_output") or {}


def _bundle_to_context(bundle: SignalBundle) -> str:
    lines: list[str] = ["## Agent Signal Bundle\n"]
    for name, signals in bundle.agents.items():
        lines.append(f"### Agent: {name}")
        if signals.conversation_turns:
            lines.append("Conversation turns (recent):")
            for t in signals.conversation_turns[:20]:
                lines.append(f"  - {str(t.get('content', ''))[:200]}")
        if signals.tool_events:
            failures = [e for e in signals.tool_events if not e.get("data", {}).get("success", True)]
            lines.append(f"Tool call failures (last 24h): {len(failures)}")
            for e in failures[:5]:
                d = e.get("data", {})
                lines.append(f"  - {d.get('tool')}.{d.get('action')}: {d.get('error')}")
        if signals.task_events:
            failed = [e for e in signals.task_events if "failed" in e.get("event_type", "")]
            lines.append(f"Task failures: {len(failed)}")
        if signals.errors:
            lines.append(f"Collection errors: {signals.errors}")
        lines.append("")
    return "\n".join(lines)


def _read_component(component: str) -> str:
    """Read the current content of a skill file or config. Returns empty string if missing."""
    paths_to_try = [
        Path(_EXPERIMENTS) / component,
    ]
    # Also try each agent's skills dir
    for agent in ("samantha", "derek", "ingrid", "caroline"):
        paths_to_try.append(Path(_EXPERIMENTS) / agent / component)
        paths_to_try.append(Path(_EXPERIMENTS) / agent / "skills" / Path(component).name)
    for p in paths_to_try:
        if p.exists():
            return p.read_text()[:3000]
    return ""


def _is_small_fix(proposal_raw: dict, finding: Finding) -> bool:
    """True if the fix meets all auto-apply criteria."""
    diff = proposal_raw.get("diff", "")
    added_lines = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed_lines = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    changed_lines = added_lines + removed_lines
    if changed_lines > SMALL_FIX_MAX_LINES:
        return False
    if finding.evidence_count < SMALL_FIX_MIN_EVIDENCE:
        return False
    if proposal_raw.get("risk", "low") not in ("low",):
        return False
    return True


def run_analysis(bundle: SignalBundle) -> list[FixProposal]:
    """Run two-pass analysis on a SignalBundle. Returns list of FixProposals."""
    context = _bundle_to_context(bundle)

    # Pass 1: Haiku — pattern detection
    pass1_prompt = (
        "You are an agent quality analyst. Review these agent signal bundles and identify "
        "recurring failure patterns — tool failures, behavioral loops, task stalls, and "
        "explicit user corrections. Only report patterns with clear evidence. Be conservative.\n\n"
        f"{context}"
    )
    try:
        pass1_raw = _invoke_claude(pass1_prompt, _PASS1_SCHEMA, "claude-haiku-4-5-20251001")
    except Exception as exc:
        logger.warning("paia_agent_health.pass1_failed: %s", exc)
        return []

    findings: list[Finding] = []
    for raw in pass1_raw.get("findings", []):
        try:
            f = Finding(
                agent=raw["agent"],
                pattern_type=raw["pattern_type"],
                evidence=raw["evidence"],
                evidence_count=raw["evidence_count"],
                affected_component=raw["affected_component"],
                severity=raw["severity"],
                confidence=float(raw["confidence"]),
            )
            if f.confidence >= CONFIDENCE_THRESHOLD:
                findings.append(f)
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("malformed finding: %s", exc)

    if not findings:
        return []

    # Pass 2: Sonnet — fix design per finding
    proposals: list[FixProposal] = []
    for finding in findings:
        current_content = _read_component(finding.affected_component)
        pass2_prompt = (
            f"You are a PAIA agent improvement engineer. Design a specific fix for this finding.\n\n"
            f"Agent: {finding.agent}\n"
            f"Pattern: {finding.pattern_type}\n"
            f"Component: {finding.affected_component}\n"
            f"Evidence: {finding.evidence}\n"
            f"Evidence count: {finding.evidence_count}\n\n"
            f"Current component content:\n{current_content}\n\n"
            "Produce a specific diff that addresses the failure pattern. "
            "Set auto_apply=true only if: diff is ≤20 lines changed, risk is 'low', "
            "and no agent restart is needed."
        )
        try:
            pass2_raw = _invoke_claude(pass2_prompt, _PASS2_SCHEMA, "claude-sonnet-4-6")
            auto_apply = bool(pass2_raw.get("auto_apply")) and _is_small_fix(pass2_raw, finding)
            proposals.append(FixProposal(
                finding=finding,
                change_summary=pass2_raw.get("change_summary", ""),
                diff=pass2_raw.get("diff", ""),
                auto_apply=auto_apply,
                risk=pass2_raw.get("risk", "unknown"),
            ))
        except Exception as exc:
            logger.warning("paia_agent_health.pass2_failed for %s/%s: %s",
                           finding.agent, finding.affected_component, exc)

    return proposals
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_paia_agent_health_analyzer.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add driftdriver/paia_agent_health/analyzer.py tests/test_paia_agent_health_analyzer.py
git commit -m "feat: add paia_agent_health two-pass analyzer"
```

---

## Task 4: Fix Application and Approval Flow

**Files:**
- Create: `driftdriver/driftdriver/paia_agent_health/fixes.py`
- Create: `driftdriver/tests/test_paia_agent_health_fixes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paia_agent_health_fixes.py
# ABOUTME: Tests for fix application and proposal Telegram messaging.

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from driftdriver.paia_agent_health.analyzer import Finding, FixProposal
from driftdriver.paia_agent_health.fix_history import FixRecord, load_history
from driftdriver.paia_agent_health.fixes import apply_fix, build_proposal_message, handle_agent_fix_decision


def _make_proposal(auto_apply: bool = True) -> FixProposal:
    finding = Finding(
        agent="caroline",
        pattern_type="tool_failure",
        evidence=["user: that didn't work", "user: try again"],
        evidence_count=4,
        affected_component="skills/outreach_templates.md",
        severity="medium",
        confidence=0.85,
    )
    return FixProposal(
        finding=finding,
        change_summary="Add fallback search protocol",
        diff="--- a/outreach_templates.md\n+++ b/outreach_templates.md\n@@ -10 +10,2 @@\n+## Fallback\n+retry",
        auto_apply=auto_apply,
        risk="low",
    )


def test_build_proposal_message_contains_key_elements():
    proposal = _make_proposal(auto_apply=False)
    dec_id = "dec-20260401-abc123"
    msg = build_proposal_message(proposal, dec_id)
    assert "🤖 Agent Health" in msg
    assert "caroline" in msg
    assert dec_id in msg
    assert "approve" in msg.lower() or "yes" in msg.lower()
    assert "Fallback" in msg


def test_apply_fix_writes_skill_file():
    proposal = _make_proposal(auto_apply=True)
    with tempfile.TemporaryDirectory() as td:
        skill_path = Path(td) / "outreach_templates.md"
        skill_path.write_text("line1\nline2\nIf search returns no results, try with just last name before proceeding.\n")
        history_path = Path(td) / "fixes.json"

        with patch("driftdriver.paia_agent_health.fixes._resolve_skill_path", return_value=skill_path):
            with patch("driftdriver.paia_agent_health.fixes._publish_event") as mock_publish:
                apply_fix(proposal, history_path=history_path)
                mock_publish.assert_called_once()

        history = load_history(history_path)
        assert len(history) == 1
        assert history[0].agent == "caroline"
        assert history[0].auto_applied is True


def test_handle_agent_fix_decision_approve():
    with tempfile.TemporaryDirectory() as td:
        pending_path = Path(td) / "pending.json"
        history_path = Path(td) / "fixes.json"
        dec_id = "dec-20260401-abc123"

        # Store a pending proposal
        import json
        from dataclasses import asdict
        finding = Finding("caroline", "tool_failure", ["x"], 4,
                          "skills/outreach_templates.md", "medium", 0.85)
        proposal = FixProposal(finding, "Add fallback", "+fallback\n", False, "low")
        # Serialize proposal to dict for pending storage
        pending = {
            dec_id: {
                "agent": proposal.finding.agent,
                "component": proposal.finding.affected_component,
                "pattern": proposal.finding.pattern_type,
                "change_summary": proposal.change_summary,
                "diff": proposal.diff,
                "risk": proposal.risk,
            }
        }
        pending_path.write_text(json.dumps(pending))

        skill_path = Path(td) / "outreach_templates.md"
        skill_path.write_text("existing content\n")

        with patch("driftdriver.paia_agent_health.fixes._resolve_skill_path", return_value=skill_path):
            with patch("driftdriver.paia_agent_health.fixes._publish_event"):
                result = handle_agent_fix_decision(
                    dec_id, "yes",
                    pending_path=pending_path, history_path=history_path
                )
        assert result == "applied"
        history = load_history(history_path)
        assert len(history) == 1


def test_handle_agent_fix_decision_skip():
    with tempfile.TemporaryDirectory() as td:
        pending_path = Path(td) / "pending.json"
        import json
        dec_id = "dec-20260401-skip001"
        pending_path.write_text(json.dumps({dec_id: {"agent": "derek", "component": "x",
                                                       "pattern": "y", "change_summary": "z",
                                                       "diff": "", "risk": "low"}}))
        history_path = Path(td) / "fixes.json"
        result = handle_agent_fix_decision(dec_id, "no", pending_path=pending_path, history_path=history_path)
        assert result == "skipped"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_paia_agent_health_fixes.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement fixes.py**

```python
# driftdriver/paia_agent_health/fixes.py
# ABOUTME: Applies auto-fix skill edits and sends large-fix proposals via Telegram.
# ABOUTME: handle_agent_fix_decision() resolves pending approvals from Telegram chat.

from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from driftdriver.factory_brain.telegram import load_telegram_config, send_telegram
from driftdriver.paia_agent_health.analyzer import FixProposal
from driftdriver.paia_agent_health.fix_history import (
    FixRecord, add_fix, DEFAULT_PATH as DEFAULT_HISTORY_PATH
)

logger = logging.getLogger(__name__)

_EXPERIMENTS = os.environ.get("EXPERIMENTS_DIR", os.path.expanduser("~/projects/experiments"))
DEFAULT_PENDING_PATH = Path.home() / ".config" / "workgraph" / "agent_health_pending.json"
EVENTS_URL = os.environ.get("PAIA_EVENTS_URL", "http://localhost:3511")


def _resolve_skill_path(component: str, agent: str) -> Path | None:
    """Resolve a component path like 'skills/outreach_templates.md' to an absolute path."""
    candidates = [
        Path(_EXPERIMENTS) / agent / component,
        Path(_EXPERIMENTS) / agent / "skills" / Path(component).name,
        Path(_EXPERIMENTS) / component,
    ]
    for p in candidates:
        if p.exists():
            return p
    # Return first candidate even if it doesn't exist (for new files)
    return candidates[0]


def _publish_event(event_type: str, data: dict) -> None:
    """Fire-and-forget POST to paia-events. Never raises."""
    try:
        import httpx
        payload = {
            "event_type": event_type,
            "actor_ref": "paia-healer",
            "data": data,
        }
        httpx.post(f"{EVENTS_URL}/v1/events", json=payload, timeout=5)
    except Exception as exc:
        logger.debug("_publish_event failed: %s", exc)


def _apply_diff(current_content: str, diff: str) -> str:
    """Apply a unified diff to content. Falls back to appending if patch fails."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".orig", delete=False) as orig:
        orig.write(current_content)
        orig_path = orig.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as patch_f:
        patch_f.write(diff)
        patch_path = patch_f.name
    try:
        result = subprocess.run(
            ["patch", "--no-backup-if-mismatch", orig_path, patch_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return Path(orig_path).read_text()
        logger.debug("patch failed, appending diff: %s", result.stderr)
        return current_content + "\n" + diff
    except Exception:
        return current_content + "\n" + diff
    finally:
        Path(orig_path).unlink(missing_ok=True)
        Path(patch_path).unlink(missing_ok=True)


def apply_fix(
    proposal: FixProposal,
    *,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> None:
    """Apply a small fix directly to the skill file. Log to fix history."""
    skill_path = _resolve_skill_path(proposal.finding.affected_component, proposal.finding.agent)
    if skill_path is None:
        logger.warning("Cannot resolve skill path for %s", proposal.finding.affected_component)
        return

    current = skill_path.read_text() if skill_path.exists() else ""
    patched = _apply_diff(current, proposal.diff)
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(patched)

    now = datetime.now(timezone.utc)
    record = FixRecord(
        fix_id=uuid.uuid4().hex[:8],
        applied_at=now.isoformat(),
        agent=proposal.finding.agent,
        component=proposal.finding.affected_component,
        finding_pattern=proposal.finding.pattern_type,
        change_summary=proposal.change_summary,
        diff=proposal.diff,
        auto_applied=True,
        check_after=(now + timedelta(days=7)).isoformat(),
        outcome=None,
    )
    add_fix(history_path, record)

    _publish_event("healer.fix.applied", {
        "agent": proposal.finding.agent,
        "component": proposal.finding.affected_component,
        "change_summary": proposal.change_summary,
        "auto_applied": True,
    })
    logger.info("auto-applied fix to %s/%s", proposal.finding.agent, proposal.finding.affected_component)


def build_proposal_message(proposal: FixProposal, dec_id: str) -> str:
    """Build the Telegram message for a large fix proposal."""
    return (
        f"🤖 *Agent Health*\n\n"
        f"*Agent:* {proposal.finding.agent}\n"
        f"*Pattern:* {proposal.finding.pattern_type} ({proposal.finding.severity})\n"
        f"*Component:* `{proposal.finding.affected_component}`\n\n"
        f"*Evidence ({proposal.finding.evidence_count}x):*\n"
        + "\n".join(f"  • {e[:120]}" for e in proposal.finding.evidence[:3])
        + f"\n\n*Proposed fix:* {proposal.change_summary}\n\n"
        f"```\n{proposal.diff[:800]}\n```\n\n"
        f"Reply `{dec_id} yes` to apply, `{dec_id} no` to skip."
    )


def store_pending_proposal(
    proposal: FixProposal,
    dec_id: str,
    *,
    pending_path: Path = DEFAULT_PENDING_PATH,
) -> None:
    """Store a pending proposal keyed by decision ID."""
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if pending_path.exists():
        try:
            existing = json.loads(pending_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing[dec_id] = {
        "agent": proposal.finding.agent,
        "component": proposal.finding.affected_component,
        "pattern": proposal.finding.pattern_type,
        "change_summary": proposal.change_summary,
        "diff": proposal.diff,
        "risk": proposal.risk,
    }
    pending_path.write_text(json.dumps(existing, indent=2))


def send_proposal(
    proposal: FixProposal,
    *,
    pending_path: Path = DEFAULT_PENDING_PATH,
) -> str | None:
    """Send a large fix proposal via Telegram. Returns dec_id or None on failure."""
    cfg = load_telegram_config()
    if not cfg:
        logger.warning("send_proposal: no Telegram config found")
        return None

    now = datetime.now(timezone.utc)
    dec_id = f"dec-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    store_pending_proposal(proposal, dec_id, pending_path=pending_path)

    msg = build_proposal_message(proposal, dec_id)
    sent = send_telegram(bot_token=cfg["bot_token"], chat_id=cfg["chat_id"], message=msg)
    if not sent:
        logger.warning("send_proposal: Telegram send failed for %s", dec_id)
    return dec_id


def handle_agent_fix_decision(
    dec_id: str,
    answer: str,
    *,
    pending_path: Path = DEFAULT_PENDING_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> str:
    """Handle a user's decision on a pending fix proposal. Returns 'applied', 'skipped', or 'unknown'."""
    if not pending_path.exists():
        return "unknown"
    try:
        pending: dict = json.loads(pending_path.read_text())
    except (json.JSONDecodeError, OSError):
        return "unknown"

    proposal_data = pending.get(dec_id)
    if not proposal_data:
        return "unknown"

    answer_lower = answer.strip().lower()

    if answer_lower in ("yes", "y", "approve", "approved"):
        # Apply the fix
        skill_path = _resolve_skill_path(proposal_data["component"], proposal_data["agent"])
        current = skill_path.read_text() if (skill_path and skill_path.exists()) else ""
        if skill_path:
            patched = _apply_diff(current, proposal_data["diff"])
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(patched)

        now = datetime.now(timezone.utc)
        record = FixRecord(
            fix_id=uuid.uuid4().hex[:8],
            applied_at=now.isoformat(),
            agent=proposal_data["agent"],
            component=proposal_data["component"],
            finding_pattern=proposal_data["pattern"],
            change_summary=proposal_data["change_summary"],
            diff=proposal_data["diff"],
            auto_applied=False,
            check_after=(now + timedelta(days=7)).isoformat(),
            outcome=None,
        )
        add_fix(history_path, record)
        _publish_event("healer.fix.applied", {
            "agent": proposal_data["agent"],
            "component": proposal_data["component"],
            "change_summary": proposal_data["change_summary"],
            "auto_applied": False,
        })
        # Remove from pending
        del pending[dec_id]
        pending_path.write_text(json.dumps(pending, indent=2))
        return "applied"

    elif answer_lower in ("no", "n", "skip"):
        _publish_event("healer.fix.skipped", {
            "agent": proposal_data["agent"],
            "component": proposal_data["component"],
            "dec_id": dec_id,
        })
        del pending[dec_id]
        pending_path.write_text(json.dumps(pending, indent=2))
        return "skipped"

    return "unknown"
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_paia_agent_health_fixes.py -v
```
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add driftdriver/paia_agent_health/fixes.py tests/test_paia_agent_health_fixes.py
git commit -m "feat: add paia_agent_health fix application and approval flow"
```

---

## Task 5: New Directives

**Files:**
- Modify: `driftdriver/driftdriver/factory_brain/directives.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to existing tests/test_factory_brain_directives.py (or create new file):
def test_new_agent_health_directives_in_schema():
    from driftdriver.factory_brain.directives import DIRECTIVE_SCHEMA, DESTRUCTIVE_ACTIONS
    assert "apply_skill_fix" in DIRECTIVE_SCHEMA
    assert "propose_agent_fix" in DIRECTIVE_SCHEMA
    assert "restart_paia_service" in DIRECTIVE_SCHEMA
    assert "restart_paia_service" in DESTRUCTIVE_ACTIONS
    assert DIRECTIVE_SCHEMA["apply_skill_fix"] == ["agent", "skill_file", "diff"]
    assert DIRECTIVE_SCHEMA["propose_agent_fix"] == ["agent", "component", "finding_summary", "proposed_diff"]
    assert DIRECTIVE_SCHEMA["restart_paia_service"] == ["service"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/ -k "test_new_agent_health_directives_in_schema" -v
```
Expected: `AssertionError`

- [ ] **Step 3: Add new directives to directives.py**

In `driftdriver/factory_brain/directives.py`, add these three entries to `DIRECTIVE_SCHEMA` (around line 22, after `"noop"`):

```python
    "apply_skill_fix":      ["agent", "skill_file", "diff"],
    "propose_agent_fix":    ["agent", "component", "finding_summary", "proposed_diff"],
    "restart_paia_service": ["service"],
```

Add `"restart_paia_service"` to `DESTRUCTIVE_ACTIONS` (currently defined in `chat.py`). Since `DESTRUCTIVE_ACTIONS` lives in `chat.py`, add it there:

In `driftdriver/factory_brain/chat.py`, update:
```python
DESTRUCTIVE_ACTIONS = frozenset({"kill_daemon", "kill_process", "unenroll", "restart_paia_service"})
```

Add executor branches in `directives.py` `execute_directives()` function — find the `if directive.action == ...` chain and add:

```python
elif directive.action == "apply_skill_fix":
    _execute_apply_skill_fix(directive.params, dry_run=dry_run)
elif directive.action == "propose_agent_fix":
    _execute_propose_agent_fix(directive.params, dry_run=dry_run)
elif directive.action == "restart_paia_service":
    _execute_restart_paia_service(directive.params, dry_run=dry_run)
```

Add the three handler functions at the bottom of `directives.py`:

```python
def _execute_apply_skill_fix(params: dict, *, dry_run: bool = False) -> str:
    """Auto-apply a skill fix directly (used for small/auto fixes from the healer)."""
    from driftdriver.paia_agent_health.analyzer import Finding, FixProposal
    from driftdriver.paia_agent_health.fixes import apply_fix
    agent = params.get("agent", "")
    skill_file = params.get("skill_file", "")
    diff = params.get("diff", "")
    if not agent or not skill_file or not diff:
        return "error: apply_skill_fix requires agent, skill_file, diff"
    if dry_run:
        return f"dry_run: would apply fix to {agent}/{skill_file}"
    finding = Finding(agent=agent, pattern_type="directive", evidence=[],
                      evidence_count=0, affected_component=skill_file,
                      severity="low", confidence=1.0)
    proposal = FixProposal(finding=finding, change_summary="directive-triggered fix",
                           diff=diff, auto_apply=True, risk="low")
    apply_fix(proposal)
    return f"applied fix to {agent}/{skill_file}"


def _execute_propose_agent_fix(params: dict, *, dry_run: bool = False) -> str:
    """Send a large fix proposal via Telegram for user approval."""
    from driftdriver.paia_agent_health.analyzer import Finding, FixProposal
    from driftdriver.paia_agent_health.fixes import send_proposal
    agent = params.get("agent", "")
    component = params.get("component", "")
    finding_summary = params.get("finding_summary", "")
    proposed_diff = params.get("proposed_diff", "")
    if dry_run:
        return f"dry_run: would propose fix for {agent}/{component}"
    finding = Finding(agent=agent, pattern_type="directive", evidence=[finding_summary],
                      evidence_count=1, affected_component=component,
                      severity="medium", confidence=1.0)
    proposal = FixProposal(finding=finding, change_summary=finding_summary,
                           diff=proposed_diff, auto_apply=False, risk="medium")
    dec_id = send_proposal(proposal)
    return f"proposed fix {dec_id} for {agent}/{component}"


def _execute_restart_paia_service(params: dict, *, dry_run: bool = False) -> str:
    """Restart a PAIA launchd service via launchctl kickstart."""
    import os
    import subprocess
    service = params.get("service", "")
    if not service:
        return "error: restart_paia_service requires service"
    label = f"com.paia.{service}"
    uid = os.getuid()
    domain = f"gui/{uid}/{label}"
    if dry_run:
        return f"dry_run: would run launchctl kickstart -k {domain}"
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", domain],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        return f"restarted {label}"
    return f"restart failed ({result.returncode}): {result.stderr.strip()[:200]}"
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/ -k "test_new_agent_health_directives_in_schema" -v
```
Expected: PASS

- [ ] **Step 5: Run full driftdriver test suite to check for regressions**

```bash
uv run pytest tests/ -q --tb=short 2>&1 | tail -15
```
Expected: existing tests still pass, no new failures

- [ ] **Step 6: Commit**

```bash
git add driftdriver/factory_brain/directives.py driftdriver/factory_brain/chat.py
git commit -m "feat: add apply_skill_fix/propose_agent_fix/restart_paia_service directives"
```

---

## Task 6: Ecosystem Hub Decision Hook

**Files:**
- Modify: `driftdriver/driftdriver/ecosystem_hub/api.py:546-558`

This adds decision routing for agent fix approvals. When `handle_chat_message` returns a `decision_route` whose `decision_id` matches a pending agent fix, it calls `handle_agent_fix_decision`.

- [ ] **Step 1: Write the failing test**

```python
# Add to a new test file: tests/test_paia_agent_health_decision_hook.py
# ABOUTME: Tests decision routing for agent fix approvals via ecosystem hub.

from unittest.mock import patch, MagicMock
from driftdriver.paia_agent_health.fixes import handle_agent_fix_decision

def test_handle_agent_fix_decision_unknown_dec_id():
    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        result = handle_agent_fix_decision(
            "dec-99999999-zzzzzz", "yes",
            pending_path=Path(f.name),
            history_path=Path(f.name),
        )
    assert result == "unknown"
```

- [ ] **Step 2: Run test to verify it passes already** (pure logic, no hub code needed)

```bash
uv run pytest tests/test_paia_agent_health_decision_hook.py -v
```
Expected: PASS

- [ ] **Step 3: Add decision hook to ecosystem_hub/api.py**

In `driftdriver/ecosystem_hub/api.py`, find the block starting at line 546 where `handle_chat_message` result is sent:

```python
            result = handle_chat_message(
                message=message,
                chat_id=chat_id,
                ...
            )
            self._send_json(result)
```

Replace with:

```python
            result = handle_chat_message(
                message=message,
                chat_id=chat_id,
                user_name=user_name,
                snapshot=snapshot,
                roster=roster,
                pending_decisions=pending_decisions,
            )

            # Agent fix decision routing
            decision_route = result.get("decision_route")
            if decision_route:
                dec_id = decision_route.get("decision_id", "")
                answer = decision_route.get("answer", "")
                if dec_id and answer:
                    try:
                        from driftdriver.paia_agent_health.fixes import handle_agent_fix_decision
                        fix_result = handle_agent_fix_decision(dec_id, answer)
                        result["agent_fix_result"] = fix_result
                    except Exception as _exc:
                        logger.debug("agent fix decision hook failed: %s", _exc)

            self._send_json(result)
```

- [ ] **Step 4: Run full test suite**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/ -q --tb=short 2>&1 | tail -15
```
Expected: no regressions

- [ ] **Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/api.py tests/test_paia_agent_health_decision_hook.py
git commit -m "feat: route agent fix decisions from ecosystem hub chat handler"
```

---

## Task 7: Router Integration

**Files:**
- Modify: `driftdriver/driftdriver/factory_brain/router.py`

Adds agent health tick lane to `BrainState` and `run_brain_tick()`. The lane runs full analysis every 24 hours and event-triggered scoped analysis on `agent.task.failed` / repeated `agent.tool.called` failures.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paia_agent_health_router.py
# ABOUTME: Tests agent health tick lane in the factory brain router.

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from driftdriver.factory_brain.router import BrainState, should_run_agent_health


def test_brain_state_has_agent_health_fields():
    state = BrainState()
    assert hasattr(state, "last_agent_health_check")
    assert state.last_agent_health_check is None


def test_should_run_agent_health_first_time():
    state = BrainState()
    assert should_run_agent_health(state, interval_seconds=86400) is True


def test_should_run_agent_health_not_yet_due():
    state = BrainState()
    state.last_agent_health_check = datetime.now(timezone.utc) - timedelta(hours=1)
    assert should_run_agent_health(state, interval_seconds=86400) is False


def test_should_run_agent_health_due():
    state = BrainState()
    state.last_agent_health_check = datetime.now(timezone.utc) - timedelta(hours=25)
    assert should_run_agent_health(state, interval_seconds=86400) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_paia_agent_health_router.py -v
```
Expected: `AttributeError: 'BrainState' has no field 'last_agent_health_check'`

- [ ] **Step 3: Add `last_agent_health_check` to `BrainState` in router.py**

In `driftdriver/factory_brain/router.py`, add to the `BrainState` dataclass:

```python
@dataclass
class BrainState:
    """Mutable state for the router across ticks."""

    last_heartbeat_check: datetime | None = None
    last_sweep: datetime | None = None
    last_event_ts: str = ""
    recent_directives: list[dict] = field(default_factory=list)
    tier1_escalation_count: int = 0
    last_known_stale: set[str] = field(default_factory=set)
    last_agent_health_check: datetime | None = None    # ← add this line
```

Add `should_run_agent_health()` function after `should_sweep()`:

```python
def should_run_agent_health(state: BrainState, *, interval_seconds: int = 86400) -> bool:
    """True if last_agent_health_check is None or interval has elapsed."""
    if state.last_agent_health_check is None:
        return True
    elapsed = (datetime.now(timezone.utc) - state.last_agent_health_check).total_seconds()
    return elapsed >= interval_seconds
```

- [ ] **Step 4: Add agent health lane to `run_brain_tick()`**

At the end of `run_brain_tick()`, just before `return all_results`, add:

```python
    # Agent health lane — periodic 24h sweep + event-triggered fast path
    agent_health_triggered = any(
        e.kind in ("agent.task.failed",) for e in events
    )
    if should_run_agent_health(state) or agent_health_triggered:
        state.last_agent_health_check = now
        try:
            import asyncio
            from driftdriver.paia_agent_health.collector import collect_signals
            from driftdriver.paia_agent_health.analyzer import run_analysis
            from driftdriver.paia_agent_health.fixes import apply_fix, send_proposal
            from driftdriver.paia_agent_health.fix_history import is_duplicate_pending

            bundle = asyncio.run(collect_signals())
            proposals = run_analysis(bundle)

            for proposal in proposals:
                if is_duplicate_pending(
                    DEFAULT_HISTORY_PATH,
                    proposal.finding.agent,
                    proposal.finding.affected_component,
                    proposal.finding.pattern_type,
                ):
                    continue
                if proposal.auto_apply:
                    apply_fix(proposal)
                else:
                    send_proposal(proposal)
        except Exception as exc:
            logger.warning("agent_health_lane_failed: %s", exc)
```

Add the import at the top of `run_brain_tick()`:

```python
    from driftdriver.paia_agent_health.fix_history import DEFAULT_PATH as DEFAULT_HISTORY_PATH, pending_checks, update_outcome
```

Also add the 7-day outcome re-check loop just before the agent health analysis block:

```python
    # 7-day outcome checks — re-escalate unresolved fixes
    try:
        from driftdriver.paia_agent_health.fix_history import pending_checks, update_outcome, DEFAULT_PATH as DEFAULT_HISTORY_PATH
        from driftdriver.paia_agent_health.fixes import send_proposal
        from driftdriver.paia_agent_health.analyzer import Finding, FixProposal
        import asyncio
        for due_record in pending_checks(DEFAULT_HISTORY_PATH):
            from driftdriver.paia_agent_health.collector import collect_signals
            bundle = asyncio.run(collect_signals())
            # Quick re-check: does the pattern still appear for this agent?
            agent_signals = bundle.agents.get(due_record.agent)
            still_failing = agent_signals and any(
                due_record.finding_pattern in str(e) or due_record.component in str(e)
                for e in (agent_signals.conversation_turns + agent_signals.tool_events)[:10]
            )
            if still_failing:
                finding = Finding(
                    agent=due_record.agent, pattern_type=due_record.finding_pattern,
                    evidence=[f"Previous fix did not resolve: {due_record.change_summary}"],
                    evidence_count=due_record.evidence_count if hasattr(due_record, "evidence_count") else 1,
                    affected_component=due_record.component, severity="high", confidence=1.0,
                )
                proposal = FixProposal(
                    finding=finding,
                    change_summary=f"Re-escalation: previous fix for {due_record.component} did not resolve the issue.",
                    diff=due_record.diff,
                    auto_apply=False, risk="medium",
                )
                send_proposal(proposal)
                update_outcome(DEFAULT_HISTORY_PATH, due_record.fix_id, "persists")
            else:
                update_outcome(DEFAULT_HISTORY_PATH, due_record.fix_id, "resolved")
    except Exception as exc:
        logger.warning("agent_health_outcome_check_failed: %s", exc)
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_paia_agent_health_router.py -v
uv run pytest tests/ -q --tb=short 2>&1 | tail -15
```
Expected: new tests pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add driftdriver/factory_brain/router.py tests/test_paia_agent_health_router.py
git commit -m "feat: add agent health tick lane to factory brain router"
```

---

## Task 8: paia-agent-runtime Tool Call Events

**Files:**
- Modify: `paia-agent-runtime/src/paia_agent_runtime/tools/__init__.py:198-232`

Adds optional fire-and-forget `agent.tool.called` event publishing after each tool execution. Uses `PAIA_EVENTS_URL` env var. Never blocks or raises.

- [ ] **Step 1: Write the failing test**

```python
# In paia-agent-runtime: tests/test_tool_call_events.py
# ABOUTME: Tests fire-and-forget tool call event emission from ToolDispatcher.

from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from paia_agent_runtime.tools import ToolDispatcher, ToolRequest


def test_tool_call_event_fired_on_success():
    dispatcher = ToolDispatcher()

    class FakeTool:
        @staticmethod
        def describe():
            return {"name": "fake", "description": "test"}
        async def execute(self, action, **params):
            return {"status": "ok", "result": "done"}

    dispatcher.register("fake", FakeTool())

    events_fired = []

    async def run():
        with patch("paia_agent_runtime.tools._emit_tool_event", new_callable=AsyncMock) as mock_emit:
            result = await dispatcher.execute(ToolRequest(name="fake", arguments={"action": "test"}))
            assert result.success is True
            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args
            assert call_kwargs[1]["tool_name"] == "fake" or call_kwargs[0][0] == "fake"

    asyncio.run(run())


def test_tool_call_event_fired_on_failure():
    dispatcher = ToolDispatcher()

    class FailingTool:
        @staticmethod
        def describe():
            return {"name": "failing", "description": "test"}
        async def execute(self, action, **params):
            raise RuntimeError("tool exploded")

    dispatcher.register("failing", FailingTool())

    async def run():
        with patch("paia_agent_runtime.tools._emit_tool_event", new_callable=AsyncMock) as mock_emit:
            result = await dispatcher.execute(ToolRequest(name="failing", arguments={"action": "do"}))
            assert result.success is False
            mock_emit.assert_called_once()

    asyncio.run(run())
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/braydon/projects/experiments/paia-agent-runtime
uv run pytest tests/test_tool_call_events.py -v
```
Expected: `ImportError` or `AttributeError: module has no attribute '_emit_tool_event'`

- [ ] **Step 3: Add `_emit_tool_event` and fire it in ToolDispatcher.execute()**

At the top of `paia_agent_runtime/tools/__init__.py`, add after the existing imports:

```python
import asyncio as _asyncio
import os as _os
import time as _time

_PAIA_EVENTS_URL = _os.environ.get("PAIA_EVENTS_URL", "http://localhost:3511")
_AGENT_ID = _os.environ.get("PAIA_AGENT_ID", "unknown")


async def _emit_tool_event(
    tool_name: str,
    action: str,
    *,
    success: bool,
    duration_ms: float,
    error: str | None = None,
) -> None:
    """Fire-and-forget tool call event to paia-events. Never raises."""
    try:
        import httpx as _httpx
        payload = {
            "event_type": "agent.tool.called",
            "actor_ref": _AGENT_ID,
            "data": {
                "agent": _AGENT_ID,
                "tool": tool_name,
                "action": action,
                "success": success,
                "error": error,
                "duration_ms": round(duration_ms, 1),
            },
        }
        async with _httpx.AsyncClient(timeout=3) as client:
            await client.post(f"{_PAIA_EVENTS_URL}/v1/events", json=payload)
    except Exception:
        pass  # Never block tool execution for telemetry
```

In `ToolDispatcher.execute()`, replace the try/except block (lines 210-232) with:

```python
        try:
            action = request.arguments.get("action", "")
            params = {k: v for k, v in request.arguments.items() if k != "action"}
            _log.warning("[TOOL] %s.%s params=%s", request.name, action, params)
            _t0 = _time.monotonic()
            result = await tool.execute(action, **params)
            _elapsed_ms = (_time.monotonic() - _t0) * 1000

            success = result.get("status") == "ok"
            output_preview = str(result)[:300]
            _log.warning("[TOOL] %s.%s success=%s result=%s", request.name, action, success, output_preview)

            try:
                _asyncio.create_task(_emit_tool_event(
                    request.name, action, success=success, duration_ms=_elapsed_ms
                ))
            except RuntimeError:
                pass  # no running event loop in test context

            return ToolResult(
                tool_name=request.name,
                success=success,
                output=result,
                error=result.get("error") or result.get("message") if not success else None,
            )
        except Exception as exc:
            _elapsed_ms = (_time.monotonic() - _t0) * 1000 if "_t0" in dir() else 0.0
            _log.warning("[TOOL] %s.%s FAILED: %s", request.name, action, exc)
            logger.error("tool_dispatcher.execution_error", tool=request.name, error=str(exc))

            try:
                _asyncio.create_task(_emit_tool_event(
                    request.name, action, success=False,
                    duration_ms=_elapsed_ms, error=str(exc)
                ))
            except RuntimeError:
                pass

            return ToolResult(
                tool_name=request.name,
                success=False,
                error=str(exc),
            )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_tool_call_events.py -v
uv run pytest tests/ -q --tb=short 2>&1 | tail -10
```
Expected: new tests pass, no regressions

- [ ] **Step 5: Commit**

```bash
cd /Users/braydon/projects/experiments/paia-agent-runtime
git add src/paia_agent_runtime/tools/__init__.py tests/test_tool_call_events.py
git commit -m "feat: emit agent.tool.called events from ToolDispatcher (fire-and-forget)"
```

---

## Task 9: Integration Smoke Test

Manual verification that the full lane works end-to-end.

- [ ] **Step 1: Verify driftdriver tests are all passing**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/ -q 2>&1 | tail -5
```
Expected: all existing tests pass, new paia_agent_health tests pass

- [ ] **Step 2: Verify paia-agent-runtime tests pass**

```bash
cd /Users/braydon/projects/experiments/paia-agent-runtime
uv run pytest tests/ -q 2>&1 | tail -5
```
Expected: passes (pre-existing failures only, no new failures)

- [ ] **Step 3: Test fix_history directly**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run python3 -c "
from driftdriver.paia_agent_health.fix_history import load_history, pending_checks
from pathlib import Path
h = load_history()
print(f'Existing fix records: {len(h)}')
p = pending_checks()
print(f'Pending outcome checks: {len(p)}')
"
```
Expected: runs without error (0 records on first run)

- [ ] **Step 4: Test collector (requires paia services running)**

```bash
uv run python3 -c "
import asyncio
from driftdriver.paia_agent_health.collector import collect_signals
bundle = asyncio.run(collect_signals())
for name, signals in bundle.agents.items():
    print(f'{name}: {len(signals.conversation_turns)} turns, {len(signals.tool_events)} tool events, errors={signals.errors}')
"
```
Expected: prints agent signal counts (some may be 0 if services are down, no crash)

- [ ] **Step 5: Final commit if anything needed, then done**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git log --oneline -8
```
