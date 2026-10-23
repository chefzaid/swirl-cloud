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

# shellcheck source=lib/node-access-policy.sh
source "$SCRIPT_DIR/lib/node-access-policy.sh"

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

# Leaves an identical file untouched so reruns do not change inodes and
# timestamps, which AIDE and auditd would report.
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

# shellcheck source=lib/intrusion-config.sh
source "$SCRIPT_DIR/lib/intrusion-config.sh"

# shellcheck source=lib/node-firewall.sh
source "$SCRIPT_DIR/lib/node-firewall.sh"

# shellcheck source=lib/host-baseline.sh
source "$SCRIPT_DIR/lib/host-baseline.sh"

configure_fail2ban() {
  ensure_packages fail2ban
  write_fail2ban_config
  sudo fail2ban-client -t >/dev/null
  ensure_service fail2ban
}

# Export Fail2ban and CrowdSec counts so Prometheus can alert when protection
# stops or an attack starts. Banned addresses stay on the host.
configure_intrusion_metrics() {
  write_root_file /usr/local/sbin/swirl-cloud-intrusion-metrics 0755 <<'EOF'
#!/bin/bash
# Managed by scripts/configure-node-security.sh
set -euo pipefail
metrics_dir=/var/lib/node_exporter/textfile_collector
metrics_file="$metrics_dir/swirl_cloud_intrusion.prom"
temporary="$(mktemp "$metrics_dir/.swirl_cloud_intrusion.XXXXXX")"
trap 'rm -f "$temporary"' EXIT
up() { systemctl is-active --quiet "$1" && echo 1 || echo 0; }
{
  printf '# HELP swirl_cloud_intrusion_service_up Whether a host intrusion-prevention service is active.\n'
  printf '# TYPE swirl_cloud_intrusion_service_up gauge\n'
  for service in fail2ban crowdsec crowdsec-firewall-bouncer; do
    printf 'swirl_cloud_intrusion_service_up{service="%s"} %s\n' "$service" "$(up "$service")"
  done
  if fail2ban-client ping >/dev/null 2>&1; then
    # Each metric family is written as one contiguous group, as the text format requires.
    jails="$(fail2ban-client status | sed -n 's/.*Jail list:[[:space:]]*//p' | tr -d ' ' | tr ',' ' ')"
    declare -A jail_status=()
    for jail in $jails; do
      [[ "$jail" =~ ^[A-Za-z0-9_-]+$ ]] || continue
      jail_status[$jail]="$(fail2ban-client status "$jail")"
    done
    for family in "banned|gauge|Addresses currently banned by a Fail2ban jail.|Currently banned" \
                  "banned_total|counter|Addresses banned by a Fail2ban jail since it started.|Total banned" \
                  "failed_total|counter|Failed attempts seen by a Fail2ban jail since it started.|Total failed"; do
      IFS='|' read -r suffix type help field <<< "$family"
      printf '# HELP swirl_cloud_fail2ban_%s %s\n# TYPE swirl_cloud_fail2ban_%s %s\n' "$suffix" "$help" "$suffix" "$type"
      for jail in "${!jail_status[@]}"; do
        printf 'swirl_cloud_fail2ban_%s{jail="%s"} %s\n' "$suffix" "$jail" \
          "$(awk -F':' -v field="$field" 'index($0, field) {gsub(/[^0-9]/, "", $2); print $2+0}' <<< "${jail_status[$jail]}")"
      done
    done
  fi
  if command -v cscli >/dev/null 2>&1 && decisions="$(cscli decisions list -o json 2>/dev/null)"; then
    printf '# HELP swirl_cloud_crowdsec_decisions Active CrowdSec decisions by scenario.\n'
    printf '# TYPE swirl_cloud_crowdsec_decisions gauge\n'
    jq -r '[.[]? | .decisions[]? | .scenario // "unknown"] | group_by(.) | .[] |
      "swirl_cloud_crowdsec_decisions{scenario=\"\(.[0] | gsub("[^A-Za-z0-9_./-]"; "_"))\"} \(length)"' <<< "${decisions:-[]}"
  fi
  # UFW active and both INPUT policies dropping; a failed reload once left it off.
  firewall=0
  if ufw status 2>/dev/null | grep -qx 'Status: active' && iptables -S INPUT | grep -qx -- '-P INPUT DROP' &&
      { ! grep -qx 'IPV6=yes' /etc/default/ufw || ip6tables -S INPUT | grep -qx -- '-P INPUT DROP'; }; then
    firewall=1
  fi
  printf '# HELP swirl_cloud_host_firewall_active Whether UFW is active with default-deny INPUT for IPv4 and IPv6.\n'
  printf '# TYPE swirl_cloud_host_firewall_active gauge\n'
  printf 'swirl_cloud_host_firewall_active %s\n' "$firewall"
  printf '# HELP swirl_cloud_intrusion_metrics_timestamp_seconds When these metrics were written.\n'
  printf '# TYPE swirl_cloud_intrusion_metrics_timestamp_seconds gauge\n'
  printf 'swirl_cloud_intrusion_metrics_timestamp_seconds %s\n' "$(date +%s)"
} > "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" "$metrics_file"
trap - EXIT
EOF
  write_root_file /etc/systemd/system/swirl-cloud-intrusion-metrics.service <<'EOF'
[Unit]
Description=Export Swirl Cloud Fail2ban and CrowdSec activity for node-exporter

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/swirl-cloud-intrusion-metrics
EOF
  write_root_file /etc/systemd/system/swirl-cloud-intrusion-metrics.timer <<'EOF'
[Unit]
Description=Refresh the Swirl Cloud intrusion-prevention metrics

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now swirl-cloud-intrusion-metrics.timer >/dev/null
  sudo systemctl start swirl-cloud-intrusion-metrics.service
}

remove_intrusion_metrics() {
  sudo systemctl disable --now swirl-cloud-intrusion-metrics.timer >/dev/null 2>&1 || true
  sudo rm -f /etc/systemd/system/swirl-cloud-intrusion-metrics.timer /etc/systemd/system/swirl-cloud-intrusion-metrics.service \
    /usr/local/sbin/swirl-cloud-intrusion-metrics /var/lib/node_exporter/textfile_collector/swirl_cloud_intrusion.prom
  sudo systemctl daemon-reload
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
    # A provider address may stay for bootstrap and outbound traffic.
    # configure_tailscale_firewall_integration requires tailscale0 before UFW.
    info "Tailscale worker transport selected; validating the overlay before closing public ingress."
  else
    # Private SSH is proven before UFW. A provider NIC may stay for outbound
    # traffic but gets no inbound rule in configure_ufw.
    info "vRack/LAN private node selected; validating private SSH before closing public ingress."
  fi
fi

if [[ "$RECONCILE_CONTROL_PLANE_SSH" == "true" ]]; then
  reconcile_control_plane_ssh
  exit 0
fi
prepare_control_plane_ssh_policy

# Search workloads need this; setting it here keeps init containers unprivileged.
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
  configure_intrusion_metrics
  configure_log_retention
  info "Internet-facing SSH protection applied with Fail2ban and CrowdSec."
else
  remove_internet_only_tools
  remove_intrusion_metrics
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
