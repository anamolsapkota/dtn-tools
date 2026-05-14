# Chat Overhaul & Discovery Fast Recovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add IRC-style persistent chat with per-sender conversations and unread indicators, plus discovery-based contact re-injection after ION restart.

**Architecture:** Extract chat logic from the monolithic `dtn` CLI into a new `dtn_tools/chat.py` module containing `ChatHistory` (persistent storage) and `ChatSession` (interactive UI). Modify `discovery.py` to cache outduct info and re-inject contacts on startup. The main `dtn` CLI delegates to the new module.

**Tech Stack:** Python 3.10+, JSON file storage, ION-DTN CLI tools (bpsource, bprecvfile, ionadmin, ipnadmin)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `dtn_tools/chat.py` | **Create** | ChatHistory class, ChatSession class, receiver thread, all chat logic |
| `dtn` (main CLI) | **Modify** lines 566-800 | Replace `cmd_chat()` body with delegation to `dtn_tools/chat.py` |
| `dtn_tools/discovery.py` | **Modify** lines 248-409 | Add outduct caching, staleness pruning, `reinject_cached_nodes()` |
| `docs/ARCHITECTURE.md` | **Modify** | Update chat protocol section, add history file docs |
| `docs/DISCOVERY.md` | **Modify** | Document fast recovery and pruning |

---

### Task 1: Create ChatHistory class

**Files:**
- Create: `dtn_tools/chat.py`

This is the persistent storage layer. All chat messages are saved here, grouped by conversation (remote IPN).

- [ ] **Step 1: Create `dtn_tools/chat.py` with ChatHistory class**

```python
#!/usr/bin/env python3
"""
Persistent chat history and interactive chat session for DTN terminal chat.

History file: ~/dtn/chat-history.json
Format:
{
  "conversations": {
    "<ipn>": {
      "name": "<display name>",
      "messages": [
        {"dir": "in"|"out", "from": "<ipn>", "name": "<name>", "msg": "<text>", "ts": "<ISO8601>", "read": true|false}
      ]
    }
  },
  "last_active": "<ipn>"
}
"""

import json
import os
import tempfile
from datetime import datetime, timezone

MAX_MESSAGES_PER_CONVERSATION = 500

class ChatHistory:
    """Persistent chat history stored as JSON."""

    def __init__(self, history_path: str):
        self.path = history_path
        self.data = {"conversations": {}, "last_active": None}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        # Ensure structure
        if "conversations" not in self.data:
            self.data["conversations"] = {}
        if "last_active" not in self.data:
            self.data["last_active"] = None

    def save(self):
        """Atomic write: temp file + rename."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(self.path), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _ensure_conversation(self, ipn: str, name: str = ""):
        if ipn not in self.data["conversations"]:
            self.data["conversations"][ipn] = {"name": name, "messages": []}
        elif name and not self.data["conversations"][ipn].get("name"):
            self.data["conversations"][ipn]["name"] = name

    def add_message(self, remote_ipn: str, direction: str, sender_ipn: str,
                    sender_name: str, msg: str, read: bool = False):
        """Add a message to a conversation. direction is 'in' or 'out'."""
        self._ensure_conversation(remote_ipn, sender_name if direction == "in" else "")
        conv = self.data["conversations"][remote_ipn]
        conv["messages"].append({
            "dir": direction,
            "from": sender_ipn,
            "name": sender_name,
            "msg": msg,
            "ts": datetime.now(timezone.utc).isoformat(),
            "read": read,
        })
        # Prune oldest if over limit
        if len(conv["messages"]) > MAX_MESSAGES_PER_CONVERSATION:
            conv["messages"] = conv["messages"][-MAX_MESSAGES_PER_CONVERSATION:]
        self.save()

    def add_incoming(self, sender_ipn: str, sender_name: str, msg: str, read: bool = False):
        """Add an incoming message."""
        self.add_message(sender_ipn, "in", sender_ipn, sender_name, msg, read)

    def add_outgoing(self, dest_ipn: str, my_ipn: str, my_name: str, msg: str):
        """Add an outgoing message (always read)."""
        self.add_message(dest_ipn, "out", my_ipn, my_name, msg, read=True)

    def mark_read(self, ipn: str):
        """Mark all messages in a conversation as read."""
        conv = self.data["conversations"].get(ipn)
        if conv:
            for m in conv["messages"]:
                m["read"] = True
            self.save()

    def unread_count(self, ipn: str) -> int:
        conv = self.data["conversations"].get(ipn)
        if not conv:
            return 0
        return sum(1 for m in conv["messages"] if not m.get("read", True))

    def all_unread(self) -> dict:
        """Return {ipn: unread_count} for conversations with unread messages."""
        result = {}
        for ipn, conv in self.data["conversations"].items():
            count = sum(1 for m in conv["messages"] if not m.get("read", True))
            if count > 0:
                result[ipn] = count
        return result

    def get_recent(self, ipn: str, n: int = 20) -> list:
        """Get the last N messages from a conversation."""
        conv = self.data["conversations"].get(ipn)
        if not conv:
            return []
        return conv["messages"][-n:]

    def conversation_name(self, ipn: str) -> str:
        conv = self.data["conversations"].get(ipn)
        return conv.get("name", "") if conv else ""

    def set_last_active(self, ipn: str):
        self.data["last_active"] = ipn
        self.save()

    def get_last_active(self) -> str:
        return self.data.get("last_active")

    def list_conversations(self) -> list:
        """Return list of (ipn, name, unread_count, last_message_ts) sorted by recency."""
        result = []
        for ipn, conv in self.data["conversations"].items():
            name = conv.get("name", "")
            unread = sum(1 for m in conv["messages"] if not m.get("read", True))
            last_ts = conv["messages"][-1]["ts"] if conv["messages"] else ""
            result.append((ipn, name, unread, last_ts))
        # Sort by last message timestamp descending
        result.sort(key=lambda x: x[3], reverse=True)
        return result
```

- [ ] **Step 2: Verify file created**

Run: `python3 -c "import sys; sys.path.insert(0, '/tmp/dtn-tools'); from dtn_tools.chat import ChatHistory; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /tmp/dtn-tools
git add dtn_tools/chat.py
git commit -m "feat: add ChatHistory class for persistent per-sender chat storage"
```

---

### Task 2: Add ChatSession (interactive UI) to chat.py

**Files:**
- Modify: `dtn_tools/chat.py`

Add the interactive chat session with receiver thread, notification system, and slash commands.

- [ ] **Step 1: Add ChatSession class to `dtn_tools/chat.py`**

Append to the file after the ChatHistory class:

```python
import os
import re
import subprocess
import threading
import time


def _run(cmd, timeout=30):
    """Run a shell command and return (stdout, stderr, returncode)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 1


def _run_admin(admin, commands):
    """Run an ION admin program with piped commands."""
    try:
        r = subprocess.run(
            [admin], input=commands, capture_output=True, text=True, timeout=15
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except Exception:
        return "", "", 1


class ChatSession:
    """Interactive IRC-style DTN chat session."""

    CHAT_SVC = "5"

    def __init__(self, my_ipn: str, dtn_dir: str, discovery_db: str):
        self.my_ipn = my_ipn
        self.dtn_dir = dtn_dir
        self.discovery_db = discovery_db
        self.my_name = os.environ.get("USER", "unknown")
        self.recv_eid = f"ipn:{my_ipn}.{self.CHAT_SVC}"

        # History
        history_path = os.path.join(dtn_dir, "chat-history.json")
        self.history = ChatHistory(history_path)

        # State
        self.active_ipn = None  # currently chatting with
        self.running = False
        self.node_names = {}    # ipn -> name
        self.node_list = []     # ordered list for number selection
        self.plans = set()      # IPNs with direct plans

        # Load node names from discovery DB
        self._load_node_names()

    def _load_node_names(self):
        if os.path.exists(self.discovery_db):
            try:
                with open(self.discovery_db) as f:
                    for ipn, info in json.load(f).get("nodes", {}).items():
                        if info.get("name"):
                            self.node_names[ipn] = info["name"]
            except Exception:
                pass

    def node_label(self, ipn: str) -> str:
        name = self.node_names.get(ipn, "") or self.history.conversation_name(ipn)
        return f"{name} (ipn:{ipn})" if name else f"ipn:{ipn}"

    def node_short(self, ipn: str) -> str:
        return self.node_names.get(ipn, "") or self.history.conversation_name(ipn) or f"ipn:{ipn}"

    def send_bundle(self, dest_ipn: str, msg: str) -> bool:
        dest = f"ipn:{dest_ipn}.{self.CHAT_SVC}"
        payload = json.dumps({
            "from": self.my_ipn,
            "name": self.my_name,
            "msg": msg,
            "ts": time.strftime("%H:%M:%S"),
        })
        _, _, rc = _run(f"bpsource {dest} '{payload}' 2>/dev/null")
        if rc == 0:
            self.history.add_outgoing(dest_ipn, self.my_ipn, self.my_name, msg)
        return rc == 0

    def receiver_thread(self, recv_dir: str):
        """Background: receive bundles and route to conversations."""
        proc = subprocess.Popen(
            ["bprecvfile", self.recv_eid],
            cwd=recv_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        while self.running:
            time.sleep(0.5)
            # Restart bprecvfile if it died
            if proc.poll() is not None and self.running:
                proc = subprocess.Popen(
                    ["bprecvfile", self.recv_eid],
                    cwd=recv_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            # Poll for new files
            try:
                for fname in sorted(os.listdir(recv_dir)):
                    fpath = os.path.join(recv_dir, fname)
                    if not os.path.isfile(fpath):
                        continue
                    try:
                        with open(fpath) as f:
                            content = f.read().strip()
                        os.unlink(fpath)
                        if not content:
                            continue
                        self._handle_incoming(content)
                    except Exception:
                        pass
            except Exception:
                pass
        proc.terminate()

    def _handle_incoming(self, content: str):
        """Process an incoming bundle — route to conversation, print or notify."""
        try:
            data = json.loads(content)
            sender_ipn = str(data.get("from", "?"))
            sender_name = data.get("name", "")
            msg = data.get("msg", content)
            ts = data.get("ts", "")
        except json.JSONDecodeError:
            # Plain text bundle — unknown sender
            sender_ipn = "unknown"
            sender_name = ""
            msg = content
            ts = time.strftime("%H:%M:%S")

        # Update node name if we learned one
        if sender_name and sender_ipn != "unknown":
            self.node_names[sender_ipn] = sender_name

        if sender_ipn == self.active_ipn:
            # Active conversation — print and mark read
            self.history.add_incoming(sender_ipn, sender_name, msg, read=True)
            label = sender_name or self.node_short(sender_ipn)
            print(f"\r  [{ts}] {label}: {msg}")
            self._print_prompt()
        else:
            # Other conversation — save as unread, show notification
            self.history.add_incoming(sender_ipn, sender_name, msg, read=False)
            unread = self.history.unread_count(sender_ipn)
            label = sender_name or self.node_short(sender_ipn)
            print(f"\r  <- {label} sent a message ({unread} unread)")
            self._print_prompt()

    def _print_prompt(self):
        if self.active_ipn:
            tag = self.node_short(self.active_ipn)
            print(f"  [{tag}] you> ", end="", flush=True)
        else:
            print(f"  you> ", end="", flush=True)

    def _fetch_node_list(self) -> list:
        """Get all nodes from ION contact graph."""
        out, _, _ = _run_admin("ionadmin", "l contact\nq")
        all_ipns = set()
        for line in out.splitlines():
            m = re.search(r"node\s+(\d+)\s+to\s+node\s+(\d+)", line)
            if m:
                all_ipns.add(m.group(1))
                all_ipns.add(m.group(2))
        all_ipns.discard(self.my_ipn)

        # Get plans
        plan_out, _, _ = _run_admin("ipnadmin", "l plan\nq")
        self.plans = set()
        for line in plan_out.splitlines():
            line = line.strip().lstrip(":").strip()
            m = re.match(r"(\d+)\s+xmit", line)
            if m and m.group(1) != self.my_ipn:
                self.plans.add(m.group(1))

        self.node_list = sorted(all_ipns, key=lambda x: int(x))
        return self.node_list

    def show_nodes(self):
        nodes = self._fetch_node_list()
        if nodes:
            print()
            print(f"  Available nodes ({len(nodes)}):")
            for i, ipn in enumerate(nodes, 1):
                tag = " *" if ipn in self.plans else ""
                unread = self.history.unread_count(ipn)
                unread_str = f"  [{unread} unread]" if unread > 0 else ""
                print(f"    {i:>3}. {self.node_label(ipn)}{tag}{unread_str}")
            print()
            print("  * = direct neighbor.  All others routed via contact graph.")
        else:
            print("  No nodes found. Is ION running?")

    def show_conversations(self):
        """Show /list — all conversations with unread counts."""
        convos = self.history.list_conversations()
        if not convos:
            print("  No conversations yet.")
            return
        print()
        print("  Conversations:")
        for i, (ipn, name, unread, last_ts) in enumerate(convos, 1):
            label = name or f"ipn:{ipn}"
            tag = " *" if ipn in self.plans else ""
            active = " <- active" if ipn == self.active_ipn else ""
            unread_str = f"  [{unread} unread]" if unread > 0 else ""
            # Format timestamp
            ts_short = ""
            if last_ts:
                try:
                    dt = datetime.fromisoformat(last_ts)
                    ts_short = dt.strftime("%H:%M")
                except ValueError:
                    ts_short = last_ts[:5]
            print(f"    {i:>3}. {label}{tag} — {ts_short}{unread_str}{active}")
        print()

    def show_history(self, n: int = 20):
        """Show recent messages in the active conversation."""
        if not self.active_ipn:
            print("  No active conversation. Use /to <node> to select one.")
            return
        messages = self.history.get_recent(self.active_ipn, n)
        if not messages:
            print("  No messages yet.")
            return
        for m in messages:
            ts = ""
            if m.get("ts"):
                try:
                    dt = datetime.fromisoformat(m["ts"])
                    ts = dt.strftime("%H:%M:%S")
                except ValueError:
                    ts = m["ts"]
            if m.get("dir") == "out":
                print(f"  [{ts}] you: {m['msg']}")
            else:
                label = m.get("name") or self.node_short(m.get("from", "?"))
                print(f"  [{ts}] {label}: {m['msg']}")

    def switch_to(self, target: str) -> bool:
        """Switch active conversation. target can be a number, IPN, or name."""
        target = target.strip().replace("ipn:", "")

        # Try as conversation list number
        convos = self.history.list_conversations()
        if target.isdigit():
            idx = int(target)
            # Try node list first
            if 1 <= idx <= len(self.node_list):
                ipn = self.node_list[idx - 1]
                self.active_ipn = ipn
                self.history.mark_read(ipn)
                self.history.set_last_active(ipn)
                print(f"\n  Now chatting with: {self.node_label(ipn)}")
                self.show_history()
                return True
            # Try as raw IPN
            if len(target) >= 6:
                self.active_ipn = target
                self.history.mark_read(target)
                self.history.set_last_active(target)
                print(f"\n  Now chatting with: {self.node_label(target)}")
                self.show_history()
                return True

        # Try as name match
        for ipn, name in self.node_names.items():
            if name.lower() == target.lower():
                self.active_ipn = ipn
                self.history.mark_read(ipn)
                self.history.set_last_active(ipn)
                print(f"\n  Now chatting with: {self.node_label(ipn)}")
                self.show_history()
                return True

        # Try as raw IPN
        if target.isdigit() and len(target) >= 6:
            self.active_ipn = target
            self.history.mark_read(target)
            self.history.set_last_active(target)
            print(f"\n  Now chatting with: {self.node_label(target)}")
            self.show_history()
            return True

        print(f"  Unknown node: {target}")
        return False

    def run_interactive(self, initial_dest: str = None):
        """Main interactive chat loop."""
        import tempfile as _tempfile

        recv_dir = _tempfile.mkdtemp(prefix="dtn-chat-")
        self.running = True

        # Ensure endpoint
        _run_admin("bpadmin", f"a endpoint {self.recv_eid} q\nq\n")

        # Header
        print("=" * 60)
        print("  DTN Terminal Chat")
        print(f"  Your node: {self.node_label(self.my_ipn)}")
        print(f"  Listening on: {self.recv_eid}")
        print("=" * 60)

        # Show unread summary
        unread = self.history.all_unread()
        if unread:
            print()
            print("  Unread messages:")
            for ipn, count in unread.items():
                print(f"    {self.node_label(ipn)}: {count} unread")

        # Select destination
        if initial_dest:
            self.active_ipn = initial_dest.replace("ipn:", "")
            self.history.mark_read(self.active_ipn)
            self.history.set_last_active(self.active_ipn)
        elif self.history.get_last_active():
            last = self.history.get_last_active()
            print(f"\n  Last conversation: {self.node_label(last)}")
            try:
                choice = input("  Resume? (Y/n or enter node #): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Cancelled.")
                return
            if not choice or choice.lower() in ("y", "yes"):
                self.active_ipn = last
                self.history.mark_read(last)
            elif choice.lower() not in ("n", "no"):
                self.switch_to(choice)

        if not self.active_ipn:
            self.show_nodes()
            while True:
                try:
                    choice = input("\n  Select node to chat with: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\n  Cancelled.")
                    return
                if not choice:
                    continue
                if self.switch_to(choice):
                    break

        print()
        print(f"  Chatting with: {self.node_label(self.active_ipn)}")
        print("  Commands: /to /list /history /nodes /help /quit")
        print("-" * 60)

        # Show recent history for active conversation
        self.show_history()

        # Start receiver
        recv_thread = threading.Thread(target=self.receiver_thread, args=(recv_dir,), daemon=True)
        recv_thread.start()

        try:
            while True:
                try:
                    tag = self.node_short(self.active_ipn) if self.active_ipn else ""
                    prompt = f"  [{tag}] you> " if tag else "  you> "
                    line = input(prompt).strip()
                except EOFError:
                    break

                if not line:
                    continue

                # Commands
                cmd = line.lstrip("/").lower()
                if cmd in ("quit", "exit", "q"):
                    break
                elif cmd.startswith("to "):
                    self.switch_to(cmd[3:])
                    continue
                elif cmd == "list":
                    self.show_conversations()
                    continue
                elif cmd == "nodes":
                    self.show_nodes()
                    continue
                elif cmd.startswith("history"):
                    parts = cmd.split()
                    n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
                    self.show_history(n)
                    continue
                elif cmd == "help":
                    print("  Commands:")
                    print("    /to <name|#|IPN>  — switch conversation")
                    print("    /list             — show all conversations with unread counts")
                    print("    /history [N]      — show last N messages (default 20)")
                    print("    /nodes            — list all nodes from contact graph")
                    print("    /quit             — exit chat")
                    continue
                elif line.startswith("/"):
                    print("  Unknown command. Type /help for commands.")
                    continue

                # Send message
                if not self.active_ipn:
                    print("  No active conversation. Use /to <node> first.")
                    continue

                if self.send_bundle(self.active_ipn, line):
                    pass  # sent and saved to history
                else:
                    print("  [send failed]")

        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            self.history.set_last_active(self.active_ipn)
            print("\n  Chat ended.")
            import shutil
            shutil.rmtree(recv_dir, ignore_errors=True)

    def send_oneshot(self, dest_ipn: str, msg: str):
        """Send a message without entering interactive mode."""
        dest_ipn = dest_ipn.replace("ipn:", "")
        print(f"  Sending to {self.node_label(dest_ipn)}: {msg}")
        if self.send_bundle(dest_ipn, msg):
            print("  Sent.")
        else:
            print("  Failed.")
```

- [ ] **Step 2: Verify import**

Run: `python3 -c "import sys; sys.path.insert(0, '/tmp/dtn-tools'); from dtn_tools.chat import ChatHistory, ChatSession; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
cd /tmp/dtn-tools
git add dtn_tools/chat.py
git commit -m "feat: add ChatSession with IRC-style conversation switching and notifications"
```

---

### Task 3: Replace cmd_chat in main CLI

**Files:**
- Modify: `dtn` lines 566-800

Replace the existing `cmd_chat()` function body with delegation to the new `ChatSession`.

- [ ] **Step 1: Replace cmd_chat function**

Replace lines 566-800 in `dtn` with:

```python
def cmd_chat(args):
    """Interactive terminal chat over DTN bundles."""
    detect_ipn()

    mod = _import_module("chat", "chat.py")
    session = mod.ChatSession(MY_IPN, DTN_DIR, DISCOVERY_DB)

    dest = None
    if hasattr(args, "dest") and args.dest:
        dest = args.dest.replace("ipn:", "")

    if dest and hasattr(args, "message") and args.message:
        # One-shot mode
        session.send_oneshot(dest, " ".join(args.message))
    else:
        # Interactive mode
        session.run_interactive(initial_dest=dest)
```

- [ ] **Step 2: Test on Pi05**

Deploy to Pi05 and run:
```bash
dtn chat
```
Verify:
- Shows unread messages on startup
- Shows last conversation resume prompt
- `/list` shows conversations with unread counts
- `/to` switches and shows history
- Messages from active sender print inline
- Messages from other senders show notification only

- [ ] **Step 3: Commit**

```bash
cd /tmp/dtn-tools
git add dtn
git commit -m "feat: replace flat chat with IRC-style persistent conversations"
```

---

### Task 4: Add outduct caching to discovery.py

**Files:**
- Modify: `dtn_tools/discovery.py` lines 190-223, 307-409

During scans, also read `ipnadmin l plan` and store outduct IP:port for each known node. This is needed for Task 5 (re-injection).

- [ ] **Step 1: Add outduct caching to `get_ion_known_nodes()`**

Find the existing `get_ion_known_nodes()` function (around line 190) and modify it to also return outduct info. Current function returns a set of IPNs. Change it to return a dict of `{ipn: outduct_str}`.

Then in `run_scan()` (around line 370-387), when updating `state["nodes"]`, also store the outduct:

```python
# In the existing loop where state["nodes"] is updated:
if ipn in ion_plans:
    existing["outduct"] = ion_plans[ipn]
```

- [ ] **Step 2: Add `node_staleness_days` and `node_prune_days` to DEFAULTS**

Add to the DEFAULTS dict (around line 41):
```python
"node_staleness_days": "7",
"node_prune_days": "30",
```

- [ ] **Step 3: Add pruning at end of `run_scan()`**

After the scan merge loop, before saving, prune old nodes:

```python
# Prune nodes not seen in prune_days
prune_days = int(cfg.get("node_prune_days", "30"))
now_dt = datetime.now(timezone.utc)
to_prune = []
for ipn, info in state["nodes"].items():
    last_seen = info.get("last_seen", "")
    if last_seen:
        try:
            ls_dt = datetime.fromisoformat(last_seen)
            if (now_dt - ls_dt).days > prune_days:
                to_prune.append(ipn)
        except ValueError:
            pass
for ipn in to_prune:
    logging.info("Pruning stale node ipn:%s (not seen in %d days)", ipn, prune_days)
    del state["nodes"][ipn]
```

- [ ] **Step 4: Commit**

```bash
cd /tmp/dtn-tools
git add dtn_tools/discovery.py
git commit -m "feat: cache outduct info and prune stale nodes in discovery"
```

---

### Task 5: Add `reinject_cached_nodes()` to discovery.py

**Files:**
- Modify: `dtn_tools/discovery.py`

Add a function that runs on daemon startup to re-inject cached nodes into ION after a restart.

- [ ] **Step 1: Add `reinject_cached_nodes()` function**

Add before `main()`:

```python
def reinject_cached_nodes(cfg: dict, state: dict):
    """Re-inject cached nodes into ION after a restart.

    Compares ION's current contacts with cached nodes. If ION has significantly
    fewer contacts, re-inject nodes seen within staleness threshold.
    """
    staleness_days = int(cfg.get("node_staleness_days", "7"))
    my_ipn = cfg["my_ipn"]
    gw_ipn = cfg["gateway_ipn"]
    now_dt = datetime.now(timezone.utc)

    # Count current ION contacts
    ion_known = get_ion_known_nodes()
    ion_count = len(ion_known)

    # Count eligible cached nodes
    eligible = {}
    for ipn, info in state.get("nodes", {}).items():
        if ipn in (my_ipn, gw_ipn):
            continue
        last_seen = info.get("last_seen", "")
        if not last_seen:
            continue
        try:
            ls_dt = datetime.fromisoformat(last_seen)
            if (now_dt - ls_dt).days <= staleness_days:
                eligible[ipn] = info
        except ValueError:
            continue

    if not eligible:
        logging.info("No cached nodes eligible for re-injection")
        return

    # Only re-inject if ION has significantly fewer contacts
    if ion_count >= len(eligible) * 0.5:
        logging.info("ION has %d nodes, cache has %d eligible — no re-injection needed",
                     ion_count, len(eligible))
        return

    logging.info("ION has %d nodes but cache has %d eligible — re-injecting contacts",
                 ion_count, len(eligible))

    injected = 0
    for ipn, info in eligible.items():
        if ipn in ion_known:
            continue

        reachable = info.get("reachable_via", "unknown")
        outduct = info.get("outduct", "")

        if reachable == "direct" and outduct:
            # Re-add full plan: contact + range + outduct + plan
            rate = cfg["contact_rate"]
            duration = cfg["contact_duration"]
            owlt = cfg["owlt"]
            ip_port = outduct
            cmds = [
                f"a contact +1 +{duration} {my_ipn} {ipn} {rate}",
                f"a contact +1 +{duration} {ipn} {my_ipn} {rate}",
                f"a range +1 +{duration} {my_ipn} {ipn} {owlt}",
                f"a range +1 +{duration} {ipn} {my_ipn} {owlt}",
            ]
            ion_command("ionadmin", cmds)

            # Add outduct and plan
            bp_cmds = [f"a outduct udp {ip_port} udpclo"]
            ion_command("bpadmin", bp_cmds)
            plan_cmds = [f"a plan {ipn} udp/{ip_port}"]
            ion_command("ipnadmin", plan_cmds)

            logging.info("Re-injected direct neighbor ipn:%s via %s", ipn, ip_port)
            injected += 1

        elif reachable == "gateway":
            # Gateway-routed: just add contact + range
            ok = add_node_via_gateway(ipn, cfg)
            if ok:
                logging.info("Re-injected gateway-routed ipn:%s", ipn)
                injected += 1

    logging.info("Re-injection complete: %d nodes re-injected into ION", injected)
```

- [ ] **Step 2: Call `reinject_cached_nodes()` in `main()` after loading state**

In `main()`, after `state = load_discovered(cfg["discovered_db"])` (around line 456), add:

```python
    # Re-inject cached nodes if ION was restarted
    reinject_cached_nodes(cfg, state)
```

- [ ] **Step 3: Commit**

```bash
cd /tmp/dtn-tools
git add dtn_tools/discovery.py
git commit -m "feat: re-inject cached contacts into ION after restart from discovery DB"
```

---

### Task 6: Update documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DISCOVERY.md`
- Modify: `README.md`

- [ ] **Step 1: Update ARCHITECTURE.md**

In the "Terminal Chat Protocol" section (around line 158), update:

- Change message format to include `dir` field
- Add section about `chat-history.json` file format
- Document the conversation switching flow
- Update the receiving flow to mention per-sender routing and notifications

Add a new "Chat History" subsection under "Data Files":

```
| `chat-history.json` | `~/dtn/` | Persistent chat history with per-sender conversations |
```

- [ ] **Step 2: Update DISCOVERY.md**

Add a section about fast recovery:

```markdown
### Fast Recovery After ION Restart

When the discovery daemon starts, it checks if ION has significantly fewer
contacts than the cached node database. If so, it re-injects contacts for
nodes seen within the staleness threshold (default: 7 days).

Nodes not seen in 30 days are pruned from the database entirely.

Configuration:
- `node_staleness_days=7` — re-inject nodes seen within this many days
- `node_prune_days=30` — remove nodes not seen within this many days
```

- [ ] **Step 3: Update README.md**

Update the `dtn chat` description to mention conversations:
```
| `dtn chat [IPN]` | Interactive chat with per-sender conversations, unread indicators |
```

- [ ] **Step 4: Commit**

```bash
cd /tmp/dtn-tools
git add docs/ARCHITECTURE.md docs/DISCOVERY.md README.md
git commit -m "docs: update architecture and discovery docs for chat overhaul and fast recovery"
```

---

### Task 7: Deploy and test on real nodes

- [ ] **Step 1: Deploy to Pi05**

```bash
scp dtn dtn_tools/chat.py dtn_tools/discovery.py pi05@10.16.16.169:/tmp/dtn-update/
ssh pi05@10.16.16.169 'sudo cp /tmp/dtn-update/dtn /usr/local/bin/dtn && cp /tmp/dtn-update/chat.py ~/dtn-tools/dtn_tools/ && cp /tmp/dtn-update/discovery.py ~/dtn-tools/dtn_tools/'
```

- [ ] **Step 2: Test interactive chat on Pi05**

```bash
dtn chat
```

Verify:
- Resume last conversation prompt appears
- `/list` shows conversations
- Send a message, verify it saves to `~/dtn/chat-history.json`
- `/to echo-dhulikhel` switches and shows history
- `/history 5` shows last 5 messages

- [ ] **Step 3: Test cross-node messaging**

From Pi05: `dtn chat 268485111 "test from pi05"`
From Echo: `dtn chat 268485091 "test from echo"`

Verify messages appear in both nodes' history files.

- [ ] **Step 4: Test discovery re-injection**

On Pi05:
```bash
dtn stop ion
dtn start ion
# Watch discovery logs for re-injection
journalctl -u dtn-discovery -f
```

Verify: "Re-injecting contacts" appears and nodes are re-added.

- [ ] **Step 5: Push to GitHub**

```bash
cd /tmp/dtn-tools
git push origin main
```
