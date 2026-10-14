#!/bin/bash
# Weekly proof that the off-node restic copy of the recovery archives reads
# back: check the repository with a rotating subset of its pack data, then
# stream the newest archive out of restic and verify its recorded SHA-256 and
# that it is a complete tar.gz. The plaintext archive never touches the disk.
set -euo pipefail
set +x

[[ "$EUID" -eq 0 ]] || { echo "Run this verification as root." >&2; exit 1; }
BACKUP_ENVIRONMENT_FILE="${BACKUP_ENVIRONMENT_FILE:-/etc/swirl-cloud/backup.env}"
if [[ -r "$BACKUP_ENVIRONMENT_FILE" ]]; then
  # This root-owned file is written with shell-safe escaped values; restic
  # reads the repository, password and S3 credentials from the environment.
  set -a
  # shellcheck disable=SC1090
  source "$BACKUP_ENVIRONMENT_FILE"
  set +a
fi

# Each run reads one of VERIFY_DATA_SUBSETS parts of the pack data, so the
# whole repository is downloaded once every VERIFY_DATA_SUBSETS weeks.
VERIFY_DATA_SUBSETS="${VERIFY_DATA_SUBSETS:-4}"
VERIFY_LOCK_WAIT_SECONDS="${VERIFY_LOCK_WAIT_SECONDS:-3600}"
[[ "$VERIFY_DATA_SUBSETS" =~ ^[1-9][0-9]*$ ]] || { echo "VERIFY_DATA_SUBSETS must be a positive integer." >&2; exit 1; }
[[ "$VERIFY_LOCK_WAIT_SECONDS" =~ ^[0-9]+$ ]] || { echo "VERIFY_LOCK_WAIT_SECONDS must be a whole number of seconds." >&2; exit 1; }

if [[ -z "${RESTIC_REPOSITORY:-}" ]]; then
  echo "No off-node restic repository is configured; nothing to verify."
  exit 0
fi
command -v restic >/dev/null || { echo "restic is required to verify off-node backups." >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required." >&2; exit 1; }

# The backup, upload and prune hold this lock. Wait for a running backup
# instead of skipping: a skipped run would be recorded as a success. Create it
# root-only, as the backup does, so no other user can open and hold it.
umask 077
exec 9>/run/lock/swirl-cloud-k3s-backup.lock
flock -w "$VERIFY_LOCK_WAIT_SECONDS" 9 || {
  echo "The backup lock is still held after $VERIFY_LOCK_WAIT_SECONDS seconds; nothing was verified." >&2
  exit 1
}

# Count whole UTC weeks (Monday to Sunday) since the epoch: unlike ISO week
# numbers, which repeat a subset after week 53, this keeps a strict cycle, and
# a rerun in the same week reads the same subset.
week="$(( ($(date -u +%s) / 86400 + 3) / 7 ))"
subset="$(( week % VERIFY_DATA_SUBSETS + 1 ))/$VERIFY_DATA_SUBSETS"
restic check --read-data-subset "$subset" || {
  echo "restic check of the off-node repository failed (data subset $subset)." >&2
  exit 1
}

# Archive names carry their UTC creation time, so the greatest name is the
# newest archive whatever host or clock recorded the snapshot.
newest="$(restic snapshots --tag swirl-cloud-k3s --json | jq -c '
  [.[] | .id as $id | .paths[]
   | select(test("^/.+/k3s-[0-9]{8}T[0-9]{6}Z\\.tar\\.gz$"))
   | {id: $id, path: ., name: (split("/") | last)}]
  | max_by(.name)')"
[[ "$newest" != null ]] || { echo "The off-node repository holds no K3s recovery archive." >&2; exit 1; }
snapshot_id="$(jq -r .id <<< "$newest")"
archive="$(jq -r .path <<< "$newest")"
source_label="$archive from off-node snapshot ${snapshot_id:0:8}"

recorded_digest="$(restic dump "$snapshot_id" "$archive.sha256" | awk 'NR == 1 { print $1 }')" || recorded_digest=""
[[ "$recorded_digest" =~ ^[0-9a-f]{64}$ ]] || { echo "$source_label has no readable SHA-256 record." >&2; exit 1; }

# Stream once: tee feeds the digest while tar decompresses and lists the whole
# archive, which also checks the gzip CRC and that RESTORE.txt is present.
digest_file="$(mktemp)"
trap 'rm -f -- "$digest_file"' EXIT
exec 3> >(sha256sum | awk '{ print $1 }' > "$digest_file")
digest_pid=$!
readable=true
restic dump "$snapshot_id" "$archive" | tee /dev/fd/3 | tar -tzf - \
  | awk '$0 == "./RESTORE.txt" { found = 1 } END { exit !found }' || readable=false
exec 3>&-
wait "$digest_pid"

if [[ "$readable" != true ]]; then
  echo "$source_label could not be read back as a complete tar.gz recovery archive." >&2
  exit 1
fi
actual_digest="$(< "$digest_file")"
if [[ "$actual_digest" != "$recorded_digest" ]]; then
  echo "$source_label does not match its recorded SHA-256 (read $actual_digest, recorded $recorded_digest)." >&2
  exit 1
fi

echo "Verified $source_label: SHA-256 matches and the archive reads back completely; restic check passed for data subset $subset."
