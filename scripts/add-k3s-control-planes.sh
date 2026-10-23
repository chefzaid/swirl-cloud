#!/bin/bash
set -euo pipefail

# Shares worker enrollment's networking, SSH and credential handling; the
# control-plane role selects server joins and etcd ports.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K3S_ENROLLMENT_ROLE=control-plane exec "$SCRIPT_DIR/add-k3s-workers.sh" "$@"
