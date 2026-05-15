"""DTN package manager — install/uninstall optional DTN modules."""

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Package registry
# ---------------------------------------------------------------------------

PACKAGES = {
    "dtn-chat": {
        "description": "Terminal chat TUI (urwid-based interactive chat over DTN)",
        "pip_deps": ["urwid"],
        "files": {},  # chat.py and chat_tui.py are already in dtn_tools/
        "systemd_service": None,  # TUI is interactive, not a daemon
    },
    "metadata-updater": {
        "description": "Auto-update dtnex.conf with live system stats (CPU, RAM, disk, uptime)",
        "pip_deps": [],
        "files": {
            "scripts/dtn-metadata-updater.sh": "scripts/dtn-metadata-updater.sh",
        },
        "systemd_service": "dtn-metadata-updater",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 1


def _pip_install(packages):
    """Install pip packages, handling --break-system-packages for newer pip."""
    if not packages:
        return True
    pkg_str = " ".join(packages)
    # Try normal install first, then with --break-system-packages
    _, _, rc = _run(f"pip3 install {pkg_str} 2>/dev/null")
    if rc != 0:
        _, err, rc = _run(f"pip3 install --break-system-packages {pkg_str}")
        if rc != 0:
            # Try with sudo
            _, _, rc = _run(f"sudo pip3 install {pkg_str} 2>/dev/null")
            if rc != 0:
                _, err, rc = _run(f"sudo pip3 install --break-system-packages {pkg_str}")
                if rc != 0:
                    print(f"  ERROR: Failed to install {pkg_str}")
                    print(f"  Try manually: pip3 install {pkg_str}")
                    return False
    return True


def _check_pip_dep(dep):
    """Check if a pip package is importable."""
    try:
        __import__(dep)
        return True
    except ImportError:
        return False


def _get_script_dir():
    """Get the dtn-tools repo root directory."""
    return str(Path(__file__).resolve().parent.parent)


def _install_systemd_service(name, dtn_dir, ipn):
    """Create and install a systemd service unit."""
    user = getpass.getuser()

    templates = {
        "dtn-metadata-updater": f"""[Unit]
Description=DTN Metadata Updater for ipn:{ipn}
After=dtnex.service
Wants=dtnex.service

[Service]
Type=simple
User={user}
WorkingDirectory={dtn_dir}
Environment=HOME={os.path.expanduser('~')}
Environment=DTN_DIR={dtn_dir}
ExecStart=/bin/bash {dtn_dir}/scripts/dtn-metadata-updater.sh
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target""",
    }

    template = templates.get(name)
    if not template:
        return False

    unit_path = f"/etc/systemd/system/{name}.service"
    tmp_path = f"/tmp/{name}.service"

    # Write to tmp, then sudo copy
    with open(tmp_path, "w") as f:
        f.write(template)

    _, _, rc = _run(f"sudo cp {tmp_path} {unit_path}")
    if rc != 0:
        print(f"  WARNING: Could not install {name}.service (need sudo)")
        print(f"  Copy manually: sudo cp {tmp_path} {unit_path}")
        return False

    os.remove(tmp_path)
    _run("sudo systemctl daemon-reload")
    _run(f"sudo systemctl enable {name}")
    print(f"  Installed {name}.service (enabled)")
    return True


def _remove_systemd_service(name):
    """Stop, disable, and remove a systemd service."""
    unit_path = f"/etc/systemd/system/{name}.service"
    if not os.path.exists(unit_path):
        return

    _run(f"sudo systemctl stop {name} 2>/dev/null")
    _run(f"sudo systemctl disable {name} 2>/dev/null")
    _run(f"sudo rm -f {unit_path}")
    _run("sudo systemctl daemon-reload")
    print(f"  Removed {name}.service")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_packages(dtn_dir):
    """List all available packages and their install status."""
    print("Available DTN packages:\n")
    print(f"  {'Package':<22} {'Status':<12} {'Description'}")
    print(f"  {'-'*22} {'-'*12} {'-'*45}")

    for name, pkg in PACKAGES.items():
        installed = is_installed(name, dtn_dir)
        status = "installed" if installed else "not installed"
        print(f"  {name:<22} {status:<12} {pkg['description']}")

    print(f"\nUsage: dtn install <package>")
    print(f"       dtn uninstall <package>")


def is_installed(name, dtn_dir):
    """Check if a package is installed."""
    pkg = PACKAGES.get(name)
    if not pkg:
        return False

    # Check pip deps
    for dep in pkg["pip_deps"]:
        if not _check_pip_dep(dep):
            return False

    # Check files
    for _, dest in pkg["files"].items():
        dest_path = os.path.join(dtn_dir, dest)
        if not os.path.exists(dest_path):
            return False

    # Check systemd service
    if pkg["systemd_service"]:
        unit = f"/etc/systemd/system/{pkg['systemd_service']}.service"
        if not os.path.exists(unit):
            return False

    return True


def install_package(name, dtn_dir, ipn, script_dir=None):
    """Install a DTN package."""
    if name not in PACKAGES:
        print(f"Unknown package: {name}")
        print(f"Available: {', '.join(PACKAGES.keys())}")
        return False

    pkg = PACKAGES[name]
    if script_dir is None:
        script_dir = _get_script_dir()

    print(f"Installing {name}: {pkg['description']}")

    # 1. Install pip dependencies
    if pkg["pip_deps"]:
        missing = [d for d in pkg["pip_deps"] if not _check_pip_dep(d)]
        if missing:
            print(f"  Installing Python packages: {', '.join(missing)}")
            if not _pip_install(missing):
                return False
            print(f"  Python packages installed")
        else:
            print(f"  Python packages already installed: {', '.join(pkg['pip_deps'])}")

    # 2. Copy files from repo to DTN_DIR
    for src_rel, dest_rel in pkg["files"].items():
        src_path = os.path.join(script_dir, src_rel)
        dest_path = os.path.join(dtn_dir, dest_rel)

        if not os.path.exists(src_path):
            print(f"  WARNING: Source file not found: {src_path}")
            continue

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        if os.path.isdir(src_path):
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)
            shutil.copytree(src_path, dest_path)
            print(f"  Copied {src_rel}/ -> {dest_path}/")
        else:
            shutil.copy2(src_path, dest_path)
            # Make shell scripts executable
            if dest_path.endswith(".sh"):
                os.chmod(dest_path, 0o755)
            print(f"  Copied {src_rel} -> {dest_path}")

    # 3. Install systemd service
    if pkg["systemd_service"]:
        unit = f"/etc/systemd/system/{pkg['systemd_service']}.service"
        if os.path.exists(unit):
            print(f"  {pkg['systemd_service']}.service already installed")
        else:
            _install_systemd_service(pkg["systemd_service"], dtn_dir, ipn)

    print(f"\n  {name} installed successfully!")

    # Post-install hints
    if name == "dtn-chat":
        print(f"  Run: dtn chat")
    elif name == "metadata-updater":
        print(f"  Start: dtn start metadata")
        print(f"  Check: dtn logs metadata")

    return True


def uninstall_package(name, dtn_dir):
    """Uninstall a DTN package."""
    if name not in PACKAGES:
        print(f"Unknown package: {name}")
        print(f"Available: {', '.join(PACKAGES.keys())}")
        return False

    pkg = PACKAGES[name]
    print(f"Uninstalling {name}...")

    # 1. Remove systemd service
    if pkg["systemd_service"]:
        _remove_systemd_service(pkg["systemd_service"])

    # 2. Remove copied files
    for _, dest_rel in pkg["files"].items():
        dest_path = os.path.join(dtn_dir, dest_rel)
        if os.path.exists(dest_path):
            if os.path.isdir(dest_path):
                shutil.rmtree(dest_path)
            else:
                os.remove(dest_path)
            print(f"  Removed {dest_path}")

    # Note: pip packages are NOT uninstalled (may be shared)
    if pkg["pip_deps"]:
        print(f"  Note: Python packages kept ({', '.join(pkg['pip_deps'])})")
        print(f"  To remove: pip3 uninstall {' '.join(pkg['pip_deps'])}")

    print(f"  {name} uninstalled.")
    return True
