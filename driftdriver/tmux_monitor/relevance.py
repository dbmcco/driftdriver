# ABOUTME: Relevance scoring — match running agents to a target repo/context.
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RelevantAgent:
    session: str
    pane: str
    pane_id: str
    agent_type: str
    cwd: str
    title: str
    current_task: str
    summary: str
    relevance: str
    active_since: str
    tmux_session_age: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "pane": self.pane,
            "pane_id": self.pane_id,
            "type": self.agent_type,
            "cwd": self.cwd,
            "title": self.title,
            "current_task": self.current_task,
            "summary": self.summary,
            "relevance": self.relevance,
            "active_since": self.active_since,
            "tmux_session_age": self.tmux_session_age,
            "controllable": bool(self.pane_id),
        }


def _repo_name(cwd: str) -> str:
    return Path(cwd).name if cwd else ""


def score_relevance(
    status: dict[str, Any],
    target_repo: str = "",
    target_path: str = "",
) -> list[RelevantAgent]:
    agents: list[RelevantAgent] = []

    target_name = target_repo
    if target_path:
        target_name = Path(target_path).name
    if not target_name:
        return agents

    for sess_name, sess_data in status.get("sessions", {}).items():
        for pane_key, pd in sess_data.get("panes", {}).items():
            if pd.get("type") in ("shell", "idle"):
                continue

            cwd = pd.get("cwd", "")
            cwd_name = _repo_name(cwd)

            if cwd_name == target_name:
                relevance = "same_repo"
            elif cwd_name and target_name and (
                target_name in cwd or cwd_name in target_name
            ):
                relevance = "related"
            else:
                relevance = "unrelated"

            agents.append(RelevantAgent(
                session=sess_name,
                pane=pane_key,
                pane_id=pd.get("pane_id", ""),
                agent_type=pd.get("type", "unknown"),
                cwd=cwd,
                title=pd.get("title", ""),
                current_task=pd.get("current_task", ""),
                summary=pd.get("summary", ""),
                relevance=relevance,
                active_since=pd.get("active_since", ""),
                tmux_session_age=sess_data.get("created_at", ""),
            ))

    agents.sort(key=lambda a: {"same_repo": 0, "related": 1, "unrelated": 2}.get(a.relevance, 3))
    return agents


def format_relevant_json(
    status: dict[str, Any],
    target_repo: str = "",
    target_path: str = "",
    include_unrelated: bool = False,
) -> dict[str, Any]:
    agents = score_relevance(status, target_repo=target_repo, target_path=target_path)

    relevant = [a for a in agents if a.relevance != "unrelated"]
    all_agents = agents if include_unrelated else relevant

    return {
        "my_repo": target_repo or (Path(target_path).name if target_path else ""),
        "relevant_agents": [a.to_dict() for a in all_agents],
        "total_agents": len(agents),
        "relevant_count": len(relevant),
    }


def format_relevant_text(
    status: dict[str, Any],
    target_repo: str = "",
    target_path: str = "",
    include_unrelated: bool = False,
) -> str:
    data = format_relevant_json(status, target_repo, target_path, include_unrelated)
    lines: list[str] = []

    repo = data["my_repo"]
    lines.append(f"Repo: {repo}")
    lines.append(f"Relevant agents: {data['relevant_count']} of {data['total_agents']}")
    lines.append("")

    for a in data["relevant_agents"]:
        rel_marker = "***" if a["relevance"] == "same_repo" else " * "
        lines.append(f'{rel_marker} [{a["type"]}] {a["pane"]} ({a["session"]})')
        if a["title"]:
            lines.append(f'    title: {a["title"]}')
        if a["current_task"]:
            lines.append(f'    task:   {a["current_task"]}')
        if a["summary"]:
            lines.append(f'    summary: {a["summary"][:150]}')
        short_cwd = a["cwd"].replace(str(Path.home()), "~") if a["cwd"] else ""
        lines.append(f'    cwd: {short_cwd}')
        lines.append(f'    control: tmux send-keys -t {a["pane_id"]}')
        lines.append("")

    return "\n".join(lines)
