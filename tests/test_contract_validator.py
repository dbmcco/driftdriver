# ABOUTME: Tests for contract_validator — bounded assertion normalization and
# ABOUTME: mechanically provable contradiction detection for planned nodes.

from __future__ import annotations

import json
from pathlib import Path

from driftdriver.contract_validator import validate_node_contract
from driftdriver.planner_core import PlannedNode, parse_plan_output


def test_parse_preserves_routing_properties() -> None:
    nodes = parse_plan_output(json.dumps([{
        "id": "n1",
        "title": "N1",
        "routing_properties": {"blast_radius": "shared_module"},
    }]))
    assert nodes[0].routing_properties == {"blast_radius": "shared_module"}


def test_rejects_required_and_forbidden_symbol() -> None:
    node = PlannedNode(
        id="impl-judge",
        title="Implement evaluator",
        description=(
            "Define class Evaluator. The file must contain no occurrence "
            "of Evaluator, including comments and strings."
        ),
    )
    findings = validate_node_contract(node, Path("/repo"))
    assert any(f.category == "contract-contradiction" for f in findings)
    assert any("Evaluator" in f.message for f in findings)


def test_rejects_import_success_and_failure() -> None:
    node = PlannedNode(
        id="impl-judge",
        title="Implement evaluator",
        description="Import Evaluator successfully. Importing Evaluator must fail.",
    )
    findings = validate_node_contract(node, Path("/repo"))
    assert any(f.category == "contract-contradiction" for f in findings)


def test_valid_evaluator_contract_passes() -> None:
    node = PlannedNode(
        id="impl-judge",
        title="Implement evaluator",
        description="Define class Evaluator and verify that importing Evaluator succeeds.",
    )
    assert validate_node_contract(node, Path("/repo")) == []


def test_rejects_ast_present_and_absent() -> None:
    node = PlannedNode(
        id="impl-judge",
        title="Implement evaluator",
        description=(
            "The AST must contain a node named Evaluator. "
            "The AST must not contain any node named Evaluator."
        ),
    )
    findings = validate_node_contract(node, Path("/repo"))
    assert any(f.category == "contract-contradiction" for f in findings)
    assert any("Evaluator" in f.message for f in findings)


def test_ast_present_assertion_alone_passes() -> None:
    node = PlannedNode(
        id="impl-judge",
        title="Implement evaluator",
        description="The AST must contain a node named Evaluator.",
    )
    assert validate_node_contract(node, Path("/repo")) == []


def test_rejects_command_pass_and_fail() -> None:
    node = PlannedNode(
        id="verify-slice",
        title="Verify slice",
        description=(
            "The command `uv run pytest tests/` must pass. "
            "The command `uv run pytest tests/` must fail."
        ),
    )
    findings = validate_node_contract(node, Path("/repo"))
    assert any(f.category == "contract-contradiction" for f in findings)
    assert any("uv run pytest tests/" in f.message for f in findings)


def test_malformed_command_phrase_flagged() -> None:
    node = PlannedNode(
        id="verify-slice",
        title="Verify slice",
        description="The command must pass before completion.",
    )
    findings = validate_node_contract(node, Path("/repo"))
    assert any(f.category == "malformed-contract" for f in findings)


def test_non_string_verify_flagged_malformed() -> None:
    node = PlannedNode(
        id="verify-slice",
        title="Verify slice",
        verify=["uv run pytest tests/"],  # type: ignore[arg-type]
    )
    findings = validate_node_contract(node, Path("/repo"))
    assert any(f.category == "malformed-contract" and f.source == "verify" for f in findings)


def test_ambiguous_assertion_language_not_rejected() -> None:
    node = PlannedNode(
        id="impl-judge",
        title="Implement evaluator",
        description=(
            "Discuss the import strategy in the design notes. The class Evaluator "
            "may import helpers, but nothing here requires or forbids an import "
            "of Evaluator itself, and the AST discussion is illustrative only."
        ),
    )
    assert validate_node_contract(node, Path("/repo")) == []


def test_contradiction_across_acceptance_and_description() -> None:
    node = PlannedNode(
        id="impl-judge",
        title="Implement evaluator",
        description="Define class Evaluator.",
        acceptance=["The file must contain no occurrence of Evaluator."],
    )
    findings = validate_node_contract(node, Path("/repo"))
    assert any(f.category == "contract-contradiction" for f in findings)
    assert any(
        f.source == "description" and f.related_source == "acceptance" and f.related_index == 0
        for f in findings
    )
