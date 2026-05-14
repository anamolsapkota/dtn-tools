# Architecture

Detailed architecture documentation for dtn-tools — how the system works, how bundles are routed, and how the components fit together.

## System Architecture

```
                          ┌─────────────────────────────────────────────────────────┐
                          │              DTN Node (e.g., Raspberry Pi)              │
                          │                                                         │
                          │  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  │
                          │  │  dtn CLI     │  │  ionwd       │  │  dtnex       │  │
                          │  │  (user tool) │  │  (watchdog)  │  │  (metadata)  │  │
                          │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
                          │         │                 │                  │          │
                          │  ┌──────▼─────────────────▼──────────────────▼───────┐  │
                          │  │                  ION-DTN Engine                    │  │
                          │  │                                                    │  │
                          │  │  ionadmin ─── Contact Graph (CGR routing)          │  │
                          │  │  bpadmin  ─── Bundle Protocol (send/recv)          │  │
                          │  │  ipnadmin ─── Plans & Outducts (forwarding)        │  │
                          │  │  ipnfw    ─── Bundle Forwarding Engine             │  │
                          │  └────────────────────────┬─────────────────────────┘  │
                          │                           │                            │
                          │  ┌────────────────────────▼─────────────────────────┐  │
                          │  │              UDP Convergence Layer                │  │
                          │  │         udpcli (inbound) / udpclo (outbound)     │  │
                          │  │                    Port 4556                      │  │
                          │  └────────────────────────┬─────────────────────────┘  │
                          └───────────────────────────┼─────────────────────────────┘
                                                      │
                                          VPN (Tailscale/ZeroTier)
                                                      │
                          ┌───────────────────────────┼─────────────────────────────┐
                          │          Other DTN Nodes / Gateway (DTNGW)               │
                          └─────────────────────────────────────────────────────────┘
```

## Component Overview

### 1. dtn CLI (`dtn`)

The main user interface. A single Python script (~800 lines) that provides all node management commands. Communicates with ION via `ionadmin`, `bpadmin`, `ipnadmin` admin programs and uses `bpsource`/`bprecvfile` for data transfer.

**Key design decisions:**
- Single-file CLI for easy distribution (`install.sh` symlinks it to `/usr/local/bin/dtn`)
- Auto-detects DTN directory from `~/dtn`, `~/ion-dtn`, or `DTN_DIR` env var
- Follows symlinks with `os.path.realpath()` to find `dtn_tools/` modules
- All neighbor modifications persist to both running ION and `host*.rc` file

### 2. ION-DTN Engine

NASA JPL's Bundle Protocol implementation. The core networking layer that:
- Manages the **contact graph** — knows which nodes can talk to which, and when
- Computes **CGR routes** — finds multi-hop paths through the network
- Handles **store-and-forward** — queues bundles until a link is available
- Provides **convergence layers** — UDP (primary), TCP, LTP

ION is configured via a single `host<IPN>.rc` file containing four sections:

```
## begin ionadmin      ← Contact graph: contacts, ranges, production/consumption rates
## begin bpadmin       ← Bundle Protocol: endpoints, protocol, inducts, outducts
## begin ipnadmin      ← Forwarding: plans mapping IPN → outduct
## begin ionsecadmin   ← Security: (minimal for open network)
```

### 3. dtnex (Metadata Exchange)

A protocol for exchanging node metadata and contact information between DTN nodes. Runs as a background service and:
- Sends your node's metadata (name, email, GPS, contacts) to neighbors
- Receives metadata from other nodes
- Updates the ION contact graph with discovered contacts
- Writes `nodesmetadata.txt` with all known nodes

Contact lifetime is configurable (default: 3600s). Contacts expire and must be refreshed by dtnex.

### 4. ionwd (ION Watchdog)

A shell script that monitors ION and restarts it if it crashes. Runs as a systemd service and:
- Periodically checks if ION is responsive
- Restarts ION with the host.rc config if it's down
- Logs restart events

### 5. Discovery Daemon (`discovery.py`)

A Python daemon that discovers DTN nodes from multiple sources:
- **openipn.org metadata_list.txt** — all nodes exchanging metadata globally
- **openipn.org contactGraph.gv** — the global contact graph in Graphviz DOT format
- **Local dtnex nodesmetadata.txt** — nodes seen by the local dtnex instance
- **ION contacts** — nodes already in the local contact graph

Discovered nodes are stored in `discovered_nodes.json` and optionally auto-added to ION via gateway routing.

### 6. bpecho (Echo Service)

Listens on specific endpoints and echoes back any received bundles. Used by:
- **openipn.org monitoring** — pings nodes to check if they're UP (endpoint `.12161`)
- **dtn neighbors ping** — local connectivity testing (endpoint `.2`)

## Bundle Routing: How Messages Get Delivered

### Contact Graph Routing (CGR)

ION uses CGR to compute routes. The contact graph is a set of edges:

```
contact: node A → node B, rate 100000 bytes/sec, from time T1 to T2
range:   node A ↔ node B, one-way light time 1 second
plan:    node B → outduct udp/10.0.0.2:4556
```

When you send a bundle to `ipn:268485002`, ION:
1. Looks up the contact graph for a path from your node to 268485002
2. Finds: `268485091 → 268485000 → 268485002` (2 hops via gateway)
3. Checks the plan for the first hop (268485000): `udp/100.96.108.37:4556`
4. Sends the bundle to the gateway via UDP
5. The gateway's ION computes the next hop and forwards

**First-hop constraint:** Your node can only send to nodes it has a **plan** for (i.e., direct neighbors with an outduct). Multi-hop routing relies on intermediate nodes forwarding the bundle.

### Direct vs. Multi-Hop

```
Direct (1 hop):       You ──────────► Neighbor
                      plan exists, bundle sent via UDP outduct

Multi-hop (2+ hops):  You ──► Gateway ──► Destination
                      plan to gateway, gateway has plan to destination
                      CGR computes the path from the contact graph

Store-and-forward:    You ──► Gateway ─ ─ ─ ► Destination (offline)
                      bundle queued at gateway until destination contact opens
```

### How `dtn trace` Works

The `traceroute.py` module simulates CGR routing:

1. **Parse contacts** — reads all `ionadmin` contact edges
2. **Parse plans** — reads all `ipnadmin` plans (outducts)
3. **BFS with first-hop constraint** — only seeds through nodes we have plans for
4. **Verify each hop** — checks contact exists, range exists, return contact exists, IP reachable
5. **bping** — attempts DTN-level round-trip time measurement

```
BFS Algorithm:
  queue = [nodes we have plans for AND contacts to]
  while queue:
    current = queue.pop()
    for neighbor in contacts[current]:
      if neighbor == destination: return path
      queue.append(neighbor)
```

## Terminal Chat Protocol

### Service Number

Terminal chat uses **service number 8** (`ipn:<node>.8`). This is separate from:
- `.1` — general purpose / bpsink
- `.2` — bpecho (standard)
- `.7` — web-based dtn-chat application
- `.12161` — bpecho (openipn.org monitoring)

### Message Format

Chat messages are JSON bundles:

```json
{
    "from": "268485091",
    "name": "pi05",
    "msg": "Hello from terminal!",
    "ts": "14:32:10"
}
```

### Sending Flow

```
User types message
       │
       ▼
cmd_chat() builds JSON payload
       │
       ▼
bpsource ipn:<dest>.8 '<JSON>'
       │
       ▼
ION CGR computes route
       │
       ▼
Bundle sent via UDP to first hop
       │
       ▼ (may traverse multiple hops)
       │
Destination ION receives bundle on ipn:<dest>.8
```

### Receiving Flow

```
Background thread starts bprecvfile ipn:<local>.8
       │
       ▼
bprecvfile writes received bundles as files in /tmp/dtn-chat-*/
       │
       ▼
Receiver thread polls directory every 0.5 seconds
       │
       ▼
Reads file → parses JSON → prints message → deletes file
```

### Node Selection

When entering interactive chat mode:
1. All unique IPNs from `ionadmin` contacts are collected
2. Nodes from `ipnadmin` plans are marked with `*` (direct neighbor)
3. Node names are looked up from the discovery daemon's `discovered_nodes.json`
4. User selects by number or enters an IPN directly

No IP address is needed — CGR handles routing through the contact graph.

## Setup Wizard Flow (`dtn init`)

```
Step 1: Install system dependencies
        │ Check: dpkg -s <package>
        │ Action: apt-get install build-essential autoconf automake ...
        ▼
Step 2: Build ION-DTN from source
        │ Check: which ionadmin
        │ Action: git clone → autoreconf → configure → make → make install
        ▼
Step 3: Build dtnex
        │ Check: which dtnex
        │ Action: git clone → build_standalone.sh → make install
        ▼
Step 4: Set up ionwd watchdog
        │ Check: ionwd/ionwd.sh exists
        │ Action: git clone → patch paths in ionwd.sh
        ▼
Step 5: Create directories
        │ Check: ~/dtn/dtn-discovery/ exists
        │ Action: mkdir -p ~/dtn/{dtn-discovery,scripts,logs}
        ▼
Step 6: Generate configuration files
        │ Check: host<IPN>.rc exists
        │ Action: Generate host.rc, dtnex.conf, discovery.conf, ipnd.rc
        ▼
Step 7: Start ION
        │ Check: bpversion returns output
        │ Action: ionstart -I host<IPN>.rc
        ▼
Step 8: Install systemd services
        │ Check: systemctl is-enabled ionwd/dtnex/bpecho/dtn-discovery
        │ Action: Write .service files, daemon-reload, enable, start
        ▼
Step 9: Start bpecho endpoints
        │ Check: pgrep bpecho
        │ Action: bpecho ipn:<IPN>.2 & bpecho ipn:<IPN>.12161
```

Each step is **idempotent** — it checks if the work is already done and skips if so. This makes `dtn init` safe to run multiple times, and allows resuming after failures.

## ION Integration Points

| ION Component | dtn-tools Usage |
|--------------|-----------------|
| `ionadmin` | Read/modify contact graph (contacts, ranges) |
| `bpadmin` | Manage endpoints, outducts, protocols |
| `ipnadmin` | Manage forwarding plans |
| `bpsource` | Send bundles (chat, send) |
| `bprecvfile` | Receive bundles as files (chat receiver) |
| `bpecho` | Echo service for monitoring |
| `bping` | DTN-level ping for RTT measurement |
| `ionstart` | Start ION with configuration |
| `ionstop` | Stop ION |
| `ipnfw` | Bundle forwarding engine (runs automatically) |

## Systemd Services

| Service | Purpose | Depends On |
|---------|---------|------------|
| `ionwd` | ION watchdog — keeps ION running | network.target |
| `dtnex` | Metadata exchange with other nodes | ionwd |
| `bpecho` | Echo service for monitoring (.2 and .12161) | ionwd |
| `dtn-discovery` | Discovery daemon (openipn.org + IPND + dtnex) | dtnex |

Service dependency chain: `ionwd → dtnex → dtn-discovery`

## Network Topology Example

```
                    ┌──────────────────────┐
                    │   openipn.org        │
                    │   (monitoring)       │
                    └──────────┬───────────┘
                               │
                    ┌──────────▼───────────┐
                    │  DTNGW               │
                    │  ipn:268485000       │
                    │  (gateway node)      │
                    └──┬───────────────┬───┘
                       │               │
              Tailscale│               │Tailscale
                       │               │
           ┌───────────▼──┐     ┌──────▼──────────┐
           │  Pi05         │     │  Other Nodes     │
           │  ipn:268485091│     │  ipn:268485002   │
           │  (your node)  │     │  ipn:268485003   │
           └───────┬───────┘     │  ...              │
                   │             └──────────────────┘
          ZeroTier │
                   │
           ┌───────▼───────┐
           │  Echo          │
           │  ipn:268485111 │
           │  (via Pi05)    │
           └────────────────┘
```

Bundles from Echo to other nodes traverse: Echo → Pi05 → DTNGW → Destination

## Data Files

| File | Location | Purpose |
|------|----------|---------|
| `host<IPN>.rc` | `~/dtn/` | ION startup configuration (ionadmin + bpadmin + ipnadmin + ionsecadmin) |
| `dtnex.conf` | `~/dtn/` | dtnex metadata exchange configuration |
| `nodesmetadata.txt` | `~/dtn/` | Metadata received from other nodes via dtnex |
| `contactGraph.png` | `~/dtn/` | Visualization of the contact graph (generated by dtnex) |
| `discovery.conf` | `~/dtn/dtn-discovery/` | Discovery daemon configuration |
| `discovered_nodes.json` | `~/dtn/dtn-discovery/` | Persistent database of discovered nodes |
| `ipnd.rc` | `~/dtn/dtn-discovery/` | IPND beacon configuration |

## Security Model

The current network uses `presSharedNetworkKey=open` in dtnex, meaning all nodes trust each other. This is suitable for research and testing. For production:

- Use a strong shared network key in dtnex.conf
- Consider ION's built-in security (Bundle Protocol Security, BPSec)
- Restrict VPN access (Tailscale ACLs)
