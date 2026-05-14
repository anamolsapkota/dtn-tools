# Research Proposal: dtn-tools — Simplifying Delay-Tolerant Network Node Management for Terrestrial IoT and Research Networks

## 1. Title

**dtn-tools: A Unified Command-Line Toolkit for Simplified DTN Node Deployment, Management, and Monitoring on ION-DTN**

## 2. Principal Investigator

Anamol Sapkota
Independent Researcher
Kathmandu, Nepal

## 3. Abstract

Delay-Tolerant Networking (DTN) enables communication in environments with intermittent connectivity, high latency, and disruption — from deep-space to rural IoT. While the Bundle Protocol version 7 (BPv7, RFC 9171) and implementations like NASA JPL's ION-DTN provide the protocol stack, deploying and managing DTN nodes remains prohibitively complex. Operators must manually edit multiple configuration files (ionadmin, bpadmin, ipnadmin, ionsecadmin), manage systemd services, compile software from source, and maintain contact graphs — tasks that require expert-level ION knowledge.

This research presents **dtn-tools**, an open-source command-line toolkit that reduces DTN node setup from hours to minutes. We describe the design, implementation, and operational experience of deploying dtn-tools across a multi-node DTN testbed connected to the OpenIPN global research network. We evaluate the toolkit's impact on setup complexity, operational overhead, and network observability, and present solutions to problems encountered in real-world DTN operations including contact graph management, multi-hop routing diagnostics, and persistent chat over DTN bundles.

## 4. Introduction and Background

### 4.1 Delay-Tolerant Networking

DTN was originally developed for deep-space communication where round-trip times can exceed minutes and connectivity windows are scheduled. The Bundle Protocol (BP) provides store-and-forward message delivery using Contact Graph Routing (CGR) to compute paths through time-varying network topologies. BP version 7 (RFC 9171) standardized the protocol, and several implementations exist: ION-DTN (NASA JPL), uD3TN, DTN7, and HDTN.

### 4.2 Terrestrial DTN Applications

DTN principles apply beyond space: rural connectivity in developing regions, disaster response networks, IoT sensor data collection, and challenged tactical networks. The OpenIPN network (openipn.org), operated by the Interplanetary Networking Special Interest Group (IPNSIG), provides a global research testbed with 40+ nodes across multiple countries, enabling researchers to test DTN applications over real multi-hop routes.

### 4.3 The Management Gap

Despite mature protocol implementations, a significant gap exists in **operational tooling**. Setting up a single ION-DTN node requires:

1. Compiling ION-DTN from source (autoconf/automake build system)
2. Compiling auxiliary tools (dtnex, ionwd, bpbme280)
3. Writing a multi-section configuration file (ionadmin, bpadmin, ipnadmin, ionsecadmin)
4. Creating and managing systemd service units
5. Manually adding contacts, ranges, and plans for each neighbor
6. Monitoring node health and diagnosing routing issues

No unified management interface exists. Operators use raw ION admin programs (ionadmin, bpadmin, ipnadmin) with cryptic command syntax. This complexity limits DTN adoption in research and education.

### 4.4 Research Objectives

1. Design and implement a unified CLI toolkit that abstracts ION-DTN's complexity into intuitive commands
2. Develop automated node discovery and contact graph management
3. Implement persistent terminal-based DTN messaging with per-sender conversations
4. Evaluate the toolkit through deployment on a real multi-node testbed connected to the OpenIPN network
5. Identify and document operational challenges in terrestrial DTN networks

## 5. Methodology

### 5.1 System Design

dtn-tools is a Python CLI that wraps ION-DTN's admin programs (ionadmin, bpadmin, ipnadmin) and user tools (bpsource, bprecvfile, bping) into high-level commands:

- **dtn init** — Complete setup wizard: installs ION, dtnex, ionwd from source; generates configuration; creates systemd services; starts the node. Idempotent — safe to run multiple times.
- **dtn status/diagnose** — Node health monitoring with service status, contact graph statistics, and multi-hop route diagnostics.
- **dtn neighbors add/remove/ping** — Neighbor management with persistent configuration (writes to both running ION and host.rc).
- **dtn discover** — Multi-source node discovery (OpenIPN metadata, contact graph, local dtnex, IPND beacons).
- **dtn chat** — Persistent terminal chat with per-sender conversations, unread indicators, and conversation switching.
- **dtn trace** — Route tracing that simulates CGR to show the full multi-hop path and identify issues at each hop.
- **dtn sensor** — IoT sensor data transmission to the IPNSIG Bundle Board.
- **dtn start/stop/restart/enable/disable** — Service management with systemd and direct process fallback.

### 5.2 Testbed Configuration

| Node | IPN | Platform | Location | Connectivity |
|------|-----|----------|----------|-------------|
| Pi05 | ipn:268485091 | Raspberry Pi 4 | Kathmandu, Nepal | Tailscale VPN + ZeroTier |
| Echo | ipn:268485111 | x86_64 server | Dhulikhel, Nepal | ZeroTier (relayed via Pi05) |
| DTNGW | ipn:268485000 | Gateway | OpenIPN infrastructure | Internet (Tailscale) |

Additional connectivity to 40+ nodes in the OpenIPN global network via the DTNGW gateway.

### 5.3 Evaluation Metrics

1. **Setup complexity**: Steps/time required to deploy a new node (before vs. after dtn-tools)
2. **Operational overhead**: Commands needed for common tasks (add neighbor, send message, diagnose issues)
3. **Network recovery time**: Time to restore full connectivity after ION restart (with vs. without discovery caching)
4. **Message delivery**: End-to-end chat message delivery over multi-hop routes
5. **Diagnostic accuracy**: Ability of `dtn trace`/`dtn diagnose` to identify real routing issues

## 6. Expected Contributions

1. **dtn-tools**: An open-source toolkit reducing DTN node setup from expert-level to beginner-friendly
2. **Multi-source discovery**: Automated contact graph management combining local and global sources
3. **DTN terminal chat**: First persistent, conversation-based chat application over BPv7
4. **Operational insights**: Documented challenges and solutions for terrestrial ION-DTN deployments
5. **Fast recovery mechanism**: Discovery-based contact re-injection for rapid network restoration after restarts

## 7. Timeline

| Phase | Duration | Activities |
|-------|----------|-----------|
| Phase 1 | Months 1-2 | Core CLI development, setup wizard, neighbor management |
| Phase 2 | Months 2-3 | Discovery system, route diagnostics, chat implementation |
| Phase 3 | Months 3-4 | Testbed deployment, operational experience, bug fixes |
| Phase 4 | Months 4-5 | Chat overhaul (persistent history), discovery caching |
| Phase 5 | Months 5-6 | Evaluation, paper writing, community feedback |

## 8. Related Work

- **ION-DTN** (NASA JPL): The reference BPv7 implementation used as our underlying engine. Provides admin programs but no unified management interface.
- **dtnex** (Samo Grasic): Metadata exchange protocol that enables automatic contact sharing between ION nodes. dtn-tools integrates dtnex as a discovery source.
- **RFC 9675 — DTNMA** (November 2024): DTN Management Architecture standard addressing OAM challenges. Focuses on protocol-level management but does not provide operational tooling.
- **uD3TN** (D3TN): Lightweight DTN implementation with Python management library (ud3tn-utils) and AAP2Client. Different approach (library API vs CLI).
- **DTN7-go**: DTN implementation in Go with REST API, WebSocket API, and `dtnclient` CLI. Not ION-compatible.
- **HDTN** (NASA Glenn): High-rate DTN implementation with web-based GUI dashboard. Streamed 4K UHD video over BPv7 at 900+ Mbps in 2024. Management via web interface, not CLI.
- **DTN-COMET** (2025): Automated containerized testbed for multi-implementation benchmarking. Focuses on performance evaluation, not operational management.
- **NASA PACE Mission** (2024): First Class-B NASA mission using DTN operationally — 34M bundles with 100% success rate, demonstrating DTN maturity.
- **OpenIPN / IPNSIG**: Global DTN network with 40+ nodes and 1000+ members. dtn-tools integrates as both a data source and deployment target.

No comparable unified CLI toolkit exists for ION-DTN node management.

## 9. Budget

This is a software-only project. Hardware (Raspberry Pi, sensors) is already available. No additional funding required for the software development phase. Conference travel for paper presentation may be sought separately.

## 10. References

[1] S. Burleigh et al., "Bundle Protocol Version 7," RFC 9171, IETF, January 2022.
[2] S. Burleigh, "Interplanetary Overlay Network (ION) Design and Operation," JPL, 2020.
[3] S. Grasic, "OpenIPN: An Open Interplanetary Network for DTN Research," IPNSIG, 2023.
[4] K. Scott and S. Burleigh, "Bundle Protocol Specification," RFC 5050, IETF, November 2007.
[5] E. Birrane et al., "Bundle Protocol Security (BPSec)," RFC 9172, IETF, January 2022.
[6] S. Burleigh, "Contact Graph Routing," Internet-Draft, IETF, 2010.
[7] E. Birrane and S. Heiner, "Delay-Tolerant Networking Management Architecture (DTNMA)," RFC 9675, IETF, November 2024.
[8] V. Cerf et al., "Delay-Tolerant Networking Architecture," RFC 4838, IETF, April 2007.
[9] B. Nöthlich et al., "DTN-COMET: Automated Containerized Testbeds for Multi-Implementation Benchmarking," January 2025.
[10] NASA GSFC, "PACE Mission DTN Operations Report," NASA Technical Reports Server, 2024.
[11] NASA GRC, "HDTN 4K UHD Video Streaming over BPv7," NASA Technical Reports Server, 2024.
[12] M. Feldmann and F. Walter, "uD3TN: A Lightweight DTN Protocol Implementation," Proceedings of NetSys, 2021.
[13] IETF DTN WG, "Bundle Protocol Version 7 Administrative Record Types Registry," RFC 9713, January 2025.
