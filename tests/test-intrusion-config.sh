#!/usr/bin/env bash
# Guard the CrowdSec and Fail2ban configuration without a host: the bouncer key
# reaches the local API on standard input only, a saved key is reused only when
# the API accepts it (the package's placeholder is not), a new bouncer is
# registered otherwise, the bouncer drops banned sources on input and forward
# for IPv4 and IPv6, and Fail2ban watches the configured SSH port.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/intrusion-config.sh
source "$SCRIPT_DIR/lib/intrusion-config.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
err() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }
sleep() { :; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
LOG="$STATE/log"
backup_file_if_exists() { :; }
write_root_file() { cat > "$STATE/${1//\//_}"; }
written() { cat "$STATE/${1//\//_}"; }

# The local API answers with the queued codes in turn (000: not up yet); the
# header arrives on stdin. curl runs in a subshell, so the queue is a file.
QUEUE="$STATE/codes"
codes() { printf '%s\n' "$@" > "$QUEUE"; }
curl() {
    local header code
    header="$(cat)"
    printf 'curl %s | stdin=%s\n' "$*" "$header" >> "$LOG"
    code="$(head -n 1 "$QUEUE")"
    sed -i 1d "$QUEUE"
    printf '%s' "${code:-000}"
}
SAVED_KEY=""
sudo() {
    case "$1" in
        test) [[ -n "$SAVED_KEY" ]] ;;
        awk) printf '%s\n' "$SAVED_KEY" ;;
        cscli) printf 'cscli %s\n' "$*" >> "$LOG"; printf 'new-bouncer-key\n' ;;
        *) "$@" ;;
    esac
}
hostname() { printf 'node-1\n'; }

: > "$LOG"
codes 000 000 200
crowdsec_bouncer_key_valid key-1 || fail 'A key accepted after the API starts is refused'
[[ "$(grep -c '^curl' "$LOG")" == 3 ]] || fail 'The check does not wait for the API to start'
grep -Fq '| stdin=X-Api-Key: key-1' "$LOG" || fail 'The key is not sent on standard input'
! grep -v '| stdin=' "$LOG" | grep -Fq key-1 || fail 'The key is on the command line'
grep -Fq 'curl -s -o /dev/null -w %{http_code} -H @- http://127.0.0.1:8080/v1/decisions' "$LOG" || fail 'The key is not checked locally'
codes 403
if crowdsec_bouncer_key_valid key-1; then fail 'A rejected key is accepted'; fi
codes
if crowdsec_bouncer_key_valid key-1; then fail 'A key is accepted while the API never answers'; fi

# A saved key the API accepts is reused; a placeholder or a rejected key is replaced.
: > "$LOG"
SAVED_KEY=saved-key
codes 200
[[ "$(get_crowdsec_bouncer_api_key)" == saved-key ]] || fail 'A valid saved key is replaced'
! grep -q cscli "$LOG" || fail 'A bouncer is registered although the saved key works'
SAVED_KEY='<API_KEY>'
codes 401
: > "$LOG"
[[ "$(get_crowdsec_bouncer_api_key)" == new-bouncer-key ]] || fail 'The placeholder key is kept'
grep -Eq 'cscli bouncers add swirl-cloud-node-1-firewall-bouncer-[0-9]+ -o raw' "$LOG" || fail 'The new bouncer is not named for the host'
SAVED_KEY=""
[[ "$(get_crowdsec_bouncer_api_key)" == new-bouncer-key ]] || fail 'No bouncer is registered on a fresh host'
sudo() { case "$1" in test) return 1 ;; cscli) return 1 ;; *) "$@" ;; esac; }
if (get_crowdsec_bouncer_api_key) 2>/dev/null; then fail 'A missing key is accepted'; fi

# The bouncer configuration.
write_crowdsec_bouncer_config bouncer-key
config="$(written /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml)"
for line in 'api_key: bouncer-key' 'api_url: http://127.0.0.1:8080/' 'mode: nftables' 'deny_action: DROP' 'disable_ipv6: false' \
            '  - input' '  - forward' 'insecure_skip_verify: false'; do
    grep -Fqx -- "$line" <<< "$config" || fail "The bouncer configuration lacks: $line"
done
python3 -c 'import sys, yaml; c = yaml.safe_load(sys.stdin); assert c["nftables"]["ipv4"]["enabled"] and c["nftables"]["ipv6"]["enabled"]' \
    <<< "$config" || fail 'The bouncer configuration is not valid YAML with both address families'

# Fail2ban follows the hardened SSH port.
HARDENED_SSH_PORT=2222
write_fail2ban_config
jail="$(written /etc/fail2ban/jail.local)"
for line in 'port = 2222' 'enabled = true' 'banaction = ufw' 'maxretry = 3' 'bantime.increment = true'; do
    grep -Fqx "$line" <<< "$jail" || fail "The Fail2ban jail lacks: $line"
done

# Log acquisition replaces the package's single file with the managed ones.
write_crowdsec_acquisitions
grep -Fqx '  - /var/log/auth.log' <<< "$(written /etc/crowdsec/acquis.d/setup.sshd.yaml)" || fail 'SSH logs are not acquired'
grep -Fqx '  - /var/log/syslog' <<< "$(written /etc/crowdsec/acquis.d/setup.linux.yaml)" || fail 'System logs are not acquired'

printf '[PASS] Intrusion prevention configuration\n'
