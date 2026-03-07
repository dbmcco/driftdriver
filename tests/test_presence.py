# ABOUTME: Tests for presence tracking subsystem.
# ABOUTME: Covers heartbeat writing, reading, removal, GC, and activity detection.

from __future__ import annotations

import datetime
import json
import time
from pathlib import Path

import pytest

from driftdriver.actor import Actor
from driftdriver.presence import (
    PresenceRecord,
    active_actors,
    gc_stale_presence,
    is_repo_active,
    presence_dir,
    read_all_presence,
    read_presence,
    remove_presence,
    write_heartbeat,
)


def _make_actor(id: str = "test-1", actor_class: str = "worker", name: str = "test-worker") -> Actor:
    return Actor(id=id, actor_class=actor_class, name=name, repo="testrepo")


class TestPresenceDir:
    def test_returns_correct_path(self, tmp_path: Path):
        assert presence_dir(tmp_path) == tmp_path / ".workgraph" / "presence"


class TestWriteHeartbeat:
    def test_creates_presence_file(self, tmp_path: Path):
        actor = _make_actor()
        rec = write_heartbeat(tmp_path, actor)
        fpath = presence_dir(tmp_path) / f"{actor.id}.json"
        assert fpath.exists()
        assert rec.actor == actor
        assert rec.status == "active"

    def test_updates_existing_heartbeat(self, tmp_path: Path):
        actor = _make_actor()
        rec1 = write_heartbeat(tmp_path, actor)
        started = rec1.started_at
        first_hb = rec1.last_heartbeat

        # Small delay so timestamps differ
        time.sleep(0.01)
        rec2 = write_heartbeat(tmp_path, actor, current_task="task-42")

        assert rec2.started_at == started, "started_at must not change on update"
        assert rec2.last_heartbeat >= first_hb
        assert rec2.current_task == "task-42"

    def test_custom_status(self, tmp_path: Path):
        actor = _make_actor()
        rec = write_heartbeat(tmp_path, actor, status="stopping")
        assert rec.status == "stopping"

    def test_file_content_is_valid_json(self, tmp_path: Path):
        actor = _make_actor()
        write_heartbeat(tmp_path, actor)
        fpath = presence_dir(tmp_path) / f"{actor.id}.json"
        data = json.loads(fpath.read_text())
        assert data["actor"]["id"] == actor.id
        assert "started_at" in data
        assert "last_heartbeat" in data


class TestReadPresence:
    def test_read_all_presence_returns_all(self, tmp_path: Path):
        a1 = _make_actor(id="a1", name="worker-1")
        a2 = _make_actor(id="a2", name="worker-2")
        write_heartbeat(tmp_path, a1)
        write_heartbeat(tmp_path, a2)
        records = read_all_presence(tmp_path)
        ids = {r.actor.id for r in records}
        assert ids == {"a1", "a2"}

    def test_read_all_presence_empty_dir(self, tmp_path: Path):
        assert read_all_presence(tmp_path) == []

    def test_read_presence_specific_actor(self, tmp_path: Path):
        actor = _make_actor(id="specific-1")
        write_heartbeat(tmp_path, actor, current_task="task-99")
        rec = read_presence(tmp_path, "specific-1")
        assert rec is not None
        assert rec.actor.id == "specific-1"
        assert rec.current_task == "task-99"

    def test_read_presence_missing_actor(self, tmp_path: Path):
        assert read_presence(tmp_path, "nonexistent") is None


class TestRemovePresence:
    def test_remove_existing(self, tmp_path: Path):
        actor = _make_actor(id="rm-1")
        write_heartbeat(tmp_path, actor)
        assert remove_presence(tmp_path, "rm-1") is True
        assert read_presence(tmp_path, "rm-1") is None

    def test_remove_nonexistent(self, tmp_path: Path):
        assert remove_presence(tmp_path, "ghost") is False


class TestGcStalePresence:
    def _write_stale_record(self, tmp_path: Path, actor_id: str, age_seconds: int):
        """Write a presence file with an artificially old last_heartbeat."""
        actor = _make_actor(id=actor_id, name=f"stale-{actor_id}")
        pdir = presence_dir(tmp_path)
        pdir.mkdir(parents=True, exist_ok=True)
        old_ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_seconds)
        data = {
            "actor": {"id": actor.id, "actor_class": actor.actor_class, "name": actor.name, "repo": actor.repo},
            "started_at": old_ts.isoformat(),
            "last_heartbeat": old_ts.isoformat(),
            "current_task": "",
            "status": "active",
        }
        (pdir / f"{actor_id}.json").write_text(json.dumps(data))

    def test_removes_old_records(self, tmp_path: Path):
        self._write_stale_record(tmp_path, "old-1", age_seconds=700)
        self._write_stale_record(tmp_path, "old-2", age_seconds=800)
        removed = gc_stale_presence(tmp_path, max_age_seconds=600)
        assert removed == 2
        assert read_all_presence(tmp_path) == []

    def test_keeps_fresh_records(self, tmp_path: Path):
        actor = _make_actor(id="fresh-1")
        write_heartbeat(tmp_path, actor)
        removed = gc_stale_presence(tmp_path, max_age_seconds=600)
        assert removed == 0
        assert len(read_all_presence(tmp_path)) == 1

    def test_mixed_stale_and_fresh(self, tmp_path: Path):
        self._write_stale_record(tmp_path, "stale-1", age_seconds=700)
        actor = _make_actor(id="fresh-1")
        write_heartbeat(tmp_path, actor)
        removed = gc_stale_presence(tmp_path, max_age_seconds=600)
        assert removed == 1
        records = read_all_presence(tmp_path)
        assert len(records) == 1
        assert records[0].actor.id == "fresh-1"


class TestIsRepoActive:
    def test_active_when_fresh_presence(self, tmp_path: Path):
        actor = _make_actor()
        write_heartbeat(tmp_path, actor)
        assert is_repo_active(tmp_path) is True

    def test_inactive_when_no_presence(self, tmp_path: Path):
        assert is_repo_active(tmp_path) is False

    def test_inactive_when_all_stale(self, tmp_path: Path):
        pdir = presence_dir(tmp_path)
        pdir.mkdir(parents=True, exist_ok=True)
        old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=700)).isoformat()
        data = {
            "actor": {"id": "old", "actor_class": "worker", "name": "old", "repo": ""},
            "started_at": old_ts,
            "last_heartbeat": old_ts,
            "current_task": "",
            "status": "active",
        }
        (pdir / "old.json").write_text(json.dumps(data))
        assert is_repo_active(tmp_path, max_age_seconds=600) is False


class TestActiveActors:
    def test_filters_stale(self, tmp_path: Path):
        # Write a fresh actor
        fresh = _make_actor(id="fresh")
        write_heartbeat(tmp_path, fresh)

        # Write a stale actor directly
        pdir = presence_dir(tmp_path)
        old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=700)).isoformat()
        stale_data = {
            "actor": {"id": "stale", "actor_class": "worker", "name": "stale", "repo": ""},
            "started_at": old_ts,
            "last_heartbeat": old_ts,
            "current_task": "",
            "status": "active",
        }
        (pdir / "stale.json").write_text(json.dumps(stale_data))

        result = active_actors(tmp_path, max_age_seconds=600)
        assert len(result) == 1
        assert result[0].actor.id == "fresh"

    def test_returns_empty_when_no_presence(self, tmp_path: Path):
        assert active_actors(tmp_path) == []
