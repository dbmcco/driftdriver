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
cleanup() { rm -rf "$TMPDIR" "${TMPDIR2:-}"; }
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

UXDRIFT_DUMMY="$TMPDIR/uxdrift-dummy"
UXDRIFT_MARKER="$TMPDIR/uxdrift-called.txt"
cat > "$UXDRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "uxdrift $*" >> "${UXDRIFT_MARKER:?}"
exit 0
SH
chmod +x "$UXDRIFT_DUMMY"

SPECDRIFT_DUMMY="$TMPDIR/specdrift-dummy"
SPECDRIFT_MARKER="$TMPDIR/specdrift-called.txt"
cat > "$SPECDRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "specdrift $*" >> "${SPECDRIFT_MARKER:?}"
exit 0
SH
chmod +x "$SPECDRIFT_DUMMY"

DATADRIFT_DUMMY="$TMPDIR/datadrift-dummy"
DATADRIFT_MARKER="$TMPDIR/datadrift-called.txt"
cat > "$DATADRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "datadrift $*" >> "${DATADRIFT_MARKER:?}"
exit 0
SH
chmod +x "$DATADRIFT_DUMMY"

DEPSDRIFT_DUMMY="$TMPDIR/depsdrift-dummy"
DEPSDRIFT_MARKER="$TMPDIR/depsdrift-called.txt"
cat > "$DEPSDRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "depsdrift $*" >> "${DEPSDRIFT_MARKER:?}"
exit 0
SH
chmod +x "$DEPSDRIFT_DUMMY"

THERAPYDRIFT_DUMMY="$TMPDIR/therapydrift-dummy"
THERAPYDRIFT_MARKER="$TMPDIR/therapydrift-called.txt"
cat > "$THERAPYDRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "therapydrift $*" >> "${THERAPYDRIFT_MARKER:?}"
exit 0
SH
chmod +x "$THERAPYDRIFT_DUMMY"

YAGNIDRIFT_DUMMY="$TMPDIR/yagnidrift-dummy"
YAGNIDRIFT_MARKER="$TMPDIR/yagnidrift-called.txt"
cat > "$YAGNIDRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "yagnidrift $*" >> "${YAGNIDRIFT_MARKER:?}"
exit 0
SH
chmod +x "$YAGNIDRIFT_DUMMY"

REDRIFT_DUMMY="$TMPDIR/redrift-dummy"
REDRIFT_MARKER="$TMPDIR/redrift-called.txt"
cat > "$REDRIFT_DUMMY" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "redrift $*" >> "${REDRIFT_MARKER:?}"
exit 0
SH
chmod +x "$REDRIFT_DUMMY"

export SPEEDRIFT_MARKER
export UXDRIFT_MARKER
export SPECDRIFT_MARKER
export DATADRIFT_MARKER
export DEPSDRIFT_MARKER
export THERAPYDRIFT_MARKER
export YAGNIDRIFT_MARKER
export REDRIFT_MARKER

"$ROOT/bin/driftdriver" --dir "$TMPDIR" install --no-ensure-contracts --speedrift-bin "$SPEEDRIFT_DUMMY" --specdrift-bin "$SPECDRIFT_DUMMY" --datadrift-bin "$DATADRIFT_DUMMY" --depsdrift-bin "$DEPSDRIFT_DUMMY" --uxdrift-bin "$UXDRIFT_DUMMY" --therapydrift-bin "$THERAPYDRIFT_DUMMY" --yagnidrift-bin "$YAGNIDRIFT_DUMMY" --redrift-bin "$REDRIFT_DUMMY" >/dev/null

test -x "$TMPDIR/.workgraph/driftdriver"
test -x "$TMPDIR/.workgraph/drifts"
test -x "$TMPDIR/.workgraph/speedrift"
test -x "$TMPDIR/.workgraph/specdrift"
test -x "$TMPDIR/.workgraph/datadrift"
test -x "$TMPDIR/.workgraph/depsdrift"
test -x "$TMPDIR/.workgraph/uxdrift"
test -x "$TMPDIR/.workgraph/therapydrift"
test -x "$TMPDIR/.workgraph/yagnidrift"
test -x "$TMPDIR/.workgraph/redrift"

rg -n "## Speedrift Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "\\./\\.workgraph/drifts check" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "## Superpowers Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "## Model-Mediated Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "## Speedrift Protocol" "$TMPDIR/.workgraph/executors/claude.toml" >/dev/null
rg -n "## Superpowers Protocol" "$TMPDIR/.workgraph/executors/claude.toml" >/dev/null
rg -n "## Model-Mediated Protocol" "$TMPDIR/.workgraph/executors/claude.toml" >/dev/null
rg -n "^\\.speedrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "^\\.specdrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "^\\.datadrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "^\\.depsdrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "## uxdrift Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "^\\.uxdrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "## therapydrift Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "^\\.therapydrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "## yagnidrift Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "^\\.yagnidrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null
rg -n "## redrift Protocol" "$TMPDIR/.workgraph/executors/custom.toml" >/dev/null
rg -n "^\\.redrift/$" "$TMPDIR/.workgraph/.gitignore" >/dev/null

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

echo "1) drifts runs baseline speedrift always"
rm -f "$SPEEDRIFT_MARKER" "$SPECDRIFT_MARKER" "$DATADRIFT_MARKER" "$DEPSDRIFT_MARKER" "$UXDRIFT_MARKER" "$THERAPYDRIFT_MARKER" "$YAGNIDRIFT_MARKER" "$REDRIFT_MARKER"
./.workgraph/drifts check --task core-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test ! -e "$SPECDRIFT_MARKER"
test ! -e "$DATADRIFT_MARKER"
test ! -e "$DEPSDRIFT_MARKER"
test ! -e "$UXDRIFT_MARKER"
test ! -e "$THERAPYDRIFT_MARKER"
test ! -e "$YAGNIDRIFT_MARKER"
test ! -e "$REDRIFT_MARKER"
echo "ok"

echo "2) drifts runs uxdrift only when task declares a uxdrift block"
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

```uxdrift
schema = 1
url = "http://localhost:12345"
pages = ["/"]
llm = false
```

Run uxdrift.
MD

wg add "UX task" --id ux-task -d "$(cat "$UX_DESC_FILE")" >/dev/null

rm -f "$SPEEDRIFT_MARKER" "$SPECDRIFT_MARKER" "$UXDRIFT_MARKER" "$THERAPYDRIFT_MARKER" "$YAGNIDRIFT_MARKER" "$REDRIFT_MARKER"
./.workgraph/drifts check --task ux-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test ! -e "$SPECDRIFT_MARKER"
test -s "$UXDRIFT_MARKER"
test ! -e "$THERAPYDRIFT_MARKER"
test ! -e "$YAGNIDRIFT_MARKER"
test ! -e "$REDRIFT_MARKER"
echo "ok"

echo "3) drifts runs specdrift only when task declares a specdrift block"
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

```specdrift
schema = 1
spec = ["README.md", "docs/**"]
require_spec_update_when_code_changes = true
```

Run specdrift.
MD

wg add "Spec task" --id spec-task -d "$(cat "$SPEC_DESC_FILE")" >/dev/null

rm -f "$SPEEDRIFT_MARKER" "$SPECDRIFT_MARKER" "$UXDRIFT_MARKER" "$THERAPYDRIFT_MARKER" "$YAGNIDRIFT_MARKER" "$REDRIFT_MARKER"
./.workgraph/drifts check --task spec-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test -s "$SPECDRIFT_MARKER"
test ! -e "$UXDRIFT_MARKER"
test ! -e "$THERAPYDRIFT_MARKER"
test ! -e "$YAGNIDRIFT_MARKER"
test ! -e "$REDRIFT_MARKER"
echo "ok"

echo "4) drifts runs datadrift only when task declares a datadrift block"
DATA_DESC_FILE="$(mktemp)"
cat > "$DATA_DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "data task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

```datadrift
schema = 1
migrations = ["db/migrations/**"]
schema_files = ["schema.sql"]
require_schema_update_when_code_changes = true
```

Run datadrift.
MD

wg add "Data task" --id data-task -d "$(cat "$DATA_DESC_FILE")" >/dev/null

rm -f "$SPEEDRIFT_MARKER" "$SPECDRIFT_MARKER" "$DATADRIFT_MARKER" "$DEPSDRIFT_MARKER" "$UXDRIFT_MARKER" "$THERAPYDRIFT_MARKER" "$YAGNIDRIFT_MARKER" "$REDRIFT_MARKER"
./.workgraph/drifts check --task data-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test ! -e "$SPECDRIFT_MARKER"
test -s "$DATADRIFT_MARKER"
test ! -e "$DEPSDRIFT_MARKER"
test ! -e "$UXDRIFT_MARKER"
test ! -e "$THERAPYDRIFT_MARKER"
test ! -e "$YAGNIDRIFT_MARKER"
test ! -e "$REDRIFT_MARKER"
echo "ok"

echo "5) drifts wrapper runs unified checks"
rm -f "$SPEEDRIFT_MARKER"
./.workgraph/drifts check --task data-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
echo "ok"

echo "6) drifts runs depsdrift only when task declares a depsdrift block"
DEPS_DESC_FILE="$(mktemp)"
cat > "$DEPS_DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "deps task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

```depsdrift
schema = 1
manifests = ["package.json"]
locks = ["package-lock.json"]
require_lock_update_when_manifest_changes = true
```

Run depsdrift.
MD

wg add "Deps task" --id deps-task -d "$(cat "$DEPS_DESC_FILE")" >/dev/null

rm -f "$SPEEDRIFT_MARKER" "$SPECDRIFT_MARKER" "$DATADRIFT_MARKER" "$DEPSDRIFT_MARKER" "$UXDRIFT_MARKER" "$THERAPYDRIFT_MARKER" "$YAGNIDRIFT_MARKER" "$REDRIFT_MARKER"
./.workgraph/drifts check --task deps-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test ! -e "$SPECDRIFT_MARKER"
test ! -e "$DATADRIFT_MARKER"
test -s "$DEPSDRIFT_MARKER"
test ! -e "$UXDRIFT_MARKER"
test ! -e "$THERAPYDRIFT_MARKER"
test ! -e "$YAGNIDRIFT_MARKER"
test ! -e "$REDRIFT_MARKER"
echo "ok"

echo "7) drifts runs therapydrift only when task declares a therapydrift block"
THERAPY_DESC_FILE="$(mktemp)"
cat > "$THERAPY_DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "therapy task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

```therapydrift
schema = 1
min_signal_count = 2
followup_prefixes = ["drift-", "speedrift-pit-"]
require_recovery_plan = true
```

Run therapydrift.
MD

wg add "Therapy task" --id therapy-task -d "$(cat "$THERAPY_DESC_FILE")" >/dev/null
wg log therapy-task "Speedrift: yellow (scope_drift)" >/dev/null
wg log therapy-task "Specdrift: yellow (spec_not_updated)" >/dev/null

rm -f "$SPEEDRIFT_MARKER" "$SPECDRIFT_MARKER" "$DATADRIFT_MARKER" "$DEPSDRIFT_MARKER" "$UXDRIFT_MARKER" "$THERAPYDRIFT_MARKER" "$YAGNIDRIFT_MARKER" "$REDRIFT_MARKER"
./.workgraph/drifts check --task therapy-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test ! -e "$SPECDRIFT_MARKER"
test ! -e "$DATADRIFT_MARKER"
test ! -e "$DEPSDRIFT_MARKER"
test ! -e "$UXDRIFT_MARKER"
test -s "$THERAPYDRIFT_MARKER"
test ! -e "$YAGNIDRIFT_MARKER"
test ! -e "$REDRIFT_MARKER"
echo "ok"

echo "8) drifts runs yagnidrift only when task declares a yagnidrift block"
YAGNI_DESC_FILE="$(mktemp)"
cat > "$YAGNI_DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "yagni task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

```yagnidrift
schema = 1
max_new_files = 1
max_new_dirs = 0
enforce_no_speculative_abstractions = true
abstraction_keywords = ["factory", "adapter", "manager", "engine"]
```

Run yagnidrift.
MD

wg add "Yagni task" --id yagni-task -d "$(cat "$YAGNI_DESC_FILE")" >/dev/null
mkdir -p src/adapters
echo "x" > src/adapters/http_adapter.py

rm -f "$SPEEDRIFT_MARKER" "$SPECDRIFT_MARKER" "$DATADRIFT_MARKER" "$DEPSDRIFT_MARKER" "$UXDRIFT_MARKER" "$THERAPYDRIFT_MARKER" "$YAGNIDRIFT_MARKER" "$REDRIFT_MARKER"
./.workgraph/drifts check --task yagni-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test ! -e "$SPECDRIFT_MARKER"
test ! -e "$DATADRIFT_MARKER"
test ! -e "$DEPSDRIFT_MARKER"
test ! -e "$UXDRIFT_MARKER"
test ! -e "$THERAPYDRIFT_MARKER"
test -s "$YAGNIDRIFT_MARKER"
test ! -e "$REDRIFT_MARKER"
echo "ok"

echo "9) drifts runs redrift only when task declares a redrift block"
REDRIFT_DESC_FILE="$(mktemp)"
cat > "$REDRIFT_DESC_FILE" <<'MD'
```wg-contract
schema = 1
mode = "core"
objective = "redrift task"
non_goals = ["No fallbacks"]
touch = ["src/**"]
acceptance = []
max_files = 10
max_loc = 200
auto_followups = true
```

```redrift
schema = 1
artifact_root = ".workgraph/.redrift"
required_artifacts = [
  "analyze/inventory.md",
  "respec/v2-spec.md",
]
create_phase_followups = true
```

Run redrift.
MD

wg add "Redrift task" --id redrift-task -d "$(cat "$REDRIFT_DESC_FILE")" >/dev/null

rm -f "$SPEEDRIFT_MARKER" "$SPECDRIFT_MARKER" "$DATADRIFT_MARKER" "$DEPSDRIFT_MARKER" "$UXDRIFT_MARKER" "$THERAPYDRIFT_MARKER" "$YAGNIDRIFT_MARKER" "$REDRIFT_MARKER"
./.workgraph/drifts check --task redrift-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER"
test ! -e "$SPECDRIFT_MARKER"
test ! -e "$DATADRIFT_MARKER"
test ! -e "$DEPSDRIFT_MARKER"
test ! -e "$UXDRIFT_MARKER"
test ! -e "$THERAPYDRIFT_MARKER"
test ! -e "$YAGNIDRIFT_MARKER"
test -s "$REDRIFT_MARKER"
echo "ok"

echo "e2e_smoke: OK"

echo ""
echo "portable install (PATH-based wrappers)"

TMPDIR2="$(mktemp -d)"
cd "$TMPDIR2"
git init -q
mkdir -p src
echo "hi" > src/app.txt
git add src/app.txt
git commit -qm "init"

wg init >/dev/null

mkdir -p "$TMPDIR2/.workgraph/executors"
cat > "$TMPDIR2/.workgraph/executors/custom.toml" <<'TOML'
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

BIN_DIR="$TMPDIR2/bin"
mkdir -p "$BIN_DIR"

SPEEDRIFT_MARKER_2="$TMPDIR2/speedrift-called.txt"
cat > "$BIN_DIR/speedrift" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "speedrift $*" >> "${SPEEDRIFT_MARKER_2:?}"
exit 0
SH
chmod +x "$BIN_DIR/speedrift"

UXDRIFT_MARKER_2="$TMPDIR2/uxdrift-called.txt"
cat > "$BIN_DIR/uxdrift" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "uxdrift $*" >> "${UXDRIFT_MARKER_2:?}"
exit 0
SH
chmod +x "$BIN_DIR/uxdrift"

SPECDRIFT_MARKER_2="$TMPDIR2/specdrift-called.txt"
cat > "$BIN_DIR/specdrift" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "specdrift $*" >> "${SPECDRIFT_MARKER_2:?}"
exit 0
SH
chmod +x "$BIN_DIR/specdrift"

DATADRIFT_MARKER_2="$TMPDIR2/datadrift-called.txt"
cat > "$BIN_DIR/datadrift" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "datadrift $*" >> "${DATADRIFT_MARKER_2:?}"
exit 0
SH
chmod +x "$BIN_DIR/datadrift"

DEPSDRIFT_MARKER_2="$TMPDIR2/depsdrift-called.txt"
cat > "$BIN_DIR/depsdrift" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "depsdrift $*" >> "${DEPSDRIFT_MARKER_2:?}"
exit 0
SH
chmod +x "$BIN_DIR/depsdrift"

THERAPYDRIFT_MARKER_2="$TMPDIR2/therapydrift-called.txt"
cat > "$BIN_DIR/therapydrift" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "therapydrift $*" >> "${THERAPYDRIFT_MARKER_2:?}"
exit 0
SH
chmod +x "$BIN_DIR/therapydrift"

YAGNIDRIFT_MARKER_2="$TMPDIR2/yagnidrift-called.txt"
cat > "$BIN_DIR/yagnidrift" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "yagnidrift $*" >> "${YAGNIDRIFT_MARKER_2:?}"
exit 0
SH
chmod +x "$BIN_DIR/yagnidrift"

REDRIFT_MARKER_2="$TMPDIR2/redrift-called.txt"
cat > "$BIN_DIR/redrift" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "redrift $*" >> "${REDRIFT_MARKER_2:?}"
exit 0
SH
chmod +x "$BIN_DIR/redrift"

export SPEEDRIFT_MARKER_2
export UXDRIFT_MARKER_2
export SPECDRIFT_MARKER_2
export DATADRIFT_MARKER_2
export DEPSDRIFT_MARKER_2
export THERAPYDRIFT_MARKER_2
export YAGNIDRIFT_MARKER_2
export REDRIFT_MARKER_2

export PATH="$BIN_DIR:$ROOT/bin:$PATH"

# Portable wrappers should not embed absolute tool paths.
"$ROOT/bin/driftdriver" --dir "$TMPDIR2" install --wrapper-mode portable --with-uxdrift --with-therapydrift --with-yagnidrift --with-redrift --no-ensure-contracts >/dev/null

test -x "$TMPDIR2/.workgraph/driftdriver"
test -x "$TMPDIR2/.workgraph/drifts"
test -x "$TMPDIR2/.workgraph/speedrift"
test -x "$TMPDIR2/.workgraph/specdrift"
test -x "$TMPDIR2/.workgraph/datadrift"
test -x "$TMPDIR2/.workgraph/depsdrift"
test -x "$TMPDIR2/.workgraph/uxdrift"
test -x "$TMPDIR2/.workgraph/therapydrift"
test -x "$TMPDIR2/.workgraph/yagnidrift"
test -x "$TMPDIR2/.workgraph/redrift"

rg -n "^TOOL=\\\"driftdriver\\\"$" "$TMPDIR2/.workgraph/driftdriver" >/dev/null
rg -n "^TOOL=\\\"speedrift\\\"$" "$TMPDIR2/.workgraph/speedrift" >/dev/null
rg -n "^TOOL=\\\"specdrift\\\"$" "$TMPDIR2/.workgraph/specdrift" >/dev/null
rg -n "^TOOL=\\\"datadrift\\\"$" "$TMPDIR2/.workgraph/datadrift" >/dev/null
rg -n "^TOOL=\\\"depsdrift\\\"$" "$TMPDIR2/.workgraph/depsdrift" >/dev/null
rg -n "^TOOL=\\\"uxdrift\\\"$" "$TMPDIR2/.workgraph/uxdrift" >/dev/null
rg -n "^TOOL=\\\"therapydrift\\\"$" "$TMPDIR2/.workgraph/therapydrift" >/dev/null
rg -n "^TOOL=\\\"yagnidrift\\\"$" "$TMPDIR2/.workgraph/yagnidrift" >/dev/null
rg -n "^TOOL=\\\"redrift\\\"$" "$TMPDIR2/.workgraph/redrift" >/dev/null

DESC_FILE_2="$(mktemp)"
cat > "$DESC_FILE_2" <<'MD'
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

wg add "Core task" --id core-task -d "$(cat "$DESC_FILE_2")" >/dev/null

rm -f "$SPEEDRIFT_MARKER_2" "$SPECDRIFT_MARKER_2" "$DATADRIFT_MARKER_2" "$DEPSDRIFT_MARKER_2" "$UXDRIFT_MARKER_2" "$THERAPYDRIFT_MARKER_2" "$YAGNIDRIFT_MARKER_2" "$REDRIFT_MARKER_2"
./.workgraph/drifts check --task core-task --write-log --create-followups >/dev/null
test -s "$SPEEDRIFT_MARKER_2"
test ! -e "$THERAPYDRIFT_MARKER_2"
test ! -e "$YAGNIDRIFT_MARKER_2"
test ! -e "$REDRIFT_MARKER_2"

echo "portable e2e_smoke: OK"
