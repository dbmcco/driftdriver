# Secdrift + Qadrift (Model-Mediated Program Ops)

## Objective

Add two first-class ecosystem modules that run continuously under Speedrift dark-factory control:

- `secdrift`: security scanning + optional safe pentest baseline
- `qadrift`: recurring quality/UX/program health assessment

Both modules must:

- operate at ecosystem scope (all discovered/registered repos),
- report into the central register and hub websocket snapshots,
- create actionable local Workgraph review tasks,
- keep decision ownership model-mediated (code gathers evidence; model decides remediation paths).

## Model-Mediated Contract

### Decision ownership

- Code owns:
  - deterministic scanning, normalization, deduplication, and persistence
  - safe bounded task emission (idempotent by stable task IDs)
  - dashboards, metrics, and transport
- Model owns:
  - remediation prioritization
  - smallest-safe-fix strategy
  - dependency/task choreography
  - escalation decision framing

### Runtime contract payloads

Each module emits:

- normalized findings (fingerprint, category, severity, confidence, evidence, recommendation),
- `model_prompt` per finding,
- `model_contract` block describing required outputs:
  - root cause
  - remediation plan
  - verification plan
  - exact Workgraph updates

## Module Design

## `secdrift`

Deterministic scanners:

- secret pattern detection in candidate text files
- sensitive artifact detection (`.env`, key material filenames/extensions)
- dependency posture checks (manifest without lockfile)
- optional non-invasive pentest baseline (HTTP header checks) when policy allows network scans

Output:

- risk summary per repo (`critical/high/medium/low`, `risk_score`, `at_risk`)
- top findings with model prompts
- review-task candidates

Task emission:

- writes `secdrift-<fingerprint>` review tasks into repo-local Workgraph
- idempotent (`wg show` before `wg add`)
- policy-bounded caps per repo

## `qadrift`

Deterministic scanners:

- existing qadrift lane findings (coverage gaps, false confidence, integration quality)
- workgraph health signals (stalled execution, dependency gaps, service down with active work)
- test-surface health
- optional Playwright/e2e coverage expectations for web repos

Output:

- quality summary per repo (`quality_score`, severity counts, `at_risk`)
- top findings with model prompts
- review-task candidates

Task emission:

- writes `qadrift-<fingerprint>` review tasks into repo-local Workgraph
- idempotent and policy-capped

## Policy Surface

Added `[secdrift]`:

- `enabled`
- `interval_seconds`
- `max_findings_per_repo`
- `scan_max_files`
- `scan_max_file_bytes`
- `run_pentest`
- `allow_network_scans`
- `target_urls`
- `emit_review_tasks`
- `max_review_tasks_per_repo`
- `hard_stop_on_critical`

Added `[qadrift]`:

- `enabled`
- `interval_seconds`
- `max_findings_per_repo`
- `emit_review_tasks`
- `max_review_tasks_per_repo`
- `include_playwright`
- `include_test_health`
- `include_workgraph_health`

## Runtime Integration

## Snapshot layer (`ecosystem_hub`)

Per repo snapshot now includes:

- `security`
- `security_findings`
- `quality`
- `quality_findings`

Top-level snapshot now includes:

- `secdrift` overview
- `qadrift` overview

Overview + narrative + attention scoring include secdrift/qadrift pressure.

## Factory layer (`factorydrift`)

Planner now emits:

- `run_security_scan` (`secdrift`)
- `run_quality_audit` (`qadrift`)

Priority increases with:

- security critical/high counts
- quality risk and low quality score

Executor now supports deterministic handlers for both actions and can emit review tasks into each repo.

## Dashboard / Action Center

Added actionable panels:

- Security Reviews
- Quality Reviews

Each item includes prompt-ready remediation text for Claude/Codex and graph focus actions.

## Safety

- Default behavior is non-destructive and review-task oriented.
- Task emission is idempotent and capped.
- Optional pentest runs only when explicitly enabled and network scanning is allowed.
- Hard-stop on security critical findings is policy-controlled (`hard_stop_on_critical`).

## Testing Strategy

- Policy parse/default/sanitization tests for new sections
- Secdrift scanner + task emission tests
- Qadrift program scanner + task emission tests
- Factory planner/executor tests for new action kinds
- Ecosystem hub snapshot/dashboard tests for secdrift/qadrift payload visibility

