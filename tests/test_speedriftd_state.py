# ABOUTME: Tests for speedriftd state management I/O layer
# ABOUTME: Covers runtime paths, control state, snapshots, JSON read/write helpers

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from driftdriver.speedriftd_state import (
    CONTROL_MODES,
    _append_jsonl,
    _default_control,
    _iso_now,
    _normalize_control_state,
    _parse_iso_timestamp,
    _read_json,
    _safe_slug,
    _write_json,
    load_control_state,
    load_runtime_snapshot,
    runtime_paths,
    write_control_state,
    write_runtime_snapshot,
)


def _scaffold_repo(tmp_path: Path) -> Path:
    """Create minimal .workgraph scaffold so find_workgraph_dir succeeds."""
    repo = tmp_path / "test-repo"
    wg = repo / ".workgraph"
    wg.mkdir(parents=True)
    (wg / "graph.jsonl").write_text(
        json.dumps({"kind": "task", "id": "t1", "title": "T1", "status": "open"}) + "\n"
    )
    return repo


# ---------------------------------------------------------------------------
# _iso_now
# ---------------------------------------------------------------------------


class TestIsoNow:
    def test_returns_valid_iso_string(self) -> None:
        result = _iso_now()
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    def test_with_explicit_timestamp(self) -> None:
        ts = 1700000000.0
        result = _iso_now(ts)
        dt = datetime.fromisoformat(result)
        assert abs(dt.timestamp() - ts) < 1.0

    def test_none_uses_current_time(self) -> None:
        before = time.time()
        result = _iso_now()
        after = time.time()
        dt = datetime.fromisoformat(result)
        # Allow small float rounding: _iso_now truncates to microseconds
        assert before - 0.001 <= dt.timestamp() <= after + 1.0


# ---------------------------------------------------------------------------
# _safe_slug
# ---------------------------------------------------------------------------


class TestSafeSlug:
    def test_alphanumeric_passthrough(self) -> None:
        assert _safe_slug("hello-world") == "hello-world"

    def test_spaces_become_hyphens(self) -> None:
        assert _safe_slug("hello world") == "hello-world"

    def test_special_chars_removed(self) -> None:
        assert _safe_slug("a@b#c$d") == "a-b-c-d"

    def test_empty_string_returns_unknown(self) -> None:
        assert _safe_slug("") == "unknown"

    def test_none_returns_unknown(self) -> None:
        assert _safe_slug(None) == "unknown"

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert _safe_slug("---hello---") == "hello"

    def test_dots_and_underscores_preserved(self) -> None:
        assert _safe_slug("file.name_v2") == "file.name_v2"


# ---------------------------------------------------------------------------
# _parse_iso_timestamp
# ---------------------------------------------------------------------------


class TestParseIsoTimestamp:
    def test_valid_iso_string(self) -> None:
        iso = "2024-01-15T12:00:00+00:00"
        result = _parse_iso_timestamp(iso)
        assert result > 0.0
        assert isinstance(result, float)

    def test_z_suffix_handled(self) -> None:
        iso = "2024-01-15T12:00:00Z"
        result = _parse_iso_timestamp(iso)
        assert result > 0.0

    def test_empty_string_returns_zero(self) -> None:
        assert _parse_iso_timestamp("") == 0.0

    def test_none_returns_zero(self) -> None:
        assert _parse_iso_timestamp(None) == 0.0

    def test_garbage_returns_zero(self) -> None:
        assert _parse_iso_timestamp("not-a-date") == 0.0

    def test_round_trips_with_iso_now(self) -> None:
        ts = time.time()
        iso = _iso_now(ts)
        parsed = _parse_iso_timestamp(iso)
        assert abs(parsed - ts) < 1.0


# ---------------------------------------------------------------------------
# _read_json / _write_json
# ---------------------------------------------------------------------------


class TestReadJson:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path) -> None:
        result = _read_json(tmp_path / "nonexistent.json")
        assert result == {}

    def test_valid_json_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "data.json"
        f.write_text(json.dumps({"key": "value"}))
        result = _read_json(f)
        assert result == {"key": "value"}

    def test_json_array_returns_empty_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "data.json"
        f.write_text(json.dumps([1, 2, 3]))
        result = _read_json(f)
        assert result == {}

    def test_invalid_json_returns_empty_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "data.json"
        f.write_text("not valid json {{{")
        result = _read_json(f)
        assert result == {}

    def test_empty_file_returns_empty_dict(self, tmp_path: Path) -> None:
        f = tmp_path / "data.json"
        f.write_text("")
        result = _read_json(f)
        assert result == {}


class TestWriteJson:
    def test_creates_file_with_valid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        payload = {"hello": "world", "n": 42}
        _write_json(f, payload)
        assert f.exists()
        loaded = json.loads(f.read_text())
        assert loaded == payload

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        f = tmp_path / "a" / "b" / "c" / "out.json"
        _write_json(f, {"nested": True})
        assert f.exists()
        assert json.loads(f.read_text()) == {"nested": True}

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        _write_json(f, {"version": 1})
        _write_json(f, {"version": 2})
        loaded = json.loads(f.read_text())
        assert loaded == {"version": 2}

    def test_atomic_write_no_tmp_left_behind(self, tmp_path: Path) -> None:
        f = tmp_path / "out.json"
        _write_json(f, {"data": True})
        tmp_file = f.with_suffix(".json.tmp")
        assert not tmp_file.exists()

    def test_round_trip_with_read(self, tmp_path: Path) -> None:
        f = tmp_path / "roundtrip.json"
        payload = {"a": 1, "b": [2, 3], "c": {"d": True}}
        _write_json(f, payload)
        result = _read_json(f)
        assert result == payload


# ---------------------------------------------------------------------------
# _append_jsonl
# ---------------------------------------------------------------------------


class TestAppendJsonl:
    def test_creates_file_if_missing(self, tmp_path: Path) -> None:
        f = tmp_path / "log.jsonl"
        _append_jsonl(f, {"event": "first"})
        assert f.exists()
        lines = f.read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"event": "first"}

    def test_appends_multiple_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "log.jsonl"
        _append_jsonl(f, {"n": 1})
        _append_jsonl(f, {"n": 2})
        _append_jsonl(f, {"n": 3})
        lines = f.read_text().strip().splitlines()
        assert len(lines) == 3
        assert [json.loads(l)["n"] for l in lines] == [1, 2, 3]

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        f = tmp_path / "deep" / "dir" / "log.jsonl"
        _append_jsonl(f, {"ok": True})
        assert f.exists()

    def test_each_line_is_valid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "log.jsonl"
        for i in range(5):
            _append_jsonl(f, {"i": i, "data": f"item-{i}"})
        for line in f.read_text().strip().splitlines():
            parsed = json.loads(line)
            assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# runtime_paths
# ---------------------------------------------------------------------------


class TestRuntimePaths:
    def test_returns_expected_keys(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        paths = runtime_paths(repo)
        expected_keys = {
            "wg_dir", "dir", "current", "workers", "stalls",
            "leases", "control", "events_dir", "heartbeats_dir", "results_dir",
        }
        assert set(paths.keys()) == expected_keys

    def test_paths_under_runtime_dir(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        paths = runtime_paths(repo)
        base = paths["dir"]
        assert str(base).endswith("service/runtime")
        for key in ("current", "workers", "stalls", "leases", "control"):
            assert paths[key].parent == base

    def test_wg_dir_is_workgraph(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        paths = runtime_paths(repo)
        assert paths["wg_dir"].name == ".workgraph"

    def test_subdirs_under_runtime(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        paths = runtime_paths(repo)
        base = paths["dir"]
        assert paths["events_dir"] == base / "events"
        assert paths["heartbeats_dir"] == base / "heartbeats"
        assert paths["results_dir"] == base / "results"


# ---------------------------------------------------------------------------
# _default_control
# ---------------------------------------------------------------------------


class TestDefaultControl:
    def test_has_required_keys(self) -> None:
        ctrl = _default_control("myrepo", {})
        required = {
            "repo", "updated_at", "mode", "dispatch_enabled",
            "interactive_service_start", "lease_owner", "lease_acquired_at",
            "lease_ttl_seconds", "lease_expires_at", "lease_active",
            "source", "reason",
        }
        assert required.issubset(set(ctrl.keys()))

    def test_default_mode_is_observe(self) -> None:
        ctrl = _default_control("myrepo", {})
        assert ctrl["mode"] == "observe"
        assert ctrl["dispatch_enabled"] is False

    def test_custom_default_mode(self) -> None:
        ctrl = _default_control("myrepo", {"default_mode": "autonomous"})
        assert ctrl["mode"] == "autonomous"
        assert ctrl["dispatch_enabled"] is True

    def test_invalid_mode_falls_back_to_observe(self) -> None:
        ctrl = _default_control("myrepo", {"default_mode": "invalid-mode"})
        assert ctrl["mode"] == "observe"

    def test_repo_name_set(self) -> None:
        ctrl = _default_control("my-project", {})
        assert ctrl["repo"] == "my-project"

    def test_lease_defaults_inactive(self) -> None:
        ctrl = _default_control("repo", {})
        assert ctrl["lease_owner"] == ""
        assert ctrl["lease_active"] is False

    def test_lease_ttl_from_config(self) -> None:
        ctrl = _default_control("repo", {"default_lease_ttl_seconds": 300})
        assert ctrl["lease_ttl_seconds"] == 300

    def test_supervise_enables_dispatch(self) -> None:
        ctrl = _default_control("repo", {"default_mode": "supervise"})
        assert ctrl["dispatch_enabled"] is True
        assert ctrl["interactive_service_start"] is True

    def test_manual_disables_dispatch(self) -> None:
        ctrl = _default_control("repo", {"default_mode": "manual"})
        assert ctrl["dispatch_enabled"] is False


# ---------------------------------------------------------------------------
# _normalize_control_state
# ---------------------------------------------------------------------------


class TestNormalizeControlState:
    def test_fills_defaults_for_empty_dict(self) -> None:
        result = _normalize_control_state({}, repo_name="test", cfg={})
        assert result["mode"] == "observe"
        assert result["repo"] == "test"
        assert "updated_at" in result

    def test_preserves_valid_mode(self) -> None:
        result = _normalize_control_state(
            {"mode": "autonomous"}, repo_name="test", cfg={}
        )
        assert result["mode"] == "autonomous"
        assert result["dispatch_enabled"] is True

    def test_invalid_mode_passthrough_quirk(self) -> None:
        # NOTE: _normalize_control_state has a quirk where an invalid mode
        # passed in raw dict survives because the fallback (line 109) reads
        # back from control["mode"] which was already overwritten by update().
        # The _default_control guard catches invalid *config* defaults, but
        # _normalize_control_state does not re-validate after merging raw.
        result = _normalize_control_state(
            {"mode": "bogus"}, repo_name="test", cfg={}
        )
        # Dispatch stays false because "bogus" is not in the dispatch set
        assert result["dispatch_enabled"] is False

    def test_lease_owner_activates_lease(self) -> None:
        result = _normalize_control_state(
            {"lease_owner": "agent-1", "lease_ttl_seconds": 0},
            repo_name="test", cfg={},
        )
        assert result["lease_active"] is True

    def test_lease_with_ttl_and_future_expiry(self) -> None:
        future = _iso_now(time.time() + 3600)
        result = _normalize_control_state(
            {
                "lease_owner": "agent-1",
                "lease_ttl_seconds": 3600,
                "lease_expires_at": future,
            },
            repo_name="test", cfg={},
        )
        assert result["lease_active"] is True

    def test_lease_with_ttl_and_past_expiry(self) -> None:
        past = _iso_now(time.time() - 3600)
        result = _normalize_control_state(
            {
                "lease_owner": "agent-1",
                "lease_ttl_seconds": 3600,
                "lease_expires_at": past,
            },
            repo_name="test", cfg={},
        )
        assert result["lease_active"] is False

    def test_empty_lease_owner_clears_lease_fields(self) -> None:
        result = _normalize_control_state(
            {"lease_owner": "", "lease_acquired_at": "2024-01-01T00:00:00+00:00"},
            repo_name="test", cfg={},
        )
        assert result["lease_active"] is False
        assert result["lease_acquired_at"] == ""
        assert result["lease_expires_at"] == ""

    def test_repo_name_always_set(self) -> None:
        result = _normalize_control_state(
            {"repo": "wrong-name"}, repo_name="correct-name", cfg={}
        )
        assert result["repo"] == "correct-name"

    def test_dispatch_enabled_matches_mode(self) -> None:
        for mode in CONTROL_MODES:
            result = _normalize_control_state(
                {"mode": mode}, repo_name="test", cfg={}
            )
            expected = mode in {"supervise", "autonomous"}
            assert result["dispatch_enabled"] is expected, f"mode={mode}"
            assert result["interactive_service_start"] is expected

    def test_negative_ttl_clamped_to_zero(self) -> None:
        result = _normalize_control_state(
            {"lease_ttl_seconds": -100}, repo_name="test", cfg={}
        )
        assert result["lease_ttl_seconds"] == 0


# ---------------------------------------------------------------------------
# load_control_state / write_control_state (integration with disk)
# ---------------------------------------------------------------------------


class TestLoadControlState:
    def test_returns_defaults_when_no_file(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        state = load_control_state(repo)
        assert state["mode"] == "observe"
        assert state["repo"] == "test-repo"
        assert state["dispatch_enabled"] is False

    def test_round_trips_with_write(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        written = write_control_state(
            repo, mode="supervise", lease_owner="agent-x", reason="testing"
        )
        loaded = load_control_state(repo)
        assert loaded["mode"] == "supervise"
        assert loaded["lease_owner"] == "agent-x"
        assert loaded["dispatch_enabled"] is True

    def test_write_with_release_lease(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        write_control_state(repo, mode="supervise", lease_owner="agent-x")
        released = write_control_state(repo, release_lease=True, reason="done")
        assert released["lease_owner"] == ""
        assert released["lease_active"] is False

    def test_write_preserves_mode_when_not_specified(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        write_control_state(repo, mode="autonomous", lease_owner="bot")
        updated = write_control_state(repo, reason="just a reason update")
        assert updated["mode"] == "autonomous"

    def test_write_invalid_mode_ignored(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        write_control_state(repo, mode="supervise")
        result = write_control_state(repo, mode="bogus-mode")
        # Should keep the previous valid mode since "bogus-mode" is not in CONTROL_MODES
        assert result["mode"] == "supervise"

    def test_write_sets_source_and_reason(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        result = write_control_state(
            repo, mode="observe", source="test-harness", reason="test reason"
        )
        assert result["source"] == "test-harness"
        assert result["reason"] == "test reason"

    def test_write_with_lease_ttl(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        result = write_control_state(
            repo, lease_owner="agent-a", lease_ttl_seconds=600
        )
        assert result["lease_ttl_seconds"] == 600
        assert result["lease_active"] is True
        assert result["lease_expires_at"] != ""

    def test_write_lease_no_ttl_means_no_expiry(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        result = write_control_state(repo, lease_owner="agent-b")
        assert result["lease_active"] is True
        assert result["lease_expires_at"] == ""


# ---------------------------------------------------------------------------
# load_runtime_snapshot / write_runtime_snapshot
# ---------------------------------------------------------------------------


class TestRuntimeSnapshot:
    def test_load_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        snapshot = load_runtime_snapshot(repo)
        assert snapshot == {}

    def test_write_and_load_round_trip(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        snap = {
            "repo": "test-repo",
            "updated_at": _iso_now(),
            "daemon_state": "idle",
            "active_workers": [],
            "active_task_ids": [],
            "stalled_task_ids": [],
            "runtime_mix": [],
            "control": {"mode": "observe"},
        }
        write_runtime_snapshot(repo, snap)
        loaded = load_runtime_snapshot(repo)
        assert loaded["repo"] == "test-repo"
        assert loaded["daemon_state"] == "idle"

    def test_write_creates_runtime_dirs(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        snap = {
            "repo": "test-repo",
            "updated_at": _iso_now(),
            "active_workers": [],
            "stalled_task_ids": [],
            "terminal_workers": [],
        }
        write_runtime_snapshot(repo, snap)
        paths = runtime_paths(repo)
        assert paths["dir"].is_dir()
        assert paths["events_dir"].is_dir()
        assert paths["heartbeats_dir"].is_dir()
        assert paths["results_dir"].is_dir()

    def test_write_with_active_workers(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        snap = {
            "repo": "test-repo",
            "updated_at": _iso_now(),
            "cycle_id": "cycle-1",
            "active_workers": [
                {
                    "worker_id": "w1",
                    "task_id": "t1",
                    "runtime": "claude-code",
                    "state": "running",
                    "started_at": _iso_now(),
                    "last_heartbeat_at": _iso_now(),
                    "last_output_at": _iso_now(),
                    "event_count": 5,
                },
            ],
            "active_task_ids": ["t1"],
            "stalled_task_ids": [],
            "terminal_workers": [],
        }
        write_runtime_snapshot(repo, snap)
        paths = runtime_paths(repo)
        # Heartbeat file created for worker
        hb_file = paths["heartbeats_dir"] / "w1.json"
        assert hb_file.exists()
        hb = json.loads(hb_file.read_text())
        assert hb["worker_id"] == "w1"
        assert hb["task_id"] == "t1"
        # Workers JSONL appended
        workers_lines = paths["workers"].read_text().strip().splitlines()
        assert len(workers_lines) >= 1
        w_row = json.loads(workers_lines[-1])
        assert w_row["worker_id"] == "w1"
        assert w_row["event_type"] == "heartbeat"
        # Leases file created
        leases = json.loads(paths["leases"].read_text())
        assert len(leases["active_leases"]) == 1

    def test_write_with_stalled_tasks(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        snap = {
            "repo": "test-repo",
            "updated_at": _iso_now(),
            "cycle_id": "cycle-2",
            "active_workers": [],
            "stalled_task_ids": ["t-stalled-1", "t-stalled-2"],
            "terminal_workers": [],
            "next_action": "escalate",
        }
        write_runtime_snapshot(repo, snap)
        paths = runtime_paths(repo)
        stall_lines = paths["stalls"].read_text().strip().splitlines()
        assert len(stall_lines) == 2
        for line in stall_lines:
            row = json.loads(line)
            assert row["event_type"] == "stall_detected"
            assert row["task_id"] in ("t-stalled-1", "t-stalled-2")

    def test_write_with_terminal_workers(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        snap = {
            "repo": "test-repo",
            "updated_at": _iso_now(),
            "active_workers": [],
            "stalled_task_ids": [],
            "terminal_workers": [
                {
                    "worker_id": "w-done",
                    "task_id": "t-done",
                    "runtime": "claude-code",
                    "state": "done",
                },
                {
                    "worker_id": "w-fail",
                    "task_id": "t-fail",
                    "runtime": "claude-code",
                    "state": "failed",
                },
            ],
        }
        write_runtime_snapshot(repo, snap)
        paths = runtime_paths(repo)
        done_result = paths["results_dir"] / "w-done.json"
        fail_result = paths["results_dir"] / "w-fail.json"
        assert done_result.exists()
        assert fail_result.exists()
        assert json.loads(done_result.read_text())["terminal_state"] == "done"
        assert json.loads(fail_result.read_text())["terminal_state"] == "failed"

    def test_write_ignores_non_terminal_states(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        snap = {
            "repo": "test-repo",
            "updated_at": _iso_now(),
            "active_workers": [],
            "stalled_task_ids": [],
            "terminal_workers": [
                {
                    "worker_id": "w-running",
                    "task_id": "t-running",
                    "runtime": "claude-code",
                    "state": "running",
                },
            ],
        }
        write_runtime_snapshot(repo, snap)
        paths = runtime_paths(repo)
        assert not (paths["results_dir"] / "w-running.json").exists()

    def test_write_events_file_uses_date(self, tmp_path: Path) -> None:
        repo = _scaffold_repo(tmp_path)
        ts = "2025-03-01T12:00:00+00:00"
        snap = {
            "repo": "test-repo",
            "updated_at": ts,
            "active_workers": [],
            "stalled_task_ids": [],
            "terminal_workers": [],
        }
        write_runtime_snapshot(repo, snap)
        paths = runtime_paths(repo)
        event_file = paths["events_dir"] / "2025-03-01.jsonl"
        assert event_file.exists()
        lines = event_file.read_text().strip().splitlines()
        assert len(lines) >= 1
        last_event = json.loads(lines[-1])
        assert last_event["event_type"] == "repo_service_state"
