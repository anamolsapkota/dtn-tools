#!/bin/bash
set -e

# dtn-tools installer
# Installs the dtn CLI and supporting modules

INSTALL_DIR="/usr/local/lib/dtn-tools"
BIN_DIR="/usr/local/bin"

echo "Installing dtn-tools..."

# Check for Python 3
if ! command -v python3 &>/dev/null; then
    echo "Error: Python 3 is required."
    echo "Install with: sudo apt install python3 python3-pip"
    exit 1
fi

# Check Python version
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python version: $PYVER"

# Check for requests module
if ! python3 -c "import requests" 2>/dev/null; then
    echo "Installing Python requests module..."
    pip3 install requests 2>/dev/null || sudo pip3 install requests 2>/dev/null || {
        echo "Warning: Could not install 'requests'. Discovery may not work."
        echo "Install manually: pip3 install requests"
    }
fi

# Create installation directory
sudo mkdir -p "$INSTALL_DIR"

# Copy main CLI
sudo cp dtn "$BIN_DIR/dtn"
sudo chmod +x "$BIN_DIR/dtn"

# Copy Python modules
sudo cp -r dtn_tools/ "$INSTALL_DIR/"

# Copy examples
sudo mkdir -p "$INSTALL_DIR/examples"
if [ -d examples ]; then
    sudo cp -r examples/ "$INSTALL_DIR/"
fi

echo ""
echo "Installed:"
echo "  CLI:     $BIN_DIR/dtn"
echo "  Modules: $INSTALL_DIR/"
echo ""
echo "Usage:"
echo "  dtn init          # Setup a new DTN node"
echo "  dtn status        # Check node status"
echo "  dtn neighbors     # List neighbors"
echo "  dtn discover      # Discover nodes"
echo ""
echo "Run 'dtn --help' for all commands."
