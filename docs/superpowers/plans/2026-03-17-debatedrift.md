# debatedrift Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `debatedrift` — a speedrift lane that launches a three-agent tmux debate session (Debater A, Debater B, Proxy) for tasks tagged with a `debatedrift` fenced TOML block.

**Architecture:** A Python package at `driftdriver/driftdriver/debatedrift/` containing config parsing, prompt generation, tmux session launching, background log aggregation, and a `run_as_lane()` interface. A `driftdriver debate` CLI subcommand provides explicit control. The lane is also registered as an internal lane in `check.py` so fence detection works automatically.

**Tech Stack:** Python 3.11+, tmux (system), `ts` from moreutils (optional), `wg` CLI, `subprocess`, `pathlib`, existing `LaneResult` / `LaneFinding` from `speedrift-lane-sdk`.

**Spec:** `docs/superpowers/specs/2026-03-17-debatedrift-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `driftdriver/driftdriver/debatedrift/__init__.py` | Create | Package re-exports |
| `driftdriver/driftdriver/debatedrift/config.py` | Create | Parse `debatedrift` TOML fence from task description |
| `driftdriver/driftdriver/debatedrift/prompts.py` | Create | Generate agent system prompts (debater-a, debater-b, proxy) |
| `driftdriver/driftdriver/debatedrift/session.py` | Create | tmux session launch + pipe-pane wiring |
| `driftdriver/driftdriver/debatedrift/aggregator.py` | Create | Background log merge + watchdog + sentinel detection |
| `driftdriver/driftdriver/debatedrift/lane.py` | Create | `run_as_lane()` LaneResult interface |
| `driftdriver/driftdriver/debatedrift/proxy-constitution.md` | Create | Hand-authored proxy judgment document |
| `driftdriver/driftdriver/cli/debate_cmd.py` | Create | `driftdriver debate start/status/conclude` subcommand |
| `driftdriver/driftdriver/cli/__init__.py` | Modify | Register `debate` subparser in `_build_parser()` |
| `driftdriver/driftdriver/cli/check.py` | Modify | Add `debatedrift` to `INTERNAL_LANES` |
| `driftdriver/driftdriver/install.py` | Modify | Add `write_debatedrift_wrapper()`, `ensure_debatedrift_gitignore()` |
| `driftdriver/driftdriver/cli/install_cmd.py` | Modify | Wire debatedrift into `cmd_install()` |
| `driftdriver/tests/test_debatedrift_config.py` | Create | Config parsing tests |
| `driftdriver/tests/test_debatedrift_prompts.py` | Create | Prompt generation tests |
| `driftdriver/tests/test_debatedrift_aggregator.py` | Create | Aggregator sentinel + round counting tests |
| `driftdriver/tests/test_debatedrift_lane.py` | Create | Lane interface + install integration tests |

---

## Task 1: Proxy Constitution

**Files:**
- Create: `driftdriver/driftdriver/debatedrift/proxy-constitution.md`

No tests needed — this is a hand-authored document.

- [ ] **Step 1: Create the package directory**

```bash
mkdir -p /Users/braydon/projects/experiments/driftdriver/driftdriver/debatedrift
```

- [ ] **Step 2: Write the proxy constitution**

Create `driftdriver/driftdriver/debatedrift/proxy-constitution.md`:

```markdown
# Proxy Constitution — debatedrift

This document governs how the Proxy agent makes final decisions in debatedrift sessions.
The Proxy is NOT Claude's general assistant. It is Braydon's distilled judgment.

---

## Decision Principles

**YAGNI by default.** When in doubt, pick the simpler answer. The clever solution must
earn its complexity. If both debaters agree on something complex, still ask: is there a
simpler path that gets 80% of the value?

**Simplicity-first.** Code that doesn't exist can't break. Abstractions have a maintenance
cost that must be paid by the team. Resist the urge to over-engineer for hypothetical futures.

**Good enough is a real state.** "Good enough" means: solves the problem, doesn't create
new problems, can be changed later without major surgery. It is a valid end state.

**Bias toward reversibility.** When choosing between two solutions of similar quality,
prefer the one that's easier to undo or modify. Lock in as little as possible.

---

## Judgment Heuristics

**When to call it:** The debate has converged when the debaters are refining details
rather than challenging fundamentals. Circular arguments (returning to the same ground)
are a signal: call it and pick the better-defended position.

**When a contrarian view is worth pursuing:** When Debater B identifies a failure mode
that Debater A's proposal doesn't address. When the contrarian view is grounded in a
real constraint (not hypothetical). When B's alternative is actually simpler.

**When a contrarian view is noise:** When B is being contrary for its own sake without
a concrete alternative. When the objection is to a detail, not the approach. When B
has raised the same point more than once without new evidence.

**Breaking ties:** If genuinely equal, prefer the option that a junior developer could
understand and maintain without asking questions. Clarity beats cleverness.

**On deadlock:** A real deadlock means the problem is underspecified. Create a follow-up
task to sharpen the spec, not another debate session.

---

## Task-Type Overlays

### planning
Risk tolerance: medium. Speed matters. A good plan shipped today beats a perfect plan
next week. Optimize for: can we start building? Does the plan decompose into clear tasks?

Decide: which decomposition is cleaner? Which has fewer cross-cutting dependencies?

### troubleshoot
Risk tolerance: low. Do not pick a fix that could introduce new breakage. Prefer the
narrowest surgical change. If both fixes are narrow, pick the one with a clearer test.

Decide: which fix is more targeted? Which has a more convincing test?

### usecase
Risk tolerance: medium-high. Use cases are about discovery. A use case that exposes a
gap is more valuable than one that confirms the happy path. Prefer the more challenging
interpretation.

Decide: which framing reveals more about system behavior? Which is more honest about
edge cases?

---

## Escalation Rules

The Proxy **never** decides these alone — always escalates to real Braydon:

1. **Architecture changes** that affect more than 3 repos or services
2. **Security decisions** of any kind
3. **Irreversible data operations** (migrations, deletes, schema changes)
4. **Budget or resource commitments** (API costs, infrastructure changes)
5. **Anything the debaters explicitly flag as "needs human"**
6. **Genuine deadlock** after the full round cap — don't guess, escalate

---

## Refinement Protocol

When Braydon would have decided differently than the Proxy did:
1. Note what decision the Proxy made
2. Note what decision Braydon would have made
3. Identify which principle or heuristic the Proxy misapplied
4. Update the relevant section above

The constitution gets sharper with each correction. Corrections are not failures — they
are the primary mechanism of improvement.
```

- [ ] **Step 3: Create package `__init__.py`**

Create `driftdriver/driftdriver/debatedrift/__init__.py`:

```python
# ABOUTME: debatedrift — three-agent tmux debate lane for speedrift.
# ABOUTME: Exposes run_as_lane() for the internal lane interface and session management.

from driftdriver.debatedrift.lane import run_as_lane

__all__ = ["run_as_lane"]
```

- [ ] **Step 4: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/debatedrift/
git commit -m "feat: scaffold debatedrift package + proxy constitution"
```

---

## Task 2: Config Parser

**Files:**
- Create: `driftdriver/driftdriver/debatedrift/config.py`
- Create: `driftdriver/tests/test_debatedrift_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_debatedrift_config.py`:

```python
# ABOUTME: Tests for debatedrift TOML fence config parser.
# ABOUTME: Verifies fence extraction, defaults, and validation.
from __future__ import annotations

import pytest
from driftdriver.debatedrift.config import DebateDriftConfig, parse_debatedrift_config


class TestParseFence:
    def test_extracts_type_and_defaults(self) -> None:
        desc = (
            "Do the thing.\n\n"
            "```debatedrift\n"
            "schema = 1\n"
            "type = \"planning\"\n"
            "```\n"
        )
        cfg = parse_debatedrift_config(desc)
        assert cfg is not None
        assert cfg.type == "planning"
        assert cfg.max_rounds == 5
        assert cfg.watchdog_timeout == 90
        assert cfg.context_files == []

    def test_extracts_all_fields(self) -> None:
        desc = (
            "```debatedrift\n"
            "schema = 1\n"
            "type = \"troubleshoot\"\n"
            "max_rounds = 3\n"
            "watchdog_timeout = 60\n"
            "context_files = [\"src/foo.py\", \"README.md\"]\n"
            "```\n"
        )
        cfg = parse_debatedrift_config(desc)
        assert cfg is not None
        assert cfg.type == "troubleshoot"
        assert cfg.max_rounds == 3
        assert cfg.watchdog_timeout == 60
        assert cfg.context_files == ["src/foo.py", "README.md"]

    def test_returns_none_when_no_fence(self) -> None:
        assert parse_debatedrift_config("just a regular task description") is None

    def test_invalid_type_raises(self) -> None:
        desc = "```debatedrift\nschema = 1\ntype = \"invalid\"\n```\n"
        with pytest.raises(ValueError, match="type"):
            parse_debatedrift_config(desc)

    def test_usecase_type_accepted(self) -> None:
        desc = "```debatedrift\nschema = 1\ntype = \"usecase\"\n```\n"
        cfg = parse_debatedrift_config(desc)
        assert cfg is not None
        assert cfg.type == "usecase"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m pytest tests/test_debatedrift_config.py -v 2>&1 | tail -20
```

Expected: `ModuleNotFoundError` or `ImportError` — module doesn't exist yet.

- [ ] **Step 3: Implement config.py**

Create `driftdriver/driftdriver/debatedrift/config.py`:

```python
# ABOUTME: Parse debatedrift fenced TOML block from workgraph task descriptions.
# ABOUTME: Returns DebateDriftConfig or None if no fence present.
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field


_VALID_TYPES = {"planning", "troubleshoot", "usecase"}
_FENCE_RE = re.compile(r"```debatedrift\n(.*?)```", re.DOTALL)


@dataclass
class DebateDriftConfig:
    type: str
    max_rounds: int = 5
    watchdog_timeout: int = 90
    context_files: list[str] = field(default_factory=list)


def parse_debatedrift_config(description: str) -> DebateDriftConfig | None:
    """Extract and parse a debatedrift fenced TOML block from a task description.

    Returns None if no fence is present. Raises ValueError for invalid values.
    """
    match = _FENCE_RE.search(description)
    if not match:
        return None

    raw = match.group(1)
    try:
        data = tomllib.loads(raw)
    except Exception as exc:
        raise ValueError(f"debatedrift fence is not valid TOML: {exc}") from exc

    debate_type = str(data.get("type", "")).strip()
    if debate_type not in _VALID_TYPES:
        raise ValueError(
            f"debatedrift type={debate_type!r} is not valid; "
            f"must be one of {sorted(_VALID_TYPES)}"
        )

    context_files_raw = data.get("context_files", [])
    context_files = [str(f) for f in context_files_raw] if isinstance(context_files_raw, list) else []

    return DebateDriftConfig(
        type=debate_type,
        max_rounds=max(1, int(data.get("max_rounds", 5))),
        watchdog_timeout=max(10, int(data.get("watchdog_timeout", 90))),
        context_files=context_files,
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m pytest tests/test_debatedrift_config.py -v 2>&1 | tail -10
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/debatedrift/config.py tests/test_debatedrift_config.py
git commit -m "feat: add debatedrift config parser with TOML fence extraction"
```

---

## Task 3: Agent Prompts

**Files:**
- Create: `driftdriver/driftdriver/debatedrift/prompts.py`
- Create: `driftdriver/tests/test_debatedrift_prompts.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_debatedrift_prompts.py`:

```python
# ABOUTME: Tests for debatedrift agent prompt generation.
# ABOUTME: Verifies sentinel instructions, round protocol, and proxy constitution path.
from __future__ import annotations

from pathlib import Path
from driftdriver.debatedrift.prompts import (
    debater_a_prompt,
    debater_b_prompt,
    proxy_prompt,
)


class TestDebaterAPrompt:
    def test_contains_topic(self) -> None:
        p = debater_a_prompt(topic="cache invalidation strategy", task_id="t1",
                              max_rounds=5, context_files=[])
        assert "cache invalidation strategy" in p

    def test_contains_round_end_sentinel(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="t1", max_rounds=5, context_files=[])
        assert "[ROUND:END]" in p

    def test_contains_wg_msg_instruction(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="t1", max_rounds=5, context_files=[])
        assert "wg msg list" in p

    def test_contains_task_id(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="my-task-123", max_rounds=5, context_files=[])
        assert "my-task-123" in p

    def test_mentions_proxy_presence(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="t1", max_rounds=5, context_files=[])
        assert "proxy" in p.lower()

    def test_includes_context_files(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="t1", max_rounds=5,
                              context_files=["src/main.py", "README.md"])
        assert "src/main.py" in p
        assert "README.md" in p


class TestDebaterBPrompt:
    def test_diverges_from_a(self) -> None:
        pa = debater_a_prompt(topic="x", task_id="t", max_rounds=5, context_files=[])
        pb = debater_b_prompt(topic="x", task_id="t", max_rounds=5, context_files=[])
        # B's core instruction should differ — B is the contrarian
        assert pa != pb
        assert "contrarian" in pb.lower() or "challenge" in pb.lower() or "diverge" in pb.lower()

    def test_contains_round_end_sentinel(self) -> None:
        p = debater_b_prompt(topic="foo", task_id="t1", max_rounds=5, context_files=[])
        assert "[ROUND:END]" in p


class TestProxyPrompt:
    def test_contains_topic(self) -> None:
        p = proxy_prompt(topic="routing strategy", task_id="t1",
                         context_files=[], constitution_path=Path("/tmp/const.md"))
        assert "routing strategy" in p

    def test_contains_concluded_sentinel(self) -> None:
        p = proxy_prompt(topic="foo", task_id="t1",
                         context_files=[], constitution_path=Path("/tmp/const.md"))
        assert "DEBATE:CONCLUDED" in p

    def test_contains_deadlock_sentinel(self) -> None:
        p = proxy_prompt(topic="foo", task_id="t1",
                         context_files=[], constitution_path=Path("/tmp/const.md"))
        assert "DEBATE:DEADLOCK" in p

    def test_proxy_does_not_mention_round_end(self) -> None:
        # Proxy doesn't participate in rounds — no [ROUND:END]
        p = proxy_prompt(topic="foo", task_id="t1",
                         context_files=[], constitution_path=Path("/tmp/const.md"))
        assert "[ROUND:END]" not in p
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/test_debatedrift_prompts.py -v 2>&1 | tail -10
```

Expected: `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 3: Implement prompts.py**

Create `driftdriver/driftdriver/debatedrift/prompts.py`:

```python
# ABOUTME: Generate agent system prompts for debatedrift sessions.
# ABOUTME: Produces debater-a, debater-b, and proxy prompts with sentinel instructions.
from __future__ import annotations

from pathlib import Path


def _context_section(context_files: list[str]) -> str:
    if not context_files:
        return ""
    lines = ["", "## Context Files", "Read these files before beginning:", ""]
    for f in context_files:
        lines.append(f"- `{f}`")
    return "\n".join(lines)


def debater_a_prompt(
    *,
    topic: str,
    task_id: str,
    max_rounds: int,
    context_files: list[str],
) -> str:
    ctx = _context_section(context_files)
    return f"""You are Debater A in a structured debate session.

## Your Role
Attack the problem from your strongest angle. Develop a clear position and defend it
with reasoning. You are not here to be agreeable — you are here to find the best answer.

**Important:** The Proxy is listening to this entire session. Make your case clearly
enough that the Proxy can make a final decision based on what you and Debater B say.

## Topic
{topic}

## Task ID
{task_id}
{ctx}
## Round Protocol

You have up to {max_rounds} rounds. Each round:
1. Read Debater B's latest position (it will appear in your context)
2. Check for human messages: run `wg msg list {task_id}` — acknowledge any messages before continuing
3. State your position clearly and directly
4. Challenge or respond to B's last point
5. At the end of your turn, write exactly: `[ROUND:END]`

## Termination
When the Proxy writes `DEBATE:CONCLUDED`, stop. The debate is over.

## Style
- Direct and opinionated. No hedging.
- Ground every claim in reasoning or evidence.
- If you change your mind, say so explicitly and explain why.
- Do not repeat yourself without adding new reasoning.
"""


def debater_b_prompt(
    *,
    topic: str,
    task_id: str,
    max_rounds: int,
    context_files: list[str],
) -> str:
    ctx = _context_section(context_files)
    return f"""You are Debater B in a structured debate session.

## Your Role
Challenge, diverge, and find what's missing. When Debater A takes a position, your job
is to find the contrarian view, the corner case, the alternative approach, or the hidden
assumption. You are not being contrary for sport — you are making the final answer better.

**Important:** The Proxy is listening. When you identify a genuine weakness in A's position,
articulate it clearly so the Proxy can weigh it.

## Topic
{topic}

## Task ID
{task_id}
{ctx}
## Round Protocol

You have up to {max_rounds} rounds. Each round:
1. Read Debater A's latest position (it will appear in your context)
2. Check for human messages: run `wg msg list {task_id}` — acknowledge any messages before continuing
3. Find the strongest challenge to A's position — a corner case, alternative, or missing assumption
4. If A's position is actually correct, say so and explain what edge cases still need addressing
5. At the end of your turn, write exactly: `[ROUND:END]`

## Termination
When the Proxy writes `DEBATE:CONCLUDED`, stop. The debate is over.

## Style
- Contrarian but constructive. Every challenge should make the answer better.
- Ground objections in specifics: what breaks, when, and why.
- Don't repeat objections that A has already addressed adequately.
- If you find yourself agreeing, say so — the Proxy needs to know.
"""


def proxy_prompt(
    *,
    topic: str,
    task_id: str,
    context_files: list[str],
    constitution_path: Path,
) -> str:
    ctx = _context_section(context_files)
    const_note = ""
    if constitution_path.exists():
        try:
            const_note = constitution_path.read_text(encoding="utf-8")
        except OSError:
            const_note = f"[Could not read constitution at {constitution_path}]"
    else:
        const_note = "[Constitution file not found — use general judgment principles]"

    return f"""You are the Proxy in a structured debate session. You do not debate.
You listen. You decide.

## Your Role
Two debaters are working through a problem. Your job is to monitor the debate and call
it when you have enough to make a final decision. You speak once — at the end.

## Topic
{topic}

## Task ID
{task_id}
{ctx}
## Your Judgment Constitution

{const_note}

## How to Monitor
- Read both panes' output as the debate progresses
- Note where the debaters agree and disagree
- Note when the debate starts repeating itself — that is your signal to call it
- Do not interject during the debate

## When to Decide
Call the debate when:
- The debaters have converged on a core approach (even if details differ)
- The debate is circling back to the same points without new reasoning
- A genuine deadlock has emerged that more debate won't resolve
- The round cap has been reached

## How to Call It
When ready, write your decision in this format:

---
**PROXY DECISION**

**Decision:** [Your clear final call on the topic]

**Reasoning:** [Why this is the right answer based on the debate]

**Key tensions:** [What the debaters disagreed on that's worth tracking]

**Deferred:** [Any angles worth revisiting in a follow-up]

DEBATE:CONCLUDED
---

If there is a genuine deadlock that you cannot resolve:

---
**PROXY DEADLOCK**

**What's blocked:** [The specific decision that cannot be made]

**Why:** [The genuine conflict that debate couldn't resolve]

**Escalation:** [What Braydon needs to decide]

DEBATE:DEADLOCK
---
"""
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/test_debatedrift_prompts.py -v 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/debatedrift/prompts.py tests/test_debatedrift_prompts.py
git commit -m "feat: add debatedrift agent prompt generator"
```

---

## Task 4: Aggregator

**Files:**
- Create: `driftdriver/driftdriver/debatedrift/aggregator.py`
- Create: `driftdriver/tests/test_debatedrift_aggregator.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_debatedrift_aggregator.py`:

```python
# ABOUTME: Tests for debatedrift log aggregator — sentinel detection, round counting, merge.
# ABOUTME: Uses real temp files; no mocks.
from __future__ import annotations

import tempfile
import time
from pathlib import Path

from driftdriver.debatedrift.aggregator import (
    AggregatorState,
    count_round_ends,
    detect_sentinel,
    merge_logs,
)


class TestCountRoundEnds:
    def test_zero_when_empty(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("")
            path = Path(f.name)
        assert count_round_ends(path) == 0

    def test_counts_round_end_markers(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("some output\n[ROUND:END]\nmore output\n[ROUND:END]\n")
            path = Path(f.name)
        assert count_round_ends(path) == 2

    def test_partial_marker_not_counted(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("ROUND:END without brackets\n")
            path = Path(f.name)
        assert count_round_ends(path) == 0


class TestDetectSentinel:
    def test_detects_concluded(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("Some proxy output\nDEBATE:CONCLUDED\n")
            path = Path(f.name)
        assert detect_sentinel(path, "DEBATE:CONCLUDED") is True

    def test_detects_deadlock(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("DEBATE:DEADLOCK\n")
            path = Path(f.name)
        assert detect_sentinel(path, "DEBATE:DEADLOCK") is True

    def test_no_false_positive(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("The debate is ongoing\n")
            path = Path(f.name)
        assert detect_sentinel(path, "DEBATE:CONCLUDED") is False

    def test_returns_false_when_file_missing(self) -> None:
        assert detect_sentinel(Path("/tmp/nonexistent_xyz_log.txt"), "DEBATE:CONCLUDED") is False


class TestMergeLogs:
    def test_merged_contains_both_panes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text(
                "2026-03-17T10:00:00 Debater A says hello\n", encoding="utf-8"
            )
            (debate_dir / "pane-b.log").write_text(
                "2026-03-17T10:00:05 Debater B responds\n", encoding="utf-8"
            )
            out = debate_dir / "debate.log"
            merge_logs(debate_dir=debate_dir, output_path=out)
            content = out.read_text(encoding="utf-8")
            assert "Debater A says hello" in content
            assert "Debater B responds" in content

    def test_merge_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text("line1\n", encoding="utf-8")
            (debate_dir / "pane-b.log").write_text("line2\n", encoding="utf-8")
            out = debate_dir / "debate.log"
            merge_logs(debate_dir=debate_dir, output_path=out)
            first = out.read_text(encoding="utf-8")
            merge_logs(debate_dir=debate_dir, output_path=out)
            second = out.read_text(encoding="utf-8")
            assert first == second


class TestAggregatorState:
    def test_initial_state(self) -> None:
        state = AggregatorState()
        assert state.round_count == 0
        assert state.terminated is False
        assert state.termination_kind is None

    def test_update_increments_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text("[ROUND:END]\n[ROUND:END]\n", encoding="utf-8")
            (debate_dir / "pane-b.log").write_text("[ROUND:END]\n", encoding="utf-8")
            (debate_dir / "pane-c.log").write_text("proxy listening\n", encoding="utf-8")
            state = AggregatorState()
            state.update(debate_dir=debate_dir)
            # Total [ROUND:END] markers across both debater panes
            assert state.round_count == 3

    def test_detects_concluded_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text("", encoding="utf-8")
            (debate_dir / "pane-b.log").write_text("", encoding="utf-8")
            (debate_dir / "pane-c.log").write_text("DEBATE:CONCLUDED\n", encoding="utf-8")
            state = AggregatorState()
            state.update(debate_dir=debate_dir)
            assert state.terminated is True
            assert state.termination_kind == "concluded"

    def test_detects_deadlock_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text("", encoding="utf-8")
            (debate_dir / "pane-b.log").write_text("", encoding="utf-8")
            (debate_dir / "pane-c.log").write_text("DEBATE:DEADLOCK\n", encoding="utf-8")
            state = AggregatorState()
            state.update(debate_dir=debate_dir)
            assert state.terminated is True
            assert state.termination_kind == "deadlock"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/test_debatedrift_aggregator.py -v 2>&1 | tail -10
```

Expected: `ImportError`.

- [ ] **Step 3: Implement aggregator.py**

Create `driftdriver/driftdriver/debatedrift/aggregator.py`:

```python
# ABOUTME: debatedrift log aggregator — merges pane logs, counts rounds, detects sentinels.
# ABOUTME: Designed to run as a background polling loop; all operations are pure functions.
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


_ROUND_END = "[ROUND:END]"
_CONCLUDED = "DEBATE:CONCLUDED"
_DEADLOCK = "DEBATE:DEADLOCK"


def count_round_ends(log_path: Path) -> int:
    """Count [ROUND:END] sentinels in a log file. Returns 0 if file missing."""
    if not log_path.exists():
        return 0
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return text.count(_ROUND_END)


def detect_sentinel(log_path: Path, sentinel: str) -> bool:
    """Return True if sentinel string appears anywhere in log_path."""
    if not log_path.exists():
        return False
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return sentinel in text


def merge_logs(*, debate_dir: Path, output_path: Path) -> None:
    """Merge pane-a.log and pane-b.log into debate.log sorted by leading timestamp.

    Lines without timestamps are kept in file order after timestamped lines.
    If ts(1) timestamps are not present, files are interleaved in file order.
    Output is deterministic for the same input — idempotent.
    """
    lines: list[tuple[str, str]] = []  # (timestamp_or_empty, line)

    for pane_file in ["pane-a.log", "pane-b.log"]:
        pane_path = debate_dir / pane_file
        if not pane_path.exists():
            continue
        try:
            text = pane_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines(keepends=True):
            # ts(1) format: "YYYY-MM-DDTHH:MM:SS.ffffff "
            parts = line.split(" ", 1)
            ts = parts[0] if len(parts) == 2 and "T" in parts[0] else ""
            lines.append((ts, line))

    lines.sort(key=lambda x: x[0])
    merged = "".join(line for _, line in lines)
    output_path.write_text(merged, encoding="utf-8")


@dataclass
class AggregatorState:
    round_count: int = 0
    terminated: bool = False
    termination_kind: str | None = None

    def update(self, *, debate_dir: Path) -> None:
        """Refresh state from the debate directory. Idempotent."""
        if self.terminated:
            return

        pane_a = debate_dir / "pane-a.log"
        pane_b = debate_dir / "pane-b.log"
        pane_c = debate_dir / "pane-c.log"

        a_rounds = count_round_ends(pane_a)
        b_rounds = count_round_ends(pane_b)
        self.round_count = a_rounds + b_rounds

        if detect_sentinel(pane_c, _CONCLUDED):
            self.terminated = True
            self.termination_kind = "concluded"
        elif detect_sentinel(pane_c, _DEADLOCK):
            self.terminated = True
            self.termination_kind = "deadlock"


def send_nudge(*, task_id: str, pane: str) -> None:
    """Send a wg msg nudge to the stalled agent."""
    msg = (
        f"You haven't posted a [ROUND:END] in a while ({pane}). "
        "Please complete your current turn and write [ROUND:END] to continue."
    )
    try:
        subprocess.run(
            ["wg", "msg", "send", task_id, msg],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/test_debatedrift_aggregator.py -v 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/debatedrift/aggregator.py tests/test_debatedrift_aggregator.py
git commit -m "feat: add debatedrift log aggregator with sentinel detection"
```

---

## Task 5: Session Launcher

**Files:**
- Create: `driftdriver/driftdriver/debatedrift/session.py`

No unit tests for the tmux launcher itself (it requires a real display). Integration verification is done manually. Document the contract instead.

- [ ] **Step 1: Implement session.py**

Create `driftdriver/driftdriver/debatedrift/session.py`:

```python
# ABOUTME: debatedrift tmux session launcher — creates 4-pane layout with pipe-pane capture.
# ABOUTME: Emits session.started events for factory brain suppression.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from driftdriver.debatedrift.config import DebateDriftConfig
from driftdriver.debatedrift.prompts import (
    debater_a_prompt,
    debater_b_prompt,
    proxy_prompt,
)


_CONSTITUTION_PATH = Path(__file__).parent / "proxy-constitution.md"


@dataclass
class DebateSession:
    task_id: str
    debate_dir: Path
    tmux_session: str
    config: DebateDriftConfig


def _tmux(*args: str) -> int:
    return subprocess.call(["tmux", *args])


def _tmux_out(*args: str) -> str:
    result = subprocess.run(["tmux", *args], text=True, capture_output=True)
    return result.stdout.strip()


def _has_ts() -> bool:
    return shutil.which("ts") is not None


def _pipe_pane_cmd(log_path: Path) -> str:
    """Return the pipe-pane shell command for a given log file."""
    log_str = str(log_path)
    if _has_ts():
        return f"ts '%Y-%m-%dT%H:%M:%.S' >> {log_str}"
    return f"cat >> {log_str}"


def _emit_session_event(task_id: str, pane: str, event: str) -> None:
    """Emit a session.started or session.ended event for factory brain suppression."""
    try:
        events_dir = Path(".workgraph") / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        events_file = events_dir / "events.jsonl"
        record = json.dumps({
            "event": event,
            "session": f"debatedrift-{task_id}-{pane}",
            "pid": os.getpid(),
        })
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(record + "\n")
    except Exception:
        pass


def launch_debate_session(
    *,
    task_id: str,
    topic: str,
    config: DebateDriftConfig,
    workgraph_dir: Path,
) -> DebateSession:
    """Launch a 4-pane tmux debate session and wire pipe-pane capture.

    Layout:
      pane 0 (top-left):  Debater A
      pane 1 (top-right): Debater B
      pane 2 (bot-left):  Proxy
      pane 3 (bot-right): tail -f debate.log (read-only observer)

    Returns a DebateSession describing the running session.
    Raises RuntimeError if tmux is not available.
    """
    if not shutil.which("tmux"):
        raise RuntimeError("tmux is required for debatedrift but is not installed")

    debate_dir = workgraph_dir / ".debatedrift" / task_id
    debate_dir.mkdir(parents=True, exist_ok=True)

    pane_a_log = debate_dir / "pane-a.log"
    pane_b_log = debate_dir / "pane-b.log"
    pane_c_log = debate_dir / "pane-c.log"
    debate_log = debate_dir / "debate.log"

    # Initialise log files
    for log in [pane_a_log, pane_b_log, pane_c_log, debate_log]:
        if not log.exists():
            log.write_text("", encoding="utf-8")

    # Write prompt files so claude can be launched with -p flag
    prompt_a = debate_dir / "prompt-a.txt"
    prompt_b = debate_dir / "prompt-b.txt"
    prompt_c = debate_dir / "prompt-c.txt"

    prompt_a.write_text(
        debater_a_prompt(topic=topic, task_id=task_id,
                         max_rounds=config.max_rounds, context_files=config.context_files),
        encoding="utf-8",
    )
    prompt_b.write_text(
        debater_b_prompt(topic=topic, task_id=task_id,
                         max_rounds=config.max_rounds, context_files=config.context_files),
        encoding="utf-8",
    )
    prompt_c.write_text(
        proxy_prompt(topic=topic, task_id=task_id,
                     context_files=config.context_files,
                     constitution_path=_CONSTITUTION_PATH),
        encoding="utf-8",
    )

    session_name = f"debate-{task_id}"

    # Kill existing session if any
    subprocess.call(["tmux", "kill-session", "-t", session_name],
                    stderr=subprocess.DEVNULL)

    # Create session with 4 panes (2x2 layout)
    # Pane 0: top-left (Debater A)
    _tmux("new-session", "-d", "-s", session_name, "-x", "220", "-y", "50")
    # Split horizontally for pane 1 (Debater B)
    _tmux("split-window", "-h", "-t", f"{session_name}:0")
    # Split pane 0 vertically for pane 2 (Proxy)
    _tmux("split-window", "-v", "-t", f"{session_name}:0.0")
    # Split pane 1 vertically for pane 3 (debate.log observer)
    _tmux("split-window", "-v", "-t", f"{session_name}:0.1")

    # Pane indices after splits:
    #   0.0 = Debater A (top-left, initial pane)
    #   0.1 = Debater B (top-right, after split-window -h)
    #   0.2 = Proxy     (bottom-left, after split-window -v on pane 0)
    #   0.3 = Observer  (bottom-right, after split-window -v on pane 1)

    # Wire pipe-pane for debaters and proxy
    _tmux("pipe-pane", "-t", f"{session_name}:0.0",
          _pipe_pane_cmd(pane_a_log))
    _tmux("pipe-pane", "-t", f"{session_name}:0.1",
          _pipe_pane_cmd(pane_b_log))
    _tmux("pipe-pane", "-t", f"{session_name}:0.2",
          _pipe_pane_cmd(pane_c_log))

    # Launch claude in each debater pane
    _tmux("send-keys", "-t", f"{session_name}:0.0",
          f"claude --print < {prompt_a}", "Enter")
    time.sleep(0.5)
    _tmux("send-keys", "-t", f"{session_name}:0.1",
          f"claude --print < {prompt_b}", "Enter")
    time.sleep(0.5)
    _tmux("send-keys", "-t", f"{session_name}:0.2",
          f"claude --print < {prompt_c}", "Enter")

    # Pane 3: tail the debate log (observer, read-only)
    _tmux("send-keys", "-t", f"{session_name}:0.3",
          f"tail -f {debate_log}", "Enter")

    # Emit session.started events for factory brain
    for pane in ["debater-a", "debater-b", "proxy"]:
        _emit_session_event(task_id, pane, "session.started")

    print(f"debatedrift: session launched → tmux attach -t {session_name}", file=sys.stderr)
    print(f"debatedrift: logs → {debate_dir}", file=sys.stderr)

    return DebateSession(
        task_id=task_id,
        debate_dir=debate_dir,
        tmux_session=session_name,
        config=config,
    )


def teardown_session(session: DebateSession) -> None:
    """Close the tmux session and emit session.ended events."""
    subprocess.call(["tmux", "kill-session", "-t", session.tmux_session],
                    stderr=subprocess.DEVNULL)
    for pane in ["debater-a", "debater-b", "proxy"]:
        _emit_session_event(session.task_id, pane, "session.ended")
```

- [ ] **Step 2: Verify syntax only**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -c "from driftdriver.debatedrift.session import launch_debate_session, teardown_session; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add driftdriver/debatedrift/session.py
git commit -m "feat: add debatedrift tmux session launcher with pipe-pane wiring"
```

---

## Task 6: Lane Interface

**Files:**
- Create: `driftdriver/driftdriver/debatedrift/lane.py`
- Create: `driftdriver/tests/test_debatedrift_lane.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_debatedrift_lane.py`:

```python
# ABOUTME: Tests for debatedrift lane interface (run_as_lane).
# ABOUTME: Verifies LaneResult output, fence detection, and no-fence pass-through.
from __future__ import annotations

import tempfile
from pathlib import Path

from driftdriver.debatedrift.lane import run_as_lane_check


class TestRunAsLaneCheck:
    """run_as_lane_check inspects the task description for a debatedrift fence
    and returns a LaneResult indicating whether a session should be launched.
    It does NOT launch the session itself (that's the CLI's job).
    """

    def test_returns_lane_result_with_no_fence(self) -> None:
        from driftdriver.lane_contract import LaneResult
        with tempfile.TemporaryDirectory() as td:
            result = run_as_lane_check(
                project_dir=Path(td),
                task_description="just a normal task",
            )
        assert isinstance(result, LaneResult)
        assert result.lane == "debatedrift"
        assert len(result.findings) == 0
        assert result.exit_code == 0

    def test_returns_finding_when_fence_present_no_session(self) -> None:
        from driftdriver.lane_contract import LaneResult
        desc = (
            "Do the thing.\n\n"
            "```debatedrift\n"
            "schema = 1\n"
            "type = \"planning\"\n"
            "```\n"
        )
        with tempfile.TemporaryDirectory() as td:
            result = run_as_lane_check(
                project_dir=Path(td),
                task_description=desc,
            )
        assert isinstance(result, LaneResult)
        assert result.lane == "debatedrift"
        # A fence with no running session → advisory finding
        assert len(result.findings) == 1
        assert result.findings[0].severity == "warning"
        assert result.exit_code == 3

    def test_returns_info_when_session_already_running(self) -> None:
        from driftdriver.lane_contract import LaneResult
        desc = (
            "```debatedrift\n"
            "schema = 1\n"
            "type = \"troubleshoot\"\n"
            "```\n"
        )
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # Simulate a running session by creating the debate dir
            (td_path / ".workgraph" / ".debatedrift" / "task-123").mkdir(parents=True)
            result = run_as_lane_check(
                project_dir=td_path,
                task_description=desc,
                task_id="task-123",
            )
        assert isinstance(result, LaneResult)
        assert result.exit_code == 0  # session running — no advisory needed
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
python -m pytest tests/test_debatedrift_lane.py -v 2>&1 | tail -10
```

Expected: `ImportError`.

- [ ] **Step 3: Implement lane.py**

Create `driftdriver/driftdriver/debatedrift/lane.py`:

```python
# ABOUTME: debatedrift lane interface — run_as_lane() and run_as_lane_check() for driftdriver integration.
# ABOUTME: Checks for debatedrift fence and session state; does not launch sessions directly.
from __future__ import annotations

from pathlib import Path

from driftdriver.debatedrift.config import parse_debatedrift_config
from driftdriver.lane_contract import LaneFinding, LaneResult


def _session_running(*, project_dir: Path, task_id: str) -> bool:
    """Return True if a debate session directory already exists for this task."""
    debate_dir = project_dir / ".workgraph" / ".debatedrift" / task_id
    return debate_dir.exists()


def run_as_lane_check(
    *,
    project_dir: Path,
    task_description: str = "",
    task_id: str = "",
) -> LaneResult:
    """Check lane — inspects task description and returns LaneResult.

    Does NOT launch a session. Advisory finding if fence present but no session running.
    """

    cfg = parse_debatedrift_config(task_description)
    if cfg is None:
        return LaneResult(
            lane="debatedrift",
            findings=[],
            exit_code=0,
            summary="no debatedrift fence — skipping",
        )

    if task_id and _session_running(project_dir=project_dir, task_id=task_id):
        return LaneResult(
            lane="debatedrift",
            findings=[],
            exit_code=0,
            summary=f"debate session active for task {task_id}",
        )

    return LaneResult(
        lane="debatedrift",
        findings=[
            LaneFinding(
                message=(
                    f"debatedrift fence detected (type={cfg.type}) — "
                    "run `driftdriver debate start --task <id>` to launch"
                ),
                severity="warning",
                tags=["debatedrift", cfg.type],
            )
        ],
        exit_code=3,
        summary=f"debatedrift fence present, session not started (type={cfg.type})",
    )


def run_as_lane(project_dir: Path) -> LaneResult:
    """Standard internal lane entrypoint — called by driftdriver check.

    Without task_id context, always returns clean (no advisory). Full
    activation requires the `driftdriver debate start` subcommand.
    This registration exists so `check.py` can detect the fence via
    `_task_has_fence` without needing a separate plugin binary.
    """
    return run_as_lane_check(
        project_dir=project_dir,
        task_description="",
        task_id="",
    )
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python -m pytest tests/test_debatedrift_lane.py -v 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/debatedrift/lane.py tests/test_debatedrift_lane.py
git commit -m "feat: add debatedrift lane interface (run_as_lane)"
```

---

## Task 7: CLI Subcommand

**Files:**
- Create: `driftdriver/driftdriver/cli/debate_cmd.py`
- Modify: `driftdriver/driftdriver/cli/__init__.py`

- [ ] **Step 1: Write debate_cmd.py**

Create `driftdriver/driftdriver/cli/debate_cmd.py`:

```python
# ABOUTME: 'driftdriver debate' subcommand — start, status, conclude debate sessions.
# ABOUTME: Wraps the debatedrift session launcher and aggregator.
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from driftdriver.workgraph import find_workgraph_dir


def cmd_debate_start(args: argparse.Namespace) -> int:
    task_id = str(args.task or "").strip()
    if not task_id:
        print("error: --task is required", file=sys.stderr)
        return 2

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent

    from driftdriver.workgraph import load_workgraph
    wg = load_workgraph(wg_dir)
    task = wg.tasks.get(task_id)
    if not task:
        print(f"error: task {task_id!r} not found in workgraph", file=sys.stderr)
        return 2

    description = str(task.get("description") or "")
    title = str(task.get("title") or task_id)

    from driftdriver.debatedrift.config import parse_debatedrift_config
    cfg = parse_debatedrift_config(description)
    if cfg is None:
        print(
            f"error: task {task_id!r} has no debatedrift fence in its description",
            file=sys.stderr,
        )
        return 2

    from driftdriver.debatedrift.session import launch_debate_session
    session = launch_debate_session(
        task_id=task_id,
        topic=title,
        config=cfg,
        workgraph_dir=wg_dir,
    )

    if args.watch:
        return _watch_loop(session=session, wg_dir=wg_dir, cfg=cfg, task_id=task_id)

    print(f"Session started. Attach: tmux attach -t {session.tmux_session}")
    print(f"Logs: {session.debate_dir}")
    print(f"Status: driftdriver debate status --task {task_id}")
    return 0


def _watch_loop(*, session: "DebateSession", wg_dir: Path, cfg: "DebateDriftConfig", task_id: str) -> int:
    from driftdriver.debatedrift.aggregator import AggregatorState, merge_logs, send_nudge

    state = AggregatorState()
    debate_log = session.debate_dir / "debate.log"
    last_nudge: dict[str, float] = {"a": 0.0, "b": 0.0}
    poll_interval = 10  # seconds

    print(f"Watching debate session (Ctrl-C to detach)...")
    try:
        while not state.terminated:
            state.update(debate_dir=session.debate_dir)
            merge_logs(debate_dir=session.debate_dir, output_path=debate_log)

            now = time.time()
            # Check for stalled debaters
            for pane, log_name in [("a", "pane-a.log"), ("b", "pane-b.log")]:
                pane_log = session.debate_dir / log_name
                mtime = pane_log.stat().st_mtime if pane_log.exists() else 0.0
                elapsed = now - mtime
                if elapsed > cfg.watchdog_timeout and now - last_nudge[pane] > cfg.watchdog_timeout:
                    send_nudge(task_id=task_id, pane=f"pane-{pane}")
                    last_nudge[pane] = now
                    print(f"nudge sent to pane-{pane} (silent {elapsed:.0f}s)")

            # Check round cap
            if state.round_count >= cfg.max_rounds * 2:
                print(f"Round cap ({cfg.max_rounds}) reached — concluding debate")
                _force_conclude(task_id=task_id)
                break

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nDetached from watch loop. Session continues.")
        return 0

    _on_termination(state=state, session=session, task_id=task_id, wg_dir=wg_dir)
    return 0


def _force_conclude(task_id: str) -> None:
    import subprocess
    subprocess.call(
        ["wg", "msg", "send", task_id,
         "Round cap reached. Write DEBATE:CONCLUDED now with your final decision."],
        capture_output=True,
    )


def _on_termination(*, state: "AggregatorState", session: "DebateSession", task_id: str, wg_dir: Path) -> None:
    import subprocess
    from driftdriver.debatedrift.session import teardown_session

    kind = state.termination_kind or "unknown"
    print(f"Debate terminated: {kind}")

    # Write wg log entry
    subprocess.call(
        ["wg", "log", task_id, f"debatedrift: terminated ({kind})"],
        capture_output=True,
    )

    teardown_session(session)
    print(f"Summary: {session.debate_dir}/summary.md")
    print(f"Log: {session.debate_dir}/debate.log")


def cmd_debate_status(args: argparse.Namespace) -> int:
    task_id = str(args.task or "").strip()
    if not task_id:
        print("error: --task is required", file=sys.stderr)
        return 2

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    debate_dir = wg_dir / ".debatedrift" / task_id

    if not debate_dir.exists():
        print(f"No debate session found for task {task_id}")
        return 0

    from driftdriver.debatedrift.aggregator import AggregatorState
    state = AggregatorState()
    state.update(debate_dir=debate_dir)

    print(f"Task:        {task_id}")
    print(f"Rounds:      {state.round_count}")
    print(f"Terminated:  {state.terminated}")
    if state.termination_kind:
        print(f"Outcome:     {state.termination_kind}")
    print(f"Logs:        {debate_dir}")
    return 0


def cmd_debate_conclude(args: argparse.Namespace) -> int:
    task_id = str(args.task or "").strip()
    if not task_id:
        print("error: --task is required", file=sys.stderr)
        return 2

    import subprocess
    subprocess.call(
        ["wg", "msg", "send", task_id,
         "Human requests immediate conclusion. Write DEBATE:CONCLUDED now."],
        capture_output=True,
    )
    print(f"Conclude signal sent to task {task_id}")
    return 0


def register_debate_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register 'driftdriver debate' subcommand with start/status/conclude."""
    debate_parser = subparsers.add_parser("debate", help="manage debatedrift sessions")
    debate_sub = debate_parser.add_subparsers(dest="debate_command")

    start_p = debate_sub.add_parser("start", help="launch a debate session for a task")
    start_p.add_argument("--task", required=True, help="task ID")
    start_p.add_argument("--dir", default="", help="project directory")
    start_p.add_argument("--watch", action="store_true", help="watch and manage the session loop")

    status_p = debate_sub.add_parser("status", help="show debate session status")
    status_p.add_argument("--task", required=True, help="task ID")
    status_p.add_argument("--dir", default="", help="project directory")

    conclude_p = debate_sub.add_parser("conclude", help="signal proxy to conclude immediately")
    conclude_p.add_argument("--task", required=True, help="task ID")
    conclude_p.add_argument("--dir", default="", help="project directory")

    debate_parser.set_defaults(func=_dispatch_debate)


def _dispatch_debate(args: argparse.Namespace) -> int:
    cmd = str(getattr(args, "debate_command", "") or "").strip()
    if cmd == "start":
        return cmd_debate_start(args)
    if cmd == "status":
        return cmd_debate_status(args)
    if cmd == "conclude":
        return cmd_debate_conclude(args)
    print("usage: driftdriver debate {start|status|conclude} --task <id>", file=sys.stderr)
    return 2
```

- [ ] **Step 2: Register in `cli/__init__.py`**

Subparser registration lives in `driftdriver/driftdriver/cli/__init__.py` in `_build_parser()` (line ~1101). The `sub` variable is the `_SubParsersAction` object.

Add the import near the top of `cli/__init__.py` with the other local CLI imports:

```python
from driftdriver.cli.debate_cmd import register_debate_parser
```

Then at line ~1569, just before `return p` at the end of `_build_parser()`, add:

```python
    register_debate_parser(sub)
```

- [ ] **Step 3: Verify CLI works**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m driftdriver.cli debate --help 2>&1 | head -10
```

Expected: help output showing `start`, `status`, `conclude`.

- [ ] **Step 4: Commit**

```bash
git add driftdriver/cli/debate_cmd.py driftdriver/cli/__init__.py
git commit -m "feat: add driftdriver debate start/status/conclude CLI subcommand"
```

---

## Task 8: Install Integration

**Files:**
- Modify: `driftdriver/driftdriver/install.py`
- Modify: `driftdriver/driftdriver/cli/install_cmd.py`
- Modify: `driftdriver/driftdriver/cli/check.py`

- [ ] **Step 1: Add gitignore helper and wrapper to install.py**

Find `ensure_qadrift_gitignore` (last `ensure_*_gitignore`) and add immediately after:

```python
def ensure_debatedrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".debatedrift/")
```

Find `write_qadrift_wrapper` (last `write_*_wrapper`) and add immediately after:

```python
def write_debatedrift_wrapper(wg_dir: Path) -> bool:
    """debatedrift uses driftdriver's own CLI — no separate binary needed.

    Writes a thin wrapper that calls `driftdriver debate`.
    """
    wrapper = wg_dir / "debatedrift"
    content = (
        "#!/usr/bin/env sh\n"
        "# debatedrift wrapper — delegates to driftdriver debate\n"
        'exec driftdriver debate "$@"\n'
    )
    try:
        wrapper.write_text(content, encoding="utf-8")
        wrapper.chmod(0o755)
        return True
    except OSError:
        return False
```

Also add `wrote_debatedrift: bool` to the `InstallResult` dataclass (line ~56, after `wrote_qadrift`):

```python
    wrote_qadrift: bool
    wrote_debatedrift: bool  # ← add this line
    wrote_handlers: bool
```

- [ ] **Step 2: Wire into cmd_install**

In `install_cmd.py`:

First, add to the `from driftdriver.install import (...)` block at the top:

```python
    write_debatedrift_wrapper,
    ensure_debatedrift_gitignore,
```

Then find the call to `write_qadrift_wrapper(wg_dir)` and add immediately after:

```python
    wrote_debatedrift = write_debatedrift_wrapper(wg_dir)
    ensure_debatedrift_gitignore(wg_dir)
```

Then find the `result = InstallResult(...)` constructor call and add `wrote_debatedrift=wrote_debatedrift` alongside the other `wrote_*` fields.

- [ ] **Step 3: Add to INTERNAL_LANES in check.py**

Find `INTERNAL_LANES` dict and add:

```python
"debatedrift": "driftdriver.debatedrift.lane",
```

This follows the same explicit module pattern as `"secdrift": "driftdriver.secdrift"` and `"qadrift": "driftdriver.qadrift"` — pointing directly to the module that defines `run_as_lane`.

- [ ] **Step 4: Verify install imports**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -c "
from driftdriver.install import write_debatedrift_wrapper, ensure_debatedrift_gitignore
from driftdriver.cli.check import INTERNAL_LANES
assert 'debatedrift' in INTERNAL_LANES
print('install integration ok')
"
```

Expected: `install integration ok`

- [ ] **Step 5: Run full test suite**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all existing tests still pass, plus the new debatedrift tests.

- [ ] **Step 6: Commit**

```bash
git add driftdriver/install.py driftdriver/cli/install_cmd.py driftdriver/cli/check.py
git commit -m "feat: wire debatedrift into install + check internal lanes"
```

---

## Task 9: Smoke Test End-to-End

This task verifies the full flow without launching a real tmux session.

- [ ] **Step 1: Create a test task in workgraph**

```bash
cd /Users/braydon/projects/experiments/driftdriver
wg add "Test debatedrift: plan the aggregator retry mechanism" --description "$(cat <<'EOF'
Test planning task for debatedrift smoke test.

\`\`\`debatedrift
schema = 1
type = "planning"
max_rounds = 2
watchdog_timeout = 30
\`\`\`
EOF
)"
```

Note the task ID from the output.

- [ ] **Step 2: Verify fence detection**

```bash
# Replace TASK_ID with the actual ID from Step 1
python -c "
from driftdriver.workgraph import find_workgraph_dir, load_workgraph
from driftdriver.debatedrift.config import parse_debatedrift_config
from pathlib import Path

wg_dir = find_workgraph_dir(None)
wg = load_workgraph(wg_dir)
# Get the last added task
tasks = list(wg.tasks.values())
task = tasks[-1]
cfg = parse_debatedrift_config(task.get('description', ''))
print('Config parsed:', cfg)
"
```

Expected: `Config parsed: DebateDriftConfig(type='planning', max_rounds=2, watchdog_timeout=30, context_files=[])`

- [ ] **Step 3: Verify lane check**

```bash
python -c "
from driftdriver.debatedrift.lane import run_as_lane_check
from pathlib import Path

desc = '''
\`\`\`debatedrift
schema = 1
type = \"planning\"
\`\`\`
'''
result = run_as_lane_check(project_dir=Path('.'), task_description=desc, task_id='nonexistent')
print('Lane result:', result.summary, '| exit:', result.exit_code)
"
```

Expected: `Lane result: debatedrift fence present, session not started (type=planning) | exit: 3`

- [ ] **Step 4: Final full test run**

```bash
python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 5: Final commit**

```bash
git add .
git commit -m "feat: debatedrift lane — complete implementation

Three-agent tmux debate lane for speedrift. Debater A + Debater B challenge
each other via pipe-pane captured output. Proxy listens and calls the decision
using proxy-constitution.md. Aggregator monitors sentinels and round counts.
CLI: driftdriver debate start/status/conclude --task <id>.
"
```

---

## Notes for Implementer

1. **tomllib**: Available in Python 3.11+ stdlib. No external dep needed.
2. **speedrift-lane-sdk**: Already installed as a dependency. Import from `speedrift_lane_sdk.lane_contract`.
3. **wg CLI**: Must be on PATH. All `wg` calls use `subprocess` — no Python bindings.
4. **ts (moreutils)**: Optional. The session launcher degrades gracefully without it.
5. **cli/__init__.py modification**: Subparser registration is in `_build_parser()`. Add `register_debate_parser(sub)` just before `return p` at line ~1569. Add import at top of `__init__.py`.
6. **install_cmd.py modification**: Find where `write_qadrift_wrapper` is called and add `write_debatedrift_wrapper` immediately after in the same pattern.
7. **ABOUTME headers**: Required on all new files — 2 lines.
8. **No mocks**: Tests use real temp files and real implementations throughout.
