#!/usr/bin/env bash
# Bootstrap embedded etcd without reinstalling or resetting an existing server.
set -euo pipefail
set +x
umask 077

k3s_ha_prepare() {
    local config_path=/etc/rancher/k3s/config.yaml.d/30-swirl-cloud-embedded-etcd.yaml
    local database=/var/lib/rancher/k3s/server/db/state.db
    local existing_server=false config_file backup_dir

    if [[ -n "${K3S_DATASTORE_ENDPOINT:-}" ]]; then
        printf '[ERROR] K3S_DATASTORE_ENDPOINT is incompatible with managed embedded etcd.\n' >&2
        return 1
    fi
    if sudo test -d /var/lib/rancher/k3s/server/db/etcd/member; then
        printf '[INFO] Embedded etcd is already initialized; preserving its membership.\n'
        return
    fi
    if systemctl cat k3s.service >/dev/null 2>&1; then
        existing_server=true
        # Do not silently convert an external datastore or a custom service.
        if sudo grep -Eq -- 'datastore-endpoint|K3S_DATASTORE_ENDPOINT' \
            /etc/systemd/system/k3s.service /etc/systemd/system/k3s.service.env \
            /etc/default/k3s /etc/sysconfig/k3s 2>/dev/null || \
           sudo grep -REq -- '^[[:space:]]*datastore-endpoint:' /etc/rancher/k3s 2>/dev/null; then
            printf '[ERROR] External datastores require a separate migration; embedded-etcd initialization was not applied.\n' >&2
            return 1
        fi
        sudo test -f "$database" || {
            printf '[ERROR] Existing K3s has no recognized SQLite or embedded etcd datastore; restore it before adding servers.\n' >&2
            return 1
        }
        if ! command -v sqlite3 >/dev/null 2>&1; then
            sudo apt-get update -qq
            sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sqlite3 >/dev/null
        fi
        backup_dir="/var/backups/swirl-cloud/k3s/pre-etcd-$(date -u +%Y%m%dT%H%M%SZ)"
        sudo install -d -o root -g root -m 0700 "$backup_dir"
        sudo sqlite3 "$database" '.timeout 60000' ".backup '$backup_dir/state.db'"
        [[ "$(sudo sqlite3 "$backup_dir/state.db" 'PRAGMA integrity_check;')" == ok ]] || {
            printf '[ERROR] Pre-migration SQLite backup failed its integrity check.\n' >&2
            return 1
        }
        sudo tar -czf "$backup_dir/server-config.tar.gz" -C / \
            etc/rancher/k3s var/lib/rancher/k3s/server/token var/lib/rancher/k3s/server/cred
        printf '[INFO] SQLite recovery copy saved at %s before conversion.\n' "$backup_dir"
    fi

    config_file="$(mktemp)"
    printf '# Managed by scripts/configure-k3s-ha.sh\ncluster-init: true\n' > "$config_file"
    if ! sudo install -D -o root -g root -m 0600 "$config_file" "$config_path"; then
        rm -f -- "$config_file"
        return 1
    fi
    rm -f -- "$config_file"
    if [[ "$existing_server" == true ]]; then
        printf '[INFO] Converting the bootstrap server from SQLite to embedded etcd.\n'
        sudo systemctl restart k3s
        sudo k3s kubectl wait --for=condition=Ready node --all --timeout=300s
        sudo test -d /var/lib/rancher/k3s/server/db/etcd/member || {
            printf '[ERROR] Embedded etcd was not initialized; additional servers cannot join.\n' >&2
            return 1
        }
    else
        printf '[INFO] The first K3s server will initialize embedded etcd when installed.\n'
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        '') k3s_ha_prepare ;;
        -h|--help) printf 'Usage: %s\nInitialize embedded etcd on the bootstrap host; back up SQLite before conversion.\n' "$0" ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 1 ;;
    esac
fi
