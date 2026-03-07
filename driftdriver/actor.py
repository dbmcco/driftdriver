# ABOUTME: Actor identity model for the Speedrift authority system.
# ABOUTME: Every entity touching the workgraph has a class, id, and name.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

ACTOR_CLASSES: tuple[str, ...] = (
    "human",
    "interactive",
    "worker",
    "daemon",
    "lane",
)

ALL_OPERATIONS: frozenset[str] = frozenset({"read", "claim", "create", "dispatch", "modify"})

DEFAULT_AUTHORITY: dict[str, frozenset[str]] = {
    "human": frozenset({"read", "claim", "create", "dispatch", "modify"}),
    "interactive": frozenset({"read", "claim", "create", "modify"}),
    "worker": frozenset({"read", "claim", "modify"}),
    "daemon": frozenset({"read", "create", "dispatch"}),
    "lane": frozenset({"read", "create"}),
}


@dataclass
class Actor:
    id: str
    actor_class: str
    name: str
    repo: str = ""

    def __post_init__(self) -> None:
        if self.actor_class not in ACTOR_CLASSES:
            raise ValueError(
                f"invalid actor_class {self.actor_class!r}; "
                f"must be one of {ACTOR_CLASSES}"
            )


def actor_to_dict(a: Actor) -> dict[str, Any]:
    """Serialize an Actor to a plain dict suitable for JSON."""
    return {
        "id": a.id,
        "actor_class": a.actor_class,
        "name": a.name,
        "repo": a.repo,
    }


def actor_from_dict(d: dict[str, Any]) -> Actor:
    """Deserialize a dict to an Actor. Raises ValueError on invalid class."""
    return Actor(
        id=d["id"],
        actor_class=d["actor_class"],
        name=d["name"],
        repo=d.get("repo", ""),
    )
