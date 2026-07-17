# Drift Lane Roadmap

Date: 2026-07-17
Status: active reference
Companion: [`drift-lane-audit.md`](./drift-lane-audit.md) (boundary compliance of the existing lanes)

## Context

The drift lane fleet today covers *traditional software health* — spec, architecture, deps, data, UX — but has no lane covering *model-mediated-architecture health*: whether the systems we build are correctly shaped for a model to operate them. This roadmap records the two lanes that close that gap, the ideas we deferred (with reasons), and the foundational state the whole fleet depends on.

The categorization below comes from inspecting every installed lane's actual behavior (browser vs. static, LLM-driven vs. deterministic), not from names.

## Build — the model-mediated-compliance dimension (two lanes)

These two form a coherent pair. Together they turn the model-mediated-architecture thesis (see the `model-mediated-development` skill) from a guideline into an enforced gate. No existing lane touches either side.

### `surfacedrift` (working name; originated as "clirift")

- **Checks**: every model-facing surface a task adds — an API route, a CLI command, an MCP tool, an agent-callable function — carries the guidance a model needs to use it correctly: usage instructions, a **common-errors catalog**, and **examples**.
- **Evidence sources**: OpenAPI / JSON-schema, CLI `--help` output, MCP tool-description schemas, function docstrings.
- **Why**: a model consuming an under-documented surface hallucinates usage and mishandles errors. This is the *input/consumption* side of model-mediated architecture, made enforceable at the task boundary.
- **Lane-shape fit**: static, per-task, pass/fail + findings. Strong.
- **Naming note**: "clirift" undersells the scope (all model-facing surfaces, not just CLIs). `surfacedrift` or `contractrift` describes it better.

### `modelrift`

- **Checks**: decisions in a task's code are actually model-mediated, not hardcoded — flags hidden thresholds, regex-based semantic routing, `if/else` judgment, and deterministic fallbacks in places the architecture reserves for the model.
- **Evidence sources**: AST / pattern scan for magic numbers in routing, regex routers, branching decision logic that should defer to the model.
- **Why**: this is the exact anti-pattern the `model-mediated-development` skill warns about (agents adding hidden deterministic judgment). `modelrift` turns that guideline into a gate.
- **Pair story**: `surfacedrift` ensures the model can *use* what you build; `modelrift` ensures your build actually *defers* to the model. Model-mediated architecture enforced from both ends.

### Build status — SHIPPED 2026-07-17

Both lanes shipped as **internal lanes** (`driftdriver.<lane>`), so they are boundary-compliant by construction: findings flow through `_run_internal_lane` → `_create_followups_from_findings` → `guarded_add_drift_task`. They never call `wg` directly — the exact mistake the 10 external lanes made. Posture is **advisory** (`exit_code=0`, `warning` findings) so they surface evidence for review without blocking the gate; both reuse the model-mediated deviation register as the owned escape hatch.

- **`modelrift`** (`driftdriver/modelrift.py`): two v1 detectors — keyword/marker intent gates (`keyword-gate`) and hardcoded float thresholds on semantic dimensions (`semantic-threshold`). 12 tests. CLI entry via `python3 -m driftdriver.modelrift`.
- **`surfacedrift` Layer 1** (`driftdriver/surfacedrift.py`): static structural gate. Discovers classes marked `@model_operable` or subclassing a `ModelOperable*` base, and validates they declare every field in the contract's `REQUIRED_FIELDS` (error, message, expected, valid_examples, retryable, next_step) via AST — no imports, no side effects, no tokens. 22 tests. **Layer 2** (Instructor-mediated LLM judgment of whether guidance is *adequate*) is now implemented — opt-in via `DRIFTDRIVER_SURFACEDRIFT_LAYER2`, reuses `route_for('surfacedrift')`, function-local imports, and degrades gracefully when instructor/openai/a route/an API key is unavailable so it never breaks the structural gate. Dormant by default (env-gated + zero marked surfaces today).
- **Shared contract** (`driftdriver/contracts.py`): `ModelOperableErrorContract` (pydantic, import-safe without pydantic) + the `@model_operable` marker + `REQUIRED_FIELDS` as a single source of truth. Apps import/subclass this; surfacedrift validates against it — surfacedrift is a contract provider, not just a checker.
- **Shared lane support** (`driftdriver/_lanecommon.py`): the walk + deviation-register machinery both lanes share, factored out so suppression semantics are identical across lanes (one register, many lanes).

**Dogfood signal**: running `modelrift` against driftdriver's own repo surfaces **238 possible model-agency violations** (`governancedrift`, `continuation_intent`, `quality_planner`, `policy`, `plandrift`, …). That is the lane working exactly as designed — and honest evidence that driftdriver is not yet compliant with the doctrine it enforces. Resolving those is future work: either migrate the judgment to the model or log each as an intentional deviation in the register.

## Deferred (with reasons)

### `deadrift` — dead-code detection + pruning

- **Name collision**: one letter from `redrift`; near-identical spoken aloud. Reject in a fleet these names are typed in constantly.
- **Duplicates tooling**: the `tech-debt` skill already does dead-code detection and pruning; ecosystem tools (knip, vulture, pyflakes) are mature.
- **Wrong scope**: dead code is whole-repo, not per-task. Lanes are scoped to a task (`driftdriver check --task <id>`).
- **Mutation risk**: pruning is a graph/data mutation, and the fleet's open wound is lanes mutating outside the directive layer (see `drift-lane-audit.md`).
- **Decision**: keep dead-code as the periodic `tech-debt` sweep it already is. Not a lane.

### `datarift` — schema → routes/middleware coverage mapping

- **Name collision**: there is already a `datadrift` lane (schema-vs-code drift).
- **Overlaps `archdrift`**: schema-to-route coverage is architectural integrity, which is `archdrift`'s territory.
- **Decision**: grow a schema-coverage mode in `archdrift`, or ship as a distinct lane under a non-colliding name (`flowdrift`, `schemadrift`). Do not ship a second `datarift`.

## Foundational state

### Model-route registry discoverability — FIXED 2026-07-17

The central model-route registry exists and is populated at `experiments/paia-agent-runtime/config/cognition-presets.toml` (with `docs/model-route-registry.md` as its contract). It was **not discoverable**: `PAIA_MODEL_ROUTE_REGISTRY_PATH` was unset everywhere except per-invocation speedrift runs, so standalone lane invocations crashed ("Unable to find central model route registry"), and planforge's default path was missing the `experiments/` segment.

Fixes shipped:
- `~/.zshrc`: `export PAIA_MODEL_ROUTE_REGISTRY_PATH=...` so shell-launched driftdriver/lane subprocesses inherit it.
- `planforge`: corrected the `COGNITION_PRESETS_PATH` default to the `experiments/` path (commit `2a0990d` on `claude-agent-toolkit`).

Verified: `uxdrift --help` runs clean; `uxdrift.llm_critique` resolves to `openai/gpt-4o-mini`.

### Directive-layer boundary compliance — OPEN DEBT

All 10 external lanes bypass `guarded_add_drift_task` when creating followups (`wg_log` + `ensure_task` direct `wg` calls), ignoring authority budgets, dedup, and the directive audit trail. See `drift-lane-audit.md` for the full audit and phased remediation plan.

**New lanes (`surfacedrift`, `modelrift`) must be boundary-compliant from day one** — route all followup creation through `guarded_add_drift_task`, emit structured JSON findings, and never call `wg` directly. Do not repeat the ten lanes' mistake.

### `uxdrift` is installed-only — GAP

`uxdrift` has no source repo at `experiments/uxdrift` (the 2026-03-08 audit referenced one; it is no longer present). It exists only as the installed uv tool, so its code (including `model_routes.py` path resolution) cannot be durably fixed — edits to site-packages are lost on reinstall. It currently depends entirely on the env var.

**Action**: locate or restore the `uxdrift` source repo so it can be maintained like the other lanes.

## Build sequence

1. **(Done)** Fix registry discoverability — unblocks the LLM-driven lane category.
2. **(Done)** Build `surfacedrift` + `modelrift` — shipped as internal lanes (boundary-compliant by construction), 2026-07-17. surfacedrift Layer 2 (Instructor judgment) shipped 2026-07-17 (opt-in, graceful). The self-violation triage is resolved: the scan-hygiene bug (nested-worktree descent) is fixed, the dogfood is clean (0 findings), and the 52 production findings are logged in `docs/model-mediated/deviation-register.md` as reviewed deterministic exceptions. Open follow-up: F1 — modelrift keyword-gate detector recalibration (over-matches on variable-name shape).
3. Decide `datarift`-mapping: `archdrift` mode vs. new lane (non-colliding name).
4. Tackle directive-layer boundary compliance (phased, per `drift-lane-audit.md`) — separate effort across 10 repos.
5. Restore the `uxdrift` source repo.
