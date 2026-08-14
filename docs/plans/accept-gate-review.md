# Bounded Adversarial Review — Acceptance-Criteria Gate

- **Task**: `accept-gate-adversarial-review`
- **Reviewer**: agent-205 (pi, review role)
- **Date**: 2026-08-14
- **Scope**: gate design and wiring as landed on `main` at `8c2bf20`..`7b2fac8` — `driftdriver/acceptance_gate.py`, `driftdriver/executor_shim.py`, `driftdriver/cli/check.py`, `driftdriver/cli/acceptance_cmd.py`, tests, `.workgraph/drift-policy.toml`, against spec `docs/superpowers/specs/2026-08-06-add-a-deterministic-acceptance-criteria-gate-to-the-workgraph-ta-spec.md`.
- **Method**: read-first per contract, live tracing of all call sites, execution of the full gate test suite (43/43 pass), inspection of the installed runtime and the `wg` binary's own gates, and construction of bypass attempts against the degrade hatch.

## Verdict summary

| # | Dimension | Verdict | Worst severity |
|---|-----------|---------|----------------|
| D1 | Determinism gaps | **concern** | warning |
| D2 | Ceiling bypass vectors | **fail** | critical (x2, escalated) |
| D3 | Wiring breakage / backward compat | **pass** (backward compat explicitly confirmed) with concerns | warning |
| D4 | Pattern divergence from `signal_gate.py` | **fail** | warning |
| D5 | Overbuilding | **pass** | info |

No bypass vector found is left unflagged; every critical finding is escalated (see §9).

---

## D1. Determinism gaps — verdict: concern

**D1-1 — Malformed verify entries silently degrade to passthrough (spec violation). Severity: warning.**
`_extract_verify_commands` (`driftdriver/acceptance_gate.py:305-328`) parses the verify list with `re.findall(r'"([^"]*)"', raw)` — double quotes only, first `verify = [...]` match anywhere in the description. Valid TOML using single-quoted strings (`verify = ['pytest tests/']`) extracts to `[]`, and `evaluate_acceptance` then returns `no_criteria` (non-blocking) at `acceptance_gate.py:216-221`. The spec risk register explicitly required the opposite: "malformed or uncheckable criteria produce a clear error, not a silent pass". A contract author who deviates slightly from the expected quoting style silently disables the gate.
*Remediation*: parse the verify list with a real TOML parser scoped to the `wg-contract` fence; return a distinct `unparseable` status (blocking or at minimum loudly logged) when a `verify` key exists but cannot be parsed.

**D1-2 — First-match-anywhere extraction is spoofable/fragile. Severity: warning.**
The regex scans the entire task description, not just the `wg-contract` fence (`acceptance_gate.py:310-311`). Any earlier occurrence of `verify = [...]` in prose wins. Task descriptions are agent-editable at any time via `wg edit -d` (verified: `wg edit --help` accepts `--description`), so the completing agent controls the gate's input. See also D2-4.
*Remediation*: extract only from inside a ```` ```wg-contract ```` fence; ideally snapshot the verify list at claim time so mid-flight description edits cannot change what completion must satisfy.

**D1-3 — Two verify engines can disagree for the same task. Severity: warning.**
The `wg` binary itself runs a verify-command gate at `wg done` (see `wg done --help`: `--skip-verify`, "blocked when WG_AGENT_ID is set") and even auto-corrects malformed verify commands (binary strings: "Auto-corrected malformed verify command", "verify commands must be executable. Common examples: 'cargo test', 'npm test', 'true'"). The Rust parser and the Python regex in `acceptance_gate.py` will not always agree (e.g., the single-quote case above: wg enforces, driftdriver passes). The system verdict for one task is therefore engine-dependent, even though each engine is internally deterministic. The relationship between the two gates is documented nowhere in `acceptance_gate.py`'s docstring, which reads as if it is *the* gate before `wg done`.
*Remediation*: document the layering (wg gate = strict binary, driftdriver gate = ceiling/degrade budget) in the module docstring, and align the extraction logic with wg's parsing (or reuse one parser).

**D1-4 — Evaluation is environment- and timing-dependent. Severity: info.**
The decision logic itself is pure (no randomness, no clock reads, no network — verified by reading `evaluate_acceptance` and `_run_verify`, `acceptance_gate.py:189-301`). But `_run_verify` executes commands with `shell=True` in the repo working tree with a 120 s default timeout (`acceptance_gate.py:274-301`): PATH differences, ambient repo state, and slow machines (timeout → failure, `tests/test_acceptance_gate.py::test_timeout_treated_as_failure`) can flip results across runs/machines. This is inherent to command-based verification and acceptable, but it means "deterministic" applies to the *policy*, not the *verdict*. Non-idempotent verify commands are executed up to three times per completion attempt under current wiring (see D3-2), which amplifies side effects.
*Remediation*: none required for the design; note the caveat in the docstring; ensure verify lists contain only idempotent commands (planner guidance).

**D1-5 — Criteria are read at completion time, not creation time. Severity: info (by design, but see D2-4).**
`check_task` reads the current `graph.jsonl` description when invoked (`acceptance_gate.py:345-360`). Mid-flight edits to acceptance criteria are enforced as-of completion. Answering the mandated edge case: the evaluator uses **completion-time** criteria. Combined with `wg edit -d` this is an input-control bypass (D2-4), not a determinism bug per se.

---

## D2. Ceiling bypass vectors — verdict: fail

The ceiling's threat model (spec): "a broken repo cannot rack up waived gates across many tasks and lose signal in volume." Four bypass vectors were constructed; two are rated critical and are escalated (§9).

**D2-1 / CRIT-1 — Corrupt or missing state file silently resets the budget (fail-open). Severity: critical. Escalated.**
`load_degrade_state` (`acceptance_gate.py:106-114`) returns `{}` on `JSONDecodeError`/`OSError`. A task at the ceiling can restore its full degrade budget by truncating or corrupting `.workgraph/service/acceptance-degrade.json` — a file the completing agent can write (it lives in the repo's workgraph dir). Because `save_degrade_state` is non-atomic (D2-2), even an *accidental* torn write converts to a silent budget reset. `tests/test_acceptance_gate.py::test_load_corrupt_returns_empty` enshrines the fail-open behavior as intended. The mandated edge case asks whether the gate fails closed or resets to zero: **it silently resets to zero.**
*Remediation*: on corrupt state, quarantine the file (rename to `*.corrupt-<ts>`), treat the task as at-ceiling (fail closed for the affected tasks or the whole repo), and emit an operator-visible alert. Accept the small deadlock risk in exchange for budget integrity, mirroring how the spec prioritizes signal retention.

**D2-2 / CRIT-2 — Read-increment-write cycle is non-atomic and unlocked: race allows ceiling exceedance. Severity: critical. Escalated.**
`evaluate_acceptance` does `load_degrade_state` → increment → `save_degrade_state` (`acceptance_gate.py:238-245`) with no lock, and `save_degrade_state` uses plain `write_text` with no temp-file rename (`acceptance_gate.py:117-121`). Two concurrent completions in one repo (coordinator directive + agent-side `task-completing.sh` drifts check is a realistic pairing — see D3-2) both read `{t: 2}` with ceiling 3, both write `{t: 3}`, and both degrade: the counter shows 3/3 while **four** degrades were granted. Interleaved partial writes additionally produce the corruption case in CRIT-1. This is precisely the race named in this task's escalation conditions. The in-repo precedent does it correctly: `signal_gate.py:87-95` (`_save_hash`, tmp + `replace`) and `cli/check.py:806-824` (`_write_gate_failures`, tmp + `replace`).
*Remediation*: tmp+replace atomic write, plus a sidecar lock file (or `O_EXCL` claim) serializing the read-modify-write; add the spec's `TestConcurrentDegrade` (two concurrent degrades must yield exactly two increments).

**D2-3 — Unaudited reset command. Severity: warning.**
`driftdriver acceptance reset [--task]` (`driftdriver/cli/acceptance_cmd.py:70-77` → `reset_degrade`, `acceptance_gate.py:124-133`) clears the counter with no audit trail: no `wg log` entry, no timestamp, no record of who reset what. Anyone with shell access to the repo can reset at will and leave no trace. The gate state file records only counts — never when a degrade happened, for which task, or why.
*Remediation*: append every reset (and every degrade) to an append-only audit log (e.g., `.workgraph/service/acceptance-audit.jsonl`) with actor and timestamp, and `wg log` the reset to the affected task.

**D2-4 — The gated agent controls the gate's input. Severity: warning.**
The completing agent can rewrite the verify list at any time before completion via `wg edit -d <task> --description ...` (e.g., `verify = ["true"]`), and both gates read the description at completion time (D1-5). There is no immutability at claim time and no diff alert on contract edits in the completion path (coredrift contract checks exist but do not freeze verify lists).
*Remediation*: snapshot the verify list into the task record at claim time and evaluate the snapshot; or require coordinator/human confirmation when a task's contract changed after claim.

**Non-bypasses verified (for completeness):** the CLI `acceptance check` path is genuinely read-only (`record_degrade=False`, `acceptance_cmd.py:56`); `record_degrade=False` never touches state (tested: `test_read_only_check_does_not_consume_degrade`); the counter never resets on pass, so budget only shrinks within a task's lifetime; `[acceptance] degrade_ceiling` values < 1 or non-integer fall back to the default rather than becoming an infinite ceiling (`acceptance_gate.py:146-171`, tested).

---

## D3. Wiring breakage of existing completions — verdict: pass on backward compatibility, with concerns

**Backward compatibility: explicitly CONFIRMED.** Tasks without acceptance criteria / verify commands pass through the gate without blocking and without noise: `evaluate_acceptance` returns `no_criteria` for empty verify lists (`acceptance_gate.py:216-221`), `check_task` returns `no_criteria` for a missing `graph.jsonl` (`acceptance_gate.py:337-342`), the module writes no state and emits no logs on that path (there is no logging in the module at all), and `GateResult.is_blocking` is False for `no_criteria`. Covered by `tests/test_acceptance_gate_integration.py::test_task_without_criteria_passes_through` and `test_check_task_without_verify_commands`. The 22 blocked / 8 done legacy tasks named in the spec's risk register are safe on that axis.

Concerns found while tracing the wiring:

**D3-1 — The gate is not live in the installed runtime. Severity: warning.**
The `drifts` script and `task-completing.sh` handler invoke the **uv-installed** driftdriver (`~/.local/bin/driftdriver` → `~/.local/share/uv/tools/driftdriver/...`), and that installation contains **no acceptance gate**: no `acceptance_gate.py` module, and no acceptance wiring in its `cli/check.py` / `executor_shim.py` (verified by direct inspection of the installed package). The code exists only on `main` in the repo. Until the tool is reinstalled, every path that is supposed to enforce the gate (drifts check, task-completing handler, coordinator executor) runs without it — including this task's own pre-check. This is not a regression (it fails open, exactly like pre-gate behavior), but it means **zero enforcement is currently in effect**, and the wiring below is unexercised in production.
*Remediation*: reinstall the uv tool from `main` (or pin the drifts script to `uv run --project <repo>`), and add a version assert / doctor check that the installed driftdriver contains the acceptance gate.

**D3-2 — The drifts-check wiring burns degrade budget on every in-progress check, while having no blocking power. Severity: warning.**
`cli/check.py:1015-1031` runs the gate on every `drifts check --task X` and derives `_is_completion_attempt` purely from the task's status being `in-progress`. The mandated task protocol runs `drifts check` at pre-task (task already in-progress — this session is a live example) and again at post-task. Each of those runs executes the verify commands with `record_degrade=True`, so a task with failing verify commands can consume 2 of its 3 default budget slots **before any real completion attempt**, purely by following the protocol. Meanwhile the gate's result on this path is advisory-only: its `exit_code` lands in `plugins_json["acceptance_gate"]` but feeds neither `final_rc` (enforcement reads `report.findings` only — `policy_enforcement.py:14-44` — and the gate's report has no `findings`; `[enforcement]` is also disabled in `drift-policy.toml`) nor gate mode (`_gate_blocks`, `cli/check.py:697-716`, also reads `findings` only). Worst of both worlds: consumes escape-hatch budget, enforces nothing. Note also `task-completing.sh` swallows the check's exit code (`|| echo ""`), so even a blocking check cannot stop `wg done` through this path.
*Remediation*: record degrades only when the check is genuinely invoked at completion (explicit flag from the handler, or a one-shot marker), and/or give the acceptance gate's blocked status teeth on this path by emitting a `findings` list the enforcement/gate-mode machinery already understands.

**D3-3 — Verify commands run against the main checkout, not the agent worktree. Severity: warning.**
`executor_shim.py:65-66` calls `check_task(self.wg_dir, ...)`, and `check_task` derives `repo = wg_dir.parent` (`acceptance_gate.py:332`) — the main repo root. Worktree-dispatched agents (this session: `.wg-worktrees/agent-205`) produce artifacts that exist only on their branch until `wg done` merges; the gate evaluates **before** `wg done` runs, i.e., against the pre-merge main tree. Verify commands that check produced artifacts will fail (or worse, pass against stale files) regardless of the worktree's actual state. The same applies to the drifts-check path.
*Remediation*: resolve the task's active worktree (wg knows it — `--ignore-unmerged-worktree` exists) and run verify commands there, or run the gate post-merge as part of the completion transaction.

**D3-4 — Degraded results are dropped silently by the executor. Severity: warning (folded into D4's escalation contract, but wiring-owned).**
`executor_shim.py:68-89` handles only `gate.is_blocking`. A `degraded` verdict (budget consumed, "Task completes with a warning" per `acceptance_gate.py:253-256`) proceeds to `wg done` with the warning string discarded — no `wg log`, no directive-log detail. The only synchronous hard-block path in the system is this executor branch; it works as designed, but the degrade side of the same branch is silent.

---

## D4. Pattern divergence from `signal_gate.py` — verdict: fail

The canonical contract this repo already implements (twice): **hard by default, degrade-to-advisory escape, escalate ignored findings, loud side effects, atomic persistence.**

**D4-1 — No escalation when the ceiling is hit; degrades are silent. Severity: warning.**
The spec requires "when exceeded, the gate reverts to hard mode and **the operator is notified**" and "escalate ignored findings." `acceptance_gate.py` contains no logging at all (no `logging.getLogger`), no notification, no follow-up creation. At ceiling it returns a `blocked` `GateResult` whose `reason` text is seen only by the immediate caller. Contrast the in-repo canonical pattern `_apply_gate_with_escape` (`cli/check.py:765-838`): the degrade is loud — `wg log` to the task plus a waived-followup task created at the threshold crossing, with failure-tolerant side effects. The acceptance gate implements hard-block + degrade but not escalate; of the three-part pattern, one third is missing.
*Remediation*: on every degrade, `wg log <task>` the failing criteria and counter; on the ceiling crossing, create an `acceptance-waived:<task>` followup and fire the existing notification channel; persist degrade events to an audit log (see D2-3).

**D4-2 — Persistence does not mirror `signal_gate.py`'s mechanism. Severity: warning.**
The spec says the counter "persists ... mirroring the persistence mechanism used by `signal_gate.py`." `signal_gate._save_hash` writes tmp + `replace` (`signal_gate.py:87-95`); `save_degrade_state` does a plain in-place `write_text` (`acceptance_gate.py:117-121`). Same divergence from `cli/check.py:_write_gate_failures` (`tmp.write_text` + `path.replace`). This is the mechanical root of CRIT-2's torn-write failure mode.

**D4-3 — Fail-open polarity is inverted relative to the precedent. Severity: warning.**
`signal_gate` and `_read_gate_failures` both fail open on corrupt state, and both document it as safe: for a *suppression* gate, fail-open means "do the work anyway" (benign); for a *consecutive-failure counter*, fail-open means "fresh budget of attempts" (bounded by design intent, `cli/check.py:776-781`). For a *ceiling-limited override budget*, fail-open means "unlimited overrides" — the exact outcome the ceiling exists to prevent (CRIT-1). The pattern was copied without re-deriving the safety polarity. (Also note the counter semantics diverge: `_apply_gate_with_escape` counts consecutive failures and **resets on pass**; the acceptance counter is per-task lifetime monotonic. Monotonic is defensible — arguably stricter — but the difference is undocumented.)

**D4-4 — No canary/observability counterpart. Severity: info.**
`signal_gate.py` logs every fire/suppress decision to a canary JSONL (`log_canary_decision`, `signal_gate.py:246-274`) so misconfiguration is detectable. The acceptance gate has no equivalent: nothing outside the caller's return value records that a gate ever ran, degraded, or blocked. Fold into D4-1's audit log.

---

## D5. Overbuilding — verdict: pass

The evaluator stayed inside "deterministic structural checks": it runs commands and checks exit codes; there is no probabilistic scoring, no artifact-semantics engine, no speculative config surface. The data model (`CriterionResult`/`GateResult`) is minimal and serializable. The CLI surface (check/status/reset) matches the spec's three verbs. Tests are real behavior tests, not snapshot theater.

Nits (info):
- **D5-1** — dead import: `import datetime as dt` (`acceptance_gate.py:17`) is unused.
- **D5-2** — second TOML parser for `drift-policy.toml`: `_load_ceiling` re-parses the policy file (`acceptance_gate.py:136-171`) independently of `driftdriver/policy.py`'s `DriftPolicy`, which knows nothing of `[acceptance]`. Two parsers for one file invites drift in validation/documentation.
- **D5-3** — the ceiling feature overlaps `wg`'s own verify gate (D1-3) and `cli/check.py`'s escape hatch (`gate_max_failures`, default 3 — `cli/__init__.py:931`). Three escape-hatch counters now exist in the completion neighborhood. Not overbuilt *within* the module, but the layer count is growing; document or consolidate.

---

## Mandated edge-case matrix

| Edge case | Finding |
|---|---|
| Evaluator called twice on the same task | **Not idempotent by design**: each `record_degrade=True` call consumes a slot (tests confirm). Correct per-completion semantics, but current wiring can invoke the gate 2-3× per task (D3-2), over-consuming budget. |
| Counter file missing or corrupted | **Fails open** — silently resets to zero (CRIT-1, `acceptance_gate.py:111-114`). Not hard mode. |
| Criteria reference nonexistent artifact paths | Only enforced if encoded as verify commands (e.g., `test -f path` → fail → degrade/block). Prose `acceptance` lists are not evaluated by this gate at all — delegated to the critic/model by design (module docstring). |
| `drift-policy.toml` lacks the ceiling key | Key is `[acceptance] degrade_ceiling` (not `accept_gate_ceiling`). Default applied is **3** (`DEFAULT_DEGRADE_CEILING`, `acceptance_gate.py:29`), not the spec's 5, and it is **not logged** — the fallback is silent (`_load_ceiling` has no logging; the module has none). Not infinite (values < 1 or invalid fall back to 3, tested). See §8. |
| Concurrent completions in one repo | **Not atomic** — lost updates and torn writes allow ceiling exceedance (CRIT-2). No locking anywhere in the module. |
| Criteria authored mid-flight (after task start) | Gate uses **completion-time** criteria from the live `graph.jsonl` description (D1-5); combined with `wg edit -d` this is input control by the gated agent (D2-4). |

---

## 8. Spec-conformance cross-check (supplementary, feeds specdrift)

End-state contract vs implementation:

| Spec says | Implementation | Status |
|---|---|---|
| Ceiling default **5** | `DEFAULT_DEGRADE_CEILING = 3` (`acceptance_gate.py:29`); `test_missing_policy_falls_back_to_three` enshrines 3 | drift — spec or code must be amended |
| State at `.workgraph/acceptance_degrade_state.json` | `.workgraph/service/acceptance-degrade.json` (`acceptance_gate.py:98-103`) | drift — path differs (gitignored either way, verified via `.workgraph/.gitignore:6`) |
| "wg done invokes the evaluator" | Only via coordinator `COMPLETE_TASK` directives (`executor_shim.py:63-89`) and advisory drifts-check wiring; direct `wg done` is gated by the wg binary's own verify gate, not this one | partial — see D1-3/D3 |
| "Operator is notified" at ceiling | No notification of any kind | missing — D4-1 |
| "Malformed criteria → clear error, not silent pass" | Silent `no_criteria` passthrough | violation — D1-1 |
| Verification: named test classes `TestAcceptanceEvaluator`, `TestDegradeCounter`, `TestAcceptanceGateCLI`, `TestBackwardCompat`, **`TestConcurrentDegrade`** | None of these class names exist; concurrency test absent entirely | drift — D2-2 remediation |
| e2e: "CLI check exits non-zero when blocked" | `test_check_blocked_task_exits_nonzero` (`tests/test_acceptance_gate_e2e.py:41-61`) never asserts a non-zero exit — it asserts `returncode == 0` on a non-exhausted task; the blocked-exit path is untested | test-gap |
| e2e: cmd_check wiring | `TestCmdCheckWiring` never invokes `cmd_check`; it calls `check_task` directly, so `cli/check.py:1015-1031` (the actual production wiring, incl. `_is_completion_attempt`) has **no test coverage** | test-gap |

---

## 9. Critical findings register — escalation status

Every severity-critical finding from this review, each escalated to the human operator with evidence (this document + `wg log` entries on the task + ready-to-run remediation commands in §10; `wg notify` was attempted and refused by worker-control policy — final escalation channel is the task log and this completion record). Worker-control policy (`worker_control.operation_refused`) likewise prevents agent-205 from creating the remediation tasks itself; the commands below are prepared for an operator or the coordinator.

| ID | Finding | Evidence | Escalation |
|----|---------|----------|------------|
| CRIT-1 | Degrade budget silently resets when state file is missing/corrupt — fail-open on a budget control | `acceptance_gate.py:106-114, 117-121`; enshrined in `tests/test_acceptance_gate.py::test_load_corrupt_returns_empty` | Escalated: wg log + §10 FU-1 |
| CRIT-2 | Non-atomic, unlocked read-increment-write allows concurrent completions to exceed the ceiling (lost updates, torn writes) | `acceptance_gate.py:238-245` vs atomic precedents `signal_gate.py:87-95`, `cli/check.py:806-824`; no `TestConcurrentDegrade` | Escalated: wg log + §10 FU-1 |

**Handoff decision**: per the task contract, critical findings require escalation before handoff rather than an automatic code rollback (this task is read-only by design: `touch = ["docs/plans/accept-gate-review.md"]`). Both criticals are narrow, mechanical fixes (atomic write + quarantine-on-corrupt + lock) that do **not** require redesign of the gate's architecture; they are escalated above for a human waive-or-fix call. The fail/concern verdicts (D2, D4) each carry concrete remediations below.

---

## 10. Remediation backlog (operator-ready)

```bash
# FU-1 (CRIT-1 + CRIT-2): atomic + corruption-safe + locked degrade state
wg add "Atomic, corruption-safe, locked degrade-state persistence for acceptance gate" \
  --id accept-gate-state-atomicity -t followup \
  -d "acceptance_gate.py save_degrade_state: tmp+replace; load_degrade_state: quarantine corrupt file + operator alert instead of silent reset; serialize read-modify-write with sidecar lock; add TestConcurrentDegrade. See docs/plans/accept-gate-review.md CRIT-1/CRIT-2."

# FU-2 (D4-1, D2-3, D4-4): loud degrade + escalation at ceiling + audit trail
wg add "Escalate acceptance-gate degrades: wg log, waived followup at ceiling, audit log, notify" \
  --id accept-gate-escalate-wiring -t followup \
  -d "Match _apply_gate_with_escape pattern: wg log on every degrade/block, followup task on ceiling crossing, append-only .workgraph/service/acceptance-audit.jsonl for degrade/reset events with actor+timestamp, operator notification at ceiling. See docs/plans/accept-gate-review.md D4."

# FU-3 (D3-2): stop drifts-check budget burn on non-completion checks
wg add "Acceptance gate in drifts check must not consume degrade budget on pre-task checks" \
  --id accept-gate-drifts-budget-burn -t followup \
  -d "cli/check.py:1015-1031 derives record_degrade from task status in-progress, so protocol-mandated pre-task checks consume budget while the result is advisory-only. Gate on explicit completion context instead; optionally give blocked status teeth via report.findings. See docs/plans/accept-gate-review.md D3-2."

# FU-4 (D1-1, D1-2, D2-4): deterministic fence-scoped extraction + claim-time snapshot
wg add "Deterministic verify extraction scoped to wg-contract fence; snapshot at claim" \
  --id accept-gate-verify-extraction -t followup \
  -d "Replace regex-first-match-anywhere extraction with TOML parse of the wg-contract fence; unparseable verify lists must error loudly (spec: clear error, not silent pass); snapshot verify list at claim time so wg edit cannot rewrite gate input mid-flight. See docs/plans/accept-gate-review.md D1/D2-4."

# FU-5 (§8, D3-1, D3-3): reconcile spec drift + runtime install + worktree evaluation
wg add "Reconcile acceptance-gate spec/code drift; install gate into runtime; fix worktree evaluation" \
  --id accept-gate-spec-reconcile -t followup \
  -d "Default ceiling 5-vs-3, state-file path, missing named test classes (incl. TestConcurrentDegrade), e2e tests that under-assert; reinstall uv driftdriver so the gate is live (currently absent from installed runtime); run verify commands in the task worktree not the main checkout; document relationship to wg binary verify gate. See docs/plans/accept-gate-review.md §8/D3."
```

---

## Verification of this review

- Read-first list consumed in full: `signal_gate.py`, `acceptance_gate.py`, `workgraph.py`, `policy.py` (relevant sections), `DRIFT_PLUGIN_CONTRACT.md`, both gate test files, e2e test file, `drift-policy.toml` (worktree + main repo).
- All gate tests executed: `uv run --with pytest python -m pytest tests/test_acceptance_gate.py tests/test_acceptance_gate_integration.py tests/test_acceptance_gate_e2e.py` → **43 passed**.
- Live evidence: main-repo degrade state (`{}` — gate never fired there), installed-package inspection, `wg done --help` / `wg edit --help`, `wg` binary strings, predecessor task `accept-gate-discovery` (completed with prose verify entries), this task's own in-progress pre-check behavior.
