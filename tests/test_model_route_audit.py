from __future__ import annotations

import json
from pathlib import Path


def test_scan_flags_runtime_model_literal(tmp_path: Path) -> None:
    from driftdriver.model_route_audit import scan_model_route_literals

    source = tmp_path / "app" / "runtime.py"
    source.parent.mkdir()
    source.write_text('client.responses.create(model="gpt-4o")\n', encoding="utf-8")

    report = scan_model_route_literals(tmp_path)

    assert report.finding_count == 1
    finding = report.findings[0]
    assert finding.path == Path("app/runtime.py")
    assert finding.line == 1
    assert finding.model == "gpt-4o"
    assert finding.category == "runtime_literal"


def test_scan_ignores_docs_tests_and_registry_by_default(tmp_path: Path) -> None:
    from driftdriver.model_route_audit import scan_model_route_literals

    docs = tmp_path / "docs" / "example.md"
    docs.parent.mkdir()
    docs.write_text("Example: claude-sonnet-4-6\n", encoding="utf-8")

    tests = tmp_path / "tests" / "test_model.py"
    tests.parent.mkdir()
    tests.write_text('assert route.model == "claude-sonnet-4-6"\n', encoding="utf-8")

    registry = tmp_path / "paia-agent-runtime" / "config" / "cognition-presets.toml"
    registry.parent.mkdir(parents=True)
    registry.write_text('model = "claude-sonnet-4-6"\n', encoding="utf-8")

    report = scan_model_route_literals(tmp_path)

    assert report.findings == []


def test_scan_can_include_tests(tmp_path: Path) -> None:
    from driftdriver.model_route_audit import scan_model_route_literals

    tests = tmp_path / "tests" / "test_model.py"
    tests.parent.mkdir()
    tests.write_text('assert route.model == "claude-sonnet-4-6"\n', encoding="utf-8")

    report = scan_model_route_literals(tmp_path, include_tests=True)

    assert report.finding_count == 1
    assert report.findings[0].path == Path("tests/test_model.py")


def test_scan_ignores_gitignored_generated_files(tmp_path: Path) -> None:
    import subprocess

    from driftdriver.model_route_audit import scan_model_route_literals

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("generated-routes.js\n", encoding="utf-8")
    generated = tmp_path / "generated-routes.js"
    generated.write_text('model = "gemini-2.5-flash-image"\n', encoding="utf-8")

    report = scan_model_route_literals(tmp_path)

    assert report.findings == []


def test_cli_advisory_outputs_json_and_returns_zero(tmp_path: Path, capsys) -> None:
    from driftdriver.cli import main

    source = tmp_path / "app.py"
    source.write_text('MODEL = "gemini-2.5-pro"\n', encoding="utf-8")

    rc = main(["--dir", str(tmp_path), "--json", "model-route-audit"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["finding_count"] == 1
    assert data["mode"] == "advisory"


def test_cli_blocking_returns_findings_exit_code(tmp_path: Path, capsys) -> None:
    from driftdriver.cli import main

    source = tmp_path / "app.py"
    source.write_text('MODEL = "x-ai/grok-4"\n', encoding="utf-8")

    rc = main(["--dir", str(tmp_path), "model-route-audit", "--blocking"])

    assert rc == 3
    out = capsys.readouterr().out
    assert "x-ai/grok-4" in out
    assert "model-route-audit found 1 hardcoded model literal" in out
