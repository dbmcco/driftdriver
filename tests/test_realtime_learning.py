# ABOUTME: Tests for real-time learning pipeline (replacing batch pending.jsonl flow)
# ABOUTME: Verifies that events record to lessons.db immediately at every lifecycle point

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


def _create_lessons_db(db_path: Path) -> None:
    """Create a lessons.db with the exact schema Lessons MCP uses."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_events (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            cli_tool TEXT NOT NULL,
            project TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            dedupe_key TEXT UNIQUE,
            timestamp TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS knowledge_entries (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            project TEXT,
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            source_session_ids TEXT DEFAULT '[]',
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


class TestRecordLearningImmediate(unittest.TestCase):
    """Tests for recording self-reflect learnings directly to lessons.db."""

    def test_record_learning_writes_to_db(self) -> None:
        """A Learning dataclass gets recorded to lessons.db immediately."""
        from driftdriver.reporting import record_learning_immediate
        from driftdriver.self_reflect import Learning

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            learning = Learning(
                learning_type="pattern",
                content="Tool 'Edit' called 5 times — possible loop",
                confidence="medium",
                source_task="task-42",
            )
            result = record_learning_immediate(
                learning,
                session_id="sess-1",
                project="myproject",
                db_path=db_path,
            )
            self.assertTrue(result)

            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT event_type, payload FROM session_events").fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "learning")
            payload = json.loads(rows[0][1])
            self.assertEqual(payload["learning_type"], "pattern")
            self.assertIn("loop", payload["content"])
            self.assertEqual(payload["source_task"], "task-42")

    def test_record_learning_missing_db_returns_false(self) -> None:
        """Returns False when db doesn't exist."""
        from driftdriver.reporting import record_learning_immediate
        from driftdriver.self_reflect import Learning

        learning = Learning(
            learning_type="gotcha",
            content="Large diff detected",
            confidence="high",
        )
        result = record_learning_immediate(
            learning,
            db_path=Path("/nonexistent/lessons.db"),
        )
        self.assertFalse(result)

    def test_record_learning_deduplicates(self) -> None:
        """Same learning recorded twice should not create duplicates."""
        from driftdriver.reporting import record_learning_immediate
        from driftdriver.self_reflect import Learning

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            learning = Learning(
                learning_type="anti_pattern",
                content="File edited 5 times in one task",
                confidence="medium",
                source_task="task-99",
            )
            r1 = record_learning_immediate(learning, session_id="s1", project="p", db_path=db_path)
            r2 = record_learning_immediate(learning, session_id="s1", project="p", db_path=db_path)
            self.assertTrue(r1)
            # Second insert should be deduplicated (not an error, just skipped)
            # We don't care if it returns True or False, just that we don't get two rows
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT * FROM session_events").fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)


class TestSelfReflectAndRecord(unittest.TestCase):
    """Tests for the integrated self-reflect + immediate record pipeline."""

    def test_self_reflect_and_record_all(self) -> None:
        """self_reflect_and_record runs extraction and records each learning immediately."""
        from driftdriver.reporting import self_reflect_and_record

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            # Events with a tool called many times (triggers pattern detection)
            events = [
                {"event": "pre_tool_use", "tool": "Edit", "tool_input": {"file_path": "/a.py"}},
                {"event": "pre_tool_use", "tool": "Edit", "tool_input": {"file_path": "/a.py"}},
                {"event": "pre_tool_use", "tool": "Edit", "tool_input": {"file_path": "/a.py"}},
                {"event": "pre_tool_use", "tool": "Edit", "tool_input": {"file_path": "/a.py"}},
                {"event": "pre_tool_use", "tool": "Edit", "tool_input": {"file_path": "/a.py"}},
            ]

            count = self_reflect_and_record(
                events=events,
                session_id="sess-rt",
                project="myproject",
                db_path=db_path,
            )
            self.assertGreater(count, 0)

            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT event_type, payload FROM session_events WHERE event_type = 'learning'"
            ).fetchall()
            conn.close()
            self.assertEqual(len(rows), count)

    def test_self_reflect_and_record_no_learnings(self) -> None:
        """When no learnings are extracted, returns 0 and writes nothing."""
        from driftdriver.reporting import self_reflect_and_record

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            count = self_reflect_and_record(
                events=[{"event": "pre_tool_use", "tool": "Read"}],
                session_id="sess-empty",
                project="myproject",
                db_path=db_path,
            )
            self.assertEqual(count, 0)

    def test_self_reflect_and_record_with_diff(self) -> None:
        """Diff-based learnings get recorded immediately too."""
        from driftdriver.reporting import self_reflect_and_record

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            # Large diff triggers a learning
            diff_lines = ["--- a/file.py", "+++ b/file.py"]
            diff_lines += [f"+line{i}" for i in range(300)]
            diff_text = "\n".join(diff_lines)

            count = self_reflect_and_record(
                diff_text=diff_text,
                session_id="sess-diff",
                project="myproject",
                db_path=db_path,
            )
            self.assertGreater(count, 0)


class TestCommonShUsesImmediateRecording(unittest.TestCase):
    """Tests that common.sh has been updated for real-time recording."""

    def test_common_sh_lessons_mcp_uses_driftdriver_record_event(self) -> None:
        """lessons_mcp() in common.sh should call driftdriver record-event for record operations."""
        common = Path(__file__).parent.parent / "driftdriver" / "templates" / "handlers" / "common.sh"
        if not common.exists():
            self.skipTest("common.sh not found")
        content = common.read_text()
        self.assertIn("driftdriver", content, "common.sh should invoke driftdriver for immediate recording")
        self.assertIn("record-event", content, "common.sh should use record-event subcommand")

    def test_common_sh_no_longer_appends_to_pending_jsonl(self) -> None:
        """lessons_mcp() should not write to pending.jsonl anymore."""
        common = Path(__file__).parent.parent / "driftdriver" / "templates" / "handlers" / "common.sh"
        if not common.exists():
            self.skipTest("common.sh not found")
        content = common.read_text()
        self.assertNotIn('>> "$events_dir/pending.jsonl"', content,
                         "common.sh should not append to pending.jsonl")


class TestHandlersUseImmediateRecording(unittest.TestCase):
    """Tests that handler scripts use driftdriver record-event for immediate recording."""

    HANDLERS_DIR = Path(__file__).parent.parent / "driftdriver" / "templates" / "handlers"

    def test_agent_error_uses_immediate_recording(self) -> None:
        """agent-error.sh should use driftdriver record-event."""
        path = self.HANDLERS_DIR / "agent-error.sh"
        if not path.exists():
            self.skipTest("agent-error.sh not found")
        content = path.read_text()
        self.assertIn("driftdriver", content)
        self.assertIn("record-event", content)

    def test_agent_stop_uses_immediate_recording(self) -> None:
        """agent-stop.sh should use driftdriver record-event."""
        path = self.HANDLERS_DIR / "agent-stop.sh"
        if not path.exists():
            self.skipTest("agent-stop.sh not found")
        content = path.read_text()
        self.assertIn("driftdriver", content)
        self.assertIn("record-event", content)

    def test_pre_compact_no_flush(self) -> None:
        """pre-compact.sh should not call flush_learnings since recording is now real-time."""
        path = self.HANDLERS_DIR / "pre-compact.sh"
        if not path.exists():
            self.skipTest("pre-compact.sh not found")
        content = path.read_text()
        self.assertNotIn("flush_learnings", content,
                         "pre-compact.sh should not flush since events are recorded in real-time")


class TestFlushPendingStillWorks(unittest.TestCase):
    """flush_pending_events remains as backward-compatible fallback."""

    def test_flush_pending_events_still_functional(self) -> None:
        """flush_pending_events still works for any stragglers."""
        from driftdriver.reporting import flush_pending_events

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            events_dir = wg_dir / ".lessons-events"
            events_dir.mkdir(parents=True)
            (events_dir / "pending.jsonl").write_text(
                json.dumps({"ts": "2025-01-01T00:00:00Z", "tool": "record_event",
                            "args": {"event_type": "observation", "payload": {"msg": "straggler"}}}) + "\n"
            )

            result = flush_pending_events(wg_dir, "sess-compat", "myproject", db_path)
            self.assertEqual(result.events_read, 1)
            self.assertEqual(result.events_written, 1)


class TestGenerateReportWithoutPending(unittest.TestCase):
    """Session report generation works when there's no pending.jsonl (real-time path)."""

    def test_generate_report_no_pending_file(self) -> None:
        """generate_session_report works when pending.jsonl doesn't exist."""
        from driftdriver.reporting import ReportingConfig, generate_session_report

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            # No pending.jsonl, no output/, no chat/ — pure real-time scenario
            config = ReportingConfig(central_repo="", auto_report=True, include_knowledge=True, db_path=db_path)
            report = generate_session_report(wg_dir, "sess-rt", "myproject", config)

            self.assertEqual(report.flush_result.events_read, 0)
            self.assertEqual(report.flush_result.events_written, 0)
            self.assertEqual(report.flush_result.errors, 0)
            self.assertEqual(report.session_id, "sess-rt")


if __name__ == "__main__":
    unittest.main()
