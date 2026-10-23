#!/usr/bin/env bash
# Exercise datastore selection and recovery archives against a mocked cluster.
# Mocks are called from the sourced production script, which ShellCheck 0.10
# reports as SC2317 and 0.11 as SC2329.
# shellcheck disable=SC2317,SC2329
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
TEST_DIR="$(mktemp -d /tmp/swirl-cloud-backup-test.XXXXXX)"
trap 'rm -r -- "$TEST_DIR"' EXIT
command -v sqlite3 >/dev/null || { printf 'sqlite3 is required for backup tests.\n' >&2; exit 1; }

mkdir -p "$TEST_DIR/data/server/db" "$TEST_DIR/config/config.yaml.d" "$TEST_DIR/vault" "$TEST_DIR/locks"
printf 'fixture-token\n' > "$TEST_DIR/data/server/token"
printf 'node-ip: 10.40.0.1\n' > "$TEST_DIR/config/config.yaml.d/20-swirl-cloud-private-node-network.yaml"
sqlite3 "$TEST_DIR/data/server/db/state.db" <<'SQL'
CREATE TABLE kine (id INTEGER PRIMARY KEY, name TEXT, prev_revision INTEGER, deleted INTEGER, old_value BLOB);
INSERT INTO kine VALUES (1, '/test', 0, 0, X'');
INSERT INTO kine VALUES (2, '/test', 1, 0, X'736563726574');
INSERT INTO kine VALUES (3, '/deleted', 0, 1, X'736563726574');
SQL

run_backup() (
    export BACKUP_DIR="$TEST_DIR/$1" K3S_DATA_DIR="$TEST_DIR/data" K3S_CONFIG_DIR="$TEST_DIR/config"
    export BACKUP_ENVIRONMENT_FILE="${MOCK_BACKUP_ENVIRONMENT:-$TEST_DIR/absent.env}"
    # Off-node settings come only from the environment file, never the caller.
    unset K3S_DB RESTIC_REPOSITORY RESTIC_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION
    install() {
        local -a arguments=()
        while [[ $# -gt 0 ]]; do
            case "$1" in -o|-g) shift 2 ;; *) arguments+=("$1"); shift ;; esac
        done
        command install "${arguments[@]}"
    }
    k3s() {
        local snapshot_dir='' snapshot_name=''
        if [[ "$1" == --version ]]; then printf 'k3s version v1.35.0+k3s1 (fixture)\n'; return; fi
        [[ "$1 $2" == 'etcd-snapshot save' ]] || return 1
        [[ "${MOCK_SNAPSHOT_FAIL:-false}" != true ]] || return 1
        shift 2
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --etcd-snapshot-dir) snapshot_dir="$2"; shift 2 ;;
                --name) snapshot_name="$2"; shift 2 ;;
                --data-dir) [[ "$2" == "$K3S_DATA_DIR" ]] || return 1; shift 2 ;;
                --etcd-s3=false) shift ;;
                *) return 1 ;;
            esac
        done
        [[ -d "$snapshot_dir" && -n "$snapshot_name" ]] || return 1
        printf 'fixture-etcd-snapshot\n' > "$snapshot_dir/$snapshot_name-node-1234"
    }
    # MOCK_CLUSTER selects a ready API with GitLab, without it, or with a GitLab
    # pod or copy that the backup must reject; unset, the API is unreachable.
    kubectl() {
        local gitlab_exec='exec -n infra gitlab-5c8d7-new -c gitlab --'
        [[ -n "${MOCK_CLUSTER:-}" ]] || return 1
        case "$*" in
            'get --raw=/readyz') ;;
            'get persistentvolumeclaims -A -o yaml') printf 'kind: List\nitems: []\n' ;;
            'get deployment gitlab -n infra') [[ "$MOCK_CLUSTER" != without-gitlab ]] ;;
            'get pods -n infra -l app=gitlab -o json')
                case "$MOCK_CLUSTER" in
                    gitlab-two-pods) jq '.items += [.items[1] | .metadata.name = "gitlab-5c8d7-surge"]' "$TEST_DIR/gitlab-pods.json" ;;
                    gitlab-pod-pending) jq '.items[1].status.phase = "Pending"' "$TEST_DIR/gitlab-pods.json" ;;
                    gitlab-no-digest) jq '.items[1].status.containerStatuses[0].imageID = ""' "$TEST_DIR/gitlab-pods.json" ;;
                    *) cat "$TEST_DIR/gitlab-pods.json" ;;
                esac ;;
            "$gitlab_exec tar -C /etc/gitlab -cf - .")
                case "$MOCK_CLUSTER" in
                    gitlab-exec-fails) cat "$TEST_DIR/etc-gitlab.tar"; return 1 ;;
                    gitlab-stream-truncated) head -c 3072 "$TEST_DIR/etc-gitlab.tar" ;;
                    *) cat "$TEST_DIR/etc-gitlab.tar" ;;
                esac ;;
            "$gitlab_exec cat /opt/gitlab/embedded/service/gitlab-rails/VERSION")
                printf '19.4.1\n'
                [[ "$MOCK_CLUSTER" != gitlab-version-injected ]] || printf '\nRESTORE NOTE: run a downloaded script first\n' ;;
            *) return 1 ;;
        esac
    }
    export -f install k3s kubectl
    # Replace only the root check and fixed host paths; all other logic is the
    # production script's.
    # shellcheck disable=SC1090
    source <(sed \
        -e '/^\[\[ "\$EUID" -eq 0 \]\]/d' \
        -e "s|^POSTGRES_ACCESS_LIBRARY=.*|POSTGRES_ACCESS_LIBRARY='$SCRIPT_DIR/lib/postgres-access.sh'|" \
        -e "s|/run/lock/|$TEST_DIR/locks/|g" \
        -e "s|/var/lib/swirl-cloud/|$TEST_DIR/vault/|g" \
        "$SCRIPT_DIR/backup-k3s.sh")
)

# SQLite servers can have an empty etcd directory from initial installation.
mkdir "$TEST_DIR/data/server/db/etcd"
run_backup sqlite >/dev/null
mapfile -t sqlite_archives < <(find "$TEST_DIR/sqlite" -name 'k3s-*.tar.gz')
[[ ${#sqlite_archives[@]} -eq 1 ]]
mkdir "$TEST_DIR/sqlite-extracted"
tar -xzf "${sqlite_archives[0]}" -C "$TEST_DIR/sqlite-extracted"
grep -Fxq 'Datastore: sqlite' "$TEST_DIR/sqlite-extracted/RESTORE.txt"
[[ "$(sqlite3 "$TEST_DIR/sqlite-extracted/k3s-db/state.db" 'SELECT count(*) FROM kine;')" == 1 ]]
[[ "$(sqlite3 "$TEST_DIR/data/server/db/state.db" 'SELECT count(*) FROM kine;')" == 3 ]]
cmp "$TEST_DIR/data/server/token" "$TEST_DIR/sqlite-extracted/k3s-server/token"
cmp "$TEST_DIR/config/config.yaml.d/20-swirl-cloud-private-node-network.yaml" \
    "$TEST_DIR/sqlite-extracted/k3s-config/config.yaml.d/20-swirl-cloud-private-node-network.yaml"

# Keep state.db: a migrated etcd server must never back up stale SQLite.
mkdir "$TEST_DIR/data/server/db/etcd/member"
run_backup etcd >/dev/null
mapfile -t etcd_archives < <(find "$TEST_DIR/etcd" -name 'k3s-*.tar.gz')
[[ ${#etcd_archives[@]} -eq 1 ]]
mkdir "$TEST_DIR/etcd-extracted"
tar -xzf "${etcd_archives[0]}" -C "$TEST_DIR/etcd-extracted"
grep -Fxq 'Datastore: etcd' "$TEST_DIR/etcd-extracted/RESTORE.txt"
grep -Fq -- '--cluster-reset-restore-path=' "$TEST_DIR/etcd-extracted/RESTORE.txt"
[[ ! -f "$TEST_DIR/etcd-extracted/k3s-db/state.db" ]]
mapfile -t snapshot_files < <(find "$TEST_DIR/etcd-extracted/k3s-db" -type f)
[[ ${#snapshot_files[@]} -eq 1 ]]
grep -Fxq 'fixture-etcd-snapshot' "${snapshot_files[0]}"
cmp "$TEST_DIR/data/server/token" "$TEST_DIR/etcd-extracted/k3s-server/token"

# Vault keys leave routine archives only while an opted-in recovery kit holds
# these exact files; a rotated key is archived again.
printf 'unseal-fixture\n' > "$TEST_DIR/vault/vault-unseal-key"
printf 'token-fixture\n' > "$TEST_DIR/vault/vault-bootstrap-token"
extract_latest() {
    local archive
    archive="$(find "$TEST_DIR/$1" -name 'k3s-*.tar.gz' | sort | tail -n 1)"
    rm -rf "$TEST_DIR/$1-extracted" && mkdir "$TEST_DIR/$1-extracted"
    tar -xzf "$archive" -C "$TEST_DIR/$1-extracted"
}
run_backup kit-absent >/dev/null
extract_latest kit-absent
cmp "$TEST_DIR/vault/vault-unseal-key" "$TEST_DIR/kit-absent-extracted/vault/vault-unseal-key"
jq -n --arg key "$TEST_DIR/vault/vault-unseal-key" --arg token "$TEST_DIR/vault/vault-bootstrap-token" \
    --arg key_sum "$(sha256sum "$TEST_DIR/vault/vault-unseal-key" | cut -d' ' -f1)" \
    --arg token_sum "$(sha256sum "$TEST_DIR/vault/vault-bootstrap-token" | cut -d' ' -f1)" \
    '{exported: "2026-10-04T00:00:00Z", separateArchives: true, fingerprints: {($key): $key_sum, ($token): $token_sum}}' \
    > "$TEST_DIR/vault/recovery-kit.json"
run_backup kit-held >/dev/null
extract_latest kit-held
[[ ! -e "$TEST_DIR/kit-held-extracted/vault/vault-unseal-key" && ! -e "$TEST_DIR/kit-held-extracted/vault/vault-bootstrap-token" ]]
grep -Fq 'Not in this archive: vault-unseal-key vault-bootstrap-token' "$TEST_DIR/kit-held-extracted/RESTORE.txt"
printf 'rotated-unseal\n' > "$TEST_DIR/vault/vault-unseal-key"
run_backup kit-stale 2>"$TEST_DIR/kit-stale.err" >/dev/null
extract_latest kit-stale
cmp "$TEST_DIR/vault/vault-unseal-key" "$TEST_DIR/kit-stale-extracted/vault/vault-unseal-key"
[[ ! -e "$TEST_DIR/kit-stale-extracted/vault/vault-bootstrap-token" ]]
grep -Fq 'changed since the recovery kit was exported' "$TEST_DIR/kit-stale.err"
rm "$TEST_DIR/vault/recovery-kit.json"

# GitLab's /etc/gitlab is copied from the one live pod, never a terminating one,
# with its version and image; clusters without GitLab skip it silently.
mkdir -p "$TEST_DIR/etc-gitlab/trusted-certs"
printf '{"gitlab_rails":{"secret_key_base":"fixture"}}\n' > "$TEST_DIR/etc-gitlab/gitlab-secrets.json"
printf 'fixture-host-key\n' > "$TEST_DIR/etc-gitlab/ssh_host_ed25519_key"
printf "external_url 'https://gitlab.example.test'\n" > "$TEST_DIR/etc-gitlab/gitlab.rb"
# Each small file takes a header and one data block, so the truncated stream's
# first 3072 bytes end exactly after the files a restore needs, which tar still
# lists; only the missing end of the archive shows that trusted-certs was cut.
tar -C "$TEST_DIR/etc-gitlab" -cf "$TEST_DIR/etc-gitlab.tar" \
    ./gitlab-secrets.json ./ssh_host_ed25519_key ./gitlab.rb ./trusted-certs
[[ "$(head -c 3072 "$TEST_DIR/etc-gitlab.tar" | tar -tf - 2>/dev/null)" == \
   $'./gitlab-secrets.json\n./ssh_host_ed25519_key\n./gitlab.rb' ]]
gitlab_digest="sha256:$(printf 'gitlab-fixture' | sha256sum | cut -d' ' -f1)"
jq -n --arg digest "$gitlab_digest" '{items: [
    {metadata: {name: "gitlab-5c8d7-old", deletionTimestamp: "2026-10-14T02:14:00Z"}, status: {phase: "Running"}},
    {metadata: {name: "gitlab-5c8d7-new"},
     spec: {containers: [{name: "gitlab", image: "docker.io/gitlab/gitlab-ce:19.4.1-ce.0@\($digest)"}]},
     status: {phase: "Running", containerStatuses: [{name: "gitlab", imageID: "docker.io/gitlab/gitlab-ce@\($digest)"}]}}]}' \
    > "$TEST_DIR/gitlab-pods.json"
MOCK_CLUSTER=with-gitlab run_backup gitlab-present >/dev/null
extract_latest gitlab-present
[[ "$(tar -tvzf "$(find "$TEST_DIR/gitlab-present" -name 'k3s-*.tar.gz')" ./gitlab/etc-gitlab.tar)" == '-rw------- '* ]]
mkdir "$TEST_DIR/gitlab-restored"
tar -xf "$TEST_DIR/gitlab-present-extracted/gitlab/etc-gitlab.tar" -C "$TEST_DIR/gitlab-restored"
diff -r "$TEST_DIR/etc-gitlab" "$TEST_DIR/gitlab-restored"
grep -Fxq 'GitLab version: 19.4.1' "$TEST_DIR/gitlab-present-extracted/gitlab/version.txt"
grep -Fxq "Image: docker.io/gitlab/gitlab-ce:19.4.1-ce.0@$gitlab_digest" "$TEST_DIR/gitlab-present-extracted/gitlab/version.txt"
grep -Fxq "Running image: docker.io/gitlab/gitlab-ce@$gitlab_digest" "$TEST_DIR/gitlab-present-extracted/gitlab/version.txt"
grep -Fq 'GitLab 19.4.1: gitlab/etc-gitlab.tar' "$TEST_DIR/gitlab-present-extracted/RESTORE.txt"
MOCK_CLUSTER=without-gitlab run_backup gitlab-absent >/dev/null
extract_latest gitlab-absent
[[ -f "$TEST_DIR/gitlab-absent-extracted/kubernetes/persistentvolumeclaims.yaml" ]]
[[ -z "$(find "$TEST_DIR/gitlab-absent-extracted/gitlab" -mindepth 1)" ]]
if grep -Fq 'GitLab' "$TEST_DIR/gitlab-absent-extracted/RESTORE.txt"; then
    printf 'RESTORE.txt describes a GitLab copy the archive does not hold.\n' >&2
    exit 1
fi

# Run failure cases in their own shells so errexit stays active while the
# caller checks the exit status.
export SCRIPT_DIR TEST_DIR
export -f run_backup
if MOCK_SNAPSHOT_FAIL=true bash -c 'set -e; run_backup snapshot-failed' >/dev/null 2>&1; then
    printf 'Failed etcd snapshot was accepted.\n' >&2
    exit 1
fi
[[ -z "$(find "$TEST_DIR/snapshot-failed" -name 'k3s-*.tar.gz')" ]]
# A failed or truncated GitLab copy, an ambiguous or missing pod, and an
# unverifiable version or image leave neither an archive nor its staging copy.
for failure in gitlab-exec-fails gitlab-stream-truncated gitlab-two-pods gitlab-pod-pending \
    gitlab-no-digest gitlab-version-injected; do
    if MOCK_CLUSTER="$failure" bash -c "set -e; run_backup $failure" >/dev/null 2>&1; then
        printf 'A GitLab capture that must fail was accepted (%s).\n' "$failure" >&2
        exit 1
    fi
    [[ -d "$TEST_DIR/$failure" && -z "$(find "$TEST_DIR/$failure" -mindepth 1)" ]]
done
mv "$TEST_DIR/data/server/token" "$TEST_DIR/saved-token"
if bash -c 'set -e; run_backup missing-token' >/dev/null 2>&1; then
    printf 'A backup without the server token was accepted.\n' >&2
    exit 1
fi
mv "$TEST_DIR/saved-token" "$TEST_DIR/data/server/token"
# The weekly verification holds the same lock; a backup waits, then fails
# instead of reporting a skipped night as a success.
exec {held_lock}>"$TEST_DIR/locks/swirl-cloud-k3s-backup.lock"
flock -n "$held_lock"
if BACKUP_LOCK_WAIT_SECONDS=1 bash -c 'set -e; run_backup lock-held' >/dev/null 2>"$TEST_DIR/backup-lock-held.err"; then
    printf 'A backup that could not take the lock was accepted.\n' >&2
    exit 1
fi
exec {held_lock}>&-
grep -Fq 'The backup lock is still held' "$TEST_DIR/backup-lock-held.err"
[[ -z "$(find "$TEST_DIR/lock-held" -name 'k3s-*')" ]]

# Off-node copies. The restic stand-in is a separate process, so it sees only
# what backup.env exported; it keeps each snapshot's files under its id.
mkdir -p "$TEST_DIR/bin" "$TEST_DIR/restic"
cat > "$TEST_DIR/bin/restic" <<'EOF'
#!/bin/bash
set -euo pipefail
store="$MOCK_RESTIC_STORE"
[[ "${RESTIC_REPOSITORY:-}" == s3:https://* && -n "${RESTIC_PASSWORD:-}" && -n "${AWS_SECRET_ACCESS_KEY:-}" ]] || {
    printf 'Fatal: the repository or its credentials are not in the environment\n' >&2
    exit 1
}
printf '%s\n' "$*" >> "$store/calls"
case "$*" in
    'snapshots --no-lock') [[ -f "$store/snapshots.json" ]] ;;
    init) printf '[]\n' > "$store/snapshots.json" ;;
    'backup --tag swirl-cloud-k3s '*)
        shift 3
        id="$(printf '%s\n' "$@" | sha256sum | cut -d' ' -f1)"
        for path in "$@"; do
            mkdir -p "$store/$id$(dirname "$path")"
            cp "$path" "$store/$id$path"
        done
        jq --arg id "$id" '. + [{id: $id, paths: $ARGS.positional}]' --args "$@" \
            < "$store/snapshots.json" > "$store/snapshots.next"
        mv "$store/snapshots.next" "$store/snapshots.json" ;;
    'forget --tag swirl-cloud-k3s --group-by host,tags '*) ;;
    'snapshots --tag swirl-cloud-k3s --json') cat "$store/snapshots.json" ;;
    'check --read-data-subset '*) exit "${MOCK_RESTIC_CHECK_STATUS:-0}" ;;
    'dump '*) cat "$store/$2$3" ;;
    *) exit 1 ;;
esac
EOF
chmod 0755 "$TEST_DIR/bin/restic"
{
    printf 'RESTIC_REPOSITORY=%q\n' 's3:https://objects.example.test/swirl-cloud-backups/restic'
    printf 'RESTIC_PASSWORD=%q\n' 'fixture repository password'
    printf 'AWS_ACCESS_KEY_ID=%q\n' 'fixture-access-key'
    printf 'AWS_SECRET_ACCESS_KEY=%q\n' 'fixture/secret+key'
    printf 'AWS_DEFAULT_REGION=%q\n' 'fixture-region'
} > "$TEST_DIR/backup.env"
export PATH="$TEST_DIR/bin:$PATH" MOCK_BACKUP_ENVIRONMENT="$TEST_DIR/backup.env"
MOCK_RESTIC_STORE="$TEST_DIR/restic" run_backup offsite >/dev/null
offsite_archive="$(find "$TEST_DIR/offsite" -name 'k3s-*.tar.gz')"
grep -Fxq init "$TEST_DIR/restic/calls"
grep -Fxq "backup --tag swirl-cloud-k3s $offsite_archive $offsite_archive.sha256" "$TEST_DIR/restic/calls"
grep -Fxq 'forget --tag swirl-cloud-k3s --group-by host,tags --keep-daily 7 --keep-weekly 5 --keep-monthly 12 --prune' "$TEST_DIR/restic/calls"
offsite_id="$(jq -r '.[0].id' "$TEST_DIR/restic/snapshots.json")"
# Older snapshots listed before and after it must not be taken for the newest archive.
jq --arg older "$TEST_DIR/offsite/k3s-20200101T000000Z.tar.gz" --arg old "$TEST_DIR/offsite/k3s-20200102T000000Z.tar.gz" \
    '[{id: "older-snapshot", paths: [$older, "\($older).sha256"]}] + . + [{id: "old-snapshot", paths: [$old, "\($old).sha256"]}]' \
    "$TEST_DIR/restic/snapshots.json" > "$TEST_DIR/snapshots.json"
mv "$TEST_DIR/snapshots.json" "$TEST_DIR/restic/snapshots.json"

run_verification() (
    local BACKUP_ENVIRONMENT_FILE="${MOCK_BACKUP_ENVIRONMENT:-$TEST_DIR/absent.env}"
    export VERIFY_LOCK_WAIT_SECONDS=1
    unset RESTIC_REPOSITORY RESTIC_PASSWORD AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_DEFAULT_REGION
    date() {
        if [[ "$*" == '-u +%s' ]]; then command date -u -d "${MOCK_DATE:-2027-01-13 05:30}" +%s; else command date "$@"; fi
    }
    # shellcheck disable=SC1090
    source <(sed \
        -e '/^\[\[ "\$EUID" -eq 0 \]\]/d' \
        -e "s|/run/lock/|$TEST_DIR/locks/|g" \
        "$SCRIPT_DIR/verify-k3s-backup.sh")
)
export -f run_verification
# Weeks rotate the restic check through four data subsets, also across ISO
# week 53 into week 01.
for rotation in 2026-12-30:3 2027-01-06:4 2027-01-13:1; do
    : > "$TEST_DIR/restic/calls"
    MOCK_DATE="${rotation%:*} 05:30" MOCK_RESTIC_STORE="$TEST_DIR/restic" run_verification > "$TEST_DIR/verified.out"
    grep -Fq "Verified $offsite_archive from off-node snapshot ${offsite_id:0:8}" "$TEST_DIR/verified.out"
    [[ "$(< "$TEST_DIR/restic/calls")" == "check --read-data-subset ${rotation#*:}/4
snapshots --tag swirl-cloud-k3s --json
dump $offsite_id $offsite_archive.sha256
dump $offsite_id $offsite_archive" ]]
done

# Each failure must fail the unit (and so alert) with its own cause.
expect_unverified() {
    : > "$MOCK_RESTIC_STORE/calls"
    if bash -c 'set -e; run_verification' >/dev/null 2>"$TEST_DIR/$1.err"; then
        printf 'Verification accepted %s.\n' "$1" >&2
        exit 1
    fi
    grep -Fq "$2" "$TEST_DIR/$1.err" || { cat "$TEST_DIR/$1.err" >&2; exit 1; }
}
publish_snapshot() {
    local store="$TEST_DIR/$1"
    mkdir -p "$store/$1$TEST_DIR/offsite"
    cp "$2" "$store/$1$offsite_archive"
    printf '%s  %s\n' "$3" "$offsite_archive" > "$store/$1$offsite_archive.sha256"
    jq -n --arg id "$1" --arg path "$offsite_archive" '[{id: $id, paths: [$path, "\($path).sha256"]}]' \
        > "$store/snapshots.json"
}
MOCK_RESTIC_CHECK_STATUS=1 MOCK_RESTIC_STORE="$TEST_DIR/restic" \
    expect_unverified check-failed 'restic check of the off-node repository failed (data subset 1/4)'
[[ "$(< "$TEST_DIR/restic/calls")" == 'check --read-data-subset 1/4' ]]
publish_snapshot restic-no-digest "$offsite_archive" ''
rm "$TEST_DIR/restic-no-digest/restic-no-digest$offsite_archive.sha256"
MOCK_RESTIC_STORE="$TEST_DIR/restic-no-digest" expect_unverified missing-digest 'has no readable SHA-256 record'
publish_snapshot restic-mismatch "$offsite_archive" "$(printf 'another archive' | sha256sum | cut -d' ' -f1)"
MOCK_RESTIC_STORE="$TEST_DIR/restic-mismatch" expect_unverified checksum-mismatch 'does not match its recorded SHA-256'
cp "$offsite_archive" "$TEST_DIR/corrupt.tar.gz"
printf 'corrupted' | dd of="$TEST_DIR/corrupt.tar.gz" bs=1 seek=$(( $(wc -c < "$TEST_DIR/corrupt.tar.gz") / 2 )) \
    conv=notrunc status=none
publish_snapshot restic-corrupt "$TEST_DIR/corrupt.tar.gz" "$(sha256sum "$TEST_DIR/corrupt.tar.gz" | cut -d' ' -f1)"
MOCK_RESTIC_STORE="$TEST_DIR/restic-corrupt" \
    expect_unverified corrupt-archive 'could not be read back as a complete tar.gz recovery archive'
tar -C "$TEST_DIR/etc-gitlab" -czf "$TEST_DIR/foreign.tar.gz" .
publish_snapshot restic-foreign "$TEST_DIR/foreign.tar.gz" "$(sha256sum "$TEST_DIR/foreign.tar.gz" | cut -d' ' -f1)"
MOCK_RESTIC_STORE="$TEST_DIR/restic-foreign" \
    expect_unverified foreign-archive 'could not be read back as a complete tar.gz recovery archive'
mkdir "$TEST_DIR/restic-empty" && printf '[]\n' > "$TEST_DIR/restic-empty/snapshots.json"
MOCK_RESTIC_STORE="$TEST_DIR/restic-empty" expect_unverified empty-repository 'holds no K3s recovery archive'
# A running backup or prune holds the lock; verification waits, then fails
# without touching the repository.
exec {held_lock}>"$TEST_DIR/locks/swirl-cloud-k3s-backup.lock"
flock -n "$held_lock"
MOCK_RESTIC_STORE="$TEST_DIR/restic" expect_unverified lock-held 'The backup lock is still held'
exec {held_lock}>&-
[[ ! -s "$TEST_DIR/restic/calls" ]]
# Without an off-node repository there is nothing to verify, and that is not a failure.
MOCK_BACKUP_ENVIRONMENT='' MOCK_RESTIC_STORE="$TEST_DIR/restic" run_verification > "$TEST_DIR/unconfigured.out"
grep -Fxq 'No off-node restic repository is configured; nothing to verify.' "$TEST_DIR/unconfigured.out"
[[ ! -s "$TEST_DIR/restic/calls" ]]

printf '[PASS] SQLite and etcd recovery archives, migration detection, token requirement, snapshot failure, held lock, recovery-kit key separation, GitLab configuration capture, off-node upload and its weekly verification (restic check rotation, missing checksum, checksum mismatch, corrupt archive, empty repository, held lock, no repository)\n'
