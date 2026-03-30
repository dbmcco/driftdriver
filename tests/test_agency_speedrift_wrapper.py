# ABOUTME: Tests for the Agency-Speedrift protocol wrapper.
# ABOUTME: Verifies that the wrapper injects wg-contract, drift checks, and executor guidance around Agency output.

import pytest

from driftdriver.agency_speedrift_wrapper import wrap_agency_output


# ---------------------------------------------------------------------------
# Sample inputs
# ---------------------------------------------------------------------------

SAMPLE_AGENCY_OUTPUT = (
    "You are a senior Python engineer specializing in distributed systems.\n"
    "Trade-off: favor correctness over speed.\n"
    "Skills: async programming, testing, code review."
)

SAMPLE_WG_CONTRACT = (
    '```wg-contract\n'
    'schema = 1\n'
    'mode = "core"\n'
    'objective = "Implement the frobulator"\n'
    'touch = ["src/frob.py", "tests/test_frob.py"]\n'
    'acceptance = ["Tests pass", "Frob works"]\n'
    'max_files = 4\n'
    '```'
)

SAMPLE_TASK_ID = "implement-frobulator"


# ---------------------------------------------------------------------------
# Agency-only output has NO protocol elements
# ---------------------------------------------------------------------------
class TestAgencyOnlyOutput:
    """Verify that raw Agency output contains zero speedrift protocol context."""

    def test_no_wg_contract_in_agency_output(self):
        assert "wg-contract" not in SAMPLE_AGENCY_OUTPUT

    def test_no_drift_check_in_agency_output(self):
        assert "drifts check" not in SAMPLE_AGENCY_OUTPUT
        assert "rifts check" not in SAMPLE_AGENCY_OUTPUT

    def test_no_executor_guidance_in_agency_output(self):
        assert "wg log" not in SAMPLE_AGENCY_OUTPUT
        assert "wg done" not in SAMPLE_AGENCY_OUTPUT
        assert "wg fail" not in SAMPLE_AGENCY_OUTPUT


# ---------------------------------------------------------------------------
# Wrapped output has ALL protocol elements
# ---------------------------------------------------------------------------
class TestWrappedOutputContainsProtocol:
    """Verify wrap_agency_output injects all required speedrift protocol elements."""

    @pytest.fixture
    def wrapped(self):
        return wrap_agency_output(
            agency_prompt=SAMPLE_AGENCY_OUTPUT,
            wg_contract=SAMPLE_WG_CONTRACT,
            task_id=SAMPLE_TASK_ID,
        )

    # --- wg-contract injection ---
    def test_contains_wg_contract_block(self, wrapped):
        assert "wg-contract" in wrapped
        assert 'objective = "Implement the frobulator"' in wrapped

    def test_wg_contract_appears_before_executor_guidance(self, wrapped):
        contract_pos = wrapped.index("wg-contract")
        guidance_pos = wrapped.index("wg done")
        assert contract_pos < guidance_pos

    # --- drift check obligations ---
    def test_contains_drift_check_at_start(self, wrapped):
        assert "drifts check --task" in wrapped or "rifts check --task" in wrapped

    def test_drift_check_references_task_id(self, wrapped):
        assert SAMPLE_TASK_ID in wrapped

    # --- executor guidance ---
    def test_contains_wg_log(self, wrapped):
        assert "wg log" in wrapped

    def test_contains_wg_done(self, wrapped):
        assert "wg done" in wrapped

    def test_contains_wg_fail(self, wrapped):
        assert "wg fail" in wrapped

    # --- Agency identity preserved ---
    def test_preserves_agency_identity(self, wrapped):
        assert "senior Python engineer" in wrapped
        assert "correctness over speed" in wrapped

    # --- coredrift awareness ---
    def test_contains_coredrift_awareness(self, wrapped):
        assert "coredrift" in wrapped.lower() or "wg-contract" in wrapped

    # --- follow-up task creation ---
    def test_mentions_follow_up_tasks(self, wrapped):
        assert "follow-up" in wrapped.lower() or "create-followups" in wrapped


# ---------------------------------------------------------------------------
# Edge cases: empty / missing Agency output
# ---------------------------------------------------------------------------
class TestFallbackBehavior:
    """Wrapper works with empty or missing Agency output — falls back to generic."""

    def test_empty_agency_prompt_still_has_contract(self):
        result = wrap_agency_output(
            agency_prompt="",
            wg_contract=SAMPLE_WG_CONTRACT,
            task_id=SAMPLE_TASK_ID,
        )
        assert "wg-contract" in result

    def test_empty_agency_prompt_still_has_executor_guidance(self):
        result = wrap_agency_output(
            agency_prompt="",
            wg_contract=SAMPLE_WG_CONTRACT,
            task_id=SAMPLE_TASK_ID,
        )
        assert "wg done" in result
        assert "wg fail" in result

    def test_empty_agency_prompt_still_has_drift_checks(self):
        result = wrap_agency_output(
            agency_prompt="",
            wg_contract=SAMPLE_WG_CONTRACT,
            task_id=SAMPLE_TASK_ID,
        )
        assert "drifts check" in result or "rifts check" in result

    def test_none_agency_prompt_treated_as_empty(self):
        result = wrap_agency_output(
            agency_prompt=None,
            wg_contract=SAMPLE_WG_CONTRACT,
            task_id=SAMPLE_TASK_ID,
        )
        assert "wg-contract" in result
        assert "wg done" in result

    def test_empty_contract_still_has_executor_guidance(self):
        result = wrap_agency_output(
            agency_prompt=SAMPLE_AGENCY_OUTPUT,
            wg_contract="",
            task_id=SAMPLE_TASK_ID,
        )
        assert "wg done" in result
        assert "wg log" in result

    def test_empty_contract_omits_contract_block(self):
        result = wrap_agency_output(
            agency_prompt=SAMPLE_AGENCY_OUTPUT,
            wg_contract="",
            task_id=SAMPLE_TASK_ID,
        )
        # Should not inject an empty contract section
        assert 'objective = "Implement the frobulator"' not in result
