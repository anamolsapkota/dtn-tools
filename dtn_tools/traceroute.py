#!/usr/bin/env python3
"""
DTN Route Diagnostics — trace the bundle path between two nodes and identify issues.

Analyzes the ION contact graph, plans, and network connectivity to determine:
1. The computed CGR route from source to destination
2. Which hops have active contacts/ranges
3. Which hops are reachable (UDP connectivity)
4. Where the route breaks down
5. End-to-end DTN round-trip time via bping

Usage (called from main dtn CLI):
    dtn trace <destination_ipn>
    dtn diagnose
"""

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass

def _default_discovery_db():
    home = os.path.expanduser("~")
    for d in [os.path.join(home, "dtn"), os.path.join(home, "ion-dtn"), "/opt/dtn"]:
        p = os.path.join(d, "dtn-discovery", "discovered_nodes.json")
        if os.path.exists(p):
            return p
    return os.path.join(home, "dtn", "dtn-discovery", "discovered_nodes.json")


DISCOVERY_DB = os.environ.get("DTN_DISCOVERY_DB", _default_discovery_db())


@dataclass
class Hop:
    """Represents one hop in a DTN route."""
    from_ipn: str
    to_ipn: str
    has_contact: bool = False
    has_range: bool = False
    has_plan: bool = False
    outduct_ip: str = ""
    udp_reachable: bool = False
    rtt_ms: float = -1
    issue: str = ""


def run(cmd, timeout=30):
    """Run a shell command and return stdout."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return ""


def run_admin(program, commands, timeout=30):
    """Run an ION admin program with commands via temp file (avoids pipe hangs)."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cmd', delete=False) as f:
        f.write(commands if commands.endswith('\n') else commands + '\n')
        tmp = f.name
    try:
        return run(f"{program} < {tmp} 2>&1", timeout=timeout)
    finally:
        os.unlink(tmp)


def get_my_ipn():
    """Detect local IPN from various sources."""
    # Method 1: loopback plan in ipnadmin (most reliable)
    out = run_admin("ipnadmin", "l plan\nq")
    for line in out.splitlines():
        line = line.strip().lstrip(":").strip()
        m = re.match(r"(\d+)\s+xmit\s+127\.0\.0\.1", line)
        if m:
            return m.group(1)

    # Method 2: host*.rc files
    import glob
    for f in glob.glob("/home/*/dtn/host*.rc"):
        m = re.search(r"host(\d+)\.rc", f)
        if m:
            return m.group(1)

    # Method 3: loopback contacts (self→self)
    out = run_admin("ionadmin", "l contact\nq")
    for line in out.splitlines():
        m = re.search(r"node\s+(\d+)\s+to\s+node\s+(\d+)", line)
        if m and m.group(1) == m.group(2):
            return m.group(1)

    return None


def get_contacts():
    """Get all ION contacts as (from, to) pairs."""
    contacts = []
    out = run_admin("ionadmin", "l contact\nq")
    for line in out.splitlines():
        m = re.search(r"node\s+(\d+)\s+to\s+node\s+(\d+)", line)
        if m:
            contacts.append((m.group(1), m.group(2)))
    return contacts


def get_ranges():
    """Get all ION ranges as (from, to) pairs."""
    ranges = []
    out = run_admin("ionadmin", "l range\nq")
    for line in out.splitlines():
        m = re.search(r"node\s+(\d+)\s+to\s+node\s+(\d+)", line)
        if m:
            ranges.append((m.group(1), m.group(2)))
    return ranges


def get_plans():
    """Get ION plans as {ipn: outduct_ip}."""
    plans = {}
    out = run_admin("ipnadmin", "l plan\nq")
    for line in out.splitlines():
        line = line.strip().lstrip(":").strip()
        m = re.match(r"(\d+)\s+xmit\s+(\S+)", line)
        if m:
            plans[m.group(1)] = m.group(2)
    return plans


def get_node_name(ipn):
    """Look up a friendly name for a node from the discovery DB."""
    if os.path.exists(DISCOVERY_DB):
        try:
            with open(DISCOVERY_DB) as f:
                data = json.load(f)
            node = data.get("nodes", {}).get(ipn)
            if node and node.get("name"):
                return node["name"]
        except Exception:
            pass
    return None


def check_udp_reachable(ip_port):
    """Check if a UDP endpoint is reachable via ICMP ping."""
    ip = ip_port.split(":")[0]
    if ip in ("127.0.0.1", "0.0.0.0"):
        return True, 0.0
    out = run(f"ping -c 1 -W 3 {ip} 2>/dev/null")
    m = re.search(r"time=(\S+)\s*ms", out)
    if m:
        return True, float(m.group(1))
    return False, -1


def bping_rtt(my_ipn, dest_ipn, timeout=10):
    """Send a DTN bping and measure round-trip time. Returns (success, rtt_ms).

    Tries bpecho on common service numbers (.2, .1) since different nodes
    may configure bpecho on different endpoints.
    """
    src_eid = f"ipn:{my_ipn}.3"

    for svc in (2, 1):
        dst_eid = f"ipn:{dest_ipn}.{svc}"
        out = run(
            f"timeout {timeout} bping -c 1 -q 5 {src_eid} {dst_eid} 2>&1",
            timeout=timeout + 5,
        )

        # Parse bping output: "time=1.234567 s"
        m = re.search(r"time=(\S+)\s+s", out)
        if m:
            rtt_s = float(m.group(1))
            return True, rtt_s * 1000.0  # convert to ms

        # Check for bundle loss
        loss_m = re.search(r"(\d+\.?\d*)%\s+bundle loss", out)
        if loss_m and float(loss_m.group(1)) == 0:
            return True, 0.0

    return False, -1


def find_cgr_route(my_ipn, dest_ipn, contacts, plans):
    """
    Compute the likely CGR route from my_ipn to dest_ipn.

    Uses BFS on the contact graph. Prefers paths through nodes we have
    direct plans for (i.e. first hop must be a node with a plan).
    """
    # Build adjacency from contacts
    adj = {}
    for src, dst in contacts:
        adj.setdefault(src, set()).add(dst)

    if my_ipn == dest_ipn:
        return [my_ipn]

    # Direct contact?
    if dest_ipn in adj.get(my_ipn, set()):
        return [my_ipn, dest_ipn]

    # BFS — but only start through nodes we have plans for (first hop constraint)
    # ION can only send to nodes it has an outduct/plan for
    plan_nodes = set(plans.keys()) - {my_ipn}

    visited = {my_ipn}
    queue = []

    # Seed BFS with reachable first-hop nodes that have contacts to other nodes
    for first_hop in adj.get(my_ipn, set()):
        if first_hop == dest_ipn:
            return [my_ipn, dest_ipn]
        if first_hop in plan_nodes:
            visited.add(first_hop)
            queue.append((first_hop, [my_ipn, first_hop]))

    while queue:
        current, path = queue.pop(0)
        for neighbor in adj.get(current, set()):
            if neighbor == dest_ipn:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    return []  # No route found


def format_node(ipn):
    """Format a node with name if available."""
    name = get_node_name(ipn)
    if name:
        return f"ipn:{ipn} ({name})"
    return f"ipn:{ipn}"


def trace_route(dest_ipn):
    """Trace the DTN route to a destination and identify issues."""
    my_ipn = get_my_ipn()
    if not my_ipn:
        print("Error: Could not detect local IPN. Is ION running?")
        return

    dest_name = get_node_name(dest_ipn)
    my_name = get_node_name(my_ipn)

    src_label = f"ipn:{my_ipn}" + (f" ({my_name})" if my_name else "")
    dst_label = f"ipn:{dest_ipn}" + (f" ({dest_name})" if dest_name else "")

    print(f"DTN Route Trace: {src_label} -> {dst_label}")
    print("=" * 70)

    contacts = get_contacts()
    ranges = get_ranges()
    plans = get_plans()

    # Find route
    route = find_cgr_route(my_ipn, dest_ipn, contacts, plans)

    if not route:
        print(f"\n  NO ROUTE FOUND to {dst_label}")
        print()
        print("  Diagnosis:")

        # Check if we have any contact to the destination
        has_contact = any(
            (s == my_ipn and d == dest_ipn) or (s == dest_ipn and d == my_ipn)
            for s, d in contacts
        )
        if not has_contact:
            print(f"  [!!] No contact between ipn:{my_ipn} and ipn:{dest_ipn}")
            print(f"       Fix: ionadmin 'a contact +1 +360000000 {my_ipn} {dest_ipn} 100000'")
            print(f"            ionadmin 'a contact +1 +360000000 {dest_ipn} {my_ipn} 100000'")
            print(f"            ionadmin 'a range +1 +360000000 {my_ipn} {dest_ipn} 1'")
            print(f"            ionadmin 'a range +1 +360000000 {dest_ipn} {my_ipn} 1'")

        # Check if gateway has contact
        gw_ipn = "268485000"
        gw_to_dest = any(s == gw_ipn and d == dest_ipn for s, d in contacts)
        dest_to_gw = any(s == dest_ipn and d == gw_ipn for s, d in contacts)
        has_gw_plan = gw_ipn in plans

        if not has_gw_plan:
            print(f"  [!!] No plan for gateway ipn:{gw_ipn}")
        elif not gw_to_dest:
            print(f"  [!!] Gateway (ipn:{gw_ipn}) has no contact to ipn:{dest_ipn}")
            print(f"       CGR cannot route through the gateway to this node")
            print(f"       The destination node may need to exchange contacts via dtnex")
        if not dest_to_gw:
            print(f"  [!!] ipn:{dest_ipn} has no return contact to gateway")

        # Check discovered nodes for hints
        if os.path.exists(DISCOVERY_DB):
            try:
                with open(DISCOVERY_DB) as f:
                    disc = json.load(f)
                node_info = disc.get("nodes", {}).get(dest_ipn)
                if node_info:
                    via = node_info.get("reachable_via", "unknown")
                    print(f"\n  Discovery info: node was found via '{via}' source")
                    if via == "unknown":
                        print(f"  This node exists on the network but has no known route from here")
                else:
                    print(f"\n  Node ipn:{dest_ipn} was NOT found by the discovery daemon")
                    print(f"  It may not exist or may not be exchanging metadata via dtnex")
            except Exception:
                pass
        return

    print(f"\n  Route: {len(route)-1} hop(s)")
    print(f"  Path:  {' -> '.join(format_node(n) for n in route)}")
    print()

    hops = []
    all_ok = True

    for i in range(len(route) - 1):
        src = route[i]
        dst = route[i + 1]
        hop = Hop(from_ipn=src, to_ipn=dst)

        # Check contact
        hop.has_contact = any(s == src and d == dst for s, d in contacts)
        reverse_contact = any(s == dst and d == src for s, d in contacts)

        # Check range
        hop.has_range = any(s == src and d == dst for s, d in ranges)

        # Check plan (relevant for first hop — our node must have a plan to send)
        if src == my_ipn:
            hop.has_plan = dst in plans
            if hop.has_plan:
                hop.outduct_ip = plans[dst]

        # Check UDP reachability (only for nodes we have plans for)
        if dst in plans:
            hop.outduct_ip = plans[dst]
            reachable, rtt = check_udp_reachable(hop.outduct_ip)
            hop.udp_reachable = reachable
            hop.rtt_ms = rtt

        # Determine issues
        issues = []
        if not hop.has_contact:
            issues.append("NO CONTACT")
        if not hop.has_range:
            issues.append("NO RANGE")
        if src == my_ipn and not hop.has_plan:
            issues.append("NO PLAN (first hop needs outduct)")
        if hop.outduct_ip and not hop.udp_reachable:
            issues.append("UNREACHABLE")
        if not reverse_contact:
            issues.append("NO RETURN CONTACT")

        hop.issue = ", ".join(issues)
        if hop.issue:
            all_ok = False

        hops.append(hop)

        # Display hop
        dst_name = get_node_name(dst)
        dst_label = f"ipn:{dst}" + (f" ({dst_name})" if dst_name else "")
        ip_info = f" via {hop.outduct_ip}" if hop.outduct_ip else ""
        rtt_info = f" icmp={hop.rtt_ms:.0f}ms" if hop.rtt_ms >= 0 else ""
        plan_info = " [plan]" if hop.has_plan else ""

        marker = "[OK]" if not hop.issue else "[!!]"
        print(f"  {marker} Hop {i+1}: ipn:{src} -> {dst_label}{ip_info}{rtt_info}{plan_info}")
        print(f"         Contact: {'yes' if hop.has_contact else 'NO'} | "
              f"Range: {'yes' if hop.has_range else 'NO'} | "
              f"Return: {'yes' if reverse_contact else 'NO'}")
        if hop.issue:
            print(f"         ISSUE: {hop.issue}")
        print()

    # End-to-end DTN bping
    bping_ok = False
    print("-" * 70)
    blocking_issues = any(
        "NO PLAN" in h.issue or "UNREACHABLE" in h.issue or "NO CONTACT" in h.issue
        for h in hops
    )
    if not blocking_issues:
        print(f"  DTN bping ipn:{my_ipn} -> ipn:{dest_ipn} ...", end=" ", flush=True)
        ok, rtt = bping_rtt(my_ipn, dest_ipn)
        if ok:
            print(f"OK  rtt={rtt:.1f}ms ({rtt/1000:.2f}s)")
            bping_ok = True
        else:
            print(f"no response (remote bpecho may not be running)")
    else:
        print(f"  DTN bping: SKIPPED (route has blocking issues)")

    # Summary
    print()
    print("=" * 70)
    if all_ok and bping_ok:
        print(f"  Route to {dst_label}: ALL OK (bping verified)")
    elif all_ok:
        print(f"  Route to {dst_label}: ROUTE OK (bping unverified — remote bpecho may be down)")
    else:
        print(f"  Route to {dst_label}: ISSUES FOUND")
        print()
        for i, hop in enumerate(hops):
            if hop.issue:
                print(f"  Hop {i+1} (ipn:{hop.from_ipn} -> ipn:{hop.to_ipn}): {hop.issue}")
                if "NO CONTACT" in hop.issue:
                    print(f"    Fix: ionadmin 'a contact +1 +360000000 {hop.from_ipn} {hop.to_ipn} 100000'")
                if "NO RANGE" in hop.issue:
                    print(f"    Fix: ionadmin 'a range +1 +360000000 {hop.from_ipn} {hop.to_ipn} 1'")
                if "NO PLAN" in hop.issue and hop.from_ipn == my_ipn:
                    print(f"    Fix: ipnadmin 'a plan {hop.to_ipn} udp/<IP>:4556'")
                if "UNREACHABLE" in hop.issue:
                    print(f"    Fix: Check network connectivity to {hop.outduct_ip}")
                if "NO RETURN" in hop.issue:
                    print(f"    Fix: ionadmin 'a contact +1 +360000000 {hop.to_ipn} {hop.from_ipn} 100000'")


def diagnose_all():
    """Run diagnostics on all configured plans."""
    my_ipn = get_my_ipn()
    if not my_ipn:
        print("Error: Could not detect local IPN. Is ION running?")
        return

    plans = get_plans()
    contacts = get_contacts()
    ranges = get_ranges()

    # Count unique nodes in contact graph
    all_nodes = set()
    for s, d in contacts:
        all_nodes.add(s)
        all_nodes.add(d)
    all_nodes.discard(my_ipn)

    print(f"DTN Node Diagnostics: ipn:{my_ipn}")
    print("=" * 70)

    # Check ION status
    out = run("bpversion 2>/dev/null")
    print(f"  ION: {'Running' if out else 'NOT RUNNING'}")

    # Check dtnex
    out = run("pgrep -x dtnex 2>/dev/null")
    pids = out.replace("\n", ", ") if out else ""
    print(f"  dtnex: {'Running (pid ' + pids + ')' if out else 'NOT RUNNING'}")

    # Check bpecho
    out = run("pgrep -x bpecho 2>/dev/null")
    print(f"  bpecho: {'Running (pid ' + out + ')' if out else 'NOT RUNNING'}")

    # Check discovery
    out = run("pgrep -f 'discovery.py' 2>/dev/null")
    print(f"  discovery: {'Running (pid ' + out.split()[0] + ')' if out else 'NOT RUNNING'}")

    print()
    print(f"  Plans (direct neighbors): {max(0, len(plans) - 1)}")  # minus loopback
    print(f"  Contacts (edges):         {len(contacts)}")
    print(f"  Ranges:                   {len(ranges)}")
    print(f"  Unique nodes in graph:    {len(all_nodes)}")

    # Discovered nodes
    disc_count = 0
    if os.path.exists(DISCOVERY_DB):
        try:
            with open(DISCOVERY_DB) as f:
                disc_count = len(json.load(f).get("nodes", {}))
        except Exception:
            pass
    print(f"  Discovered nodes:         {disc_count}")

    print()

    # Check each direct neighbor
    print("Direct Neighbors (with plans):")
    print("-" * 70)

    issues_found = 0
    for ipn, outduct in sorted(plans.items()):
        if ipn == my_ipn:
            continue

        ip = outduct.split(":")[0]
        issues = []
        name = get_node_name(ipn)
        label = f"ipn:{ipn}" + (f" ({name})" if name else "")

        # Check contact
        has_fwd = any(s == my_ipn and d == ipn for s, d in contacts)
        has_rev = any(s == ipn and d == my_ipn for s, d in contacts)
        has_range_fwd = any(s == my_ipn and d == ipn for s, d in ranges)

        if not has_fwd:
            issues.append("no forward contact")
        if not has_rev:
            issues.append("no return contact")
        if not has_range_fwd:
            issues.append("no range")

        # Check IP reachability
        reachable, rtt = check_udp_reachable(outduct)
        if not reachable:
            issues.append(f"unreachable ({ip})")

        rtt_str = f" icmp={rtt:.0f}ms" if rtt >= 0 else ""
        marker = "[OK]" if not issues else "[!!]"

        print(f"  {marker} {label} via {outduct}{rtt_str}")
        if issues:
            issues_found += 1
            for issue in issues:
                print(f"       - {issue}")

    print()
    if issues_found == 0:
        print(f"All {len(plans) - 1} direct neighbor(s) OK.")
    else:
        print(f"{issues_found} of {len(plans) - 1} neighbor(s) with issues.")

    # All nodes in contact graph (not just plans)
    remote_nodes = all_nodes - set(plans.keys())
    if remote_nodes:
        print()
        print(f"Remote Nodes (via contact graph, no direct plan — {len(remote_nodes)} nodes):")
        print("-" * 70)

        routable = 0
        no_route = 0
        for ipn in sorted(remote_nodes, key=lambda x: int(x)):
            name = get_node_name(ipn)
            label = f"ipn:{ipn}" + (f" ({name})" if name else "")

            route = find_cgr_route(my_ipn, ipn, contacts, plans)
            if route and len(route) > 1:
                hops = len(route) - 1
                via = route[1]
                via_name = get_node_name(via)
                via_label = f"ipn:{via}" + (f" ({via_name})" if via_name else "")
                print(f"  [OK] {label} — {hops} hop(s) via {via_label}")
                routable += 1
            else:
                # Check if gateway has contact
                gw_has = any(s == "268485000" and d == ipn for s, d in contacts)
                if gw_has:
                    print(f"  [--] {label} — gateway has contact but no route from here")
                else:
                    print(f"  [!!] {label} — no route")
                no_route += 1

        print()
        print(f"  {routable} routable, {no_route} unreachable from here.")

    # Grand total
    print()
    total_nodes = len(all_nodes)
    total_routable = (len(plans) - 1) + (routable if remote_nodes else 0)
    print(f"Total: {total_nodes} nodes known, {total_routable} routable, "
          f"{total_nodes - total_routable} unreachable.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        trace_route(sys.argv[1])
    else:
        diagnose_all()
