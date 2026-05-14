# Research Proposal: dtn-tools — A Unified Command-Line Toolkit for Simplified ION-DTN Node Deployment, Management, and Monitoring in Terrestrial Research Networks

## 1. Title

**dtn-tools: Bridging the Operational Gap in Delay-Tolerant Network Management for Terrestrial IoT and Research Networks**

## 2. Principal Investigator

**Anamol Sapkota**
Independent Researcher
Kathmandu, Nepal
Contact: anamolsapkota [at] gmail.com

## 3. Abstract

Delay-Tolerant Networking (DTN) has matured from a deep-space communication concept into a protocol suite with demonstrated operational success: NASA's PACE mission delivered 34 million bundles with 100% reliability, and HDTN streamed 4K UHD video at 900+ Mbps over BPv7. Yet despite this protocol-level maturity, deploying and operating DTN nodes remains prohibitively complex. The Bundle Protocol version 7 (BPv7, RFC 9171) reference implementation — NASA JPL's ION-DTN — requires operators to manually compile software from source, author multi-section configuration files spanning four admin subsystems (ionadmin, bpadmin, ipnadmin, ionsecadmin), manage systemd service units, maintain time-varying contact graphs, and diagnose routing failures through cryptic admin program output. No unified management interface exists, and the recently standardized DTN Management Architecture (DTNMA, RFC 9675) acknowledges that current deployments rely on "pre-placed keys and bespoke tooling."

This research presents **dtn-tools**, an open-source Python command-line toolkit (~3,500 lines across 6 modules, 19 CLI commands) that reduces DTN node setup from a multi-hour expert process to a single idempotent command. The toolkit unifies node initialization, neighbor management with persistent configuration, multi-source node discovery, BFS-based route diagnostics, persistent terminal chat with per-sender conversations, IoT sensor integration, and service lifecycle management. We describe the design and implementation of dtn-tools, report operational experience from a two-node testbed (Raspberry Pi 4 in Kathmandu and x86_64 server in Dhulikhel, Nepal) connected to the 40-node OpenIPN global research network, and document eight distinct operational challenges encountered — including contact graph stale state, dtnex CBOR buffer overflow crashes, ION exit code anomalies, and multi-hop relay routing — along with their solutions. We evaluate the toolkit's impact on setup complexity (50+ manual steps reduced to 1 command), operational overhead (8 admin commands per neighbor reduced to 1), and network recovery time (30-minute convergence reduced to under 10 seconds with discovery caching).

**Keywords:** Delay-Tolerant Networking, Bundle Protocol, ION-DTN, Network Management, CLI Tools, OpenIPN, Contact Graph Routing, BPv7

## 4. Introduction and Background

### 4.1 Delay-Tolerant Networking

Delay-Tolerant Networking emerged from the recognition that the Internet's end-to-end TCP/IP model fails in environments characterized by intermittent connectivity, long or variable propagation delays, asymmetric data rates, and high error rates. The DTN architecture (RFC 4838) introduces a store-and-forward overlay network where data units called *bundles* are relayed through a series of custodial nodes, each storing the bundle until a communication opportunity arises with the next hop.

The Bundle Protocol version 7 (BPv7), standardized as RFC 9171 in January 2022, defines the format and processing of bundles, including block structures, administrative records, and endpoint identification using the IPN (Interplanetary Network) naming scheme. Companion standards include Bundle Protocol Security (BPSec, RFC 9172) for integrity and confidentiality, and the recently registered administrative record types (RFC 9713). Contact Graph Routing (CGR) enables path computation through time-varying network topologies, where each *contact* represents a scheduled communication opportunity between two nodes with a specified data rate and time window. A *range* specifies the one-way light time between nodes, enabling delay-aware routing decisions.

Several BPv7 implementations exist. NASA JPL's ION-DTN is the reference implementation, written in C with a shared-memory architecture (Simple Data Recorder, SDR) for inter-process communication. uD3TN (D3TN GmbH) provides a lightweight implementation targeting microcontrollers and embedded systems. DTN7-go implements BPv7 in Go with a REST API. HDTN (NASA Glenn Research Center) targets high-rate applications and demonstrated 4K UHD video streaming at 900+ Mbps in 2024. Each implementation has different management interfaces, but none provide a unified operational toolkit.

### 4.2 Terrestrial DTN Applications

While DTN was originally designed for interplanetary communication where round-trip times can exceed minutes and link availability is scheduled, its principles apply broadly to terrestrial challenged networks:

- **Rural connectivity in developing regions:** In areas like rural Nepal, where Internet connectivity is intermittent and infrastructure unreliable, DTN can provide asynchronous communication and data relay through mobile nodes or scheduled connections.
- **Disaster response:** When infrastructure is damaged, DTN enables communication between first responders using intermittent radio links, with bundles stored at relay points until connectivity is restored.
- **IoT sensor networks:** Environmental monitoring sensors in remote locations can transmit data bundles opportunistically, leveraging DTN's store-and-forward mechanism to bridge connectivity gaps.
- **Tactical military networks:** Mobile units in contested environments face frequent disruptions; DTN provides resilient data delivery where TCP connections cannot be maintained.
- **Wildlife tracking and environmental monitoring:** GPS-collared animals or remote weather stations can relay data through DTN networks using scheduled satellite passes or ranger patrol encounters.

The OpenIPN network (openipn.org), operated by the Interplanetary Networking Special Interest Group (IPNSIG), provides the infrastructure for terrestrial DTN research. With approximately 40 nodes across multiple countries and over 1,000 registered members, OpenIPN offers IPN number allocation, a gateway node (DTNGW, ipn:268485000) for inter-node routing, a monitoring system that pings node bpecho endpoints, and a Bundle Board that collects and displays IoT sensor data. Nodes connect via VPN overlays (Tailscale, ZeroTier) to traverse NATs and firewalls.

### 4.3 The Management Gap

Despite mature protocol implementations and growing research networks, a significant and well-recognized gap exists in **operational tooling** for DTN. Setting up a single ION-DTN node requires:

1. **Compilation from source:** ION-DTN uses an autoconf/automake build system requiring build-essential, autoconf, automake, libtool, and other development packages. The build process takes 15-30 minutes on a Raspberry Pi 4.
2. **Auxiliary tool compilation:** Additional tools — dtnex (metadata exchange protocol by Samo Grasic), ionwd (watchdog daemon), and bpbme280 (sensor data tool) — must be separately cloned and compiled.
3. **Multi-section configuration authoring:** A single `host.rc` configuration file contains four sections for four distinct admin programs, each with its own command syntax:
   - `ionadmin`: contacts (time-varying edges), ranges (one-way light times), production/consumption rates
   - `bpadmin`: protocol definitions, inducts (inbound convergence layer adapters), outducts (outbound adapters), endpoints
   - `ipnadmin`: forwarding plans mapping IPN node numbers to outducts
   - `ionsecadmin`: security policies and keys
4. **Systemd service creation:** Writing unit files for ionwd, dtnex, bpecho, and discovery daemons with correct dependency chains, user permissions, and restart policies.
5. **Manual contact graph management:** Adding a single neighbor requires issuing commands to three separate admin programs (8 total commands) and editing the persistent configuration file to survive restarts.
6. **Routing diagnosis through raw admin output:** Understanding why a bundle cannot reach its destination requires manual inspection of contacts, ranges, plans, and outducts across multiple admin programs, followed by mental BFS traversal of the contact graph.

RFC 9675 (November 2024) formalized the DTN Management Architecture (DTNMA), explicitly recognizing that current deployments depend on "pre-placed keys and bespoke tooling" and proposing a protocol-level management framework. However, DTNMA addresses management *architecture* — agent/manager roles, information models, and management protocols — rather than providing the practical *operational tooling* that node operators need daily. The gap between management architecture standards and hands-on deployment tools remains wide.

This management complexity directly impedes DTN adoption. Researchers who wish to experiment with DTN must invest substantial time learning ION internals before they can conduct their actual research. Students in networking courses cannot set up DTN nodes within a single lab session. Network operators accustomed to tools like `ip`, `nmcli`, or `docker` find ION's interface alien and unforgiving.

### 4.4 Research Objectives

This research aims to:

1. **Design and implement** a unified CLI toolkit that abstracts ION-DTN's multi-subsystem complexity into intuitive, composable commands — reducing the prerequisite knowledge from ION expert to general Linux user.
2. **Develop automated node discovery** that aggregates multiple information sources (OpenIPN metadata, contact graphs, dtnex protocol, ION state) into a persistent node database with fast recovery after restarts.
3. **Implement BFS-based route diagnostics** that simulate Contact Graph Routing to trace multi-hop bundle paths and identify failures at each hop (missing contacts, ranges, plans, or IP reachability).
4. **Create persistent terminal-based DTN messaging** with per-sender conversation history, unread indicators, and conversation switching — demonstrating an application-layer use case over BPv7.
5. **Deploy and evaluate the toolkit** on a real multi-node testbed connected to the OpenIPN global network, documenting all operational challenges encountered and their solutions.
6. **Quantify the complexity reduction** through before/after comparisons of setup time, command count, and recovery time.

### 4.5 Research Questions

- **RQ1:** To what extent can a CLI abstraction layer reduce the operational complexity of ION-DTN node deployment and management?
- **RQ2:** What are the dominant operational challenges in terrestrial ION-DTN networks, and how can automated tooling address them?
- **RQ3:** Can multi-source node discovery with persistent caching meaningfully reduce network convergence time after node restarts?
- **RQ4:** What is the minimum viable toolset that enables non-expert users to participate in a global DTN research network?

## 5. Methodology

### 5.1 System Design

dtn-tools is implemented as a Python CLI (~3,500 lines across 6 modules) that wraps ION-DTN's admin programs through subprocess calls rather than using ION's C API directly. This design choice ensures:
- **Version compatibility:** Works across ION versions without recompilation
- **No shared memory coupling:** Avoids the complexity of ION's SDR API
- **Operational transparency:** All ION commands can be inspected and replayed manually
- **Co-existence:** The tool operates alongside ION without interference

The toolkit provides 19 CLI commands organized into functional groups:

**Node Lifecycle:**
- `dtn init` — 9-step idempotent setup wizard (system dependencies, ION build, dtnex build, ionwd setup, directory creation, configuration generation, ION start, systemd services, bpecho endpoints)
- `dtn start/stop/restart` — Service management with systemd primary and direct process fallback
- `dtn enable/disable` — Boot persistence management
- `dtn config` — Configuration display

**Network Management:**
- `dtn neighbors add/remove/ping` — Unified neighbor management persisting to both running ION and host.rc
- `dtn nodes` — Formatted table of all nodes in the contact graph
- `dtn contacts` — Raw contact graph edge listing
- `dtn plans` — Forwarding plan listing

**Diagnostics:**
- `dtn status` — Node health dashboard (ION version, services, contact/plan counts)
- `dtn trace <IPN>` — BFS-based multi-hop route tracing with per-hop verification
- `dtn diagnose` — Comprehensive network diagnostics across all known nodes

**Discovery:**
- `dtn discover` — Multi-source node discovery with persistent caching and fast recovery

**Applications:**
- `dtn chat [IPN]` — Persistent terminal chat with per-sender conversations, unread indicators, and conversation switching
- `dtn send <IPN> "message"` — Raw bundle transmission
- `dtn sensor` — BME280/BMP280 sensor data to IPNSIG Bundle Board

### 5.2 Testbed Configuration

The evaluation testbed consists of two nodes with distinct hardware profiles and network configurations, connected to the OpenIPN global network:

| Property | Pi05 | Echo |
|----------|------|------|
| **IPN** | ipn:268485091 | ipn:268485111 |
| **Platform** | Raspberry Pi 4 (ARM64) | x86_64 server |
| **OS** | Raspberry Pi OS (Debian-based) | Ubuntu 22.04 |
| **Location** | Kathmandu, Nepal | Dhulikhel, Nepal |
| **VPN** | Tailscale + ZeroTier | ZeroTier only |
| **Gateway connectivity** | Direct (Tailscale, port 4556) | Relayed via Pi05 (ZeroTier, port 4557) |
| **Role** | Primary node, gateway relay | Secondary node, relay-dependent |

This configuration creates a realistic multi-hop topology: Echo reaches the DTNGW gateway (and thus the entire OpenIPN network) only through Pi05, exercising ION's CGR routing. Pi05 requires dual UDP inducts (port 4556 for Tailscale traffic from the gateway, port 4557 for ZeroTier traffic from Echo) to serve as a relay node.

Additionally, the testbed connects to approximately 40 nodes worldwide through the DTNGW gateway, enabling testing against a diverse set of real DTN nodes operated by independent researchers and institutions.

### 5.3 Evaluation Metrics

1. **Setup complexity (RQ1):** Number of manual steps and wall-clock time to deploy a fully functional DTN node, measured before and after dtn-tools. Includes ION compilation, configuration, service setup, and neighbor addition.
2. **Operational overhead (RQ1):** Number of distinct commands required for common tasks (add neighbor, diagnose routing, send message, check status) compared to raw ION admin programs.
3. **Network recovery time (RQ3):** Time from ION restart to full contact graph restoration, measured with and without discovery caching. Contacts in ION's shared memory are volatile; after restart, nodes have no routes until dtnex exchanges or manual re-addition.
4. **Diagnostic accuracy (RQ2):** Ability of `dtn trace` and `dtn diagnose` to correctly identify real routing issues (missing contacts, unreachable neighbors, stale plans) validated against manual inspection.
5. **Message delivery (RQ4):** End-to-end chat message delivery over multi-hop routes, including message persistence across chat session restarts.
6. **Operational challenges documented (RQ2):** Number and severity of ION-DTN operational issues discovered and resolved during deployment.

### 5.4 Data Collection

- ION admin program output (contacts, plans, outducts) before and after operations
- systemd journal logs for service health and restart counts
- OpenIPN monitoring uptime percentages (24h and 7d windows)
- Bundle round-trip times via bping
- Discovery daemon logs showing node count and re-injection events
- Chat message delivery confirmation across multi-hop paths

## 6. Expected Contributions

1. **dtn-tools: An open-source operational toolkit for ION-DTN.** The first unified CLI that covers the complete lifecycle from node deployment through daily operation and diagnostics. Reduces the barrier to entry from ION expert to general Linux user. Available under MIT License.

2. **Multi-source node discovery with fast recovery.** A novel approach to DTN network discovery that aggregates OpenIPN metadata, global contact graphs, local dtnex exchanges, and ION state into a persistent node database. The fast recovery mechanism re-injects cached contacts after ION restarts, reducing convergence time from up to 30 minutes (dtnex exchange cycle) to under 10 seconds.

3. **BFS-based route diagnostics for ION-DTN.** The first user-facing route tracing tool for ION that simulates CGR routing with a first-hop constraint, verifies each hop (contact, range, return contact, IP reachability, DTN-level ping), and identifies the specific point of failure in multi-hop paths.

4. **Persistent DTN terminal chat.** A conversation-based messaging application over BPv7 bundles with per-sender history, unread indicators, conversation switching, and JSON-encoded messages on service number 5 — demonstrating a practical application-layer use case for DTN.

5. **Operational experience report.** A detailed account of eight distinct operational challenges encountered in terrestrial ION-DTN deployment — contact graph stale state, dtnex CBOR buffer overflow, bpversion exit code anomaly, relay routing configuration, bpecho endpoint misconfiguration, contact expiration after restart, bpclm stale state, and dtnex semaphore errors after killm — with root causes and solutions. This experience is directly useful to the DTN research community.

6. **Complexity quantification.** Concrete before/after measurements demonstrating the reduction in setup steps (50+ to 1), per-operation commands (8 to 1 for neighbor addition), and recovery time (30 minutes to <10 seconds).

## 7. Timeline

| Phase | Duration | Activities | Deliverables |
|-------|----------|------------|--------------|
| **Phase 1: Core Development** | Months 1-2 | CLI framework, setup wizard (9 steps), neighbor add/remove with host.rc persistence, status command, bpversion exit code workaround | Working `dtn init`, `dtn status`, `dtn neighbors` commands |
| **Phase 2: Diagnostics & Discovery** | Months 2-3 | BFS route tracing, multi-source discovery daemon, node table display, contact/plan listing, diagnose command | `dtn trace`, `dtn diagnose`, `dtn discover`, `dtn nodes` |
| **Phase 3: Testbed Deployment** | Months 3-4 | Deploy on Pi05 and Echo, connect to OpenIPN, resolve operational issues (contact stale state, dtnex crash, relay routing, bpecho endpoints), service management commands | Two operational nodes on OpenIPN, bug documentation |
| **Phase 4: Chat & Recovery** | Months 4-5 | Persistent chat with per-sender history, conversation switching, unread indicators, discovery caching with fast recovery, sensor integration | `dtn chat` with persistence, fast recovery mechanism, `dtn sensor` |
| **Phase 5: Evaluation & Writing** | Months 5-6 | Quantitative evaluation, before/after comparisons, paper writing, community feedback integration | Research article, proposal, open-source release |

## 8. Related Work

### 8.1 DTN Implementations and Their Management Interfaces

**ION-DTN** (NASA JPL) [2] is the reference BPv7 implementation and the most widely deployed in research networks. It provides low-level admin programs (ionadmin, bpadmin, ipnadmin, ionsecadmin) with command-line interfaces, but no unified management tool. Configuration requires authoring multi-section `.rc` files with cryptic syntax. ION's shared-memory architecture (SDR) makes programmatic interaction complex, as external tools must either use the C API or wrap the admin programs.

**uD3TN** (D3TN GmbH) [12] provides a lightweight BPv7 implementation with a Python management library (ud3tn-utils) and AAP2Client for daemon interaction. This library API approach differs fundamentally from dtn-tools' CLI approach and targets a different implementation.

**DTN7-go** [14] implements BPv7 in Go with REST API, WebSocket API, and UNIX socket interfaces, plus a `dtnclient` CLI tool. While offering more modern APIs than ION, it is not compatible with ION's ecosystem or the OpenIPN network.

**HDTN** (NASA Glenn Research Center) [11] targets high-rate DTN applications and provides a web-based GUI with configuration and telemetry dashboards. In 2024, HDTN streamed 4K UHD video between a PC-12 aircraft and the ISS at 900+ Mbps, demonstrating BPv7's throughput capability. However, HDTN's management interface is not compatible with ION.

**DTNME** is a C++ DTN implementation used operationally on the International Space Station for file transfers between the ISS and ground. Its management is tightly integrated with NASA mission operations.

### 8.2 DTN Management Standards

**RFC 9675 — DTNMA** (November 2024) [7] defines the DTN Management Architecture, addressing Operations, Administration, and Management (OAM) challenges in DTN networks. DTNMA introduces an agent/manager model with Autonomy, Management and Control (AMC) agents that can execute predefined control procedures. The standard explicitly acknowledges that current DTN deployments rely on "pre-placed keys and bespoke tooling" for management. However, DTNMA operates at the protocol and architecture level — it defines how management *should* work rather than providing the operational tools that node operators need today. dtn-tools addresses the practical gap that DTNMA identifies but does not yet fill.

### 8.3 DTN Auxiliary Tools

**dtnex** (Samo Grasic) [3] is a metadata exchange protocol for ION-DTN that enables automatic contact sharing between nodes. Nodes broadcast their metadata (name, location, contacts) to neighbors, who update their contact graphs accordingly. dtn-tools integrates dtnex as both a build target (compiled during `dtn init`) and a discovery source (parsed by the discovery daemon).

**ionwd** (Samo Grasic) is a watchdog daemon that monitors ION's health and restarts it after crashes. dtn-tools installs and manages ionwd as a systemd service.

### 8.4 Recent DTN Deployments and Research

**NASA PACE Mission** (2024) [10] became the first Class-B NASA mission using DTN operationally, transmitting 34 million bundles with a 100% success rate. This demonstrated DTN's reliability for real mission-critical data, but relied on mission-specific tooling and ground support systems.

**DTN-COMET** (2025) [9] developed automated containerized testbeds for multi-implementation DTN benchmarking, enabling reproducible performance comparisons between ION, uD3TN, DTN7, and HDTN. While DTN-COMET addresses the *testing* gap, it does not address the *operational management* gap that dtn-tools targets.

**OpenIPN / IPNSIG** [3] maintains the global DTN research network with 40+ nodes and 1,000+ registered members. The network provides IPN number allocation, a gateway node, monitoring infrastructure, and a Bundle Board for IoT data. dtn-tools integrates with OpenIPN as both a data source (discovery) and a deployment target (testbed nodes).

### 8.5 Positioning of dtn-tools

No comparable unified CLI toolkit exists for ION-DTN node management. Table 1 summarizes the landscape:

| Tool | Scope | Implementation | ION-Compatible | Unified CLI |
|------|-------|----------------|----------------|-------------|
| ION admin programs | Low-level config | C (ION) | Yes | No (4 separate programs) |
| dtnex | Metadata exchange | C | Yes | No (single function) |
| ud3tn-utils | Management API | Python (uD3TN) | No | No (library) |
| dtnclient (DTN7) | CLI client | Go | No | Partial |
| HDTN GUI | Dashboard | Web (HDTN) | No | No (GUI) |
| DTN-COMET | Testing | Docker | Multi-impl | No (testing) |
| **dtn-tools** | **Full lifecycle** | **Python (ION)** | **Yes** | **Yes (19 commands)** |

## 9. Budget

This is a software-only research project. All required hardware is already available:
- Raspberry Pi 4 (4GB) with BMP280 sensor — Pi05 testbed node
- x86_64 server — Echo testbed node
- Tailscale and ZeroTier VPN accounts (free tier)
- OpenIPN network membership (free)

No additional funding is required for software development and testing. Conference registration and travel for paper presentation may be sought separately through travel grants or institutional support.

## 10. References

[1] S. Burleigh, K. Fall, and E. Birrane, "Bundle Protocol Version 7," RFC 9171, Internet Engineering Task Force, January 2022. https://doi.org/10.17487/RFC9171

[2] S. Burleigh, "Interplanetary Overlay Network (ION) Design and Operation, v4.1," Jet Propulsion Laboratory, California Institute of Technology, 2020.

[3] S. Grasic, "OpenIPN: An Open Interplanetary Network for DTN Research," IPNSIG Technical Report, 2023. https://openipn.org

[4] K. Scott and S. Burleigh, "Bundle Protocol Specification," RFC 5050, Internet Engineering Task Force, November 2007. https://doi.org/10.17487/RFC5050

[5] E. Birrane, A. Mayer, and J. Miner, "Bundle Protocol Security (BPSec)," RFC 9172, Internet Engineering Task Force, January 2022. https://doi.org/10.17487/RFC9172

[6] S. Burleigh, "Contact Graph Routing," Internet-Draft, Internet Engineering Task Force, 2010.

[7] E. Birrane and S. Heiner, "Delay-Tolerant Networking Management Architecture (DTNMA)," RFC 9675, Internet Engineering Task Force, November 2024. https://doi.org/10.17487/RFC9675

[8] V. Cerf et al., "Delay-Tolerant Networking Architecture," RFC 4838, Internet Engineering Task Force, April 2007. https://doi.org/10.17487/RFC4838

[9] B. Nothlich et al., "DTN-COMET: Automated Containerized Testbeds for Multi-Implementation Benchmarking," Technical Report, January 2025.

[10] NASA Goddard Space Flight Center, "PACE Mission DTN Operations Report," NASA Technical Reports Server, 2024.

[11] NASA Glenn Research Center, "HDTN 4K UHD Video Streaming over BPv7 between PC-12 Aircraft and ISS," NASA Technical Reports Server, 2024.

[12] M. Feldmann and F. Walter, "uD3TN: A Lightweight DTN Protocol Implementation for Microcontrollers," Proceedings of the International Conference on Networked Systems (NetSys), 2021.

[13] IETF DTN Working Group, "Bundle Protocol Version 7 Administrative Record Types Registry," RFC 9713, Internet Engineering Task Force, January 2025. https://doi.org/10.17487/RFC9713

[14] D. Batz et al., "DTN7: A Flexible Delay-Tolerant Networking System in Go," Proceedings of the International Conference on Information and Communications Technologies in Disaster Management (ICT-DM), 2019.

[15] H. Kruse et al., "Datagram Convergence Layers for the Delay- and Disruption-Tolerant Networking (DTN) Bundle Protocol and Licklider Transmission Protocol (LTP)," RFC 7122, Internet Engineering Task Force, March 2014. https://doi.org/10.17487/RFC7122

[16] T. Johnson, "DTN IP Neighbor Discovery (IPND)," Internet-Draft, Internet Engineering Task Force, 2019.

[17] S. Grasic and E. Lindgren, "An Analysis of Evaluation Practices for Delay-Tolerant Networking Routing Protocols," IEEE Communications Surveys and Tutorials, vol. 17, no. 1, 2015.
