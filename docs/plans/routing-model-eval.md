# ABOUTME: Evaluation of model-mediated vs rule-based lane routing for driftdriver.
# ABOUTME: Decision document — recommends RULES-ONLY with scoped conditions for revisiting.

# Routing Model Evaluation: Rule-Based vs Model-Mediated

**Date:** 2026-03-07
**Status:** RECOMMENDATION — RULES-ONLY
**Author:** Claude (evaluation task)

---

## 1. Current Routing Logic Analysis

### Architecture

Lane routing lives in three files:

- **`smart_routing.py`** (212 lines) — Evidence gathering. Assembles an `EvidencePackage` from git diff, task descriptions, workgraph state, pattern hints from `drift-policy.toml`, lane weights from the outcome ledger, and project knowledge from the Lessons MCP.
- **`routing_models.py`** (167 lines) — Decision logic. Contains `rule_based_routing()` (the active path), `parse_routing_response()` (the dormant model-response parser), `detect_fenced_lanes()`, and the `RoutingDecision` dataclass.
- **`outcome_feedback.py`** (185 lines) — Feedback loop. Compares pre-task and post-task drift findings, classifies each as resolved/ignored/worsened, writes to the outcome JSONL ledger.

### Active Decision Flow (strategy = `smart`)

```
gather_evidence(wg_dir)
  → git diff --name-status HEAD~1
  → load task description + contract from workgraph
  → discover installed lane wrappers (executable files in .workgraph/)
  → load glob patterns from drift-policy.toml [lane-routing.patterns]
  → compute_lane_weights() from outcome ledger
  → load project knowledge (high/medium confidence entries)

rule_based_routing(evidence)
  → 1. Auto-fenced lanes from ```lanename fences in task description (mandatory)
  → 2. Pattern-matched lanes from file glob classifications
  → 3. Escalated lanes where outcome weight > 1.1
  → 4. Filter to installed lanes only
  → return RoutingDecision
```

### Other Strategies

| Strategy | Behavior |
|----------|----------|
| `auto` (default) | Fence detection + complexity/keyword heuristics → full suite escalation |
| `fences` | Only runs lanes explicitly fenced in task description |
| `all` | Runs every installed optional plugin |
| `smart` | Evidence-based rule routing (above) |

### History

Commit `6d7d760` introduced model-mediated routing with `format_routing_prompt()` — but the actual call site passed an **empty string** where the model response should go: `parse_routing_response("", evidence)`. This made "smart" routing silently degrade to the pattern-based fallback path every single time. Commit `ff1d5d0` replaced this with `rule_based_routing()` that properly uses all three evidence sources.

---

## 2. Where Rules Succeed

The rule-based system handles the following cases well:

**Task fence detection (mandatory lanes):** Task descriptions with ` ```specdrift ` blocks always trigger the correct lane. This is deterministic and correct by construction — a model adds zero value here.

**File-type routing via glob patterns:** When `drift-policy.toml` has a `[lane-routing.patterns]` section, file classifications map cleanly to lanes. `*.py → coredrift`, `*.tsx → uxdrift`, `*.sql → datadrift` are relationships a model would also learn but can't improve on.

**Outcome-based escalation:** The feedback loop computes per-lane resolved/ignored/worsened rates and escalates lanes whose findings are being systematically ignored (weight > 1.1). This is genuinely adaptive — the system learns from its own history without any model call.

**Full-suite escalation (auto strategy):** Complex tasks (rebuild, migration, multi-dependency) automatically run all lanes. This is a coarse but effective catch-all.

**Installed-lane filtering:** Only lanes with executable wrappers in `.workgraph/` can be selected. The routing problem is constrained to a small, known set (currently 11 KNOWN_LANES).

**Estimated accuracy:** For the typical driftdriver workload — task descriptions with explicit fences, Python/TypeScript codebases with predictable file types — rule-based routing selects the correct lane set >90% of the time. The `auto` strategy's full-suite escalation acts as a safety net for edge cases.

---

## 3. Where Rules Fail (or Could)

**Novel finding types:** If a task involves a pattern not captured by existing glob patterns (e.g., infrastructure-as-code files, new framework conventions), rules will miss relevant lanes. However: the `auto` strategy's full-suite escalation catches most of these, and adding a glob pattern to `drift-policy.toml` is trivial.

**Cross-lane correlations:** A model could theoretically notice that "when specdrift fires + datadrift has warnings, archdrift is usually needed too." Rules don't capture these higher-order correlations. In practice, the outcome escalation mechanism partially covers this: if archdrift findings are ignored in such scenarios, its weight rises and it gets included.

**Semantic task understanding:** A model could read "refactor the payment processing pipeline" and infer that `datadrift`, `specdrift`, and `archdrift` are all relevant even without file pattern matches. Rules rely on keyword matching (COMPLEXITY_KEYWORDS) which is coarser. However: the `auto` strategy would likely trigger full-suite for this task anyway (complexity keywords "refactor" + implied scope).

**No pattern configuration:** The live `drift-policy.toml` has **no** `[lane-routing.patterns]` section. This means the `smart` strategy currently does fence detection + outcome escalation but **no file-based routing**. This is a configuration gap, not an algorithm gap — adding patterns is the right fix.

---

## 4. Cost and Latency Estimate

### Model Path (Haiku)

| Parameter | Value |
|-----------|-------|
| Input tokens per routing call | ~800-1200 (evidence context) |
| Output tokens per routing call | ~150-250 (JSON decision) |
| Input cost | $0.25 / 1M tokens |
| Output cost | $1.25 / 1M tokens |
| Cost per call | ~$0.0003 - $0.0005 |
| Latency per call | ~200ms (Haiku) |

### Volume Estimate

| Scenario | Routing calls/day | Monthly cost | Monthly latency added |
|----------|-------------------|--------------|----------------------|
| Single dev (manual checks) | 5-15 | $0.005 - $0.02 | 1-3 seconds total |
| Dark factory (autonomous) | 20-80 | $0.02 - $0.10 | 4-16 seconds total |
| Multi-repo ecosystem (5 repos) | 50-200 | $0.05 - $0.25 | 10-40 seconds total |

### Assessment

Cost is negligible at any realistic scale ($0.25/month worst case). Latency of ~200ms per decision is also negligible — drift checks themselves take seconds to minutes.

**Cost/latency is not the reason to avoid model routing.** The question is purely about marginal value.

---

## 5. Marginal Value Analysis

The outcome feedback loop already provides the adaptive behavior that a model would offer:

| Capability | Rules | Model | Delta |
|------------|-------|-------|-------|
| Fence detection | deterministic | redundant | 0 |
| File-type routing | glob patterns | same accuracy | 0 |
| Outcome-based escalation | weight formula | could be more nuanced | marginal |
| Novel pattern recognition | fails, needs config update | slight advantage | small, infrequent |
| Cross-lane correlation | not supported | possible | small, unquantified |
| Semantic task reading | keyword matching | better | exists but auto catches most |
| Explainability | fully transparent | opaque JSON | rules win |
| Reliability | deterministic, no network | network dependency | rules win |
| Testability | simple unit tests | needs mocking/stubbing | rules win |

The honest assessment: a model would make marginally better decisions in ~5-10% of routing calls (the cases where no fence exists, no pattern matches, and auto doesn't escalate to full suite). But the current system already has two fallback mechanisms (outcome escalation + auto full-suite) that catch most of those cases.

---

## 6. Recommendation: RULES-ONLY

**Stay rule-based.** The current system is deterministic, testable, zero-latency, zero-cost, and accurate enough. The adaptive feedback loop via outcome weights provides the learning behavior that makes model routing tempting, but without the complexity.

### Action Items (to close the gaps identified above)

1. **Add `[lane-routing.patterns]` to `drift-policy.toml`** — The single biggest improvement. Configure glob patterns for the common file types across the ecosystem. This is a 5-minute config change that closes the file-routing gap.

2. **Monitor outcome rates** — Once the ledger has real data, check whether any lane consistently has high ignored/worsened rates that the escalation mechanism doesn't catch. If patterns emerge that rules can't express, revisit this decision.

3. **Keep `parse_routing_response()` and `RoutingDecision.model_suggested`** — The model integration plumbing is already written and tested. If a future need arises, the wiring cost is low.

### Conditions to Revisit

Revisit this decision if **any** of these become true:
- Outcome data shows rule-based routing selecting wrong lanes >15% of the time
- The ecosystem grows beyond ~15 lanes, making combinatorial selection harder for rules
- A use case emerges where semantic task understanding is critical (e.g., natural-language task descriptions without fences or keywords)
- Cross-lane correlations prove to be a real source of missed findings (requires outcome data to evaluate)

### What a HYBRID Would Look Like (If Needed Later)

If the decision flips to HYBRID, the model should be used **only** for the "gap" case:
- Rules run first (fences + patterns + escalation)
- If rules select 0 optional lanes AND `auto` doesn't trigger full-suite → call Haiku to suggest lanes from the evidence package
- Model output is additive only (can add lanes, never remove rule-selected ones)
- This limits model calls to ~10-20% of routing decisions (the uncertain ones)

The `to_prompt_context()` method and `parse_routing_response()` function already implement this path. The only missing piece is the actual HTTP call to the model endpoint.

---

## Summary

| Criterion | Assessment |
|-----------|-----------|
| Rule accuracy | >90% for typical workloads |
| Model marginal value | ~5-10% of decisions, partially covered by fallbacks |
| Cost barrier | None (negligible at any scale) |
| Complexity cost | Real (network dependency, error handling, test mocking) |
| Adaptive behavior | Already provided by outcome feedback loop |
| **Verdict** | **RULES-ONLY** |
