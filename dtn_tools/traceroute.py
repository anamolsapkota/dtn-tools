#!/usr/bin/env python3
"""
DTN Route Diagnostics — trace the bundle path between two nodes and identify issues.

Analyzes the ION contact graph, plans, and network connectivity to determine:
1. The computed CGR route from source to destination
2. Which hops have active contacts/ranges
3. Which hops are reachable (UDP connectivity)
4. Where the route breaks down

Usage (called from main dtn CLI):
    dtn trace <destination_ipn>
    dtn diagnose
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass


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


def run(cmd):
    """Run a shell command and return stdout."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_my_ipn():
    """Detect local IPN."""
    out = run("echo 'l' | ionadmin 2>/dev/null")
    m = re.search(r"own node nbr:\s*(\d+)", out)
    if m:
        return m.group(1)
    return None


def get_contacts():
    """Get all ION contacts as (from, to) pairs."""
    contacts = []
    out = run("echo 'l contact' | ionadmin 2>/dev/null")
    for line in out.splitlines():
        m = re.search(r"node\s+(\d+)\s+to\s+node\s+(\d+)", line)
        if m:
            contacts.append((m.group(1), m.group(2)))
    return contacts


def get_ranges():
    """Get all ION ranges as (from, to) pairs."""
    ranges = []
    out = run("echo 'l range' | ionadmin 2>/dev/null")
    for line in out.splitlines():
        m = re.search(r"node\s+(\d+)\s+to\s+node\s+(\d+)", line)
        if m:
            ranges.append((m.group(1), m.group(2)))
    return ranges


def get_plans():
    """Get ION plans as {ipn: outduct_ip}."""
    plans = {}
    out = run("echo 'l plan' | ipnadmin 2>/dev/null")
    for line in out.splitlines():
        line = line.strip().lstrip(":").strip()
        m = re.match(r"(\d+)\s+xmit\s+(\S+)", line)
        if m:
            plans[m.group(1)] = m.group(2)
    return plans


def check_udp_reachable(ip_port):
    """Check if a UDP endpoint is reachable via basic ping."""
    ip = ip_port.split(":")[0]
    if ip in ("127.0.0.1", "0.0.0.0"):
        return True, 0.0
    out = run(f"ping -c 1 -W 3 {ip} 2>/dev/null")
    m = re.search(r"time=(\S+)\s*ms", out)
    if m:
        return True, float(m.group(1))
    return False, -1


def find_cgr_route(my_ipn, dest_ipn, contacts, plans):
    """
    Compute the likely CGR route from my_ipn to dest_ipn.

    ION's CGR uses the contact graph to find the best path. We simulate
    a simplified version by finding the shortest path in the contact graph.
    """
    # Build adjacency from contacts
    adj = {}
    for src, dst in contacts:
        adj.setdefault(src, set()).add(dst)

    # BFS from my_ipn to dest_ipn
    if my_ipn == dest_ipn:
        return [my_ipn]

    visited = {my_ipn}
    queue = [(my_ipn, [my_ipn])]
    while queue:
        current, path = queue.pop(0)
        for neighbor in adj.get(current, []):
            if neighbor == dest_ipn:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    return []  # No route found


def trace_route(dest_ipn):
    """Trace the DTN route to a destination and identify issues."""
    my_ipn = get_my_ipn()
    if not my_ipn:
        print("Error: Could not detect local IPN. Is ION running?")
        return

    print(f"DTN Route Trace: ipn:{my_ipn} -> ipn:{dest_ipn}")
    print("=" * 60)

    contacts = get_contacts()
    ranges = get_ranges()
    plans = get_plans()

    # Find route
    route = find_cgr_route(my_ipn, dest_ipn, contacts, plans)

    if not route:
        print(f"\n  NO ROUTE FOUND to ipn:{dest_ipn}")
        print()
        print("  Possible causes:")

        # Check if we have a contact to the destination
        has_contact = any(
            (s == my_ipn and d == dest_ipn) or (s == dest_ipn and d == my_ipn)
            for s, d in contacts
        )
        if not has_contact:
            print(f"  - No contact between ipn:{my_ipn} and ipn:{dest_ipn}")
            print(f"    Fix: ionadmin 'a contact +1 +360000000 {my_ipn} {dest_ipn} 100000'")

        # Check if gateway has contact
        gw_ipn = "268485000"
        gw_to_dest = any(s == gw_ipn and d == dest_ipn for s, d in contacts)
        dest_to_gw = any(s == dest_ipn and d == gw_ipn for s, d in contacts)
        if not gw_to_dest:
            print(f"  - Gateway (ipn:{gw_ipn}) has no contact to ipn:{dest_ipn}")
            print(f"    This means CGR can't route through the gateway")
        if not dest_to_gw:
            print(f"  - ipn:{dest_ipn} has no contact to gateway (ipn:{gw_ipn})")

        # Check plan
        if dest_ipn not in plans and gw_ipn not in plans:
            print(f"  - No plan for ipn:{dest_ipn} or gateway")
        return

    print(f"\n  Route ({len(route)-1} hops):")
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

        # Check plan (only relevant for first hop from our node)
        if src == my_ipn:
            hop.has_plan = dst in plans
            if hop.has_plan:
                hop.outduct_ip = plans[dst]
        elif i == 0:
            # For intermediate hops, check if we have a plan to the next hop
            hop.has_plan = dst in plans
            if hop.has_plan:
                hop.outduct_ip = plans[dst]

        # Check UDP reachability (only for direct neighbors we have plans for)
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
            issues.append("NO PLAN")
        if hop.outduct_ip and not hop.udp_reachable:
            issues.append("UNREACHABLE")
        if not reverse_contact:
            issues.append("NO RETURN CONTACT")

        hop.issue = ", ".join(issues)
        if hop.issue:
            all_ok = False

        hops.append(hop)

        # Display hop
        status = "OK" if not hop.issue else hop.issue
        ip_info = f" ({hop.outduct_ip})" if hop.outduct_ip else ""
        rtt_info = f" rtt={hop.rtt_ms:.0f}ms" if hop.rtt_ms >= 0 else ""
        plan_info = " [has plan]" if hop.has_plan else ""

        marker = "  [OK]" if not hop.issue else "  [!!]"
        print(f"  Hop {i+1}: ipn:{src} -> ipn:{dst}{ip_info}{rtt_info}{plan_info}")
        print(f"         Contact: {'yes' if hop.has_contact else 'NO'} | "
              f"Range: {'yes' if hop.has_range else 'NO'} | "
              f"Return: {'yes' if reverse_contact else 'NO'}{marker}")
        if hop.issue:
            print(f"         ISSUE: {hop.issue}")
        print()

    # Summary
    print("=" * 60)
    if all_ok:
        print(f"  Route to ipn:{dest_ipn}: ALL HOPS OK")
    else:
        print(f"  Route to ipn:{dest_ipn}: ISSUES FOUND")
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

    print(f"DTN Node Diagnostics: ipn:{my_ipn}")
    print("=" * 60)

    # Check ION status
    out = run("bpversion 2>/dev/null")
    print(f"  ION: {'Running' if out else 'NOT RUNNING'}")

    # Check dtnex
    out = run("pgrep -x dtnex 2>/dev/null")
    print(f"  dtnex: {'Running (pid ' + out + ')' if out else 'NOT RUNNING'}")

    # Check bpecho
    out = run("pgrep -x bpecho 2>/dev/null")
    print(f"  bpecho: {'Running (pid ' + out + ')' if out else 'NOT RUNNING'}")

    print()
    print(f"  Plans: {len(plans)}")
    print(f"  Contacts: {len(contacts)}")
    print(f"  Ranges: {len(ranges)}")
    print()

    # Check each direct neighbor
    print("Neighbor Connectivity:")
    print("-" * 60)

    issues_found = 0
    for ipn, outduct in plans.items():
        if ipn == my_ipn:
            continue

        ip = outduct.split(":")[0]
        issues = []

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

        status = "OK" if not issues else "ISSUES: " + ", ".join(issues)
        rtt_str = f" rtt={rtt:.0f}ms" if rtt >= 0 else ""
        marker = "[OK]" if not issues else "[!!]"

        print(f"  {marker} ipn:{ipn} via {outduct}{rtt_str}")
        if issues:
            issues_found += 1
            for issue in issues:
                print(f"       - {issue}")

    print()
    if issues_found == 0:
        print("All neighbors OK.")
    else:
        print(f"{issues_found} neighbor(s) with issues.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        trace_route(sys.argv[1])
    else:
        diagnose_all()
