#!/usr/bin/env bash
# Guard control-plane enrollment's plan without a cluster: the final server count
# stays odd for etcd quorum and matches the expected count, a host already
# registered as a worker or listed twice is refused, the bootstrap server cannot
# enroll itself, targets stay inside the node network, and the management
# sources come only from control-plane InternalIPs inside that network.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/network.sh
source "$SCRIPT_DIR/lib/network.sh"
# shellcheck source=../scripts/lib/control-plane-access.sh
source "$SCRIPT_DIR/lib/control-plane-access.sh"
# shellcheck source=../scripts/lib/node-enrollment.sh
source "$SCRIPT_DIR/lib/node-enrollment.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
error() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }

node() { # node NAME IP ROLE
    local labels='{}'
    [[ "$3" == worker ]] || labels='{"node-role.kubernetes.io/control-plane":"true"}'
    printf '{"metadata":{"name":"%s","labels":%s},"status":{"addresses":[{"type":"InternalIP","address":"%s"}]}}' "$1" "$labels" "$2"
}
NODES=""
kubectl() { printf '{"items":[%s]}' "$NODES"; }
plan() {
    (
        ENROLLMENT_ROLE=control-plane NON_INTERACTIVE=true NODE_TRANSPORT=vrack WORKER_IPS="" WORKER_HOSTS=""
        REQUESTED_WORKER_COUNT="" EXPECTED_CONTROL_PLANE_COUNT="" NODE_NETWORK_CIDR=10.0.0.0/24 CONTROL_PLANE_IP=10.0.0.1 worker_count=0
        local assignment
        for assignment in "$@"; do
            declare "$assignment"
        done
        preflight_control_plane_count
        printf '%s\n' "$EXPECTED_CONTROL_PLANE_COUNT"
    ) 2>&1
}
expect() {
    local wanted="$1" result
    shift
    result="$(plan "$@")" || fail "plan $* failed: $result"
    [[ "$result" == "$wanted" ]] || fail "plan $* gave '$result', expected '$wanted'"
}
refuse() {
    local message="$1" result
    shift
    if result="$(plan "$@")"; then fail "plan $* was accepted: $result"; fi
    [[ "$result" == *"$message"* ]] || fail "plan $* failed with '$result', expected '$message'"
}

NODES="$(node cp1 10.0.0.1 control-plane)"
expect 3 WORKER_IPS=10.0.0.2,10.0.0.3
expect 3 "WORKER_IPS=10.0.0.2, 10.0.0.3" EXPECTED_CONTROL_PLANE_COUNT=3 REQUESTED_WORKER_COUNT=2
refuse "would leave 2 control planes" WORKER_IPS=10.0.0.2
refuse "produce 3 control planes, but 5 were requested" WORKER_IPS=10.0.0.2,10.0.0.3 EXPECTED_CONTROL_PLANE_COUNT=5
refuse "positive odd integer" WORKER_IPS=10.0.0.2,10.0.0.3 EXPECTED_CONTROL_PLANE_COUNT=4
refuse "must contain exactly 3 entries" WORKER_IPS=10.0.0.2,10.0.0.3 REQUESTED_WORKER_COUNT=3
refuse "empty item" WORKER_IPS=10.0.0.2,,10.0.0.3
refuse "Duplicate control-plane enrollment target: 10.0.0.2" WORKER_IPS=10.0.0.2,10.0.0.2
refuse "cannot enroll itself" WORKER_IPS=10.0.0.1,10.0.0.2
refuse "outside 10.0.0.0/24" WORKER_IPS=10.0.0.2,10.0.1.3
refuse "Invalid vrack control-plane IP: 203.0.113.4" WORKER_IPS=10.0.0.2,203.0.113.4
refuse "Invalid lan control-plane IP: 100.64.0.2" NODE_TRANSPORT=lan WORKER_IPS=10.0.0.2,100.64.0.2
expect "" ENROLLMENT_ROLE=worker WORKER_IPS=10.0.0.2 # workers have no quorum to keep
expect 3 NON_INTERACTIVE=false worker_count=2
refuse "would leave 2 control planes" NON_INTERACTIVE=false worker_count=1

# A rerun after a partial enrollment counts only the hosts not yet joined.
NODES="$(node cp1 10.0.0.1 control-plane),$(node cp2 10.0.0.2 control-plane),$(node w1 10.0.0.9 worker)"
expect 3 WORKER_IPS=10.0.0.2,10.0.0.3
refuse "already registered as a worker" WORKER_IPS=10.0.0.3,10.0.0.9

# Management sources: control-plane InternalIPs only, inside the node network.
NODES_JSON="{\"items\":[$(node cp1 10.0.0.1 control-plane),$(node cp2 10.0.0.2 control-plane),$(node w1 10.0.0.9 worker)]}"
[[ "$(control_plane_private_ips "$NODES_JSON" 10.0.0.0/24)" == "10.0.0.1,10.0.0.2" ]] || fail 'Workers or duplicates become management sources'
if control_plane_private_ips "$NODES_JSON" 10.0.0.0/31 >/dev/null; then fail 'A control plane outside the node network is accepted'; fi
if control_plane_private_ips "$NODES_JSON" 203.0.113.0/24 >/dev/null; then fail 'A public node network is accepted'; fi
if control_plane_private_ips "{\"items\":[$(node w1 10.0.0.9 worker)]}" 10.0.0.0/24 >/dev/null 2>&1; then
    fail 'A cluster without control planes yields management sources'
fi
control_plane_address_member 10.0.0.1,10.0.0.2 10.0.0.2 || fail 'A listed control plane is not a member'
if control_plane_address_member 10.0.0.1,10.0.0.22 10.0.0.2; then fail 'A prefix of a listed address is a member'; fi

printf '[PASS] Control-plane enrollment plan\n'
