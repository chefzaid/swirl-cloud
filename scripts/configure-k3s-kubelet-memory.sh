#!/usr/bin/env bash
# Reserve memory for the host and K3s on this node and let the kubelet evict pods
# before the node swaps, and cap the pods the node runs (KUBELET_MAX_PODS), through a
# managed config.yaml.d drop-in. Also stop pods in priority order when the host shuts
# down, through a kubelet configuration drop-in and a logind drop-in that lets the
# kubelet delay the shutdown that long.
# K3s reads both at start: --restart applies them now (the kubelet, and on a server
# the API, restart briefly; workloads keep running), otherwise the next K3s restart does.
set -Eeuo pipefail
set +x
CONFIG_ROOT=/etc/rancher/k3s
DROPIN_PATH="$CONFIG_ROOT/config.yaml.d/30-swirl-cloud-kubelet-memory.yaml"
# K3s 1.32+ reads kubelet configuration drop-ins here, after its own defaults.
KUBELET_CONFIG_PATH="${KUBELET_CONFIG_DIR:-/var/lib/rancher/k3s/agent/etc/kubelet.conf.d}/50-swirl-cloud-shutdown.conf"
# logind reads its drop-ins from /etc, /run and /usr/lib in file-name order and the
# last one wins. Ubuntu's unattended-upgrades-logind-maxdelay.conf (30 seconds) comes
# after the kubelet's own 99-kubelet.conf, and with less than the sum of the groups
# the kubelet's shutdown manager does not start, so this name sorts after both.
LOGIND_PATH="${LOGIND_CONFIG_DIR:-/etc/systemd/logind.conf.d}/zz-swirl-cloud-kubelet-shutdown.conf"
MEMINFO=/proc/meminfo
# A server's K3s flags, where the installer passes them (node-cidr-mask-size among them).
K3S_UNIT_PATH=/etc/systemd/system/k3s.service
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
KUBELET_MAX_PODS caps the pods on this node (the sizing profile's kubeletMaxPods);
empty restores K3s's 110, and unset keeps the node's current cap. It must fit the
node's pod range: 252 on K3s's default /24.
USAGE
}

# Addresses the node's pod range (podCIDR) leaves for pods: the network and broadcast
# addresses, the cni0 gateway and one spare are kept out.
POD_RANGE_RESERVE=4

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
    [[ -z "${MAX_PODS:-}" ]] || printf '  - "%s"\n' "max-pods=$MAX_PODS"
}

# The prefix length of every node's pod range: the controller manager gives each node
# the same (node-cidr-mask-size, /24 by default in K3s). Read from the registered
# nodes when the API answers, else from the server's K3s configuration, else /24.
node_cidr_mask() {
    local cidrs file mask=""
    cidrs="$(KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}" kubectl --request-timeout=5s get nodes \
        -o jsonpath='{.items[*].spec.podCIDR}' 2>/dev/null)" || cidrs=""
    mask="$(tr ' ' '\n' <<< "$cidrs" | sed -n 's#^[0-9.]*/\([0-9]\{1,2\}\)$#\1#p' | sort -n | tail -1)"
    if [[ -z "$mask" ]]; then
        for file in "$CONFIG_ROOT/config.yaml" "$CONFIG_ROOT"/config.yaml.d/*.yaml "$K3S_UNIT_PATH"; do
            [[ -r "$file" ]] || continue
            mask="$(grep -Eo 'node-cidr-mask-size(-ipv4)?=[0-9]+' "$file" | tail -1 | sed 's/.*=//')" || true
            [[ -z "$mask" ]] || break
        done
    fi
    echo "${mask:-24}"
}

# MAX_PODS: KUBELET_MAX_PODS when given (empty: K3s's default), else the cap the
# drop-in already sets, so a rerun without the sizing profile keeps it.
resolve_max_pods() {
    local mask capacity
    if [[ -n "${KUBELET_MAX_PODS+set}" ]]; then
        MAX_PODS="$KUBELET_MAX_PODS"
    else
        MAX_PODS="$(sed -n 's/^  - "max-pods=\([0-9]*\)"$/\1/p' "$DROPIN_PATH" 2>/dev/null | tail -1)" || MAX_PODS=""
    fi
    [[ -n "$MAX_PODS" ]] || return 0
    [[ "$MAX_PODS" =~ ^[1-9][0-9]{0,5}$ ]] || error "KUBELET_MAX_PODS must be a positive whole number, not '$MAX_PODS'"
    mask="$(node_cidr_mask)"
    capacity=$(( mask > 30 ? 0 : (1 << (32 - mask)) - POD_RANGE_RESERVE ))
    (( MAX_PODS <= capacity )) || \
        error "KUBELET_MAX_PODS=$MAX_PODS does not fit the node's /$mask pod range ($capacity pods at most)"
}

# On a host shutdown the kubelet stops pods from the lowest priority class up, each
# group within its time, so data stores and sign-in stop last and cleanly. The
# classes are in k8s/base/security-baseline.yaml; the kubelet holds the shutdown
# with a systemd inhibitor lock for the sum (150 seconds).
shutdown_config() {
    cat <<'CONFIG'
# Managed by scripts/configure-k3s-kubelet-memory.sh
apiVersion: kubelet.config.k8s.io/v1beta1
kind: KubeletConfiguration
shutdownGracePeriodByPodPriority:
  - priority: -100       # CI jobs
    shutdownGracePeriodSeconds: 10
  - priority: -50        # analysis tools, int and uat
    shutdownGracePeriodSeconds: 20
  - priority: 0          # platform services and unclassified pods
    shutdownGracePeriodSeconds: 30
  - priority: 500000     # production
    shutdownGracePeriodSeconds: 30
  - priority: 1000000    # data stores, sign-in, ingress and system pods
    shutdownGracePeriodSeconds: 60
CONFIG
}

shutdown_seconds() {
    shutdown_config | awk '$1 == "shutdownGracePeriodSeconds:" {total += $2} END {print total}'
}

logind_config() {
    printf '# Managed by scripts/configure-k3s-kubelet-memory.sh\n[Login]\nInhibitDelayMaxSec=%s\n' "$(shutdown_seconds)"
}

# The kubelet's shutdown manager holds a delay lock on shutdown while it runs.
shutdown_armed() {
    busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager \
        ListInhibitors 2>/dev/null | grep -Eq '"shutdown" "kubelet" "[^"]*" "delay"'
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
    [[ "$("${SUDO[@]}" stat -c %Y "$DROPIN_PATH")" -ge "$started" ]] ||
        [[ "$("${SUDO[@]}" stat -c %Y "$KUBELET_CONFIG_PATH" 2>/dev/null || echo 0)" -ge "$started" ]] ||
        [[ "$(stat -c %Y "$LOGIND_PATH" 2>/dev/null || echo 0)" -ge "$started" ]]
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
        if ! "${SUDO[@]}" test -f "$DROPIN_PATH" 2>/dev/null || ! "${SUDO[@]}" test -f "$KUBELET_CONFIG_PATH" 2>/dev/null ||
            ! test -f "$LOGIND_PATH"; then
            echo "missing"
            exit 1
        fi
        if [[ -z "$service" ]] || ! systemctl is-active --quiet "$service"; then echo "configured"
        elif pending "$service"; then echo "pending-restart"
        elif ! shutdown_armed; then echo "shutdown-off"
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
    resolve_max_pods
    temporary="$(mktemp)"
    trap 'rm -f "$temporary"' EXIT
    dropin > "$temporary"
    if ! sudo cmp -s "$temporary" "$DROPIN_PATH" 2>/dev/null; then
        sudo install -D -o root -g root -m 0644 "$temporary" "$DROPIN_PATH"
        changed=true
    fi
    shutdown_config > "$temporary"
    if ! sudo cmp -s "$temporary" "$KUBELET_CONFIG_PATH" 2>/dev/null; then
        sudo install -D -o root -g root -m 0644 "$temporary" "$KUBELET_CONFIG_PATH"
        changed=true
    fi
    # The kubelet arms its shutdown manager only when it starts, hence a restart too.
    logind_config > "$temporary"
    if ! sudo cmp -s "$temporary" "$LOGIND_PATH" 2>/dev/null; then
        sudo install -D -o root -g root -m 0644 "$temporary" "$LOGIND_PATH"
        # As the kubelet does: logind rereads its drop-ins on SIGHUP; sessions stay.
        if systemctl is-active --quiet systemd-logind; then
            sudo systemctl kill --signal=SIGHUP systemd-logind
        fi
        changed=true
    fi

    info "Pods per node: ${MAX_PODS:-K3s default (110)}."
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
        if systemctl is-active --quiet systemd-logind; then
            for _ in $(seq 15); do
                shutdown_armed && break
                sleep 2
            done
            shutdown_armed || error "The kubelet holds no shutdown lock, so pods would stop all at once; check: sudo journalctl -u $service | grep 'node shutdown'"
        fi
        info "The kubelet memory reservation is active."
    else
        info "The kubelet memory reservation is configured; it applies at the next K3s restart (or rerun with --restart)."
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
