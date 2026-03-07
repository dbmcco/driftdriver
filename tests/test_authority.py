# ABOUTME: Tests for authority and budget enforcement module.
# ABOUTME: Covers can_do, check_budget, get_budget, get_authority, and load_authority_policy.

from __future__ import annotations

import pytest
from pathlib import Path

from driftdriver.actor import Actor
from driftdriver.authority import (
    Budget,
    DEFAULT_BUDGETS,
    can_do,
    check_budget,
    get_authority,
    get_budget,
    load_authority_policy,
)


class TestCanDo:
    def test_human_can_dispatch(self):
        actor = Actor(id="h-1", actor_class="human", name="braydon")
        assert can_do(actor, "dispatch") is True

    def test_lane_cannot_dispatch(self):
        actor = Actor(id="l-1", actor_class="lane", name="qadrift")
        assert can_do(actor, "dispatch") is False

    def test_interactive_can_create(self):
        actor = Actor(id="i-1", actor_class="interactive", name="claude-code")
        assert can_do(actor, "create") is True

    def test_can_do_with_policy_override_grants_dispatch_to_interactive(self):
        actor = Actor(id="i-2", actor_class="interactive", name="claude-code")
        # Without override, interactive cannot dispatch
        assert can_do(actor, "dispatch") is False
        # With override granting dispatch
        policy = {
            "grants": {"interactive": frozenset({"read", "claim", "create", "modify", "dispatch"})},
        }
        assert can_do(actor, "dispatch", policy=policy) is True


class TestGetBudget:
    def test_default_budget_for_lane(self):
        budget = get_budget("lane")
        assert budget.max_active_tasks == 3
        assert budget.max_creates_per_hour == 10
        assert budget.max_dispatches_per_hour == 0

    def test_get_budget_with_policy_override(self):
        policy = {
            "budgets": {"lane": Budget(max_active_tasks=5, max_creates_per_hour=15, max_dispatches_per_hour=0)},
        }
        budget = get_budget("lane", policy=policy)
        assert budget.max_active_tasks == 5
        assert budget.max_creates_per_hour == 15


class TestCheckBudget:
    def test_allows_create_when_under_limit(self):
        actor = Actor(id="w-1", actor_class="worker", name="impl")
        allowed, reason = check_budget(actor, "create", current_count=0, recent_count=0)
        assert allowed is True
        assert reason == ""

    def test_denies_create_when_at_max_active_tasks(self):
        actor = Actor(id="w-2", actor_class="worker", name="impl")
        # worker max_active_tasks = 1
        allowed, reason = check_budget(actor, "create", current_count=1, recent_count=0)
        assert allowed is False
        assert "max_active_tasks" in reason

    def test_denies_create_when_at_max_creates_per_hour(self):
        actor = Actor(id="w-3", actor_class="worker", name="impl")
        # worker max_creates_per_hour = 5
        allowed, reason = check_budget(actor, "create", current_count=0, recent_count=5)
        assert allowed is False
        assert "max_creates_per_hour" in reason

    def test_allows_dispatch_for_daemon_under_limit(self):
        actor = Actor(id="d-1", actor_class="daemon", name="speedriftd")
        # daemon max_dispatches_per_hour = 10
        allowed, reason = check_budget(actor, "dispatch", recent_count=5)
        assert allowed is True
        assert reason == ""

    def test_denies_dispatch_for_lane(self):
        actor = Actor(id="l-1", actor_class="lane", name="qadrift")
        # lane max_dispatches_per_hour = 0
        allowed, reason = check_budget(actor, "dispatch", recent_count=0)
        assert allowed is False
        assert "max_dispatches_per_hour" in reason


class TestLoadAuthorityPolicy:
    def test_no_file_returns_defaults(self, tmp_path: Path):
        policy = load_authority_policy(tmp_path / "nonexistent.toml")
        assert policy == {}

    def test_partial_override_merges_correctly(self, tmp_path: Path):
        toml_file = tmp_path / "drift-policy.toml"
        toml_file.write_text(
            '[authority]\n'
            '[authority.grants]\n'
            'interactive = ["read", "claim", "create", "modify", "dispatch"]\n'
            '\n'
            '[authority.budgets.lane]\n'
            'max_active_tasks = 5\n'
            'max_creates_per_hour = 15\n',
            encoding="utf-8",
        )
        policy = load_authority_policy(toml_file)
        # Grants override for interactive should include dispatch
        assert "dispatch" in policy["grants"]["interactive"]
        # Budget override for lane
        assert policy["budgets"]["lane"].max_active_tasks == 5
        assert policy["budgets"]["lane"].max_creates_per_hour == 15
        # Budget for lane dispatch should use default (0)
        assert policy["budgets"]["lane"].max_dispatches_per_hour == 0


class TestGetAuthority:
    def test_returns_correct_frozenset_for_each_class(self):
        from driftdriver.actor import DEFAULT_AUTHORITY as ACTOR_DEFAULT_AUTHORITY
        for cls in ("human", "interactive", "worker", "daemon", "lane"):
            auth = get_authority(cls)
            assert isinstance(auth, frozenset)
            assert auth == ACTOR_DEFAULT_AUTHORITY[cls]

    def test_with_policy_override_adds_dispatch_to_interactive(self):
        policy = {
            "grants": {"interactive": frozenset({"read", "claim", "create", "modify", "dispatch"})},
        }
        auth = get_authority("interactive", policy=policy)
        assert "dispatch" in auth
        assert auth == frozenset({"read", "claim", "create", "modify", "dispatch"})
