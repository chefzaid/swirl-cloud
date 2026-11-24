#!/usr/bin/env bash
# Guard the host SSH configuration without a host: the allowed users are a plain
# list of local names, root cannot log in and forwarding is off, a configuration
# that sshd rejects is rolled back (to the saved copy, or removed) and SSH is not
# reloaded, and an unchanged session drop-in does not reload SSH.
# The stubs below replace commands that the library runs through sudo.
# shellcheck disable=SC2032
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/host-baseline.sh
source "$SCRIPT_DIR/lib/host-baseline.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
err() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
LOG="$STATE/log"
# Host paths live under $STATE/root.
host() { printf '%s/root%s' "$STATE" "$1"; }
backup_file_if_exists() { [[ ! -f "$(host "$1")" ]] || { mkdir -p "$(dirname "$(host "/var/backups/swirl-cloud/config$1.bak")")"; cp "$(host "$1")" "$(host "/var/backups/swirl-cloud/config$1.bak")"; }; }
write_root_file() { mkdir -p "$(dirname "$(host "$1")")"; cat > "$(host "$1")"; printf 'write %s %s\n' "$1" "${2:-0644}" >> "$LOG"; }
SSHD_VALID=true
sudo() {
    case "$1" in
        sshd) printf 'sshd %s\n' "$*" >> "$LOG"; [[ "$SSHD_VALID" == true ]] ;;
        systemctl) printf '%s\n' "$*" >> "$LOG" ;;
        test) [[ -f "$(host "$3")" ]] ;;
        cat) command cat "$(host "$2")" ;;
        cp) command cp "$(host "$2")" "$(host "$3")" ;;
        rm) command rm -f "$(host "$3")" ;;
        *) fail "unexpected sudo $*" ;;
    esac
}
systemctl() { [[ "$*" == "is-active --quiet ssh" ]]; }
CONFIG=/etc/ssh/sshd_config.d/40-swirl-cloud-hardening.conf
SESSIONS=/etc/ssh/sshd_config.d/41-swirl-cloud-sessions.conf

# A valid configuration is written private and SSH reloads.
: > "$LOG"
SSH_ALLOWED_USERS="ubuntu zaid.c deploy@ops"
configure_sshd
config="$(cat "$(host "$CONFIG")")"
for line in 'PermitRootLogin no' 'AllowUsers ubuntu zaid.c deploy@ops' 'DisableForwarding yes' 'AllowTcpForwarding no' \
            'X11Forwarding no' 'MaxAuthTries 3' 'PerSourceMaxStartups 3'; do
    grep -Fqx "$line" <<< "$config" || fail "The SSH configuration lacks: $line"
done
grep -Fqx "write $CONFIG 0600" "$LOG" || fail 'The SSH configuration is not root-only'
grep -Fqx 'systemctl reload ssh' "$LOG" || fail 'SSH is not reloaded'

# Invalid user lists stop before anything is written.
for users in "" "ubuntu," "ubuntu;rm" 'ubuntu $(id)' "a*b"; do
    : > "$LOG"
    SSH_ALLOWED_USERS="$users"
    if (configure_sshd) 2>/dev/null; then fail "SSH_ALLOWED_USERS='$users' is accepted"; fi
    [[ ! -s "$LOG" ]] || fail "SSH_ALLOWED_USERS='$users' writes a configuration"
done

# A configuration sshd rejects is rolled back to the saved copy, without a reload.
SSH_ALLOWED_USERS="ubuntu"
printf 'PreviousConfig yes\n' > "$(host "$CONFIG")"
: > "$LOG"
SSHD_VALID=false
if (configure_sshd) 2>"$STATE/error"; then fail 'A rejected SSH configuration is accepted'; fi
grep -Fq 'failed validation' "$STATE/error" || fail "Unexpected error: $(cat "$STATE/error")"
[[ "$(cat "$(host "$CONFIG")")" == "PreviousConfig yes" ]] || fail 'The previous SSH configuration is not restored'
! grep -q reload "$LOG" || fail 'SSH is reloaded with a rejected configuration'
# ... or removed when there was none.
command rm -f "$(host "$CONFIG")" "$(host "/var/backups/swirl-cloud/config$CONFIG.bak")"
if (configure_sshd) 2>/dev/null; then fail 'A rejected first SSH configuration is accepted'; fi
[[ ! -e "$(host "$CONFIG")" ]] || fail 'A rejected first SSH configuration is left in place'
SSHD_VALID=true

# Session limits: written once, then a rerun with the same content does not reload SSH.
: > "$LOG"
configure_ssh_session_limits
grep -Fqx 'MaxSessions 4' "$(host "$SESSIONS")" || fail 'The session limits are not written'
grep -Fqx 'systemctl reload ssh' "$LOG" || fail 'SSH is not reloaded for new session limits'
: > "$LOG"
configure_ssh_session_limits
! grep -q reload "$LOG" || fail 'An unchanged session drop-in reloads SSH'
printf 'MaxSessions 10\n' > "$(host "$SESSIONS")"
SSHD_VALID=false
if (configure_ssh_session_limits) 2>/dev/null; then fail 'Rejected session limits are accepted'; fi
[[ "$(cat "$(host "$SESSIONS")")" == "MaxSessions 10" ]] || fail 'The previous session limits are not restored'

# The Lynis profile skips only named checks, each with its reason, and states a review date.
configure_lynis_accepted_findings
profile="$(cat "$(host /etc/lynis/custom.prf)")"
grep -Eq '^# Accepted Lynis findings\. Owner: .+\. Review by [0-9]{4}-[0-9]{2}-[0-9]{2}\.$' <<< "$profile" || fail 'The Lynis profile has no owner or review date'
previous=""
while IFS= read -r line; do
    if [[ "$line" == skip-test=* ]]; then
        [[ "$line" =~ ^skip-test=[A-Z]+-[0-9]{4}(:[a-z0-9_.]+)?$ ]] || fail "Malformed Lynis skip: $line"
        [[ "$previous" == "#"* || "$previous" == skip-test=* ]] || fail "Lynis skip without a reason: $line"
    fi
    previous="$line"
done <<< "$profile"
! grep -Eq '^skip-test=(SSH-7408|KRNL-6000)$' <<< "$profile" || fail 'A whole Lynis test is skipped where one value was meant'

printf '[PASS] Host SSH configuration\n'
