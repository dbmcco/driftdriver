# ABOUTME: Tests for plan_preflight — all-or-nothing graph and scope validation
# ABOUTME: over a complete planner batch before any wg add publication.

from __future__ import annotations

from pathlib import Path

from driftdriver.plan_preflight import path_matches, preflight_plan
from driftdriver.planner_core import PlannedNode


def _categories(nodes: list[PlannedNode], **kwargs: object) -> list[str]:
    return [f.category for f in preflight_plan(nodes, Path("/repo"), **kwargs).findings]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Graph validation
# ---------------------------------------------------------------------------


def test_duplicate_ids_are_fatal() -> None:
    nodes = [
        PlannedNode(id="same", title="One"),
        PlannedNode(id="same", title="Two"),
    ]
    result = preflight_plan(nodes, Path("/repo"))
    assert not result.ok
    assert "duplicate-task-id" in _categories(nodes)
    assert all(f.task_id == "same" for f in result.findings if f.category == "duplicate-task-id")


def test_blank_id_is_fatal() -> None:
    result = preflight_plan([PlannedNode(id="", title="Missing")], Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["missing-task-id"]


def test_unknown_dependency_is_fatal_but_existing_ids_are_allowed() -> None:
    nodes = [PlannedNode(id="child", title="Child", after=["missing"])]
    result = preflight_plan(nodes, Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["unknown-dependency"]
    assert "missing" in result.findings[0].message

    allowed = preflight_plan(nodes, Path("/repo"), existing_ids={"missing"})
    assert allowed.ok
    assert allowed.findings == []


def test_self_dependency_is_fatal() -> None:
    nodes = [PlannedNode(id="loop", title="Loop", after=["loop"])]
    result = preflight_plan(nodes, Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["self-dependency"]


def test_dependency_cycle_is_fatal_with_cycle_members() -> None:
    nodes = [
        PlannedNode(id="a", title="A", after=["b"]),
        PlannedNode(id="b", title="B", after=["a"]),
    ]
    result = preflight_plan(nodes, Path("/repo"))
    assert not result.ok
    cycle_findings = [f for f in result.findings if f.category == "dependency-cycle"]
    assert len(cycle_findings) == 1
    assert "a" in cycle_findings[0].message
    assert "b" in cycle_findings[0].message


def test_larger_cycle_reports_every_member_once() -> None:
    nodes = [
        PlannedNode(id="c", title="C", after=["a"]),
        PlannedNode(id="a", title="A", after=["b"]),
        PlannedNode(id="b", title="B", after=["c"]),
        PlannedNode(id="after-cycle", title="Downstream", after=["a"]),
    ]
    result = preflight_plan(nodes, Path("/repo"))
    cycle_findings = [f for f in result.findings if f.category == "dependency-cycle"]
    assert len(cycle_findings) == 1
    for member in ("a", "b", "c"):
        assert member in cycle_findings[0].message


def test_valid_graph_passes() -> None:
    nodes = [
        PlannedNode(id="base", title="Base"),
        PlannedNode(id="child", title="Child", after=["base"]),
    ]
    result = preflight_plan(nodes, Path("/repo"))
    assert result.ok
    assert result.findings == []


def test_contract_contradiction_surfaces_through_preflight() -> None:
    nodes = [
        PlannedNode(
            id="impl-judge",
            title="Impossible",
            description=(
                "Define class Evaluator and forbid every occurrence "
                "of Evaluator."
            ),
        ),
    ]
    result = preflight_plan(nodes, Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["contract-contradiction"]
    assert result.findings[0].task_id == "impl-judge"


def test_findings_stable_in_node_then_source_order() -> None:
    nodes = [
        PlannedNode(id="first", title="First", after=["ghost-a", "ghost-b"]),
        PlannedNode(
            id="second",
            title="Second",
            description=(
                "Define class Evaluator and forbid every occurrence of Evaluator."
            ),
        ),
    ]
    result = preflight_plan(nodes, Path("/repo"))
    assert [f.category for f in result.findings] == [
        "unknown-dependency",
        "unknown-dependency",
        "contract-contradiction",
    ]
    assert [f.task_id for f in result.findings] == ["first", "first", "second"]
    assert [f.source for f in result.findings[:2]] == ["after", "after"]
    assert [f.source_index for f in result.findings[:2]] == [0, 1]


# ---------------------------------------------------------------------------
# Touch/creates scope globs
# ---------------------------------------------------------------------------


def test_scope_glob_covers_required_path() -> None:
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        touch=["src/evaluator.py"],
        description='```wg-contract\ntouch = ["src/**"]\n```',
    )
    result = preflight_plan([node], Path("/repo"))
    assert result.ok
    assert result.findings == []


def test_exact_scope_path_covers_required_path() -> None:
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        touch=["src/evaluator.py"],
        description='```wg-contract\ntouch = ["src/evaluator.py"]\n```',
    )
    assert preflight_plan([node], Path("/repo")).ok


def test_creates_scope_covers_required_path() -> None:
    node = PlannedNode(
        id="report",
        title="Emit report",
        touch=["dist/report.txt"],
        description='```wg-contract\ncreates = ["dist/**"]\n```',
    )
    assert preflight_plan([node], Path("/repo")).ok


def test_scope_glob_rejects_required_path_outside_declared_scope() -> None:
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        touch=["src/evaluator.py"],
        description='```wg-contract\ntouch = ["tests/**"]\n```',
    )
    result = preflight_plan([node], Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["scope-contract-conflict"]
    finding = result.findings[0]
    assert finding.task_id == "impl"
    assert "src/evaluator.py" in finding.message
    assert finding.source == "touch"
    assert finding.source_index == 0


def test_touch_without_contract_fence_passes() -> None:
    # Plain descriptions are not contracts: no fence means no declared scope
    # to conflict with (canonical fences arrive with structured verification).
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        touch=["src/evaluator.py"],
        description="Implement the evaluator behind the public API.",
    )
    assert preflight_plan([node], Path("/repo")).ok


def test_malformed_contract_fence_is_fatal() -> None:
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        touch=["src/evaluator.py"],
        description='```wg-contract\ntouch = ["src/**"]',
    )
    result = preflight_plan([node], Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["malformed-contract"]

    broken_toml = PlannedNode(
        id="impl2",
        title="Implement evaluator",
        description='```wg-contract\ntouch = [\n```',
    )
    result2 = preflight_plan([broken_toml], Path("/repo"))
    assert not result2.ok
    assert [f.category for f in result2.findings] == ["malformed-contract"]


def test_second_malformed_fence_is_fatal() -> None:
    # Every wg-contract fence is authoritative, matching the acceptance
    # gate: a malformed second fence must not slip past first-fence parsing.
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        touch=["src/evaluator.py"],
        description=(
            '```wg-contract\ntouch = ["src/**"]\n```\n\n'
            'Prose between fences.\n\n'
            '```wg-contract\ncreates = ["dist/"]'
        ),
    )
    result = preflight_plan([node], Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["malformed-contract"]


def test_second_fence_scope_covers_required_path() -> None:
    # Declared scope is the union across every fence, so a required path
    # covered only by a later fence still passes.
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        touch=["src/evaluator.py"],
        description=(
            '```wg-contract\nschema = 1\n```\n\n'
            '```wg-contract\ntouch = ["src/**"]\n```'
        ),
    )
    result = preflight_plan([node], Path("/repo"))
    assert result.ok
    assert result.findings == []


# ---------------------------------------------------------------------------
# Fence verify reconciliation (fail-closed against gate-time dead ends)
# ---------------------------------------------------------------------------


def test_fence_verify_matching_node_verify_passes() -> None:
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        verify="pytest tests/evaluator.py",
        description=(
            '```wg-contract\ntouch = ["src/**"]\n'
            'verify = ["pytest tests/evaluator.py"]\n```'
        ),
    )
    result = preflight_plan([node], Path("/repo"))
    assert result.ok
    assert result.findings == []


def test_fence_verify_disagreeing_with_node_verify_blocks() -> None:
    # A fence verify that differs from node.verify passes preflight today,
    # publishes, then blocks completion forever at gate time (contradictory
    # verify declarations, no degrade). The disagreement must block here.
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        verify="pytest tests/evaluator.py",
        description=(
            '```wg-contract\ntouch = ["src/**"]\n'
            'verify = ["make check"]\n```'
        ),
    )
    result = preflight_plan([node], Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["contract-contradiction"]
    finding = result.findings[0]
    assert finding.task_id == "impl"
    assert "make check" in finding.message
    assert "pytest tests/evaluator.py" in finding.message


def test_fence_verify_without_node_verify_blocks() -> None:
    # node.verify is authoritative: a fence declaring verify commands the
    # structured field does not confirm is the same drift seam, not a
    # second, unvalidated path to gate commands.
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        description=(
            '```wg-contract\ntouch = ["src/**"]\n'
            'verify = ["make check"]\n```'
        ),
    )
    result = preflight_plan([node], Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["contract-contradiction"]
    assert "verify" in result.findings[0].message


def test_scalar_fence_verify_is_malformed_at_preflight() -> None:
    node = PlannedNode(
        id="impl",
        title="Implement evaluator",
        verify="make check",
        description='```wg-contract\nverify = "make check"\n```',
    )
    result = preflight_plan([node], Path("/repo"))
    assert not result.ok
    assert [f.category for f in result.findings] == ["malformed-contract"]


# ---------------------------------------------------------------------------
# Shared path matcher (Task 5 reuses this helper)
# ---------------------------------------------------------------------------


def test_path_matches_exact_paths_and_globs() -> None:
    assert path_matches("src/evaluator.py", "src/evaluator.py")
    assert path_matches("src/evaluator.py", "src/**")
    assert path_matches("src/pkg/deep/mod.py", "src/**")
    assert path_matches("src", "src/")
    assert not path_matches("src/evaluator.py", "tests/**")
    assert not path_matches("src/evaluator.py", "src")
    assert not path_matches("src/evaluator.py", "")
