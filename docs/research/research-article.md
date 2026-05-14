# dtn-tools: A Unified Command-Line Toolkit for ION-DTN Node Management in Terrestrial Research Networks

**Anamol Sapkota**
Independent Researcher, Kathmandu, Nepal

---

## Abstract

Deploying and managing Delay-Tolerant Networking (DTN) nodes using NASA JPL's ION-DTN implementation requires expert knowledge of multiple configuration subsystems, convergence layer setup, contact graph management, and service orchestration. This complexity limits DTN adoption in research and education despite the protocol's demonstrated operational maturity — NASA's PACE mission delivered 34 million bundles with 100% reliability, and HDTN streamed 4K video at 900+ Mbps over BPv7. We present dtn-tools, an open-source Python command-line toolkit (~3,500 lines, 19 commands) that unifies ION-DTN node setup, neighbor management, route diagnostics, node discovery, persistent terminal chat, and service lifecycle management into a single CLI. We report operational experience from a two-node testbed — a Raspberry Pi 4 (ipn:268485091, Kathmandu) and an x86_64 server (ipn:268485111, Dhulikhel) — connected to the 40-node OpenIPN global DTN research network, documenting eight distinct operational challenges including contact graph stale state after add/remove cycles, dtnex CBOR buffer overflow on x86_64, bpversion exit code 7 on success, and relay routing through intermediate nodes on different ports. Our evaluation shows that dtn-tools reduces node setup from 50+ manual steps to a single command (~15 minutes build time), reduces per-neighbor configuration from 8 admin commands to 1, and cuts post-restart network recovery from up to 30 minutes to under 10 seconds through discovery-based contact re-injection. The toolkit is available at https://github.com/anamolsapkota/dtn-tools under the MIT License.

**Keywords:** Delay-Tolerant Networking, Bundle Protocol, ION-DTN, Network Management, CLI Tools, OpenIPN, Contact Graph Routing, BPv7

---

## 1. Introduction

Delay-Tolerant Networking (DTN) provides reliable communication in environments where conventional TCP/IP protocols fail due to intermittent connectivity, long or variable propagation delays, and frequent disruptions. Originally designed for deep-space communication where round-trip times can exceed minutes and connectivity windows must be scheduled in advance, DTN's store-and-forward paradigm has proven applicable to terrestrial challenged networks: rural connectivity in developing regions, disaster response, IoT sensor data collection, wildlife tracking, and tactical military communications.

The Bundle Protocol version 7 (BPv7, RFC 9171 [1]) standardizes the format and processing of DTN data units (bundles). Several implementations exist, with NASA JPL's ION-DTN [2] being the most widely deployed in research networks and operational missions. ION provides a complete BPv7 stack with Contact Graph Routing (CGR), multiple convergence layers (UDP, TCP, LTP), security features (BPSec, RFC 9172 [4]), and a shared-memory architecture for high performance. The OpenIPN network [3], operated by the Interplanetary Networking Special Interest Group (IPNSIG), leverages ION to connect approximately 40 research nodes worldwide.

Despite this protocol-level maturity, a significant gap exists between the DTN stack and its operational usability. Setting up a single ION-DTN node requires compiling software from source, authoring multi-section configuration files spanning four admin subsystems, creating systemd service units with correct dependency chains, and manually managing time-varying contact graphs. Common operations such as adding a neighbor demand commands to three separate admin programs plus manual configuration file edits. Diagnosing routing failures requires mental BFS traversal of contact graph output from multiple admin tools.

The recently standardized DTN Management Architecture (DTNMA, RFC 9675 [7]) acknowledges this problem, noting that current deployments rely on "pre-placed keys and bespoke tooling." However, DTNMA addresses management architecture — agent/manager roles and management protocols — rather than the practical operational tooling that node operators need for daily tasks.

This paper presents dtn-tools, a unified command-line toolkit that bridges this operational gap. Our contributions are:

1. **A complete CLI toolkit** (~3,500 lines Python, 19 commands) covering the full lifecycle from node deployment through daily operation and diagnostics.
2. **Multi-source node discovery** with persistent caching and fast post-restart recovery, reducing network convergence from 30 minutes to under 10 seconds.
3. **BFS-based route diagnostics** that simulate CGR routing with per-hop verification, providing network observability previously unavailable to ION operators.
4. **Persistent terminal chat** over BPv7 bundles with per-sender conversations and conversation switching.
5. **A detailed operational experience report** documenting eight distinct challenges in terrestrial ION-DTN deployment, with root causes and solutions.

## 2. Background and Related Work

### 2.1 DTN Architecture and Bundle Protocol

The DTN architecture (RFC 4838 [5]) introduces a store-and-forward overlay network operating above the transport layer. Data units called *bundles* carry application data along with metadata (source and destination endpoint identifiers, creation timestamp, lifetime, class of service). Each node in a DTN network stores received bundles until a communication opportunity arises with the next hop, enabling data delivery across networks with no contemporaneous end-to-end path.

BPv7 (RFC 9171 [1]) standardized the bundle format using CBOR (Concise Binary Object Representation) encoding, replacing the earlier RFC 5050 [6]. Key features include: canonical block structures for extensibility, IPN-scheme endpoint identification (e.g., `ipn:268485091.5` identifies service 5 on node 268485091), administrative records for status reporting, and a modular convergence layer architecture that decouples the bundle protocol from the underlying transport.

Bundle Protocol Security (BPSec, RFC 9172 [4]) provides integrity (Block Integrity Block, BIB) and confidentiality (Block Confidentiality Block, BCB) services on a per-block basis, enabling hop-by-hop or end-to-end security depending on deployment requirements.

### 2.2 Contact Graph Routing in ION-DTN

ION-DTN uses Contact Graph Routing (CGR) [8] to compute paths through time-varying network topologies. The contact graph consists of three types of information:

- **Contacts:** Directed edges representing scheduled communication opportunities. A contact specifies: source node, destination node, start time, end time, and data rate in bytes per second. For example, "node A can transmit to node B at 100,000 bytes/sec from time T1 to T2."
- **Ranges:** Undirected edges specifying the one-way light time (propagation delay) between two nodes. In terrestrial networks, ranges are typically 1 second.
- **Plans:** Forwarding rules that map an IPN node number to a specific outduct (convergence layer adapter). A plan effectively says "to send to node B, use UDP outduct at IP:port."

CGR examines the contact graph to find time-valid paths from source to destination. The **first-hop constraint** is critical: the sending node must have a *plan* (outduct) for the first hop; subsequent hops are the responsibility of intermediate nodes. This means multi-hop routing requires each intermediate node to have appropriate contacts and plans configured.

When contacts expire (reach their end time), they are removed from the graph. This creates a maintenance burden: operators must either set very long contact durations (reducing graph accuracy) or frequently refresh contacts (increasing operational overhead). The dtnex protocol addresses this by automatically exchanging contacts between neighbors, but its exchange cycle introduces latency.

### 2.3 ION-DTN Internals

ION-DTN is implemented in C and uses a shared-memory architecture called the Simple Data Recorder (SDR) for inter-process communication and data storage. Multiple ION processes — the bundle forwarder (ipnfw), convergence layer adapters (udpcli, udpclo), and the Contact List Manager (bpclm) — communicate through SDR.

Configuration is managed through four admin programs, each with its own command syntax:

- `ionadmin`: Manages the contact graph (contacts, ranges, production/consumption rates). Commands include `a contact` (add contact), `d contact` (delete contact), `l contact` (list contacts).
- `bpadmin`: Manages Bundle Protocol configuration — protocol definitions, inducts (inbound convergence layer adapters), outducts (outbound adapters), and endpoints. Commands include `a protocol`, `a induct`, `a outduct`.
- `ipnadmin`: Manages forwarding plans that map IPN node numbers to outducts. The command `a plan <IPN> <protocol>/<IP:port>` creates a forwarding rule.
- `ionsecadmin`: Manages security policies and key material. For the OpenIPN network, a shared key (`presSharedNetworkKey=open`) is used.

All configuration is typically loaded from a single `host<IPN>.rc` file at ION startup. Changes made through admin programs at runtime affect the SDR state but are lost on restart unless the `.rc` file is also updated — a dual-write requirement that is a frequent source of configuration drift.

### 2.4 The OpenIPN Network

The OpenIPN network (openipn.org) [3], operated by IPNSIG, provides the infrastructure for global DTN research. Key components include:

- **IPN number allocation:** Researchers register and receive unique IPN numbers in the 268484608-268500991 range.
- **Gateway node (DTNGW, ipn:268485000):** A central routing node that forwards bundles between network participants.
- **Monitoring system:** Periodically pings nodes' bpecho endpoints (service 12161) to track uptime.
- **Bundle Board (ipn:268484800.6):** Collects and displays IoT sensor data bundles from participating nodes.
- **VPN overlays:** Nodes connect via Tailscale or ZeroTier to traverse NATs and firewalls.
- **Metadata and contact graph publication:** The network publishes node metadata lists and a global contact graph in Graphviz DOT format, enabling discovery tools.

At the time of writing, approximately 40 nodes from multiple countries participate, with over 1,000 registered members.

### 2.5 Existing Tools and Management Approaches

**dtnex** (Samo Grasic) is a metadata exchange protocol for ION-DTN. Nodes broadcast their metadata (name, email, GPS coordinates) and known contacts to neighbors, who update their contact graphs accordingly. Contact lifetime is configurable (default: 3600 seconds). dtnex is critical for automated contact management but introduces exchange cycle latency and has known stability issues (Section 4.2.2).

**ionwd** (Samo Grasic) is a watchdog daemon that monitors ION's health and restarts it after crashes, providing basic fault tolerance.

**uD3TN** [12] offers a Python management library (ud3tn-utils) with AAP2Client for programmatic daemon interaction — a library API approach fundamentally different from dtn-tools' CLI approach, and targeting a different DTN implementation.

**DTN7-go** [14] provides REST API, WebSocket API, and `dtnclient` CLI tool, but is not compatible with ION's ecosystem.

**HDTN** (NASA Glenn) [11] provides a web-based GUI dashboard with configuration and telemetry visualization. In 2024, HDTN demonstrated 4K UHD video streaming between a PC-12 aircraft and the ISS at 900+ Mbps using BPv7 with BPSec — a significant throughput milestone. HDTN's management interface targets its own implementation, not ION.

**DTN-COMET** (2025) [9] developed automated containerized testbeds for multi-implementation benchmarking, enabling reproducible performance comparisons. DTN-COMET addresses the *testing* gap but not the *operational management* gap.

### 2.6 DTN Management Architecture (RFC 9675)

RFC 9675 [7] (November 2024) formalizes the DTN Management Architecture (DTNMA), introducing Autonomy, Management and Control (AMC) agents that execute predefined control procedures on DTN nodes. DTNMA recognizes three key challenges: (1) communication disruptions between managers and agents, (2) the need for autonomous agent operation, and (3) current reliance on bespoke tooling. While DTNMA provides the architectural framework for future management systems, no DTNMA-compliant management tools exist at the time of writing. dtn-tools addresses the practical operational gap that DTNMA identifies.

### 2.7 Recent Operational Milestones

**NASA PACE Mission** (2024) [10] became the first Class-B NASA mission using DTN operationally, transmitting 34 million bundles with a 100% success rate from the spacecraft to ground stations. This validated DTN's reliability for mission-critical data but used mission-specific ground support tools.

These developments demonstrate DTN's protocol-level maturity while underscoring the need for accessible operational tooling — the space community has invested heavily in custom tools, but the terrestrial research community lacks comparable infrastructure.

## 3. System Design

### 3.1 Architecture Overview

dtn-tools is implemented as a Python CLI with the following module structure:

| Module | Lines | Purpose |
|--------|-------|---------|
| `dtn` (main CLI) | ~1,370 | Command dispatch, neighbor management, chat, service management, status, all user-facing commands |
| `dtn_tools/init.py` | ~936 | 9-step setup wizard |
| `dtn_tools/traceroute.py` | ~574 | BFS route tracing and diagnostics |
| `dtn_tools/discovery.py` | ~482 | Multi-source node discovery daemon |
| `dtn_tools/dtn_nodes_cli.py` | ~130 | Node listing and formatting utilities |
| `dtn_tools/__init__.py` | 3 | Package marker |
| **Total** | **~3,494** | |

The CLI wraps ION's admin programs through Python subprocess calls rather than using ION's C API. This design provides:

- **Version decoupling:** Works across ION versions without recompilation or ABI compatibility concerns.
- **No SDR coupling:** Avoids the complexity and fragility of direct shared-memory interaction.
- **Transparency:** Every ION command can be logged and replayed manually for debugging.
- **Safe co-existence:** The tool operates alongside ION processes without interference.

The main `dtn` script is installed via symlink to `/usr/local/bin/dtn`. It auto-detects the DTN working directory from `~/dtn`, `~/ion-dtn`, or the `DTN_DIR` environment variable, and follows symlinks with `os.path.realpath()` to locate the `dtn_tools/` module directory.

```
User Commands                    ION Admin Layer                ION Engine
─────────────                    ───────────────                ──────────
dtn init          ──►  ionadmin, bpadmin,         ──►  SDR (Shared Memory)
dtn neighbors add      ipnadmin, ionsecadmin           Contact Graph Router
dtn trace         ──►  bpsource, bprecvfile       ──►  UDP Convergence Layer
dtn chat               bping, bpecho                   Bundle Forwarder (ipnfw)
dtn discover      ──►  HTTP (openipn.org)              bpclm (Contact List Mgr)
dtn sensor             dtnex metadata files
```

All neighbor modifications persist to both the running ION instance (via admin program commands) and the `host<IPN>.rc` configuration file, ensuring consistency across restarts.

### 3.2 Setup Wizard (`dtn init`)

The setup wizard automates the complete node deployment process through nine idempotent steps. Each step checks whether its work has already been completed and skips if so, making the wizard safe to run multiple times and allowing it to resume after partial failures.

**Step 1: System Dependencies.** Checks for and installs build-essential, autoconf, automake, libtool, pkg-config, and other packages required to compile ION-DTN from source. Uses `dpkg -s` to check installed status, `apt-get install` to install missing packages.

**Step 2: ION-DTN Build.** Checks whether `ionadmin` is already available on PATH. If not, clones the ION-DTN repository (ione-1.1.0 branch), runs `autoreconf -fi`, `./configure`, `make`, and `make install`. On a Raspberry Pi 4, this build takes approximately 15 minutes.

**Step 3: dtnex Build.** Checks for the `dtnex` binary. If absent, clones the dtnex repository and runs the `build_standalone.sh` script followed by `make install`.

**Step 4: ionwd Watchdog.** Checks for the ionwd directory. If absent, clones the ionwd repository and patches the `ionwd.sh` script with correct paths.

**Step 5: Directory Structure.** Creates the working directory tree: `~/dtn/`, `~/dtn/dtn-discovery/`, `~/dtn/scripts/`, `~/dtn/logs/`. Idempotent via `mkdir -p`.

**Step 6: Configuration Generation.** The most complex step. If no `host<IPN>.rc` exists, the wizard generates it with all four admin sections:

- `ionadmin` section: node number, SDR configuration, production/consumption rates, initial contacts and ranges to the DTNGW gateway.
- `bpadmin` section: UDP protocol definition, induct on port 4556 (and optionally 4557 for relay configurations), outduct to the gateway, local endpoints for bpecho and chat.
- `ipnadmin` section: forwarding plan for the gateway node.
- `ionsecadmin` section: security policy using the OpenIPN shared key.

Additionally generates `dtnex.conf` (metadata exchange configuration with node name, email, GPS coordinates, and network key), `discovery.conf` (discovery daemon settings), and `ipnd.rc` (IP Neighbor Discovery beacon configuration).

**Step 7: Start ION.** Checks if ION is already running by testing `bpversion` output (not exit code — see Section 4.2.3). If not running, executes `ionstart -I host<IPN>.rc`.

**Step 8: Systemd Services.** Writes systemd unit files for four services: ionwd (watchdog, depends on network.target), dtnex (metadata exchange, depends on ionwd), bpecho (echo endpoints, depends on ionwd), and dtn-discovery (discovery daemon, depends on dtnex). Runs `systemctl daemon-reload`, `enable`, and `start` for each. Dependency chain: `network.target -> ionwd -> dtnex -> dtn-discovery`.

**Step 9: bpecho Endpoints.** Starts bpecho on two endpoints: `.2` (standard echo for bping) and `.12161` (OpenIPN monitoring endpoint). Uses a forking service type to manage both processes.

### 3.3 Neighbor Management

Adding a DTN neighbor in raw ION requires 8 separate commands across 3 admin programs:

```
ionadmin: a contact +0 +86400 <local> <remote> 100000
ionadmin: a contact +0 +86400 <remote> <local> 100000
ionadmin: a range +0 +86400 <local> <remote> 1
ionadmin: a range +0 +86400 <remote> <local> 1
bpadmin:  a outduct udp <IP>:4556 udpclo
ipnadmin: a plan <remote> udp/<IP>:4556
# Plus: edit host.rc to persist all 6 additions
# Plus: handle existing outducts/plans gracefully
```

dtn-tools reduces this to:

```bash
dtn neighbors add <IPN> <IP> [--rate 100000] [--duration 86400] [--owlt 1]
```

The command:
1. Checks for existing outducts to prevent the "Duct is already attached to a plan" error
2. Adds bidirectional contacts with the specified rate and duration
3. Adds bidirectional ranges with one-way light time
4. Adds a UDP outduct to `<IP>:4556`
5. Adds a forwarding plan mapping the IPN to the outduct
6. Persists all changes to the `host.rc` file by appending to the appropriate admin sections

Removal is equally unified: `dtn neighbors remove <IPN>` reverses all operations in both the running ION state and the configuration file, using `d contact`, `d range`, `d outduct`, and `d plan` commands.

`dtn neighbors ping [IPN]` performs both ICMP ping (IP-layer reachability) and bping (DTN-layer round-trip time) to one or all neighbors, providing layered connectivity verification.

### 3.4 Route Diagnostics (`dtn trace` and `dtn diagnose`)

The `traceroute.py` module (~574 lines) implements BFS-based route tracing that simulates ION's Contact Graph Routing:

**Algorithm:**

1. **Parse contacts:** Read all contact edges from `ionadmin` output. Build an adjacency list: for each source node, maintain a set of destination nodes reachable via contacts.
2. **Parse plans:** Read all forwarding plans from `ipnadmin` output. Extract the outduct IP:port for each planned node.
3. **BFS with first-hop constraint:** Initialize the BFS queue with only those nodes for which the local node has both a contact AND a plan (i.e., direct neighbors with outducts). This mirrors CGR's requirement that the first hop must have a local plan. Subsequent hops are discovered through the contact graph.
4. **Path reconstruction:** When the destination is reached, reconstruct the full path from source to destination using parent pointers.
5. **Per-hop verification:** For each hop in the path, verify:
   - Forward contact exists (source -> destination contact in the graph)
   - Range exists (propagation delay defined)
   - Return contact exists (destination -> source, needed for acknowledgments)
   - IP reachable (ICMP ping to the outduct address, for direct neighbors)
   - Optionally: DTN reachable (bping for DTN-level round-trip time)

**Output format:**

```
DTN Route Trace: ipn:268485091 -> ipn:268485002
======================================================================
  Route: 2 hop(s)
  Path:  ipn:268485091 -> ipn:268485000 (DTNGW) -> ipn:268485002

  [OK] Hop 1: ipn:268485091 -> ipn:268485000 (DTNGW)
       via 100.96.108.37:4556   icmp=45ms   [plan]
       Contact: yes | Range: yes | Return: yes

  [OK] Hop 2: ipn:268485000 -> ipn:268485002
       Contact: yes | Range: yes | Return: yes
```

The `dtn diagnose` command extends route tracing to all known nodes, producing a comprehensive report: service status, contact/plan/node counts, per-neighbor verification for direct neighbors, and multi-hop route status for remote nodes. This provides a complete snapshot of the node's view of the network.

### 3.5 Node Discovery

The discovery daemon (`discovery.py`, ~482 lines) aggregates node information from four sources:

1. **OpenIPN metadata list:** HTTP fetch of the global `metadata_list.txt` from openipn.org, containing node names, email addresses, and GPS coordinates for all metadata-exchanging nodes.
2. **OpenIPN contact graph:** HTTP fetch of the global `contactGraph.gv` in Graphviz DOT format, containing all inter-node contact edges. Parsed to extract node adjacencies and identify gateway-routable nodes.
3. **Local dtnex metadata:** Reads `nodesmetadata.txt` written by the local dtnex instance, containing metadata from nodes that have exchanged directly with this node's neighbors.
4. **ION contact graph:** Reads the local contact and plan state from ionadmin/ipnadmin, identifying nodes already configured locally.

Discovered nodes are classified by reachability:
- **Direct:** Node has a local forwarding plan (outduct) — bundles can be sent immediately.
- **Gateway-routed:** Node appears in the global contact graph and is reachable through the DTNGW gateway.
- **Unknown:** Node is known by name but no route exists.

The persistent database (`discovered_nodes.json`) stores all discovered nodes with their metadata, last-seen timestamp, reachability classification, and outduct addresses. This database survives ION restarts, node reboots, and dtnex exchange cycles.

**Fast recovery mechanism:** ION's shared memory (SDR) is volatile — after `ionstop` / `ionstart` or a system reboot, all contacts, ranges, and plans are lost unless the host.rc file is reloaded. The dtnex exchange cycle can take up to 30 minutes to fully restore the contact graph. The discovery daemon addresses this by detecting sparse contact state on startup (few contacts relative to the cached database) and re-injecting cached nodes:
- Nodes with stored outduct addresses are re-added as direct neighbors (contact + range + outduct + plan).
- Gateway-routable nodes are re-added with contacts and ranges through the gateway.
- A configurable staleness threshold (default: 7 days) prevents re-injection of outdated information.
- Nodes not seen in 30 days are pruned from the database entirely.

This reduces post-restart convergence from up to 30 minutes to under 10 seconds.

### 3.6 Terminal Chat

dtn-tools provides an interactive terminal chat application over BPv7 bundles using **service number 5** (`ipn:<node>.5`). The chat system is implemented within the main `dtn` CLI (~400 lines in the `cmd_chat` function and supporting code).

**Features:**

- **Persistent per-sender conversations:** Messages are stored in `chat-history.json`, a JSON file grouped by remote node IPN. History survives chat session restarts and node reboots.
- **Unread indicators:** Messages arriving from non-active senders are stored as unread. A one-line notification appears: `[New message from <name>]`. Switching to a conversation marks all its messages as read.
- **Conversation switching:** The `/to <name|#|IPN>` command switches the active conversation and displays recent history. The `/list` command shows all conversations with unread message counts.
- **No IP addresses needed:** Users specify IPN numbers or node names; CGR routes bundles automatically through the contact graph.
- **Node selection on entry:** On startup, the chat presents a numbered list of all nodes from the contact graph (with names from the discovery database), marking direct neighbors with `*`.

**Message format:** Chat messages are JSON-encoded bundles transmitted via `bpsource`:

```json
{
  "from": "268485091",
  "name": "pi05",
  "msg": "Hello from terminal!",
  "ts": "14:32:10"
}
```

**Sending:** The user types a message, the chat function constructs the JSON payload, and invokes `bpsource ipn:<dest>.5 '<JSON>'` to transmit the bundle. ION's CGR computes the route and forwards the bundle through the contact graph, potentially traversing multiple hops.

**Receiving:** A background receiver thread starts `bprecvfile ipn:<local>.5`, which writes received bundles as files in a temporary directory (`/tmp/dtn-chat-*/`). The receiver thread polls this directory every 500ms, reads each file, parses the JSON payload, routes the message to the correct per-sender conversation, displays it if the sender is active (or stores as unread), and deletes the file.

### 3.7 Service Management

dtn-tools manages six services with a **dual-mode execution strategy**:

| Service | Purpose | Depends On |
|---------|---------|------------|
| ionwd | ION watchdog — monitors health, restarts on crash | network.target |
| dtnex | Metadata exchange with neighbor nodes | ionwd |
| bpecho | Echo service on endpoints .2 and .12161 | ionwd |
| dtn-chat | Web-based chat application (service 7) | ionwd |
| dtn-discovery | Discovery daemon (4-source aggregation) | dtnex |
| dtn-metadata-updater | Periodic metadata refresh | dtnex |

Commands `dtn start/stop/restart [service]` and `dtn enable/disable [service]` support both targeted (single service) and global (all services) operation.

**Dual-mode execution:** If systemd service files exist, commands use `systemctl` for process management. If systemd units are not configured (common during initial setup or on non-systemd systems), the tool falls back to direct process management via `nohup` (for starting) and `pkill` (for stopping). This ensures the toolkit works even before the setup wizard has created service files.

### 3.8 IoT Sensor Integration

The `dtn sensor` command wraps the `bpbme280` tool to send BME280/BMP280 environmental sensor data (temperature, pressure, humidity) as DTN bundles to the IPNSIG Bundle Board (`ipn:268484800.6`). This enables IoT data collection over DTN with a single command. The command supports cron-based periodic reporting (e.g., `*/5 * * * * dtn sensor`) for continuous environmental monitoring.

## 4. Deployment Experience

### 4.1 Testbed Configuration

We deployed dtn-tools on two nodes with complementary hardware profiles and network configurations:

**Pi05 (ipn:268485091):** A Raspberry Pi 4 (4GB RAM, ARM64) running Raspberry Pi OS, located in Kathmandu, Nepal. Connected to the DTNGW gateway via Tailscale VPN (port 4556) and to Echo via ZeroTier (port 4557). Serves as both an endpoint and a relay node for Echo's gateway traffic. Equipped with a BMP280 environmental sensor for Bundle Board data submission.

**Echo (ipn:268485111):** An x86_64 server running Ubuntu 22.04, located in Dhulikhel, Nepal (approximately 30 km east of Kathmandu). Connected to Pi05 via ZeroTier local network but has no direct Tailscale connectivity to the DTNGW gateway. All gateway traffic must be relayed through Pi05.

**Network topology:** Echo -> Pi05 (ZeroTier, port 4557) -> DTNGW (Tailscale, port 4556) -> OpenIPN network (~40 nodes). Pi05 requires dual UDP inducts: port 4556 for Tailscale traffic from the gateway and port 4557 for ZeroTier traffic from Echo.

### 4.2 Problems Encountered and Solutions

Over the course of deployment and operation, we encountered eight distinct operational challenges. We document each with its symptoms, root cause analysis, and solution, as these findings are directly useful to the DTN research community.

#### 4.2.1 Contact Graph Stale State After Add/Remove Cycles

**Symptoms:** After multiple cycles of adding and removing a neighbor (`dtn neighbors add` / `dtn neighbors remove`), ION refused to re-add the neighbor with the error: "Duct is already attached to a plan." The outduct appeared to be gone (not shown in `bpadmin l outduct`) but an internal reference persisted.

**Root cause:** ION's SDR maintains internal references between outducts, plans, and the Bundle Protocol Contact List Manager (bpclm). Deleting a plan does not fully clean up all internal references, leaving a "ghost" association that prevents re-attachment.

**Solution:** A full ION restart (`ionstop`, `killm`, `ionstart -I host.rc`) clears the SDR state. For prevention, the neighbor management code in dtn-tools checks for existing outducts before adding new ones and warns the operator if a restart may be needed. The `dtn restart` command handles this sequence cleanly, including re-injection of cached contacts via the discovery daemon.

#### 4.2.2 dtnex Buffer Overflow Crash on x86_64

**Symptoms:** The dtnex binary on the Echo node crashed immediately on startup with "stack smashing detected" in the CBOR encoder. The ionwd watchdog restarted dtnex repeatedly, creating a restart loop — 113+ restarts were observed before diagnosis.

**Root cause:** The node metadata string configured in `dtnex.conf` exceeded a fixed-size buffer in dtnex's CBOR encoder. The original metadata included system metrics (uptime, available memory, disk usage, CPU load average) concatenated into a single string that overflowed the buffer. This issue manifested only on x86_64; the ARM64 Pi05 node had shorter metadata strings that fit within the buffer.

**Solution:** Shortened the metadata string to contain only essential fields: node name, operator email, and geographic location. After this fix, dtnex ran stably on Echo and successfully exchanged metadata with 2 direct neighbors, refreshing 86 contacts in the process. This bug highlights the importance of defensive input validation in DTN auxiliary tools.

#### 4.2.3 bpversion Returns Exit Code 7 on Success

**Symptoms:** The `dtn status` command reported "ION not running" even when ION was operating normally. The internal check `bpversion && echo "running"` failed because `bpversion` returned exit code 7 despite successfully printing "bpv7" to stdout.

**Root cause:** The `bpversion` utility returns the BP version number (7) as its process exit code rather than the conventional 0 for success. This is technically correct (it reports the version) but violates POSIX exit code conventions and breaks standard shell constructs like `cmd || fallback`.

**Solution:** Changed the ION detection logic to check for output content rather than exit code: if `bpversion` produces any output, ION is installed and responding. This fix was committed as a dedicated patch (`fix: dtn status false negative when bpversion returns rc=7`).

#### 4.2.4 Relay Routing Through Intermediate Node

**Symptoms:** Echo could communicate with Pi05 (direct ZeroTier neighbor) but could not reach the DTNGW gateway or any other OpenIPN node. Bundles to the gateway were dropped silently.

**Root cause:** Echo had no direct Tailscale connectivity to the gateway. The forwarding plan needed to route gateway-bound traffic through Pi05 as an intermediate relay, but this required specific configuration on both sides.

**Solution:** Two configuration changes were required:

On Echo: The gateway plan was configured to point to Pi05's ZeroTier IP on a non-standard port:
```
ipnadmin: a plan 268485000 udp/10.16.16.169:4557
```

On Pi05: An additional UDP induct was added on port 4557 to receive Echo's relayed traffic:
```
bpadmin: a induct udp 0.0.0.0:4557 udpcli
```

ION's CGR on Pi05 then automatically forwards the gateway-bound bundle via the Tailscale outduct on port 4556. This relay pattern — using different ports for different network overlays — is documented in dtn-tools' configuration generator for reuse.

#### 4.2.5 bpecho Wrong Endpoint

**Symptoms:** Echo showed as DOWN on the OpenIPN monitoring dashboard despite being fully operational. Local bping tests succeeded.

**Root cause:** Echo's bpecho service was configured to listen on endpoint `.1` (general purpose) instead of `.2` (standard echo) and `.12161` (OpenIPN monitoring). The OpenIPN monitor sends bundles to `.12161`; with no listener on that endpoint, Echo appeared offline.

**Solution:** Fixed the systemd service file to start bpecho on both required endpoints:
```
ExecStart=/bin/bash -c 'bpecho ipn:268485111.2 & bpecho ipn:268485111.12161'
Type=forking
```
After this fix, Echo appeared as UP on the OpenIPN dashboard. This issue illustrates the importance of endpoint convention documentation in DTN networks.

#### 4.2.6 Contact Expiration After ION Restart

**Symptoms:** After an ION restart (whether manual via `dtn restart`, watchdog-triggered, or system reboot), the node had zero contacts and zero plans. No bundles could be sent or received. The node remained in this state for up to 30 minutes until the next dtnex exchange cycle restored contacts.

**Root cause:** ION's contact graph lives in volatile shared memory (SDR). The host.rc file loaded at startup contains only the initial configuration (typically just the gateway contact). All contacts added by dtnex during operation are stored only in SDR and lost on restart. The dtnex exchange cycle runs periodically (configurable, typically every 10-30 minutes), so the next full contact restore can take up to one cycle duration.

**Solution:** Implemented the discovery-based fast recovery mechanism (Section 3.5). On startup, the discovery daemon detects the sparse contact graph (few contacts relative to the cached database in `discovered_nodes.json`) and immediately re-injects cached nodes. Direct neighbors with known outduct addresses are re-added with full contact/range/plan configurations. Gateway-routable nodes are re-added with contacts through the gateway. This reduces recovery from up to 30 minutes to under 10 seconds.

#### 4.2.7 bpclm Not Starting After Plan Re-Add

**Symptoms:** After removing and re-adding a neighbor's plan, bundles to that neighbor were queued but never transmitted. The Bundle Protocol Contact List Manager (bpclm) process for the re-added plan did not start, even though the plan appeared correctly in `ipnadmin l plan`.

**Root cause:** When a plan is deleted via `ipnadmin d plan`, ION terminates the associated bpclm process. When a new plan is added, ION should start a new bpclm, but under certain conditions (particularly after rapid add/remove cycles), the bpclm process is not spawned. This appears to be an ION-internal state management issue.

**Solution:** A full ION restart (`dtn restart`) clears all SDR state and properly initializes bpclm processes for all configured plans. Combined with discovery-based fast recovery, the restart penalty is minimal (under 10 seconds to restore all contacts). We reported this behavior to the ION development community.

#### 4.2.8 dtnex Semaphore Error After killm

**Symptoms:** After using `killm` (ION's process cleanup command) to forcefully terminate ION, dtnex failed to start with a semaphore-related error. The watchdog (ionwd) could not recover dtnex.

**Root cause:** `killm` removes ION's shared memory segments and semaphores, but dtnex maintains its own semaphore state. When ION's semaphores are destroyed while dtnex holds references to them, dtnex enters an unrecoverable state.

**Solution:** The `dtn restart` command sequences the shutdown properly: stop all dtn-tools services first (discovery, dtnex, bpecho), then stop ION (`ionstop`), then clean up (`killm`), then restart in the correct order. This prevents the semaphore conflict. When the error does occur (e.g., after manual killm), restarting the dtnex service after ION is fully running resolves the issue.

### 4.3 Operational Results

After deploying dtn-tools and resolving the issues documented above, the testbed achieved stable operation:

- **Pi05 uptime:** 91% (24-hour window) and 83% (7-day window) as measured by the OpenIPN monitoring system, with 0% bundle loss and 340ms round-trip time to the DTNGW gateway.
- **Echo recovery:** Transitioned from DOWN (0% uptime due to the dtnex CBOR crash loop) to operational status on the OpenIPN dashboard after the metadata string fix and bpecho endpoint correction.
- **Chat messaging:** Both nodes successfully exchanged chat messages over DTN bundles, with messages traversing the Pi05-DTNGW-destination path for remote nodes. Persistent history survived multiple chat session restarts and node reboots.
- **Sensor data:** Environmental data from Pi05's BMP280 sensor appeared correctly on the IPNSIG Bundle Board after transmission via `dtn sensor`.
- **Network view:** From Pi05, the discovery system identified 28 known nodes, of which 20 were routable (direct or via gateway), with 11 direct neighbors.

## 5. Evaluation

### 5.1 Setup Complexity Reduction

Table 1 compares the operational complexity of common DTN tasks with and without dtn-tools:

| Task | Without dtn-tools | With dtn-tools | Reduction |
|------|-------------------|----------------|-----------|
| Full node deployment | ~2 hours, 50+ manual steps (compile ION, dtnex, ionwd; write host.rc with 4 sections; create 4 systemd units; start services; configure bpecho) | `dtn init` — 1 interactive command, ~15 min (dominated by compilation time) | 50+ steps -> 1 command |
| Add a neighbor | 8 commands across ionadmin, bpadmin, ipnadmin + manual host.rc edit | `dtn neighbors add <IPN> <IP>` — 1 command | 8 -> 1 (8x reduction) |
| Remove a neighbor | 6 commands across admin programs + manual host.rc edit | `dtn neighbors remove <IPN>` — 1 command | 6 -> 1 |
| Check node health | `ionadmin l contact`, `ipnadmin l plan`, `bpadmin l outduct`, manual interpretation | `dtn status` — 1 command with formatted output | 3+ -> 1 |
| Trace route to node | Manual: parse `ionadmin l contact`, mentally perform BFS, check plans at each hop | `dtn trace <IPN>` — 1 command with per-hop verification | Mental BFS -> automated |
| Full network diagnostics | Repeat trace for all nodes, manually check each | `dtn diagnose` — 1 command | N * trace -> 1 command |
| Send a message | Construct `bpsource` command with full endpoint syntax | `dtn chat` — interactive UI with node selection | Expert syntax -> interactive |
| View network topology | Parse `ionadmin l contact` output manually | `dtn nodes` — formatted table with names | Raw output -> formatted table |

### 5.2 Network Recovery Time

Table 2 compares post-restart convergence times:

| Scenario | Without dtn-tools | With dtn-tools (discovery caching) |
|----------|-------------------|-----------------------------------|
| ION restart, all contacts lost | Wait for dtnex exchange cycle: 10-30 minutes depending on configuration and neighbor availability | Discovery daemon detects sparse graph, re-injects from cache: <10 seconds |
| Node reboot (full system restart) | Full convergence: 5-30 minutes (systemd starts ION, then dtnex must exchange with all neighbors) | ionwd starts ION, discovery re-injects cached contacts: <10 seconds for cached nodes; full convergence via dtnex continues in background |
| dtnex crash loop (Section 4.2.2) | Indefinite downtime until manual intervention | Discovery cache maintains routes from last good state; `dtn diagnose` identifies the crash |

### 5.3 Diagnostic Accuracy

The `dtn trace` command was validated against manual contact graph inspection and real bundle delivery tests. It correctly identified:

- **Missing contacts:** After contact expiration, trace correctly reported "Contact: no" for the affected hop, matching the actual delivery failure.
- **Unreachable neighbors:** 100% correlation between ICMP ping failure in trace output and actual bundle non-delivery.
- **Missing return contacts:** Identified asymmetric contact configurations where forward contacts existed but return contacts did not, explaining acknowledgment failures.
- **Multi-hop paths:** Correctly traced 2-hop and 3-hop paths through the gateway, matching the actual bundle forwarding behavior confirmed via ION log inspection.
- **Stale plans:** Detected cases where plans existed but associated bpclm processes were not running (Section 4.2.7).

### 5.4 Discovery Effectiveness

The multi-source discovery system identified 28 unique nodes from the following sources (with overlap):

| Source | Nodes Found | Unique Contribution |
|--------|-------------|-------------------|
| OpenIPN metadata | 24 | Node names, emails, GPS coordinates |
| OpenIPN contact graph | 31 | Contact edges, gateway routing information |
| Local dtnex | 11 | Direct neighbor metadata, real-time contacts |
| ION contacts | 11 | Already-configured local contacts |
| **Combined (deduplicated)** | **28 routable** | Persistent database with fast recovery |

The aggregation approach provides richer node information than any single source: OpenIPN provides names and coordinates, dtnex provides real-time contact state, and ION provides local configuration. The persistent database enables fast recovery, which would be impossible with any single source alone.

## 6. Limitations

1. **ION-DTN coupling:** dtn-tools is tightly coupled to ION's admin program interface and command syntax. Supporting uD3TN, DTN7, or HDTN would require implementing backend adapters for each implementation's management interface.

2. **UDP convergence layer only:** The current implementation manages UDP outducts (udpclo) and inducts (udpcli). TCP (TCPCL, RFC 9174) and LTP convergence layers are not yet supported. Adding TCPCL support would require additional outduct/induct management logic.

3. **No end-to-end encryption:** Chat messages are transmitted as plaintext JSON bundles. While ION supports BPSec (RFC 9172) for integrity and confidentiality, dtn-tools does not configure or leverage these security features. The OpenIPN network uses a shared key ("open") providing minimal security.

4. **Single-user chat model:** The terminal chat supports one-to-one conversations only. Group chat rooms or broadcast messaging would require a multicast or publish/subscribe extension.

5. **Linux-only deployment:** Tested on Raspberry Pi OS, Ubuntu 22.04, and Debian. macOS and Windows are not supported, primarily due to ION's build system assumptions and systemd dependency.

6. **Admin program overhead:** Using subprocess calls to ION admin programs introduces per-command overhead (process spawn, pipe I/O) compared to direct C API integration. However, for management operations (which are infrequent relative to data plane operations), this overhead is negligible — typically under 100ms per command.

7. **Contact graph scale:** The BFS-based route tracing examines all contacts in the graph. For the current OpenIPN network (~40 nodes, ~200 contact edges), this completes in under 1 second. For networks with thousands of nodes, the algorithm would need optimization (priority queues, pruning) or replacement with ION's native CGR computation.

8. **Security model:** The OpenIPN network's shared key model means all nodes trust each other. dtn-tools inherits this trust model and does not implement additional authentication, authorization, or access control. This is appropriate for a research testbed but insufficient for production deployments.

## 7. Future Work

1. **Web-based monitoring dashboard:** A browser-based interface for real-time node status visualization, contact graph rendering, chat, and historical trend analysis. Could leverage the existing CLI commands as a backend API.

2. **TCP convergence layer support:** Extend neighbor management and service configuration to support TCPCL (RFC 9174) in addition to UDP, enabling reliable transport for high-throughput links.

3. **Reliable file transfer:** A `dtn send-file` command for chunked, resumable file transfer over DTN bundles, with integrity verification and progress tracking.

4. **Group messaging:** Multi-party chat rooms over DTN, potentially using endpoint multiplexing or a publish/subscribe model.

5. **BPSec integration:** Automated BPSec configuration management, including key generation, policy configuration, and encrypted chat messaging.

6. **Multi-implementation support:** An adapter pattern enabling dtn-tools to manage uD3TN, DTN7, and HDTN backends through a common interface, promoting implementation-agnostic DTN operations.

7. **Automated testing:** A test framework using ION's loopback mode for CI/CD, enabling regression testing of CLI commands and configuration generation without a live network.

8. **Containerized deployment:** Docker images for DTN nodes, enabling rapid testbed deployment and reproducible experiments.

9. **DTNMA alignment:** As RFC 9675 DTNMA implementations emerge, align dtn-tools' management capabilities with the DTNMA agent/manager model to ensure interoperability with future standardized management tools.

## 8. Conclusion

dtn-tools demonstrates that significant improvements in DTN operational usability are achievable through thoughtful CLI design. By wrapping ION-DTN's multi-subsystem administration interface into 19 intuitive commands, the toolkit reduces the prerequisite knowledge for DTN node operation from ION expert to general Linux user. The 9-step idempotent setup wizard transforms a multi-hour manual process into a single command. Multi-source node discovery with persistent caching reduces post-restart convergence from 30 minutes to under 10 seconds. BFS-based route diagnostics provide network observability that was previously unavailable to ION operators.

Our deployment on a real two-node testbed connected to the 40-node OpenIPN global network validates the approach and reveals eight distinct operational challenges in terrestrial ION-DTN deployments. These challenges — from contact graph stale state to CBOR buffer overflows to exit code anomalies — represent practical knowledge that the DTN research community can directly benefit from.

The toolkit's contributions — automated setup, unified neighbor management, multi-source discovery with fast recovery, BFS-based route diagnostics, persistent terminal chat, and comprehensive service management — collectively address the operational gap that has historically limited DTN adoption beyond the space networking community. We hope dtn-tools lowers the barrier to participation in DTN research and accelerates application development on the OpenIPN network.

## 9. Availability

dtn-tools is open source under the MIT License:
- **Repository:** https://github.com/anamolsapkota/dtn-tools
- **OpenIPN registration:** https://openipn.org
- **Documentation:** https://github.com/anamolsapkota/dtn-tools/blob/main/docs/ARCHITECTURE.md

## References

[1] S. Burleigh, K. Fall, and E. Birrane, "Bundle Protocol Version 7," RFC 9171, Internet Engineering Task Force, January 2022. https://doi.org/10.17487/RFC9171

[2] S. Burleigh, "Interplanetary Overlay Network (ION) Design and Operation, v4.1," Jet Propulsion Laboratory, California Institute of Technology, 2020.

[3] S. Grasic, "OpenIPN: Building a Global DTN Research Network," IPNSIG Technical Report, 2023. https://openipn.org

[4] E. Birrane, A. Mayer, and J. Miner, "Bundle Protocol Security (BPSec)," RFC 9172, Internet Engineering Task Force, January 2022. https://doi.org/10.17487/RFC9172

[5] V. Cerf et al., "Delay-Tolerant Networking Architecture," RFC 4838, Internet Engineering Task Force, April 2007. https://doi.org/10.17487/RFC4838

[6] K. Scott and S. Burleigh, "Bundle Protocol Specification," RFC 5050, Internet Engineering Task Force, November 2007. https://doi.org/10.17487/RFC5050

[7] E. Birrane and S. Heiner, "Delay-Tolerant Networking Management Architecture (DTNMA)," RFC 9675, Internet Engineering Task Force, November 2024. https://doi.org/10.17487/RFC9675

[8] S. Burleigh, "Contact Graph Routing," Internet-Draft, Internet Engineering Task Force, 2010.

[9] B. Nothlich et al., "DTN-COMET: Automated Containerized Testbeds for Multi-Implementation Benchmarking," Technical Report, January 2025.

[10] NASA Goddard Space Flight Center, "PACE Mission DTN Operations Report," NASA Technical Reports Server, 2024.

[11] NASA Glenn Research Center, "HDTN 4K UHD Video Streaming over BPv7 between PC-12 Aircraft and ISS," NASA Technical Reports Server, 2024.

[12] M. Feldmann and F. Walter, "uD3TN: A Lightweight DTN Protocol Implementation for Microcontrollers," Proceedings of the International Conference on Networked Systems (NetSys), 2021.

[13] S. Grasic and E. Lindgren, "An Analysis of Evaluation Practices for Delay-Tolerant Networking Routing Protocols," IEEE Communications Surveys and Tutorials, vol. 17, no. 1, 2015.

[14] D. Batz et al., "DTN7: A Flexible Delay-Tolerant Networking System in Go," Proceedings of the International Conference on Information and Communications Technologies in Disaster Management (ICT-DM), 2019.

[15] H. Kruse et al., "Datagram Convergence Layers for the Delay- and Disruption-Tolerant Networking (DTN) Bundle Protocol and Licklider Transmission Protocol (LTP)," RFC 7122, Internet Engineering Task Force, March 2014. https://doi.org/10.17487/RFC7122

[16] T. Johnson, "DTN IP Neighbor Discovery (IPND)," Internet-Draft, Internet Engineering Task Force, 2019.

[17] IETF DTN Working Group, "Bundle Protocol Version 7 Administrative Record Types Registry," RFC 9713, Internet Engineering Task Force, January 2025. https://doi.org/10.17487/RFC9713
