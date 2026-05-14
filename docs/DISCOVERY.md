# DTN Neighbor Discovery

Automatic discovery of DTN (Delay-Tolerant Networking) nodes across the internet using ION-DTN's IPND beacons and openipn.org data scraping.

## Overview

Standard ION IPND (IP Neighbor Discovery) only works on local subnets via multicast/broadcast beacons. This system extends discovery to the global DTN network by combining:

| Source | Scope | Method |
|--------|-------|--------|
| IPND beacons | Local subnet (ZeroTier, Tailscale) | UDP multicast/broadcast on port 4550 |
| openipn.org metadata | Global | HTTP scraping of `metadata_list.txt` |
| openipn.org contact graph | Global | HTTP parsing of `contactGraph.gv` (Graphviz) |
| Local dtnex metadata | Neighbors | Reading `nodesmetadata.txt` from local dtnex |

Discovered nodes are automatically added to the running ION instance's contact graph so that CGR (Contact Graph Routing) can compute multi-hop routes through the gateway.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 dtn-discovery daemon                 │
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │  IPND    │  │ openipn  │  │  local   │         │
│  │ beacons  │  │ scraper  │  │  dtnex   │         │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘         │
│       │              │              │               │
│       └──────────────┴──────────────┘               │
│                      │                              │
│              ┌───────▼────────┐                     │
│              │  Node Merger   │                     │
│              │  & Classifier  │                     │
│              └───────┬────────┘                     │
│                      │                              │
│       ┌──────────────┼──────────────┐               │
│       ▼              ▼              ▼               │
│  ┌─────────┐  ┌───────────┐  ┌──────────┐         │
│  │ ION Add │  │ JSON DB   │  │  Log     │         │
│  │ Contacts│  │ (state)   │  │  File    │         │
│  └─────────┘  └───────────┘  └──────────┘         │
└─────────────────────────────────────────────────────┘
```

### How it works

1. **Every 5 minutes** (configurable), the daemon runs a discovery scan
2. Fetches `https://openipn.org/metadata_list.txt` for the global node list
3. Fetches `https://openipn.org/contactGraph.gv` for the network topology
4. Reads the local dtnex `nodesmetadata.txt` for locally-seen nodes
5. Queries ION via `ionadmin` and `ipnadmin` to determine which nodes are already known
6. Classifies each discovered node:
   - **direct** — already has a plan in ION
   - **gateway** — reachable via the gateway (ipn:268485000) based on the contact graph
   - **unknown** — no known route
7. For gateway-reachable nodes not yet in ION, adds contacts/ranges via `ionadmin` so CGR can compute routes through the gateway
8. Saves all discovered nodes to a persistent JSON database

### IPND (Local Subnet Discovery)

IPND sends UDP beacons on local subnets. On Pi05:

- **ZeroTier** (10.16.16.x) — broadcasts to 10.16.16.255, discovers Echo and any other ZeroTier nodes
- **Tailscale** (100.75.250.x) — broadcasts to 100.75.250.255, discovers other Tailscale DTN nodes

When a beacon is received, ION automatically creates a temporary contact and can route bundles to the discovered node.

## Installation

### Prerequisites

- ION-DTN 4.1.2 with IPND compiled (`ipnd` binary in PATH)
- Python 3.10+ with `requests` library
- Running ION instance with dtnex

### Setup

```bash
# Clone the repo
git clone git@github.com:anamolsapkota/dtn-pi05.git
cd dtn-pi05

# Copy discovery files to DTN directory
cp -r dtn-discovery/ ~/dtn/dtn-discovery/
chmod +x ~/dtn/dtn-discovery/discovery.py ~/dtn/dtn-discovery/dtn-nodes

# Edit configuration
nano ~/dtn/dtn-discovery/discovery.conf

# Install systemd service
sudo cp dtn-discovery/dtn-discovery.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable dtn-discovery
sudo systemctl start dtn-discovery
```

### Verify

```bash
# Check service status
sudo systemctl status dtn-discovery

# View discovery log
tail -f ~/dtn/dtn-discovery/discovery.log

# List discovered nodes
python3 ~/dtn/dtn-discovery/dtn-nodes

# Summary
python3 ~/dtn/dtn-discovery/dtn-nodes --summary
```

## Configuration

Edit `dtn-discovery/discovery.conf`:

```ini
# This node's IPN number
my_ipn=268485091

# Gateway node (for routing to non-local nodes)
gateway_ipn=268485000

# Scan interval in seconds (300 = 5 minutes)
scan_interval=300

# openipn.org data sources
openipn_metadata_url=https://openipn.org/metadata_list.txt
openipn_graph_url=https://openipn.org/contactGraph.gv

# Local dtnex metadata file
local_metadata_file=/home/pi05/dtn/nodesmetadata.txt

# Persistent discovery database
discovered_db=/home/pi05/dtn/dtn-discovery/discovered_nodes.json

# Log file
log_file=/home/pi05/dtn/dtn-discovery/discovery.log

# Auto-add discovered gateway-reachable nodes to ION
auto_add_contacts=true
auto_add_via_gateway=true

# ION contact parameters for auto-added nodes
contact_rate=100000
contact_duration=360000000
owlt=1

# IPND for local subnet beacons
ipnd_enabled=true
ipnd_config=/home/pi05/dtn/dtn-discovery/ipnd.rc

# Enable debug logging
debug=false
```

### Adapting for your node

To use this on a different node, change these values:

| Parameter | What to change |
|-----------|---------------|
| `my_ipn` | Your node's IPN number |
| `gateway_ipn` | Your gateway's IPN number (usually 268485000) |
| `local_metadata_file` | Path to your dtnex nodesmetadata.txt |
| `discovered_db` | Path for the JSON database |
| `log_file` | Path for the log file |

Also edit `ipnd.rc` to update:
- `m eid` — your node's EID (e.g., `ipn:268485111.0`)
- `a svcadv` — your node's IP and UDP port
- `a destination` — broadcast addresses for your subnets

## IPND Configuration

The `ipnd.rc` file configures ION's beacon-based neighbor discovery:

```
# Initialize
1
e 1

# This node's EID
m eid ipn:268485091.0

# Beacon port (all IPND nodes must use the same port)
m port 4550

# Advertise beacon period and EID
m announce period 1
m announce eid 1

# Beacon intervals (seconds)
m interval unicast 10
m interval multicast 30
m interval broadcast 30

# Service advertisement: where bundles should be sent
a svcadv CLA-UDP-v4 IP:10.16.16.169 Port:4556

# Listen on all interfaces
a listen 0.0.0.0

# Send beacons to these subnets
a destination 10.16.16.255      # ZeroTier
a destination 100.75.250.255    # Tailscale
s
```

## CLI Tool: dtn-nodes

Query discovered nodes from the command line:

```bash
# List all discovered nodes
dtn-nodes

# Output:
# IPN            Name                 Via        In ION  Last Seen    Neighbors
# ------------------------------------------------------------------------------------------
# ipn:268484800  OpenIPNNode          gateway    yes     5m ago       9
# ipn:268485000  DTNGW                unknown    no      5m ago       18
# ipn:268485111  echo-dhulikhel       gateway    yes     5m ago       3

# Show summary statistics
dtn-nodes --summary

# Search by name, IPN, or location
dtn-nodes --search nepal
dtn-nodes --search 268485

# JSON output (for scripting)
dtn-nodes --json
```

## How ION Auto-Addition Works

When a new node is discovered that is reachable via the gateway:

1. The daemon checks the contact graph to see if the gateway (268485000) has a direct edge to the new node
2. If yes, it runs `ionadmin` commands to add bidirectional contacts and ranges:

```
a contact +1 +360000000 268485091 <new_node> 100000
a contact +1 +360000000 <new_node> 268485091 100000
a range +1 +360000000 268485091 <new_node> 1
a range +1 +360000000 <new_node> 268485091 1
a contact +1 +360000000 268485000 <new_node> 100000
a contact +1 +360000000 <new_node> 268485000 100000
a range +1 +360000000 268485000 <new_node> 1
a range +1 +360000000 <new_node> 268485000 1
```

3. ION's CGR then computes routes: bundles for the new node go through the gateway's existing plan

No new outducts or plans are needed — CGR handles routing through the gateway automatically.

## Files

| File | Purpose |
|------|---------|
| `discovery.py` | Main discovery daemon |
| `discovery.conf` | Configuration file |
| `ipnd.rc` | ION IPND beacon configuration |
| `dtn-nodes` | CLI tool for querying discovered nodes |
| `dtn-discovery.service` | systemd service file |
| `discovered_nodes.json` | Persistent node database (auto-generated) |
| `discovery.log` | Discovery log (auto-generated) |

## Data Sources

### openipn.org metadata_list.txt

Format:
```
NODE ID    | METADATA
------------------------------------------------------------
268484800  | OpenIPNNode,samo@grasic.net,Sweden (LOCAL NODE)
268485091  | pi05-anamol,admin@ekrasunya.com,27.717394,85.302740
268485111  | echo-dhulikhel,anamol@ekrasunya.com,27.619200,85.538100
```

Updated by the gateway whenever dtnex receives new metadata from any node.

### openipn.org contactGraph.gv

Graphviz DOT format showing directed edges between nodes:
```dot
"ipn:268485000" -> "ipn:268485091"
"ipn:268485000" -> "ipn:268485111"
"ipn:268484800" -> "ipn:268485000"
```

An edge `A -> B` means node A has declared a contact to node B in its ION configuration (propagated via dtnex).

### Local nodesmetadata.txt

Written by the local dtnex instance. Lists nodes that have exchanged metadata with this node directly or transitively:
```
268485000 DTNGW,samo@grasic.net
268485111 echo-dhulikhel,anamol@ekrasunya.com,27.619200,85.538100
```

## Limitations

- **IPND** only discovers nodes on the same IP subnet (broadcast/multicast range). It cannot discover nodes across the internet.
- **openipn.org scraping** depends on the gateway being online and the openipn.org website being available.
- **Auto-addition** only works for nodes that have a direct edge to the gateway in the contact graph. Nodes behind other relays (multi-hop, no gateway edge) are discovered but not auto-added.
- The daemon does not remove stale contacts. Contacts added via `ionadmin` persist until ION is restarted or they expire based on their duration.

## Troubleshooting

**Discovery daemon not starting:**
```bash
sudo systemctl status dtn-discovery
journalctl -u dtn-discovery -f
```

**No nodes discovered:**
- Check internet connectivity: `curl -s https://openipn.org/metadata_list.txt | head -5`
- Check ION is running: `bpversion`
- Check dtnex is running: `ps aux | grep dtnex`

**IPND not working:**
- Verify `ipnd` binary exists: `which ipnd`
- Check IPND config: `cat ~/dtn/dtn-discovery/ipnd.rc`
- Check firewall: `sudo iptables -L -n | grep 4550`

**Nodes not being auto-added:**
- Check the contact graph has an edge from gateway to the node
- Set `debug=true` in `discovery.conf` and restart
- Check ION is accepting commands: `echo "l plan" | ipnadmin`
