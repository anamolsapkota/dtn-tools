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
        {"dir": "in"|"out", "from": "<ipn>", "name": "<name>",
         "msg": "<text>", "ts": "<ISO8601>", "read": true|false}
      ]
    }
  },
  "last_active": "<ipn>"
}
"""

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

MAX_MESSAGES_PER_CONVERSATION = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# ChatHistory — persistent storage
# ---------------------------------------------------------------------------

class ChatHistory:
    """Persistent chat history stored as JSON."""

    def __init__(self, history_path: str):
        self.path = history_path
        self.data = {"conversations": {}, "last_active": None}
        self._lock = threading.Lock()
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self.data = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
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
        with self._lock:
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
        self.add_message(sender_ipn, "in", sender_ipn, sender_name, msg, read)

    def add_outgoing(self, dest_ipn: str, my_ipn: str, my_name: str, msg: str):
        self.add_message(dest_ipn, "out", my_ipn, my_name, msg, read=True)

    def mark_read(self, ipn: str):
        with self._lock:
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
        result = {}
        for ipn, conv in self.data["conversations"].items():
            count = sum(1 for m in conv["messages"] if not m.get("read", True))
            if count > 0:
                result[ipn] = count
        return result

    def get_recent(self, ipn: str, n: int = 20) -> list:
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
        result.sort(key=lambda x: x[3], reverse=True)
        return result


# ---------------------------------------------------------------------------
# ChatSession — interactive UI
# ---------------------------------------------------------------------------

class ChatSession:
    """Interactive IRC-style DTN chat session."""

    CHAT_SVC = "5"

    def __init__(self, my_ipn: str, dtn_dir: str, discovery_db: str):
        self.my_ipn = my_ipn
        self.dtn_dir = dtn_dir
        self.discovery_db = discovery_db
        self.my_name = os.environ.get("USER", "unknown")
        self.recv_eid = f"ipn:{my_ipn}.{self.CHAT_SVC}"

        history_path = os.path.join(dtn_dir, "chat-history.json")
        self.history = ChatHistory(history_path)

        self.active_ipn = None
        self.running = False
        self.node_names = {}
        self.node_list = []
        self.plans = set()

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
        # Escape single quotes in payload for shell
        escaped = payload.replace("'", "'\\''")
        _, _, rc = _run(f"bpsource {dest} '{escaped}' 2>/dev/null")
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
            if proc.poll() is not None and self.running:
                proc = subprocess.Popen(
                    ["bprecvfile", self.recv_eid],
                    cwd=recv_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
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
            sender_ipn = "unknown"
            sender_name = ""
            msg = content
            ts = time.strftime("%H:%M:%S")

        if sender_name and sender_ipn != "unknown":
            self.node_names[sender_ipn] = sender_name

        if sender_ipn == self.active_ipn:
            self.history.add_incoming(sender_ipn, sender_name, msg, read=True)
            label = sender_name or self.node_short(sender_ipn)
            print(f"\r  [{ts}] {label}: {msg}")
            self._print_prompt()
        else:
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
        out, _, _ = _run_admin("ionadmin", "l contact\nq")
        all_ipns = set()
        for line in out.splitlines():
            m = re.search(r"node\s+(\d+)\s+to\s+node\s+(\d+)", line)
            if m:
                all_ipns.add(m.group(1))
                all_ipns.add(m.group(2))
        all_ipns.discard(self.my_ipn)

        plan_out, _, _ = _run_admin("ipnadmin", "l plan\nq")
        self.plans = set()
        for line in plan_out.splitlines():
            line = line.strip().lstrip(":").strip()
            m = re.match(r"(\d+)\s+xmit", line)
            if m and m.group(1) != self.my_ipn:
                self.plans.add(m.group(1))

        # Also include discovered nodes from discovery DB
        if os.path.exists(self.discovery_db):
            try:
                with open(self.discovery_db) as f:
                    for ipn, info in json.load(f).get("nodes", {}).items():
                        if ipn != self.my_ipn:
                            all_ipns.add(ipn)
                            if info.get("name") and ipn not in self.node_names:
                                self.node_names[ipn] = info["name"]
            except Exception:
                pass

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
        if not self.active_ipn:
            print("  No active conversation. Use /to <node> to select one.")
            return
        messages = self.history.get_recent(self.active_ipn, n)
        if not messages:
            print("  No messages yet in this conversation.")
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
        target = target.strip().replace("ipn:", "")

        # Try as node list number
        if target.isdigit():
            idx = int(target)
            if 1 <= idx <= len(self.node_list):
                ipn = self.node_list[idx - 1]
                self._activate(ipn)
                return True
            # Could be a raw IPN
            if len(target) >= 6:
                self._activate(target)
                return True

        # Try as name match
        for ipn, name in self.node_names.items():
            if name.lower() == target.lower():
                self._activate(ipn)
                return True

        # Try partial name match
        for ipn, name in self.node_names.items():
            if target.lower() in name.lower():
                self._activate(ipn)
                return True

        # Try as raw IPN
        if target.isdigit() and len(target) >= 6:
            self._activate(target)
            return True

        print(f"  Unknown node: {target}")
        return False

    def _activate(self, ipn: str):
        self.active_ipn = ipn
        self.history.mark_read(ipn)
        self.history.set_last_active(ipn)
        print(f"\n  Now chatting with: {self.node_label(ipn)}")
        self.show_history()

    def run_interactive(self, initial_dest: str = None):
        """Main interactive chat loop."""
        recv_dir = tempfile.mkdtemp(prefix="dtn-chat-")
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
            initial_dest = initial_dest.replace("ipn:", "")
            self._activate(initial_dest)
        elif self.history.get_last_active():
            last = self.history.get_last_active()
            print(f"\n  Last conversation: {self.node_label(last)}")
            try:
                choice = input("  Resume? (Y/n or enter node #): ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n  Cancelled.")
                return
            if not choice or choice.lower() in ("y", "yes"):
                self._activate(last)
            elif choice.lower() not in ("n", "no"):
                if not self.switch_to(choice):
                    self.show_nodes()
                    self._select_node()
            else:
                self.show_nodes()
                self._select_node()
        else:
            self.show_nodes()
            self._select_node()

        if not self.active_ipn:
            return

        print()
        print(f"  Chatting with: {self.node_label(self.active_ipn)}")
        print("  Commands: /to /list /history /nodes /help /quit")
        print("-" * 60)

        # Start receiver
        recv_thread = threading.Thread(
            target=self.receiver_thread, args=(recv_dir,), daemon=True
        )
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
                    print("    /list             — show conversations with unread counts")
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

                if not self.send_bundle(self.active_ipn, line):
                    print("  [send failed]")

        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            if self.active_ipn:
                self.history.set_last_active(self.active_ipn)
            print("\n  Chat ended.")
            import shutil
            shutil.rmtree(recv_dir, ignore_errors=True)

    def _select_node(self):
        """Prompt user to select a node."""
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

    def send_oneshot(self, dest_ipn: str, msg: str):
        """Send a message without entering interactive mode."""
        dest_ipn = dest_ipn.replace("ipn:", "")
        print(f"  Sending to {self.node_label(dest_ipn)}: {msg}")
        if self.send_bundle(dest_ipn, msg):
            print("  Sent.")
        else:
            print("  Failed.")
