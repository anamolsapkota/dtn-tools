# dtn-tools: A Unified Command-Line Toolkit for ION-DTN Node Management in Terrestrial Research Networks

**Anamol Sapkota**
Independent Researcher, Kathmandu, Nepal

---

## Abstract

Deploying and managing Delay-Tolerant Networking (DTN) nodes using NASA JPL's ION-DTN implementation requires expert knowledge of multiple configuration files, admin programs, and service management. This complexity limits DTN adoption in research and education. We present dtn-tools, an open-source command-line toolkit that unifies ION-DTN node setup, neighbor management, route diagnostics, node discovery, and interactive messaging into a single CLI. We describe the design and implementation of dtn-tools, report operational experience from a multi-node testbed connected to the OpenIPN global DTN research network, and present solutions to problems encountered in real-world operations including contact graph stale state, service crash recovery, relay routing through intermediate nodes, and persistent chat over DTN bundles. Our evaluation shows that dtn-tools reduces node setup from a multi-hour manual process to a single command, provides network observability previously unavailable to ION operators, and enables non-expert users to participate in DTN research networks. dtn-tools is available at https://github.com/anamolsapkota/dtn-tools under the MIT License.

**Keywords:** Delay-Tolerant Networking, Bundle Protocol, ION-DTN, Network Management, CLI Tools, OpenIPN, Contact Graph Routing

---

## 1. Introduction

Delay-Tolerant Networking (DTN) addresses communication in challenged environments where conventional TCP/IP protocols fail due to intermittent connectivity, long delays, and frequent disruptions. Originally developed for deep-space communication, DTN's store-and-forward paradigm has found applications in rural connectivity, disaster response, IoT sensor networks, and military tactical communications.

The Bundle Protocol version 7 (BPv7), standardized as RFC 9171, defines the protocol for transferring data units (bundles) through DTN networks. Several implementations exist, with NASA JPL's ION-DTN being the most widely deployed in research networks. ION provides a complete BPv7 stack with Contact Graph Routing (CGR), multiple convergence layers, and security features.

However, a significant gap exists between the protocol stack and operational usability. Setting up a single ION-DTN node requires writing a multi-section configuration file with four admin sections (ionadmin, bpadmin, ipnadmin, ionsecadmin), compiling software from source, creating systemd services, and manually managing the contact graph. Common operations like adding a neighbor require issuing commands to three separate admin programs and editing the persistent configuration file. Diagnosing routing issues requires manual inspection of contact graphs, ranges, plans, and outducts across multiple ION admin programs.

This paper presents dtn-tools, a unified command-line toolkit that addresses these operational challenges. We describe the system architecture, report on deployment experience across a multi-node testbed connected to the OpenIPN global DTN network, document problems encountered and solutions developed, and evaluate the toolkit's impact on operational complexity.

## 2. Background and Related Work

### 2.1 ION-DTN Architecture

ION-DTN uses shared memory (SDR — Simple Data Recorder) for inter-process communication and data storage. The system is configured through four admin programs:

- **ionadmin**: Manages the contact graph — time-varying edges between nodes with data rates and one-way light times
- **bpadmin**: Manages Bundle Protocol endpoints, convergence layer protocols, inducts (inbound), and outducts (outbound)
- **ipnadmin**: Manages forwarding plans that map IPN node numbers to outducts
- **ionsecadmin**: Manages security policies

All configuration is typically stored in a single `host.rc` file that is loaded at ION startup. Changes to the running system can be made via the admin programs, but these changes are lost on restart unless the host.rc file is also updated.

### 2.2 Contact Graph Routing

ION uses CGR to compute routes through time-varying network topologies. A contact represents a scheduled communication opportunity: "node A can transmit to node B at rate R from time T1 to T2." CGR examines these contacts to find paths from source to destination, considering intermediate nodes. The first-hop constraint means the source must have a forwarding plan (outduct) for the first hop; subsequent hops are handled by intermediate nodes.

### 2.3 OpenIPN Network

The OpenIPN network (openipn.org), operated by the Interplanetary Networking Special Interest Group (IPNSIG), provides a global DTN research testbed. Nodes register for IPN numbers and connect via VPN overlays (Tailscale, ZeroTier). The network includes a gateway node (DTNGW, ipn:268485000) that routes bundles between nodes, a monitoring system that pings nodes' bpecho endpoints, and a Bundle Board that collects and displays sensor data bundles. At the time of writing, approximately 40 nodes from multiple countries participate.

### 2.4 DTN Management Standards

RFC 9675 (November 2024) defines the DTN Management Architecture (DTNMA), recognizing that current DTN deployments rely on "pre-placed keys and bespoke tooling" for management. DTNMA addresses Operations, Administration, and Management (OAM) challenges, but focuses on protocol-level management rather than operational tooling. The gap between management architecture standards and practical deployment tools remains wide.

### 2.5 Recent DTN Deployments

DTN has seen significant operational success in 2024-2025. NASA's PACE mission became the first Class-B NASA mission using DTN operationally, transmitting 34 million bundles with a 100% success rate. NASA's HDTN streamed 4K UHD video between a PC-12 aircraft and the ISS at 900+ Mbps using BPv7 with BPSec. The DTN-COMET project (2025) developed automated containerized testbeds for multi-implementation benchmarking. These successes demonstrate DTN's maturity while highlighting the need for better operational tooling.

### 2.6 Existing Management Tools

The dtnex protocol enables automatic contact and metadata exchange between ION nodes. ionwd provides watchdog monitoring to restart ION after crashes. However, no unified management interface exists for ION-DTN. Operators interact directly with the admin programs.

Other DTN implementations offer varying management approaches:
- **uD3TN**: Python management library (ud3tn-utils) with AAP2Client for daemon interaction
- **DTN7-go**: REST API, WebSocket API, and UNIX socket interface with `dtnclient` CLI
- **HDTN**: Web-based GUI with configuration interface and telemetry dashboard
- **DTNME**: C++ implementation used operationally on the ISS

None of these are compatible with ION's ecosystem or the OpenIPN network, and none provide the unified setup-to-monitoring CLI experience that dtn-tools offers.

## 3. System Design

### 3.1 Architecture Overview

dtn-tools is a Python CLI (~1400 lines) with supporting modules for discovery (~480 lines), route diagnostics (~570 lines), setup wizard (~830 lines), and chat (~400 lines). The main `dtn` script is symlinked to `/usr/local/bin/dtn` for system-wide access.

The CLI wraps ION's admin programs rather than using ION's C API directly. This design choice ensures compatibility across ION versions, avoids shared memory complexities, and allows the tool to work alongside ION without interference.

```
User ─── dtn CLI ─── ION Admin Programs (ionadmin, bpadmin, ipnadmin)
              │                          │
              ├── dtn_tools/init.py      ├── ION SDR (shared memory)
              ├── dtn_tools/discovery.py ├── UDP Convergence Layer
              ├── dtn_tools/traceroute.py└── Contact Graph Router
              └── dtn_tools/chat.py
```

### 3.2 Setup Wizard (dtn init)

The setup wizard automates the complete node deployment process through nine idempotent steps:

1. Install system dependencies (build-essential, autoconf, automake, libtool, etc.)
2. Clone and compile ION-DTN from source (ione-1.1.0 branch)
3. Clone and compile dtnex (metadata exchange protocol)
4. Set up ionwd watchdog
5. Create directory structure (~~/dtn, dtn-discovery, scripts, logs)
6. Generate configuration files (host.rc, dtnex.conf, discovery.conf, ipnd.rc)
7. Start ION with the generated configuration
8. Install and enable systemd services (ionwd, dtnex, bpecho, dtn-discovery)
9. Start bpecho endpoints (.2 and .12161 for monitoring)

Each step checks whether its work has already been done and skips if so. This makes the wizard safe to run multiple times and allows it to resume after failures.

### 3.3 Neighbor Management

Adding a DTN neighbor requires modifications to three ION subsystems: contacts and ranges (ionadmin), outducts (bpadmin), and plans (ipnadmin). dtn-tools unifies this into a single command:

```bash
dtn neighbors add 268485099 100.72.24.15
```

This command:
1. Adds bidirectional contacts with the specified rate and duration
2. Adds bidirectional ranges with one-way light time
3. Adds a UDP outduct to the neighbor's IP:4556
4. Adds a forwarding plan mapping the IPN to the outduct
5. Persists all changes to the host.rc file

Removal is equally simple: `dtn neighbors remove 268485099` reverses all five operations.

### 3.4 Route Diagnostics

The `dtn trace` command simulates CGR routing to show the complete multi-hop path to any destination and identify issues at each hop:

```
$ dtn trace 268485002
DTN Route Trace: ipn:268485091 -> ipn:268485002
  Route: 2 hop(s)
  Path: ipn:268485091 -> ipn:268485000 (DTNGW) -> ipn:268485002
  [OK] Hop 1: via 100.96.108.37:4556 icmp=45ms
       Contact: yes | Range: yes | Return: yes
  [OK] Hop 2: ipn:268485000 -> ipn:268485002
       Contact: yes | Range: yes | Return: yes
```

The trace algorithm:
1. Parses all contacts from ionadmin
2. Parses all plans from ipnadmin
3. Performs BFS with a first-hop constraint (can only seed through nodes with plans)
4. Verifies each hop: contact exists, range exists, return contact exists, IP reachable (ICMP)
5. Optionally attempts bping for DTN-level round-trip time

The `dtn diagnose` command extends this to all known nodes, producing a comprehensive report of the node's view of the network.

### 3.5 Node Discovery

The discovery daemon aggregates node information from four sources:

1. **OpenIPN metadata**: HTTP fetch of the global node list (name, email, GPS coordinates)
2. **OpenIPN contact graph**: HTTP fetch of the global contact graph in Graphviz DOT format
3. **Local dtnex**: Reads metadata exchanged with neighbors via the dtnex protocol
4. **ION contacts**: Reads the local contact graph for already-known nodes

Discovered nodes are classified by reachability (direct, gateway-routed, or unknown) and optionally auto-added to the ION contact graph. The persistent database (discovered_nodes.json) survives restarts and provides node name lookups for other commands.

**Fast recovery**: After ION restarts (which wipe all contacts from shared memory), the discovery daemon detects the sparse contact graph and re-injects cached node information. Nodes with stored outduct addresses are re-added as direct neighbors; gateway-routable nodes are re-added with CGR routing. A configurable staleness threshold (default: 7 days) ensures stale data is not re-injected. Nodes not seen in 30 days are pruned from the database entirely.

### 3.6 Terminal Chat

dtn-tools provides an interactive terminal chat application over DTN bundles using service number 5 (ipn:<node>.5). The chat system features:

- **Persistent per-sender conversations**: Messages are stored in `chat-history.json`, grouped by remote node. History survives chat session restarts.
- **Unread indicators**: Messages from non-active senders are stored as unread with a one-line notification. Switching to a conversation marks all messages as read.
- **Conversation switching**: `/to <name|#|IPN>` switches the active conversation and displays recent history. `/list` shows all conversations with unread counts.
- **No IP addresses needed**: CGR routes bundles automatically through the contact graph. Users specify IPN numbers, not IP addresses.

Messages are JSON-encoded bundles:
```json
{"from": "268485091", "name": "pi05", "msg": "Hello!", "ts": "14:32:10"}
```

The receiver thread runs bprecvfile on the chat endpoint, polls the output directory every 500ms, parses incoming bundles, and routes them to the correct conversation.

### 3.7 Service Management

dtn-tools manages six services: ionwd (watchdog), dtnex (metadata exchange), bpecho (echo service), dtn-chat (web chat), dtn-discovery (discovery daemon), and dtn-metadata-updater (periodic metadata refresh).

Commands (`dtn start/stop/restart/enable/disable`) support optional service arguments and a systemd fallback mechanism: if systemd service files exist, use systemctl; otherwise, manage processes directly via nohup/pkill. This supports devices where systemd services haven't been configured.

### 3.8 IoT Integration

The `dtn sensor` command wraps the bpbme280 tool to send BME280/BMP280 environmental sensor data (temperature, pressure, humidity) as DTN bundles to the IPNSIG Bundle Board (ipn:268484800.6). This enables IoT data collection over DTN with a single command, supporting cron-based periodic reporting.

## 4. Deployment Experience

### 4.1 Testbed

We deployed dtn-tools on two nodes connected to the OpenIPN network:

- **Pi05** (ipn:268485091): Raspberry Pi 4 running Raspberry Pi OS, located in Kathmandu, Nepal. Connected to DTNGW via Tailscale VPN and to Echo via ZeroTier.
- **Echo** (ipn:268485111): x86_64 server running Ubuntu 22.04, located in Dhulikhel, Nepal. Connected to Pi05 via ZeroTier local network.

Echo relays gateway traffic through Pi05 (port 4557 on ZeroTier), requiring Pi05 to have UDP inducts on both port 4556 (Tailscale) and 4557 (ZeroTier relay).

### 4.2 Problems Encountered and Solutions

#### 4.2.1 Contact Graph Stale State

**Problem**: After multiple neighbor add/remove/add cycles, ION's outduct-to-plan attachments become stale. The error "Duct is already attached to a plan" prevents re-adding neighbors even after removing them.

**Solution**: A full ION restart (ionstop, killm, ionstart) clears the shared memory state. dtn-tools' restart command handles this cleanly. For prevention, the neighbor management code checks for existing outducts before adding new ones.

#### 4.2.2 dtnex Buffer Overflow on x86_64

**Problem**: The dtnex binary crashed with "stack smashing detected" during CBOR metadata exchange on the x86_64 Echo node. The crash occurred on every start, creating a restart loop (113+ restarts observed).

**Root cause**: The node metadata string in dtnex.conf was too long for a fixed-size buffer in the CBOR encoder. The original string included system metrics (uptime, memory, disk, load) that made it exceed the buffer.

**Solution**: Shortened the metadata string to contain only essential fields (node name, email, location). dtnex then ran stably and exchanged metadata with 2 neighbors, refreshing 86 contacts.

#### 4.2.3 bpversion Exit Code

**Problem**: `bpversion` returns exit code 7 when successful (outputting "bpv7"), causing shell constructs like `bpversion || echo "not running"` to trigger the fallback. This caused `dtn status` to show "ION not running" even when ION was operating normally.

**Solution**: Check for output content rather than exit code: if bpversion produces output, ION is installed and responding.

#### 4.2.4 Relay Routing

**Problem**: Echo needed to reach the DTNGW gateway but had no direct Tailscale connectivity. Bundles had to be relayed through Pi05.

**Solution**: Echo's gateway plan points to Pi05's ZeroTier IP on port 4557 (`a plan 268485000 udp/10.16.16.169:4557`). Pi05 needed an additional UDP induct on port 4557 (`a induct udp 0.0.0.0:4557 udpcli`). ION's CGR at Pi05 then forwards the bundle to the actual gateway via Tailscale.

#### 4.2.5 bpecho Wrong Endpoint

**Problem**: Echo's bpecho service was configured on endpoint .1 instead of .2 and .12161. The OpenIPN monitor pings .12161, so Echo showed as DOWN despite being operational.

**Solution**: Fixed the systemd service file to start bpecho on both .2 (standard) and .12161 (OpenIPN monitoring) endpoints using a forking service type.

#### 4.2.6 Contact Expiration After Restart

**Problem**: After ION restart, all contacts from dtnex expired and were not re-added until the next dtnex exchange cycle (up to 30 minutes). During this window, the node had no routes.

**Solution**: Implemented discovery-based fast recovery (Section 3.5). The discovery daemon detects sparse contact state and re-injects cached nodes immediately on startup.

#### 4.2.7 bpclm Not Starting

**Problem**: After re-adding a previously removed plan, ION's Bundle Protocol Contact List Manager (bpclm) did not start for the new plan, preventing bundle forwarding.

**Solution**: Full ION restart clears the stale state. This is an ION-internal issue where plan deletion doesn't fully clean up the bpclm state.

### 4.3 Operational Results

After deploying dtn-tools and resolving the issues above:

- **Pi05** achieved 91% uptime (24h) and 83% (7d) on OpenIPN monitoring with 0% bundle loss and 340ms RTT to the gateway.
- **Echo** transitioned from DOWN (0% uptime due to dtnex crash) to operational after the metadata fix and bpecho endpoint correction.
- Both nodes successfully exchanged chat messages over DTN bundles, with messages surviving node restarts via persistent history.
- Sensor data from Pi05's BMP280 sensor appeared on the IPNSIG Bundle Board.
- The full network view from Pi05 showed 28 known nodes, 20 routable, with 11 direct neighbors.

## 5. Evaluation

### 5.1 Setup Complexity Reduction

| Task | Without dtn-tools | With dtn-tools |
|------|-------------------|----------------|
| Full node setup | ~2 hours, 50+ manual steps | `dtn init` — 1 command, ~15 min (build time) |
| Add a neighbor | 8 ionadmin/bpadmin/ipnadmin commands + edit host.rc | `dtn neighbors add <IPN> <IP>` — 1 command |
| Check node health | Multiple admin queries, manual interpretation | `dtn status` or `dtn diagnose` — 1 command |
| Trace a route | Manual BFS through contact graph output | `dtn trace <IPN>` — 1 command |
| Send a message | Construct bpsource command with endpoint | `dtn chat` — interactive UI |
| View network | Parse ionadmin output manually | `dtn nodes` — formatted table |

### 5.2 Network Recovery Time

| Scenario | Without caching | With discovery caching |
|----------|----------------|----------------------|
| ION restart, contacts lost | Wait for dtnex cycle (up to 30 min) | Immediate re-injection from cache |
| Node reboot | Full convergence: 5-30 min | Contacts restored in <10 seconds |

### 5.3 Diagnostic Accuracy

The `dtn trace` command correctly identified:
- Missing contacts between Pi05 and Echo after expiration
- Unreachable neighbors (100% ICMP failure detection)
- Missing return contacts that would prevent bundle acknowledgment
- Multi-hop paths through the gateway

## 6. Limitations

1. **ION-DTN only**: dtn-tools is tightly coupled to ION's admin programs. Supporting uD3TN, DTN7, or HDTN would require a different backend.
2. **UDP convergence layer only**: The current implementation manages UDP outducts. TCP (TCPCL) and LTP convergence layers are not yet supported.
3. **No end-to-end encryption**: Chat messages are transmitted in plaintext. ION's BPSec is not utilized.
4. **Single-user chat**: The chat system doesn't support group conversations or chat rooms.
5. **Linux only**: Tested on Raspberry Pi OS, Ubuntu, and Debian. macOS and Windows are not supported.
6. **Admin program wrapping**: Using subprocess calls to ION admin programs is slower than using the C API directly, though the difference is negligible for management operations.
7. **Contact graph scale**: The BFS-based route tracing works well for the current OpenIPN network (~40 nodes) but may need optimization for larger networks.
8. **Security**: The OpenIPN network uses a shared key ("open") for all nodes. dtn-tools does not implement additional security beyond what ION provides.

## 7. Future Work

1. **Web dashboard**: A browser-based monitoring interface for node status, contact graph visualization, and chat.
2. **TCP convergence layer**: Support for TCPCL in addition to UDP.
3. **File transfer**: `dtn send-file` for reliable file transfer over DTN bundles.
4. **Group chat**: Multi-party chat rooms over DTN.
5. **Security integration**: BPSec configuration management and encrypted chat.
6. **Multi-implementation support**: Adapter pattern to support uD3TN and DTN7 backends.
7. **Automated testing**: Test framework using ION's loopback mode for CI/CD.
8. **Docker support**: Containerized DTN nodes for easy testbed deployment.

## 8. Conclusion

dtn-tools demonstrates that significant improvements in DTN operational usability are achievable through thoughtful CLI design. By wrapping ION-DTN's complex admin interface into intuitive commands, the toolkit makes DTN accessible to researchers, students, and operators who lack deep ION expertise. Our deployment experience on a real multi-node testbed connected to the OpenIPN global network validates the approach and reveals practical challenges in terrestrial DTN operations that the research community can benefit from.

The toolkit's contributions — automated setup, multi-source discovery, route diagnostics, persistent chat, and fast recovery — address the operational gap that has historically limited DTN adoption beyond the space networking community. We hope dtn-tools enables broader participation in DTN research and accelerates the development of applications on the OpenIPN network.

## 9. Availability

dtn-tools is open source under the MIT License:
- Repository: https://github.com/anamolsapkota/dtn-tools
- OpenIPN registration: https://openipn.org

## References

[1] S. Burleigh, K. Fall, and E. Birrane, "Bundle Protocol Version 7," RFC 9171, IETF, January 2022.

[2] S. Burleigh, "Interplanetary Overlay Network (ION) Design and Operation, v4.1," Jet Propulsion Laboratory, California Institute of Technology, 2020.

[3] K. Scott and S. Burleigh, "Bundle Protocol Specification," RFC 5050, IETF, November 2007.

[4] E. Birrane, A. Mayer, and J. Miner, "Bundle Protocol Security (BPSec)," RFC 9172, IETF, January 2022.

[5] V. Cerf et al., "Delay-Tolerant Networking Architecture," RFC 4838, IETF, April 2007.

[6] S. Burleigh, "Contact Graph Routing," Internet-Draft, IETF, 2010.

[7] E. Birrane and S. Heiner, "Delay-Tolerant Networking Management Architecture (DTNMA)," RFC 9675, IETF, November 2024.

[8] S. Grasic, "OpenIPN: Building a Global DTN Research Network," IPNSIG Technical Report, 2023.

[9] M. Feldmann and F. Walter, "uD3TN: A Lightweight DTN Protocol Implementation for Microcontrollers," Proceedings of the International Conference on Networked Systems, 2021.

[10] S. Grasic and E. Lindgren, "An Analysis of Evaluation Practices for Delay-Tolerant Networking," IEEE Communications Surveys & Tutorials, 2015.

[11] B. Nöthlich et al., "DTN-COMET: Automated Containerized Testbeds for Multi-Implementation Benchmarking," January 2025.

[12] NASA Goddard Space Flight Center, "PACE Mission DTN Operations Report," NASA Technical Reports Server, 2024.

[13] NASA Glenn Research Center, "HDTN 4K UHD Video Streaming over BPv7 between PC-12 Aircraft and ISS," NASA Technical Reports Server, 2024.

[14] T. Johnson, "DTN IP Neighbor Discovery (IPND)," Internet-Draft, IETF, 2019.

[15] H. Kruse et al., "Datagram Convergence Layers for the Delay- and Disruption-Tolerant Networking (DTN) Bundle Protocol and Licklider Transmission Protocol (LTP)," RFC 7122, IETF, March 2014.

[16] IETF DTN Working Group, "Bundle Protocol Version 7 Administrative Record Types Registry," RFC 9713, IETF, January 2025.
