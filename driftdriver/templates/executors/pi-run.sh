#!/usr/bin/env bash
# ABOUTME: WorkGraph executor wrapper that drives `pi` (pi-coding-agent) in print mode.
# ABOUTME: Reads the rendered task prompt from stdin (the wg custom-executor contract)
# ABOUTME: and runs it non-interactively via `pi -p`. Peer of codex-run.sh / claude-run.sh.

set -euo pipefail

# Make repo-local wg shims discoverable.
if [[ -d "$PWD/.workgraph/bin" ]]; then
  export PATH="$PWD/.workgraph/bin:$PATH"
fi

# Parse wg-passed flags; forward --model to pi (pi default model is resolved by
# pi itself via provider/model spec, e.g. zai/glm-5.2 or openai/gpt-5.5).
MODEL_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL_ARGS+=(--model "$2"); shift 2 ;;
    --model=*) MODEL_ARGS+=(--model "${1#--model=}"); shift ;;
    # Flags we don't need but wg may pass — consume value-pairs and bare flags gracefully.
    --system-prompt) shift 2 ;;
    --system-prompt=*) shift ;;
    --allowedTools|--disallowedTools|--input-format|--output-format) shift 2 ;;
    --allowedTools=*|--disallowedTools=*|--input-format=*|--output-format=*) shift ;;
    --print|--verbose|--dangerously-skip-permissions|--no-session-persistence|--disable-slash-commands) shift ;;
    *) shift ;;
  esac
done

# Read the rendered task prompt from stdin (the [executor.prompt_template]).
PROMPT="$(cat)"
if [[ -z "${PROMPT//[[:space:]]/}" ]]; then
  echo "error: empty workgraph prompt" >&2
  exit 1
fi

# Optional prompt-enrichment hook (shared with codex-run.sh / claude-run.sh).
PRE_DISPATCH_HOOK="$PWD/.workgraph/hooks/pre-dispatch.sh"
if [[ -x "$PRE_DISPATCH_HOOK" ]]; then
  ENRICHED=$(printf '%s' "$PROMPT" | "$PRE_DISPATCH_HOOK" 2>/dev/null) || true
  if [[ -n "$ENRICHED" ]]; then
    PROMPT="$ENRICHED"
  fi
fi

# CRITICAL: Suppress the Avery persona and turn-end memory writes for autonomous
# workgraph runs. Without this, every dispatched task would inject Avery's identity
# into the run AND pollute Avery's memory store with task evidence. Codex needs no
# such guard because it has no Avery persona; pi does, so this is non-negotiable.
export PAIA_AVERY="${PAIA_AVERY:-0}"

# Resolve the pi binary. Honor PI_BIN / PI_CMD overrides, else rely on PATH.
PI_BIN="${PI_BIN:-${PI_CMD:-pi}}"

# Run pi non-interactively. Prompt is a positional argument (pi -p "<prompt>").
set +e
"$PI_BIN" -p "${MODEL_ARGS[@]}" "$PROMPT"
EXIT_CODE=$?
set -e

exit $EXIT_CODE
