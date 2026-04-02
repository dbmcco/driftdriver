# driftdriver/paia_agent_health/fixes.py
# ABOUTME: Applies auto-fix skill edits and sends large-fix proposals via Telegram.
# ABOUTME: handle_agent_fix_decision() resolves pending approvals from Telegram chat.

from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from driftdriver.factory_brain.telegram import load_telegram_config, send_telegram
from driftdriver.paia_agent_health.analyzer import FixProposal
from driftdriver.paia_agent_health.fix_history import (
    FixRecord, add_fix, DEFAULT_PATH as DEFAULT_HISTORY_PATH
)

logger = logging.getLogger(__name__)

_EXPERIMENTS = os.environ.get("EXPERIMENTS_DIR", os.path.expanduser("~/projects/experiments"))
DEFAULT_PENDING_PATH = Path.home() / ".config" / "workgraph" / "agent_health_pending.json"
EVENTS_URL = os.environ.get("PAIA_EVENTS_URL", "http://localhost:3511")


def _resolve_skill_path(component: str, agent: str) -> Path | None:
    """Resolve a component path like 'skills/outreach_templates.md' to an absolute path."""
    candidates = [
        Path(_EXPERIMENTS) / agent / component,
        Path(_EXPERIMENTS) / agent / "skills" / Path(component).name,
        Path(_EXPERIMENTS) / component,
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _publish_event(event_type: str, data: dict) -> None:
    """Fire-and-forget POST to paia-events. Never raises."""
    try:
        import httpx
        payload = {
            "event_type": event_type,
            "actor_ref": "paia-healer",
            "data": data,
        }
        httpx.post(f"{EVENTS_URL}/v1/events", json=payload, timeout=5)
    except Exception as exc:
        logger.debug("_publish_event failed: %s", exc)


def _apply_diff(current_content: str, diff: str) -> str:
    """Apply a unified diff to content. Falls back to appending if patch fails."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".orig", delete=False) as orig:
        orig.write(current_content)
        orig_path = orig.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as patch_f:
        patch_f.write(diff)
        patch_path = patch_f.name
    try:
        result = subprocess.run(
            ["patch", "--no-backup-if-mismatch", orig_path, patch_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return Path(orig_path).read_text()
        logger.debug("patch failed, appending diff: %s", result.stderr)
        return current_content + "\n" + diff
    except Exception:
        return current_content + "\n" + diff
    finally:
        Path(orig_path).unlink(missing_ok=True)
        Path(patch_path).unlink(missing_ok=True)


def apply_fix(
    proposal: FixProposal,
    *,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> None:
    """Apply a small fix directly to the skill file. Log to fix history."""
    skill_path = _resolve_skill_path(proposal.finding.affected_component, proposal.finding.agent)
    if skill_path is None:
        logger.warning("Cannot resolve skill path for %s", proposal.finding.affected_component)
        return

    current = skill_path.read_text() if skill_path.exists() else ""
    patched = _apply_diff(current, proposal.diff)
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(patched)

    now = datetime.now(timezone.utc)
    record = FixRecord(
        fix_id=uuid.uuid4().hex[:8],
        applied_at=now.isoformat(),
        agent=proposal.finding.agent,
        component=proposal.finding.affected_component,
        finding_pattern=proposal.finding.pattern_type,
        change_summary=proposal.change_summary,
        diff=proposal.diff,
        auto_applied=True,
        check_after=(now + timedelta(days=7)).isoformat(),
        outcome=None,
    )
    add_fix(history_path, record)

    _publish_event("healer.fix.applied", {
        "agent": proposal.finding.agent,
        "component": proposal.finding.affected_component,
        "change_summary": proposal.change_summary,
        "auto_applied": True,
    })
    logger.info("auto-applied fix to %s/%s", proposal.finding.agent, proposal.finding.affected_component)


def build_proposal_message(proposal: FixProposal, dec_id: str) -> str:
    """Build the Telegram message for a large fix proposal."""
    return (
        f"🤖 Agent Health\n\n"
        f"*Agent:* {proposal.finding.agent}\n"
        f"*Pattern:* {proposal.finding.pattern_type} ({proposal.finding.severity})\n"
        f"*Component:* `{proposal.finding.affected_component}`\n\n"
        f"*Evidence ({proposal.finding.evidence_count}x):*\n"
        + "\n".join(f"  • {e[:120]}" for e in proposal.finding.evidence[:3])
        + f"\n\n*Proposed fix:* {proposal.change_summary}\n\n"
        f"```\n{proposal.diff[:800]}\n```\n\n"
        f"Reply `{dec_id} yes` to apply, `{dec_id} no` to skip."
    )


def store_pending_proposal(
    proposal: FixProposal,
    dec_id: str,
    *,
    pending_path: Path = DEFAULT_PENDING_PATH,
) -> None:
    """Store a pending proposal keyed by decision ID."""
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if pending_path.exists():
        try:
            existing = json.loads(pending_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    existing[dec_id] = {
        "agent": proposal.finding.agent,
        "component": proposal.finding.affected_component,
        "pattern": proposal.finding.pattern_type,
        "change_summary": proposal.change_summary,
        "diff": proposal.diff,
        "risk": proposal.risk,
    }
    pending_path.write_text(json.dumps(existing, indent=2))


def send_proposal(
    proposal: FixProposal,
    *,
    pending_path: Path = DEFAULT_PENDING_PATH,
) -> str | None:
    """Send a large fix proposal via Telegram. Returns dec_id or None on failure."""
    cfg = load_telegram_config()
    if not cfg:
        logger.warning("send_proposal: no Telegram config found")
        return None

    now = datetime.now(timezone.utc)
    dec_id = f"dec-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    store_pending_proposal(proposal, dec_id, pending_path=pending_path)

    msg = build_proposal_message(proposal, dec_id)
    sent = send_telegram(bot_token=cfg["bot_token"], chat_id=cfg["chat_id"], message=msg)
    if not sent:
        logger.warning("send_proposal: Telegram send failed for %s", dec_id)
    return dec_id


def handle_agent_fix_decision(
    dec_id: str,
    answer: str,
    *,
    pending_path: Path = DEFAULT_PENDING_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
) -> str:
    """Handle a user's decision on a pending fix proposal. Returns 'applied', 'skipped', or 'unknown'."""
    if not pending_path.exists():
        return "unknown"
    try:
        pending: dict = json.loads(pending_path.read_text())
    except (json.JSONDecodeError, OSError):
        return "unknown"

    proposal_data = pending.get(dec_id)
    if not proposal_data:
        return "unknown"

    answer_lower = answer.strip().lower()

    if answer_lower in ("yes", "y", "approve", "approved"):
        skill_path = _resolve_skill_path(proposal_data["component"], proposal_data["agent"])
        current = skill_path.read_text() if (skill_path and skill_path.exists()) else ""
        if skill_path:
            patched = _apply_diff(current, proposal_data["diff"])
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(patched)

        now = datetime.now(timezone.utc)
        record = FixRecord(
            fix_id=uuid.uuid4().hex[:8],
            applied_at=now.isoformat(),
            agent=proposal_data["agent"],
            component=proposal_data["component"],
            finding_pattern=proposal_data["pattern"],
            change_summary=proposal_data["change_summary"],
            diff=proposal_data["diff"],
            auto_applied=False,
            check_after=(now + timedelta(days=7)).isoformat(),
            outcome=None,
        )
        add_fix(history_path, record)
        _publish_event("healer.fix.applied", {
            "agent": proposal_data["agent"],
            "component": proposal_data["component"],
            "change_summary": proposal_data["change_summary"],
            "auto_applied": False,
        })
        del pending[dec_id]
        pending_path.write_text(json.dumps(pending, indent=2))
        return "applied"

    elif answer_lower in ("no", "n", "skip"):
        _publish_event("healer.fix.skipped", {
            "agent": proposal_data["agent"],
            "component": proposal_data["component"],
            "dec_id": dec_id,
        })
        del pending[dec_id]
        pending_path.write_text(json.dumps(pending, indent=2))
        return "skipped"

    return "unknown"
