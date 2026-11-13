#!/usr/bin/env bash
# Reserve memory for the host and K3s on this node and let the kubelet evict pods
# before the node swaps, through a managed config.yaml.d drop-in. K3s reads it at
# start: --restart applies it now (the kubelet, and on a server the API, restart
# briefly; workloads keep running), otherwise the next K3s restart does.
set -Eeuo pipefail
set +x
CONFIG_ROOT=/etc/rancher/k3s
DROPIN_PATH="$CONFIG_ROOT/config.yaml.d/30-swirl-cloud-kubelet-memory.yaml"
MEMINFO=/proc/meminfo
error() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO]  $*"; }

usage() {
    cat <<USAGE
Usage: $0 [--check | --restart]

  (no option)  Install the K3s drop-in; report a pending restart.
  --check      Report whether the reservation is configured and active; change nothing.
  --restart    Install, then restart K3s now if the configuration changed.

Memory for K3s itself scales with the node (KUBE_RESERVED_MEMORY overrides it, for
example 4Gi); the host keeps SYSTEM_RESERVED_MEMORY (default 1Gi).
USAGE
}

# Memory for the K3s process (API server, datastore, kubelet and containerd),
# scaled as GKE reserves it: 25 % of the first 4 GiB, 20 % of the next 4 GiB,
# 10 % of the next 8 GiB, 6 % up to 128 GiB and 2 % above, in MiB.
kube_reserved_mib() {
    local left=$1 reserved=0 band size
    for band in 4096:25 4096:20 8192:10 114688:6 0:2; do
        size=${band%:*}
        (( size == 0 || size > left )) && size=$left
        reserved=$(( reserved + size * ${band#*:} / 100 ))
        left=$(( left - size ))
    done
    echo "$reserved"
}

dropin() {
    local total_mib
    total_mib=$(( $(awk '$1 == "MemTotal:" {print $2}' "$MEMINFO") / 1024 ))
    (( total_mib > 0 )) || error "Cannot read the node's memory from $MEMINFO"
    printf '# Managed by scripts/configure-k3s-kubelet-memory.sh\nkubelet-arg+:\n'
    # eviction-hard replaces K3s's default, so it repeats K3s's disk thresholds.
    printf '  - "%s"\n' \
        "kube-reserved=memory=${KUBE_RESERVED_MEMORY:-$(kube_reserved_mib "$total_mib")Mi}" \
        "system-reserved=memory=${SYSTEM_RESERVED_MEMORY:-1Gi}" \
        "eviction-hard=memory.available<500Mi,nodefs.available<5%,imagefs.available<5%" \
        "eviction-soft=memory.available<1536Mi" \
        "eviction-soft-grace-period=memory.available=1m" \
        "eviction-max-pod-grace-period=60"
}

unit() {
    if systemctl cat k3s.service >/dev/null 2>&1; then echo k3s
    elif systemctl cat k3s-agent.service >/dev/null 2>&1; then echo k3s-agent
    fi
}

pending() {
    local service="$1" started
    systemctl is-active --quiet "$service" || return 1
    started="$(date -d "$(systemctl show "$service.service" -p ExecMainStartTimestamp --value)" +%s)"
    [[ "$("${SUDO[@]}" stat -c %Y "$DROPIN_PATH")" -ge "$started" ]]
}

kubelet_healthy() {
    [[ "$(curl -fsS --max-time 5 http://127.0.0.1:10248/healthz 2>/dev/null)" == ok ]]
}

main() {
    # temporary is not local: the EXIT trap runs after main returns.
    local mode="${1:-}" service changed=false file
    SUDO=(sudo)
    # Read-only checks run from status screens and must never wait for a password.
    [[ "$mode" != --check ]] || SUDO=(sudo -n)
    case "$mode" in
        ""|--restart|--check) ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 1 ;;
    esac
    service="$(unit)"

    if [[ "$mode" == --check ]]; then
        "${SUDO[@]}" test -f "$DROPIN_PATH" 2>/dev/null || { echo "missing"; exit 1; }
        if [[ -z "$service" ]] || ! systemctl is-active --quiet "$service"; then echo "configured"
        elif pending "$service"; then echo "pending-restart"
        else echo "active"
        fi
        exit 0
    fi

    # K3s's configuration tree is world-readable: kubectl (the k3s binary) reads these
    # drop-ins as any user. Create it explicitly so the installer's umask 077 cannot
    # leave it 0700, which also hides the drop-ins from the check below.
    sudo install -d -o root -g root -m 0755 "$(dirname "$CONFIG_ROOT")" "$CONFIG_ROOT" "$CONFIG_ROOT/config.yaml.d"
    # A later drop-in that replaces kubelet-arg would silently drop the reservation.
    for file in "$CONFIG_ROOT"/config.yaml.d/*.yaml; do
        sudo test -f "$file" || continue
        [[ "$file" > "$DROPIN_PATH" ]] && sudo grep -Eq '^[[:space:]]*kubelet-arg:' "$file" && \
            error "A later K3s drop-in replaces kubelet arguments: $file"
    done
    temporary="$(mktemp)"
    trap 'rm -f "$temporary"' EXIT
    dropin > "$temporary"
    if ! sudo cmp -s "$temporary" "$DROPIN_PATH" 2>/dev/null; then
        sudo install -D -o root -g root -m 0644 "$temporary" "$DROPIN_PATH"
        changed=true
    fi

    if [[ -z "$service" ]]; then
        info "The kubelet memory reservation will apply when K3s is installed."
    elif [[ "$changed" == false ]] && ! pending "$service"; then
        info "The kubelet memory reservation is active."
    elif [[ "$mode" == --restart ]]; then
        info "Restarting $service to apply the kubelet memory reservation (workloads keep running)..."
        sudo systemctl restart "$service"
        for _ in $(seq 90); do
            kubelet_healthy && break
            sleep 2
        done
        kubelet_healthy || error "$service restarted but the kubelet is not healthy; check: sudo journalctl -u $service"
        info "The kubelet memory reservation is active."
    else
        info "The kubelet memory reservation is configured; it applies at the next K3s restart (or rerun with --restart)."
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
