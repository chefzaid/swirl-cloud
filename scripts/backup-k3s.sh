#!/bin/bash
set -euo pipefail
set +x

POSTGRES_ACCESS_LIBRARY="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/postgres-access.sh"
[[ -r "$POSTGRES_ACCESS_LIBRARY" ]] || POSTGRES_ACCESS_LIBRARY=/usr/local/lib/swirl-cloud/postgres-access.sh
# shellcheck source=lib/postgres-access.sh
source "$POSTGRES_ACCESS_LIBRARY"
VAULT_ACCESS_LIBRARY="$(dirname "$POSTGRES_ACCESS_LIBRARY")/vault-access.sh"
# shellcheck source=lib/vault-access.sh
source "$VAULT_ACCESS_LIBRARY"

BACKUP_ENVIRONMENT_FILE="${BACKUP_ENVIRONMENT_FILE:-/etc/swirl-cloud/backup.env}"
if [[ -r "$BACKUP_ENVIRONMENT_FILE" ]]; then
  # Root-owned, with shell-escaped values; restic reads its repository and
  # credentials from the environment.
  set -a
  # shellcheck disable=SC1090
  source "$BACKUP_ENVIRONMENT_FILE"
  set +a
fi

BACKUP_DIR="${BACKUP_DIR:-/var/backups/swirl-cloud/k3s}"
RETENTION_COUNT="${RETENTION_COUNT:-7}"
BACKUP_LOCK_WAIT_SECONDS="${BACKUP_LOCK_WAIT_SECONDS:-14400}"
K3S_DATA_DIR="${K3S_DATA_DIR:-/var/lib/rancher/k3s}"
K3S_CONFIG_DIR="${K3S_CONFIG_DIR:-/etc/rancher/k3s}"
K3S_DB="${K3S_DB:-$K3S_DATA_DIR/server/db/state.db}"
K3S_ETCD_DIR="$K3S_DATA_DIR/server/db/etcd"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$BACKUP_DIR/k3s-$TIMESTAMP.tar.gz"

[[ "$EUID" -eq 0 ]] || { echo "Run this backup as root." >&2; exit 1; }
[[ "$RETENTION_COUNT" =~ ^[1-9][0-9]*$ ]] || { echo "RETENTION_COUNT must be a positive integer." >&2; exit 1; }
[[ "$BACKUP_LOCK_WAIT_SECONDS" =~ ^[0-9]+$ ]] || { echo "BACKUP_LOCK_WAIT_SECONDS must be a whole number of seconds." >&2; exit 1; }
command -v k3s >/dev/null || { echo "k3s is required." >&2; exit 1; }
[[ -s "$K3S_DATA_DIR/server/token" ]] || { echo "K3s server token is required for a restorable backup." >&2; exit 1; }
# SQLite servers can have an empty etcd directory, and migrated servers keep
# state.db, so an initialized etcd member wins.
if [[ -d "$K3S_ETCD_DIR/member" ]]; then
  DATASTORE=etcd
elif [[ -f "$K3S_DB" ]]; then
  DATASTORE=sqlite
  command -v sqlite3 >/dev/null || { echo "sqlite3 is required for a SQLite datastore." >&2; exit 1; }
else
  echo "No supported local K3s datastore was found under $K3S_DATA_DIR/server/db." >&2
  exit 1
fi

umask 077
install -d -o root -g root -m 0700 "$BACKUP_DIR"
# The weekly verification holds this lock too (its unit stops after four
# hours). Wait instead of skipping, which would be recorded as a success.
exec 9>/run/lock/swirl-cloud-k3s-backup.lock
flock -w "$BACKUP_LOCK_WAIT_SECONDS" 9 || {
  echo "The backup lock is still held after $BACKUP_LOCK_WAIT_SECONDS seconds; no archive was created." >&2
  exit 1
}

staging_dir="$(mktemp -d "$BACKUP_DIR/.staging.XXXXXX")"
trap 'rm -rf -- "$staging_dir"' EXIT
install -d -m 0700 "$staging_dir/k3s-db" "$staging_dir/k3s-server" \
  "$staging_dir/k3s-config" "$staging_dir/vault" "$staging_dir/application-data" \
  "$staging_dir/kubernetes" "$staging_dir/gitlab"

if [[ "$DATASTORE" == "etcd" ]]; then
  # Snapshot into staging so it follows the archive's retention and off-node
  # policy, and no on-demand snapshots pile up.
  k3s etcd-snapshot save \
    --data-dir "$K3S_DATA_DIR" --name "swirl-cloud-$TIMESTAMP" \
    --etcd-snapshot-dir "$staging_dir/k3s-db" --etcd-s3=false
  mapfile -t etcd_snapshots < <(find "$staging_dir/k3s-db" -maxdepth 1 -type f -name "swirl-cloud-$TIMESTAMP*" -size +0c)
  [[ ${#etcd_snapshots[@]} -eq 1 ]] || {
    echo "K3s did not produce exactly one nonempty local etcd snapshot." >&2
    exit 1
  }
  snapshot_name="$(basename "${etcd_snapshots[0]}")"
else
  # The online backup API gives a consistent copy while K3s keeps writing.
  backup_db="$staging_dir/k3s-db/state.db"
  sqlite3 "$K3S_DB" ".timeout 60000" ".backup '$backup_db'"

  # Compact the staging copy to current state: old revisions and free pages are
  # not needed to restore and can keep deleted secrets. Never run on the live DB.
  sqlite3 "$backup_db" <<'SQL'
BEGIN IMMEDIATE;
DELETE FROM kine
WHERE id IN (
  SELECT prev_revision
  FROM kine
  WHERE name != 'compact_rev_key' AND prev_revision != 0
  UNION
  SELECT id
  FROM kine
  WHERE deleted != 0
);
UPDATE kine
SET prev_revision = 0, old_value = X''
WHERE name != 'compact_rev_key';
COMMIT;
VACUUM;
SQL
  [[ "$(sqlite3 "$backup_db" 'PRAGMA integrity_check;')" == "ok" ]] || {
    echo "Backup database integrity check failed." >&2
    exit 1
  }
fi

for source_file in \
  "$K3S_DATA_DIR/server/token" \
  "$K3S_DATA_DIR/server/cred/encryption-config.json" \
  "$K3S_DATA_DIR/server/cred/encryption-state.json"; do
  [[ -f "$source_file" ]] && install -m 0600 "$source_file" "$staging_dir/k3s-server/$(basename "$source_file")"
done

for source_file in "$K3S_CONFIG_DIR/config.yaml" "$K3S_CONFIG_DIR/registries.yaml"; do
  [[ -f "$source_file" ]] && install -m 0600 "$source_file" "$staging_dir/k3s-config/$(basename "$source_file")"
done
if [[ -d "$K3S_CONFIG_DIR/config.yaml.d" ]]; then
  cp -a "$K3S_CONFIG_DIR/config.yaml.d" "$staging_dir/k3s-config/config.yaml.d"
fi

# Vault keys are left out only while a kit exported with
# export-recovery-kit.sh --separate-archives holds these exact files.
RECOVERY_KIT_MANIFEST="${RECOVERY_KIT_MANIFEST:-/var/lib/swirl-cloud/recovery-kit.json}"
kit_holds() {
  [[ -f "$RECOVERY_KIT_MANIFEST" ]] && command -v jq >/dev/null 2>&1 || return 1
  jq -e '.separateArchives == true' "$RECOVERY_KIT_MANIFEST" >/dev/null 2>&1 || return 1
  [[ "$(jq -r --arg file "$1" '.fingerprints[$file] // empty' "$RECOVERY_KIT_MANIFEST")" == \
     "$(sha256sum "$1" | cut -d' ' -f1)" ]]
}
kit_omitted=()
for source_file in /var/lib/swirl-cloud/vault-unseal-key /var/lib/swirl-cloud/vault-bootstrap-token; do
  [[ -f "$source_file" ]] || continue
  if kit_holds "$source_file"; then
    kit_omitted+=("$(basename "$source_file")")
    continue
  fi
  if [[ -f "$RECOVERY_KIT_MANIFEST" ]] && jq -e '.separateArchives == true' "$RECOVERY_KIT_MANIFEST" >/dev/null 2>&1; then
    echo "WARNING: $source_file changed since the recovery kit was exported; archiving it. Export a new kit." >&2
  fi
  install -m 0600 "$source_file" "$staging_dir/vault/$(basename "$source_file")"
done

if command -v kubectl >/dev/null 2>&1 && kubectl get --raw=/readyz >/dev/null 2>&1; then
  kubectl get persistentvolumeclaims -A -o yaml > "$staging_dir/kubernetes/persistentvolumeclaims.yaml"
  kubectl get volumes.longhorn.io -n longhorn-system -o yaml \
    > "$staging_dir/kubernetes/longhorn-volumes.yaml" 2>/dev/null || true
  if kubectl get recurringjob.longhorn.io swirl-cloud-daily-backup -n longhorn-system >/dev/null 2>&1; then
    kubectl label volumes.longhorn.io -n longhorn-system --all \
      recurring-job-group.longhorn.io/swirl-cloud=enabled --overwrite >/dev/null 2>&1 || true
  fi

  if kubectl get service vault -n infra >/dev/null 2>&1 && [[ -s /var/lib/swirl-cloud/vault-bootstrap-token ]]; then
    vault_backup_pod="$(vault_runtime_pod infra)"
    vault_snapshot_path="/vault/data/.swirl-cloud-$TIMESTAMP.snap"
    vault_token="$(< /var/lib/swirl-cloud/vault-bootstrap-token)"
    { printf '%s\n' "$vault_token"; } | kubectl exec -i -n infra "$vault_backup_pod" -- sh -ceu '
      IFS= read -r VAULT_TOKEN
      export VAULT_TOKEN VAULT_ADDR=http://127.0.0.1:8200
      vault operator raft snapshot save "$1"
    ' sh "$vault_snapshot_path" >/dev/null
    kubectl exec -n infra "$vault_backup_pod" -- cat "$vault_snapshot_path" > "$staging_dir/vault/raft.snap"
    kubectl exec -n infra "$vault_backup_pod" -- rm -f -- "$vault_snapshot_path"
    unset vault_token
  fi

  if kubectl get service postgres -n infra >/dev/null 2>&1; then
    postgres_target="$(postgres_runtime_target infra)"
    postgres_username="$(kubectl get secret postgres-secret -n infra -o jsonpath='{.data.POSTGRES_USER}' | base64 -d)"
    postgres_password="$(kubectl get secret postgres-secret -n infra -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
    { printf '%s\n%s\n' "$postgres_username" "$postgres_password"; } | kubectl exec -i -n infra "$postgres_target" -c postgres -- sh -ceu '
      IFS= read -r postgres_username
      IFS= read -r PGPASSWORD
      export PGPASSWORD
      exec pg_dumpall --host=127.0.0.1 --username="$postgres_username" --clean --if-exists
    ' | gzip -9 > "$staging_dir/application-data/postgresql.sql.gz"
    unset postgres_username postgres_password
  fi

  if kubectl get deployment mongodb -n infra >/dev/null 2>&1; then
    mongodb_username="$(kubectl get secret mongodb-secret -n infra -o jsonpath='{.data.MONGO_INITDB_ROOT_USERNAME}' | base64 -d)"
    mongodb_password="$(kubectl get secret mongodb-secret -n infra -o jsonpath='{.data.MONGO_INITDB_ROOT_PASSWORD}' | base64 -d)"
    { printf '%s\n%s\n' "$mongodb_username" "$mongodb_password"; } | kubectl exec -i -n infra deployment/mongodb -- sh -ceu '
      IFS= read -r mongodb_username
      IFS= read -r mongodb_password
      exec mongodump --username "$mongodb_username" --password "$mongodb_password" \
        --authenticationDatabase admin --archive --gzip
    ' > "$staging_dir/application-data/mongodb.archive.gz"
    unset mongodb_username mongodb_password
  fi

  # Attachments live outside PostgreSQL; copy them after the dump that references them.
  if kubectl get deployment odoo -n corp >/dev/null 2>&1; then
    kubectl exec -n corp deployment/odoo -c odoo -- tar -C /var/lib/odoo -czf - filestore \
      > "$staging_dir/application-data/odoo-filestore.tar.gz"
  fi

  # GitLab data restores need the same version and this /etc/gitlab (secrets,
  # SSH host keys, gitlab.rb). GitLab's native backup is separate.
  if kubectl get deployment gitlab -n infra >/dev/null 2>&1; then
    # Recreate rollouts leave one live pod; read everything from it.
    gitlab_pod="$(kubectl get pods -n infra -l app=gitlab -o json | jq -c '[.items[] |
      select(.status.phase == "Running" and .metadata.deletionTimestamp == null)] |
      if length == 1 then .[0] else empty end')"
    [[ -n "$gitlab_pod" ]] || { echo "GitLab has no single running pod to copy /etc/gitlab from." >&2; exit 1; }
    gitlab_pod_name="$(jq -r '.metadata.name' <<< "$gitlab_pod")"
    gitlab_config="$staging_dir/gitlab/etc-gitlab.tar"
    kubectl exec -n infra "$gitlab_pod_name" -c gitlab -- tar -C /etc/gitlab -cf - . > "$gitlab_config"
    # tar lists a stream cut between members without error; only a complete
    # archive ends in zero blocks.
    if [[ -n "$(tail -c 1024 "$gitlab_config" | tr -d '\0')" ]] ||
      ! tar -tf "$gitlab_config" ./gitlab-secrets.json ./gitlab.rb ./ssh_host_ed25519_key >/dev/null ||
      ! tar -xOf "$gitlab_config" ./gitlab-secrets.json | jq -e .gitlab_rails >/dev/null; then
      echo "The /etc/gitlab copy is incomplete or lacks gitlab-secrets.json, gitlab.rb or its SSH host key." >&2
      exit 1
    fi
    gitlab_version="$(kubectl exec -n infra "$gitlab_pod_name" -c gitlab -- cat /opt/gitlab/embedded/service/gitlab-rails/VERSION)"
    gitlab_image="$(jq -r '.spec.containers[] | select(.name == "gitlab") | .image' <<< "$gitlab_pod")"
    gitlab_image_id="$(jq -r '.status.containerStatuses[]? | select(.name == "gitlab") | .imageID' <<< "$gitlab_pod")"
    # The version goes into RESTORE.txt, so accept only a single version token.
    [[ "$gitlab_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9a-z]+)*$ && "$gitlab_image_id" == *sha256:* ]] || {
      echo "Could not record the running GitLab version and image digest." >&2
      exit 1
    }
    printf 'GitLab version: %s\nImage: %s\nRunning image: %s\n' \
      "$gitlab_version" "$gitlab_image" "$gitlab_image_id" > "$staging_dir/gitlab/version.txt"
  fi
fi

cat > "$staging_dir/RESTORE.txt" <<EOF
Created: $TIMESTAMP
K3s version: $(k3s --version | head -1)
Datastore: $DATASTORE

This archive contains credentials and encryption keys. Keep it root-only and
is uploaded through an encrypted restic repository when off-node storage is
configured. It also contains a Vault Raft snapshot, logical PostgreSQL and
MongoDB dumps and the Odoo filestore (application-data/odoo-filestore.tar.gz;
restore it into /var/lib/odoo after the database) when those services were present. Longhorn recurring backups hold
the remaining PVC data. Restore with the same K3s minor version. Restore the
server token, encryption files, and K3s configuration (including config.yaml.d)
before starting the restored server. Review node-specific private addresses,
names, and join settings before reusing configuration on a replacement host.
EOF
if (( ${#kit_omitted[@]} > 0 )); then
  cat >> "$staging_dir/RESTORE.txt" <<EOF

Not in this archive: ${kit_omitted[*]}. They are in the offline recovery kit
exported on $(jq -r '.exported' "$RECOVERY_KIT_MANIFEST") (scripts/export-recovery-kit.sh);
place them in /var/lib/swirl-cloud (root, mode 600) before unsealing Vault.
EOF
fi
if [[ "$DATASTORE" == "etcd" ]]; then
  cat >> "$staging_dir/RESTORE.txt" <<EOF

Embedded etcd: stop K3s on every server. On the first server, restore using:
  k3s server --cluster-reset --etcd-s3=false \\
    --cluster-reset-restore-path=/absolute/extracted/path/k3s-db/$snapshot_name \\
    --token-file=/absolute/extracted/path/k3s-server/token
Restart the first server without reset flags. Preserve each other server's old
server/db directory outside the K3s data directory, then restart those servers
to rejoin the restored cluster. Do not copy a snapshot over a live etcd database.
Detailed procedure: https://docs.k3s.io/cli/etcd-snapshot#restoring-snapshots
EOF
else
  cat >> "$staging_dir/RESTORE.txt" <<'EOF'

SQLite: stop K3s, preserve the existing server/db directory, and restore
k3s-db/state.db into a clean server/db directory before restarting K3s.
If rolling back a SQLite-to-etcd migration, also remove the managed cluster-init
drop-in from the restored configuration; never leave old etcd data beside it.
EOF
fi
printf '\nAfter startup, verify: k3s secrets-encrypt status\n' >> "$staging_dir/RESTORE.txt"
if [[ -f "$staging_dir/gitlab/etc-gitlab.tar" ]]; then
  cat >> "$staging_dir/RESTORE.txt" <<EOF

GitLab $gitlab_version: gitlab/etc-gitlab.tar holds its /etc/gitlab (gitlab-secrets.json,
SSH host keys, gitlab.rb) and gitlab/version.txt the exact image. Before GitLab
starts against restored data, extract the tar as root into its gitlab-config
volume (mounted at /etc/gitlab) and run that same image: GitLab data restores
need the same version, and without these secrets CI variables, two-factor
secrets and runner tokens cannot be decrypted. Repositories and the GitLab
database are not in this archive.
EOF
fi

tar -C "$staging_dir" -czf "$ARCHIVE" .
chmod 0600 "$ARCHIVE"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
chmod 0600 "$ARCHIVE.sha256"

mapfile -t archives < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'k3s-*.tar.gz' -printf '%f\n' | sort -r)
if (( ${#archives[@]} > RETENTION_COUNT )); then
  for old_archive in "${archives[@]:RETENTION_COUNT}"; do
    rm -f -- "$BACKUP_DIR/$old_archive" "$BACKUP_DIR/$old_archive.sha256"
  done
fi

if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  command -v restic >/dev/null || { echo "restic is required for off-node backup storage." >&2; exit 1; }
  if ! restic snapshots --no-lock >/dev/null 2>&1; then
    restic init
  fi
  restic backup --tag swirl-cloud-k3s "$ARCHIVE" "$ARCHIVE.sha256"
  # Each archive has its own path, so the default host+paths grouping would keep
  # every snapshot forever.
  restic forget --tag swirl-cloud-k3s --group-by host,tags --keep-daily 7 --keep-weekly 5 --keep-monthly 12 --prune
fi

echo "Created $ARCHIVE"
