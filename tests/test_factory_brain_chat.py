# ABOUTME: Tests for factory brain chat handler — confirmation flow, chat_id auth,
# ABOUTME: decision pre-screening, conversation state, and directive execution.
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from driftdriver.factory_brain.chat import (
    DESTRUCTIVE_ACTIONS,
    _conversations,
    _pending_confirmations,
    append_conversation,
    build_chat_context,
    build_chat_prompt,
    clear_pending_confirmation,
    extract_decision_id,
    get_conversation,
    get_pending_confirmation,
    handle_chat_message,
    load_authorized_chat_ids,
    needs_confirmation,
    parse_chat_response,
    set_pending_confirmation,
)


@pytest.fixture(autouse=True)
def _clear_state():
    """Clear conversation and confirmation state between tests."""
    _conversations.clear()
    _pending_confirmations.clear()
    yield
    _conversations.clear()
    _pending_confirmations.clear()


# ---------------------------------------------------------------------------
# needs_confirmation
# ---------------------------------------------------------------------------

class TestNeedsConfirmation:
    def test_kill_daemon(self):
        assert needs_confirmation([{"action": "kill_daemon", "params": {"repo": "x"}}])

    def test_kill_process(self):
        assert needs_confirmation([{"action": "kill_process", "params": {"pid": 123}}])

    def test_unenroll(self):
        assert needs_confirmation([{"action": "unenroll", "params": {"repo": "x"}}])

    def test_set_mode_autonomous(self):
        assert needs_confirmation([{"action": "set_mode", "params": {"repo": "x", "mode": "autonomous"}}])

    def test_set_mode_observe_safe(self):
        assert not needs_confirmation([{"action": "set_mode", "params": {"repo": "x", "mode": "observe"}}])

    def test_set_mode_supervise_safe(self):
        assert not needs_confirmation([{"action": "set_mode", "params": {"repo": "x", "mode": "supervise"}}])

    def test_safe_actions(self):
        safe = [
            {"action": "start_dispatch_loop", "params": {"repo": "x"}},
            {"action": "stop_dispatch_loop", "params": {"repo": "x"}},
            {"action": "spawn_agent", "params": {"repo": "x", "task_id": "t1"}},
            {"action": "adjust_concurrency", "params": {"repo": "x", "max_agents": 3}},
            {"action": "enroll", "params": {"repo": "x"}},
            {"action": "clear_locks", "params": {"repo": "x"}},
            {"action": "set_attractor_target", "params": {"repo": "x", "target": "hardened"}},
        ]
        for d in safe:
            assert not needs_confirmation([d]), f"{d['action']} should not need confirmation"

    def test_empty_list(self):
        assert not needs_confirmation([])

    def test_mixed_safe_and_destructive(self):
        directives = [
            {"action": "start_dispatch_loop", "params": {"repo": "x"}},
            {"action": "kill_daemon", "params": {"repo": "x"}},
        ]
        assert needs_confirmation(directives)


# ---------------------------------------------------------------------------
# Decision pre-screening
# ---------------------------------------------------------------------------

class TestDecisionPreScreening:
    def test_extract_decision_id(self):
        assert extract_decision_id("dec-20260314-abc123 yes") == "dec-20260314-abc123"

    def test_no_decision_id(self):
        assert extract_decision_id("what's lodestar doing") is None

    def test_decision_id_routing(self):
        with patch("driftdriver.factory_brain.chat.load_authorized_chat_ids", return_value=set()):
            result = handle_chat_message(
                message="dec-20260314-abc123 approved",
                chat_id="authorized",
            )
        assert result["decision_route"]["decision_id"] == "dec-20260314-abc123"
        assert result["decision_route"]["answer"] == "approved"
        assert result["directives_executed"] == []

    def test_decision_id_default_answer(self):
        with patch("driftdriver.factory_brain.chat.load_authorized_chat_ids", return_value=set()):
            result = handle_chat_message(
                message="dec-20260314-abc123",
                chat_id="authorized",
            )
        assert result["decision_route"]["answer"] == "approved"


# ---------------------------------------------------------------------------
# Conversation state
# ---------------------------------------------------------------------------

class TestConversationState:
    def test_append_and_get(self):
        append_conversation("chat1", "user", "hello")
        append_conversation("chat1", "assistant", "hi")
        history = get_conversation("chat1")
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "hello"}

    def test_eviction(self):
        for i in range(25):
            append_conversation("chat1", "user", f"msg-{i}")
            append_conversation("chat1", "assistant", f"reply-{i}")
        history = get_conversation("chat1")
        # 10 pairs = 20 entries
        assert len(history) == 20

    def test_separate_chat_ids(self):
        append_conversation("a", "user", "for a")
        append_conversation("b", "user", "for b")
        assert len(get_conversation("a")) == 1
        assert len(get_conversation("b")) == 1

    def test_empty_history(self):
        assert get_conversation("nonexistent") == []

    def test_eviction_keeps_newest(self):
        """Verify oldest messages are evicted and newest remain."""
        for i in range(15):
            append_conversation("chat1", "user", f"msg-{i}")
            append_conversation("chat1", "assistant", f"reply-{i}")
        history = get_conversation("chat1")
        # 10 pairs = 20 entries, oldest (0-4) should be evicted
        assert history[0] == {"role": "user", "content": "msg-5"}
        assert history[-1] == {"role": "assistant", "content": "reply-14"}

    def test_returns_copy(self):
        """get_conversation returns a copy, not a reference."""
        append_conversation("chat1", "user", "hello")
        h1 = get_conversation("chat1")
        h1.append({"role": "user", "content": "injected"})
        h2 = get_conversation("chat1")
        assert len(h2) == 1  # original unaffected


# ---------------------------------------------------------------------------
# Pending confirmation state
# ---------------------------------------------------------------------------

class TestPendingConfirmation:
    def test_set_and_get(self):
        set_pending_confirmation("c1", {"action": "kill_daemon"})
        result = get_pending_confirmation("c1")
        assert result == {"action": "kill_daemon"}
        # Should be cleared after get
        assert get_pending_confirmation("c1") is None

    def test_clear(self):
        set_pending_confirmation("c1", {"action": "kill_daemon"})
        clear_pending_confirmation("c1")
        assert get_pending_confirmation("c1") is None

    def test_clear_nonexistent(self):
        clear_pending_confirmation("nonexistent")  # should not raise


# ---------------------------------------------------------------------------
# parse_chat_response
# ---------------------------------------------------------------------------

class TestParseChatResponse:
    def test_valid_response(self):
        raw = {
            "reply_text": "Done.",
            "directives": [{"action": "start_dispatch_loop", "params": {"repo": "x"}}],
            "needs_confirmation": False,
        }
        result = parse_chat_response(raw)
        assert result["reply_text"] == "Done."
        assert len(result["directives"]) == 1
        assert result["needs_confirmation"] is False

    def test_missing_reply_text(self):
        raw = {"directives": [], "needs_confirmation": False}
        result = parse_chat_response(raw)
        assert "couldn't process" in result["reply_text"]

    def test_non_dict(self):
        result = parse_chat_response("garbage")
        assert "couldn't process" in result["reply_text"]
        assert result["directives"] == []

    def test_infers_needs_confirmation(self):
        raw = {
            "reply_text": "Kill daemon?",
            "directives": [{"action": "kill_daemon", "params": {"repo": "x"}}],
        }
        result = parse_chat_response(raw)
        assert result["needs_confirmation"] is True

    def test_filters_bad_directives(self):
        raw = {
            "reply_text": "ok",
            "directives": [42, {"no_action": True}, {"action": "noop", "params": {}}],
            "needs_confirmation": False,
        }
        result = parse_chat_response(raw)
        assert len(result["directives"]) == 1

    def test_none_input(self):
        result = parse_chat_response(None)
        assert "couldn't process" in result["reply_text"]
        assert result["directives"] == []
        assert result["needs_confirmation"] is False

    def test_empty_string_reply(self):
        raw = {"reply_text": "", "directives": [], "needs_confirmation": False}
        result = parse_chat_response(raw)
        assert "couldn't process" in result["reply_text"]

    def test_whitespace_only_reply(self):
        raw = {"reply_text": "   ", "directives": [], "needs_confirmation": False}
        result = parse_chat_response(raw)
        assert "couldn't process" in result["reply_text"]

    def test_directives_not_list(self):
        raw = {"reply_text": "ok", "directives": "not a list", "needs_confirmation": False}
        result = parse_chat_response(raw)
        assert result["directives"] == []

    def test_list_input(self):
        result = parse_chat_response([1, 2, 3])
        assert "couldn't process" in result["reply_text"]

    def test_integer_input(self):
        result = parse_chat_response(42)
        assert "couldn't process" in result["reply_text"]

    def test_needs_confirmation_non_bool_inferred(self):
        """When needs_confirmation is not a bool, infer from directives."""
        raw = {
            "reply_text": "Start loop.",
            "directives": [{"action": "start_dispatch_loop", "params": {"repo": "x"}}],
            "needs_confirmation": "yes",  # not a bool
        }
        result = parse_chat_response(raw)
        # start_dispatch_loop is safe, so inferred as False
        assert result["needs_confirmation"] is False

    def test_preserves_multiple_valid_directives(self):
        raw = {
            "reply_text": "Doing both.",
            "directives": [
                {"action": "stop_dispatch_loop", "params": {"repo": "x"}},
                {"action": "start_dispatch_loop", "params": {"repo": "x"}},
            ],
            "needs_confirmation": False,
        }
        result = parse_chat_response(raw)
        assert len(result["directives"]) == 2
        assert result["directives"][0]["action"] == "stop_dispatch_loop"
        assert result["directives"][1]["action"] == "start_dispatch_loop"


# ---------------------------------------------------------------------------
# build_chat_context
# ---------------------------------------------------------------------------

class TestBuildChatContext:
    def test_includes_roster(self):
        ctx = build_chat_context(roster={"repos": ["a", "b"]})
        assert "Enrolled Repos" in ctx
        assert '"a"' in ctx

    def test_includes_pending_decisions(self):
        ctx = build_chat_context(pending_decisions=[{"id": "dec-1"}])
        assert "Pending Decisions" in ctx

    def test_empty(self):
        ctx = build_chat_context()
        assert ctx == ""

    def test_includes_events_history(self):
        events = [{"type": "crash", "repo": "lodestar", "ts": "2026-03-14T10:00:00"}]
        ctx = build_chat_context(events_history=events)
        assert "Event History" in ctx
        assert "crash" in ctx
        assert "lodestar" in ctx

    def test_includes_brain_log(self):
        log = [{"tier": "haiku", "model": "haiku", "reasoning": "all ok"}]
        ctx = build_chat_context(brain_log=log)
        assert "Brain Invocation Log" in ctx
        assert "haiku" in ctx

    def test_includes_speedriftd_states(self):
        states = {"lodestar": {"mode": "observe", "lease_owner": None}}
        ctx = build_chat_context(speedriftd_states=states)
        assert "Speedriftd Mode" in ctx
        assert "observe" in ctx

    def test_includes_velocity_data(self):
        velocity = {"completed_today": 5, "rate": 0.8}
        ctx = build_chat_context(velocity_data=velocity)
        assert "Velocity" in ctx
        assert "0.8" in ctx

    def test_includes_conversation_history(self):
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        ctx = build_chat_context(conversation_history=history)
        assert "Conversation History" in ctx

    def test_snapshot_repo_summaries(self):
        snapshot = {
            "repos": [
                {
                    "name": "lodestar",
                    "activity_state": "active",
                    "narrative": "running fine",
                    "task_counts": {"open": 3, "done": 10},
                    "in_progress": 2,
                    "ready": 1,
                    "stalled": 0,
                    "stall_reasons": [],
                    "service_running": True,
                    "runtime": {"uptime": 3600},
                    "presence_actors": ["agent-1"],
                }
            ],
            "overview": {"total_repos": 1, "total_agents": 2},
            "narrative": "Ecosystem is healthy.",
        }
        ctx = build_chat_context(snapshot=snapshot)
        assert "Repo Snapshots" in ctx
        assert "lodestar" in ctx
        assert "Ecosystem Overview" in ctx
        assert "Ecosystem Narrative" in ctx
        assert "Ecosystem is healthy." in ctx

    def test_snapshot_skips_non_dict_repos(self):
        snapshot = {"repos": ["not-a-dict", {"name": "real", "activity_state": "idle"}]}
        ctx = build_chat_context(snapshot=snapshot)
        assert "real" in ctx

    def test_all_sections_together(self):
        ctx = build_chat_context(
            roster={"repos": ["a"]},
            events_history=[{"type": "stall"}],
            brain_log=[{"tier": "sonnet"}],
            pending_decisions=[{"id": "dec-1"}],
            speedriftd_states={"a": {"mode": "observe"}},
            velocity_data={"rate": 1.0},
            conversation_history=[{"role": "user", "content": "test"}],
        )
        assert "Enrolled Repos" in ctx
        assert "Event History" in ctx
        assert "Brain Invocation Log" in ctx
        assert "Pending Decisions" in ctx
        assert "Speedriftd Mode" in ctx
        assert "Velocity" in ctx
        assert "Conversation History" in ctx


# ---------------------------------------------------------------------------
# build_chat_prompt
# ---------------------------------------------------------------------------

class TestBuildChatPrompt:
    def test_includes_directive_vocabulary(self):
        prompt = build_chat_prompt()
        assert "kill_daemon" in prompt
        assert "needs_confirmation" in prompt

    def test_mentions_restart_pattern(self):
        prompt = build_chat_prompt()
        assert "restart_dispatch_loop" in prompt


# ---------------------------------------------------------------------------
# load_authorized_chat_ids
# ---------------------------------------------------------------------------

class TestLoadAuthorizedChatIds:
    def test_missing_file(self, tmp_path):
        result = load_authorized_chat_ids(tmp_path / "nonexistent.toml")
        assert result == set()

    def test_telegram_factory_section(self, tmp_path):
        toml_file = tmp_path / "notify.toml"
        toml_file.write_text('[telegram_factory]\nchat_id = "12345"\n')
        result = load_authorized_chat_ids(toml_file)
        assert result == {"12345"}

    def test_telegram_fallback(self, tmp_path):
        toml_file = tmp_path / "notify.toml"
        toml_file.write_text('[telegram]\nchat_id = "67890"\n')
        result = load_authorized_chat_ids(toml_file)
        assert result == {"67890"}


# ---------------------------------------------------------------------------
# handle_chat_message — confirmation flow
# ---------------------------------------------------------------------------

class TestHandleChatMessageConfirmation:
    """Test the full confirmation flow: request → confirm/cancel → execute."""

    def _mock_invoke_chat(self, response_data):
        """Return a patch that makes invoke_chat return response_data."""
        return patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=response_data,
        )

    def _mock_auth(self, chat_ids=None):
        """Return a patch that makes load_authorized_chat_ids return the given set."""
        if chat_ids is None:
            chat_ids = set()
        return patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=chat_ids,
        )

    def test_destructive_action_stores_confirmation(self):
        llm_response = {
            "reply_text": "Kill daemon on lodestar — sure?",
            "directives": [{"action": "kill_daemon", "params": {"repo": "lodestar"}}],
            "needs_confirmation": True,
        }
        with self._mock_invoke_chat(llm_response), self._mock_auth():
            result = handle_chat_message(message="kill lodestar daemon", chat_id="c1")
        assert result["pending_confirmation"] is not None
        assert result["directives_executed"] == []
        assert "kill" in result["reply"].lower() or "sure" in result["reply"].lower()

    def test_yes_confirms_and_executes(self):
        # Set up a pending confirmation
        set_pending_confirmation("c1", {
            "directives": [{"action": "noop", "params": {"reason": "test"}}],
            "reply_text": "Are you sure?",
        })
        with self._mock_auth(), \
             patch("driftdriver.factory_brain.directives.execute_directives", return_value=[{"status": "ok"}]):
            result = handle_chat_message(message="yes", chat_id="c1")
        assert result["directives_executed"] == [{"status": "ok"}]
        assert result["pending_confirmation"] is None

    def test_y_confirms(self):
        set_pending_confirmation("c1", {
            "directives": [{"action": "noop", "params": {"reason": "test"}}],
        })
        with self._mock_auth(), \
             patch("driftdriver.factory_brain.directives.execute_directives", return_value=[{"status": "ok"}]):
            result = handle_chat_message(message="Y", chat_id="c1")
        assert result["directives_executed"] == [{"status": "ok"}]

    def test_no_cancels_without_llm(self):
        set_pending_confirmation("c1", {
            "directives": [{"action": "kill_daemon", "params": {"repo": "x"}}],
        })
        with self._mock_auth():
            result = handle_chat_message(message="no", chat_id="c1")
        assert result["reply"] == "Cancelled."
        assert result["directives_executed"] == []
        assert result["pending_confirmation"] is None

    def test_n_cancels(self):
        set_pending_confirmation("c1", {
            "directives": [{"action": "kill_daemon", "params": {"repo": "x"}}],
        })
        with self._mock_auth():
            result = handle_chat_message(message="N", chat_id="c1")
        assert result["reply"] == "Cancelled."

    def test_other_message_cancels_and_processes(self):
        set_pending_confirmation("c1", {
            "directives": [{"action": "kill_daemon", "params": {"repo": "x"}}],
        })
        llm_response = {
            "reply_text": "Lodestar is fine.",
            "directives": [],
            "needs_confirmation": False,
        }
        with self._mock_invoke_chat(llm_response), self._mock_auth():
            result = handle_chat_message(message="what's lodestar doing?", chat_id="c1")
        assert result["reply"] == "Lodestar is fine."
        assert get_pending_confirmation("c1") is None

    def test_safe_action_executes_immediately(self):
        llm_response = {
            "reply_text": "Starting dispatch loop.",
            "directives": [{"action": "start_dispatch_loop", "params": {"repo": "x"}}],
            "needs_confirmation": False,
        }
        with self._mock_invoke_chat(llm_response), \
             self._mock_auth(), \
             patch("driftdriver.factory_brain.directives.execute_directives", return_value=[{"status": "ok"}]) as mock_exec:
            result = handle_chat_message(message="start dispatch on x", chat_id="c1")
        assert result["directives_executed"] == [{"status": "ok"}]
        assert result["pending_confirmation"] is None
        mock_exec.assert_called_once()


# ---------------------------------------------------------------------------
# handle_chat_message — chat_id auth
# ---------------------------------------------------------------------------

class TestChatIdAuth:
    def test_unauthorized_rejected(self):
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value={"authorized_id"},
        ):
            result = handle_chat_message(message="hello", chat_id="unknown_id")
        assert result["reply"] == "Unauthorized chat_id."
        assert result["directives_executed"] == []

    def test_authorized_proceeds(self):
        llm_response = {
            "reply_text": "Hello!",
            "directives": [],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value={"my_id"},
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ):
            result = handle_chat_message(message="hello", chat_id="my_id")
        assert result["reply"] == "Hello!"

    def test_empty_allowlist_allows_all(self):
        """When no chat_ids are configured, all are allowed."""
        llm_response = {
            "reply_text": "Hello!",
            "directives": [],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ):
            result = handle_chat_message(message="hello", chat_id="anyone")
        assert result["reply"] == "Hello!"


# ---------------------------------------------------------------------------
# handle_chat_message — LLM error handling
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# handle_chat_message — read-only queries
# ---------------------------------------------------------------------------

class TestReadOnlyQueries:
    """Read-only queries should produce no directives and no confirmation."""

    def test_status_query_no_directives(self):
        llm_response = {
            "reply_text": "Lodestar has 2 agents running.",
            "directives": [],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ):
            result = handle_chat_message(message="what's lodestar doing?", chat_id="c1")
        assert result["reply"] == "Lodestar has 2 agents running."
        assert result["directives_executed"] == []
        assert result["pending_confirmation"] is None

    def test_advisory_query_no_directives(self):
        llm_response = {
            "reply_text": "I'd recommend changing the attractor target to hardened.",
            "directives": [],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ):
            result = handle_chat_message(message="should I change lodestar target?", chat_id="c1")
        assert result["directives_executed"] == []
        assert result["pending_confirmation"] is None


# ---------------------------------------------------------------------------
# handle_chat_message — directive routing
# ---------------------------------------------------------------------------

class TestDirectiveRouting:
    """Action queries produce correct directive types with correct params."""

    def test_spawn_agent_directive(self):
        llm_response = {
            "reply_text": "Spawning agent for task t1.",
            "directives": [{"action": "spawn_agent", "params": {"repo": "lodestar", "task_id": "t1"}}],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ), patch(
            "driftdriver.factory_brain.directives.execute_directives",
            return_value=[{"status": "ok", "action": "spawn_agent"}],
        ) as mock_exec:
            result = handle_chat_message(message="spawn agent on lodestar for t1", chat_id="c1")
        assert result["directives_executed"] == [{"status": "ok", "action": "spawn_agent"}]
        # Verify the Directive objects passed to execute_directives
        called_directives = mock_exec.call_args[0][0]
        assert len(called_directives) == 1
        assert called_directives[0].action == "spawn_agent"
        assert called_directives[0].params == {"repo": "lodestar", "task_id": "t1"}

    def test_stop_then_start_restart_pattern(self):
        """Restart = stop + start (no restart_dispatch_loop action exists)."""
        llm_response = {
            "reply_text": "Restarting dispatch loop on lodestar.",
            "directives": [
                {"action": "stop_dispatch_loop", "params": {"repo": "lodestar"}},
                {"action": "start_dispatch_loop", "params": {"repo": "lodestar"}},
            ],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ), patch(
            "driftdriver.factory_brain.directives.execute_directives",
            return_value=[{"status": "ok"}, {"status": "ok"}],
        ) as mock_exec:
            result = handle_chat_message(message="restart dispatch on lodestar", chat_id="c1")
        assert len(result["directives_executed"]) == 2
        called_directives = mock_exec.call_args[0][0]
        assert called_directives[0].action == "stop_dispatch_loop"
        assert called_directives[1].action == "start_dispatch_loop"

    def test_set_mode_observe_executes_immediately(self):
        """set_mode to observe is safe — no confirmation needed."""
        llm_response = {
            "reply_text": "Setting lodestar to observe mode.",
            "directives": [{"action": "set_mode", "params": {"repo": "lodestar", "mode": "observe"}}],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ), patch(
            "driftdriver.factory_brain.directives.execute_directives",
            return_value=[{"status": "ok"}],
        ):
            result = handle_chat_message(message="set lodestar to observe", chat_id="c1")
        assert result["directives_executed"] == [{"status": "ok"}]
        assert result["pending_confirmation"] is None

    def test_set_mode_autonomous_needs_confirmation(self):
        """set_mode to autonomous requires confirmation even if LLM doesn't flag it."""
        llm_response = {
            "reply_text": "Set lodestar to autonomous — are you sure?",
            "directives": [{"action": "set_mode", "params": {"repo": "lodestar", "mode": "autonomous"}}],
            "needs_confirmation": False,  # LLM didn't flag it, but code should catch it
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ):
            result = handle_chat_message(message="set lodestar to autonomous", chat_id="c1")
        # Should still require confirmation because needs_confirmation() catches it
        assert result["pending_confirmation"] is not None
        assert result["directives_executed"] == []


# ---------------------------------------------------------------------------
# handle_chat_message — conversation recording
# ---------------------------------------------------------------------------

class TestConversationRecording:
    """Verify that handle_chat_message records messages in conversation history."""

    def test_normal_message_recorded(self):
        llm_response = {
            "reply_text": "All good.",
            "directives": [],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ):
            handle_chat_message(message="status?", chat_id="c1")
        history = get_conversation("c1")
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "status?"}
        assert history[1] == {"role": "assistant", "content": "All good."}

    def test_error_message_recorded(self):
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            side_effect=RuntimeError("fail"),
        ):
            handle_chat_message(message="test", chat_id="c1")
        history = get_conversation("c1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert "couldn't process" in history[1]["content"]

    def test_confirmation_yes_recorded(self):
        set_pending_confirmation("c1", {
            "directives": [{"action": "noop", "params": {"reason": "test"}}],
        })
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.directives.execute_directives",
            return_value=[],
        ):
            handle_chat_message(message="yes", chat_id="c1")
        history = get_conversation("c1")
        assert history[0] == {"role": "user", "content": "yes"}

    def test_confirmation_no_recorded(self):
        set_pending_confirmation("c1", {
            "directives": [{"action": "kill_daemon", "params": {"repo": "x"}}],
        })
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ):
            handle_chat_message(message="no", chat_id="c1")
        history = get_conversation("c1")
        assert history[0] == {"role": "user", "content": "no"}
        assert history[1] == {"role": "assistant", "content": "Cancelled."}

    def test_unauthorized_not_recorded(self):
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value={"other_id"},
        ):
            handle_chat_message(message="hello", chat_id="bad_id")
        history = get_conversation("bad_id")
        assert len(history) == 0

    def test_decision_route_not_recorded(self):
        """Decision pre-screening bypasses conversation recording."""
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ):
            handle_chat_message(message="dec-20260314-abc123 yes", chat_id="c1")
        history = get_conversation("c1")
        assert len(history) == 0


# ---------------------------------------------------------------------------
# handle_chat_message — LLM error handling
# ---------------------------------------------------------------------------

class TestChatLLMError:
    def test_llm_failure_returns_error_reply(self):
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            side_effect=RuntimeError("claude crashed"),
        ):
            result = handle_chat_message(message="hello", chat_id="c1")
        assert "couldn't process" in result["reply"]
        assert result["directives_executed"] == []


# ---------------------------------------------------------------------------
# Integration test — full /api/chat HTTP round-trip
# ---------------------------------------------------------------------------

class TestChatApiIntegration:
    """POST /api/chat through the real HTTP handler with invoke_chat stubbed."""

    @pytest.fixture(autouse=True)
    def _server(self, tmp_path):
        """Spin up a ThreadingHTTPServer on a free port with a test snapshot."""
        from http.server import ThreadingHTTPServer

        from driftdriver.ecosystem_hub.api import _handler_factory
        from driftdriver.ecosystem_hub.websocket import LiveStreamHub

        # Write a minimal snapshot
        snapshot = {
            "schema": 1,
            "generated_at": "2026-03-14T00:00:00Z",
            "repos": [
                {
                    "name": "test-repo",
                    "path": str(tmp_path / "test-repo"),
                    "activity_state": "active",
                    "task_counts": {"open": 2, "done": 5},
                    "in_progress": 1,
                    "ready": 1,
                    "stalled": 0,
                    "stall_reasons": [],
                    "service_running": True,
                    "runtime": {"uptime": 3600},
                    "presence_actors": ["agent-1"],
                }
            ],
            "overview": {"total_repos": 1, "total_agents": 1},
            "narrative": "Test ecosystem.",
        }
        snapshot_path = tmp_path / "snapshot.json"
        snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        state_path = tmp_path / "state.json"
        state_path.write_text("{}", encoding="utf-8")

        import threading

        stop_event = threading.Event()
        live_hub = LiveStreamHub(stop_event)
        handler_cls = _handler_factory(snapshot_path, state_path, live_hub)

        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self._port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        yield
        server.shutdown()
        stop_event.set()

    def _post_chat(self, payload: dict) -> dict:
        """POST JSON to /api/chat and return parsed response."""
        import urllib.request

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._port}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            return json.loads(resp.read())

    def _post_chat_raw(self, payload: dict) -> tuple[int, dict]:
        """POST and return (status_code, body) — handles non-200 responses."""
        import urllib.error
        import urllib.request

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._port}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_status_query_round_trip(self):
        """Full round-trip: POST status question, get coherent response."""
        llm_response = {
            "reply_text": "test-repo is active with 1 agent running.",
            "directives": [],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ) as mock_invoke:
            result = self._post_chat({
                "message": "what's test-repo doing?",
                "chat_id": "integration-test",
                "user_name": "Tester",
            })

        assert result["reply"] == "test-repo is active with 1 agent running."
        assert result["directives_executed"] == []
        assert result["pending_confirmation"] is None

        # Verify context was assembled and passed to invoke_chat
        mock_invoke.assert_called_once()
        call_args = mock_invoke.call_args
        # invoke_chat(system_prompt, context, user_message)
        system_prompt = call_args[0][0]
        context = call_args[0][1]
        user_message = call_args[0][2]
        assert "what's test-repo doing?" in user_message
        assert "kill_daemon" in system_prompt  # directive vocab present
        assert "test-repo" in context  # snapshot context assembled

    def test_auth_rejection(self):
        """Unauthorized chat_id gets 403 — no LLM call."""
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value={"allowed-id"},
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
        ) as mock_invoke:
            status, body = self._post_chat_raw({
                "message": "hello",
                "chat_id": "unauthorized-id",
            })

        assert status == 403
        assert body["error"] == "unauthorized"
        mock_invoke.assert_not_called()

    def test_missing_fields_rejected(self):
        """Missing message or chat_id returns 400."""
        status, body = self._post_chat_raw({"message": "hello"})
        assert status == 400
        assert body["error"] == "missing_fields"

    def test_action_directive_round_trip(self):
        """Action request → directives executed, returned in response."""
        llm_response = {
            "reply_text": "Starting dispatch loop on test-repo.",
            "directives": [{"action": "start_dispatch_loop", "params": {"repo": "test-repo"}}],
            "needs_confirmation": False,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ), patch(
            "driftdriver.factory_brain.directives.execute_directives",
            return_value=[{"status": "ok", "action": "start_dispatch_loop"}],
        ):
            result = self._post_chat({
                "message": "start dispatch on test-repo",
                "chat_id": "integration-test",
            })

        assert result["reply"] == "Starting dispatch loop on test-repo."
        assert result["directives_executed"] == [{"status": "ok", "action": "start_dispatch_loop"}]
        assert result["pending_confirmation"] is None

    def test_destructive_action_requires_confirmation(self):
        """Destructive directive stores confirmation, no execution."""
        llm_response = {
            "reply_text": "Kill daemon on test-repo — sure?",
            "directives": [{"action": "kill_daemon", "params": {"repo": "test-repo"}}],
            "needs_confirmation": True,
        }
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            return_value=llm_response,
        ):
            result = self._post_chat({
                "message": "kill daemon on test-repo",
                "chat_id": "integration-test",
            })

        assert result["directives_executed"] == []
        assert result["pending_confirmation"] is not None
        assert result["pending_confirmation"]["directives"][0]["action"] == "kill_daemon"

    def test_llm_error_returns_error_reply(self):
        """invoke_chat failure → user-friendly error, no crash."""
        with patch(
            "driftdriver.factory_brain.chat.load_authorized_chat_ids",
            return_value=set(),
        ), patch(
            "driftdriver.factory_brain.chat.invoke_chat",
            side_effect=RuntimeError("model unavailable"),
        ):
            result = self._post_chat({
                "message": "status?",
                "chat_id": "integration-test",
            })

        assert "couldn't process" in result["reply"]
        assert result["directives_executed"] == []
