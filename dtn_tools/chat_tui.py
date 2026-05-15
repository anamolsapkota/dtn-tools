#!/usr/bin/env python3
"""
Full-screen urwid TUI for DTN chat.

Replaces ChatSession for interactive use while reusing ChatHistory for
persistent message storage.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

import urwid

from dtn_tools.chat import ChatHistory

# ---------------------------------------------------------------------------
# Helpers (same as chat.py)
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
# Color palette
# ---------------------------------------------------------------------------

PALETTE = [
    # (name,           foreground,       background,      mono, fg_high, bg_high)
    ("default",         "white",          "black"),
    ("title_bar",       "white,bold",     "dark blue"),
    ("status_bar",      "white",          "dark gray"),
    ("msg_you",         "light cyan",     "black"),
    ("msg_them",        "light green",    "black"),
    ("msg_ts",          "dark gray",      "black"),
    ("date_sep",        "yellow",         "black"),
    ("neighbor_header", "white,bold",     "dark green"),
    ("known_header",    "white,bold",     "dark magenta"),
    ("unread_count",    "light red,bold", "black"),
    ("active_conv",     "white,bold",     "dark cyan"),
    ("sidebar_focused", "white,bold",     "dark blue"),
    ("sidebar_dim",     "light gray",     "black"),
    ("input_prompt",    "yellow,bold",    "black"),
    ("net_ok",          "light green",    "black"),
    ("net_down",        "light red",      "black"),
]


# ---------------------------------------------------------------------------
# SidebarEntry — selectable widget for a single node in the sidebar
# ---------------------------------------------------------------------------

class SidebarEntry(urwid.WidgetWrap):
    """A selectable sidebar entry showing node name, unread count, and extra info."""

    def __init__(self, ipn: str, name: str, unread: int = 0, extra: str = "",
                 is_active: bool = False):
        self.ipn = ipn
        self.node_name = name
        self.unread = unread
        self.extra = extra
        self.is_active = is_active

        # Build the text markup
        markup = self._build_markup()
        self._text = urwid.Text(markup)

        if is_active:
            widget = urwid.AttrMap(self._text, "active_conv", focus_map="sidebar_focused")
        else:
            widget = urwid.AttrMap(self._text, "sidebar_dim", focus_map="sidebar_focused")

        super().__init__(widget)

    def _build_markup(self):
        """Build urwid text markup with name, unread count, and extra info."""
        parts = [f" {self.node_name}"]
        if self.unread > 0:
            parts = [f" {self.node_name} ", ("unread_count", f"({self.unread})")]
        if self.extra:
            if isinstance(parts, list) and len(parts) > 1:
                parts.append(("msg_ts", f" {self.extra}"))
            else:
                parts = [f" {self.node_name} ", ("msg_ts", f"{self.extra}")]
        return parts

    def selectable(self):
        return True

    def keypress(self, size, key):
        return key


# ---------------------------------------------------------------------------
# ChatTUI — full-screen urwid interface
# ---------------------------------------------------------------------------

class ChatTUI:
    """Full-screen terminal chat UI built with urwid."""

    CHAT_SVC = "5"

    def __init__(self, my_ipn: str, dtn_dir: str, discovery_db: str,
                 dry_run: bool = False):
        self.my_ipn = my_ipn
        self.dtn_dir = dtn_dir
        self.discovery_db = discovery_db
        self.dry_run = dry_run
        self.my_name = os.environ.get("USER", "unknown")
        self.recv_eid = f"ipn:{my_ipn}.{self.CHAT_SVC}"

        # Chat history (persistent JSON)
        history_path = os.path.join(dtn_dir, "chat-history.json")
        self.history = ChatHistory(history_path)

        # Node name cache
        self.node_names = {}
        self._load_node_names()

        # Active conversation
        self.active_ipn = None
        self._last_date = None

        # UI state
        self.sidebar_focused = False
        self.plans = {}
        self.node_list = []

        # Receiver thread wakeup pipe
        self._pipe_w = None  # set by watch_pipe() in run()
        self._pending_messages = []
        self._pending_lock = threading.Lock()

        # Build the UI widget tree
        self._build_ui()

        # Main loop (created in run())
        self.loop = None

    def _load_node_names(self):
        """Load human-readable node names from the discovery database."""
        if os.path.exists(self.discovery_db):
            try:
                with open(self.discovery_db) as f:
                    for ipn, info in json.load(f).get("nodes", {}).items():
                        if info.get("name"):
                            self.node_names[ipn] = info["name"]
            except Exception:
                pass

    def _build_ui(self):
        """Construct the full urwid widget tree."""

        # --- Title bar ---
        title_left = urwid.Text(f" DTN Chat — {self.my_name} (ipn:{self.my_ipn})", align="left")
        title_right = urwid.Text("F1:Help  F2:Nodes  F10:Quit ", align="right")
        self.title_bar = urwid.AttrMap(
            urwid.Columns([title_left, title_right]),
            "title_bar",
        )

        # --- Status bar ---
        self.status_text = urwid.Text(" Ready", align="left")
        self.status_bar = urwid.AttrMap(self.status_text, "status_bar")

        # --- Sidebar: Neighbor list ---
        self.neighbor_walker = urwid.SimpleFocusListWalker([])
        self.neighbor_listbox = urwid.ListBox(self.neighbor_walker)
        self.neighbor_header_widget = urwid.AttrMap(
            urwid.Text(" Neighbors", align="left"), "neighbor_header"
        )

        # --- Sidebar: Known nodes list ---
        self.known_walker = urwid.SimpleFocusListWalker([])
        self.known_listbox = urwid.ListBox(self.known_walker)
        self.known_header_widget = urwid.AttrMap(
            urwid.Text(" Known Nodes", align="left"), "known_header"
        )

        # --- Sidebar: Network status ---
        self.net_status = urwid.AttrMap(
            urwid.Text(" NET: --", align="left"), "net_ok"
        )

        # --- Sidebar pile ---
        sidebar_pile = urwid.Pile([
            ("pack", self.neighbor_header_widget),
            ("weight", 1, self.neighbor_listbox),
            ("pack", self.known_header_widget),
            ("weight", 1, self.known_listbox),
            ("pack", self.net_status),
        ])
        self.sidebar = urwid.LineBox(sidebar_pile, title="Nodes")

        # --- Right pane: chat header ---
        self.chat_header_text = urwid.Text(" No conversation selected", align="left")
        self.chat_header = urwid.AttrMap(self.chat_header_text, "active_conv")

        # --- Right pane: message list ---
        self.msg_walker = urwid.SimpleFocusListWalker([])
        self.msg_listbox = urwid.ListBox(self.msg_walker)

        # --- Right pane: input bar ---
        self.input_edit = urwid.Edit(("input_prompt", "you> "))
        input_bar = urwid.AttrMap(self.input_edit, "default")

        # --- Right pane frame ---
        self.right_pane = urwid.Frame(
            body=self.msg_listbox,
            header=self.chat_header,
            footer=input_bar,
        )

        # --- Main columns ---
        self.columns = urwid.Columns([
            ("weight", 30, self.sidebar),
            ("weight", 70, self.right_pane),
        ])

        # --- Main frame ---
        self.frame = urwid.Frame(
            body=self.columns,
            header=self.title_bar,
            footer=self.status_bar,
        )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self):
        """Start the urwid main loop with receiver thread."""
        self.running = True

        # Kill any existing bprecvfile on our endpoint to avoid
        # "Endpoint is already open" errors
        if not self.dry_run:
            _run(f"pkill -f 'bprecvfile {self.recv_eid}' 2>/dev/null")
            time.sleep(0.5)
            _run_admin("bpadmin", f"a endpoint {self.recv_eid} q\nq\n")

        # Create temp directory for received bundles
        self.recv_dir = tempfile.mkdtemp(prefix="dtn-chat-")

        self.loop = urwid.MainLoop(
            self.frame,
            palette=PALETTE,
            unhandled_input=self._handle_input,
            handle_mouse=False,
        )
        # Register the pipe fd so the receiver thread can wake the loop
        # watch_pipe() creates its own pipe and returns the write fd
        self._pipe_w = self.loop.watch_pipe(self._on_pipe_data)

        # Start the receiver thread
        recv_thread = threading.Thread(target=self._receiver_loop, daemon=True)
        recv_thread.start()

        # Populate sidebar
        self._refresh_nodes()

        # Resume last conversation if available
        last = self.history.get_last_active()
        if last:
            self._switch_to(last)

        try:
            self.loop.run()
        finally:
            self.running = False
            if self.active_ipn:
                self.history.set_last_active(self.active_ipn)
            shutil.rmtree(self.recv_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Message pane
    # ------------------------------------------------------------------

    def _append_message(self, msg):
        """Render a message dict as urwid Text widget and append to msg_walker.

        Inserts a date separator when the date changes between messages.
        """
        ts_str = msg.get("ts", "")
        try:
            dt = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)

        date_text = dt.strftime("%Y-%m-%d")
        if date_text != self._last_date:
            sep = urwid.Text(("date_sep", f"  — {date_text} —"), align="center")
            sep._is_date_sep = True
            self.msg_walker.append(sep)
            self._last_date = date_text

        time_text = dt.strftime("%H:%M")
        direction = msg.get("dir", "in")
        msg_text = msg.get("msg", "")

        if direction == "out":
            name_attr = "msg_you"
            name_text = "you"
        else:
            name_attr = "msg_them"
            name_text = msg.get("name") or msg.get("from", "?")

        widget = urwid.Text([
            ("msg_ts", f"  {time_text} "),
            (name_attr, name_text),
            ("default", f": {msg_text}"),
        ])
        self.msg_walker.append(widget)

    def _load_conversation(self, ipn):
        """Clear the message pane and load the last 50 messages for the given IPN."""
        self.msg_walker[:] = []
        self._last_date = None

        messages = self.history.get_recent(ipn, 50)
        for msg in messages:
            self._append_message(msg)

        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        """Scroll the message listbox to the last item."""
        if len(self.msg_walker) > 0:
            self.msg_listbox.set_focus(len(self.msg_walker) - 1)

    # ------------------------------------------------------------------
    # Placeholder methods — filled in by later tasks
    # ------------------------------------------------------------------

    def _handle_input(self, key):
        """Handle unhandled keystrokes (keybindings, focus switching, etc.)."""
        if key in ("f10", "ctrl c"):
            raise urwid.ExitMainLoop()
        elif key == "f1":
            self._show_help()
        elif key == "f2":
            self._refresh_nodes()
        elif key == "tab":
            self.sidebar_focused = not self.sidebar_focused
            if self.sidebar_focused:
                self.columns.set_focus(0)
            else:
                self.columns.set_focus(1)
                self.right_pane.set_focus("footer")
            self._update_status_bar()
        elif key == "esc" and self.sidebar_focused:
            self.sidebar_focused = False
            self.columns.set_focus(1)
            self.right_pane.set_focus("footer")
        elif key == "enter" and self.sidebar_focused:
            # Find the focused sidebar entry
            widget = None
            try:
                # Try neighbor listbox first
                w = self.neighbor_listbox.get_focus()[0]
                if isinstance(w, SidebarEntry):
                    widget = w
            except (IndexError, TypeError):
                pass
            if widget is None:
                try:
                    w = self.known_listbox.get_focus()[0]
                    if isinstance(w, SidebarEntry):
                        widget = w
                except (IndexError, TypeError):
                    pass
            if widget is not None:
                self._switch_to(widget.ipn)
                self.sidebar_focused = False
                self.columns.set_focus(1)
                self.right_pane.set_focus("footer")
        elif key == "enter" and not self.sidebar_focused:
            text = self.input_edit.get_edit_text()
            if text:
                if text.startswith("/"):
                    self._process_command(text)
                else:
                    self._send_message(text)
                self.input_edit.set_edit_text("")
        elif key == "meta up":
            self._cycle_conversation(-1)
        elif key == "meta down":
            self._cycle_conversation(1)
        elif key == "meta n":
            self._jump_to_unread()
        elif key in ("page up", "page down"):
            self.msg_listbox.keypress((20,), key)

    def _on_pipe_data(self, data):
        """Called by urwid main loop when the receiver thread writes to the pipe."""
        with self._pending_lock:
            pending = list(self._pending_messages)
            self._pending_messages.clear()

        for sender_ipn, sender_name, msg_data in pending:
            if sender_ipn == self.active_ipn:
                self.history.add_incoming(sender_ipn, sender_name,
                                          msg_data["msg"], read=True)
                self._append_message(msg_data)
                self._scroll_to_bottom()
            else:
                self.history.add_incoming(sender_ipn, sender_name,
                                          msg_data["msg"], read=False)
                label = sender_name or f"ipn:{sender_ipn}"
                unread = self.history.unread_count(sender_ipn)
                self._set_status(f"New message from {label} ({unread} unread)")

        if not self.dry_run:
            self._refresh_nodes()

        return True

    def _receiver_loop(self):
        """Background thread: receive bundles and queue them for the main loop."""
        proc = subprocess.Popen(
            ["bprecvfile", self.recv_eid],
            cwd=self.recv_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            while self.running:
                time.sleep(0.5)

                # Restart bprecvfile if it died
                if proc.poll() is not None and self.running:
                    proc = subprocess.Popen(
                        ["bprecvfile", self.recv_eid],
                        cwd=self.recv_dir,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

                # Scan recv_dir for incoming bundle files
                try:
                    for fname in sorted(os.listdir(self.recv_dir)):
                        fpath = os.path.join(self.recv_dir, fname)
                        if not os.path.isfile(fpath):
                            continue
                        try:
                            with open(fpath) as f:
                                content = f.read().strip()
                            os.unlink(fpath)
                            if not content:
                                continue
                        except Exception:
                            continue

                        # Parse content as JSON
                        try:
                            data = json.loads(content)
                            sender_ipn = str(data.get("from", "unknown"))
                            sender_name = data.get("name", "")
                            msg = data.get("msg", content)
                        except (json.JSONDecodeError, ValueError):
                            sender_ipn = "unknown"
                            sender_name = ""
                            msg = content

                        # Update node name cache
                        if sender_name and sender_ipn != "unknown":
                            self.node_names[sender_ipn] = sender_name

                        # Build message data
                        msg_data = {
                            "dir": "in",
                            "from": sender_ipn,
                            "name": sender_name,
                            "msg": msg,
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "read": sender_ipn == self.active_ipn,
                        }

                        # Queue for the main loop
                        with self._pending_lock:
                            self._pending_messages.append(
                                (sender_ipn, sender_name, msg_data)
                            )

                        # Wake urwid main loop
                        os.write(self._pipe_w, b"1")
                except Exception:
                    pass
        finally:
            proc.terminate()

    def _populate_sidebar(self, neighbors, known):
        """Populate the neighbor and known node sidebar lists.

        Args:
            neighbors: {ipn: {"name": str, "outduct": str}} — direct neighbors
            known: {ipn: {"name": str, "hops": int|str}} — remote nodes
        """
        # Clear existing entries
        self.neighbor_walker[:] = []
        self.known_walker[:] = []

        # Populate neighbors sorted by name
        for ipn in sorted(neighbors, key=lambda i: neighbors[i].get("name", i).lower()):
            info = neighbors[ipn]
            name = info.get("name", ipn)
            extra = info.get("outduct", "")
            is_active = (ipn == self.active_ipn)
            # Count unread messages for this node
            unread = self.history.unread_count(ipn) if hasattr(self.history, "unread_count") else 0
            entry = SidebarEntry(ipn, name, unread=unread, extra=extra,
                                 is_active=is_active)
            self.neighbor_walker.append(entry)

        # Populate known nodes sorted by name
        for ipn in sorted(known, key=lambda i: known[i].get("name", i).lower()):
            info = known[ipn]
            name = info.get("name", ipn)
            hops = info.get("hops", "")
            extra = f"{hops}h" if hops != "" else ""
            is_active = (ipn == self.active_ipn)
            unread = self.history.unread_count(ipn) if hasattr(self.history, "unread_count") else 0
            entry = SidebarEntry(ipn, name, unread=unread, extra=extra,
                                 is_active=is_active)
            self.known_walker.append(entry)

        # Update header text with counts
        self.neighbor_header_widget.original_widget.set_text(
            f" Neighbors ({len(neighbors)})"
        )
        self.known_header_widget.original_widget.set_text(
            f" Known Nodes ({len(known)})"
        )

    def _refresh_nodes(self):
        """Re-fetch the node list from ION and update the sidebar."""
        if self.dry_run:
            return

        neighbors = {}
        known = {}
        all_nodes = set()

        # --- Get neighbors (nodes with outducts) via ipnadmin l plan ---
        plan_out, _, plan_rc = _run_admin("ipnadmin", "l plan\nq\n")
        self.plans = {}
        if plan_rc == 0 and plan_out:
            # Format: "268485000 xmit 100.96.108.37:4556 xmit rate: 0"
            # Some lines may start with ": " prefix
            for line in plan_out.splitlines():
                m = re.match(r"\s*:?\s*(\d{5,})\s+xmit\s+(\S+)", line)
                if m:
                    ipn = m.group(1)
                    outduct = m.group(2)
                    if ipn != self.my_ipn:
                        self.plans[ipn] = outduct
                        name = self.node_names.get(ipn, f"node-{ipn}")
                        neighbors[ipn] = {"name": name, "outduct": outduct}

        # --- Get all contact graph nodes via ionadmin l contact ---
        contact_out, _, contact_rc = _run_admin("ionadmin", "l contact\nq\n")
        if contact_rc == 0 and contact_out:
            # Format: "From ... node 268485091 to node 268485000 ..."
            for line in contact_out.splitlines():
                for m in re.finditer(r"node\s+(\d+)", line):
                    all_nodes.add(m.group(1))

        # Compute known = all - neighbors - self
        known_ipns = all_nodes - set(neighbors.keys()) - {self.my_ipn}
        for ipn in known_ipns:
            name = self.node_names.get(ipn, f"node-{ipn}")
            known[ipn] = {"name": name, "hops": "?"}

        # Store sorted node list
        self.node_list = sorted(
            list(neighbors.keys()) + list(known_ipns),
            key=lambda i: self.node_names.get(i, i).lower()
        )

        # Update the sidebar
        self._populate_sidebar(neighbors, known)

        # Update network status
        node_count = len(neighbors) + len(known)
        status_text = f" NET: {len(neighbors)}N {len(known)}K"

        # Check ION status
        ion_out, _, ion_rc = _run("ionadmin l", timeout=5)
        if ion_rc == 0:
            self.net_status.original_widget.set_text(status_text)
            self.net_status.set_attr_map({None: "net_ok"})
        else:
            self.net_status.original_widget.set_text(" NET: ION down")
            self.net_status.set_attr_map({None: "net_down"})

    def _switch_to(self, ipn: str):
        """Switch the active conversation to the given IPN."""
        ipn = ipn.replace("ipn:", "")
        self.active_ipn = ipn
        self.history.mark_read(ipn)
        self.history.set_last_active(ipn)

        # Build chat header with node name, IPN, and connection info
        name = self.node_names.get(ipn, "") or self.history.conversation_name(ipn) or f"node-{ipn}"
        if ipn in self.plans:
            conn_info = f"direct via {self.plans[ipn]}"
        else:
            conn_info = "routed"
        self.chat_header_text.set_text(f" {name} (ipn:{ipn}) — {conn_info}")

        self._load_conversation(ipn)

        if not self.dry_run:
            self._refresh_nodes()

        self._update_status_bar()

    # ------------------------------------------------------------------
    # Send and command processing
    # ------------------------------------------------------------------

    def _send_message(self, text):
        """Send a chat message to the active conversation."""
        if not self.active_ipn:
            self._set_status("No active conversation")
            return

        payload = json.dumps({
            "from": self.my_ipn,
            "name": self.my_name,
            "msg": text,
            "ts": time.strftime("%H:%M:%S"),
        })
        escaped = payload.replace("'", "'\\''")
        dest = f"ipn:{self.active_ipn}.{self.CHAT_SVC}"
        _, _, rc = _run(f"bpsource {dest} '{escaped}' 2>/dev/null")

        if rc == 0:
            self.history.add_outgoing(self.active_ipn, self.my_ipn, self.my_name, text)
            self._append_message({
                "dir": "out",
                "from": self.my_ipn,
                "name": self.my_name,
                "msg": text,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            self._scroll_to_bottom()
        else:
            self._set_status("Send failed!")

    def _process_command(self, text):
        """Process a slash command."""
        parts = text.lstrip("/").split(None, 1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("quit", "exit", "q"):
            raise urwid.ExitMainLoop()
        elif cmd == "to" and args:
            target = args.strip().replace("ipn:", "")
            # Try exact name match
            for ipn, name in self.node_names.items():
                if name.lower() == target.lower():
                    self._switch_to(ipn)
                    return
            # Try partial name match
            for ipn, name in self.node_names.items():
                if target.lower() in name.lower():
                    self._switch_to(ipn)
                    return
            # Try as raw IPN number
            if target.isdigit():
                self._switch_to(target)
                return
            self._set_status(f"Unknown node: {target}")
        elif cmd == "nodes":
            self._refresh_nodes()
            self._set_status("Node list refreshed")
        elif cmd == "help":
            self._show_help()
        else:
            self._set_status("Unknown command")

    # ------------------------------------------------------------------
    # Conversation navigation
    # ------------------------------------------------------------------

    def _cycle_conversation(self, direction):
        """Cycle through conversations by direction (-1 = prev, 1 = next)."""
        convos = self.history.list_conversations()
        if not convos:
            return
        ipn_list = [c[0] for c in convos]
        if not ipn_list:
            return
        if self.active_ipn in ipn_list:
            idx = ipn_list.index(self.active_ipn)
            idx = (idx + direction) % len(ipn_list)
        else:
            idx = 0
        self._switch_to(ipn_list[idx])

    def _jump_to_unread(self):
        """Jump to the first conversation with unread messages."""
        unread = self.history.all_unread()
        for ipn in unread:
            if ipn != self.active_ipn:
                self._switch_to(ipn)
                return

    # ------------------------------------------------------------------
    # Status bar helpers
    # ------------------------------------------------------------------

    def _update_status_bar(self):
        """Update the status bar with active conversation and keybinding hints."""
        if self.active_ipn:
            name = self.node_names.get(self.active_ipn, "") or self.history.conversation_name(self.active_ipn) or f"ipn:{self.active_ipn}"
            status = f" {name} | Tab:sidebar  Alt+↑↓:switch  Alt+N:unread  F1:help  F10:quit"
        else:
            status = " No conversation | Tab:sidebar  F1:help  F2:refresh  F10:quit"
        self.status_text.set_text(status)

    def _set_status(self, text):
        """Set status bar to a simple message."""
        self.status_text.set_text(f" {text}")

    def _show_help(self):
        """Show a help overlay with keybindings and commands."""
        help_text = (
            "DTN Chat — Keybindings & Commands\n"
            "─────────────────────────────────\n"
            "\n"
            "Keybindings:\n"
            "  Tab         Toggle sidebar focus\n"
            "  Enter       Select node / send message\n"
            "  Esc         Return to chat input\n"
            "  Alt+Up      Previous conversation\n"
            "  Alt+Down    Next conversation\n"
            "  Alt+N       Jump to next unread\n"
            "  PgUp/PgDn   Scroll messages\n"
            "  F1          This help screen\n"
            "  F2          Refresh node list\n"
            "  F10         Quit\n"
            "\n"
            "Commands (type in input bar):\n"
            "  /to <name|IPN>   Switch conversation\n"
            "  /nodes           Refresh node list\n"
            "  /help            Show this help\n"
            "  /quit            Exit chat\n"
            "\n"
            "Press any key to dismiss."
        )
        help_widget = urwid.Filler(urwid.Text(help_text), valign="middle")
        help_box = urwid.LineBox(help_widget, title="Help")
        overlay = urwid.Overlay(
            help_box, self.frame,
            align="center", width=("relative", 60),
            valign="middle", height=("relative", 70),
        )

        if self.loop is not None:
            original_widget = self.loop.widget
            original_handler = self.loop.unhandled_input

            def dismiss(key):
                self.loop.widget = original_widget
                self.loop.unhandled_input = original_handler

            self.loop.widget = overlay
            self.loop.unhandled_input = dismiss
