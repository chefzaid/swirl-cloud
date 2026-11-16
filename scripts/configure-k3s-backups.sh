#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIGURE_OFFSITE_BACKUPS="${CONFIGURE_OFFSITE_BACKUPS:-false}"
# s3: object storage; restic stores archives and host files, Longhorn the volumes.
# nfs: an NFS version 3 export (OVHcloud Backup Storage, for one), mounted only
# while restic runs; Longhorn backs the volumes up to the same export.
BACKUP_STORAGE="${BACKUP_STORAGE:-s3}"
BACKUP_S3_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-}"
BACKUP_S3_REGION="${BACKUP_S3_REGION:-}"
BACKUP_S3_ACCESS_KEY="${BACKUP_S3_ACCESS_KEY:-}"
BACKUP_S3_SECRET_KEY="${BACKUP_S3_SECRET_KEY:-}"
BACKUP_NFS_EXPORT="${BACKUP_NFS_EXPORT:-}"
# Folder of the restic repository on the export; Longhorn writes backupstore/.
BACKUP_NFS_PATH="${BACKUP_NFS_PATH:-swirl-cloud}"
BACKUP_REPOSITORY_PASSWORD="${BACKUP_REPOSITORY_PASSWORD:-}"
# Lowering it below 16 is an accepted risk: whoever holds the repository can
# guess the password offline.
BACKUP_REPOSITORY_PASSWORD_MIN_LENGTH="${BACKUP_REPOSITORY_PASSWORD_MIN_LENGTH:-16}"
# false: once the off-node upload succeeds, no archive stays on this server.
BACKUP_KEEP_LOCAL="${BACKUP_KEEP_LOCAL:-true}"
# Daily start of the archive and host backup: HH:MM and a time zone.
BACKUP_TIME="${BACKUP_TIME:-02:15 UTC}"
# Longhorn runs its recurring jobs in UTC.
LONGHORN_BACKUP_CRON="${LONGHORN_BACKUP_CRON:-15 3 * * *}"
# Volumes (namespace/claim) left out of the daily volume backup: caches the
# cluster refills by itself.
LONGHORN_BACKUP_EXCLUDE="${LONGHORN_BACKUP_EXCLUDE:-gitlab-runners/gitlab-runner-cache infra/data-trivy-server-0}"
RUN_BACKUP_NOW="${RUN_BACKUP_NOW:-true}"
backup_environment=""
timer_file=""

cleanup() {
  [[ -z "$backup_environment" || ! -f "$backup_environment" ]] || rm -f -- "$backup_environment"
  [[ -z "$timer_file" || ! -f "$timer_file" ]] || rm -f -- "$timer_file"
  BACKUP_S3_ACCESS_KEY=""
  BACKUP_S3_SECRET_KEY=""
  BACKUP_REPOSITORY_PASSWORD=""
  unset BACKUP_S3_ACCESS_KEY BACKUP_S3_SECRET_KEY BACKUP_REPOSITORY_PASSWORD 2>/dev/null || true
}
trap cleanup EXIT

command -v sudo >/dev/null || { echo "sudo is required." >&2; exit 1; }
command -v k3s >/dev/null || { echo "K3s is not installed." >&2; exit 1; }
command -v kubectl >/dev/null || { echo "kubectl is required." >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required." >&2; exit 1; }
[[ "$CONFIGURE_OFFSITE_BACKUPS" =~ ^(true|false)$ ]] || { echo "CONFIGURE_OFFSITE_BACKUPS must be true or false." >&2; exit 1; }
[[ "$RUN_BACKUP_NOW" =~ ^(true|false)$ ]] || { echo "RUN_BACKUP_NOW must be true or false." >&2; exit 1; }
[[ "$BACKUP_KEEP_LOCAL" =~ ^(true|false)$ ]] || { echo "BACKUP_KEEP_LOCAL must be true or false." >&2; exit 1; }
[[ "$BACKUP_KEEP_LOCAL" == true || "$CONFIGURE_OFFSITE_BACKUPS" == true ]] || \
  { echo "BACKUP_KEEP_LOCAL=false needs off-node backups; otherwise no archive would be kept anywhere." >&2; exit 1; }
[[ "$BACKUP_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]\ [A-Za-z][A-Za-z0-9_+/-]*$ ]] || \
  { echo "BACKUP_TIME must be HH:MM and a time zone, for example '06:00 Europe/Paris'." >&2; exit 1; }
on_calendar="*-*-* ${BACKUP_TIME%% *}:00 ${BACKUP_TIME#* }"
if command -v systemd-analyze >/dev/null && ! systemd-analyze calendar "$on_calendar" >/dev/null 2>&1; then
  echo "BACKUP_TIME names an unknown time zone: $BACKUP_TIME" >&2
  exit 1
fi

if [[ "$CONFIGURE_OFFSITE_BACKUPS" == "true" ]]; then
  [[ "$BACKUP_STORAGE" =~ ^(s3|nfs)$ ]] || { echo "BACKUP_STORAGE must be s3 or nfs." >&2; exit 1; }
  if [[ "$BACKUP_STORAGE" == s3 ]]; then
    [[ "$BACKUP_S3_ENDPOINT" == https://* ]] || { echo "BACKUP_S3_ENDPOINT must use HTTPS." >&2; exit 1; }
    [[ -n "$BACKUP_S3_BUCKET" && -n "$BACKUP_S3_REGION" ]] || { echo "BACKUP_S3_BUCKET and BACKUP_S3_REGION are required." >&2; exit 1; }
    [[ -n "$BACKUP_S3_ACCESS_KEY" && -n "$BACKUP_S3_SECRET_KEY" ]] || { echo "S3 access credentials are required." >&2; exit 1; }
  else
    [[ "$BACKUP_NFS_EXPORT" =~ ^[A-Za-z0-9.-]+:/[A-Za-z0-9._/-]+$ ]] || \
      { echo "BACKUP_NFS_EXPORT must be host:/path." >&2; exit 1; }
    [[ "$BACKUP_NFS_PATH" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$ && "$BACKUP_NFS_PATH" != backupstore* ]] || \
      { echo "BACKUP_NFS_PATH must be a relative folder such as swirl-cloud, apart from Longhorn's backupstore." >&2; exit 1; }
  fi
  [[ "$LONGHORN_BACKUP_CRON" =~ ^[0-9*/,-]+(\ [0-9*/,-]+){4}$ ]] || { echo "LONGHORN_BACKUP_CRON must be a five-field cron schedule." >&2; exit 1; }
  if [[ ! "$BACKUP_REPOSITORY_PASSWORD_MIN_LENGTH" =~ ^[0-9]+$ ]] || (( BACKUP_REPOSITORY_PASSWORD_MIN_LENGTH < 12 )); then
    echo "BACKUP_REPOSITORY_PASSWORD_MIN_LENGTH must be 12 or more." >&2
    exit 1
  fi
  (( ${#BACKUP_REPOSITORY_PASSWORD} >= BACKUP_REPOSITORY_PASSWORD_MIN_LENGTH )) || \
    { echo "BACKUP_REPOSITORY_PASSWORD must contain at least $BACKUP_REPOSITORY_PASSWORD_MIN_LENGTH characters." >&2; exit 1; }
fi

if ! sudo test -d "${K3S_DATA_DIR:-/var/lib/rancher/k3s}/server/db/etcd/member" && ! command -v sqlite3 >/dev/null; then
  sudo apt-get update -qq
  sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sqlite3 >/dev/null
fi

# The backup unit mounts these read-only, even before Vault or off-node
# credentials exist.
sudo install -d -o root -g root -m 0700 /etc/swirl-cloud /var/lib/swirl-cloud

if [[ "$CONFIGURE_OFFSITE_BACKUPS" == "true" ]]; then
  packages=(restic)
  [[ "$BACKUP_STORAGE" != nfs ]] || packages+=(nfs-common)
  if ! command -v restic >/dev/null || { [[ "$BACKUP_STORAGE" == nfs ]] && ! command -v mount.nfs >/dev/null; }; then
    sudo apt-get update -qq
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${packages[@]}" >/dev/null
  fi
  backup_environment="$(mktemp)"
  chmod 600 "$backup_environment"
  {
    if [[ "$BACKUP_STORAGE" == s3 ]]; then
      printf 'RESTIC_REPOSITORY=s3:%s/%s/restic\n' "${BACKUP_S3_ENDPOINT%/}" "$BACKUP_S3_BUCKET"
      printf 'RESTIC_PASSWORD=%q\n' "$BACKUP_REPOSITORY_PASSWORD"
      printf 'AWS_ACCESS_KEY_ID=%q\n' "$BACKUP_S3_ACCESS_KEY"
      printf 'AWS_SECRET_ACCESS_KEY=%q\n' "$BACKUP_S3_SECRET_KEY"
      printf 'AWS_DEFAULT_REGION=%q\n' "$BACKUP_S3_REGION"
    else
      # Each run mounts the export in its own runtime directory and points
      # restic at the repository there.
      printf 'BACKUP_NFS_EXPORT=%q\n' "$BACKUP_NFS_EXPORT"
      printf 'BACKUP_NFS_PATH=%q\n' "$BACKUP_NFS_PATH"
      printf 'RESTIC_PASSWORD=%q\n' "$BACKUP_REPOSITORY_PASSWORD"
    fi
    printf 'BACKUP_KEEP_LOCAL=%q\n' "$BACKUP_KEEP_LOCAL"
  } > "$backup_environment"
  sudo install -o root -g root -m 0600 "$backup_environment" /etc/swirl-cloud/backup.env
fi

sudo install -d -o root -g root -m 0700 /var/backups/swirl-cloud/k3s
sudo install -o root -g root -m 0750 "$REPO_ROOT/scripts/backup-k3s.sh" /usr/local/sbin/swirl-cloud-k3s-backup
sudo install -d -o root -g root -m 0755 /usr/local/lib/swirl-cloud
sudo install -o root -g root -m 0644 "$REPO_ROOT/scripts/lib/postgres-access.sh" /usr/local/lib/swirl-cloud/postgres-access.sh
sudo install -o root -g root -m 0644 "$REPO_ROOT/scripts/lib/vault-access.sh" /usr/local/lib/swirl-cloud/vault-access.sh
sudo install -o root -g root -m 0644 "$REPO_ROOT/config/host/swirl-cloud-k3s-backup.service" /etc/systemd/system/swirl-cloud-k3s-backup.service
timer_file="$(mktemp)"
sed "s|^OnCalendar=.*|OnCalendar=$on_calendar|" "$REPO_ROOT/config/host/swirl-cloud-k3s-backup.timer" > "$timer_file"
sudo install -o root -g root -m 0644 "$timer_file" /etc/systemd/system/swirl-cloud-k3s-backup.timer
sudo install -o root -g root -m 0750 "$REPO_ROOT/scripts/verify-k3s-backup.sh" /usr/local/sbin/swirl-cloud-k3s-backup-verify
sudo install -o root -g root -m 0644 "$REPO_ROOT/config/host/swirl-cloud-k3s-backup-verify.service" /etc/systemd/system/swirl-cloud-k3s-backup-verify.service
sudo install -o root -g root -m 0644 "$REPO_ROOT/config/host/swirl-cloud-k3s-backup-verify.timer" /etc/systemd/system/swirl-cloud-k3s-backup-verify.timer
sudo systemctl daemon-reload
sudo systemctl enable --now swirl-cloud-k3s-backup.timer >/dev/null
# The verification reads the off-node copy, so it runs only while backup.env
# names a restic repository or the export that holds one.
if sudo grep -Eqs '^(RESTIC_REPOSITORY|BACKUP_NFS_EXPORT)=.' /etc/swirl-cloud/backup.env; then
  sudo systemctl enable --now swirl-cloud-k3s-backup-verify.timer >/dev/null
else
  sudo systemctl disable --now swirl-cloud-k3s-backup-verify.timer >/dev/null
fi
if [[ "$RUN_BACKUP_NOW" == "true" ]] && ! sudo systemctl start swirl-cloud-k3s-backup.service; then
  # Exit 3 left an archive without some parts; the unit stays failed for the alerts.
  [[ "$(systemctl show -p ExecMainStatus --value swirl-cloud-k3s-backup.service)" == 3 ]] || exit 1
  echo "WARNING: the recovery archive is incomplete; see journalctl -u swirl-cloud-k3s-backup.service." >&2
fi

if [[ "$CONFIGURE_OFFSITE_BACKUPS" == "true" ]] && \
   kubectl get namespace longhorn-system >/dev/null 2>&1 && \
   kubectl get backuptargets.longhorn.io default -n longhorn-system >/dev/null 2>&1; then
  if [[ "$BACKUP_STORAGE" == s3 ]]; then
    jq -n \
      --arg access "$BACKUP_S3_ACCESS_KEY" \
      --arg secret "$BACKUP_S3_SECRET_KEY" \
      --arg endpoint "$BACKUP_S3_ENDPOINT" \
      '{apiVersion:"v1",kind:"Secret",metadata:{name:"swirl-cloud-longhorn-backup",namespace:"longhorn-system"},type:"Opaque",stringData:{AWS_ACCESS_KEY_ID:$access,AWS_SECRET_ACCESS_KEY:$secret,AWS_ENDPOINTS:$endpoint,VIRTUAL_HOSTED_STYLE:"false"}}' \
      | kubectl apply -f - >/dev/null
    backup_target="s3://$BACKUP_S3_BUCKET@$BACKUP_S3_REGION/longhorn"
    credential_secret=swirl-cloud-longhorn-backup
  else
    # Version 3 without locking, as such exports offer; a soft mount fails a
    # backup instead of hanging Longhorn while the storage is unreachable.
    backup_target="nfs://$BACKUP_NFS_EXPORT?nfsOptions=vers=3,nolock,soft,timeo=600,retrans=5"
    credential_secret=""
  fi
  # The pinned Longhorn release configures backups only through the default
  # BackupTarget resource.
  kubectl patch backuptargets.longhorn.io default -n longhorn-system --type=merge \
    -p "$(jq -cn --arg url "$backup_target" --arg secret "$credential_secret" --arg now "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      '{spec:{backupTargetURL:$url,credentialSecret:$secret,syncRequestedAt:$now}}')" >/dev/null
  backup_target_available=false
  for _ in {1..30}; do
    if [[ "$(kubectl get backuptargets.longhorn.io default -n longhorn-system -o jsonpath='{.status.available}')" == true ]]; then
      backup_target_available=true
      break
    fi
    sleep 10
  done
  if [[ "$backup_target_available" != true ]]; then
    echo "Longhorn cannot reach backup target ${backup_target%%\?*}; check the storage, its access list and credentials." >&2
    exit 1
  fi
  # The default group holds every volume without another recurring-job label,
  # new ones included.
  jq -n --arg cron "$LONGHORN_BACKUP_CRON" '{apiVersion:"longhorn.io/v1beta2",kind:"RecurringJob",
      metadata:{name:"swirl-cloud-daily-backup",namespace:"longhorn-system"},
      spec:{cron:$cron,task:"backup",groups:["default"],retain:14,concurrency:1,labels:{"backup-policy":"daily-off-node"}}}' \
    | kubectl apply -f - >/dev/null
  for claim in $LONGHORN_BACKUP_EXCLUDE; do
    volume="$(kubectl get persistentvolumeclaim "${claim#*/}" -n "${claim%%/*}" -o jsonpath='{.spec.volumeName}' 2>/dev/null || true)"
    [[ -n "$volume" ]] || continue
    # Out of the default group, so no daily backup; the swirl-cloud group keeps
    # the weekly trim.
    kubectl label volumes.longhorn.io "$volume" -n longhorn-system --overwrite \
      recurring-job-group.longhorn.io/swirl-cloud=enabled recurring-job-group.longhorn.io/default- >/dev/null
  done
  echo "K3s recovery archives, host files and Longhorn volume backups are stored off-node ($BACKUP_STORAGE)."
elif [[ "$CONFIGURE_OFFSITE_BACKUPS" == "true" ]]; then
  echo "K3s off-node archive storage is configured. Rerun this script after Longhorn is installed to enable recurring volume backups."
else
  echo "K3s backups are enabled locally; no off-node destination is configured."
fi
