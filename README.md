# dtn-tools

A command-line toolkit for setting up, managing, and monitoring DTN (Delay-Tolerant Networking) nodes with [ION-DTN](https://github.com/nasa-jpl/ION-DTN).

**One command to set up a DTN node. One command to discover the network. One command to chat.**

## Why dtn-tools?

Setting up a DTN node with ION-DTN typically requires editing multiple configuration files (`ionadmin`, `bpadmin`, `ipnadmin`, `ionsecadmin`), manually managing contacts and ranges, compiling multiple software packages, and running several daemons. `dtn-tools` simplifies this entire workflow into a single CLI.

- **Complete setup** — `dtn init` handles everything: installs ION-DTN, dtnex, ionwd, generates config, creates systemd services
- **Neighbor discovery** — Finds DTN nodes locally (IPND beacons) and globally (openipn.org)
- **Node management** — Add/remove neighbors, send bundles, monitor status
- **Route diagnostics** — Trace multi-hop routes, identify issues, measure RTT
- **Terminal chat** — Send and receive messages to any node in the network
- **Auto-configuration** — Discovered nodes are automatically added to your contact graph
- **Works with openipn.org** — Integrates with the global DTN node registry

## Quick Start

### Install

```bash
git clone https://github.com/anamolsapkota/dtn-tools.git
cd dtn-tools
sudo ./install.sh
```

### Set up a new DTN node

```bash
# Interactive setup wizard — installs ION, dtnex, ionwd, everything
dtn init

# Or provide parameters directly
dtn init --ipn 268485091 --name "my-node" --email "user@example.com" --yes
```

### Basic usage

```bash
# Check node status
dtn status

# List all nodes in the network
dtn nodes

# Chat with any node
dtn chat

# Trace route to a node
dtn trace 268485111

# Run full diagnostics
dtn diagnose

# Add a neighbor
dtn neighbors add 268485099 100.72.24.15

# Send a bundle
dtn send 268485000 "Hello from my DTN node"
```

## Commands

| Command | Description |
|---------|-------------|
| `dtn init` | Complete node setup wizard (ION, dtnex, ionwd, config, services) |
| `dtn status` | Show node and service status |
| `dtn nodes` | List all nodes in the ION contact graph |
| `dtn neighbors` | List direct neighbors (with plans) |
| `dtn neighbors add <IPN> <IP>` | Add a neighbor (persists to running ION + host.rc) |
| `dtn neighbors remove <IPN>` | Remove a neighbor (persists to running ION + host.rc) |
| `dtn neighbors ping [IPN]` | Ping one or all neighbors |
| `dtn discover` | List nodes found by the discovery daemon |
| `dtn chat [IPN]` | Interactive terminal chat over DTN bundles |
| `dtn chat <IPN> "message"` | Send a one-shot chat message |
| `dtn send <IPN> "message"` | Send a raw DTN bundle |
| `dtn trace <IPN>` | Trace the bundle route and identify issues |
| `dtn diagnose` | Full diagnostics on all nodes and routes |
| `dtn contacts` | List all ION contact graph edges |
| `dtn plans` | List ION forwarding plans |
| `dtn restart` | Restart ION and all DTN services |
| `dtn logs [service]` | Tail service logs |
| `dtn config` | Show node configuration |

## Features

### Complete Node Setup (`dtn init`)

The setup wizard handles the entire process:

1. **System dependencies** — Installs build tools, libraries, Python packages
2. **ION-DTN** — Clones and builds from source (ione-1.1.0 branch)
3. **dtnex** — Builds the metadata exchange protocol
4. **ionwd** — Sets up the ION watchdog for automatic restart
5. **Configuration** — Generates `host.rc`, `dtnex.conf`, `discovery.conf`, `ipnd.rc`
6. **Systemd services** — Creates and enables `ionwd`, `dtnex`, `bpecho`, `dtn-discovery`
7. **Starts ION** — Launches the node and all services

Each step checks if already done and skips automatically. Running `dtn init` on an existing node is safe.

### Terminal Chat (`dtn chat`)

Chat with any node in the DTN network directly from the terminal:

```
$ dtn chat
============================================================
  DTN Terminal Chat
  Your node: pi05-anamol (ipn:268485091)
  Listening on: ipn:268485091.8
============================================================

  Available nodes (39):
      1. DTNGW (ipn:268485000) *
      2. SatPI (ipn:268484608)
      3. echo-dhulikhel (ipn:268485111) *
      ...
     39. RoEduNet-DTN-01 (ipn:268796000)

  * = direct neighbor.  All others routed via contact graph.

  Select node to chat with: 3

  Chatting with: echo-dhulikhel (ipn:268485111)
  Type messages and press Enter. Ctrl+C or 'quit' to exit.
------------------------------------------------------------
  you> Hello from terminal!
  [14:32:10] echo: Hey back!
```

- Lists all nodes from the ION contact graph (not just direct neighbors)
- No IP address needed — CGR routes bundles automatically
- Background receiver thread for incoming messages
- Switch recipients mid-chat with `to <number>`

### Route Diagnostics (`dtn trace`, `dtn diagnose`)

Trace the bundle path to any node and identify exactly where issues exist:

```
$ dtn trace 268485002

DTN Route Trace: ipn:268485091 -> ipn:268485002
======================================================================

  Route: 2 hop(s)
  Path:  ipn:268485091 -> ipn:268485000 (DTNGW) -> ipn:268485002

  [OK] Hop 1: ipn:268485091 -> ipn:268485000 (DTNGW) via 100.96.108.37:4556 icmp=45ms [plan]
         Contact: yes | Range: yes | Return: yes

  [OK] Hop 2: ipn:268485000 -> ipn:268485002
         Contact: yes | Range: yes | Return: yes
```

Full network diagnostics with `dtn diagnose`:

```
$ dtn diagnose

DTN Node Diagnostics: ipn:268485091
======================================================================
  ION: Running
  dtnex: Running
  bpecho: Running
  discovery: Running

  Plans (direct neighbors): 10
  Contacts (edges):         126
  Unique nodes in graph:    40
  Discovered nodes:         19

Direct Neighbors (with plans):
----------------------------------------------------------------------
  [OK] ipn:268485000 (DTNGW) via 100.96.108.37:4556 icmp=45ms
  [OK] ipn:268485111 (echo-dhulikhel) via 10.16.16.17:4556 icmp=3ms
  ...

Remote Nodes (via contact graph — 30 nodes):
----------------------------------------------------------------------
  [OK] ipn:268485002 — 2 hop(s) via ipn:268485000 (DTNGW)
  [OK] ipn:268796000 (RoEduNet-DTN-01) — 3 hop(s) via echo-dhulikhel
  ...

Total: 40 nodes known, 40 routable, 0 unreachable.
```

### Neighbor Discovery (`dtn discover`)

Finds DTN nodes from multiple sources:

| Source | Scope | How |
|--------|-------|-----|
| **IPND** | Local subnet | UDP broadcast/multicast beacons on port 4550 |
| **openipn.org** | Global | Scrapes the public metadata list and contact graph |
| **dtnex** | Neighbors | Reads locally-exchanged metadata |

Discovered nodes are classified by reachability and can be auto-added to your ION contact graph.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture documentation covering:
- System architecture and component diagram
- How DTN bundle routing works
- Terminal chat protocol and message flow
- ION integration points
- Setup wizard step-by-step flow

## Requirements

- **Linux** (tested on Raspberry Pi OS, Ubuntu, Debian)
- **Python 3.10+** with `requests`
- **ION-DTN 4.1.2+** — installed automatically by `dtn init`
- Optional: [dtnex](https://github.com/samograsic/ion-dtn-dtnex) — installed automatically by `dtn init`
- Optional: [ionwd](https://github.com/samograsic/ionwd) — installed automatically by `dtn init`
- Network: [Tailscale](https://tailscale.com/), [ZeroTier](https://www.zerotier.com/), or direct IP connectivity

## Network

This tool integrates with the [OpenIPN](https://openipn.org) global DTN network:

- **Node Registration**: Get your IPN number at [openipn.org](https://openipn.org)
- **Dashboard**: View all active nodes on the [map](https://openipn.org)
- **Gateway**: Route bundles to other nodes via the DTNGW (ipn:268485000)
- **Setup Guide**: [Official setup guide](https://doc.openipn.org/s/yKisCBh65)

## Project Structure

```
dtn-tools/
├── dtn                      # Main CLI entry point (~800 lines)
├── dtn_tools/
│   ├── init.py              # Complete node setup wizard
│   ├── traceroute.py        # Route tracing and diagnostics
│   ├── discovery.py         # Neighbor discovery daemon
│   └── dtn_nodes_cli.py     # Node listing utilities
├── docs/
│   ├── ARCHITECTURE.md      # Detailed architecture documentation
│   ├── SETUP.md             # Setup guide
│   └── DISCOVERY.md         # Discovery system documentation
├── examples/
│   ├── ipnd.rc              # Example IPND config
│   └── discovery.conf       # Example discovery config
├── install.sh               # Installation script (symlinks to PATH)
├── CONTRIBUTING.md           # Contribution guidelines
└── LICENSE                   # MIT License
```

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Areas for contribution

- Support for other DTN implementations (uD3TN, HDTN, DTN7)
- TCP convergence layer support (in addition to UDP)
- Web dashboard for node monitoring
- File transfer over DTN (`dtn send-file`)
- Group chat rooms
- Docker/container support
- macOS and Windows support
- Automated testing framework

## Related Projects

- [ION-DTN](https://github.com/nasa-jpl/ION-DTN) — NASA JPL's DTN implementation
- [dtnex](https://github.com/samograsic/ion-dtn-dtnex) — DTN metadata exchange protocol
- [ionwd](https://github.com/samograsic/ionwd) — ION watchdog daemon
- [openipn.org](https://openipn.org) — Global DTN node registry
- [uD3TN](https://gitlab.com/d3tn/ud3tn) — Lightweight DTN implementation
- [DTN7](https://github.com/dtn7) — DTN in Go
- [HDTN](https://github.com/nasa/HDTN) — NASA Glenn's High-rate DTN

## License

MIT License. See [LICENSE](LICENSE) for details.
