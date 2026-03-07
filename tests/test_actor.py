# ABOUTME: Tests for actor identity model.
# ABOUTME: Covers Actor creation, validation, serialization, and default authority.

from __future__ import annotations

import pytest

from driftdriver.actor import (
    ACTOR_CLASSES,
    DEFAULT_AUTHORITY,
    Actor,
    actor_from_dict,
    actor_to_dict,
)


class TestActorCreation:
    def test_create_actor_with_valid_class(self):
        a = Actor(id="sess-1", actor_class="human", name="braydon")
        assert a.id == "sess-1"
        assert a.actor_class == "human"
        assert a.name == "braydon"
        assert a.repo == ""

    def test_create_actor_with_repo(self):
        a = Actor(id="w-1", actor_class="worker", name="implementer", repo="driftdriver")
        assert a.repo == "driftdriver"

    def test_create_actor_with_each_valid_class(self):
        for cls in ACTOR_CLASSES:
            a = Actor(id=f"id-{cls}", actor_class=cls, name=f"name-{cls}")
            assert a.actor_class == cls

    def test_create_actor_with_invalid_class_raises(self):
        with pytest.raises(ValueError, match="invalid actor_class"):
            Actor(id="bad", actor_class="robot", name="nope")


class TestActorSerialization:
    def test_actor_to_dict(self):
        a = Actor(id="x", actor_class="daemon", name="speedriftd", repo="myrepo")
        d = actor_to_dict(a)
        assert d == {
            "id": "x",
            "actor_class": "daemon",
            "name": "speedriftd",
            "repo": "myrepo",
        }

    def test_actor_from_dict(self):
        d = {"id": "y", "actor_class": "lane", "name": "qadrift", "repo": ""}
        a = actor_from_dict(d)
        assert a.id == "y"
        assert a.actor_class == "lane"
        assert a.name == "qadrift"

    def test_roundtrip(self):
        original = Actor(id="rt-1", actor_class="interactive", name="claude-code", repo="driftdriver")
        restored = actor_from_dict(actor_to_dict(original))
        assert restored == original

    def test_from_dict_invalid_class_raises(self):
        with pytest.raises(ValueError):
            actor_from_dict({"id": "z", "actor_class": "alien", "name": "et"})


class TestDefaultAuthority:
    def test_all_actor_classes_have_authority(self):
        for cls in ACTOR_CLASSES:
            assert cls in DEFAULT_AUTHORITY

    def test_human_has_all_operations(self):
        ops = DEFAULT_AUTHORITY["human"]
        assert ops == frozenset({"read", "claim", "create", "dispatch", "modify"})

    def test_interactive_cannot_dispatch(self):
        ops = DEFAULT_AUTHORITY["interactive"]
        assert "dispatch" not in ops
        assert ops == frozenset({"read", "claim", "create", "modify"})

    def test_worker_authority(self):
        assert DEFAULT_AUTHORITY["worker"] == frozenset({"read", "claim", "modify"})

    def test_daemon_authority(self):
        assert DEFAULT_AUTHORITY["daemon"] == frozenset({"read", "create", "dispatch"})

    def test_lane_cannot_dispatch(self):
        ops = DEFAULT_AUTHORITY["lane"]
        assert "dispatch" not in ops
        assert ops == frozenset({"read", "create"})

    def test_lane_cannot_claim(self):
        assert "claim" not in DEFAULT_AUTHORITY["lane"]

    def test_lane_cannot_modify(self):
        assert "modify" not in DEFAULT_AUTHORITY["lane"]


class TestActorClasses:
    def test_actor_classes_is_tuple(self):
        assert isinstance(ACTOR_CLASSES, tuple)

    def test_expected_classes(self):
        assert set(ACTOR_CLASSES) == {"human", "interactive", "worker", "daemon", "lane"}
