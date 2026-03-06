# Attractor And trycycle Integration Plan

## Purpose

Evaluate which concepts from Attractor and trycycle should enter Speedrift, without replacing WorkGraph or the repo-local runtime contract.

Sources:
- Attractor: https://github.com/strongdm/attractor
- Attractor coding loop spec: https://github.com/strongdm/attractor/blob/main/coding-agent-loop-spec.md
- trycycle: https://github.com/danshapiro/trycycle

## Decision

Adopt concepts, not platforms.

- Keep `WorkGraph` as task/state authority.
- Keep `speedriftd` as the repo-local runtime supervisor.
- Do not replace either with Attractor or trycycle.

## What Speedrift Should Adopt From Attractor

1. Host-controlled loop boundaries
- Drivers should be provider-aligned and host-owned.
- Runtime orchestration must not depend on foreground chat state.

2. Library-first execution thinking
- The worker-driver contract should stay stable even if the runtime core moves from Python to Rust.
- Driver implementations should be thin adapters over provider-specific capabilities.

3. Environment abstraction
- Runtime contracts should separate:
  - task truth
  - worker transport
  - execution environment
  - policy narration

### Speedrift Impact

- keep `claudedriver` and `codexdriver` contract-first
- avoid fake “universal session” behavior when runtimes are materially different
- preserve a Rust-friendly runtime core boundary

## What Speedrift Should Adopt From trycycle

1. Plan/review before build/review
- planning quality should be improved before execution starts

2. Fresh reviewer perspective
- reviews should not always come from the same active implementation context

3. Bounded iteration
- review loops should be explicit and finite, not open-ended churn

### Speedrift Impact

- `plandrift.review_loop_mode = "trycycle-inspired"`
- `plandrift.fresh_reviewer_required = true`
- `plandrift.review_rounds = 2` by default
- model contracts should ask for:
  - dependency updates
  - intervening tests
  - loopbacks
  - continuation edges
  - a fresh-reviewer pass

## What Speedrift Should Not Adopt

From Attractor:
- not a replacement for WorkGraph
- not a second task graph

From trycycle:
- not a new orchestration spine
- not a mandatory dependency stack
- not a replacement for repo-local drift modules

## Implementation Backlog

1. Repo control/lease model
- interactive sessions become observe-first
- only explicit supervisor modes may auto-start execution services

2. Driver contract hardening
- shape `claudedriver` and `codexdriver` around provider-specific truth

3. `plandrift` review loops
- encode trycycle-style plan/build review rounds into model contracts and follow-up prompts

4. Hub visibility
- show repo control mode and lease owner in ecosystem status

## Acceptance

This integration is successful when:
- interactive sessions stop fighting with background automation
- repos clearly show whether they are manual, observe, supervise, or autonomous
- `plandrift` outputs fresh-reviewer guidance by default
- driver/runtime docs remain aligned with a Rust kernel migration path
