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


def record_event_immediate(
    event_type: str,
    content: str,
    *,
    session_id: str = "",
    project: str = "",
    metadata: dict | None = None,
    db_path: Path | None = None,
) -> bool:
    """Write a single event directly to lessons.db immediately.

    Unlike flush_pending_events which batches from pending.jsonl,
    this writes one event right now. Returns True on success.
    """
    if db_path is None:
        db_path = Path.home() / ".claude" / "lessons-mcp" / "lessons.db"

    if not db_path.exists():
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    payload = json.dumps({"event_type": event_type, "content": content, **(metadata or {})})
    dedupe_key = f"{session_id}:{event_type}:{now_iso}:{hashlib.md5(payload[:200].encode()).hexdigest()[:16]}"
    event_id = str(uuid4())

    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute(
            "INSERT OR IGNORE INTO session_events "
            "(id, session_id, cli_tool, project, event_type, payload, dedupe_key, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, session_id, "driftdriver", project, event_type, payload, dedupe_key, now_iso),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def record_ecosystem_snapshot(
    overview: dict,
    *,
    db_path: Path | None = None,
) -> bool:
    """Persist an ecosystem overview snapshot for trend analysis.

    Called after build_ecosystem_overview() to retain aggregate stats
    that would otherwise be transient dashboard data.
    """
    return record_event_immediate(
        event_type="ecosystem_snapshot",
        content=json.dumps(overview, default=str),
        project="ecosystem",
        metadata={
            "repos_total": overview.get("repos_total", 0),
            "tasks_open": overview.get("tasks_open", 0),
            "tasks_in_progress": overview.get("tasks_in_progress", 0),
            "tasks_done": overview.get("tasks_done", 0),
            "repos_stalled": overview.get("repos_stalled", 0),
            "security_critical": overview.get("security_critical", 0),
            "quality_critical": overview.get("quality_critical", 0),
        },
        db_path=db_path,
    )


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

    # Step 4: Distill drift findings into knowledge entries
    distill_drift_knowledge(db_path, project)

    # Step 5: Export knowledge from DB to knowledge.jsonl
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

    # Step 6: Push to central if configured
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


# ---------------------------------------------------------------------------
# Drift knowledge distillation
# ---------------------------------------------------------------------------

_TAG_RE = __import__("re").compile(r"\(([^)]+)\)")


def _parse_drift_tags(message: str) -> list[str]:
    """Extract drift tags from a finding message like 'Coredrift: yellow (hardening_in_core, scope_violation)'."""
    m = _TAG_RE.search(message)
    if not m:
        return []
    return [t.strip() for t in m.group(1).split(",") if t.strip()]


def _parse_drift_lane(message: str) -> str:
    """Extract lane name from a finding message like 'Coredrift: yellow ...'."""
    colon = message.find(":")
    if colon > 0:
        return message[:colon].strip().lower()
    return "unknown"


def _parse_drift_color(message: str) -> str:
    """Extract severity color (yellow/red) from a finding message."""
    lower = message.lower()
    if "red" in lower.split("(")[0]:
        return "red"
    if "yellow" in lower.split("(")[0]:
        return "yellow"
    return "unknown"


def distill_drift_knowledge(db_path: Path, project: str) -> int:
    """Aggregate drift findings into knowledge entries.

    Groups findings by tag, computes frequency and confidence, then writes
    (or updates) knowledge_entries in lessons.db. Returns count of entries created/updated.
    """
    if not db_path.exists():
        return 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT payload FROM session_events WHERE project = ? AND event_type = 'drift_finding'",
            (project,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    # Aggregate: tag -> {count, tasks, lanes, colors, sample_messages}
    tag_stats: dict[str, dict] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        message = payload.get("message", "")
        task = payload.get("task", "")
        tags = _parse_drift_tags(message)
        lane = _parse_drift_lane(message)
        color = _parse_drift_color(message)

        for tag in tags:
            if tag not in tag_stats:
                tag_stats[tag] = {"count": 0, "tasks": set(), "lanes": set(), "colors": set(), "samples": []}
            stats = tag_stats[tag]
            stats["count"] += 1
            if task:
                stats["tasks"].add(task)
            stats["lanes"].add(lane)
            stats["colors"].add(color)
            if len(stats["samples"]) < 3:
                stats["samples"].append(message)

    if not tag_stats:
        return 0

    # Write knowledge entries
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(db_path))
    created = 0
    try:
        for tag, stats in tag_stats.items():
            # Confidence: based on occurrence count and spread across tasks
            count = stats["count"]
            task_count = len(stats["tasks"])
            if count >= 5 or task_count >= 3:
                confidence = 0.9
            elif count >= 3 or task_count >= 2:
                confidence = 0.7
            elif count >= 2:
                confidence = 0.5
            else:
                confidence = 0.3

            # Boost for red findings
            if "red" in stats["colors"]:
                confidence = min(1.0, confidence + 0.1)

            lanes_str = ", ".join(sorted(stats["lanes"]))
            tasks_str = ", ".join(sorted(stats["tasks"]))
            content = (
                f"Recurring drift signal: {tag} (seen {count}x across {task_count} tasks). "
                f"Lanes: {lanes_str}. Tasks: {tasks_str}. "
                f"Sample: {stats['samples'][0][:200]}"
            )

            # Upsert: use tag+project as dedupe key
            entry_id = hashlib.md5(f"{project}:{tag}".encode()).hexdigest()
            existing = conn.execute(
                "SELECT id FROM knowledge_entries WHERE id = ?", (entry_id,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE knowledge_entries SET content = ?, confidence = ?, updated_at = ? WHERE id = ?",
                    (content, confidence, now_iso, entry_id),
                )
            else:
                conn.execute(
                    "INSERT INTO knowledge_entries (id, category, project, content, confidence, source_session_ids, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (entry_id, "drift_pattern", project, content, confidence, "[]", now_iso, now_iso),
                )
            created += 1

        conn.commit()
    finally:
        conn.close()

    return created
