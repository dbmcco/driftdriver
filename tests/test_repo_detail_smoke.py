# ABOUTME: Smoke tests verifying the dashboard HTML contains all required detail page elements.
# ABOUTME: Checks for section IDs, CSS classes, and JS function stubs by string search.
from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer
from pathlib import Path


class TestDetailPageHtmlStructure(unittest.TestCase):
    """Parse render_dashboard_html() and verify required elements exist."""

    def setUp(self):
        from driftdriver.ecosystem_hub.dashboard import render_dashboard_html
        self.html = render_dashboard_html()

    # --- View containers ---

    def test_view_hub_div_exists(self):
        self.assertIn('id="view-hub"', self.html)

    def test_view_repo_detail_div_exists_and_is_hidden(self):
        self.assertIn('id="view-repo-detail"', self.html)
        idx = self.html.index('id="view-repo-detail"')
        surrounding = self.html[max(0, idx - 10):idx + 100]
        self.assertIn("hidden", surrounding)

    # --- Detail panel section IDs ---

    def test_detail_header_section_exists(self):
        self.assertIn('id="detail-header"', self.html)

    def test_detail_git_section_exists(self):
        self.assertIn('id="detail-git"', self.html)

    def test_detail_services_section_exists(self):
        self.assertIn('id="detail-services"', self.html)

    def test_detail_workgraph_section_exists(self):
        self.assertIn('id="detail-workgraph"', self.html)

    def test_detail_agents_section_exists(self):
        self.assertIn('id="detail-agents"', self.html)

    def test_detail_dependencies_section_exists(self):
        self.assertIn('id="detail-deps"', self.html)

    def test_detail_health_section_exists(self):
        self.assertIn('id="detail-health"', self.html)

    # --- Back button ---

    def test_back_button_calls_close_repo_detail(self):
        self.assertIn("closeRepoDetail()", self.html)

    # --- JS functions ---

    def test_open_repo_detail_function_defined(self):
        self.assertIn("function openRepoDetail(", self.html)

    def test_close_repo_detail_function_defined(self):
        self.assertIn("function closeRepoDetail(", self.html)

    def test_render_repo_detail_function_defined(self):
        self.assertIn("function renderRepoDetailHeader(", self.html)

    def test_current_view_var_defined(self):
        self.assertIn("let currentView", self.html)

    def test_detail_repo_var_defined(self):
        self.assertIn("let detailRepo", self.html)

    # --- Repo name links ---

    def test_repo_name_link_class_used(self):
        self.assertIn("repo-name-link", self.html)

    def test_open_repo_detail_called_from_link(self):
        self.assertIn("openRepoDetail(", self.html)

    # --- popstate handler ---

    def test_popstate_event_listener_registered(self):
        self.assertIn("popstate", self.html)

    # --- CSS ---

    def test_detail_panel_css_present(self):
        self.assertIn(".detail-panel", self.html)

    def test_detail_header_sticky_css_present(self):
        self.assertIn(".detail-header", self.html)

    def test_repo_name_link_css_present(self):
        self.assertIn(".repo-name-link", self.html)

    # --- Additional structural checks ---

    def test_repo_name_link_has_href_pattern(self):
        self.assertIn("/repo/", self.html)
        self.assertIn("encodeURIComponent", self.html)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_snapshot(path: Path, repos: list) -> None:
    path.write_text(json.dumps({"schema": 1, "generated_at": "2026-03-18T00:00:00Z", "repos": repos}), encoding="utf-8")


SMOKE_REPO = {
    "name": "smoketest-repo",
    "path": "/tmp/smoketest-repo",
    "exists": True,
    "source": "product:local",
    "tags": ["smoke"],
    "ecosystem_role": "product",
    "git_branch": "main",
    "git_dirty": False,
    "dirty_file_count": 0,
    "untracked_file_count": 0,
    "ahead": 0,
    "behind": 0,
    "service_running": False,
    "workgraph_exists": True,
    "task_counts": {"open": 2, "ready": 1, "in_progress": 1, "done": 5},
    "in_progress": [{"id": "t-1", "title": "Do the thing", "status": "in-progress"}],
    "ready": [{"id": "t-2", "title": "Do the next thing"}],
    "presence_actors": [],
    "cross_repo_dependencies": [],
    "stalled": False,
    "stall_reasons": [],
    "narrative": "Smoke test narrative.",
    "northstar": {"tier": "healthy", "score": 0.9},
    "security_findings": [],
    "quality_findings": [],
}


class TestDetailPageServerIntegration(unittest.TestCase):
    """Spin up a real HTTPServer and verify the repo detail API + SPA fallback."""

    @classmethod
    def setUpClass(cls):
        from driftdriver.ecosystem_hub.api import _handler_factory
        from driftdriver.ecosystem_hub.websocket import LiveStreamHub

        cls._tmp = tempfile.TemporaryDirectory()
        tmpdir = Path(cls._tmp.name)
        cls.snapshot_path = tmpdir / "snapshot.json"
        _make_snapshot(cls.snapshot_path, [SMOKE_REPO])

        cls._stop_event = threading.Event()
        hub = LiveStreamHub(cls._stop_event)
        HandlerClass = _handler_factory(cls.snapshot_path, cls.snapshot_path, hub, None)
        cls.port = _free_port()
        cls.server = HTTPServer(("127.0.0.1", cls.port), HandlerClass)
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls._tmp.cleanup()

    def _get(self, path: str) -> tuple[int, str, str]:
        """Return (status_code, content_type, body) for a GET request."""
        from urllib.error import HTTPError
        from urllib.request import urlopen
        url = f"http://127.0.0.1:{self.port}{path}"
        try:
            resp = urlopen(url, timeout=5)
            ct = resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, ct, body
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            ct = exc.headers.get("Content-Type", "") if exc.headers else ""
            return exc.code, ct, body

    # --- GET /api/repo/:name ---

    def test_api_repo_detail_returns_200_json(self):
        status, ct, body = self._get("/api/repo/smoketest-repo")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ct)

    def test_api_repo_detail_body_has_required_keys(self):
        status, ct, body = self._get("/api/repo/smoketest-repo")
        data = json.loads(body)
        for key in ("name", "git", "services", "workgraph", "health", "activity", "dependencies", "presence_actors"):
            self.assertIn(key, data, f"missing key: {key}")

    def test_api_repo_detail_name_matches(self):
        _, _, body = self._get("/api/repo/smoketest-repo")
        data = json.loads(body)
        self.assertEqual(data["name"], "smoketest-repo")

    def test_api_repo_detail_not_found_returns_404(self):
        status, _, body = self._get("/api/repo/does-not-exist")
        self.assertEqual(status, 404)
        data = json.loads(body)
        self.assertEqual(data["error"], "repo_not_found")

    def test_api_repo_detail_workgraph_task_counts(self):
        _, _, body = self._get("/api/repo/smoketest-repo")
        data = json.loads(body)
        self.assertEqual(data["workgraph"]["task_counts"]["done"], 5)
        self.assertEqual(data["workgraph"]["task_counts"]["in_progress"], 1)

    def test_api_repo_detail_in_progress_list(self):
        _, _, body = self._get("/api/repo/smoketest-repo")
        data = json.loads(body)
        self.assertEqual(len(data["workgraph"]["in_progress"]), 1)
        self.assertEqual(data["workgraph"]["in_progress"][0]["id"], "t-1")

    def test_api_repo_detail_health_narrative(self):
        _, _, body = self._get("/api/repo/smoketest-repo")
        data = json.loads(body)
        self.assertIn("Smoke test narrative", data["health"]["narrative"])

    # --- SPA fallback: /repo/:name returns dashboard HTML ---

    def test_repo_path_returns_html_not_404(self):
        status, ct, body = self._get("/repo/smoketest-repo")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ct)

    def test_repo_path_html_contains_view_repo_detail(self):
        _, _, body = self._get("/repo/smoketest-repo")
        self.assertIn("view-repo-detail", body)

    def test_repo_path_html_contains_hub_view(self):
        _, _, body = self._get("/repo/smoketest-repo")
        self.assertIn("view-hub", body)

    def test_unknown_non_api_path_returns_html(self):
        status, ct, body = self._get("/some/unknown/path")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ct)

    def test_api_404_still_returns_json(self):
        status, ct, body = self._get("/api/unknown-endpoint")
        self.assertEqual(status, 404)
        self.assertIn("application/json", ct)

    # --- Back navigation smoke ---

    def test_detail_html_has_back_link(self):
        _, _, body = self._get("/")
        self.assertIn("closeRepoDetail", body)

    def test_detail_html_has_all_section_ids(self):
        _, _, body = self._get("/")
        for section_id in ("detail-git", "detail-services", "detail-workgraph", "detail-agents", "detail-deps", "detail-health"):
            self.assertIn(f'id="{section_id}"', body, f"missing section: {section_id}")
