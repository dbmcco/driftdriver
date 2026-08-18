# Speedrift Contract Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent semantically impossible or dependency-invalid Speedrift task graphs from entering Workgraph, while preserving valid contracts and keeping completion verification authoritative.

**Architecture:** Parse planner output into a complete `PlannedNode`, normalize only mechanically recognizable contract assertions, and run an all-or-nothing preflight over the entire node graph before the first `wg add`. Keep publication serial after preflight, preserve structured verification in the generated contract/validation representation, and make completion bypasses explicit rather than silent.

**Tech Stack:** Python 3.11+, dataclasses, `tomllib`, `pytest`/`unittest`, Workgraph `wg` CLI, existing Driftdriver acceptance and drift lanes.

## Global Constraints

- Do not make application workers compensate for impossible planner contracts.
- Deterministic code may reject only mechanically provable contradictions; it must not invent semantic model judgments.
- A fatal preflight finding publishes zero nodes from that planner batch.
- Provider, daemon, authentication, and rate-limit failures remain execution failures, not contract findings.
- Preserve existing route-policy behavior unless a test proves the new preflight boundary requires an explicit change.
- Production code must follow test-first development: each behavior starts with a failing test.
- Do not modify the Workgraph upstream binary in this change; isolate any unavoidable upstream limitation behind an explicit Driftdriver boundary.

---

### Task 1: Preserve planner contracts and add assertion normalization

**Files:**
- Create: `driftdriver/contract_validator.py`
- Modify: `driftdriver/planner_core.py:50-105, 640-660`
- Test: `tests/test_contract_validator.py`
- Test: `tests/test_planner_core.py`

**Interfaces:**
- Consumes: `PlannedNode` fields `id`, `title`, `description`, `verify`, `touch`, `acceptance`, `after`, and `routing_properties`.
- Produces: `ContractFinding`, `NormalizedAssertion`, and `validate_node_contract(node: PlannedNode, repo_path: Path) -> list[ContractFinding]` from `driftdriver.contract_validator`.

- [ ] **Step 1: Write failing parser and validator tests.**

Add tests with the following required cases:

```python
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
```

Also add positive/negative AST assertion coverage, malformed command coverage, and an ambiguous assertion case proving the validator does not reject unsupported language merely because the words overlap.

- [ ] **Step 2: Run the focused tests and verify the expected red failures.**

Run:

```bash
uv run pytest tests/test_contract_validator.py tests/test_planner_core.py -q
```

Expected: the new validator import fails or the new assertions fail, while existing planner tests continue to identify any unrelated baseline failures.

- [ ] **Step 3: Implement the smallest validator and parser preservation.**

Add immutable dataclasses with explicit fields:

```python
@dataclass(frozen=True)
class NormalizedAssertion:
    kind: str
    subject: str
    polarity: str
    source: str
    source_index: int

@dataclass(frozen=True)
class ContractFinding:
    category: str
    task_id: str
    title: str
    message: str
    source: str
    source_index: int = -1
    related_source: str = ""
    related_index: int = -1
    severity: str = "error"
```

Normalize explicit `acceptance` entries, `verify`, and the description text only for bounded patterns: a class declaration followed by an identifier, an import followed by an identifier, an AST assertion followed by an identifier, a contains/no-occurrence assertion followed by an identifier, and command phrases that explicitly say a command must pass or fail. Pair assertions by `(kind, subject)` and emit one finding for opposite polarities. Do not use fuzzy synonyms or model calls.

Update `parse_plan_output` to pass `routing_properties=t.get("routing_properties")` into `PlannedNode` and to normalize non-list values to safe defaults rather than raising.

- [ ] **Step 4: Run the focused tests and confirm green.**

Run:

```bash
uv run pytest tests/test_contract_validator.py tests/test_planner_core.py -q
```

Expected: all new validator and parser tests pass.

- [ ] **Step 5: Commit the parser and validator slice.**

```bash
git add driftdriver/contract_validator.py driftdriver/planner_core.py tests/test_contract_validator.py tests/test_planner_core.py
git commit -m "feat: normalize planner contract assertions"
```

---

### Task 2: Add all-or-nothing graph and scope preflight

**Files:**
- Create: `driftdriver/plan_preflight.py`
- Modify: `driftdriver/planner_core.py:753-903`
- Modify: `driftdriver/existdrift.py:160-210, 269-285` only if the shared glob helper is the existing boundary
- Test: `tests/test_plan_preflight.py`
- Test: `tests/test_planner_core.py`

**Interfaces:**
- Consumes: `list[PlannedNode]`, repository root, optional existing route policy.
- Produces: `PreflightResult(ok: bool, findings: list[ContractFinding])` and `preflight_plan(nodes: list[PlannedNode], repo_path: Path, *, existing_ids: set[str] | None = None) -> PreflightResult`.

- [ ] **Step 1: Write failing graph and publication tests.**

Add tests proving duplicate IDs, missing IDs, unknown dependencies, self-dependencies, and cycles are fatal. Add the key atomicity test:

```python
def test_invalid_later_node_prevents_all_wg_add_calls(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    nodes = [
        PlannedNode(id="valid", title="Valid", after=[]),
        PlannedNode(
            id="impl-judge",
            title="Impossible",
            description="Define class Evaluator and forbid every occurrence of Evaluator.",
        ),
    ]
    count = materialize_plan(nodes, tmp_path, runner=runner)
    assert count == 0
    assert calls == []
```

Add touch-glob cases: `src/**` covers `src/evaluator.py`, while `tests/**` does not cover `src/evaluator.py`. Add a valid graph case proving dependencies remain in the generated `--blocked-by` arguments after preflight.

- [ ] **Step 2: Run the focused tests and verify red.**

```bash
uv run pytest tests/test_plan_preflight.py tests/test_planner_core.py -q
```

Expected: the new graph and atomicity tests fail because `materialize_plan` currently calls `wg add` without a batch preflight.

- [ ] **Step 3: Implement deterministic graph validation.**

Implement `preflight_plan` with these checks in order:

1. Reject blank or duplicate IDs.
2. Build `node_ids` and allow dependencies from `existing_ids` only when explicitly supplied.
3. Reject self-dependencies and dependencies absent from both sets.
4. Run DFS or Kahn topological validation and emit `dependency-cycle` with the cycle members.
5. Call `validate_node_contract` for each node.
6. Check required paths against `touch` and `creates` using one shared `PurePath.match`/`fnmatch` helper that supports exact paths and `**` globs.

Do not run subprocesses during preflight. Keep findings stable in node order, then source order.

- [ ] **Step 4: Wire preflight before the first `wg add`.**

At the top of `materialize_plan`, after route-policy loading and before `added_count = 0`, call `preflight_plan`. Print every fatal finding as:

```text
error: plan preflight blocked publication: [contract-contradiction] impl-judge: Define class Evaluator and forbid every occurrence of Evaluator.
```

Return `0` immediately when `ok` is false. Do not invoke `runner`, `post_commands`, or any publication command for a blocked batch. Preserve current route-policy diagnostics only after contract/graph preflight succeeds.

- [ ] **Step 5: Run focused tests and the existing planner suite.**

```bash
uv run pytest tests/test_plan_preflight.py tests/test_planner_core.py tests/test_existdrift.py -q
```

Expected: all focused tests pass, including proof that invalid later nodes produce zero `wg add` calls.

- [ ] **Step 6: Commit the preflight slice.**

```bash
git add driftdriver/plan_preflight.py driftdriver/planner_core.py driftdriver/existdrift.py tests/test_plan_preflight.py tests/test_planner_core.py tests/test_existdrift.py
git commit -m "feat: preflight Speedrift plans before publication"
```

---

### Task 3: Keep verification structured and fail closed on malformed contracts

**Files:**
- Modify: `driftdriver/planner_core.py:739-850`
- Modify: `driftdriver/acceptance_gate.py:281-383, 422-475`
- Modify: `driftdriver/quality_planner.py:118-146` only if its description builder needs the canonical renderer
- Test: `tests/test_acceptance_gate.py`
- Test: `tests/test_acceptance_gate_integration.py`
- Test: `tests/test_planner_core.py`

**Interfaces:**
- Consumes: `PlannedNode.verify`, `PlannedNode.acceptance`, and existing `wg-contract` fences.
- Produces: canonical contract rendering for materialized descriptions and a distinguishable malformed-contract result from `_extract_verify_commands`/`check_task`.

- [ ] **Step 1: Write failing verification tests.**

Add tests showing that a planned node with `verify="pytest tests"` and acceptance criteria renders an explicit structured validation block without losing the command. Add acceptance-gate tests for a malformed `verify` value and contradictory verify entries. The malformed case must not return `no_criteria`.

- [ ] **Step 2: Run focused acceptance tests and verify red.**

```bash
uv run pytest tests/test_acceptance_gate.py tests/test_acceptance_gate_integration.py tests/test_planner_core.py -q
```

Expected: the new structured-rendering and malformed-contract tests fail against the current prose-only folding/extraction behavior.

- [ ] **Step 3: Implement canonical verification rendering.**

Replace the mechanical prose-only fold with a renderer that preserves the existing description and appends one canonical `wg-contract`/Validation section containing the exact verify commands and acceptance criteria. Keep shell text unchanged. Ensure repeated rendering is idempotent for the same command.

Change extraction to return a typed result distinguishing:

```python
@dataclass(frozen=True)
class VerifyExtraction:
    commands: list[str]
    malformed: bool = False
    error: str = ""
```

`check_task` must report `malformed_contract` and block completion when a contract advertises verification but cannot be parsed. Only an explicitly absent verification section may retain the existing `no_criteria` behavior.

- [ ] **Step 4: Run focused tests and confirm green.**

```bash
uv run pytest tests/test_acceptance_gate.py tests/test_acceptance_gate_integration.py tests/test_planner_core.py -q
```

Expected: all existing acceptance behavior remains green and malformed verification is fail-closed.

- [ ] **Step 5: Commit the verification slice.**

```bash
git add driftdriver/planner_core.py driftdriver/acceptance_gate.py driftdriver/quality_planner.py tests/test_acceptance_gate.py tests/test_acceptance_gate_integration.py tests/test_planner_core.py
git commit -m "fix: preserve structured verification contracts"
```

---

### Task 4: Align publication fences and completion bypasses

**Files:**
- Inspect and modify the smallest applicable completion boundary among `driftdriver/executor_shim.py`, `driftdriver/task_router.py`, and `driftdriver/cli/check.py`.
- Modify: the repository's publication helper only after confirming the installed `wg` command semantics.
- Test: `tests/test_acceptance_gate_integration.py`
- Test: `tests/test_workgraph.py` or a new focused `tests/test_publication_boundary.py`

**Interfaces:**
- Consumes: successful `PreflightResult`, existing acceptance gate result, and Workgraph publication state.
- Produces: explicit publication-fence/coordination state and an ordinary completion path that cannot silently bypass acceptance.

- [ ] **Step 1: Write failing bypass and publication-state tests.**

Use a fake runner to assert that ordinary completion invokes the acceptance check before `wg done`, and that any `--skip-verify` invocation requires a non-empty explicit internal reason. Add a test that publication contention produces a coordination result or dependency state rather than repeated `wg done` retries.

- [ ] **Step 2: Run the focused integration tests and verify red.**

```bash
uv run pytest tests/test_acceptance_gate_integration.py tests/test_workgraph.py -q
```

Expected: the new tests expose the current bypass or lifecycle behavior.

- [ ] **Step 3: Implement the smallest safe boundary change.**

Trace every `wg done --skip-verify` call. Keep it only for a named internal recovery path with a recorded reason and ensure normal completion calls `check_task` first. Do not remove the flag blindly if Workgraph uses it for a separate internal operation.

Confirm the installed CLI's add/place/publish behavior with its help output and existing repository handlers. Encode the result in one helper and one test; do not scatter guessed flags through planner code. If the native binary cannot represent a publication fence, record the wait as a local coordination state and return a retryable structured result instead of spinning.

- [ ] **Step 4: Run focused completion tests and confirm green.**

```bash
uv run pytest tests/test_acceptance_gate_integration.py tests/test_workgraph.py tests/test_acceptance_gate_e2e.py -q
```

Expected: ordinary completion is acceptance-gated and publication contention is explicit.

- [ ] **Step 5: Commit the publication/completion slice.**

```bash
git add driftdriver/executor_shim.py driftdriver/task_router.py driftdriver/cli/check.py tests/test_acceptance_gate_integration.py tests/test_workgraph.py tests/test_acceptance_gate_e2e.py
# Include only files actually changed by the confirmed boundary.
git commit -m "fix: make Speedrift publication and completion state explicit"
```

---

### Task 5: Align scope and dependency reporting

**Files:**
- Modify: `driftdriver/verification.py:1-180`
- Modify: `driftdriver/health.py:45-90, 190-210`
- Test: `tests/test_scope_enforcement.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: canonical `after` dependency fields and `touch` glob declarations.
- Produces: consistent dependency status and scope findings using the same path semantics as preflight.

- [ ] **Step 1: Write failing glob and dependency-field tests.**

Add a scope test where `touch=["src/**"]` covers `src/evaluator.py`, and a negative case where it does not cover `tests/evaluator_test.py`. Add a `tests/test_health.py` fixture with `after=["setup"]` and assert that the status/reporting layer recognizes the dependency.

- [ ] **Step 2: Run focused tests and verify red.**

```bash
uv run pytest tests/test_scope_enforcement.py -q
```

Expected: the glob and canonical dependency cases fail or demonstrate the current mismatch.

- [ ] **Step 3: Implement shared path matching and canonical dependency translation.**

Reuse the preflight path matcher rather than adding a second glob dialect. In `driftdriver/health.py`, read `after` as the canonical Workgraph dependency field and translate only legacy `blocked_by` input when the source is explicitly identified as legacy. Do not silently treat both fields as interchangeable.

- [ ] **Step 4: Run focused tests and confirm green.**

```bash
uv run pytest tests/test_scope_enforcement.py tests/test_plan_preflight.py -q
```

Expected: preflight and post-completion scope checks agree on glob coverage.

- [ ] **Step 5: Commit the boundary-alignment slice.**

```bash
git add driftdriver/verification.py driftdriver/health.py tests/test_scope_enforcement.py tests/test_health.py
git commit -m "fix: align Speedrift scope and dependency semantics"
git commit -m "fix: align Speedrift scope and dependency semantics"
```

---

### Task 6: Add malformed-contract fixtures, documentation, and full validation

**Files:**
- Create: `tests/fixtures/contracts/impl-judge-impossible.json`
- Create: `tests/fixtures/contracts/impl-control-arm-scope.json`
- Modify: `tests/test_full_cycle_integration.py`
- Modify: `docs/superpowers/specs/2026-08-18-speedrift-contract-preflight-design.md` only if implementation behavior requires a precise correction.
- Create: `docs/validation/2026-08-18-speedrift-contract-preflight.json`

**Interfaces:**
- Consumes: completed preflight, structured verification, publication, and scope boundaries.
- Produces: durable validation evidence that can be inspected without rerunning the original relational-memory graph.

- [ ] **Step 1: Write failing fixture-driven integration tests.**

Load both JSON fixtures through the planner parser and assert:

```python
def test_impl_judge_fixture_is_rejected_before_publication(tmp_path: Path) -> None:
    nodes = load_fixture("impl-judge-impossible.json")
    calls: list[list[str]] = []
    count = materialize_plan(nodes, tmp_path, runner=recording_runner(calls))
    assert count == 0
    assert calls == []


def test_valid_judge_fixture_is_publishable(tmp_path: Path) -> None:
    nodes = load_fixture("impl-judge-valid.json")
    count = materialize_plan(nodes, tmp_path, runner=successful_runner)
    assert count == 1
```

Add the scope fixture and assert it produces `scope-contract-conflict` before publication.

- [ ] **Step 2: Run the fixture tests and verify red where behavior is missing.**

```bash
uv run pytest tests/test_full_cycle_integration.py -q
```

Expected: the new fixture assertions identify any remaining seam not covered by the earlier tasks.

- [ ] **Step 3: Add fixtures, integration coverage, and a durable validation receipt.**

The impossible fixture must contain the exact contradictory pairs from the Folio note. The valid fixture must require `class Evaluator` and a successful import without a negative occurrence or failure assertion. The validation receipt must record fixture names, command lines, exit codes, and the number/categories of findings; it must not contain provider credentials or raw worker transcripts.

- [ ] **Step 4: Run the focused quality gate.**

```bash
uv run pytest tests/test_contract_validator.py tests/test_plan_preflight.py tests/test_planner_core.py tests/test_acceptance_gate.py tests/test_acceptance_gate_integration.py tests/test_scope_enforcement.py tests/test_full_cycle_integration.py -q
```

Expected: all changed-path tests pass.

- [ ] **Step 5: Run the complete repository test suite and static checks.**

```bash
uv run pytest tests/ -q
uv run python -m compileall -q driftdriver
```

Expected: exit code 0 for both commands. If the repository has an established lint/typecheck command in `pyproject.toml`, run that exact command and record its result in the validation receipt.

- [ ] **Step 6: Review the final diff and commit the integration evidence.**

```bash
git diff --check
git status --short
git add tests/fixtures/contracts tests/test_full_cycle_integration.py docs/validation/2026-08-18-speedrift-contract-preflight.json
git commit -m "test: add Speedrift contract preflight regression fixtures"
```

The final handoff must list every changed file, every command run, its exit code, any intentionally deferred Workgraph-upstream limitation, and residual provider/publication risks.
