# Workgraph Coordinator Subprocess Unification Design

## Why This Needs To Happen Now

The local `workgraph` line still runs the coordinator through a direct Claude stdio path owned by the daemon in [coordinator_agent.rs](/Users/braydon/projects/experiments/workgraph/src/commands/service/coordinator_agent.rs). That path has accumulated special-case logic for spawning, restart handling, context injection, and error surfacing.

At the same time, task agents already run through the executor-routing spine in [execution.rs](/Users/braydon/projects/experiments/workgraph/src/commands/spawn/execution.rs). Upstream has gone further: it moves coordinators onto a handler-subprocess path driven by `spawn-task`, with the daemon acting as supervisor rather than protocol owner.

If we chase session continuity first on the current local path, we will harden the very special case we intend to replace. The better move is to first create a shared execution boundary between the daemon and the coordinator handler, then layer continuity/repair on top of that shared runtime.

## Current Reality

### Local line

- The daemon directly spawns and owns a Claude subprocess from `CoordinatorAgent::spawn(...)`.
- The daemon writes user turns to Claude stdin itself.
- Coordinator chat state lives in the single global `.workgraph/chat/` store implemented in [chat.rs](/Users/braydon/projects/experiments/workgraph/src/chat.rs).
- There is no local `spawn-task`, `claude-handler`, or `chat_sessions` substrate.

### Upstream line

- The daemon supervises a coordinator subprocess rather than speaking executor protocols directly.
- `spawn-task` is the entry point that resolves runtime/executor/handler dispatch.
- `claude-handler` is a standalone bridge between Claude CLI stdio and chat-file session state.
- `chat_sessions` provides UUID/alias-backed session routing and multi-session isolation.

### Constraint

The upstream unification path is real, but it is too large to port honestly in one move. The local line still lacks the session-registry substrate, and pretending otherwise would create fake compatibility.

## Goals

- Remove the daemon’s direct ownership of Claude stdio for the primary coordinator.
- Put coordinator execution behind a subprocess boundary that matches upstream architecture direction.
- Reuse the current effective executor/model/provider logic rather than duplicating new routing rules in the daemon.
- Preserve the existing local single-chat model while the subprocess boundary is introduced.
- Create a clean base for later session continuity, repair, and additional coordinator executors.

## Non-Goals

- Full upstream `chat_sessions` adoption.
- Multi-coordinator UUID/alias session isolation.
- Codex/Gemini/Amplifier coordinator handlers in this tranche.
- Full upstream `spawn-task` parity for all task agents.
- Claiming session continuity beyond what the current global chat files already preserve.

## Approaches Considered

### 1. Coordinator execution unification first

Move the coordinator behind a handler subprocess boundary now, while preserving the current global chat storage.

Pros:
- highest architectural leverage
- aligns with upstream direction
- reduces daemon special-casing before continuity work

Cons:
- requires some new local substrate
- does not immediately deliver full session-repair behavior

### 2. Session continuity first on the current local path

Improve restart/recovery on the direct daemon-owned Claude path before any subprocess unification.

Pros:
- smaller apparent UX delta

Cons:
- deepens the local fork’s most divergent runtime path
- makes later unification harder

### 3. Full upstream runtime import

Port `spawn-task`, handlers, session registry, and coordinator runtime together.

Pros:
- largest convergence in theory

Cons:
- too large for an honest tranche
- too many missing local substrates
- high risk of fake partial parity

## Recommended Design

Adopt a **primary-coordinator subprocess precursor**.

This tranche introduces the smallest honest upstream-aligned boundary:

- the daemon supervises a coordinator subprocess
- a local `spawn-task` precursor is added for coordinator tasks first
- a local `claude-handler` is added for the coordinator path
- the existing global `.workgraph/chat/` store remains the source of truth

This is intentionally not full upstream Phase 7. It is the minimum coherent slice that changes the architecture without fabricating missing session-registry support.

## Architecture

### 1. Daemon role

The daemon should become:

- coordinator subprocess supervisor
- restart manager / circuit breaker
- inbox forwarder for daemon-originated messages
- runtime observer for handler exit/failure

The daemon should stop being:

- direct Claude stdio protocol owner
- direct per-turn request/response pump

### 2. `spawn-task` precursor

Add a local `wg spawn-task` precursor that is explicitly scoped to coordinator tasks in this tranche.

Responsibilities:

- accept `.coordinator-0`
- resolve effective executor/model/provider from current runtime/config state
- dispatch to the correct handler command for supported coordinator executors
- fail clearly for unsupported executor types

This should be treated as a coordinator-first precursor, not as a claim that full task-agent upstream `spawn-task` behavior exists locally.

### 3. `claude-handler`

Add a local `wg claude-handler` for the coordinator path.

Responsibilities:

- spawn the Claude CLI subprocess
- read user turns from the existing global chat inbox
- inject `build_coordinator_context(...)` per interaction
- parse Claude output and append final responses to the existing global outbox
- surface runtime failures via the same error-oriented chat semantics already used locally

This handler should operate on [chat.rs](/Users/braydon/projects/experiments/workgraph/src/chat.rs), not on a UUID/alias session registry.

### 4. Chat storage

Keep the current local chat substrate:

- `.workgraph/chat/inbox.jsonl`
- `.workgraph/chat/outbox.jsonl`
- existing cursor/state files

This tranche explicitly does **not** introduce upstream `chat_sessions.rs`. The handler and daemon both target the existing single-session files.

### 5. Routing semantics

The new path should reuse the runtime intent already being established in the local line:

- effective executor selection
- provider/model inference
- endpoint-aware routing where applicable

The daemon should pass that resolved intent through the subprocess boundary rather than reconstructing executor-specific behavior inline.

## Runtime Flow

1. `wg service` decides the effective coordinator executor/model/provider.
2. The daemon starts a subprocess for `.coordinator-0` via the `spawn-task` precursor.
3. `spawn-task` resolves the coordinator runtime and dispatches to `claude-handler`.
4. `claude-handler` watches the existing inbox, prepends `build_coordinator_context(...)`, and sends the turn to Claude.
5. `claude-handler` writes the final response to the existing outbox.
6. On non-zero handler exit, the daemon treats it as a supervisor failure and applies the existing restart/circuit-breaker policy.

## Failure Model

- Unsupported coordinator executors in this tranche must fail explicitly with a clear error.
- If `claude-handler` exits non-zero, the daemon treats it as restartable coordinator failure.
- If a handler dies during an active request, the system should still surface a `system-error` style response rather than silently dropping the turn.
- Restart rate limiting remains daemon-owned.
- We do not claim stronger continuity semantics than the current global chat files already provide.

## Test Contract

This tranche should not be called complete without the following:

- unit tests for coordinator subprocess-mode selection
- unit tests for executor/provider/model resolution used by the subprocess path
- tests that unsupported executors fail clearly instead of falling back silently
- integration test that `wg service` spawns the primary coordinator through the subprocess path
- integration test that inbox messages still result in outbox responses through the handler path
- restart/failure test that handler crash surfaces a coordinator-visible error path
- `driftdriver` compatibility check dedicated to the coordinator subprocess contract

## Out Of Scope For This Tranche

- upstream UUID/alias session registry
- multiple independent coordinator sessions
- codex/gemini coordinator handlers
- full task-agent `spawn-task` parity
- session doctor / session repair / session check workflows
- compaction-aware session continuity improvements

## Success Criteria

- the daemon no longer owns Claude stdio directly for the primary coordinator
- coordinator execution crosses a real subprocess boundary
- the handler path still uses the current local chat storage successfully
- runtime intent is passed through the subprocess path rather than duplicated in daemon-only logic
- unsupported runtimes fail clearly
- `driftdriver` can track the new subprocess coordinator contract explicitly

## Follow-On Work

Once this tranche lands, the next honest runtime steps become much cleaner:

1. broaden coordinator handler support beyond Claude
2. move more runtime selection into shared handler dispatch
3. introduce session continuity/repair on top of the subprocess model
4. only then consider upstream session-registry adoption
