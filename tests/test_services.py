# ABOUTME: Tests for detect_services() in driftdriver/ecosystem_hub/services.py.
# ABOUTME: Uses real tempfile dirs and subprocess patching — no external process calls in unit tests.
from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.ecosystem_hub.services import (
    detect_services,
    _parse_launchctl_list,
    _find_launchd_plists,
    _validate_plist_path,
)


# ---------------------------------------------------------------------------
# _parse_launchctl_list
# ---------------------------------------------------------------------------

class TestParseLaunchctlList(unittest.TestCase):
    def test_running_service_has_pid(self):
        output = "PID\tStatus\tLabel\n96464\t143\tcom.paia.shell\n"
        result = _parse_launchctl_list(output)
        self.assertEqual(result["com.paia.shell"], 96464)

    def test_stopped_service_has_none(self):
        output = "-\t0\tcom.braydon.driftdriver-ecosystem\n"
        result = _parse_launchctl_list(output)
        self.assertIsNone(result["com.braydon.driftdriver-ecosystem"])

    def test_mixed_output(self):
        output = (
            "PID\tStatus\tLabel\n"
            "96464\t143\tcom.paia.media\n"
            "-\t0\tcom.braydon.driftdriver-ecosystem\n"
        )
        result = _parse_launchctl_list(output)
        self.assertEqual(result["com.paia.media"], 96464)
        self.assertIsNone(result["com.braydon.driftdriver-ecosystem"])

    def test_empty_output_returns_empty_dict(self):
        self.assertEqual(_parse_launchctl_list(""), {})

    def test_header_line_skipped(self):
        output = "PID\tStatus\tLabel\n-\t0\tcom.foo\n"
        result = _parse_launchctl_list(output)
        self.assertNotIn("Label", result)
        self.assertIn("com.foo", result)


# ---------------------------------------------------------------------------
# _find_launchd_plists
# ---------------------------------------------------------------------------

class TestFindLaunchdPlists(unittest.TestCase):
    def _make_plist(self, launch_agents_dir: Path, filename: str,
                    working_dir: str, label: str) -> Path:
        plist_path = launch_agents_dir / filename
        data = {
            "Label": label,
            "ProgramArguments": ["/usr/bin/true"],
            "WorkingDirectory": working_dir,
        }
        with open(plist_path, "wb") as fp:
            plistlib.dump(data, fp)
        return plist_path

    def test_exact_working_directory_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            repo_path = Path(tmp) / "myrepo"
            repo_path.mkdir()
            self._make_plist(agents_dir, "com.paia.shell.plist",
                             str(repo_path), "com.paia.shell")
            results = _find_launchd_plists("myrepo", str(repo_path),
                                           launch_agents_dir=agents_dir)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["label"], "com.paia.shell")
            self.assertEqual(results[0]["plist_path"], str(agents_dir / "com.paia.shell.plist"))

    def test_no_match_when_different_working_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            repo_path = Path(tmp) / "myrepo"
            repo_path.mkdir()
            other_path = Path(tmp) / "otherrepo"
            other_path.mkdir()
            self._make_plist(agents_dir, "com.other.plist",
                             str(other_path), "com.other")
            results = _find_launchd_plists("myrepo", str(repo_path),
                                           launch_agents_dir=agents_dir)
            self.assertEqual(len(results), 0)

    def test_filename_heuristic_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            repo_path = Path(tmp) / "paia-shell"
            repo_path.mkdir()
            # plist has no WorkingDirectory but filename matches
            plist_data = {"Label": "com.paia.shell", "ProgramArguments": ["/usr/bin/true"]}
            plist_path = agents_dir / "com.paia.shell.plist"
            with open(plist_path, "wb") as fp:
                plistlib.dump(plist_data, fp)
            results = _find_launchd_plists("paia-shell", str(repo_path),
                                           launch_agents_dir=agents_dir)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["label"], "com.paia.shell")

    def test_multiple_plists_for_same_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            repo_path = Path(tmp) / "lfw-ai-graph-crm"
            repo_path.mkdir()
            self._make_plist(agents_dir, "com.lfw.graph.crm.plist",
                             str(repo_path), "com.lfw.graph.crm")
            self._make_plist(agents_dir, "com.lfw.graph.crm.proactive.plist",
                             str(repo_path), "com.lfw.graph.crm.proactive")
            results = _find_launchd_plists("lfw-ai-graph-crm", str(repo_path),
                                           launch_agents_dir=agents_dir)
            self.assertEqual(len(results), 2)

    def test_malformed_plist_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            bad_plist = agents_dir / "com.broken.plist"
            bad_plist.write_bytes(b"NOT A PLIST AT ALL")
            results = _find_launchd_plists("broken", "/some/repo",
                                           launch_agents_dir=agents_dir)
            self.assertEqual(results, [])

    def test_missing_launch_agents_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_dir = Path(tmp) / "NonExistentAgents"
            results = _find_launchd_plists("myrepo", "/some/repo",
                                           launch_agents_dir=missing_dir)
            self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# _validate_plist_path
# ---------------------------------------------------------------------------

class TestValidatePlistPath(unittest.TestCase):
    def test_valid_path_within_launch_agents(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            plist = agents_dir / "com.test.plist"
            plist.write_bytes(b"")
            result = _validate_plist_path(str(plist), launch_agents_dir=agents_dir)
            self.assertTrue(result)

    def test_path_outside_launch_agents_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            outside = Path(tmp) / "other.plist"
            outside.write_bytes(b"")
            result = _validate_plist_path(str(outside), launch_agents_dir=agents_dir)
            self.assertFalse(result)

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            traversal = str(agents_dir / ".." / "etc" / "passwd")
            result = _validate_plist_path(traversal, launch_agents_dir=agents_dir)
            self.assertFalse(result)

    def test_nonexistent_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            missing = agents_dir / "missing.plist"
            result = _validate_plist_path(str(missing), launch_agents_dir=agents_dir)
            self.assertFalse(result)


# ---------------------------------------------------------------------------
# detect_services — workgraph detection
# ---------------------------------------------------------------------------

class TestDetectServicesWorkgraph(unittest.TestCase):
    def test_no_workgraph_dir_present_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            with patch("driftdriver.ecosystem_hub.services._run_launchctl_list",
                       return_value={}), \
                 patch("driftdriver.ecosystem_hub.services._run_crontab_l",
                       return_value=[]):
                result = detect_services("myrepo", str(repo))
            self.assertFalse(result["workgraph"]["present"])
            self.assertIsNone(result["workgraph"]["status"])

    def test_workgraph_dir_present_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            (repo / ".workgraph").mkdir(parents=True)
            with patch("driftdriver.ecosystem_hub.services._run_wg_service_status",
                       return_value="running"), \
                 patch("driftdriver.ecosystem_hub.services._run_launchctl_list",
                       return_value={}), \
                 patch("driftdriver.ecosystem_hub.services._run_crontab_l",
                       return_value=[]):
                result = detect_services("myrepo", str(repo))
            self.assertTrue(result["workgraph"]["present"])
            self.assertEqual(result["workgraph"]["status"], "running")

    def test_workgraph_stopped_when_wg_returns_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            (repo / ".workgraph").mkdir(parents=True)
            with patch("driftdriver.ecosystem_hub.services._run_wg_service_status",
                       return_value="stopped"), \
                 patch("driftdriver.ecosystem_hub.services._run_launchctl_list",
                       return_value={}), \
                 patch("driftdriver.ecosystem_hub.services._run_crontab_l",
                       return_value=[]):
                result = detect_services("myrepo", str(repo))
            self.assertEqual(result["workgraph"]["status"], "stopped")


# ---------------------------------------------------------------------------
# detect_services — launchd detection
# ---------------------------------------------------------------------------

class TestDetectServicesLaunchd(unittest.TestCase):
    def _make_plist(self, agents_dir: Path, filename: str,
                    working_dir: str, label: str) -> None:
        plist_data = {"Label": label, "WorkingDirectory": working_dir,
                      "ProgramArguments": ["/usr/bin/true"]}
        with open(agents_dir / filename, "wb") as fp:
            plistlib.dump(plist_data, fp)

    def test_running_launchd_service_has_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            repo_path = Path(tmp) / "paia-shell"
            repo_path.mkdir()
            self._make_plist(agents_dir, "com.paia.shell.plist",
                             str(repo_path), "com.paia.shell")
            with patch("driftdriver.ecosystem_hub.services._run_launchctl_list",
                       return_value={"com.paia.shell": 49062}), \
                 patch("driftdriver.ecosystem_hub.services._run_wg_service_status",
                       return_value="stopped"), \
                 patch("driftdriver.ecosystem_hub.services._run_crontab_l",
                       return_value=[]):
                result = detect_services(
                    "paia-shell", str(repo_path),
                    launch_agents_dir=agents_dir,
                )
            self.assertEqual(len(result["launchd"]), 1)
            svc = result["launchd"][0]
            self.assertEqual(svc["label"], "com.paia.shell")
            self.assertEqual(svc["status"], "running")
            self.assertEqual(svc["pid"], 49062)

    def test_stopped_launchd_service_has_no_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            repo_path = Path(tmp) / "paia-shell"
            repo_path.mkdir()
            self._make_plist(agents_dir, "com.paia.shell.plist",
                             str(repo_path), "com.paia.shell")
            with patch("driftdriver.ecosystem_hub.services._run_launchctl_list",
                       return_value={"com.paia.shell": None}), \
                 patch("driftdriver.ecosystem_hub.services._run_wg_service_status",
                       return_value="stopped"), \
                 patch("driftdriver.ecosystem_hub.services._run_crontab_l",
                       return_value=[]):
                result = detect_services(
                    "paia-shell", str(repo_path),
                    launch_agents_dir=agents_dir,
                )
            svc = result["launchd"][0]
            self.assertEqual(svc["status"], "stopped")
            self.assertIsNone(svc["pid"])

    def test_no_plists_returns_empty_launchd_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "myrepo"
            repo_path.mkdir()
            agents_dir = Path(tmp) / "LaunchAgents"
            agents_dir.mkdir()
            with patch("driftdriver.ecosystem_hub.services._run_launchctl_list",
                       return_value={}), \
                 patch("driftdriver.ecosystem_hub.services._run_wg_service_status",
                       return_value="stopped"), \
                 patch("driftdriver.ecosystem_hub.services._run_crontab_l",
                       return_value=[]):
                result = detect_services(
                    "myrepo", str(repo_path),
                    launch_agents_dir=agents_dir,
                )
            self.assertEqual(result["launchd"], [])


# ---------------------------------------------------------------------------
# detect_services — cron detection
# ---------------------------------------------------------------------------

class TestDetectServicesCron(unittest.TestCase):
    def test_matching_cron_job_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "myrepo"
            repo_path.mkdir()
            cron_lines = [f"0 8 * * 1   {repo_path}/scripts/weekly.sh"]
            with patch("driftdriver.ecosystem_hub.services._run_launchctl_list",
                       return_value={}), \
                 patch("driftdriver.ecosystem_hub.services._run_wg_service_status",
                       return_value="stopped"), \
                 patch("driftdriver.ecosystem_hub.services._run_crontab_l",
                       return_value=cron_lines):
                result = detect_services("myrepo", str(repo_path))
            self.assertEqual(result["cron"]["jobs"], cron_lines)

    def test_no_cron_jobs_when_none_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "myrepo"
            repo_path.mkdir()
            with patch("driftdriver.ecosystem_hub.services._run_launchctl_list",
                       return_value={}), \
                 patch("driftdriver.ecosystem_hub.services._run_wg_service_status",
                       return_value="stopped"), \
                 patch("driftdriver.ecosystem_hub.services._run_crontab_l",
                       return_value=[]):
                result = detect_services("myrepo", str(repo_path))
            self.assertEqual(result["cron"]["jobs"], [])


# ---------------------------------------------------------------------------
# detect_services — response shape
# ---------------------------------------------------------------------------

class TestDetectServicesShape(unittest.TestCase):
    def test_response_has_all_top_level_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            with patch("driftdriver.ecosystem_hub.services._run_launchctl_list",
                       return_value={}), \
                 patch("driftdriver.ecosystem_hub.services._run_crontab_l",
                       return_value=[]):
                result = detect_services("myrepo", str(repo))
            self.assertIn("workgraph", result)
            self.assertIn("launchd", result)
            self.assertIn("cron", result)
            self.assertIn("repo", result)


if __name__ == "__main__":
    unittest.main()
