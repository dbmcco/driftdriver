#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v wg >/dev/null 2>&1; then
  echo "error: wg not found on PATH" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "error: git not found on PATH" >&2
  exit 1
fi

TMPDIR="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

cd "$TMPDIR"
git init -q
mkdir -p src
echo "hi" > src/app.txt
git add src/app.txt
git commit -qm "init"

wg init >/dev/null

echo "0) install sets up wrappers + executor guidance"
mkdir -p "$TMPDIR/.workgraph/executors"
cat > "$TMPDIR/.workgraph/executors/custom.toml" <<'TOML'
[executor]
type = "claude"
command = "claude"
args = ["--print"]

[executor.prompt_template]
template = """
## Speedrift Protocol
- Treat the `wg-contract` block (in the task description) as binding.
- At start and just before completion, run:
  ./.workgraph/speedrift check --task {{task_id}} --write-log --create-followups
"""
TOML

SPEEDRIFT_DUMMY="$TMPDIR/speedrift-dummy"
SPEEDRIFT_MARKER="$TMPDIR/speedrift-called.txt"
cat > "$SPEEDRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "speedrift $*" >> "${SPEEDRIFT_MARKER:?}"
exit 0
SH
chmod +x "$SPEEDRIFT_DUMMY"

UXRIFT_DUMMY="$TMPDIR/uxrift-dummy"
UXRIFT_MARKER="$TMPDIR/uxrift-called.txt"
cat > "$UXRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "uxrift $*" >> "${UXRIFT_MARKER:?}"
exit 0
SH
chmod +x "$UXRIFT_DUMMY"

SPECRIFT_DUMMY="$TMPDIR/specrift-dummy"
SPECRIFT_MARKER="$TMPDIR/specrift-called.txt"
cat > "$SPECRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "specrift $*" >> "${SPECRIFT_MARKER:?}"
exit 0
SH
chmod +x "$SPECRIFT_DUMMY"

export SPEEDRIFT_MARKER
export UXRIFT_MARKER
export SPECRIFT_MARKER

"$ROOT/bin/driftdriver" --dir "$TMPDIR" install --no-ensure-contracts --speedrift-bin "$SPEEDRIFT_DUMMY" --specrift-bin "$SPECRIFT_DUMMY" --uxrift-bin "$UXRIFT_DUMMY" >/dev/null

test -x "$TMPDIR/.workgraph/driftdriver"
test -x "$TMPDIR/.workgraph/rifts"
test -x "$TMPDIR/.workgraph/speedrift"
test -x "$TMPDIR/.workgraph/specrift"
test -x "$TMPDIR/.workgraph/uxrift"

rg -n "## Speedrift Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "\\./\\.workgraph/rifts check" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "## Superpowers Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "## Model-Mediated Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "## Speedrift Protocol" "$TMPDIR/.workgraph/executors/claude.toml" >/dev/null
rg -n "## Superpowers Protocol" "$TMPDIR/.workgraph/executors/claude.toml" >/dev/null
rg -n "## Model-Mediated Protocol" "$TMPDIR/.workgraph/executors/claude.toml" >/dev/null
rg -n "^\\.speedrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "^\\.specrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "## uxrift Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "^\\.uxrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null

echo "ok"

DESC_FILE="$(mktemp)"
cat > "$DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "core task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

Do the thing.
MD

wg add "Core task" --id core-task -d "$(cat "$DESC_FILE")" >/dev/null

echo "1) rifts runs baseline speedrift always"
rm -f "$SPEEDRIFT_MARKER" "$SPECRIFT_MARKER" "$UXRIFT_MARKER"
./.workgraph/rifts check --task core-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test ! -e "$SPECRIFT_MARKER"
test ! -e "$UXRIFT_MARKER"
echo "ok"

echo "2) rifts runs uxrift only when task declares a uxrift block"
UX_DESC_FILE="$(mktemp)"
cat > "$UX_DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "ux task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

```uxrift
schema = 1
url = "http://localhost:12345"
pages = ["/"]
llm = false
```

Run uxrift.
MD

wg add "UX task" --id ux-task -d "$(cat "$UX_DESC_FILE")" >/dev/null

rm -f "$SPEEDRIFT_MARKER" "$SPECRIFT_MARKER" "$UXRIFT_MARKER"
./.workgraph/rifts check --task ux-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test ! -e "$SPECRIFT_MARKER"
test -s "$UXRIFT_MARKER"
echo "ok"

echo "3) rifts runs specrift only when task declares a specrift block"
SPEC_DESC_FILE="$(mktemp)"
cat > "$SPEC_DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "spec task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

```specrift
schema = 1
spec = ["README.md", "docs/**"]
require_spec_update_when_code_changes = true
```

Run specrift.
MD

wg add "Spec task" --id spec-task -d "$(cat "$SPEC_DESC_FILE")" >/dev/null

rm -f "$SPEEDRIFT_MARKER" "$SPECRIFT_MARKER" "$UXRIFT_MARKER"
./.workgraph/rifts check --task spec-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test -s "$SPECRIFT_MARKER"
test ! -e "$UXRIFT_MARKER"
echo "ok"

echo "e2e_smoke: OK"
