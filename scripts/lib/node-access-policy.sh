#!/usr/bin/env bash
# shellcheck disable=SC2034 # Policy globals are read by configure-node-security.sh.
# Node role, private control-plane SSH allowlist and Tailscale firewall checks
# for configure-node-security.sh. This file is sourced; callers also source
# lib/network.sh and provide info/warn/err and the policy globals.

normalize_node_role() {
  case "${1,,}" in
    control-plane|controlplane|server|master) echo "control-plane" ;;
    worker|agent) echo "worker" ;;
    *) return 1 ;;
  esac
}

private_node_policy() {
  [[ "$NODE_ROLE" == "worker" || "$PRIVATE_CONTROL_PLANE" == "true" ]]
}

# Enrollment writes this marker before the private firewall, so a later local
# run without --private-control-plane cannot make the host a public server.
inherit_private_control_plane_policy() {
  local configuration="" saved="" address private_ip private_interface
  [[ "$NODE_ROLE" == control-plane ]] || return 0
  if sudo test -f "$PRIVATE_NETWORK_CONFIG"; then
    configuration="$(sudo cat "$PRIVATE_NETWORK_CONFIG")"
  fi
  if ! grep -Fxq '# exposure: private-only' <<< "$configuration"; then
    if [[ "$PRIVATE_CONTROL_PLANE" != true ]] && {
      sudo test -f "$CONTROL_PLANE_SSH_STATE" ||
      sudo test -f "$PRIVATE_NETWORK_CIDR_STATE" ||
      sudo test -f /etc/nftables.d/swirl-cloud-worker-ingress.nft;
    }; then
      err "Existing private-node policy lacks its managed control-plane network marker; refusing public firewall reconciliation"
    fi
    return 0
  fi
  grep -Fxq '# Managed by scripts/configure-k3s-control-plane-network.sh' <<< "$configuration" || \
    err "Private control-plane network configuration is not managed by this installer"
  private_ip="$(sed -n 's/^node-ip: "\([0-9.]*\)"$/\1/p' <<< "$configuration")"
  private_interface="$(sed -n 's/^flannel-iface: "\([A-Za-z0-9_.:-]*\)"$/\1/p' <<< "$configuration")"
  if ! trusted_private_ipv4 "$private_ip" || [[ -z "$private_interface" ]]; then
    err "Managed private control-plane address/interface is missing or invalid"
  fi
  [[ "$(interface_owning_ip "$private_ip")" == "$private_interface" ]] || \
    err "Managed private control-plane address no longer belongs to its interface"
  [[ -z "${K3S_PRIVATE_ADDRESS:-}" || "$K3S_PRIVATE_ADDRESS" == "$private_ip" ]] || \
    err "K3S_PRIVATE_ADDRESS conflicts with the persisted private control-plane address"
  K3S_PRIVATE_ADDRESS="$private_ip"
  if sudo test -f "$PRIVATE_NETWORK_CIDR_STATE"; then
    saved="$(sudo cat "$PRIVATE_NETWORK_CIDR_STATE")"
    [[ -z "$K3S_NODE_NETWORK_CIDR" || "$K3S_NODE_NETWORK_CIDR" == "$saved" ]] || \
      err "Private node CIDR changed; migrate the network before reconciling its firewall"
    K3S_NODE_NETWORK_CIDR="$saved"
  fi
  if ! trusted_private_cidr "$K3S_NODE_NETWORK_CIDR" || ! cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$private_ip"; then
    err "A validated K3S_NODE_NETWORK_CIDR is required for this existing private control plane"
  fi
  PRIVATE_CONTROL_PLANE=true
  SERVER_EXPOSURE=local
  if sudo test -f "$CONTROL_PLANE_SSH_STATE"; then
    saved="$(sudo cat "$CONTROL_PLANE_SSH_STATE")"
    [[ -n "$saved" ]] || err "Existing private control-plane SSH allowlist is empty"
    while IFS= read -r address; do
      if ! trusted_private_ipv4 "$address" || ! cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$address"; then
        err "Existing private control-plane SSH allowlist contains an invalid address"
      fi
    done <<< "$saved"
    if [[ -z "$CONTROL_PLANE_SSH_IPS" ]]; then
      CONTROL_PLANE_SSH_IPS="$(paste -sd, - <<< "$saved")"
    fi
    INHERITED_PRIVATE_CONTROL_PLANE=true
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
      address="${SSH_CONNECTION%% *}"
      grep -Fxq "$address" <<< "$saved" || \
        err "Current SSH source is outside the persisted control-plane allowlist"
      [[ -z "$CONTROL_PLANE_IP" || "$CONTROL_PLANE_IP" == "$address" ]] || \
        err "CONTROL_PLANE_IP does not match the current private SSH source"
      CONTROL_PLANE_IP="$address"
    elif [[ -z "$CONTROL_PLANE_IP" ]]; then
      CONTROL_PLANE_IP="$(awk -v local_ip="$private_ip" '$0 != local_ip {print; exit}' <<< "$saved")"
      [[ -n "$CONTROL_PLANE_IP" ]] || err "Private console reconciliation needs a recorded control-plane peer"
    fi
  fi
  info "Preserving the enrolled control plane's private firewall policy"
}

# The SSH source is kept apart from the full management allowlist. The saved
# list survives a later full firewall run from another control plane.
prepare_control_plane_ssh_policy() {
  local address saved=""
  local -a addresses=()
  private_node_policy || return 0
  if [[ -z "$CONTROL_PLANE_SSH_IPS" ]] && sudo test -f "$CONTROL_PLANE_SSH_STATE"; then
    saved="$(sudo cat "$CONTROL_PLANE_SSH_STATE")"
    CONTROL_PLANE_SSH_IPS="$(tr '\n' ',' <<< "$saved")"
    CONTROL_PLANE_SSH_IPS="${CONTROL_PLANE_SSH_IPS%,}"
  fi
  CONTROL_PLANE_SSH_IPS="${CONTROL_PLANE_SSH_IPS:-$CONTROL_PLANE_IP}"
  [[ -n "$CONTROL_PLANE_SSH_IPS" && "$CONTROL_PLANE_SSH_IPS" != ,* &&
     "$CONTROL_PLANE_SSH_IPS" != *, && "$CONTROL_PLANE_SSH_IPS" != *,,* ]] || \
    err "Control-plane SSH addresses must be a non-empty comma-separated list"
  IFS=',' read -r -a addresses <<< "$CONTROL_PLANE_SSH_IPS"
  for address in "${addresses[@]}"; do
    if ! trusted_private_ipv4 "$address" || ! cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$address"; then
      err "Invalid control-plane SSH source outside the private node network: $address"
    fi
  done
  [[ ",$CONTROL_PLANE_SSH_IPS," == *",$CONTROL_PLANE_IP,"* ]] || \
    err "The verified current SSH source must remain in the control-plane allowlist"
  CONTROL_PLANE_SSH_IPS="$(printf '%s\n' "${addresses[@]}" | LC_ALL=C sort -u | paste -sd, -)"
}

persist_control_plane_ssh_policy() {
  local policy_file
  policy_file="$(mktemp)"
  printf '%s\n' "$CONTROL_PLANE_SSH_IPS" | tr ',' '\n' > "$policy_file"
  sudo install -D -o root -g root -m 0644 "$policy_file" "$CONTROL_PLANE_SSH_STATE"
  if [[ "$PRIVATE_CONTROL_PLANE" == true ]]; then
    printf '%s\n' "$K3S_NODE_NETWORK_CIDR" > "$policy_file"
    sudo install -D -o root -g root -m 0644 "$policy_file" "$PRIVATE_NETWORK_CIDR_STATE"
  fi
  rm -f "$policy_file"
}

allow_control_plane_ssh() {
  local cluster_interface="$1" address
  local -a addresses=()
  IFS=',' read -r -a addresses <<< "$CONTROL_PLANE_SSH_IPS"
  for address in "${addresses[@]}"; do
    sudo ufw allow in on "$cluster_interface" from "$address" to any port "$HARDENED_SSH_PORT" proto tcp comment 'Swirl Cloud control-plane SSH' >/dev/null
  done
}

reconcile_control_plane_ssh() {
  local cluster_interface previous="" address
  private_node_policy || err "SSH-only reconciliation requires a private node policy"
  command -v ufw >/dev/null 2>&1 || err "UFW is not installed on this node"
  sudo ufw status | grep -q '^Status: active' || err "Refusing SSH-only reconciliation while UFW is inactive"
  # Validate every input and the active private session before changing a rule.
  prepare_control_plane_ssh_policy
  configure_tailscale_firewall_integration
  validate_worker_private_ssh_before_firewall
  cluster_interface="$(interface_owning_ip "$(awk '{print $3}' <<< "$SSH_CONNECTION")")"
  if sudo test -f "$CONTROL_PLANE_SSH_STATE"; then
    previous="$(sudo cat "$CONTROL_PLANE_SSH_STATE")"
  fi
  while IFS= read -r address; do
    [[ -z "$address" ]] || trusted_private_ipv4 "$address" || err "Invalid saved control-plane SSH policy"
  done <<< "$previous"
  allow_control_plane_ssh "$cluster_interface"
  # Remove only previously managed addresses, after their replacements are
  # active. Other host rules and the active SSH source stay.
  while IFS= read -r address; do
    [[ -n "$address" && ",$CONTROL_PLANE_SSH_IPS," != *",$address,"* ]] || continue
    sudo ufw --force delete allow in on "$cluster_interface" from "$address" to any port "$HARDENED_SSH_PORT" proto tcp comment 'Swirl Cloud control-plane SSH' >/dev/null
  done <<< "$previous"
  persist_control_plane_ssh_policy
  info "Private SSH administration reconciled for verified control planes: $CONTROL_PLANE_SSH_IPS"
}

detect_control_plane_cluster_interface() {
  local interface address default_route default_source

  if [[ -n "${K3S_PRIVATE_ADDRESS:-}" ]] && \
      cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$K3S_PRIVATE_ADDRESS"; then
    interface_owning_ip "$K3S_PRIVATE_ADDRESS"
    return
  fi

  default_route="$(ip -4 route get 1.1.1.1 2>/dev/null || true)"
  default_source="$(awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}' <<< "$default_route")"
  if [[ -n "$default_source" ]] && cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$default_source"; then
    awk '{for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}' <<< "$default_route"
    return
  fi

  while read -r interface address; do
    [[ "$interface" =~ ^(lo|docker|br-|cni|flannel|veth|tailscale) ]] && continue
    address="${address%/*}"
    if cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$address"; then
      printf '%s\n' "$interface"
      return
    fi
  done < <(ip -4 -o address show scope global | awk '{print $2, $4}')

  if ip -4 address show dev tailscale0 >/dev/null 2>&1; then
    address="$(ip -4 -o address show dev tailscale0 scope global | awk 'NR == 1 {sub(/\/.*/, "", $4); print $4}')"
    if tailscale_ipv4 "$address" && cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$address"; then
      printf 'tailscale0\n'
      return
    fi
  fi

  return 1
}

tailscale_transport_selected() {
  local cidr_address=""

  if [[ -n "$K3S_NODE_NETWORK_CIDR" ]]; then
    cidr_address="${K3S_NODE_NETWORK_CIDR%/*}"
  fi
  if tailscale_ipv4 "${CONTROL_PLANE_IP:-}" || \
      tailscale_ipv4 "${K3S_PRIVATE_ADDRESS:-}" || \
      tailscale_ipv4 "$cidr_address"; then
    return 0
  fi
  return 1
}

configure_tailscale_firewall_integration() {
  local local_tailscale_ip route route_interface route_source

  tailscale_transport_selected || return 0

  command -v tailscale >/dev/null 2>&1 || \
    err "Refusing to configure UFW: install Tailscale before selecting a Tailscale node network"
  sudo tailscale status >/dev/null 2>&1 || \
    err "Refusing to configure UFW: connect this node to Tailscale first"
  local_tailscale_ip="$(sudo tailscale ip -4 2>/dev/null | head -n 1)"
  tailscale_ipv4 "$local_tailscale_ip" || \
    err "Refusing to configure UFW: Tailscale has not assigned this node an IPv4 address"

  if private_node_policy; then
    tailscale_ipv4 "$CONTROL_PLANE_IP" || \
      err "Refusing to configure worker UFW: the control-plane address must be a Tailscale IPv4"
    route="$(ip -4 route get "$CONTROL_PLANE_IP" 2>/dev/null)" || \
      err "Refusing to configure worker UFW: the Tailscale control plane is not routable"
    route_interface="$(awk '{for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}' <<< "$route")"
    route_source="$(awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}' <<< "$route")"
    [[ "$route_interface" == "tailscale0" ]] || \
      err "Refusing to configure worker UFW: route to $CONTROL_PLANE_IP uses ${route_interface:-no interface}, not tailscale0"
    [[ "$route_source" == "$local_tailscale_ip" ]] || \
      err "Refusing to configure worker UFW: route source ${route_source:-none} does not match Tailscale IP $local_tailscale_ip"
    info "Tailscale preflight passed on $local_tailscale_ip; worker UFW can now close public ingress."
  fi

}

make_ufw_authoritative_for_tailscale() {
  tailscale_transport_selected || return 0

  # Runs only after UFW's tailscale0 rules are enabled, so some layer always
  # admits the current private SSH connection.
  sudo tailscale set --ssh=false --netfilter-mode=nodivert >/dev/null || \
    err "Unable to make UFW authoritative for Tailscale traffic"
}

validate_worker_private_ssh_before_firewall() {
  local route route_interface route_source default_interface
  local ssh_client_ip ssh_server_ip ssh_server_port ssh_interface

  private_node_policy || return 0
  if [[ "${INHERITED_PRIVATE_CONTROL_PLANE:-false}" == true && -z "${SSH_CONNECTION:-}" ]]; then
    # A local console run may keep an already validated policy. Enrollment and
    # SSH runs still need the SSH proof below.
    [[ -n "${K3S_PRIVATE_ADDRESS:-}" && -n "$(interface_owning_ip "$K3S_PRIVATE_ADDRESS")" ]] || \
      err "Private console reconciliation lost its validated local interface"
    return 0
  fi
  route="$(ip -4 route get "$CONTROL_PLANE_IP" 2>/dev/null)" || \
    err "Refusing to configure worker UFW: the private control-plane address is not routable"
  route_interface="$(awk '{for (i=1; i<=NF; i++) if ($i == "dev") {print $(i+1); exit}}' <<< "$route")"
  route_source="$(awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}' <<< "$route")"
  trusted_private_ipv4 "$route_source" || \
    err "Refusing to configure worker UFW: route to the control plane uses non-private source ${route_source:-none}"
  cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$route_source" || \
    err "Refusing to configure worker UFW: route source $route_source is outside $K3S_NODE_NETWORK_CIDR"
  [[ -n "$route_interface" ]] || err "Refusing to configure worker UFW: private route has no interface"

  # A LAN host behind NAT may use its only NIC for cluster traffic, but a public
  # default route must never carry vRack/LAN node traffic.
  if ! tailscale_transport_selected && ! default_route_is_private; then
    default_interface="$(default_route_interface)"
    [[ "$route_interface" != "$default_interface" ]] || \
      err "Refusing to configure worker UFW: private node traffic uses public default interface $route_interface"
  fi
  [[ -n "${SSH_CONNECTION:-}" ]] || \
    err "Refusing to configure private-node UFW without a proven private SSH session. Enroll this node from the control plane over the selected private transport."
  read -r ssh_client_ip _ ssh_server_ip ssh_server_port <<< "$SSH_CONNECTION"
  [[ "$ssh_client_ip" == "$CONTROL_PLANE_IP" ]] || \
    err "Refusing to configure worker UFW: current SSH client $ssh_client_ip is not control plane $CONTROL_PLANE_IP"
  [[ "$ssh_server_ip" == "$route_source" ]] || \
    err "Refusing to configure worker UFW: current SSH endpoint $ssh_server_ip is not private worker address $route_source"
  ssh_interface="$(interface_owning_ip "$ssh_server_ip")"
  [[ "$ssh_interface" == "$route_interface" ]] || \
    err "Refusing to configure worker UFW: current SSH endpoint is not on private interface $route_interface"
  [[ "$ssh_server_port" == "$HARDENED_SSH_PORT" ]] || \
    err "Refusing to configure worker UFW: current SSH port $ssh_server_port differs from configured port $HARDENED_SSH_PORT"
  info "Private SSH preflight passed from $CONTROL_PLANE_IP to $route_source on $route_interface; UFW may now close public ingress."
}
