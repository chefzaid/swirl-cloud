#!/usr/bin/env bash
# Guard the monthly Lynis runner without Lynis: a complete report replaces the
# canonical files Filebeat follows and is archived, the metrics carry the run,
# its exit code and the hardening index, a failed run keeps the last success,
# and a run without a report changes no canonical file.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
BIN="$STATE/bin"
mkdir -p "$BIN" "$STATE/var/log" "$STATE/var/lib"
# The runner as installed, with its host paths moved under $STATE.
sed -n "/^install_managed_file \/usr\/local\/sbin\/swirl-cloud-lynis-monthly 0750 <<'RUNNER'$/,/^RUNNER$/p" \
    "$ROOT/scripts/configure-lynis-schedule.sh" | sed '1d;$d' | \
    sed "s#/var/log#$STATE/var/log#g; s#/var/lib/node_exporter#$STATE/var/lib/node_exporter#g" > "$STATE/runner"
[[ -s "$STATE/runner" ]] || fail 'The runner script is not found in configure-lynis-schedule.sh'
cat > "$BIN/install" <<'EOF'
#!/usr/bin/env bash
args=()
while (( $# )); do case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac; done
exec /usr/bin/install "${args[@]}"
EOF
# lynis stub: LYNIS_INDEX writes a report, LYNIS_EXIT sets the exit code.
cat > "$BIN/lynis" <<'EOF'
#!/usr/bin/env bash
report="" log=""
while (( $# )); do
    case "$1" in --report-file) report="$2"; shift 2 ;; --log-file) log="$2"; shift 2 ;; *) shift ;; esac
done
printf 'lynis log\n' > "$log"
[[ -z "${LYNIS_INDEX:-}" ]] || printf 'report_version=1.0\nhardening_index=%s\n' "$LYNIS_INDEX" > "$report"
echo "Lynis output"
exit "${LYNIS_EXIT:-0}"
EOF
chmod +x "$BIN"/*
METRICS="$STATE/var/lib/node_exporter/textfile_collector/swirl_cloud_lynis.prom"
run() { env PATH="$BIN:$PATH" "$@" bash "$STATE/runner"; }
metric() { awk -v name="$1" '$1 == name {print $2}' "$METRICS"; }

# A complete run.
run LYNIS_INDEX=71 || fail 'A clean Lynis run fails'
[[ "$(metric swirl_cloud_lynis_hardening_index)" == 71 && "$(metric swirl_cloud_lynis_last_run_exit_code)" == 0 ]] || \
    fail "Unexpected metrics: $(cat "$METRICS")"
first_success="$(metric swirl_cloud_lynis_last_success_timestamp_seconds)"
[[ -n "$first_success" ]] || fail 'A clean run records no success'
grep -Fqx 'hardening_index=71' "$STATE/var/log/lynis-report.dat" || fail 'The canonical report is not replaced'
[[ "$(stat -c %a "$STATE/var/log/lynis-report.dat")" == 600 ]] || fail 'The canonical report is readable by others'
[[ "$(find "$STATE/var/log/lynis-reports" -maxdepth 1 -name 'lynis-*-report.dat' | wc -l)" == 1 ]] || fail 'The report is not archived'
[[ "$(stat -c %a "$STATE/var/log/lynis-reports")" == 700 ]] || fail 'The archive is readable by others'
[[ -z "$(find "$STATE/var/log/lynis-reports" -name '.run.*')" ]] || fail 'The working directory is left behind'
if command -v promtool >/dev/null 2>&1; then
    promtool check metrics < "$METRICS" || fail 'The Lynis metrics are not valid exposition format'
fi

# A run with findings that exits non-zero keeps the last success and reports the code.
sleep 1
if run LYNIS_INDEX=65 LYNIS_EXIT=78; then fail 'A failing Lynis run exits 0'; fi
[[ "$(metric swirl_cloud_lynis_last_run_exit_code)" == 78 && "$(metric swirl_cloud_lynis_hardening_index)" == 65 ]] || \
    fail "Unexpected metrics after a failing run: $(cat "$METRICS")"
[[ "$(metric swirl_cloud_lynis_last_success_timestamp_seconds)" == "$first_success" ]] || fail 'A failing run moves the last success'

# A run without a report replaces no canonical file and drops the index.
if run LYNIS_EXIT=0 2>/dev/null; then fail 'A run without a report exits 0'; fi
grep -Fqx 'hardening_index=65' "$STATE/var/log/lynis-report.dat" || fail 'A run without a report replaces the canonical report'
[[ "$(metric swirl_cloud_lynis_last_run_exit_code)" == 1 && -z "$(metric swirl_cloud_lynis_hardening_index)" ]] || \
    fail "Unexpected metrics without a report: $(cat "$METRICS")"
[[ "$(metric swirl_cloud_lynis_last_success_timestamp_seconds)" == "$first_success" ]] || fail 'A run without a report moves the last success'

# The timer runs monthly and catches up after downtime.
grep -Fqx 'OnCalendar=*-*-15 03:00:00' "$ROOT/scripts/configure-lynis-schedule.sh" || fail 'The audit is not monthly'
grep -Fqx 'Persistent=true' "$ROOT/scripts/configure-lynis-schedule.sh" || fail 'A missed monthly audit is not caught up'

printf '[PASS] Lynis schedule\n'
