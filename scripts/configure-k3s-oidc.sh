#!/usr/bin/env bash
# Let administrators use kubectl as themselves: the K3s API server accepts tokens
# from Keycloak (client "kubernetes"), naming users and groups with an "oidc:"
# prefix, so the API audit log records the person instead of system:admin.
# Through a managed config.yaml.d drop-in that K3s reads at start: --restart
# applies it now (a short API outage; workloads keep running), otherwise the
# next K3s restart does. Root's kubeconfig keeps working for emergencies.
set -Eeuo pipefail
set +x
CONFIG_ROOT=/etc/rancher/k3s
DROPIN_PATH="$CONFIG_ROOT/config.yaml.d/41-swirl-cloud-oidc.yaml"
error() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO]  $*"; }

usage() {
    cat <<USAGE
Usage: PLATFORM_DOMAIN=example.com KEYCLOAK_REALM=example $0 [--check | --restart]

  (no option)  Install the K3s drop-in; report a pending restart.
  --check      Report whether Keycloak sign-in is configured; change nothing.
  --restart    Install, then restart K3s now if the configuration changed.
USAGE
}

dropin() {
    printf '# Managed by scripts/configure-k3s-oidc.sh\nkube-apiserver-arg+:\n'
    printf '  - "%s"\n' "oidc-issuer-url=https://keycloak.${PLATFORM_DOMAIN}/auth/realms/${KEYCLOAK_REALM}" \
        oidc-client-id=kubernetes oidc-username-claim=preferred_username "oidc-username-prefix=oidc:" \
        oidc-groups-claim=groups "oidc-groups-prefix=oidc:"
}

main() {
    local mode="${1:-}" changed=false file temporary started
    SUDO=(sudo)
    [[ "$mode" != --check ]] || SUDO=(sudo -n)
    case "$mode" in
        ""|--restart|--check) ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 1 ;;
    esac
    if [[ "$mode" == --check ]]; then
        "${SUDO[@]}" test -f "$DROPIN_PATH" 2>/dev/null || { echo "missing"; exit 1; }
        started="$(date -d "$(systemctl show k3s.service -p ExecMainStartTimestamp --value 2>/dev/null)" +%s 2>/dev/null || echo 0)"
        if [[ "$("${SUDO[@]}" stat -c %Y "$DROPIN_PATH")" -ge "$started" ]]; then echo "pending-restart"; else echo "active"; fi
        exit 0
    fi
    systemctl cat k3s-agent.service >/dev/null 2>&1 && error "API server sign-in belongs on K3s servers, not workers."
    [[ "${PLATFORM_DOMAIN:-}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]] ||
        error "Set PLATFORM_DOMAIN to the platform's public domain"
    [[ "${KEYCLOAK_REALM:-}" =~ ^[A-Za-z0-9_-][A-Za-z0-9_.-]*$ && "$KEYCLOAK_REALM" != master ]] ||
        error "Set KEYCLOAK_REALM to the platform's realm"
    sudo install -d -o root -g root -m 0755 "$(dirname "$CONFIG_ROOT")" "$CONFIG_ROOT" "$CONFIG_ROOT/config.yaml.d"
    # A later drop-in that replaces kube-apiserver-arg would silently drop this one.
    for file in "$CONFIG_ROOT"/config.yaml.d/*.yaml; do
        sudo test -f "$file" || continue
        [[ "$file" > "$DROPIN_PATH" ]] && sudo grep -Eq '^[[:space:]]*kube-apiserver-arg:' "$file" && \
            error "A later K3s drop-in replaces kube-apiserver arguments: $file"
    done
    temporary="$(mktemp)"
    trap 'rm -f "$temporary"' EXIT
    dropin > "$temporary"
    if ! sudo cmp -s "$temporary" "$DROPIN_PATH" 2>/dev/null; then
        sudo install -D -o root -g root -m 0644 "$temporary" "$DROPIN_PATH"
        changed=true
    fi
    if ! systemctl cat k3s.service >/dev/null 2>&1; then
        info "Keycloak sign-in for kubectl will start with the K3s installation."
    elif [[ "$changed" == false ]]; then
        info "Keycloak sign-in for kubectl is configured."
    elif [[ "$mode" == --restart ]]; then
        info "Restarting K3s to accept Keycloak tokens (the API is briefly unavailable; workloads keep running)..."
        sudo systemctl restart k3s
        for _ in $(seq 60); do
            sudo k3s kubectl get --raw /readyz >/dev/null 2>&1 && break
            sleep 2
        done
        sudo k3s kubectl get --raw /readyz >/dev/null 2>&1 || error "K3s restarted but its API is not ready; check: sudo journalctl -u k3s"
        info "Keycloak sign-in for kubectl is active."
    else
        info "Keycloak sign-in for kubectl is configured; it starts at the next K3s restart (or rerun with --restart)."
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
