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
# Newest archives always kept; on a filesystem of RETENTION_TIERS_MIN_GIB or more,
# also the newest archive of each of the last RETENTION_DAYS days, RETENTION_WEEKS
# ISO weeks and RETENTION_MONTHS months that have one (restic's --keep-* rules).
RETENTION_COUNT="${RETENTION_COUNT:-7}"
RETENTION_TIERS_MIN_GIB="${RETENTION_TIERS_MIN_GIB:-400}"
BACKUP_LOCK_WAIT_SECONDS="${BACKUP_LOCK_WAIT_SECONDS:-14400}"
# Free share the backup must leave: the kubelet's image GC starts at 85 % used.
BACKUP_MIN_FREE_PERCENT="${BACKUP_MIN_FREE_PERCENT:-15}"
K3S_DATA_DIR="${K3S_DATA_DIR:-/var/lib/rancher/k3s}"
K3S_CONFIG_DIR="${K3S_CONFIG_DIR:-/etc/rancher/k3s}"
K3S_DB="${K3S_DB:-$K3S_DATA_DIR/server/db/state.db}"
K3S_ETCD_DIR="$K3S_DATA_DIR/server/db/etcd"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$BACKUP_DIR/k3s-$TIMESTAMP.tar.gz"

[[ "$EUID" -eq 0 ]] || { echo "Run this backup as root." >&2; exit 1; }
[[ "$RETENTION_COUNT" =~ ^[1-9][0-9]*$ ]] || { echo "RETENTION_COUNT must be a positive integer." >&2; exit 1; }
for retention_setting in RETENTION_TIERS_MIN_GIB RETENTION_DAYS RETENTION_WEEKS RETENTION_MONTHS; do
  [[ "${!retention_setting:-0}" =~ ^[0-9]+$ ]] || { echo "$retention_setting must be a whole number." >&2; exit 1; }
done
[[ "$BACKUP_LOCK_WAIT_SECONDS" =~ ^[0-9]+$ ]] || { echo "BACKUP_LOCK_WAIT_SECONDS must be a whole number of seconds." >&2; exit 1; }
[[ "$BACKUP_MIN_FREE_PERCENT" =~ ^[0-9]$|^[1-9][0-9]$ ]] || { echo "BACKUP_MIN_FREE_PERCENT must be 0 to 99." >&2; exit 1; }
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

# A backup that fills the disk evicts pods (2026-09-28). Expect the previous
# run's staging plus archive size, and stop before anything is written.
estimate_file="$BACKUP_DIR/.last-run-bytes"
expected_bytes=0
[[ -r "$estimate_file" ]] && expected_bytes="$(< "$estimate_file")"
[[ "$expected_bytes" =~ ^[0-9]+$ ]] || expected_bytes=0
read -r filesystem_bytes available_bytes < <(df -B1 --output=size,avail "$BACKUP_DIR" | tail -n 1)
if (( (available_bytes - expected_bytes) * 100 < filesystem_bytes * BACKUP_MIN_FREE_PERCENT )); then
  echo "$((available_bytes / 1073741824)) GiB free under $BACKUP_DIR; this backup needs about" \
    "$((expected_bytes / 1073741824)) GiB and must leave $BACKUP_MIN_FREE_PERCENT % of the filesystem free." \
    "Free space first (alert runbook NodeMaintenanceHeadroomLow); no archive was created." >&2
  exit 1
fi

# Whether the filesystem keeps the free share after another $1 bytes.
space_allows() {
  local size available
  read -r size available < <(df -B1 --output=size,avail "$BACKUP_DIR" | tail -n 1)
  (( (available - $1) * 100 >= size * BACKUP_MIN_FREE_PERCENT ))
}
if (( filesystem_bytes >= RETENTION_TIERS_MIN_GIB * 1073741824 )); then
  RETENTION_DAYS="${RETENTION_DAYS:-14}" RETENTION_WEEKS="${RETENTION_WEEKS:-8}" RETENTION_MONTHS="${RETENTION_MONTHS:-12}"
else
  RETENTION_DAYS="${RETENTION_DAYS:-0}" RETENTION_WEEKS="${RETENTION_WEEKS:-0}" RETENTION_MONTHS="${RETENTION_MONTHS:-0}"
fi

# Parts that fail later still leave an archive of everything else; the run then
# exits nonzero so the backup alerts fire.
failures=()
application_backups=()
backup_failed() {
  echo "$1" >&2
  failures+=("$1")
}

staging_dir="$(mktemp -d "$BACKUP_DIR/.staging.XXXXXX")"
trap 'rm -rf -- "$staging_dir"' EXIT
install -d -m 0700 "$staging_dir/k3s-db" "$staging_dir/k3s-server" \
  "$staging_dir/k3s-config" "$staging_dir/vault" "$staging_dir/application-data" \
  "$staging_dir/kubernetes" "$staging_dir/gitlab" "$staging_dir/elasticsearch"

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

  # Long-retention evidence (identity audit, platform reports, Lynis, security
  # findings, access grants) as an Elasticsearch snapshot (names are lowercase).
  # The repository keeps only the newest snapshot; container logs and the API
  # audit stream stay out.
  if kubectl get deployment elasticsearch -n infra >/dev/null 2>&1; then
    elastic_password="$(kubectl get secret elasticsearch-security-bootstrap -n infra -o jsonpath='{.data.ELASTIC_PASSWORD}' | base64 -d)"
    evidence="$staging_dir/elasticsearch/evidence-snapshot.tar"
    evidence_script="$(cat <<'SCRIPT'
IFS= read -r password
escaped="$(printf '%s' "$password" | sed 's/[\\"]/\\&/g')"
api() { printf 'user = "elastic:%s"\n' "$escaped" | curl --fail --silent --show-error -K - -H 'Content-Type: application/json' "$@"; }
base=http://127.0.0.1:9200/_snapshot/swirl-cloud-evidence
api -X PUT "$base" -d '{"type":"fs","settings":{"location":"swirl-cloud-evidence","compress":true}}' >/dev/null
for old in $(api "$base/_all" | grep -o '"snapshot":"[^"]*"' | cut -d'"' -f4); do
  api -X DELETE "$base/$old" >/dev/null
done
api -X PUT "$base/nightly-$1?wait_for_completion=true" -d '{"indices":"identity-audit-*,platform-reports*,lynis-audits-*,security-findings-*,access-grants,security-detection-state,swirl-cloud-kibana-identity-ownership-*","ignore_unavailable":true,"include_global_state":false}' |
  grep -q '"state":"SUCCESS"'
cd /usr/share/elasticsearch/snapshots
exec tar -cf - swirl-cloud-evidence
SCRIPT
)"
    if { printf '%s\n' "$elastic_password"; } |
        kubectl exec -i -n infra deployment/elasticsearch -c elasticsearch -- sh -ceu "$evidence_script" sh "${TIMESTAMP,,}" > "$evidence" &&
      [[ -z "$(tail -c 1024 "$evidence" | tr -d '\0')" ]] &&
      tar -tf "$evidence" swirl-cloud-evidence/index.latest >/dev/null 2>&1; then
      elasticsearch_evidence=true
    else
      rm -f -- "$evidence"
      backup_failed "The Elasticsearch evidence snapshot failed; this archive holds no audit or report evidence."
    fi
    unset elastic_password
  fi

  # Applications declare the files they keep on their Deployment (docs/operations.md):
  #   swirl-cloud.io/backup-sqlite  SQLite databases, copied consistently (VACUUM INTO)
  #   swirl-cloud.io/backup-files   directories, copied with tar
  # Both take comma-separated absolute paths. Copies run in the application's own
  # container (sqlite3, else Node's node:sqlite), next to the database.
  sqlite_copy_script="$(cat <<'SCRIPT'
copy="$(dirname "$1")/.swirl-cloud-backup-$(basename "$1")"
rm -f -- "$copy"
trap 'rm -f -- "$copy"' EXIT
vacuum="VACUUM INTO '$copy'"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$1" ".timeout 60000" "$vacuum" >/dev/null
else
  node --no-warnings -e 'const { DatabaseSync } = require("node:sqlite");
const database = new DatabaseSync(process.argv[1]);
database.exec("PRAGMA busy_timeout = 60000");
database.exec(process.argv[2]);
database.close();' "$1" "$vacuum"
fi
cat -- "$copy"
SCRIPT
)"
  files_copy_script='cd "$(dirname "$1")" && exec tar -cf - "$(basename "$1")"'
  if declared="$(kubectl get deployments -A -o json | jq -r '.items[] |
      (.metadata.annotations // {}) as $a |
      select($a["swirl-cloud.io/backup-sqlite"] or $a["swirl-cloud.io/backup-files"]) |
      [.metadata.namespace, .metadata.name, ($a["swirl-cloud.io/backup-sqlite"] // ""),
       ($a["swirl-cloud.io/backup-files"] // "")] | @tsv')"; then
    while IFS=$'\t' read -r app_namespace app_name app_databases app_directories; do
      [[ -n "$app_namespace" ]] || continue
      app_dir="$staging_dir/application-data/apps/$app_namespace/$app_name"
      install -d -m 0700 "$app_dir"
      for kind in sqlite files; do
        if [[ "$kind" == sqlite ]]; then app_paths="$app_databases"; else app_paths="$app_directories"; fi
        IFS=, read -r -a declared_paths <<< "${app_paths// /}"
        for app_path in "${declared_paths[@]}"; do
          [[ -n "$app_path" ]] || continue
          if [[ ! "$app_path" =~ ^/[A-Za-z0-9._/-]+$ || "$app_path" == *..* ]]; then
            backup_failed "$app_namespace/$app_name declares an unusable backup path: $app_path"
            continue
          fi
          target="$app_dir/$(tr / _ <<< "${app_path#/}")"
          if [[ "$kind" == sqlite ]]; then
            if kubectl exec -n "$app_namespace" "deployment/$app_name" -- sh -ceu "$sqlite_copy_script" sh "$app_path" \
                < /dev/null > "$target" &&
              [[ -s "$target" ]] &&
              { ! command -v sqlite3 >/dev/null 2>&1 || [[ "$(sqlite3 "$target" 'PRAGMA integrity_check;')" == ok ]]; }; then
              application_backups+=("$app_namespace/$app_name:$app_path")
            else
              rm -f -- "$target"
              backup_failed "Could not copy $app_namespace/$app_name's SQLite database $app_path."
            fi
          else
            target="$target.tar"
            if kubectl exec -n "$app_namespace" "deployment/$app_name" -- sh -ceu "$files_copy_script" sh "$app_path" \
                < /dev/null > "$target" &&
              [[ -s "$target" && -z "$(tail -c 1024 "$target" | tr -d '\0')" ]]; then
              application_backups+=("$app_namespace/$app_name:$app_path")
            else
              rm -f -- "$target"
              backup_failed "Could not copy $app_namespace/$app_name's files under $app_path."
            fi
          fi
        done
      done
    done <<< "$declared"
  else
    backup_failed "Could not list Deployments for application-declared backups."
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

    # GitLab's own database, repositories, uploads, packages and LFS objects
    # (TODO §12). The Registry, CI artifacts and job logs stay out: images are
    # rebuilt from release tags and artifacts expire within days. The backup is
    # written in the GitLab volume, then here, then into the archive: three
    # times its size, which must leave the free share.
    gitlab_estimate_file="$BACKUP_DIR/.last-gitlab-bytes"
    gitlab_expected=0
    [[ -r "$gitlab_estimate_file" ]] && gitlab_expected="$(< "$gitlab_estimate_file")"
    [[ "$gitlab_expected" =~ ^[0-9]+$ ]] || gitlab_expected=0
    gitlab_backup_id="swirl-cloud-$TIMESTAMP"
    gitlab_backup_file="/var/opt/gitlab/backups/${gitlab_backup_id}_gitlab_backup.tar"
    gitlab_backup="$staging_dir/gitlab/gitlab_backup.tar"
    if ! space_allows "$((gitlab_expected * 3 + expected_bytes))"; then
      backup_failed "Too little space for GitLab's native backup (about $((gitlab_expected * 3 / 1073741824)) GiB); this archive holds only /etc/gitlab."
    elif kubectl exec -n infra "$gitlab_pod_name" -c gitlab -- \
        gitlab-backup create BACKUP="$gitlab_backup_id" SKIP=registry,artifacts,builds CRON=1 >/dev/null &&
      kubectl exec -n infra "$gitlab_pod_name" -c gitlab -- cat "$gitlab_backup_file" > "$gitlab_backup" &&
      [[ -z "$(tail -c 1024 "$gitlab_backup" | tr -d '\0')" ]] &&
      tar -tf "$gitlab_backup" backup_information.yml db/database.sql.gz >/dev/null 2>&1; then
      gitlab_native_backup=true
      stat -c %s "$gitlab_backup" > "$gitlab_estimate_file"
    else
      rm -f -- "$gitlab_backup"
      backup_failed "GitLab's native backup failed; this archive holds only /etc/gitlab."
    fi
    kubectl exec -n infra "$gitlab_pod_name" -c gitlab -- rm -f -- "$gitlab_backup_file" >/dev/null 2>&1 || true
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
restore it into /var/lib/odoo after the database) when those services were present; the
sections below list what else it holds. Longhorn volume backups, once an off-node
target is configured, hold the remaining PVC data. Restore with the same K3s minor version. Restore the
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
secrets and runner tokens cannot be decrypted.
EOF
  if [[ "${gitlab_native_backup:-false}" == true ]]; then
    cat >> "$staging_dir/RESTORE.txt" <<EOF
gitlab/gitlab_backup.tar is GitLab's native backup $gitlab_backup_id: its database,
repositories, uploads, packages and LFS objects, without the Registry, CI
artifacts and job logs. With /etc/gitlab restored and that image running, copy
it to /var/opt/gitlab/backups/${gitlab_backup_id}_gitlab_backup.tar (owner git),
run gitlab-ctl stop puma and gitlab-ctl stop sidekiq, then
gitlab-backup restore BACKUP=$gitlab_backup_id and gitlab-ctl restart.
EOF
  else
    printf 'Repositories and the GitLab database are not in this archive.\n' >> "$staging_dir/RESTORE.txt"
  fi
fi
if [[ "${elasticsearch_evidence:-false}" == true ]]; then
  cat >> "$staging_dir/RESTORE.txt" <<EOF

elasticsearch/evidence-snapshot.tar is an Elasticsearch snapshot repository
holding snapshot nightly-${TIMESTAMP,,} of the identity audit, platform report,
Lynis, security finding and access grant indices. Extract it into
/usr/share/elasticsearch/snapshots, register an fs repository with location
swirl-cloud-evidence, and restore with POST _snapshot/<repository>/nightly-${TIMESTAMP,,}/_restore.
EOF
fi
if (( ${#application_backups[@]} > 0 )); then
  cat >> "$staging_dir/RESTORE.txt" <<EOF

application-data/apps/<namespace>/<deployment>/ holds what applications declare
with swirl-cloud.io/backup-sqlite and swirl-cloud.io/backup-files: ${application_backups[*]}.
Each file is named after its path with / as _. Scale the Deployment to zero,
put each .sqlite back at its path with no -wal or -shm file beside it, extract
each .tar in the parent of its directory, then scale the Deployment back up.
EOF
fi
if (( ${#failures[@]} > 0 )); then
  {
    printf '\nThis run did not complete; missing from this archive:\n'
    printf -- '- %s\n' "${failures[@]}"
  } >> "$staging_dir/RESTORE.txt"
fi

tar -C "$staging_dir" -czf "$ARCHIVE" .
chmod 0600 "$ARCHIVE"
# GitLab's native backup has its own estimate and space check.
run_bytes="$(( $(du -sb "$staging_dir" | cut -f1) + $(stat -c %s "$ARCHIVE") ))"
[[ "${gitlab_native_backup:-false}" != true ]] || run_bytes="$(( run_bytes - 2 * $(< "$gitlab_estimate_file") ))"
printf '%s\n' "$(( run_bytes > 0 ? run_bytes : 0 ))" > "$estimate_file"
sha256sum "$ARCHIVE" > "$ARCHIVE.sha256"
chmod 0600 "$ARCHIVE.sha256"

# Installer runs add archives too, so a count alone can keep only one busy day.
mapfile -t archives < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'k3s-*.tar.gz' -printf '%f\n' | sort -r)
declare -A keep=() seen_day=() seen_week=() seen_month=()
days=0 weeks=0 months=0
for index in "${!archives[@]}"; do
  archive_name="${archives[$index]}"
  day="${archive_name:4:8}"
  if [[ ! "$day" =~ ^[0-9]{8}$ ]] || ! week="$(date -u -d "$day" +%G%V 2>/dev/null)"; then
    keep[$archive_name]=1
    continue
  fi
  month="${day:0:6}"
  (( index >= RETENTION_COUNT )) || keep[$archive_name]=1
  if [[ -z "${seen_day[$day]:-}" ]]; then
    seen_day[$day]=1 days=$((days + 1))
    (( days > RETENTION_DAYS )) || keep[$archive_name]=1
  fi
  if [[ -z "${seen_week[$week]:-}" ]]; then
    seen_week[$week]=1 weeks=$((weeks + 1))
    (( weeks > RETENTION_WEEKS )) || keep[$archive_name]=1
  fi
  if [[ -z "${seen_month[$month]:-}" ]]; then
    seen_month[$month]=1 months=$((months + 1))
    (( months > RETENTION_MONTHS )) || keep[$archive_name]=1
  fi
done
for archive_name in "${archives[@]}"; do
  [[ -n "${keep[$archive_name]:-}" ]] || rm -f -- "$BACKUP_DIR/$archive_name" "$BACKUP_DIR/$archive_name.sha256"
done

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
# Exit 3: an archive exists without the parts listed above.
if (( ${#failures[@]} > 0 )); then
  printf 'Incomplete: %s\n' "${failures[@]}" >&2
  exit 3
fi
