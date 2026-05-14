#!/usr/bin/env python3
"""
DTN Neighbor Discovery Daemon for ION-DTN

Discovers DTN nodes from multiple sources:
  1. openipn.org metadata_list.txt — all nodes exchanging metadata via dtnex
  2. openipn.org contactGraph.gv   — contact graph edges between nodes
  3. Local dtnex nodesmetadata.txt  — nodes seen by local dtnex instance
  4. ION IPND beacons              — local subnet neighbor discovery

Discovered nodes are logged and optionally added to the running ION instance
via ionadmin/bpadmin/ipnadmin commands (if they are reachable via the gateway).
"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONFIG_FILE = os.environ.get(
    "DTN_DISCOVERY_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "discovery.conf"),
)

# Auto-detect DTN working directory
_HOME_DTN = os.environ.get("DTN_DIR", os.path.join(os.path.expanduser("~"), "dtn"))

DEFAULTS = {
    "my_ipn": "",
    "gateway_ipn": "268485000",
    "scan_interval": "300",  # seconds between scans
    "openipn_metadata_url": "https://openipn.org/metadata_list.txt",
    "openipn_graph_url": "https://openipn.org/contactGraph.gv",
    "local_metadata_file": os.path.join(_HOME_DTN, "nodesmetadata.txt"),
    "discovered_db": os.path.join(_HOME_DTN, "dtn-discovery", "discovered_nodes.json"),
    "log_file": os.path.join(_HOME_DTN, "dtn-discovery", "discovery.log"),
    "auto_add_contacts": "true",
    "auto_add_via_gateway": "true",
    "contact_rate": "100000",
    "contact_duration": "360000000",
    "owlt": "1",
    "ipnd_enabled": "true",
    "ipnd_config": os.path.join(_HOME_DTN, "dtn-discovery", "ipnd.rc"),
    "debug": "false",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip()
    return cfg


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DTNNode:
    ipn: str
    name: str = ""
    email: str = ""
    location: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    source: str = ""           # where we discovered this node
    first_seen: str = ""
    last_seen: str = ""
    neighbors: list = field(default_factory=list)
    reachable_via: str = ""    # "direct", "gateway", "unknown"
    added_to_ion: bool = False


# ---------------------------------------------------------------------------
# Discovery sources
# ---------------------------------------------------------------------------

def fetch_openipn_metadata(url: str) -> dict[str, DTNNode]:
    """Parse openipn.org metadata_list.txt into DTNNode dict."""
    nodes = {}
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("NODE") or line.startswith("-"):
                continue
            # Format: "268484800  | OpenIPNNode,samo@grasic.net,Sweden (LOCAL NODE)"
            parts = line.split("|", 1)
            if len(parts) != 2:
                continue
            ipn = parts[0].strip()
            metadata = parts[1].strip().rstrip("(LOCAL NODE)").strip()
            fields = [f.strip() for f in metadata.split(",")]
            node = DTNNode(ipn=ipn, source="openipn-metadata")
            if len(fields) >= 1:
                node.name = fields[0]
            if len(fields) >= 2:
                node.email = fields[1]
            if len(fields) >= 3:
                # Could be location string or latitude
                try:
                    node.lat = float(fields[2])
                    if len(fields) >= 4:
                        node.lon = float(fields[3])
                except ValueError:
                    node.location = fields[2]
            now = datetime.now(timezone.utc).isoformat()
            node.first_seen = now
            node.last_seen = now
            nodes[ipn] = node
    except Exception as e:
        logging.error("Failed to fetch openipn metadata: %s", e)
    return nodes


def fetch_openipn_graph(url: str) -> dict[str, list[str]]:
    """Parse contactGraph.gv for neighbor edges. Returns {ipn: [neighbor_ipns]}."""
    edges: dict[str, list[str]] = {}
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        for line in resp.text.splitlines():
            m = re.match(r'"ipn:(\d+)"\s*->\s*"ipn:(\d+)"', line.strip())
            if m:
                src, dst = m.group(1), m.group(2)
                edges.setdefault(src, [])
                if dst not in edges[src]:
                    edges[src].append(dst)
    except Exception as e:
        logging.error("Failed to fetch openipn graph: %s", e)
    return edges


def read_local_metadata(path: str) -> dict[str, DTNNode]:
    """Parse local dtnex nodesmetadata.txt."""
    nodes = {}
    if not os.path.exists(path):
        return nodes
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Format varies: "268485091 pi05-anamol,admin@ekrasunya.com,..."
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                ipn = parts[0]
                if not ipn.isdigit():
                    continue
                metadata = parts[1]
                fields = [f.strip() for f in metadata.split(",")]
                node = DTNNode(ipn=ipn, source="local-dtnex")
                if len(fields) >= 1:
                    node.name = fields[0]
                if len(fields) >= 2:
                    node.email = fields[1]
                now = datetime.now(timezone.utc).isoformat()
                node.first_seen = now
                node.last_seen = now
                nodes[ipn] = node
    except Exception as e:
        logging.error("Failed to read local metadata: %s", e)
    return nodes


def get_ion_known_nodes() -> set[str]:
    """Get set of IPN numbers that ION already has plans for."""
    known = set()
    try:
        result = subprocess.run(
            ["ionadmin"],
            input="l contact\nq\n",
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            # Contact lines: "From ... node XXXXXX to node YYYYYY ..."
            m = re.search(r"node\s+(\d+)\s+to\s+node\s+(\d+)", line)
            if m:
                known.add(m.group(1))
                known.add(m.group(2))
    except Exception as e:
        logging.debug("Could not list ION contacts: %s", e)

    try:
        result = subprocess.run(
            ["ipnadmin"],
            input="l plan\nq\n",
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            # Plan lines: ": 268485000 xmit ..." or "268485000 xmit ..."
            line = line.strip().lstrip(":").strip()
            m = re.match(r"(\d{6,})\s+xmit", line)
            if m:
                known.add(m.group(1))
    except Exception as e:
        logging.debug("Could not list ION plans: %s", e)

    return known


# ---------------------------------------------------------------------------
# ION integration — add discovered node contacts via gateway
# ---------------------------------------------------------------------------

def ion_command(admin: str, commands: list[str]) -> bool:
    """Run ionadmin/bpadmin/ipnadmin commands."""
    cmd_input = "\n".join(commands) + "\nq\n"
    try:
        result = subprocess.run(
            [admin],
            input=cmd_input,
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logging.warning("%s returned %d: %s", admin, result.returncode, result.stderr.strip())
            return False
        return True
    except Exception as e:
        logging.error("%s failed: %s", admin, e)
        return False


def add_node_via_gateway(node_ipn: str, cfg: dict) -> bool:
    """Add contacts/ranges for a discovered node, routed through the gateway."""
    my_ipn = cfg["my_ipn"]
    gw_ipn = cfg["gateway_ipn"]
    rate = cfg["contact_rate"]
    duration = cfg["contact_duration"]
    owlt = cfg["owlt"]

    if node_ipn in (my_ipn, gw_ipn):
        return False

    logging.info("Adding ION contacts for node %s (via gateway %s)", node_ipn, gw_ipn)

    # Add contact and range between us and the new node
    ionadmin_cmds = [
        f"a contact +1 +{duration} {my_ipn} {node_ipn} {rate}",
        f"a contact +1 +{duration} {node_ipn} {my_ipn} {rate}",
        f"a range +1 +{duration} {my_ipn} {node_ipn} {owlt}",
        f"a range +1 +{duration} {node_ipn} {my_ipn} {owlt}",
        # Also ensure contact between gateway and node so CGR can route
        f"a contact +1 +{duration} {gw_ipn} {node_ipn} {rate}",
        f"a contact +1 +{duration} {node_ipn} {gw_ipn} {rate}",
        f"a range +1 +{duration} {gw_ipn} {node_ipn} {owlt}",
        f"a range +1 +{duration} {node_ipn} {gw_ipn} {owlt}",
    ]
    ok = ion_command("ionadmin", ionadmin_cmds)
    if not ok:
        return False

    # No need to add plan — CGR will route via gateway's plan
    # ION's CGR uses the contact graph to compute routes automatically
    logging.info("Successfully added contacts for node %s", node_ipn)
    return True


# ---------------------------------------------------------------------------
# Persistent state
# ---------------------------------------------------------------------------

def load_discovered(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {"nodes": {}, "last_scan": None, "stats": {"total_discovered": 0, "scans": 0}}


def save_discovered(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Main discovery loop
# ---------------------------------------------------------------------------

def run_scan(cfg: dict, state: dict) -> dict:
    """Run one discovery scan cycle."""
    my_ipn = cfg["my_ipn"]
    auto_add = cfg["auto_add_contacts"] == "true"
    auto_gw = cfg["auto_add_via_gateway"] == "true"
    new_count = 0

    logging.info("=== Discovery scan starting ===")

    # 1. Fetch openipn.org metadata
    remote_nodes = fetch_openipn_metadata(cfg["openipn_metadata_url"])
    logging.info("openipn.org metadata: %d nodes", len(remote_nodes))

    # 2. Fetch contact graph edges
    graph_edges = fetch_openipn_graph(cfg["openipn_graph_url"])
    total_edges = sum(len(v) for v in graph_edges.values())
    logging.info("openipn.org contact graph: %d edges", total_edges)

    # 3. Read local dtnex metadata
    local_nodes = read_local_metadata(cfg["local_metadata_file"])
    logging.info("Local dtnex metadata: %d nodes", len(local_nodes))

    # 4. Get ION's current known nodes
    ion_known = get_ion_known_nodes()
    logging.info("ION currently knows %d nodes", len(ion_known))

    # Merge all discovered nodes
    all_discovered: dict[str, DTNNode] = {}
    for src_nodes in [remote_nodes, local_nodes]:
        for ipn, node in src_nodes.items():
            if ipn == my_ipn:
                continue
            if ipn in all_discovered:
                # Update last_seen and merge info
                existing = all_discovered[ipn]
                existing.last_seen = node.last_seen
                if node.name and not existing.name:
                    existing.name = node.name
                if node.email and not existing.email:
                    existing.email = node.email
                if node.lat and not existing.lat:
                    existing.lat = node.lat
                    existing.lon = node.lon
            else:
                all_discovered[ipn] = node

    # Add neighbor info from graph
    for ipn, node in all_discovered.items():
        if ipn in graph_edges:
            node.neighbors = graph_edges[ipn]

    # Determine reachability
    for ipn, node in all_discovered.items():
        if ipn in ion_known:
            node.reachable_via = "direct"
            node.added_to_ion = True
        elif cfg["gateway_ipn"] in graph_edges.get(ipn, []) or ipn in graph_edges.get(cfg["gateway_ipn"], []):
            node.reachable_via = "gateway"
        else:
            node.reachable_via = "unknown"

    # Update persistent state
    now = datetime.now(timezone.utc).isoformat()
    for ipn, node in all_discovered.items():
        if ipn not in state["nodes"]:
            state["nodes"][ipn] = asdict(node)
            state["nodes"][ipn]["first_seen"] = now
            new_count += 1
            logging.info("NEW node discovered: ipn:%s (%s) via %s [%s]",
                         ipn, node.name, node.source, node.reachable_via)
        else:
            # Update existing
            existing = state["nodes"][ipn]
            existing["last_seen"] = now
            if node.name:
                existing["name"] = node.name
            if node.email:
                existing["email"] = node.email
            if node.neighbors:
                existing["neighbors"] = node.neighbors
            existing["reachable_via"] = node.reachable_via

    # Auto-add new gateway-reachable nodes to ION
    if auto_add and auto_gw:
        for ipn, node in all_discovered.items():
            if ipn == my_ipn or ipn == cfg["gateway_ipn"]:
                continue
            if node.reachable_via == "gateway" and ipn not in ion_known:
                ok = add_node_via_gateway(ipn, cfg)
                if ok:
                    state["nodes"][ipn]["added_to_ion"] = True
                    state["nodes"][ipn]["reachable_via"] = "gateway"

    state["last_scan"] = now
    state["stats"]["scans"] = state["stats"].get("scans", 0) + 1
    state["stats"]["total_discovered"] = len(state["nodes"])

    logging.info(
        "Scan complete: %d total nodes known, %d new this scan, %d in ION",
        len(state["nodes"]), new_count, len(ion_known),
    )
    logging.info("=== Discovery scan finished ===")
    return state


def start_ipnd(cfg: dict):
    """Start IPND daemon if enabled."""
    if cfg["ipnd_enabled"] != "true":
        return None
    ipnd_rc = cfg["ipnd_config"]
    if not os.path.exists(ipnd_rc):
        logging.warning("IPND config not found: %s", ipnd_rc)
        return None
    try:
        proc = subprocess.Popen(
            ["ipnd", ipnd_rc],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        logging.info("Started IPND (pid %d) with config %s", proc.pid, ipnd_rc)
        return proc
    except Exception as e:
        logging.error("Failed to start IPND: %s", e)
        return None


def main():
    cfg = load_config()

    # Setup logging
    log_level = logging.DEBUG if cfg["debug"] == "true" else logging.INFO
    log_file = cfg["log_file"]
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ],
    )

    logging.info("DTN Neighbor Discovery starting for ipn:%s", cfg["my_ipn"])
    logging.info("Scan interval: %ss", cfg["scan_interval"])
    logging.info("Auto-add contacts: %s", cfg["auto_add_contacts"])
    logging.info("Gateway: ipn:%s", cfg["gateway_ipn"])

    # Load persistent state
    state = load_discovered(cfg["discovered_db"])

    # Start IPND for local subnet discovery
    ipnd_proc = start_ipnd(cfg)

    try:
        while True:
            try:
                state = run_scan(cfg, state)
                save_discovered(cfg["discovered_db"], state)
            except Exception as e:
                logging.error("Scan failed: %s", e, exc_info=True)

            interval = int(cfg["scan_interval"])
            logging.info("Next scan in %d seconds", interval)
            time.sleep(interval)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    finally:
        if ipnd_proc:
            ipnd_proc.terminate()
            logging.info("IPND stopped")
        save_discovered(cfg["discovered_db"], state)


if __name__ == "__main__":
    main()
