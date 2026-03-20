# Ecosystem Evaluation & Governance Design

**Date:** 2026-03-19
**Status:** Draft — pending spec review
**Scope:** Speedrift north star update + new evaluation governance layer

---

## North Stars

### Speedrift (updated)

> An autonomous dark-factory workflow that keeps repos operating, improving, coordinating, and recovering with low human intervention — across a classified ecosystem of active, maintenance, retired, and experimental repos — producing clean work without accumulating operational debt.

**Key updates from prior version:**
- "Quality" axis renamed to "Product Quality" (explicit scope: code quality, tests, verification)
- New 6th axis: "Operational Health" (factory quality: process cleanliness, task debt, daemon posture, lifecycle compliance)
- Repo lifecycle classification is now a first-class input to scoring — only `active` repos scored on all 6 axes
- Overall effectiveness score filtered to `active` repos only — retired/experimental excluded

### Evaluation Project (new)

> A systematic governance layer that continuously validates the speedrift ecosystem against its declared intent — ensuring every repo operates at its classified posture, every deviation is surfaced with evidence and a remediation path, and the dark factory effectiveness score reflects only meaningful signal.

**Three things the evaluation project owns that speedrift itself does not:**
1. **Classification** — declaring and enforcing lifecycle posture per repo
2. **Conformance** — detecting when reality diverges from declared intent
3. **Remediation** — producing actionable paths back to conformance, not just alarms

The evaluation project succeeds when a manual audit like the one that triggered this design (22 zombie daemons, 16GB RAM, 30 stuck agents in paia-program) is never necessary — the system surfaces and remediates these conditions automatically.

---

## Architecture

Two distinct layers, one hub surface. The evaluation project governs; speedrift operates. They share the hub as the output surface and `ecosystem.toml` as the shared config layer.

```
┌─────────────────────────────────────────────────────┐
│              Ecosystem Hub (8777)                   │
│  effectiveness · conformance panel (new)            │
└──────────┬──────────────────────────────────────────┘
           │ reads
┌──────────▼──────────────────────────────────────────┐
│              EVALUATION PROJECT                     │
│  classifier · conformance checker · remediator      │
│  expressed as: governancedrift lane in driftdriver  │
└──────────┬──────────────────────────────────────────┘
           │ governs / reads
┌──────────▼──────────────────────────────────────────┐
│                   SPEEDRIFT                         │
│  operating model · 6-axis scoring · drift lanes     │
│  (coredrift, specdrift, qadrift, secdrift…)         │
└──────────┬──────────────────────────────────────────┘
           │ orchestrates via
┌──────────▼──────────────────────────────────────────┐
│                  DRIFTDRIVER                        │
│  lane routing · ecosystem hub · northstardrift      │
└──────────┬──────────────────────────────────────────┘
           │ task graph / execution
┌──────────▼──────────────────────────────────────────┐
│                  WORKGRAPH                          │
│  graph.jsonl · coordinator · executor · wg CLI      │
└─────────────────────────────────────────────────────┘
```

**Architectural invariants:**
- `governancedrift` is a lane in driftdriver — same pattern as coredrift, specdrift, qadrift. No new runtime infrastructure.
- The evaluation project uses workgraph for its own task management — findings, remediation tasks, and conformance records all live in the graph.
- It reads the hub snapshot driftdriver already produces — no new data collection.
- Repo lifecycle declarations live in `ecosystem.toml` — extended, not replaced.
- Remediation outputs follow the intelligence inbox pattern — signals with approve/snooze, not hard actions.
- `governancedrift` never writes into another lane's task space or modifies `ecosystem.toml` directly.

---

## Model-Mediated Architecture

Every component follows the pipes-vs-decisions split. The model never invents telemetry — it interprets deterministic telemetry and proposes bounded interventions.

| Layer | Role | Model involved? |
|-------|------|-----------------|
| Collection | Read ecosystem.toml, daemon sockets, process counts, task statuses | No |
| Scoring | Compute conformance delta, Operational Health score, classify findings | No |
| Interpretation | Explain why a finding matters in this repo's context, assess confidence, determine remediation path | Yes |
| Narrative | Generate operator summary, Claude/Codex prompts per finding | Yes |
| Execution | Stop daemon, archive tasks, emit workgraph tasks | No |

Model output is always grounded in a metric. Model outputs include: explanation, confidence score, remediation path (automatic vs. human-judgment), Claude prompt, Codex prompt.

---

## Components

### 1. `ecosystem.toml` — extended

Add per-repo lifecycle declaration. Two new fields per repo entry: `lifecycle` and `daemon_posture`.

**Lifecycle classes:**
- `active` — full speedrift posture, daemon always-on, scored on all 6 axes
- `maintenance` — daemon on-demand only, scored on Product Quality + Operational Health only
- `retired` — no daemon, excluded from scoring, tasks archived
- `experimental` — lightweight tracking, not scored

**Daemon postures:**
- `always-on` — daemon expected running at all times
- `on-demand` — daemon started only when work is being done
- `never` — no daemon should ever run

Example:
```toml
[[repo]]
name = "paia-os"
lifecycle = "active"
daemon_posture = "always-on"

[[repo]]
name = "news-briefing"
lifecycle = "retired"
daemon_posture = "never"

[[repo]]
name = "speedrift-ecosystem-v2-run3"
lifecycle = "experimental"
daemon_posture = "never"

[[repo]]
name = "lodestar"
lifecycle = "maintenance"
daemon_posture = "on-demand"
```

### 2. `northstardrift` — updated

**Changes:**
- Rename "Quality" → "Product Quality" throughout: formula, ledger schema, dashboard contracts, narratives
- Add Operational Health axis with formula and inputs
- Apply lifecycle filter before scoring: only `active` repos in overall effectiveness number
- `maintenance` repos scored on Product Quality + Operational Health only
- `retired`/`experimental` excluded from all scoring
- Add `lifecycle` field to each repo card in the hub snapshot
- Hub shows "real score" (lifecycle-filtered) alongside "raw score" (all repos) — both are permanent columns, not a transition state. Raw score is preserved for historical continuity and to make clear what changed when lifecycle classification was introduced.

**Updated effectiveness formula:**
```
dark_factory_effectiveness =
  0.22 * continuity +
  0.18 * autonomy +
  0.18 * product_quality +
  0.18 * coordination +
  0.12 * self_improvement +
  0.12 * operational_health
```

**Operational Health formula:**
```
operational_health =
  0.30 * process_cleanliness +      # zombie ratio inverse
  0.25 * task_debt_inverse +         # failed/abandoned ratio inverse
  0.25 * daemon_posture_alignment +  # declared vs. observed posture match
  0.20 * abandoned_task_inverse      # abandoned task age pressure inverse
```

**Tier thresholds unchanged:** healthy ≥ 80, watch 60–79, at-risk < 60.

### 3. `governancedrift` lane — new

Implemented in driftdriver alongside existing lanes. Operates on the ecosystem as a whole, not per-repo.

**Pipe (deterministic):**
- Read `ecosystem.toml` lifecycle declarations
- Observe reality: daemon socket live/dead, process count per repo, task status counts (open/failed/abandoned/in-progress), RAM per repo
- Compute conformance delta: declared posture vs. observed posture per repo
- Classify each finding into a category:
  - `lifecycle-violation` — retired/experimental repo with live daemon or running agents
  - `process-debt` — active repo with zombie agents (agents alive, tasks_ready = 0)
  - `architecture-gap` — active repo missing north star, drift policy, or contracts on open tasks
  - `posture-mismatch` — daemon posture doesn't match declaration
- Score Operational Health axis inputs and write to hub snapshot

**Decision (model-mediated):**
- Interpret why a finding matters in this specific repo's context
- Assess confidence: high → automatic remediation; low → inbox signal with human judgment
- Determine remediation path per finding
- Generate operator narrative: what's wrong, why it accumulated, what to do
- Produce Claude prompt and Codex prompt per finding

**Remediation outputs:**
- High-confidence mechanical fixes (confidence ≥ 0.85) → workgraph task in affected repo (e.g., archive abandoned tasks, stop daemon)
- Human-judgment calls (confidence < 0.85) → intelligence inbox signal with approve/snooze/override
- Ambiguous cases → inbox signal with confidence score and model reasoning attached

**Confidence threshold rationale:** 0.85 is the starting value. It applies to the model's self-assessed confidence in its finding classification and remediation recommendation. Threshold is configurable in `governancedrift` config and should be tuned as the lane accumulates a track record.

### 4. Hub conformance panel — new

One new tab in the dashboard. All content deterministic except the narrative paragraph.

**Sections:**
- **Lifecycle map** — all repos bucketed by active/maintenance/retired/experimental, with counts and status indicators
- **Violation table** — repo · declared intent · observed reality · severity · remediation action · age
- **Score comparison** — real (lifecycle-filtered) effectiveness score vs. raw (all repos) score, side by side
- **One-click remediation** — for high-confidence mechanical fixes: stop daemon, archive tasks, reclassify repo (proposes change to ecosystem.toml via inbox)
- **Conformance narrative** — model-written paragraph: current governance state, what changed since last run, operator focus priority

---

## Data Flow

```
ecosystem.toml              observed reality
(declared lifecycle)        (daemon sockets, process count,
                             task statuses, git state)
        │                          │
        └──────────┬───────────────┘
                   │ deterministic collection
                   ▼
         governancedrift (pipe)
         ┌──────────────────────┐
         │ compute conformance  │
         │ delta per repo       │
         │ classify findings    │
         │ score Op. Health     │
         └────────┬─────────────┘
                  │ structured findings + metrics
                  ▼
         governancedrift (model)
         ┌──────────────────────────┐
         │ interpret findings       │
         │ assess confidence        │
         │ determine remediation    │
         │ generate narratives +    │
         │ Claude/Codex prompts     │
         └───────┬──────────────────┘
                 │
        ┌────────┴──────────┐
        │                   │
        ▼                   ▼
  high confidence      human judgment
  mechanical fix       required
        │                   │
        ▼                   ▼
  workgraph task       intelligence inbox
  in affected repo     signal (approve/
  (archive tasks,      snooze/override)
  stop daemon)
        │                   │
        └────────┬───────────┘
                 │ all findings flow to
                 ▼
         hub snapshot
         ┌──────────────────────────┐
         │ northstardrift reads     │
         │ Op. Health inputs        │
         │ updates 6-axis score     │
         │ filters to active repos  │
         └────────┬─────────────────┘
                  │
                  ▼
         ecosystem hub dashboard
         ┌──────────────────────────┐
         │ conformance panel        │
         │ lifecycle map            │
         │ violation table          │
         │ real vs. raw score       │
         │ one-click remediation    │
         └──────────────────────────┘
```

**Cadence:**

| Trigger | What runs |
|---------|-----------|
| Every ecosystem cycle | `governancedrift` pipe — collect facts, compute delta, update Op. Health inputs in snapshot |
| Hourly | `governancedrift` model — interpret findings, emit inbox signals or workgraph tasks |
| Daily | Northstardrift daily ledger entry includes Op. Health axis + lifecycle-filtered score |
| On-demand | Full evaluation run — operator triggers from hub, produces complete conformance report |

---

## Testing

TDD throughout. Tests written before implementation. No mocks — real fixtures and real implementations.

### `governancedrift` pipe — unit tests (>90% coverage)

- Conformance delta computation: fixture `ecosystem.toml` + fixture observed states → assert correct finding categories and severities
- Lifecycle filter: fixture repo set with mixed classifications → assert only `active` repos in overall score
- Operational Health formula: known inputs → assert expected score and tier
- Edge cases:
  - Repo in `ecosystem.toml` but no `.workgraph/` present
  - Daemon socket exists but process dead
  - Task counts all zero
  - Repo declared `retired` but has 50 open tasks

### Model-mediated boundary tests

- **Input construction**: given structured finding → assert prompt contains required fields (repo, category, metrics, confidence inputs), nothing invented
- **Output parsing**: given model response fixture → assert correct extraction of remediation path, confidence score, narrative, prompts
- **Routing logic** (deterministic):
  - High-confidence finding → workgraph task emitted
  - Low-confidence → inbox signal
  - Ambiguous → inbox with override option

### `northstardrift` updates — unit tests

- Product Quality score unchanged by rename — assert formula outputs identical before/after
- Operational Health inputs flow correctly from `governancedrift` output
- Side-by-side score: assert real (lifecycle-filtered) and raw (all repos) both computed and distinct
- Lifecycle filter: `retired` and `experimental` repos excluded from overall score

### Hub conformance panel — integration tests

- Lifecycle map renders all repos in correct buckets given fixture snapshot
- Violation table shows correct repo · declared · observed · severity for each finding type
- One-click remediation calls correct hub API endpoint
- Conformance narrative present and non-empty when findings exist

### End-to-end scenario tests

1. **Retired repo with live daemon** — `ecosystem.toml` declares `retired`, daemon socket live → `lifecycle-violation` finding → high confidence → workgraph task emitted to stop daemon → hub shows violation resolved after next cycle
2. **Active repo with zombie agents** — declared `active`, 30 agents alive, 0 ready tasks → `process-debt` finding → model determines human-judgment required → inbox signal with approve/snooze → operator approves → task emitted
3. **Clean active repo** — declared `active`, daemon posture matches, no task debt, north star present → zero findings → Operational Health score reflects clean state → overall effectiveness score improves

---

## What Does Not Change

- workgraph (graph.jsonl, coordinator, executor, wg CLI)
- coredrift, specdrift, qadrift, secdrift, plandrift
- Intelligence inbox structure
- Hub WebSocket and federation
- Existing 5-axis formulas (only renamed, not recomputed)

`governancedrift` is purely additive. The evaluation project has zero new runtime dependencies.

---

## Implementation Sequence

Per the northstardrift pattern: deterministic collection before model interpretation, always.

1. Classify all 44 repos in `ecosystem.toml` (active/maintenance/retired/experimental)
2. Implement `governancedrift` pipe — collection, delta computation, finding classification
3. Update `northstardrift` — rename Product Quality, add Operational Health axis with inputs from step 2
4. Implement `governancedrift` model layer — interpretation, confidence, remediation routing
5. Add hub conformance panel — lifecycle map, violation table, score comparison
6. Wire cadence — ecosystem cycle, hourly, daily hooks
7. **Future work (not in this plan):** Daemon supervisor enforcement — the hub's existing supervisor reads lifecycle declarations and automatically stops daemons on `retired`/`experimental` repos between cycles. This requires extending the supervisor's config in `ecosystem_hub_daemon.sh` and is scoped separately after steps 1–6 are stable.
