#!/bin/bash
# Logical dump of the platform PostgreSQL between the nightly recovery archives,
# so at most six hours of database changes are lost (owner's decision 2026-11-25).
#
# Installed by configure-k3s-backups.sh as /usr/local/sbin/swirl-cloud-postgres-dump
# and run by swirl-cloud-postgres-dump.timer at 08:15, 14:15 and 20:15 UTC; the
# nightly archive (swirl-cloud-k3s-backup) holds the fourth dump of the day.
# Writes $BACKUP_DIR/postgres/postgresql-<UTC time>.sql.gz (root-only) with its
# SHA-256, keeps the newest POSTGRES_DUMP_KEEP locally, and with an off-node
# restic repository uploads it (tag swirl-cloud-postgres, the same number kept).
# Restore: see docs/operations.md#coverage-and-recovery-points.
set -euo pipefail
set +x

POSTGRES_ACCESS_LIBRARY="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/postgres-access.sh"
[[ -r "$POSTGRES_ACCESS_LIBRARY" ]] || POSTGRES_ACCESS_LIBRARY=/usr/local/lib/swirl-cloud/postgres-access.sh
# shellcheck source=lib/postgres-access.sh
source "$POSTGRES_ACCESS_LIBRARY"

BACKUP_ENVIRONMENT_FILE="${BACKUP_ENVIRONMENT_FILE:-/etc/swirl-cloud/backup.env}"
if [[ -r "$BACKUP_ENVIRONMENT_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$BACKUP_ENVIRONMENT_FILE"
  set +a
fi
OFFSITE_MOUNT="${OFFSITE_MOUNT:-${RUNTIME_DIRECTORY:-/run/swirl-cloud-postgres-dump}/offsite}"
[[ -z "${BACKUP_NFS_EXPORT:-}" ]] || export RESTIC_REPOSITORY="$OFFSITE_MOUNT/${BACKUP_NFS_PATH:-swirl-cloud}/restic"
# Large exec streams with stdin need SPDY (see backup-k3s.sh).
export KUBECTL_REMOTE_COMMAND_WEBSOCKETS=false
BACKUP_DIR="${BACKUP_DIR:-/var/backups/swirl-cloud/k3s}"
DUMP_DIR="$BACKUP_DIR/postgres"
POSTGRES_DUMP_KEEP="${POSTGRES_DUMP_KEEP:-8}"
BACKUP_LOCK_WAIT_SECONDS="${BACKUP_LOCK_WAIT_SECONDS:-14400}"
BACKUP_MIN_FREE_PERCENT="${BACKUP_MIN_FREE_PERCENT:-15}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP="$DUMP_DIR/postgresql-$TIMESTAMP.sql.gz"

[[ "$EUID" -eq 0 ]] || { echo "Run this dump as root." >&2; exit 1; }
[[ "$POSTGRES_DUMP_KEEP" =~ ^[1-9][0-9]*$ ]] || { echo "POSTGRES_DUMP_KEEP must be a positive integer." >&2; exit 1; }
[[ "$BACKUP_LOCK_WAIT_SECONDS" =~ ^[0-9]+$ ]] || { echo "BACKUP_LOCK_WAIT_SECONDS must be a whole number of seconds." >&2; exit 1; }
[[ "$BACKUP_MIN_FREE_PERCENT" =~ ^[0-9]$|^[1-9][0-9]$ ]] || { echo "BACKUP_MIN_FREE_PERCENT must be 0 to 99." >&2; exit 1; }

umask 077
install -d -o root -g root -m 0700 "$BACKUP_DIR" "$DUMP_DIR"
# One backup job at a time: the nightly archive and its verification hold this lock.
exec 9>/run/lock/swirl-cloud-k3s-backup.lock
flock -w "$BACKUP_LOCK_WAIT_SECONDS" 9 || {
  echo "The backup lock is still held after $BACKUP_LOCK_WAIT_SECONDS seconds; no dump was taken." >&2
  exit 1
}

if ! kubectl get service postgres -n infra >/dev/null 2>&1; then
  echo "The platform PostgreSQL (infra/postgres) is not installed; nothing to dump."
  exit 0
fi

# The previous dump's size, which the filesystem must still hold above its free share.
previous="$(find "$DUMP_DIR" -maxdepth 1 -type f -name 'postgresql-*.sql.gz' -printf '%s\n' | sort -n | tail -n 1)"
read -r filesystem_bytes available_bytes < <(df -B1 --output=size,avail "$DUMP_DIR" | tail -n 1)
if (( (available_bytes - ${previous:-0}) * 100 < filesystem_bytes * BACKUP_MIN_FREE_PERCENT )); then
  echo "Too little free space under $DUMP_DIR to keep $BACKUP_MIN_FREE_PERCENT % free after a dump; no dump was taken." >&2
  exit 1
fi

partial="$DUMP.partial"
offsite_mounted=false
cleanup() {
  rm -f -- "$partial"
  [[ "$offsite_mounted" == false ]] || umount "$OFFSITE_MOUNT" || true
}
trap cleanup EXIT

postgres_target="$(postgres_runtime_target infra)"
postgres_username="$(kubectl get secret postgres-secret -n infra -o jsonpath='{.data.POSTGRES_USER}' | base64 -d)"
postgres_password="$(kubectl get secret postgres-secret -n infra -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
# The credentials go on standard input, never into arguments or the environment.
{ printf '%s\n%s\n' "$postgres_username" "$postgres_password"; } | kubectl exec -i -n infra "$postgres_target" -c postgres -- sh -ceu '
  IFS= read -r postgres_username
  IFS= read -r PGPASSWORD
  export PGPASSWORD
  exec pg_dumpall --host=127.0.0.1 --username="$postgres_username" --clean --if-exists
' | gzip -9 > "$partial"
unset postgres_username postgres_password
# A dump that stopped early still decompresses; pg_dumpall ends with this line.
gzip -dc "$partial" | tail -n 5 | grep -q '^-- PostgreSQL database cluster dump complete' || {
  echo "pg_dumpall did not finish; the partial dump was discarded." >&2
  exit 1
}
mv -f -- "$partial" "$DUMP"
(cd "$DUMP_DIR" && sha256sum "$(basename "$DUMP")" > "$(basename "$DUMP").sha256")

# Keep the newest dumps; the nightly archives keep the long history.
mapfile -t dumps < <(find "$DUMP_DIR" -maxdepth 1 -type f -name 'postgresql-*.sql.gz' -printf '%f\n' | sort -r)
for old in "${dumps[@]:$POSTGRES_DUMP_KEEP}"; do
  rm -f -- "$DUMP_DIR/$old" "$DUMP_DIR/$old.sha256"
done

if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  command -v restic >/dev/null || { echo "restic is required for off-node backup storage." >&2; exit 1; }
  if [[ -n "${BACKUP_NFS_EXPORT:-}" ]]; then
    install -d -m 0700 "$OFFSITE_MOUNT"
    mount -t nfs -o vers=3,nolock,soft,timeo=600,retrans=5 "$BACKUP_NFS_EXPORT" "$OFFSITE_MOUNT"
    offsite_mounted=true
  fi
  restic snapshots --no-lock >/dev/null 2>&1 || restic init
  restic backup --tag swirl-cloud-postgres "$DUMP" "$DUMP.sha256"
  # The nightly run prunes; forgetting here only drops the references.
  restic forget --tag swirl-cloud-postgres --group-by host,tags --keep-last "$POSTGRES_DUMP_KEEP"
fi
echo "Dumped the platform PostgreSQL to $DUMP"
