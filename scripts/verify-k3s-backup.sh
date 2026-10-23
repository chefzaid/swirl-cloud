#!/bin/bash
# Weekly check that the off-node restic copy reads back: restic check on a
# rotating data subset, then stream the newest archive and verify its SHA-256
# and tar.gz. The plaintext archive never touches the disk.
set -euo pipefail
set +x

[[ "$EUID" -eq 0 ]] || { echo "Run this verification as root." >&2; exit 1; }
BACKUP_ENVIRONMENT_FILE="${BACKUP_ENVIRONMENT_FILE:-/etc/swirl-cloud/backup.env}"
if [[ -r "$BACKUP_ENVIRONMENT_FILE" ]]; then
  # Root-owned, with shell-escaped values; restic reads its repository and
  # credentials from the environment.
  set -a
  # shellcheck disable=SC1090
  source "$BACKUP_ENVIRONMENT_FILE"
  set +a
fi

# Each weekly run reads one of VERIFY_DATA_SUBSETS parts of the pack data.
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

# Shared with the backup. Wait instead of skipping, which would be recorded as
# a success; root-only so no other user can hold it.
umask 077
exec 9>/run/lock/swirl-cloud-k3s-backup.lock
flock -w "$VERIFY_LOCK_WAIT_SECONDS" 9 || {
  echo "The backup lock is still held after $VERIFY_LOCK_WAIT_SECONDS seconds; nothing was verified." >&2
  exit 1
}

# Whole UTC weeks (Monday-Sunday) since the epoch: unlike ISO weeks this never
# repeats a subset after week 53, and a rerun reads the same subset.
week="$(( ($(date -u +%s) / 86400 + 3) / 7 ))"
subset="$(( week % VERIFY_DATA_SUBSETS + 1 ))/$VERIFY_DATA_SUBSETS"
restic check --read-data-subset "$subset" || {
  echo "restic check of the off-node repository failed (data subset $subset)." >&2
  exit 1
}

# Names carry the UTC creation time, so the greatest name is the newest archive.
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

# One stream: tee feeds the digest while tar lists the archive, which also
# checks the gzip CRC and that RESTORE.txt is present.
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
