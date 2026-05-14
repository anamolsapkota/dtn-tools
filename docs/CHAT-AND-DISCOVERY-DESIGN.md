# Chat Overhaul & Discovery Fast Recovery

Spec for two improvements to dtn-tools: IRC-style interactive chat with persistent history, and discovery-based contact re-injection after ION restart.

## 1. Chat System Overhaul

### Goal

Replace the current flat chat (all incoming messages print inline regardless of sender) with per-sender conversations, unread indicators, conversation switching via slash commands, and persistent history that survives `dtn chat` restarts.

### Architecture

```
dtn chat
  |-- ChatHistory (persistent, ~/dtn/chat-history.json)
  |   |-- conversations: {ipn: {name, messages[]}}
  |   |-- last_active: ipn (resume on restart)
  |
  |-- ReceiverThread (background)
  |   |-- bprecvfile ipn:<local>.5 -> temp dir
  |   |-- Parse JSON, route to correct conversation in ChatHistory
  |   |-- If from active conversation -> print immediately
  |   |-- If from other sender -> increment unread, show notification
  |
  |-- Interactive UI
      |-- Prompt: [node-name] you>
      |-- Notification: "<- DTNGW sent a message (2 unread)"
      |-- /to <name|#> -- switch conversation, show recent history
      |-- /list -- conversations with unread counts
      |-- /history [N] -- last N messages in current conversation (default 20)
      |-- /nodes -- all known nodes
      |-- /help -- updated command list
```

### Message Routing

- **Incoming from active sender**: print inline immediately, save to history, mark as read.
- **Incoming from other sender**: save to history as unread, print one-line notification: `<- DTNGW sent a message (2 unread)`. Do NOT print the message content.
- **Switching conversations** (`/to`): mark all messages in target conversation as read, display last 20 messages from history.
- **On startup**: load `chat-history.json`, show `/list` view with unread counts. If `last_active` is set, offer to resume. Otherwise prompt for node selection.

### History File (`~/dtn/chat-history.json`)

```json
{
  "conversations": {
    "268485000": {
      "name": "DTNGW",
      "messages": [
        {
          "dir": "in",
          "from": "268485000",
          "name": "DTNGW",
          "msg": "hello",
          "ts": "2026-05-14T18:40:10",
          "read": true
        },
        {
          "dir": "out",
          "from": "268485091",
          "name": "pi05",
          "msg": "hi back",
          "ts": "2026-05-14T18:40:15",
          "read": true
        }
      ]
    }
  },
  "last_active": "268485000"
}
```

Fields:
- `dir`: `"in"` (received) or `"out"` (sent by us)
- `from`: sender IPN
- `name`: sender display name
- `msg`: message text
- `ts`: ISO 8601 timestamp
- `read`: boolean, unread messages have `false`

### Constraints

- Max 500 messages per conversation. When exceeded, prune oldest messages.
- History file written after every send/receive (atomic write with temp file + rename).
- One-shot mode (`dtn chat <IPN> "message"`) saves to history but does not enter interactive mode.
- Backward compatible: old JSON bundles without `dir` field treated as incoming.

### Slash Commands

| Command | Action |
|---------|--------|
| `/to <name\|#\|IPN>` | Switch active conversation. Show last 20 messages. |
| `/list` | Show all conversations with unread counts. |
| `/history [N]` | Show last N messages in current conversation (default 20). |
| `/nodes` | List all nodes from contact graph (existing behavior). |
| `/help` | Show command list. |
| `/quit` or `quit` | Exit chat. |

### Notification Format

When a message arrives from a non-active sender:
```
  <- echo-dhulikhel sent a message (1 unread)
```

When multiple unread from same sender:
```
  <- echo-dhulikhel sent a message (3 unread)
```

### Prompt Format

```
[echo-dhulikhel] you> _
```

Bracket prefix shows active conversation. If no active conversation, prompt is just `you> `.

## 2. Discovery Fast Recovery

### Goal

After ION restarts (contacts/ranges/plans wiped from shared memory), the discovery daemon should re-inject cached node data from `discovered_nodes.json` so the network is usable immediately rather than waiting for the next full scan cycle.

### Mechanism

On daemon startup:

1. Load `discovered_nodes.json`.
2. Query ION contacts via `ionadmin l contact`.
3. If ION has significantly fewer contacts than cached nodes, trigger re-injection.
4. For each cached node within staleness threshold:
   - If `reachable_via == "direct"` and `outduct` is set: add contact, range, and plan via ionadmin/ipnadmin.
   - If `reachable_via == "gateway"`: add contact and range (routed via existing gateway plan).
   - Skip nodes with `last_seen` older than `node_staleness_days`.
5. Log all re-injected nodes.
6. Continue normal scan cycle.

### Schema Addition to discovered_nodes.json

Add `outduct` field to node entries so plans can be reconstructed:

```json
{
  "268485000": {
    "ipn": "268485000",
    "name": "DTNGW",
    "outduct": "100.96.108.37:4556",
    "reachable_via": "direct",
    "last_seen": "2026-05-14T18:40:00",
    "first_seen": "2026-05-01T12:00:00",
    "source": "local-dtnex",
    "neighbors": ["268485091", "268485032"]
  }
}
```

The `outduct` field is populated during scans by reading `ipnadmin l plan` output.

### Staleness & Pruning

| Threshold | Action |
|-----------|--------|
| `node_staleness_days` (default 7) | Nodes not seen within this period are NOT re-injected into ION on restart. |
| `node_prune_days` (default 30) | Nodes not seen within this period are REMOVED from `discovered_nodes.json` entirely. |

Both are configurable in `discovery.conf`.

### New Config Keys

```ini
node_staleness_days=7
node_prune_days=30
```

### Re-injection Trigger

Rather than detecting ION restart explicitly, compare:
- `cached_count` = nodes in discovered_nodes.json within staleness threshold
- `ion_count` = unique nodes in `ionadmin l contact`

If `ion_count < cached_count * 0.5`, trigger re-injection. This handles both fresh restarts (0 contacts) and partial state loss.

## 3. Files Changed

| File | Change |
|------|--------|
| `dtn` (main CLI) | Rewrite `cmd_chat()`, `receiver_thread()`, `send_bundle()`. Add `ChatHistory` class. Add `/to`, `/list`, `/history` commands. Update prompt format. |
| `dtn_tools/discovery.py` | Add `reinject_cached_nodes()`. Add outduct caching in scan. Add staleness pruning. Add `node_staleness_days`, `node_prune_days` config. |
| `docs/ARCHITECTURE.md` | Update chat protocol section, add history file documentation. |

## 4. Not In Scope

- Web-based chat UI (endpoint .7) — separate project.
- End-to-end encryption — out of scope for now.
- Group chat rooms — future enhancement.
- File transfer in chat — future enhancement.
