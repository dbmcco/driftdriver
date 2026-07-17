# Model-Mediated Deviation Register

This is the owned, reviewable record of intentional deterministic exceptions to
the model-mediated-development doctrine. A lane (modelrift, surfacedrift) may
suppress a finding by matching its `file:line` against a **Location** entry here.

Every entry states the doctrine clause it touches, the deterministic mechanism
in use, and the decision. Deviations are not exemptions from thought — they are
the *evidence* that a deterministic choice was made deliberately and by whom.

---

## Dogfood triage — driftdriver self-scan (2026-07-17)

Scope: 52 production findings from `python -m driftdriver.modelrift .` after the
git-boundary walk fix (worktree echo eliminated). Test-file findings (`tests/**`,
76 findings) are covered wholesale by the class below — test assertions are
deterministic by design and model agency is explicitly unwanted in test code.

**Headline finding:** zero of the 52 are genuine migrate-to-model candidates.
All are legitimately deterministic. This also surfaces a detector-quality issue
(see Follow-up F1): the keyword-gate detector over-matches on variable names
containing `keyword` / `marker` / `intent` regardless of whether classification
is actually being performed.

### Class T1 — Protocol / document text anchors

Literal string anchors used to detect insertion points during idempotent edits
to agent prompt-template files (e.g. "does CLAUDE.md already contain the
Speedrift Ecosystem section?"). These are exact-match document boundaries, not
intent classification. Text-boundary detection is correctly deterministic.

- **Location:** `` `driftdriver/install.py:11-26` `` `` `driftdriver/install.py:546-546` ``
- **Doctrine clause:** keyword/marker intent gates
- **Mechanism:** literal text-anchor constants for document section detection
- **Decision:** `deviation` — text anchors are not model agency

### Class T2 — Report-filtering keywords read from user config

`report_keywords` / `keyword_filter` / `keywords` are read from user-owned
configuration and used to filter which events surface in reports. The human
chooses the terms; the code honors them. This is explicit user-driven filtering,
not the code guessing model meaning.

- **Location:** `` `driftdriver/cli/_helpers.py:158-158` `` `` `driftdriver/cli/_helpers.py:274-274` `` `` `driftdriver/cli/check.py:1289-1289` `` `` `driftdriver/ecosystem_hub/discovery.py:944-944` `` `` `driftdriver/intelligence/adapters/github.py:87-87` `` `` `driftdriver/intelligence/adapters/github.py:99-99` `` `` `driftdriver/intelligence/adapters/vibez.py:78-78` `` `` `driftdriver/northstardrift.py:678-678` ``
- **Doctrine clause:** keyword/marker intent gates
- **Mechanism:** user-configured filter terms, read from config and applied verbatim
- **Decision:** `deviation` — honoring explicit user config is not model agency

### Class P1 — Policy tier tables on semantic scores

A continuous, model/system-computed score mapped to discrete action tiers. The
model owns computing the score; humans own the tier policy (the cutoffs and
their consequences). This is the correct "code owns structure, model owns
meaning" split — analogous to tax brackets or incident-severity tiers.
Migrating these to per-run model judgment would make budgets and rollback
behavior non-deterministic and unauditable, which is undesirable.

- **Location:** `` `driftdriver/quality_signal.py:155-164` `` `` `driftdriver/rollback.py:51-64` `` `` `driftdriver/decision_notifier.py:99-99` `` `` `driftdriver/decision_queue.py:138-138` `` `` `driftdriver/governancedrift.py:119-119` `` `` `driftdriver/quality_planner.py:390-390` ``
- **Doctrine clause:** hardcoded semantic thresholds
- **Mechanism:** human-set policy tables mapping a score to a fixed action tier
- **Decision:** `deviation` — policy tiers are human-owned; the score is model-owned

### Class E1 — Enum validation / dispatch on model-classified output

The continuation intent is classified by a model upstream. These sites validate
the model's label is within the allowed closed set, and dispatch on it. Validating
against a closed enum and routing on a model decision are correctly deterministic
(the model already decided; the code enforces the contract).

- **Location:** `` `driftdriver/continuation_intent.py:10-10` `` `` `driftdriver/continuation_intent.py:16-16` `` `` `driftdriver/continuation_intent.py:54-54` `` `` `driftdriver/continuation_intent.py:72-72` `` `` `driftdriver/factory_brain/router.py:93-94` ``
- **Doctrine clause:** keyword/marker intent gates
- **Mechanism:** closed-enum validation and deterministic routing on model output
- **Decision:** `deviation` — validation/dispatch on model-classified output

### Class X1 — Detector false positives (display / assignment / default / construction)

These lines do not perform intent classification or semantic judgment at all.
They were flagged because a variable name contains `keyword`, `marker`, or
`intent`. They are display strings, default argument values, dict construction,
field assignment, or filename-pattern detection. See Follow-up F1.

- **Location:** `` `driftdriver/cli/intent_cmd.py:52-52` `` `` `driftdriver/cli/decisions_cmd.py:35-35` `` `` `driftdriver/ecosystem_hub/session_launcher.py:65-65` `` `` `driftdriver/ecosystem_hub/session_launcher.py:164-164` `` `` `driftdriver/ecosystem_hub/snapshot.py:531-531` `` `` `driftdriver/factory_brain/router.py:218-218` `` `` `driftdriver/tmux_monitor/relevance.py:127-127` `` `` `driftdriver/policy.py:480-480` `` `` `driftdriver/plandrift.py:139-139` ``
- **Doctrine clause:** keyword/marker intent gates (false match)
- **Mechanism:** display formatting, default values, data access, framework filename detection
- **Decision:** `deviation` — not classification; detector over-match (F1)

### Class TEST — Test files

Test assertions are deterministic by design; model agency is explicitly unwanted
in test code. modelrift skips test trees (`tests/`, `test/`) at scan time, so
findings there never appear. This is a lane-level applicability decision, not a
per-site deviation — there is nothing to review in test fixtures.

- **Doctrine clause:** keyword/marker intent gates; semantic thresholds
- **Mechanism:** scan-level skip of test directories (`_is_test_path`)
- **Decision:** `out-of-scope` — tests are deterministic by intent

---

## Follow-ups

- **F1 — modelrift detector recalibration.** The keyword-gate detector matches
  on variable-name shape (`*keyword*`, `*marker*`, `*intent*` bound to strings)
  without checking whether classification is actually performed at that site.
  Result: display strings, default args, dict construction, and config passthrough
  are flagged. Tightening the detector (require an actual comparison/use as a
  gate, not merely a definition) would remove the X1 and most T2-class false
  positives at the source. Out of scope for this triage; tracked separately.
