#!/bin/bash
# Updates dtnex.conf nodemetadata with live system stats.
# Runs in a loop, updating every 30 minutes (matches dtnex updateInterval).
# Install via: dtn install metadata-updater

DTN_DIR="${DTN_DIR:-$HOME/dtn}"
CONF="$DTN_DIR/dtnex.conf"

if [ ! -f "$CONF" ]; then
    echo "dtnex.conf not found at $CONF"
    exit 1
fi

# Extract the static prefix from existing metadata (name,email,location)
# Format: nodemetadata="name,email,location <dynamic stats>"
EXISTING=$(grep '^nodemetadata=' "$CONF" | sed 's/^nodemetadata="//' | sed 's/"$//')
# Get the static part (up to first space or end if no space after location)
# We preserve everything before the first system-stat keyword
STATIC=$(echo "$EXISTING" | sed 's/ *[0-9.]*C up:.*//; s/ *RPi[0-9]* .*//; s/ *x86_64 .*//; s/ *aarch64 .*//')
if [ -z "$STATIC" ]; then
    STATIC="$EXISTING"
fi

ARCH=$(uname -m)

while true; do
    # CPU temperature (Linux thermal zone)
    TEMP="N/A"
    if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
        TEMP=$(awk '{printf "%.1f", $1/1000}' /sys/class/thermal/thermal_zone0/temp 2>/dev/null)
    fi

    UPDAYS=$(awk '{printf "%dd", $1/86400}' /proc/uptime 2>/dev/null || echo "?d")
    MEM_USED=$(free -m 2>/dev/null | awk '/Mem:/{printf "%d/%dMB", $3, $2}' || echo "?MB")
    DISK_USED=$(df -h / 2>/dev/null | awk 'NR==2{print $5}' || echo "?%")
    LOAD=$(awk '{print $1}' /proc/loadavg 2>/dev/null || echo "?")

    META="${STATIC} ${ARCH} ${TEMP}C up:${UPDAYS} mem:${MEM_USED} disk:${DISK_USED} load:${LOAD}"

    sed -i "s|^nodemetadata=.*|nodemetadata=\"${META}\"|" "$CONF"

    sleep 1800
done
