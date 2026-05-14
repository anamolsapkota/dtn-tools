#!/usr/bin/env python3
"""
dtn-nodes — CLI tool to query discovered DTN nodes.

Usage:
    dtn-nodes              List all discovered nodes
    dtn-nodes --summary    Show discovery summary
    dtn-nodes --new        Show nodes discovered in the last scan
    dtn-nodes --search X   Search for a node by name/IPN/location
    dtn-nodes --json       Output as JSON
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get(
    "DTN_DISCOVERY_DB",
    "/home/pi05/dtn/dtn-discovery/discovered_nodes.json",
)


def load_db():
    if not os.path.exists(DB_PATH):
        print(f"Discovery database not found: {DB_PATH}")
        print("Is dtn-discovery running?")
        sys.exit(1)
    with open(DB_PATH) as f:
        return json.load(f)


def format_time(iso_str):
    if not iso_str:
        return "never"
    try:
        dt = datetime.fromisoformat(iso_str)
        now = datetime.now(timezone.utc)
        delta = now - dt
        if delta.total_seconds() < 60:
            return "just now"
        elif delta.total_seconds() < 3600:
            return f"{int(delta.total_seconds() / 60)}m ago"
        elif delta.total_seconds() < 86400:
            return f"{int(delta.total_seconds() / 3600)}h ago"
        else:
            return f"{int(delta.total_seconds() / 86400)}d ago"
    except Exception:
        return iso_str[:19]


def list_nodes(data, search=None, json_out=False):
    nodes = data.get("nodes", {})
    if search:
        search = search.lower()
        nodes = {
            k: v for k, v in nodes.items()
            if search in k
            or search in v.get("name", "").lower()
            or search in v.get("location", "").lower()
            or search in v.get("email", "").lower()
        }

    if json_out:
        print(json.dumps(nodes, indent=2))
        return

    if not nodes:
        print("No nodes found.")
        return

    print(f"{'IPN':<14} {'Name':<20} {'Via':<10} {'In ION':<7} {'Last Seen':<12} {'Neighbors'}")
    print("-" * 90)
    for ipn in sorted(nodes.keys(), key=lambda x: int(x)):
        n = nodes[ipn]
        name = (n.get("name") or "?")[:19]
        via = n.get("reachable_via", "?")
        in_ion = "yes" if n.get("added_to_ion") else "no"
        last = format_time(n.get("last_seen"))
        neighbors = len(n.get("neighbors", []))
        print(f"ipn:{ipn:<10} {name:<20} {via:<10} {in_ion:<7} {last:<12} {neighbors}")


def show_summary(data):
    nodes = data.get("nodes", {})
    stats = data.get("stats", {})
    last_scan = data.get("last_scan")

    direct = sum(1 for n in nodes.values() if n.get("reachable_via") == "direct")
    gateway = sum(1 for n in nodes.values() if n.get("reachable_via") == "gateway")
    unknown = sum(1 for n in nodes.values() if n.get("reachable_via") == "unknown")
    in_ion = sum(1 for n in nodes.values() if n.get("added_to_ion"))

    print("=== DTN Neighbor Discovery Summary ===")
    print(f"Total nodes discovered: {len(nodes)}")
    print(f"  Direct (in ION):     {direct}")
    print(f"  Via gateway:         {gateway}")
    print(f"  Unknown route:       {unknown}")
    print(f"  Added to ION:        {in_ion}")
    print(f"Total scans:           {stats.get('scans', 0)}")
    print(f"Last scan:             {format_time(last_scan)}")


def main():
    parser = argparse.ArgumentParser(description="Query discovered DTN nodes")
    parser.add_argument("--summary", action="store_true", help="Show discovery summary")
    parser.add_argument("--search", type=str, help="Search by name/IPN/location")
    parser.add_argument("--new", action="store_true", help="Show recently discovered nodes")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    data = load_db()

    if args.summary:
        show_summary(data)
    elif args.new:
        list_nodes(data, json_out=args.json)
    else:
        list_nodes(data, search=args.search, json_out=args.json)


if __name__ == "__main__":
    main()
