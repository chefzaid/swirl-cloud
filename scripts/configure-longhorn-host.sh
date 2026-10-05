#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_SOURCE="$SCRIPT_DIR/../config/host/multipath-longhorn.conf"
CONFIG_TARGET="/etc/multipath.conf"

[[ -f "$CONFIG_SOURCE" ]] || { echo "Missing $CONFIG_SOURCE" >&2; exit 1; }

if command -v multipathd >/dev/null 2>&1; then
  sudo install -o root -g root -m 0644 "$CONFIG_SOURCE" "$CONFIG_TARGET"
  sudo systemctl reload multipathd 2>/dev/null || sudo multipathd reconfigure
fi

# Encrypted volumes (StorageClass longhorn-encrypted) use dm-crypt.
echo dm_crypt | sudo tee /etc/modules-load.d/swirl-cloud-longhorn.conf >/dev/null
sudo modprobe dm_crypt
command -v cryptsetup >/dev/null 2>&1 || echo "Install cryptsetup before using encrypted Longhorn volumes." >&2
echo "Longhorn host storage prerequisites configured."
