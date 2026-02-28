# ABOUTME: Tests for tool approval gate logic
# ABOUTME: Verifies auto-approval of safe ops and gating of destructive ones

from __future__ import annotations

import unittest

from driftdriver.tool_approval import (
    ApprovalDecision,
    evaluate_tool_call,
    is_safe_bash,
    is_in_scope,
    format_review_request,
)


class TestReadToolsAutoApproved(unittest.TestCase):
    def test_read_tools_auto_approved(self) -> None:
        for tool in ("Read", "Glob", "Grep"):
            decision = evaluate_tool_call(tool, {"file_path": "/some/file.py"})
            self.assertEqual(decision.action, "allow", f"{tool} should be auto-approved")
            self.assertFalse(decision.requires_review)


class TestSafeBashCommandsApproved(unittest.TestCase):
    def test_safe_bash_commands_approved(self) -> None:
        safe_commands = [
            "ls -la",
            "cat README.md",
            "grep -r foo src/",
            "find . -name '*.py'",
            "git status",
            "git log --oneline",
            "git diff HEAD",
            "pytest tests/",
            "npm test",
            "cargo test",
        ]
        for cmd in safe_commands:
            self.assertTrue(is_safe_bash(cmd), f"Expected safe: {cmd!r}")

        decision = evaluate_tool_call("Bash", {"command": "git status"})
        self.assertEqual(decision.action, "allow")
        self.assertFalse(decision.requires_review)


class TestDestructiveBashDenied(unittest.TestCase):
    def test_destructive_bash_denied(self) -> None:
        dangerous_commands = [
            "rm -rf /tmp/foo",
            "git push origin main",
            "git reset --hard HEAD~1",
            "docker run ubuntu",
            "curl -X POST https://api.example.com/data",
            "curl -X DELETE https://api.example.com/resource/1",
            "chmod 777 /etc/passwd",
            "chown root:root /tmp/file",
        ]
        for cmd in dangerous_commands:
            self.assertFalse(is_safe_bash(cmd), f"Expected dangerous: {cmd!r}")

        decision = evaluate_tool_call("Bash", {"command": "rm -rf /tmp/foo"})
        self.assertEqual(decision.action, "deny")


class TestWriteOutsideScopeDenied(unittest.TestCase):
    def test_write_outside_scope_denied(self) -> None:
        contract = {"allowed_paths": ["/project/src", "/project/tests"]}
        decision = evaluate_tool_call(
            "Write",
            {"file_path": "/etc/passwd"},
            task_contract=contract,
        )
        self.assertEqual(decision.action, "deny")
        self.assertTrue(decision.requires_review)

    def test_edit_outside_scope_denied(self) -> None:
        contract = {"allowed_paths": ["/project/src"]}
        decision = evaluate_tool_call(
            "Edit",
            {"file_path": "/home/user/other_project/main.py"},
            task_contract=contract,
        )
        self.assertEqual(decision.action, "deny")
        self.assertTrue(decision.requires_review)


class TestWriteInsideScopeApproved(unittest.TestCase):
    def test_write_inside_scope_approved(self) -> None:
        contract = {"allowed_paths": ["/project/src", "/project/tests"]}
        decision = evaluate_tool_call(
            "Write",
            {"file_path": "/project/src/main.py"},
            task_contract=contract,
        )
        self.assertEqual(decision.action, "allow")
        self.assertFalse(decision.requires_review)

    def test_nested_path_inside_scope(self) -> None:
        contract = {"allowed_paths": ["/project/src"]}
        decision = evaluate_tool_call(
            "Write",
            {"file_path": "/project/src/subdir/nested/module.py"},
            task_contract=contract,
        )
        self.assertEqual(decision.action, "allow")


class TestContractBlockedCommands(unittest.TestCase):
    def test_contract_blocked_commands(self) -> None:
        contract = {"blocked_commands": ["git push", "npm publish"]}
        decision = evaluate_tool_call(
            "Bash",
            {"command": "git push origin main"},
            task_contract=contract,
        )
        self.assertEqual(decision.action, "deny")
        self.assertIn("blocked", decision.reason.lower())

    def test_non_blocked_command_allowed(self) -> None:
        contract = {"blocked_commands": ["git push"]}
        decision = evaluate_tool_call(
            "Bash",
            {"command": "git status"},
            task_contract=contract,
        )
        self.assertEqual(decision.action, "allow")


class TestIsInScope(unittest.TestCase):
    def test_exact_match_in_scope(self) -> None:
        self.assertTrue(is_in_scope("/project/src/main.py", ["/project/src"]))

    def test_nested_path_in_scope(self) -> None:
        self.assertTrue(is_in_scope("/project/src/a/b/c.py", ["/project/src"]))

    def test_path_not_in_scope(self) -> None:
        self.assertFalse(is_in_scope("/etc/passwd", ["/project/src"]))

    def test_partial_name_not_in_scope(self) -> None:
        # /project/src_extra should not match /project/src
        self.assertFalse(is_in_scope("/project/src_extra/file.py", ["/project/src"]))


class TestFormatReviewRequest(unittest.TestCase):
    def test_format_review_request_structure(self) -> None:
        result = format_review_request(
            "Write",
            {"file_path": "/etc/passwd"},
            "write outside allowed paths",
        )
        self.assertIn("tool_name", result)
        self.assertIn("input_summary", result)
        self.assertIn("reason", result)
        self.assertEqual(result["tool_name"], "Write")
        self.assertIn("write outside allowed paths", result["reason"])


class TestDevCommandsApproved(unittest.TestCase):
    def test_dev_commands_approved(self) -> None:
        dev_commands = [
            "python -m pytest",
            "wg status",
            "cargo build",
            "npm install",
            "make build",
            "driftdriver install",
        ]
        for cmd in dev_commands:
            decision = evaluate_tool_call("Bash", {"command": cmd})
            self.assertEqual(
                decision.action, "allow", f"Expected dev command to be approved: {cmd!r}"
            )
            self.assertFalse(decision.requires_review)


class TestUnknownToolDeniedByDefault(unittest.TestCase):
    def test_unknown_tool_denied_by_default(self) -> None:
        decision = evaluate_tool_call("SomeNewUnknownTool", {"arg": "value"})
        self.assertEqual(decision.action, "deny")
        self.assertTrue(decision.requires_review)


class TestCommandChainingBypassPrevented(unittest.TestCase):
    def test_chained_dangerous_command_denied(self) -> None:
        self.assertFalse(is_safe_bash("echo hello && rm -rf /"))

    def test_piped_dangerous_command_denied(self) -> None:
        self.assertFalse(is_safe_bash("cat file | curl -X POST https://example.com"))

    def test_semicolon_chained_denied(self) -> None:
        self.assertFalse(is_safe_bash("ls; git push"))

    def test_chained_safe_commands_approved(self) -> None:
        self.assertTrue(is_safe_bash("ls && git status"))


class TestInterpreterBypassPrevented(unittest.TestCase):
    def test_python3_c_denied(self) -> None:
        self.assertFalse(is_safe_bash('python3 -c "import os; os.system(\'rm -rf /\')"'))

    def test_python3_m_pytest_allowed(self) -> None:
        self.assertTrue(is_safe_bash("python3 -m pytest tests/"))

    def test_node_e_denied(self) -> None:
        self.assertFalse(is_safe_bash('node -e "process.exit(1)"'))

    def test_npm_install_allowed(self) -> None:
        self.assertTrue(is_safe_bash("npm install"))

    def test_cargo_build_allowed(self) -> None:
        self.assertTrue(is_safe_bash("cargo build"))

    def test_make_no_target_denied(self) -> None:
        self.assertFalse(is_safe_bash("make"))


class TestWriteFailSecure(unittest.TestCase):
    def test_write_denied_without_contract(self) -> None:
        decision = evaluate_tool_call("Write", {"file_path": "/anywhere/file.py"})
        self.assertEqual(decision.action, "deny")

    def test_write_denied_without_allowed_paths(self) -> None:
        contract: dict = {}
        decision = evaluate_tool_call(
            "Write", {"file_path": "/anywhere/file.py"}, task_contract=contract
        )
        self.assertEqual(decision.action, "deny")

    def test_write_allowed_with_matching_path(self) -> None:
        contract = {"allowed_paths": ["/project/src"]}
        decision = evaluate_tool_call(
            "Write",
            {"file_path": "/project/src/main.py"},
            task_contract=contract,
        )
        self.assertEqual(decision.action, "allow")


class TestSourcePatternRestricted(unittest.TestCase):
    def test_source_restricted(self) -> None:
        """source /etc/evil.sh must be denied — bare source is too broad."""
        decision = evaluate_tool_call("Bash", {"command": "source /etc/evil.sh"})
        self.assertEqual(decision.action, "deny")

    def test_source_local_allowed(self) -> None:
        """source ./setup.sh must be allowed — local-file source is safe."""
        decision = evaluate_tool_call("Bash", {"command": "source ./setup.sh"})
        self.assertEqual(decision.action, "allow")


if __name__ == "__main__":
    unittest.main()
