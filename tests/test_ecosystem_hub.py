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
from unittest.mock import patch
from urllib.request import urlopen
from pathlib import Path

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


def _ws_connect(port: int) -> socket.socket:
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
    return sock_obj


def _ws_read_text(sock_obj: socket.socket, timeout: float = 3.0) -> str:
    sock_obj.settimeout(timeout)
    header = _recv_exact(sock_obj, 2)
    first, second = header[0], header[1]
    opcode = first & 0x0F
    if opcode != 0x1:
        raise RuntimeError(f"unexpected_opcode:{opcode}")
    size = second & 0x7F
    if size == 126:
        size = struct.unpack("!H", _recv_exact(sock_obj, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", _recv_exact(sock_obj, 8))[0]
    payload = _recv_exact(sock_obj, size) if size else b""
    return payload.decode("utf-8")


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

    def test_supervise_repo_services_restarts_active_repo_and_applies_cooldown(self) -> None:
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
            self.assertNotIn("old-repo", names)

            sources = snapshot.get("repo_sources", {})
            self.assertEqual(sources.get("meridian"), "autodiscovered")

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
        self.assertIn("/api/status", html)
        self.assertIn("Narrated Overview", html)
        self.assertIn("Action Center", html)
        self.assertIn("Dependency Graph", html)
        self.assertIn("Stalled Repos", html)
        self.assertIn("graph-zoom-in", html)
        self.assertIn("graph-mode", html)
        self.assertIn("all repos", html)
        self.assertIn("repo-dep-graph", html)
        self.assertIn("graph-path", html)
        self.assertIn("/ws/status", html)
        self.assertIn("By Repo", html)
        self.assertIn("repo-sort", html)
        self.assertIn("action-sort", html)
        self.assertIn("action-dirty-filter", html)

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
            self.assertTrue((central / "ecosystem-hub" / "register" / "driftdriver.json").exists())

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
                with urlopen(f"http://127.0.0.1:{port}/api/graph", timeout=1.0) as resp:  # noqa: S310
                    graph_payload = json.loads(resp.read().decode("utf-8"))
                self.assertIsInstance(graph_payload, list)
                with urlopen(f"http://127.0.0.1:{port}/api/repo-dependencies", timeout=1.0) as resp:  # noqa: S310
                    dep_payload = json.loads(resp.read().decode("utf-8"))
                self.assertIn("nodes", dep_payload)
                self.assertIn("edges", dep_payload)
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
            try:
                deadline = time.time() + 6.0
                while time.time() < deadline:
                    try:
                        ws_sock = _ws_connect(port)
                        break
                    except Exception:
                        time.sleep(0.1)
                self.assertIsNotNone(ws_sock)
                assert ws_sock is not None
                payload = json.loads(_ws_read_text(ws_sock))
                self.assertIn("schema", payload)
                self.assertIn("repos", payload)
            finally:
                if ws_sock is not None:
                    ws_sock.close()
                stop_service_process(project)


if __name__ == "__main__":
    unittest.main()
