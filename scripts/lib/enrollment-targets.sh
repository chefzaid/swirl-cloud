#!/usr/bin/env bash
# shellcheck disable=SC2034,SC2154 # SSH options and target results are shared with add-k3s-workers.sh.
# SSH reachability and private-transport provisioning for nodes that
# add-k3s-workers.sh enrolls. Callers provide logging, ssh_options, scp_options
# and the enrollment globals.

build_target() {
    local host="$1"
    if [[ "$host" == *@* || -z "$SSH_USER" ]]; then
        printf '%s' "$host"
    else
        printf '%s@%s' "$SSH_USER" "$host"
    fi
}

check_bootstrap_target() {
    local target="$1"
    [[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ || "$target" =~ ^[A-Za-z0-9._:-]+$ ]] || \
        error "Invalid SSH bootstrap target: $target"
    ssh "${ssh_options[@]}" "$target" \
        'test "$(id -u)" -eq 0 || (command -v sudo >/dev/null 2>&1 && sudo -n true)' >/dev/null || \
        error "Cannot use passwordless sudo on $target. Configure SSH keys and NOPASSWD sudo, or connect as root."
}

provision_vrack_target() {
    local bootstrap_target="$1" worker_ip="$2" private_interface="$3" interface_mac="$4" vlan_id="$5" ovh_server="$6"
    local remote_dir quoted_dir remote_script quoted_script configured_interface detected_mac="" private_target reachable=false
    local remote_command argument quoted_argument

    info "Checking public/bootstrap SSH before OVHcloud vRack configuration on $bootstrap_target..."
    check_bootstrap_target "$bootstrap_target"
    if [[ "$OVH_VRACK_AUTOMATE_ACCOUNT" == "true" ]]; then
        [[ -n "$ovh_server" ]] || error "The OVHcloud Dedicated Server service name is required for API attachment."
        detected_mac="$(OVH_APPLICATION_KEY="$OVH_APPLICATION_KEY" \
            OVH_APPLICATION_SECRET="$OVH_APPLICATION_SECRET" \
            OVH_CONSUMER_KEY="$OVH_CONSUMER_KEY" \
            "$OVH_VRACK_CONFIGURATOR" --attach-server "$ovh_server" \
                --vrack "$OVH_VRACK_SERVICE_NAME" --ovh-endpoint "$OVH_API_ENDPOINT" --non-interactive)"
        if [[ -z "$interface_mac" && "${detected_mac,,}" =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ ]]; then
            interface_mac="$detected_mac"
        fi
    fi

    remote_dir="$(ssh "${ssh_options[@]}" "$bootstrap_target" 'mktemp -d /tmp/swirl-cloud-vrack.XXXXXX')"
    [[ "$remote_dir" == /tmp/swirl-cloud-vrack.* ]] || error "Could not create a safe vRack configuration directory on $bootstrap_target."
    printf -v quoted_dir '%q' "$remote_dir"
    ssh "${ssh_options[@]}" "$bootstrap_target" "mkdir -m 700 $quoted_dir/lib"
    scp "${scp_options[@]}" "$OVH_VRACK_CONFIGURATOR" "$bootstrap_target:$remote_dir/"
    scp "${scp_options[@]}" "$NETWORK_LIBRARY" "$bootstrap_target:$remote_dir/lib/"
    remote_script="$remote_dir/configure-ovh-vrack.sh"
    printf -v quoted_script '%q' "$remote_script"
    remote_args=(
        --configure-node
        --private-ip "$worker_ip"
        --network-cidr "$NODE_NETWORK_CIDR"
        --non-interactive
    )
    [[ -z "$private_interface" ]] || remote_args+=(--interface "$private_interface")
    [[ -z "$interface_mac" ]] || remote_args+=(--interface-mac "$interface_mac")
    [[ -z "$vlan_id" ]] || remote_args+=(--vlan-id "$vlan_id")
    remote_command="chmod 700 $quoted_script && $quoted_script"
    for argument in "${remote_args[@]}"; do
        printf -v quoted_argument '%q' "$argument"
        remote_command+=" $quoted_argument"
    done
    info "Configuring $worker_ip on the OVHcloud private NIC before UFW changes..."
    if ! configured_interface="$(ssh "${ssh_options[@]}" "$bootstrap_target" "$remote_command")"; then
        ssh "${ssh_options[@]}" "$bootstrap_target" "rm -r -- $quoted_dir" >/dev/null 2>&1 || true
        error "vRack NIC configuration failed on $bootstrap_target; its public firewall was not changed."
    fi
    configured_interface="$(awk 'NF {value=$0} END {print value}' <<< "$configured_interface")"
    ssh "${ssh_options[@]}" "$bootstrap_target" "rm -r -- $quoted_dir" >/dev/null 2>&1 || true

    # Prove the original bootstrap path survived before attempting the private path.
    check_bootstrap_target "$bootstrap_target"
    cidr_contains_ip "$NODE_NETWORK_CIDR" "$worker_ip" || error "$worker_ip is outside $NODE_NETWORK_CIDR."
    private_target="$(build_target "$worker_ip")"
    for _ in {1..15}; do
        if ssh "${ssh_options[@]}" "$private_target" true >/dev/null 2>&1; then
            reachable=true
            break
        fi
        sleep 2
    done
    [[ "$reachable" == "true" ]] || \
        error "Private SSH did not become reachable at $private_target; bootstrap SSH remains available at $bootstrap_target and UFW was not changed."
    info "Private SSH is proven through $configured_interface; node UFW may now close public ingress."
    VRACK_TARGET="$private_target"
}

provision_tailscale_target() {
    local bootstrap_target="$1" requested_hostname="${2:-}" remote_dir remote_script quoted_dir quoted_script node_auth_key worker_ip detected_hostname
    local tagged_target="" reachable=false

    if [[ -z "$requested_hostname" ]]; then
        info "Checking bootstrap SSH access to $bootstrap_target..."
        check_bootstrap_target "$bootstrap_target"
        detected_hostname="$(remote_hostname "$bootstrap_target")"
        TAILSCALE_NODE_NAME="$detected_hostname"
    else
        TAILSCALE_NODE_NAME="$requested_hostname"
    fi
    [[ -n "$TAILSCALE_NODE_NAME" ]] || error "Could not determine the hostname of $bootstrap_target."
    [[ "$TAILSCALE_NODE_NAME" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || \
        error "Invalid Tailscale hostname '$TAILSCALE_NODE_NAME'; use lower-case letters, numbers, and hyphens."

    remote_dir="$(ssh "${ssh_options[@]}" "$bootstrap_target" 'mktemp -d /tmp/swirl-cloud-tailscale.XXXXXX')"
    [[ "$remote_dir" == /tmp/swirl-cloud-tailscale.* ]] || error "Could not create a safe Tailscale bootstrap directory on $bootstrap_target."
    printf -v quoted_dir '%q' "$remote_dir"
    ssh "${ssh_options[@]}" "$bootstrap_target" "chmod 700 $quoted_dir"
    scp "${scp_options[@]}" "$TAILSCALE_CONFIGURATOR" "$bootstrap_target:$remote_dir/"
    remote_script="$remote_dir/configure-tailscale.sh"
    printf -v quoted_script '%q' "$remote_script"

    info "Creating a one-use Tailscale $ENROLLMENT_ROLE key for $TAILSCALE_NODE_NAME."
    node_auth_key="$(printf '%s\n' "$TAILSCALE_API_TOKEN" | \
        "$TAILSCALE_CONFIGURATOR" --role "$ENROLLMENT_ROLE" \
            --tailnet "$TAILSCALE_TAILNET" --mesh-name "$TAILSCALE_MESH_NAME" \
            --auth-key-expiry "$TAILSCALE_AUTH_KEY_EXPIRY_SECONDS" \
            --create-auth-key --api-token-stdin)"
    [[ "$node_auth_key" =~ ^tskey-auth-[A-Za-z0-9_-]+$ ]] || error "Could not create a Tailscale worker auth key."
    info "Installing and connecting Tailscale on $bootstrap_target."
    if ! worker_ip="$(printf '%s\n' "$node_auth_key" | ssh "${ssh_options[@]}" "$bootstrap_target" \
        "chmod 700 $quoted_script && $quoted_script --role $(printf '%q' "$ENROLLMENT_ROLE") --tailnet $(printf '%q' "$TAILSCALE_TAILNET") --mesh-name $(printf '%q' "$TAILSCALE_MESH_NAME") --hostname $(printf '%q' "$TAILSCALE_NODE_NAME") --auth-key-stdin")"; then
        node_auth_key=""
        ssh "${ssh_options[@]}" "$bootstrap_target" "rm -r -- $quoted_dir" >/dev/null 2>&1 || true
        error "Tailscale provisioning failed on $bootstrap_target."
    fi
    node_auth_key=""
    worker_ip="$(awk 'NF {value=$0} END {print value}' <<< "$worker_ip")"
    tailscale_ipv4 "$worker_ip" || error "The worker did not return a valid Tailscale IPv4 address: ${worker_ip:-none}"
    printf '%s\n' "$TAILSCALE_API_TOKEN" | \
        "$TAILSCALE_CONFIGURATOR" --role "$ENROLLMENT_ROLE" \
            --tailnet "$TAILSCALE_TAILNET" --mesh-name "$TAILSCALE_MESH_NAME" \
            --tag-ip "$worker_ip" --api-token-stdin >/dev/null
    ssh "${ssh_options[@]}" "$bootstrap_target" "rm -r -- $quoted_dir" >/dev/null 2>&1 || true

    "${LOCAL_SUDO[@]}" tailscale ping --c=3 --timeout=5s "$worker_ip" >/dev/null || \
        error "The control plane cannot reach new Tailscale peer $worker_ip."
    tagged_target="$(build_target "$worker_ip")"
    for _ in {1..15}; do
        if ssh "${ssh_options[@]}" "$tagged_target" true >/dev/null 2>&1; then
            reachable=true
            break
        fi
        sleep 2
    done
    [[ "$reachable" == "true" ]] || error "SSH did not become reachable over Tailscale at $tagged_target."
    TAILSCALE_TARGET="$tagged_target"
    TAILSCALE_WORKER_IP="$worker_ip"
}

check_target() {
    local target="$1" expected_worker_ip="$2"
    local connection_info client_ip worker_ip
    [[ "$target" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ || "$target" =~ ^[A-Za-z0-9._:-]+$ ]] || \
        error "Invalid SSH target: $target"
    ssh "${ssh_options[@]}" "$target" \
        'test "$(id -u)" -eq 0 || (command -v sudo >/dev/null 2>&1 && sudo -n true)' >/dev/null || \
        error "Cannot use passwordless sudo on $target. Configure SSH keys and NOPASSWD sudo, or connect as root."
    connection_info="$(ssh "${ssh_options[@]}" "$target" 'printf "%s" "$SSH_CONNECTION"')"
    read -r client_ip _ worker_ip _ <<< "$connection_info"
    trusted_private_ipv4 "$client_ip" || \
        error "SSH to $target does not originate from an RFC1918/Tailscale control-plane address (observed: ${client_ip:-unknown})."
    trusted_private_ipv4 "$worker_ip" || \
        error "SSH to $target reached a non-private worker address (${worker_ip:-unknown})."
    [[ "$worker_ip" == "$expected_worker_ip" ]] || \
        error "SSH to $target reached $worker_ip instead of the entered worker local IP $expected_worker_ip."
    cidr_contains_ip "$NODE_NETWORK_CIDR" "$client_ip" || \
        error "Control-plane source $client_ip is outside trusted node CIDR $NODE_NETWORK_CIDR."
    cidr_contains_ip "$NODE_NETWORK_CIDR" "$worker_ip" || \
        error "Worker address $worker_ip is outside trusted node CIDR $NODE_NETWORK_CIDR."
    [[ "$client_ip" == "$CONTROL_PLANE_IP" ]] || \
        error "SSH used $client_ip instead of the verified local control-plane source $CONTROL_PLANE_IP"
    TARGET_CONTROL_PLANE_IP="$client_ip"
    TARGET_WORKER_IP="$worker_ip"
}

remote_hostname() {
    ssh "${ssh_options[@]}" "$1" "hostname -s | tr '[:upper:]' '[:lower:]'"
}
