#!/bin/bash
# ABOUTME: Polls DarklyFactory_bot for Telegram messages and routes answers to the hub.
# ABOUTME: In a private chat, any message from the user is an answer to a pending decision.
#
# Usage: ./scripts/factory-telegram-poller.sh
# Runs forever, polling every 5 seconds. Safe to run via launchd.

set -euo pipefail

CONFIG_FILE="$HOME/.config/workgraph/notify.toml"
HUB_URL="http://127.0.0.1:8777"
POLL_INTERVAL=5
OFFSET_FILE="/tmp/factory-telegram-poller-offset"

# Extract factory bot token from notify.toml
BOT_TOKEN=$(python3 -c "
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
import pathlib
data = tomllib.loads(pathlib.Path('$CONFIG_FILE').read_text())
print(data.get('telegram_factory', {}).get('bot_token', ''))
" 2>/dev/null)

if [ -z "$BOT_TOKEN" ]; then
    echo "ERROR: No [telegram_factory] bot_token in $CONFIG_FILE"
    exit 1
fi

API="https://api.telegram.org/bot${BOT_TOKEN}"

# Read last offset
OFFSET=0
if [ -f "$OFFSET_FILE" ]; then
    OFFSET=$(cat "$OFFSET_FILE")
fi

echo "Factory Telegram poller starting (bot: DarklyFactory_bot, offset: $OFFSET)"

while true; do
    # Poll for updates (long poll 5s)
    RESPONSE=$(curl -s "${API}/getUpdates?offset=${OFFSET}&timeout=5&allowed_updates=[\"message\"]" 2>/dev/null || echo '{"ok":false}')

    # Process updates
    python3 -c "
import json, sys, re, urllib.request

response = json.loads('''$( echo "$RESPONSE" | python3 -c "import sys,json; print(json.dumps(json.loads(sys.stdin.read())))" 2>/dev/null || echo '{"ok":false}' )''')

if not response.get('ok'):
    sys.exit(0)

results = response.get('result', [])
if not results:
    sys.exit(0)

for update in results:
    update_id = update.get('update_id', 0)
    # Always advance offset
    print(f'OFFSET:{update_id + 1}', flush=True)

    msg = update.get('message', {})
    text = (msg.get('text') or '').strip()
    from_user = msg.get('from', {})

    # Skip bot messages and empty messages
    if from_user.get('is_bot') or not text:
        continue

    # Skip /start and other commands
    if text.startswith('/'):
        print(f'LOG:Skipping command: {text}', flush=True)
        continue

    user_name = from_user.get('first_name', 'unknown')
    print(f'LOG:Message from {user_name}: {text}', flush=True)

    # Strategy 1: Check if message text contains a decision ID
    id_match = re.search(r'dec-\d{8}-[a-f0-9]{6}', text)

    # Strategy 2: Check reply_to_message for decision ID
    if not id_match:
        reply_to = msg.get('reply_to_message', {})
        original_text = reply_to.get('text', '')
        id_match = re.search(r'dec-\d{8}-[a-f0-9]{6}', original_text)

    # Strategy 3: No explicit ID — find the most recent pending decision via hub
    decision_id = id_match.group(0) if id_match else None
    answer_text = text

    if decision_id:
        # Strip the decision ID from the answer text if they included it
        answer_text = re.sub(r'dec-\d{8}-[a-f0-9]{6}\s*', '', answer_text).strip() or text

    if not decision_id:
        # Ask the hub for pending decisions and pick the most recent
        try:
            req = urllib.request.Request('${HUB_URL}/api/decisions/pending')
            resp = urllib.request.urlopen(req, timeout=5)
            pending = json.loads(resp.read())
            if isinstance(pending, list) and len(pending) == 1:
                decision_id = pending[0].get('id')
                print(f'LOG:Auto-matched to single pending decision: {decision_id}', flush=True)
            elif isinstance(pending, list) and len(pending) > 1:
                # Multiple pending — tell user to specify
                bot_token = '${BOT_TOKEN}'
                chat_id = str(msg.get('chat', {}).get('id', ''))
                lines = ['Multiple decisions pending. Reply with the ID + your answer:', '']
                for d in pending:
                    lines.append(f\"  {d.get('id')}: {d.get('question', '?')}\")
                reply_msg = chr(10).join(lines)
                payload = json.dumps({'chat_id': chat_id, 'text': reply_msg}).encode()
                req2 = urllib.request.Request(
                    '${API}/sendMessage',
                    data=payload,
                    headers={'Content-Type': 'application/json'}
                )
                urllib.request.urlopen(req2, timeout=5)
                print(f'LOG:Multiple pending decisions, asked user to specify', flush=True)
                continue
            elif isinstance(pending, list) and len(pending) == 0:
                # No pending decisions
                bot_token = '${BOT_TOKEN}'
                chat_id = str(msg.get('chat', {}).get('id', ''))
                payload = json.dumps({'chat_id': chat_id, 'text': 'No pending decisions right now.'}).encode()
                req2 = urllib.request.Request(
                    '${API}/sendMessage',
                    data=payload,
                    headers={'Content-Type': 'application/json'}
                )
                urllib.request.urlopen(req2, timeout=5)
                print(f'LOG:No pending decisions', flush=True)
                continue
        except Exception as e:
            print(f'LOG:Could not fetch pending decisions: {e}', flush=True)
            # Hub might be down — can't route without a decision ID
            continue

    if not decision_id:
        print(f'LOG:Could not determine decision ID, skipping', flush=True)
        continue

    # POST answer to hub
    print(f'LOG:Answering {decision_id}: {answer_text}', flush=True)
    payload = json.dumps({
        'decision_id': decision_id,
        'answer': answer_text,
        'answered_via': 'telegram'
    }).encode()

    try:
        req = urllib.request.Request(
            '${HUB_URL}/api/decisions/answer',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        print(f'LOG:Hub response: {json.dumps(result)}', flush=True)

        # Confirm to user via Telegram
        chat_id = str(msg.get('chat', {}).get('id', ''))
        confirm = f\"Decision {decision_id} answered. Repo resuming.\"
        payload2 = json.dumps({'chat_id': chat_id, 'text': confirm}).encode()
        req2 = urllib.request.Request(
            '${API}/sendMessage',
            data=payload2,
            headers={'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req2, timeout=5)
    except urllib.error.HTTPError as e:
        body = e.read().decode() if hasattr(e, 'read') else ''
        print(f'LOG:Hub error {e.code}: {body}', flush=True)
        # Tell user
        chat_id = str(msg.get('chat', {}).get('id', ''))
        payload2 = json.dumps({'chat_id': chat_id, 'text': f'Could not route answer: {body[:200]}'}).encode()
        req2 = urllib.request.Request(
            '${API}/sendMessage',
            data=payload2,
            headers={'Content-Type': 'application/json'}
        )
        try:
            urllib.request.urlopen(req2, timeout=5)
        except:
            pass
    except Exception as e:
        print(f'LOG:Hub error: {e}', flush=True)
" 2>&1 | while IFS= read -r line; do
        if [[ "$line" == OFFSET:* ]]; then
            NEW_OFFSET="${line#OFFSET:}"
            echo "$NEW_OFFSET" > "$OFFSET_FILE"
            OFFSET="$NEW_OFFSET"
        elif [[ "$line" == LOG:* ]]; then
            echo "[$(date +%H:%M:%S)] ${line#LOG:}"
        fi
    done

    sleep "$POLL_INTERVAL"
done
