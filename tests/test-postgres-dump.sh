#!/usr/bin/env bash
# Guard the six-hourly PostgreSQL dump without a cluster: a complete dump is kept
# root-only with its checksum and uploaded to the off-node repository, the
# credentials reach pg_dumpall on standard input only, an incomplete dump is
# discarded, only the newest dumps are kept, a full disk stops the run before it
# writes, and the timer gives a dump every six hours with the nightly archive.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
BIN="$STATE/bin"
mkdir -p "$BIN"
LOG="$STATE/log"
# kubectl: the postgres Service and Secret, and pg_dumpall's output (MOCK_DUMP
# complete|truncated); the exec's standard input is logged to look for secrets.
cat > "$BIN/kubectl" <<EOF
#!/usr/bin/env bash
case "\$*" in
    "get service postgres -n infra") [[ "\${MOCK_POSTGRES:-yes}" == yes ]] ;;
    *"get secret postgres-secret"*POSTGRES_USER*) printf 'cG9zdGdyZXM=' ;;
    *"get secret postgres-secret"*POSTGRES_PASSWORD*) printf 'czNjcjN0' ;;
    *"get pods"*|*"get deployment"*|*"get endpoints"*) printf 'postgres-0' ;;
    "exec -i -n infra "*)
        printf 'exec %s | stdin=%s\n' "\$*" "\$(cat | tr '\n' ' ')" >> "$LOG"
        printf -- '--\n-- PostgreSQL database cluster dump\n--\nCREATE ROLE app;\n'
        [[ "\${MOCK_DUMP:-complete}" != complete ]] || printf -- '--\n-- PostgreSQL database cluster dump complete\n--\n' ;;
    *) printf 'kubectl %s\n' "\$*" >> "$LOG" ;;
esac
EOF
cat > "$BIN/restic" <<EOF
#!/usr/bin/env bash
printf 'restic %s\n' "\$*" >> "$LOG"
EOF
cat > "$BIN/df" <<'EOF'
#!/usr/bin/env bash
printf 'Size Avail\n%s\n' "${MOCK_DF:-1000000000000 900000000000}"
EOF
# flock: the lock file lives under /run/lock, which a test may not write.
cat > "$BIN/flock" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$BIN"/*
# The script locks /run/lock/swirl-cloud-k3s-backup.lock through fd 9; point it at $STATE.
sed "s#/run/lock/swirl-cloud-k3s-backup.lock#$STATE/backup.lock#" "$ROOT/scripts/backup-postgres.sh" > "$STATE/backup-postgres.sh"
mkdir -p "$STATE/lib"
# The target lookup is tested with the library; here the pod is fixed.
printf 'postgres_runtime_target() { printf "postgres-0\\n"; }\n' > "$STATE/lib/postgres-access.sh"
BACKUPS="$STATE/backups"
dump() {
    env PATH="$BIN:$PATH" EUID=0 BACKUP_ENVIRONMENT_FILE=/nonexistent BACKUP_DIR="$BACKUPS" "$@" bash "$STATE/backup-postgres.sh" 2>&1
}
[[ "$(id -u)" == 0 ]] || { printf '[SKIP] The dump script runs as root\n'; exit 0; }

# A complete dump, uploaded when an off-node repository is set.
: > "$LOG"
output="$(dump RESTIC_REPOSITORY=s3:https://s3.example.com/backups)" || fail "A dump fails: $output"
mapfile -t dumps < <(find "$BACKUPS/postgres" -name 'postgresql-*.sql.gz')
(( ${#dumps[@]} == 1 )) || fail "Expected one dump, found ${#dumps[@]}"
gzip -dc "${dumps[0]}" | grep -q 'CREATE ROLE app;' || fail 'The dump does not hold the database'
[[ "$(stat -c %a "${dumps[0]}")" == 600 && "$(stat -c %a "$BACKUPS/postgres")" == 700 ]] || fail 'The dump is readable by others'
(cd "$BACKUPS/postgres" && sha256sum -c --quiet "$(basename "${dumps[0]}").sha256") || fail 'The checksum does not match'
grep -Fq "| stdin=postgres s3cr3t " "$LOG" || fail 'The credentials are not passed on standard input'
! grep -v '| stdin=' "$LOG" | grep -q s3cr3t || fail 'The password appears in a command line'
grep -q "^restic backup --tag swirl-cloud-postgres .*postgresql-.*\.sql\.gz .*\.sha256$" "$LOG" || fail 'The dump is not uploaded'
grep -Fq "restic forget --tag swirl-cloud-postgres --group-by host,tags --keep-last 8" "$LOG" || fail 'Old uploads are not forgotten'
! grep -q 'restic prune' "$LOG" || fail 'The six-hourly dump prunes the repository'

# Only the newest dumps are kept locally.
for n in 1 2 3 4 5 6 7 8 9; do
    touch "$BACKUPS/postgres/postgresql-20260101T0${n}0000Z.sql.gz" "$BACKUPS/postgres/postgresql-20260101T0${n}0000Z.sql.gz.sha256"
done
dump POSTGRES_DUMP_KEEP=3 >/dev/null || fail 'A dump with old dumps around fails'
[[ "$(find "$BACKUPS/postgres" -name 'postgresql-*.sql.gz' | wc -l)" == 3 ]] || fail 'Old dumps are not removed'
[[ "$(find "$BACKUPS/postgres" -name '*.sha256' | wc -l)" == 3 ]] || fail 'Old checksums are not removed'
[[ -e "${dumps[0]}" ]] || fail 'The newest dump is removed'

# An incomplete dump is discarded; a full disk stops before writing.
before="$(find "$BACKUPS/postgres" -type f | sort)"
if output="$(dump MOCK_DUMP=truncated)"; then fail 'An incomplete dump is kept'; fi
[[ "$output" == *"did not finish"* && "$(find "$BACKUPS/postgres" -type f | sort)" == "$before" ]] || fail "Unexpected result: $output"
: > "$LOG"
if output="$(dump MOCK_DF='1000000000000 100000000000')"; then fail 'A dump runs on a full disk'; fi
[[ "$output" == *"Too little free space"* ]] || fail "Unexpected result on a full disk: $output"
if grep -q '^exec' "$LOG"; then fail 'A dump runs on a full disk'; fi
[[ "$(dump MOCK_POSTGRES=no)" == *"nothing to dump"* ]] || fail 'A host without PostgreSQL fails'
if dump POSTGRES_DUMP_KEEP=0 >/dev/null; then fail 'POSTGRES_DUMP_KEEP=0 is accepted'; fi

# The timer and service.
timer="$ROOT/config/host/swirl-cloud-postgres-dump.timer"
grep -Fqx 'OnCalendar=*-*-* 08,14,20:15:00 UTC' "$timer" || fail 'The timer is not six-hourly'
grep -Fqx 'Persistent=true' "$timer" || fail 'A missed dump is not caught up'
grep -Fqx 'OnCalendar=*-*-* 02:15:00 UTC' "$ROOT/config/host/swirl-cloud-k3s-backup.timer" || fail 'The nightly archive moved; realign the dump timer'
grep -Fqx 'ExecStart=/usr/local/sbin/swirl-cloud-postgres-dump' "$ROOT/config/host/swirl-cloud-postgres-dump.service" || fail 'The service runs another script'
grep -q 'swirl-cloud-postgres-dump.service' "$ROOT/scripts/lib/host-baseline.sh" || fail 'The dump records no unit metrics'
grep -q 'unit="swirl-cloud-postgres-dump.service"' "$ROOT/k8s/files/prometheus/rules.yaml" || fail 'No alert watches the dump'

printf '[PASS] Six-hourly PostgreSQL dump\n'
