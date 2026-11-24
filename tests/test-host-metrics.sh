#!/usr/bin/env bash
# Guard the host metrics scripts the baseline installs, without a host: the
# pending-reboot metric keeps the first time a reboot was requested while later
# updates rewrite the flag and clears once the host rebooted, and a maintenance
# unit's metrics keep its last success across failed runs, for unit names that
# are safe to put in a file name and a label only.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/host-baseline.sh
source "$SCRIPT_DIR/lib/host-baseline.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
METRICS_DIR="$STATE/textfile"
mkdir -p "$METRICS_DIR" "$STATE/scripts"
# Capture what the baseline would install; move host paths under $STATE.
write_root_file() {
    [[ "$1" == /usr/local/sbin/* ]] || { cat >/dev/null; return 0; }
    sed "s#/var/lib/node_exporter/textfile_collector#$METRICS_DIR#g; s#/var/run/reboot-required#$STATE/reboot-required#g" \
        > "$STATE/scripts/${1##*/}"
}
sudo() { :; }
configure_reboot_required_metrics
configure_host_unit_metrics
REBOOT="$STATE/scripts/swirl-cloud-reboot-metrics"
UNIT="$STATE/scripts/swirl-cloud-unit-metrics"
[[ -s "$REBOOT" && -s "$UNIT" ]] || fail 'The metrics scripts are not installed'
metric() { awk -v name="$2" '$1 == name || index($1, name "{") == 1 {print $2}' "$METRICS_DIR/$1"; }
check() { command -v promtool >/dev/null 2>&1 || return 0; promtool check metrics < "$METRICS_DIR/$1" || fail "$1 is not valid exposition format"; }

# Pending reboot.
bash "$REBOOT"
[[ "$(metric swirl_cloud_reboot.prom swirl_cloud_reboot_required)" == 0 ]] || fail 'A reboot is reported without the flag'
[[ "$(metric swirl_cloud_reboot.prom swirl_cloud_reboot_required_since_timestamp_seconds)" == 0 ]] || fail 'A reboot time is reported without the flag'
touch -d '2026-11-20 10:00:00 UTC' "$STATE/reboot-required"
bash "$REBOOT"
first="$(date -u -d '2026-11-20 10:00:00 UTC' +%s)"
[[ "$(metric swirl_cloud_reboot.prom swirl_cloud_reboot_required)" == 1 ]] || fail 'The flag is not reported'
[[ "$(metric swirl_cloud_reboot.prom swirl_cloud_reboot_required_since_timestamp_seconds)" == "$first" ]] || fail 'The reboot is not dated by the flag'
touch -d '2026-11-23 10:00:00 UTC' "$STATE/reboot-required"
bash "$REBOOT"
[[ "$(metric swirl_cloud_reboot.prom swirl_cloud_reboot_required_since_timestamp_seconds)" == "$first" ]] || fail 'A later update resets how long the reboot has waited'
check swirl_cloud_reboot.prom
rm "$STATE/reboot-required"
bash "$REBOOT"
[[ "$(metric swirl_cloud_reboot.prom swirl_cloud_reboot_required_since_timestamp_seconds)" == 0 ]] || fail 'The reboot stays pending after the host rebooted'
[[ -z "$(find "$METRICS_DIR" -name '.swirl_cloud_*')" ]] || fail 'A temporary metrics file is left behind'

# Maintenance unit results.
FILE=swirl_cloud_unit_swirl_cloud_k3s_backup_service.prom
SERVICE_RESULT=success bash "$UNIT" swirl-cloud-k3s-backup.service
success="$(metric "$FILE" swirl_cloud_systemd_unit_last_success_timestamp_seconds)"
[[ -n "$success" && "$(metric "$FILE" swirl_cloud_systemd_unit_last_run_success)" == 1 ]] || fail "A successful run is not recorded: $(cat "$METRICS_DIR/$FILE")"
grep -Fq 'swirl_cloud_systemd_unit_last_run_success{unit="swirl-cloud-k3s-backup.service"} 1' "$METRICS_DIR/$FILE" || fail 'The unit label is missing'
check "$FILE"
sleep 1
SERVICE_RESULT=exit-code bash "$UNIT" swirl-cloud-k3s-backup.service
[[ "$(metric "$FILE" swirl_cloud_systemd_unit_last_run_success)" == 0 ]] || fail 'A failed run is reported as a success'
[[ "$(metric "$FILE" swirl_cloud_systemd_unit_last_success_timestamp_seconds)" == "$success" ]] || fail 'A failed run loses the last success'
env -u SERVICE_RESULT bash "$UNIT" dailyaidecheck.service
[[ "$(metric swirl_cloud_unit_dailyaidecheck_service.prom swirl_cloud_systemd_unit_last_run_success)" == 0 ]] || \
    fail 'A run without a result is a success'
grep -q swirl_cloud_systemd_unit_last_success "$METRICS_DIR/swirl_cloud_unit_dailyaidecheck_service.prom" && fail 'A unit that never succeeded has a last success'
for unit in 'bad"unit' 'a/b' '../x' 'x y'; do
    if bash "$UNIT" "$unit" 2>/dev/null; then fail "Unit name '$unit' is accepted"; fi
done
[[ "$(find "$METRICS_DIR" -name 'swirl_cloud_unit_*' | wc -l)" == 2 ]] || fail 'An unsafe unit name writes a metrics file'

printf '[PASS] Host metrics scripts\n'
