from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from typing import Any
from unittest.mock import patch
from urllib.request import urlopen
from pathlib import Path

import driftdriver.ecosystem_hub as ecosystem_hub_module
from driftdriver.ecosystem_hub import (
    _SUPERVISOR_LAST_ATTEMPT,
    _compute_ready_tasks,
    _load_ecosystem_repos,
    UpstreamCandidate,
    apply_upstream_automation,
    build_draft_pr_requests,
    collect_ecosystem_snapshot,
    collect_repo_snapshot,
    generate_upstream_candidates,
    read_service_status,
    resolve_central_repo_path,
    render_dashboard_html,
    render_upstream_packets,
    run_draft_pr_requests,
    start_service_process,
    supervise_repo_services,
    stop_service_process,
    write_snapshot_once,
)


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True, capture_output=True)
    (path / "README.md").write_text("# repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), check=True, capture_output=True)


def _write_graph(path: Path, tasks: list[dict]) -> None:
    wg_dir = path / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    graph = wg_dir / "graph.jsonl"
    lines = [json.dumps({**task, "type": "task"}) for task in tasks]
    graph.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_policy(path: Path, content: str | None = None) -> None:
    wg_dir = path / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    policy = wg_dir / "drift-policy.toml"
    policy.write_text(
        content
        or (
            'schema = 1\n'
            'mode = "redirect"\n'
            'order = ["coredrift", "specdrift", "datadrift", "depsdrift", "uxdrift", "therapydrift", "yagnidrift", "redrift"]\n'
        ),
        encoding="utf-8",
    )


def _recv_exact(sock_obj: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        part = sock_obj.recv(count - len(chunks))
        if not part:
            raise RuntimeError("socket_closed")
        chunks.extend(part)
    return bytes(chunks)


def _ws_connect(port: int) -> tuple[socket.socket, bytes]:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    sock_obj = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    request = (
        "GET /ws/status HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock_obj.sendall(request.encode("utf-8"))
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock_obj.recv(4096)
        if not chunk:
            raise RuntimeError("websocket_handshake_failed")
        response += chunk
    if b"101 Switching Protocols" not in response:
        raise RuntimeError(f"unexpected_handshake:{response.decode('utf-8', errors='replace')}")
    # On loopback the server often flushes the 101 headers and the initial WS
    # frame in a single TCP segment.  Bytes after \r\n\r\n are the start of the
    # first WebSocket frame — capture them so they aren't silently discarded.
    _, _, leftover = response.partition(b"\r\n\r\n")
    return sock_obj, leftover


def _ws_read_text(sock_obj: socket.socket, timeout: float = 3.0, *, prefix: bytes = b"") -> str:
    sock_obj.settimeout(timeout)
    _buf = bytearray(prefix)

    def _read(count: int) -> bytes:
        result = bytearray()
        while len(result) < count:
            needed = count - len(result)
            if _buf:
                take = min(len(_buf), needed)
                result.extend(_buf[:take])
                del _buf[:take]
            else:
                part = sock_obj.recv(needed)
                if not part:
                    raise RuntimeError("socket_closed")
                result.extend(part)
        return bytes(result)

    header = _read(2)
    first, second = header[0], header[1]
    opcode = first & 0x0F
    if opcode != 0x1:
        raise RuntimeError(f"unexpected_opcode:{opcode}")
    size = second & 0x7F
    if size == 126:
        size = struct.unpack("!H", _read(2))[0]
    elif size == 127:
        size = struct.unpack("!Q", _read(8))[0]
    payload = _read(size) if size else b""
    return payload.decode("utf-8")


class SubpackageBoundaryTests(unittest.TestCase):
    """Smoke tests ensuring hub subpackage re-exports work correctly."""

    def test_submodule_re_exports_accessible_from_package(self) -> None:
        """Key names should be importable from driftdriver.ecosystem_hub directly."""
        from driftdriver.ecosystem_hub import (
            RepoSnapshot,
            _run,
            collect_repo_snapshot,
            supervise_repo_services,
            render_dashboard_html,
            main,
            LiveStreamHub,
        )
        self.assertTrue(callable(_run))
        self.assertTrue(callable(collect_repo_snapshot))
        self.assertTrue(callable(supervise_repo_services))
        self.assertTrue(callable(render_dashboard_html))
        self.assertTrue(callable(main))
        self.assertIsNotNone(RepoSnapshot)
        self.assertIsNotNone(LiveStreamHub)

    def test_submodules_importable_directly(self) -> None:
        """Each submodule should be importable on its own."""
        from driftdriver.ecosystem_hub import models, discovery, snapshot, websocket, api, dashboard, server
        self.assertTrue(hasattr(models, 'RepoSnapshot'))
        self.assertTrue(hasattr(discovery, '_run'))
        self.assertTrue(hasattr(snapshot, 'collect_repo_snapshot'))
        self.assertTrue(hasattr(websocket, 'LiveStreamHub'))
        self.assertTrue(hasattr(api, '_HubHandler'))
        self.assertTrue(hasattr(dashboard, 'render_dashboard_html'))
        self.assertTrue(hasattr(server, 'main'))


class EcosystemHubTests(unittest.TestCase):
    def test_load_ecosystem_repos_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "speedrift-ecosystem").mkdir(parents=True)
            toml_path = root / "speedrift-ecosystem" / "ecosystem.toml"
            toml_path.write_text(
                "schema = 1\n"
                "[repos.driftdriver]\nrole='orchestrator'\nurl='https://example.com'\n"
                "[repos.coredrift]\nrole='baseline'\nurl='https://example.com'\n",
                encoding="utf-8",
            )
            repos = _load_ecosystem_repos(toml_path, root)
            self.assertEqual(set(repos.keys()), {"driftdriver", "coredrift"})
            self.assertEqual(repos["driftdriver"], root / "driftdriver")

    def test_load_ecosystem_repos_supports_explicit_repo_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Relative paths resolve from ecosystem.toml's parent directory
            toml_dir = root / "speedrift-ecosystem"
            toml_dir.mkdir(parents=True)
            toml_path = toml_dir / "ecosystem.toml"
            toml_path.write_text(
                "schema = 1\n"
                "[repos.atlas_product]\n"
                "role='product'\n"
                "path='../outside-repo'\n",
                encoding="utf-8",
            )
            repos = _load_ecosystem_repos(toml_path, root)
            expected = (toml_dir / "../outside-repo").resolve()
            self.assertEqual(repos["atlas_product"], expected)

    def test_discover_active_workspace_repos_includes_all_matching_repos_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            expected: set[str] = set()
            for idx in range(18):
                repo = root / f"repo-{idx:02d}"
                repo.mkdir(parents=True)
                _init_repo(repo)
                _write_graph(repo, [{"id": "t1", "title": "ready", "status": "open"}])
                _write_policy(repo)
                expected.add(repo.name)

            discovered = ecosystem_hub_module._discover_active_workspace_repos(root, existing=set())
            self.assertEqual(set(discovered.keys()), expected)

    def test_compute_ready_tasks_respects_dependencies(self) -> None:
        tasks = {
            "root": {"id": "root", "status": "done", "title": "Root"},
            "a": {"id": "a", "status": "open", "title": "A", "after": ["root"]},
            "b": {"id": "b", "status": "open", "title": "B", "after": ["a"]},
        }
        ready = _compute_ready_tasks(tasks)
        ids = [t["id"] for t in ready]
        self.assertEqual(ids, ["a"])

    def test_collect_repo_snapshot_missing_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope"
            snap = collect_repo_snapshot("missing", missing)
            self.assertFalse(snap.exists)
            self.assertIn("repo_missing", snap.errors)

    def test_collect_repo_snapshot_includes_ready_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            (repo / "README.md").write_text("## North Star\nBuild a durable test system.\n", encoding="utf-8")
            _write_graph(
                repo,
                [
                    {"id": "t0", "title": "done", "status": "done"},
                    {"id": "t1", "title": "ready", "status": "open", "after": ["t0"]},
                    {"id": "t2", "title": "blocked", "status": "open", "after": ["t1"]},
                    {"id": "t3", "title": "active", "status": "in-progress"},
                ],
            )
            snap = collect_repo_snapshot("repo", repo)
            self.assertTrue(snap.exists)
            self.assertTrue(snap.workgraph_exists)
            self.assertEqual(len(snap.in_progress), 1)
            self.assertEqual([t["id"] for t in snap.ready], ["t1"])
            self.assertTrue(snap.repo_north_star["present"])
            self.assertEqual(snap.repo_north_star["status"], "present")

    def test_collect_repo_snapshot_reads_runtime_supervisor_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            _write_graph(repo, [{"id": "t1", "title": "active", "status": "in-progress"}])
            runtime_dir = repo / ".workgraph" / "service" / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "current.json").write_text(
                json.dumps(
                    {
                        "repo": "repo",
                        "daemon_state": "running",
                        "active_workers": [
                            {
                                "worker_id": "repo-t1-worker",
                                "task_id": "t1",
                                "runtime": "claude",
                                "state": "running",
                            }
                        ],
                        "stalled_task_ids": [],
                        "next_action": "continue supervision",
                    }
                ),
                encoding="utf-8",
            )

            snap = collect_repo_snapshot("repo", repo)
            self.assertEqual(snap.runtime["daemon_state"], "running")
            self.assertEqual(len(snap.runtime["active_workers"]), 1)
            self.assertEqual(snap.activity_state, "active")

    def test_collect_repo_snapshot_uses_ancestor_git_and_workgraph_for_nested_component(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "paia-agents"
            repo.mkdir(parents=True)
            _init_repo(repo)
            agent_dir = repo / "caroline"
            agent_dir.mkdir(parents=True)
            (agent_dir / "README.md").write_text("## North Star\nStay helpful.\n", encoding="utf-8")
            _write_graph(repo, [{"id": "t1", "title": "done", "status": "done"}])
            runtime_dir = repo / ".workgraph" / "service" / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "current.json").write_text(
                json.dumps(
                    {
                        "repo": "paia-agents",
                        "daemon_state": "idle",
                        "active_workers": [],
                        "stalled_task_ids": [],
                        "next_action": "await new ready work",
                    }
                ),
                encoding="utf-8",
            )

            real_run = ecosystem_hub_module._run

            def fake_run(cmd, cwd=None, timeout=None):  # type: ignore[no-untyped-def]
                cmd_list = [str(part) for part in cmd]
                if cmd_list[:6] == [
                    "wg",
                    "--dir",
                    str(repo / ".workgraph"),
                    "service",
                    "status",
                    "--json",
                ]:
                    return (0, json.dumps({"status": "not_running", "running": False}), "")
                return real_run(cmd, cwd=cwd, timeout=timeout)

            with patch("driftdriver.ecosystem_hub._run", side_effect=fake_run):
                snap = collect_repo_snapshot("caroline", agent_dir)

            self.assertTrue(snap.exists)
            self.assertTrue(snap.workgraph_exists)
            self.assertTrue(snap.wg_available)
            self.assertTrue(snap.service_running)
            self.assertEqual(snap.runtime["daemon_state"], "idle")
            self.assertNotIn("not_a_git_repo", snap.errors)
            self.assertEqual(snap.activity_state, "idle")

    def test_collect_repo_snapshot_marks_in_progress_without_live_workers_stalled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            _write_graph(
                repo,
                [
                    {
                        "id": "t1",
                        "title": "active",
                        "status": "in-progress",
                        "created_at": "2026-03-01T00:00:00+00:00",
                    }
                ],
            )

            real_run = ecosystem_hub_module._run

            def fake_run(cmd, cwd=None, timeout=None):  # type: ignore[no-untyped-def]
                cmd_list = [str(part) for part in cmd]
                if cmd_list[:6] == [
                    "wg",
                    "--dir",
                    str(repo / ".workgraph"),
                    "service",
                    "status",
                    "--json",
                ]:
                    return (
                        0,
                        json.dumps(
                            {
                                "status": "running",
                                "pid": 12345,
                                "agents": {
                                    "agents_defined": False,
                                    "alive": 0,
                                    "idle": 0,
                                    "total": 8,
                                },
                                "coordinator": {
                                    "enabled": True,
                                    "tasks_ready": 0,
                                },
                                "warning": "No agents defined — run 'wg agency init' or 'wg agent create'",
                            }
                        ),
                        "",
                    )
                return real_run(cmd, cwd=cwd, timeout=timeout)

            with patch("driftdriver.ecosystem_hub._run", side_effect=fake_run):
                snap = collect_repo_snapshot("repo", repo)

            self.assertEqual(snap.activity_state, "stalled")
            self.assertTrue(snap.stalled)
            self.assertIn("workgraph service running but no live agents", snap.stall_reasons)
            self.assertEqual(snap.service_status.get("agents", {}).get("alive"), 0)

    def test_collect_repo_snapshot_marks_missing_repo_north_star(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            _write_graph(repo, [{"id": "t1", "title": "ready", "status": "open"}])
            snap = collect_repo_snapshot("repo", repo)
            self.assertFalse(snap.repo_north_star["present"])
            self.assertEqual(snap.repo_north_star["status"], "missing")
            self.assertIsInstance(snap.task_graph_nodes, list)
            self.assertIsInstance(snap.task_graph_edges, list)
            self.assertIn("repo:", snap.narrative)

    def test_collect_repo_snapshot_marks_stalled_when_open_without_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            _write_graph(
                repo,
                [
                    {"id": "d0", "title": "done", "status": "done"},
                    {"id": "o1", "title": "open", "status": "open", "after": ["d0"]},
                    {"id": "o2", "title": "blocked", "status": "open", "after": ["missing-task"]},
                ],
            )
            snap = collect_repo_snapshot("repo", repo)
            self.assertEqual(snap.activity_state, "stalled")
            self.assertTrue(snap.stalled)
            self.assertGreater(len(snap.stall_reasons), 0)
            self.assertIn("stalled", snap.narrative)
            self.assertIn("control mode observe", snap.stall_reasons[0])

    def test_collect_repo_snapshot_marks_idle_when_only_done_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            _write_graph(
                repo,
                [
                    {"id": "d0", "title": "done", "status": "done"},
                    {"id": "d1", "title": "done2", "status": "done"},
                ],
            )
            snap = collect_repo_snapshot("repo", repo)
            self.assertEqual(snap.activity_state, "idle")
            self.assertFalse(snap.stalled)

    def test_collect_repo_snapshot_flags_aging_and_dependency_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            _write_graph(
                repo,
                [
                    {"id": "done", "title": "done", "status": "done", "created_at": "2020-01-01T00:00:00Z"},
                    {
                        "id": "missing-dep",
                        "title": "missing dep",
                        "status": "open",
                        "after": ["no-task"],
                        "created_at": "2020-01-01T00:00:00Z",
                    },
                    {
                        "id": "blocked-open",
                        "title": "blocked",
                        "status": "open",
                        "after": ["long-run"],
                        "created_at": "2020-01-01T00:00:00Z",
                    },
                    {
                        "id": "long-run",
                        "title": "long running",
                        "status": "in-progress",
                        "created_at": "2020-01-01T00:00:00Z",
                    },
                ],
            )
            snap = collect_repo_snapshot("repo", repo)
            self.assertGreaterEqual(snap.missing_dependencies, 1)
            self.assertGreaterEqual(snap.blocked_open, 1)
            self.assertGreaterEqual(len(snap.stale_open), 1)
            self.assertGreaterEqual(len(snap.stale_in_progress), 1)
            self.assertGreaterEqual(len(snap.dependency_issues), 1)
            self.assertGreaterEqual(len(snap.task_graph_nodes), 1)

    def test_collect_repo_snapshot_detects_cross_repo_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            _write_graph(
                repo,
                [
                    {
                        "id": "sync-meridian",
                        "title": "Sync with meridian register",
                        "status": "open",
                        "after": ["meridian:task-21"],
                    },
                    {"id": "local-task", "title": "Local task", "status": "in-progress"},
                ],
            )
            snap = collect_repo_snapshot("repo", repo, known_repo_names={"repo", "meridian"})
            deps = [row["repo"] for row in snap.cross_repo_dependencies]
            self.assertIn("meridian", deps)

    def test_generate_upstream_candidates_from_dirty_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            (repo / "src").mkdir()
            (repo / "src" / "main.py").write_text("print('x')\n", encoding="utf-8")
            candidates = generate_upstream_candidates("repo", repo)
            self.assertEqual(len(candidates), 1)
            self.assertTrue(candidates[0].working_tree_dirty)
            self.assertTrue(any(name.startswith("src") for name in candidates[0].changed_files))

    @patch("driftdriver.speedriftd_state.load_control_state", return_value={"mode": "supervise"})
    def test_supervise_repo_services_restarts_active_repo_and_applies_cooldown(self, _mock_ctrl: Any) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            _SUPERVISOR_LAST_ATTEMPT.clear()
            payload = [
                {
                    "name": "repo",
                    "path": str(repo),
                    "exists": True,
                    "workgraph_exists": True,
                    "service_running": False,
                    "in_progress": [{"id": "t1", "title": "T1"}],
                    "ready": [],
                }
            ]
            with patch("driftdriver.ecosystem_hub._run", return_value=(0, "started", "")) as fake_run:
                first = supervise_repo_services(repos_payload=payload, cooldown_seconds=60, max_starts=3)
                self.assertEqual(first["attempted"], 1)
                self.assertEqual(first["started"], 1)
                self.assertEqual(first["failed"], 0)
                self.assertEqual(fake_run.call_count, 1)

                second = supervise_repo_services(repos_payload=payload, cooldown_seconds=60, max_starts=3)
                self.assertEqual(second["attempted"], 0)
                self.assertEqual(second["cooldown_skipped"], 1)
                self.assertEqual(fake_run.call_count, 1)

    def test_collect_snapshot_aggregates_and_renders_packets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            _init_repo(project)
            _write_graph(project, [{"id": "a", "title": "A", "status": "open"}])

            ecosystem_root = root / "speedrift-ecosystem"
            ecosystem_root.mkdir()
            (ecosystem_root / "ecosystem.toml").write_text(
                "schema = 1\n[repos.driftdriver]\nrole='orchestrator'\nurl='https://example.com'\n",
                encoding="utf-8",
            )

            def fake_updates(**_kwargs):  # type: ignore[no-untyped-def]
                return {"has_updates": False, "has_discoveries": False, "summary": "ok", "raw": {}}

            snapshot = collect_ecosystem_snapshot(
                project_dir=project,
                workspace_root=root,
                include_updates=True,
                update_checker=fake_updates,
            )
            self.assertEqual(snapshot["repo_count"], 1)
            self.assertIn("repos", snapshot)
            self.assertIn("overview", snapshot)
            self.assertIn("repo_dependency_overview", snapshot)
            self.assertIn("narrative", snapshot)
            self.assertIn("secdrift", snapshot)
            self.assertIn("qadrift", snapshot)
            self.assertIn("security", snapshot["repos"][0])
            self.assertIn("quality", snapshot["repos"][0])
            packets = render_upstream_packets([])
            self.assertIn("No upstream contribution candidates detected", packets)

    def test_collect_snapshot_builds_repo_dependency_overview(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            _init_repo(project)
            _write_graph(
                project,
                [
                    {"id": "d1", "title": "Integrate meridian events", "status": "open", "after": ["meridian:m1"]},
                    {"id": "d2", "title": "local", "status": "in-progress"},
                ],
            )
            _write_policy(project)

            meridian = root / "meridian"
            meridian.mkdir(parents=True)
            _init_repo(meridian)
            _write_graph(meridian, [{"id": "m1", "title": "M1", "status": "open"}])
            _write_policy(meridian)

            ecosystem_root = root / "speedrift-ecosystem"
            ecosystem_root.mkdir()
            (ecosystem_root / "ecosystem.toml").write_text(
                "schema = 1\n"
                "[repos.driftdriver]\nrole='orchestrator'\nurl='https://example.com'\n"
                "[repos.meridian]\nrole='workbench'\nurl='https://example.com'\n",
                encoding="utf-8",
            )

            snapshot = collect_ecosystem_snapshot(
                project_dir=project,
                workspace_root=root,
                include_updates=False,
            )
            overview = snapshot.get("repo_dependency_overview") or {}
            self.assertIn("nodes", overview)
            self.assertIn("edges", overview)
            edges = overview.get("edges") or []
            self.assertTrue(
                any(row.get("source") == "driftdriver" and row.get("target") == "meridian" for row in edges)
            )

    def test_collect_snapshot_includes_central_reports_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            _init_repo(project)
            _write_graph(project, [{"id": "a", "title": "A", "status": "open"}])

            central = root / "central"
            report_dir = central / "reports" / "repo-a" / "2026-03-05T00-00-00Z"
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "report.md").write_text("# report\n", encoding="utf-8")

            snapshot = collect_ecosystem_snapshot(
                project_dir=project,
                workspace_root=root,
                include_updates=False,
                central_repo=central,
            )
            self.assertIn("central_reports", snapshot)
            self.assertEqual(len(snapshot["central_reports"]), 1)
            self.assertEqual(snapshot["central_reports"][0]["project"], "repo-a")

    def test_collect_snapshot_autodiscovers_recent_workspace_repos(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            _init_repo(project)
            _write_graph(project, [{"id": "a", "title": "A", "status": "open"}])
            _write_policy(project)

            ecosystem_root = root / "speedrift-ecosystem"
            ecosystem_root.mkdir()
            (ecosystem_root / "ecosystem.toml").write_text(
                "schema = 1\n[repos.driftdriver]\nrole='orchestrator'\nurl='https://example.com'\n",
                encoding="utf-8",
            )

            extra = root / "meridian"
            extra.mkdir(parents=True)
            _init_repo(extra)
            _write_graph(extra, [{"id": "m1", "title": "M1", "status": "open"}])
            _write_policy(extra)

            stale = root / "old-repo"
            stale.mkdir(parents=True)
            _init_repo(stale)
            _write_graph(stale, [{"id": "s1", "title": "S1", "status": "open"}])
            _write_policy(stale)
            old_graph = stale / ".workgraph" / "graph.jsonl"
            old_mtime = time.time() - (45 * 86400)
            os.utime(old_graph, (old_mtime, old_mtime))

            snapshot = collect_ecosystem_snapshot(
                project_dir=project,
                workspace_root=root,
                include_updates=False,
            )
            names = {row["name"] for row in snapshot.get("repos", [])}
            self.assertIn("driftdriver", names)
            self.assertIn("meridian", names)

    def test_collect_snapshot_includes_explicit_out_of_tree_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            _init_repo(project)
            _write_graph(project, [{"id": "a", "title": "A", "status": "open"}])

            external = root / "external" / "atlas_product"
            external.mkdir(parents=True, exist_ok=True)
            _init_repo(external)
            (external / "README.md").write_text("# Atlas\n", encoding="utf-8")

            ecosystem_root = root / "speedrift-ecosystem"
            ecosystem_root.mkdir()
            # Relative paths resolve from ecosystem.toml's parent directory
            (ecosystem_root / "ecosystem.toml").write_text(
                "schema = 1\n"
                "[repos.driftdriver]\nrole='orchestrator'\nurl='https://example.com'\n"
                "[repos.atlas_product]\nrole='product'\npath='../external/atlas_product'\n",
                encoding="utf-8",
            )

            snapshot = collect_ecosystem_snapshot(
                project_dir=project,
                workspace_root=root,
                include_updates=False,
            )
            names = {row["name"] for row in snapshot.get("repos", [])}
            self.assertIn("atlas_product", names)
            atlas = next(row for row in snapshot.get("repos", []) if row["name"] == "atlas_product")
            self.assertEqual(Path(atlas["path"]).resolve(), external.resolve())
            self.assertNotIn("old-repo", names)

            sources = snapshot.get("repo_sources", {})
            self.assertEqual(sources.get("atlas_product"), "ecosystem-toml")

    def test_service_status_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "driftdriver"
            project.mkdir(parents=True)
            status = read_service_status(project)
            self.assertFalse(status["running"])
            self.assertFalse(status["snapshot_exists"])

    def test_service_lifecycle_start_and_stop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            (project / ".workgraph").mkdir(parents=True)

            started = start_service_process(
                project_dir=project,
                workspace_root=root,
                host="127.0.0.1",
                port=0,
                interval_seconds=2,
                include_updates=False,
                max_next=3,
                ecosystem_toml=None,
                central_repo=None,
                execute_draft_prs=False,
                draft_pr_title_prefix="speedrift",
            )
            try:
                self.assertTrue(started["running"])
                self.assertIsNotNone(started["pid"])
            finally:
                stopped = stop_service_process(project)
                self.assertFalse(stopped["running"])

    def test_dashboard_template_contains_expected_sections(self) -> None:
        html = render_dashboard_html()
        self.assertIn("Speedrift Ecosystem Hub", html)
        self.assertIn('data-tab="home"', html)
        self.assertIn("Factory Status", html)
        self.assertIn("Needs You", html)
        self.assertIn("Autonomous This Week", html)
        self.assertIn("Convergence Trend", html)
        self.assertIn("Confidence", html)
        self.assertIn("operator-scorecard", html)
        self.assertIn("operator-now-list", html)
        self.assertIn("operator-decide-list", html)
        self.assertIn("operator-watch-list", html)
        self.assertIn("operator-evidence-drawer", html)
        self.assertIn("/api/status", html)
        self.assertIn("briefing-bar", html)
        self.assertIn("repo-health-filter", html)
        self.assertIn("repo-dep-graph", html)
        self.assertIn("dep-zoom-in", html)
        self.assertIn("dep-zoom-out", html)
        self.assertIn("dep-zoom-reset", html)
        self.assertIn("repo-table", html)
        self.assertIn("repo-search", html)
        self.assertIn("Dependencies", html)
        self.assertIn("repo-dep-meta", html)
        self.assertIn("repo-expanded-row", html)
        self.assertIn("all roles", html)
        self.assertIn("all status", html)
        self.assertIn("drawTaskDag", html)
        self.assertIn("renderBriefing", html)
        self.assertIn("qualityPill", html)
        self.assertIn("renderRepoTable", html)
        self.assertIn("drawRepoDependencyOverview", html)
        self.assertIn("/ws/status", html)
        self.assertIn("Pending Decisions", html)
        self.assertIn("factory-decision-count", html)
        self.assertIn("factory-decisions-table", html)
        self.assertIn("fetch('/api/decisions')", html)
        self.assertIn("fetch('/api/operator/home')", html)
        self.assertIn("refreshFactoryDecisionBadge()", html)
        self.assertIn("loadOperatorHome()", html)
        self.assertIn("openOperatorEvidence", html)
        self.assertIn("openOperatorFullView", html)

    def test_dashboard_template_emits_javascript_that_parses(self) -> None:
        html = render_dashboard_html()
        start = html.rfind("<script>")
        end = html.rfind("</script>")
        self.assertGreater(start, -1)
        self.assertGreater(end, start)
        script = html[start + len("<script>"):end]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write(script)
            temp_path = Path(handle.name)
        try:
            result = subprocess.run(
                ["node", "--check", str(temp_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            temp_path.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, msg=result.stderr)

    def test_build_draft_pr_requests_dry_run(self) -> None:
        candidates = [
            UpstreamCandidate(
                repo="driftdriver",
                path="/tmp/driftdriver",
                branch="feature/test",
                base_ref="origin/main",
                ahead=2,
                behind=0,
                working_tree_dirty=False,
                changed_files=["README.md", "scripts/tool.sh"],
                category="docs",
                summary="test summary",
            )
        ]
        requests = build_draft_pr_requests(candidates, title_prefix="speedrift")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].command[:3], ["gh", "pr", "create"])

        result = run_draft_pr_requests(requests, execute=False)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["dry_run"])
        self.assertIn("--draft", result[0]["command"])

    def test_apply_upstream_automation_writes_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            service_dir = Path(td)
            candidates = [
                UpstreamCandidate(
                    repo="driftdriver",
                    path="/tmp/driftdriver",
                    branch="feature/test",
                    base_ref="origin/main",
                    ahead=1,
                    behind=0,
                    working_tree_dirty=False,
                    changed_files=["README.md"],
                    category="docs",
                    summary="docs candidate",
                )
            ]
            payload = apply_upstream_automation(
                service_dir=service_dir,
                candidates=candidates,
                title_prefix="speedrift",
                execute_draft_prs=False,
            )
            self.assertEqual(payload["request_count"], 1)
            self.assertTrue((service_dir / "upstream-actions.json").exists())

    def test_resolve_central_repo_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "driftdriver"
            project.mkdir(parents=True)
            explicit = Path(td) / "central"
            out = resolve_central_repo_path(project, explicit_path=str(explicit))
            self.assertEqual(out, explicit.resolve())

    def test_resolve_central_repo_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "meridian"
            project.mkdir(parents=True)
            explicit = Path(td) / "central-env"
            with patch.dict(os.environ, {"ECOSYSTEM_HUB_CENTRAL_REPO": str(explicit)}):
                out = resolve_central_repo_path(project)
            self.assertEqual(out, explicit.resolve())

    def test_resolve_central_repo_from_workspace_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "meridian"
            sibling = root / "speedrift-ecosystem"
            project.mkdir(parents=True)
            sibling.mkdir(parents=True)
            env_backup = os.environ.pop("ECOSYSTEM_HUB_CENTRAL_REPO", None)
            try:
                out = resolve_central_repo_path(project)
                self.assertEqual(out, (sibling / ".workgraph" / "service" / "ecosystem-central").resolve())
            finally:
                if env_backup is not None:
                    os.environ["ECOSYSTEM_HUB_CENTRAL_REPO"] = env_backup

    def test_write_snapshot_once_writes_central_register(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            _init_repo(project)
            _write_graph(project, [{"id": "t1", "title": "T1", "status": "open"}])
            central = root / "central"

            snapshot = write_snapshot_once(
                project_dir=project,
                workspace_root=root,
                ecosystem_toml=None,
                include_updates=False,
                max_next=2,
                central_repo=central,
            )
            self.assertIn("generated_at", snapshot)
            self.assertIn("northstardrift", snapshot)
            self.assertIn("summary", snapshot["northstardrift"])
            self.assertIn("northstar", snapshot["repos"][0])
            self.assertIn("history", snapshot["northstardrift"])
            self.assertIn("targets", snapshot["northstardrift"])
            self.assertTrue((central / "ecosystem-hub" / "register" / "driftdriver.json").exists())
            self.assertTrue((central / "northstardrift" / "current.json").exists())

    def test_service_serves_status_api(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            (project / ".workgraph").mkdir(parents=True)

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            start_service_process(
                project_dir=project,
                workspace_root=root,
                host="127.0.0.1",
                port=port,
                interval_seconds=2,
                include_updates=False,
                max_next=3,
                ecosystem_toml=None,
                central_repo=None,
                execute_draft_prs=False,
                draft_pr_title_prefix="speedrift",
            )
            try:
                deadline = time.time() + 6.0
                payload = {}
                while time.time() < deadline:
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1.0) as resp:  # noqa: S310
                            payload = json.loads(resp.read().decode("utf-8"))
                            break
                    except Exception:
                        time.sleep(0.1)
                self.assertIn("schema", payload)
                self.assertIn("repos", payload)
                self.assertIn("overview", payload)
                self.assertIn("narrative", payload)
                self.assertIn("northstardrift", payload)
                with urlopen(f"http://127.0.0.1:{port}/api/graph", timeout=1.0) as resp:  # noqa: S310
                    graph_payload = json.loads(resp.read().decode("utf-8"))
                self.assertIsInstance(graph_payload, list)
                with urlopen(f"http://127.0.0.1:{port}/api/repo-dependencies", timeout=1.0) as resp:  # noqa: S310
                    dep_payload = json.loads(resp.read().decode("utf-8"))
                self.assertIn("nodes", dep_payload)
                self.assertIn("edges", dep_payload)
                with urlopen(f"http://127.0.0.1:{port}/api/effectiveness", timeout=1.0) as resp:  # noqa: S310
                    effectiveness_payload = json.loads(resp.read().decode("utf-8"))
                self.assertIn("summary", effectiveness_payload)
                with urlopen(f"http://127.0.0.1:{port}/api/effectiveness-history", timeout=1.0) as resp:  # noqa: S310
                    history_payload = json.loads(resp.read().decode("utf-8"))
                self.assertIn("points", history_payload)
                self.assertIn("weekly_points", history_payload)
                self.assertIn("windows", history_payload)
                with urlopen(f"http://127.0.0.1:{port}/api/security", timeout=1.0) as resp:  # noqa: S310
                    security_payload = json.loads(resp.read().decode("utf-8"))
                self.assertIn("summary", security_payload)
                with urlopen(f"http://127.0.0.1:{port}/api/quality", timeout=1.0) as resp:  # noqa: S310
                    quality_payload = json.loads(resp.read().decode("utf-8"))
                self.assertIn("summary", quality_payload)
            finally:
                stop_service_process(project)

    def test_service_streams_status_websocket(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            (project / ".workgraph").mkdir(parents=True)

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            start_service_process(
                project_dir=project,
                workspace_root=root,
                host="127.0.0.1",
                port=port,
                interval_seconds=2,
                include_updates=False,
                max_next=3,
                ecosystem_toml=None,
                central_repo=None,
                execute_draft_prs=False,
                draft_pr_title_prefix="speedrift",
            )
            ws_sock: socket.socket | None = None
            ws_leftover: bytes = b""
            try:
                deadline = time.time() + 6.0
                while time.time() < deadline:
                    try:
                        ws_sock, ws_leftover = _ws_connect(port)
                        break
                    except Exception:
                        time.sleep(0.1)
                self.assertIsNotNone(ws_sock)
                assert ws_sock is not None
                payload = json.loads(_ws_read_text(ws_sock, prefix=ws_leftover))
                self.assertIn("schema", payload)
                self.assertIn("repos", payload)
            finally:
                if ws_sock is not None:
                    ws_sock.close()
                stop_service_process(project)


class DashboardDecisionDisplayTests(unittest.TestCase):
    """Tests for needs_human badge in dashboard and /api/decisions endpoint."""

    def test_repo_snapshot_includes_continuation_intent(self) -> None:
        """Repo snapshot should include continuation_intent field from control.json."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            _write_graph(repo, [{"id": "t1", "title": "ready", "status": "open"}])

            # Write continuation intent to control.json
            runtime_dir = repo / ".workgraph" / "service" / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "control.json").write_text(
                json.dumps({
                    "continuation_intent": {
                        "intent": "needs_human",
                        "reason": "awaiting approval",
                        "set_by": "agent",
                        "set_at": "2026-03-13T12:00:00+00:00",
                        "decision_id": "dec-20260313-abc123",
                    }
                }),
                encoding="utf-8",
            )

            snap = collect_repo_snapshot("repo", repo)
            self.assertEqual(snap.continuation_intent.get("intent"), "needs_human")
            self.assertEqual(snap.continuation_intent.get("decision_id"), "dec-20260313-abc123")

    def test_repo_snapshot_continuation_intent_empty_when_not_set(self) -> None:
        """Repo snapshot should have empty dict when no continuation intent."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True)
            _init_repo(repo)
            _write_graph(repo, [{"id": "t1", "title": "ready", "status": "open"}])
            snap = collect_repo_snapshot("repo", repo)
            self.assertEqual(snap.continuation_intent, {})

    def test_dashboard_contains_needs_human_badge_function(self) -> None:
        """Dashboard HTML should contain the needsHumanBadge JS function."""
        html = render_dashboard_html()
        self.assertIn("needsHumanBadge", html)

    def test_dashboard_repo_rows_invoke_needs_human_badge(self) -> None:
        """The renderRepoTable function should call needsHumanBadge."""
        html = render_dashboard_html()
        self.assertIn("needsHumanBadge(repo)", html)

    def test_api_decisions_endpoint_aggregates_pending(self) -> None:
        """The /api/decisions endpoint should aggregate pending decisions across repos."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            _init_repo(project)
            _write_graph(project, [{"id": "a", "title": "A", "status": "open"}])

            # Write a pending decision for the repo
            decisions_dir = project / ".workgraph" / "service" / "runtime"
            decisions_dir.mkdir(parents=True, exist_ok=True)
            (decisions_dir / "decisions.jsonl").write_text(
                json.dumps({
                    "id": "dec-20260313-aaa111",
                    "repo": "driftdriver",
                    "status": "pending",
                    "question": "Should we upgrade?",
                    "context": {},
                    "category": "external_dep",
                    "created_at": "2026-03-13T12:00:00+00:00",
                    "notified_via": [],
                }) + "\n",
                encoding="utf-8",
            )

            paia_program = root / "paia-program"
            paia_program.mkdir()
            (paia_program / "config.toml").write_text(
                f"""
[repos]
paia-agents = "{root / 'paia-agents'}"
samantha = "{root / 'paia-agents' / 'samantha'}"
caroline = "{root / 'paia-agents' / 'caroline'}"
derek = "{root / 'paia-agents' / 'derek'}"
ingrid = "{root / 'paia-agents' / 'ingrid'}"

[topology.canonical]
target_repos = ["paia-agents"]

[topology.agent_family]
target_repo = "paia-agents"
members = ["samantha", "caroline", "derek", "ingrid"]
""".strip()
                + "\n",
                encoding="utf-8",
            )
            config_dir = root / ".config" / "workgraph"
            config_dir.mkdir(parents=True)
            (config_dir / "agent_health_pending.json").write_text(
                json.dumps(
                    {
                        "dec-20260410-xyz789": {
                            "agent": "derek",
                            "pattern": "toolinvocationfailure",
                            "component": "workgraph_cli_integration",
                            "risk": "medium",
                            "change_summary": "Teach Derek the current wg flags.",
                            "diff": "--- a/experiments/derek/CLAUDE.md\n+++ b/experiments/derek/CLAUDE.md\n",
                        }
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            ecosystem_root = root / "speedrift-ecosystem"
            ecosystem_root.mkdir()
            (ecosystem_root / "ecosystem.toml").write_text(
                "schema = 1\n[repos.driftdriver]\nrole='orchestrator'\nurl='https://example.com'\n",
                encoding="utf-8",
            )

            # Start the service and query the endpoint
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            with patch.dict(os.environ, {"WORKGRAPH_CONFIG_DIR": str(config_dir)}):
                start_service_process(
                    project_dir=project,
                    workspace_root=root,
                    host="127.0.0.1",
                    port=port,
                    interval_seconds=2,
                    include_updates=False,
                    max_next=3,
                    ecosystem_toml=None,
                    central_repo=None,
                    execute_draft_prs=False,
                    draft_pr_title_prefix="speedrift",
                )
                try:
                # Wait for snapshot to be written (repos list populated)
                    deadline = time.time() + 6.0
                    while time.time() < deadline:
                        try:
                            with urlopen(f"http://127.0.0.1:{port}/api/repos", timeout=1.0) as resp:  # noqa: S310
                                repos_data = json.loads(resp.read().decode("utf-8"))
                                if isinstance(repos_data, list) and len(repos_data) > 0:
                                    break
                        except Exception:
                            pass
                        time.sleep(0.1)

                # Now query decisions
                    with urlopen(f"http://127.0.0.1:{port}/api/decisions", timeout=2.0) as resp:  # noqa: S310
                        payload = json.loads(resp.read().decode("utf-8"))
                    self.assertIn("decisions", payload)
                    self.assertIsInstance(payload["decisions"], list)
                    pending = [d for d in payload["decisions"] if d.get("status") == "pending"]
                    pending_ids = {d["id"] for d in pending}
                    self.assertIn("dec-20260313-aaa111", pending_ids)
                    self.assertIn("dec-20260410-xyz789", pending_ids)
                finally:
                    stop_service_process(project)

    def test_api_operator_home_returns_scorecard_and_action_buckets(self) -> None:
        """The /api/operator/home endpoint should expose scorecard and canonical action buckets."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project = root / "driftdriver"
            project.mkdir(parents=True)
            _init_repo(project)
            _write_graph(project, [{"id": "a", "title": "A", "status": "open"}])

            decisions_dir = project / ".workgraph" / "service" / "runtime"
            decisions_dir.mkdir(parents=True, exist_ok=True)
            (decisions_dir / "decisions.jsonl").write_text(
                json.dumps({
                    "id": "dec-20260410-home01",
                    "repo": "paia-agents",
                    "status": "pending",
                    "question": "Review operator queue?",
                    "context": {"severity": "high", "confidence": 0.9},
                    "category": "feature",
                    "created_at": "2026-04-10T19:59:00+00:00",
                    "notified_via": [],
                }) + "\n",
                encoding="utf-8",
            )

            paia_program = root / "paia-program"
            paia_program.mkdir()
            (paia_program / "config.toml").write_text(
                f"""
[repos]
paia-agents = "{root / 'paia-agents'}"

[topology.canonical]
target_repos = ["paia-agents"]
""".strip()
                + "\n",
                encoding="utf-8",
            )

            ecosystem_root = root / "speedrift-ecosystem"
            ecosystem_root.mkdir()
            (ecosystem_root / "ecosystem.toml").write_text(
                "schema = 1\n[repos.driftdriver]\nrole='orchestrator'\nurl='https://example.com'\n",
                encoding="utf-8",
            )

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            start_service_process(
                project_dir=project,
                workspace_root=root,
                host="127.0.0.1",
                port=port,
                interval_seconds=2,
                include_updates=False,
                max_next=3,
                ecosystem_toml=None,
                central_repo=None,
                execute_draft_prs=False,
                draft_pr_title_prefix="speedrift",
            )
            try:
                deadline = time.time() + 6.0
                while time.time() < deadline:
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/api/repos", timeout=1.0) as resp:  # noqa: S310
                            repos_data = json.loads(resp.read().decode("utf-8"))
                            if isinstance(repos_data, list) and len(repos_data) > 0:
                                break
                    except Exception:
                        pass
                    time.sleep(0.1)

                with urlopen(f"http://127.0.0.1:{port}/api/operator/home", timeout=2.0) as resp:  # noqa: S310
                    payload = json.loads(resp.read().decode("utf-8"))
                self.assertIn("scorecard", payload)
                self.assertIn("decide", payload)
                self.assertEqual(payload["decide"][0]["repo"], "paia-agents")
                self.assertEqual(payload["counts"]["decide"], 1)
            finally:
                stop_service_process(project)


if __name__ == "__main__":
    unittest.main()
