#!/usr/bin/env bash
# Guard node enrollment's SSH targets without hosts: a target must reach the
# entered private worker address from the verified control-plane source with
# passwordless sudo, and a Tailscale enrollment passes the one-use auth key and
# the API token on standard input only, removes its remote files, and proves SSH
# over the tailnet before the worker is used.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/network.sh
source "$SCRIPT_DIR/lib/network.sh"
# shellcheck source=../scripts/lib/enrollment-targets.sh
source "$SCRIPT_DIR/lib/enrollment-targets.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
error() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }
sleep() { :; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
ssh_options=(-o BatchMode=yes)
scp_options=(-q)

# build_target
SSH_USER=ubuntu
[[ "$(build_target 10.0.0.5)" == ubuntu@10.0.0.5 ]] || fail 'The SSH user is not added'
[[ "$(build_target root@10.0.0.5)" == root@10.0.0.5 ]] || fail 'An explicit user is replaced'
SSH_USER=""
[[ "$(build_target node-2)" == node-2 ]] || fail 'A target without a user changes'

# check_target: SSH_CONNECTION is "client_ip client_port server_ip server_port".
NODE_NETWORK_CIDR=10.0.0.0/24
CONTROL_PLANE_IP=10.0.0.1
SUDO_OK=true
CONNECTION="10.0.0.1 50000 10.0.0.5 22"
ssh() {
    local command="${*: -1}"
    case "$command" in
        *'sudo -n true'*) [[ "$SUDO_OK" == true ]] ;;
        *SSH_CONNECTION*) printf '%s' "$CONNECTION" ;;
        *) fail "unexpected ssh command: $command" ;;
    esac
}
checked() { (check_target "$@" && printf '%s %s\n' "$TARGET_CONTROL_PLANE_IP" "$TARGET_WORKER_IP") 2>&1; }
[[ "$(checked ubuntu@10.0.0.5 10.0.0.5)" == "10.0.0.1 10.0.0.5" ]] || fail 'A valid private target is refused'
refuse() {
    local message="$1" result
    shift
    if result="$(checked "$@")"; then
        fail "check_target $* was accepted"
    fi
    [[ "$result" == *"$message"* ]] || fail "check_target $* failed with '$result', expected '$message'"
}
refuse "Invalid SSH target" 'ubuntu@10.0.0.5;id' 10.0.0.5
refuse "Invalid SSH target" 'a b' 10.0.0.5
refuse "instead of the entered worker local IP 10.0.0.6" ubuntu@10.0.0.5 10.0.0.6
CONNECTION="203.0.113.9 50000 10.0.0.5 22"
refuse "does not originate from an RFC1918/Tailscale" ubuntu@10.0.0.5 10.0.0.5
CONNECTION="10.0.0.1 50000 198.51.100.7 22"
refuse "reached a non-private worker address" ubuntu@198.51.100.7 198.51.100.7
CONNECTION="10.0.1.1 50000 10.0.0.5 22"
refuse "outside trusted node CIDR" ubuntu@10.0.0.5 10.0.0.5
CONNECTION="10.0.0.2 50000 10.0.0.5 22"
refuse "instead of the verified local control-plane source 10.0.0.1" ubuntu@10.0.0.5 10.0.0.5
CONNECTION="10.0.0.1 50000 10.0.0.5 22"
SUDO_OK=false
refuse "Cannot use passwordless sudo" ubuntu@10.0.0.5 10.0.0.5
SUDO_OK=true

# provision_tailscale_target: every ssh and configurator call is logged with its
# arguments and standard input, so secrets can be looked for in arguments.
LOG="$STATE/log"
: > "$LOG"
TAILSCALE_API_TOKEN="tskey-api-SECRETTOKEN"
AUTH_KEY="tskey-auth-ONEUSEKEY"
TAILSCALE_CONFIGURATOR="$STATE/configure-tailscale.sh"
cat > "$TAILSCALE_CONFIGURATOR" <<EOF
#!/usr/bin/env bash
printf 'configurator %s | stdin=%s\n' "\$*" "\$(cat)" >> "$LOG"
[[ " \$* " == *" --create-auth-key "* ]] && printf '%s\n' "$AUTH_KEY"
exit 0
EOF
chmod +x "$TAILSCALE_CONFIGURATOR"
ENROLLMENT_ROLE=worker TAILSCALE_TAILNET=example.com TAILSCALE_MESH_NAME=swirl TAILSCALE_AUTH_KEY_EXPIRY_SECONDS=600
LOCAL_SUDO=()
REMOTE_FAILS=false
ssh() {
    local target command input=""
    while [[ "$1" == -* ]]; do shift 2; done
    target="$1" command="${*:2}"
    [[ -t 0 ]] || input="$(timeout 1 cat || true)"
    printf 'ssh %s %s | stdin=%s\n' "$target" "$command" "$input" >> "$LOG"
    case "$command" in
        *'sudo -n true'*) return 0 ;;
        'hostname -s'*) printf 'node-2\n' ;;
        mktemp*) printf '/tmp/swirl-cloud-tailscale.abc123\n' ;;
        *'--auth-key-stdin'*) [[ "$REMOTE_FAILS" == false ]] || return 1; printf 'Connected\n100.101.102.103\n' ;;
        *) return 0 ;;
    esac
}
scp() { printf 'scp %s\n' "$*" >> "$LOG"; }
tailscale() { printf 'tailscale %s\n' "$*" >> "$LOG"; }
SSH_USER=ubuntu
( provision_tailscale_target ubuntu@203.0.113.20 </dev/null && printf '%s %s %s\n' "$TAILSCALE_NODE_NAME" "$TAILSCALE_WORKER_IP" "$TAILSCALE_TARGET" ) \
    > "$STATE/result" || fail "Tailscale provisioning failed: $(cat "$LOG")"
[[ "$(cat "$STATE/result")" == "node-2 100.101.102.103 ubuntu@100.101.102.103" ]] || fail "Unexpected result: $(cat "$STATE/result")"
grep -v '| stdin=' "$LOG" | grep -Fq -e "$AUTH_KEY" -e "$TAILSCALE_API_TOKEN" && fail 'A secret appears outside standard input'
while IFS= read -r line; do
    arguments="${line%% | stdin=*}"
    [[ "$arguments" != *"$AUTH_KEY"* && "$arguments" != *"$TAILSCALE_API_TOKEN"* ]] || fail "A secret is passed as an argument: $arguments"
done < "$LOG"
grep -Fq "| stdin=$AUTH_KEY" "$LOG" || fail 'The auth key is not passed on standard input'
grep -Fq -- "--hostname node-2 --auth-key-stdin" "$LOG" || fail 'The detected hostname is not used'
grep -Fq -- "--tag-ip 100.101.102.103 --api-token-stdin | stdin=$TAILSCALE_API_TOKEN" "$LOG" || fail 'The new node is not tagged'
grep -Fq "rm -r -- /tmp/swirl-cloud-tailscale.abc123" "$LOG" || fail 'The remote bootstrap directory is left behind'
grep -Fq "ssh ubuntu@100.101.102.103 true" "$LOG" || fail 'SSH over the tailnet is not proven'

# A failed remote connection removes the remote files and stops.
: > "$LOG"
REMOTE_FAILS=true
if ( provision_tailscale_target ubuntu@203.0.113.20 node-2 </dev/null ) 2> "$STATE/error"; then
    fail 'A failed Tailscale connection is accepted'
fi
grep -Fq "Tailscale provisioning failed on ubuntu@203.0.113.20" "$STATE/error" || fail "Unexpected error: $(cat "$STATE/error")"
grep -Fq "rm -r -- /tmp/swirl-cloud-tailscale.abc123" "$LOG" || fail 'A failed run leaves the remote bootstrap directory'
grep -q -- '--tag-ip' "$LOG" && fail 'A failed node is tagged'
REMOTE_FAILS=false
if ( provision_tailscale_target ubuntu@203.0.113.20 'Bad_Name' </dev/null ) 2> "$STATE/error"; then
    fail 'An invalid Tailscale hostname is accepted'
fi
grep -Fq "Invalid Tailscale hostname" "$STATE/error" || fail "Unexpected error: $(cat "$STATE/error")"

printf '[PASS] Enrollment targets\n'
