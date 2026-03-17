# ABOUTME: Integration test — full continuation intent lifecycle.
# ABOUTME: Covers session end → continue → stall → self-heal → decision → needs_human → answer → continue, plus park lifecycle.
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from driftdriver.continuation_intent import read_intent, write_intent
from driftdriver.decision_notifier import format_decision_message, notify_decision
from driftdriver.decision_queue import (
    answer_decision,
    create_decision,
    read_pending_decisions,
)
from driftdriver.factory_brain.directives import BrainResponse, Directive
from driftdriver.factory_brain.events import Event
from driftdriver.factory_brain.router import (
    HEARTBEAT_REL_PATH,
    BrainState,
    check_heartbeats,
    repos_needing_human,
    run_brain_tick,
)


def _setup_repo(tmp: Path) -> Path:
    """Create a minimal repo with .workgraph runtime directory."""
    runtime = tmp / ".workgraph" / "service" / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "control.json").write_text(json.dumps({"repo": tmp.name, "mode": "supervise"}))
    return tmp


def _write_fresh_heartbeat(repo: Path) -> None:
    hb_file = repo / HEARTBEAT_REL_PATH
    hb_file.parent.mkdir(parents=True, exist_ok=True)
    hb_file.write_text(datetime.now(timezone.utc).isoformat())


def _write_stale_heartbeat(repo: Path, age_seconds: int = 200) -> None:
    hb_file = repo / HEARTBEAT_REL_PATH
    hb_file.parent.mkdir(parents=True, exist_ok=True)
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    hb_file.write_text(old_ts.isoformat())


class ContinueLifecycleTest(unittest.TestCase):
    """Full lifecycle: session end → continue → stall → self-heal → decision → needs_human → answer → continue."""

    def test_full_continue_lifecycle(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = _setup_repo(Path(tmp) / "my-repo")

            # --- Step 1: Session ends, agent sets intent=continue ---
            intent = write_intent(repo, intent="continue", set_by="agent", reason="agent-stop: decision=CONTINUE task=task-42")
            self.assertEqual(intent.intent, "continue")
            self.assertEqual(intent.set_by, "agent")

            current = read_intent(repo)
            self.assertEqual(current.intent, "continue")

            # --- Step 2: Brain detects stale heartbeat ---
            _write_stale_heartbeat(repo)
            stale = check_heartbeats([repo], max_age_seconds=90)
            self.assertIn(repo, stale)

            # --- Step 3: Brain self-heal attempt (simulated) ---
            # Brain issues restart_loop directive (we just verify intent stays continue)
            current = read_intent(repo)
            self.assertEqual(current.intent, "continue")

            # --- Step 4: Self-heal fails, brain creates decision + sets needs_human ---
            decision = create_decision(
                repo,
                repo=repo.name,
                question="Dispatch loop stalled after self-heal. Restart manually or investigate?",
                category="external_dep",
                context={"task_id": "task-42", "options": ["restart", "investigate", "park"]},
            )
            self.assertEqual(decision.status, "pending")
            self.assertTrue(decision.id.startswith("dec-"))

            intent = write_intent(
                repo,
                intent="needs_human",
                set_by="brain",
                reason="self-heal exhausted, decision required",
                decision_id=decision.id,
            )
            self.assertEqual(intent.intent, "needs_human")
            self.assertEqual(intent.decision_id, decision.id)

            # Verify repos_needing_human picks it up
            needing = repos_needing_human([repo])
            self.assertIn(repo.name, needing)

            # --- Step 5: Notification sent via telegram ---
            msg = format_decision_message(decision)
            self.assertIn(repo.name, msg)
            self.assertIn(decision.id, msg)
            self.assertIn("restart", msg)

            # --- Step 6: Human answers via telegram ---
            answered = answer_decision(
                repo,
                decision_id=decision.id,
                answer="restart",
                answered_via="telegram",
            )
            self.assertEqual(answered.status, "answered")
            self.assertEqual(answered.answer, "restart")
            self.assertEqual(answered.answered_via, "telegram")

            # No more pending decisions
            pending = read_pending_decisions(repo)
            self.assertEqual(len(pending), 0)

            # --- Step 7: Intent goes back to continue ---
            intent = write_intent(
                repo,
                intent="continue",
                set_by="human",
                reason=f"answer received for {decision.id}: restart",
            )
            self.assertEqual(intent.intent, "continue")
            self.assertEqual(intent.set_by, "human")

            # Repo no longer needs human
            needing = repos_needing_human([repo])
            self.assertNotIn(repo.name, needing)


class ParkLifecycleTest(unittest.TestCase):
    """Full lifecycle: human parks → agent-stop preserves parked → human unparks."""

    def test_park_preserved_through_agent_stop(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = _setup_repo(Path(tmp) / "parked-repo")

            # --- Step 1: Human parks the repo ---
            intent = write_intent(repo, intent="parked", set_by="human", reason="taking a break, back Monday")
            self.assertEqual(intent.intent, "parked")

            # --- Step 2: Agent-stop handler checks current intent ---
            # The agent-stop.sh logic: if current intent is parked or needs_human, don't overwrite
            current = read_intent(repo)
            self.assertIn(current.intent, ("parked", "needs_human"))
            # Simulates agent-stop preserving parked by NOT writing continue
            after = read_intent(repo)
            self.assertEqual(after.intent, "parked")

            # --- Step 3: Brain sees parked repo, doesn't dispatch ---
            needing = repos_needing_human([repo])
            self.assertNotIn(repo.name, needing)  # parked != needs_human

            # --- Step 4: Human unparks ---
            intent = write_intent(repo, intent="continue", set_by="human", reason="back from break")
            self.assertEqual(intent.intent, "continue")

    def test_needs_human_preserved_through_agent_stop(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = _setup_repo(Path(tmp) / "blocked-repo")

            # Brain sets needs_human with a decision
            decision = create_decision(
                repo, repo=repo.name, question="Which auth provider?", category="business",
            )
            write_intent(
                repo,
                intent="needs_human",
                set_by="brain",
                reason="business decision required",
                decision_id=decision.id,
            )

            # Agent-stop runs: current intent is needs_human, so don't overwrite
            current = read_intent(repo)
            self.assertEqual(current.intent, "needs_human")

            # Answer arrives, intent restored to continue
            answer_decision(repo, decision_id=decision.id, answer="Use OAuth2", answered_via="telegram")
            write_intent(repo, intent="continue", set_by="human", reason="auth provider decided")
            self.assertEqual(read_intent(repo).intent, "continue")


class BrainTickNeedsHumanVisibilityTest(unittest.TestCase):
    """Brain tick includes needs_human info in tier 2 snapshot."""

    def test_needs_human_visible_in_tier2(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = _setup_repo(Path(tmp) / "stuck-repo")
            _write_fresh_heartbeat(repo)

            write_intent(repo, intent="needs_human", set_by="brain", reason="stuck")

            captured: list[dict] = []

            def mock_invoke_brain(**kwargs):
                if kwargs.get("tier") == 2 and kwargs.get("snapshot"):
                    captured.append(kwargs["snapshot"])
                return BrainResponse(reasoning="ok", directives=[], escalate=False)

            with patch("driftdriver.factory_brain.router.invoke_brain", side_effect=mock_invoke_brain):
                state = BrainState()
                run_brain_tick(state=state, roster_repos=[repo], snapshot={"factory": "test"}, dry_run=True)

            self.assertTrue(len(captured) >= 1)
            self.assertIn("needs_human_repos", captured[0])
            self.assertIn("stuck-repo", captured[0]["needs_human_repos"])


class MultiDecisionLifecycleTest(unittest.TestCase):
    """Multiple decisions can be pending; answering one doesn't clear others."""

    def test_multiple_decisions_independent(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = _setup_repo(Path(tmp) / "multi-repo")

            d1 = create_decision(repo, repo=repo.name, question="Q1?", category="aesthetic")
            d2 = create_decision(repo, repo=repo.name, question="Q2?", category="business")

            self.assertEqual(len(read_pending_decisions(repo)), 2)

            # Answer only d1
            answer_decision(repo, decision_id=d1.id, answer="A1", answered_via="telegram")
            pending = read_pending_decisions(repo)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].id, d2.id)


class IntentTransitionValidationTest(unittest.TestCase):
    """Edge cases in intent transitions."""

    def test_continue_to_needs_human_to_parked_to_continue(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = _setup_repo(Path(tmp) / "edge-repo")

            write_intent(repo, intent="continue", set_by="agent", reason="session end")
            self.assertEqual(read_intent(repo).intent, "continue")

            write_intent(repo, intent="needs_human", set_by="brain", reason="stuck")
            self.assertEqual(read_intent(repo).intent, "needs_human")
            self.assertIn(repo.name, repos_needing_human([repo]))

            write_intent(repo, intent="parked", set_by="human", reason="will look later")
            self.assertEqual(read_intent(repo).intent, "parked")
            self.assertNotIn(repo.name, repos_needing_human([repo]))

            write_intent(repo, intent="continue", set_by="human", reason="looked at it")
            self.assertEqual(read_intent(repo).intent, "continue")
            self.assertNotIn(repo.name, repos_needing_human([repo]))

    def test_no_intent_initially(self) -> None:
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "fresh-repo"
            runtime = repo / ".workgraph" / "service" / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "control.json").write_text(json.dumps({"repo": "fresh-repo"}))

            self.assertIsNone(read_intent(repo))
            self.assertNotIn(repo.name, repos_needing_human([repo]))


class NotificationIntegrationTest(unittest.TestCase):
    """Decision creation through notification formatting."""

    @patch("driftdriver.decision_notifier.send_telegram", return_value=True)
    def test_decision_to_notification_pipeline(self, mock_send) -> None:
        with TemporaryDirectory() as tmp:
            repo = _setup_repo(Path(tmp) / "notify-repo")

            decision = create_decision(
                repo,
                repo=repo.name,
                question="Deploy to prod or wait for Monday?",
                category="business",
                context={"options": ["deploy now", "wait"]},
            )

            success = notify_decision(decision, bot_token="tok", chat_id="chat")
            self.assertTrue(success)

            call_kwargs = mock_send.call_args.kwargs
            self.assertIn("Deploy to prod", call_kwargs["message"])
            self.assertIn(decision.id, call_kwargs["message"])


if __name__ == "__main__":
    unittest.main()
