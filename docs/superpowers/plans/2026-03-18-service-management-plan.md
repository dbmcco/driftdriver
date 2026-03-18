# Service Management UI — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `detect_services()` function to a new `services.py` module. Wire it into `api.py` as `GET /api/repo/:name/services` plus four POST endpoints for workgraph stop and launchd start/stop/restart. Add JS service cards to the detail page. All subprocess calls use explicit argument lists (`shell=False`). Plist path validation prevents privilege escalation.

**Architecture:** One new Python file (`driftdriver/ecosystem_hub/services.py`). One existing file modified (`api.py`) for five new routes. One existing file modified (`dashboard.py`) for the service cards JS and HTML. The `_detect_services()` function runs detection for all three service types (workgraph, launchd, cron) and is called by both the GET endpoint and the validation step inside launchd POST handlers.

**Tech Stack:** Python 3.11+, `plistlib` + `subprocess` + `pathlib` (stdlib), vanilla JS in `dashboard.py`, `unittest` + `tempfile` + `json` for tests. Test runner: `uv run pytest`.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `driftdriver/ecosystem_hub/services.py` | `detect_services()`, `_parse_launchctl_list()`, plist scanning |
| Modify | `driftdriver/ecosystem_hub/api.py` | Add `GET /api/repo/:name/services` and four POST endpoints |
| Modify | `driftdriver/ecosystem_hub/dashboard.py` | Add `loadServiceCards()`, `renderServiceCard()`, service card HTML |
| Create | `tests/test_services.py` | Full test coverage for `detect_services()` and helpers |

---

## Step 1 — Create test file with failing tests for `services.py`

- [ ] Create `tests/test_services.py`

```python
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
            # Write garbage plist
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
            # Should not raise
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
```

**Run (expect failures):**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_services.py -x 2>&1 | head -30
```

---

## Step 2 — Implement `driftdriver/ecosystem_hub/services.py`

- [ ] Create `driftdriver/ecosystem_hub/services.py`

```python
# ABOUTME: Service detection and control helpers for the ecosystem hub.
# ABOUTME: Detects workgraph, launchd, and cron services per repo. No shell=True.
from __future__ import annotations

import logging
import plistlib
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


# ---------------------------------------------------------------------------
# Low-level subprocess helpers — these are the only places subprocesses fire.
# All callers use these functions; tests patch them directly.
# ---------------------------------------------------------------------------

def _run_wg_service_status(repo_path: str) -> str:
    """Run `wg service status` and return 'running' or 'stopped'."""
    try:
        result = subprocess.run(  # noqa: S603
            ["wg", "service", "status"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        combined = (result.stdout + result.stderr).lower()
        if result.returncode == 0 and "running" in combined:
            return "running"
        return "stopped"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "stopped"


def _run_launchctl_list() -> dict[str, int | None]:
    """Run `launchctl list` and return parsed {label: pid_or_None} dict."""
    try:
        result = subprocess.run(  # noqa: S603
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return _parse_launchctl_list(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}


def _run_crontab_l(repo_path: str) -> list[str]:
    """Run `crontab -l` and return lines matching repo_path."""
    try:
        result = subprocess.run(  # noqa: S603
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [
            line for line in result.stdout.splitlines()
            if repo_path in line and not line.strip().startswith("#")
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_launchctl_list(output: str) -> dict[str, int | None]:
    """Parse `launchctl list` stdout into {label: pid_or_None}."""
    result: dict[str, int | None] = {}
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        pid_str, _exit, label = parts
        if label in ("Label",):  # skip header
            continue
        try:
            result[label] = int(pid_str)
        except ValueError:
            result[label] = None  # pid_str is "-"
    return result


# ---------------------------------------------------------------------------
# Plist scanning
# ---------------------------------------------------------------------------

def _find_launchd_plists(
    repo_name: str,
    repo_path: str,
    *,
    launch_agents_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Scan LaunchAgents for plist files belonging to repo_name/repo_path."""
    agents_dir = launch_agents_dir or _DEFAULT_LAUNCH_AGENTS
    if not agents_dir.is_dir():
        return []

    # Normalize repo name for filename heuristic: paia-shell → paia.shell
    norm_name = repo_name.replace("-", ".").lower()
    results: list[dict[str, Any]] = []

    for plist_file in agents_dir.glob("*.plist"):
        try:
            with open(plist_file, "rb") as fp:
                data = plistlib.load(fp)
        except Exception:
            log.debug("Skipping malformed plist: %s", plist_file)
            continue

        label = str(data.get("Label") or "")
        working_dir = str(data.get("WorkingDirectory") or "")

        # Strategy 1: exact WorkingDirectory match
        matched = working_dir == repo_path

        # Strategy 2: filename heuristic
        if not matched:
            stem = plist_file.stem.lower()  # e.g. "com.paia.shell"
            if norm_name and norm_name in stem:
                matched = True

        if matched:
            results.append({
                "label": label,
                "plist_path": str(plist_file),
            })

    return results


# ---------------------------------------------------------------------------
# Path validation for launchd POST endpoints
# ---------------------------------------------------------------------------

def _validate_plist_path(
    plist_path: str,
    *,
    launch_agents_dir: Path | None = None,
) -> bool:
    """Return True if plist_path is a real file inside LaunchAgents dir."""
    agents_dir = launch_agents_dir or _DEFAULT_LAUNCH_AGENTS
    try:
        resolved = Path(plist_path).resolve()
        agents_resolved = agents_dir.resolve()
        return (
            resolved.exists()
            and resolved.is_file()
            and resolved.parts[: len(agents_resolved.parts)] == agents_resolved.parts
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main detection function
# ---------------------------------------------------------------------------

def detect_services(
    repo_name: str,
    repo_path: str,
    *,
    launch_agents_dir: Path | None = None,
) -> dict[str, Any]:
    """Detect all services (workgraph, launchd, cron) for a repo.

    Returns:
        {
          "repo": repo_name,
          "workgraph": {"present": bool, "status": "running"|"stopped"|None},
          "launchd": [{"label": str, "plist_path": str, "status": str, "pid": int|None}],
          "cron": {"jobs": [str]},
        }
    """
    # --- Workgraph ---
    wg_dir = Path(repo_path) / ".workgraph"
    if wg_dir.is_dir():
        wg_status = _run_wg_service_status(repo_path)
        workgraph: dict[str, Any] = {"present": True, "status": wg_status}
    else:
        workgraph = {"present": False, "status": None}

    # --- launchd ---
    launchctl_map = _run_launchctl_list()
    raw_plists = _find_launchd_plists(
        repo_name, repo_path, launch_agents_dir=launch_agents_dir
    )
    launchd_services: list[dict[str, Any]] = []
    for plist_entry in raw_plists:
        label = plist_entry["label"]
        pid = launchctl_map.get(label)  # None means stopped or not in list
        if label in launchctl_map:
            status = "running" if pid is not None else "stopped"
        else:
            # Not in launchctl list at all — treat as stopped (not loaded)
            status = "stopped"
            pid = None
        launchd_services.append({
            "label": label,
            "plist_path": plist_entry["plist_path"],
            "status": status,
            "pid": pid,
        })

    # --- Cron ---
    cron_lines = _run_crontab_l(repo_path)

    return {
        "repo": repo_name,
        "workgraph": workgraph,
        "launchd": launchd_services,
        "cron": {"jobs": cron_lines},
    }
```

**Run tests (expect green):**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_services.py -x -v
```

**Commit:**
```bash
git add driftdriver/ecosystem_hub/services.py tests/test_services.py
git commit -m "feat(services): add detect_services() with workgraph, launchd, cron detection"
```

---

## Step 3 — Add `GET /api/repo/:name/services` to `api.py`

- [ ] Modify `driftdriver/ecosystem_hub/api.py`

Add the import at the top of `api.py` alongside the other ecosystem hub imports:

```python
from .services import detect_services as _detect_services
```

Add the `GET /api/repo/:name/services` handler inside `do_GET`, before the final `not_found` fallback:

```python
if route.startswith("/api/repo/") and route.endswith("/services"):
    repo_name = route[len("/api/repo/"):-len("/services")]
    if not repo_name:
        self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
        return
    repo_path = self._find_repo_path(repo_name)
    if not repo_path or not Path(repo_path).is_dir():
        self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
        return
    try:
        services_payload = _detect_services(repo_name, repo_path)
        self._send_json(services_payload)
    except Exception as exc:
        logging.getLogger(__name__).debug("services detect failed", exc_info=True)
        self._send_json({"error": str(exc)[:200]}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
    return
```

**Verify import:**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run python -c "from driftdriver.ecosystem_hub.api import _HubHandler; print('ok')"
```

---

## Step 4 — Add POST endpoints for workgraph stop and launchd control to `api.py`

- [ ] Modify `driftdriver/ecosystem_hub/api.py`

Add the following four endpoint handlers inside `do_POST`, before the final `not_found` return at the bottom. Place them after the existing `/api/repo/:name/start` handler:

```python
# --- Workgraph stop ---
if route.startswith("/api/repo/") and route.endswith("/service/workgraph/stop"):
    repo_name = route[len("/api/repo/"):-len("/service/workgraph/stop")]
    if not repo_name:
        self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
        return
    repo_path = self._find_repo_path(repo_name)
    if not repo_path or not Path(repo_path).is_dir():
        self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
        return
    if not (Path(repo_path) / ".workgraph").is_dir():
        self._send_json({"error": "no_workgraph", "repo": repo_name}, status=HTTPStatus.BAD_REQUEST)
        return
    import subprocess as _sp
    try:
        result = _sp.run(  # noqa: S603
            ["wg", "service", "stop"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self._send_json({
            "repo": repo_name,
            "action": "workgraph/stop",
            "returncode": result.returncode,
            "stdout": result.stdout[:500],
            "stderr": result.stderr[:500],
        })
    except Exception as exc:
        self._send_json({"error": str(exc), "repo": repo_name}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
    return

# --- launchd start ---
if route.startswith("/api/repo/") and route.endswith("/service/launchd/start"):
    repo_name = route[len("/api/repo/"):-len("/service/launchd/start")]
    body = self._read_body()
    try:
        body_data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
        return
    plist_path = str(body_data.get("plist_path") or "")
    from .services import _validate_plist_path
    if not plist_path or not _validate_plist_path(plist_path):
        self._send_json({"error": "invalid_plist_path"}, status=HTTPStatus.BAD_REQUEST)
        return
    import subprocess as _sp
    try:
        result = _sp.run(  # noqa: S603
            ["launchctl", "load", plist_path],
            capture_output=True, text=True, timeout=10,
        )
        self._send_json({
            "repo": repo_name, "action": "launchd/start",
            "plist_path": plist_path,
            "returncode": result.returncode,
            "stdout": result.stdout[:500],
            "stderr": result.stderr[:500],
        })
    except Exception as exc:
        self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
    return

# --- launchd stop ---
if route.startswith("/api/repo/") and route.endswith("/service/launchd/stop"):
    repo_name = route[len("/api/repo/"):-len("/service/launchd/stop")]
    body = self._read_body()
    try:
        body_data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
        return
    plist_path = str(body_data.get("plist_path") or "")
    from .services import _validate_plist_path
    if not plist_path or not _validate_plist_path(plist_path):
        self._send_json({"error": "invalid_plist_path"}, status=HTTPStatus.BAD_REQUEST)
        return
    import subprocess as _sp
    try:
        result = _sp.run(  # noqa: S603
            ["launchctl", "unload", plist_path],
            capture_output=True, text=True, timeout=10,
        )
        self._send_json({
            "repo": repo_name, "action": "launchd/stop",
            "plist_path": plist_path,
            "returncode": result.returncode,
            "stdout": result.stdout[:500],
            "stderr": result.stderr[:500],
        })
    except Exception as exc:
        self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
    return

# --- launchd restart ---
if route.startswith("/api/repo/") and route.endswith("/service/launchd/restart"):
    repo_name = route[len("/api/repo/"):-len("/service/launchd/restart")]
    body = self._read_body()
    try:
        body_data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
        return
    plist_path = str(body_data.get("plist_path") or "")
    from .services import _validate_plist_path
    if not plist_path or not _validate_plist_path(plist_path):
        self._send_json({"error": "invalid_plist_path"}, status=HTTPStatus.BAD_REQUEST)
        return
    import subprocess as _sp
    try:
        unload = _sp.run(  # noqa: S603
            ["launchctl", "unload", plist_path],
            capture_output=True, text=True, timeout=10,
        )
        if unload.returncode != 0:
            self._send_json({
                "repo": repo_name, "action": "launchd/restart",
                "plist_path": plist_path,
                "unload": {"returncode": unload.returncode,
                           "stdout": unload.stdout[:500],
                           "stderr": unload.stderr[:500]},
                "load": None,
                "error": "unload_failed",
            }, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        load = _sp.run(  # noqa: S603
            ["launchctl", "load", plist_path],
            capture_output=True, text=True, timeout=10,
        )
        self._send_json({
            "repo": repo_name, "action": "launchd/restart",
            "plist_path": plist_path,
            "unload": {"returncode": unload.returncode,
                       "stdout": unload.stdout[:500],
                       "stderr": unload.stderr[:500]},
            "load": {"returncode": load.returncode,
                     "stdout": load.stdout[:500],
                     "stderr": load.stderr[:500]},
        })
    except Exception as exc:
        self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
    return
```

Also add the workgraph/start alias (mirrors the existing `/start` handler):

```python
if route.startswith("/api/repo/") and route.endswith("/service/workgraph/start"):
    repo_name = route[len("/api/repo/"):-len("/service/workgraph/start")]
    # Same logic as /api/repo/:name/start — delegate to shared implementation
    # (copy the existing start block's body here, or extract to a helper)
    ...
```

**Commit:**
```bash
git add driftdriver/ecosystem_hub/api.py
git commit -m "feat(services): add GET /services and POST service control endpoints to api.py"
```

---

## Step 5 — Add service cards JS to `dashboard.py`

- [ ] Modify `driftdriver/ecosystem_hub/dashboard.py`

**New JS to add inside the `<script>` block:**

```javascript
async function loadServiceCards(repoName) {
  const container = document.getElementById('section-services');
  if (!container) return;
  container.innerHTML = '<p class="muted">Checking services…</p>';

  try {
    const resp = await fetch(`/api/repo/${encodeURIComponent(repoName)}/services`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderServiceCards(repoName, data, container);
  } catch (err) {
    container.innerHTML = `<p class="muted">Could not load services: ${escHtml(String(err))}</p>`;
  }
}

function renderServiceCards(repoName, data, container) {
  const services = data.services || data; // handle both shapes
  const wg = data.workgraph || {};
  const launchd = data.launchd || [];
  const cron = data.cron || {};

  let html = '';
  let anyCard = false;

  // --- Workgraph card ---
  if (wg.present) {
    anyCard = true;
    const dot = wg.status === 'running'
      ? '<span style="color:#22c55e">●</span>'
      : '<span style="color:#6b7280">○</span>';
    const statusLabel = wg.status === 'running' ? 'Running' : 'Stopped';
    const btn = wg.status === 'running'
      ? `<button class="svc-btn" onclick="serviceAction('${escHtml(repoName)}','workgraph','stop',null,this)">Stop</button>`
      : `<button class="svc-btn" onclick="serviceAction('${escHtml(repoName)}','workgraph','start',null,this)">Start</button>`;
    html += `<div class="svc-card" id="svc-workgraph-${escHtml(repoName)}">
      <div class="svc-card-header">
        <span>⚙ Workgraph Service</span>
        <span class="svc-status">${dot} ${statusLabel}</span>
      </div>
      <div class="svc-card-actions">${btn}</div>
      <div class="svc-error" style="display:none"></div>
    </div>`;
  }

  // --- launchd cards ---
  launchd.forEach((svc, i) => {
    anyCard = true;
    const dot = svc.status === 'running'
      ? '<span style="color:#22c55e">●</span>'
      : '<span style="color:#6b7280">○</span>';
    const statusLabel = svc.status === 'running' ? 'Running' : 'Stopped';
    const plistJson = JSON.stringify(svc.plist_path);
    let btns = '';
    if (svc.status === 'running') {
      btns = `<button class="svc-btn" onclick="serviceAction('${escHtml(repoName)}','launchd','stop',${plistJson},this)">Stop</button>
              <button class="svc-btn" onclick="serviceAction('${escHtml(repoName)}','launchd','restart',${plistJson},this)">Restart</button>`;
    } else {
      btns = `<button class="svc-btn" onclick="serviceAction('${escHtml(repoName)}','launchd','start',${plistJson},this)">Start</button>`;
    }
    html += `<div class="svc-card" id="svc-launchd-${escHtml(repoName)}-${i}">
      <div class="svc-card-header">
        <span>■ launchd<br><code class="muted small">${escHtml(svc.label)}</code></span>
        <span class="svc-status">${dot} ${statusLabel}</span>
      </div>
      <div class="svc-card-actions">${btns}</div>
      <div class="svc-error" style="display:none"></div>
    </div>`;
  });

  // --- Cron card ---
  const cronJobs = (cron.jobs || []);
  if (cronJobs.length > 0) {
    anyCard = true;
    const jobLines = cronJobs.map(j => `<code>${escHtml(j)}</code>`).join('<br>');
    html += `<div class="svc-card">
      <div class="svc-card-header">
        <span>⏱ Cron Jobs</span>
        <span class="muted small">(read-only)</span>
      </div>
      <div class="svc-cron-jobs">${jobLines}</div>
    </div>`;
  }

  container.innerHTML = anyCard ? html : '<p class="muted">No services detected.</p>';
}

async function serviceAction(repoName, serviceType, action, plistPath, btn) {
  const card = btn.closest('.svc-card');
  const errDiv = card ? card.querySelector('.svc-error') : null;
  btn.disabled = true;
  const origText = btn.textContent;
  btn.textContent = 'Working…';
  if (errDiv) { errDiv.style.display = 'none'; errDiv.textContent = ''; }

  try {
    const url = `/api/repo/${encodeURIComponent(repoName)}/service/${serviceType}/${action}`;
    const body = plistPath ? JSON.stringify({ plist_path: plistPath }) : '{}';
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    const data = await resp.json();
    if (!resp.ok || data.error) {
      const msg = data.stderr || data.message || data.error || `HTTP ${resp.status}`;
      if (errDiv) { errDiv.textContent = 'Error: ' + msg.slice(0, 100); errDiv.style.display = 'block'; }
    }
  } catch (err) {
    if (errDiv) { errDiv.textContent = 'Error: ' + String(err); errDiv.style.display = 'block'; }
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
    // Re-fetch to get updated status
    loadServiceCards(repoName);
  }
}
```

**New HTML section** — add inside the repo detail view, in the Services section (Section 3 of the detail page), replacing the existing 3-column status row from the detail page plan:

```html
<div class="detail-section">
  <h3>Services</h3>
  <div id="section-services">
    <p class="muted">Checking services…</p>
  </div>
</div>
```

**Call in `openRepoDetail(name)`** (after the existing data fetch):

```javascript
loadServiceCards(name);
```

**Commit:**
```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat(services): add service cards JS and Services section to detail page"
```

---

## Step 6 — Run full test suite

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_services.py -v
uv run pytest tests/test_ecosystem_hub.py -x -v
```

---

## Completion Checklist

- [ ] `tests/test_services.py` — all tests green
- [ ] `driftdriver/ecosystem_hub/services.py` — created, passes tests
- [ ] `driftdriver/ecosystem_hub/api.py` — 5 new routes added (GET /services, POST workgraph/start alias, stop, launchd start/stop/restart)
- [ ] `driftdriver/ecosystem_hub/dashboard.py` — `loadServiceCards()`, `renderServiceCards()`, `serviceAction()`, service card HTML
- [ ] `_validate_plist_path()` prevents loading plists outside `~/Library/LaunchAgents`
- [ ] All subprocess calls use explicit argument lists, `shell=False` (default)
- [ ] `uv run pytest tests/test_services.py -v` — all green
- [ ] No new external dependencies
