# ABOUTME: Query functions and handler logic for ecosystem intelligence dashboard API endpoints
# ABOUTME: Reads signals, evaluation_runs, source_configs from Postgres for briefing, inbox, and decision log views

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from driftdriver.intelligence.db import PostgresConfig
from driftdriver.updates import load_update_state


def _get_psycopg():
    """Lazy import psycopg so the module is importable without it installed."""
    import psycopg  # noqa: E402
    return psycopg


def _connect(**kwargs):
    return _get_psycopg().connect(**kwargs)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def _signal_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        signal_id, source_type, source_id, signal_type, title, raw_payload,
        detected_at, evaluated_at, decision, decision_reason, decision_confidence,
        decided_by, acted_on, action_log, vetoed_at, veto_reason, created_at, updated_at,
    ) = row
    return {
        "id": str(signal_id),
        "source_type": str(source_type),
        "source_id": str(source_id),
        "signal_type": str(signal_type),
        "title": str(title),
        "raw_payload": dict(raw_payload) if isinstance(raw_payload, dict) else {},
        "detected_at": _iso(detected_at),
        "evaluated_at": _iso(evaluated_at),
        "decision": str(decision) if decision else None,
        "decision_reason": str(decision_reason) if decision_reason else None,
        "decision_confidence": float(decision_confidence) if decision_confidence is not None else None,
        "decided_by": str(decided_by) if decided_by else None,
        "acted_on": bool(acted_on),
        "action_log": list(action_log) if isinstance(action_log, list) else [],
        "vetoed_at": _iso(vetoed_at),
        "veto_reason": str(veto_reason) if veto_reason else None,
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
    }


_SIGNAL_COLUMNS = """
    id, source_type, source_id, signal_type, title, raw_payload,
    detected_at, evaluated_at, decision, decision_reason, decision_confidence,
    decided_by, acted_on, action_log, vetoed_at, veto_reason, created_at, updated_at
"""


def build_briefing(config: PostgresConfig) -> dict[str, Any]:
    now = _utc_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM signals")
            total_signals = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM signals WHERE evaluated_at >= %s",
                (today_start,),
            )
            evaluated_today = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM signals WHERE evaluated_at >= %s AND acted_on = true",
                (today_start,),
            )
            auto_decided = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM signals WHERE evaluated_at >= %s AND acted_on = false AND evaluated_at IS NOT NULL",
                (today_start,),
            )
            escalated = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM signals WHERE evaluated_at IS NULL",
                (),
            )
            pending = cur.fetchone()[0]

            # Recent actions taken today
            cur.execute(
                f"""SELECT {_SIGNAL_COLUMNS} FROM signals
                    WHERE evaluated_at >= %s AND acted_on = true
                    ORDER BY evaluated_at DESC LIMIT 10""",
                (today_start,),
            )
            actions = [_signal_row_to_dict(row) for row in cur.fetchall()]

            # Source health
            cur.execute(
                """SELECT source_type, enabled, last_synced_at, sync_interval_minutes
                     FROM source_configs ORDER BY source_type"""
            )
            source_health = []
            for row in cur.fetchall():
                source_health.append({
                    "source_type": str(row[0]),
                    "enabled": bool(row[1]),
                    "last_synced_at": _iso(row[2]),
                    "sync_interval_minutes": int(row[3]),
                })

            # Stack impact from recent evaluated signals
            cur.execute(
                f"""SELECT {_SIGNAL_COLUMNS} FROM signals
                    WHERE evaluated_at >= %s AND decision IN ('adopt', 'watch')
                    ORDER BY decision_confidence DESC LIMIT 5""",
                (today_start,),
            )
            stack_impact = [_signal_row_to_dict(row) for row in cur.fetchall()]

    return {
        "stats": {
            "total_signals": total_signals,
            "evaluated_today": evaluated_today,
            "auto_decided": auto_decided,
            "escalated": escalated,
            "pending": pending,
        },
        "actions": actions,
        "stack_impact": stack_impact,
        "source_health": source_health,
        "generated_at": _iso(now),
    }


def build_briefing_history(config: PostgresConfig, *, days: int = 7) -> dict[str, Any]:
    now = _utc_now()
    daily: list[dict[str, Any]] = []

    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            for offset in range(days):
                day_start = (now - timedelta(days=offset)).replace(
                    hour=0, minute=0, second=0, microsecond=0,
                )
                day_end = day_start + timedelta(days=1)
                cur.execute(
                    """SELECT
                        count(*) FILTER (WHERE evaluated_at >= %s AND evaluated_at < %s),
                        count(*) FILTER (WHERE evaluated_at >= %s AND evaluated_at < %s AND acted_on = true),
                        count(*) FILTER (WHERE evaluated_at >= %s AND evaluated_at < %s AND acted_on = false AND evaluated_at IS NOT NULL)
                       FROM signals""",
                    (day_start, day_end, day_start, day_end, day_start, day_end),
                )
                row = cur.fetchone()
                daily.append({
                    "date": day_start.date().isoformat(),
                    "evaluated": row[0],
                    "auto_decided": row[1],
                    "escalated": row[2],
                })

    return {"days": daily}


def build_inbox(config: PostgresConfig) -> dict[str, Any]:
    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {_SIGNAL_COLUMNS} FROM signals
                    WHERE evaluated_at IS NOT NULL
                      AND acted_on = false
                      AND vetoed_at IS NULL
                    ORDER BY
                      CASE WHEN decision = 'adopt' THEN 0
                           WHEN decision = 'defer' THEN 1
                           WHEN decision = 'watch' THEN 2
                           ELSE 3 END,
                      decision_confidence DESC,
                      evaluated_at ASC""",
            )
            signals = [_signal_row_to_dict(row) for row in cur.fetchall()]

    return {"signals": signals, "count": len(signals)}


def approve_signal(config: PostgresConfig, *, signal_id: UUID) -> dict[str, Any]:
    now = _utc_now()
    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, acted_on FROM signals WHERE id = %s", (signal_id,))
            row = cur.fetchone()
            if row is None:
                return {"error": "signal_not_found", "signal_id": str(signal_id)}

            cur.execute(
                """UPDATE signals
                      SET acted_on = true,
                          action_log = action_log || %s::jsonb,
                          updated_at = %s
                    WHERE id = %s""",
                (
                    json.dumps([{
                        "action": "approve",
                        "status": "completed",
                        "decided_by": "human",
                        "timestamp": _iso(now),
                    }]),
                    now,
                    signal_id,
                ),
            )
        conn.commit()

    return {"status": "approved", "signal_id": str(signal_id)}


def override_signal(
    config: PostgresConfig,
    *,
    signal_id: UUID,
    new_decision: str,
    reason: str,
) -> dict[str, Any]:
    now = _utc_now()
    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM signals WHERE id = %s", (signal_id,))
            if cur.fetchone() is None:
                return {"error": "signal_not_found", "signal_id": str(signal_id)}

            cur.execute(
                """UPDATE signals
                      SET decision = %s,
                          decision_reason = %s,
                          decided_by = 'human',
                          acted_on = true,
                          vetoed_at = %s,
                          veto_reason = %s,
                          action_log = action_log || %s::jsonb,
                          updated_at = %s
                    WHERE id = %s""",
                (
                    new_decision,
                    reason,
                    now,
                    reason,
                    json.dumps([{
                        "action": "override",
                        "status": "completed",
                        "new_decision": new_decision,
                        "reason": reason,
                        "decided_by": "human",
                        "timestamp": _iso(now),
                    }]),
                    now,
                    signal_id,
                ),
            )
        conn.commit()

    return {
        "status": "overridden",
        "signal_id": str(signal_id),
        "decision": new_decision,
    }


def snooze_signal(config: PostgresConfig, *, signal_id: UUID) -> dict[str, Any]:
    now = _utc_now()
    snooze_until = now + timedelta(hours=48)
    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM signals WHERE id = %s", (signal_id,))
            if cur.fetchone() is None:
                return {"error": "signal_not_found", "signal_id": str(signal_id)}

            cur.execute(
                """UPDATE signals
                      SET action_log = action_log || %s::jsonb,
                          updated_at = %s
                    WHERE id = %s""",
                (
                    json.dumps([{
                        "action": "snooze",
                        "status": "completed",
                        "snooze_until": _iso(snooze_until),
                        "decided_by": "human",
                        "timestamp": _iso(now),
                    }]),
                    now,
                    signal_id,
                ),
            )
        conn.commit()

    return {"status": "snoozed", "signal_id": str(signal_id), "snooze_until": _iso(snooze_until)}


def batch_approve_signals(config: PostgresConfig) -> dict[str, Any]:
    now = _utc_now()
    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM signals
                    WHERE evaluated_at IS NOT NULL
                      AND acted_on = false
                      AND vetoed_at IS NULL""",
            )
            ids = [row[0] for row in cur.fetchall()]
            for signal_id in ids:
                cur.execute(
                    """UPDATE signals
                          SET acted_on = true,
                              action_log = action_log || %s::jsonb,
                              updated_at = %s
                        WHERE id = %s""",
                    (
                        json.dumps([{
                            "action": "batch_approve",
                            "status": "completed",
                            "decided_by": "human",
                            "timestamp": _iso(now),
                        }]),
                        now,
                        signal_id,
                    ),
                )
        conn.commit()

    return {"status": "batch_approved", "approved_count": len(ids)}


def build_decision_log(
    config: PostgresConfig,
    *,
    source_type: str | None = None,
    decision: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    conditions = ["evaluated_at IS NOT NULL"]
    params: list[Any] = []

    if source_type:
        conditions.append("source_type = %s")
        params.append(source_type)
    if decision:
        conditions.append("decision = %s")
        params.append(decision)
    if search:
        conditions.append("title ILIKE %s")
        params.append(f"%{search}%")

    where_clause = " AND ".join(conditions)
    params.append(limit)

    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {_SIGNAL_COLUMNS} FROM signals
                    WHERE {where_clause}
                    ORDER BY evaluated_at DESC, updated_at DESC
                    LIMIT %s""",
                tuple(params),
            )
            decisions = [_signal_row_to_dict(row) for row in cur.fetchall()]

    return {"decisions": decisions, "count": len(decisions)}


def build_decision_trends(config: PostgresConfig, *, days: int = 30) -> dict[str, Any]:
    now = _utc_now()
    cutoff = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)

    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT
                    date_trunc('day', evaluated_at) AS day,
                    decision,
                    count(*) AS cnt,
                    avg(decision_confidence) AS avg_confidence
                   FROM signals
                  WHERE evaluated_at >= %s AND evaluated_at IS NOT NULL
                  GROUP BY day, decision
                  ORDER BY day DESC, decision""",
                (cutoff,),
            )
            rows = cur.fetchall()

    daily: list[dict[str, Any]] = []
    current_day: str | None = None
    current_entry: dict[str, Any] | None = None
    for row in rows:
        day_str = row[0].date().isoformat() if row[0] else "unknown"
        if day_str != current_day:
            if current_entry is not None:
                daily.append(current_entry)
            current_day = day_str
            current_entry = {"date": day_str, "total": 0, "by_decision": {}, "avg_confidence": 0.0}
        decision_name = str(row[1]) if row[1] else "unknown"
        count = int(row[2])
        avg_conf = float(row[3]) if row[3] is not None else 0.0
        current_entry["by_decision"][decision_name] = count
        current_entry["total"] += count
        # Weighted average
        prev_total = current_entry["total"] - count
        if current_entry["total"] > 0:
            current_entry["avg_confidence"] = (
                (current_entry["avg_confidence"] * prev_total + avg_conf * count)
                / current_entry["total"]
            )
    if current_entry is not None:
        daily.append(current_entry)

    return {"daily": daily}


def build_tracking(config: PostgresConfig, *, wg_dir: Path | None = None) -> dict[str, Any]:
    """Return tracking state: all monitored repos/users with current SHA, dates, and most recent signal."""
    # Load update state (current SHA + commit_date per repo/user)
    active_wg_dir = wg_dir or Path(".workgraph")
    state = load_update_state(active_wg_dir)
    state_repos: dict[str, Any] = state.get("repos") if isinstance(state.get("repos"), dict) else {}
    state_users: dict[str, Any] = state.get("users") if isinstance(state.get("users"), dict) else {}

    # Load source configs + source health from DB
    source_configs: dict[str, dict[str, Any]] = {}
    source_health: dict[str, dict[str, Any]] = {}
    with _connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_type, config, enabled, last_synced_at, sync_interval_minutes FROM source_configs"
            )
            for row in cur.fetchall():
                st = str(row[0])
                source_configs[st] = dict(row[1]) if isinstance(row[1], dict) else {}
                source_health[st] = {
                    "enabled": bool(row[2]),
                    "last_synced_at": _iso(row[3]),
                    "sync_interval_minutes": int(row[4]) if row[4] is not None else None,
                }

            # Most recent signal per GitHub repo path (source_id = "{repo}@{sha}" or "{repo}@{date}")
            cur.execute(
                """SELECT DISTINCT ON (split_part(source_id, '@', 1))
                   split_part(source_id, '@', 1) AS repo_path,
                   signal_type, title, detected_at, decision, decision_confidence
                   FROM signals
                   WHERE source_type = 'github'
                   ORDER BY split_part(source_id, '@', 1), detected_at DESC"""
            )
            latest_by_repo: dict[str, dict[str, Any]] = {}
            for row in cur.fetchall():
                latest_by_repo[str(row[0])] = {
                    "signal_type": str(row[1]),
                    "title": str(row[2]),
                    "detected_at": _iso(row[3]),
                    "decision": str(row[4]) if row[4] else None,
                    "confidence": float(row[5]) if row[5] is not None else None,
                }

            # Most recent signal per user (activity/new_repo signals have title "... from @user: ...")
            cur.execute(
                """SELECT DISTINCT ON (
                       substring(title FROM 'from @([^ :]+)')
                   )
                   substring(title FROM 'from @([^ :]+)') AS username,
                   signal_type, title, detected_at, decision, decision_confidence
                   FROM signals
                   WHERE source_type = 'github'
                     AND signal_type IN ('activity', 'new_repo')
                     AND title LIKE '%from @%'
                   ORDER BY substring(title FROM 'from @([^ :]+)'), detected_at DESC"""
            )
            latest_by_user: dict[str, dict[str, Any]] = {}
            for row in cur.fetchall():
                uname = str(row[0]) if row[0] else ""
                if uname:
                    latest_by_user[uname] = {
                        "signal_type": str(row[1]),
                        "title": str(row[2]),
                        "detected_at": _iso(row[3]),
                        "decision": str(row[4]) if row[4] else None,
                        "confidence": float(row[5]) if row[5] is not None else None,
                    }

    # Build repos list from state (covers ECOSYSTEM_REPOS + extra_repos)
    repos: list[dict[str, Any]] = []
    for name, entry in sorted(state_repos.items(), key=lambda x: str(x[1].get("commit_date") or ""), reverse=True):
        if not isinstance(entry, dict):
            continue
        repo_path = str(entry.get("repo") or "")
        sha = str(entry.get("sha") or "")
        repos.append({
            "name": name,
            "repo": repo_path,
            "sha": sha[:12] if sha else None,
            "commit_date": str(entry.get("commit_date") or ""),
            "seen_at": str(entry.get("seen_at") or ""),
            "recent_signal": latest_by_repo.get(repo_path),
        })

    # Build users list from state — include top recently-pushed repos
    users: list[dict[str, Any]] = []
    for username, entry in sorted(state_users.items()):
        if not isinstance(entry, dict):
            continue
        # Extract repos sorted by pushed_at descending
        raw_repos = entry.get("repos") if isinstance(entry.get("repos"), dict) else {}
        recent_repos = sorted(
            [
                {
                    "path": path,
                    "description": str(r.get("description") or ""),
                    "pushed_at": str(r.get("pushed_at") or ""),
                    "html_url": str(r.get("html_url") or ""),
                }
                for path, r in raw_repos.items()
                if isinstance(r, dict)
            ],
            key=lambda r: r["pushed_at"],
            reverse=True,
        )[:5]
        users.append({
            "username": username,
            "seen_at": str(entry.get("seen_at") or ""),
            "repo_count": int(entry.get("repo_count") or len(raw_repos)),
            "recent_repos": recent_repos,
            "recent_signal": latest_by_user.get(username),
        })

    return {
        "repos": repos,
        "users": users,
        "sources": [
            {
                "source_type": st,
                **source_health.get(st, {}),
                "config_summary": _summarize_source_config(st, source_configs.get(st, {})),
            }
            for st in sorted(source_health)
        ],
    }


def _summarize_source_config(source_type: str, config: dict[str, Any]) -> dict[str, Any]:
    if source_type == "github":
        extra = config.get("extra_repos") or {}
        users = config.get("github_users") or []
        return {
            "extra_repo_count": len(extra) if isinstance(extra, dict) else 0,
            "user_count": len(users) if isinstance(users, list) else 0,
        }
    if source_type == "vibez":
        return {
            "api_endpoint": config.get("api_endpoint", ""),
            "keyword_filter": config.get("keyword_filter") or [],
        }
    return {}


def trigger_sync(config: PostgresConfig) -> dict[str, Any]:
    """Run the intelligence sync pipeline and return a summary."""
    import asyncio
    from driftdriver.intelligence.sync import run_sync
    summary = asyncio.run(run_sync(config))
    return summary.as_dict()
