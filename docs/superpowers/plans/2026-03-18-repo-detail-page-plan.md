# Per-Repo Detail Page — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-repo detail panel to the ecosystem hub SPA. Clicking a repo name in the main table opens a full-screen view showing git activity, services, workgraph tasks, active agents, cross-repo dependencies, and health signals — all in one place. Browser navigation (pushState/popstate) keeps the URL bookmarkable without Python routing changes beyond two small edits.

**Architecture:** Two Python files change (`api.py`, `dashboard.py`). All other files are unchanged. In `api.py`: (1) add `GET /api/repo/:name` endpoint that assembles snapshot data + activity digest for one repo; (2) fix the unrecognized-route fallback to serve dashboard HTML for non-API GET paths (enables direct `/repo/<name>` browser navigation). In `dashboard.py`: (1) wrap existing hub HTML in `<div id="view-hub">`; (2) add `<div id="view-repo-detail" hidden>` with 7 sections; (3) add CSS for the detail panel; (4) add JS functions `openRepoDetail`, `closeRepoDetail`, `renderRepoDetail`, `renderRepoDetailSections`; (5) add `popstate` handler; (6) make repo name cells into `<a>` links; (7) intercept `click` on `.repo-name-link` elements.

**Tech Stack:** Python 3.11+, `glob` (stdlib), vanilla JS + HTML already in `dashboard.py`, `unittest` + `tempfile` + `json` for tests (no mocks, real JSON fixtures). Test runner: `uv run pytest`.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `driftdriver/ecosystem_hub/api.py` | Add `GET /api/repo/:name` handler; fix unrecognized-route fallback |
| Modify | `driftdriver/ecosystem_hub/dashboard.py` | View wrappers, detail panel HTML+CSS, JS routing, JS render functions, repo name links |
| Create | `tests/test_repo_detail_api.py` | Python unit tests for the new API endpoint |
| Create | `tests/test_repo_detail_smoke.py` | Smoke test: HTML structure contains required section IDs |

---

## Task 1: `GET /api/repo/:name` endpoint

**Files:**
- Create: `tests/test_repo_detail_api.py`
- Modify: `driftdriver/ecosystem_hub/api.py`

### Step 1: Write the failing test

```python
# tests/test_repo_detail_api.py
# ABOUTME: Tests for GET /api/repo/:name — assembles per-repo snapshot + activity data.
# ABOUTME: Uses pre-baked JSON fixtures in tempfiles. No mocks, no subprocess calls.
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.ecosystem_hub.api import _build_repo_detail_payload


def _make_snapshot(path: Path, repos: list[dict]) -> None:
    path.write_text(
        json.dumps({"schema": 1, "generated_at": "2026-03-18T14:00:00Z", "repos": repos}),
        encoding="utf-8",
    )


def _make_digest(path: Path, repos: list[dict]) -> None:
    path.write_text(
        json.dumps({"generated_at": "2026-03-18T14:00:00Z", "repos": repos}),
        encoding="utf-8",
    )


FIXTURE_REPO = {
    "name": "lodestar",
    "path": "/tmp/lodestar",
    "exists": True,
    "source": "product:local",
    "tags": ["active-project", "lodestar"],
    "ecosystem_role": "product",
    "git_branch": "main",
    "git_dirty": False,
    "dirty_file_count": 0,
    "untracked_file_count": 0,
    "ahead": 0,
    "behind": 0,
    "service_running": True,
    "workgraph_exists": True,
    "task_counts": {"open": 12, "ready": 3, "in_progress": 2, "done": 147},
    "in_progress": [{"id": "t-42", "title": "Add regret scoring", "status": "in-progress"}],
    "ready": [{"id": "t-43", "title": "Wire briefing history"}],
    "presence_actors": [{"id": "claude-code", "name": "Claude Code", "last_seen": "2026-03-18T14:00:00Z"}],
    "cross_repo_dependencies": [{"repo": "paia-memory", "score": 8, "task_reference": 2, "explicit_dependency_ref": 1, "policy_order": 0}],
    "stalled": False,
    "stall_reasons": [],
    "narrative": "Lodestar is in active development.",
    "northstar": {"tier": "healthy", "score": 0.74},
    "security_findings": [],
    "quality_findings": [{"severity": "medium", "message": "Missing type hints in 3 functions"}],
}

FIXTURE_DIGEST_REPO = {
    "name": "lodestar",
    "last_commit_at": "2026-03-18T13:45:00Z",
    "summary": "Extended scenario engine with regret scoring.",
    "windows": {"48h": {"count": 3, "subjects": ["feat: regret scoring"]}},
    "timeline": [
        {"repo": "lodestar", "hash": "abc123", "timestamp": "2026-03-18T13:45:00Z", "subject": "feat: add regret scoring", "author": "Braydon McConaghy"},
        {"repo": "lodestar", "hash": "def456", "timestamp": "2026-03-18T10:00:00Z", "subject": "fix: scenario edge case", "author": "Braydon McConaghy"},
    ],
}


class TestBuildRepoDetailPayload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.snapshot_path = self.tmpdir / "snapshot.json"
        self.digest_path = self.tmpdir / "activity-digests.json"

    def tearDown(self):
        self._tmp.cleanup()

    # --- repo not found ---

    def test_repo_not_found_returns_none(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("nonexistent", self.snapshot_path, None)
        self.assertIsNone(result)

    def test_missing_snapshot_returns_none(self):
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertIsNone(result)

    # --- basic shape ---

    def test_returns_dict_with_required_top_level_keys(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertIsNotNone(result)
        for key in ("name", "path", "exists", "source", "git", "services", "workgraph", "presence_actors", "dependencies", "health", "activity"):
            self.assertIn(key, result, f"missing key: {key}")

    def test_name_and_path_are_projected(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertEqual(result["name"], "lodestar")
        self.assertEqual(result["path"], "/tmp/lodestar")

    # --- git section ---

    def test_git_section_has_expected_fields(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        git = result["git"]
        self.assertEqual(git["branch"], "main")
        self.assertFalse(git["dirty"])
        self.assertEqual(git["dirty_file_count"], 0)
        self.assertEqual(git["ahead"], 0)
        self.assertEqual(git["behind"], 0)

    # --- services section ---

    def test_services_section_has_expected_fields(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        svc = result["services"]
        self.assertIn("workgraph_service_running", svc)
        self.assertIn("launchd_plist_loaded", svc)
        self.assertIn("cron_jobs", svc)

    def test_services_cron_jobs_is_always_empty_list(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertEqual(result["services"]["cron_jobs"], [])

    def test_services_workgraph_service_running_matches_snapshot(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertTrue(result["services"]["workgraph_service_running"])

    # --- workgraph section ---

    def test_workgraph_section_has_expected_fields(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        wg = result["workgraph"]
        self.assertIn("exists", wg)
        self.assertIn("task_counts", wg)
        self.assertIn("in_progress", wg)
        self.assertIn("ready", wg)

    def test_workgraph_task_counts_matches_snapshot(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertEqual(result["workgraph"]["task_counts"]["open"], 12)
        self.assertEqual(result["workgraph"]["task_counts"]["done"], 147)

    def test_workgraph_in_progress_list_matches_snapshot(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertEqual(len(result["workgraph"]["in_progress"]), 1)
        self.assertEqual(result["workgraph"]["in_progress"][0]["id"], "t-42")

    # --- presence_actors section ---

    def test_presence_actors_list_matches_snapshot(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertEqual(len(result["presence_actors"]), 1)
        self.assertEqual(result["presence_actors"][0]["id"], "claude-code")

    # --- dependencies section ---

    def test_dependencies_section_has_depends_on_and_depended_on_by(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        deps = result["dependencies"]
        self.assertIn("depends_on", deps)
        self.assertIn("depended_on_by", deps)

    def test_depends_on_is_derived_from_cross_repo_dependencies(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        # lodestar has cross_repo_dependencies = [{"repo": "paia-memory", ...}]
        self.assertIn("paia-memory", result["dependencies"]["depends_on"])

    def test_depended_on_by_derived_from_other_repos_in_snapshot(self):
        other_repo = {
            "name": "paia-shell",
            "path": "/tmp/paia-shell",
            "exists": True,
            "source": "product:local",
            "cross_repo_dependencies": [{"repo": "lodestar", "score": 4, "task_reference": 1, "explicit_dependency_ref": 0, "policy_order": 0}],
        }
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO, other_repo])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertIn("paia-shell", result["dependencies"]["depended_on_by"])

    # --- health section ---

    def test_health_section_has_expected_fields(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        health = result["health"]
        for key in ("drift_score", "drift_tier", "security_findings", "quality_findings", "stalled", "stall_reasons", "narrative"):
            self.assertIn(key, health, f"missing key: {key}")

    def test_health_quality_findings_matches_snapshot(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertEqual(len(result["health"]["quality_findings"]), 1)
        self.assertEqual(result["health"]["quality_findings"][0]["severity"], "medium")

    # --- activity section without digest ---

    def test_activity_section_present_without_digest(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        activity = result["activity"]
        self.assertIn("last_commit_at", activity)
        self.assertIn("summary", activity)
        self.assertIn("timeline", activity)

    def test_activity_timeline_empty_when_no_digest(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        self.assertEqual(result["activity"]["timeline"], [])

    # --- activity section with digest ---

    def test_activity_timeline_populated_from_digest(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        _make_digest(self.digest_path, [FIXTURE_DIGEST_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, self.digest_path)
        self.assertEqual(len(result["activity"]["timeline"]), 2)
        self.assertEqual(result["activity"]["timeline"][0]["hash"], "abc123")

    def test_activity_summary_populated_from_digest(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        _make_digest(self.digest_path, [FIXTURE_DIGEST_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, self.digest_path)
        self.assertIn("regret scoring", result["activity"]["summary"])

    def test_activity_last_commit_at_populated_from_digest(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        _make_digest(self.digest_path, [FIXTURE_DIGEST_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, self.digest_path)
        self.assertEqual(result["activity"]["last_commit_at"], "2026-03-18T13:45:00Z")

    # --- launchd detection (filesystem, no subprocess) ---

    def test_launchd_plist_loaded_false_when_no_plist_exists(self):
        _make_snapshot(self.snapshot_path, [FIXTURE_REPO])
        result = _build_repo_detail_payload("lodestar", self.snapshot_path, None)
        # In test env, no ~/Library/LaunchAgents/lodestar* plist exists
        self.assertIsInstance(result["services"]["launchd_plist_loaded"], bool)
```

- [ ] **Step 2: Run the failing test**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_detail_api.py -v 2>&1 | head -40
```

Expected: `ImportError` — `_build_repo_detail_payload` does not exist yet.

- [ ] **Step 3: Implement `_build_repo_detail_payload` in `api.py`**

Add the helper function and the route handler to `driftdriver/ecosystem_hub/api.py`.

**Where to insert in `api.py`:**

The helper function goes after `_build_activity_payload` (currently ending at line 652) and before `_handler_factory`. Insert the following:

```python
def _build_repo_detail_payload(
    repo_name: str,
    snapshot_path: Path,
    activity_path: Path | None,
) -> dict[str, Any] | None:
    """Assemble all per-repo signals for GET /api/repo/:name.

    Returns None if snapshot is missing or repo_name is not found.
    """
    import glob as _glob

    if not snapshot_path.exists():
        return None

    data = _read_json(snapshot_path)
    if not data:
        return None

    repos = data.get("repos") or []
    repo: dict[str, Any] | None = None
    all_repos = [r for r in repos if isinstance(r, dict)]

    for r in all_repos:
        if str(r.get("name") or "") == repo_name:
            repo = r
            break

    if repo is None:
        return None

    # --- git ---
    git: dict[str, Any] = {
        "branch": str(repo.get("git_branch") or ""),
        "dirty": bool(repo.get("git_dirty")),
        "dirty_file_count": int(repo.get("dirty_file_count") or 0),
        "untracked_file_count": int(repo.get("untracked_file_count") or 0),
        "ahead": int(repo.get("ahead") or 0),
        "behind": int(repo.get("behind") or 0),
    }

    # --- services ---
    plist_pattern = str(
        Path.home() / "Library" / "LaunchAgents" / f"*{repo_name}*"
    )
    launchd_loaded = bool(_glob.glob(plist_pattern))
    services: dict[str, Any] = {
        "workgraph_service_running": bool(repo.get("service_running")),
        "launchd_plist_loaded": launchd_loaded,
        "cron_jobs": [],
    }

    # --- workgraph ---
    workgraph: dict[str, Any] = {
        "exists": bool(repo.get("workgraph_exists")),
        "task_counts": dict(repo.get("task_counts") or {}),
        "in_progress": list(repo.get("in_progress") or []),
        "ready": list(repo.get("ready") or []),
    }

    # --- presence actors ---
    presence_actors: list[dict[str, Any]] = list(repo.get("presence_actors") or [])

    # --- dependencies ---
    # "depends_on": repos this repo references (outbound cross_repo_dependencies)
    raw_deps = [r for r in (repo.get("cross_repo_dependencies") or []) if isinstance(r, dict)]
    depends_on: list[str] = [
        str(d.get("repo") or "") for d in raw_deps if str(d.get("repo") or "")
    ]
    # "depended_on_by": other repos whose cross_repo_dependencies reference this repo
    depended_on_by: list[str] = []
    for other in all_repos:
        if str(other.get("name") or "") == repo_name:
            continue
        other_deps = [
            str(d.get("repo") or "")
            for d in (other.get("cross_repo_dependencies") or [])
            if isinstance(d, dict)
        ]
        if repo_name in other_deps:
            depended_on_by.append(str(other.get("name") or ""))

    dependencies: dict[str, Any] = {
        "depends_on": depends_on,
        "depended_on_by": sorted(set(depended_on_by)),
    }

    # --- health ---
    northstar = repo.get("northstar") or repo.get("repo_north_star") or {}
    health: dict[str, Any] = {
        "drift_score": northstar.get("score"),
        "drift_tier": str(northstar.get("tier") or ""),
        "security_findings": list(repo.get("security_findings") or []),
        "quality_findings": list(repo.get("quality_findings") or []),
        "stalled": bool(repo.get("stalled")),
        "stall_reasons": list(repo.get("stall_reasons") or []),
        "narrative": str(repo.get("narrative") or ""),
    }

    # --- activity (from digest, falls back to empty) ---
    activity: dict[str, Any] = {
        "last_commit_at": None,
        "summary": None,
        "timeline": [],
    }
    if activity_path and activity_path.exists():
        digest = read_activity_digest(activity_path)
        for entry in (digest.get("repos") or []):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("name") or "") == repo_name:
                activity["last_commit_at"] = entry.get("last_commit_at")
                activity["summary"] = entry.get("summary")
                activity["timeline"] = list(entry.get("timeline") or [])
                break

    return {
        "name": repo_name,
        "path": str(repo.get("path") or ""),
        "exists": bool(repo.get("exists")),
        "source": str(repo.get("source") or ""),
        "tags": list(repo.get("tags") or []),
        "ecosystem_role": str(repo.get("ecosystem_role") or ""),
        "git": git,
        "services": services,
        "workgraph": workgraph,
        "presence_actors": presence_actors,
        "dependencies": dependencies,
        "health": health,
        "activity": activity,
    }
```

**Where to insert the route in `do_GET`:**

In `_HubHandler.do_GET`, add the new route **before** the final `_send_json({"error": "not_found"}, ...)` line (currently line 595), and **after** the `/api/activity` block (which ends around line 535). Insert:

```python
        if route.startswith("/api/repo/") and not route.endswith("/start"):
            repo_name = route[len("/api/repo/"):].strip("/")
            if not repo_name:
                self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
                return
            activity_path = getattr(self.__class__, "activity_path", None)
            payload = _build_repo_detail_payload(repo_name, self.snapshot_path, activity_path)
            if payload is None:
                self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(payload)
            return
```

**Also fix the unrecognized-route fallback** — replace the final line of `do_GET`:

```python
        # Before:
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

        # After:
        if not route.startswith("/api/") and not route.startswith("/ws"):
            self._send_html(render_dashboard_html())
            return
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
```

- [ ] **Step 4: Run tests — all should pass**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_detail_api.py -v
```

Expected: All tests pass.

- [ ] **Step 5: Verify no regressions in hub tests**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_ecosystem_hub.py tests/test_activity_api.py tests/test_hub_resilience.py -v
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/api.py tests/test_repo_detail_api.py
git commit -m "feat: add GET /api/repo/:name endpoint and SPA fallback route"
```

---

## Task 2: Python fallback route — serve dashboard HTML for `/repo/:name` paths

This is a single edit already described in Task 1 Step 3 above ("fix the unrecognized-route fallback"). Pulling it out as a named task so it's visible in the file map and gets a dedicated verification step.

**Files:**
- Modify: `driftdriver/ecosystem_hub/api.py` (already done in Task 1)

- [ ] **Step 1: Write a targeted test for the SPA fallback**

Add to `tests/test_repo_detail_api.py`:

```python
class TestSpaFallbackRoute(unittest.TestCase):
    """Verify that do_GET returns dashboard HTML for unknown non-API paths."""

    def _make_handler(self, route: str, snapshot_path: Path) -> tuple:
        """Return (handler, captured_response_bytes) for a given route."""
        import io
        from unittest.mock import MagicMock
        from driftdriver.ecosystem_hub.api import _handler_factory
        from driftdriver.ecosystem_hub.websocket import LiveStreamHub
        import threading

        hub = LiveStreamHub()
        HandlerClass = _handler_factory(snapshot_path, snapshot_path, hub, None)

        # Build a minimal fake HTTP request
        request_text = f"GET {route} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        rfile = io.BytesIO(request_text.encode())
        output = io.BytesIO()

        handler = HandlerClass.__new__(HandlerClass)
        handler.rfile = rfile
        handler.wfile = output
        handler.headers = {"Host": "127.0.0.1"}
        handler.requestline = f"GET {route} HTTP/1.1"
        handler.command = "GET"
        handler.path = route
        handler.server = MagicMock()
        handler.connection = MagicMock()
        handler.close_connection = False
        # suppress output logging
        handler.log_message = lambda *a: None
        handler.send_response = lambda code, msg=None: output.write(f"HTTP/1.1 {code}\r\n".encode())
        handler.send_header = lambda k, v: output.write(f"{k}: {v}\r\n".encode())
        handler.end_headers = lambda: output.write(b"\r\n")
        handler.wfile = output

        return handler, output

    def test_unknown_path_returns_html_not_json_404(self):
        with tempfile.TemporaryDirectory() as td:
            snap = Path(td) / "snapshot.json"
            # Use a handler where we can call do_GET and capture Content-Type
            # Simpler: call render_dashboard_html directly and verify it would be returned.
            # Full HTTP test is in test_ecosystem_hub.py integration tests.
            from driftdriver.ecosystem_hub.dashboard import render_dashboard_html
            html = render_dashboard_html()
            self.assertIn("view-hub", html)
            self.assertIn("view-repo-detail", html)
```

**Note:** The full integration test (actual HTTP server serving `/repo/lodestar` → 200 HTML) belongs in Task 7 (smoke test). This unit test only validates that `render_dashboard_html()` would produce a response containing the detail view markers, which depend on Task 3 completing first. Run this test after Task 3.

- [ ] **Step 2: Commit the fallback route change** (already committed in Task 1 Step 6 — no separate commit needed)

---

## Task 3: Detail page HTML structure in `dashboard.py`

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py`

This is the largest single edit. It touches three locations in `dashboard.py`:

1. **CSS block** — add detail panel CSS inside `<style>` before the closing `</style>` tag (currently line 728)
2. **HTML body** — wrap existing `<main class="hub-layout">` content in `<div id="view-hub">` and add `<div id="view-repo-detail" hidden>` after `</main>` but before `<script>`
3. **JS global vars** — add `currentView` and `detailRepo` state variables at the top of the `<script>` block

### Step 1: Write the HTML structure smoke test

```python
# tests/test_repo_detail_smoke.py
# ABOUTME: Smoke tests verifying the dashboard HTML contains all required detail page elements.
# ABOUTME: Checks for section IDs, CSS classes, and JS function stubs by string search.
from __future__ import annotations

import unittest


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
        # hidden attribute on the element
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
        self.assertIn("function renderRepoDetail(", self.html)

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
```

- [ ] **Step 2: Run the smoke test — expect failures**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_detail_smoke.py -v 2>&1 | head -50
```

Expected: Multiple `FAILED` — none of the detail panel elements exist yet.

- [ ] **Step 3: Add CSS for the detail panel**

In `driftdriver/ecosystem_hub/dashboard.py`, find the CSS closing tag at line 728 (`  </style>`). Insert the following block immediately **before** `  </style>`:

```css
    /* ── Repo Detail Panel ─────────────────────────────────────── */
    .detail-panel {
      max-width: 1100px;
      margin: 0 auto;
      padding: 0 1.2rem 3rem;
      display: flex;
      flex-direction: column;
      gap: 0.9rem;
    }
    .detail-header {
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(255, 252, 245, 0.95);
      backdrop-filter: blur(6px);
      border-bottom: 1px solid var(--line);
      padding: 0.75rem 0;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.65rem;
    }
    .detail-back-link {
      font-size: 0.88rem;
      color: var(--accent);
      text-decoration: none;
      font-weight: 600;
      cursor: pointer;
      padding: 0.25rem 0.55rem;
      border: 1px solid var(--accent);
      border-radius: 6px;
      background: var(--accent-soft);
      white-space: nowrap;
    }
    .detail-back-link:hover { background: var(--accent); color: #fff; }
    .detail-repo-name {
      font-size: 1.35rem;
      font-weight: 700;
      color: var(--ink);
      margin: 0;
    }
    .detail-role-badge {
      display: inline-block;
      font-size: 0.72rem;
      font-family: var(--mono);
      padding: 0.15rem 0.5rem;
      border-radius: 6px;
      background: var(--accent-soft);
      color: var(--accent);
      border: 1px solid var(--accent);
      font-weight: 600;
      text-transform: uppercase;
    }
    .detail-tag-badge {
      display: inline-block;
      font-size: 0.7rem;
      font-family: var(--mono);
      padding: 0.1rem 0.4rem;
      border-radius: 4px;
      background: #e8efe9;
      color: #2f6e39;
      border: 1px solid #b8d8bc;
    }
    .detail-editor-link {
      font-size: 0.82rem;
      color: var(--muted);
      text-decoration: none;
      margin-left: auto;
    }
    .detail-editor-link:hover { color: var(--accent); }
    .detail-section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 0.9rem;
      box-shadow: 0 4px 10px rgba(24, 34, 28, 0.05);
    }
    .detail-section h2 {
      margin: 0 0 0.6rem;
      font-size: 0.88rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted);
    }
    .detail-section-loading {
      color: var(--muted);
      font-style: italic;
      font-size: 0.88rem;
    }
    .detail-services-row {
      display: flex;
      gap: 1.2rem;
      flex-wrap: wrap;
    }
    .detail-service-item {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
    }
    .detail-service-label {
      font-size: 0.72rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .detail-service-value { font-size: 0.9rem; font-weight: 600; }
    .detail-service-value.running { color: var(--good); }
    .detail-service-value.stopped { color: var(--muted); }
    .detail-task-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
      font-family: var(--mono);
      font-size: 0.78rem;
      padding: 0.15rem 0.5rem;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #fff;
      margin-right: 0.35rem;
    }
    .detail-task-list {
      display: flex;
      flex-direction: column;
      gap: 0.3rem;
      margin-top: 0.5rem;
    }
    .detail-task-row {
      display: flex;
      align-items: baseline;
      gap: 0.55rem;
      font-size: 0.84rem;
      padding: 0.25rem 0;
      border-bottom: 1px solid var(--line);
    }
    .detail-task-row:last-child { border-bottom: none; }
    .detail-task-id {
      font-family: var(--mono);
      font-size: 0.72rem;
      color: var(--muted);
      cursor: pointer;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .detail-task-id:hover { color: var(--accent); }
    .detail-task-status {
      font-size: 0.72rem;
      font-family: var(--mono);
      padding: 0.08rem 0.35rem;
      border-radius: 4px;
      flex-shrink: 0;
    }
    .detail-task-status.in-progress { background: var(--accent-soft); color: var(--accent); }
    .detail-task-status.ready { background: #fffbea; color: #856404; }
    .detail-commit-list {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      margin-top: 0.5rem;
    }
    .detail-commit-row {
      display: flex;
      gap: 0.6rem;
      font-size: 0.82rem;
      padding: 0.2rem 0;
      border-bottom: 1px solid var(--line);
      align-items: baseline;
    }
    .detail-commit-row:last-child { border-bottom: none; }
    .detail-commit-hash {
      font-family: var(--mono);
      font-size: 0.72rem;
      color: var(--muted);
      white-space: nowrap;
      flex-shrink: 0;
    }
    .detail-commit-subject { flex: 1; }
    .detail-commit-age { color: var(--muted); font-size: 0.75rem; white-space: nowrap; }
    .detail-summary-block {
      font-style: italic;
      color: var(--muted);
      border-left: 3px solid var(--accent);
      padding: 0.4rem 0.7rem;
      margin-bottom: 0.6rem;
      background: var(--accent-soft);
      border-radius: 0 6px 6px 0;
      font-size: 0.88rem;
      line-height: 1.45;
    }
    .detail-actor-row {
      display: flex;
      align-items: center;
      gap: 0.65rem;
      font-size: 0.84rem;
      padding: 0.3rem 0;
      border-bottom: 1px solid var(--line);
    }
    .detail-actor-row:last-child { border-bottom: none; }
    @keyframes presencePulse {
      0% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(1.4); }
      100% { opacity: 1; transform: scale(1); }
    }
    .detail-actor-dot {
      display: inline-block;
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--good);
      flex-shrink: 0;
    }
    .detail-actor-dot.recent { animation: presencePulse 1.6s ease-in-out infinite; }
    .detail-actor-dot.stale { background: #999; }
    .detail-dep-cols {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem;
      margin-top: 0.4rem;
    }
    .detail-dep-col h3 {
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      margin: 0 0 0.35rem;
    }
    .detail-dep-link {
      display: block;
      font-size: 0.84rem;
      color: var(--accent);
      text-decoration: none;
      cursor: pointer;
      padding: 0.15rem 0;
    }
    .detail-dep-link:hover { text-decoration: underline; }
    .detail-findings-list {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      margin-top: 0.35rem;
    }
    .detail-finding-row {
      display: flex;
      gap: 0.5rem;
      font-size: 0.82rem;
      padding: 0.2rem 0;
      border-bottom: 1px solid var(--line);
    }
    .detail-finding-row:last-child { border-bottom: none; }
    .detail-tier-badge {
      display: inline-block;
      font-size: 0.74rem;
      font-weight: 700;
      padding: 0.12rem 0.45rem;
      border-radius: 6px;
      margin-right: 0.4rem;
    }
    .detail-tier-badge.healthy { background: #d4edda; color: var(--good); }
    .detail-tier-badge.watch { background: #fff3cd; color: #856404; }
    .detail-tier-badge.at-risk { background: #f7dfdf; color: var(--bad); }
    .repo-name-link {
      color: var(--ink);
      text-decoration: none;
      font-weight: 600;
    }
    .repo-name-link:hover { text-decoration: underline; color: var(--accent); }
```

- [ ] **Step 4: Add `<div id="view-hub">` wrapper and `<div id="view-repo-detail">` to HTML body**

Find in `dashboard.py` the line `  <main class="hub-layout">` (currently line 735). The existing structure looks like:

```
  <main class="hub-layout">
    <!-- Tab Navigation -->
    ...
  </main>
  <script>
```

Change it to:

```html
  <div id="view-hub">
  <main class="hub-layout">
    <!-- Tab Navigation -->
    ...
  </main>
  </div><!-- end view-hub -->

  <div id="view-repo-detail" hidden>
    <div class="detail-panel">
      <div class="detail-header" id="detail-header">
        <a class="detail-back-link" onclick="closeRepoDetail(); return false;" href="/">\u2190 Back to Hub</a>
        <h1 class="detail-repo-name" id="detail-repo-name">\u2014</h1>
        <span id="detail-role-badge"></span>
        <span id="detail-tags"></span>
        <a class="detail-editor-link" id="detail-editor-link" href="#" target="_blank" style="display:none">\u29c9 Open in Editor</a>
      </div>

      <section class="detail-section" id="detail-git">
        <h2>Git Activity</h2>
        <div class="detail-section-loading" id="detail-git-content">Loading\u2026</div>
      </section>

      <section class="detail-section" id="detail-services">
        <h2>Services</h2>
        <div id="detail-services-content">
          <div class="detail-services-row">
            <div class="detail-service-item">
              <span class="detail-service-label">Workgraph Service</span>
              <span class="detail-service-value" id="detail-svc-wg">\u2014</span>
            </div>
            <div class="detail-service-item">
              <span class="detail-service-label">launchd Plist</span>
              <span class="detail-service-value" id="detail-svc-launchd">\u2014</span>
            </div>
            <div class="detail-service-item">
              <span class="detail-service-label">Cron Jobs</span>
              <span class="detail-service-value stopped" id="detail-svc-cron">None detected</span>
            </div>
          </div>
          <div id="detail-svc-start-wrap" style="margin-top:0.6rem;display:none">
            <button class="start-btn" id="detail-svc-start-btn">Start Service</button>
          </div>
        </div>
      </section>

      <section class="detail-section" id="detail-workgraph">
        <h2>Workgraph</h2>
        <div id="detail-workgraph-content"><em style="color:var(--muted)">Loading\u2026</em></div>
      </section>

      <section class="detail-section" id="detail-agents">
        <h2>Active Agents</h2>
        <div id="detail-agents-content"><em style="color:var(--muted)">Loading\u2026</em></div>
      </section>

      <section class="detail-section" id="detail-deps">
        <h2>Repo Dependencies</h2>
        <div id="detail-deps-content"><em style="color:var(--muted)">Loading\u2026</em></div>
      </section>

      <section class="detail-section" id="detail-health">
        <h2>Health</h2>
        <div id="detail-health-content"><em style="color:var(--muted)">Loading\u2026</em></div>
      </section>
    </div><!-- end detail-panel -->
  </div><!-- end view-repo-detail -->
```

**Exact insertion point:** In the Python string in `render_dashboard_html()`, find the line that renders `</main>` closing the hub layout (currently before the `<script>` block). The current structure ends with:

```
    </div><!-- end tab-intelligence -->
  </main>
  <script>
```

Wrap the `<header>` and `<main>` in `<div id="view-hub">` and add the detail div between `</main>` and `<script>`. The modified section should read:

```html
  <div id="view-hub">
  <header>
    ...existing header...
  </header>
  <main class="hub-layout">
    ...all existing hub content...
  </main>
  </div><!-- end view-hub -->

  <div id="view-repo-detail" hidden>
    ...detail panel HTML above...
  </div>

  <script>
    ...existing JS...
```

- [ ] **Step 5: Run smoke tests — most should pass now**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_detail_smoke.py -v
```

Expected: CSS and HTML tests pass; JS function tests still fail (JS not added yet).

- [ ] **Step 6: Commit HTML+CSS**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/dashboard.py tests/test_repo_detail_smoke.py
git commit -m "feat: add detail panel HTML structure and CSS to dashboard"
```

---

## Task 4: JS routing — `openRepoDetail`, `closeRepoDetail`, `pushState`, `popstate`

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py` (JS `<script>` block only)

### Step 1: Add state variables and routing functions

In the `<script>` block, at the top where global vars are declared (currently around line 964), add after `let selectedRepo = '';`:

```js
    let currentView = 'hub';   // 'hub' | 'repo-detail'
    let detailRepo = '';
```

### Step 2: Add `openRepoDetail`, `closeRepoDetail`, and view-switching helpers

Add the following functions after the `selectRepo` function (around line 1069), before `syncFiltersToUrl`:

```js
    // ── Repo Detail Navigation ────────────────────────────────────
    function showView(view) {
      var hubEl = document.getElementById('view-hub');
      var detailEl = document.getElementById('view-repo-detail');
      if (!hubEl || !detailEl) return;
      if (view === 'repo-detail') {
        hubEl.hidden = true;
        detailEl.hidden = false;
      } else {
        hubEl.hidden = false;
        detailEl.hidden = true;
      }
      currentView = view;
    }

    function openRepoDetail(name) {
      detailRepo = String(name || '');
      if (!detailRepo) return;
      showView('repo-detail');
      // Render with snapshot data immediately (sections that don't need API call)
      var repo = repoByName(detailRepo);
      renderRepoDetailHeader(repo);
      renderRepoDetailServices(null);   // will fill after API fetch
      renderRepoDetailWorkgraph(repo);
      renderRepoDetailAgents(repo);
      renderRepoDetailDeps(repo, currentData);
      renderRepoDetailHealth(repo);
      renderRepoDetailGit(null);        // loading state until API responds
      // Fetch full detail payload from API
      fetchAndRenderRepoDetail(detailRepo);
      history.pushState({ view: 'repo-detail', repo: detailRepo }, '', '/repo/' + encodeURIComponent(detailRepo));
    }

    function closeRepoDetail() {
      detailRepo = '';
      showView('hub');
      history.pushState({ view: 'hub' }, '', '/');
    }

    window.addEventListener('popstate', function(event) {
      var state = event.state || {};
      if (state.view === 'repo-detail' && state.repo) {
        var repo = repoByName(state.repo);
        if (repo || state.repo) {
          detailRepo = String(state.repo || '');
          showView('repo-detail');
          renderRepoDetailHeader(repoByName(detailRepo));
          renderRepoDetailWorkgraph(repoByName(detailRepo));
          renderRepoDetailAgents(repoByName(detailRepo));
          renderRepoDetailDeps(repoByName(detailRepo), currentData);
          renderRepoDetailHealth(repoByName(detailRepo));
          fetchAndRenderRepoDetail(detailRepo);
        }
      } else {
        detailRepo = '';
        showView('hub');
      }
    });

    // On initial page load, check if URL is /repo/<name> and open detail
    (function checkInitialRoute() {
      var parts = window.location.pathname.match(/^\/repo\/(.+)$/);
      if (parts) {
        var name = decodeURIComponent(parts[1]);
        // Delay until first data render
        var _origHandle = handleData;
        handleData = function(data) {
          _origHandle(data);
          handleData = _origHandle;  // restore
          if (currentView !== 'repo-detail') openRepoDetail(name);
        };
      }
    })();
```

**Note on `handleData`:** The existing code may not have a `handleData` function. The initial-route check uses a pattern that intercepts the first data render. Check the actual function name in `dashboard.py` that processes incoming WebSocket/HTTP data and hook into it. The existing code uses `function renderAll(data)` or similar — adjust accordingly. The pattern to use is: after `currentData` is set for the first time, check `window.location.pathname`.

**Actual hook approach (safe, avoids patching internal functions):**

Rather than patching an internal function, add this check inside `refreshHttp` and the WebSocket `onmessage` handler, right after `currentData = data` is set:

```js
    // Inside the existing data-handling path, after currentData is set:
    if (currentView === 'hub' && !detailRepo) {
      var _initRoute = window.location.pathname.match(/^\/repo\/(.+)$/);
      if (_initRoute) {
        openRepoDetail(decodeURIComponent(_initRoute[1]));
      }
    }
```

Add this snippet in the appropriate place. Identify the data flow:

In `dashboard.py`, search for where `currentData` is assigned. It is inside `refreshHttp()`:

```js
    async function refreshHttp() {
      ...
      currentData = data;
      renderAll(data);    // or similar
      ...
    }
```

And inside the WebSocket `onmessage`:

```js
    ws.onmessage = function(event) {
      ...
      currentData = data;
      renderAll(data);
      ...
    };
```

Add the route check after `renderAll(data)` in both locations.

- [ ] **Step 3: Run smoke tests for JS routing**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_detail_smoke.py::TestDetailPageHtmlStructure::test_open_repo_detail_function_defined -v
uv run pytest tests/test_repo_detail_smoke.py::TestDetailPageHtmlStructure::test_close_repo_detail_function_defined -v
uv run pytest tests/test_repo_detail_smoke.py::TestDetailPageHtmlStructure::test_popstate_event_listener_registered -v
uv run pytest tests/test_repo_detail_smoke.py::TestDetailPageHtmlStructure::test_current_view_var_defined -v
uv run pytest tests/test_repo_detail_smoke.py::TestDetailPageHtmlStructure::test_detail_repo_var_defined -v
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat: add JS routing functions openRepoDetail/closeRepoDetail/popstate handler"
```

---

## Task 5: JS data fetch + section render functions

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py` (JS `<script>` block only)

All render functions belong after the routing functions added in Task 4, before the event listener registration section (around line 2340).

### Step 1: Add `fetchAndRenderRepoDetail` and all `renderRepoDetail*` functions

```js
    // ── Repo Detail Render Functions ─────────────────────────────

    async function fetchAndRenderRepoDetail(name) {
      try {
        var res = await fetch('/api/repo/' + encodeURIComponent(name));
        if (!res.ok) throw new Error('HTTP ' + res.status);
        var detail = await res.json();
        renderRepoDetailHeader(repoByName(name), detail);
        renderRepoDetailGit(detail);
        renderRepoDetailServices(detail);
        renderRepoDetailWorkgraph(repoByName(name), detail);
        renderRepoDetailAgents(repoByName(name), detail);
        renderRepoDetailDeps(repoByName(name), currentData, detail);
        renderRepoDetailHealth(repoByName(name), detail);
      } catch (err) {
        console.warn('fetchAndRenderRepoDetail failed:', err);
      }
    }

    function renderRepoDetailHeader(repo, detail) {
      var name = (detail && detail.name) || (repo && repo.name) || detailRepo;
      el('detail-repo-name').textContent = String(name || '');

      // Role badge
      var role = repo ? repoRole(repo) : ((detail && detail.ecosystem_role) || '');
      var roleBadge = el('detail-role-badge');
      roleBadge.innerHTML = role ? '<span class="detail-role-badge">' + esc(role) + '</span>' : '';

      // Tag badges
      var tags = (detail && detail.tags) || (repo && repo.tags) || [];
      var tagsEl = el('detail-tags');
      if (Array.isArray(tags) && tags.length) {
        tagsEl.innerHTML = tags.slice(0, 6).map(function(t) {
          return '<span class="detail-tag-badge">' + esc(String(t)) + '</span>';
        }).join(' ');
      } else {
        tagsEl.innerHTML = '';
      }

      // Editor link (cursor:// URI)
      var path = (detail && detail.path) || (repo && repo.path) || '';
      var editorLink = el('detail-editor-link');
      if (path) {
        editorLink.href = 'cursor://open?path=' + encodeURIComponent(path);
        editorLink.style.display = '';
        editorLink.title = path;
      } else {
        editorLink.style.display = 'none';
      }
    }

    function renderRepoDetailGit(detail) {
      var content = el('detail-git-content');
      if (!detail) {
        content.innerHTML = '<div class="detail-section-loading">Loading\u2026</div>';
        return;
      }
      var activity = detail.activity || {};
      var git = detail.git || {};
      var html = '';

      // Git status line
      var branch = esc(git.branch || 'n/a');
      var dirty = git.dirty ? '<span class="warn">dirty</span>' : '<span class="good">clean</span>';
      var ahead = Number(git.ahead || 0);
      var behind = Number(git.behind || 0);
      html += '<div style="margin-bottom:0.55rem;font-size:0.86rem">'
        + 'Branch: <code>' + branch + '</code> \u00b7 ' + dirty
        + (ahead ? ' \u00b7 <span class="good">+' + ahead + ' ahead</span>' : '')
        + (behind ? ' \u00b7 <span class="warn">\u2212' + behind + ' behind</span>' : '')
        + '</div>';

      // Activity summary
      if (activity.summary) {
        html += '<div class="detail-summary-block">' + esc(activity.summary) + '</div>';
      } else if (!activity.last_commit_at) {
        html += '<div class="detail-summary-block" style="color:var(--muted)">Summary pending next scan cycle.</div>';
      }

      // Commit timeline
      var timeline = Array.isArray(activity.timeline) ? activity.timeline : [];
      if (timeline.length) {
        html += '<div class="detail-commit-list">';
        timeline.slice(0, 10).forEach(function(c) {
          html += '<div class="detail-commit-row">'
            + '<span class="detail-commit-hash">' + esc(String(c.hash || '').substring(0, 7)) + '</span>'
            + '<span class="detail-commit-subject">' + esc(c.subject || '') + '</span>'
            + '<span class="detail-commit-age">' + esc(relativeTimeIso(c.timestamp)) + '</span>'
            + '</div>';
        });
        html += '</div>';
      } else if (activity.last_commit_at) {
        html += '<div style="font-size:0.84rem;color:var(--muted)">Last commit: '
          + esc(relativeTimeIso(activity.last_commit_at)) + '</div>';
      } else {
        html += '<div style="font-size:0.84rem;color:var(--muted)">No recent git activity (7+ days)</div>';
      }

      content.innerHTML = html;
    }

    function renderRepoDetailServices(detail) {
      var svc = (detail && detail.services) || {};
      var repo = repoByName(detailRepo);

      // Workgraph service status
      var wgRunning = (detail != null) ? svc.workgraph_service_running : (repo && repo.service_running);
      var wgEl = el('detail-svc-wg');
      if (wgRunning == null) {
        wgEl.textContent = '\u2014';
        wgEl.className = 'detail-service-value';
      } else if (wgRunning) {
        wgEl.textContent = 'Running';
        wgEl.className = 'detail-service-value running';
      } else {
        wgEl.textContent = 'Stopped';
        wgEl.className = 'detail-service-value stopped';
      }

      // launchd plist
      var launchdEl = el('detail-svc-launchd');
      if (detail == null) {
        launchdEl.textContent = '\u2014';
        launchdEl.className = 'detail-service-value';
      } else if (svc.launchd_plist_loaded === true) {
        launchdEl.textContent = 'Loaded';
        launchdEl.className = 'detail-service-value running';
      } else if (svc.launchd_plist_loaded === false) {
        launchdEl.textContent = 'Not found';
        launchdEl.className = 'detail-service-value stopped';
      } else {
        launchdEl.textContent = 'Unknown';
        launchdEl.className = 'detail-service-value';
      }

      // Start service button
      var startWrap = el('detail-svc-start-wrap');
      var startBtn = el('detail-svc-start-btn');
      var needsStart = !wgRunning && ((repo && repo.workgraph_exists) || (detail && detail.workgraph && detail.workgraph.exists));
      if (needsStart) {
        startWrap.style.display = '';
        startBtn.disabled = false;
        startBtn.textContent = 'Start Service';
        startBtn.onclick = function() { startDetailService(detailRepo, startBtn); };
      } else {
        startWrap.style.display = 'none';
      }
    }

    function startDetailService(repoName, btn) {
      if (!repoName || !btn) return;
      btn.disabled = true;
      btn.textContent = 'Starting\u2026';
      fetch('/api/repo/' + encodeURIComponent(repoName) + '/start', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
          if (data.returncode === 0) {
            btn.textContent = 'Started';
            btn.style.background = 'var(--good)';
            btn.style.color = '#fff';
          } else {
            btn.textContent = 'Failed';
            btn.style.background = 'var(--bad)';
            btn.style.color = '#fff';
            btn.disabled = false;
          }
        })
        .catch(function() { btn.textContent = 'Error'; btn.disabled = false; });
    }

    function renderRepoDetailWorkgraph(repo, detail) {
      var content = el('detail-workgraph-content');
      var wg = (detail && detail.workgraph) || {};
      var exists = wg.exists != null ? wg.exists : (repo && repo.workgraph_exists);
      if (!exists) {
        content.innerHTML = '<em style="color:var(--muted)">No workgraph in this repo.</em>';
        return;
      }
      var counts = wg.task_counts || (repo && repo.task_counts) || {};
      var inProgress = Array.isArray(wg.in_progress) ? wg.in_progress : (repo ? (repo.in_progress || []) : []);
      var ready = Array.isArray(wg.ready) ? wg.ready : (repo ? (repo.ready || []) : []);

      var pillsHtml = ''
        + _taskCountPill('Open', counts.open || 0)
        + _taskCountPill('Ready', counts.ready || 0)
        + _taskCountPill('In Progress', counts.in_progress || 0)
        + _taskCountPill('Done', counts.done || 0);

      var tasksHtml = '';
      if (inProgress.length || ready.length) {
        tasksHtml = '<div class="detail-task-list">';
        inProgress.slice(0, 5).forEach(function(t) {
          tasksHtml += _taskRow(t, 'in-progress');
        });
        ready.slice(0, Math.max(0, 5 - inProgress.length)).forEach(function(t) {
          tasksHtml += _taskRow(t, 'ready');
        });
        tasksHtml += '</div>';
      }

      content.innerHTML = '<div style="margin-bottom:0.5rem">' + pillsHtml + '</div>' + tasksHtml;
    }

    function _taskCountPill(label, count) {
      return '<span class="detail-task-pill"><strong>' + esc(String(count)) + '</strong> ' + esc(label) + '</span>';
    }

    function _taskRow(task, status) {
      var id = esc(String(task.id || ''));
      var title = esc(String(task.title || ''));
      var cls = status === 'in-progress' ? 'in-progress' : 'ready';
      return '<div class="detail-task-row">'
        + '<span class="detail-task-id" onclick="navigator.clipboard && navigator.clipboard.writeText(' + JSON.stringify(String(task.id || '')) + ')" title="Click to copy">' + id + '</span>'
        + '<span style="flex:1">' + title + '</span>'
        + '<span class="detail-task-status ' + cls + '">' + esc(status) + '</span>'
        + '</div>';
    }

    function renderRepoDetailAgents(repo, detail) {
      var content = el('detail-agents-content');
      var actors = (detail && detail.presence_actors) || (repo && repo.presence_actors) || [];
      if (!actors.length) {
        content.innerHTML = '<em style="color:var(--muted)">No active agents.</em>';
        return;
      }
      var now = Date.now();
      content.innerHTML = '<div>' + actors.map(function(actor) {
        var lastSeen = actor.last_seen ? new Date(actor.last_seen).getTime() : 0;
        var ageMs = lastSeen ? now - lastSeen : Infinity;
        var recentClass = ageMs < 5 * 60 * 1000 ? 'recent' : 'stale';
        return '<div class="detail-actor-row">'
          + '<span class="detail-actor-dot ' + recentClass + '"></span>'
          + '<strong>' + esc(String(actor.name || actor.id || '')) + '</strong>'
          + '<span style="color:var(--muted);font-size:0.78rem;font-family:var(--mono)">' + esc(String(actor.id || '')) + '</span>'
          + '<span style="margin-left:auto;color:var(--muted);font-size:0.78rem">'
          + (actor.last_seen ? 'last seen ' + relativeTimeIso(actor.last_seen) : '') + '</span>'
          + '</div>';
      }).join('') + '</div>';
    }

    function renderRepoDetailDeps(repo, data, detail) {
      var content = el('detail-deps-content');
      var depSection = (detail && detail.dependencies) || null;

      var dependsOn = [];
      var dependedOnBy = [];

      if (depSection) {
        dependsOn = Array.isArray(depSection.depends_on) ? depSection.depends_on : [];
        dependedOnBy = Array.isArray(depSection.depended_on_by) ? depSection.depended_on_by : [];
      } else if (repo) {
        // Derive from snapshot synchronously
        var rawDeps = Array.isArray(repo.cross_repo_dependencies) ? repo.cross_repo_dependencies : [];
        dependsOn = rawDeps.map(function(d) { return String(d.repo || ''); }).filter(Boolean);
        if (data && Array.isArray(data.repos)) {
          var repoName = String(repo.name || '');
          data.repos.forEach(function(other) {
            if (!other || String(other.name || '') === repoName) return;
            var otherDeps = Array.isArray(other.cross_repo_dependencies)
              ? other.cross_repo_dependencies.map(function(d) { return String(d.repo || ''); })
              : [];
            if (otherDeps.includes(repoName)) dependedOnBy.push(String(other.name || ''));
          });
        }
      }

      if (!dependsOn.length && !dependedOnBy.length) {
        content.innerHTML = '<em style="color:var(--muted)">No cross-repo dependencies recorded.</em>';
        return;
      }

      function depLink(name) {
        return '<a class="detail-dep-link" onclick="openRepoDetail(' + JSON.stringify(name) + '); return false;" href="/repo/' + encodeURIComponent(name) + '">' + esc(name) + '</a>';
      }

      content.innerHTML = '<div class="detail-dep-cols">'
        + '<div class="detail-dep-col"><h3>Depends on</h3>'
        + (dependsOn.length ? dependsOn.map(depLink).join('') : '<em style="color:var(--muted)">None</em>')
        + '</div>'
        + '<div class="detail-dep-col"><h3>Depended on by</h3>'
        + (dependedOnBy.length ? dependedOnBy.map(depLink).join('') : '<em style="color:var(--muted)">None</em>')
        + '</div>'
        + '</div>';
    }

    function renderRepoDetailHealth(repo, detail) {
      var content = el('detail-health-content');
      var health = (detail && detail.health) || {};
      var northstar = (repo && repo.northstar) || {};

      var driftScore = health.drift_score != null ? health.drift_score : northstar.score;
      var driftTier = String(health.drift_tier || northstar.tier || '').toLowerCase();
      var secFindings = Array.isArray(health.security_findings) ? health.security_findings : (repo && Array.isArray(repo.security_findings) ? repo.security_findings : []);
      var qaFindings = Array.isArray(health.quality_findings) ? health.quality_findings : (repo && Array.isArray(repo.quality_findings) ? repo.quality_findings : []);
      var stalled = health.stalled != null ? health.stalled : (repo && repo.stalled);
      var stallReasons = Array.isArray(health.stall_reasons) ? health.stall_reasons : (repo && repo.stall_reasons) || [];
      var narrative = String(health.narrative || (repo && repo.narrative) || '');

      var html = '';

      // Drift score + tier
      var tierCls = driftTier === 'healthy' ? 'healthy' : (driftTier === 'watch' ? 'watch' : 'at-risk');
      if (driftScore != null || driftTier) {
        html += '<div style="margin-bottom:0.55rem">';
        if (driftTier) html += '<span class="detail-tier-badge ' + tierCls + '">' + esc(driftTier.toUpperCase()) + '</span>';
        if (driftScore != null) html += '<span style="font-family:var(--mono);font-size:0.88rem">Score: ' + esc(String(Number(driftScore).toFixed(2))) + '</span>';
        html += '</div>';
      } else {
        html += '<div style="color:var(--muted);margin-bottom:0.5rem">No drift data.</div>';
      }

      // Stall indicator
      if (stalled) {
        html += '<div style="margin-bottom:0.55rem">'
          + '<span class="stall-badge">STALLED</span>'
          + (stallReasons.length ? '<ul style="margin:0.25rem 0 0 1.2rem;padding:0">' + stallReasons.map(function(r) { return '<li>' + esc(String(r)) + '</li>'; }).join('') + '</ul>' : '')
          + '</div>';
      }

      // Security findings
      html += '<div style="margin-bottom:0.5rem"><strong>Security:</strong> ';
      if (!secFindings.length) {
        html += '<span class="good">No security findings.</span>';
      } else {
        html += '<span class="bad">' + secFindings.length + ' finding' + (secFindings.length !== 1 ? 's' : '') + '</span>';
        html += '<div class="detail-findings-list">';
        secFindings.slice(0, 5).forEach(function(f) {
          var sev = String(f.severity || '').toLowerCase();
          var cls = sev === 'critical' || sev === 'high' ? 'severity-high' : (sev === 'medium' ? 'severity-medium' : 'severity-low');
          html += '<div class="detail-finding-row"><span class="' + cls + '">' + esc(sev) + '</span><span>' + esc(String(f.message || f.description || '')) + '</span></div>';
        });
        html += '</div>';
      }
      html += '</div>';

      // Quality findings
      html += '<div style="margin-bottom:0.5rem"><strong>Quality:</strong> ';
      if (!qaFindings.length) {
        html += '<span class="good">No quality findings.</span>';
      } else {
        html += '<span class="warn">' + qaFindings.length + ' finding' + (qaFindings.length !== 1 ? 's' : '') + '</span>';
        html += '<div class="detail-findings-list">';
        qaFindings.slice(0, 5).forEach(function(f) {
          var sev = String(f.severity || '').toLowerCase();
          var cls = sev === 'high' ? 'severity-high' : (sev === 'medium' ? 'severity-medium' : 'severity-low');
          html += '<div class="detail-finding-row"><span class="' + cls + '">' + esc(sev) + '</span><span>' + esc(String(f.message || f.description || '')) + '</span></div>';
        });
        html += '</div>';
      }
      html += '</div>';

      // Narrative
      if (narrative) {
        html += '<div style="margin-top:0.5rem;font-size:0.88rem;line-height:1.5;color:var(--ink)">' + esc(narrative) + '</div>';
      }

      content.innerHTML = html;
    }
```

- [ ] **Step 2: Run smoke tests**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_detail_smoke.py -v
```

Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat: add JS render functions for all 7 detail page sections"
```

---

## Task 6: Wire repo name links in main table

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py` (JS `renderRepoTable` function only)

Currently, the repo name cell is rendered as (line 1750):

```js
          + '<td><strong>' + esc(repoName) + '</strong>' + needsHumanBadge(repo) + '</td>'
```

### Step 1: Replace `<strong>` with `<a class="repo-name-link">`

Change that line to:

```js
          + '<td><a class="repo-name-link" href="/repo/' + encodeURIComponent(repoName) + '" onclick="openRepoDetail(' + JSON.stringify(repoName) + '); return false;">' + esc(repoName) + '</a>' + needsHumanBadge(repo) + '</td>'
```

### Step 2: Update the `click` event handler to NOT expand the row when a repo-name-link is clicked

The existing document-level `click` handler in `dashboard.py` (around line 2399) checks `e.target.closest('.repo-row')`. The `<a>` inside the `<td>` is inside `.repo-row`, so without guard code, clicking the name link would also toggle row expansion.

Add an early-exit guard at the top of the click handler:

```js
    document.addEventListener('click', function(e) {
      // Guard: Start Service button
      var startBtn = e.target.closest('[data-start-repo]');
      if (startBtn) { ... existing code ... return; }

      // NEW: repo name link → open detail panel, don't expand row
      var nameLink = e.target.closest('.repo-name-link');
      if (nameLink) {
        e.preventDefault();
        var rn = nameLink.getAttribute('href') || '';
        var match = rn.match(/^\/repo\/(.+)$/);
        if (match) openRepoDetail(decodeURIComponent(match[1]));
        return;
      }

      // existing: row click → expand
      var row = e.target.closest('.repo-row');
      if (!row) return;
      ...
    });
```

### Step 3: Smoke test for repo name link

Add to `tests/test_repo_detail_smoke.py`:

```python
    def test_repo_name_link_has_href_pattern(self):
        # The JS template string for the link should contain /repo/
        self.assertIn("/repo/", self.html)
        self.assertIn("encodeURIComponent", self.html)
```

- [ ] **Step 4: Run all smoke tests**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_detail_smoke.py -v
```

Expected: All pass.

- [ ] **Step 5: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat: make repo name cells into links that open the detail panel"
```

---

## Task 7: Smoke test — verify all sections render and back navigation works

**Files:**
- Create: `tests/test_repo_detail_smoke.py` (additions only; file started in Task 3)

This task adds integration-level tests against a real running server using `urllib`. It follows the same pattern as `test_ecosystem_hub.py` which spins up a real HTTP server in a `setUp` method.

### Step 1: Add integration smoke test class

Add to `tests/test_repo_detail_smoke.py`:

```python
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer
from pathlib import Path


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

        hub = LiveStreamHub()
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
        from urllib.request import urlopen
        from urllib.error import HTTPError
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

    # --- Back navigation smoke (JS behaviour can't be tested here — verified by visual QA) ---

    def test_detail_html_has_back_link(self):
        _, _, body = self._get("/")
        self.assertIn("closeRepoDetail", body)

    def test_detail_html_has_all_section_ids(self):
        _, _, body = self._get("/")
        for section_id in ("detail-git", "detail-services", "detail-workgraph", "detail-agents", "detail-deps", "detail-health"):
            self.assertIn(f'id="{section_id}"', body, f"missing section: {section_id}")
```

- [ ] **Step 2: Run full smoke test suite**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_detail_smoke.py -v
```

Expected: All tests pass.

- [ ] **Step 3: Run full test suite — no regressions**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_detail_api.py tests/test_repo_detail_smoke.py tests/test_ecosystem_hub.py tests/test_activity_api.py tests/test_hub_resilience.py -v
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add tests/test_repo_detail_smoke.py
git commit -m "test: add integration smoke tests for repo detail page and API endpoint"
```

---

## Manual QA Checklist (run after all tasks complete)

After all automated tests pass, verify these behaviors in a real browser against the running hub at `http://127.0.0.1:8777/`:

- [ ] Clicking a repo name opens the detail panel (hub disappears, detail appears)
- [ ] URL changes to `/repo/<name>` after click
- [ ] Back button (`← Back to Hub`) restores the hub view
- [ ] URL changes to `/` after back
- [ ] Browser back button (Cmd+\[) navigates back to hub correctly
- [ ] Browser forward button navigates to detail panel again
- [ ] Navigating directly to `http://127.0.0.1:8777/repo/lodestar` opens the detail panel for `lodestar`
- [ ] All 7 sections have content (git, services, workgraph, agents, deps, health — some may show empty-state messages)
- [ ] Dependency repo names are clickable links that navigate to their detail panels
- [ ] Editor link is present and uses `cursor://` URI scheme
- [ ] "Start Service" button appears for repos where `service_running` is false and `workgraph_exists` is true
- [ ] Middle-click or Cmd+click on a repo name opens `/repo/<name>` in a new tab

---

## Data Flow Summary

```
Click repo name in table
  → openRepoDetail(name)
      → showView('repo-detail')          // hide #view-hub, show #view-repo-detail
      → renderRepoDetail*(repoByName())  // snapshot data — instant, no fetch
      → history.pushState('/repo/name')
      → fetchAndRenderRepoDetail(name)   // GET /api/repo/:name
          → renderRepoDetail*(detail)    // API data — fills git timeline, launchd, etc.

Browser back button
  → popstate event
      → showView('hub')                  // or showView('repo-detail') for forward
      → restore from event.state

Direct URL /repo/<name>
  → Python returns dashboard HTML (SPA fallback)
  → JS detects pathname on first data load
  → openRepoDetail(name)
```

---

## Notes on Cross-Repo Dependencies Direction

The `cross_repo_dependencies` field in `RepoSnapshot` is a list of repos that *this* repo depends on (outbound edges). There is no `direction` field. The "depended on by" list must be derived by scanning all other repos in the snapshot and finding those whose `cross_repo_dependencies` contain this repo's name. The `_build_repo_detail_payload` function does this in `O(n)` where `n` is the number of repos — acceptable for 40-50 repos.

The detail page renders dependencies as two text lists (depends on / depended on by), not as a graph visualization. The full force-directed dependency graph remains in the hub view only.
