"""Canonicalize legacy PAIA agent-health decisions into the shared decision surface."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.decision_queue import DecisionRecord, _record_to_dict
from driftdriver.paia_topology import PaiaTopology, load_paia_topology


def _workgraph_config_dir(config_dir: Path | None = None) -> Path:
    if config_dir is not None:
        return config_dir.expanduser().resolve(strict=False)
    env_value = os.environ.get("WORKGRAPH_CONFIG_DIR", "").strip()
    if env_value:
        return Path(env_value).expanduser().resolve(strict=False)
    return (Path.home() / ".config" / "workgraph").resolve(strict=False)


def _pending_path(config_dir: Path | None = None) -> Path:
    return _workgraph_config_dir(config_dir) / "agent_health_pending.json"


def _answered_path(config_dir: Path | None = None) -> Path:
    return _workgraph_config_dir(config_dir) / "agent_health_answered.jsonl"


def _load_pending_payload(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for decision_id, payload in raw.items():
        if isinstance(decision_id, str) and isinstance(payload, dict):
            out[decision_id] = payload
    return out


def _save_pending_payload(path: Path, payload: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_topology(workspace_root: Path | None, config_dir: Path) -> PaiaTopology | None:
    if workspace_root is not None:
        return load_paia_topology(workspace_root)
    return load_paia_topology(config_dir)


def _infer_repo_name(payload: dict[str, Any]) -> str:
    explicit = str(payload.get("repo") or "").strip()
    if explicit:
        return explicit
    agent = str(payload.get("agent") or "").strip()
    if agent:
        return agent
    diff = str(payload.get("diff") or "")
    for marker in ("a/experiments/", "b/experiments/"):
        idx = diff.find(marker)
        if idx == -1:
            continue
        tail = diff[idx + len(marker):]
        candidate = tail.split("/", 1)[0].strip()
        if candidate:
            return candidate
    return ""


def _canonical_target(payload: dict[str, Any], topology: PaiaTopology) -> tuple[str, str | None] | None:
    repo_name = _infer_repo_name(payload)
    if not repo_name:
        return None
    if repo_name in topology.agent_members and topology.agent_family_root:
        return topology.agent_family_root, repo_name
    if repo_name in topology.canonical_targets:
        return repo_name, None
    return None


def _build_question(repo: str, member: str | None, payload: dict[str, Any]) -> str:
    subject = member or repo
    component = str(payload.get("component") or "").strip()
    pattern = str(payload.get("pattern") or "").strip()
    summary = str(payload.get("change_summary") or "").strip()
    parts = [f"Apply agent-health fix for {subject}"]
    details: list[str] = []
    if component:
        details.append(component)
    if pattern:
        details.append(pattern)
    if details:
        parts.append(f"({', '.join(details)})")
    if summary:
        parts.append(f"- {summary}")
    return " ".join(parts).strip()


def _decision_from_payload(
    decision_id: str,
    payload: dict[str, Any],
    *,
    topology: PaiaTopology,
) -> DecisionRecord | None:
    target = _canonical_target(payload, topology)
    if target is None:
        return None
    repo, member = target
    context = dict(payload)
    context["source_queue"] = "agent_health_pending"
    context["canonical_repo"] = repo
    if member:
        context["agent_member"] = member
    if "options" not in context:
        context["options"] = ["yes", "no"]
    return DecisionRecord(
        id=decision_id,
        repo=repo,
        status="pending",
        question=_build_question(repo, member, payload),
        context=context,
        category="agent_health",
        created_at=str(payload.get("created_at") or datetime.now(timezone.utc).isoformat()),
    )


def load_pending_agent_health_decisions(
    *,
    config_dir: Path | None = None,
    workspace_root: Path | None = None,
) -> list[DecisionRecord]:
    """Load pending agent-health decisions as canonical PAIA decision records."""
    resolved_config = _workgraph_config_dir(config_dir)
    topology = _resolve_topology(workspace_root, resolved_config)
    if topology is None:
        return []

    records: list[DecisionRecord] = []
    for decision_id, payload in _load_pending_payload(_pending_path(resolved_config)).items():
        record = _decision_from_payload(decision_id, payload, topology=topology)
        if record is not None:
            records.append(record)
    records.sort(key=lambda record: record.created_at)
    return records


def answer_agent_health_decision(
    *,
    decision_id: str,
    answer: str,
    answered_via: str,
    config_dir: Path | None = None,
    workspace_root: Path | None = None,
) -> DecisionRecord | None:
    """Answer an agent-health decision, remove it from pending, and archive it."""
    resolved_config = _workgraph_config_dir(config_dir)
    topology = _resolve_topology(workspace_root, resolved_config)
    if topology is None:
        return None

    pending_path = _pending_path(resolved_config)
    pending_payload = _load_pending_payload(pending_path)
    raw_payload = pending_payload.get(decision_id)
    if raw_payload is None:
        return None

    record = _decision_from_payload(decision_id, raw_payload, topology=topology)
    if record is None:
        return None

    pending_payload.pop(decision_id, None)
    _save_pending_payload(pending_path, pending_payload)

    record.status = "answered"
    record.answer = answer
    record.answered_via = answered_via
    record.answered_at = datetime.now(timezone.utc).isoformat()

    archive_path = _answered_path(resolved_config)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_record_to_dict(record), sort_keys=True) + "\n")
    return record
