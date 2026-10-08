#!/usr/bin/env bash
# Disable timeout-based storage detach before enabling fenced HA recovery.
set -euo pipefail
set +x
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_ROOT=/etc/rancher/k3s
CONFIG_PATH="$CONFIG_ROOT/config.yaml.d/99-swirl-cloud-ha-storage-safety.yaml"
POLICY_KEY=node.swirl-cloud.io/fenced-detach-policy
error() { echo "[ERROR] $*" >&2; exit 1; }

prepare_local() {
    [[ "${HIGH_AVAILABILITY_ENABLED:-false}" == true ]] || return 0
    local file temporary
    # CLI arguments replace configuration-file lists; custom locations could
    # omit this drop-in entirely. Refuse those layouts rather than claim safety.
    if sudo grep -Eq -- 'kube-controller-manager-arg|disable-controller-manager|--config([=[:space:]]|$)|K3S_CONFIG_FILE' \
        /etc/systemd/system/k3s.service /etc/systemd/system/k3s.service.env /etc/default/k3s /etc/sysconfig/k3s 2>/dev/null; then
        error "Custom K3s controller/config arguments require review before managed storage safety"
    fi
    for file in "$CONFIG_ROOT/config.yaml" "$CONFIG_ROOT"/config.yaml.d/*.yaml; do
        [[ "$file" != "$CONFIG_PATH" ]] || continue
        sudo test -f "$file" || continue
        if sudo grep -Eq -- 'disable-force-detach-on-timeout|disable-controller-manager' "$file"; then
            error "Conflicting custom controller policy in $file"
        fi
        if [[ "$file" == "$CONFIG_ROOT"/config.yaml.d/* && "$file" > "$CONFIG_PATH" ]] && \
            sudo grep -Eq '^[[:space:]]*kube-controller-manager-arg:' "$file"; then
            error "A later K3s drop-in replaces controller arguments: $file"
        fi
    done
    temporary="$(mktemp)"
    printf '# Managed by scripts/configure-ha-control-planes.sh\nkube-controller-manager-arg+:\n  - disable-force-detach-on-timeout=true\n' > "$temporary"
    if ! sudo test -f "$CONFIG_PATH" || ! sudo cmp -s "$temporary" "$CONFIG_PATH"; then
        sudo install -D -o root -g root -m 0644 "$temporary" "$CONFIG_PATH"
    fi
    rm -f "$temporary"
}

verify_local() {
    local invocation
    sudo test -f "$CONFIG_PATH" || return 1
    sudo grep -Fxq '  - disable-force-detach-on-timeout=true' "$CONFIG_PATH" || return 1
    systemctl is-active --quiet k3s || return 1
    invocation="$(systemctl show k3s.service --property=InvocationID --value)"
    [[ "$invocation" =~ ^[a-f0-9]{32}$ ]] || return 1
    # Verify the actual controller startup command for the current service
    # invocation. No logs or credentials are emitted by this check.
    sudo journalctl "_SYSTEMD_INVOCATION_ID=$invocation" --no-pager -o cat |
        awk '/Running kube-controller-manager / {
            good = /--disable-force-detach-on-timeout=true([ "\t]|$)/ && !/--disable-force-detach-on-timeout=false/
        } END {exit !good}'
}

restart_pending_local() {
    local started changed
    sudo test -f "$CONFIG_PATH" || return 1
    started="$(systemctl show k3s.service --property=ExecMainStartTimestamp --value)"
    [[ -n "$started" ]] || return 1
    started="$(date -d "$started" +%s)" || return 1
    changed="$(sudo stat -c %Y "$CONFIG_PATH")" || return 1
    [[ "$changed" =~ ^[0-9]+$ && "$changed" -ge "$started" ]]
}

main() {
    local mode="${1:---help}" cidr="${K3S_NODE_NETWORK_CIDR:-}" source_ip="${K3S_PRIVATE_ADDRESS:-}"
    local account="${K3S_NODE_SSH_USER:-${USER:-}}" port="${K3S_NODE_SSH_PORT:-22}" identity="${K3S_NODE_SSH_IDENTITY_FILE:-${K3S_NODE_IDENTITY_FILE:-}}"
    case "$mode" in
        --prepare-local) prepare_local; return ;;
        --verify-local) verify_local; return ;;
        --restart-pending-local) restart_pending_local; return ;;
        --reconcile) shift ;;
        -h|--help)
            echo 'Usage: configure-ha-control-planes.sh --prepare-local | --verify-local | --reconcile [--node-network-cidr CIDR --control-plane-ip IP --ssh-user USER --ssh-port PORT --identity-file FILE]'
            echo 'HA only: --prepare-local writes the K3s drop-in without restarting; --reconcile verifies/restarts existing control planes sequentially.'
            return ;;
        *) error "Unknown mode: $mode" ;;
    esac
    [[ "${HIGH_AVAILABILITY_ENABLED:-false}" == true ]] || return 0
    local option nodes addresses name address uid recorded_user target_user connection actual_source actual_target actual_port
    local FLEET_MEMBERSHIP
    local target remote_dir command current annotation_patch
    while (( $# )); do
        option="$1"; shift; (( $# )) || error "Missing value for $option"
        case "$option" in
            --node-network-cidr) cidr="$1" ;; --control-plane-ip) source_ip="$1" ;;
            --ssh-user) account="$1" ;; --ssh-port) port="$1" ;; --identity-file) identity="$1" ;;
            *) error "Unknown option: $option" ;;
        esac
        shift
    done
    # shellcheck source=lib/network.sh
    source "$SCRIPT_DIR/lib/network.sh"
    # shellcheck source=lib/control-plane-access.sh
    source "$SCRIPT_DIR/lib/control-plane-access.sh"
    # shellcheck source=lib/control-plane-restart.sh
    source "$SCRIPT_DIR/lib/control-plane-restart.sh"
    [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] || error "Invalid SSH port"
    [[ -z "$identity" || -f "$identity" ]] || error "SSH identity file is missing"
    nodes="$(kubectl --request-timeout=10s get nodes -o json)"
    FLEET_MEMBERSHIP="$(jq -c '[.items[] | select(.metadata.labels | has("node-role.kubernetes.io/control-plane") or has("node-role.kubernetes.io/master")) | {key:.metadata.name,value:.metadata.uid}] | from_entries' <<< "$nodes")"
    addresses="$(control_plane_private_ips "$nodes" "$cidr")" || error "Control planes require verified private InternalIPs"
    control_plane_address_member "$addresses" "$source_ip" || error "Run from a verified control-plane private address"
    [[ -n "$(interface_owning_ip "$source_ip")" ]] || error "Source address is not local"
    local -a ssh_options=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$port")
    local -a scp_options=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -P "$port")
    if [[ -n "$identity" ]]; then ssh_options+=(-i "$identity"); scp_options+=(-i "$identity"); fi
    fresh_majority ''
    while IFS=$'\t' read -r name address uid recorded_user; do
        [[ -n "$name" ]] || continue
        control_plane_address_member "$addresses" "$address" || error "Target is not a verified control plane"
        # Recheck Kubernetes object identity before touching the corresponding host.
        current="$(kubectl --request-timeout=10s get node "$name" -o json)"
        jq -e --arg uid "$uid" --arg address "$address" '.metadata.uid == $uid and any(.status.addresses[]?; .type == "InternalIP" and .address == $address)' <<< "$current" >/dev/null || error "Control-plane UID or address changed"
        if [[ "$address" == "$source_ip" ]]; then
            prepare_local
            if ! verify_local; then
                restart_pending_local || error "Cannot verify $name's running policy; unchanged configuration will not trigger a restart. Review the current K3s startup logs."
                fresh_majority "$name"
                echo "[INFO] Restarting K3s on $name to require verified storage fencing"
                sudo systemctl restart k3s
                verify_local || error "Current K3s controller did not confirm the storage-safety flag"
            fi
        else
            target_user="$account"; [[ "$recorded_user" == - ]] || target_user="$recorded_user"
            [[ "$target_user" =~ ^[A-Za-z_][A-Za-z0-9_.-]*\$?$ ]] || error "Invalid SSH account"
            target="$target_user@$address"
            connection="$(ssh -n "${ssh_options[@]}" "$target" 'sudo -n true && printf "%s" "$SSH_CONNECTION"')"
            read -r actual_source _ actual_target actual_port <<< "$connection"
            [[ "$actual_source" == "$source_ip" && "$actual_target" == "$address" && "$actual_port" == "$port" ]] || error "Private SSH identity mismatch"
            remote_dir="$(ssh -n "${ssh_options[@]}" "$target" 'mktemp -d /tmp/swirl-cloud-ha-control-plane.XXXXXX')"
            [[ "$remote_dir" =~ ^/tmp/swirl-cloud-ha-control-plane\.[A-Za-z0-9]+$ ]] || error "Invalid remote staging directory"
            scp "${scp_options[@]}" "$SCRIPT_DIR/configure-ha-control-planes.sh" "$target:$remote_dir/"
            printf -v command 'HIGH_AVAILABILITY_ENABLED=true bash %q' "$remote_dir/configure-ha-control-planes.sh"
            ssh -n "${ssh_options[@]}" "$target" "$command --prepare-local"
            if ! ssh -n "${ssh_options[@]}" "$target" "$command --verify-local"; then
                ssh -n "${ssh_options[@]}" "$target" "$command --restart-pending-local" || \
                    error "Cannot verify $name's running policy; unchanged configuration will not trigger a restart. Review the current K3s startup logs."
                fresh_majority "$name"
                echo "[INFO] Restarting K3s on $name to require verified storage fencing"
                ssh -n "${ssh_options[@]}" "$target" "sudo -n systemctl restart k3s && $command --verify-local"
            fi
            ssh -n "${ssh_options[@]}" "$target" "rm -rf -- $(printf '%q' "$remote_dir")"
        fi
        # A post-restart heartbeat is required before considering another host.
        wait_fresh_ready "$name" "$uid" "$(date -u +%s)" || \
            error "Control plane $name has not returned with a fresh Ready heartbeat"
        current="$(kubectl --request-timeout=10s get node "$name" -o json)"
        annotation_patch="$(jq -cn --arg uid "$uid" --arg key "$POLICY_KEY" --argjson current "$current" '
            [{op:"test",path:"/metadata/uid",value:$uid},
             {op:"test",path:"/metadata/resourceVersion",value:$current.metadata.resourceVersion},
             {op:"add",path:"/metadata/annotations",value:(($current.metadata.annotations // {}) + {($key):"verified"})}]')"
        kubectl --request-timeout=10s patch node "$name" --type=json -p "$annotation_patch" >/dev/null
        fresh_majority ''
        echo "[INFO] $name runs the verified fencing-based storage detach policy"
    done < <(jq -r --arg local "$source_ip" '[.items[] |
        select((.metadata.labels | has("node-role.kubernetes.io/control-plane")) or (.metadata.labels | has("node-role.kubernetes.io/master"))) |
        [.metadata.name, ([.status.addresses[] | select(.type == "InternalIP") | .address][0]), .metadata.uid,
         (.metadata.annotations["node.swirl-cloud.io/ssh-user"] // "-")]] |
        sort_by(.[1] == $local)[] | @tsv' <<< "$nodes")
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
