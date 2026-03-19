# ABOUTME: Tests for the task routing layer — dispatches workgraph tasks to executors based on tags.
# ABOUTME: Covers config loading, tag matching, scheduling, ready-task detection, claiming, and dispatch.

from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from driftdriver.task_router import (
    DispatchResult,
    ExecutorConfig,
    RoutingConfig,
    claim_task,
    dispatch_task,
    load_routing_config,
    match_executor,
    route_ready_tasks,
    route_ecosystem,
    _find_ready_tasks,
    _parse_schedule_tag,
)


# ---------------------------------------------------------------------------
# LoadConfigTests
# ---------------------------------------------------------------------------
class TestLoadConfig(unittest.TestCase):
    """Load routing config from drift-policy.toml."""

    def test_load_full_config(self) -> None:
        toml_content = b"""
[routing]
enabled = true
default_executor = "wg-daemon"

[routing.executors.samantha]
type = "http"
endpoint = "http://localhost:3530/api/agent/task"
tag_match = "agent:samantha"

[routing.executors.derek]
type = "http"
endpoint = "http://localhost:3531/api/agent/task"
tag_match = "agent:derek"
"""
        with tempfile.TemporaryDirectory() as td:
            policy = Path(td) / "drift-policy.toml"
            policy.write_bytes(toml_content)
            config = load_routing_config(policy)

        self.assertTrue(config.enabled)
        self.assertEqual(config.default_executor, "wg-daemon")
        self.assertEqual(len(config.executors), 2)
        self.assertEqual(config.executors["samantha"].type, "http")
        self.assertEqual(
            config.executors["samantha"].endpoint,
            "http://localhost:3530/api/agent/task",
        )
        self.assertEqual(config.executors["samantha"].tag_match, "agent:samantha")

    def test_defaults_when_section_missing(self) -> None:
        toml_content = b"""
[lanes]
enabled = true
"""
        with tempfile.TemporaryDirectory() as td:
            policy = Path(td) / "drift-policy.toml"
            policy.write_bytes(toml_content)
            config = load_routing_config(policy)

        self.assertFalse(config.enabled)
        self.assertEqual(config.default_executor, "wg-daemon")
        self.assertEqual(config.executors, {})

    def test_defaults_when_file_missing(self) -> None:
        config = load_routing_config(Path("/nonexistent/drift-policy.toml"))
        self.assertFalse(config.enabled)

    def test_multiple_executor_types(self) -> None:
        toml_content = b"""
[routing]
enabled = true
default_executor = "claude"

[routing.executors.sam]
type = "http"
endpoint = "http://localhost:3530/api/agent/task"
tag_match = "agent:samantha"

[routing.executors.nightly]
type = "schedule"
endpoint = ""
tag_match = "schedule:*"

[routing.executors.dev]
type = "claude"
endpoint = ""
tag_match = "executor:claude"
"""
        with tempfile.TemporaryDirectory() as td:
            policy = Path(td) / "drift-policy.toml"
            policy.write_bytes(toml_content)
            config = load_routing_config(policy)

        self.assertEqual(len(config.executors), 3)
        self.assertEqual(config.executors["nightly"].type, "schedule")
        self.assertEqual(config.executors["dev"].type, "claude")


# ---------------------------------------------------------------------------
# MatchExecutorTests
# ---------------------------------------------------------------------------
class TestMatchExecutor(unittest.TestCase):
    """Match tasks to executors based on tags."""

    def _make_config(self) -> RoutingConfig:
        return RoutingConfig(
            enabled=True,
            default_executor="wg-daemon",
            executors={
                "samantha": ExecutorConfig(
                    name="samantha",
                    type="http",
                    endpoint="http://localhost:3530/api/agent/task",
                    tag_match="agent:samantha",
                ),
                "derek": ExecutorConfig(
                    name="derek",
                    type="http",
                    endpoint="http://localhost:3531/api/agent/task",
                    tag_match="agent:derek",
                ),
                "scheduled": ExecutorConfig(
                    name="scheduled",
                    type="schedule",
                    endpoint="",
                    tag_match="schedule:*",
                ),
            },
        )

    def test_exact_tag_match(self) -> None:
        config = self._make_config()
        task = {"id": "t1", "tags": ["agent:samantha", "priority:high"]}
        result = match_executor(task, config)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "samantha")

    def test_wildcard_match(self) -> None:
        config = self._make_config()
        task = {"id": "t2", "tags": ["schedule:14:00"]}
        result = match_executor(task, config)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "scheduled")

    def test_no_match_returns_none(self) -> None:
        config = self._make_config()
        task = {"id": "t3", "tags": ["priority:high"]}
        result = match_executor(task, config)
        self.assertIsNone(result)

    def test_first_match_wins(self) -> None:
        config = self._make_config()
        task = {"id": "t4", "tags": ["agent:samantha", "agent:derek"]}
        result = match_executor(task, config)
        self.assertIsNotNone(result)
        # samantha should match first (dict iteration order in Python 3.7+)
        self.assertEqual(result.name, "samantha")

    def test_no_tags_returns_none(self) -> None:
        config = self._make_config()
        task = {"id": "t5", "tags": []}
        result = match_executor(task, config)
        self.assertIsNone(result)

    def test_missing_tags_key_returns_none(self) -> None:
        config = self._make_config()
        task = {"id": "t6"}
        result = match_executor(task, config)
        self.assertIsNone(result)

    def test_agent_owner_matches_executor_without_tags(self) -> None:
        config = self._make_config()
        task = {"id": "t7", "agent": "samantha"}
        result = match_executor(task, config)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "samantha")

    def test_agent_owner_overrides_conflicting_tags(self) -> None:
        config = self._make_config()
        task = {"id": "t8", "agent": "samantha", "tags": ["agent:derek"]}
        result = match_executor(task, config)
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "samantha")

    def test_wildcard_with_various_suffixes(self) -> None:
        config = self._make_config()
        for tag in ["schedule:09:00", "schedule:2026-03-17T09:00", "schedule:daily"]:
            task = {"id": "t", "tags": [tag]}
            result = match_executor(task, config)
            self.assertIsNotNone(result, f"Expected match for tag {tag}")
            self.assertEqual(result.name, "scheduled")


# ---------------------------------------------------------------------------
# ScheduleTests
# ---------------------------------------------------------------------------
class TestScheduleParsing(unittest.TestCase):
    """Parse schedule tags and check if dispatch time has arrived."""

    def test_parse_time_only(self) -> None:
        dt = _parse_schedule_tag("schedule:14:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 14)
        self.assertEqual(dt.minute, 0)

    def test_parse_iso_datetime(self) -> None:
        dt = _parse_schedule_tag("schedule:2026-03-17T09:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 3)
        self.assertEqual(dt.day, 17)
        self.assertEqual(dt.hour, 9)

    def test_parse_invalid_returns_none(self) -> None:
        dt = _parse_schedule_tag("schedule:whenever")
        self.assertIsNone(dt)

    def test_parse_non_schedule_tag_returns_none(self) -> None:
        dt = _parse_schedule_tag("agent:samantha")
        self.assertIsNone(dt)

    def test_schedule_time_arrived(self) -> None:
        """If current time is past schedule, dispatch should proceed."""
        # Set schedule to 1 hour ago
        past = datetime.now(timezone.utc).replace(
            hour=(datetime.now(timezone.utc).hour - 1) % 24
        )
        tag = f"schedule:{past.strftime('%H:%M')}"
        dt = _parse_schedule_tag(tag)
        self.assertIsNotNone(dt)

    def test_schedule_iso_in_past(self) -> None:
        dt = _parse_schedule_tag("schedule:2020-01-01T00:00")
        self.assertIsNotNone(dt)
        self.assertTrue(dt < datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# FindReadyTests
# ---------------------------------------------------------------------------
class TestFindReadyTasks(unittest.TestCase):
    """Find tasks that are open with all dependencies met."""

    def test_open_no_deps(self) -> None:
        tasks = [
            {"kind": "task", "id": "t1", "status": "open"},
        ]
        ready = _find_ready_tasks(tasks)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["id"], "t1")

    def test_open_with_met_deps(self) -> None:
        tasks = [
            {"kind": "task", "id": "t1", "status": "done"},
            {"kind": "task", "id": "t2", "status": "open", "after": ["t1"]},
        ]
        ready = _find_ready_tasks(tasks)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["id"], "t2")

    def test_open_with_unmet_deps(self) -> None:
        tasks = [
            {"kind": "task", "id": "t1", "status": "in-progress"},
            {"kind": "task", "id": "t2", "status": "open", "after": ["t1"]},
        ]
        ready = _find_ready_tasks(tasks)
        self.assertEqual(len(ready), 0)

    def test_paused_skipped(self) -> None:
        tasks = [
            {"kind": "task", "id": "t1", "status": "open", "paused": True},
        ]
        ready = _find_ready_tasks(tasks)
        self.assertEqual(len(ready), 0)

    def test_non_task_ignored(self) -> None:
        tasks = [
            {"kind": "note", "id": "n1", "status": "open"},
            {"kind": "task", "id": "t1", "status": "open"},
        ]
        ready = _find_ready_tasks(tasks)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["id"], "t1")

    def test_abandoned_dep_counts_as_met(self) -> None:
        tasks = [
            {"kind": "task", "id": "t1", "status": "abandoned"},
            {"kind": "task", "id": "t2", "status": "open", "after": ["t1"]},
        ]
        ready = _find_ready_tasks(tasks)
        self.assertEqual(len(ready), 1)

    def test_multiple_deps_all_must_be_met(self) -> None:
        tasks = [
            {"kind": "task", "id": "t1", "status": "done"},
            {"kind": "task", "id": "t2", "status": "in-progress"},
            {"kind": "task", "id": "t3", "status": "open", "after": ["t1", "t2"]},
        ]
        ready = _find_ready_tasks(tasks)
        self.assertEqual(len(ready), 0)

    def test_in_progress_not_ready(self) -> None:
        tasks = [
            {"kind": "task", "id": "t1", "status": "in-progress"},
        ]
        ready = _find_ready_tasks(tasks)
        self.assertEqual(len(ready), 0)


# ---------------------------------------------------------------------------
# ClaimTests
# ---------------------------------------------------------------------------
class TestClaimTask(unittest.TestCase):
    """Claim a task by updating graph.jsonl."""

    def _write_graph(self, td: str, tasks: list[dict]) -> Path:
        wg_dir = Path(td) / ".workgraph"
        wg_dir.mkdir(parents=True)
        graph_path = wg_dir / "graph.jsonl"
        graph_path.write_text(
            "\n".join(json.dumps(t) for t in tasks) + "\n",
            encoding="utf-8",
        )
        return Path(td)

    def test_claim_open_task(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self._write_graph(td, [
                {"kind": "task", "id": "t1", "status": "open", "title": "Do stuff"},
            ])
            result = claim_task("t1", repo)
            self.assertTrue(result)

            # Verify the graph was updated
            lines = (repo / ".workgraph" / "graph.jsonl").read_text().splitlines()
            task = json.loads(lines[0])
            self.assertEqual(task["status"], "in-progress")
            self.assertIn("started_at", task)

    def test_claim_already_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self._write_graph(td, [
                {"kind": "task", "id": "t1", "status": "in-progress", "title": "Busy"},
            ])
            result = claim_task("t1", repo)
            self.assertFalse(result)

    def test_claim_nonexistent_task(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self._write_graph(td, [
                {"kind": "task", "id": "t1", "status": "open", "title": "Do stuff"},
            ])
            result = claim_task("t999", repo)
            self.assertFalse(result)

    def test_claim_preserves_other_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = self._write_graph(td, [
                {"kind": "task", "id": "t1", "status": "open", "title": "First"},
                {"kind": "task", "id": "t2", "status": "done", "title": "Second"},
            ])
            claim_task("t1", repo)
            lines = (repo / ".workgraph" / "graph.jsonl").read_text().splitlines()
            t2 = json.loads(lines[1])
            self.assertEqual(t2["status"], "done")


# ---------------------------------------------------------------------------
# DispatchTests
# ---------------------------------------------------------------------------
class TestDispatchTask(unittest.TestCase):
    """Dispatch tasks to different executor types."""

    def _make_task(self, **overrides: object) -> dict:
        base = {
            "id": "t1",
            "title": "Test task",
            "description": "Do the thing",
            "tags": ["agent:samantha"],
        }
        base.update(overrides)
        return base

    @patch("driftdriver.task_router.urlopen")
    def test_http_dispatch(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"ok": true}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        executor = ExecutorConfig(
            name="samantha",
            type="http",
            endpoint="http://localhost:3530/api/agent/task",
            tag_match="agent:samantha",
        )
        task = self._make_task()
        result = dispatch_task(task, executor, Path("/fake/repo"))

        self.assertTrue(result.dispatched)
        self.assertEqual(result.executor, "samantha")
        self.assertIsNone(result.error)
        mock_urlopen.assert_called_once()

    @patch("driftdriver.task_router.urlopen")
    def test_http_dispatch_error(self, mock_urlopen: MagicMock) -> None:
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("Connection refused")

        executor = ExecutorConfig(
            name="samantha",
            type="http",
            endpoint="http://localhost:3530/api/agent/task",
            tag_match="agent:samantha",
        )
        result = dispatch_task(self._make_task(), executor, Path("/fake/repo"))

        self.assertFalse(result.dispatched)
        self.assertIn("Connection refused", result.error)

    def test_wg_daemon_returns_skip(self) -> None:
        executor = ExecutorConfig(
            name="default",
            type="wg-daemon",
            endpoint="",
            tag_match="",
        )
        result = dispatch_task(self._make_task(), executor, Path("/fake/repo"))

        self.assertFalse(result.dispatched)
        self.assertIsNotNone(result.skipped_reason)
        self.assertIn("wg-daemon", result.skipped_reason)

    def test_schedule_not_arrived(self) -> None:
        # Schedule far in the future
        executor = ExecutorConfig(
            name="scheduled",
            type="schedule",
            endpoint="",
            tag_match="schedule:*",
        )
        task = self._make_task(tags=["schedule:2099-12-31T23:59"])
        result = dispatch_task(task, executor, Path("/fake/repo"))

        self.assertFalse(result.dispatched)
        self.assertIsNotNone(result.skipped_reason)
        self.assertIn("not yet", result.skipped_reason.lower())

    def test_schedule_arrived(self) -> None:
        executor = ExecutorConfig(
            name="scheduled",
            type="schedule",
            endpoint="",
            tag_match="schedule:*",
        )
        task = self._make_task(tags=["schedule:2020-01-01T00:00"])
        result = dispatch_task(task, executor, Path("/fake/repo"))

        self.assertTrue(result.dispatched)
        self.assertEqual(result.executor, "scheduled")

    def test_schedule_no_matching_tag(self) -> None:
        executor = ExecutorConfig(
            name="scheduled",
            type="schedule",
            endpoint="",
            tag_match="schedule:*",
        )
        task = self._make_task(tags=["priority:high"])
        result = dispatch_task(task, executor, Path("/fake/repo"))

        self.assertFalse(result.dispatched)
        self.assertIsNotNone(result.skipped_reason)

    @patch("driftdriver.task_router.subprocess")
    def test_claude_dispatch(self, mock_subprocess: MagicMock) -> None:
        mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="Spawned agent-1", stderr="")

        executor = ExecutorConfig(
            name="claude-runner",
            type="claude",
            endpoint="",
            tag_match="executor:claude",
        )
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph").mkdir()
            result = dispatch_task(self._make_task(), executor, repo)

        self.assertTrue(result.dispatched)
        self.assertEqual(result.executor, "claude-runner")
        mock_subprocess.run.assert_called_once()

    def test_unknown_executor_type(self) -> None:
        executor = ExecutorConfig(
            name="mystery",
            type="carrier-pigeon",
            endpoint="",
            tag_match="bird:*",
        )
        result = dispatch_task(self._make_task(), executor, Path("/fake/repo"))

        self.assertFalse(result.dispatched)
        self.assertIn("unknown executor type", result.error.lower())


# ---------------------------------------------------------------------------
# RouteReadyTests (end-to-end with multiple tasks and executors)
# ---------------------------------------------------------------------------
class TestRouteReadyTasks(unittest.TestCase):
    """End-to-end routing: find ready tasks, match executors, claim, dispatch."""

    def _setup_repo(self, td: str, tasks: list[dict], toml_bytes: bytes) -> Path:
        repo = Path(td) / "my-repo"
        wg_dir = repo / ".workgraph"
        wg_dir.mkdir(parents=True)
        (wg_dir / "graph.jsonl").write_text(
            "\n".join(json.dumps(t) for t in tasks) + "\n",
            encoding="utf-8",
        )
        (wg_dir / "drift-policy.toml").write_bytes(toml_bytes)
        return repo

    @patch("driftdriver.task_router.urlopen")
    def test_routes_multiple_tasks(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"ok": true}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        tasks = [
            {
                "kind": "task",
                "id": "t1",
                "status": "open",
                "title": "Samantha task",
                "description": "Do stuff",
                "tags": ["agent:samantha"],
            },
            {
                "kind": "task",
                "id": "t2",
                "status": "open",
                "title": "Unrouted task",
                "description": "Generic work",
                "tags": ["priority:high"],
            },
        ]
        toml = b"""
[routing]
enabled = true
default_executor = "wg-daemon"

[routing.executors.samantha]
type = "http"
endpoint = "http://localhost:3530/api/agent/task"
tag_match = "agent:samantha"
"""
        with tempfile.TemporaryDirectory() as td:
            repo = self._setup_repo(td, tasks, toml)
            config = load_routing_config(repo / ".workgraph" / "drift-policy.toml")
            results = route_ready_tasks(repo, config)

        # t1 should be dispatched to samantha
        dispatched = [r for r in results if r.dispatched]
        skipped = [r for r in results if not r.dispatched]

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0].task_id, "t1")
        self.assertEqual(dispatched[0].executor, "samantha")

        # t2 should fall through to default (wg-daemon → skipped)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].task_id, "t2")

    def test_respects_dependencies(self) -> None:
        tasks = [
            {
                "kind": "task",
                "id": "t1",
                "status": "in-progress",
                "title": "Blocker",
                "tags": [],
            },
            {
                "kind": "task",
                "id": "t2",
                "status": "open",
                "title": "Blocked",
                "description": "Depends on t1",
                "tags": ["agent:samantha"],
                "after": ["t1"],
            },
        ]
        toml = b"""
[routing]
enabled = true
default_executor = "wg-daemon"

[routing.executors.samantha]
type = "http"
endpoint = "http://localhost:3530/api/agent/task"
tag_match = "agent:samantha"
"""
        with tempfile.TemporaryDirectory() as td:
            repo = self._setup_repo(td, tasks, toml)
            config = load_routing_config(repo / ".workgraph" / "drift-policy.toml")
            results = route_ready_tasks(repo, config)

        self.assertEqual(len(results), 0)

    @patch("driftdriver.task_router.urlopen")
    def test_claims_before_dispatch(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"ok": true}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        tasks = [
            {
                "kind": "task",
                "id": "t1",
                "status": "open",
                "title": "Route me",
                "tags": ["agent:samantha"],
            },
        ]
        toml = b"""
[routing]
enabled = true
default_executor = "wg-daemon"

[routing.executors.samantha]
type = "http"
endpoint = "http://localhost:3530/api/agent/task"
tag_match = "agent:samantha"
"""
        with tempfile.TemporaryDirectory() as td:
            repo = self._setup_repo(td, tasks, toml)
            config = load_routing_config(repo / ".workgraph" / "drift-policy.toml")
            route_ready_tasks(repo, config)

            # Verify the task was claimed (in-progress)
            lines = (repo / ".workgraph" / "graph.jsonl").read_text().splitlines()
            task = json.loads(lines[0])
            self.assertEqual(task["status"], "in-progress")

    @patch("driftdriver.task_router.urlopen")
    def test_routes_explicit_owner_without_tag_using_executor_match(
        self, mock_urlopen: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"ok": true}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        tasks = [
            {
                "kind": "task",
                "id": "t1",
                "status": "open",
                "title": "Owned by Samantha",
                "agent": "samantha",
            },
        ]
        toml = b"""
[routing]
enabled = true
default_executor = "wg-daemon"

[routing.executors.samantha]
type = "http"
endpoint = "http://localhost:3530/api/agent/task"
tag_match = "agent:samantha"
"""
        with tempfile.TemporaryDirectory() as td:
            repo = self._setup_repo(td, tasks, toml)
            config = load_routing_config(repo / ".workgraph" / "drift-policy.toml")
            results = route_ready_tasks(repo, config)

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].dispatched)
        self.assertEqual(results[0].executor, "samantha")

    @patch("driftdriver.task_router.subprocess")
    def test_holds_explicit_owner_without_executor_instead_of_falling_back(
        self, mock_subprocess: MagicMock
    ) -> None:
        tasks = [
            {
                "kind": "task",
                "id": "t1",
                "status": "open",
                "title": "Owned by Braydon",
                "agent": "braydon",
            },
        ]
        toml = b"""
[routing]
enabled = true
default_executor = "claude"

[routing.executors.samantha]
type = "http"
endpoint = "http://localhost:3530/api/agent/task"
tag_match = "agent:samantha"
"""
        with tempfile.TemporaryDirectory() as td:
            repo = self._setup_repo(td, tasks, toml)
            config = load_routing_config(repo / ".workgraph" / "drift-policy.toml")
            results = route_ready_tasks(repo, config)

            lines = (repo / ".workgraph" / "graph.jsonl").read_text().splitlines()
            task = json.loads(lines[0])

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].dispatched)
        self.assertEqual(results[0].executor, "braydon")
        self.assertIn("holding for manual work", results[0].skipped_reason)
        self.assertEqual(task["status"], "open")
        mock_subprocess.run.assert_not_called()


# ---------------------------------------------------------------------------
# RouteEcosystemTests
# ---------------------------------------------------------------------------
class TestRouteEcosystem(unittest.TestCase):
    """Route tasks across multiple repos."""

    @patch("driftdriver.task_router.urlopen")
    def test_iterates_repos(self, mock_urlopen: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"ok": true}'
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        toml = b"""
[routing]
enabled = true
default_executor = "wg-daemon"

[routing.executors.samantha]
type = "http"
endpoint = "http://localhost:3530/api/agent/task"
tag_match = "agent:samantha"
"""
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            # Create two repos
            for repo_name in ["repo-a", "repo-b"]:
                wg_dir = workspace / repo_name / ".workgraph"
                wg_dir.mkdir(parents=True)
                (wg_dir / "graph.jsonl").write_text(
                    json.dumps({
                        "kind": "task",
                        "id": f"{repo_name}-t1",
                        "status": "open",
                        "title": "Task",
                        "tags": ["agent:samantha"],
                    }) + "\n"
                )
                (wg_dir / "drift-policy.toml").write_bytes(toml)

            config = load_routing_config(
                workspace / "repo-a" / ".workgraph" / "drift-policy.toml"
            )
            summary = route_ecosystem(
                workspace, config, repo_names=["repo-a", "repo-b"]
            )

        self.assertIn("repo-a", summary)
        self.assertIn("repo-b", summary)
        self.assertEqual(summary["repo-a"]["dispatched"], 1)
        self.assertEqual(summary["repo-b"]["dispatched"], 1)

    def test_skips_repos_without_workgraph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            (workspace / "no-wg-repo").mkdir()

            config = RoutingConfig(
                enabled=True, default_executor="wg-daemon", executors={}
            )
            summary = route_ecosystem(
                workspace, config, repo_names=["no-wg-repo"]
            )

        self.assertEqual(summary.get("no-wg-repo", {}).get("skipped"), True)


if __name__ == "__main__":
    unittest.main()
