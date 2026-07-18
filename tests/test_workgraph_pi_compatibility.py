from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from driftdriver.workgraph import (
    ALLOWED_PI_MODEL_IDS,
    parse_workgraph_ready,
    parse_workgraph_status,
    validate_pi_model_spec,
)


ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "workgraph_status_ready.json"


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_current_workgraph_status_json_shape_is_accepted() -> None:
    status = parse_workgraph_status(_fixture()["status_json"])

    assert status["service"] == {"running": False, "pid": None, "uptime": None, "socket": None}
    assert status["coordinator"]["executor"] == "pi"
    assert status["coordinator"]["model"] == "pi:zai/glm-5.2"
    assert status["tasks"]["in_progress"] == 0
    assert status["recent"][0]["task_id"] == "phase0.reaper-regression"


def test_current_workgraph_ready_json_shape_is_accepted() -> None:
    ready = parse_workgraph_ready(_fixture()["ready_json"])

    assert ready
    assert all(row["ready"] is True for row in ready)
    assert all({"id", "title", "ready", "assigned", "priority", "estimate"} <= row.keys() for row in ready)
    assert any(row["id"] == ".assign-agents.pi-bindings" for row in ready)


@pytest.mark.parametrize(
    "model_spec",
    sorted(ALLOWED_PI_MODEL_IDS),
)
def test_allowed_live_pi_model_ids_are_provider_qualified(model_spec: str) -> None:
    assert validate_pi_model_spec(model_spec) == model_spec


def test_pi_model_specs_preserve_allowed_thinking_suffix() -> None:
    model_spec = "anthropic/claude-sonnet-4-5:high"
    assert validate_pi_model_spec(model_spec) == model_spec

    with pytest.raises(ValueError, match="allowed Pi model"):
        validate_pi_model_spec("unknown-provider/not-a-live-model")


def test_pi_executor_preserves_selected_and_fallback_model_specs(tmp_path: Path) -> None:
    capture = tmp_path / "pi-invocation.json"
    fake_pi = tmp_path / "fake-pi"
    fake_pi.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'selected': os.environ.get('WG_SELECTED_MODEL'), "
        f"'fallback': os.environ.get('WG_FALLBACK_MODEL')}}, open({str(capture)!r}, 'w'))\n",
        encoding="utf-8",
    )
    fake_pi.chmod(0o755)

    env = os.environ.copy()
    env["PI_BIN"] = str(fake_pi)
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "driftdriver" / "templates" / "executors" / "pi-run.sh"),
            "--provider",
            "anthropic",
            "--model",
            "anthropic/claude-opus-4-8",
            "--fallback-model",
            "zai/glm-5.2:high",
        ],
        input="deterministic compatibility prompt",
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    invocation = json.loads(capture.read_text(encoding="utf-8"))
    assert invocation["argv"] == [
        "-p",
        "--model",
        "anthropic/claude-opus-4-8",
        "deterministic compatibility prompt",
    ]
    assert invocation["selected"] == "anthropic/claude-opus-4-8"
    assert invocation["fallback"] == "zai/glm-5.2:high"
