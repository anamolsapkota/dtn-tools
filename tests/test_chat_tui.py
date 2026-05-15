import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def test_palette_has_required_entries():
    from dtn_tools.chat_tui import PALETTE
    names = [p[0] for p in PALETTE]
    required = [
        "title_bar", "status_bar", "msg_you", "msg_them", "msg_ts",
        "date_sep", "neighbor_header", "known_header", "unread_count",
        "active_conv", "sidebar_focused", "sidebar_dim", "input_prompt",
        "net_ok", "net_down",
    ]
    for name in required:
        assert name in names, f"Missing palette entry: {name}"


def test_chat_tui_construction():
    """ChatTUI can be constructed without ION running (for layout testing)."""
    from dtn_tools.chat_tui import ChatTUI
    tui = ChatTUI(
        my_ipn="268485091",
        dtn_dir="/tmp/test-dtn",
        discovery_db="/tmp/test-discovery.json",
        dry_run=True,
    )
    assert tui.my_ipn == "268485091"
    assert tui.frame is not None


def test_sidebar_population():
    """Sidebar populates neighbor and known node lists."""
    from dtn_tools.chat_tui import ChatTUI, SidebarEntry
    tui = ChatTUI("268485091", "/tmp/test-dtn", "/tmp/test-discovery.json", dry_run=True)

    neighbors = {"268485000": {"name": "DTNGW", "outduct": "100.96.108.37:4556"}}
    known = {"268485111": {"name": "echo-dhulikhel", "hops": 2}}

    tui._populate_sidebar(neighbors, known)

    assert len(tui.neighbor_walker) == 1
    assert len(tui.known_walker) == 1


def test_sidebar_entry_widget():
    from dtn_tools.chat_tui import SidebarEntry
    entry = SidebarEntry("268485000", "DTNGW", unread=3, extra="45ms")
    canvas = entry.render((30,))
    assert canvas is not None
