#!/usr/bin/env python3
"""
DTN Node Setup Wizard — complete setup from bare Linux to running DTN node.

Installs ION-DTN, dtnex, ionwd, generates all configuration, creates systemd
services, and starts the node. Each step checks if already done and skips
automatically, so running 'dtn init' on an existing node is safe.

Usage:
    dtn init                          Interactive setup
    dtn init --ipn 268485091 --yes    Non-interactive with defaults
    dtn init --skip-build             Skip ION/dtnex compilation
"""

import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ION_REPO = "https://git.code.sf.net/p/ione/code"
ION_BRANCH = "ione-1.1.0"
DTNEX_REPO = "https://github.com/samograsic/ion-dtn-dtnex"
IONWD_REPO = "https://github.com/samograsic/ionwd"

APT_PACKAGES = [
    "build-essential", "autoconf", "automake", "libtool", "m4",
    "pkg-config", "git", "libssl-dev", "graphviz", "python3-pip",
]

GATEWAY_IPN = "268485000"
GATEWAY_IP = "100.96.108.37"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def prompt(msg, default=None, auto_yes=False):
    if auto_yes and default is not None:
        return default
    if default:
        val = input(f"  {msg} [{default}]: ").strip()
        return val if val else default
    while True:
        val = input(f"  {msg}: ").strip()
        if val:
            return val
        print("    (required)")


def confirm(msg, default=True, auto_yes=False):
    if auto_yes:
        return default
    suffix = " [Y/n]: " if default else " [y/N]: "
    val = input(f"  {msg}{suffix}").strip().lower()
    if not val:
        return default
    return val in ("y", "yes")


def run_cmd(cmd, check=True, sudo=False, capture=True, cwd=None, timeout=None):
    """Run a shell command. Returns (stdout, returncode)."""
    if sudo:
        cmd = f"sudo {cmd}"
    try:
        if capture:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               cwd=cwd, timeout=timeout)
            return r.stdout.strip(), r.returncode
        else:
            r = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout)
            return "", r.returncode
    except subprocess.TimeoutExpired:
        return "", 124
    except Exception as e:
        return str(e), 1


def has_binary(name):
    return shutil.which(name) is not None


def pkg_installed(pkg):
    _, rc = run_cmd(f"dpkg -s {pkg} 2>/dev/null | grep -q 'Status: install ok installed'")
    return rc == 0


def detect_os():
    info = {"distro": "unknown", "arch": platform.machine(), "apt": False}
    osr = Path("/etc/os-release")
    if osr.exists():
        data = {}
        for line in osr.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k] = v.strip('"')
        info["distro"] = data.get("ID", "unknown")
    if info["distro"] in ("debian", "ubuntu", "raspbian"):
        info["apt"] = True
    return info


def print_step(num, total, desc):
    print(f"\n[{num}/{total}] {desc}")
    print("-" * 60)


def print_ok(msg):
    print(f"  ✓ {msg}")


def print_skip(msg):
    print(f"  → {msg} (already done)")


def print_warn(msg):
    print(f"  ! {msg}")


def print_fail(msg):
    print(f"  ✗ {msg}")


# ---------------------------------------------------------------------------
# Step 1: System dependencies
# ---------------------------------------------------------------------------

def check_system_deps(cfg):
    if not cfg["os"]["apt"]:
        return True  # can't check on non-apt systems
    missing = [p for p in APT_PACKAGES if not pkg_installed(p)]
    if missing:
        return False
    # Check python requests
    _, rc = run_cmd("python3 -c 'import requests' 2>/dev/null")
    return rc == 0


def run_system_deps(cfg):
    if not cfg["os"]["apt"]:
        print_warn("Not a Debian/Ubuntu system — install these packages manually:")
        print(f"    {' '.join(APT_PACKAGES)}")
        print(f"    pip3 install requests")
        return

    missing = [p for p in APT_PACKAGES if not pkg_installed(p)]
    if missing:
        print(f"  Installing: {', '.join(missing)}")
        _, rc = run_cmd("apt-get update -qq", sudo=True, capture=False)
        _, rc = run_cmd(f"apt-get install -y {' '.join(missing)}", sudo=True, capture=False)
        if rc != 0:
            raise RuntimeError("apt-get install failed")
    else:
        print_ok("All system packages installed")

    _, rc = run_cmd("python3 -c 'import requests' 2>/dev/null")
    if rc != 0:
        print("  Installing Python requests module...")
        run_cmd("pip3 install requests 2>/dev/null || pip3 install --break-system-packages requests", sudo=True)


# ---------------------------------------------------------------------------
# Step 2: Build ION-DTN
# ---------------------------------------------------------------------------

def check_ion_built(cfg):
    return has_binary("ionadmin") and has_binary("bpadmin") and has_binary("bpecho")


def run_build_ion(cfg):
    src_dir = Path(cfg["src_dir"]) / "ione-code"

    if not src_dir.exists():
        print(f"  Cloning ION-DTN to {src_dir}...")
        src_dir.parent.mkdir(parents=True, exist_ok=True)
        _, rc = run_cmd(f"git clone {ION_REPO} {src_dir}", capture=False)
        if rc != 0:
            raise RuntimeError("git clone failed")

    # Checkout correct branch
    print(f"  Checking out branch {ION_BRANCH}...")
    run_cmd(f"git checkout {ION_BRANCH} 2>/dev/null; git pull origin {ION_BRANCH} 2>/dev/null",
            cwd=str(src_dir), check=False)

    # Build
    nproc, _ = run_cmd("nproc 2>/dev/null || echo 2")
    nproc = nproc.strip() or "2"

    print(f"  Building ION-DTN (this may take 10-20 minutes on a Raspberry Pi)...")
    _, rc = run_cmd("autoreconf -ivf 2>&1 | tail -3", cwd=str(src_dir), capture=False)
    _, rc = run_cmd("./configure 2>&1 | tail -5", cwd=str(src_dir), capture=False)
    if rc != 0:
        raise RuntimeError("./configure failed")
    _, rc = run_cmd(f"make -j{nproc}", cwd=str(src_dir), capture=False, timeout=1800)
    if rc != 0:
        raise RuntimeError("make failed")
    _, rc = run_cmd("make install", sudo=True, cwd=str(src_dir), capture=False)
    if rc != 0:
        raise RuntimeError("make install failed")
    run_cmd("ldconfig", sudo=True)
    print_ok("ION-DTN installed")


# ---------------------------------------------------------------------------
# Step 3: Build dtnex
# ---------------------------------------------------------------------------

def check_dtnex_built(cfg):
    return has_binary("dtnex")


def run_build_dtnex(cfg):
    src_dir = Path(cfg["src_dir"]) / "ion-dtn-dtnex"

    if not src_dir.exists():
        print(f"  Cloning dtnex to {src_dir}...")
        src_dir.parent.mkdir(parents=True, exist_ok=True)
        _, rc = run_cmd(f"git clone {DTNEX_REPO} {src_dir}", capture=False)
        if rc != 0:
            raise RuntimeError("git clone failed")

    print("  Building dtnex...")
    _, rc = run_cmd("./build_standalone.sh", cwd=str(src_dir), capture=False)
    if rc != 0:
        raise RuntimeError("build_standalone.sh failed")
    _, rc = run_cmd("make install", sudo=True, cwd=str(src_dir), capture=False)
    if rc != 0:
        raise RuntimeError("make install failed")
    print_ok("dtnex installed")


# ---------------------------------------------------------------------------
# Step 4: Set up ionwd
# ---------------------------------------------------------------------------

def check_ionwd_setup(cfg):
    ionwd_dir = Path(cfg["dtn_dir"]) / "ionwd"
    return (ionwd_dir / "ionwd.sh").exists()


def run_setup_ionwd(cfg):
    ionwd_dir = Path(cfg["dtn_dir"]) / "ionwd"

    if not ionwd_dir.exists():
        print(f"  Cloning ionwd to {ionwd_dir}...")
        _, rc = run_cmd(f"git clone {IONWD_REPO} {ionwd_dir}", capture=False)
        if rc != 0:
            raise RuntimeError("git clone failed")

    # Patch ionwd.sh with correct paths
    ionwd_sh = ionwd_dir / "ionwd.sh"
    if ionwd_sh.exists():
        content = ionwd_sh.read_text()
        host_rc = f"{cfg['dtn_dir']}/host{cfg['ipn']}.rc"
        log_dir = cfg["dtn_dir"]

        # Replace config lines
        import re
        content = re.sub(r'ION_CONFIG_FILE="[^"]*"', f'ION_CONFIG_FILE="{host_rc}"', content)
        content = re.sub(r'LOG_DIR="[^"]*"', f'LOG_DIR="{log_dir}"', content)
        ionwd_sh.write_text(content)
        ionwd_sh.chmod(0o755)
        print_ok(f"ionwd configured at {ionwd_dir}")
    else:
        print_warn("ionwd.sh not found in cloned repo")


# ---------------------------------------------------------------------------
# Step 5: Create directories
# ---------------------------------------------------------------------------

def check_dirs_exist(cfg):
    dtn_dir = Path(cfg["dtn_dir"])
    return all((dtn_dir / d).is_dir() for d in ["dtn-discovery", "scripts", "logs"])


def run_create_dirs(cfg):
    dtn_dir = Path(cfg["dtn_dir"])
    for d in ["", "dtn-discovery", "scripts", "logs"]:
        (dtn_dir / d).mkdir(parents=True, exist_ok=True)
    print_ok(f"Directories created in {dtn_dir}")


# ---------------------------------------------------------------------------
# Step 6: Generate configuration files
# ---------------------------------------------------------------------------

def check_configs_exist(cfg):
    dtn_dir = Path(cfg["dtn_dir"])
    host_rc = dtn_dir / f"host{cfg['ipn']}.rc"
    dtnex_conf = dtn_dir / "dtnex.conf"
    return host_rc.exists() and dtnex_conf.exists()


def run_gen_configs(cfg):
    dtn_dir = Path(cfg["dtn_dir"])

    # host.rc
    host_rc = dtn_dir / f"host{cfg['ipn']}.rc"
    if host_rc.exists():
        print_warn(f"{host_rc.name} already exists — keeping existing file")
    else:
        sections = [
            generate_ionrc(cfg), "",
            generate_bprc(cfg), "",
            generate_ipnrc(cfg), "",
            generate_ionsecrc(), "",
        ]
        host_rc.write_text("\n".join(sections))
        print_ok(f"Created {host_rc}")

    # ionconfig
    ionconfig = dtn_dir / f"host{cfg['ipn']}.ionconfig"
    if not ionconfig.exists():
        ionconfig.write_text(f"wmKey 1\nsdrName ion{cfg['ipn'][-3:]}\n")
        print_ok(f"Created {ionconfig}")

    # dtnex.conf
    dtnex_conf = dtn_dir / "dtnex.conf"
    if dtnex_conf.exists():
        print_warn("dtnex.conf already exists — keeping existing file")
    else:
        dtnex_conf.write_text(generate_dtnex_conf(cfg))
        print_ok(f"Created {dtnex_conf}")

    # discovery.conf
    disc_conf = dtn_dir / "dtn-discovery" / "discovery.conf"
    if not disc_conf.exists():
        disc_conf.write_text(generate_discovery_conf(cfg))
        print_ok(f"Created {disc_conf}")

    # ipnd.rc
    ipnd_rc = dtn_dir / "dtn-discovery" / "ipnd.rc"
    if not ipnd_rc.exists():
        ipnd_rc.write_text(generate_ipnd_rc(cfg))
        print_ok(f"Created {ipnd_rc}")

    # Copy discovery daemon
    src_dir = Path(__file__).resolve().parent
    discovery_src = src_dir / "discovery.py"
    discovery_dst = dtn_dir / "dtn-discovery" / "discovery.py"
    if discovery_src.exists() and not discovery_dst.exists():
        shutil.copy2(discovery_src, discovery_dst)
        discovery_dst.chmod(0o755)
        print_ok(f"Copied discovery.py")
    elif discovery_src.exists():
        # Update if source is newer
        if discovery_src.stat().st_mtime > discovery_dst.stat().st_mtime:
            shutil.copy2(discovery_src, discovery_dst)
            print_ok("Updated discovery.py")


# ---------------------------------------------------------------------------
# Step 7: Start ION
# ---------------------------------------------------------------------------

def check_ion_running(cfg):
    _, rc = run_cmd("bpversion 2>/dev/null")
    return rc == 0 and _ != ""


def run_start_ion(cfg):
    # Check if ionwd service is managing ION
    _, rc = run_cmd("systemctl is-active ionwd 2>/dev/null")
    if rc == 0:
        print_ok("ION is managed by ionwd service")
        return

    # Start manually with ionstart
    host_rc = f"{cfg['dtn_dir']}/host{cfg['ipn']}.rc"
    if not Path(host_rc).exists():
        print_fail(f"Config file not found: {host_rc}")
        return

    print(f"  Starting ION with {host_rc}...")
    _, rc = run_cmd(f"ionstart -I {host_rc}", capture=False, timeout=30)
    if rc != 0:
        # ION might already be running in a weird state, try restart
        print_warn("ionstart failed, trying ionstop + ionstart...")
        run_cmd("ionstop 2>/dev/null", timeout=15)
        import time
        time.sleep(3)
        _, rc = run_cmd(f"ionstart -I {host_rc}", capture=False, timeout=30)
        if rc != 0:
            raise RuntimeError("Could not start ION")
    print_ok("ION started")


# ---------------------------------------------------------------------------
# Step 8: Install and start services
# ---------------------------------------------------------------------------

def check_services_installed(cfg):
    services = ["ionwd", "dtnex", "bpecho", "dtn-discovery"]
    for svc in services:
        svc_path = Path(f"/etc/systemd/system/{svc}.service")
        if not svc_path.exists():
            return False
    return True


def run_install_services(cfg):
    services = generate_all_services(cfg)

    for name, content in services.items():
        svc_path = Path(f"/etc/systemd/system/{name}.service")
        try:
            if svc_path.exists():
                existing = svc_path.read_text()
                if existing.strip() == content.strip():
                    print_skip(f"{name}.service")
                    continue
                print(f"  Updating {name}.service...")
            else:
                print(f"  Creating {name}.service...")

            # Write via sudo
            tmp = Path(f"/tmp/{name}.service")
            tmp.write_text(content)
            _, rc = run_cmd(f"cp {tmp} {svc_path}", sudo=True)
            tmp.unlink(missing_ok=True)
            if rc != 0:
                print_warn(f"Could not write {svc_path} — needs sudo")
                continue
        except PermissionError:
            print_warn(f"Permission denied writing {svc_path}")
            continue

    # Reload and enable
    run_cmd("systemctl daemon-reload", sudo=True)
    for name in services:
        run_cmd(f"systemctl enable {name}", sudo=True)
        _, rc = run_cmd(f"systemctl is-active {name} 2>/dev/null")
        if rc != 0:
            print(f"  Starting {name}...")
            run_cmd(f"systemctl start {name}", sudo=True)
        else:
            print_skip(f"{name} running")

    print_ok("All services installed and enabled")


# ---------------------------------------------------------------------------
# Step 9: Start bpecho
# ---------------------------------------------------------------------------

def check_bpecho_running(cfg):
    out, _ = run_cmd("pgrep -a bpecho 2>/dev/null")
    return bool(out.strip())


def run_start_bpecho(cfg):
    ipn = cfg["ipn"]

    # Check what's already running
    out, _ = run_cmd("pgrep -a bpecho 2>/dev/null")

    # Start bpecho on .2 (standard) if not running
    if f"ipn:{ipn}.2" not in out:
        _, rc = run_cmd(f"nohup bpecho ipn:{ipn}.2 > /dev/null 2>&1 &")
        print_ok(f"Started bpecho on ipn:{ipn}.2")
    else:
        print_skip(f"bpecho ipn:{ipn}.2")

    # Start bpecho on .12161 (openipn.org monitoring) if not running
    if f"ipn:{ipn}.12161" not in out:
        _, rc = run_cmd(f"nohup bpecho ipn:{ipn}.12161 > /dev/null 2>&1 &")
        print_ok(f"Started bpecho on ipn:{ipn}.12161")
    else:
        print_skip(f"bpecho ipn:{ipn}.12161")


# ---------------------------------------------------------------------------
# Config file generators
# ---------------------------------------------------------------------------

def generate_ionrc(cfg):
    ipn = cfg["ipn"]
    gw = cfg["gateway_ipn"]
    rate = cfg["contact_rate"]
    dur = cfg["contact_duration"]
    owlt = cfg["owlt"]

    return f"""## begin ionadmin
1 {ipn} ''
s

# Loopback contact
a contact +1 +{dur} {ipn} {ipn} {rate}
a range +1 +{dur} {ipn} {ipn} {owlt}

# Gateway ({gw})
a contact +1 +{dur} {ipn} {gw} {rate}
a contact +1 +{dur} {gw} {ipn} {rate}
a range +1 +{dur} {ipn} {gw} {owlt}
a range +1 +{dur} {gw} {ipn} {owlt}

m production 1000000
m consumption 1000000
e 1
## end ionadmin"""


def generate_bprc(cfg):
    ipn = cfg["ipn"]
    gw_ip = cfg["gateway_ip"]
    port = cfg["udp_port"]

    return f"""## begin bpadmin
1
a scheme ipn 'ipnfw' 'ipnadminep'

# Local endpoints
a endpoint ipn:{ipn}.1 q
a endpoint ipn:{ipn}.2 q
a endpoint ipn:{ipn}.3 q
a endpoint ipn:{ipn}.4 q
a endpoint ipn:{ipn}.5 q
a endpoint ipn:{ipn}.8 q
a endpoint ipn:{ipn}.64 q
a endpoint ipn:{ipn}.65 q

# UDP protocol
a protocol udp 1400 100

# Listen on all interfaces
a induct udp 0.0.0.0:{port} udpcli

# Outducts
a outduct udp {gw_ip}:{port} udpclo
a outduct udp 127.0.0.1:{port} udpclo

s
e 1
## end bpadmin"""


def generate_ipnrc(cfg):
    ipn = cfg["ipn"]
    gw = cfg["gateway_ipn"]
    gw_ip = cfg["gateway_ip"]
    port = cfg["udp_port"]

    return f"""## begin ipnadmin
a plan {gw} udp/{gw_ip}:{port}
a plan {ipn} udp/127.0.0.1:{port}

e 1
## end ipnadmin"""


def generate_ionsecrc():
    return "## begin ionsecadmin\n1\ne 1\n## end ionsecadmin"


def generate_dtnex_conf(cfg):
    return f"""# DTNEx configuration — generated by dtn init
updateInterval=1800
bundleTTL=1800
contactLifetime=3600
contactTimeTolerance=1800
presSharedNetworkKey=open
nodemetadata="{cfg['node_name']},{cfg['email']},{cfg.get('location', '')}"
gpsLatitude={cfg.get('lat', '0.0')}
gpsLongitude={cfg.get('lon', '0.0')}
createGraph=true
graphFile={cfg['dtn_dir']}/contactGraph.png
serviceMode=true
debugMode=false
noMetadataExchange=false
"""


def generate_discovery_conf(cfg):
    dtn_dir = cfg["dtn_dir"]
    return f"""# DTN Discovery — generated by dtn init
my_ipn={cfg['ipn']}
gateway_ipn={cfg['gateway_ipn']}
scan_interval=300
openipn_metadata_url=https://openipn.org/metadata_list.txt
openipn_graph_url=https://openipn.org/contactGraph.gv
local_metadata_file={dtn_dir}/nodesmetadata.txt
discovered_db={dtn_dir}/dtn-discovery/discovered_nodes.json
log_file={dtn_dir}/dtn-discovery/discovery.log
auto_add_contacts=true
auto_add_via_gateway=true
contact_rate=100000
contact_duration=360000000
owlt=1
ipnd_enabled=true
ipnd_config={dtn_dir}/dtn-discovery/ipnd.rc
debug=false
"""


def generate_ipnd_rc(cfg):
    ipn = cfg["ipn"]
    port = cfg["udp_port"]
    broadcast = cfg.get("broadcast_ip", "255.255.255.255")

    return f"""## IPND configuration — generated by dtn init
1
e 1
m eid ipn:{ipn}.0
m port 4550
m announce period 1
m announce eid 1
m interval unicast 10
m interval multicast 30
m interval broadcast 30
m multicast ttl 255
a svcadv CLA-UDP-v4 IP:0.0.0.0 Port:{port}
a listen 0.0.0.0
a destination {broadcast}
s
"""


def generate_all_services(cfg):
    user = cfg["user"]
    dtn_dir = cfg["dtn_dir"]
    ipn = cfg["ipn"]
    services = {}

    services["ionwd"] = f"""[Unit]
Description=ION DTN Watchdog
After=network.target
Wants=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={dtn_dir}
ExecStart={dtn_dir}/ionwd/ionwd.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

    services["dtnex"] = f"""[Unit]
Description=DTN Exchange (dtnex) metadata service
Requires=ionwd.service
After=ionwd.service

[Service]
Type=simple
User={user}
WorkingDirectory={dtn_dir}
ExecStartPre=/bin/sleep 15
ExecStart=/usr/local/bin/dtnex {dtn_dir}/dtnex.conf
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

    services["bpecho"] = f"""[Unit]
Description=DTN bpecho service (monitoring endpoints)
Requires=ionwd.service
After=ionwd.service

[Service]
Type=simple
User={user}
ExecStartPre=/bin/sleep 10
ExecStart=/bin/bash -c '/usr/local/bin/bpecho ipn:{ipn}.12161 & exec /usr/local/bin/bpecho ipn:{ipn}.2'
Restart=always
RestartSec=10
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
"""

    services["dtn-discovery"] = f"""[Unit]
Description=DTN Neighbor Discovery Daemon
Requires=dtnex.service
After=dtnex.service

[Service]
Type=simple
User={user}
WorkingDirectory={dtn_dir}/dtn-discovery
ExecStartPre=/bin/sleep 5
ExecStart=/usr/bin/python3 {dtn_dir}/dtn-discovery/discovery.py
Restart=on-failure
RestartSec=30
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
"""

    return services


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------

@dataclass
class Step:
    name: str
    desc: str
    check: object  # callable(cfg) -> bool
    run: object    # callable(cfg)
    needs_sudo: bool = False
    skip_flag: Optional[str] = None  # cfg key to check for --skip-*


STEPS = [
    Step("system-deps",  "Install system dependencies",   check_system_deps,      run_system_deps,      needs_sudo=True),
    Step("build-ion",    "Build and install ION-DTN",      check_ion_built,        run_build_ion,        needs_sudo=True,  skip_flag="skip_build"),
    Step("build-dtnex",  "Build and install dtnex",        check_dtnex_built,      run_build_dtnex,      needs_sudo=True,  skip_flag="skip_build"),
    Step("setup-ionwd",  "Set up ionwd watchdog",          check_ionwd_setup,      run_setup_ionwd),
    Step("directories",  "Create DTN directories",         check_dirs_exist,       run_create_dirs),
    Step("configs",      "Generate configuration files",   check_configs_exist,    run_gen_configs),
    Step("start-ion",    "Start ION",                      check_ion_running,      run_start_ion),
    Step("services",     "Install systemd services",       check_services_installed, run_install_services, needs_sudo=True, skip_flag="skip_services"),
    Step("bpecho",       "Start bpecho endpoints",         check_bpecho_running,   run_start_bpecho),
]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_init(args):
    """Run the DTN node setup wizard."""
    auto_yes = getattr(args, "yes", False)

    print("=" * 60)
    print("  DTN Node Setup Wizard")
    print("  Sets up a complete DTN node with ION-DTN")
    print("=" * 60)
    print()

    # Detect system
    os_info = detect_os()
    user = getpass.getuser()
    home = os.path.expanduser("~")

    print(f"  System:   {os_info['distro']} ({os_info['arch']})")
    print(f"  User:     {user}")
    print(f"  Home:     {home}")
    print()

    # Check what's already installed
    ion_ok = has_binary("ionadmin")
    dtnex_ok = has_binary("dtnex")
    ion_running = False
    if ion_ok:
        out, rc = run_cmd("bpversion 2>/dev/null")
        ion_running = rc == 0 and out != ""

    if ion_ok:
        print_ok(f"ION-DTN: installed ({shutil.which('ionadmin')})")
    else:
        print(f"  ION-DTN: not installed (will build from source)")
    if dtnex_ok:
        print_ok(f"dtnex:   installed ({shutil.which('dtnex')})")
    else:
        print(f"  dtnex:   not installed (will build from source)")
    if ion_running:
        print_ok("ION:     running")
    print()

    # Gather configuration
    cfg = {}
    cfg["os"] = os_info
    cfg["user"] = user
    cfg["home"] = home
    cfg["auto_yes"] = auto_yes
    cfg["skip_build"] = getattr(args, "skip_build", False)
    cfg["skip_services"] = getattr(args, "skip_services", False)

    # IPN number — try to auto-detect from running ION
    detected_ipn = None
    if ion_running:
        import tempfile, re
        with tempfile.NamedTemporaryFile(mode='w', suffix='.cmd', delete=False) as _f:
            _f.write("l plan\nq\n"); _tmp = _f.name
        out, _ = run_cmd(f"ipnadmin < {_tmp} 2>/dev/null")
        os.unlink(_tmp)
        for line in out.splitlines():
            line = line.strip().lstrip(":").strip()
            m = re.match(r"(\d+)\s+xmit\s+127\.0\.0\.1", line)
            if m:
                detected_ipn = m.group(1)
                break

    default_ipn = getattr(args, "ipn", None) or detected_ipn
    cfg["ipn"] = default_ipn or prompt(
        "Your IPN node number (register at openipn.org)", auto_yes=auto_yes
    )
    if not cfg["ipn"]:
        print_fail("IPN number is required. Register at https://openipn.org")
        return

    cfg["node_name"] = getattr(args, "name", None) or prompt(
        "Node name", f"dtn-node-{cfg['ipn'][-3:]}", auto_yes
    )
    cfg["email"] = getattr(args, "email", None) or prompt(
        "Contact email", f"{user}@example.com", auto_yes
    )
    cfg["location"] = getattr(args, "location", None) or prompt(
        "Location (city, country)", "", auto_yes
    )
    cfg["lat"] = getattr(args, "lat", None) or prompt("GPS latitude", "0.0", auto_yes)
    cfg["lon"] = getattr(args, "lon", None) or prompt("GPS longitude", "0.0", auto_yes)

    # Directories
    default_dtn_dir = getattr(args, "dtn_dir", None)
    if not default_dtn_dir:
        for d in [f"{home}/dtn", f"{home}/ion-dtn"]:
            if os.path.isdir(d):
                default_dtn_dir = d
                break
        if not default_dtn_dir:
            default_dtn_dir = f"{home}/dtn"
    cfg["dtn_dir"] = prompt("DTN working directory", default_dtn_dir, auto_yes)
    cfg["src_dir"] = prompt("Source build directory", f"{home}/src", auto_yes)

    # Network
    cfg["gateway_ipn"] = getattr(args, "gateway_ipn", None) or prompt(
        "Gateway IPN", GATEWAY_IPN, auto_yes
    )
    cfg["gateway_ip"] = getattr(args, "gateway_ip", None) or prompt(
        "Gateway IP (Tailscale/VPN)", GATEWAY_IP, auto_yes
    )
    cfg["udp_port"] = prompt("UDP port", "4556", auto_yes)
    cfg["contact_rate"] = prompt("Contact rate (bytes/sec)", "100000", auto_yes)
    cfg["contact_duration"] = prompt("Contact duration (seconds)", "360000000", auto_yes)
    cfg["owlt"] = prompt("One-way light time (seconds)", "1", auto_yes)
    cfg["broadcast_ip"] = prompt("IPND broadcast address", "255.255.255.255", auto_yes)

    # Summary
    print()
    print("  Configuration Summary:")
    print(f"    IPN:        ipn:{cfg['ipn']}")
    print(f"    Name:       {cfg['node_name']}")
    print(f"    Email:      {cfg['email']}")
    print(f"    Gateway:    ipn:{cfg['gateway_ipn']} at {cfg['gateway_ip']}")
    print(f"    DTN Dir:    {cfg['dtn_dir']}")
    print(f"    Source Dir: {cfg['src_dir']}")
    print()

    if not confirm("Proceed with setup?", True, auto_yes):
        print("  Cancelled.")
        return

    # Run steps
    total = len(STEPS)
    failed = []

    for i, step in enumerate(STEPS, 1):
        print_step(i, total, step.desc)

        # Check skip flags
        if step.skip_flag and cfg.get(step.skip_flag):
            print_skip(f"Skipped (--{step.skip_flag.replace('_', '-')})")
            continue

        # Check if already done
        try:
            if step.check(cfg):
                print_skip(step.desc)
                continue
        except Exception:
            pass  # check failed, run the step

        # Run
        if step.needs_sudo:
            print("  (requires sudo)")

        try:
            step.run(cfg)
        except Exception as e:
            print_fail(str(e))
            failed.append(step.name)
            if not confirm("Continue to next step?", True, auto_yes):
                break

    # Summary
    print()
    print("=" * 60)
    if not failed:
        print("  Setup complete!")
    else:
        print(f"  Setup completed with {len(failed)} issue(s): {', '.join(failed)}")
        print("  Run 'dtn init' again to retry failed steps.")
    print()
    print("  Your node: ipn:" + cfg["ipn"])
    print()
    print("  Useful commands:")
    print("    dtn status      — check node and service status")
    print("    dtn neighbors   — list configured neighbors")
    print("    dtn nodes       — list all nodes in the network")
    print("    dtn discover    — discover DTN nodes")
    print("    dtn trace <IPN> — trace route to a node")
    print("    dtn send <IPN> \"message\" — send a bundle")
    print()
    print("  To add a neighbor:")
    print("    dtn neighbors add <IPN> <IP>")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DTN Node Setup Wizard")
    parser.add_argument("--ipn", help="IPN node number")
    parser.add_argument("--name", help="Node name")
    parser.add_argument("--email", help="Contact email")
    parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive (accept defaults)")
    parser.add_argument("--skip-build", action="store_true", help="Skip ION/dtnex compilation")
    parser.add_argument("--skip-services", action="store_true", help="Skip systemd service setup")
    parser.add_argument("--gateway-ip", help="Gateway Tailscale IP")
    parser.add_argument("--dtn-dir", help="DTN working directory")
    parser.add_argument("--location", help="Node location")
    parser.add_argument("--lat", help="GPS latitude")
    parser.add_argument("--lon", help="GPS longitude")
    run_init(parser.parse_args())
