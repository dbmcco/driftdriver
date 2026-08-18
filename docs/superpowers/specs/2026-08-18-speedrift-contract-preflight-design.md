# Speedrift Contract Preflight and Publication Safety

## Status

Approved design for implementation on 2026-08-18.

## Problem

PlanForge and the quality planner can produce a task graph whose individual fields are structurally valid but whose requirements are semantically impossible or whose dependencies cannot be published safely. The current materializer sends `wg add` one task at a time, so a later contradiction can be discovered after earlier tasks have already entered the graph. Verification is also folded into prose rather than kept in the structured contract surface consumed by the completion gate.

The observed failure required a task to both define and forbid `Evaluator`, require an import to both succeed and fail, and require an AST symbol to both exist and not exist. The worker discovered this only at completion. That is too late.

## Goals

1. Preserve all planner fields, including routing properties, through parsing and materialization.
2. Validate the complete planned graph before the first publication call.
3. Detect contradictory contract requirements with precise task, criterion, and verification references.
4. Detect dependency errors, duplicate IDs, cycles, and scope declarations that cannot support required behavior before publication.
5. Keep structured verification available to the acceptance gate instead of relying only on prose.
6. Make publication and completion state explicit, including publication-fence waits and any `--skip-verify` bypass.
7. Add regression coverage for the malformed `impl-judge` contract, the `impl-control-arm` scope case, dependency failures, touch globs, and prevention of partial publication.

## Non-goals

- Replacing Workgraph's native graph or task database.
- Making a model decide semantic satisfiability. The model may author requirements; deterministic code normalizes and rejects mechanically provable contradictions.
- Solving provider authentication, rate limits, or worker routing failures. Those remain separate execution-state failures.
- Rewriting application code in affected repositories to satisfy malformed tasks.
- Adding a broad static analyzer for arbitrary shell commands. The validator will support a bounded, documented assertion vocabulary and report unsupported language rather than guessing.

## Architecture

### 1. Parse and normalize

Extend `PlannedNode` parsing so every emitted planner field survives, including `routing_properties`. Add a small contract-validation module with explicit data types for normalized assertions and validation findings. The module accepts a `PlannedNode` plus its description and returns either a clean normalized contract or findings with stable categories, task ID, field/criterion index, and human-readable evidence.

The first supported assertion categories are:

- `required-present` and `required-absent` for named symbols, classes, files, and dependencies.
- `must-succeed` and `must-fail` for imports and verification commands.
- AST-present and AST-absent checks when the criterion or verification command clearly names the same symbol.
- Declared touch/create paths required by behavior, including path-glob coverage.

The validator must be conservative. It rejects only contradictions it can identify from explicit structured fields or bounded command/criterion patterns. It emits an actionable unsupported-assertion finding for ambiguous combinations rather than inventing a semantic judgment.

### 2. Whole-plan preflight

`materialize_plan` performs a preflight over all nodes before invoking `wg add`:

1. Validate node IDs are present and unique.
2. Validate every dependency names a node in the same plan or an explicitly existing graph dependency when that interface is available.
3. Detect cycles and self-dependencies.
4. Normalize and validate each task contract.
5. Validate required behavior against the declared `touch` and `creates` set, including globs.
6. Validate route properties and model routes.
7. Return a structured failure and publish zero nodes when any fatal finding exists.

The preflight is all-or-nothing for the planner batch. Existing route behavior remains intact: prohibited routes are skipped according to current policy only where that behavior is already explicitly defined; contract and graph contradictions fail closed because publication would otherwise create unsafe partial state. Diagnostics go to stderr and are also available to callers through a result object or exception suitable for CLI reporting.

### 3. Structured verification and publication

Keep verification commands in the task's structured contract representation through materialization. When the installed Workgraph interface requires the description form, generate a canonical `wg-contract`/Validation representation from the normalized contract rather than losing the relationship between acceptance and verification. The acceptance gate's extractor must reject malformed or contradictory verification data instead of silently returning `no_criteria`.

Publication remains serial after successful preflight. The implementation must use the repository's installed Workgraph publication semantics explicitly, including whether tasks are added in draft/no-place state and when a publish operation is required. Shared-root contention must be represented as a coordination/dependency state and must not cause uncontrolled retry loops.

### 4. Completion integrity

Audit the completion path that invokes `wg done --skip-verify`. If it is an intentional internal escape hatch, require an explicit, recorded reason and ensure ordinary task completion cannot use it to bypass the acceptance gate. Preserve provider failure as a separate retryable execution result; do not turn it into a contract-validation finding.

### 5. Scope and dependency alignment

Make touch-path validation understand the same glob semantics used by contracts. Align dependency terminology in health/reporting code with Workgraph's canonical `after` representation, or add an explicit translation at the boundary. Do not silently support two meanings for the same field.

## Error model

Fatal pre-publication findings include:

- `duplicate-task-id`
- `missing-task-id`
- `unknown-dependency`
- `dependency-cycle`
- `contract-contradiction`
- `scope-contract-conflict`
- `malformed-contract`

Each finding includes:

- task ID and title;
- category and severity;
- source field or verification entry;
- conflicting requirement pair where applicable;
- a precise repair instruction;
- whether publication was prevented.

Provider, daemon, authentication, and rate-limit failures remain execution findings and must retain their existing retryability semantics.

## Test strategy

### Unit tests

- `parse_plan_output` preserves routing properties and all contract fields.
- Assertion normalization identifies positive/negative symbol pairs, import success/failure pairs, and AST present/absent pairs.
- Valid `Evaluator` class plus successful import passes.
- The malformed `impl-judge` fixture reports every contradiction with source references.
- Duplicate IDs, unknown dependencies, self-dependencies, and cycles fail preflight.
- Touch globs cover required paths; uncovered required behavior fails with a scope finding.
- Ambiguous unsupported assertions do not produce false contradictions.

### Materialization tests

- A valid multi-node plan invokes `wg add` only after preflight succeeds.
- An invalid later node invokes `wg add` zero times, proving partial publication is prevented.
- Diagnostics include task and criterion details.
- Route validation remains compatible with the preflight result.
- Publication-fence state is explicit and does not spin through repeated completion attempts.

### Integration and CLI tests

- Planner output flows through parse, preflight, and materialization with a fake runner.
- Acceptance extraction rejects malformed verification rather than returning `no_criteria`.
- Ordinary completion cannot bypass acceptance with `--skip-verify` without an explicit reason.
- Existing coredrift and acceptance-gate suites remain green.
- A durable validation artifact records the malformed-contract rejection and the clean valid-contract pass.

## Implementation order

1. Preserve parser fields and add contract-validation data types/tests.
2. Add graph and contract preflight before the first `wg add`.
3. Wire structured verification into materialization and acceptance extraction.
4. Align publication-fence and completion bypass behavior.
5. Fix touch-glob and dependency-field boundary checks.
6. Add fixtures, integration tests, documentation, and run the focused then full test suites.

## Correctness boundary

The repair is correct when an impossible contract cannot enter Workgraph, a valid contract reaches publication unchanged, no invalid batch partially publishes, dependencies are explicit and acyclic, completion cannot silently bypass acceptance, and provider failures remain distinguishable from planning defects.
