# ABOUTME: Builds redacted model registry status payloads for the ecosystem hub.
# ABOUTME: Shows route and credential coverage without exposing secret values.
from __future__ import annotations

import os
import re
import subprocess
import tomllib
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
MODEL_LITERAL_RE = re.compile(
    r"\b(?:gpt-[A-Za-z0-9_.:-]+|claude-[A-Za-z0-9_.:-]+|glm-[A-Za-z0-9_.:-]+|"
    r"gemini-[A-Za-z0-9_.:-]+|qwen[0-9A-Za-z_.:-]*|grok-[A-Za-z0-9_.:-]+)\b",
    re.IGNORECASE,
)
CREDENTIAL_REFERENCE_RE = re.compile(
    r"\b(?:ANTHROPIC_API_KEY|OPENAI_API_KEY|ZAI_API_KEY|XAI_API_KEY|"
    r"OPENROUTER_API_KEY|GEMINI_API_KEY|PERPLEXITY_API_KEY)\b"
)
SECRET_LITERAL_RE = re.compile(
    r"\b(?:"
    r"sk-ant-[A-Za-z0-9_-]{20,}|"
    r"sk-proj-[A-Za-z0-9_-]{20,}|"
    r"sk-[A-Za-z0-9]{20,}|"
    r"xai-[A-Za-z0-9]{20,}"
    r")"
)
SCAN_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".sh",
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".plist",
}
SCAN_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "__pycache__",
}
SECRET_SCAN_SKIP_GLOBS = [
    "tests/**",
    "**/tests/**",
    "test_*.py",
    "**/test_*.py",
    "**/__tests__/**",
    "**/*.test.ts",
    "**/*.spec.ts",
    "**/*.test.tsx",
    "**/*.spec.tsx",
]
MAX_SCAN_FILES_PER_REPO = 1200
PROBE_STATUSES = ("verified", "unresolved", "waived", "unsupported")
CRITICAL_PROBE_REPOS = {
    "caroline",
    "derek",
    "driftdriver",
    "grok-aurora-cli",
    "ingrid",
    "lodestar",
    "paia-agents",
    "paia-os",
    "planner",
    "samantha",
    "supernote",
    "workgraph",
}


def build_model_registry_status(
    *,
    workspace_root: Path | None = None,
    registry_path: Path | None = None,
    ecosystem_path: Path | None = None,
    env_files: list[Path] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    root = _resolve_workspace_root((workspace_root or Path("/Users/braydon/projects/experiments")).resolve())
    registry = _resolve_registry_path(root, registry_path)
    ecosystem = ecosystem_path or root / "speedrift-ecosystem" / "ecosystem.toml"
    generated_at = datetime.now(timezone.utc).isoformat()
    env_sources = env_files or [
        root.parent / ".env",
        root / "skills" / ".env",
    ]
    env_map = env if env is not None else os.environ

    raw = _load_toml(registry)
    ecosystem_raw = _load_toml(ecosystem) if ecosystem.exists() else {}
    env_file_keys = _env_file_keys(env_sources)

    credentials_raw = _dict(raw.get("credentials"))
    provider_defaults = _string_map(raw.get("provider_credential_defaults"))
    agent_credentials = _nested_string_map(raw.get("agent_credential_assignments"))
    service_credentials = _nested_string_map(raw.get("service_credential_assignments"))
    agent_assignments = _string_map(raw.get("agent_assignments"))
    service_assignments = _string_map(raw.get("service_assignments"))
    provider_surfaces = _dict(raw.get("provider_surfaces"))
    model_routes = _dict(raw.get("model_routes"))
    presets = _dict(raw.get("presets"))

    credential_owner_map = _credential_owner_map(
        provider_defaults=provider_defaults,
        agent_credentials=agent_credentials,
        service_credentials=service_credentials,
    )
    credentials = [
        _credential_row(
            credential_id=credential_id,
            body=_dict(body),
            owners=credential_owner_map.get(credential_id.lower(), []),
            env_map=env_map,
            env_file_keys=env_file_keys,
            generated_at=generated_at,
        )
        for credential_id, body in sorted(credentials_raw.items())
    ]

    route_owner_map = _route_owner_map(
        presets=presets,
        agent_assignments=agent_assignments,
        service_assignments=service_assignments,
    )
    route_rows = [
        _model_route_row(
            route_id=route_id,
            body=_dict(body),
            owners=route_owner_map.get(route_id, []),
            provider_surfaces=provider_surfaces,
            generated_at=generated_at,
        )
        for route_id, body in sorted(model_routes.items())
    ]
    route_by_id = {row["id"]: row for row in route_rows}
    credential_by_id = {row["id"].lower(): row for row in credentials}

    repo_rows = _repo_coverage_rows(
        ecosystem_raw=ecosystem_raw,
        ecosystem_path=ecosystem,
        workspace_root=root,
        service_assignments=service_assignments,
        service_credentials=service_credentials,
        agent_assignments=agent_assignments,
        agent_credentials=agent_credentials,
        credential_rows=credentials,
        credentials_by_id=credential_by_id,
        provider_defaults=provider_defaults,
        provider_surfaces=provider_surfaces,
        presets=presets,
        route_by_id=route_by_id,
        generated_at=generated_at,
    )

    strict_presets = [
        name
        for name, body in presets.items()
        if not any(
            _profile_fallback_enabled(_dict(body), _dict(profile))
            for profile in _preset_profiles(_dict(body)).values()
        )
    ]

    env_sources_payload = [
        {
            "path": str(path),
            "exists": path.exists(),
            "keys_detected": len(env_file_keys.get(path, set())),
        }
        for path in env_sources
    ]

    present_credentials = [row for row in credentials if row["present"]]
    credential_gaps = [row for row in credentials if not row["present"]]
    fallback_profiles = _fallback_profiles(presets)

    return {
        "schema": 1,
        "generated_at": generated_at,
        "workspace_root": str(root),
        "registry_path": str(registry),
        "registry_exists": registry.exists(),
        "ecosystem_path": str(ecosystem),
        "active_preset": str(raw.get("active_preset") or ""),
        "summary": {
            "credentials_total": len(credentials),
            "credentials_present": len(present_credentials),
            "credentials_missing": len(credential_gaps),
            "model_routes_total": len(route_rows),
            "provider_surfaces_total": len(provider_surfaces),
            "presets_total": len(presets),
            "strict_presets_total": len(strict_presets),
            "fallback_profiles_total": len(fallback_profiles),
            "repo_rows_total": len(repo_rows),
            "repo_rows_centralized": sum(1 for row in repo_rows if str(row["status"]).startswith("centralized")),
            "repo_rows_attention": sum(1 for row in repo_rows if row["severity"] in {"warn", "bad"}),
            "repo_rows_not_model_using": sum(1 for row in repo_rows if row["status"] == "not-model-using"),
            "probe_status_counts": _probe_status_counts(repo_rows),
        },
        "env_sources": env_sources_payload,
        "credentials": credentials,
        "credential_gaps": credential_gaps,
        "provider_credential_defaults": provider_defaults,
        "agent_credential_assignments": agent_credentials,
        "service_credential_assignments": service_credentials,
        "agent_assignments": _assignment_rows(agent_assignments, presets),
        "service_assignments": _assignment_rows(service_assignments, presets),
        "provider_surfaces": [
            {"id": surface_id, **_redacted_surface_row(_dict(body))}
            for surface_id, body in sorted(provider_surfaces.items())
        ],
        "model_routes": route_rows,
        "presets": _preset_rows(presets),
        "fallback_profiles": fallback_profiles,
        "repo_coverage": repo_rows,
    }


def _resolve_registry_path(root: Path, registry_path: Path | None) -> Path:
    if registry_path is not None:
        return registry_path.resolve()
    configured = os.environ.get("PAIA_MODEL_ROUTE_REGISTRY_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (root / "paia-agent-runtime" / "config" / "cognition-presets.toml").resolve()


def _resolve_workspace_root(root: Path) -> Path:
    if (root / "paia-agent-runtime" / "config" / "cognition-presets.toml").exists():
        return root
    experiments = root / "experiments"
    if (experiments / "paia-agent-runtime" / "config" / "cognition-presets.toml").exists():
        return experiments.resolve()
    return root


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(val) for key, val in value.items() if isinstance(val, str)}


def _nested_string_map(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for owner, assignments in value.items():
        if not isinstance(assignments, dict):
            continue
        nested = {str(provider): str(credential) for provider, credential in assignments.items() if isinstance(credential, str)}
        if nested:
            out[str(owner)] = nested
    return out


def _env_file_keys(paths: list[Path]) -> dict[Path, set[str]]:
    out: dict[Path, set[str]] = {}
    for path in paths:
        keys: set[str] = set()
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                match = ENV_ASSIGNMENT_RE.match(line)
                if match:
                    keys.add(match.group(1))
        out[path] = keys
    return out


def _credential_owner_map(
    *,
    provider_defaults: dict[str, str],
    agent_credentials: dict[str, dict[str, str]],
    service_credentials: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}

    def add(credential_id: str, owner: str) -> None:
        owners.setdefault(credential_id.lower(), []).append(owner)

    for provider, credential_id in provider_defaults.items():
        add(credential_id, f"provider_default:{provider}")
    for agent, assignments in agent_credentials.items():
        for provider, credential_id in assignments.items():
            add(credential_id, f"agent:{agent}:{provider}")
    for service, assignments in service_credentials.items():
        for provider, credential_id in assignments.items():
            add(credential_id, f"service:{service}:{provider}")
    return {credential_id: sorted(set(values)) for credential_id, values in owners.items()}


def _credential_row(
    *,
    credential_id: str,
    body: dict[str, Any],
    owners: list[str],
    env_map: Mapping[str, str],
    env_file_keys: dict[Path, set[str]],
    generated_at: str,
) -> dict[str, Any]:
    env_var = str(body.get("env_var") or "")
    source_files = [str(path) for path, keys in env_file_keys.items() if env_var and env_var in keys]
    present_in_environment = bool(env_var and env_map.get(env_var))
    present = present_in_environment or bool(source_files)
    completion = _completion_metadata(
        body,
        default_status="verified" if present else "unresolved",
        default_last_verified_at=generated_at if present else "",
        default_exception_reason="",
        default_owner_next_step=(
            "Keep the central secret alias present."
            if present
            else "Populate the central secret source or add explicit owner waiver metadata."
        ),
        generated_at=generated_at,
    )
    return {
        "id": credential_id,
        "provider": str(body.get("provider") or ""),
        "source": str(body.get("source") or ""),
        "env_var": env_var,
        "secret_ref": str(body.get("secret_ref") or ""),
        "present": present,
        "present_in_environment": present_in_environment,
        "present_in_env_files": bool(source_files),
        "source_files": source_files,
        "owners": owners,
        **completion,
    }


def _route_owner_map(
    *,
    presets: dict[str, Any],
    agent_assignments: dict[str, str],
    service_assignments: dict[str, str],
) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}

    def add(route_id: str, owner: str) -> None:
        owners.setdefault(route_id, []).append(owner)

    for preset_name, preset_body in presets.items():
        for profile, profile_body in _dict(preset_body).items():
            for route_id in _dict(profile_body).get("routes") or []:
                if isinstance(route_id, str):
                    add(route_id, f"preset:{preset_name}:{profile}")
    for agent, preset_name in agent_assignments.items():
        for route_id in _preset_route_ids(presets, preset_name):
            add(route_id, f"agent:{agent}")
    for service, preset_name in service_assignments.items():
        for route_id in _preset_route_ids(presets, preset_name):
            add(route_id, f"service:{service}")
    return {route_id: sorted(set(values)) for route_id, values in owners.items()}


def _preset_route_ids(presets: dict[str, Any], preset_name: str) -> list[str]:
    preset = _dict(presets.get(preset_name))
    route_ids: list[str] = []
    for profile_body in _preset_profiles(preset).values():
        for route_id in _dict(profile_body).get("routes") or []:
            if isinstance(route_id, str):
                route_ids.append(route_id)
    return route_ids


def _preset_profiles(preset: dict[str, Any]) -> dict[str, Any]:
    profiles = preset.get("profiles")
    if isinstance(profiles, dict):
        return profiles
    return {key: value for key, value in preset.items() if isinstance(value, dict)}


def _profile_fallback_enabled(preset: dict[str, Any], profile: dict[str, Any]) -> bool:
    if "fallback_enabled" in profile:
        return bool(profile.get("fallback_enabled"))
    return bool(preset.get("fallback_enabled"))


def _model_route_row(
    *,
    route_id: str,
    body: dict[str, Any],
    owners: list[str],
    provider_surfaces: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    surface_id = str(body.get("surface") or "")
    surface = _dict(provider_surfaces.get(surface_id))
    completion = _completion_metadata(
        body,
        default_status="unresolved",
        default_last_verified_at="",
        default_exception_reason="",
        default_owner_next_step="Add a route-level probe or explicit waiver before integration validation.",
        generated_at=generated_at,
    )
    return {
        "id": route_id,
        "owner": str(body.get("owner") or ""),
        "surface": surface_id,
        "transport": _surface_transport(surface_id, surface),
        "provider": str(body.get("provider") or ""),
        "model": str(body.get("model") or ""),
        "quality_tier": str(body.get("quality_tier") or ""),
        "cost_tier": str(body.get("cost_tier") or ""),
        "source": str(body.get("source") or ""),
        "last_reviewed": str(body.get("last_reviewed") or ""),
        "supports_tools": bool(body.get("supports_tools")),
        "supports_streaming": bool(body.get("supports_streaming")),
        "supports_json_schema": bool(body.get("supports_json_schema")),
        "owners": owners,
        **completion,
    }


def _redacted_surface_row(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": str(body.get("provider") or ""),
        "transport": _surface_transport("", body),
        "base_url": str(body.get("base_url") or ""),
        "api_key_env": str(body.get("api_key_env") or ""),
        "api_key_required": _surface_api_key_required(body),
        "supports_tools": bool(body.get("supports_tools")),
        "supports_streaming": bool(body.get("supports_streaming")),
        "supports_json_schema": bool(body.get("supports_json_schema")),
    }


def _surface_transport(surface_id: str, body: dict[str, Any]) -> str:
    provider = str(body.get("provider") or "").strip().lower()
    base_url = str(body.get("base_url") or "").strip().lower()
    api_key_required = _surface_api_key_required(body)
    if surface_id.endswith("_cli") or provider == "codex":
        return "cli"
    if provider == "ollama" or "localhost" in base_url or "127.0.0.1" in base_url:
        return "local"
    if base_url or api_key_required or body.get("api_key_env"):
        return "api"
    return "adapter"


def _surface_api_key_required(body: dict[str, Any]) -> bool:
    if "api_key_required" in body:
        return bool(body.get("api_key_required"))
    return bool(body.get("api_key_env") or body.get("base_url"))


def _preset_rows(presets: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for preset_name, preset_body in sorted(presets.items()):
        preset = _dict(preset_body)
        profiles = []
        for profile, profile_body in sorted(_preset_profiles(preset).items()):
            body = _dict(profile_body)
            profiles.append(
                {
                    "profile": profile,
                    "routes": [str(route) for route in body.get("routes") or []],
                    "fallback_enabled": _profile_fallback_enabled(preset, body),
                }
            )
        rows.append({"id": preset_name, "profiles": profiles})
    return rows


def _fallback_profiles(presets: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for preset_name, preset_body in sorted(presets.items()):
        preset = _dict(preset_body)
        for profile, profile_body in sorted(_preset_profiles(preset).items()):
            body = _dict(profile_body)
            if _profile_fallback_enabled(preset, body):
                rows.append(
                    {
                        "preset": preset_name,
                        "profile": profile,
                        "routes": [str(route) for route in body.get("routes") or []],
                    }
                )
    return rows


def _assignment_rows(assignments: dict[str, str], presets: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": owner,
            "preset": preset_name,
            "known_preset": preset_name in presets,
            "route_ids": _preset_route_ids(presets, preset_name),
        }
        for owner, preset_name in sorted(assignments.items())
    ]


def _repo_coverage_rows(
    *,
    ecosystem_raw: dict[str, Any],
    ecosystem_path: Path,
    workspace_root: Path,
    service_assignments: dict[str, str],
    service_credentials: dict[str, dict[str, str]],
    agent_assignments: dict[str, str],
    agent_credentials: dict[str, dict[str, str]],
    credential_rows: list[dict[str, Any]],
    credentials_by_id: dict[str, dict[str, Any]],
    provider_defaults: dict[str, str],
    provider_surfaces: dict[str, Any],
    presets: dict[str, Any],
    route_by_id: dict[str, dict[str, Any]],
    generated_at: str,
) -> list[dict[str, Any]]:
    repos = _dict(ecosystem_raw.get("repos"))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for repo_name, body in sorted(repos.items()):
        seen.add(str(repo_name))
        route_assignment = service_assignments.get(str(repo_name)) or agent_assignments.get(str(repo_name))
        credential_assignment = service_credentials.get(str(repo_name)) or agent_credentials.get(str(repo_name)) or {}
        rows.append(
            _repo_row(
                repo_name=str(repo_name),
                body=_dict(body),
                repo_path=_repo_path_for(str(repo_name), _dict(body), ecosystem_path, workspace_root),
                route_assignment=route_assignment or "",
                credential_assignment=credential_assignment,
                credential_rows=credential_rows,
                credentials_by_id=credentials_by_id,
                provider_defaults=provider_defaults,
                provider_surfaces=provider_surfaces,
                presets=presets,
                route_by_id=route_by_id,
                generated_at=generated_at,
            )
        )

    for service in sorted(set(service_assignments) | set(service_credentials)):
        if service in seen:
            continue
        rows.append(
            _repo_row(
                repo_name=service,
                body={"role": "service", "lifecycle": "registry-only"},
                repo_path=None,
                route_assignment=service_assignments.get(service, ""),
                credential_assignment=service_credentials.get(service, {}),
                credential_rows=credential_rows,
                credentials_by_id=credentials_by_id,
                provider_defaults=provider_defaults,
                provider_surfaces=provider_surfaces,
                presets=presets,
                route_by_id=route_by_id,
                generated_at=generated_at,
            )
        )

    for agent in sorted(set(agent_assignments) | set(agent_credentials)):
        if agent in seen:
            continue
        rows.append(
            _repo_row(
                repo_name=agent,
                body={"role": "agent", "lifecycle": "registry-only"},
                repo_path=None,
                route_assignment=agent_assignments.get(agent, ""),
                credential_assignment=agent_credentials.get(agent, {}),
                credential_rows=credential_rows,
                credentials_by_id=credentials_by_id,
                provider_defaults=provider_defaults,
                provider_surfaces=provider_surfaces,
                presets=presets,
                route_by_id=route_by_id,
                generated_at=generated_at,
            )
        )

    return rows


def _repo_row(
    *,
    repo_name: str,
    body: dict[str, Any],
    repo_path: Path | None,
    route_assignment: str,
    credential_assignment: dict[str, str],
    credential_rows: list[dict[str, Any]],
    credentials_by_id: dict[str, dict[str, Any]],
    provider_defaults: dict[str, str],
    provider_surfaces: dict[str, Any],
    presets: dict[str, Any],
    route_by_id: dict[str, dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    preset_route_ids = _preset_route_ids_for_owner(presets, route_assignment, repo_name) if route_assignment else []
    route_ids = _unique_values(preset_route_ids + _owned_route_ids_for_repo(repo_name, route_by_id))
    routes = [
        _route_entry_for_preset_route(route_id, route_by_id=route_by_id, provider_surfaces=provider_surfaces)
        for route_id in route_ids
    ]
    routes = [route for route in routes if route]
    providers = sorted({str(route.get("provider") or "") for route in routes if route.get("provider")})
    transports = sorted({str(route.get("transport") or "") for route in routes if route.get("transport")})
    resolved_credentials = _resolved_credentials(
        providers=providers,
        explicit_assignments=credential_assignment,
        provider_defaults=provider_defaults,
        credentials_by_id=credentials_by_id,
    )
    missing_credentials = [row for row in resolved_credentials if not row.get("present")]
    matching_credentials = _matching_credentials_for_repo(repo_name, credential_rows)
    signals = _repo_model_signals(repo_path)
    status, severity, next_action = _repo_status(
        body=body,
        route_assignment=route_assignment,
        credential_assignment=credential_assignment,
        resolved_credentials=resolved_credentials,
        missing_credentials=missing_credentials,
        matching_credentials=matching_credentials,
        transports=transports,
        signals=signals,
        route_ids=route_ids,
    )
    probe = _repo_probe_metadata(
        repo_name=repo_name,
        body=body,
        generated_at=generated_at,
        status=status,
        severity=severity,
        route_ids=route_ids,
        missing_credentials=missing_credentials,
        next_action=next_action,
    )
    return {
        "repo": repo_name,
        "path": str(repo_path) if repo_path is not None else "",
        "role": str(body.get("role") or ""),
        "lifecycle": str(body.get("lifecycle") or ""),
        "route_preset": route_assignment,
        "route_ids": route_ids,
        "providers": providers,
        "transports": transports,
        "credential_assignments": credential_assignment,
        "resolved_credentials": [
            {
                "id": str(row.get("id") or ""),
                "provider": str(row.get("provider") or ""),
                "env_var": str(row.get("env_var") or ""),
                "present": bool(row.get("present")),
            }
            for row in resolved_credentials
        ],
        "missing_credentials": [
            {
                "id": str(row.get("id") or ""),
                "provider": str(row.get("provider") or ""),
                "env_var": str(row.get("env_var") or ""),
            }
            for row in missing_credentials
        ],
        "matching_credentials": matching_credentials,
        "signals": signals,
        "status": status,
        "severity": severity,
        "next_action": next_action,
        **probe,
    }


def _route_entry_for_preset_route(
    route_id: str,
    *,
    route_by_id: dict[str, dict[str, Any]],
    provider_surfaces: dict[str, Any],
) -> dict[str, Any]:
    if route_id in route_by_id:
        return route_by_id[route_id]
    surface = _dict(provider_surfaces.get(route_id))
    if surface:
        return {
            "id": route_id,
            "surface": route_id,
            "transport": _surface_transport(route_id, surface),
            "provider": str(surface.get("provider") or ""),
        }
    return {"id": route_id, "surface": route_id, "transport": "unknown", "provider": ""}


def _preset_route_ids_for_owner(presets: dict[str, Any], preset_name: str, owner: str) -> list[str]:
    preset = _dict(presets.get(preset_name))
    profiles = _preset_profiles(preset)
    selected_profile_names = _profile_names_for_owner(owner, profiles)
    route_ids: list[str] = []
    for profile_name in selected_profile_names:
        profile_body = _dict(profiles.get(profile_name))
        for route_id in profile_body.get("routes") or []:
            if isinstance(route_id, str):
                route_ids.append(route_id)
    return route_ids


def _profile_names_for_owner(owner: str, profiles: dict[str, Any]) -> list[str]:
    normalized = owner.strip().lower()
    if normalized in {"samantha", "caroline", "derek", "ingrid"}:
        return [name for name in ("companion_chat",) if name in profiles]
    if normalized == "planner":
        return [name for name in ("task_planning",) if name in profiles]
    if normalized in {"paia-os", "paia_os"}:
        return [name for name in profiles if name.startswith("paia_os_")]
    if normalized in {"supernote", "paia-supernote", "paia_supernote"}:
        return [name for name in profiles if name.startswith("supernote_")]
    if normalized in {"paia-meetings", "meetings"}:
        return [name for name in profiles if name.startswith("meetings_")]
    return sorted(profiles)


def _repo_path_for(
    repo_name: str,
    body: dict[str, Any],
    ecosystem_path: Path,
    workspace_root: Path,
) -> Path:
    path_raw = str(body.get("path") or "").strip()
    if path_raw:
        candidate = Path(path_raw).expanduser()
        if not candidate.is_absolute():
            candidate = (ecosystem_path.parent / candidate).resolve()
        return candidate
    return workspace_root / repo_name


def _resolved_credentials(
    *,
    providers: list[str],
    explicit_assignments: dict[str, str],
    provider_defaults: dict[str, str],
    credentials_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    credential_ids: list[str] = []
    for provider in providers:
        credential_id = explicit_assignments.get(provider) or provider_defaults.get(provider)
        if credential_id:
            credential_ids.append(credential_id)
    for credential_id in explicit_assignments.values():
        credential_ids.append(credential_id)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for credential_id in credential_ids:
        normalized = credential_id.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        row = credentials_by_id.get(normalized)
        if row is not None:
            rows.append(row)
    return rows


def _repo_status(
    *,
    body: dict[str, Any],
    route_assignment: str,
    credential_assignment: dict[str, str],
    resolved_credentials: list[dict[str, Any]],
    missing_credentials: list[dict[str, Any]],
    matching_credentials: list[str],
    transports: list[str],
    signals: dict[str, Any],
    route_ids: list[str],
) -> tuple[str, str, str]:
    if signals.get("hardcoded_secret_files", 0):
        return "hardcoded-secret-found", "bad", "Move embedded secret to central secret alias; keep value redacted."
    if signals.get("model_literal_files", 0) and _repo_route_waiver(body):
        return "waived", "muted", _repo_waiver_next_action(body)
    if route_assignment or route_ids or credential_assignment or resolved_credentials:
        if missing_credentials:
            return "needs-secret-source", "warn", "Populate the central secret source for assigned credential aliases."
        if transports and all(transport == "cli" for transport in transports):
            return "centralized-cli", "ok", "No API migration required; keep CLI wrapper resolving through registry."
        if transports and all(transport == "local" for transport in transports):
            return "centralized-local", "ok", "No hosted API key required; keep local service/model route registered."
        if "api" in transports:
            return "centralized-api", "ok", "Verify live probe and remove any remaining local pins."
        return "centralized", "ok", "Verify route probe and keep assignment current."
    if signals.get("model_literal_files", 0):
        return "hardcoded-route-found", "bad", "Replace model literals with a named registry route or mark as documented non-runtime data."
    if matching_credentials or signals.get("credential_reference_files", 0):
        return "local-env-pending-migration", "warn", "Map this repo to app-specific credential aliases or mark it as not using models."
    return "not-model-using", "muted", "No model registry work detected from current signals."


def _repo_route_waiver(body: dict[str, Any]) -> bool:
    nested = _dict(body.get("model_registry"))
    status = str(nested.get("probe_status") or body.get("probe_status") or "").strip().lower()
    return status == "waived"


def _repo_waiver_next_action(body: dict[str, Any]) -> str:
    nested = _dict(body.get("model_registry"))
    return str(
        nested.get("owner_next_step")
        or body.get("owner_next_step")
        or "Keep waiver metadata current; add a registry route if runtime model calls are introduced."
    )


def _matching_credentials_for_repo(repo_name: str, credential_rows: list[dict[str, Any]]) -> list[str]:
    normalized_repo = _normalize_owner(repo_name)
    tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", repo_name.lower())
        if token and token not in {"cli", "service"}
    ]
    matches: list[str] = []
    for row in credential_rows:
        credential_id = str(row.get("id") or "")
        normalized_credential = _normalize_owner(credential_id)
        if normalized_repo and (normalized_repo in normalized_credential or normalized_credential in normalized_repo):
            matches.append(credential_id)
            continue
        if tokens and all(token in normalized_credential for token in tokens[:2]):
            matches.append(credential_id)
    return sorted(set(matches))


def _repo_model_signals(repo_path: Path | None) -> dict[str, Any]:
    base = {
        "path_exists": bool(repo_path and repo_path.exists()),
        "scanned_files": 0,
        "model_literal_files": 0,
        "credential_reference_files": 0,
        "hardcoded_secret_files": 0,
    }
    if repo_path is None or not repo_path.exists() or not repo_path.is_dir():
        return base
    model_files = _rg_matching_files(repo_path, MODEL_LITERAL_RE.pattern)
    credential_files = _rg_matching_files(repo_path, CREDENTIAL_REFERENCE_RE.pattern)
    secret_files = _rg_matching_files(repo_path, SECRET_LITERAL_RE.pattern, exclude_globs=SECRET_SCAN_SKIP_GLOBS)
    base["model_literal_files"] = len(model_files)
    base["credential_reference_files"] = len(credential_files)
    base["hardcoded_secret_files"] = len(secret_files)
    base["scanned_files"] = max(len(model_files), len(credential_files), len(secret_files))
    return base


def _rg_matching_files(repo_path: Path, pattern: str, *, exclude_globs: list[str] | None = None) -> list[str]:
    glob_exts = ",".join(sorted(ext.lstrip(".") for ext in SCAN_EXTENSIONS))
    cmd = [
        "rg",
        "--files-with-matches",
        "--max-count",
        "1",
        "--glob",
        f"*.{{{glob_exts}}}",
    ]
    for skip in sorted(SCAN_SKIP_DIRS):
        cmd.extend(["--glob", f"!{skip}/**"])
    for glob in exclude_globs or []:
        cmd.extend(["--glob", f"!{glob}"])
    cmd.extend([pattern, str(repo_path)])
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode not in {0, 1}:
        return []
    return [line for line in result.stdout.splitlines() if line][:MAX_SCAN_FILES_PER_REPO]


def _normalize_owner(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _completion_metadata(
    body: dict[str, Any],
    *,
    default_status: str,
    default_last_verified_at: str,
    default_exception_reason: str,
    default_owner_next_step: str,
    generated_at: str,
) -> dict[str, str]:
    nested = _dict(body.get("model_registry"))
    status = str(nested.get("probe_status") or body.get("probe_status") or default_status).strip().lower()
    if status not in PROBE_STATUSES:
        status = default_status
    last_verified_at = str(
        nested.get("last_verified_at") or body.get("last_verified_at") or default_last_verified_at
    )
    if status == "verified" and not last_verified_at:
        last_verified_at = generated_at
    return {
        "probe_status": status,
        "last_verified_at": last_verified_at,
        "exception_reason": str(
            nested.get("exception_reason") or body.get("exception_reason") or default_exception_reason
        ),
        "owner_next_step": str(
            nested.get("owner_next_step") or body.get("owner_next_step") or default_owner_next_step
        ),
    }


def _repo_probe_metadata(
    *,
    repo_name: str,
    body: dict[str, Any],
    generated_at: str,
    status: str,
    severity: str,
    route_ids: list[str],
    missing_credentials: list[dict[str, Any]],
    next_action: str,
) -> dict[str, str]:
    if _has_completion_metadata(body):
        return _completion_metadata(
            body,
            default_status="unresolved",
            default_last_verified_at="",
            default_exception_reason="",
            default_owner_next_step=next_action,
            generated_at=generated_at,
        )

    if severity in {"warn", "bad"} or missing_credentials:
        return _completion_metadata(
            {},
            default_status="unresolved",
            default_last_verified_at="",
            default_exception_reason="",
            default_owner_next_step=next_action,
            generated_at=generated_at,
        )

    if status.startswith("centralized") and (route_ids or _is_critical_probe_repo(repo_name)):
        return _completion_metadata(
            {},
            default_status="verified",
            default_last_verified_at=generated_at,
            default_exception_reason="",
            default_owner_next_step="Keep registry route and credential aliases current.",
            generated_at=generated_at,
        )

    if _is_critical_probe_repo(repo_name):
        return _completion_metadata(
            {},
            default_status="unresolved",
            default_last_verified_at="",
            default_exception_reason="",
            default_owner_next_step="Add registry route/credential metadata or an explicit waiver.",
            generated_at=generated_at,
        )

    return _completion_metadata(
        {},
        default_status="unsupported",
        default_last_verified_at="",
        default_exception_reason="",
        default_owner_next_step="No model-registry probe is defined for this row.",
        generated_at=generated_at,
    )


def _has_completion_metadata(body: dict[str, Any]) -> bool:
    nested = _dict(body.get("model_registry"))
    keys = {"probe_status", "last_verified_at", "exception_reason", "owner_next_step"}
    return any(key in body for key in keys) or any(key in nested for key in keys)


def _owned_route_ids_for_repo(repo_name: str, route_by_id: dict[str, dict[str, Any]]) -> list[str]:
    normalized_repo = _normalize_owner(repo_name)
    matches: list[str] = []
    for route_id, route in route_by_id.items():
        normalized_owner = _normalize_owner(str(route.get("owner") or ""))
        if not normalized_owner:
            continue
        if normalized_owner == normalized_repo or normalized_repo in normalized_owner or normalized_owner in normalized_repo:
            matches.append(route_id)
    return sorted(matches)


def _unique_values(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _is_critical_probe_repo(repo_name: str) -> bool:
    normalized = repo_name.strip().lower()
    return normalized in CRITICAL_PROBE_REPOS


def _probe_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in PROBE_STATUSES}
    for row in rows:
        status = str(row.get("probe_status") or "unsupported")
        if status not in counts:
            status = "unsupported"
        counts[status] += 1
    return counts
