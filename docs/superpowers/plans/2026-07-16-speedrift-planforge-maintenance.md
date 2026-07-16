# PlanForge Speedrift Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Speedrift owns task state and dependencies; use fresh Pi Lunaroute implementation and review contexts. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PlanForge emit canonical handler-first execution contracts, consume driftdriver's graph-location decision, and generate native Workgraph Validation and Deliverables sections.

**Architecture:** PlanForge continues to use dictionary-based JSON plans and JSON Schema validation. A normalization pass converts legacy route fields into canonical `route_id` and `execution_spec` fields before policy validation or materialization. PlanForge obtains the graph directory from `driftdriver --dir "$PWD" --json graph-dir` and passes it to every Workgraph command.

**Tech Stack:** Python 3.12, standard library, `jsonschema`, `unittest`, JSON Schema Draft 2020-12, Workgraph CLI, driftdriver CLI.

## Global Constraints

- `execution_spec` is a string, not an object.
- Canonical Pi values begin with `pi:`.
- `route_id` remains semantic and must not carry executor/provider inference.
- Legacy `selected_route`, `selected_model`, `fallback_route`, and `fallback_model` remain readable during the compatibility window.
- A bare legacy model such as `lunaroute/glm-5.2-nvfp4` may become `pi:lunaroute/glm-5.2-nvfp4` only when the plan's explicit `agency_plan.default_runtime` is `pi`.
- Canonical and legacy values that disagree are errors.
- Every generated Workgraph command contains `--dir "$GRAPH_DIR"`, where `GRAPH_DIR` is the absolute resolver result.
- `## Validation` is always emitted.
- `## Deliverables` is emitted only for a non-empty explicit `deliverables` list.
- `touch_set` is never treated as promised output.
- External Agency remains composition metadata, not an executor.

---

### Task 1: Canonical routing schema and compatibility normalizer

**Files:**
- Modify: `skills/planforge/schemas/planforge-plan-v2.schema.json`
- Modify: `skills/planforge/scripts/planforge_v2.py`
- Modify: `skills/planforge/tests/test_planforge_v2.py`

**Interfaces:**
- Adds schema fields: `route_id`, `execution_spec`, `fallback_route_id`, `fallback_execution_spec`.
- Preserves schema fields: `selected_route`, `selected_model`, `fallback_route`, `fallback_model` as legacy aliases.
- Produces: `normalize_plan(plan: dict[str, Any]) -> dict[str, Any]`.
- Produces: `normalize_model_routing(routing: dict[str, Any], *, default_runtime: str, node_label: str) -> dict[str, Any]`.
- Changes: `validate_plan(plan: dict[str, Any], schema_path: Path = SCHEMA_PATH) -> dict[str, Any]` returns the canonical normalized plan.

- [ ] **Step 1: Extend the sample plan with canonical fields and add failing tests**

Add tests inside `PlanForgeV2Tests`:

```python
def test_normalize_plan_accepts_canonical_route_and_execution_spec(self):
    plan = sample_plan()
    routing = plan["workgraph"]["nodes"][0]["routing"]
    routing["route_id"] = routing.pop("selected_route")
    routing["execution_spec"] = "pi:lunaroute/glm-5.2-nvfp4"
    routing.pop("selected_model", None)
    normalized = MODULE.validate_plan(plan)
    actual = normalized["workgraph"]["nodes"][0]["routing"]
    self.assertEqual(actual["route_id"], routing["route_id"])
    self.assertEqual(actual["execution_spec"], "pi:lunaroute/glm-5.2-nvfp4")


def test_normalize_plan_maps_legacy_pi_model(self):
    plan = sample_plan()
    plan["agency_plan"]["default_runtime"] = "pi"
    routing = plan["workgraph"]["nodes"][0]["routing"]
    routing["selected_model"] = "lunaroute/glm-5.2-nvfp4"
    normalized = MODULE.validate_plan(plan)
    actual = normalized["workgraph"]["nodes"][0]["routing"]
    self.assertEqual(actual["execution_spec"], "pi:lunaroute/glm-5.2-nvfp4")


def test_normalize_plan_rejects_conflicting_canonical_and_legacy_values(self):
    plan = sample_plan()
    routing = plan["workgraph"]["nodes"][0]["routing"]
    routing["route_id"] = "standard"
    routing["selected_route"] = "premium"
    with self.assertRaisesRegex(MODULE.PlanForgeError, "route_id.*selected_route"):
        MODULE.validate_plan(plan)


def test_normalize_plan_rejects_bare_canonical_execution_spec(self):
    plan = sample_plan()
    routing = plan["workgraph"]["nodes"][0]["routing"]
    routing["route_id"] = routing.pop("selected_route")
    routing["execution_spec"] = "lunaroute/glm-5.2-nvfp4"
    routing.pop("selected_model", None)
    with self.assertRaisesRegex(MODULE.PlanForgeError, "handler-first"):
        MODULE.validate_plan(plan)


def test_normalize_plan_rejects_cross_handler_fallback(self):
    plan = sample_plan()
    routing = plan["workgraph"]["nodes"][0]["routing"]
    routing["route_id"] = routing.pop("selected_route")
    routing["execution_spec"] = "pi:lunaroute/glm-5.2-nvfp4"
    routing["fallback_execution_spec"] = "claude:haiku"
    routing.pop("selected_model", None)
    with self.assertRaisesRegex(MODULE.PlanForgeError, "same handler"):
        MODULE.validate_plan(plan)
```

- [ ] **Step 2: Run and verify failure**

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
```

Expected: `validate_plan` returns `None`; canonical fields are rejected by the schema.

- [ ] **Step 3: Update the schema**

Within `$defs.model_routing`:

- Remove `selected_route` and `fallback_route` from the unconditional `required` list.
- Add optional canonical string properties with `minLength: 1`.
- Add an `allOf` rule requiring either `route_id` or `selected_route`.
- Add an `allOf` rule requiring either `execution_spec` or `selected_model`.
- Keep `additionalProperties: false`.

Use a handler-first pattern for canonical execution strings:

```json
{
  "type": "string",
  "pattern": "^[a-z][a-z0-9_-]*:.+$",
  "minLength": 3
}
```

- [ ] **Step 4: Implement normalization without hidden provider inference**

Add:

```python
HANDLER_FIRST_RE = re.compile(r"^[a-z][a-z0-9_-]*:.+$")


def _coalesce_legacy(
    *,
    canonical_name: str,
    canonical_value: str | None,
    legacy_name: str,
    legacy_value: str | None,
    normalized_legacy_value: str | None,
    node_label: str,
) -> str | None:
    if canonical_value and legacy_value and canonical_value != normalized_legacy_value:
        raise PlanForgeError(
            f"{node_label}: conflicting {canonical_name}={canonical_value!r} "
            f"and {legacy_name}={legacy_value!r}"
        )
    return canonical_value or normalized_legacy_value


def normalize_model_routing(
    routing: dict[str, Any],
    *,
    default_runtime: str,
    node_label: str,
) -> dict[str, Any]:
    normalized = dict(routing)
    legacy_model = str(routing.get("selected_model") or "") or None
    legacy_execution = legacy_model
    if legacy_model and not HANDLER_FIRST_RE.fullmatch(legacy_model):
        if default_runtime != "pi":
            raise PlanForgeError(
                f"{node_label}: bare selected_model requires explicit Pi runtime; "
                "use handler-first execution_spec"
            )
        legacy_execution = f"pi:{legacy_model}"
    normalized["route_id"] = _coalesce_legacy(
        canonical_name="route_id",
        canonical_value=routing.get("route_id"),
        legacy_name="selected_route",
        legacy_value=routing.get("selected_route"),
        normalized_legacy_value=routing.get("selected_route"),
        node_label=node_label,
    )
    normalized["execution_spec"] = _coalesce_legacy(
        canonical_name="execution_spec",
        canonical_value=routing.get("execution_spec"),
        legacy_name="selected_model",
        legacy_value=legacy_model,
        normalized_legacy_value=legacy_execution,
        node_label=node_label,
    )
    execution_spec = str(normalized.get("execution_spec") or "")
    if not HANDLER_FIRST_RE.fullmatch(execution_spec):
        raise PlanForgeError(
            f"{node_label}: execution_spec must be handler-first, for example "
            "pi:lunaroute/glm-5.2-nvfp4"
        )
    if default_runtime == "pi" and not execution_spec.startswith("pi:"):
        raise PlanForgeError(f"{node_label}: Pi-routed execution_spec must begin with pi:")
    fallback_spec = str(normalized.get("fallback_execution_spec") or "")
    if fallback_spec and fallback_spec.split(":", 1)[0] != execution_spec.split(":", 1)[0]:
        raise PlanForgeError(f"{node_label}: fallback_execution_spec must use the same handler")
    return normalized
```

`normalize_plan` must deep-copy the plan, normalize every node using `agency_plan.default_runtime`, and normalize fallback aliases using the same conflict rule. `validate_plan` must normalize first, validate the canonical copy, run `validate_model_routing` on that copy, and return it.

Update every call that later writes or materializes the plan to use the returned normalized value.

- [ ] **Step 5: Update routing-policy error text**

Change `validate_model_routing` diagnostics from `selected_route=` to `route_id=` and read the canonical field.

- [ ] **Step 6: Run tests**

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add \
  skills/planforge/schemas/planforge-plan-v2.schema.json \
  skills/planforge/scripts/planforge_v2.py \
  skills/planforge/tests/test_planforge_v2.py
git commit -m "feat(planforge): canonicalize handler-first execution specs"
```

---

### Task 2: Native Validation and conditional Deliverables sections

**Files:**
- Modify: `skills/planforge/schemas/planforge-plan-v2.schema.json`
- Modify: `skills/planforge/scripts/planforge_v2.py`
- Modify: `skills/planforge/tests/test_planforge_v2.py`

**Interfaces:**
- Adds: `workgraph_node.deliverables: list[str]`, optional and defaulted by code to `[]`.
- Changes: `node_description` reads canonical routing fields.
- Produces literal Markdown `## Validation` for every node.
- Produces literal Markdown `## Deliverables` only when `deliverables` is non-empty.

- [ ] **Step 1: Add failing rendering tests**

```python
def test_node_description_always_emits_validation(self):
    plan = MODULE.validate_plan(sample_plan())
    node = plan["workgraph"]["nodes"][0]
    text = MODULE.node_description(node, Path("spec.md"), plan["agency_plan"])
    self.assertIn("## Validation\n", text)
    for command in node["verify"]:
        self.assertIn(f"- {command}", text)


def test_node_description_omits_deliverables_when_empty(self):
    plan = MODULE.validate_plan(sample_plan())
    node = plan["workgraph"]["nodes"][0]
    node.pop("deliverables", None)
    text = MODULE.node_description(node, Path("spec.md"), plan["agency_plan"])
    self.assertNotIn("## Deliverables\n", text)


def test_node_description_emits_explicit_deliverables(self):
    plan = MODULE.validate_plan(sample_plan())
    node = plan["workgraph"]["nodes"][0]
    node["deliverables"] = ["src/example.py", "tests/test_example.py"]
    text = MODULE.node_description(node, Path("spec.md"), plan["agency_plan"])
    self.assertIn("## Deliverables\n", text)
    self.assertIn("- src/example.py", text)
    self.assertIn("- tests/test_example.py", text)
```

- [ ] **Step 2: Verify failure**

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
```

Expected: the schema rejects `deliverables` and rendered descriptions lack both literal headings.

- [ ] **Step 3: Add the schema field**

Add to `$defs.workgraph_node.properties`:

```json
"deliverables": {
  "type": "array",
  "items": {"type": "string", "minLength": 1},
  "uniqueItems": true
}
```

Do not add it to `required`.

- [ ] **Step 4: Render the sections**

At the end of the existing narrative portion of `node_description`, append:

```python
validation = markdown_list(verify).rstrip()
deliverables = markdown_list(node.get("deliverables", [])).rstrip()
contract_sections = f"\n\n## Validation\n{validation}"
if deliverables:
    contract_sections += f"\n\n## Deliverables\n{deliverables}"
```

Return the existing description plus `contract_sections`. Update the `wg-contract` fields to use `route_id` and `execution_spec`. Remove `preferred_runtime = "agency"` semantics; external Agency may remain profile metadata but is not an executor.

- [ ] **Step 5: Run tests and commit**

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
git add \
  skills/planforge/schemas/planforge-plan-v2.schema.json \
  skills/planforge/scripts/planforge_v2.py \
  skills/planforge/tests/test_planforge_v2.py
git commit -m "feat(planforge): emit Workgraph validation and deliverables"
```

---

### Task 3: Consume driftdriver graph-dir and pass explicit Workgraph directories

**Files:**
- Modify: `skills/planforge/scripts/planforge_v2.py`
- Modify: `skills/planforge/tests/test_planforge_v2.py`

**Interfaces:**
- Consumes CLI from driftdriver plan Task 2: `driftdriver --dir "$PWD" --json graph-dir`.
- Produces: `GraphResolution(path: Path, initialized: bool, source: str)`.
- Produces: `resolve_graph_dir(repo: Path) -> GraphResolution`.
- Changes: `session_dir(repo: Path, session: str, graph_dir: Path) -> Path`.
- Changes: `workgraph_commands(plan, spec_path, graph_dir) -> list[list[str]]`.

- [ ] **Step 1: Add failing resolver and command tests**

```python
def test_resolve_graph_dir_parses_driftdriver_json(self):
    completed = {
        "command": [],
        "returncode": 0,
        "stdout": json.dumps({
            "project_dir": "/repo",
            "graph_dir": "/repo/.wg",
            "initialized": True,
            "source": "current",
        }),
        "stderr": "",
    }
    with unittest.mock.patch.object(MODULE, "run_command", return_value=completed):
        result = MODULE.resolve_graph_dir(Path("/repo"))
    self.assertEqual(result.path, Path("/repo/.wg"))
    self.assertTrue(result.initialized)


def test_workgraph_commands_always_include_resolved_dir(self):
    plan = MODULE.validate_plan(sample_plan())
    commands = MODULE.workgraph_commands(
        plan,
        Path("spec.md"),
        Path("/repo/.wg"),
    )
    self.assertEqual(commands[0][:3], ["wg", "--dir", "/repo/.wg"])
    self.assertIn("pi:", commands[0][commands[0].index("--model") + 1])
```

Add a failure test for invalid JSON/non-zero driftdriver results that expects a `PlanForgeError` naming `driftdriver graph-dir`.

- [ ] **Step 2: Verify failure**

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
```

Expected: missing `GraphResolution` and current commands begin with `wg add` without `--dir`.

- [ ] **Step 3: Implement the resolver**

```python
@dataclass(frozen=True)
class GraphResolution:
    path: Path
    initialized: bool
    source: str


def resolve_graph_dir(repo: Path) -> GraphResolution:
    result = run_command(
        ["driftdriver", "--dir", str(repo), "--json", "graph-dir"],
        repo,
        timeout=30,
        max_chars=20000,
    )
    if result["returncode"] != 0:
        raise PlanForgeError(
            "driftdriver graph-dir failed: "
            + (result["stderr"] or result["stdout"] or "unknown error")
        )
    try:
        payload = json.loads(result["stdout"])
        return GraphResolution(
            path=Path(payload["graph_dir"]),
            initialized=bool(payload["initialized"]),
            source=str(payload["source"]),
        )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise PlanForgeError("driftdriver graph-dir returned invalid JSON") from exc
```

Import `dataclass` from `dataclasses`.

- [ ] **Step 4: Thread the graph directory through PlanForge**

- `collect_evidence` resolves once and runs `wg --dir "$GRAPH_DIR" status` and `wg --dir "$GRAPH_DIR" ready`.
- `session_dir` writes under `Path(graph_dir) / "planforge" / session`.
- `workgraph_commands` starts every command with `wg --dir "$GRAPH_DIR" add`.
- `materialize_plan` resolves once, uses the canonical normalized plan, and records `graph_dir`, `graph_initialized`, and `graph_source` in its returned manifest.
- When `apply=True` and the graph is not initialized, run `wg --dir "$GRAPH_DIR" init` before the add commands.
- Do not pass `--model` to `wg init`.

- [ ] **Step 5: Run tests and commit**

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
git add skills/planforge/scripts/planforge_v2.py skills/planforge/tests/test_planforge_v2.py
git commit -m "fix(planforge): share Speedrift graph location"
```

---

### Task 4: Update PlanForge and Speedrift guidance

**Files:**
- Modify: `skills/planforge/references/planforge-v2-flow.md`
- Modify: `skills/planforge/prompts/planforge-v2-persona.md`
- Modify: `skills/planforge/SKILL.md`
- Modify: `skills/speedrift/SKILL.md`
- Modify: `skills/planforge/tests/test_planforge_v2.py`

**Interfaces:**
- Documents canonical `route_id` and `execution_spec`.
- Documents explicit `wg --dir` and `speedriftd` authority.
- Documents literal Validation and conditional Deliverables sections.
- Marks old routing fields as compatibility aliases.
- Removes external Agency from executor/runtime guidance.

- [ ] **Step 1: Add failing documentation-contract tests**

Read each document and assert the canonical phrases exist:

```python
self.assertIn("route_id", flow_text)
self.assertIn("execution_spec", flow_text)
self.assertIn("pi:lunaroute/glm-5.2-nvfp4", persona_text)
self.assertIn("## Validation", planforge_skill_text)
self.assertIn("## Deliverables", planforge_skill_text)
self.assertIn("speedriftd", speedrift_skill_text)
self.assertNotIn("Use `wg service start` to dispatch work", speedrift_skill_text)
```

Also assert legacy field names appear only in an explicitly labelled compatibility section.

- [ ] **Step 2: Verify failure**

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
```

Expected: current documents still present `selected_route`, `selected_model`, direct Workgraph defaults, or Agency runtime language as canonical.

- [ ] **Step 3: Update the documents**

Every canonical example must use a handler-first value:

```text
route_id = standard
execution_spec = pi:lunaroute/glm-5.2-nvfp4
```

State plainly that:

- `route_id` chooses policy semantics.
- `execution_spec` chooses the Workgraph handler and model.
- PlanForge obtains the graph directory from driftdriver.
- `speedriftd` owns supervision.
- External Agency composes prompts only when explicitly enabled and does not execute tasks.
- `selected_route`, `selected_model`, `fallback_route`, and `fallback_model` are temporary input aliases.

Remove stale `--blocked-by` examples, stale runner paths, and unscoped `wg service start` instructions from the touched documents.

- [ ] **Step 4: Run tests and commit**

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
git add \
  skills/planforge/references/planforge-v2-flow.md \
  skills/planforge/prompts/planforge-v2-persona.md \
  skills/planforge/SKILL.md \
  skills/speedrift/SKILL.md \
  skills/planforge/tests/test_planforge_v2.py
git commit -m "docs(speedrift): align PlanForge with current runtime contracts"
```

---

### Task 5: PlanForge regression and review gate

**Files:**
- No product files unless a failing regression identifies a defect introduced by Tasks 1-4.

- [ ] **Step 1: Run the focused test file**

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
```

Expected: PASS.

- [ ] **Step 2: Run a dry-run materialization**

Use a temporary repository with the updated driftdriver installed. Validate that `materialize.commands.json` contains:

```json
{
  "argv": [
    "wg",
    "--dir",
    "/absolute/temp/repo/.workgraph",
    "add",
    "--id",
    "...",
    "...",
    "--description",
    "...## Validation...",
    "--model",
    "pi:lunaroute/glm-5.2-nvfp4"
  ]
}
```

Expected: every add command has one explicit `--dir`; no add command has `--executor`.

- [ ] **Step 3: Request Pi Lunaroute reviews**

A requirements reviewer compares the diff to `docs/superpowers/specs/2026-07-16-speedrift-maintenance-design.md`. A separate quality reviewer checks schema compatibility, normalization conflicts, subprocess errors, and test isolation.

- [ ] **Step 4: Push the PlanForge branch**

```bash
git status --short
git push -u origin speedrift-maintenance-20260716
```

Expected: only PlanForge/Speedrift files from this plan are committed; the original workspace-skills working tree remains untouched.
