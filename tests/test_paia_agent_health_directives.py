# ABOUTME: Tests for PAIA agent health directives added to factory brain.
# ABOUTME: Verifies schema entries and DESTRUCTIVE_ACTIONS membership.


def test_new_agent_health_directives_in_schema():
    from driftdriver.factory_brain.directives import DIRECTIVE_SCHEMA
    from driftdriver.factory_brain.chat import DESTRUCTIVE_ACTIONS
    assert "apply_skill_fix" in DIRECTIVE_SCHEMA
    assert "propose_agent_fix" in DIRECTIVE_SCHEMA
    assert "restart_paia_service" in DIRECTIVE_SCHEMA
    assert "restart_paia_service" in DESTRUCTIVE_ACTIONS
    assert DIRECTIVE_SCHEMA["apply_skill_fix"] == ["agent", "skill_file", "diff"]
    assert DIRECTIVE_SCHEMA["propose_agent_fix"] == ["agent", "component", "finding_summary", "proposed_diff"]
    assert DIRECTIVE_SCHEMA["restart_paia_service"] == ["service"]
