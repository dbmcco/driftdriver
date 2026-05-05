"""Shared model route lookup for Driftdriver runtime callers."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class ModelRoute:
    id: str
    owner: str
    surface: str
    provider: str
    model: str
    base_url: str | None = None


def model_for_route(route_id: str) -> str:
    """Return the concrete model ID for a semantic route."""

    return route_for(route_id).model


def route_for(route_id: str) -> ModelRoute:
    """Resolve a semantic model route from the central Paia registry."""

    normalized = route_id.strip().lower()
    routes = _load_routes()
    if normalized not in routes:
        raise KeyError(f"Unknown model route: {route_id}")
    return routes[normalized]


@lru_cache(maxsize=1)
def _load_routes() -> dict[str, ModelRoute]:
    registry_path = _resolve_registry_path()
    raw = tomllib.loads(registry_path.read_text(encoding="utf-8"))
    surfaces_raw = raw.get("provider_surfaces") or {}
    routes_raw = raw.get("model_routes") or {}
    routes: dict[str, ModelRoute] = {}

    for route_id, body in routes_raw.items():
        if not isinstance(body, dict):
            continue
        surface = str(body.get("surface", "")).strip().lower()
        surface_cfg = surfaces_raw.get(surface) if isinstance(surfaces_raw, dict) else None
        base_url = body.get("base_url")
        if base_url is None and isinstance(surface_cfg, dict):
            base_url = surface_cfg.get("base_url")
        routes[str(route_id).strip().lower()] = ModelRoute(
            id=str(route_id).strip().lower(),
            owner=str(body.get("owner", "")).strip(),
            surface=surface,
            provider=str(body.get("provider", "")).strip().lower(),
            model=str(body.get("model", "")).strip(),
            base_url=str(base_url).strip().rstrip("/") if base_url else None,
        )
    return routes


def _resolve_registry_path() -> Path:
    configured = os.environ.get("PAIA_MODEL_ROUTE_REGISTRY_PATH", "").strip()
    package_root = Path(__file__).resolve().parents[1]
    candidates = [
        Path(configured) if configured else None,
        Path.cwd() / "../paia-agent-runtime/config/cognition-presets.toml",
        Path.cwd() / "../../paia-agent-runtime/config/cognition-presets.toml",
        package_root.parent / "paia-agent-runtime/config/cognition-presets.toml",
        package_root.parent.parent / "paia-agent-runtime/config/cognition-presets.toml",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        "Unable to find central model route registry. Set PAIA_MODEL_ROUTE_REGISTRY_PATH."
    )
