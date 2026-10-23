#!/usr/bin/env bash
# UFW policy and the worker forwarding guard for configure-node-security.sh,
# which provides logging, root-file helpers and lib/node-access-policy.sh.

configure_worker_forwarding_guard() {
  local interfaces=("$@")
  local interface

  if ! private_node_policy || [[ ${#interfaces[@]} -eq 0 ]]; then
    if sudo systemctl cat swirl-cloud-worker-ingress-guard.service >/dev/null 2>&1; then
      sudo systemctl disable --now swirl-cloud-worker-ingress-guard.service >/dev/null 2>&1 || true
    fi
    return 0
  fi
  for interface in "${interfaces[@]}"; do
    [[ ${#interface} -le 15 && "$interface" =~ ^[A-Za-z0-9_.:-]+$ ]] || \
      err "Unsafe worker interface name: $interface"
  done
  ensure_packages nftables util-linux
  sudo install -d -o root -g root -m 0755 /etc/nftables.d
  {
    printf '%s\n' \
      '# Managed by scripts/configure-node-security.sh' \
      'table inet swirl_cloud_worker_guard {' \
      '  chain input_guard {' \
      '    type filter hook input priority -150; policy accept;' \
      '    ct state established,related accept'
    for interface in "${interfaces[@]}"; do
      printf '    iifname "%s" udp sport 67 udp dport 68 accept\n' "$interface"
      printf '    iifname "%s" udp sport 547 udp dport 546 accept\n' "$interface"
      printf '    iifname "%s" meta l4proto ipv6-icmp accept\n' "$interface"
      printf '    iifname "%s" drop\n' "$interface"
    done
    printf '%s\n' \
      '  }' \
      '  chain forward_guard {' \
      '    type filter hook forward priority -150; policy accept;' \
      '    ct state established,related accept'
    for interface in "${interfaces[@]}"; do
      printf '    iifname "%s" drop\n' "$interface"
    done
    printf '%s\n' '  }' '}'
  } | write_root_file /etc/nftables.d/swirl-cloud-worker-ingress.nft 0600

  write_root_file /usr/local/sbin/swirl-cloud-worker-ingress-guard 0755 <<'EOF'
#!/bin/sh
set -eu
table_name=swirl_cloud_worker_guard
if /usr/sbin/nft list table inet "$table_name" >/dev/null 2>&1; then
    /usr/sbin/nft delete table inet "$table_name"
fi
if [ "${1:-start}" = stop ]; then
    exit 0
fi
exec /usr/sbin/nft -f /etc/nftables.d/swirl-cloud-worker-ingress.nft
EOF

  write_root_file /etc/systemd/system/swirl-cloud-worker-ingress-guard.service <<'EOF'
[Unit]
Description=Block public input and forwarded ingress on Swirl Cloud private nodes
After=network-pre.target nftables.service ufw.service
Before=docker.service k3s-agent.service k3s.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/swirl-cloud-worker-ingress-guard start
ExecStop=/usr/local/sbin/swirl-cloud-worker-ingress-guard stop

[Install]
WantedBy=multi-user.target
EOF
  sudo unshare --net /usr/sbin/nft -c -f /etc/nftables.d/swirl-cloud-worker-ingress.nft || \
    err "Refusing to replace the worker ingress guard because its nftables policy is invalid"
  sudo systemctl daemon-reload
  sudo systemctl enable swirl-cloud-worker-ingress-guard.service >/dev/null
  sudo systemctl restart swirl-cloud-worker-ingress-guard.service
}

configure_ufw() {
  local cloudflare_response=""
  local cloudflare_cidrs=()
  local cluster_interface=""
  local cidr interface
  local untrusted_interfaces=()

  ensure_packages ufw

  if private_node_policy; then
    cluster_interface="$(ip -4 route get "$CONTROL_PLANE_IP" 2>/dev/null | \
      awk '{for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}')"
    [[ -n "$cluster_interface" ]] || err "Unable to determine the private interface used to reach $CONTROL_PLANE_IP"
    [[ "$cluster_interface" == "tailscale0" ]] || [[ ! "$cluster_interface" =~ ^(lo|docker|br-|cni|flannel|veth) ]] || \
      err "Route to the control plane uses unsupported virtual interface $cluster_interface"
  elif [[ -n "$K3S_NODE_NETWORK_CIDR" ]]; then
    cluster_interface="$(detect_control_plane_cluster_interface || true)"
    [[ -n "$cluster_interface" ]] || \
      err "Unable to find a private control-plane interface within $K3S_NODE_NETWORK_CIDR"
  fi

  if [[ -n "$K3S_NODE_NETWORK_CIDR" ]] && ! trusted_private_cidr "$K3S_NODE_NETWORK_CIDR"; then
    err "K3S_NODE_NETWORK_CIDR must be RFC1918 or Tailscale 100.64.0.0/10: $K3S_NODE_NETWORK_CIDR"
  fi

  if [[ "$NODE_ROLE" == "control-plane" ]] && ! private_node_policy; then
    if [[ "$SERVER_EXPOSURE" == "local" ]]; then
      CLOUDFLARE_PROXY_ONLY=false
    elif [[ -z "$CLOUDFLARE_PROXY_ONLY" ]]; then
      if sudo test -f /etc/swirl-cloud/cloudflare-proxy-only; then
        CLOUDFLARE_PROXY_ONLY=true
      else
        CLOUDFLARE_PROXY_ONLY=false
      fi
    fi
    [[ "$CLOUDFLARE_PROXY_ONLY" =~ ^(true|false)$ ]] || err "CLOUDFLARE_PROXY_ONLY must be true or false"
  else
    # Workers and additional private control planes do not terminate public ingress.
    CLOUDFLARE_PROXY_ONLY=false
  fi

  # Fetch the allowlist before resetting UFW so an API failure cannot leave the
  # firewall disabled.
  if [[ "$NODE_ROLE" == "control-plane" && "$CLOUDFLARE_PROXY_ONLY" == "true" ]]; then
    ensure_packages ca-certificates curl jq
    cloudflare_response="$(curl -fsS https://api.cloudflare.com/client/v4/ips)" || err "Unable to download Cloudflare proxy networks"
    jq -e '.success == true and ((.result.ipv4_cidrs | length) + (.result.ipv6_cidrs | length) > 0)' \
      <<< "$cloudflare_response" >/dev/null || err "Cloudflare returned an invalid proxy network list"
    mapfile -t cloudflare_cidrs < <(jq -r '.result.ipv4_cidrs[], .result.ipv6_cidrs[]' <<< "$cloudflare_response")
  fi

  info "Configuring UFW baseline..."
  sudo sed -i 's/^DEFAULT_FORWARD_POLICY=.*/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw
  if sudo grep -q '^IPV6=' /etc/default/ufw; then
    sudo sed -i 's/^IPV6=.*/IPV6=yes/' /etc/default/ufw
  else
    printf 'IPV6=yes\n' | sudo tee -a /etc/default/ufw >/dev/null
  fi
  sudo ufw --force reset >/dev/null
  sudo ufw default deny incoming >/dev/null
  sudo ufw default allow outgoing >/dev/null
  sudo ufw logging medium >/dev/null

  info "Allowing required inbound ports..."
  if [[ "$NODE_ROLE" == "control-plane" ]] && ! private_node_policy; then
    # Password SSH stays open here by operator request; CrowdSec and Fail2ban
    # protect it.
    sudo ufw allow "$HARDENED_SSH_PORT/tcp" >/dev/null
    if [[ "$CLOUDFLARE_PROXY_ONLY" == "true" ]]; then
      info "Restricting ports 80/443 to Cloudflare proxy networks..."
      for cidr in "${cloudflare_cidrs[@]}"; do
        sudo ufw allow from "$cidr" to any port 80 proto tcp comment 'Cloudflare proxy' >/dev/null
        sudo ufw allow from "$cidr" to any port 443 proto tcp comment 'Cloudflare proxy' >/dev/null
      done
    else
      sudo ufw allow 80/tcp >/dev/null
      sudo ufw allow 443/tcp >/dev/null
    fi
  else
    info "Allowing private-node SSH from verified control planes on $cluster_interface..."
    allow_control_plane_ssh "$cluster_interface"

    # NodePort and Docker-published traffic can use FORWARD instead of INPUT, so
    # deny both on every non-cluster interface. Replies to outbound traffic pass.
    mapfile -t untrusted_interfaces < <(
      {
        ip -4 -o address show scope global
        ip -6 -o address show scope global
      } | awk '{print $2}' | LC_ALL=C sort -u
    )
    for interface in "${untrusted_interfaces[@]}"; do
      [[ "$interface" == "$cluster_interface" ]] && continue
      [[ "$interface" =~ ^(lo|docker|br-|cni|flannel|veth|tailscale|kube-ipvs) ]] && continue
      info "Blocking host and forwarded ingress on private-node interface $interface..."
      sudo ufw deny in on "$interface" comment 'No private-node provider ingress' >/dev/null
      sudo ufw route deny in on "$interface" comment 'No private-node forwarded ingress' >/dev/null
    done
  fi
  if [[ -n "$K3S_NODE_NETWORK_CIDR" ]]; then
    if [[ "$NODE_ROLE" == "control-plane" ]]; then
      info "Restricting the K3s API, embedded etcd, Flannel, and kubelet traffic to $K3S_NODE_NETWORK_CIDR on $cluster_interface..."
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 6443 proto tcp >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 2379:2380 proto tcp comment 'K3s embedded etcd peers' >/dev/null
      # keepalived moves the API VIP between control planes (configure-k3s-api-endpoint.sh).
      if [[ "$cluster_interface" != tailscale0 ]]; then
        sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any proto vrrp comment 'Kubernetes API VIP (VRRP)' >/dev/null
      fi
    else
      info "Restricting worker Flannel and kubelet traffic to $K3S_NODE_NETWORK_CIDR on $cluster_interface..."
    fi
    if [[ "$NODE_ROLE" == "worker" ]]; then
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 8472 proto udp >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 10250 proto tcp >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 2049 proto tcp comment 'Longhorn RWX' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 3260 proto tcp comment 'Longhorn iSCSI' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 8000 proto tcp comment 'Longhorn backing image' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 8002 proto tcp comment 'Longhorn backing data' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 8500:8504 proto tcp comment 'Longhorn instance manager' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 9500:9503 proto tcp comment 'Longhorn manager' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 10000:31000 proto tcp comment 'Longhorn engines and replicas' >/dev/null
    else
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 8472 proto udp >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 10250 proto tcp >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 2049 proto tcp comment 'Longhorn RWX' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 3260 proto tcp comment 'Longhorn iSCSI' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 8000 proto tcp comment 'Longhorn backing image' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 8002 proto tcp comment 'Longhorn backing data' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 8500:8504 proto tcp comment 'Longhorn instance manager' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 9500:9503 proto tcp comment 'Longhorn manager' >/dev/null
      sudo ufw allow in on "$cluster_interface" from "$K3S_NODE_NETWORK_CIDR" to any port 10000:31000 proto tcp comment 'Longhorn engines and replicas' >/dev/null
    fi
  else
    # A single node needs no public Kubernetes API; add-k3s-workers.sh adds a
    # local-network rule when workers join.
    warn "K3S_NODE_NETWORK_CIDR is unset; the K3s API and peer ports are not exposed publicly."
  fi

  if ip link show cni0 >/dev/null 2>&1; then
    sudo ufw allow in on cni0 >/dev/null
  fi
  if ip link show flannel.1 >/dev/null 2>&1; then
    sudo ufw allow in on flannel.1 >/dev/null
  fi

  enable_ufw_verified
  configure_worker_forwarding_guard "${untrusted_interfaces[@]}"
}

# Under iptables-nft, `ufw --force reset` can leave IPv4 chains loaded; ufw-init
# then skips the IPv6 rules and `ufw enable` fails (seen 2026-10-04). Retry with
# a full reload, check both families, and fail closed.
enable_ufw_verified() {
  if ! sudo ufw --force enable >/dev/null 2>&1; then
    warn "ufw enable failed; reloading every UFW rule file"
    sudo sed -i 's/^ENABLED=.*/ENABLED=yes/' /etc/ufw/ufw.conf
    sudo /lib/ufw/ufw-init force-reload >/dev/null
  fi
  if sudo ufw status 2>/dev/null | grep -qx 'Status: active' &&
      sudo iptables -S INPUT | grep -qx -- '-P INPUT DROP' &&
      { [[ "$(sudo sed -n 's/^IPV6=//p' /etc/default/ufw)" != yes ]] || sudo ip6tables -S INPUT | grep -qx -- '-P INPUT DROP'; }; then
    return 0
  fi
  # Keep only SSH and cluster interfaces open while an operator investigates.
  local interface
  for interface in cni0 flannel.1 tailscale0; do
    ip link show "$interface" >/dev/null 2>&1 && sudo iptables -I INPUT 1 -i "$interface" -j ACCEPT
  done
  sudo iptables -I INPUT 1 -p tcp --dport "$HARDENED_SSH_PORT" -j ACCEPT
  sudo iptables -I INPUT 1 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  sudo iptables -I INPUT 1 -i lo -j ACCEPT
  sudo iptables -P INPUT DROP
  sudo ip6tables -P INPUT DROP 2>/dev/null || true
  err "UFW could not be enabled; INPUT now drops everything except SSH, loopback, cluster interfaces and established traffic. Check: sudo ufw status verbose"
}
