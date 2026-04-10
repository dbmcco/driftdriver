# Dark Factory Operator Hub Design

**Date:** 2026-04-10  
**Status:** Draft — approved direction, pending final spec review  
**Scope:** Ecosystem Hub information architecture, monitoring methodology, and North Star operator model

---

## Why This Spec Exists

The current hub exposes a large amount of real telemetry, but it does not reliably answer the operator questions that matter:

- What needs my attention now?
- What requires human judgment?
- What can the factory handle without me?
- Are we getting closer to the Dark Factory North Star?

This is no longer a minor UX issue. It is an operating-model problem.

On April 10, 2026, multiple failures converged:

- pending decisions were present in the control plane but not visible in the hub surface
- legacy PAIA repo identities leaked into decisioning and notifications
- the dashboard carried a JavaScript syntax error that made navigation inert while the page shell still rendered
- even when the underlying data was present, the operator still had to interpret raw telemetry to figure out what to do

The current hub is organized around system implementation domains (`Operations`, `Factory`, `Intelligence`, `Conformance`, `Convergence`). That reflects how the system is built. It does not reflect how an operator makes decisions.

The redesign in this spec changes the hub from a telemetry-first observability surface into an operator-first control surface, while preserving existing tabs, drill-down, and evidence views.

---

## North Star Alignment

This design is derived directly from the Dark Factory North Star in [NORTH_STAR.md](/Users/braydon/projects/experiments/driftdriver/NORTH_STAR.md):

> Every enrolled repo converges to its declared attractor state with zero manual drift remediation, with human judgment required only at the Gate layer.

The current hub does not provide a simple way to answer whether this is happening.

This redesign makes the hub answer three top-level questions immediately:

1. Is the factory alive and coherent?
2. How much true human judgment is required right now?
3. Is the ecosystem moving toward or away from its attractor states?

If the hub cannot answer those three questions in under ten seconds, it is not serving the North Star.

---

## Problem Statement

### Current failure mode

The hub currently optimizes for introspection:

- repo lists
- drift telemetry
- signal collections
- lane outputs
- graphs
- logs

Those are all useful, but they are supporting evidence, not the main product.

The operator’s actual job is:

- decide what matters now
- make a small number of high-value judgments
- delegate or approve the rest
- verify whether the factory is working as intended

Today, the operator must infer those answers from raw telemetry.

### Why this is a problem now

This issue has become acute because:

- PAIA repo count is large and still growing
- repo topology is more structured than before (`paia-agents`, canonical PAIA repo set, lifecycle posture)
- the control plane now has real decision objects and provenance
- the Gate layer is starting to produce more decisions than a human should manually triage from raw lists

At small scale, a telemetry-first hub is tolerable. At current scale, it creates operational drag and hides the actual state of the factory.

---

## Goals

- Make the default hub view answer what the operator should do now.
- Make the default hub view answer whether the Dark Factory is on-track toward the North Star.
- Preserve current detailed tabs and repo drill-down surfaces.
- Use existing underlying data sources and decision objects; do not create a parallel truth system.
- Make every summary item traceable to its underlying evidence.
- Reduce the operator’s need to translate telemetry into action.

---

## Non-Goals

- Replace repo detail pages, graphs, logs, or existing deep inspection tools.
- Rebuild the entire hub in one pass.
- Hide complexity that operators may still need to inspect.
- Change Workgraph’s role as the execution spine.
- Introduce a second decision queue separate from the canonical queue.

---

## Core Design Decision

The hub becomes **operator-first on the landing view, evidence-first on drill-down**.

This means:

- the default landing screen is organized by operator intent
- existing detailed tabs remain intact
- every summary card links to the exact underlying evidence object

The operator no longer starts with “what data exists?”  
The operator starts with “what needs me?”

---

## New Hub Model

### Top-level structure

The new default landing view becomes:

- `Now`
- `Decide`
- `Watch`
- `Explore`

### Definitions

#### `Now`

Urgent items that require action or escalation soon.

These include:

- control-plane outages or degradations
- severe autonomy stalls
- critical repo convergence regressions
- high-confidence urgent follow-ups

Each `Now` item must include:

- why it matters
- recommended next step
- owner
- urgency
- confidence
- evidence link

#### `Decide`

Human-only Gate items.

These are not all interesting findings. They are the subset that actually require human judgment.

Each `Decide` item must include:

- the decision to make
- the recommended default
- the consequence of each path
- confidence
- underlying source object
- evidence link

#### `Watch`

Real drift or risk that should remain visible but does not require immediate human action.

These are things the factory should likely resolve or continue observing without paging the operator.

Each `Watch` item must include:

- why it is being watched
- whether automation is already in progress
- review deadline or aging pressure
- evidence link

#### `Explore`

The detailed evidence layer.

This is where the current hub surfaces live:

- `Operations`
- `Factory`
- `Intelligence`
- `Conformance`
- `Convergence`
- repo detail pages
- dependency graphs
- service views
- logs
- sessions

`Explore` is not removed. It is demoted from the default mental model.

---

## Existing Tabs and Drill-Down

The existing detailed surfaces remain visible and accessible. They are not collapsed into a single opaque page.

### Preserve

- `Operations`
- `Factory`
- `Intelligence`
- `Conformance`
- `Convergence`
- repo detail views
- graphs
- logs
- service/session surfaces

### New relationship

The operator-first home screen sits above them. It does not replace them.

Every item in `Now`, `Decide`, and `Watch` must have:

- a first-click evidence drawer or side panel
- a secondary `Open Full View` action into the underlying detailed tab/page

That preserves inspectability without forcing the operator to start in raw telemetry.

---

## Monitoring Methodology Change

The current methodology is too close to internal implementation. The new monitoring methodology should reflect operating outcomes.

The hub should summarize the factory in four health domains.

### 1. Control Plane Health

Question answered:

> Is the factory alive and coherent?

Signals:

- hub availability
- factory cycle success/failure
- control-plane exception rate
- repo topology integrity
- notification pipeline health
- queue ingestion and answerability

### 2. Gate Health

Question answered:

> How much true human judgment is required right now, and how noisy is it?

Signals:

- pending decision count
- urgent decision count
- median age of pending decisions
- stale decision count
- false-positive / low-quality decision rate
- decisions by canonical repo family

### 3. Autonomy Health

Question answered:

> Is the factory converting drift into execution without the operator?

Signals:

- percentage of findings turned into tasks automatically
- percentage of drift resolved without human intervention
- finding-to-task latency
- task-to-resolution latency
- blocked autonomy rate

### 4. Convergence Health

Question answered:

> Are repos moving toward their declared attractor states?

Signals:

- repos closer vs farther vs stalled
- attractor distance trend
- lifecycle-aware rollups
- North Star drift score and trend
- manual remediation count

---

## Operator Scorecard

The landing view must include one explicit scorecard.

### Fields

- `Factory Status` — `green`, `yellow`, or `red`
- `Why` — one short sentence
- `Needs You` — number of real human decisions
- `Autonomous This Week` — number of items closed without operator intervention
- `Convergence Trend` — improving / flat / regressing
- `Confidence` — confidence in the current score

### Status semantics

#### Green

- control plane healthy
- Gate queue small and high-signal
- autonomy resolving most eligible drift
- convergence trend improving or stable healthy

#### Yellow

- system functioning
- but Gate load is high, autonomy is stalled, or convergence is flat/regressing

#### Red

- control-plane failures
- severe autonomy failure
- Gate overloaded or polluted
- convergence materially worsening

### Design rule

The operator should be able to answer:

> “Is the Dark Factory working according to the North Star?”

from this scorecard alone.

The rest of the hub exists to explain and justify that answer.

---

## Decision Object Requirements

`Decide` and `Watch` items cannot be detached summaries.

Each item must point to a canonical underlying object with stable provenance.

Minimum fields:

- canonical repo
- canonical service/member if applicable
- source object type
- source object id
- severity / urgency
- recommended action
- confidence
- evidence fingerprint
- underlying view link

This requirement ensures:

- the hub and Telegram refer to the same objects
- operator actions are traceable
- drill-down stays exact
- duplicate summaries do not become parallel truth surfaces

---

## Information Architecture

### Default landing order

1. `Factory Status` scorecard
2. `Now`
3. `Decide`
4. `Watch`
5. `Explore`

### Home-page behavior

- `Now`, `Decide`, and `Watch` are visible by default
- `Explore` exposes the detailed tabs
- tab badges show counts
- operator cards sort by urgency, then confidence, then age

### Card behavior

Each card supports:

- `Open Evidence`
- `Open Full View`
- `Approve` / `Defer` / `Route` when applicable
- lightweight inline rationale

### Evidence drawer

First click on an item opens an in-page drawer or side panel with:

- summary
- rationale
- provenance
- recommended action
- evidence snippets
- link to full underlying view

This is the default inspection action because it preserves operator flow.

---

## North Star Progress Model

The hub should stop implying that high telemetry density equals good oversight.

Instead, progress toward the North Star should be represented explicitly:

- fewer human-only Gate items
- more automatic finding-to-task conversion
- more automatic closure of non-human drift
- higher proportion of repos moving closer to attractor states
- fewer control-plane failures

The dominant top-line trend should be convergence, not raw finding volume.

---

## Rollout Strategy

This redesign should be staged.

### Phase 1 — Operator scorecard + decision visibility

Deliver:

- `Factory Status` scorecard
- landing-page `Factory`/Gate counts
- `Now` + `Decide` scaffolding
- drill-down links to existing evidence

Purpose:

- immediately fix “I don’t know what to do with this information”

### Phase 2 — Gate quality shaping

Deliver:

- severity thresholds
- digest vs page routing
- stale decision expiry
- dedupe and rollups

Purpose:

- make the human queue high-signal

### Phase 3 — Autonomy and convergence instrumentation

Deliver:

- autonomy health metrics
- convergence health metrics
- attractor-aware repo movement tracking

Purpose:

- make “are we nearing the North Star?” measurable

### Phase 4 — Full operator-first home

Deliver:

- polished `Now / Decide / Watch / Explore`
- consistent drawers and full-view transitions
- tighter alignment between Telegram, hub, and decision objects

Purpose:

- complete the move from telemetry-first to operator-first

---

## Acceptance Criteria

This redesign is successful when all of the following are true.

### Operator comprehension

- A human can open the hub and know what requires action in under ten seconds.
- A human can tell whether the Dark Factory is healthy without scanning raw repo tables.

### Gate quality

- `Decide` only contains items that truly require human judgment.
- The operator can answer or defer a decision from the hub without searching for its evidence manually.

### Drill-down preservation

- Every `Now`, `Decide`, and `Watch` item links to the exact underlying evidence object.
- Existing tabs and repo detail views remain accessible.

### North Star visibility

- The hub explicitly states current North Star status and why.
- Convergence and autonomy are visible as first-class health signals.

### Operational confidence

- Telegram and hub show the same decision objects.
- Pending decisions are visible both as queue state and as actionable operator items.

---

## Open Questions

These do not block the design, but they affect implementation order.

1. Should `Watch` include active autonomous work items, or only passive monitored risks?
2. Should `Explore` remain a top-level tab label, or should it simply group the existing tabs visually?
3. Should the operator scorecard use a single global status first, or also show one status per major repo family (`paia-agents`, `paia-os`, etc.)?

My recommendation:

- `Watch` includes both passive risks and active autonomous work that does not need human judgment yet
- `Explore` remains a grouping concept, not necessarily a single new mega-tab
- start with one global status, then add family-level slices only if the summary proves too coarse

---

## Recommendation

Proceed with this redesign now.

This is the correct moment because:

- the control plane now has enough canonical structure to support a stable operator surface
- repo count and system complexity make telemetry-first operation too expensive
- the current hub already proved it can contain useful data while still failing the operator

The next artifact after this spec should be an implementation plan that stages:

1. the monitoring-methodology change
2. the operator-first landing view
3. the Gate-quality improvements
4. the autonomy/convergence instrumentation needed to truthfully answer North Star progress
