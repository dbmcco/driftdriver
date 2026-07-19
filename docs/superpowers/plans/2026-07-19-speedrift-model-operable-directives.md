# Speedrift Model-Operable Directive Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Speedrift directive execution reject malformed or intent-changing payloads with structured repair guidance and preserve complete failure evidence, while seeding explicit follow-up work for Workboard API/CLI lifecycle parity and Samantha’s durable learning loop.

**Architecture:** Keep semantic choices in the model and keep code responsible for schema validation, effect execution, and audit persistence. Add stdlib-only action-specific directive schemas so the control plane does not acquire a new runtime dependency. Validate before invoking `wg`, never strip fields from a mutation, and write the original directive plus structured failure details to JSONL.

**Tech Stack:** Python 3.12, stdlib dataclasses/typing, pytest, Workgraph JSONL, Speedrift drift lanes.

## Global Constraints

- Do not add Pydantic or Instructor to driftdriver; the current package has no such dependency and this boundary only needs deterministic shape validation.
- Do not infer task priority, lifecycle meaning, or user intent in code.
- Do not silently drop unknown or invalid mutation fields.
- Keep existing valid directive payloads backward compatible.
- API/CLI lifecycle parity in `paia-work` and proactive learning in `paia-agents` are follow-up tasks, not hidden scope in this change.

---

### Task 1: Add action-specific directive schemas

**Files:**
- Create: `driftdriver/directive_schemas.py`
- Test: `tests/test_directive_schemas.py`

**Interfaces:**
- `validate_params(action: Action, params: dict[str, Any]) -> None` raises `DirectiveValidationError`.
- `DirectiveValidationError.to_payload() -> dict[str, Any]` returns `error_code`, `message`, `expected`, `received`, `valid_examples`, `retryable`, and `next_step`.
- `ALL_SCHEMAS` contains one schema for each `Action` member.

- [ ] **Step 1: Write failing tests** covering all fourteen actions, missing required fields, wrong primitive/list types, unknown fields, and valid examples.
- [ ] **Step 2: Run `uv run pytest tests/test_directive_schemas.py -q` and confirm the new module/imports or behavior fail.**
- [ ] **Step 3: Implement stdlib dataclass schemas with required fields, optional fields, type checks, and field-level violations.**
- [ ] **Step 4: Run the focused schema tests and confirm they pass.**
- [ ] **Step 5: Run `uv run pytest tests/test_directives.py -q` to verify existing serialization behavior remains intact.**

**Edge cases:** Empty required strings, booleans passed where numbers are expected, non-string list members, and extra fields must all produce repairable validation errors.

---

### Task 2: Enforce validation and intent preservation at execution

**Files:**
- Modify: `driftdriver/executor_shim.py`
- Modify: `driftdriver/directives.py`
- Test: `tests/test_intent_preservation.py`
- Test: `tests/test_directive_integration.py`

**Interfaces:**
- `ExecutorShim.execute()` validates the complete directive before calling `subprocess.run`.
- Invalid directives return `failed`, make no subprocess call, and record the full original directive plus structured repair guidance.
- Existing valid directive execution and result strings remain unchanged.

- [ ] **Step 1: Write failing tests proving invalid `create_task` with an unknown field does not call `wg`, invalid `page`-equivalent actions are not narrowed, and failure records preserve the original params.**
- [ ] **Step 2: Run the focused tests and confirm they fail because validation is not yet enforced.**
- [ ] **Step 3: Add pre-execution validation and pass the directive to `DirectiveLog.mark_failed()` for structured recording.**
- [ ] **Step 4: Extend failed records with source, repo, action, reason, authority, params, error code, expected shape, examples, retryability, and next step without changing completed-record compatibility.**
- [ ] **Step 5: Run `uv run pytest tests/test_intent_preservation.py tests/test_directive_integration.py tests/test_executor_shim.py -q`.**

**Edge cases:** Validation failure must not consume a subprocess timeout, must not mutate the pending directive payload, and must be replayable after a fresh `DirectiveLog` instance is created.

---

### Task 3: Record lifecycle and learning follow-ups

**Files:**
- Create: `.workgraph/follow-ups-lifecycle-parity.md`
- Test: `tests/test_directive_integration.py`

**Interfaces:**
- The follow-up document names concrete downstream owners, acceptance criteria, and suggested touch sets.
- It must include at least:
  - `paia-work` API/CLI parity for project abandon and task cancel/delete, idempotency, stale-reference rejection, and 404/409/422 repair envelopes.
  - `paia-agents`/`paia-agent-runtime` durable structured tool-failure learning and proactive surfacing.

- [ ] **Step 1: Write a failing assertion for the follow-up artifact and required sections.**
- [ ] **Step 2: Run the focused test and confirm the artifact is absent.**
- [ ] **Step 3: Write the follow-up artifact with explicit non-goals and acceptance criteria.**
- [ ] **Step 4: Run the test and confirm both downstream tasks are represented.**

---

### Task 4: Add Speedrift drift coverage and verify the slice

**Files:**
- Modify: `.workgraph/planforge/20260719-123004/plan.final.json` (mechanically corrected PlanForge artifact only if needed)
- Test: `tests/test_directive_schemas.py`
- Test: `tests/test_intent_preservation.py`

- [ ] **Step 1: Run `uv run pytest tests/test_directive_schemas.py tests/test_intent_preservation.py tests/test_directive_integration.py tests/test_executor_shim.py -q`.**
- [ ] **Step 2: Run `./.workgraph/drifts check --write-log --create-followups` and record any advisory findings as explicit follow-ups.**
- [ ] **Step 3: Run the full `uv run pytest -q` suite.**
- [ ] **Step 4: Review the diff, confirm no unrelated files changed, and commit only after all verification passes.**

**Rollback:** Revert the directive schema/validation and audit changes together; existing valid directives continue to use the old executor path if the guard is disabled by reverting the single commit.
