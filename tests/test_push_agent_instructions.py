# ABOUTME: Tests the managed Speedrift guidance blocks pushed into repo CLAUDE/AGENTS files.
# ABOUTME: Verifies wg examples track the live CLI contract and do not use removed flags.

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_push_agent_instructions_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "push_agent_instructions.py"
    spec = importlib.util.spec_from_file_location("push_agent_instructions", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_managed_guidance_uses_current_wg_add_flags() -> None:
    module = _load_push_agent_instructions_module()

    assert "--no-place" in module.CLAUDE_BLOCK
    assert "--no-place" in module.AGENTS_BLOCK
    assert "--immediate" not in module.CLAUDE_BLOCK
    assert "--immediate" not in module.AGENTS_BLOCK
