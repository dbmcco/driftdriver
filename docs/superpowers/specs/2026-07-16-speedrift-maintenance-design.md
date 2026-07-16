# Speedrift Maintenance and Compatibility Design

**Date:** 2026-07-16  
**Status:** Approved for implementation planning  
**Primary repository:** `experiments/driftdriver`  
**Related repositories:** `experiments/braydon-workspace-skills`, `experiments/workgraph-pr-staging`

## Purpose

This package removes accumulated compatibility drift between Speedrift, PlanForge, Workgraph, Pi, and external Agency without turning maintenance into a broader product redesign.

The work has two levels:

1. This specification covers bounded cleanup, compatibility repair, documentation, tests, and the tested Workgraph update.
2. After this package ships, a separate design effort will decide what Speedrift should become as a capability.

The second effort must not be smuggled into the first through opportunistic architecture changes.

## Current problems

The current system works through several compensating assumptions rather than one coherent contract:

- PlanForge records semantic routes and bare provider/model values separately, while current Workgraph expects one handler-first execution specification.
- Driftdriver routinely passes `--executor pi`, masking the difference between a handler and a model.
- Driftdriver assumes `.workgraph`, while current Workgraph can initialize `.wg` by default.
- PlanForge can initialize or materialize a graph without using the same graph-location decision as driftdriver.
- PlanForge task descriptions contain validation metadata but do not emit Workgraph's literal `## Validation` contract.
- Artifact-producing tasks do not have an explicit, conditional `## Deliverables` contract.
- Repository guidance names both `speedriftd` and `wg service start` as operator-facing supervision controls.
- The PlanForge Agency adapter defaults to port `8765`, while the installed Agency service is healthy on port `8000`.
- The Agency adapter calls the persistent assignment protocol but does not complete the required evaluate-and-submit loop.
- The driftdriver installer can create `.workgraph/drift-policy.toml` before `wg init`; current Workgraph then refuses to initialize because the target directory already exists.
- The tested Workgraph base is `58380883`, while the reviewed upstream target is `e0b0a047`.

## Design principles

1. Execution identity must be explicit and handler-first.
2. Semantic routing policy and concrete execution must remain separate concepts.
3. Graph location must be decided once and passed explicitly everywhere.
4. Existing repositories must not be migrated merely to make the implementation tidier.
5. `speedriftd` is the sole operator-facing supervision authority.
6. Workgraph remains the source of truth for task state and dependency readiness.
7. Pi remains the dominant execution harness.
8. External Agency must not be described as learning when the caller does not complete its learning protocol.
9. Compatibility failures must stop with plain, actionable errors rather than silently choosing a fallback interpretation.
10. The maintenance package must remain independently reversible from the Workgraph update.

## Architecture and ownership

### PlanForge

PlanForge owns planning, schema validation, model-mediated route selection, task descriptions, and graph materialization commands. It does not start or supervise workers.

### Driftdriver

Driftdriver owns Speedrift runtime integration, graph-location resolution, dispatch preparation, installation order, and the operator-facing `speedriftd` lifecycle.

### Workgraph

Workgraph owns task state, dependencies, readiness, execution selection validation, worker spawning, deliverable preflight, and task completion state.

### Pi

Pi executes model calls selected through handler-first Workgraph execution specifications.

### External Agency

External Agency composes agent prompts. Its persistent assign → execute → evaluate → submit loop is not implemented by the current adapter and is outside this maintenance package.

## Canonical execution contract

PlanForge routing metadata will distinguish two canonical fields:

- `route_id`: the semantic registry route selected by policy.
- `execution_spec`: the concrete handler-first Workgraph execution specification, such as `pi:lunaroute/glm-5.2-nvfp4`.

The schema and normalizer will temporarily accept the legacy fields:

- `selected_route`
- `selected_model`
- `fallback_route`
- `fallback_model`

The normalizer will convert legacy values into the canonical representation. It will fail if canonical and legacy values are both present and disagree.

A Pi-routed task must have an `execution_spec` beginning with `pi:`. Bare provider/model values are not valid canonical execution specifications.

PlanForge will pass `execution_spec` verbatim through `wg --model`. Driftdriver will no longer emit `--executor pi`. Pi remains selected by the `pi:` handler prefix, not by a deprecated parallel flag. The `--model` flag is available in the tested Workgraph base `58380883` and remains present in reviewed upstream `e0b0a047`.

Speedrift will not infer a handler from a provider name. Ambiguous or bare values will fail with an actionable message.

Fallback metadata will remain explicit and same-handler. Speedrift will not implement a second fallback engine; Workgraph remains responsible for validating and applying supported fallback policy.

## Canonical graph-location contract

Driftdriver will provide the canonical graph resolver and expose its decision through a machine-readable command that PlanForge can call.

Resolution rules are:

1. An explicit graph directory wins if it is valid.
2. If only `.workgraph` contains graph state, use `.workgraph`.
3. If only `.wg` contains graph state, use `.wg`.
4. If both contain graph state, raise a hard conflict error naming both directories and the required remediation.
5. If neither contains graph state but one managed directory already exists, reuse that directory.
6. If neither managed directory exists, a new Speedrift-managed repository uses `.workgraph` explicitly.
7. Every Workgraph invocation receives `--dir`; no Speedrift path relies on Workgraph's implicit directory default.

Graph state detection will use concrete Workgraph state files rather than directory existence alone. An empty or partially-created directory must not be mistaken for a valid initialized graph.

PlanForge will resolve the graph directory before initialization or materialization and record the chosen path in its materialization manifest. Driftdriver dispatch, graph reads, policy reads, and ecosystem scans will use the same resolver rather than constructing `repo / ".workgraph"` independently.

There will be no fleet-wide directory rename.

## Installation ordering

Driftdriver installation must initialize Workgraph before writing files into the graph directory when current Workgraph requires a non-existent target directory.

The supported sequence is:

1. Resolve the intended graph directory.
2. If no initialized graph exists, run `wg --dir <path> init` before writing Speedrift policy or handler files into that directory.
3. Install Speedrift policy, handlers, executor handler scripts, and hooks.
4. Validate the installed graph and runtime files.

A failed installation must not leave a partial directory that is later misclassified as an initialized graph. Installation will either clean up only the files it created or return a precise repair command without deleting user-owned state.

## Task-description contract

Every materialized PlanForge node will include a literal section:

```markdown
## Validation
- `<command or observable check>`
```

The section will be derived from the node's existing `verify` list and will always be present.

The workgraph-node schema will gain an optional explicit `deliverables` list. When that list is non-empty, the task description will include:

```markdown
## Deliverables
- `path/or/registry-entry`
```

PlanForge will not infer deliverables from `touch_set`. A file that may be touched is not necessarily an output promised by the task.

Research, review, and decision tasks without explicit artifacts will not receive a Deliverables section.

## Lifecycle and supervision

The operator-visible lifecycle is:

1. Resolve the graph directory.
2. Initialize the graph when necessary.
3. Materialize tasks.
4. Validate that an explicit execution route or profile is active.
5. Arm, observe, or release supervision through `speedriftd`.

PlanForge never starts supervision. Interactive agents do not manually claim or dispatch work when `speedriftd` owns the repository lease.

Speedrift documentation will remove or narrowly scope direct `wg service start` instructions. Workgraph service control may remain an internal implementation detail, but it is not a second operator-facing authority.

Structured Workgraph failures, including execution-selection failures such as `WG-EXEC-UNSELECTED`, will be preserved rather than collapsed into generic dispatch errors.

## External Agency maintenance boundary

The default Agency endpoint will be corrected to `127.0.0.1:8000`.

Correcting the port alone would silently activate an incomplete persistent assignment loop. Therefore external Agency will remain disabled by default in this package.

If an operator explicitly enables experimental composition:

- output will state whether Agency was used;
- output will state that learning/evaluation submission was not completed;
- any returned Agency task identifier will be preserved when available;
- failure will fall back to the built-in prompt with a recorded degradation reason;
- no documentation will claim adaptive learning.

The later Speedrift capability design must choose one of two honest outcomes:

1. Implement the complete persistent Agency learning loop.
2. Remove the partial external Agency adapter and retain only built-in/model-mediated composition.

## Documentation cleanup

The package will update current Speedrift and PlanForge guidance to:

- use canonical `route_id` and `execution_spec` terminology;
- show handler-first Pi examples;
- explain `.workgraph` and `.wg` resolution;
- name `speedriftd` as the supervision authority;
- emit literal Validation and conditional Deliverables sections;
- remove stale `--blocked-by` examples in favor of current dependency syntax;
- remove stale runner paths;
- distinguish PlanForge V2 from legacy debate guidance;
- describe external Agency as optional prompt composition, not an executor or completed learning system.

Generated or WG-managed repository guidance will not be edited blindly. The implementation will change the owning template or add a clearly-scoped Speedrift override so regeneration does not restore contradictory instructions.

## Backward compatibility

The package preserves:

- existing `.workgraph` repositories;
- `.wg`-only repositories created by current Workgraph;
- existing PlanForge plan artifacts through legacy-field normalization;
- Pi as the default execution harness;
- existing non-Pi executor routes through their current legacy dispatch paths; migrating those paths to handler-first execution specifications is outside this package;
- current semantic route policy;
- Workgraph as task-state authority;
- `speedriftd` observe mode as the safe default.

The package intentionally rejects:

- conflicting graph state in both managed directories;
- conflicting legacy and canonical routing values;
- bare provider/model values used as canonical execution specifications;
- implicit model execution without an active Workgraph execution selection;
- silent external Agency learning claims.

## Error handling

New errors will be plain and actionable:

- Graph conflicts name both directories and explain that the operator must choose or archive one.
- Missing execution specifications identify the task and show an accepted handler-first example.
- Legacy/canonical routing conflicts show both values.
- Workgraph initialization failures distinguish an empty partial directory from an initialized graph.
- Agency degradation reports the endpoint, failure category, and built-in fallback use without exposing credentials.
- Workgraph structured failure codes pass through unchanged where available.

## Test strategy

All implementation follows test-driven development.

### PlanForge tests

Focused tests will verify:

- canonical and legacy routing normalization;
- conflict rejection;
- handler-first execution-spec validation;
- materialization through `wg --model` without `--executor pi` coupling;
- literal Validation output;
- artifact-conditional Deliverables output;
- research/review tasks without Deliverables;
- explicit graph-directory use in discovery and materialization manifests.

### Driftdriver tests

Focused tests will verify:

- `.workgraph`-only resolution;
- `.wg`-only resolution;
- hard failure when both contain graph state;
- deterministic behavior for empty or partial directories;
- dispatch without `--executor pi`;
- handler-first model forwarding;
- installer initialization before policy writes;
- partial-install recovery behavior;
- Agency default-off behavior and port `8000` when explicitly enabled;
- `speedriftd` as the sole operator-facing supervision authority;
- structured Workgraph error preservation.

### Workgraph and live integration tests

After PlanForge and driftdriver are green against Workgraph `58380883`:

1. Create a clean Workgraph branch from reviewed upstream commit `e0b0a047`.
2. Reapply patched-Pi installer fix `9cda81cb`.
3. Build Workgraph and run focused smoke tests.
4. Run a direct Pi Lunaroute marker request.
5. Run a real Workgraph task using `pi:lunaroute/glm-5.2-nvfp4` and verify terminal completion.
6. Run real driftdriver dispatch without `--executor pi` and verify terminal completion.
7. Verify `.workgraph`, `.wg`, and dual-state graph resolution behavior.
8. Verify Agency remains disabled by default and reports experimental non-learning semantics when explicitly enabled.
9. Confirm `speedriftd` remains in observe mode unless explicitly armed.

Credential-dependent tests will report a clean skip only when their required credential is genuinely unavailable. Stale smoke fixtures will be updated separately from product code so fixture failures are not confused with runtime failures.

## Isolation, review, and delivery

The existing working trees contain unrelated changes. Work will occur in dedicated worktrees and feature branches for each repository.

The parent agent is the only writer. Pi Lunaroute subagents will provide read-only architecture, test, and final-diff reviews. They will not edit shared files.

Each repository will use explicit path staging. Broad staging commands are prohibited.

The compatibility changes and the Workgraph update will remain separate commits so the Workgraph update can be reverted without removing the maintenance fixes.

Every changed repository will be tested, committed, and pushed. Remaining work, if any, will be recorded with a traceable identifier.

## Acceptance criteria

The package is complete only when:

1. PlanForge emits and materializes handler-first `execution_spec` values distinct from semantic `route_id` values.
2. Legacy routing fields remain readable and conflicting values fail.
3. PlanForge and driftdriver use one deterministic graph-location decision.
4. Existing `.workgraph` and `.wg` repositories work without migration.
5. Dual graph state fails before task mutation or dispatch.
6. Driftdriver installation succeeds against current Workgraph without the partial-directory ordering failure.
7. PlanForge emits literal Validation sections and conditional explicit Deliverables sections.
8. Driftdriver no longer emits `--executor pi` during Pi dispatch.
9. `speedriftd` is the sole documented operator-facing supervision authority.
10. External Agency defaults to off, uses port `8000` when explicitly enabled, and makes no learning claim.
11. Workgraph `e0b0a047` plus installer fix `9cda81cb` builds and passes the agreed tests.
12. Real Pi Lunaroute, Workgraph, and driftdriver smokes complete successfully.
13. All intended changes are committed and pushed without unrelated working-tree files.

## Out of scope

This package does not include:

- the full external Agency learning loop;
- a second Speedrift fallback engine;
- fleet-wide graph-directory migration;
- Telegram or human-task workflows;
- a PlanForge relocation or rewrite;
- a new general-purpose agent runtime;
- the strategic Speedrift capability redesign.

## Follow-on capability design

After this package ships, the next design will start from the desired capability rather than the current implementation. It will answer:

- What user outcome should Speedrift own end to end?
- Which decisions should remain model-mediated and which should be deterministic infrastructure?
- What feedback should improve future plans and routing?
- What is the smallest useful control plane for a portfolio of repositories?
- Which Workgraph and Agency capabilities should Speedrift adopt, wrap, or deliberately ignore?
- What evidence proves that Speedrift improves delivery rather than merely producing more orchestration artifacts?

That work will receive its own specification, implementation plan, tests, and approval boundary.
