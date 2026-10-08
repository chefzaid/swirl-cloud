#!/usr/bin/env bash
# Give the Kubernetes API one address that survives the loss of any control
# plane: a keepalived VIP on vRack/LAN or a Tailscale Service on Tailscale.
set -euo pipefail
set +x
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAN_CONFIG=/etc/rancher/k3s/config.yaml.d/25-swirl-cloud-api-endpoint.yaml
K3S_TLS_DIR=/var/lib/rancher/k3s/server/tls
LIBEXEC_DIR=/usr/local/libexec/swirl-cloud
READY_CHECK="$LIBEXEC_DIR/k3s-api-ready"
SERVICE_WATCH="$LIBEXEC_DIR/k3s-api-endpoint-watch"
SERVICE_WATCH_UNIT=/etc/systemd/system/swirl-cloud-api-endpoint.service
KEEPALIVED_CONFIG=/etc/keepalived/keepalived.conf
RECORD_NAMESPACE=kube-system
RECORD_NAME=swirl-cloud-api-endpoint
MIN_TAILSCALE_VERSION=1.86.0

info()  { printf '\033[0;32m[INFO]\033[0m  %s\n' "$*" >&2; }
warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }
error() { printf '\033[0;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Give the K3s API a stable private address served by every control plane.

  vrack, lan  keepalived moves a VIP from the node network to a control plane
              whose local API is ready (VRRP unicast between control planes).
  tailscale   every control plane hosts one Tailscale Service on TCP 6443 and
              drains it while its local API is not ready.

Every server's certificate lists the endpoint address (and Tailscale name), so
kubectl and joining nodes verify it normally.

Usage (on a control plane with kubectl, after enrollment):
  configure-k3s-api-endpoint.sh --reconcile [--transport vrack|lan|tailscale]
      [--address IP] [--hostname NAME] [--service svc:NAME]
      --node-network-cidr CIDR --control-plane-ip IP
      [--ssh-user USER] [--ssh-port PORT] [--identity-file FILE]
  configure-k3s-api-endpoint.sh --kubeconfig PATH
  configure-k3s-api-endpoint.sh --verify

--reconcile needs an odd membership of at least three control planes. It
configures every control plane over private SSH, restarts K3s one server at a
time only where the served certificate lacks the endpoint, keeping a fresh etcd
majority, and records the endpoint in kube-system/swirl-cloud-api-endpoint.
Omitted endpoint options are read from that record. Enrollment
(add-node.sh --role control-plane) runs it automatically.

--kubeconfig writes a new administrator kubeconfig (mode 0600) that uses the
endpoint. --verify checks the endpoint from this control plane.

Modes used by enrollment: --write-sans ADDRESS[,HOSTNAME],
--check-sans ADDRESS[,HOSTNAME], --apply-local and
--validate-vip VIP NODE_CIDR NODE_ADDRESSES.
EOF
}

# ---------- Pure helpers (also exercised by tests/test-k3s-api-endpoint.sh) ----------

valid_service_name() {
    [[ ${#1} -le 67 && "$1" =~ ^svc:[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]
}

valid_dns_name() {
    [[ ${#1} -le 253 && "$1" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]
}

render_san_config() {
    local address="$1" hostname="${2:-}"
    printf '# Managed by scripts/configure-k3s-api-endpoint.sh\n'
    printf 'tls-san+:\n  - "%s"\n' "$address"
    [[ -z "$hostname" ]] || printf '  - "%s"\n' "$hostname"
}

# A deterministic VRRP router ID (1-255) for the VIP; adverts are unicast, so
# it only has to agree between the control planes of one cluster.
vrrp_router_id() {
    local c d
    IFS='.' read -r _ _ c d <<< "$1"
    printf '%d\n' $(( ((10#$c << 8) + 10#$d) % 255 + 1 ))
}

# Rejects a VIP that could be taken by, or route around, a cluster node.
validate_vip() {
    local vip="$1" cidr="$2" node_addresses="$3" network broadcast prefix value mask
    if ! trusted_private_ipv4 "$vip" || tailscale_ipv4 "$vip"; then
        printf 'The API VIP must be an RFC1918 IPv4 address: %s\n' "$vip"
        return 1
    fi
    cidr_contains_ip "$cidr" "$vip" || { printf 'The API VIP %s is outside the node network %s\n' "$vip" "$cidr"; return 1; }
    prefix=$((10#${cidr#*/}))
    if (( prefix < 31 )); then
        mask=$(( (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF ))
        value="$(ipv4_to_int "$vip")"
        network=$(( value & mask ))
        broadcast=$(( network | (~mask & 0xFFFFFFFF) ))
        (( value != network && value != broadcast )) || {
            printf 'The API VIP %s is the network or broadcast address of %s\n' "$vip" "$cidr"
            return 1
        }
    fi
    [[ ",$node_addresses," != *",$vip,"* ]] || { printf 'The API VIP %s is already a node address\n' "$vip"; return 1; }
}

render_keepalived_config() {
    local node_name="$1" interface="$2" self_ip="$3" vip="$4" prefix="$5" peers="$6" peer
    local -a peer_list=()
    IFS=',' read -r -a peer_list <<< "$peers"
    cat <<EOF
# Managed by scripts/configure-k3s-api-endpoint.sh
global_defs {
    router_id ${node_name}
    enable_script_security
    script_user root
    vrrp_garp_master_refresh 60
}

vrrp_script k3s_api_ready {
    script "${READY_CHECK}"
    interval 2
    timeout 3
    fall 2
    rise 2
}

vrrp_instance swirl_cloud_api {
    state BACKUP
    nopreempt
    interface ${interface}
    virtual_router_id $(vrrp_router_id "$vip")
    priority 100
    advert_int 1
    unicast_src_ip ${self_ip}
    unicast_fault_no_peer
    unicast_peer {
EOF
    for peer in "${peer_list[@]}"; do
        printf '        %s\n' "$peer"
    done
    cat <<EOF
    }
    virtual_ipaddress {
        ${vip}/${prefix} dev ${interface}
    }
    track_script {
        k3s_api_ready
    }
}
EOF
}

# The scheduler identity can read /readyz but holds none of the admin rights.
render_ready_check() {
    cat <<EOF
#!/bin/sh
# Managed by scripts/configure-k3s-api-endpoint.sh: succeeds while this server's API is ready.
exec curl -fsS --max-time 2 -o /dev/null \\
    --cacert ${K3S_TLS_DIR}/server-ca.crt \\
    --cert ${K3S_TLS_DIR}/client-scheduler.crt \\
    --key ${K3S_TLS_DIR}/client-scheduler.key \\
    https://127.0.0.1:6443/readyz
EOF
}

render_service_watch() {
    cat <<EOF
#!/bin/bash
# Managed by scripts/configure-k3s-api-endpoint.sh: host the Tailscale Service
# only while this server's API is ready, so clients are routed to healthy servers.
set -u
service="\$1" state="" failures=0
while :; do
    if ${READY_CHECK}; then
        failures=0
        if [[ "\$state" != advertised ]] && tailscale serve advertise "\$service" >/dev/null; then
            state=advertised
        fi
    else
        failures=\$((failures + 1))
        if (( failures >= 3 )) && [[ "\$state" != drained ]] && tailscale serve drain "\$service" >/dev/null; then
            state=drained
        fi
    fi
    sleep 5
done
EOF
}

render_service_watch_unit() {
    cat <<EOF
# Managed by scripts/configure-k3s-api-endpoint.sh
[Unit]
Description=Host the Kubernetes API Tailscale Service while the local API is ready
After=tailscaled.service k3s.service
Wants=tailscaled.service

[Service]
Type=simple
ExecStart=${SERVICE_WATCH} $1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
}

version_at_least() {
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" == "$2" ]]
}

# ---------- Host-local operations (run as an account with passwordless sudo) ----------

# Installs stdin at $1 with mode $2; sets CONTENT_CHANGED.
install_content() {
    local path="$1" mode="$2" temporary
    temporary="$(mktemp)"
    cat > "$temporary"
    CONTENT_CHANGED=false
    if ! sudo test -f "$path" || ! sudo cmp -s "$temporary" "$path"; then
        sudo install -D -o root -g root -m "$mode" "$temporary" "$path"
        CONTENT_CHANGED=true
    fi
    rm -f -- "$temporary"
}

split_endpoint() {
    ENDPOINT_ADDRESS="${1%%,*}"
    ENDPOINT_HOSTNAME=""
    [[ "$1" != *,* ]] || ENDPOINT_HOSTNAME="${1#*,}"
    valid_ipv4 "$ENDPOINT_ADDRESS" || error "Invalid endpoint address: $ENDPOINT_ADDRESS"
    [[ -z "$ENDPOINT_HOSTNAME" ]] || valid_dns_name "$ENDPOINT_HOSTNAME" || error "Invalid endpoint hostname: $ENDPOINT_HOSTNAME"
}

# Written before K3s first starts on a joining server, or picked up by the next
# restart of an existing one.
write_sans() {
    split_endpoint "$1"
    render_san_config "$ENDPOINT_ADDRESS" "$ENDPOINT_HOSTNAME" | install_content "$SAN_CONFIG" 0644
    [[ "$CONTENT_CHANGED" != true ]] || info "K3s will serve the API endpoint $1 after its next start."
}

check_sans() {
    local served
    split_endpoint "$1"
    served="$(openssl s_client -connect 127.0.0.1:6443 -servername kubernetes </dev/null 2>/dev/null | \
        openssl x509 -noout -ext subjectAltName 2>/dev/null)" || return 1
    grep -Eq "IP Address:${ENDPOINT_ADDRESS//./\\.}(,|$)" <<< "$served" || return 1
    [[ -z "$ENDPOINT_HOSTNAME" ]] || grep -Eq "DNS:${ENDPOINT_HOSTNAME//./\\.}(,|$)" <<< "$served"
}

apply_local() {
    local transport="" address="" hostname="" service="" private_ip="" peers="" node_name interface subnet prefix version
    while (( $# )); do
        case "$1" in
            --transport|--address|--hostname|--service|--private-ip|--peers)
                [[ $# -ge 2 ]] || error "Missing value for $1"
                case "$1" in
                    --transport) transport="$2" ;; --address) address="$2" ;; --hostname) hostname="$2" ;;
                    --service) service="$2" ;; --private-ip) private_ip="$2" ;; --peers) peers="$2" ;;
                esac
                shift ;;
            *) error "Unknown option: $1" ;;
        esac
        shift
    done
    systemctl cat k3s.service >/dev/null 2>&1 || error "This host is not a K3s server."
    interface="$(interface_owning_ip "$private_ip")"
    [[ -n "$interface" ]] || error "$private_ip is not assigned to this host."
    write_sans "$address${hostname:+,$hostname}"
    render_ready_check | install_content "$READY_CHECK" 0755
    case "$transport" in
        vrack|lan)
            [[ "$interface" != tailscale0 ]] || error "$transport endpoints cannot use tailscale0."
            subnet="$(connected_ipv4_cidr "$private_ip")" || error "Cannot read the connected network of $private_ip."
            cidr_contains_ip "$subnet" "$address" || \
                error "The API VIP $address is outside $interface's connected network $subnet; VRRP cannot move it here."
            prefix="${subnet#*/}"
            if ! command -v keepalived >/dev/null 2>&1; then
                info "Installing keepalived for the API VIP..."
                sudo apt-get update -qq
                sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq keepalived >/dev/null
            fi
            node_name="$(hostname -s | tr '[:upper:]' '[:lower:]')"
            render_keepalived_config "$node_name" "$interface" "$private_ip" "$address" "$prefix" "$peers" | \
                install_content "$KEEPALIVED_CONFIG" 0640
            sudo keepalived -t -f "$KEEPALIVED_CONFIG" >/dev/null 2>&1 || \
                error "keepalived rejected $KEEPALIVED_CONFIG."
            sudo systemctl enable keepalived >/dev/null 2>&1
            if [[ "$CONTENT_CHANGED" == true ]] || ! systemctl is-active --quiet keepalived; then
                sudo systemctl restart keepalived
            fi
            systemctl is-active --quiet keepalived || error "keepalived is not running; inspect: sudo journalctl -u keepalived"
            ;;
        tailscale)
            [[ "$interface" == tailscale0 ]] || error "Tailscale endpoints require the node address on tailscale0."
            valid_service_name "$service" || error "Invalid Tailscale Service name: $service"
            version="$(tailscale version 2>/dev/null | awk 'NR == 1 {print $1}')"
            version_at_least "${version:-0}" "$MIN_TAILSCALE_VERSION" || \
                error "Tailscale ${version:-unknown} cannot host Services; upgrade to $MIN_TAILSCALE_VERSION or later."
            sudo tailscale serve --service="$service" --tcp=6443 --yes tcp://127.0.0.1:6443 >/dev/null || \
                error "Tailscale refused to host $service; the device must carry the control-plane tag."
            render_service_watch | install_content "$SERVICE_WATCH" 0755
            local watch_changed="$CONTENT_CHANGED"
            render_service_watch_unit "$service" | install_content "$SERVICE_WATCH_UNIT" 0644
            [[ "$CONTENT_CHANGED" != true ]] || watch_changed=true
            sudo systemctl daemon-reload
            sudo systemctl enable swirl-cloud-api-endpoint.service >/dev/null 2>&1
            if [[ "$watch_changed" == true ]] || ! systemctl is-active --quiet swirl-cloud-api-endpoint.service; then
                sudo systemctl restart swirl-cloud-api-endpoint.service
            fi
            ;;
        *) error "Unknown transport: $transport" ;;
    esac
}

# ---------- Cluster record and client helpers ----------

load_record() {
    local record
    record="$(kubectl --request-timeout=10s -n "$RECORD_NAMESPACE" get configmap "$RECORD_NAME" \
        --ignore-not-found -o json)"
    [[ -n "$record" ]] || return 1
    RECORD_TRANSPORT="$(jq -r '.data.transport // empty' <<< "$record")"
    RECORD_ADDRESS="$(jq -r '.data.address // empty' <<< "$record")"
    RECORD_HOSTNAME="$(jq -r '.data.hostname // empty' <<< "$record")"
    RECORD_SERVICE="$(jq -r '.data.service // empty' <<< "$record")"
    [[ -n "$RECORD_TRANSPORT" && -n "$RECORD_ADDRESS" ]]
}

# Succeeds when the endpoint presents this cluster's server certificate for $1.
verify_endpoint() {
    sudo curl -fsS --max-time 5 -o /dev/null --cacert "$K3S_TLS_DIR/server-ca.crt" "https://$1:6443/cacerts"
}

write_kubeconfig() {
    local path="$1" cluster host
    [[ -n "$path" ]] || error "--kubeconfig requires a destination path."
    [[ ! -e "$path" ]] || error "$path already exists; choose a new path."
    load_record || error "No API endpoint is recorded; run --reconcile first."
    host="${RECORD_HOSTNAME:-$RECORD_ADDRESS}"
    sudo cat /etc/rancher/k3s/k3s.yaml | tee "$path" >/dev/null
    chmod 600 "$path"
    cluster="$(kubectl --kubeconfig "$path" config view -o jsonpath='{.clusters[0].name}')"
    kubectl --kubeconfig "$path" config set-cluster "$cluster" --server "https://$host:6443" >/dev/null
    info "Wrote $path for https://$host:6443. It holds administrator credentials; keep it private."
}

# ---------- Fleet reconciliation ----------

reconcile() {
    local transport="" address="" hostname="" service="" cidr="${K3S_NODE_NETWORK_CIDR:-}" source_ip="${K3S_PRIVATE_ADDRESS:-}"
    local account="${USER:-}" port=22 identity="" option nodes addresses node_addresses count
    local name ip uid recorded_user target target_user connection actual_source actual_target actual_port
    local member remote_dir peers sans command current attempt restarted problem reachable=false
    local -a apply_args ssh_options scp_options members
    local FLEET_MEMBERSHIP
    while (( $# )); do
        option="$1"; shift; (( $# )) || error "Missing value for $option"
        case "$option" in
            --transport) transport="$1" ;; --address) address="$1" ;; --hostname) hostname="$1" ;;
            --service) service="$1" ;; --node-network-cidr) cidr="$1" ;; --control-plane-ip) source_ip="$1" ;;
            --ssh-user) account="$1" ;; --ssh-port) port="$1" ;; --identity-file) identity="$1" ;;
            *) error "Unknown option: $option" ;;
        esac
        shift
    done
    # shellcheck source=lib/control-plane-access.sh
    source "$SCRIPT_DIR/lib/control-plane-access.sh"
    # shellcheck source=lib/control-plane-restart.sh
    source "$SCRIPT_DIR/lib/control-plane-restart.sh"
    if load_record; then
        transport="${transport:-$RECORD_TRANSPORT}"
        address="${address:-$RECORD_ADDRESS}"
        if [[ "$transport" == tailscale ]]; then
            hostname="${hostname:-$RECORD_HOSTNAME}"
            service="${service:-$RECORD_SERVICE}"
        fi
    fi
    transport="$(normalize_node_transport "$transport")" || error "Set --transport vrack, lan or tailscale."
    [[ -n "$address" ]] || error "Set --address (the API VIP, or the Tailscale Service IPv4)."
    [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] || error "Invalid SSH port"
    [[ -z "$identity" || -f "$identity" ]] || error "SSH identity file is missing"
    [[ -n "$(interface_owning_ip "$source_ip")" ]] || error "Run from a control plane and pass its private --control-plane-ip."

    nodes="$(kubectl --request-timeout=10s get nodes -o json)"
    FLEET_MEMBERSHIP="$(jq -c '[.items[] | select(.metadata.labels | has("node-role.kubernetes.io/control-plane") or has("node-role.kubernetes.io/master")) | {key:.metadata.name,value:.metadata.uid}] | from_entries' <<< "$nodes")"
    count="$(jq 'length' <<< "$FLEET_MEMBERSHIP")"
    (( count >= 3 && count % 2 == 1 )) || \
        error "The API endpoint needs an odd membership of at least three control planes (found $count)."
    addresses="$(control_plane_private_ips "$nodes" "$cidr")" || error "Control planes require verified private InternalIPs in $cidr."
    control_plane_address_member "$addresses" "$source_ip" || error "Run from a verified control-plane private address."
    node_addresses="$(jq -r '[.items[].status.addresses[]? | select(.type == "InternalIP") | .address] | join(",")' <<< "$nodes")"
    case "$transport" in
        vrack|lan)
            problem="$(validate_vip "$address" "$cidr" "$node_addresses")" || error "$problem"
            hostname="" service=""
            ;;
        tailscale)
            if [[ "$cidr" != 100.64.0.0/10 ]] || ! tailscale_ipv4 "$address"; then
                error "Tailscale endpoints use a Tailscale Service IPv4 inside 100.64.0.0/10."
            fi
            valid_service_name "$service" || error "Set --service svc:<name> for the Tailscale endpoint."
            [[ -z "$hostname" ]] || valid_dns_name "$hostname" || error "Invalid Tailscale Service hostname: $hostname"
            ;;
    esac
    sans="$address${hostname:+,$hostname}"

    ssh_options=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$port")
    scp_options=(-q -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -P "$port")
    if [[ -n "$identity" ]]; then ssh_options+=(-i "$identity"); scp_options+=(-i "$identity"); fi

    # This host goes last so its own API restart cannot interrupt the sequence.
    mapfile -t members < <(jq -r --arg local "$source_ip" '[.items[] |
        select((.metadata.labels | has("node-role.kubernetes.io/control-plane")) or (.metadata.labels | has("node-role.kubernetes.io/master"))) |
        [.metadata.name, ([.status.addresses[] | select(.type == "InternalIP") | .address][0]), .metadata.uid,
         (.metadata.annotations["node.swirl-cloud.io/ssh-user"] // "-")]] |
        sort_by(.[1] == $local)[] | @tsv' <<< "$nodes")
    for member in "${members[@]}"; do
        IFS=$'\t' read -r name ip uid recorded_user <<< "$member"
        control_plane_address_member "$addresses" "$ip" || error "$name is not a verified control plane."
        current="$(kubectl --request-timeout=10s get node "$name" -o json)"
        jq -e --arg uid "$uid" --arg ip "$ip" '.metadata.uid == $uid and any(.status.addresses[]?; .type == "InternalIP" and .address == $ip)' \
            <<< "$current" >/dev/null || error "Control plane $name changed identity or address."
        peers="$(tr ',' '\n' <<< "$addresses" | grep -vFx "$ip" | paste -sd, - || true)"
        apply_args=(--transport "$transport" --address "$address" --private-ip "$ip" --peers "$peers")
        [[ -z "$hostname" ]] || apply_args+=(--hostname "$hostname")
        [[ -z "$service" ]] || apply_args+=(--service "$service")
        info "Configuring the API endpoint on $name ($ip)..."
        restarted=false
        if [[ "$ip" == "$source_ip" ]]; then
            apply_local "${apply_args[@]}" </dev/null
            if ! check_sans "$sans"; then
                fresh_majority "$name" || error "Restarting $name now would risk the etcd majority."
                info "Restarting K3s on $name to serve the endpoint certificate..."
                sudo systemctl restart k3s </dev/null
                restarted=true
            fi
        else
            target_user="$account"; [[ "$recorded_user" == - ]] || target_user="$recorded_user"
            [[ "$target_user" =~ ^[A-Za-z_][A-Za-z0-9_.-]*\$?$ ]] || error "Invalid SSH account for $name"
            target="$target_user@$ip"
            connection="$(ssh -n "${ssh_options[@]}" "$target" 'sudo -n true && printf "%s" "$SSH_CONNECTION"')"
            read -r actual_source _ actual_target actual_port <<< "$connection"
            [[ "$actual_source" == "$source_ip" && "$actual_target" == "$ip" && "$actual_port" == "$port" ]] || \
                error "Private SSH identity mismatch for $name."
            remote_dir="$(ssh -n "${ssh_options[@]}" "$target" 'mktemp -d /tmp/swirl-cloud-api-endpoint.XXXXXX')"
            [[ "$remote_dir" =~ ^/tmp/swirl-cloud-api-endpoint\.[A-Za-z0-9]+$ ]] || error "Invalid remote staging directory on $name"
            ssh -n "${ssh_options[@]}" "$target" "mkdir -m 700 $(printf '%q' "$remote_dir/lib")"
            scp "${scp_options[@]}" "$SCRIPT_DIR/configure-k3s-api-endpoint.sh" "$target:$remote_dir/" </dev/null
            scp "${scp_options[@]}" "$SCRIPT_DIR/lib/network.sh" "$target:$remote_dir/lib/" </dev/null
            printf -v command 'bash %q' "$remote_dir/configure-k3s-api-endpoint.sh"
            ssh -n "${ssh_options[@]}" "$target" "$command --apply-local $(printf '%q ' "${apply_args[@]}")"
            if ! ssh -n "${ssh_options[@]}" "$target" "$command --check-sans $(printf '%q' "$sans")"; then
                fresh_majority "$name" || error "Restarting $name now would risk the etcd majority."
                info "Restarting K3s on $name to serve the endpoint certificate..."
                ssh -n "${ssh_options[@]}" "$target" 'sudo -n systemctl restart k3s'
                restarted=true
            fi
        fi
        if [[ "$restarted" == true ]]; then
            wait_fresh_ready "$name" "$uid" "$(date -u +%s)" || error "Control plane $name is not Ready with a fresh heartbeat."
        fi
        for (( attempt=0; attempt<12; attempt++ )); do
            if [[ "$ip" == "$source_ip" ]]; then
                check_sans "$sans" && break
            elif ssh -n "${ssh_options[@]}" "$target" "$command --check-sans $(printf '%q' "$sans")"; then
                break
            fi
            sleep 5
        done
        (( attempt < 12 )) || error "$name does not serve a certificate for $sans."
        [[ "$ip" == "$source_ip" ]] || ssh -n "${ssh_options[@]}" "$target" "rm -rf -- $(printf '%q' "$remote_dir")"
        fresh_majority '' || error "The etcd majority is not healthy after configuring $name."
    done

    kubectl -n "$RECORD_NAMESPACE" create configmap "$RECORD_NAME" \
        --from-literal=transport="$transport" --from-literal=address="$address" \
        --from-literal=hostname="$hostname" --from-literal=service="$service" \
        --from-literal=url="https://${hostname:-$address}:6443" \
        --dry-run=client -o yaml | kubectl label --local -f - app.kubernetes.io/managed-by=swirl-cloud -o yaml | \
        kubectl apply -f - >/dev/null

    for (( attempt=0; attempt<12; attempt++ )); do
        if verify_endpoint "$address" 2>/dev/null; then reachable=true; break; fi
        sleep 5
    done
    if [[ "$reachable" == true ]]; then
        info "The Kubernetes API endpoint https://${hostname:-$address}:6443 is served by $count control planes."
    elif [[ "$transport" == tailscale ]]; then
        # A Service host does not necessarily route its own connections to the
        # Service; the authoritative check is from another tailnet device.
        warn "This control plane could not reach $address through the Tailscale Service. From an admin device, run: kubectl --kubeconfig <file from --kubeconfig> get --raw /readyz"
    else
        error "No control plane answers on the API VIP $address; inspect: sudo journalctl -u keepalived"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    NETWORK_LIBRARY="$SCRIPT_DIR/lib/network.sh"
    [[ -r "$NETWORK_LIBRARY" ]] || error "Shared network library not found: $NETWORK_LIBRARY"
    # shellcheck source=lib/network.sh
    source "$NETWORK_LIBRARY"
    case "${1:-}" in
        --reconcile) shift; reconcile "$@" ;;
        --apply-local) shift; apply_local "$@" ;;
        --write-sans) [[ $# -eq 2 ]] || error "--write-sans takes ADDRESS[,HOSTNAME]"; write_sans "$2" ;;
        --check-sans) [[ $# -eq 2 ]] || error "--check-sans takes ADDRESS[,HOSTNAME]"; check_sans "$2" ;;
        --validate-vip)
            [[ $# -eq 4 ]] || error "--validate-vip takes VIP NODE_CIDR NODE_ADDRESSES"
            problem="$(validate_vip "$2" "$3" "$4")" || error "$problem"
            ;;
        --kubeconfig) [[ $# -eq 2 ]] || error "--kubeconfig takes a destination path"; write_kubeconfig "$2" ;;
        --verify)
            load_record || error "No API endpoint is recorded."
            verify_endpoint "$RECORD_ADDRESS" || error "This control plane cannot reach https://$RECORD_ADDRESS:6443."
            info "https://$RECORD_ADDRESS:6443 presents this cluster's API certificate."
            ;;
        -h|--help|'') usage ;;
        *) error "Unknown mode: $1 (use --help)" ;;
    esac
fi
