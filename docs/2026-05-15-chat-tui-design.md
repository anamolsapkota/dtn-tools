# DTN Chat TUI Design Spec

## Goal

Replace the current `input()`-based `dtn chat` with a full-screen urwid TUI featuring a split-screen layout: scrollable sidebar with separate Neighbors and Known Nodes sections, a scrollable message pane with colored output, an input bar with cursor editing, and multiple conversation-switching mechanisms (Tab focus, Alt shortcuts, /to command).

## Architecture

The TUI is built with **urwid** (pure Python, pip-installable, works on all Linux/Pi/SSH terminals). The existing `ChatHistory` class is reused unchanged — it already handles persistent JSON storage, thread-safe locking, and atomic writes. The new code replaces `ChatSession` with `ChatTUI`, a urwid-based class that manages the full-screen layout and event loop.

The receiver thread from the current implementation is preserved — it runs `bprecvfile` in a temp directory and polls for incoming bundles, feeding them into the urwid event loop via `urwid.AsyncioEventLoop` or a pipe-based wakeup mechanism.

## Tech Stack

- **urwid** — TUI framework (pure Python, `pip install urwid`)
- **ChatHistory** — existing class from `dtn_tools/chat.py` (unchanged)
- **bpsource / bprecvfile** — existing ION tools for send/receive
- **Python 3.10+** — already required

## Layout

```
┌─────────────────────────────────────────────────────────────┐
│ DTN Chat          pi05-anamol (ipn:268485091) F1 F2 F10     │  <- Title bar
├──────────────┬──────────────────────────────────────────────┤
│ ● NEIGHBORS  │ echo-dhulikhel  ipn:268485111  ● direct 3ms │  <- Chat header
│──────────────│──────────────────────────────────────────────│
│ echo-dhulikh │ — May 15, 2026 —                            │
│ DTNGW    (3) │ 14:28 echo: Hey! I see you deployed...      │  <- Messages
│ OpenIPNNode  │ 14:29 you: Yeah! Testing the TUI now        │     (scrollable)
│ EEEPC        │ 14:30 echo: Loud and clear. 0.8s            │
│ Pi5Home      │ 14:31 you: Nice! Sidebar shows 40 nodes     │
│──────────────│ 14:32 echo: Cool. Unread count updating      │
│ ◆ KNOWN (35) │                                              │
│──────────────│                                              │
│ SatPI  2 hop │                                              │
│ BPv7   2 hop │──────────────────────────────────────────────│
│ MarsPI 2 hop │ ❯ Can you check the scrolling?█              │  <- Input bar
│ ...29 more ↓ │                                              │
│──────────────│                                              │
│ ● ION running│                                              │
│ 5n · 40 · 126│                                              │
├──────────────┴──────────────────────────────────────────────┤
│ echo-dhulikhel · direct · 3ms   ↑↓ Tab PgUp/Dn / Ctrl+C   │  <- Status bar
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. Title Bar
- Node name and IPN number
- Function key hints: F1 (help), F2 (refresh nodes), F10 (quit)
- Blue background, white text

### 2. Sidebar (left, ~30% width)
Split into three fixed sections, top to bottom:

**Neighbors section** (scrollable):
- Header: `● NEIGHBORS (N)` in green
- Lists nodes that have ION plans (outducts) — direct neighbors
- Each entry shows: name, unread count in gold if >0, RTT if available
- Active conversation highlighted with blue background
- Scrollable with arrow keys when sidebar is focused

**Known Nodes section** (scrollable):
- Header: `◆ KNOWN (N)` in blue
- Lists all nodes from the contact graph minus neighbors
- Each entry shows: name, hop count
- Scrollable independently
- Click/Enter to start conversation

**Network status** (fixed at bottom):
- ION running status (green/red)
- Counts: neighbors, total nodes, contacts

### 3. Chat Header
- Active conversation name + IPN
- Connection type: `● direct` (green) or `◇ N hops via DTNGW` (yellow)
- IP:port and RTT for direct neighbors

### 4. Message Pane (scrollable)
- Date separators: `— May 15, 2026 —` in dim
- Timestamps in dim gray
- Sender names color-coded: **blue** for you, **red/pink** for them
- Word-wrapped long messages
- Scrollable with PageUp/PageDown
- Auto-scrolls to bottom on new messages (unless user has scrolled up)

### 5. Input Bar
- Green prompt character `❯`
- Full cursor editing: arrow keys, Home, End, Ctrl+A/E
- Input history with Up/Down (when input is focused)
- /commands still work: /to, /list, /history, /nodes, /help, /quit

### 6. Status Bar
- Current conversation name and connection info
- Keybinding hints
- Blue background, white text

## Conversation Switching

Three mechanisms, all available simultaneously:

### Tab Focus Mode
1. Press `Tab` to move focus from input bar to sidebar
2. Sidebar border lights up (visual indicator)
3. Use `↑↓` arrow keys to navigate through Neighbors and Known Nodes
4. Press `Enter` to switch to highlighted node
5. Press `Tab` or `Escape` to return focus to input bar

### Quick Shortcuts (while in input bar)
- `Alt+↑` / `Alt+↓` — cycle to previous/next conversation (ordered by recency)
- `Alt+N` — jump to next conversation with unread messages
- These work without leaving the input bar

### /to Command (SSH-safe fallback)
- `/to echo` or `/to 268485111` or `/to 3` — same as current implementation
- Always works regardless of terminal Alt key support

### Switching Behavior
- When switching, mark all messages in new conversation as read
- Show last 20 messages from history
- Update sidebar highlight
- Save last active conversation for session resume

## Incoming Message Handling

Same logic as current implementation:
- **Active conversation**: message appears inline in message pane, marked as read
- **Other conversation**: unread count increments in sidebar, notification in status bar flashes briefly
- Receiver thread uses a pipe to wake up the urwid event loop when new messages arrive

## Keybindings Summary

| Key | Context | Action |
|-----|---------|--------|
| `Tab` | Any | Toggle focus between sidebar and input |
| `Enter` | Sidebar | Switch to highlighted conversation |
| `Enter` | Input | Send message |
| `↑↓` | Sidebar | Navigate conversations |
| `↑↓` | Input | Input history |
| `Alt+↑/↓` | Input | Cycle conversations |
| `Alt+N` | Input | Next unread conversation |
| `PageUp/Down` | Any | Scroll message pane |
| `F1` | Any | Help overlay |
| `F2` | Any | Refresh node list |
| `F10` | Any | Quit |
| `Ctrl+C` | Any | Quit |
| `Escape` | Sidebar | Return focus to input |

## File Structure

```
dtn_tools/
├── chat.py          # ChatHistory class (UNCHANGED)
├── chat_tui.py      # NEW: ChatTUI class (urwid-based full-screen UI)
└── ...
```

- `chat.py` keeps `ChatHistory` and `send_oneshot()` for non-interactive use
- `chat_tui.py` contains the new `ChatTUI` class replacing `ChatSession`
- The `dtn` CLI's `cmd_chat()` checks if stdout is a TTY: if yes, launches `ChatTUI`; if not (piped), falls back to `ChatSession` for non-interactive use

## Color Palette

| Element | Foreground | Background |
|---------|-----------|------------|
| Title bar | white | blue (#1f6feb) |
| Status bar | white | blue (#1f6feb) |
| Your messages | light blue | default |
| Their messages | light red | default |
| Timestamps | dark gray | default |
| Date separators | dark gray | default |
| Neighbor header | green | default |
| Known Nodes header | light blue | default |
| Unread count | yellow/gold | default |
| Active conversation | white | blue |
| Sidebar focused border | blue | default |
| Sidebar unfocused border | dark gray | default |
| Input prompt (❯) | green | default |
| Network status OK | green | default |
| Network status DOWN | red | default |

## Dependencies

- `urwid` — `pip install urwid` (pure Python, no C extensions)
- Already required: `requests`, Python 3.10+

## Compatibility

- Works on all terminals supporting basic ANSI (256-color preferred, falls back to 16-color)
- Works over SSH (standard terminal, no special requirements)
- Minimum terminal size: 80x24 (urwid handles resize events)
- Tested targets: Raspberry Pi OS, Ubuntu, Debian

## Non-Goals

- No mouse support in first version (urwid supports it, can add later)
- No file transfer UI (just chat messages)
- No notification sound (terminal bell could be added later)
- No encryption UI (uses existing ION security model)
