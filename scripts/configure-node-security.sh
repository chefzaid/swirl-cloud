#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NETWORK_LIBRARY="$SCRIPT_DIR/lib/network.sh"
if [[ ! -r "$NETWORK_LIBRARY" ]]; then
  echo "[ERROR] Shared network library not found: $NETWORK_LIBRARY" >&2
  exit 1
fi
# shellcheck source=lib/network.sh
source "$NETWORK_LIBRARY"

APPLY=false
HOST_BASELINE_ONLY=false
RECONCILE_CONTROL_PLANE_SSH=false
SERVER_EXPOSURE="${SERVER_EXPOSURE:-internet}"
NODE_ROLE="${NODE_ROLE:-control-plane}"
PRIVATE_CONTROL_PLANE=false
INHERITED_PRIVATE_CONTROL_PLANE=false
K3S_NODE_NETWORK_CIDR="${K3S_NODE_NETWORK_CIDR:-}"
CONTROL_PLANE_IP="${CONTROL_PLANE_IP:-}"
CONTROL_PLANE_SSH_IPS="${CONTROL_PLANE_SSH_IPS:-}"
CONTROL_PLANE_SSH_STATE=/etc/swirl-cloud/control-plane-ssh-ips
PRIVATE_NETWORK_CIDR_STATE=/etc/swirl-cloud/private-node-network-cidr
PRIVATE_NETWORK_CONFIG=/etc/rancher/k3s/config.yaml.d/20-swirl-cloud-private-node-network.yaml
HARDENED_SSH_PORT="${HARDENED_SSH_PORT:-}"
CLOUDFLARE_PROXY_ONLY="${CLOUDFLARE_PROXY_ONLY:-}"
SSH_ALLOWED_USERS="${SSH_ALLOWED_USERS:-${SUDO_USER:-$USER}}"
APT_UPDATED=false

usage() {
  echo "Usage: $0 [--apply] [--server-exposure internet|local] [--node-role control-plane|worker] [--private-control-plane] [--control-plane-ip PRIVATE_IP] [--ssh-port PORT]"
  echo "  --private-control-plane: private join-server policy, no public ingress"
  echo "  --control-plane-ssh-ips CSV: verified control-plane private IPs allowed to administer this node"
  echo "  --reconcile-control-plane-ssh: update only managed private SSH rules (requires --apply)"
  echo "  --host-baseline: apply only the firewall-independent host baseline"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=true ;;
    --host-baseline) HOST_BASELINE_ONLY=true ;;
    --reconcile-control-plane-ssh) RECONCILE_CONTROL_PLANE_SSH=true ;;
    --control-plane-ssh-ips) shift; [[ $# -gt 0 ]] || { usage; exit 1; }; CONTROL_PLANE_SSH_IPS="$1" ;;
    --control-plane-ssh-ips=*) CONTROL_PLANE_SSH_IPS="${1#*=}" ;;
    --private-control-plane) PRIVATE_CONTROL_PLANE=true ;;
    --server-exposure)
      shift
      [[ $# -gt 0 ]] || { echo "Missing value for --server-exposure"; usage; exit 1; }
      SERVER_EXPOSURE="$1"
      ;;
    --server-exposure=*)
      SERVER_EXPOSURE="${1#*=}"
      ;;
    --node-role)
      shift
      [[ $# -gt 0 ]] || { echo "Missing value for --node-role"; usage; exit 1; }
      NODE_ROLE="$1"
      ;;
    --node-role=*)
      NODE_ROLE="${1#*=}"
      ;;
    --control-plane-ip)
      shift
      [[ $# -gt 0 ]] || { echo "Missing value for --control-plane-ip"; usage; exit 1; }
      CONTROL_PLANE_IP="$1"
      ;;
    --control-plane-ip=*)
      CONTROL_PLANE_IP="${1#*=}"
      ;;
    --ssh-port)
      shift
      [[ $# -gt 0 ]] || { echo "Missing value for --ssh-port"; usage; exit 1; }
      HARDENED_SSH_PORT="$1"
      ;;
    --ssh-port=*)
      HARDENED_SSH_PORT="${1#*=}"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
  shift
done

if [[ -z "$HARDENED_SSH_PORT" && -n "${SSH_CONNECTION:-}" ]]; then
  read -r _ _ _ HARDENED_SSH_PORT <<< "$SSH_CONNECTION"
fi
if [[ -z "$HARDENED_SSH_PORT" ]] && command -v sshd >/dev/null 2>&1; then
  HARDENED_SSH_PORT="$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2; exit}')"
fi
HARDENED_SSH_PORT="${HARDENED_SSH_PORT:-22}"

info() { echo -e "\033[0;32m[INFO]\033[0m  $*"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
err()  { echo -e "\033[0;31m[ERROR]\033[0m $*"; exit 1; }

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

# Enrollment persists this marker before installing the private firewall.
# A later local installer must not turn the same host into a public bootstrap
# server just because --private-control-plane was omitted.
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

# Keep the actual SSH source separate from the complete management allowlist.
# A persisted list survives a later full firewall reconciliation from another CP.
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
  # Only remove addresses previously recorded as managed, after their replacements
  # are active. All unrelated host rules and the active SSH source are preserved.
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

  # Keep Tailscale's existing admission path active while UFW is rebuilt. Only
  # hand authority to UFW after its exact tailscale0 rules are enabled, avoiding
  # a window where neither layer admits the current private SSH connection.
  sudo tailscale set --ssh=false --netfilter-mode=nodivert >/dev/null || \
    err "Unable to make UFW authoritative for Tailscale traffic"
}

validate_worker_private_ssh_before_firewall() {
  local route route_interface route_source default_interface
  local ssh_client_ip ssh_server_ip ssh_server_port ssh_interface

  private_node_policy || return 0
  if [[ "${INHERITED_PRIVATE_CONTROL_PLANE:-false}" == true && -z "${SSH_CONNECTION:-}" ]]; then
    # A local console run can preserve an already validated policy. First-time
    # enrollment and all SSH runs still require the original SSH proof below.
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

  # A LAN host behind a NAT router may carry cluster traffic on its only NIC.
  # A public default route must never carry vRack/LAN node traffic.
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

apt_update() {
  if [[ "$APT_UPDATED" != "true" ]]; then
    sudo apt-get update -qq
    APT_UPDATED=true
  fi
}

ensure_packages() {
  local missing=()
  local package

  for package in "$@"; do
    if ! dpkg -s "$package" >/dev/null 2>&1; then
      missing+=("$package")
    fi
  done

  if [[ "${#missing[@]}" -gt 0 ]]; then
    info "Installing packages: ${missing[*]}"
    apt_update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}" >/dev/null
  fi
}

backup_file_if_exists() {
  local path="$1"
  local backup_path="/var/backups/swirl-cloud/config${path}.bak"

  if sudo test -f "$path" && ! sudo test -f "$backup_path"; then
    sudo install -d -o root -g root -m 0700 "$(dirname "$backup_path")"
    sudo cp "$path" "$backup_path"
    sudo chmod 0600 "$backup_path"
  fi
}

# Leaves an identical root-owned file untouched, so reruns do not churn inodes
# and timestamps that AIDE and auditd report as changes.
write_root_file() {
  local destination_path="$1"
  local mode="${2:-0644}"
  local tmp_file

  tmp_file="$(mktemp)"
  cat > "$tmp_file"
  if ! sudo test -f "$destination_path" || ! sudo cmp -s "$tmp_file" "$destination_path" || \
      [[ "$(sudo stat -c '%a %u %g' "$destination_path")" != "$(printf '%o' "$((8#$mode))") 0 0" ]]; then
    sudo install -D -o root -g root -m "$mode" "$tmp_file" "$destination_path"
  fi
  rm -f "$tmp_file"
}

# Applies an extended sed expression to a root-owned file only when the result
# differs, keeping the file's inode, owner and mode.
edit_root_file() {
  local path="$1" expression="$2" tmp_file

  tmp_file="$(mktemp)"
  sudo cat "$path" | sed -E "$expression" > "$tmp_file"
  if ! sudo cmp -s "$tmp_file" "$path"; then
    sudo cp "$tmp_file" "$path"
  fi
  rm -f "$tmp_file"
}

ensure_service() {
  local service_name="$1"
  sudo systemctl enable --now "$service_name" >/dev/null 2>&1
  sudo systemctl restart "$service_name" >/dev/null 2>&1
}

remove_packages_if_installed() {
  local installed=()
  local package

  for package in "$@"; do
    if dpkg -s "$package" >/dev/null 2>&1; then
      installed+=("$package")
    fi
  done

  if [[ ${#installed[@]} -gt 0 ]]; then
    info "Removing packages excluded by this node's security policy: ${installed[*]}"
    sudo env DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq "${installed[@]}" >/dev/null
  fi
}

ensure_crowdsec_repo() {
  local candidate

  candidate="$(apt-cache policy crowdsec 2>/dev/null | awk '/Candidate:/ {candidate=$2} END {print candidate}')"
  if [[ -n "$candidate" && "$candidate" != "(none)" ]]; then
    return 0
  fi

  info "Installing CrowdSec package repository..."
  ensure_packages ca-certificates curl gpg
  curl -fsSL https://install.crowdsec.net | sudo -E sh >/dev/null
  APT_UPDATED=false
}

ensure_crowdsec_collection() {
  local collection="$1"

  if sudo cscli collections list -o raw 2>/dev/null | awk -v target="$collection" '$1 == target {found=1} END {exit(found ? 0 : 1)}'; then
    return 0
  fi

  info "Installing CrowdSec collection ${collection}..."
  sudo cscli collections install "$collection" >/dev/null
}

write_fail2ban_config() {
  backup_file_if_exists /etc/fail2ban/jail.local
  write_root_file /etc/fail2ban/jail.local <<EOF
# Managed by scripts/configure-node-security.sh
[DEFAULT]
# Stricter SSH brute-force policy: fewer attempts, longer observation window,
# longer bans, and exponential repeat-offender escalation.
bantime = 24h
bantime.increment = true
bantime.factor = 2
bantime.rndtime = 10m
bantime.maxtime = 30d
findtime = 1h
maxretry = 3
backend = systemd
usedns = warn
banaction = ufw

[sshd]
enabled = true
port = $HARDENED_SSH_PORT
filter = sshd[mode=aggressive]
backend = systemd
journalmatch = _SYSTEMD_UNIT=ssh.service + _COMM=sshd
EOF
}

write_crowdsec_profiles() {
  backup_file_if_exists /etc/crowdsec/profiles.yaml
  write_root_file /etc/crowdsec/profiles.yaml <<'EOF'
name: default_ip_remediation
#debug: true
filters:
 - Alert.Remediation == true && Alert.GetScope() == "Ip"
decisions:
 - type: ban
   duration: 7d
# notifications:
#   - slack_default  # Set the webhook in /etc/crowdsec/notifications/slack.yaml before enabling this.
#   - splunk_default # Set the splunk url and token in /etc/crowdsec/notifications/splunk.yaml before enabling this.
#   - http_default   # Set the required http parameters in /etc/crowdsec/notifications/http.yaml before enabling this.
#   - email_default  # Set the required email parameters in /etc/crowdsec/notifications/email.yaml before enabling this.
on_success: break
---
name: default_range_remediation
#debug: true
filters:
 - Alert.Remediation == true && Alert.GetScope() == "Range"
decisions:
 - type: ban
   duration: 7d
# notifications:
#   - slack_default  # Set the webhook in /etc/crowdsec/notifications/slack.yaml before enabling this.
#   - splunk_default # Set the splunk url and token in /etc/crowdsec/notifications/splunk.yaml before enabling this.
#   - http_default   # Set the required http parameters in /etc/crowdsec/notifications/http.yaml before enabling this.
#   - email_default  # Set the required email parameters in /etc/crowdsec/notifications/email.yaml before enabling this.
on_success: break
EOF
}

write_crowdsec_ssh_time_based_scenario() {
  backup_file_if_exists /etc/crowdsec/scenarios/ssh-time-based-bf.yaml
  write_root_file /etc/crowdsec/scenarios/ssh-time-based-bf.yaml <<'EOF'
# ssh time-based bruteforce with false positive reduction
type: conditional
name: crowdsecurity/ssh-time-based-bf
description: "Detect time-based ssh bruteforce attempts that evade rate limiting (with false positive reduction)"
filter: "evt.Meta.service == 'ssh' && evt.Meta.log_type in ['ssh_failed-auth', 'auth_success']"
groupby: evt.Meta.source_ip
capacity: -1
cancel_on: "evt.Meta.log_type == 'auth_success'"
condition: |
    let failedAuths = filter(queue.Queue, {#.Meta.log_type == 'ssh_failed-auth'});
    len(failedAuths) >= 4 &&
    MedianInterval(map(failedAuths[-4:], {#.Time})) > duration("10m")
leakspeed: 2h
blackhole: 5m
reprocess: true
labels:
  service: ssh
  behavior: "ssh:bruteforce"
  spoofable: 0
  confidence: 3
  classification:
    - attack.T1110
  label: "SSH Time-Based Bruteforce"
  remediation: true
---
# ssh user-enum time-based with false positive reduction
type: conditional
name: crowdsecurity/ssh-time-based-bf_user-enum
description: "Detect time-based ssh user enum bruteforce attempts (with false positive reduction)"
filter: "evt.Meta.service == 'ssh' && evt.Meta.log_type in ['ssh_failed-auth', 'auth_success']"
groupby: evt.Meta.source_ip
distinct: evt.Meta.target_user
capacity: -1
cancel_on: "evt.Meta.log_type == 'auth_success'"
condition: |
    let failedAuths = filter(queue.Queue, {#.Meta.log_type == 'ssh_failed-auth'});
    len(failedAuths) >= 4 &&
    MedianInterval(map(failedAuths[-4:], {#.Time})) > duration("10m")
leakspeed: 2h
blackhole: 5m
reprocess: true
labels:
  service: ssh
  behavior: "ssh:bruteforce"
  spoofable: 0
  confidence: 3
  classification:
    - attack.T1589
    - attack.T1110
  label: "SSH Time-Based User Enumeration"
  remediation: true
EOF
}

configure_crowdsec_inotify_limits() {
  write_root_file /etc/sysctl.d/99-crowdsec-inotify.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
# Allow CrowdSec and container-heavy workloads to create enough filesystem watches.
fs.inotify.max_user_instances = 1024
fs.inotify.max_user_watches = 1048576
EOF

  sudo sysctl -w fs.inotify.max_user_instances=1024 >/dev/null
  sudo sysctl -w fs.inotify.max_user_watches=1048576 >/dev/null
}

write_crowdsec_acquisitions() {
  backup_file_if_exists /etc/crowdsec/acquis.d/setup.linux.yaml
  backup_file_if_exists /etc/crowdsec/acquis.d/setup.sshd.yaml
  backup_file_if_exists /etc/crowdsec/acquis.yaml
  sudo rm -f /etc/crowdsec/acquis.yaml

  write_root_file /etc/crowdsec/acquis.d/setup.linux.yaml <<'EOF'
# Managed by scripts/configure-node-security.sh
filenames:
  - /var/log/messages
  - /var/log/syslog
  - /var/log/kern.log
labels:
  type: syslog
  source: file
EOF

  write_root_file /etc/crowdsec/acquis.d/setup.sshd.yaml <<'EOF'
# Managed by scripts/configure-node-security.sh
filenames:
  - /var/log/auth.log
  - /var/log/secure
labels:
  type: syslog
  source: file
EOF
}

get_crowdsec_bouncer_api_key() {
  local config_path="/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml"
  local api_key=""
  local bouncer_name=""

  if sudo test -f "$config_path"; then
    api_key="$(sudo awk -F': ' '$1=="api_key"{gsub(/"/,"",$2); print $2; exit}' "$config_path")"
  fi

  if [[ -z "$api_key" ]]; then
    bouncer_name="swirl-cloud-$(hostname -s)-firewall-bouncer-$(date +%s)"
    api_key="$(sudo cscli bouncers add "$bouncer_name" -o raw 2>/dev/null || true)"
  fi

  [[ -n "$api_key" ]] || err "Unable to determine CrowdSec bouncer API key"
  printf '%s\n' "$api_key"
}

write_crowdsec_bouncer_config() {
  local api_key="$1"

  backup_file_if_exists /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
  write_root_file /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml <<EOF
# Managed by scripts/configure-node-security.sh
mode: nftables
update_frequency: 10s
log_mode: file
log_dir: /var/log/
log_level: info
log_compression: true
log_max_size: 100
log_max_backups: 3
log_max_age: 30
api_url: http://127.0.0.1:8080/
api_key: ${api_key}
insecure_skip_verify: false
disable_ipv6: false
deny_action: DROP
deny_log: false
supported_decisions_types:
  - ban
blacklists_ipv4: crowdsec-blacklists
blacklists_ipv6: crowdsec6-blacklists
ipset_type: nethash
iptables_chains:
  - INPUT
iptables_add_rule_comments: true
nftables:
  ipv4:
    enabled: true
    set-only: false
    table: crowdsec
    chain: crowdsec-chain
    priority: -10
  ipv6:
    enabled: true
    set-only: false
    table: crowdsec6
    chain: crowdsec6-chain
    priority: -10
nftables_hooks:
  - input
  - forward
pf:
  anchor_name: ""
prometheus:
  enabled: false
  listen_addr: 127.0.0.1
  listen_port: 60601
EOF
}

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

  # Fetch and validate the allowlist before resetting UFW so a network/API
  # failure can never leave the host firewall disabled.
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
    # Password SSH remains available on the internet-facing control plane by
    # operator request; CrowdSec and Fail2ban protect it in internet mode.
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

    # Kubernetes NodePort and Docker-published traffic may traverse FORWARD
    # instead of INPUT. Deny both paths on every provider/non-cluster interface
    # while preserving stateful replies for worker-initiated outbound traffic.
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
    # A single-node cluster does not need a public Kubernetes API. Worker
    # enrollment adds an explicit local-network rule through add-k3s-workers.sh.
    warn "K3S_NODE_NETWORK_CIDR is unset; the K3s API and peer ports are not exposed publicly."
  fi

  if ip link show cni0 >/dev/null 2>&1; then
    sudo ufw allow in on cni0 >/dev/null
  fi
  if ip link show flannel.1 >/dev/null 2>&1; then
    sudo ufw allow in on flannel.1 >/dev/null
  fi

  sudo ufw --force enable >/dev/null
  configure_worker_forwarding_guard "${untrusted_interfaces[@]}"
}

configure_sshd() {
  local config_path="/etc/ssh/sshd_config.d/40-swirl-cloud-hardening.conf"
  local backup_path="/var/backups/swirl-cloud/config${config_path}.bak"

  [[ "$SSH_ALLOWED_USERS" =~ ^[a-zA-Z0-9_.@-]+([[:space:]]+[a-zA-Z0-9_.@-]+)*$ ]] || \
    err "SSH_ALLOWED_USERS must be a space-separated list of local usernames"

  backup_file_if_exists "$config_path"
  write_root_file "$config_path" 0600 <<EOF
# Managed by scripts/configure-node-security.sh
# Password login is intentionally retained by operator request.
PasswordAuthentication yes
PermitRootLogin no
MaxAuthTries 3
LoginGraceTime 30
AllowUsers $SSH_ALLOWED_USERS
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
DisableForwarding yes
PermitUserRC no
MaxStartups 10:30:30
PerSourceMaxStartups 3
EOF

  if ! sudo sshd -t; then
    if sudo test -f "$backup_path"; then
      sudo cp "$backup_path" "$config_path"
    else
      sudo rm -f "$config_path"
    fi
    err "Refusing to reload SSH because the hardened configuration failed validation"
  fi
  sudo systemctl reload ssh
}

configure_login_banner() {
  local path
  for path in /etc/issue /etc/issue.net; do
    backup_file_if_exists "$path"
    write_root_file "$path" 0644 <<'EOF'
Authorized access only. Activity on this system is monitored and recorded.
Disconnect immediately if you are not an authorized user.
EOF
  done
}

# Lynis DEB-0280, DEB-0811, PKGS-7370 and PKGS-7394. apt-listchanges uses the
# text frontend so interactive APT runs never stop in a pager.
configure_package_audit_tools() {
  printf '%s\n' 'apt-listchanges apt-listchanges/frontend select text' | sudo debconf-set-selections
  ensure_packages libpam-tmpdir apt-listchanges debsums apt-show-versions
  if ! sudo grep -Eq '^frontend[[:space:]]*=[[:space:]]*text$' /etc/apt/listchanges.conf 2>/dev/null; then
    sudo env DEBIAN_FRONTEND=noninteractive dpkg-reconfigure apt-listchanges >/dev/null
  fi
  # Weekly report-only package checksum verification; findings go to syslog.
  edit_root_file /etc/default/debsums 's/^CRON_CHECK=.*/CRON_CHECK=weekly/'
}

# Lynis PKGS-7346. Every kernel update leaves the previous kernel's
# configuration-only ("rc") packages behind; they hold no binaries. Other rc
# packages may be deliberately retained configuration and are left alone.
purge_residual_kernel_packages() {
  local -a residual=()
  mapfile -t residual < <(dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' 2>/dev/null | \
    awk '$1 == "rc" && $2 ~ /^linux-(image|modules|modules-extra|main-modules-zfs|headers)-[0-9]/ {print $2}')
  if [[ ${#residual[@]} -gt 0 ]]; then
    info "Purging configuration left by ${#residual[@]} removed kernel package(s)."
    sudo env DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq "${residual[@]}" >/dev/null
  fi
}

# Lynis AUTH-9262. pam_pwquality only applies when a password is changed; it
# never affects existing passwords, key-based login or service accounts.
configure_password_quality() {
  ensure_packages libpam-pwquality
  write_root_file /etc/security/pwquality.conf.d/90-swirl-cloud.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
minlen = 12
dictcheck = 1
usercheck = 1
EOF
}

# Lynis KRNL-6000, limited to values that Kubernetes does not depend on.
# Deliberately unchanged: ip_forward and rp_filter (pod routing), modules_disabled
# (CNI, iSCSI and Longhorn load modules), sysrq (console recovery), suid_dumpable
# (owned by apport) and log_martians (every new pod sandbox logs benign martians).
configure_kernel_hardening() {
  write_root_file /etc/sysctl.d/90-swirl-cloud-hardening.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
kernel.unprivileged_bpf_disabled = 1
net.core.bpf_jit_harden = 2
fs.protected_fifos = 2
fs.protected_regular = 2
fs.protected_symlinks = 1
fs.protected_hardlinks = 1
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
EOF
  sudo sysctl -q -p /etc/sysctl.d/90-swirl-cloud-hardening.conf
}

# Lynis NETW-3200: rare protocols no platform workload or Service uses. USB
# storage stays available because remote consoles attach recovery media as USB.
configure_unused_protocol_modules() {
  write_root_file /etc/modprobe.d/swirl-cloud-unused-protocols.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
blacklist dccp
install dccp /bin/false
blacklist rds
install rds /bin/false
blacklist sctp
install sctp /bin/false
blacklist tipc
install tipc /bin/false
EOF
}

# Lynis FILE-7524: root-only scheduler and SSH daemon configuration. Packaged
# paths get a dpkg statoverride so package upgrades keep the restricted mode.
configure_restricted_file_modes() {
  local mode path current owner group current_mode
  while read -r mode path; do
    sudo test -e "$path" || continue
    if dpkg -S "$path" >/dev/null 2>&1; then
      current="$(sudo dpkg-statoverride --list "$path" || true)"
      read -r owner group current_mode _ <<< "$current"
      if [[ "$owner" != root || "$group" != root || -z "$current_mode" ]] || \
          (( 8#$current_mode != 8#$mode )); then
        [[ -z "$current" ]] || sudo dpkg-statoverride --remove "$path" >/dev/null
        sudo dpkg-statoverride --update --add root root "$mode" "$path"
      fi
    fi
    sudo chown root:root "$path"
    sudo chmod "$mode" "$path"
  done <<'EOF'
0600 /etc/crontab
0600 /etc/ssh/sshd_config
0700 /etc/cron.d
0700 /etc/cron.hourly
0700 /etc/cron.daily
0700 /etc/cron.weekly
0700 /etc/cron.monthly
EOF
}

# Accepted Lynis findings. Each entry skips only the named check or value, so a
# new gap in the same test is still reported. Review every entry by its date.
configure_lynis_accepted_findings() {
  write_root_file /etc/lynis/custom.prf <<'EOF'
# Managed by scripts/configure-node-security.sh
# Accepted Lynis findings. Owner: platform administrators. Review by 2027-04-04.
# AUTH-9230: yescrypt hashing is stronger than the SHA-512 rounds this test reads.
skip-test=AUTH-9230
# AUTH-9328: UMASK 027 would make sudo/cron files and workspace sources group-only,
# breaking image COPY for non-root containers; user-private groups isolate users.
skip-test=AUTH-9328
# BOOT-5122: a GRUB password blocks remote console recovery.
skip-test=BOOT-5122
# FILE-6310: separate /home and /var partitions require reinstalling the host.
skip-test=FILE-6310
# USB-1000: remote consoles attach recovery media as USB storage.
skip-test=USB-1000
# LOGG-2154: Fluent Bit forwards the journal to Elasticsearch; Lynis only detects
# syslog-daemon forwarding. An off-host copy needs the Elasticsearch snapshot
# repository (TODO §14/§16).
skip-test=LOGG-2154
# SSH-7408: port 22 is kept for tooling; MaxSessions 4 leaves room for automation.
skip-test=SSH-7408:port
skip-test=SSH-7408:maxsessions
# KRNL-6000: Kubernetes routes pods (forwarding, loose rp_filter) and loads CNI,
# iSCSI and Longhorn modules; SysRq stays for console recovery; apport owns
# suid_dumpable; every new pod sandbox logs a benign martian.
skip-test=KRNL-6000:net.ipv4.conf.all.forwarding
skip-test=KRNL-6000:net.ipv4.conf.all.rp_filter
skip-test=KRNL-6000:kernel.modules_disabled
skip-test=KRNL-6000:kernel.sysrq
skip-test=KRNL-6000:fs.suid_dumpable
skip-test=KRNL-6000:net.ipv4.conf.all.log_martians
skip-test=KRNL-6000:net.ipv4.conf.default.log_martians
EOF
}

# Lynis SSH-7408 session options for every node. Client-alive probes detect dead
# sessions instead of TCP keepalives; a reload keeps existing sessions open.
configure_ssh_session_limits() {
  local config_path=/etc/ssh/sshd_config.d/41-swirl-cloud-sessions.conf
  local previous=""

  sudo test -f "$config_path" && previous="$(sudo cat "$config_path")"
  write_root_file "$config_path" 0600 <<'EOF'
# Managed by scripts/configure-node-security.sh
ClientAliveInterval 300
ClientAliveCountMax 2
TCPKeepAlive no
MaxSessions 4
LogLevel VERBOSE
EOF
  if ! sudo sshd -t; then
    if [[ -n "$previous" ]]; then
      printf '%s\n' "$previous" | write_root_file "$config_path" 0600
    else
      sudo rm -f "$config_path"
    fi
    err "Refusing to reload SSH because the session configuration failed validation"
  fi
  if [[ "$(sudo cat "$config_path")" != "$previous" ]] && systemctl is-active --quiet ssh; then
    sudo systemctl reload ssh
  fi
}

# node-exporter's textfile collector reads host-written metrics such as the
# managed Lynis run; the directory exists on every node so it never errors.
configure_metrics_textfile_directory() {
  sudo install -d -o root -g root -m 0755 /var/lib/node_exporter/textfile_collector
}

# Unattended security updates install packages but never reboot. Export the
# pending-reboot flag so Prometheus can alert when a reboot stays outstanding.
configure_reboot_required_metrics() {
  write_root_file /usr/local/sbin/swirl-cloud-reboot-metrics 0755 <<'EOF'
#!/bin/bash
# Managed by scripts/configure-node-security.sh
set -euo pipefail
metrics_dir=/var/lib/node_exporter/textfile_collector
metrics_file="$metrics_dir/swirl_cloud_reboot.prom"
flag=/var/run/reboot-required
required=0
since=0
if [[ -e "$flag" ]]; then
  required=1
  # Later updates rewrite the flag; keep the first time this reboot was seen.
  if [[ -r "$metrics_file" ]]; then
    since="$(awk '$1 == "swirl_cloud_reboot_required_since_timestamp_seconds" {print $2}' "$metrics_file")"
  fi
  [[ "$since" =~ ^[1-9][0-9]*$ ]] || since="$(stat -c %Y "$flag")"
fi
temporary="$(mktemp "$metrics_dir/.swirl_cloud_reboot.XXXXXX")"
{
  printf '# HELP swirl_cloud_reboot_required Whether installed updates require a reboot.\n'
  printf '# TYPE swirl_cloud_reboot_required gauge\n'
  printf 'swirl_cloud_reboot_required %s\n' "$required"
  printf '# HELP swirl_cloud_reboot_required_since_timestamp_seconds When the pending reboot was first requested, or 0.\n'
  printf '# TYPE swirl_cloud_reboot_required_since_timestamp_seconds gauge\n'
  printf 'swirl_cloud_reboot_required_since_timestamp_seconds %s\n' "$since"
} > "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" "$metrics_file"
EOF
  write_root_file /etc/systemd/system/swirl-cloud-reboot-metrics.service <<'EOF'
[Unit]
Description=Export the Swirl Cloud pending-reboot state for node-exporter

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/swirl-cloud-reboot-metrics
EOF
  write_root_file /etc/systemd/system/swirl-cloud-reboot-metrics.timer <<'EOF'
[Unit]
Description=Refresh the Swirl Cloud pending-reboot metric

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now swirl-cloud-reboot-metrics.timer >/dev/null
  sudo systemctl start swirl-cloud-reboot-metrics.service
}

# Lynis ACCT-9622/9628: process accounting and a small, watch-based audit rule
# set. Execve and time syscalls are not audited: CI pods and chrony would flood
# the log. Rules stay mutable (no -e 2) so reconciliation never needs a reboot;
# a full audit disk suspends auditing instead of halting the node.
configure_host_audit() {
  ensure_packages auditd acct
  write_root_file /etc/audit/rules.d/50-swirl-cloud.rules 0640 <<'EOF'
## Managed by scripts/configure-node-security.sh
## containerd/runc load a BPF device filter per container; skip those records.
-a never,exclude -F msgtype=BPF
## Ubuntu 26.04's who AppArmor profile denies the Rust coreutils binary reading
## /etc/passwd; SSH clients that poll who each second flood the log otherwise.
-a never,exclude -F exe=/usr/lib/cargo/bin/coreutils/who
-a always,exit -F path=/etc/passwd -F perm=wa -k identity
-a always,exit -F path=/etc/group -F perm=wa -k identity
-a always,exit -F path=/etc/shadow -F perm=wa -k identity
-a always,exit -F path=/etc/gshadow -F perm=wa -k identity
-a always,exit -F path=/etc/sudoers -F perm=wa -k privilege
-a always,exit -F dir=/etc/sudoers.d -F perm=wa -k privilege
-a always,exit -F path=/etc/ssh/sshd_config -F perm=wa -k sshd
-a always,exit -F dir=/etc/ssh/sshd_config.d -F perm=wa -k sshd
-a always,exit -F path=/etc/crontab -F perm=wa -k cron
-a always,exit -F dir=/etc/cron.d -F perm=wa -k cron
-a always,exit -F dir=/var/spool/cron -F perm=wa -k cron
-a always,exit -F dir=/etc/systemd/system -F perm=wa -k systemd
-a always,exit -F dir=/etc/rancher -F perm=wa -k k3s-config
-a always,exit -F dir=/etc/swirl-cloud -F perm=wa -k swirl-cloud
-a always,exit -F dir=/etc/ufw -F perm=wa -k firewall
-a always,exit -F path=/etc/localtime -F perm=wa -k time-change
-a always,exit -F arch=b64 -S init_module,finit_module,delete_module -k modules
EOF
  edit_root_file /etc/audit/auditd.conf 's/^max_log_file[[:space:]]*=.*/max_log_file = 32/; s/^num_logs[[:space:]]*=.*/num_logs = 8/'
  # Container churn writes roughly 250 MB of raw accounting a day; keep two weeks.
  edit_root_file /etc/logrotate.d/acct 's/^([[:space:]]*)rotate [0-9]+$/\1rotate 14/'
  sudo augenrules --load >/dev/null
  sudo systemctl enable --now auditd acct >/dev/null 2>&1
}

# Lynis HRDN-7230: weekly, report-only rkhunter scan. Recommends are skipped
# because they pull in a listening mail server. Warnings go to the journal and
# a node-exporter metric; package updates refresh the file-property baseline.
configure_rootkit_scan() {
  if ! dpkg -s rkhunter >/dev/null 2>&1; then
    apt_update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends rkhunter >/dev/null
  fi
  write_root_file /etc/rkhunter.conf.local 0640 <<'EOF'
# Managed by scripts/configure-node-security.sh
PKGMGR=DPKG
# PermitRootLogin no is set in sshd_config.d, which rkhunter does not read.
ALLOW_SSH_ROOT_USER=unset
# Java preferences, systemd's update stamp and systemd-resolved's backup.
ALLOWHIDDENDIR=/etc/.java
ALLOWHIDDENFILE=/etc/.updated
ALLOWHIDDENFILE=/etc/.resolv.conf.systemd-resolved.bak
EOF
  edit_root_file /etc/default/rkhunter 's/^APT_AUTOGEN=.*/APT_AUTOGEN="true"/'
  sudo rkhunter --config-check --nocolors >/dev/null || err "rkhunter rejected its managed configuration"
  if ! sudo test -s /var/lib/rkhunter/db/rkhunter.dat; then
    sudo rkhunter --propupd --nocolors >/dev/null
  fi
  write_root_file /usr/local/sbin/swirl-cloud-rkhunter-weekly 0750 <<'EOF'
#!/bin/bash
# Managed by scripts/configure-node-security.sh
set -uo pipefail
metrics_dir=/var/lib/node_exporter/textfile_collector
output="$(rkhunter --check --sk --rwo --nocolors 2>&1)"
status=$?
[[ -z "$output" ]] || printf '%s\n' "$output"
warnings="$(grep -c '^Warning:' <<< "$output")"
temporary="$(mktemp "$metrics_dir/.swirl_cloud_rkhunter.XXXXXX")"
{
  printf '# HELP swirl_cloud_rkhunter_last_run_timestamp_seconds Last rkhunter scan.\n'
  printf '# TYPE swirl_cloud_rkhunter_last_run_timestamp_seconds gauge\n'
  printf 'swirl_cloud_rkhunter_last_run_timestamp_seconds %s\n' "$(date +%s)"
  printf '# HELP swirl_cloud_rkhunter_exit_code Exit code of the last scan (1 means warnings).\n'
  printf '# TYPE swirl_cloud_rkhunter_exit_code gauge\n'
  printf 'swirl_cloud_rkhunter_exit_code %s\n' "$status"
  printf '# HELP swirl_cloud_rkhunter_warnings Warnings reported by the last scan.\n'
  printf '# TYPE swirl_cloud_rkhunter_warnings gauge\n'
  printf 'swirl_cloud_rkhunter_warnings %s\n' "$warnings"
} > "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" "$metrics_dir/swirl_cloud_rkhunter.prom"
# Report-only: warnings are findings to review, not a failed run.
(( status <= 1 )) || exit "$status"
EOF
  write_root_file /etc/systemd/system/swirl-cloud-rkhunter.service <<'EOF'
[Unit]
Description=Swirl Cloud weekly rkhunter scan (report-only)

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/swirl-cloud-rkhunter-weekly
Nice=10
IOSchedulingClass=idle
EOF
  write_root_file /etc/systemd/system/swirl-cloud-rkhunter.timer <<'EOF'
[Unit]
Description=Run the Swirl Cloud rkhunter scan weekly

[Timer]
OnCalendar=Sun *-*-* 04:30:00
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now swirl-cloud-rkhunter.timer >/dev/null
}

# Record the result of host maintenance services for Prometheus. Drop-ins are
# written even before a unit exists, so installation order does not matter;
# the collector path is optional so a missing directory never blocks a unit.
configure_host_unit_metrics() {
  local unit
  write_root_file /usr/local/sbin/swirl-cloud-unit-metrics 0755 <<'EOF'
#!/bin/bash
# Managed by scripts/configure-node-security.sh
# Called from ExecStopPost; systemd provides SERVICE_RESULT for the run.
set -euo pipefail
unit="$1"
[[ "$unit" =~ ^[A-Za-z0-9@._-]+$ ]] || exit 1
metrics_dir=/var/lib/node_exporter/textfile_collector
metrics_file="$metrics_dir/swirl_cloud_unit_${unit//[^A-Za-z0-9]/_}.prom"
now="$(date +%s)"
success=0
last_success=""
[[ "${SERVICE_RESULT:-}" != success ]] || success=1
if [[ -r "$metrics_file" ]]; then
  last_success="$(awk '$1 ~ /^swirl_cloud_systemd_unit_last_success_timestamp_seconds/ {print $2}' "$metrics_file")"
fi
(( success == 0 )) || last_success="$now"
temporary="$(mktemp "$metrics_dir/.swirl_cloud_unit.XXXXXX")"
{
  printf '# HELP swirl_cloud_systemd_unit_last_run_timestamp_seconds Last run of a host maintenance service.\n'
  printf '# TYPE swirl_cloud_systemd_unit_last_run_timestamp_seconds gauge\n'
  printf 'swirl_cloud_systemd_unit_last_run_timestamp_seconds{unit="%s"} %s\n' "$unit" "$now"
  printf '# HELP swirl_cloud_systemd_unit_last_run_success Whether the last run succeeded.\n'
  printf '# TYPE swirl_cloud_systemd_unit_last_run_success gauge\n'
  printf 'swirl_cloud_systemd_unit_last_run_success{unit="%s"} %s\n' "$unit" "$success"
  if [[ -n "$last_success" ]]; then
    printf '# HELP swirl_cloud_systemd_unit_last_success_timestamp_seconds Last successful run.\n'
    printf '# TYPE swirl_cloud_systemd_unit_last_success_timestamp_seconds gauge\n'
    printf 'swirl_cloud_systemd_unit_last_success_timestamp_seconds{unit="%s"} %s\n' "$unit" "$last_success"
  fi
} > "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" "$metrics_file"
EOF
  # "+" runs the hook with full privileges, also for units with their own user.
  for unit in swirl-cloud-k3s-backup.service swirl-cloud-vault-unseal.service dailyaidecheck.service; do
    write_root_file "/etc/systemd/system/$unit.d/50-swirl-cloud-metrics.conf" <<'EOF'
# Managed by scripts/configure-node-security.sh
[Service]
ReadWritePaths=-/var/lib/node_exporter/textfile_collector
ExecStopPost=-+/usr/local/sbin/swirl-cloud-unit-metrics %n
EOF
  done
  sudo systemctl daemon-reload
}

# Lynis FINT-4350: weekly, report-only AIDE check through the packaged
# dailyaidecheck units. Cluster, storage, container, log and backup paths churn
# constantly and are excluded; in home directories only ~/.ssh and login shell
# files are monitored. Package installs and
# updates are filtered out, and each remaining change is reported once to the
# journal (there is no mailer). The first database build runs asynchronously.
configure_file_integrity() {
  if ! dpkg -s aide-common >/dev/null 2>&1; then
    apt_update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends aide aide-common >/dev/null
  fi
  write_root_file /etc/aide/aide.conf.d/98_swirl-cloud <<'EOF'
# Managed by scripts/configure-node-security.sh
!/var/lib/(rancher|longhorn|kubelet|containerd|docker|cni|fluent-bit|node_exporter)(/|$)
!/var/lib/swirl-cloud/filebeat-lynis(/|$)
!/var/log/(pods|containers|audit|account|lynis-reports|journal)(/|$)
!/var/backups/swirl-cloud(/|$)
!/run/.
!/var/lib/crowdsec/data(/|$)
!/(var/)?tmp/.
# Home directories: only ~/.ssh and login shell files are monitored.
!/home/[^/]+/(?!\.ssh(/|$)|\.(bashrc|profile|bash_profile|bash_logout)$)
EOF
  write_root_file /usr/local/sbin/swirl-cloud-aide-report 0755 <<'EOF'
#!/bin/sh
# Managed by scripts/configure-node-security.sh
# dailyaidecheck's MAILCMD: ignore the mail arguments and log the report.
exec systemd-cat -t aide-report
EOF
  edit_root_file /etc/default/aide 's/^COPYNEWDB=.*/COPYNEWDB=yes/; s/^FILTERUPDATES=.*/FILTERUPDATES=yes/; s/^FILTERINSTALLATIONS=.*/FILTERINSTALLATIONS=yes/'
  if sudo grep -q '^MAILCMD=' /etc/default/aide; then
    edit_root_file /etc/default/aide 's#^MAILCMD=.*#MAILCMD=/usr/local/sbin/swirl-cloud-aide-report#'
  else
    printf 'MAILCMD=/usr/local/sbin/swirl-cloud-aide-report\n' | sudo tee -a /etc/default/aide >/dev/null
  fi
  write_root_file /etc/systemd/system/dailyaidecheck.timer.d/50-swirl-cloud-weekly.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
[Timer]
OnCalendar=
OnCalendar=Sun *-*-* 02:50:00
RandomizedDelaySec=1h
EOF
  sudo aide --config=/etc/aide/aide.conf --config-check >/dev/null || err "AIDE rejected its managed configuration"
  sudo systemctl daemon-reload
  sudo systemctl enable --now dailyaidecheck.timer >/dev/null
  if ! sudo test -s /var/lib/aide/aide.db && ! systemctl is-active --quiet swirl-cloud-aide-init.service; then
    info "Building the initial AIDE database in the background (swirl-cloud-aide-init.service)."
    sudo systemd-run --quiet --unit=swirl-cloud-aide-init --property=Nice=10 \
      --property=IOSchedulingClass=idle /usr/sbin/aideinit --yes --force
  fi
}

# Role- and exposure-independent controls that never touch the firewall or
# Kubernetes networking, so they can be reconciled on a live host. SSH changes
# are limited to validated session options.
configure_host_baseline() {
  configure_login_banner
  configure_metrics_textfile_directory
  configure_reboot_required_metrics
  configure_host_unit_metrics
  configure_package_audit_tools
  purge_residual_kernel_packages
  configure_password_quality
  configure_kernel_hardening
  configure_host_audit
  configure_rootkit_scan
  configure_file_integrity
  configure_unused_protocol_modules
  configure_restricted_file_modes
  configure_lynis_accepted_findings
  configure_ssh_session_limits
  info "Host baseline applied."
}

disable_unused_rpcbind() {
  # NFSv4 clients do not require rpcbind. Keep it only when this node is
  # actively using an older NFS mount; otherwise close the unused listener.
  if findmnt -rn -t nfs,nfs4 >/dev/null 2>&1; then
    warn "An NFS mount is active; leaving rpcbind unchanged."
    return 0
  fi

  if systemctl list-unit-files rpcbind.service rpcbind.socket --no-legend 2>/dev/null | grep -q rpcbind; then
    sudo systemctl disable --now rpcbind.socket rpcbind.service >/dev/null 2>&1 || true
  fi
}

configure_log_retention() {
  local cloud_init_config="/etc/logrotate.d/cloud-init"
  local duplicate_config="/etc/logrotate.d/cloud-init-base"
  local legacy_backup

  write_root_file /etc/systemd/journald.conf.d/99-swirl-cloud-retention.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
Storage=persistent
SystemMaxUse=4G
MaxRetentionSec=3month
Compress=yes
Seal=yes
EOF

  backup_file_if_exists /etc/logrotate.d/rsyslog
  write_root_file /etc/logrotate.d/rsyslog <<'EOF'
# Managed by scripts/configure-node-security.sh
# Keep authentication evidence for approximately three months while retaining
# the higher-volume general system logs for four weeks.
/var/log/auth.log
{
    rotate 13
    weekly
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate
    endscript
}

/var/log/syslog
/var/log/mail.log
/var/log/kern.log
/var/log/user.log
/var/log/cron.log
{
    rotate 4
    weekly
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate
    endscript
}
EOF

  # Ubuntu releases that temporarily ship both packages can define the same
  # cloud-init log twice, causing every logrotate run to fail. Preserve the
  # duplicate outside logrotate's accepted filename pattern.
  if sudo test -f "$cloud_init_config" && sudo test -f "$duplicate_config" && \
     sudo cmp -s "$cloud_init_config" "$duplicate_config"; then
    sudo install -d -o root -g root -m 0700 /var/backups/swirl-cloud/config/etc/logrotate.d
    sudo mv "$duplicate_config" /var/backups/swirl-cloud/config/etc/logrotate.d/cloud-init-base.disabled-swirl-cloud
  fi

  # Older revisions left backup files inside logrotate.d, where logrotate
  # interpreted them as active policies. Move those backups out of the include
  # directory before validating the complete configuration.
  while IFS= read -r legacy_backup; do
    sudo install -d -o root -g root -m 0700 /var/backups/swirl-cloud/config/etc/logrotate.d
    sudo mv "$legacy_backup" "/var/backups/swirl-cloud/config/etc/logrotate.d/$(basename "$legacy_backup")"
  done < <(sudo find /etc/logrotate.d -maxdepth 1 -type f -name '*.bak-*' -print)

  if sudo logrotate --debug /etc/logrotate.conf 2>&1 | grep -q '^error:'; then
    err "The generated logrotate configuration failed validation"
  fi
  sudo systemctl restart systemd-journald
  sudo systemctl reset-failed logrotate.service
  sudo systemctl start logrotate.service
}

configure_fail2ban() {
  ensure_packages fail2ban
  write_fail2ban_config
  sudo fail2ban-client -t >/dev/null
  ensure_service fail2ban
}

configure_crowdsec() {
  ensure_crowdsec_repo
  ensure_packages crowdsec crowdsec-firewall-bouncer-nftables

  configure_crowdsec_inotify_limits
  write_crowdsec_acquisitions
  write_crowdsec_profiles
  sudo cscli hub update >/dev/null
  ensure_crowdsec_collection crowdsecurity/linux
  ensure_crowdsec_collection crowdsecurity/sshd
  write_crowdsec_ssh_time_based_scenario
  sudo crowdsec -t -c /etc/crowdsec/config.yaml >/dev/null
  ensure_service crowdsec
  write_crowdsec_bouncer_config "$(get_crowdsec_bouncer_api_key)"
  ensure_service crowdsec-firewall-bouncer
}

configure_lynis_control_plane() {
  ensure_packages lynis openssh-client
  if [[ -x "$SCRIPT_DIR/audit-cluster-nodes.sh" ]]; then
    sudo install -D -o root -g root -m 0755 \
      "$SCRIPT_DIR/audit-cluster-nodes.sh" /usr/local/sbin/swirl-cloud-audit-nodes
  fi
  [[ -x "$SCRIPT_DIR/configure-lynis-schedule.sh" ]] || \
    err "The Lynis schedule reconciler is missing or not executable"
  sudo "$SCRIPT_DIR/configure-lynis-schedule.sh"
  if ! sudo test -s /var/log/lynis-report.dat; then
    info "Running the initial Lynis baseline audit..."
    sudo systemctl start swirl-cloud-lynis.service
  fi
}

remove_internet_only_tools() {
  sudo fail2ban-client stop >/dev/null 2>&1 || true
  sudo systemctl disable --now fail2ban crowdsec crowdsec-firewall-bouncer \
    crowdsec-firewall-bouncer.service >/dev/null 2>&1 || true
  remove_packages_if_installed \
    fail2ban crowdsec crowdsec-firewall-bouncer-nftables crowdsec-firewall-bouncer-iptables
}

remove_worker_lynis() {
  remove_packages_if_installed lynis
}

show_status() {
  sudo ufw status verbose || true

  if command -v fail2ban-client >/dev/null 2>&1; then
    sudo fail2ban-client status sshd 2>/dev/null || sudo fail2ban-client status || true
  fi

  if command -v cscli >/dev/null 2>&1; then
    sudo cscli metrics 2>/dev/null || true
  fi

  if command -v lynis >/dev/null 2>&1; then
    sudo lynis show version 2>/dev/null || true
  fi
}

command -v sudo >/dev/null 2>&1 || err "sudo is required"
SERVER_EXPOSURE="$(normalize_server_exposure "$SERVER_EXPOSURE")" || err "server exposure must be 'internet' or 'local'"
NODE_ROLE="$(normalize_node_role "$NODE_ROLE")" || err "node role must be 'control-plane' or 'worker'"
[[ "$HARDENED_SSH_PORT" =~ ^[0-9]+$ && "$HARDENED_SSH_PORT" -ge 1 && "$HARDENED_SSH_PORT" -le 65535 ]] || \
  err "SSH port must be an integer from 1 to 65535"

if [[ "$HOST_BASELINE_ONLY" == "true" ]]; then
  configure_host_baseline
  exit 0
fi

if [[ "$APPLY" != "true" ]]; then
  info "Audit mode (no changes). Run with --apply to enforce rules."
  show_status
  exit 0
fi

inherit_private_control_plane_policy

if [[ "$PRIVATE_CONTROL_PLANE" == "true" && "$NODE_ROLE" != "control-plane" ]]; then
  err "--private-control-plane requires --node-role control-plane"
fi
if private_node_policy; then
  [[ "$SERVER_EXPOSURE" == "local" ]] || err "Workers and additional private control planes require local/private exposure"
  [[ -n "$K3S_NODE_NETWORK_CIDR" ]] || err "K3S_NODE_NETWORK_CIDR is required when configuring a private node firewall"
  trusted_private_cidr "$K3S_NODE_NETWORK_CIDR" || \
    err "K3S_NODE_NETWORK_CIDR must be RFC1918 or Tailscale 100.64.0.0/10"
  [[ -n "$CONTROL_PLANE_IP" ]] || err "CONTROL_PLANE_IP is required so private-node SSH can be restricted to the bootstrap control plane"
  trusted_private_ipv4 "$CONTROL_PLANE_IP" || \
    err "CONTROL_PLANE_IP must be an RFC1918 or Tailscale IPv4 address"
  if [[ "$K3S_NODE_NETWORK_CIDR" == "100.64.0.0/10" ]]; then
    # Cross-provider workers may need a provider address for initial bootstrap
    # and outbound updates. configure_tailscale_firewall_integration runs before
    # any UFW reset and refuses to continue until tailscale0 is ready.
    info "Tailscale worker transport selected; validating the overlay before closing public ingress."
  else
    # vRack or LAN addressing is configured and private SSH is proven before
    # UFW. A provider NIC may remain for bootstrap/outbound traffic, but
    # receives no inbound allowance in configure_ufw.
    info "vRack/LAN private node selected; validating private SSH before closing public ingress."
  fi
fi

if [[ "$RECONCILE_CONTROL_PLANE_SSH" == "true" ]]; then
  reconcile_control_plane_ssh
  exit 0
fi
prepare_control_plane_ssh_policy

# Search workloads need this node setting; application init containers must
# never receive privileged access just to set a host-wide sysctl.
current_map_count="$(sysctl -n vm.max_map_count)"
if (( current_map_count < 1048576 )); then
  current_map_count=1048576
fi
printf 'vm.max_map_count=%s\n' "$current_map_count" | write_root_file /etc/sysctl.d/99-swirl-cloud-search.conf
sudo sysctl -p /etc/sysctl.d/99-swirl-cloud-search.conf >/dev/null
configure_host_baseline

configure_tailscale_firewall_integration
validate_worker_private_ssh_before_firewall
configure_ufw
if private_node_policy; then persist_control_plane_ssh_policy; fi
make_ufw_authoritative_for_tailscale
if [[ "$SERVER_EXPOSURE" == "internet" ]]; then
  configure_sshd
  disable_unused_rpcbind
  configure_fail2ban
  configure_crowdsec
  configure_log_retention
  info "Internet-facing SSH protection applied with Fail2ban and CrowdSec."
else
  remove_internet_only_tools
  info "Local-only node: UFW applied; Fail2ban and CrowdSec are not installed."
fi

if [[ "$NODE_ROLE" == "control-plane" ]]; then
  configure_lynis_control_plane
  info "Lynis is installed on the control plane for local and remote-node audits."
else
  remove_worker_lynis
  info "Worker node: Lynis is not installed persistently."
fi

info "Host security policy applied for $SERVER_EXPOSURE $NODE_ROLE node."
show_status
