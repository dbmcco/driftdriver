# Dark Factory Operator Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the ecosystem hub into an operator-first surface that answers what needs attention now, what requires human judgment, and whether the Dark Factory is moving toward its North Star, while preserving the current detailed evidence views.

**Architecture:** Add a backend operator-home builder that computes the `Factory Status` scorecard plus canonical `Now / Decide / Watch` items from the existing snapshot, decision queues, and notification ledger. Expose that payload through a single API route, render it as a new default `Home` tab with an evidence drawer and full-view transitions into the existing detailed tabs, then tighten queue shaping and metrics so Gate health, autonomy, and convergence are measured at the control-plane layer instead of guessed in the UI.

**Tech Stack:** Python stdlib HTTP server, existing ecosystem hub HTML/JS dashboard, JSON snapshot + decision queues, pytest/unittest, Node `--check` for generated dashboard JavaScript.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `driftdriver/ecosystem_hub/operator_home.py` | Create | Canonical operator-home payload builder: scorecard, `Now`, `Decide`, `Watch`, evidence metadata |
| `driftdriver/hub_analytics.py` | Modify | Shared health-domain and convergence helpers used by snapshot views and operator scorecard |
| `driftdriver/ecosystem_hub/api.py` | Modify | Add `/api/operator/home` and route payloads through canonical builder |
| `driftdriver/ecosystem_hub/dashboard.py` | Modify | Add `Home` tab, scorecard, `Now / Decide / Watch`, evidence drawer, and full-view transitions |
| `driftdriver/decision_queue.py` | Modify | Add queue-shaping helpers for age, urgency, and stale/duplicate handling |
| `driftdriver/decision_notifier.py` | Modify | Add digest/page routing metadata and persist notification provenance for the operator view |
| `tests/test_operator_home.py` | Create | Unit tests for scorecard, action grouping, and drill-down metadata |
| `tests/test_ecosystem_hub.py` | Modify | API + dashboard coverage for operator-home payload, Home tab, drawer wiring, and JS parse checks |
| `tests/test_decision_notifier.py` | Modify | Verify routing metadata and notification ledger fields used by Gate health |

---

## Task 1: Build the operator-home payload and scorecard backbone

**Files:**
- Create: `driftdriver/ecosystem_hub/operator_home.py`
- Create: `tests/test_operator_home.py`
- Modify: `driftdriver/hub_analytics.py`

- [ ] **Step 1: Write the failing unit tests for scorecard and action grouping**

```python
# tests/test_operator_home.py
from __future__ import annotations

from driftdriver.ecosystem_hub.operator_home import build_operator_home


def test_build_operator_home_promotes_control_plane_failures_into_now() -> None:
    snapshot = {
        "generated_at": "2026-04-10T20:00:00+00:00",
        "repos": [],
        "northstardrift": {"summary": {"overall_score": 82.0, "overall_trend": "regressing", "overall_tier": "watch"}},
        "overview": {"repos_with_errors": 1},
        "control_plane": {"errors": ["factory cycle failed"], "hub_available": True},
    }
    payload = build_operator_home(snapshot=snapshot, decisions=[], notification_ledger=[])
    assert payload["now"][0]["kind"] == "control_plane"
    assert payload["scorecard"]["status"] == "red"


def test_build_operator_home_keeps_human_items_in_decide() -> None:
    decision = {
        "id": "dec-20260410-abc123",
        "repo": "paia-agents",
        "status": "pending",
        "question": "Adopt the Derek prompt fix?",
        "category": "agent_health",
        "context": {
            "agent_member": "derek",
            "severity": "medium",
            "confidence": 0.78,
            "full_view": {"tab": "factory", "focus": "decision:dec-20260410-abc123"},
        },
        "created_at": "2026-04-10T19:50:00+00:00",
    }
    payload = build_operator_home(snapshot={"repos": [], "northstardrift": {"summary": {}}}, decisions=[decision], notification_ledger=[])
    assert payload["decide"][0]["decision_id"] == "dec-20260410-abc123"
    assert payload["decide"][0]["full_view"]["tab"] == "factory"


def test_build_operator_home_moves_low_signal_or_stale_items_into_watch() -> None:
    decision = {
        "id": "dec-20260410-stale01",
        "repo": "paia-agents",
        "status": "pending",
        "question": "Minor CLAUDE.md wording fix?",
        "category": "agent_health",
        "context": {"severity": "low", "confidence": 0.41},
        "created_at": "2026-04-05T12:00:00+00:00",
    }
    payload = build_operator_home(snapshot={"repos": [], "northstardrift": {"summary": {}}}, decisions=[decision], notification_ledger=[])
    assert payload["decide"] == []
    assert payload["watch"][0]["decision_id"] == "dec-20260410-stale01"
```

- [ ] **Step 2: Run the new tests to confirm the module is missing/failing**

```bash
cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_operator_home.py -q
```

Expected: `ModuleNotFoundError` for `driftdriver.ecosystem_hub.operator_home` or assertion failures.

- [ ] **Step 3: Implement the operator-home builder and shared health helpers**

```python
# driftdriver/ecosystem_hub/operator_home.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class OperatorItem:
    bucket: str
    kind: str
    title: str
    repo: str
    urgency: int
    confidence: float
    rationale: str
    evidence: dict[str, Any]
    full_view: dict[str, Any]
    decision_id: str | None = None


def build_operator_home(
    *,
    snapshot: dict[str, Any],
    decisions: list[dict[str, Any]],
    notification_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    domains = build_operator_domains(snapshot=snapshot, decisions=decisions, notification_ledger=notification_ledger)
    items = rank_operator_items(snapshot=snapshot, decisions=decisions)
    return {
        "scorecard": build_factory_scorecard(domains),
        "domains": domains,
        "now": [item for item in items if item["bucket"] == "now"],
        "decide": [item for item in items if item["bucket"] == "decide"],
        "watch": [item for item in items if item["bucket"] == "watch"],
        "counts": {
            "now": sum(1 for item in items if item["bucket"] == "now"),
            "decide": sum(1 for item in items if item["bucket"] == "decide"),
            "watch": sum(1 for item in items if item["bucket"] == "watch"),
        },
    }


def build_factory_scorecard(domains: dict[str, Any]) -> dict[str, Any]:
    control_errors = int(domains["control_plane"]["error_count"])
    pending_decisions = int(domains["gate"]["pending_count"])
    convergence = str(domains["convergence"]["trend"])
    status = "green"
    why = "control plane healthy and convergence stable"
    if control_errors > 0:
        status = "red"
        why = "control-plane failures are blocking reliable factory operation"
    elif pending_decisions > 10 or convergence == "regressing":
        status = "yellow"
        why = "Gate load or convergence trend is above healthy operating range"
    return {
        "status": status,
        "why": why,
        "needs_you": pending_decisions,
        "autonomous_this_week": int(domains["autonomy"]["closed_without_operator"]),
        "convergence_trend": convergence,
        "confidence": domains["control_plane"]["confidence"],
    }
```

```python
# driftdriver/hub_analytics.py
def build_operator_domains(
    *,
    snapshot: dict[str, Any],
    decisions: list[dict[str, Any]],
    notification_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    north_star = ((snapshot.get("northstardrift") or {}).get("summary") or {})
    control_errors = list((snapshot.get("control_plane") or {}).get("errors") or [])
    return {
        "control_plane": {
            "error_count": len(control_errors),
            "confidence": "high" if not control_errors else "medium",
        },
        "gate": {
            "pending_count": len([d for d in decisions if d.get("status") == "pending"]),
            "stale_count": len([d for d in decisions if is_stale_decision(d)]),
        },
        "autonomy": {
            "closed_without_operator": count_autonomous_closures(notification_ledger),
        },
        "convergence": {
            "score": float(north_star.get("overall_score") or 0),
            "trend": str(north_star.get("overall_trend") or "flat"),
        },
    }
```

- [ ] **Step 4: Run the targeted tests and confirm they pass**

```bash
PYTHONPATH=$PWD pytest tests/test_operator_home.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit the operator-home backbone**

```bash
git add driftdriver/ecosystem_hub/operator_home.py driftdriver/hub_analytics.py tests/test_operator_home.py
git commit -m "feat: add operator-home scorecard backbone"
```

---

## Task 2: Expose the canonical operator-home API

**Files:**
- Modify: `driftdriver/ecosystem_hub/api.py`
- Modify: `tests/test_ecosystem_hub.py`

- [ ] **Step 1: Add failing API tests for `/api/operator/home`**

```python
# tests/test_ecosystem_hub.py
def test_api_operator_home_returns_scorecard_and_action_buckets(self) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        snapshot_path = root / "snapshot.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "generated_at": "2026-04-10T20:00:00+00:00",
                    "repos": [{"name": "paia-agents", "path": str(root / "paia-agents")}],
                    "northstardrift": {"summary": {"overall_score": 77.4, "overall_trend": "watch"}},
                }
            ),
            encoding="utf-8",
        )
        project = root / "paia-agents"
        project.mkdir()
        decisions_dir = project / ".workgraph" / "service" / "runtime"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        (decisions_dir / "decisions.jsonl").write_text(
            json.dumps(
                {
                    "id": "dec-20260410-home01",
                    "repo": "paia-agents",
                    "status": "pending",
                    "question": "Review operator queue?",
                    "category": "feature",
                    "context": {"severity": "high", "confidence": 0.9},
                    "created_at": "2026-04-10T19:59:00+00:00",
                }
            ) + "\n",
            encoding="utf-8",
        )
        port = _start_test_hub(snapshot_path=snapshot_path, state_path=root / "state.json")
        with urlopen(f"http://127.0.0.1:{port}/api/operator/home", timeout=2.0) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertIn("scorecard", payload)
        self.assertIn("decide", payload)
        self.assertEqual(payload["decide"][0]["repo"], "paia-agents")
```

- [ ] **Step 2: Run the API test and confirm the route is missing**

```bash
cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_ecosystem_hub.py -q -k 'api_operator_home_returns_scorecard_and_action_buckets'
```

Expected: `404` response or route assertion failure.

- [ ] **Step 3: Add the route and reuse the canonical decision + ledger inputs**

```python
# driftdriver/ecosystem_hub/api.py
from driftdriver.ecosystem_hub.operator_home import build_operator_home


def _load_notification_ledger(self) -> list[dict[str, Any]]:
    ledger_path = Path.home() / ".config" / "workgraph" / "factory-brain" / "notification-ledger.jsonl"
    if not ledger_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


if self.path == "/api/operator/home":
    snapshot = self._read_snapshot()
    decisions = self._load_chat_decisions(snapshot) or []
    payload = build_operator_home(
        snapshot=snapshot,
        decisions=decisions,
        notification_ledger=self._load_notification_ledger(),
    )
    self._send_json(payload)
    return
```

- [ ] **Step 4: Run the API tests and the existing decision aggregation tests**

```bash
PYTHONPATH=$PWD pytest tests/test_ecosystem_hub.py -q -k 'api_operator_home_returns_scorecard_and_action_buckets or api_decisions_endpoint_aggregates_pending'
```

Expected: both tests pass.

- [ ] **Step 5: Commit the operator-home API**

```bash
git add driftdriver/ecosystem_hub/api.py tests/test_ecosystem_hub.py
git commit -m "feat: expose operator-home api"
```

---

## Task 3: Add the Home tab, operator scorecard, and evidence drawer

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py`
- Modify: `tests/test_ecosystem_hub.py`

- [ ] **Step 1: Add failing dashboard tests for the new operator-first home**

```python
# tests/test_ecosystem_hub.py
def test_dashboard_template_contains_operator_home_sections(self) -> None:
    html = render_dashboard_html()
    self.assertIn('data-tab="home"', html)
    self.assertIn("Factory Status", html)
    self.assertIn("operator-now-list", html)
    self.assertIn("operator-decide-list", html)
    self.assertIn("operator-watch-list", html)
    self.assertIn("operator-evidence-drawer", html)
    self.assertIn("fetch('/api/operator/home')", html)


def test_dashboard_template_wires_full_view_navigation(self) -> None:
    html = render_dashboard_html()
    self.assertIn("openOperatorEvidence", html)
    self.assertIn("openOperatorFullView", html)
    self.assertIn("loadOperatorHome()", html)
```

- [ ] **Step 2: Run the dashboard tests and confirm the Home surface does not exist yet**

```bash
cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_ecosystem_hub.py -q -k 'dashboard_template_contains_operator_home_sections or dashboard_template_wires_full_view_navigation'
```

Expected: assertions fail because the `Home` tab and operator-home wiring are not present.

- [ ] **Step 3: Implement the Home tab, drawer, and full-view transitions**

```python
# driftdriver/ecosystem_hub/dashboard.py
<button class="hub-tab active" data-tab="home">Home</button>
<button class="hub-tab" data-tab="operations">Operations</button>
<button class="hub-tab" data-tab="intelligence">Intelligence</button>
<button class="hub-tab" data-tab="conformance">Conformance</button>
<button class="hub-tab" data-tab="convergence">Convergence</button>
<button class="hub-tab" data-tab="factory">Factory <span class="badge" id="factory-decision-count">0</span></button>

<section class="hub-panel active" data-panel="home">
  <div id="operator-scorecard"></div>
  <div class="operator-columns">
    <div><h2>Now</h2><div id="operator-now-list"></div></div>
    <div><h2>Decide</h2><div id="operator-decide-list"></div></div>
    <div><h2>Watch</h2><div id="operator-watch-list"></div></div>
  </div>
</section>

<aside id="operator-evidence-drawer" class="task-graph-drawer">
  <div id="operator-evidence-summary"></div>
  <div id="operator-evidence-rationale"></div>
  <a id="operator-evidence-full-view" href="#">Open Full View</a>
</aside>
```

```javascript
async function loadOperatorHome() {
  const payload = await fetch('/api/operator/home').then(function(r) { return r.json(); });
  renderOperatorScorecard(payload.scorecard, payload.domains);
  renderOperatorList('operator-now-list', payload.now);
  renderOperatorList('operator-decide-list', payload.decide);
  renderOperatorList('operator-watch-list', payload.watch);
}

function openOperatorEvidence(itemIndex, bucket) {
  const item = operatorHomeState[bucket][itemIndex];
  el('operator-evidence-summary').textContent = item.title;
  el('operator-evidence-rationale').textContent = item.rationale;
  el('operator-evidence-full-view').onclick = function() { openOperatorFullView(item); return false; };
  el('operator-evidence-drawer').classList.add('open');
}

function openOperatorFullView(item) {
  activateHubTab(item.full_view.tab || 'factory');
  window.location.hash = item.full_view.focus || '';
}
```

- [ ] **Step 4: Run the dashboard tests plus the JavaScript parse guard**

```bash
PYTHONPATH=$PWD pytest tests/test_ecosystem_hub.py -q -k 'dashboard_template_contains_operator_home_sections or dashboard_template_wires_full_view_navigation or dashboard_template_emits_javascript_that_parses'
```

Expected: all targeted dashboard tests pass.

- [ ] **Step 5: Commit the operator-first home UI**

```bash
git add driftdriver/ecosystem_hub/dashboard.py tests/test_ecosystem_hub.py
git commit -m "feat: add operator-first home to ecosystem hub"
```

---

## Task 4: Shape Gate quality and notification routing

**Files:**
- Modify: `driftdriver/decision_queue.py`
- Modify: `driftdriver/decision_notifier.py`
- Modify: `driftdriver/ecosystem_hub/operator_home.py`
- Modify: `tests/test_decision_notifier.py`
- Modify: `tests/test_operator_home.py`

- [ ] **Step 1: Add failing tests for routing metadata and stale/low-signal decisions**

```python
# tests/test_decision_notifier.py
def test_notify_records_route_and_severity_in_ledger(self) -> None:
    with TemporaryDirectory() as td:
        ledger = Path(td) / "notification-ledger.jsonl"
        decision = self._make_decision()
        decision.context = {"severity": "high", "confidence": 0.92, "route": "page"}
        notify_decision(decision, bot_token="test-token", chat_id="test-chat", ledger_path=ledger)
        entry = __import__("json").loads(ledger.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(entry["route"], "page")
        self.assertEqual(entry["severity"], "high")


# tests/test_operator_home.py
def test_decide_omits_stale_low_confidence_items() -> None:
    stale = {
        "id": "dec-20260401-old001",
        "repo": "paia-agents",
        "status": "pending",
        "question": "Minor note tweak?",
        "category": "agent_health",
        "context": {"severity": "low", "confidence": 0.35},
        "created_at": "2026-04-01T00:00:00+00:00",
    }
    payload = build_operator_home(snapshot={"repos": [], "northstardrift": {"summary": {}}}, decisions=[stale], notification_ledger=[])
    assert payload["decide"] == []
    assert payload["watch"][0]["decision_id"] == "dec-20260401-old001"
```

- [ ] **Step 2: Run the new notifier/operator tests and confirm the fields do not exist yet**

```bash
cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_decision_notifier.py tests/test_operator_home.py -q
```

Expected: ledger field assertions fail or route/severity not present.

- [ ] **Step 3: Implement decision shaping and notification routing metadata**

```python
# driftdriver/decision_queue.py
from datetime import datetime, timezone


def decision_age_hours(decision: dict[str, Any], *, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    created_at = datetime.fromisoformat(str(decision.get("created_at")).replace("Z", "+00:00"))
    return max(0.0, (now - created_at).total_seconds() / 3600.0)


def classify_gate_bucket(decision: dict[str, Any]) -> str:
    severity = str((decision.get("context") or {}).get("severity") or "medium")
    confidence = float((decision.get("context") or {}).get("confidence") or 0.0)
    age_hours = decision_age_hours(decision)
    if severity in {"critical", "high"} and confidence >= 0.75:
        return "decide"
    if age_hours >= 72 or confidence < 0.5:
        return "watch"
    return "decide"
```

```python
# driftdriver/decision_notifier.py
def classify_notification_route(decision: DecisionRecord) -> str:
    severity = str(decision.context.get("severity") or "medium")
    confidence = float(decision.context.get("confidence") or 0.0)
    if severity in {"critical", "high"} and confidence >= 0.8:
        return "page"
    return "digest"


entry = {
    "decision_id": decision.id,
    "repo": decision.repo,
    "category": decision.category,
    "channel": "telegram_factory",
    "delivery_status": "sent" if sent else "failed",
    "route": decision.context.get("route") or classify_notification_route(decision),
    "severity": str(decision.context.get("severity") or "medium"),
    "confidence": float(decision.context.get("confidence") or 0.0),
    "sent_at": datetime.now(timezone.utc).isoformat(),
    "provenance": dict(decision.context),
}
```

- [ ] **Step 4: Run the focused Gate-quality tests**

```bash
PYTHONPATH=$PWD pytest tests/test_decision_notifier.py tests/test_operator_home.py -q
```

Expected: all targeted tests pass.

- [ ] **Step 5: Commit the Gate-shaping work**

```bash
git add driftdriver/decision_queue.py driftdriver/decision_notifier.py driftdriver/ecosystem_hub/operator_home.py tests/test_decision_notifier.py tests/test_operator_home.py
git commit -m "feat: shape gate quality for operator hub"
```

---

## Task 5: Instrument autonomy/convergence health and finish the North Star scorecard

**Files:**
- Modify: `driftdriver/hub_analytics.py`
- Modify: `driftdriver/ecosystem_hub/operator_home.py`
- Modify: `driftdriver/ecosystem_hub/dashboard.py`
- Modify: `tests/test_operator_home.py`
- Modify: `tests/test_ecosystem_hub.py`

- [ ] **Step 1: Add failing tests for the full scorecard and domain summaries**

```python
# tests/test_operator_home.py
def test_scorecard_reports_autonomy_and_convergence_fields() -> None:
    snapshot = {
        "repos": [],
        "northstardrift": {"summary": {"overall_score": 61.0, "overall_trend": "improving", "overall_tier": "healthy"}},
        "overview": {},
    }
    ledger = [
        {"decision_id": "dec-1", "delivery_status": "sent", "route": "digest", "provenance": {}},
        {"decision_id": "dec-2", "delivery_status": "autonomous_closed", "route": "digest", "provenance": {}},
    ]
    payload = build_operator_home(snapshot=snapshot, decisions=[], notification_ledger=ledger)
    assert payload["scorecard"]["autonomous_this_week"] == 1
    assert payload["scorecard"]["convergence_trend"] == "improving"
    assert "control_plane" in payload["domains"]
    assert "autonomy" in payload["domains"]
    assert "convergence" in payload["domains"]


# tests/test_ecosystem_hub.py
def test_dashboard_template_contains_factory_status_scorecard_labels(self) -> None:
    html = render_dashboard_html()
    self.assertIn("Needs You", html)
    self.assertIn("Autonomous This Week", html)
    self.assertIn("Convergence Trend", html)
    self.assertIn("Confidence", html)
```

- [ ] **Step 2: Run the scorecard tests and confirm the fields are incomplete**

```bash
cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_operator_home.py tests/test_ecosystem_hub.py -q -k 'autonomy_and_convergence_fields or factory_status_scorecard_labels'
```

Expected: missing-field assertions fail.

- [ ] **Step 3: Implement the domain metrics and render them in the scorecard**

```python
# driftdriver/hub_analytics.py
def count_autonomous_closures(notification_ledger: list[dict[str, Any]]) -> int:
    return sum(1 for row in notification_ledger if str(row.get("delivery_status") or "") == "autonomous_closed")


def build_convergence_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    summary = ((snapshot.get("northstardrift") or {}).get("summary") or {})
    return {
        "score": float(summary.get("overall_score") or 0.0),
        "trend": str(summary.get("overall_trend") or "flat"),
        "tier": str(summary.get("overall_tier") or "watch"),
    }
```

```javascript
// driftdriver/ecosystem_hub/dashboard.py
function renderOperatorScorecard(scorecard, domains) {
  el('operator-scorecard').innerHTML =
    '<div class="intel-stat"><div class="intel-stat-label">Factory Status</div><div class="intel-stat-value">' + esc(scorecard.status) + '</div></div>' +
    '<div class="intel-stat"><div class="intel-stat-label">Needs You</div><div class="intel-stat-value">' + n(scorecard.needs_you) + '</div></div>' +
    '<div class="intel-stat"><div class="intel-stat-label">Autonomous This Week</div><div class="intel-stat-value">' + n(scorecard.autonomous_this_week) + '</div></div>' +
    '<div class="intel-stat"><div class="intel-stat-label">Convergence Trend</div><div class="intel-stat-value">' + esc(scorecard.convergence_trend) + '</div></div>' +
    '<div class="intel-stat"><div class="intel-stat-label">Confidence</div><div class="intel-stat-value">' + esc(scorecard.confidence) + '</div></div>' +
    '<p class="operator-why">' + esc(scorecard.why) + '</p>';
}
```

- [ ] **Step 4: Run the full targeted verification for operator-home**

```bash
PYTHONPATH=$PWD pytest tests/test_operator_home.py tests/test_ecosystem_hub.py tests/test_decision_notifier.py -q -k 'operator_home or factory_status or api_operator_home or dashboard_template'
node --check /tmp/hub-dashboard.js
```

Expected: targeted pytest set passes and generated dashboard JavaScript parses cleanly.

- [ ] **Step 5: Commit the completed scorecard/instrumentation slice**

```bash
git add driftdriver/hub_analytics.py driftdriver/ecosystem_hub/operator_home.py driftdriver/ecosystem_hub/dashboard.py tests/test_operator_home.py tests/test_ecosystem_hub.py
git commit -m "feat: add north-star operator scorecard"
```

---

## Final Verification

- [ ] **Step 1: Run the complete focused test suite for the operator-hub slice**

```bash
cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_operator_home.py tests/test_ecosystem_hub.py tests/test_decision_notifier.py -q
```

Expected: all operator-home, dashboard, API, and notifier tests pass.

- [ ] **Step 2: Restart the live hub and sanity-check the operator surface**

```bash
cd /Users/braydon/projects/experiments/driftdriver
scripts/ecosystem_hub_daemon.sh restart
curl -fsS http://127.0.0.1:8777/api/operator/home | jq '.scorecard, .counts'
```

Expected: JSON payload with `scorecard`, `now`, `decide`, `watch`, and non-null count fields.

- [ ] **Step 3: Confirm dashboard HTML still parses after the final template change**

```bash
PYTHONPATH=$PWD python - <<'PY'
from pathlib import Path
from driftdriver.ecosystem_hub.dashboard import render_dashboard_html
html = render_dashboard_html()
start = html.index("<script>") + len("<script>")
end = html.rindex("</script>")
Path("/tmp/hub-dashboard.js").write_text(html[start:end], encoding="utf-8")
print("/tmp/hub-dashboard.js")
PY
node --check /tmp/hub-dashboard.js
```

Expected: `node --check` exits `0`.

- [ ] **Step 4: Push the completed work**

```bash
git pull --rebase
git push
git status -sb
```

Expected: `## main...origin/main`
