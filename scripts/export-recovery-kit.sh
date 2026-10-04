#!/usr/bin/env bash
# Export an encrypted recovery kit: everything needed to rebuild the platform
# when this host is gone. Store the kit and its passphrase offline (password
# manager, encrypted USB), never on this host.
#
#   scripts/export-recovery-kit.sh [--output FILE] [--separate-archives]
#   scripts/export-recovery-kit.sh --verify FILE
#
# The kit holds the Vault unseal key and root token, the K3s server token and
# encryption configuration, and the off-node backup credentials (restic password,
# S3 keys) when configured. With --separate-archives, later nightly archives omit
# the Vault keys while they still match this kit; a rotated key is archived again.
set -Eeuo pipefail
set +x
umask 077

STATE_DIR="${SWIRL_CLOUD_STATE_DIR:-/var/lib/swirl-cloud}"
BACKUP_ENV="${SWIRL_CLOUD_BACKUP_ENV:-/etc/swirl-cloud/backup.env}"
K3S_DATA_DIR="${K3S_DATA_DIR:-/var/lib/rancher/k3s}"
MANIFEST="$STATE_DIR/recovery-kit.json"
SOURCES=(
    "$STATE_DIR/vault-unseal-key"
    "$STATE_DIR/vault-bootstrap-token"
    "$K3S_DATA_DIR/server/token"
    "$K3S_DATA_DIR/server/cred/encryption-config.json"
    "$K3S_DATA_DIR/server/cred/encryption-state.json"
    "$BACKUP_ENV"
)
ITERATIONS=600000
error() { echo "[ERROR] $*" >&2; exit 1; }
info() { echo "[INFO]  $*" >&2; }

usage() { sed -n '6,13s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }

output="$HOME/swirl-cloud-recovery-kit-$(date -u +%Y%m%dT%H%M%SZ).enc"
mode="export"
separate=false
while (( $# > 0 )); do
    case "$1" in
        --output) output="${2:?--output needs a path}"; shift ;;
        --separate-archives) separate=true ;;
        --verify) mode=verify; output="${2:?--verify needs a kit file}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 1 ;;
    esac
    shift
done
[[ $EUID -ne 0 ]] || error "Run as the installing user; sudo is used for the root-only files."
command -v openssl >/dev/null || error "openssl is required."
[[ -t 0 ]] || error "The passphrase is read from the terminal."

workdir="$(mktemp -d)"
cleanup() { rm -rf -- "$workdir"; passphrase=""; confirmation=""; }
trap cleanup EXIT

read_passphrase() {
    IFS= read -rsp "$1: " passphrase </dev/tty
    printf '\n' >&2
}

# Fingerprints of the current key files, as JSON; values never leave root files.
fingerprints() {
    local file
    for file in "${SOURCES[@]}"; do
        if sudo test -f "$file"; then
            printf '%s %s\n' "$(sudo sha256sum "$file" | cut -d' ' -f1)" "$file"
        fi
    done | jq -Rn '[inputs | split(" ") | {(.[1]): .[0]}] | add // {}'
}

if [[ "$mode" == verify ]]; then
    [[ -r "$output" ]] || error "Cannot read $output"
    read_passphrase "Recovery kit passphrase"
    printf '%s' "$passphrase" | openssl enc -d -aes-256-cbc -pbkdf2 -iter "$ITERATIONS" -pass stdin \
        -in "$output" -out "$workdir/kit.tar" 2>/dev/null || error "Wrong passphrase or damaged kit."
    tar -C "$workdir" -xf "$workdir/kit.tar" kit/fingerprints.json
    current="$(fingerprints)"
    if jq -e --argjson current "$current" '. == $current' "$workdir/kit/fingerprints.json" >/dev/null; then
        info "The kit opens and matches every current key file."
    else
        jq -r --argjson current "$current" 'to_entries[] | select($current[.key] != .value) | "  differs: \(.key)"' \
            "$workdir/kit/fingerprints.json" >&2
        error "The kit is readable but no longer matches the current keys; export a new one."
    fi
    exit 0
fi

[[ ! -e "$output" ]] || error "$output exists; choose a new --output path."
sudo test -f "$STATE_DIR/vault-unseal-key" || error "No Vault unseal key on this host; run this on the control plane that holds it."
mkdir -m 700 "$workdir/kit"
for file in "${SOURCES[@]}"; do
    sudo test -f "$file" || continue
    # The copy belongs to the invoking user's private work directory, not root.
    # shellcheck disable=SC2024
    sudo cat -- "$file" > "$workdir/kit/$(basename "$file")"
done
fingerprints > "$workdir/kit/fingerprints.json"
cat > "$workdir/kit/README.txt" <<EOF
Swirl Cloud recovery kit, exported $(date -u +%Y-%m-%dT%H:%M:%SZ) from $(hostname -s).

vault-unseal-key, vault-bootstrap-token   Vault unseal key and root token
token, encryption-*.json                  K3s server token and secrets-encryption state
backup.env                                restic password and S3 credentials (when configured)

Decrypt:  openssl enc -d -aes-256-cbc -pbkdf2 -iter $ITERATIONS -in KIT -out kit.tar
Restore the K3s files with the nightly archive (see its RESTORE.txt), place the
Vault files in /var/lib/swirl-cloud (root, mode 600), and use backup.env to read
the off-node restic repository. See docs/security.md (Recovery kit).
EOF
tar -C "$workdir" -cf "$workdir/kit.tar" kit

read_passphrase "New recovery kit passphrase (16+ characters)"
(( ${#passphrase} >= 16 )) || error "Use a passphrase of at least 16 characters."
confirmation="$passphrase"
read_passphrase "Confirm the passphrase"
[[ "$passphrase" == "$confirmation" ]] || error "The passphrases do not match."
printf '%s' "$passphrase" | openssl enc -aes-256-cbc -pbkdf2 -iter "$ITERATIONS" -salt -pass stdin \
    -in "$workdir/kit.tar" -out "$output"
chmod 600 "$output"
# Prove the kit decrypts before anyone relies on it.
printf '%s' "$passphrase" | openssl enc -d -aes-256-cbc -pbkdf2 -iter "$ITERATIONS" -pass stdin -in "$output" |
    cmp -s - "$workdir/kit.tar" || { rm -f -- "$output"; error "The written kit did not decrypt; nothing was exported."; }

jq -n --arg exported "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --argjson separate "$separate" \
    --argjson files "$(cat "$workdir/kit/fingerprints.json")" \
    '{exported: $exported, separateArchives: $separate, fingerprints: $files}' > "$workdir/manifest.json"
sudo install -o root -g root -m 0600 "$workdir/manifest.json" "$MANIFEST"
info "Recovery kit written and verified: $output"
info "Move it and its passphrase off this host now, then check it with: $0 --verify FILE"
if [[ "$separate" == true ]]; then
    info "Nightly archives will omit the Vault keys while they match this kit."
fi
