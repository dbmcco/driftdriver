# ABOUTME: Tests for the Planner service — goal decomposition into task subgraphs
# ABOUTME: Planner writes tasks to the graph and stops; no dispatch or quality checking

from pathlib import Path

from driftdriver.planner import build_decompose_prompt, DECOMPOSE_PROMPT_TEMPLATE


def test_decompose_prompt_template_is_string():
    """Template exists and is a non-empty string."""
    assert isinstance(DECOMPOSE_PROMPT_TEMPLATE, str)
    assert len(DECOMPOSE_PROMPT_TEMPLATE) > 50


def test_build_decompose_prompt_includes_goal():
    """Prompt includes the goal text."""
    prompt = build_decompose_prompt("Build a REST API for user management", Path("/project"))
    assert "REST API" in prompt
    assert "user management" in prompt


def test_build_decompose_prompt_includes_project_dir():
    """Prompt includes the project directory."""
    prompt = build_decompose_prompt("Build something", Path("/my/project"))
    assert "/my/project" in prompt


def test_build_decompose_prompt_includes_wg_instructions():
    """Prompt instructs the planner to use workgraph commands."""
    prompt = build_decompose_prompt("Build something", Path("/project"))
    assert "wg add" in prompt


def test_build_decompose_prompt_returns_string():
    prompt = build_decompose_prompt("Any goal", Path("/project"))
    assert isinstance(prompt, str)
    assert len(prompt) > 0
