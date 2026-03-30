# ABOUTME: Tests for task wiring — stable IDs, finding ledger, verify commands.
# ABOUTME: Covers northstardrift, attractor_loop, and factory_brain/router wiring.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from driftdriver.drift_task_guard import record_finding_ledger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wg_dir(tmp: Path) -> Path:
    wg_dir = tmp / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    return wg_dir


def _make_fake_run_wg(show_rc: int = 1, add_rc: int = 0):
    """Return a _run_wg mock that returns not-found for show, success for add."""
    def _run(cmd, *, cwd=None, timeout=40.0):
        joined = " ".join(str(c) for c in cmd)
        if "show" in joined:
            return (show_rc, "", "not found")
        if "list" in joined:
            return (0, "[]", "")
        if "add" in joined:
            return (add_rc, "", "")
        return (0, "", "")
    return _run


# ---------------------------------------------------------------------------
# Northstar task wiring tests
# ---------------------------------------------------------------------------

class TestNorthstarTaskWiring(unittest.TestCase):
    """Tests for northstardrift.emit_northstar_review_tasks task wiring."""

    def _make_snapshot_and_report(self, tmp: Path, repo_name: str, category: str) -> tuple:
        repo_path = tmp / repo_name
        (repo_path / ".workgraph").mkdir(parents=True, exist_ok=True)
        snapshot = {
            "repos": [
                {"name": repo_name, "path": str(repo_path), "git_dirty": False}
            ]
        }
        report = {
            "recommended_reviews": [
                {
                    "repo": repo_name,
                    "fingerprint": "abc123deadbeef",
                    "category": category,
                    "severity": "high",
                    "title": "Coordination low",
                    "evidence": "score dropped",
                    "recommendation": "fix coordination",
                    "score": 65.0,
                    "human_approval_required": False,
                    "model_prompt": "improve coordination",
                    "codex_prompt": "",
                }
            ]
        }
        return snapshot, report

    def test_task_wiring_stable_id_format(self) -> None:
        """Stable task ID uses drift:{repo}:northstar:{category} format, not fingerprint."""
        from driftdriver.northstardrift import emit_northstar_review_tasks

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "myrepo"
            snapshot, report = self._make_snapshot_and_report(tmp, repo_name, "coordination-low")

            captured_ids: list[str] = []

            def fake_guard(*, wg_dir, task_id, **kwargs):
                captured_ids.append(task_id)
                return "created"

            with patch("driftdriver.northstardrift.guarded_add_drift_task", side_effect=fake_guard), \
                 patch("driftdriver.northstardrift.record_finding_ledger"):
                emit_northstar_review_tasks(snapshot=snapshot, report=report)

        self.assertEqual(len(captured_ids), 1)
        task_id = captured_ids[0]
        self.assertTrue(
            task_id.startswith("drift:myrepo:northstar:"),
            f"Expected stable drift:repo:northstar:category format, got {task_id!r}",
        )
        # Must NOT contain a raw fingerprint prefix
        self.assertNotIn("northstardrift-", task_id)

    def test_task_wiring_stable_id_no_fingerprint(self) -> None:
        """Task ID must not be derived from the fingerprint hash."""
        from driftdriver.northstardrift import emit_northstar_review_tasks

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "testrepo"
            snapshot, report = self._make_snapshot_and_report(tmp, repo_name, "repo-attention")

            captured_ids: list[str] = []

            def fake_guard(*, wg_dir, task_id, **kwargs):
                captured_ids.append(task_id)
                return "existing"

            with patch("driftdriver.northstardrift.guarded_add_drift_task", side_effect=fake_guard), \
                 patch("driftdriver.northstardrift.record_finding_ledger"):
                emit_northstar_review_tasks(snapshot=snapshot, report=report)

        self.assertEqual(len(captured_ids), 1)
        # ID must be deterministic (call again, same ID)
        captured_ids2: list[str] = []

        def fake_guard2(*, wg_dir, task_id, **kwargs):
            captured_ids2.append(task_id)
            return "existing"

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            snapshot, report = self._make_snapshot_and_report(tmp, repo_name, "repo-attention")
            with patch("driftdriver.northstardrift.guarded_add_drift_task", side_effect=fake_guard2), \
                 patch("driftdriver.northstardrift.record_finding_ledger"):
                emit_northstar_review_tasks(snapshot=snapshot, report=report)

        self.assertEqual(captured_ids[0], captured_ids2[0])

    def test_task_wiring_verify_command_in_description(self) -> None:
        """Task description must include a Verify: command."""
        from driftdriver.northstardrift import emit_northstar_review_tasks

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "verifyrepo"
            snapshot, report = self._make_snapshot_and_report(tmp, repo_name, "quality")

            captured_descs: list[str] = []

            def fake_guard(*, wg_dir, task_id, description, **kwargs):
                captured_descs.append(description)
                return "created"

            with patch("driftdriver.northstardrift.guarded_add_drift_task", side_effect=fake_guard), \
                 patch("driftdriver.northstardrift.record_finding_ledger"):
                emit_northstar_review_tasks(snapshot=snapshot, report=report)

        self.assertEqual(len(captured_descs), 1)
        self.assertIn("Verify:", captured_descs[0])

    def test_task_wiring_finding_ledger_written(self) -> None:
        """Finding ledger must be written alongside guarded_add call."""
        from driftdriver.northstardrift import emit_northstar_review_tasks

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "ledgerrepo"
            snapshot, report = self._make_snapshot_and_report(tmp, repo_name, "continuity")
            wg_dir = tmp / repo_name / ".workgraph"

            ledger_calls: list[dict] = []

            def fake_guard(*, wg_dir, task_id, **kwargs):
                return "created"

            def fake_ledger(wg, *, repo, lane, finding_type, task_id, result, **kwargs):
                ledger_calls.append({
                    "repo": repo, "lane": lane,
                    "finding_type": finding_type, "task_id": task_id, "result": result,
                })

            with patch("driftdriver.northstardrift.guarded_add_drift_task", side_effect=fake_guard), \
                 patch("driftdriver.northstardrift.record_finding_ledger", side_effect=fake_ledger):
                emit_northstar_review_tasks(snapshot=snapshot, report=report)

        self.assertEqual(len(ledger_calls), 1)
        entry = ledger_calls[0]
        self.assertEqual(entry["repo"], repo_name)
        self.assertEqual(entry["lane"], "northstardrift")
        self.assertEqual(entry["result"], "created")


# ---------------------------------------------------------------------------
# Attractor task wiring tests
# ---------------------------------------------------------------------------

class TestAttractorTaskWiring(unittest.TestCase):
    """Tests for attractor_loop.run_attractor_loop task wiring."""

    def _make_run_with_escalation(self, tmp: Path, repo_name: str) -> tuple:
        from driftdriver.attractor_planner import EscalationRecord
        from driftdriver.attractor_loop import AttractorRun, PassResult, ConvergencePlan

        repo_path = tmp / repo_name
        (repo_path / ".workgraph").mkdir(parents=True, exist_ok=True)

        esc = EscalationRecord(
            repo=repo_name,
            attractor="onboarded",
            reason="no_matching_bundle",
            remaining_findings=[{"message": "missing tests", "severity": "warning"}],
            suggested_action="create bundle for missing tests",
        )
        plan = ConvergencePlan(
            attractor="onboarded",
            repo=repo_name,
            pass_number=0,
            escalations=[esc],
        )
        pass_result = PassResult(
            pass_number=0,
            findings_before=1,
            findings_after=1,
            duration_seconds=0.1,
            plan=plan,
        )
        run = AttractorRun(repo=repo_name, attractor="onboarded", status="plateau")
        run.passes.append(pass_result)
        run.escalations.append(esc)
        run.remaining_findings.extend(esc.remaining_findings)
        return run, repo_path

    def test_task_wiring_guarded_add_called_for_escalations(self) -> None:
        """guarded_add_drift_task must be called for each unique escalation reason."""
        from driftdriver.attractor_loop import run_attractor_loop
        from driftdriver.attractor_planner import EscalationRecord
        from driftdriver.attractors import Attractor

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "escalation-repo"
            repo_path = tmp / repo_name
            (repo_path / ".workgraph").mkdir(parents=True, exist_ok=True)

            attractor = Attractor(id="onboarded", description="onboarded")

            esc = EscalationRecord(
                repo=repo_name,
                attractor="onboarded",
                reason="no_matching_bundle",
                remaining_findings=[{"message": "unmatched finding", "severity": "warning"}],
                suggested_action="add bundle",
            )
            from driftdriver.attractor_planner import ConvergencePlan
            plan = ConvergencePlan(
                attractor="onboarded",
                repo=repo_name,
                pass_number=0,
                escalations=[esc],
            )

            from driftdriver.attractor_loop import PassResult
            pass_result = PassResult(
                pass_number=0,
                findings_before=1,
                findings_after=1,
                duration_seconds=0.0,
                plan=plan,
            )

            captured_ids: list[str] = []

            def fake_guard(*, wg_dir, task_id, **kwargs):
                captured_ids.append(task_id)
                return "created"

            def fake_diagnose(path):
                return {}

            def fake_execute(plan, path):
                return {}

            with patch("driftdriver.attractor_loop.guarded_add_drift_task", side_effect=fake_guard), \
                 patch("driftdriver.attractor_loop.record_finding_ledger"), \
                 patch("driftdriver.attractor_loop.run_attractor_pass", return_value=pass_result), \
                 patch("driftdriver.attractor_loop.check_convergence", return_value="plateau"):
                run_attractor_loop(
                    repo=repo_name,
                    repo_path=repo_path,
                    attractor=attractor,
                    bundles=[],
                    diagnose_fn=fake_diagnose,
                    execute_fn=fake_execute,
                )

        self.assertGreater(len(captured_ids), 0)
        task_id = captured_ids[0]
        self.assertTrue(
            task_id.startswith(f"drift:{repo_name}:attractor:"),
            f"Expected drift:{repo_name}:attractor:<reason> format, got {task_id!r}",
        )

    def test_task_wiring_stable_attractor_id(self) -> None:
        """Attractor task ID must be stable (based on reason, not message content)."""
        from driftdriver.attractor_loop import run_attractor_loop
        from driftdriver.attractor_planner import EscalationRecord, ConvergencePlan
        from driftdriver.attractor_loop import PassResult
        from driftdriver.attractors import Attractor

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "stable-id-repo"
            repo_path = tmp / repo_name
            (repo_path / ".workgraph").mkdir(parents=True, exist_ok=True)

            attractor = Attractor(id="onboarded", description="onboarded")
            esc = EscalationRecord(
                repo=repo_name, attractor="onboarded", reason="no_matching_bundle",
                remaining_findings=[{"message": "changes each cycle!", "severity": "warning"}],
            )
            plan = ConvergencePlan(attractor="onboarded", repo=repo_name, pass_number=0, escalations=[esc])
            pass_result = PassResult(pass_number=0, findings_before=1, findings_after=1, duration_seconds=0.0, plan=plan)

            ids_run1: list[str] = []
            ids_run2: list[str] = []

            for captured in [ids_run1, ids_run2]:
                # Change the finding message between runs — ID must be the same
                esc.remaining_findings = [{"message": f"different message {len(captured)}", "severity": "warning"}]

                def fake_guard(*, wg_dir, task_id, _cap=captured, **kwargs):
                    _cap.append(task_id)
                    return "created"

                with patch("driftdriver.attractor_loop.guarded_add_drift_task", side_effect=fake_guard), \
                     patch("driftdriver.attractor_loop.record_finding_ledger"), \
                     patch("driftdriver.attractor_loop.run_attractor_pass", return_value=pass_result), \
                     patch("driftdriver.attractor_loop.check_convergence", return_value="plateau"):
                    run_attractor_loop(
                        repo=repo_name, repo_path=repo_path, attractor=attractor, bundles=[],
                        diagnose_fn=lambda p: {}, execute_fn=lambda pl, p: {},
                    )

        self.assertGreater(len(ids_run1), 0)
        self.assertGreater(len(ids_run2), 0)
        self.assertEqual(ids_run1[0], ids_run2[0], "Task ID must be stable across runs with different finding messages")

    def test_task_wiring_finding_ledger_written_for_escalations(self) -> None:
        """Finding ledger entry written for each escalation."""
        from driftdriver.attractor_loop import run_attractor_loop
        from driftdriver.attractor_planner import EscalationRecord, ConvergencePlan
        from driftdriver.attractor_loop import PassResult
        from driftdriver.attractors import Attractor

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "ledger-attractor-repo"
            repo_path = tmp / repo_name
            (repo_path / ".workgraph").mkdir(parents=True, exist_ok=True)

            attractor = Attractor(id="onboarded", description="onboarded")
            esc = EscalationRecord(
                repo=repo_name, attractor="onboarded", reason="no_matching_bundle",
                remaining_findings=[{"message": "needs bundle", "severity": "warning"}],
            )
            plan = ConvergencePlan(attractor="onboarded", repo=repo_name, pass_number=0, escalations=[esc])
            pass_result = PassResult(pass_number=0, findings_before=1, findings_after=1, duration_seconds=0.0, plan=plan)

            ledger_calls: list[dict] = []

            def fake_ledger(wg, *, repo, lane, finding_type, task_id, result, **kwargs):
                ledger_calls.append({"repo": repo, "lane": lane, "task_id": task_id, "result": result})

            with patch("driftdriver.attractor_loop.guarded_add_drift_task", return_value="created"), \
                 patch("driftdriver.attractor_loop.record_finding_ledger", side_effect=fake_ledger), \
                 patch("driftdriver.attractor_loop.run_attractor_pass", return_value=pass_result), \
                 patch("driftdriver.attractor_loop.check_convergence", return_value="plateau"):
                run_attractor_loop(
                    repo=repo_name, repo_path=repo_path, attractor=attractor, bundles=[],
                    diagnose_fn=lambda p: {}, execute_fn=lambda pl, p: {},
                )

        self.assertGreater(len(ledger_calls), 0)
        entry = ledger_calls[0]
        self.assertEqual(entry["lane"], "attractor")
        self.assertEqual(entry["result"], "created")

    def test_task_wiring_no_call_when_no_wg_dir(self) -> None:
        """guarded_add must not be called if .workgraph directory is absent."""
        from driftdriver.attractor_loop import run_attractor_loop
        from driftdriver.attractor_planner import EscalationRecord, ConvergencePlan
        from driftdriver.attractor_loop import PassResult
        from driftdriver.attractors import Attractor

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "no-wg-repo"
            repo_path = tmp / repo_name
            repo_path.mkdir(parents=True, exist_ok=True)
            # Intentionally NO .workgraph directory

            attractor = Attractor(id="onboarded", description="onboarded")
            esc = EscalationRecord(
                repo=repo_name, attractor="onboarded", reason="no_matching_bundle",
                remaining_findings=[{"message": "unmatched", "severity": "warning"}],
            )
            plan = ConvergencePlan(attractor="onboarded", repo=repo_name, pass_number=0, escalations=[esc])
            pass_result = PassResult(pass_number=0, findings_before=1, findings_after=1, duration_seconds=0.0, plan=plan)

            called: list[bool] = []

            with patch("driftdriver.attractor_loop.guarded_add_drift_task", side_effect=lambda **k: called.append(True) or "created"), \
                 patch("driftdriver.attractor_loop.record_finding_ledger"), \
                 patch("driftdriver.attractor_loop.run_attractor_pass", return_value=pass_result), \
                 patch("driftdriver.attractor_loop.check_convergence", return_value="plateau"):
                run_attractor_loop(
                    repo=repo_name, repo_path=repo_path, attractor=attractor, bundles=[],
                    diagnose_fn=lambda p: {}, execute_fn=lambda pl, p: {},
                )

        self.assertEqual(called, [], "guarded_add must not be called when .workgraph is absent")


# ---------------------------------------------------------------------------
# Factory brain task wiring tests
# ---------------------------------------------------------------------------

class TestFactoryBrainTaskWiring(unittest.TestCase):
    """Tests for factory_brain/router.py heartbeat.stale task wiring."""

    def _make_router_state(self):
        from driftdriver.factory_brain.router import BrainState
        return BrainState()

    def test_task_wiring_heartbeat_stale_calls_guarded_add(self) -> None:
        """guarded_add_drift_task must be called for each stale heartbeat repo."""
        from driftdriver.factory_brain.router import run_brain_tick, BrainState
        from driftdriver.factory_brain.directives import BrainResponse

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "stale-repo"
            repo_path = tmp / repo_name
            (repo_path / ".workgraph").mkdir(parents=True, exist_ok=True)

            state = BrainState()
            captured_ids: list[str] = []

            def fake_guard(*, wg_dir, task_id, **kwargs):
                captured_ids.append(task_id)
                return "created"

            fake_response = BrainResponse(
                directives=[], reasoning="ok", escalate=False, telegram=None,
            )

            with patch("driftdriver.factory_brain.router.aggregate_events", return_value=[]), \
                 patch("driftdriver.factory_brain.router.repos_with_active_sessions", return_value=set()), \
                 patch("driftdriver.factory_brain.router.repos_needing_human", return_value=set()), \
                 patch("driftdriver.factory_brain.router.check_heartbeats", return_value=[repo_path]), \
                 patch("driftdriver.factory_brain.router.invoke_brain", return_value=fake_response), \
                 patch("driftdriver.factory_brain.router.execute_directives", return_value={}), \
                 patch("driftdriver.factory_brain.router.guarded_add_drift_task", side_effect=fake_guard), \
                 patch("driftdriver.factory_brain.router.record_finding_ledger"), \
                 patch("driftdriver.factory_brain.router.should_sweep", return_value=False):
                run_brain_tick(
                    state=state,
                    roster_repos=[repo_path],
                )

        self.assertGreater(len(captured_ids), 0)
        task_id = captured_ids[0]
        self.assertEqual(
            task_id,
            f"drift:{repo_name}:factory-brain:heartbeat-stale",
            f"Heartbeat stale task ID must be stable, got {task_id!r}",
        )

    def test_task_wiring_heartbeat_stable_id_across_ticks(self) -> None:
        """Heartbeat stale task ID must be the same across consecutive ticks."""
        from driftdriver.factory_brain.router import run_brain_tick, BrainState
        from driftdriver.factory_brain.directives import BrainResponse

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "stable-hb-repo"
            repo_path = tmp / repo_name
            (repo_path / ".workgraph").mkdir(parents=True, exist_ok=True)

            fake_response = BrainResponse(
                directives=[], reasoning="ok", escalate=False, telegram=None,
            )

            ids_all: list[str] = []

            def fake_guard(*, wg_dir, task_id, **kwargs):
                ids_all.append(task_id)
                return "existing"

            for _tick in range(2):
                state = BrainState()
                with patch("driftdriver.factory_brain.router.aggregate_events", return_value=[]), \
                     patch("driftdriver.factory_brain.router.repos_with_active_sessions", return_value=set()), \
                     patch("driftdriver.factory_brain.router.repos_needing_human", return_value=set()), \
                     patch("driftdriver.factory_brain.router.check_heartbeats", return_value=[repo_path]), \
                     patch("driftdriver.factory_brain.router.invoke_brain", return_value=fake_response), \
                     patch("driftdriver.factory_brain.router.execute_directives", return_value={}), \
                     patch("driftdriver.factory_brain.router.guarded_add_drift_task", side_effect=fake_guard), \
                     patch("driftdriver.factory_brain.router.record_finding_ledger"), \
                     patch("driftdriver.factory_brain.router.should_sweep", return_value=False):
                    run_brain_tick(state=state, roster_repos=[repo_path])

        self.assertEqual(len(ids_all), 2)
        self.assertEqual(ids_all[0], ids_all[1], "Heartbeat stale task ID must be stable across ticks")

    def test_task_wiring_no_call_for_session_repos(self) -> None:
        """guarded_add must NOT be called for repos with active interactive sessions."""
        from driftdriver.factory_brain.router import run_brain_tick, BrainState
        from driftdriver.factory_brain.directives import BrainResponse

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "active-session-repo"
            repo_path = tmp / repo_name
            (repo_path / ".workgraph").mkdir(parents=True, exist_ok=True)

            fake_response = BrainResponse(directives=[], reasoning="ok", escalate=False, telegram=None)
            called: list[bool] = []

            with patch("driftdriver.factory_brain.router.aggregate_events", return_value=[]), \
                 patch("driftdriver.factory_brain.router.repos_with_active_sessions", return_value={repo_name}), \
                 patch("driftdriver.factory_brain.router.repos_needing_human", return_value=set()), \
                 patch("driftdriver.factory_brain.router.check_heartbeats", return_value=[repo_path]), \
                 patch("driftdriver.factory_brain.router.invoke_brain", return_value=fake_response), \
                 patch("driftdriver.factory_brain.router.execute_directives", return_value={}), \
                 patch("driftdriver.factory_brain.router.guarded_add_drift_task", side_effect=lambda **k: called.append(True) or "created"), \
                 patch("driftdriver.factory_brain.router.record_finding_ledger"), \
                 patch("driftdriver.factory_brain.router.should_sweep", return_value=False):
                state = BrainState()
                run_brain_tick(state=state, roster_repos=[repo_path])

        self.assertEqual(called, [], "guarded_add must not be called for repos with active sessions")

    def test_task_wiring_verify_in_heartbeat_description(self) -> None:
        """Heartbeat stale task description must include a Verify: command."""
        from driftdriver.factory_brain.router import run_brain_tick, BrainState
        from driftdriver.factory_brain.directives import BrainResponse

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            repo_name = "desc-check-repo"
            repo_path = tmp / repo_name
            (repo_path / ".workgraph").mkdir(parents=True, exist_ok=True)

            fake_response = BrainResponse(directives=[], reasoning="ok", escalate=False, telegram=None)
            captured_descs: list[str] = []

            def fake_guard(*, wg_dir, task_id, description, **kwargs):
                captured_descs.append(description)
                return "created"

            with patch("driftdriver.factory_brain.router.aggregate_events", return_value=[]), \
                 patch("driftdriver.factory_brain.router.repos_with_active_sessions", return_value=set()), \
                 patch("driftdriver.factory_brain.router.repos_needing_human", return_value=set()), \
                 patch("driftdriver.factory_brain.router.check_heartbeats", return_value=[repo_path]), \
                 patch("driftdriver.factory_brain.router.invoke_brain", return_value=fake_response), \
                 patch("driftdriver.factory_brain.router.execute_directives", return_value={}), \
                 patch("driftdriver.factory_brain.router.guarded_add_drift_task", side_effect=fake_guard), \
                 patch("driftdriver.factory_brain.router.record_finding_ledger"), \
                 patch("driftdriver.factory_brain.router.should_sweep", return_value=False):
                state = BrainState()
                run_brain_tick(state=state, roster_repos=[repo_path])

        self.assertGreater(len(captured_descs), 0)
        self.assertIn("Verify:", captured_descs[0])


# ---------------------------------------------------------------------------
# Finding ledger / guarded_add integration tests
# ---------------------------------------------------------------------------

class TestFindingLedgerGuardedAdd(unittest.TestCase):
    """Tests for record_finding_ledger — ledger persistence and format."""

    def test_guarded_add_finding_ledger_writes_jsonl(self) -> None:
        """record_finding_ledger writes a valid JSONL entry to finding-ledger.jsonl."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            wg_dir = _make_wg_dir(tmp)

            record_finding_ledger(
                wg_dir,
                repo="my-repo",
                lane="northstardrift",
                finding_type="coordination-low",
                task_id="drift:my-repo:northstar:coordination-low",
                result="created",
                severity="high",
                message="Coordination score dropped below target",
            )

            ledger = wg_dir / "finding-ledger.jsonl"
            self.assertTrue(ledger.exists(), "finding-ledger.jsonl must be created")
            lines = [l for l in ledger.read_text().splitlines() if l.strip()]
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["repo"], "my-repo")
            self.assertEqual(entry["lane"], "northstardrift")
            self.assertEqual(entry["finding_type"], "coordination-low")
            self.assertEqual(entry["task_id"], "drift:my-repo:northstar:coordination-low")
            self.assertEqual(entry["result"], "created")
            self.assertIn("timestamp", entry)

    def test_guarded_add_finding_ledger_appends(self) -> None:
        """Multiple record_finding_ledger calls append to the same file."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            wg_dir = _make_wg_dir(tmp)

            for i in range(3):
                record_finding_ledger(
                    wg_dir,
                    repo=f"repo-{i}",
                    lane="attractor",
                    finding_type="no-matching-bundle",
                    task_id=f"drift:repo-{i}:attractor:no-matching-bundle",
                    result="created",
                )

            ledger = wg_dir / "finding-ledger.jsonl"
            lines = [l for l in ledger.read_text().splitlines() if l.strip()]
            self.assertEqual(len(lines), 3)

    def test_guarded_add_finding_ledger_truncates_long_message(self) -> None:
        """Long messages are truncated to 200 chars in the ledger."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            wg_dir = _make_wg_dir(tmp)
            long_msg = "x" * 500

            record_finding_ledger(
                wg_dir,
                repo="r",
                lane="northstardrift",
                finding_type="t",
                task_id="drift:r:northstar:t",
                result="existing",
                message=long_msg,
            )

            ledger = wg_dir / "finding-ledger.jsonl"
            entry = json.loads(ledger.read_text().strip())
            self.assertLessEqual(len(entry["message"]), 200)

    def test_guarded_add_finding_ledger_skips_severity_when_empty(self) -> None:
        """Severity and message fields are omitted when empty."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            wg_dir = _make_wg_dir(tmp)

            record_finding_ledger(
                wg_dir,
                repo="r",
                lane="factory-brain",
                finding_type="heartbeat-stale",
                task_id="drift:r:factory-brain:heartbeat-stale",
                result="capped",
            )

            ledger = wg_dir / "finding-ledger.jsonl"
            entry = json.loads(ledger.read_text().strip())
            self.assertNotIn("severity", entry)
            self.assertNotIn("message", entry)

    def test_guarded_add_finding_ledger_survives_unwritable_dir(self) -> None:
        """record_finding_ledger must not raise even if the write fails."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            wg_dir = tmp / "nonexistent" / ".workgraph"
            # No mkdir — parent doesn't exist, but we still call mkdir inside
            # so this should succeed. Test the no-raise contract explicitly.
            try:
                record_finding_ledger(
                    wg_dir,
                    repo="r",
                    lane="test",
                    finding_type="t",
                    task_id="drift:r:test:t",
                    result="error",
                )
            except Exception as exc:
                self.fail(f"record_finding_ledger must not raise, got {exc}")


if __name__ == "__main__":
    unittest.main()
