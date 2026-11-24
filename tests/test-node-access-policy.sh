#!/usr/bin/env bash
# Guard the private-node access policy without a host: the control-plane SSH
# allowlist stays inside the node network and always keeps the current source,
# a worker's firewall is only closed from a proven private SSH session on the
# private interface, a public default route never carries node traffic, and a
# Tailscale node must route to the control plane through tailscale0.
# The stubs below replace commands that the library runs through sudo.
# shellcheck disable=SC2032
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/network.sh
source "$SCRIPT_DIR/lib/network.sh"
# shellcheck source=../scripts/lib/node-access-policy.sh
source "$SCRIPT_DIR/lib/node-access-policy.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
err() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
CONTROL_PLANE_SSH_STATE="$STATE/control-plane-ssh"
SAVED=""
sudo() {
    case "$1" in
        test) [[ -n "$SAVED" ]] ;;
        cat) printf '%s\n' "$SAVED" ;;
        tailscale) shift; tailscale "$@" ;;
        *) "$@" ;;
    esac
}
ROUTE="10.0.0.1 dev ens4 src 10.0.0.5 uid 0"
DEFAULT_ROUTE="1.1.1.1 via 203.0.113.1 dev ens3 src 203.0.113.5 uid 0"
ADDRESSES=$'2: ens3    inet 203.0.113.5/24 brd x scope global ens3\n3: ens4    inet 10.0.0.5/24 brd x scope global ens4'
ip() {
    case "$*" in
        "-4 route get 1.1.1.1") printf '%s\n' "$DEFAULT_ROUTE" ;;
        "-4 route get "*) [[ -n "$ROUTE" ]] && printf '%s\n' "$ROUTE" ;;
        "-4 route show default") printf 'default via 203.0.113.1 dev %s\n' "${DEFAULT_INTERFACE:-ens3}" ;;
        "-4 -o address show scope global") printf '%s\n' "$ADDRESSES" ;;
        *) return 1 ;;
    esac
}
TAILSCALE_IP=100.64.0.5
tailscale() {
    case "$*" in
        status) return 0 ;;
        "ip -4") printf '%s\n' "$TAILSCALE_IP" ;;
    esac
}

base() {
    NODE_ROLE=worker PRIVATE_CONTROL_PLANE=false K3S_NODE_NETWORK_CIDR=10.0.0.0/24 CONTROL_PLANE_IP=10.0.0.1
    CONTROL_PLANE_SSH_IPS="" HARDENED_SSH_PORT=22 SSH_CONNECTION="10.0.0.1 50000 10.0.0.5 22" K3S_PRIVATE_ADDRESS=""
    INHERITED_PRIVATE_CONTROL_PLANE=false
}
# outcome FUNCTION VAR=value...: run it in a subshell; print CONTROL_PLANE_SSH_IPS or the error.
outcome() {
    local function="$1"
    shift
    (
        base
        local assignment
        for assignment in "$@"; do
            declare "$assignment"
        done
        "$function"
        printf '%s\n' "$CONTROL_PLANE_SSH_IPS"
    ) 2>&1
}
expect() {
    local wanted="$1" result
    shift
    result="$(outcome "$@")" || fail "$* failed: $result"
    [[ "$result" == "$wanted" ]] || fail "$* gave '$result', expected '$wanted'"
}
refuse() {
    local message="$1" result
    shift
    if result="$(outcome "$@")"; then
        fail "$* was accepted"
    fi
    [[ "$result" == *"$message"* ]] || fail "$* failed with '$result', expected '$message'"
}

# Roles.
[[ "$(normalize_node_role Server)" == control-plane && "$(normalize_node_role agent)" == worker ]] || fail 'Role aliases'
if normalize_node_role etcd >/dev/null; then fail 'An unknown role is accepted'; fi

# The SSH allowlist: the current source by default, sorted and unique, inside the node network.
expect "10.0.0.1" prepare_control_plane_ssh_policy
expect "10.0.0.1,10.0.0.2,10.0.0.3" prepare_control_plane_ssh_policy CONTROL_PLANE_SSH_IPS=10.0.0.3,10.0.0.1,10.0.0.2,10.0.0.3
SAVED=$'10.0.0.2\n10.0.0.1'
expect "10.0.0.1,10.0.0.2" prepare_control_plane_ssh_policy
SAVED=""
refuse "outside the private node network: 10.0.1.9" prepare_control_plane_ssh_policy CONTROL_PLANE_SSH_IPS=10.0.0.1,10.0.1.9
refuse "outside the private node network: 198.51.100.4" prepare_control_plane_ssh_policy CONTROL_PLANE_SSH_IPS=10.0.0.1,198.51.100.4
refuse "non-empty comma-separated list" prepare_control_plane_ssh_policy CONTROL_PLANE_SSH_IPS=10.0.0.1,,10.0.0.2
refuse "must remain in the control-plane allowlist" prepare_control_plane_ssh_policy CONTROL_PLANE_SSH_IPS=10.0.0.2
expect "" prepare_control_plane_ssh_policy NODE_ROLE=control-plane # a public server keeps no private allowlist

# The worker firewall closes only from a proven private SSH session.
expect "" validate_worker_private_ssh_before_firewall
refuse "without a proven private SSH session" validate_worker_private_ssh_before_firewall SSH_CONNECTION=
refuse "current SSH client 10.0.0.9 is not control plane 10.0.0.1" validate_worker_private_ssh_before_firewall \
    "SSH_CONNECTION=10.0.0.9 50000 10.0.0.5 22"
refuse "current SSH endpoint 203.0.113.5 is not private worker address 10.0.0.5" validate_worker_private_ssh_before_firewall \
    "SSH_CONNECTION=10.0.0.1 50000 203.0.113.5 22"
refuse "current SSH port 2222 differs from configured port 22" validate_worker_private_ssh_before_firewall \
    "SSH_CONNECTION=10.0.0.1 50000 10.0.0.5 2222"
ROUTE="10.0.0.1 dev ens5 src 10.0.0.5 uid 0"
refuse "current SSH endpoint is not on private interface ens5" validate_worker_private_ssh_before_firewall
ROUTE="10.0.0.1 dev ens3 src 10.0.0.5 uid 0"
refuse "uses public default interface ens3" validate_worker_private_ssh_before_firewall
ROUTE="10.0.0.1 via 203.0.113.1 dev ens3 src 203.0.113.5 uid 0"
refuse "non-private source 203.0.113.5" validate_worker_private_ssh_before_firewall
ROUTE=""
refuse "not routable" validate_worker_private_ssh_before_firewall
ROUTE="10.0.0.1 dev ens4 src 10.0.1.5 uid 0"
refuse "route source 10.0.1.5 is outside 10.0.0.0/24" validate_worker_private_ssh_before_firewall
ROUTE="10.0.0.1 dev ens4 src 10.0.0.5 uid 0"
expect "" validate_worker_private_ssh_before_firewall NODE_ROLE=control-plane # nothing to prove on a public server
# A console run on an inherited private control plane keeps its validated policy.
expect "" validate_worker_private_ssh_before_firewall NODE_ROLE=control-plane PRIVATE_CONTROL_PLANE=true \
    INHERITED_PRIVATE_CONTROL_PLANE=true SSH_CONNECTION= K3S_PRIVATE_ADDRESS=10.0.0.5
refuse "lost its validated local interface" validate_worker_private_ssh_before_firewall NODE_ROLE=control-plane \
    PRIVATE_CONTROL_PLANE=true INHERITED_PRIVATE_CONTROL_PLANE=true SSH_CONNECTION= K3S_PRIVATE_ADDRESS=10.0.0.77

# Tailscale: the control plane is reached through tailscale0 from this node's tailnet address.
tailscale_transport_selected() { [[ "$K3S_NODE_NETWORK_CIDR" == 100.64.0.0/10 ]]; }
command() { [[ "$*" != "-v tailscale" ]] || return 0; builtin command "$@"; }
ROUTE="100.64.0.1 dev tailscale0 src 100.64.0.5 uid 0"
expect "" configure_tailscale_firewall_integration K3S_NODE_NETWORK_CIDR=100.64.0.0/10 CONTROL_PLANE_IP=100.64.0.1
refuse "must be a Tailscale IPv4" configure_tailscale_firewall_integration K3S_NODE_NETWORK_CIDR=100.64.0.0/10 CONTROL_PLANE_IP=10.0.0.1
ROUTE="100.64.0.1 dev ens4 src 10.0.0.5 uid 0"
refuse "uses ens4, not tailscale0" configure_tailscale_firewall_integration K3S_NODE_NETWORK_CIDR=100.64.0.0/10 CONTROL_PLANE_IP=100.64.0.1
ROUTE="100.64.0.1 dev tailscale0 src 100.64.0.9 uid 0"
refuse "does not match Tailscale IP 100.64.0.5" configure_tailscale_firewall_integration K3S_NODE_NETWORK_CIDR=100.64.0.0/10 \
    CONTROL_PLANE_IP=100.64.0.1
TAILSCALE_IP=""
refuse "has not assigned this node an IPv4 address" configure_tailscale_firewall_integration K3S_NODE_NETWORK_CIDR=100.64.0.0/10 \
    CONTROL_PLANE_IP=100.64.0.1
expect "" configure_tailscale_firewall_integration # no Tailscale transport: nothing to check

printf '[PASS] Node access policy\n'
