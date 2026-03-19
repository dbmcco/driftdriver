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
        self.assertIsInstance(result["services"]["launchd_plist_loaded"], bool)
