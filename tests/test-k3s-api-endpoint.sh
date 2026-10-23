#!/usr/bin/env bash
# Guard the floating Kubernetes API endpoint without a running cluster: a VIP
# that a node could own, a VIP held by a server whose API is down, multicast
# VRRP leaking onto the node network, and a certificate restart that would
# break the etcd majority.
# Mocks are called from the sourced production helpers, which ShellCheck 0.10
# reports as SC2317 and 0.11 as SC2329.
# shellcheck disable=SC2317,SC2329
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/network.sh
source "$SCRIPT_DIR/lib/network.sh"
# shellcheck source=../scripts/configure-k3s-api-endpoint.sh
source "$SCRIPT_DIR/configure-k3s-api-endpoint.sh"
# shellcheck source=../scripts/lib/control-plane-restart.sh
source "$SCRIPT_DIR/lib/control-plane-restart.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

nodes='10.40.0.10,10.40.0.11,10.40.0.12'
validate_vip 10.40.0.5 10.40.0.0/24 "$nodes" >/dev/null || fail 'A free node-network address was rejected'
for vip in 10.40.0.11 10.41.0.5 10.40.0.0 10.40.0.255 100.70.0.5 203.0.113.5; do
    ! validate_vip "$vip" 10.40.0.0/24 "$nodes" >/dev/null || fail "Unsafe API VIP $vip was accepted"
done

config="$(render_keepalived_config cp-01 eno2 10.40.0.10 10.40.0.5 24 10.40.0.11,10.40.0.12)"
peers="$(awk '/unicast_peer \{/ {inside=1; next} inside && /\}/ {inside=0} inside {print $1}' <<< "$config" | paste -sd, -)"
[[ "$peers" == 10.40.0.11,10.40.0.12 ]] || fail "VRRP peers are wrong: $peers"
grep -qx '    unicast_fault_no_peer' <<< "$config" || fail 'A peerless instance could fall back to multicast VRRP'
if ! grep -qx '        k3s_api_ready' <<< "$config" || ! grep -Fq "script \"$READY_CHECK\"" <<< "$config"; then
    fail 'The VIP is not gated on the local API readiness check'
fi
grep -qx '        10.40.0.5/24 dev eno2' <<< "$config" || fail 'The VIP is not bound to the private interface'
for vip in 10.40.0.5 192.168.255.254 172.16.0.255; do
    id="$(vrrp_router_id "$vip")"
    (( id >= 1 && id <= 255 )) || fail "VRRP router ID $id for $vip is outside 1-255"
done

# Three control planes; cp-02 has stopped renewing its lease. Restarting cp-03
# would leave a single fresh member, so the certificate restart must be refused.
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
stale="$(date -u -d '-10 minutes' +%Y-%m-%dT%H:%M:%SZ)"
node() { printf '{"metadata":{"name":"%s","uid":"u-%s","labels":{"node-role.kubernetes.io/control-plane":"true"}},"status":{"conditions":[{"type":"Ready","status":"%s"}]}}' "$1" "$1" "$2"; }
lease() { printf '{"metadata":{"name":"%s","ownerReferences":[{"kind":"Node","uid":"u-%s"}]},"spec":{"holderIdentity":"%s","renewTime":"%s"}}' "$1" "$1" "$1" "$2"; }
MOCK_NODES="{\"items\":[$(node cp-01 True),$(node cp-02 True),$(node cp-03 True)]}"
MOCK_LEASES="{\"items\":[$(lease cp-01 "$now"),$(lease cp-02 "$stale"),$(lease cp-03 "$now")]}"
kubectl() {
    case "$*" in
        *'get nodes'*) printf '%s\n' "$MOCK_NODES" ;;
        *'get leases'*) printf '%s\n' "$MOCK_LEASES" ;;
        *) return 1 ;;
    esac
}
fresh_majority '' 2>/dev/null || fail 'Two fresh members of three are a majority'
! fresh_majority cp-03 2>/dev/null || fail 'A restart that leaves one fresh member of three was allowed'
MOCK_LEASES="{\"items\":[$(lease cp-01 "$now"),$(lease cp-02 "$now"),$(lease cp-03 "$now")]}"
fresh_majority cp-03 2>/dev/null || fail 'A restart that keeps two fresh members was refused'
MOCK_NODES="{\"items\":[$(node cp-01 True),$(node cp-02 True)]}"
! fresh_majority '' 2>/dev/null || fail 'An even control-plane membership was accepted'

printf '[PASS] API VIP placement, health-gated unicast VRRP and etcd-majority restart guard\n'
