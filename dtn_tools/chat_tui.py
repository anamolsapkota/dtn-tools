#!/usr/bin/env python3
"""
Full-screen urwid TUI for DTN chat.

Replaces ChatSession for interactive use while reusing ChatHistory for
persistent message storage.
"""

import json
import os
import re
import subprocess
import threading
import time

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

        # Receiver thread wakeup pipe
        self._pipe_r, self._pipe_w = os.pipe()
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
        self.title_text = urwid.Text(
            f" DTN Chat  |  ipn:{self.my_ipn}", align="left"
        )
        self.title_bar = urwid.AttrMap(self.title_text, "title_bar")

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
        """Start the urwid main loop."""
        self.loop = urwid.MainLoop(
            self.frame,
            palette=PALETTE,
            unhandled_input=self._handle_input,
            handle_mouse=False,
        )
        # Register the pipe fd so the receiver thread can wake the loop
        self.loop.watch_pipe(self._on_pipe_data)
        self.loop.run()

    # ------------------------------------------------------------------
    # Placeholder methods — filled in by later tasks
    # ------------------------------------------------------------------

    def _handle_input(self, key):
        """Handle unhandled keystrokes (keybindings, focus switching, etc.)."""
        pass

    def _on_pipe_data(self, data):
        """Called by urwid main loop when the receiver thread writes to the pipe."""
        pass

    def _receiver_loop(self):
        """Background thread: receive bundles and queue them for the main loop."""
        pass

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
            # Format: ": 268485000 xmit 100.96.108.37:4556 xmit rate: 0"
            for line in plan_out.splitlines():
                m = re.search(r":\s*(\d+)\s+xmit\s+(\S+)", line)
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
        pass
