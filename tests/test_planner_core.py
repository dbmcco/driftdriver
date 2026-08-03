# ABOUTME: Tests for planner_core — the consolidated decomposition core module.
# ABOUTME: Covers parsing, route validation, prompt building, and materialization.

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from driftdriver.planner_core import (
    BUILTIN_PATTERNS,
    BUNDLE_DECOMPOSE_CLI,
    BUNDLE_QUALITY_SPEC,
    DEFAULT_MODEL_ROUTE_POLICY,
    ModelRoutePolicy,
    PlannedNode,
    PolicyBundle,
    RouteViolation,
    agency_fence,
    apply_agency_fences,
    build_decompose_prompt,
    insert_review_gates,
    materialize_plan,
    parse_plan_output,
    validate_model_routes,
)


def _ok(returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    """Build a fake subprocess result for injected runners."""
    return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)


class BuiltinPatternsTests(unittest.TestCase):
    def test_all_five_patterns_present(self) -> None:
        for name in ("e2e-breakfix", "ux-eval", "data-eval", "contract-test", "northstar-checkpoint"):
            self.assertIn(name, BUILTIN_PATTERNS)

    def test_each_pattern_has_required_keys(self) -> None:
        for name, pattern in BUILTIN_PATTERNS.items():
            self.assertIn("description", pattern, f"{name} missing description")
            self.assertIn("when", pattern, f"{name} missing when")
            self.assertIn("structure", pattern, f"{name} missing structure")


class PlannedNodeTests(unittest.TestCase):
    def test_to_dict_includes_core_fields(self) -> None:
        node = PlannedNode(id="impl-auth", title="Implement auth")
        d = node.to_dict()
        self.assertEqual(d["id"], "impl-auth")
        self.assertEqual(d["title"], "Implement auth")
        self.assertEqual(d["type"], "code")
        self.assertEqual(d["risk"], "medium")
        self.assertEqual(d["after"], [])

    def test_to_dict_conditional_inclusion(self) -> None:
        node = PlannedNode(id="x", title="X")
        d = node.to_dict()
        self.assertNotIn("description", d)
        self.assertNotIn("pattern", d)
        self.assertNotIn("max_iterations", d)
        self.assertNotIn("verify", d)
        self.assertNotIn("touch", d)
        self.assertNotIn("acceptance", d)

        node2 = PlannedNode(
            id="y", title="Y", description="desc", pattern="e2e-breakfix",
            max_iterations=3, verify="pytest", touch=["a.py"], acceptance=["passes"],
        )
        d2 = node2.to_dict()
        self.assertEqual(d2["description"], "desc")
        self.assertEqual(d2["pattern"], "e2e-breakfix")
        self.assertEqual(d2["max_iterations"], 3)
        self.assertEqual(d2["verify"], "pytest")
        self.assertEqual(d2["touch"], ["a.py"])
        self.assertEqual(d2["acceptance"], ["passes"])


class PolicyBundleTests(unittest.TestCase):
    def test_agent_executes_mode_inline_bundle(self) -> None:
        bundle = PolicyBundle(name="test", mode="agent-executes")
        self.assertEqual(bundle.mode, "agent-executes")

    def test_bundle_decompose_cli_is_emit_json(self) -> None:
        self.assertEqual(BUNDLE_DECOMPOSE_CLI.mode, "emit-json")
        self.assertEqual(BUNDLE_DECOMPOSE_CLI.name, "decompose-cli")
        self.assertEqual(BUNDLE_DECOMPOSE_CLI.task_count_hint, "3-8")

    def test_bundle_quality_spec_has_patterns(self) -> None:
        self.assertEqual(BUNDLE_QUALITY_SPEC.mode, "emit-json")
        self.assertEqual(BUNDLE_QUALITY_SPEC.name, "quality-spec")
        self.assertEqual(len(BUNDLE_QUALITY_SPEC.patterns), len(BUILTIN_PATTERNS))


class ParsePlanOutputTests(unittest.TestCase):
    def test_parses_json_array_directly(self) -> None:
        raw = json.dumps([
            {"id": "impl-auth", "title": "Implement auth", "after": [],
             "type": "code", "risk": "medium", "description": "Build OAuth"}
        ])
        result = parse_plan_output(raw)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], PlannedNode)
        self.assertEqual(result[0].id, "impl-auth")
        self.assertEqual(result[0].title, "Implement auth")

    def test_parses_tasks_wrapper_object(self) -> None:
        raw = json.dumps({"tasks": [
            {"id": "feat-x", "title": "Build X", "after": [],
             "type": "code", "risk": "low", "description": "Do X"}
        ]})
        result = parse_plan_output(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "feat-x")

    def test_parses_json_in_markdown_code_block(self) -> None:
        raw = (
            "Here is the plan:\n"
            "```json\n"
            '{"tasks": [{"id": "feat-y", "title": "Build Y", "after": [], '
            '"type": "code", "risk": "low"}]}\n'
            "```\n"
            "That's the plan."
        )
        result = parse_plan_output(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "feat-y")

    def test_parses_brace_span_extraction(self) -> None:
        raw = (
            "Some preamble text here.\n"
            '{"tasks": [{"id": "span-1", "title": "Span task", "after": [], '
            '"type": "code", "risk": "low"}]}\n'
            "Some trailing text."
        )
        result = parse_plan_output(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "span-1")

    def test_garbage_returns_empty_list(self) -> None:
        result = parse_plan_output("This is not JSON at all.")
        self.assertEqual(result, [])

    def test_empty_string_returns_empty_list(self) -> None:
        self.assertEqual(parse_plan_output(""), [])

    def test_preserves_optional_fields(self) -> None:
        raw = json.dumps({"tasks": [
            {"id": "gate-1", "title": "E2E gate", "after": ["impl-1"],
             "type": "quality-gate", "risk": "high", "description": "Run e2e",
             "pattern": "e2e-breakfix", "max_iterations": 5,
             "verify": "pytest", "touch": ["tests/"], "acceptance": ["all pass"]}
        ]})
        result = parse_plan_output(raw)
        node = result[0]
        self.assertEqual(node.pattern, "e2e-breakfix")
        self.assertEqual(node.max_iterations, 5)
        self.assertEqual(node.verify, "pytest")
        self.assertEqual(node.touch, ["tests/"])
        self.assertEqual(node.acceptance, ["all pass"])

    def test_defaults_for_missing_fields(self) -> None:
        raw = json.dumps([{"id": "bare", "title": "Bare"}])
        result = parse_plan_output(raw)
        self.assertEqual(result[0].task_type, "code")
        self.assertEqual(result[0].risk, "medium")
        self.assertEqual(result[0].after, [])
        self.assertIsNone(result[0].pattern)


class ValidateModelRoutesTests(unittest.TestCase):
    def test_anthropic_colon_prefix_flagged(self) -> None:
        violations = validate_model_routes({"n1": "anthropic:claude-sonnet"})
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].node_id, "n1")
        self.assertIn("prohibited", violations[0].reason.lower())

    def test_anthropic_slash_prefix_flagged(self) -> None:
        violations = validate_model_routes({"n1": "anthropic/claude-opus"})
        self.assertEqual(len(violations), 1)

    def test_lunaroute_flagged_by_default(self) -> None:
        violations = validate_model_routes({"n1": "lunaroute:glm-5.2-nvfp4"})
        self.assertEqual(len(violations), 1)
        self.assertIn("conditional", violations[0].reason.lower())

    def test_lunaroute_allowed_with_flag(self) -> None:
        violations = validate_model_routes(
            {"n1": "lunaroute:glm-5.2-nvfp4"}, allow_conditional=True,
        )
        self.assertEqual(violations, [])

    def test_clean_routes_pass(self) -> None:
        routes = {
            "n1": "zai:glm-5.2",
            "n2": "ollama:qwopus3.6:27b-mtp-q4",
            "n3": "kimi-coding:kimi-for-coding",
        }
        self.assertEqual(validate_model_routes(routes), [])

    def test_custom_policy(self) -> None:
        custom = ModelRoutePolicy(prohibited_prefixes=("openai",), conditional_providers=())
        violations = validate_model_routes({"n1": "openai:gpt-5.5"}, policy=custom)
        self.assertEqual(len(violations), 1)

    def test_reason_is_full_sentence(self) -> None:
        violations = validate_model_routes({"n1": "anthropic:sonnet"})
        self.assertTrue(violations[0].reason.endswith("."))

    def test_mixed_case_prohibited_prefix_flagged(self) -> None:
        violations = validate_model_routes({"n1": "Anthropic/Claude"})
        self.assertEqual(len(violations), 1)
        self.assertIn("prohibited", violations[0].reason.lower())

    def test_mixed_case_conditional_provider_flagged(self) -> None:
        violations = validate_model_routes({"n1": "LunaRoute:glm-5.2-nvfp4"})
        self.assertEqual(len(violations), 1)
        self.assertIn("conditional", violations[0].reason.lower())


class BuildDecomposePromptTests(unittest.TestCase):
    def test_agent_executes_contains_goal(self) -> None:
        prompt = build_decompose_prompt("Build a REST API", bundle=PolicyBundle(name="test", mode="agent-executes"))
        self.assertIn("REST API", prompt)

    def test_agent_executes_contains_coredrift_instruction(self) -> None:
        prompt = build_decompose_prompt("Do something", bundle=PolicyBundle(name="test", mode="agent-executes"))
        self.assertIn("coredrift ensure-contracts", prompt)

    def test_agent_executes_contains_project_dir(self) -> None:
        prompt = build_decompose_prompt(
            "Do something", project_dir=Path("/my/repo"), bundle=PolicyBundle(name="test", mode="agent-executes"),
        )
        self.assertIn("/my/repo", prompt)

    def test_agent_executes_contains_context(self) -> None:
        prompt = build_decompose_prompt(
            "Do something", context="Extra context here", bundle=PolicyBundle(name="test", mode="agent-executes"),
        )
        self.assertIn("Extra context here", prompt)

    def test_emit_json_contains_wg_contract_instruction(self) -> None:
        prompt = build_decompose_prompt("Build X", bundle=BUNDLE_DECOMPOSE_CLI)
        self.assertIn("wg-contract", prompt)
        self.assertIn("JSON", prompt)

    def test_emit_json_contains_task_count_hint(self) -> None:
        prompt = build_decompose_prompt("Build X", bundle=BUNDLE_DECOMPOSE_CLI)
        self.assertIn("3-8", prompt)

    def test_emit_json_contains_pattern_names(self) -> None:
        prompt = build_decompose_prompt("Build X", bundle=BUNDLE_QUALITY_SPEC)
        for name in BUILTIN_PATTERNS:
            self.assertIn(name, prompt)

    def test_emit_json_without_patterns_omits_repertoire(self) -> None:
        prompt = build_decompose_prompt("Build X", bundle=BUNDLE_DECOMPOSE_CLI)
        self.assertNotIn("Quality Pattern Repertoire", prompt)

    def test_extra_instructions_appended(self) -> None:
        bundle = PolicyBundle(
            name="custom", mode="emit-json", extra_instructions="REMEMBER: push before done.",
        )
        prompt = build_decompose_prompt("Build X", bundle=bundle)
        self.assertIn("REMEMBER: push before done.", prompt)


class MaterializePlanTests(unittest.TestCase):
    def test_correct_argv_construction(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return _ok()

        node = PlannedNode(
            id="impl-auth", title="Implement auth",
            after=["setup-deps"], description="Build OAuth", verify="pytest tests/",
        )
        materialize_plan([node], Path("/repo"), runner=runner)

        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        self.assertEqual(cmd[0], "wg")
        self.assertEqual(cmd[1], "add")
        self.assertIn("Implement auth", cmd)
        # --id
        idx = cmd.index("--id")
        self.assertEqual(cmd[idx + 1], "impl-auth")
        # --blocked-by
        idx = cmd.index("--blocked-by")
        self.assertEqual(cmd[idx + 1], "setup-deps")
        # -d
        idx = cmd.index("-d")
        self.assertEqual(cmd[idx + 1], "Build OAuth")
        # --verify
        idx = cmd.index("--verify")
        self.assertEqual(cmd[idx + 1], "pytest tests/")

    def test_added_count_accuracy(self) -> None:
        nodes = [
            PlannedNode(id="a", title="Task A"),
            PlannedNode(id="b", title="Task B"),
            PlannedNode(id="c", title="Task C"),
        ]
        count = materialize_plan(nodes, Path("/repo"), runner=lambda cmd, **kw: _ok())
        self.assertEqual(count, 3)

    def test_nonzero_returncode_not_counted(self) -> None:
        node = PlannedNode(id="fail-task", title="Failing")
        count = materialize_plan(
            [node], Path("/repo"),
            runner=lambda cmd, **kw: _ok(returncode=1, stderr="duplicate id"),
        )
        self.assertEqual(count, 0)

    def test_desc_builder_overrides_description(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return _ok()

        node = PlannedNode(id="x", title="X", description="original")
        materialize_plan(
            [node], Path("/repo"),
            desc_builder=lambda n: f"CUSTOM:{n.id}",
            runner=runner,
        )
        idx = calls[0].index("-d")
        self.assertEqual(calls[0][idx + 1], "CUSTOM:x")

    def test_verify_fallback_used_when_verify_empty(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return _ok()

        node = PlannedNode(id="x", title="X")
        materialize_plan(
            [node], Path("/repo"),
            verify_fallback=lambda n: f"test {n.id}",
            runner=runner,
        )
        idx = calls[0].index("--verify")
        self.assertEqual(calls[0][idx + 1], "test x")

    def test_tag_builder_adds_tags(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return _ok()

        node = PlannedNode(id="gate-1", title="E2E gate", task_type="quality-gate")
        materialize_plan(
            [node], Path("/repo"),
            tag_builder=lambda n: ["quality"] if n.task_type != "code" else [],
            runner=runner,
        )
        self.assertIn("--tag", calls[0])
        idx = calls[0].index("--tag")
        self.assertEqual(calls[0][idx + 1], "quality")

    def test_fail_closed_skips_prohibited_route(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return _ok()

        nodes = [
            PlannedNode(id="ok", title="OK task"),
            PlannedNode(id="bad", title="Bad task"),
        ]
        route_models = {"ok": "zai:glm-5.2", "bad": "anthropic:claude-sonnet"}
        count = materialize_plan(
            nodes, Path("/repo"), route_models=route_models, runner=runner,
        )
        self.assertEqual(count, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn("ok", calls[0])
        self.assertNotIn("bad", " ".join(calls[0]))

    def test_no_description_no_d_flag(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return _ok()

        node = PlannedNode(id="bare", title="Bare task")
        materialize_plan([node], Path("/repo"), runner=runner)
        self.assertNotIn("-d", calls[0])


class PlannedNodeRouteFieldsTests(unittest.TestCase):
    def test_to_dict_includes_route_fields_when_set(self) -> None:
        node = PlannedNode(
            id="n1", title="T",
            model="zai:glm-5.2", route_tier="standard",
            escalation_reason="complex domain logic",
        )
        d = node.to_dict()
        self.assertEqual(d["model"], "zai:glm-5.2")
        self.assertEqual(d["route_tier"], "standard")
        self.assertEqual(d["escalation_reason"], "complex domain logic")

    def test_to_dict_omits_route_fields_when_empty(self) -> None:
        node = PlannedNode(id="n1", title="T")
        d = node.to_dict()
        self.assertNotIn("model", d)
        self.assertNotIn("route_tier", d)
        self.assertNotIn("escalation_reason", d)

    def test_parse_maps_route_fields(self) -> None:
        raw = json.dumps([
            {"id": "n1", "title": "T", "model": "ollama:gemma",
             "route_tier": "fast", "escalation_reason": ""}
        ])
        nodes = parse_plan_output(raw)
        self.assertEqual(nodes[0].model, "ollama:gemma")
        self.assertEqual(nodes[0].route_tier, "fast")
        self.assertEqual(nodes[0].escalation_reason, "")

    def test_parse_defaults_route_fields_to_empty(self) -> None:
        raw = json.dumps([{"id": "n1", "title": "T"}])
        nodes = parse_plan_output(raw)
        self.assertEqual(nodes[0].model, "")
        self.assertEqual(nodes[0].route_tier, "")
        self.assertEqual(nodes[0].escalation_reason, "")


class TierOfTests(unittest.TestCase):
    def test_ollama_is_fast(self) -> None:
        self.assertEqual(
            DEFAULT_MODEL_ROUTE_POLICY.tier_of("ollama:qwopus3.6:27b-mtp-q4"), "fast",
        )

    def test_zai_is_standard(self) -> None:
        self.assertEqual(DEFAULT_MODEL_ROUTE_POLICY.tier_of("zai:glm-5.2"), "standard")

    def test_kimi_coding_standard(self) -> None:
        self.assertEqual(
            DEFAULT_MODEL_ROUTE_POLICY.tier_of("kimi-coding:kimi-for-coding"), "standard",
        )

    def test_kimi_coding_k3_is_premium(self) -> None:
        self.assertEqual(
            DEFAULT_MODEL_ROUTE_POLICY.tier_of("kimi-coding:k3"), "premium",
        )

    def test_openai_codex_is_premium(self) -> None:
        self.assertEqual(
            DEFAULT_MODEL_ROUTE_POLICY.tier_of("openai-codex:gpt-5.5"), "premium",
        )

    def test_unknown_model(self) -> None:
        self.assertEqual(DEFAULT_MODEL_ROUTE_POLICY.tier_of("random:model"), "unknown")


class EscalationReasonTests(unittest.TestCase):
    def test_premium_without_reason_flagged(self) -> None:
        violations = validate_model_routes({"n1": "kimi-coding:k3"})
        kinds = [v.kind for v in violations]
        self.assertIn("missing-escalation-reason", kinds)

    def test_premium_with_reason_passes(self) -> None:
        violations = validate_model_routes(
            {"n1": "kimi-coding:k3"},
            escalation_reasons={"n1": "critical cross-system integration"},
        )
        self.assertEqual(violations, [])

    def test_standard_needs_no_reason(self) -> None:
        violations = validate_model_routes({"n1": "zai:glm-5.2"})
        self.assertEqual(violations, [])

    def test_fast_needs_no_reason(self) -> None:
        violations = validate_model_routes({"n1": "ollama:gemma"})
        self.assertEqual(violations, [])

    def test_missing_reason_violation_kind(self) -> None:
        violations = validate_model_routes({"n1": "openai-codex:gpt-5.5"})
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].kind, "missing-escalation-reason")

    def test_prohibited_kind_still_set(self) -> None:
        violations = validate_model_routes({"n1": "anthropic:claude"})
        self.assertEqual(violations[0].kind, "prohibited")

    def test_conditional_kind_still_set(self) -> None:
        violations = validate_model_routes({"n1": "lunaroute:glm"})
        self.assertEqual(violations[0].kind, "conditional")


class MaterializeStripPinTests(unittest.TestCase):
    def test_strip_pin_for_missing_reason(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return _ok()

        node = PlannedNode(id="premium-task", title="Premium", model="kimi-coding:k3")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            count = materialize_plan([node], Path("/repo"), runner=runner)
        self.assertEqual(count, 1)
        self.assertNotIn("--model", calls[0])
        self.assertIn("route pin stripped", err.getvalue())

    def test_prohibited_still_hard_skipped(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return _ok()

        node = PlannedNode(id="bad", title="Bad", model="anthropic:claude")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            count = materialize_plan([node], Path("/repo"), runner=runner)
        self.assertEqual(count, 0)
        self.assertEqual(len(calls), 0)

    def test_premium_with_reason_keeps_pin(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return _ok()

        node = PlannedNode(id="premium-task", title="Premium", model="kimi-coding:k3")
        materialize_plan(
            [node], Path("/repo"),
            escalation_reasons={"premium-task": "critical integration"},
            runner=runner,
        )
        self.assertIn("--model", calls[0])
        idx = calls[0].index("--model")
        self.assertEqual(calls[0][idx + 1], "kimi-coding:k3")


class InsertReviewGatesTests(unittest.TestCase):
    def test_chain_inserts_gates_and_rewires(self) -> None:
        a = PlannedNode(id="a", title="A", task_type="code", after=[])
        b = PlannedNode(id="b", title="B", task_type="code", after=["a"])
        c = PlannedNode(id="c", title="C", task_type="code", after=["b"])
        result = insert_review_gates([a, b, c])
        ids = {n.id for n in result}
        self.assertIn("review-a", ids)
        self.assertIn("review-b", ids)
        self.assertIn("review-c", ids)
        # B now depends on review-a
        b_node = next(n for n in result if n.id == "b")
        self.assertIn("review-a", b_node.after)
        self.assertNotIn("a", b_node.after)
        # C now depends on review-b
        c_node = next(n for n in result if n.id == "c")
        self.assertIn("review-b", c_node.after)
        self.assertNotIn("b", c_node.after)

    def test_gate_after_points_to_source(self) -> None:
        a = PlannedNode(id="a", title="A", task_type="code")
        result = insert_review_gates([a])
        gate = next(n for n in result if n.id == "review-a")
        self.assertEqual(gate.after, ["a"])

    def test_gate_is_review_type(self) -> None:
        a = PlannedNode(id="a", title="A", task_type="code")
        result = insert_review_gates([a])
        gate = next(n for n in result if n.id == "review-a")
        self.assertEqual(gate.task_type, "review")

    def test_gate_description_mentions_roborev(self) -> None:
        a = PlannedNode(id="a", title="A", task_type="code")
        result = insert_review_gates([a])
        gate = next(n for n in result if n.id == "review-a")
        self.assertIn("roborev", gate.description.lower())

    def test_non_code_nodes_get_no_gate(self) -> None:
        qg = PlannedNode(id="qg", title="Quality gate", task_type="quality-gate")
        result = insert_review_gates([qg])
        ids = {n.id for n in result}
        self.assertNotIn("review-qg", ids)

    def test_diamond_no_cycle(self) -> None:
        a = PlannedNode(id="a", title="A", task_type="code", after=[])
        b = PlannedNode(id="b", title="B", task_type="code", after=["a"])
        c = PlannedNode(id="c", title="C", task_type="code", after=["a"])
        d = PlannedNode(id="d", title="D", task_type="code", after=["b", "c"])
        result = insert_review_gates([a, b, c, d])
        adj = {n.id: list(n.after) for n in result}
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {nid: WHITE for nid in adj}

        def visit(nid: str) -> bool:
            color[nid] = GRAY
            for dep in adj[nid]:
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    return True
                if color[dep] == WHITE and visit(dep):
                    return True
            color[nid] = BLACK
            return False

        has_cycle = any(color[nid] == WHITE and visit(nid) for nid in list(color))
        self.assertFalse(has_cycle)


class AgencyFenceTests(unittest.TestCase):
    def test_fence_content(self) -> None:
        fence = agency_fence("reviewer")
        self.assertIn("```agencydrift", fence)
        self.assertIn("schema = 1", fence)
        self.assertIn('profile = "reviewer"', fence)
        self.assertIn('preferred_runtime = "agency"', fence)
        self.assertIn('fallback_runtime = "codexd"', fence)

    def test_fence_custom_runtimes(self) -> None:
        fence = agency_fence("critic", preferred_runtime="codexd", fallback_runtime="codex")
        self.assertIn('preferred_runtime = "codexd"', fence)

    def test_apply_adds_fence(self) -> None:
        node = PlannedNode(id="n1", title="T", description="Do work")
        result = apply_agency_fences([node], {"n1": "worker"})
        self.assertIn("agencydrift", result[0].description)
        self.assertIn('profile = "worker"', result[0].description)

    def test_apply_idempotent(self) -> None:
        node = PlannedNode(
            id="n1", title="T",
            description='Do work\n\n```agencydrift\nprofile = "x"\n```',
        )
        result = apply_agency_fences([node], {"n1": "worker"})
        self.assertEqual(result[0].description.count("agencydrift"), 1)

    def test_apply_skips_unmapped_nodes(self) -> None:
        node = PlannedNode(id="n1", title="T", description="Do work")
        result = apply_agency_fences([node], {"n2": "worker"})
        self.assertEqual(result[0].description, "Do work")


if __name__ == "__main__":
    unittest.main()
