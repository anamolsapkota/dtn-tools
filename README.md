# dtn-tools

A command-line toolkit for setting up, managing, and monitoring DTN (Delay-Tolerant Networking) nodes with [ION-DTN](https://github.com/nasa-jpl/ION-DTN).

**One command to set up a DTN node. One command to discover the network. One command to send bundles.**

## Why dtn-tools?

Setting up a DTN node with ION-DTN typically requires editing multiple configuration files (`ionadmin`, `bpadmin`, `ipnadmin`, `ionsecadmin`), manually managing contacts and ranges, and running several daemons. `dtn-tools` simplifies this entire workflow into a single CLI.

- **Easy setup** — Interactive wizard generates ION configuration files
- **Neighbor discovery** — Finds DTN nodes locally (IPND beacons) and globally (openipn.org)
- **Node management** — Add/remove neighbors, send bundles, monitor status
- **Auto-configuration** — Discovered nodes are automatically added to your contact graph
- **Works with openipn.org** — Integrates with the global DTN node registry

## Quick Start

### Install

```bash
# Clone and install
git clone https://github.com/anamolsapkota/dtn-tools.git
cd dtn-tools
sudo ./install.sh

# Or install manually
sudo cp dtn /usr/local/bin/
sudo cp dtn_tools/*.py /usr/local/lib/dtn-tools/
```

### Setup a new DTN node

```bash
# Interactive setup wizard
dtn init

# Or provide parameters directly
dtn init --ipn 268485091 --name "my-node" --email "user@example.com"
```

### Basic usage

```bash
# Check node status
dtn status

# List neighbors
dtn neighbors

# Add a new neighbor
dtn neighbors add 268485099 100.72.24.15

# Remove a neighbor
dtn neighbors remove 268485099

# Ping neighbors
dtn neighbors ping

# Discover DTN nodes worldwide
dtn discover

# Send a message
dtn send 268485000 "Hello from my DTN node"

# Trace route to a node (identify where issues are)
dtn trace 268485111

# Run full diagnostics on all neighbors
dtn diagnose

# View contacts and plans
dtn contacts
dtn plans

# View logs
dtn logs dtnex
```

## Features

### Node Setup (`dtn init`)

Interactive wizard that:
1. Asks for your IPN number (from [openipn.org](https://openipn.org) registration)
2. Generates ION configuration files (`ionrc`, `bprc`, `ipnrc`, `ionsecrc`)
3. Configures dtnex for metadata exchange
4. Sets up IPND for local neighbor discovery
5. Creates systemd services for all daemons
6. Optionally connects to the openipn.org gateway

### Neighbor Discovery (`dtn discover`)

Finds DTN nodes from multiple sources:

| Source | Scope | How |
|--------|-------|-----|
| **IPND** | Local subnet | UDP broadcast/multicast beacons on port 4550 |
| **openipn.org** | Global | Scrapes the public metadata list and contact graph |
| **dtnex** | Neighbors | Reads locally-exchanged metadata |

Discovered nodes are classified by reachability (direct, via gateway, unknown) and can be auto-added to your ION contact graph.

### Node Management (`dtn neighbors`)

```bash
dtn neighbors              # List all configured neighbors
dtn neighbors add IPN IP   # Add a neighbor with IPN number and IP
dtn neighbors remove IPN   # Remove a neighbor
dtn neighbors ping         # Ping all neighbors
dtn neighbors ping IPN     # Ping a specific neighbor
```

Adding a neighbor:
- Creates bidirectional contacts and ranges in `ionadmin`
- Adds a UDP outduct in `bpadmin`
- Adds a forwarding plan in `ipnadmin`
- Updates the persistent configuration file

### Route Diagnostics (`dtn trace`, `dtn diagnose`)

Trace the bundle path to any node and identify exactly where issues exist:

```bash
$ dtn trace 268485111

DTN Route Trace: ipn:268485091 -> ipn:268485111
============================================================

  Route (2 hops):

  Hop 1: ipn:268485091 -> ipn:268485000 (100.96.108.37:4556) rtt=45ms [has plan]
         Contact: yes | Range: yes | Return: yes  [OK]

  Hop 2: ipn:268485000 -> ipn:268485111
         Contact: yes | Range: yes | Return: NO  [!!]
         ISSUE: NO RETURN CONTACT

============================================================
  Route to ipn:268485111: ISSUES FOUND

  Hop 2 (ipn:268485000 -> ipn:268485111): NO RETURN CONTACT
    Fix: ionadmin 'a contact +1 +360000000 268485111 268485000 100000'
```

Run diagnostics on all neighbors at once:

```bash
$ dtn diagnose

DTN Node Diagnostics: ipn:268485091
============================================================
  ION: Running
  dtnex: Running (pid 73832)
  bpecho: Running (pid 76354)

  Plans: 8
  Contacts: 42
  Ranges: 42

Neighbor Connectivity:
------------------------------------------------------------
  [OK] ipn:268485000 via 100.96.108.37:4556 rtt=45ms
  [OK] ipn:268485111 via 10.16.16.17:4556 rtt=2ms
  [!!] ipn:268485099 via 100.72.24.15:4556
       - unreachable (100.72.24.15)

1 neighbor(s) with issues.
```

### Monitoring (`dtn status`)

```
$ dtn status

DTN Node: ipn:268485091
ION Version: bpv7
ION Status: Running

Service                   Status       PID
-------------------------------------------------------
ionwd                     active       10051
dtnex                     active       73832
bpecho                    active       76354
dtn-chat                  active       59531
dtn-discovery             active       76399
```

## Architecture

```
dtn-tools/
├── dtn                     # Main CLI entry point
├── dtn_tools/
│   ├── discovery.py        # Neighbor discovery daemon
│   ├── init.py             # Node setup wizard
│   └── ipnd.py             # IPND configuration generator
├── examples/
│   ├── ipnd.rc             # Example IPND config
│   └── discovery.conf      # Example discovery config
├── docs/
│   ├── SETUP.md            # Detailed setup guide
│   └── DISCOVERY.md        # Discovery system documentation
├── install.sh              # Installation script
└── dtn-discovery.service   # systemd service for discovery
```

## Requirements

- **ION-DTN 4.1.2+** — [Installation guide](https://ion-dtn.readthedocs.io/)
- **Python 3.10+** with `requests`
- **Linux** (tested on Raspberry Pi OS, Ubuntu, Debian)
- Optional: [dtnex](https://github.com/samograsic/ion-dtn-dtnex) for metadata exchange
- Optional: [ionwd](https://github.com/samograsic/ionwd) for ION watchdog

## Network

This tool integrates with the [OpenIPN](https://openipn.org) global DTN network:

- **Node Registration**: Get your IPN number at [openipn.org](https://openipn.org)
- **Dashboard**: View all active nodes on the [map](https://openipn.org)
- **Gateway**: Route bundles to other nodes via the DTNGW (ipn:268485000)

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development

```bash
git clone https://github.com/anamolsapkota/dtn-tools.git
cd dtn-tools
# Make changes
# Test on a DTN node
scp -r . user@dtn-node:~/dtn-tools/
ssh user@dtn-node "cd dtn-tools && sudo ./install.sh"
```

### Areas for contribution

- Support for other DTN implementations (uD3TN, HDTN, DTN7)
- TCP convergence layer support (in addition to UDP)
- Web dashboard for node monitoring
- Docker/container support
- macOS and Windows support
- Automated testing framework

## Related Projects

- [ION-DTN](https://github.com/nasa-jpl/ION-DTN) — NASA JPL's DTN implementation
- [ion-core](https://github.com/nasa-jpl/ion-core) — Streamlined ION for embedded systems
- [dtnex](https://github.com/samograsic/ion-dtn-dtnex) — DTN metadata exchange protocol
- [ionwd](https://github.com/samograsic/ionwd) — ION watchdog daemon
- [openipn.org](https://openipn.org) — Global DTN node registry
- [uD3TN](https://gitlab.com/d3tn/ud3tn) — Lightweight DTN implementation
- [DTN7](https://github.com/dtn7) — DTN in Go
- [HDTN](https://github.com/nasa/HDTN) — NASA Glenn's High-rate DTN

## License

MIT License. See [LICENSE](LICENSE) for details.
