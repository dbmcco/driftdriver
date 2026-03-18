# Agent History Tracking — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `build_agent_history()` to a new `agent_history.py` module. Wire it into the `GET /api/repo/:name` handler in `api.py` as the `agent_history` field. Add a JS render function for the Agent History section on the detail page. All data read from `events.jsonl` and `graph.jsonl` already on disk — no new write paths, no new daemon, no new persistence layer.

**Architecture:** One new Python file (`driftdriver/ecosystem_hub/agent_history.py`). Two existing files modified (`api.py`, `dashboard.py`). The history builder is a pure function that reads JSONL files on demand and returns a structured dict. The `GET /api/repo/:name` handler already exists (added in the repo-detail-page plan); this plan adds the `agent_history` key to its response. The JS detail page renders the new Agent History section between Active Agents and Repo Dependencies.

**Tech Stack:** Python 3.11+, `dataclasses`, `datetime` (stdlib), vanilla JS in `dashboard.py`, `unittest` + `tempfile` + `json` for tests (real JSONL fixtures, no mocks). Test runner: `uv run pytest`.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `driftdriver/ecosystem_hub/agent_history.py` | `build_agent_history()` pure function + `AgentSession` dataclass |
| Modify | `driftdriver/ecosystem_hub/api.py` | Import and call `build_agent_history()` in `GET /api/repo/:name` handler |
| Modify | `driftdriver/ecosystem_hub/dashboard.py` | Add `renderAgentHistory()` JS function and Agent History section HTML |
| Create | `tests/test_agent_history.py` | Full test coverage for `build_agent_history()` |

---

## Step 1 — Create test file with failing tests for `agent_history.py`

- [ ] Create `tests/test_agent_history.py`

The test file uses `tempfile.TemporaryDirectory` to build real JSONL fixtures. No mocks.

```python
# ABOUTME: Tests for build_agent_history() in driftdriver/ecosystem_hub/agent_history.py.
# ABOUTME: Uses real JSONL fixtures in tempfile dirs — no mocks.
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from driftdriver.ecosystem_hub.agent_history import build_agent_history


def _make_repo(tmp: str) -> Path:
    """Create a repo dir with .workgraph/service/runtime/ structure."""
    repo = Path(tmp) / "myrepo"
    runtime = repo / ".workgraph" / "service" / "runtime"
    runtime.mkdir(parents=True)
    return repo


def _write_events(repo: Path, events: list[dict]) -> None:
    events_path = repo / ".workgraph" / "service" / "runtime" / "events.jsonl"
    lines = [json.dumps(e) for e in events]
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_graph(repo: Path, tasks: list[dict]) -> None:
    graph_path = repo / ".workgraph" / "graph.jsonl"
    lines = [json.dumps(t) for t in tasks]
    graph_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


NOW = 1742300000.0  # Fixed epoch for deterministic tests (~2026-03-18)


class TestBuildAgentHistoryNoFile(unittest.TestCase):
    def test_missing_events_jsonl_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "bare"
            repo.mkdir()
            result = build_agent_history(repo)
            self.assertEqual(result["sessions"], [])
            self.assertEqual(result["total_sessions_in_file"], 0)
            self.assertIsNone(result["history_since"])


class TestBuildAgentHistorySingleCleanSession(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "claude-code", "actor_id": "session-001"}},
            {"kind": "session.ended", "repo": "myrepo", "ts": NOW - 0,
             "payload": {"actor_id": "session-001"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_single_session_parsed(self):
        result = build_agent_history(self.repo)
        self.assertEqual(len(result["sessions"]), 1)
        s = result["sessions"][0]
        self.assertEqual(s["session_id"], "session-001")
        self.assertEqual(s["agent_type"], "claude-code")
        self.assertEqual(s["outcome"], "clean_exit")
        self.assertEqual(s["duration_seconds"], 3600)

    def test_timestamps_are_iso_strings(self):
        s = build_agent_history(self.repo)["sessions"][0]
        # ISO strings contain 'T' and 'Z'
        self.assertIn("T", s["started_at"])
        self.assertIn("T", s["ended_at"])

    def test_history_since_set_to_oldest_event(self):
        result = build_agent_history(self.repo)
        self.assertIsNotNone(result["history_since"])


class TestBuildAgentHistoryNoCrashOutcome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        # session.started but no session.ended, ts is old (> 10 min ago)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 7200,
             "payload": {"cli": "codex", "actor_id": "session-002"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_outcome_is_unknown_when_no_end_and_old(self):
        result = build_agent_history(self.repo)
        s = result["sessions"][0]
        self.assertEqual(s["outcome"], "unknown")
        self.assertIsNone(s["ended_at"])

    def test_duration_is_none_when_no_end(self):
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertIsNone(s["duration_seconds"])


class TestBuildAgentHistoryStillRunning(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        # session.started just now (< 10 min ago)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": time.time() - 60,
             "payload": {"cli": "claude-code", "actor_id": "session-003"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_outcome_is_still_running_when_recent(self):
        result = build_agent_history(self.repo)
        s = result["sessions"][0]
        self.assertEqual(s["outcome"], "still_running")


class TestBuildAgentHistoryCrashedOutcome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "codex", "actor_id": "session-004"}},
            {"kind": "loop.crashed", "repo": "myrepo", "ts": NOW - 1800,
             "payload": {}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_outcome_is_crashed(self):
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertEqual(s["outcome"], "crashed")


class TestBuildAgentHistoryStalledOutcome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "codex", "actor_id": "session-005"}},
            {"kind": "heartbeat.stale", "repo": "myrepo", "ts": NOW - 1800,
             "payload": {}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_outcome_is_stalled(self):
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertEqual(s["outcome"], "stalled")


class TestBuildAgentHistoryMultipleSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            # Older session
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 7200,
             "payload": {"cli": "codex", "actor_id": "session-A"}},
            {"kind": "session.ended", "repo": "myrepo", "ts": NOW - 3601,
             "payload": {"actor_id": "session-A"}},
            # Newer session
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "claude-code", "actor_id": "session-B"}},
            {"kind": "session.ended", "repo": "myrepo", "ts": NOW - 0,
             "payload": {"actor_id": "session-B"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_sessions_sorted_most_recent_first(self):
        result = build_agent_history(self.repo)
        sessions = result["sessions"]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["session_id"], "session-B")
        self.assertEqual(sessions[1]["session_id"], "session-A")

    def test_total_sessions_in_file(self):
        result = build_agent_history(self.repo)
        self.assertEqual(result["total_sessions_in_file"], 2)


class TestBuildAgentHistoryLimitCap(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        # Write 25 sessions
        events = []
        for i in range(25):
            events.append({
                "kind": "session.started", "repo": "myrepo",
                "ts": NOW - (25 - i) * 3600,
                "payload": {"cli": "codex", "actor_id": f"session-{i:03d}"},
            })
            events.append({
                "kind": "session.ended", "repo": "myrepo",
                "ts": NOW - (25 - i) * 3600 + 1800,
                "payload": {"actor_id": f"session-{i:03d}"},
            })
        _write_events(self.repo, events)

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_limit_is_20(self):
        result = build_agent_history(self.repo)
        self.assertEqual(len(result["sessions"]), 20)
        self.assertEqual(result["total_sessions_in_file"], 25)

    def test_custom_limit_respected(self):
        result = build_agent_history(self.repo, limit=5)
        self.assertEqual(len(result["sessions"]), 5)


class TestBuildAgentHistoryTaskCorrelation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "claude-code", "actor_id": "session-T"}},
            {"kind": "session.ended", "repo": "myrepo", "ts": NOW,
             "payload": {"actor_id": "session-T"}},
        ])
        # Two tasks completed within the session window
        import datetime as dt
        start_iso = dt.datetime.fromtimestamp(NOW - 3600, tz=dt.timezone.utc).isoformat()
        end_iso = dt.datetime.fromtimestamp(NOW - 60, tz=dt.timezone.utc).isoformat()
        _write_graph(self.repo, [
            {"id": "task-1", "title": "Write the tests", "status": "done",
             "started_at": start_iso, "completed_at": end_iso, "type": "task"},
            {"id": "task-2", "title": "Implement the handler", "status": "done",
             "started_at": start_iso, "completed_at": end_iso, "type": "task"},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_tasks_completed_attributed_to_session(self):
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertEqual(len(s["tasks_completed"]), 2)
        self.assertIn("task-1", s["tasks_completed"])

    def test_task_titles_capped_at_3(self):
        # Add more tasks in a second fixture test
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertLessEqual(len(s["task_titles"]), 3)


class TestBuildAgentHistoryMalformedLines(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        # Mix valid and malformed JSON lines
        events_path = self.repo / ".workgraph" / "service" / "runtime" / "events.jsonl"
        events_path.write_text(
            'not valid json\n'
            + json.dumps({"kind": "session.started", "repo": "myrepo",
                          "ts": NOW - 3600,
                          "payload": {"cli": "codex", "actor_id": "session-M"}})
            + '\n{broken}\n',
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_malformed_lines_skipped_silently(self):
        result = build_agent_history(self.repo)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["session_id"], "session-M")


class TestBuildAgentHistoryRepodiscoveredFiltered(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "repo.discovered", "repo": "myrepo", "ts": NOW - 100,
             "payload": {"path": str(self.repo), "source": "driftdriver-install"}},
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 60,
             "payload": {"cli": "codex", "actor_id": "session-R"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_repo_discovered_events_are_filtered(self):
        result = build_agent_history(self.repo)
        # Only one session from session.started — repo.discovered not counted
        self.assertEqual(len(result["sessions"]), 1)


if __name__ == "__main__":
    unittest.main()
```

**Run (expect failures):**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_agent_history.py -x 2>&1 | head -30
```

---

## Step 2 — Implement `driftdriver/ecosystem_hub/agent_history.py`

- [ ] Create `driftdriver/ecosystem_hub/agent_history.py`

```python
# ABOUTME: Builds per-repo agent session history from events.jsonl and graph.jsonl.
# ABOUTME: Pure function: no writes, no daemon, no caching. Called on-demand from api.py.
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STILL_RUNNING_THRESHOLD_SECONDS = 600  # 10 minutes
_SESSION_KINDS = {
    "session.started",
    "session.ended",
    "agent.died",
    "agent.completed",
    "loop.crashed",
    "heartbeat.stale",
}
_CRASH_KINDS = {"agent.died", "loop.crashed"}
_STALL_KINDS = {"heartbeat.stale"}


@dataclass
class _OpenSession:
    session_id: str
    agent_type: str
    started_at: float
    ended_at: float | None = None
    outcome: str = "unknown"
    tasks_completed: list[str] = field(default_factory=list)
    tasks_claimed: list[str] = field(default_factory=list)
    commits_in_window: int = 0
    task_titles: list[str] = field(default_factory=list)


def _epoch_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_to_epoch(iso: str) -> float | None:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


def _load_events(repo_path: Path) -> list[dict[str, Any]]:
    events_file = repo_path / ".workgraph" / "service" / "runtime" / "events.jsonl"
    if not events_file.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in events_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except json.JSONDecodeError:
            continue
    return events


def _load_tasks(repo_path: Path) -> list[dict[str, Any]]:
    graph_file = repo_path / ".workgraph" / "graph.jsonl"
    if not graph_file.exists():
        return []
    tasks: list[dict[str, Any]] = []
    for line in graph_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("status") == "done":
                tasks.append(obj)
        except json.JSONDecodeError:
            continue
    return tasks


def build_agent_history(
    repo_path: Path,
    *,
    limit: int = 20,
    activity_digest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build agent session history from events.jsonl + graph.jsonl.

    Returns a dict with keys: sessions, total_sessions_in_file, history_since.
    """
    raw_events = _load_events(repo_path)
    if not raw_events:
        return {"sessions": [], "total_sessions_in_file": 0, "history_since": None}

    # Filter to session-relevant kinds and sort by ts ascending
    relevant = [
        e for e in raw_events
        if isinstance(e.get("kind"), str) and e["kind"] in _SESSION_KINDS
    ]
    relevant.sort(key=lambda e: float(e.get("ts") or 0))

    # Oldest event timestamp for history_since
    oldest_ts = float(raw_events[0].get("ts") or 0) if raw_events else None

    now = time.time()

    # Step 2: Build session spans
    # open_sessions: actor_id -> _OpenSession (most recent open session)
    open_sessions: dict[str, _OpenSession] = {}
    closed: list[_OpenSession] = []

    for event in relevant:
        kind = event.get("kind", "")
        ts = float(event.get("ts") or 0)
        payload = event.get("payload") or {}

        if kind == "session.started":
            actor_id = str(payload.get("actor_id") or f"anon-{ts:.0f}")
            cli = str(payload.get("cli") or "unknown")
            # If this actor_id already has an open session, close it as unknown
            if actor_id in open_sessions:
                prev = open_sessions.pop(actor_id)
                closed.append(prev)
            open_sessions[actor_id] = _OpenSession(
                session_id=actor_id,
                agent_type=cli,
                started_at=ts,
            )

        elif kind == "session.ended":
            actor_id = str(payload.get("actor_id") or "")
            if actor_id in open_sessions:
                sess = open_sessions.pop(actor_id)
                sess.ended_at = ts
                sess.outcome = "clean_exit"
                closed.append(sess)
            # If actor_id not found, attach to most-recently opened open session
            elif open_sessions:
                last_key = list(open_sessions)[-1]
                sess = open_sessions.pop(last_key)
                sess.ended_at = ts
                sess.outcome = "clean_exit"
                closed.append(sess)

        elif kind in _CRASH_KINDS:
            # Mark the first open session whose window contains this ts
            for sess in open_sessions.values():
                if sess.started_at <= ts:
                    sess.outcome = "crashed"
                    break

        elif kind in _STALL_KINDS:
            for sess in open_sessions.values():
                if sess.started_at <= ts:
                    sess.outcome = "stalled"
                    break

    # Step 3: Infer ends for remaining open sessions
    all_sessions = list(closed)
    open_list = sorted(open_sessions.values(), key=lambda s: s.started_at)
    for i, sess in enumerate(open_list):
        age = now - sess.started_at
        if age < _STILL_RUNNING_THRESHOLD_SECONDS:
            sess.outcome = "still_running"
        else:
            # Infer end from next session's start if available
            if i + 1 < len(open_list):
                sess.ended_at = open_list[i + 1].started_at - 1
            # Otherwise leave ended_at as None
        all_sessions.append(sess)

    total = len(all_sessions)

    # Step 4: Correlate tasks
    tasks = _load_tasks(repo_path)
    if tasks and all_sessions:
        earliest_start = min(s.started_at for s in all_sessions)
        for task in tasks:
            completed_at_iso = task.get("completed_at") or ""
            started_at_iso = task.get("started_at") or ""
            completed_ts = _iso_to_epoch(completed_at_iso)
            started_ts = _iso_to_epoch(started_at_iso)
            if completed_ts is None:
                continue
            if completed_ts < earliest_start:
                continue
            title = str(task.get("title") or "")[:80]
            task_id = str(task.get("id") or "")
            for sess in all_sessions:
                end = sess.ended_at or now
                if sess.started_at <= completed_ts <= end:
                    if task_id not in sess.tasks_completed:
                        sess.tasks_completed.append(task_id)
                        if len(sess.task_titles) < 3:
                            sess.task_titles.append(title)
                    break
                # tasks_claimed: started_at in window but status not done is handled
                # by graph.jsonl pre-filter (we only load done tasks here)
                if started_ts is not None and sess.started_at <= started_ts <= end:
                    if task_id not in sess.tasks_claimed:
                        sess.tasks_claimed.append(task_id)

    # Step 5: Correlate commits from activity_digest
    if activity_digest and all_sessions:
        repo_name = repo_path.name
        repos_list = activity_digest.get("repos") or []
        for repo_entry in repos_list:
            if str(repo_entry.get("name") or "") != repo_name:
                continue
            commits = repo_entry.get("timeline") or []
            for commit in commits:
                commit_ts = _iso_to_epoch(str(commit.get("timestamp") or ""))
                if commit_ts is None:
                    continue
                for sess in all_sessions:
                    end = sess.ended_at or now
                    if sess.started_at <= commit_ts <= end:
                        sess.commits_in_window += 1
                        break

    # Step 6: Sort most-recent first, cap at limit
    all_sessions.sort(key=lambda s: s.started_at, reverse=True)
    capped = all_sessions[:limit]

    def _to_dict(s: _OpenSession) -> dict[str, Any]:
        return {
            "session_id": s.session_id,
            "agent_type": s.agent_type,
            "started_at": _epoch_to_iso(s.started_at),
            "ended_at": _epoch_to_iso(s.ended_at) if s.ended_at is not None else None,
            "duration_seconds": int(s.ended_at - s.started_at) if s.ended_at is not None else None,
            "tasks_completed": s.tasks_completed,
            "tasks_claimed": s.tasks_claimed,
            "commits_in_window": s.commits_in_window,
            "outcome": s.outcome,
            "task_titles": s.task_titles,
        }

    return {
        "sessions": [_to_dict(s) for s in capped],
        "total_sessions_in_file": total,
        "history_since": _epoch_to_iso(oldest_ts) if oldest_ts else None,
    }
```

**Run tests (expect green):**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_agent_history.py -x -v
```

**Commit:**
```bash
git add driftdriver/ecosystem_hub/agent_history.py tests/test_agent_history.py
git commit -m "feat(agent-history): add build_agent_history() pure function with full test suite"
```

---

## Step 3 — Wire `build_agent_history()` into `GET /api/repo/:name` in `api.py`

- [ ] Modify `driftdriver/ecosystem_hub/api.py`

The `GET /api/repo/:name` handler was added by the repo-detail-page plan. Locate the handler block that assembles the per-repo payload dict (look for the route match `route.startswith("/api/repo/")` in `do_GET`). After the existing snapshot and activity-digest reads, add:

```python
from .agent_history import build_agent_history as _build_agent_history

# Inside the GET /api/repo/:name handler, after activity_entry is resolved:
history = _build_agent_history(
    Path(repo_path),
    activity_digest=activity_digest if activity_digest else None,
)
payload["agent_history"] = history
```

The import goes at the top of the import block with the other `.activity_cache` import. The call goes inside the route handler, not at module level.

**Verify the API returns the new field:**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run python -c "
from driftdriver.ecosystem_hub.api import _HubHandler
print('import ok')
"
```

**Manual smoke test (if hub is running):**
```bash
curl -s http://127.0.0.1:8777/api/repo/driftdriver | python3 -m json.tool | grep -A 5 agent_history
```

**Commit:**
```bash
git add driftdriver/ecosystem_hub/api.py
git commit -m "feat(agent-history): wire build_agent_history() into GET /api/repo/:name response"
```

---

## Step 4 — Add `renderAgentHistory()` JS to `dashboard.py`

- [ ] Modify `driftdriver/ecosystem_hub/dashboard.py`

Locate the `renderRepoDetailSections` JS function added in the repo-detail-page plan. Inside it, after the Active Agents section render call, add a call to `renderAgentHistory(data)`.

**New JS function to add inside the `<script>` block:**

```javascript
function renderAgentHistory(data) {
  const history = data.agent_history;
  const container = document.getElementById('section-agent-history');
  if (!container) return;

  if (!history || !history.sessions || history.sessions.length === 0) {
    container.innerHTML = '<p class="muted">No session history recorded. Sessions appear here after an agent runs <code>driftdriver install</code> in this repo.</p>';
    return;
  }

  const sessions = history.sessions;
  const total = history.total_sessions_in_file || sessions.length;
  const since = history.history_since ? new Date(history.history_since).toLocaleDateString() : null;

  const OUTCOME_DOT = {
    clean_exit: '<span style="color:#22c55e;font-size:1.1em;">●</span>',
    crashed:    '<span style="color:#ef4444;font-size:1.1em;">●</span>',
    stalled:    '<span style="color:#f59e0b;font-size:1.1em;">●</span>',
    unknown:    '<span style="color:#6b7280;font-size:1.1em;">●</span>',
    still_running: '<span style="color:#22c55e;font-size:1.1em;animation:pulse 1s infinite;">●</span>',
  };
  const OUTCOME_LABEL = {
    clean_exit: 'clean exit', crashed: 'crashed', stalled: 'stalled',
    unknown: 'unknown', still_running: 'still running',
  };

  function fmtDuration(secs) {
    if (secs == null) return '';
    if (secs < 60) return secs + 's';
    const m = Math.round(secs / 60);
    if (m < 60) return m + 'm';
    const h = Math.floor(m / 60), rm = m % 60;
    return rm > 0 ? h + 'h ' + rm + 'm' : h + 'h';
  }

  let html = `<div class="section-header">Agent History <span class="badge muted">${sessions.length} session${sessions.length !== 1 ? 's' : ''}</span></div>`;
  html += '<div class="agent-history-feed">';

  sessions.forEach((s, idx) => {
    const dot = OUTCOME_DOT[s.outcome] || OUTCOME_DOT.unknown;
    const label = OUTCOME_LABEL[s.outcome] || s.outcome;
    const dur = s.duration_seconds != null ? fmtDuration(s.duration_seconds) : '';
    const taskCount = (s.tasks_completed || []).length;
    const commitCount = s.commits_in_window || 0;
    const titles = (s.task_titles || []).join(', ');
    const titlesStr = titles.length > 120 ? titles.slice(0, 117) + '…' : titles;
    const agentBadge = `<code class="agent-badge">${escHtml(s.agent_type || 'unknown')}</code>`;
    const timeAgo = relTime(s.started_at);

    html += `<div class="history-row" data-idx="${idx}" onclick="toggleHistoryExpand(this)">
      <div class="history-row-main">
        ${dot} ${agentBadge}
        <span class="muted">· ${timeAgo}</span>
        ${dur ? `<span class="muted">· ${dur}</span>` : ''}
        <span class="muted">· ${label}</span>
        <span class="muted">· ${taskCount} task${taskCount !== 1 ? 's' : ''}</span>
        <span class="muted">· ${commitCount} commit${commitCount !== 1 ? 's' : ''}</span>
      </div>`;
    if (titlesStr) {
      html += `<div class="history-row-titles muted">&nbsp;&nbsp;${escHtml(titlesStr)}</div>`;
    }
    // Expanded detail (hidden by default)
    const fullTasks = (s.tasks_completed || []).map(id => {
      const t = (s.task_titles || [])[s.tasks_completed.indexOf(id)];
      return t ? `${escHtml(id)}: ${escHtml(t)}` : escHtml(id);
    }).join('<br>');
    html += `<div class="history-row-expanded" style="display:none" data-session-id="${escHtml(s.session_id)}">
        <div class="muted small">Session: ${escHtml(s.session_id)}</div>
        ${fullTasks ? `<div class="muted small">Tasks:<br>${fullTasks}</div>` : ''}
        <div class="muted small">Outcome: ${label}</div>
      </div>`;
    html += '</div>';
  });

  html += '</div>';

  if (total > sessions.length) {
    const sinceStr = since ? ` · History since ${since}` : '';
    html += `<p class="muted small">Showing ${sessions.length} of ${total} sessions${sinceStr}</p>`;
  }

  container.innerHTML = html;
}

function toggleHistoryExpand(row) {
  const expanded = row.querySelector('.history-row-expanded');
  if (expanded) {
    expanded.style.display = expanded.style.display === 'none' ? 'block' : 'none';
  }
}
```

**New HTML section** — add inside the repo detail view div, between the Active Agents section and the Repo Dependencies section:

```html
<div class="detail-section">
  <h3>Agent History</h3>
  <div id="section-agent-history">
    <p class="muted">Loading agent history…</p>
  </div>
</div>
```

**Call in `renderRepoDetailSections`:**

```javascript
renderAgentHistory(data);
```

**Commit:**
```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat(agent-history): add renderAgentHistory() JS and Agent History section to detail page"
```

---

## Step 5 — Integration test: history in `GET /api/repo/:name` response

- [ ] Add integration test to `tests/test_agent_history.py` (or a new `tests/test_agent_history_api.py`)

```python
class TestAgentHistoryInRepoDetailEndpoint(unittest.TestCase):
    """Verify build_agent_history output shape matches what the API serializes."""

    def test_return_shape_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            _write_events(repo, [
                {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
                 "payload": {"cli": "claude-code", "actor_id": "session-API"}},
                {"kind": "session.ended", "repo": "myrepo", "ts": NOW,
                 "payload": {"actor_id": "session-API"}},
            ])
            result = build_agent_history(repo)
            # Top-level keys
            self.assertIn("sessions", result)
            self.assertIn("total_sessions_in_file", result)
            self.assertIn("history_since", result)
            # Session keys
            s = result["sessions"][0]
            for key in ("session_id", "agent_type", "started_at", "ended_at",
                        "duration_seconds", "tasks_completed", "task_titles",
                        "commits_in_window", "outcome"):
                self.assertIn(key, s, f"Missing key: {key}")
```

**Run full suite:**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_agent_history.py -v
```

---

## Completion Checklist

- [ ] `tests/test_agent_history.py` — all tests green
- [ ] `driftdriver/ecosystem_hub/agent_history.py` — created, passes tests
- [ ] `driftdriver/ecosystem_hub/api.py` — `agent_history` field in `GET /api/repo/:name`
- [ ] `driftdriver/ecosystem_hub/dashboard.py` — `renderAgentHistory()` + section HTML
- [ ] `uv run pytest tests/test_agent_history.py -v` — all green
- [ ] No new external dependencies added
- [ ] No new daemon or write paths introduced
