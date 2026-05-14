# DTN Node Setup Guide

Step-by-step guide to setting up a DTN node using dtn-tools and ION-DTN.

## Prerequisites

### Hardware

Any Linux system works. Tested on:
- Raspberry Pi 4/5 (recommended for always-on nodes)
- Ubuntu 22.04+ x86_64
- Debian 12+ ARM64

### Software

1. **ION-DTN 4.1.2+** — The DTN Bundle Protocol implementation

   ```bash
   # Build from source (recommended)
   git clone https://github.com/nasa-jpl/ION-DTN.git
   cd ION-DTN
   autoreconf -fi
   ./configure
   make
   sudo make install
   sudo ldconfig
   ```

   Or use the [OpenIPN build guide](https://doc.openipn.org/s/yKisCBh65).

2. **dtnex** — Metadata exchange protocol

   ```bash
   git clone https://github.com/samograsic/ion-dtn-dtnex.git
   cd ion-dtn-dtnex
   make
   sudo make install
   ```

3. **Python 3.10+** with `requests`

   ```bash
   sudo apt install python3 python3-pip
   pip3 install requests
   ```

4. **Network connectivity** — One of:
   - [Tailscale](https://tailscale.com/) VPN (recommended)
   - [ZeroTier](https://www.zerotier.com/)
   - Direct IP connectivity

### Get an IPN Number

Register at [openipn.org](https://openipn.org) to get your IPN node number. This is your unique identifier in the DTN network.

## Installation

```bash
git clone https://github.com/anamolsapkota/dtn-tools.git
cd dtn-tools
sudo ./install.sh
```

## Setup

### Interactive Setup

```bash
dtn init
```

The wizard will ask for:

| Parameter | Example | Description |
|-----------|---------|-------------|
| IPN number | 268485091 | Your registered IPN |
| Node name | my-node | Friendly name for your node |
| Email | user@example.com | Contact email (visible to other nodes) |
| Location | Kathmandu, Nepal | Physical location |
| GPS coordinates | 27.67, 85.33 | Optional, for map display |
| Gateway IP | 100.96.108.37 | Tailscale IP of the DTNGW |
| UDP port | 4556 | Default DTN port |

### Non-Interactive Setup

```bash
dtn init --ipn 268485091 --name "my-node" --email "user@example.com"
```

### What Gets Created

```
~/dtn/
├── host268485091.rc          # ION startup configuration
├── dtnex.conf                # dtnex metadata exchange config
├── dtn-discovery/
│   ├── discovery.py          # Discovery daemon
│   ├── discovery.conf        # Discovery configuration
│   └── ipnd.rc               # IPND beacon configuration
├── scripts/
├── logs/
└── contactGraph.png          # Generated contact graph
```

## Starting the Node

```bash
# Start ION
ionstart -I ~/dtn/host268485091.rc

# Start services
sudo systemctl start dtnex
sudo systemctl start bpecho
sudo systemctl start dtn-discovery

# Verify
dtn status
```

## Adding Neighbors

### Via CLI

```bash
# Add a neighbor with their IPN and Tailscale IP
dtn neighbors add 268485099 100.72.24.15

# Verify
dtn neighbors
dtn neighbors ping 268485099
```

### Via Discovery

The discovery daemon automatically finds and adds nodes:

```bash
# View discovered nodes
dtn discover

# See summary
dtn discover summary
```

## Verifying on openipn.org

After setup, your node should appear on [openipn.org](https://openipn.org):

1. **Metadata visible** — Your node name, email, location shown on the map
2. **Status UP** — bpecho responds to pings from the monitoring node
3. **Contacts shown** — Your connections visible in the contact graph

If metadata doesn't appear:
- Check dtnex is running: `systemctl status dtnex`
- Check dtnex config: `dtn config`
- Verify gateway connectivity: `dtn neighbors ping 268485000`

## Troubleshooting

### ION won't start

```bash
# Check for stale processes
killm

# Try starting again
ionstart -I ~/dtn/host*.rc

# Check logs
tail -f ~/dtn/ion.log
```

### Node shows DOWN on openipn.org

The monitoring node (ipn:268484800) needs to be able to ping your node:

1. Ensure bpecho is running: `systemctl status bpecho`
2. Ensure gateway has a contact to your node (via dtnex propagation)
3. Check transit forwarding: your node must forward bundles (ipnfw)

### No metadata on openipn.org

1. Ensure dtnex is running from the correct directory: `cd ~/dtn && dtnex dtnex.conf`
2. Check `noMetadataExchange=false` in dtnex.conf
3. Wait for metadata propagation (~30 minutes)

### Can't reach other nodes

1. Check VPN connectivity: `ping <tailscale-ip>`
2. Verify ION plans: `dtn plans`
3. Test with bping: `dtn neighbors ping <IPN>`
