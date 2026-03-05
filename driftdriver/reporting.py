# ABOUTME: Reporting module that closes the speedrift learning loop
# ABOUTME: Flushes pending events to Lessons MCP DB, exports knowledge, pushes to central repo

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


@dataclass
class ReportingConfig:
    central_repo: str = ""
    auto_report: bool = True
    include_knowledge: bool = True
    db_path: Path = field(default_factory=lambda: Path.home() / ".claude" / "lessons-mcp" / "lessons.db")


@dataclass
class FlushResult:
    events_read: int = 0
    events_written: int = 0
    duplicates_skipped: int = 0
    errors: int = 0


@dataclass
class SessionReport:
    session_id: str
    project: str
    timestamp: str
    flush_result: FlushResult
    drift_result: FlushResult = field(default_factory=FlushResult)
    chat_result: FlushResult = field(default_factory=FlushResult)
    knowledge_exported: int = 0
    pushed_to_central: bool = False


def load_reporting_config(wg_dir: Path) -> ReportingConfig:
    """Read [reporting] section from drift-policy.toml, falling back to defaults."""
    from driftdriver.policy import load_drift_policy

    policy = load_drift_policy(wg_dir)
    return ReportingConfig(
        central_repo=policy.reporting_central_repo,
        auto_report=policy.reporting_auto_report,
        include_knowledge=policy.reporting_include_knowledge,
    )


def _write_event_to_db(db_path: Path, session_id: str, project: str, event: dict) -> bool:
    """Write a single event to the session_events table. Returns True if inserted, False if duplicate."""
    args = event.get("args", {})
    # Handler events use 'tool' as the action name and 'args' as the full payload
    # Lessons MCP format uses 'event_type' and 'payload' inside args
    event_type = args.get("event_type", event.get("tool", "observation"))
    payload = args.get("payload", args)
    payload_json = json.dumps(payload, sort_keys=True)
    ts = event.get("ts", "")

    dedupe_key = f"{session_id}:{event_type}:{ts}:{hashlib.md5(payload_json[:200].encode()).hexdigest()[:16]}"
    event_id = str(uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO session_events (id, session_id, cli_tool, project, event_type, payload, dedupe_key, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, session_id, "driftdriver", project, event_type, payload_json, dedupe_key, now_iso),
        )
        inserted = conn.total_changes
        conn.commit()
        # Check if the row was actually inserted (not ignored due to dedupe)
        row = conn.execute("SELECT id FROM session_events WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        was_inserted = row is not None and row[0] == event_id
        return was_inserted
    finally:
        conn.close()


def _parse_concatenated_json(content: str) -> list[dict]:
    """Parse concatenated JSON objects (pretty-printed or single-line JSONL)."""
    results: list[dict] = []
    decoder = json.JSONDecoder()
    pos = 0
    length = len(content)
    while pos < length:
        # Skip whitespace
        while pos < length and content[pos] in " \t\n\r":
            pos += 1
        if pos >= length:
            break
        try:
            obj, end = decoder.raw_decode(content, pos)
            if isinstance(obj, dict):
                results.append(obj)
            pos = end
        except json.JSONDecodeError:
            # Find next '{' to try recovery
            next_brace = content.find("{", pos + 1)
            if next_brace < 0:
                break
            pos = next_brace
    return results


def flush_pending_events(wg_dir: Path, session_id: str, project: str, db_path: Path) -> FlushResult:
    """Read pending.jsonl, write events to lessons.db, rename pending file."""
    pending_path = wg_dir / ".lessons-events" / "pending.jsonl"
    result = FlushResult()

    if not pending_path.exists():
        return result

    # Read events — handles both single-line JSONL and pretty-printed concatenated JSON
    events: list[dict] = []
    content = pending_path.read_text().strip()
    if not content:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        pending_path.rename(pending_path.parent / f"flushed-{ts}.jsonl")
        return result

    events = _parse_concatenated_json(content)
    result.events_read = len(events)

    if not events:
        # Rename even empty files to avoid re-processing
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        pending_path.rename(pending_path.parent / f"flushed-{ts}.jsonl")
        return result

    # Ensure DB parent exists
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Write each event to DB
    for event in events:
        try:
            inserted = _write_event_to_db(db_path, session_id, project, event)
            if inserted:
                result.events_written += 1
            else:
                result.duplicates_skipped += 1
        except Exception:
            result.errors += 1

    # Rename pending.jsonl to flushed-{timestamp}.jsonl
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pending_path.rename(pending_path.parent / f"flushed-{ts}.jsonl")

    return result


_LIFECYCLE_PREFIXES = ("Task claimed", "Task marked as done", "Task marked as failed")


def _is_drift_finding(message: str) -> bool:
    """Return True if a drift log message contains an actionable finding."""
    if not message:
        return False
    if message.startswith(_LIFECYCLE_PREFIXES):
        return False
    if "OK (no findings)" in message:
        return False
    # Look for actual drift signals
    lower = message.lower()
    return any(k in lower for k in (
        "yellow", "red", "finding", "drift", "scope", "warning",
        "churn", "hardening", "violation", "disposition", "blocker",
    ))


def ingest_drift_outputs(wg_dir: Path, session_id: str, project: str, db_path: Path) -> FlushResult:
    """Scan .workgraph/output/ for drift check results and write findings to DB."""
    output_dir = wg_dir / "output"
    result = FlushResult()

    if not output_dir.exists():
        return result

    db_path.parent.mkdir(parents=True, exist_ok=True)

    for task_dir in sorted(output_dir.iterdir()):
        log_file = task_dir / "log.json"
        if not log_file.is_file():
            continue
        try:
            entries = json.loads(log_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        if not isinstance(entries, list):
            continue

        for entry in entries:
            message = entry.get("message", "")
            if not _is_drift_finding(message):
                continue

            result.events_read += 1
            event = {
                "tool": "drift_finding",
                "ts": entry.get("timestamp", ""),
                "args": {
                    "event_type": "drift_finding",
                    "payload": {
                        "task": task_dir.name,
                        "message": message,
                        "timestamp": entry.get("timestamp", ""),
                    },
                },
            }
            try:
                inserted = _write_event_to_db(db_path, session_id, project, event)
                if inserted:
                    result.events_written += 1
                else:
                    result.duplicates_skipped += 1
            except Exception:
                result.errors += 1

    return result


def ingest_chat_history(wg_dir: Path, session_id: str, project: str, db_path: Path) -> FlushResult:
    """Read .workgraph/chat/ inbox + outbox and write messages as chat_message events to DB."""
    chat_dir = wg_dir / "chat"
    result = FlushResult()

    if not chat_dir.is_dir():
        return result

    db_path.parent.mkdir(parents=True, exist_ok=True)

    for filename in ("inbox.jsonl", "outbox.jsonl"):
        path = chat_dir / filename
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                result.errors += 1
                continue

            result.events_read += 1
            event = {
                "tool": "chat_message",
                "ts": msg.get("timestamp", ""),
                "args": {
                    "event_type": "chat_message",
                    "payload": {
                        "role": msg.get("role", "unknown"),
                        "content": msg.get("content", ""),
                        "request_id": msg.get("request_id", ""),
                        "chat_id": msg.get("id", 0),
                        "source": filename,
                    },
                },
            }
            try:
                inserted = _write_event_to_db(db_path, session_id, project, event)
                if inserted:
                    result.events_written += 1
                else:
                    result.duplicates_skipped += 1
            except Exception:
                result.errors += 1

    return result


def export_knowledge(db_path: Path, project: str, wg_dir: Path) -> int:
    """Read knowledge_entries from lessons.db, write as knowledge.jsonl in KnowledgeFact format."""
    if not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, category, project, content, confidence, created_at FROM knowledge_entries WHERE project = ? OR project IS NULL",
            (project,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    # Map confidence float to string label
    def _confidence_label(val: float) -> str:
        if val >= 0.7:
            return "high"
        if val >= 0.4:
            return "medium"
        return "low"

    kb_path = wg_dir / "knowledge.jsonl"
    with open(kb_path, "w") as f:
        for row in rows:
            fact = {
                "fact_id": row["id"],
                "fact_type": row["category"],
                "content": row["content"],
                "affected_files": [],
                "affected_modules": [],
                "confidence": _confidence_label(row["confidence"] or 0.5),
                "provenance": f"lessons-db:{row['created_at'] or ''}",
                "usage_count": 0,
                "helpful_count": 0,
                "outdated_reports": 0,
            }
            f.write(json.dumps(fact) + "\n")

    return len(rows)


def push_to_central(report: SessionReport, wg_dir: Path, config: ReportingConfig) -> bool:
    """Copy report markdown + knowledge.jsonl to central_repo/reports/{project}/{timestamp}/."""
    if not config.central_repo:
        return False

    central = Path(config.central_repo)
    # Use a filesystem-safe timestamp
    safe_ts = report.timestamp.replace(":", "-").replace("T", "_").rstrip("Z")
    report_dir = central / "reports" / report.project / safe_ts
    report_dir.mkdir(parents=True, exist_ok=True)

    # Write report markdown
    md = format_report_markdown(report)
    (report_dir / "report.md").write_text(md, encoding="utf-8")

    # Copy knowledge.jsonl if it exists
    kb_path = wg_dir / "knowledge.jsonl"
    if kb_path.exists() and config.include_knowledge:
        shutil.copy2(str(kb_path), str(report_dir / "knowledge.jsonl"))

    return True


def generate_session_report(
    wg_dir: Path, session_id: str, project: str, config: ReportingConfig
) -> SessionReport:
    """Orchestrate: flush pending events → export knowledge → push to central."""
    db_path = config.db_path
    now_iso = datetime.now(timezone.utc).isoformat()

    # Step 1: Flush pending events to DB
    flush_result = flush_pending_events(wg_dir, session_id, project, db_path)

    # Step 2: Ingest drift outputs
    drift_result = ingest_drift_outputs(wg_dir, session_id, project, db_path)

    # Step 3: Ingest coordinator chat history
    chat_result = ingest_chat_history(wg_dir, session_id, project, db_path)

    # Step 4: Export knowledge from DB to knowledge.jsonl
    knowledge_exported = 0
    if config.include_knowledge:
        knowledge_exported = export_knowledge(db_path, project, wg_dir)

    report = SessionReport(
        session_id=session_id,
        project=project,
        timestamp=now_iso,
        flush_result=flush_result,
        drift_result=drift_result,
        chat_result=chat_result,
        knowledge_exported=knowledge_exported,
        pushed_to_central=False,
    )

    # Step 5: Push to central if configured
    if config.central_repo:
        pushed = push_to_central(report, wg_dir, config)
        report.pushed_to_central = pushed

    return report


def format_report_markdown(report: SessionReport) -> str:
    """Render a SessionReport as markdown."""
    lines = [
        f"# Session Report: {report.session_id}",
        "",
        f"**Project:** {report.project}",
        f"**Timestamp:** {report.timestamp}",
        "",
        "## Event Flush",
        "",
        f"- Events read: {report.flush_result.events_read}",
        f"- Events written: {report.flush_result.events_written}",
        f"- Duplicates skipped: {report.flush_result.duplicates_skipped}",
        f"- Errors: {report.flush_result.errors}",
        "",
        "## Drift Output Ingestion",
        "",
        f"- Drift findings read: {report.drift_result.events_read}",
        f"- Drift findings written: {report.drift_result.events_written}",
        f"- Drift duplicates skipped: {report.drift_result.duplicates_skipped}",
        "",
        "## Chat History Ingestion",
        "",
        f"- Chat messages read: {report.chat_result.events_read}",
        f"- Chat messages written: {report.chat_result.events_written}",
        f"- Chat duplicates skipped: {report.chat_result.duplicates_skipped}",
        "",
        "## Knowledge Export",
        "",
        f"- Knowledge entries exported: {report.knowledge_exported}",
        "",
        "## Central Push",
        "",
        f"- Pushed to central: {'yes' if report.pushed_to_central else 'no'}",
    ]
    return "\n".join(lines) + "\n"
