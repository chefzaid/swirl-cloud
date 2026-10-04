#!/usr/bin/env bash
# Enable Kubernetes API audit logging on this K3s server through a managed
# config.yaml.d drop-in. K3s reads it at start: --restart applies it now (a short
# API outage; workloads keep running), otherwise the next K3s restart does.
set -Eeuo pipefail
set +x
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_ROOT=/etc/rancher/k3s
POLICY_SOURCE="$SCRIPT_DIR/../config/host/k3s-audit-policy.yaml"
POLICY_PATH="$CONFIG_ROOT/audit-policy.yaml"
DROPIN_PATH="$CONFIG_ROOT/config.yaml.d/40-swirl-cloud-api-audit.yaml"
LOG_PATH=/var/log/kubernetes/audit/audit.log
error() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO]  $*"; }

usage() {
    cat <<USAGE
Usage: $0 [--check | --restart]

  (no option)  Install the audit policy and K3s drop-in; report a pending restart.
  --check      Report whether auditing is configured and active; change nothing.
  --restart    Install, then restart K3s now if the configuration changed.
USAGE
}

dropin() {
    printf '# Managed by scripts/configure-k3s-audit.sh\nkube-apiserver-arg+:\n'
    printf '  - %s\n' "audit-policy-file=$POLICY_PATH" "audit-log-path=$LOG_PATH" \
        audit-log-maxage=30 audit-log-maxbackup=10 audit-log-maxsize=100
}

active() {
    # The running API server writes the file only when auditing is enabled.
    systemctl is-active --quiet k3s && "${SUDO[@]}" test -s "$LOG_PATH" &&
        [[ "$("${SUDO[@]}" stat -c %Y "$LOG_PATH")" -ge "$(date -d "$(systemctl show k3s.service -p ExecMainStartTimestamp --value)" +%s)" ]]
}

pending() {
    local started
    systemctl is-active --quiet k3s || return 1
    started="$(date -d "$(systemctl show k3s.service -p ExecMainStartTimestamp --value)" +%s)"
    [[ "$("${SUDO[@]}" stat -c %Y "$DROPIN_PATH")" -ge "$started" ]] && ! active
}

mode="${1:-}"
SUDO=(sudo)
# Read-only checks run from status screens and must never wait for a password.
[[ "$mode" != --check ]] || SUDO=(sudo -n)
case "$mode" in
    ""|--restart|--check) ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 1 ;;
esac
systemctl cat k3s-agent.service >/dev/null 2>&1 && error "Audit logging belongs on K3s servers, not workers."

if [[ "$mode" == --check ]]; then
    "${SUDO[@]}" test -f "$DROPIN_PATH" 2>/dev/null || { echo "missing"; exit 1; }
    if active; then echo "active"; elif pending; then echo "pending-restart"; else echo "configured"; fi
    exit 0
fi

[[ -r "$POLICY_SOURCE" ]] || error "Audit policy not found: $POLICY_SOURCE"
# A later drop-in or CLI argument that replaces kube-apiserver-arg would silently drop auditing.
for file in "$CONFIG_ROOT"/config.yaml.d/*.yaml; do
    sudo test -f "$file" || continue
    [[ "$file" > "$DROPIN_PATH" ]] && sudo grep -Eq '^[[:space:]]*kube-apiserver-arg:' "$file" && \
        error "A later K3s drop-in replaces kube-apiserver arguments: $file"
done
changed=false
temporary="$(mktemp)"
trap 'rm -f "$temporary"' EXIT
if ! sudo cmp -s "$POLICY_SOURCE" "$POLICY_PATH" 2>/dev/null; then
    sudo install -D -o root -g root -m 0600 "$POLICY_SOURCE" "$POLICY_PATH"
    changed=true
fi
dropin > "$temporary"
if ! sudo cmp -s "$temporary" "$DROPIN_PATH" 2>/dev/null; then
    sudo install -D -o root -g root -m 0600 "$temporary" "$DROPIN_PATH"
    changed=true
fi
sudo install -d -o root -g root -m 0700 "$(dirname "$LOG_PATH")"

if ! systemctl cat k3s.service >/dev/null 2>&1; then
    info "API audit logging will start with the K3s installation."
elif active && [[ "$changed" == false ]]; then
    info "API audit logging is active: $LOG_PATH"
elif [[ "$mode" == --restart ]]; then
    info "Restarting K3s to enable API audit logging (the API is briefly unavailable; workloads keep running)..."
    sudo systemctl restart k3s
    for _ in $(seq 60); do
        active && break
        sleep 2
    done
    active || error "K3s restarted but $LOG_PATH is not being written; check: sudo journalctl -u k3s"
    info "API audit logging is active: $LOG_PATH"
else
    info "API audit logging is configured; it starts at the next K3s restart (or rerun with --restart)."
fi
