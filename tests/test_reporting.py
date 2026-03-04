# ABOUTME: Tests for the reporting module that closes the speedrift learning loop
# ABOUTME: Uses real temp files and SQLite DBs (no mocks) per TDD principles

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
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


def _write_pending_events(events_dir: Path, events: list[dict]) -> Path:
    """Write events to pending.jsonl and return the file path."""
    events_dir.mkdir(parents=True, exist_ok=True)
    pending = events_dir / "pending.jsonl"
    with open(pending, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return pending


class TestLoadReportingConfig(unittest.TestCase):
    def test_load_reporting_config_defaults(self) -> None:
        """No [reporting] section in policy → sensible defaults."""
        from driftdriver.reporting import load_reporting_config

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            config = load_reporting_config(wg_dir)
            self.assertEqual(config.central_repo, "")
            self.assertTrue(config.auto_report)
            self.assertTrue(config.include_knowledge)

    def test_load_reporting_config_from_toml(self) -> None:
        """Parses all three reporting fields from drift-policy.toml."""
        from driftdriver.reporting import load_reporting_config

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            (wg_dir / "drift-policy.toml").write_text(
                'schema = 1\nmode = "redirect"\norder = ["coredrift"]\n\n'
                "[recursion]\ncooldown_seconds = 1800\nmax_auto_actions_per_hour = 2\n"
                "require_new_evidence = true\nmax_auto_depth = 2\n\n"
                "[contracts]\nauto_ensure = true\n\n"
                "[updates]\nenabled = true\ncheck_interval_seconds = 21600\n"
                "create_followup = false\n\n"
                "[loop_safety]\nmax_redrift_depth = 2\n"
                "max_ready_drift_followups = 20\nblock_followup_creation = true\n\n"
                "[reporting]\n"
                'central_repo = "/tmp/central"\n'
                "auto_report = false\n"
                "include_knowledge = false\n",
                encoding="utf-8",
            )
            config = load_reporting_config(wg_dir)
            self.assertEqual(config.central_repo, "/tmp/central")
            self.assertFalse(config.auto_report)
            self.assertFalse(config.include_knowledge)


class TestFlushPendingEvents(unittest.TestCase):
    def test_flush_pending_events_writes_to_db(self) -> None:
        """Reads pending.jsonl, writes rows to session_events table."""
        from driftdriver.reporting import flush_pending_events

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            events = [
                {"ts": "2025-01-01T00:00:00Z", "tool": "record_event", "args": {"event_type": "observation", "payload": {"msg": "hello"}}},
                {"ts": "2025-01-01T00:01:00Z", "tool": "record_event", "args": {"event_type": "decision", "payload": {"chose": "A"}}},
            ]
            _write_pending_events(wg_dir / ".lessons-events", events)

            result = flush_pending_events(wg_dir, "sess-123", "myproject", db_path)
            self.assertEqual(result.events_read, 2)
            self.assertEqual(result.events_written, 2)
            self.assertEqual(result.errors, 0)

            # Verify rows in DB
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT * FROM session_events").fetchall()
            conn.close()
            self.assertEqual(len(rows), 2)

            # Verify pending.jsonl was renamed
            self.assertFalse((wg_dir / ".lessons-events" / "pending.jsonl").exists())
            flushed = list((wg_dir / ".lessons-events").glob("flushed-*.jsonl"))
            self.assertEqual(len(flushed), 1)

    def test_flush_pending_events_deduplicates(self) -> None:
        """Same event written twice → only one row in DB."""
        from driftdriver.reporting import flush_pending_events

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            event = {"ts": "2025-01-01T00:00:00Z", "tool": "record_event", "args": {"event_type": "observation", "payload": {"msg": "dup"}}}
            _write_pending_events(wg_dir / ".lessons-events", [event, event])

            result = flush_pending_events(wg_dir, "sess-123", "myproject", db_path)
            self.assertEqual(result.events_read, 2)
            self.assertEqual(result.duplicates_skipped, 1)

            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT * FROM session_events").fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)

    def test_flush_pending_events_pretty_printed_json(self) -> None:
        """Pretty-printed JSON (from jq -n) is parsed correctly."""
        from driftdriver.reporting import flush_pending_events

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            events_dir = wg_dir / ".lessons-events"
            events_dir.mkdir(parents=True)
            # Write pretty-printed JSON like common.sh's lessons_mcp() does
            (events_dir / "pending.jsonl").write_text(
                '{\n  "ts": "2025-01-01T00:00:00Z",\n  "tool": "record_event",\n'
                '  "args": {\n    "event_type": "observation",\n    "payload": {"msg": "pretty"}\n  }\n}\n'
                '{\n  "ts": "2025-01-01T00:01:00Z",\n  "tool": "search_knowledge",\n'
                '  "args": {\n    "query": "test"\n  }\n}\n'
            )

            result = flush_pending_events(wg_dir, "sess-pp", "myproject", db_path)
            self.assertEqual(result.events_read, 2)
            self.assertEqual(result.events_written, 2)
            self.assertEqual(result.errors, 0)

    def test_flush_pending_events_empty_file(self) -> None:
        """Empty pending.jsonl → no errors, no rows."""
        from driftdriver.reporting import flush_pending_events

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            events_dir = wg_dir / ".lessons-events"
            events_dir.mkdir(parents=True)
            (events_dir / "pending.jsonl").write_text("")

            result = flush_pending_events(wg_dir, "sess-123", "myproject", db_path)
            self.assertEqual(result.events_read, 0)
            self.assertEqual(result.events_written, 0)

    def test_flush_pending_events_no_file(self) -> None:
        """Missing pending.jsonl → graceful no-op."""
        from driftdriver.reporting import flush_pending_events

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            result = flush_pending_events(wg_dir, "sess-123", "myproject", db_path)
            self.assertEqual(result.events_read, 0)
            self.assertEqual(result.events_written, 0)
            self.assertEqual(result.errors, 0)


class TestExportKnowledge(unittest.TestCase):
    def test_export_knowledge_writes_jsonl(self) -> None:
        """Seeds knowledge_entries table, exports to knowledge.jsonl."""
        from driftdriver.reporting import export_knowledge

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            wg_dir.mkdir(parents=True, exist_ok=True)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            # Seed knowledge entries
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO knowledge_entries (id, category, project, content, confidence, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("k1", "pattern", "myproject", "Use dataclasses for DTOs", 0.8, "2025-01-01", "2025-01-01"),
            )
            conn.execute(
                "INSERT INTO knowledge_entries (id, category, project, content, confidence, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("k2", "gotcha", "myproject", "SQLite WAL mode needs closing", 0.6, "2025-01-01", "2025-01-01"),
            )
            conn.commit()
            conn.close()

            count = export_knowledge(db_path, "myproject", wg_dir)
            self.assertEqual(count, 2)

            kb_path = wg_dir / "knowledge.jsonl"
            self.assertTrue(kb_path.exists())
            lines = [json.loads(l) for l in kb_path.read_text().strip().splitlines()]
            self.assertEqual(len(lines), 2)
            # Verify KnowledgeFact shape
            self.assertIn("fact_id", lines[0])
            self.assertIn("fact_type", lines[0])
            self.assertIn("content", lines[0])
            self.assertIn("confidence", lines[0])

    def test_export_knowledge_empty_db(self) -> None:
        """No knowledge entries → no file created."""
        from driftdriver.reporting import export_knowledge

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            count = export_knowledge(db_path, "myproject", wg_dir)
            self.assertEqual(count, 0)
            self.assertFalse((wg_dir / "knowledge.jsonl").exists())


class TestPushToCentral(unittest.TestCase):
    def test_push_to_central_creates_directory(self) -> None:
        """Copies report + knowledge.jsonl to central repo."""
        from driftdriver.reporting import (
            FlushResult,
            ReportingConfig,
            SessionReport,
            format_report_markdown,
            push_to_central,
        )

        with tempfile.TemporaryDirectory() as td:
            central = Path(td) / "central"
            wg_dir = Path(td) / "project" / ".workgraph"
            wg_dir.mkdir(parents=True)
            (wg_dir / "knowledge.jsonl").write_text('{"fact_id":"k1","content":"test"}\n')

            config = ReportingConfig(
                central_repo=str(central),
                auto_report=True,
                include_knowledge=True,
            )
            report = SessionReport(
                session_id="sess-1",
                project="myproject",
                timestamp="2025-01-01T00:00:00Z",
                flush_result=FlushResult(events_read=2, events_written=2, duplicates_skipped=0, errors=0),
                knowledge_exported=1,
                pushed_to_central=False,
            )

            pushed = push_to_central(report, wg_dir, config)
            self.assertTrue(pushed)

            # Verify files exist in central
            report_dirs = list(central.glob("reports/myproject/*"))
            self.assertEqual(len(report_dirs), 1)
            report_dir = report_dirs[0]
            self.assertTrue((report_dir / "report.md").exists())
            self.assertTrue((report_dir / "knowledge.jsonl").exists())

    def test_push_to_central_no_config(self) -> None:
        """Empty central_repo → returns False."""
        from driftdriver.reporting import (
            FlushResult,
            ReportingConfig,
            SessionReport,
            push_to_central,
        )

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            config = ReportingConfig(central_repo="", auto_report=True, include_knowledge=True)
            report = SessionReport(
                session_id="sess-1",
                project="myproject",
                timestamp="2025-01-01T00:00:00Z",
                flush_result=FlushResult(events_read=0, events_written=0, duplicates_skipped=0, errors=0),
                knowledge_exported=0,
                pushed_to_central=False,
            )
            pushed = push_to_central(report, wg_dir, config)
            self.assertFalse(pushed)


class TestGenerateSessionReport(unittest.TestCase):
    def test_generate_session_report_full_pipeline(self) -> None:
        """End-to-end: pending events → flush → export → report."""
        from driftdriver.reporting import generate_session_report, ReportingConfig

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            # Seed pending events
            events = [
                {"ts": "2025-01-01T00:00:00Z", "tool": "record_event", "args": {"event_type": "observation", "payload": {"msg": "test"}}},
            ]
            _write_pending_events(wg_dir / ".lessons-events", events)

            # Seed knowledge
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "INSERT INTO knowledge_entries (id, category, project, content, confidence, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("k1", "pattern", "myproject", "Test pattern", 0.7, "2025-01-01", "2025-01-01"),
            )
            conn.commit()
            conn.close()

            config = ReportingConfig(central_repo="", auto_report=True, include_knowledge=True, db_path=db_path)
            report = generate_session_report(wg_dir, "sess-1", "myproject", config)

            self.assertEqual(report.session_id, "sess-1")
            self.assertEqual(report.project, "myproject")
            self.assertEqual(report.flush_result.events_read, 1)
            self.assertEqual(report.flush_result.events_written, 1)
            self.assertEqual(report.knowledge_exported, 1)
            self.assertFalse(report.pushed_to_central)


def _create_drift_outputs(wg_dir: Path, entries: list[tuple[str, list[dict]]]) -> None:
    """Create drift output directories with log.json files.

    entries: list of (task_name, log_entries) where log_entries is [{timestamp, message}]
    """
    output_dir = wg_dir / "output"
    for task_name, log_entries in entries:
        task_dir = output_dir / task_name
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "log.json").write_text(json.dumps(log_entries))
        (task_dir / "artifacts.json").write_text("[]")


class TestIngestDriftOutputs(unittest.TestCase):
    def test_ingest_drift_outputs_captures_findings(self) -> None:
        """Drift outputs with findings are written to DB as events."""
        from driftdriver.reporting import ingest_drift_outputs

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            _create_drift_outputs(wg_dir, [
                ("task-alpha", [
                    {"timestamp": "2025-01-01T00:00:00Z", "message": "Task claimed"},
                    {"timestamp": "2025-01-01T00:01:00Z", "message": "Coredrift: yellow (churn_files, hardening_in_core) | next: Split the task"},
                    {"timestamp": "2025-01-01T00:02:00Z", "message": "Task marked as done"},
                ]),
                ("task-beta", [
                    {"timestamp": "2025-01-01T00:00:00Z", "message": "Coredrift: OK (no findings)"},
                    {"timestamp": "2025-01-01T00:01:00Z", "message": "Task marked as done"},
                ]),
            ])

            result = ingest_drift_outputs(wg_dir, "sess-drift", "myproject", db_path)
            self.assertEqual(result.events_read, 1)  # only the yellow finding
            self.assertEqual(result.events_written, 1)

            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT event_type, payload FROM session_events").fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "drift_finding")
            payload = json.loads(rows[0][1])
            self.assertEqual(payload["task"], "task-alpha")
            self.assertIn("churn_files", payload["message"])

    def test_ingest_drift_outputs_skips_ok_and_lifecycle(self) -> None:
        """OK findings and lifecycle messages (claimed/done/failed) are not ingested."""
        from driftdriver.reporting import ingest_drift_outputs

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            _create_drift_outputs(wg_dir, [
                ("task-clean", [
                    {"timestamp": "2025-01-01T00:00:00Z", "message": "Task claimed"},
                    {"timestamp": "2025-01-01T00:01:00Z", "message": "Coredrift: OK (no findings)"},
                    {"timestamp": "2025-01-01T00:02:00Z", "message": "Task marked as done"},
                ]),
            ])

            result = ingest_drift_outputs(wg_dir, "sess-ok", "myproject", db_path)
            self.assertEqual(result.events_read, 0)
            self.assertEqual(result.events_written, 0)

    def test_ingest_drift_outputs_no_output_dir(self) -> None:
        """Missing output directory → graceful no-op."""
        from driftdriver.reporting import ingest_drift_outputs

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            result = ingest_drift_outputs(wg_dir, "sess-none", "myproject", db_path)
            self.assertEqual(result.events_read, 0)
            self.assertEqual(result.events_written, 0)

    def test_ingest_drift_outputs_deduplicates(self) -> None:
        """Same drift output ingested twice → no duplicate rows."""
        from driftdriver.reporting import ingest_drift_outputs

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td)
            db_path = Path(td) / "lessons.db"
            _create_lessons_db(db_path)

            _create_drift_outputs(wg_dir, [
                ("task-dup", [
                    {"timestamp": "2025-01-01T00:00:00Z", "message": "Coredrift: yellow (scope_violation) | next: Revert"},
                ]),
            ])

            r1 = ingest_drift_outputs(wg_dir, "sess-dup", "myproject", db_path)
            r2 = ingest_drift_outputs(wg_dir, "sess-dup", "myproject", db_path)
            self.assertEqual(r1.events_written, 1)
            self.assertEqual(r2.duplicates_skipped, 1)
            self.assertEqual(r2.events_written, 0)


class TestFormatReportMarkdown(unittest.TestCase):
    def test_format_report_markdown(self) -> None:
        """Verifies output shape: header, sections, stats."""
        from driftdriver.reporting import FlushResult, SessionReport, format_report_markdown

        report = SessionReport(
            session_id="sess-99",
            project="driftdriver",
            timestamp="2025-07-01T12:00:00Z",
            flush_result=FlushResult(events_read=5, events_written=4, duplicates_skipped=1, errors=0),
            drift_result=FlushResult(events_read=3, events_written=2, duplicates_skipped=1, errors=0),
            knowledge_exported=3,
            pushed_to_central=True,
        )
        md = format_report_markdown(report)
        self.assertIn("sess-99", md)
        self.assertIn("driftdriver", md)
        self.assertIn("5", md)  # events_read
        self.assertIn("4", md)  # events_written
        self.assertIn("1", md)  # duplicates
        self.assertIn("3", md)  # knowledge_exported
        self.assertIn("Session Report", md)


if __name__ == "__main__":
    unittest.main()
