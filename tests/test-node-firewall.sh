#!/usr/bin/env bash
# Guard the node firewall without a host: a public control plane opens SSH and
# 80/443 (only to Cloudflare's networks when proxied, after the list downloads,
# so a failed download never leaves UFW reset), a private node admits SSH only
# from the verified control planes and drops provider ingress on its public
# interfaces, cluster ports stay on the node network, and a UFW that does not
# come up leaves only SSH, loopback and cluster interfaces open.
# The stubs below replace commands that the library runs through sudo.
# shellcheck disable=SC2032
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/network.sh
source "$SCRIPT_DIR/lib/network.sh"
# shellcheck source=../scripts/lib/node-access-policy.sh
source "$SCRIPT_DIR/lib/node-access-policy.sh"
# shellcheck source=../scripts/lib/node-firewall.sh
source "$SCRIPT_DIR/lib/node-firewall.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
err() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }
warn() { :; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
LOG="$STATE/log"

ensure_packages() { :; }
write_root_file() { cat > /dev/null; }
configure_worker_forwarding_guard() { printf 'guard %s\n' "$*" >> "$LOG"; }
# Every privileged command is recorded; /etc/default/ufw is a scratch copy.
sudo() {
    local arguments=("${@//\/etc\/default\/ufw/$STATE/default-ufw}")
    case "${arguments[0]}" in
        ufw|iptables|ip6tables|systemctl|/lib/ufw/ufw-init) printf '%s\n' "${arguments[*]}" >> "$LOG"; "${arguments[@]}" ;;
        test) return 1 ;;
        *) "${arguments[@]}" ;;
    esac
}
ufw() {
    case "$*" in
        "--force enable") [[ "$UFW_ENABLES" == true ]] ;;
        status) [[ "$UFW_ENABLES" == true ]] && printf 'Status: active\n' || printf 'Status: inactive\n' ;;
        *) return 0 ;;
    esac
}
iptables() { [[ "$*" != "-S INPUT" ]] || printf -- '-P INPUT DROP\n'; }
ip6tables() { iptables "$@"; }
/lib/ufw/ufw-init() { :; }
ip() {
    case "$*" in
        "-4 route get 10.0.0.1") printf '10.0.0.1 dev ens4 src 10.0.0.5 uid 0\n' ;;
        "-4 route get 1.1.1.1") printf '1.1.1.1 via 203.0.113.1 dev ens3 src 203.0.113.5 uid 0\n' ;;
        "-4 -o address show scope global")
            printf '2: ens3    inet 203.0.113.5/24 brd x scope global ens3\n3: ens4    inet 10.0.0.5/24 brd x scope global ens4\n'
            printf '4: cni0    inet 10.42.0.1/24 brd x scope global cni0\n5: tailscale0    inet 100.64.0.5/32 scope global tailscale0\n'
            printf '6: flannel.1    inet 10.42.0.0/32 scope global flannel.1\n' ;;
        "-6 -o address show scope global") printf '2: ens3    inet6 2001:db8::5/64 scope global\n' ;;
        "link show cni0") return 0 ;;
        *) return 1 ;;
    esac
}
CLOUDFLARE_UP=true
curl() {
    [[ "$CLOUDFLARE_UP" == true ]] || return 22
    printf '{"success":true,"result":{"ipv4_cidrs":["173.245.48.0/20","104.16.0.0/13"],"ipv6_cidrs":["2400:cb00::/32"]}}'
}

run() {
    : > "$LOG"
    printf 'IPV6=no\nDEFAULT_FORWARD_POLICY="DROP"\n' > "$STATE/default-ufw"
    (
        NODE_ROLE=control-plane PRIVATE_CONTROL_PLANE=false SERVER_EXPOSURE=internet CLOUDFLARE_PROXY_ONLY=""
        K3S_NODE_NETWORK_CIDR="" CONTROL_PLANE_IP=10.0.0.1 CONTROL_PLANE_SSH_IPS=10.0.0.1 HARDENED_SSH_PORT=22 UFW_ENABLES=true
        local assignment
        for assignment in "$@"; do
            declare "$assignment"
        done
        configure_ufw
    ) 2> "$STATE/error"
}
has() { grep -Fqx -- "$1" "$LOG" || fail "missing: $1"$'\n'"$(cat "$LOG")"; }
lacks() { ! grep -Fq -- "$1" "$LOG" || fail "unexpected: $1"$'\n'"$(cat "$LOG")"; }

# A public control plane behind Cloudflare's proxy.
run CLOUDFLARE_PROXY_ONLY=true || fail "Public control plane: $(cat "$STATE/error")"
has "ufw --force reset"
has "ufw default deny incoming"
has "ufw allow 22/tcp"
has "ufw allow from 104.16.0.0/13 to any port 443 proto tcp comment Cloudflare proxy"
has "ufw allow from 2400:cb00::/32 to any port 80 proto tcp comment Cloudflare proxy"
lacks "ufw allow 443/tcp"
lacks "6443"
has "ufw allow in on cni0"
grep -qx 'IPV6=yes' "$STATE/default-ufw" || fail 'IPv6 filtering is not enabled'
grep -qx 'DEFAULT_FORWARD_POLICY="ACCEPT"' "$STATE/default-ufw" || fail 'Forwarding stays dropped for pods'

# The Cloudflare list is fetched before UFW is reset: a failed download changes nothing.
CLOUDFLARE_UP=false
if run CLOUDFLARE_PROXY_ONLY=true; then fail 'A failed Cloudflare download is accepted'; fi
grep -Fq "Unable to download Cloudflare proxy networks" "$STATE/error" || fail "Unexpected error: $(cat "$STATE/error")"
lacks "ufw --force reset"
CLOUDFLARE_UP=true

# Local exposure serves 80/443 to everyone, without asking Cloudflare.
CLOUDFLARE_UP=false
run SERVER_EXPOSURE=local CLOUDFLARE_PROXY_ONLY=true || fail "Local exposure: $(cat "$STATE/error")"
has "ufw allow 443/tcp"
lacks "Cloudflare proxy"
CLOUDFLARE_UP=true
if run CLOUDFLARE_PROXY_ONLY=maybe; then fail 'An invalid CLOUDFLARE_PROXY_ONLY is accepted'; fi

# A multi-node control plane keeps the API and etcd on the node network.
run K3S_NODE_NETWORK_CIDR=10.0.0.0/24 K3S_PRIVATE_ADDRESS=10.0.0.5 CLOUDFLARE_PROXY_ONLY=false || fail "Cluster CIDR: $(cat "$STATE/error")"
has "ufw allow in on ens4 from 10.0.0.0/24 to any port 6443 proto tcp"
has "ufw allow in on ens4 from 10.0.0.0/24 to any port 2379:2380 proto tcp comment K3s embedded etcd peers"
has "ufw allow in on ens4 from 10.0.0.0/24 to any proto vrrp comment Kubernetes API VIP (VRRP)"
has "ufw allow in on ens4 from 10.0.0.0/24 to any port 10250 proto tcp"
if run K3S_NODE_NETWORK_CIDR=203.0.113.0/24; then fail 'A public node network is accepted'; fi
grep -Eq "must be RFC1918 or Tailscale|Unable to find a private control-plane interface" "$STATE/error" || \
    fail "Unexpected error: $(cat "$STATE/error")"
lacks "ufw --force reset"

# A worker: SSH only from the control planes on the cluster interface; public interfaces drop.
run NODE_ROLE=worker K3S_NODE_NETWORK_CIDR=10.0.0.0/24 CONTROL_PLANE_SSH_IPS=10.0.0.1,10.0.0.2 || fail "Worker: $(cat "$STATE/error")"
has "ufw allow in on ens4 from 10.0.0.1 to any port 22 proto tcp comment Swirl Cloud control-plane SSH"
has "ufw allow in on ens4 from 10.0.0.2 to any port 22 proto tcp comment Swirl Cloud control-plane SSH"
has "ufw deny in on ens3 comment No private-node provider ingress"
has "ufw route deny in on ens3 comment No private-node forwarded ingress"
lacks "ufw allow 22/tcp"
lacks "port 80 proto"
lacks "deny in on ens4"
lacks "6443"
has "guard ens3"
lacks "deny in on cni0"
lacks "deny in on tailscale0"
lacks "deny in on flannel.1"
has "ufw allow in on ens4 from 10.0.0.0/24 to any port 9500:9503 proto tcp comment Longhorn manager"

# UFW that does not come up: fail closed, keeping SSH, loopback and cluster interfaces.
if run UFW_ENABLES=false CLOUDFLARE_PROXY_ONLY=false; then fail 'An inactive UFW is accepted'; fi
grep -Fq "UFW could not be enabled" "$STATE/error" || fail "Unexpected error: $(cat "$STATE/error")"
has "/lib/ufw/ufw-init force-reload"
has "iptables -I INPUT 1 -p tcp --dport 22 -j ACCEPT"
has "iptables -I INPUT 1 -i lo -j ACCEPT"
has "iptables -I INPUT 1 -i cni0 -j ACCEPT"
has "iptables -P INPUT DROP"
has "ip6tables -P INPUT DROP"

printf '[PASS] Node firewall\n'
