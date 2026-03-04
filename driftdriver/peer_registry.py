# ABOUTME: Discovers and queries workgraph peers for cross-repo federation
# ABOUTME: Wraps wg peer CLI with TTL cache and health checking

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PeerInfo:
    name: str
    path: str
    description: str = ""
    service_running: bool = False
    socket_path: str = ""
    pid: int | None = None
    task_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class HealthReport:
    peer_name: str
    reachable: bool
    service_running: bool
    latency_ms: float
    task_summary: dict[str, int] = field(default_factory=dict)
    error: str = ""


class PeerRegistry:
    """Cache-backed registry of workgraph peers."""

    def __init__(self, project_dir: Path, cache_ttl: float = 30.0) -> None:
        self.project_dir = project_dir
        self._cache: list[PeerInfo] = []
        self._cache_ts: float = 0.0
        self._cache_ttl = cache_ttl

    def _is_cache_valid(self) -> bool:
        return bool(self._cache) and (time.monotonic() - self._cache_ts) < self._cache_ttl

    def peers(self) -> list[PeerInfo]:
        """Return cached peers, refreshing if TTL expired."""
        if self._is_cache_valid():
            return list(self._cache)
        self._cache = discover_peers(self.project_dir)
        self._cache_ts = time.monotonic()
        return list(self._cache)

    def invalidate(self) -> None:
        """Force cache refresh on next access."""
        self._cache_ts = 0.0

    def get(self, name: str) -> PeerInfo | None:
        """Get a single peer by name, using detail endpoint."""
        return get_peer_detail(self.project_dir, name)

    def health(self, name: str) -> HealthReport:
        """Check health of a specific peer."""
        peer = self.get(name)
        if peer is None:
            return HealthReport(
                peer_name=name,
                reachable=False,
                service_running=False,
                latency_ms=0.0,
                error=f"peer '{name}' not found",
            )
        return check_peer_health(self.project_dir, peer)

    def socket(self, name: str) -> str | None:
        """Get socket path for a peer."""
        return get_peer_socket(self.project_dir, name)


def discover_peers(project_dir: Path) -> list[PeerInfo]:
    """Run `wg peer list --json` and parse into PeerInfo list."""
    result = subprocess.run(
        ["wg", "peer", "list", "--json"],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
    )
    if result.returncode != 0:
        return []

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return []

    peers = []
    for entry in data if isinstance(data, list) else []:
        peers.append(PeerInfo(
            name=entry.get("name", ""),
            path=entry.get("path", ""),
            description=entry.get("description", ""),
            service_running=entry.get("service_running", False),
            socket_path=entry.get("socket_path", ""),
            pid=entry.get("pid"),
            task_counts=entry.get("task_counts", {}),
        ))
    return peers


def get_peer_detail(project_dir: Path, name: str) -> PeerInfo | None:
    """Run `wg peer show <name> --json` and return PeerInfo or None."""
    result = subprocess.run(
        ["wg", "peer", "show", name, "--json"],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
    )
    if result.returncode != 0:
        return None

    try:
        entry = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(entry, dict):
        return None

    return PeerInfo(
        name=entry.get("name", name),
        path=entry.get("path", ""),
        description=entry.get("description", ""),
        service_running=entry.get("service_running", False),
        socket_path=entry.get("socket_path", ""),
        pid=entry.get("pid"),
        task_counts=entry.get("task_counts", {}),
    )


def check_peer_health(project_dir: Path, peer: PeerInfo) -> HealthReport:
    """Time a `wg peer show --json` call to measure reachability and latency."""
    start = time.monotonic()
    result = subprocess.run(
        ["wg", "peer", "show", peer.name, "--json"],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
    )
    latency_ms = (time.monotonic() - start) * 1000.0

    if result.returncode != 0:
        return HealthReport(
            peer_name=peer.name,
            reachable=False,
            service_running=False,
            latency_ms=latency_ms,
            error=result.stderr.strip(),
        )

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return HealthReport(
            peer_name=peer.name,
            reachable=True,
            service_running=False,
            latency_ms=latency_ms,
            error="invalid JSON response",
        )

    return HealthReport(
        peer_name=peer.name,
        reachable=True,
        service_running=data.get("service_running", False),
        latency_ms=latency_ms,
        task_summary=data.get("task_counts", {}),
    )


def get_peer_socket(project_dir: Path, name: str) -> str | None:
    """Get socket path for a peer from detail, falling back to convention."""
    detail = get_peer_detail(project_dir, name)
    if detail and detail.socket_path:
        return detail.socket_path
    if detail and detail.path:
        convention = str(Path(detail.path) / ".workgraph" / "service" / "daemon.sock")
        return convention
    return None


def register_peer(project_dir: Path, name: str, path: str, desc: str = "") -> bool:
    """Register a new peer via `wg peer add`."""
    cmd = ["wg", "peer", "add", name, "--path", path]
    if desc:
        cmd.extend(["--description", desc])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(project_dir),
    )
    return result.returncode == 0
