# ABOUTME: Comprehensive unit tests for driftdriver/install.py
# ABOUTME: Covers wrapper generation, gitignore management, adapter configs, and install orchestration

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from driftdriver.install import (
    ARCHDRIFT_MARKER,
    CLAUDE_ADAPTER_END,
    CLAUDE_ADAPTER_MARKER,
    CLAUDE_ADAPTER_START,
    CLAUDE_EXECUTOR_WRAPPER,
    CODEX_ADAPTER_END,
    CODEX_ADAPTER_MARKER,
    CODEX_ADAPTER_START,
    COREDRIFT_MARKER,
    FIXDRIFT_MARKER,
    MODEL_MEDIATED_MARKER,
    REDRIFT_MARKER,
    SUPERPOWERS_MARKER,
    THERAPYDRIFT_MARKER,
    UXDRIFT_MARKER,
    YAGNIDRIFT_MARKER,
    ClaudeAdapterResult,
    CodexAdapterResult,
    InstallResult,
    ensure_amplifier_autostart_hook,
    ensure_amplifier_executor,
    ensure_archdrift_gitignore,
    ensure_coredrift_gitignore,
    ensure_datadrift_gitignore,
    ensure_depsdrift_gitignore,
    ensure_executor_guidance,
    ensure_fixdrift_gitignore,
    ensure_qadrift_gitignore,
    ensure_redrift_gitignore,
    ensure_specdrift_gitignore,
    ensure_therapydrift_gitignore,
    ensure_uxdrift_gitignore,
    ensure_yagnidrift_gitignore,
    install_amplifier_adapter,
    install_claude_adapter,
    install_claude_code_hooks,
    install_claude_executor_support,
    install_codex_adapter,
    install_handler_scripts,
    install_lessons_mcp_config,
    install_opencode_hooks,
    install_session_driver_executor,
    resolve_bin,
    write_archdrift_wrapper,
    write_coredrift_wrapper,
    write_datadrift_wrapper,
    write_depsdrift_wrapper,
    write_drifts_wrapper,
    write_driver_wrapper,
    write_fixdrift_wrapper,
    write_qadrift_wrapper,
    write_redrift_wrapper,
    write_specdrift_wrapper,
    write_therapydrift_wrapper,
    write_tool_wrapper,
    write_uxdrift_wrapper,
    write_yagnidrift_wrapper,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_bin(tmp_path: Path, name: str) -> Path:
    """Create a fake executable binary for resolve_bin testing."""
    bin_path = tmp_path / name
    bin_path.write_text("#!/bin/sh\nexit 0\n")
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bin_path


def _is_executable(path: Path) -> bool:
    """Check that a file has the executable bit set for owner."""
    return bool(path.stat().st_mode & stat.S_IXUSR)


# ---------------------------------------------------------------------------
# InstallResult dataclass
# ---------------------------------------------------------------------------

class TestInstallResult:
    def test_all_fields_exist(self) -> None:
        result = InstallResult(
            wrote_drifts=True,
            wrote_driver=True,
            wrote_coredrift=True,
            wrote_specdrift=False,
            wrote_datadrift=False,
            wrote_archdrift=False,
            wrote_depsdrift=False,
            wrote_uxdrift=False,
            wrote_therapydrift=False,
            wrote_fixdrift=False,
            wrote_yagnidrift=False,
            wrote_redrift=False,
            wrote_qadrift=False,
            wrote_debatedrift=False,
            wrote_handlers=True,
            wrote_amplifier_executor=False,
            wrote_amplifier_runner=False,
            wrote_amplifier_autostart_hook=False,
            wrote_amplifier_autostart_hooks_json=False,
            wrote_session_driver_executor=False,
            wrote_session_driver_runner=False,
            wrote_claude_code_hooks=False,
            wrote_policy=True,
            updated_gitignore=True,
            created_executor=True,
            patched_executors=[],
            ensured_contracts=False,
        )
        assert result.wrote_drifts is True
        assert result.wrote_driver is True
        assert result.wrote_coredrift is True
        assert result.wrote_specdrift is False
        assert result.patched_executors == []

    def test_frozen(self) -> None:
        result = InstallResult(
            wrote_drifts=True,
            wrote_driver=False,
            wrote_coredrift=False,
            wrote_specdrift=False,
            wrote_datadrift=False,
            wrote_archdrift=False,
            wrote_depsdrift=False,
            wrote_uxdrift=False,
            wrote_therapydrift=False,
            wrote_fixdrift=False,
            wrote_yagnidrift=False,
            wrote_redrift=False,
            wrote_qadrift=False,
            wrote_debatedrift=False,
            wrote_handlers=False,
            wrote_amplifier_executor=False,
            wrote_amplifier_runner=False,
            wrote_amplifier_autostart_hook=False,
            wrote_amplifier_autostart_hooks_json=False,
            wrote_session_driver_executor=False,
            wrote_session_driver_runner=False,
            wrote_claude_code_hooks=False,
            wrote_policy=False,
            updated_gitignore=False,
            created_executor=False,
            patched_executors=[],
            ensured_contracts=False,
        )
        with pytest.raises(AttributeError):
            result.wrote_drifts = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Gitignore functions (parametrized)
# ---------------------------------------------------------------------------

_GITIGNORE_FUNCTIONS = [
    (ensure_coredrift_gitignore, ".coredrift/"),
    (ensure_specdrift_gitignore, ".specdrift/"),
    (ensure_datadrift_gitignore, ".datadrift/"),
    (ensure_archdrift_gitignore, ".archdrift/"),
    (ensure_depsdrift_gitignore, ".depsdrift/"),
    (ensure_uxdrift_gitignore, ".uxdrift/"),
    (ensure_therapydrift_gitignore, ".therapydrift/"),
    (ensure_fixdrift_gitignore, ".fixdrift/"),
    (ensure_yagnidrift_gitignore, ".yagnidrift/"),
    (ensure_redrift_gitignore, ".redrift/"),
    (ensure_qadrift_gitignore, ".qadrift/"),
]


class TestEnsureGitignore:
    @pytest.mark.parametrize("func,expected_line", _GITIGNORE_FUNCTIONS, ids=[f[1].strip("/") for f in _GITIGNORE_FUNCTIONS])
    def test_creates_gitignore_with_entry(self, tmp_path: Path, func, expected_line: str) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        result = func(wg_dir)
        assert result is True
        gitignore = wg_dir / ".gitignore"
        assert gitignore.exists()
        assert expected_line in gitignore.read_text(encoding="utf-8")

    @pytest.mark.parametrize("func,expected_line", _GITIGNORE_FUNCTIONS, ids=[f[1].strip("/") for f in _GITIGNORE_FUNCTIONS])
    def test_idempotent_no_duplicate(self, tmp_path: Path, func, expected_line: str) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        func(wg_dir)
        second = func(wg_dir)
        assert second is False
        content = (wg_dir / ".gitignore").read_text(encoding="utf-8")
        assert content.count(expected_line) == 1

    def test_appends_to_existing_gitignore(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        gitignore = wg_dir / ".gitignore"
        gitignore.write_text("some-existing-entry/\n")
        ensure_coredrift_gitignore(wg_dir)
        content = gitignore.read_text(encoding="utf-8")
        assert "some-existing-entry/" in content
        assert ".coredrift/" in content

    def test_multiple_entries_combine(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        ensure_coredrift_gitignore(wg_dir)
        ensure_specdrift_gitignore(wg_dir)
        ensure_uxdrift_gitignore(wg_dir)
        content = (wg_dir / ".gitignore").read_text(encoding="utf-8")
        assert ".coredrift/" in content
        assert ".specdrift/" in content
        assert ".uxdrift/" in content

    def test_creates_parent_dir_if_missing(self, tmp_path: Path) -> None:
        """_ensure_line_in_file creates parent dirs as needed."""
        wg_dir = tmp_path / "deep" / "nested" / ".workgraph"
        # Don't create wg_dir -- the function should create parent dirs
        result = ensure_coredrift_gitignore(wg_dir)
        assert result is True
        assert (wg_dir / ".gitignore").exists()


# ---------------------------------------------------------------------------
# Wrapper writers
# ---------------------------------------------------------------------------

class TestWriteToolWrapper:
    def test_pinned_wrapper_content(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        fake_bin = _make_fake_bin(tmp_path, "mytool")
        changed = write_tool_wrapper(wg_dir, tool_name="mytool", tool_bin=fake_bin, wrapper_mode="pinned")
        assert changed is True
        wrapper = wg_dir / "mytool"
        assert wrapper.exists()
        assert _is_executable(wrapper)
        content = wrapper.read_text(encoding="utf-8")
        assert "#!/usr/bin/env bash" in content
        assert str(fake_bin) in content
        assert 'exec "' in content

    def test_portable_wrapper_content(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        fake_bin = _make_fake_bin(tmp_path, "mytool")
        changed = write_tool_wrapper(wg_dir, tool_name="mytool", tool_bin=fake_bin, wrapper_mode="portable")
        assert changed is True
        wrapper = wg_dir / "mytool"
        content = wrapper.read_text(encoding="utf-8")
        assert "TOOL=\"mytool\"" in content
        assert "FOUND=" in content
        assert str(fake_bin) not in content  # portable does NOT pin the path

    def test_idempotent(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        fake_bin = _make_fake_bin(tmp_path, "mytool")
        write_tool_wrapper(wg_dir, tool_name="mytool", tool_bin=fake_bin)
        changed = write_tool_wrapper(wg_dir, tool_name="mytool", tool_bin=fake_bin)
        assert changed is False

    def test_executable_bit_set(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        fake_bin = _make_fake_bin(tmp_path, "mytool")
        write_tool_wrapper(wg_dir, tool_name="mytool", tool_bin=fake_bin)
        wrapper = wg_dir / "mytool"
        mode = wrapper.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH


# Parametrized wrapper tests for all drift tool wrappers that follow the same pattern.
_WRAPPER_FUNCTIONS = [
    ("write_driver_wrapper", write_driver_wrapper, "driver_bin", "driftdriver"),
    ("write_coredrift_wrapper", write_coredrift_wrapper, "coredrift_bin", "coredrift"),
    ("write_specdrift_wrapper", write_specdrift_wrapper, "specdrift_bin", "specdrift"),
    ("write_datadrift_wrapper", write_datadrift_wrapper, "datadrift_bin", "datadrift"),
    ("write_archdrift_wrapper", write_archdrift_wrapper, "archdrift_bin", "archdrift"),
    ("write_depsdrift_wrapper", write_depsdrift_wrapper, "depsdrift_bin", "depsdrift"),
    ("write_uxdrift_wrapper", write_uxdrift_wrapper, "uxdrift_bin", "uxdrift"),
    ("write_therapydrift_wrapper", write_therapydrift_wrapper, "therapydrift_bin", "therapydrift"),
    ("write_fixdrift_wrapper", write_fixdrift_wrapper, "fixdrift_bin", "fixdrift"),
    ("write_yagnidrift_wrapper", write_yagnidrift_wrapper, "yagnidrift_bin", "yagnidrift"),
    ("write_redrift_wrapper", write_redrift_wrapper, "redrift_bin", "redrift"),
]


class TestNamedWrapperWriters:
    @pytest.mark.parametrize(
        "name,func,kwarg,expected_filename",
        _WRAPPER_FUNCTIONS,
        ids=[w[0] for w in _WRAPPER_FUNCTIONS],
    )
    def test_creates_executable_wrapper(self, tmp_path: Path, name: str, func, kwarg: str, expected_filename: str) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        fake_bin = _make_fake_bin(tmp_path, expected_filename)
        changed = func(wg_dir, **{kwarg: fake_bin})
        assert changed is True
        wrapper = wg_dir / expected_filename
        assert wrapper.exists()
        assert _is_executable(wrapper)

    @pytest.mark.parametrize(
        "name,func,kwarg,expected_filename",
        _WRAPPER_FUNCTIONS,
        ids=[w[0] for w in _WRAPPER_FUNCTIONS],
    )
    def test_idempotent(self, tmp_path: Path, name: str, func, kwarg: str, expected_filename: str) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        fake_bin = _make_fake_bin(tmp_path, expected_filename)
        func(wg_dir, **{kwarg: fake_bin})
        changed = func(wg_dir, **{kwarg: fake_bin})
        assert changed is False

    @pytest.mark.parametrize(
        "name,func,kwarg,expected_filename",
        _WRAPPER_FUNCTIONS,
        ids=[w[0] for w in _WRAPPER_FUNCTIONS],
    )
    def test_portable_mode(self, tmp_path: Path, name: str, func, kwarg: str, expected_filename: str) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        fake_bin = _make_fake_bin(tmp_path, expected_filename)
        changed = func(wg_dir, **{kwarg: fake_bin}, wrapper_mode="portable")
        assert changed is True
        content = (wg_dir / expected_filename).read_text(encoding="utf-8")
        assert f'TOOL="{expected_filename}"' in content


# ---------------------------------------------------------------------------
# write_drifts_wrapper (special: delegates to driftdriver, no bin arg)
# ---------------------------------------------------------------------------

class TestWriteDriftsWrapper:
    def test_creates_wrapper(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        changed = write_drifts_wrapper(wg_dir)
        assert changed is True
        wrapper = wg_dir / "drifts"
        assert wrapper.exists()
        assert _is_executable(wrapper)
        content = wrapper.read_text(encoding="utf-8")
        assert "$WG_DIR/driftdriver" in content

    def test_idempotent(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        write_drifts_wrapper(wg_dir)
        changed = write_drifts_wrapper(wg_dir)
        assert changed is False


# ---------------------------------------------------------------------------
# write_qadrift_wrapper (special: reads from template file)
# ---------------------------------------------------------------------------

class TestWriteQadriftWrapper:
    def test_creates_wrapper_from_template(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        changed = write_qadrift_wrapper(wg_dir)
        assert changed is True
        wrapper = wg_dir / "qadrift"
        assert wrapper.exists()
        assert _is_executable(wrapper)
        content = wrapper.read_text(encoding="utf-8")
        assert "driftdriver.qadrift" in content

    def test_idempotent(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        write_qadrift_wrapper(wg_dir)
        changed = write_qadrift_wrapper(wg_dir)
        assert changed is False


# ---------------------------------------------------------------------------
# resolve_bin
# ---------------------------------------------------------------------------

class TestResolveBin:
    def test_explicit_path_found(self, tmp_path: Path) -> None:
        fake = _make_fake_bin(tmp_path, "tool")
        result = resolve_bin(explicit=fake, env_var=None, which_name=None, candidates=[])
        assert result == fake

    def test_explicit_path_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        result = resolve_bin(explicit=missing, env_var=None, which_name=None, candidates=[])
        assert result is None

    def test_explicit_not_executable(self, tmp_path: Path) -> None:
        f = tmp_path / "noexec"
        f.write_text("#!/bin/sh\n")
        # Don't set executable bit
        result = resolve_bin(explicit=f, env_var=None, which_name=None, candidates=[])
        assert result is None

    def test_env_var_resolution(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _make_fake_bin(tmp_path, "envtool")
        monkeypatch.setenv("MY_TOOL_BIN", str(fake))
        result = resolve_bin(explicit=None, env_var="MY_TOOL_BIN", which_name=None, candidates=[])
        assert result == fake

    def test_env_var_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_VAR_XYZ", raising=False)
        result = resolve_bin(explicit=None, env_var="NONEXISTENT_VAR_XYZ", which_name=None, candidates=[])
        assert result is None

    def test_candidates_fallback(self, tmp_path: Path) -> None:
        fake = _make_fake_bin(tmp_path, "candidate")
        result = resolve_bin(explicit=None, env_var=None, which_name=None, candidates=[fake])
        assert result == fake

    def test_candidates_skips_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing"
        fake = _make_fake_bin(tmp_path, "found")
        result = resolve_bin(explicit=None, env_var=None, which_name=None, candidates=[missing, fake])
        assert result == fake

    def test_all_none(self, tmp_path: Path) -> None:
        result = resolve_bin(explicit=None, env_var=None, which_name=None, candidates=[])
        assert result is None


# ---------------------------------------------------------------------------
# ensure_executor_guidance
# ---------------------------------------------------------------------------

class TestEnsureExecutorGuidance:
    def test_creates_claude_toml(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        created, patched = ensure_executor_guidance(
            wg_dir,
            include_archdrift=False,
            include_uxdrift=False,
            include_therapydrift=False,
            include_fixdrift=False,
            include_yagnidrift=False,
            include_redrift=False,
        )
        assert created is True
        claude_toml = wg_dir / "executors" / "claude.toml"
        assert claude_toml.exists()
        content = claude_toml.read_text(encoding="utf-8")
        assert COREDRIFT_MARKER in content
        assert SUPERPOWERS_MARKER in content
        assert MODEL_MEDIATED_MARKER in content

    def test_does_not_recreate_existing_toml(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        ensure_executor_guidance(
            wg_dir,
            include_archdrift=False,
            include_uxdrift=False,
            include_therapydrift=False,
            include_fixdrift=False,
            include_yagnidrift=False,
            include_redrift=False,
        )
        created, patched = ensure_executor_guidance(
            wg_dir,
            include_archdrift=False,
            include_uxdrift=False,
            include_therapydrift=False,
            include_fixdrift=False,
            include_yagnidrift=False,
            include_redrift=False,
        )
        assert created is False

    def test_includes_archdrift_in_toml(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        ensure_executor_guidance(
            wg_dir,
            include_archdrift=True,
            include_uxdrift=False,
            include_therapydrift=False,
            include_fixdrift=False,
            include_yagnidrift=False,
            include_redrift=False,
        )
        content = (wg_dir / "executors" / "claude.toml").read_text(encoding="utf-8")
        assert ARCHDRIFT_MARKER in content

    def test_includes_uxdrift_in_toml(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        ensure_executor_guidance(
            wg_dir,
            include_archdrift=False,
            include_uxdrift=True,
            include_therapydrift=False,
            include_fixdrift=False,
            include_yagnidrift=False,
            include_redrift=False,
        )
        content = (wg_dir / "executors" / "claude.toml").read_text(encoding="utf-8")
        assert UXDRIFT_MARKER in content

    def test_includes_all_optional_drifts(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        ensure_executor_guidance(
            wg_dir,
            include_archdrift=True,
            include_uxdrift=True,
            include_therapydrift=True,
            include_fixdrift=True,
            include_yagnidrift=True,
            include_redrift=True,
        )
        content = (wg_dir / "executors" / "claude.toml").read_text(encoding="utf-8")
        assert ARCHDRIFT_MARKER in content
        assert UXDRIFT_MARKER in content
        assert THERAPYDRIFT_MARKER in content
        assert FIXDRIFT_MARKER in content
        assert YAGNIDRIFT_MARKER in content
        assert REDRIFT_MARKER in content

    def test_patches_existing_toml_with_missing_markers(self, tmp_path: Path) -> None:
        """If a claude.toml exists without certain markers, they get injected."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        # First create without archdrift
        ensure_executor_guidance(
            wg_dir,
            include_archdrift=False,
            include_uxdrift=False,
            include_therapydrift=False,
            include_fixdrift=False,
            include_yagnidrift=False,
            include_redrift=False,
        )
        content_before = (wg_dir / "executors" / "claude.toml").read_text(encoding="utf-8")
        assert ARCHDRIFT_MARKER not in content_before

        # Now run with archdrift enabled -- should patch the existing file
        created, patched = ensure_executor_guidance(
            wg_dir,
            include_archdrift=True,
            include_uxdrift=False,
            include_therapydrift=False,
            include_fixdrift=False,
            include_yagnidrift=False,
            include_redrift=False,
        )
        assert created is False
        assert len(patched) > 0
        content_after = (wg_dir / "executors" / "claude.toml").read_text(encoding="utf-8")
        assert ARCHDRIFT_MARKER in content_after

    def test_installs_claude_run_script(self, tmp_path: Path) -> None:
        """ensure_executor_guidance also installs the claude-run.sh runner."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        ensure_executor_guidance(
            wg_dir,
            include_archdrift=False,
            include_uxdrift=False,
            include_therapydrift=False,
            include_fixdrift=False,
            include_yagnidrift=False,
            include_redrift=False,
        )
        runner = wg_dir / "executors" / "claude-run.sh"
        assert runner.exists()
        assert _is_executable(runner)


# ---------------------------------------------------------------------------
# install_claude_executor_support
# ---------------------------------------------------------------------------

class TestInstallClaudeExecutorSupport:
    def test_creates_runner_and_timeout(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        wrote_runner, wrote_timeout = install_claude_executor_support(wg_dir)
        assert wrote_runner is True
        assert wrote_timeout is True
        assert (wg_dir / "executors" / "claude-run.sh").exists()
        assert _is_executable(wg_dir / "executors" / "claude-run.sh")
        assert (wg_dir / "bin" / "timeout").exists()
        assert _is_executable(wg_dir / "bin" / "timeout")

    def test_idempotent(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        install_claude_executor_support(wg_dir)
        wrote_runner, wrote_timeout = install_claude_executor_support(wg_dir)
        assert wrote_runner is False
        assert wrote_timeout is False


# ---------------------------------------------------------------------------
# Adapter installs
# ---------------------------------------------------------------------------

class TestInstallClaudeAdapter:
    def test_creates_claude_md(self, tmp_path: Path) -> None:
        result = install_claude_adapter(tmp_path)
        assert result.wrote_claude_md is True
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text(encoding="utf-8")
        assert CLAUDE_ADAPTER_MARKER in content
        assert CLAUDE_ADAPTER_START in content
        assert CLAUDE_ADAPTER_END in content

    def test_idempotent(self, tmp_path: Path) -> None:
        install_claude_adapter(tmp_path)
        result = install_claude_adapter(tmp_path)
        assert result.wrote_claude_md is False

    def test_appends_to_existing_claude_md(self, tmp_path: Path) -> None:
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nSome content here.\n")
        result = install_claude_adapter(tmp_path)
        assert result.wrote_claude_md is True
        content = claude_md.read_text(encoding="utf-8")
        assert "My Project" in content
        assert CLAUDE_ADAPTER_MARKER in content

    def test_updates_existing_block(self, tmp_path: Path) -> None:
        """When the marked block already exists with different content, it gets replaced."""
        claude_md = tmp_path / "CLAUDE.md"
        old_block = f"{CLAUDE_ADAPTER_START}\n## Old Content\nOld stuff\n{CLAUDE_ADAPTER_END}\n"
        claude_md.write_text(f"# Project\n\n{old_block}")
        result = install_claude_adapter(tmp_path)
        assert result.wrote_claude_md is True
        content = claude_md.read_text(encoding="utf-8")
        assert "Old stuff" not in content
        assert CLAUDE_ADAPTER_MARKER in content

    def test_result_type(self, tmp_path: Path) -> None:
        result = install_claude_adapter(tmp_path)
        assert isinstance(result, ClaudeAdapterResult)


class TestInstallCodexAdapter:
    def test_creates_agents_md(self, tmp_path: Path) -> None:
        result = install_codex_adapter(tmp_path)
        assert result.wrote_agents_md is True
        agents_md = tmp_path / "AGENTS.md"
        assert agents_md.exists()
        content = agents_md.read_text(encoding="utf-8")
        assert CODEX_ADAPTER_MARKER in content

    def test_idempotent(self, tmp_path: Path) -> None:
        install_codex_adapter(tmp_path)
        result = install_codex_adapter(tmp_path)
        assert result.wrote_agents_md is False

    def test_appends_to_existing_agents_md(self, tmp_path: Path) -> None:
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Agents\n\nExisting agent docs.\n")
        result = install_codex_adapter(tmp_path)
        assert result.wrote_agents_md is True
        content = agents_md.read_text(encoding="utf-8")
        assert "Existing agent docs." in content
        assert CODEX_ADAPTER_MARKER in content

    def test_result_type(self, tmp_path: Path) -> None:
        result = install_codex_adapter(tmp_path)
        assert isinstance(result, CodexAdapterResult)


class TestInstallOpenCodeHooks:
    def test_creates_hooks_json(self, tmp_path: Path) -> None:
        wrote = install_opencode_hooks(tmp_path)
        assert wrote is True
        hooks = tmp_path / ".opencode" / "hooks.json"
        assert hooks.exists()
        data = json.loads(hooks.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_idempotent(self, tmp_path: Path) -> None:
        install_opencode_hooks(tmp_path)
        wrote = install_opencode_hooks(tmp_path)
        assert wrote is False


class TestInstallAmplifierAdapter:
    def test_creates_session_hooks(self, tmp_path: Path) -> None:
        wrote = install_amplifier_adapter(tmp_path)
        assert wrote is True
        dest = tmp_path / ".amplifier" / "hooks" / "driftdriver" / "session-hooks.sh"
        assert dest.exists()

    def test_idempotent(self, tmp_path: Path) -> None:
        install_amplifier_adapter(tmp_path)
        wrote = install_amplifier_adapter(tmp_path)
        assert wrote is False


class TestInstallClaudeCodeHooks:
    def test_creates_hooks_json(self, tmp_path: Path) -> None:
        wrote = install_claude_code_hooks(tmp_path)
        assert wrote is True
        hooks = tmp_path / ".claude" / "hooks.json"
        assert hooks.exists()
        data = json.loads(hooks.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_idempotent(self, tmp_path: Path) -> None:
        install_claude_code_hooks(tmp_path)
        wrote = install_claude_code_hooks(tmp_path)
        assert wrote is False


# ---------------------------------------------------------------------------
# install_lessons_mcp_config
# ---------------------------------------------------------------------------

class TestInstallLessonsMcpConfig:
    def test_creates_mcp_json(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        wrote = install_lessons_mcp_config(wg_dir)
        assert wrote is True
        mcp_json = tmp_path / ".mcp.json"
        assert mcp_json.exists()
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        assert "lessons-mcp" in data["mcpServers"]

    def test_idempotent(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        install_lessons_mcp_config(wg_dir)
        wrote = install_lessons_mcp_config(wg_dir)
        assert wrote is False

    def test_preserves_existing_servers(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        mcp_json = tmp_path / ".mcp.json"
        mcp_json.write_text(json.dumps({"mcpServers": {"other-mcp": {"command": "other"}}}))
        install_lessons_mcp_config(wg_dir)
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        assert "other-mcp" in data["mcpServers"]
        assert "lessons-mcp" in data["mcpServers"]

    def test_handles_malformed_json(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        mcp_json = tmp_path / ".mcp.json"
        mcp_json.write_text("{not valid json!")
        wrote = install_lessons_mcp_config(wg_dir)
        assert wrote is True
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
        assert "lessons-mcp" in data["mcpServers"]


# ---------------------------------------------------------------------------
# ensure_amplifier_executor
# ---------------------------------------------------------------------------

class TestEnsureAmplifierExecutor:
    def test_creates_toml_and_runner(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        wrote_toml, wrote_runner = ensure_amplifier_executor(wg_dir, bundle_name="speedrift")
        assert wrote_toml is True
        assert wrote_runner is True
        runner = wg_dir / "executors" / "amplifier-run.sh"
        toml = wg_dir / "executors" / "amplifier.toml"
        assert runner.exists()
        assert toml.exists()
        assert _is_executable(runner)
        assert "speedrift" in runner.read_text(encoding="utf-8")

    def test_idempotent(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        ensure_amplifier_executor(wg_dir, bundle_name="speedrift")
        wrote_toml, wrote_runner = ensure_amplifier_executor(wg_dir, bundle_name="speedrift")
        assert wrote_toml is False
        assert wrote_runner is False

    def test_custom_bundle_name(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        ensure_amplifier_executor(wg_dir, bundle_name="custom-bundle")
        runner_text = (wg_dir / "executors" / "amplifier-run.sh").read_text(encoding="utf-8")
        assert "custom-bundle" in runner_text


# ---------------------------------------------------------------------------
# ensure_amplifier_autostart_hook
# ---------------------------------------------------------------------------

class TestEnsureAmplifierAutostartHook:
    def test_creates_hook_files(self, tmp_path: Path) -> None:
        wrote_script, wrote_json = ensure_amplifier_autostart_hook(tmp_path)
        assert wrote_script is True
        assert wrote_json is True
        hook_dir = tmp_path / ".amplifier" / "hooks" / "speedrift-autostart"
        assert (hook_dir / "session-start.sh").exists()
        assert (hook_dir / "hooks.json").exists()
        assert _is_executable(hook_dir / "session-start.sh")

    def test_idempotent(self, tmp_path: Path) -> None:
        ensure_amplifier_autostart_hook(tmp_path)
        wrote_script, wrote_json = ensure_amplifier_autostart_hook(tmp_path)
        assert wrote_script is False
        assert wrote_json is False

    def test_hook_json_structure(self, tmp_path: Path) -> None:
        ensure_amplifier_autostart_hook(tmp_path)
        hook_dir = tmp_path / ".amplifier" / "hooks" / "speedrift-autostart"
        data = json.loads((hook_dir / "hooks.json").read_text(encoding="utf-8"))
        assert "SessionStart" in data["hooks"]
        hooks = data["hooks"]["SessionStart"]
        assert len(hooks) == 1
        assert hooks[0]["matcher"] == ".*"


# ---------------------------------------------------------------------------
# install_handler_scripts
# ---------------------------------------------------------------------------

class TestInstallHandlerScripts:
    def test_installs_handler_scripts(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        any_written, count = install_handler_scripts(wg_dir)
        assert any_written is True
        assert count > 0
        handlers_dir = wg_dir / "handlers"
        assert handlers_dir.exists()
        # Check at least some known handler scripts exist
        handler_files = list(handlers_dir.glob("*.sh"))
        assert len(handler_files) > 0
        for hf in handler_files:
            assert _is_executable(hf)

    def test_idempotent(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        install_handler_scripts(wg_dir)
        any_written, count = install_handler_scripts(wg_dir)
        assert any_written is False
        assert count == 0


# ---------------------------------------------------------------------------
# install_session_driver_executor
# ---------------------------------------------------------------------------

class TestInstallSessionDriverExecutor:
    def test_creates_toml_and_script(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        wrote_toml, wrote_script = install_session_driver_executor(wg_dir)
        assert wrote_toml is True
        assert wrote_script is True
        assert (wg_dir / "executors" / "session-driver.toml").exists()
        assert (wg_dir / "executors" / "session-driver-run.sh").exists()
        assert _is_executable(wg_dir / "executors" / "session-driver-run.sh")

    def test_idempotent(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        install_session_driver_executor(wg_dir)
        wrote_toml, wrote_script = install_session_driver_executor(wg_dir)
        assert wrote_toml is False
        assert wrote_script is False


# ---------------------------------------------------------------------------
# _replace_marked_block / _replace_legacy_section (tested indirectly through adapters)
# ---------------------------------------------------------------------------

class TestAdapterBlockReplacement:
    def test_claude_adapter_replaces_marked_block(self, tmp_path: Path) -> None:
        """Install twice with a manual edit in between -- the block gets replaced."""
        install_claude_adapter(tmp_path)
        claude_md = tmp_path / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        # Manually edit inside the block
        modified = content.replace("Speedrift Ecosystem", "MODIFIED MARKER")
        # Keep the start/end markers intact so _replace_marked_block can find the block
        assert CLAUDE_ADAPTER_START in modified
        claude_md.write_text(modified, encoding="utf-8")
        result = install_claude_adapter(tmp_path)
        assert result.wrote_claude_md is True
        final = claude_md.read_text(encoding="utf-8")
        assert "MODIFIED MARKER" not in final
        assert CLAUDE_ADAPTER_MARKER in final

    def test_codex_adapter_replaces_marked_block(self, tmp_path: Path) -> None:
        install_codex_adapter(tmp_path)
        agents_md = tmp_path / "AGENTS.md"
        content = agents_md.read_text(encoding="utf-8")
        modified = content.replace("Driftdriver Integration Protocol", "OLD PROTOCOL")
        assert CODEX_ADAPTER_START in modified
        agents_md.write_text(modified, encoding="utf-8")
        result = install_codex_adapter(tmp_path)
        assert result.wrote_agents_md is True
        final = agents_md.read_text(encoding="utf-8")
        assert "OLD PROTOCOL" not in final
        assert CODEX_ADAPTER_MARKER in final


# ---------------------------------------------------------------------------
# _inject_*_into_template helpers (tested through ensure_executor_guidance)
# ---------------------------------------------------------------------------

class TestTemplateInjection:
    def _make_bare_executor(self, wg_dir: Path) -> None:
        """Create a minimal executor toml with a template but no drift markers."""
        executors_dir = wg_dir / "executors"
        executors_dir.mkdir(parents=True, exist_ok=True)
        # Create claude-run.sh so install_claude_executor_support doesn't fail
        runner = executors_dir / "claude-run.sh"
        runner.write_text("#!/bin/sh\n")
        runner.chmod(runner.stat().st_mode | stat.S_IXUSR)
        bin_dir = wg_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        timeout = bin_dir / "timeout"
        timeout.write_text("#!/bin/sh\n")
        timeout.chmod(timeout.stat().st_mode | stat.S_IXUSR)

        claude_toml = executors_dir / "claude.toml"
        # Minimal toml with template but no drift markers
        claude_toml.write_text(
            '[executor]\n'
            'type = "claude"\n'
            f'command = "{CLAUDE_EXECUTOR_WRAPPER}"\n'
            'args = []\n\n'
            '[executor.prompt_template]\n'
            'template = """\n'
            'You are working on a task.\n'
            '"""\n'
        )

    def test_inject_coredrift_into_bare_template(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        self._make_bare_executor(wg_dir)

        _, patched = ensure_executor_guidance(
            wg_dir,
            include_archdrift=False,
            include_uxdrift=False,
            include_therapydrift=False,
            include_fixdrift=False,
            include_yagnidrift=False,
            include_redrift=False,
        )
        assert len(patched) > 0
        content = (wg_dir / "executors" / "claude.toml").read_text(encoding="utf-8")
        assert COREDRIFT_MARKER in content

    def test_inject_multiple_drifts_into_bare_template(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        self._make_bare_executor(wg_dir)

        _, patched = ensure_executor_guidance(
            wg_dir,
            include_archdrift=True,
            include_uxdrift=True,
            include_therapydrift=True,
            include_fixdrift=True,
            include_yagnidrift=True,
            include_redrift=True,
        )
        assert len(patched) > 0
        content = (wg_dir / "executors" / "claude.toml").read_text(encoding="utf-8")
        assert COREDRIFT_MARKER in content
        assert ARCHDRIFT_MARKER in content
        assert UXDRIFT_MARKER in content
        assert THERAPYDRIFT_MARKER in content
        assert FIXDRIFT_MARKER in content
        assert YAGNIDRIFT_MARKER in content
        assert REDRIFT_MARKER in content


# ---------------------------------------------------------------------------
# Full integration: ensure all pieces compose
# ---------------------------------------------------------------------------

class TestFullInstallComposition:
    def test_all_gitignore_entries_written(self, tmp_path: Path) -> None:
        """All gitignore functions can write to the same file without conflict."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        for func, _ in _GITIGNORE_FUNCTIONS:
            func(wg_dir)
        content = (wg_dir / ".gitignore").read_text(encoding="utf-8")
        for _, entry in _GITIGNORE_FUNCTIONS:
            assert entry in content
        # Verify no duplicates
        lines = [l.strip() for l in content.splitlines() if l.strip()]
        assert len(lines) == len(set(lines))

    def test_all_wrappers_coexist(self, tmp_path: Path) -> None:
        """All wrapper scripts can be installed into the same .workgraph dir."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        fake_bin = _make_fake_bin(tmp_path, "tool")
        write_driver_wrapper(wg_dir, driver_bin=fake_bin)
        write_drifts_wrapper(wg_dir)
        write_coredrift_wrapper(wg_dir, coredrift_bin=fake_bin)
        write_specdrift_wrapper(wg_dir, specdrift_bin=fake_bin)
        write_datadrift_wrapper(wg_dir, datadrift_bin=fake_bin)
        write_archdrift_wrapper(wg_dir, archdrift_bin=fake_bin)
        write_depsdrift_wrapper(wg_dir, depsdrift_bin=fake_bin)
        write_uxdrift_wrapper(wg_dir, uxdrift_bin=fake_bin)
        write_therapydrift_wrapper(wg_dir, therapydrift_bin=fake_bin)
        write_fixdrift_wrapper(wg_dir, fixdrift_bin=fake_bin)
        write_yagnidrift_wrapper(wg_dir, yagnidrift_bin=fake_bin)
        write_redrift_wrapper(wg_dir, redrift_bin=fake_bin)
        write_qadrift_wrapper(wg_dir)

        expected_wrappers = [
            "driftdriver", "drifts", "coredrift", "specdrift", "datadrift",
            "archdrift", "depsdrift", "uxdrift", "therapydrift", "fixdrift",
            "yagnidrift", "redrift", "qadrift",
        ]
        for name in expected_wrappers:
            wrapper = wg_dir / name
            assert wrapper.exists(), f"Missing wrapper: {name}"
            assert _is_executable(wrapper), f"Not executable: {name}"

    def test_all_adapters_coexist(self, tmp_path: Path) -> None:
        """All adapter functions can install into the same project dir."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        install_claude_adapter(tmp_path)
        install_codex_adapter(tmp_path)
        install_opencode_hooks(tmp_path)
        install_amplifier_adapter(tmp_path)
        install_claude_code_hooks(tmp_path)
        install_session_driver_executor(wg_dir)
        install_lessons_mcp_config(wg_dir)

        assert (tmp_path / "CLAUDE.md").exists()
        assert (tmp_path / "AGENTS.md").exists()
        assert (tmp_path / ".opencode" / "hooks.json").exists()
        assert (tmp_path / ".amplifier" / "hooks" / "driftdriver" / "session-hooks.sh").exists()
        assert (tmp_path / ".claude" / "hooks.json").exists()
        assert (wg_dir / "executors" / "session-driver.toml").exists()
        assert (tmp_path / ".mcp.json").exists()
