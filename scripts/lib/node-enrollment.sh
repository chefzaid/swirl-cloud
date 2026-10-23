#!/usr/bin/env bash
# shellcheck disable=SC2034,SC2153,SC2154 # SSH options and endpoint state are shared with add-k3s-workers.sh.
# Cluster-side enrollment for add-k3s-workers.sh: control-plane count, datastore,
# API endpoint, join URL and the remote node installation. Callers provide
# logging, SSH options, lib/control-plane-access.sh and the enrollment globals.

preflight_control_plane_count() {
    [[ "$ENROLLMENT_ROLE" == "control-plane" ]] || return 0
    local nodes current_count new_count=0 planned_count host node_name existing_role selected_list
    local -a selected_hosts=()
    local -A seen_targets=()
    nodes="$(kubectl get nodes -o json)"
    current_count="$(jq '[.items[] | select(.metadata.labels | has("node-role.kubernetes.io/control-plane") or has("node-role.kubernetes.io/master"))] | length' <<< "$nodes")"
    if [[ "$NON_INTERACTIVE" == "true" ]]; then
        selected_list="$WORKER_IPS"
        [[ "$NODE_TRANSPORT" != "tailscale" ]] || selected_list="$WORKER_HOSTS"
        [[ "$selected_list" != ,* && "$selected_list" != *, && "$selected_list" != *,,* ]] || \
            error "Control-plane host list contains an empty item."
        IFS=',' read -r -a selected_hosts <<< "$selected_list"
        [[ -z "$REQUESTED_WORKER_COUNT" || ${#selected_hosts[@]} -eq "$REQUESTED_WORKER_COUNT" ]] || \
            error "The control-plane host list must contain exactly $REQUESTED_WORKER_COUNT entries."
        for host in "${selected_hosts[@]}"; do
            host="${host#"${host%%[![:space:]]*}"}"
            host="${host%"${host##*[![:space:]]}"}"
            [[ -n "$host" ]] || error "Control-plane host list contains an empty item."
            if [[ "$NODE_TRANSPORT" == "tailscale" ]]; then
                check_bootstrap_target "$(build_target "$host")"
                node_name="$(remote_hostname "$(build_target "$host")")"
                [[ -n "$node_name" ]] || error "Could not determine the hostname of $host."
                existing_role="$(jq -r --arg name "$node_name" '.items[] | select(.metadata.name == $name) | if (.metadata.labels | has("node-role.kubernetes.io/control-plane") or has("node-role.kubernetes.io/master")) then "control-plane" else "worker" end' <<< "$nodes")"
            else
                if ! trusted_private_ipv4 "$host" || tailscale_ipv4 "$host"; then
                    error "Invalid $NODE_TRANSPORT control-plane IP: $host"
                fi
                cidr_contains_ip "$NODE_NETWORK_CIDR" "$host" || error "$host is outside $NODE_NETWORK_CIDR."
                [[ "$host" != "$CONTROL_PLANE_IP" ]] || error "The bootstrap control plane cannot enroll itself."
                node_name="$host"
                existing_role="$(jq -r --arg ip "$host" '.items[] | select(any(.status.addresses[]?; .type == "InternalIP" and .address == $ip)) | if (.metadata.labels | has("node-role.kubernetes.io/control-plane") or has("node-role.kubernetes.io/master")) then "control-plane" else "worker" end' <<< "$nodes")"
            fi
            [[ -z "${seen_targets[$node_name]:-}" ]] || error "Duplicate control-plane enrollment target: $node_name"
            seen_targets["$node_name"]=1
            [[ -z "$existing_role" || "$existing_role" == "control-plane" ]] || error "Target $host is already registered as a worker."
            [[ -n "$existing_role" ]] || new_count=$((new_count + 1))
        done
    else
        # The interactive count is of new servers; the list branch above counts
        # only hosts not yet joined.
        new_count="$worker_count"
    fi
    planned_count=$((current_count + new_count))
    (( planned_count % 2 == 1 )) || \
        error "This selection would leave $planned_count control planes; choose an odd final count (1, 3, 5, ...)."
    if [[ -n "$EXPECTED_CONTROL_PLANE_COUNT" ]]; then
        if [[ ! "$EXPECTED_CONTROL_PLANE_COUNT" =~ ^[1-9][0-9]*$ ]] || (( EXPECTED_CONTROL_PLANE_COUNT % 2 != 1 )); then
            error "The expected final control-plane count must be a positive odd integer."
        fi
        [[ "$planned_count" -eq "$EXPECTED_CONTROL_PLANE_COUNT" ]] || \
            error "The selected targets produce $planned_count control planes, but $EXPECTED_CONTROL_PLANE_COUNT were requested."
    fi
    EXPECTED_CONTROL_PLANE_COUNT="$planned_count"
    info "Validated control-plane plan: $current_count existing + $new_count new = $planned_count total."
}

prepare_control_plane_datastore() {
    [[ "$ENROLLMENT_ROLE" == control-plane ]] || return 0
    if "${LOCAL_SUDO[@]}" test -d /var/lib/rancher/k3s/server/db/etcd/member; then
        info "Using the existing embedded-etcd datastore."
        return 0
    fi
    # This entry point expands an existing cluster; never initialize a fresh
    # server merely because the kubeconfig reaches some other cluster.
    "${LOCAL_SUDO[@]}" test -s /var/lib/rancher/k3s/server/db/state.db || \
        error "Run control-plane enrollment on the existing SQLite or embedded-etcd bootstrap server."
    [[ -x "$K3S_HA_SCRIPT" ]] || error "Embedded-etcd configurator is missing: $K3S_HA_SCRIPT"
    info "Backing up SQLite and preparing embedded etcd for the validated control-plane plan..."
    "$K3S_HA_SCRIPT" || error "Embedded-etcd preparation failed; no additional control planes were enrolled."
    "${LOCAL_SUDO[@]}" test -d /var/lib/rancher/k3s/server/db/etcd/member || \
        error "Embedded etcd is not ready; no additional control planes were enrolled."
    # Read again after conversion so joins use the current secure server token.
    JOIN_TOKEN="$("${LOCAL_SUDO[@]}" cat /var/lib/rancher/k3s/server/token)"
    [[ -n "$JOIN_TOKEN" ]] || error "The server token is empty after embedded-etcd preparation."
}

# Creates or confirms the API endpoint; this server serves it from its next start.
prepare_api_endpoint() {
    local result
    [[ "$ENROLLMENT_ROLE" == control-plane ]] || return 0
    if [[ -z "$API_ENDPOINT_ADDRESS" ]]; then
        if [[ "$NODE_TRANSPORT" == tailscale ]]; then
            result="$(printf '%s\n' "$TAILSCALE_API_TOKEN" | "$TAILSCALE_CONFIGURATOR" --role control-plane \
                --tailnet "$TAILSCALE_TAILNET" --mesh-name "$TAILSCALE_MESH_NAME" --api-service --api-token-stdin)" || \
                error "Could not prepare the Kubernetes API Tailscale Service; no control planes were enrolled."
            read -r API_ENDPOINT_ADDRESS API_ENDPOINT_HOSTNAME <<< "$result"
            API_ENDPOINT_SERVICE="svc:${TAILSCALE_MESH_NAME}-api"
        else
            API_ENDPOINT_ADDRESS="$API_VIP"
        fi
        API_ENDPOINT_TRANSPORT="$NODE_TRANSPORT"
    fi
    "$API_ENDPOINT_SCRIPT" --write-sans "$API_ENDPOINT_ADDRESS${API_ENDPOINT_HOSTNAME:+,$API_ENDPOINT_HOSTNAME}"
}

# Nodes register through the endpoint once it is recorded and answers here;
# otherwise through the verified control plane in --server-url.
select_join_url() {
    JOIN_URL="$SERVER_URL"
    if [[ -n "$api_endpoint_record" ]] && "$API_ENDPOINT_SCRIPT" --verify >/dev/null 2>&1; then
        JOIN_URL="https://$API_ENDPOINT_ADDRESS:6443"
        info "New nodes register through the Kubernetes API endpoint $JOIN_URL."
    fi
}

install_worker() {
    local target="$1" node_name="$2" node_ip="$3" labels="$4" taints="$5" control_plane_ip="$6"
    local remote_dir="" remote_installer="" remote_hardener=""
    local remote_command quoted quoted_dir quoted_installer quoted_hardener argument existing_node existing_role registered_ip
    CONTROL_PLANE_SSH_IPS="$(control_plane_private_ips "$(kubectl get nodes -o json)" "$NODE_NETWORK_CIDR")" || \
        error "Cannot verify control-plane SSH sources before enrollment"
    control_plane_address_member "$CONTROL_PLANE_SSH_IPS" "$control_plane_ip" || \
        error "The actual SSH source is not a verified control plane"
    local worker_args=(
        --non-interactive
        --node-role "$ENROLLMENT_ROLE"
        --transport "$NODE_TRANSPORT"
        --server-url "$JOIN_URL"
        --token-stdin
        --node-name "$node_name"
        --ssh-port "$SSH_PORT"
        --control-plane-ip "$control_plane_ip"
        --control-plane-ssh-ips "$CONTROL_PLANE_SSH_IPS"
    )

    [[ "$node_name" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && ${#node_name} -le 253 ]] || \
        error "Invalid Kubernetes node name: $node_name"
    [[ "$node_ip" != "$CONTROL_PLANE_IP" ]] || error "The bootstrap control plane cannot enroll itself."
    [[ "$node_ip" != "$API_ENDPOINT_ADDRESS" ]] || error "$node_ip is the Kubernetes API endpoint address, not a node."
    existing_node="$(kubectl get nodes -o json | jq -c --arg name "$node_name" --arg ip "$node_ip" '
        [.items[] | select(.metadata.name == $name or any(.status.addresses[]?; .type == "InternalIP" and .address == $ip))]')"
    if [[ "$(jq length <<< "$existing_node")" -gt 0 ]]; then
        [[ "$(jq length <<< "$existing_node")" -eq 1 && "$(jq -r '.[0].metadata.name' <<< "$existing_node")" == "$node_name" ]] || \
            error "Node name/IP collision for $node_name ($node_ip); resolve the existing registration first."
        existing_role="$(jq -r '.[0] | if (.metadata.labels | has("node-role.kubernetes.io/control-plane") or has("node-role.kubernetes.io/master")) then "control-plane" else "worker" end' <<< "$existing_node")"
        registered_ip="$(jq -r '.[0].status.addresses[]? | select(.type == "InternalIP") | .address' <<< "$existing_node")"
        [[ "$existing_role" == "$ENROLLMENT_ROLE" && "$registered_ip" == "$node_ip" ]] || \
            error "Existing node $node_name has role $existing_role and IP $registered_ip; refusing to replace it with $ENROLLMENT_ROLE at $node_ip."
        if [[ "$ENROLLMENT_ROLE" == "control-plane" ]]; then
            ssh "${ssh_options[@]}" "$target" 'systemctl is-active --quiet k3s' || \
                error "Existing control plane $node_name is not running K3s; repair it before enrollment."
            [[ "$(ssh "${ssh_options[@]}" "$target" "k3s --version | awk 'NR == 1 {print \$3}'")" == "$K3S_VERSION" ]] || \
                error "Existing control plane $node_name uses a different K3s version; upgrade servers together."
            kubectl wait --for=condition=Ready "node/$node_name" --timeout=5m
            kubectl label "node/$node_name" svccontroller.k3s.cattle.io/enablelb=false \
                node.swirl-cloud.io/role=control-plane node.swirl-cloud.io/exposure=local --overwrite >/dev/null
            info "Control plane '$node_name' already belongs to this cluster; keeping its existing server configuration."
            return 0
        fi
    fi
    if [[ "$ENROLLMENT_ROLE" == "control-plane" ]]; then
        worker_args+=(--control-plane-schedulable "$JOIN_CONTROL_PLANE_SCHEDULABLE"
            --api-endpoint-sans "$API_ENDPOINT_ADDRESS${API_ENDPOINT_HOSTNAME:+,$API_ENDPOINT_HOSTNAME}")
    fi
    [[ -z "${PLATFORM_DOMAIN:-}" ]] || worker_args+=(--domain "$PLATFORM_DOMAIN")

    [[ "$NODE_TRANSPORT" != "tailscale" ]] || worker_args+=(--tailscale-ready)

    [[ -z "$node_ip" ]] || worker_args+=(--node-ip "$node_ip")
    [[ -z "$labels" ]] || worker_args+=(--labels "$labels")
    [[ -z "$taints" ]] || worker_args+=(--taints "$taints")
    [[ -z "$NODE_NETWORK_CIDR" ]] || worker_args+=(--node-network-cidr "$NODE_NETWORK_CIDR")
    [[ -z "$K3S_VERSION" ]] || worker_args+=(--k3s-version "$K3S_VERSION")
    remote_dir="$(ssh "${ssh_options[@]}" "$target" 'mktemp -d /tmp/swirl-cloud-worker.XXXXXX')"
    [[ "$remote_dir" == /tmp/swirl-cloud-worker.* ]] || error "Could not create a safe temporary directory on $target."
    printf -v quoted_dir '%q' "$remote_dir"
    ssh "${ssh_options[@]}" "$target" "mkdir -m 700 $quoted_dir/scripts $quoted_dir/scripts/lib $quoted_dir/config $quoted_dir/config/host"
    info "Copying the $ENROLLMENT_ROLE installer and enforced AppArmor profile to $target..."
    scp "${scp_options[@]}" "$WORKER_INSTALLER" "$K3S_APPARMOR_INSTALLER" "$K3S_REGISTRY_MIRROR_SCRIPT" "$K3S_KUBELET_MEMORY_SCRIPT" "$target:$remote_dir/scripts/"
    scp "${scp_options[@]}" "$TAILSCALE_CONFIGURATOR" "$OVH_VRACK_CONFIGURATOR" "$target:$remote_dir/scripts/"
    scp "${scp_options[@]}" "$NETWORK_LIBRARY" "$PROMPT_LIBRARY" "$TRANSPORT_GUIDE_LIBRARY" "$target:$remote_dir/scripts/lib/"
    scp "${scp_options[@]}" "$K3S_NETWORK_CONFIGURATOR" "$target:$remote_dir/scripts/"
    scp "${scp_options[@]}" "$K3S_APPARMOR_PROFILE" "$target:$remote_dir/config/host/"
    scp "${scp_options[@]}" "$LONGHORN_HOST_CONFIGURATOR" "$target:$remote_dir/scripts/"
    scp "${scp_options[@]}" "$LONGHORN_MULTIPATH_CONFIG" "$target:$remote_dir/config/host/"
    if [[ "$ENROLLMENT_ROLE" == "control-plane" ]]; then
        scp "${scp_options[@]}" "$LYNIS_SCHEDULER" "$NODE_AUDITOR" "${HA_CONTROL_PLANE_CONFIGURATOR:-$SCRIPT_DIR/configure-ha-control-planes.sh}" "$K3S_AUDIT_CONFIGURATOR" "$API_ENDPOINT_SCRIPT" "$target:$remote_dir/scripts/"
        scp "${scp_options[@]}" "$K3S_AUDIT_POLICY" "$target:$remote_dir/config/host/"
        ssh "${ssh_options[@]}" "$target" "chmod 700 $(printf '%q' "$remote_dir/scripts/configure-lynis-schedule.sh") $(printf '%q' "$remote_dir/scripts/audit-cluster-nodes.sh") $(printf '%q' "$remote_dir/scripts/configure-k3s-audit.sh")"
    fi
    info "Copying the worker host-security policy to $target..."
    scp "${scp_options[@]}" "$SECURITY_HARDENER" "$target:$remote_dir/scripts/"
    scp "${scp_options[@]}" "${SECURITY_HARDENER_LIBRARIES[@]}" "$target:$remote_dir/scripts/lib/"
    remote_installer="$remote_dir/scripts/install-k3s-worker.sh"

    printf -v quoted_installer '%q' "$remote_installer"
    remote_hardener="$remote_dir/scripts/configure-node-security.sh"
    printf -v quoted_hardener '%q' "$remote_hardener"
    remote_command="chmod 700 $quoted_installer $quoted_hardener $(printf '%q' "$remote_dir/scripts/configure-k3s-apparmor.sh") $(printf '%q' "$remote_dir/scripts/configure-longhorn-host.sh") $(printf '%q' "$remote_dir/scripts/configure-k3s-registry-mirror.sh") $(printf '%q' "$remote_dir/scripts/configure-k3s-kubelet-memory.sh") $(printf '%q' "$remote_dir/scripts/configure-tailscale.sh") $(printf '%q' "$remote_dir/scripts/configure-ovh-vrack.sh") $(printf '%q' "$remote_dir/scripts/configure-k3s-control-plane-network.sh") && $quoted_installer"
    remote_command="export K3S_REGISTRY_HOST=$(printf '%q' "$K3S_REGISTRY_HOST") K3S_REGISTRY_ENDPOINT=$(printf '%q' "$K3S_REGISTRY_ENDPOINT"); $remote_command"
    if [[ "$ENROLLMENT_ROLE" == control-plane && "${HIGH_AVAILABILITY_ENABLED:-false}" == true ]]; then
        remote_command="export HIGH_AVAILABILITY_ENABLED=true; $remote_command"
    fi
    for argument in "${worker_args[@]}"; do
        printf -v quoted '%q' "$argument"
        remote_command+=" $quoted"
    done

    info "Installing '$node_name' through $target..."
    if ! printf '%s\n' "$JOIN_TOKEN" | ssh "${ssh_options[@]}" "$target" "$remote_command"; then
        ssh "${ssh_options[@]}" "$target" "rm -r -- $quoted_dir" >/dev/null 2>&1 || true
        error "$ENROLLMENT_ROLE installation failed on $target."
    fi
    ssh "${ssh_options[@]}" "$target" "rm -r -- $quoted_dir" >/dev/null 2>&1 || true

    info "Confirming a fresh private SSH connection after $ENROLLMENT_ROLE UFW was enabled..."
    check_target "$target" "$node_ip"

    info "Waiting for Kubernetes node '$node_name' to become Ready..."
    node_registered=false
    for _ in {1..30}; do
        if kubectl get "node/$node_name" >/dev/null 2>&1; then
            node_registered=true
            break
        fi
        sleep 2
    done
    [[ "$node_registered" == "true" ]] || error "$ENROLLMENT_ROLE '$node_name' did not register within 60 seconds."
    kubectl wait --for=condition=Ready "node/$node_name" --timeout=5m
    kubectl label "node/$node_name" \
        "node.swirl-cloud.io/role=$ENROLLMENT_ROLE" \
        node.swirl-cloud.io/exposure=local \
        --overwrite >/dev/null
    if [[ "$target" == *@* ]]; then
        kubectl annotate "node/$node_name" "node.swirl-cloud.io/ssh-user=${target%@*}" --overwrite >/dev/null
    fi
    if [[ "$ENROLLMENT_ROLE" == "control-plane" ]]; then
        kubectl label "node/$node_name" svccontroller.k3s.cattle.io/enablelb=false --overwrite >/dev/null
    else
        kubectl label "node/$node_name" svccontroller.k3s.cattle.io/enablelb- >/dev/null 2>&1 || true
    fi
}
