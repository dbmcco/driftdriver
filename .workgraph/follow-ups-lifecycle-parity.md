# Downstream Follow-ups from Wk Mgmt Contract Review

These are explicit downstream tasks. They are not hidden scope in the Speedrift directive guard.

## Follow-up 1: Restore Workboard API/CLI lifecycle parity

**Owner repository:** `/Users/braydon/projects/experiments/paia-work`

**Problem:** Projects are initiatives with an `/abandon` operation, while tasks use a soft-cancel `DELETE`. The CLI exposes neither project abandon nor task delete/cancel, so agents cannot use the documented lifecycle through one interface.

**Suggested touch set:**
- `src/paia_work/api/initiative_routes.py`
- `src/paia_work/initiative.py`
- `src/paia_work/api/routes.py`
- `src/paia_work/wg_bridge.py`
- `src/paia_work/workboard_cli.py`
- lifecycle and integration tests

**Acceptance criteria:**
- The canonical semantics are documented: project `abandon`, task `cancel`/soft delete, and no implicit project deletion.
- The API and CLI expose equivalent project abandon, task cancel/delete, unlink, and inspection operations.
- Missing resources return 404, invalid terminal-state transitions return 409, and malformed fields return 422 with structured repair guidance containing `error_code`, `message`, `expected`, `valid_examples`, `retryable`, and `next_step`.
- Project links reject nonexistent or deleted task IDs instead of persisting phantom references.
- Terminal projects cannot be resumed or mutated, and repeated abandon/cancel operations are idempotent.
- Project creation supports an idempotency key or stable caller-supplied identifier so a timeout can be retried safely.

## Follow-up 2: Persist Workboard failures as Samantha learning evidence

**Owner repositories:** `/Users/braydon/projects/experiments/paia-agent-runtime` and `/Users/braydon/projects/experiments/paia-agents`

**Problem:** The runtime can produce structured tool repair outcomes, but Samantha's learning path receives prose rather than the structured outcome. Collaboration activity and Workgraph sync also omit important failed/blocked/canceled evidence.

**Suggested touch set:**
- `paia-agent-runtime/src/paia_agent_runtime/responder.py`
- `paia-agent-runtime/src/paia_agent_runtime/tools/__init__.py`
- `paia-agent-runtime/src/paia_agent_runtime/tools/workboard.py`
- `paia-agents/samantha/src/samantha/agent.py`
- `paia-agents/samantha/src/samantha/scanner.py`
- Samantha learning and integration tests

**Acceptance criteria:**
- A normalized tool-failure event preserves tool, action, original parameters, rejected fields, expected schema, valid examples, corrected call, result, source turn, topic, session, and timestamp.
- Mutation repair never executes after dropping a meaningful field; read-only narrowing remains explicitly limited to read-only actions.
- The live tool manifest remains authoritative; learned contract evidence carries provenance, freshness, and supersession metadata.
- Samantha's post-turn learning receives structured tool outcomes and can store a scoped reusable contract observation without deterministic semantic ranking.
- Failed, blocked, canceled, and retry states survive a simulated process/session boundary and can be surfaced proactively without requiring a new user message.
