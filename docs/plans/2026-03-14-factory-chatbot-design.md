# Dark Factory Chat-Bot Design

**Date:** 2026-03-14
**Status:** Draft
**Scope:** Evolve the DarklyFactory_bot from a decision-gate into an interactive chat-bot for the speedrift ecosystem

## Problem

The current Telegram integration is two pieces:
- `telegram.py` — one-way notification sender (brain alerts → Telegram)
- `factory-telegram-poller.sh` — bash script polling every 5s, routing decision answers back to the hub

This gives Braydon decision-gate access but no ability to query factory state, issue commands, or have conversations about what's happening across the ecosystem.

## Architecture

```
Telegram ←→ n8n workflow ←→ POST /api/chat (hub:8777) ←→ Factory Brain chat handler ←→ Claude Sonnet
```

**Transport:** n8n receives Telegram messages from DarklyFactory_bot → POSTs to `http://127.0.0.1:8777/api/chat` → gets JSON response → sends reply back to Telegram.

**Brain integration:** The `/api/chat` handler lives in the ecosystem hub (`driftdriver/ecosystem_hub/api.py`). It:
1. Gathers context (roster, active agents, pending decisions, recent events, repo statuses) from hub state — no HTTP round-trips since it's in-process
2. Builds a prompt with the user's message + current factory state
3. Invokes Claude Sonnet via a new `invoke_chat()` function in `chat.py` (see LLM Invocation below)
4. For write actions, the LLM returns structured directives (same vocabulary the brain uses) which get executed through the existing directive pipeline
5. Returns the response text to n8n → Telegram

**Decision answer pre-screening:** Before hitting the LLM, the handler checks incoming messages for decision ID patterns (`dec-\d{8}-[a-f0-9]{6}`). If found, routes directly to `POST /api/decisions/answer` — no LLM call needed. This preserves the poller's existing decision-routing logic and saves cost/latency.

**Conversation state:** Last 10 messages (user + assistant pairs) stored in memory (dict keyed by `chat_id`). No database. If the hub restarts, conversation history resets. Factory state is the real context, not chat history. Format: `[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]`.

**LLM model:** Claude Sonnet (1M context, extended thinking) for all chat interactions. No tiered escalation — the 1M window lets us feed the complete factory state, and extended thinking handles complex analytical queries naturally.

**Auth:** Simple `chat_id` allowlist checked against the `[telegram_factory]` `chat_id` in `notify.toml`. Messages from unknown `chat_id`s get a 403 response — no LLM call.

## Endpoint Design

### `POST /api/chat`

**Request (from n8n):**
```json
{
  "message": "what's lodestar doing right now",
  "chat_id": "123456",
  "user_name": "Braydon"
}
```

**Response — read-only query:**
```json
{
  "reply": "Lodestar has 2 agents running, 5 tasks completed today...",
  "directives_executed": [],
  "pending_confirmation": null
}
```

**Response — destructive action requested:**
```json
{
  "reply": "Kill the daemon on lodestar — are you sure?",
  "directives_executed": [],
  "pending_confirmation": {
    "id": "confirm-abc123",
    "action": "kill_daemon",
    "params": {"repo": "lodestar"}
  }
}
```

**Response — LLM error / invalid output:**
```json
{
  "reply": "Sorry, I couldn't process that — brain returned an invalid response. Try again or rephrase.",
  "directives_executed": [],
  "pending_confirmation": null
}
```

The confirmation gets stored in-memory keyed by `chat_id`. Next message with "yes"/"y" from that `chat_id` triggers the directive execution. Any other message cancels the pending confirmation and processes normally.

## LLM Invocation

The existing `_invoke_claude` in `brain.py` hardcodes the brain's adversarial directive schema (`reasoning`, `directives`, `escalate`). The chat handler needs a different schema (`reply_text`, `directives`, `needs_confirmation`).

**Approach:** New `invoke_chat()` function in `chat.py` — a focused variant of the claude CLI invocation with the chat-specific schema. This keeps `brain.py` untouched as intended, at the cost of ~30 lines of subprocess invocation that parallel `_invoke_claude`. The duplication is acceptable because the two schemas serve fundamentally different purposes and will evolve independently.

**Model:** Claude Sonnet with 1M token context window and extended thinking. This means:

1. **Rich context, not summarized context** — we can feed the full factory state into the prompt without aggressive truncation. Full event history (not just last 20), complete brain invocation log (not just last 5), all pending decisions, full velocity data, repo dependency graphs. The 1M window can hold all of it comfortably.

2. **Extended thinking for complex queries** — questions like "why does training-assistant keep stalling?" or "should I change lodestar's attractor target?" benefit from the model reasoning through event patterns, directive history, and convergence trends before answering. Simple status queries still get fast responses — the model self-regulates reasoning depth.

3. **CLI flags:**
   - `--model sonnet` (Sonnet 4.6, 1M context)
   - `--output-format json` + `--json-schema` (chat response schema)
   - `--max-budget-usd 2.00` (higher than brain's 0.50 — richer context + reasoning tokens)
   - Extended thinking is enabled by default in current Sonnet; no flag needed

**Cost note:** With 1M context available and extended thinking, individual chat calls may cost more than brain directives. This is acceptable — the bot handles a low volume of human-initiated messages, not automated event loops.

## LLM Prompt & Response Design

### System Prompt

Defines the bot's persona as the factory operator's interface. Includes:
- What repos are enrolled and their states
- The full directive vocabulary (the 16 action types from `directives.py` — see Directive Vocabulary below)
- Which actions require confirmation and why
- Telegram formatting constraints (concise, limited markdown)
- Instruction to set `needs_confirmation: true` when emitting destructive directives

### Structured Output Schema

```json
{
  "type": "object",
  "required": ["reply_text", "directives", "needs_confirmation"],
  "properties": {
    "reply_text": {
      "type": "string",
      "description": "Human-readable response for Telegram"
    },
    "directives": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["action"],
        "properties": {
          "action": {"type": "string"},
          "params": {"type": "object"}
        }
      }
    },
    "needs_confirmation": {
      "type": "boolean",
      "description": "Whether the action(s) require user confirmation before execution"
    }
  }
}
```

### Query Categories

**Read-only** (status, logs, "what's happening") — LLM gets context dump and answers conversationally. No directives emitted.

**Action** ("restart dispatch on lodestar") — LLM emits appropriate directive(s). Handler checks if destructive → confirms or executes immediately.

**Conversational** ("should I enroll this new repo?", "why does training-assistant keep stalling?") — LLM reasons over state and gives advice. May suggest actions but doesn't emit directives unless explicitly asked.

### Context Assembly

With Sonnet's 1M context window, we feed the full factory state rather than truncated summaries. Before each LLM call, the handler pulls from hub state:
- Full roster (all enrolled repos, statuses, targets, paths)
- Full snapshot (active agents, task counts, health per repo)
- All pending decisions across all repos
- Full event history from `events.jsonl` (crashes, stalls, agent lifecycle, session events)
- Full brain invocation log (tier, model, reasoning, directives issued, outcomes)
- Speedriftd mode and lease state per repo
- Velocity and convergence data (attractor status, completion rates)
- Repo dependency graph (from snapshot)
- Conversation history (last 10 user+assistant message pairs for this `chat_id`)

This gives the model complete situational awareness — it can spot patterns across the full event timeline, correlate stalls with prior directives, and reason about ecosystem-wide trends without us pre-filtering what might be relevant.

## Directive Vocabulary

The chat handler reuses the existing directive vocabulary from `directives.py` exactly as defined:

```
kill_process, kill_daemon, clear_locks, start_dispatch_loop, stop_dispatch_loop,
spawn_agent, set_mode, adjust_concurrency, enroll, unenroll,
set_attractor_target, send_telegram, escalate, noop, create_decision, enforce_compliance
```

Note: There is no `restart_dispatch_loop` — a restart is `stop_dispatch_loop` followed by `start_dispatch_loop`. The LLM prompt should instruct this pattern.

## Confirmation Flow

### Destructive Actions (require confirmation)
- `kill_daemon`
- `kill_process`
- `unenroll`
- `set_mode` — only when `params.mode == "autonomous"` (setting to `observe` or `supervise` is safe)

### Safe Actions (execute immediately)
- Status queries (read-only)
- `start_dispatch_loop` / `stop_dispatch_loop`
- `spawn_agent`
- `adjust_concurrency`
- `enroll`
- `clear_locks`
- `set_attractor_target`
- `set_mode` (to `observe` or `supervise`)
- Decision answers (pre-screened, no LLM)

### Flow
1. User: "kill the daemon on lodestar"
2. Bot: "Kill the daemon on lodestar — are you sure?" (stores pending confirmation)
3. User: "yes" → executes `kill_daemon` directive, confirms via reply
4. User: "no" or any other message → cancels confirmation, processes new message normally

## Implementation Scope

### New Files
- `driftdriver/factory_brain/chat.py` — chat handler: context assembly, prompt building, `invoke_chat()` LLM invocation (own schema, Sonnet-only), response parsing, confirmation state management, decision ID pre-screening
- `tests/test_factory_brain_chat.py` — unit tests for the handler

### Modified Files
- `driftdriver/ecosystem_hub/api.py` — add `/api/chat` POST route, wire to `chat.py` handler
- n8n — new workflow: Telegram trigger → HTTP POST to hub `/api/chat` → send reply

### Retired
- `scripts/factory-telegram-poller.sh` — replaced entirely by n8n + `/api/chat`

### Not Changing
- `brain.py` — existing event-driven brain pipeline untouched (chat has its own `invoke_chat()`)
- `directives.py` — directive vocabulary reused as-is, no new actions
- `prompts.py` / `router.py` — brain's own prompting stays separate
- `telegram.py` — brain's one-way alert notifications continue independently via existing `send_telegram`

## Testing Strategy

### Unit Tests (`test_factory_brain_chat.py`)
- **Context assembly** — given mock roster/events/agents, verify the prompt includes correct state
- **Response parsing** — LLM structured JSON → extract reply text, directives, confirmation flags
- **Invalid LLM output** — malformed JSON or missing fields → user-friendly error reply, no crash
- **Confirmation flow** — request destructive action → get confirmation → "yes" → directive executes. Also: "no" → cancels. Also: different message → cancels and processes normally.
- **set_mode confirmation granularity** — `set_mode` to `autonomous` requires confirmation, `set_mode` to `observe` does not
- **Directive routing** — action queries produce correct directive types with correct params
- **Read-only queries** — no directives emitted for status/log questions
- **Decision pre-screening** — message containing `dec-XXXXXXXX-YYYYYY` bypasses LLM, routes to `/api/decisions/answer`
- **Conversation history** — messages stored/retrieved by `chat_id`, bounded to 10 pairs, oldest evicted first
- **Auth** — unknown `chat_id` returns 403, no LLM call

### Integration Test
- Full round-trip: POST to `/api/chat` → coherent response (hub running, `invoke_chat` stubbed at boundary)

### Not Tested in Code
- n8n workflow — config, verify manually once
- LLM response quality — prompt tuning, not unit testing

## Dependencies
- `directives.py` directive vocabulary and `parse_brain_response` (reused for executing directives)
- `directives.py` `execute_directive` (for actually running actions)
- Ecosystem hub snapshot data (roster, events, agents)
- n8n Telegram integration (already in place)
- `~/.config/workgraph/notify.toml` `[telegram_factory]` section for bot token and authorized `chat_id`
