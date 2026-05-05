from __future__ import annotations

from pathlib import Path

from driftdriver.ecosystem_hub.model_registry_dashboard import render_model_registry_dashboard_html
from driftdriver.ecosystem_hub.model_registry_status import build_model_registry_status


def test_model_registry_status_redacts_secret_values_and_reports_presence(tmp_path: Path) -> None:
    workspace = tmp_path / "experiments"
    workspace.mkdir()
    registry = workspace / "cognition-presets.toml"
    registry.write_text(
        """
active_preset = "zai_glm51_standard"

[credentials.paia_zai]
provider = "zai"
source = "env"
env_var = "PAIA_ZAI_API_KEY"

[credentials.lodestar_anthropic]
provider = "anthropic"
source = "env"
env_var = "LODESTAR_ANTHROPIC_API_KEY"

[provider_credential_defaults]
zai = "paia_zai"

[service_credential_assignments.lodestar]
anthropic = "lodestar_anthropic"

[provider_surfaces.zai_coding]
provider = "zai"
base_url = "https://api.z.ai/api/coding/paas/v4"
api_key_env = "ZAI_API_KEY"
supports_tools = true

[model_routes."paia.agent_cognition"]
owner = "paia-agent-runtime"
surface = "zai_coding"
provider = "zai"
model = "glm-5.1"

[agent_assignments]
samantha = "zai_glm51_standard"

[presets.zai_glm51_standard.companion_chat]
routes = ["paia.agent_cognition"]
fallback_enabled = false
""",
        encoding="utf-8",
    )
    ecosystem = workspace / "ecosystem.toml"
    ecosystem.write_text(
        """
[repos.lodestar]
role = "product"
lifecycle = "active"

[repos.paia-os]
role = "service"
lifecycle = "active"
""",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("PAIA_ZAI_API_KEY=super-secret-value\n", encoding="utf-8")

    payload = build_model_registry_status(
        workspace_root=tmp_path,
        registry_path=registry,
        ecosystem_path=ecosystem,
        env_files=[env_file],
        env={},
    )

    assert payload["active_preset"] == "zai_glm51_standard"
    assert payload["summary"]["credentials_total"] == 2
    assert payload["summary"]["credentials_present"] == 1
    assert payload["summary"]["fallback_profiles_total"] == 0
    assert "super-secret-value" not in str(payload)

    paia_zai = next(row for row in payload["credentials"] if row["id"] == "paia_zai")
    assert paia_zai["present"] is True
    assert paia_zai["present_in_env_files"] is True
    assert paia_zai["env_var"] == "PAIA_ZAI_API_KEY"

    lodestar = next(row for row in payload["repo_coverage"] if row["repo"] == "lodestar")
    assert lodestar["status"] == "needs-secret-source"
    assert lodestar["credential_assignments"] == {"anthropic": "lodestar_anthropic"}

    samantha = next(row for row in payload["repo_coverage"] if row["repo"] == "samantha")
    assert samantha["status"] == "centralized-api"
    assert samantha["route_preset"] == "zai_glm51_standard"


def test_model_registry_dashboard_fetches_redacted_api() -> None:
    html = render_model_registry_dashboard_html()

    assert "/api/model-registry" in html
    assert "Model Registry" in html
    assert "Probe" in html
    assert "owner_next_step" in html


def test_secret_scan_ignores_test_fixture_keys(tmp_path: Path) -> None:
    workspace = tmp_path / "experiments"
    repo = workspace / "fixture-app"
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_auth.py").write_text(
        'def test_fixture_key():\n    assert "sk-ant-AAAAAAAAAAAAAAAAAAAAAAAA" \n',
        encoding="utf-8",
    )
    registry = workspace / "cognition-presets.toml"
    registry.write_text("", encoding="utf-8")
    ecosystem = workspace / "ecosystem.toml"
    ecosystem.write_text(
        """
[repos.fixture-app]
role = "service"
lifecycle = "active"
""",
        encoding="utf-8",
    )

    payload = build_model_registry_status(
        workspace_root=workspace,
        registry_path=registry,
        ecosystem_path=ecosystem,
        env_files=[],
        env={},
    )

    row = next(row for row in payload["repo_coverage"] if row["repo"] == "fixture-app")
    assert row["signals"]["hardcoded_secret_files"] == 0
    assert row["status"] != "hardcoded-secret-found"


def test_model_registry_status_honors_repo_route_waiver(tmp_path: Path) -> None:
    workspace = tmp_path / "experiments"
    repo = workspace / "docs-only-app"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("Uses Claude Code as an operator CLI.\n", encoding="utf-8")
    (repo / "config.py").write_text('LABEL = "claude-code"\n', encoding="utf-8")
    registry = workspace / "cognition-presets.toml"
    registry.write_text("", encoding="utf-8")
    ecosystem = workspace / "ecosystem.toml"
    ecosystem.write_text(
        """
[repos.docs-only-app]
role = "service"
lifecycle = "active"
probe_status = "waived"
exception_reason = "Scanner matches CLI labels and documentation, not runtime model routing."
owner_next_step = "Keep this waiver unless runtime model calls are added."
""",
        encoding="utf-8",
    )

    payload = build_model_registry_status(
        workspace_root=workspace,
        registry_path=registry,
        ecosystem_path=ecosystem,
        env_files=[],
        env={},
    )

    row = next(row for row in payload["repo_coverage"] if row["repo"] == "docs-only-app")
    assert row["signals"]["model_literal_files"] == 1
    assert row["status"] == "waived"
    assert row["severity"] == "muted"
    assert row["probe_status"] == "waived"
    assert row["exception_reason"].startswith("Scanner matches CLI labels")
    assert row["owner_next_step"] == "Keep this waiver unless runtime model calls are added."


def test_model_registry_status_adds_probe_metadata_for_critical_rows(tmp_path: Path) -> None:
    workspace = tmp_path / "experiments"
    workspace.mkdir()
    registry = workspace / "cognition-presets.toml"
    registry.write_text(
        """
[credentials.grok_aurora_openai]
provider = "openai"
source = "env"
env_var = "GROK_AURORA_OPENAI_API_KEY"

[credentials.lodestar_openai]
provider = "openai"
source = "env"
env_var = "LODESTAR_OPENAI_API_KEY"

[provider_surfaces.openai]
provider = "openai"
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"

[provider_surfaces.codex_cli]
provider = "codex"

[service_credential_assignments."grok-aurora-cli"]
openai = "grok_aurora_openai"

[service_credential_assignments.lodestar]
openai = "lodestar_openai"

[model_routes."grok_aurora.openai_structured"]
owner = "grok-aurora-cli"
surface = "openai"
provider = "openai"
model = "gpt-5-mini"
last_reviewed = "2026-05-04"

[model_routes."workgraph.codex_cli_standard"]
owner = "workgraph"
surface = "codex_cli"
provider = "codex"
model = "gpt-5"
last_reviewed = "2026-05-04"
""",
        encoding="utf-8",
    )
    ecosystem = workspace / "ecosystem.toml"
    ecosystem.write_text(
        """
[repos.grok-aurora-cli]
role = "service"
lifecycle = "active"

[repos.workgraph]
role = "dependency"
lifecycle = "active"

[repos.lodestar]
role = "product"
lifecycle = "active"
probe_status = "waived"
exception_reason = "Uses registry aliases, but live app probe is owned by integration validation."
owner_next_step = "Keep the waiver until the integration validation task runs."
last_verified_at = "2026-05-04T00:00:00+00:00"
""",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("GROK_AURORA_OPENAI_API_KEY=do-not-leak\n", encoding="utf-8")

    payload = build_model_registry_status(
        workspace_root=tmp_path,
        registry_path=registry,
        ecosystem_path=ecosystem,
        env_files=[env_file],
        env={},
    )

    assert "do-not-leak" not in str(payload)
    assert payload["summary"]["probe_status_counts"]["verified"] == 2
    assert payload["summary"]["probe_status_counts"]["waived"] == 1

    grok = next(row for row in payload["repo_coverage"] if row["repo"] == "grok-aurora-cli")
    assert grok["probe_status"] == "verified"
    assert grok["last_verified_at"]
    assert grok["exception_reason"] == ""
    assert grok["owner_next_step"] == "Keep registry route and credential aliases current."

    workgraph = next(row for row in payload["repo_coverage"] if row["repo"] == "workgraph")
    assert workgraph["status"] == "centralized-cli"
    assert workgraph["probe_status"] == "verified"
    assert workgraph["route_ids"] == ["workgraph.codex_cli_standard"]

    lodestar = next(row for row in payload["repo_coverage"] if row["repo"] == "lodestar")
    assert lodestar["probe_status"] == "waived"
    assert lodestar["exception_reason"].startswith("Uses registry aliases")


def test_model_route_rows_expose_completion_metadata_from_registry(tmp_path: Path) -> None:
    workspace = tmp_path / "experiments"
    workspace.mkdir()
    registry = workspace / "cognition-presets.toml"
    registry.write_text(
        """
[provider_surfaces.openai]
provider = "openai"
base_url = "https://api.openai.com/v1"

[model_routes."example.primary"]
owner = "example"
surface = "openai"
provider = "openai"
model = "gpt-5-mini"
probe_status = "verified"
last_verified_at = "2026-05-05T00:00:00+00:00"
owner_next_step = "Exercise this route from the app smoke test."
exception_reason = ""
""",
        encoding="utf-8",
    )

    payload = build_model_registry_status(
        workspace_root=tmp_path,
        registry_path=registry,
        ecosystem_path=workspace / "missing-ecosystem.toml",
        env_files=[],
        env={},
    )

    route = payload["model_routes"][0]
    assert route["probe_status"] == "verified"
    assert route["last_verified_at"] == "2026-05-05T00:00:00+00:00"
    assert route["owner_next_step"] == "Exercise this route from the app smoke test."
    assert route["exception_reason"] == ""


def test_grok_dop_media_planner_route_probe_preserves_route_shape(tmp_path: Path) -> None:
    workspace = tmp_path / "experiments"
    workspace.mkdir()
    registry = workspace / "cognition-presets.toml"
    registry.write_text(
        """
[provider_surfaces.zai_coding]
provider = "zai"
base_url = "https://api.z.ai/api/coding/paas/v4"
api_key_env = "ZAI_API_KEY"

[model_routes."grok_aurora.dop_media_planner"]
owner = "grok-aurora-cli"
surface = "zai_coding"
provider = "zai"
model = "glm-5.1"
quality_tier = "standard"
cost_tier = "medium"
max_tokens_default = 4096
request_timeout_seconds = 60
supports_streaming = false
supports_tools = false
supports_json_schema = true
last_reviewed = "2026-05-05"
source = "migration"
probe_status = "verified"
last_verified_at = "2026-05-05T00:00:00+00:00"
exception_reason = ""
owner_next_step = "Keep DOP media planner on strict zai_coding/glm-5.1 route; rerun registry probe after route edits."
""",
        encoding="utf-8",
    )

    payload = build_model_registry_status(
        workspace_root=tmp_path,
        registry_path=registry,
        ecosystem_path=workspace / "missing-ecosystem.toml",
        env_files=[],
        env={},
    )

    route = next(row for row in payload["model_routes"] if row["id"] == "grok_aurora.dop_media_planner")
    assert route["surface"] == "zai_coding"
    assert route["provider"] == "zai"
    assert route["model"] == "glm-5.1"
    assert route["probe_status"] == "verified"
    assert route["last_verified_at"] == "2026-05-05T00:00:00+00:00"
    assert route["exception_reason"] == ""
    assert "strict zai_coding/glm-5.1 route" in route["owner_next_step"]


def test_missing_credential_rows_expose_owner_waiver_metadata(tmp_path: Path) -> None:
    workspace = tmp_path / "experiments"
    workspace.mkdir()
    registry = workspace / "cognition-presets.toml"
    registry.write_text(
        """
[credentials.lodestar_voyage]
provider = "voyage"
source = "env"
env_var = "LODESTAR_VOYAGE_API_KEY"
probe_status = "waived"
exception_reason = "Blocked until owner provisions the Voyage key."
owner_next_step = "Provision LODESTAR_VOYAGE_API_KEY centrally before enabling embeddings."

[service_credential_assignments.lodestar]
voyage = "lodestar_voyage"
""",
        encoding="utf-8",
    )
    ecosystem = workspace / "ecosystem.toml"
    ecosystem.write_text(
        """
[repos.lodestar]
role = "product"
lifecycle = "active"
probe_status = "waived"
exception_reason = "Blocked on missing LODESTAR_VOYAGE_API_KEY."
owner_next_step = "Provision the app-specific alias centrally."
""",
        encoding="utf-8",
    )

    payload = build_model_registry_status(
        workspace_root=workspace,
        registry_path=registry,
        ecosystem_path=ecosystem,
        env_files=[],
        env={},
    )

    gap = payload["credential_gaps"][0]
    assert gap["id"] == "lodestar_voyage"
    assert gap["probe_status"] == "waived"
    assert gap["exception_reason"].startswith("Blocked until owner")
    assert gap["owner_next_step"].startswith("Provision LODESTAR_VOYAGE_API_KEY")

    row = next(row for row in payload["repo_coverage"] if row["repo"] == "lodestar")
    assert row["status"] == "needs-secret-source"
    assert row["probe_status"] == "waived"
    assert row["owner_next_step"] == "Provision the app-specific alias centrally."
