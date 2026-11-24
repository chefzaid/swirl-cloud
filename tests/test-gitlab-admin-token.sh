#!/usr/bin/env bash
# Guard the short-lived GitLab administrator token without GitLab: an operator's
# token is used as given, the local GitLab issues a one-day token (by name, for an
# administrator, replacing a previous one) that is revoked by name afterwards,
# a token the operator supplied is never revoked, and a non-interactive run
# without any token stops instead of prompting.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
LOG="$STATE/kubectl"
ISSUED="glpat-0123456789abcdef0123456789abcdef0123456789abcdef"
GITLAB_FOUND=true
RUNNER_OUTPUT="SWIRL_CLOUD_GITLAB_ADMIN_TOKEN=$ISSUED"

session() { # session SCRIPT: run SCRIPT against the library with stubbed kubectl
    bash -c '
        set -euo pipefail
        info() { :; }
        fail() { printf "%s\n" "$*" >&2; exit 1; }
        kubectl() {
            local call="$*"
            printf "%s\n" "${call//$'\''\n'\''/ }" >> "$LOG"
            case "$1" in
                get) [[ "$GITLAB_FOUND" == true ]] ;;
                exec) [[ "$*" == *"set_token"* ]] && printf "%s\n" "noise" "$RUNNER_OUTPUT"; return 0 ;;
            esac
        }
        source "$1/lib/gitlab-admin-token.sh"
        eval "$2"
    ' session "$SCRIPT_DIR" "$1" </dev/null
}
export LOG ISSUED GITLAB_FOUND RUNNER_OUTPUT

# Issued locally, then revoked by name.
: > "$LOG"
result="$(session 'gitlab_acquire_admin_token; printf "%s %s\n" "$GITLAB_ADMIN_TOKEN" "$GITLAB_ADMIN_TOKEN_EPHEMERAL"
                   gitlab_revoke_ephemeral_admin_token; printf "after=%s\n" "${GITLAB_ADMIN_TOKEN-unset}"')"
[[ "$result" == "$ISSUED true"$'\n'"after=unset" ]] || fail "Unexpected session: $result"
issue="$(grep -F 'set_token' "$LOG")"
[[ "$issue" == *"BM_TOKEN_NAME=swirl-cloud-cli-bootstrap"* && "$issue" == *"BM_ADMIN_USERNAME=root"* ]] || fail 'The token is not issued by name'
[[ "$issue" == *'abort("GitLab administrator was not found") unless user&.admin?'* ]] || fail 'A non-administrator may get a token'
[[ "$issue" == *'expires_at: Date.current + 1'* && "$issue" == *'scopes: ["api"]'* ]] || fail 'The token is not a one-day api token'
[[ "$issue" == *'find_each(&:revoke!)'* ]] || fail 'A previous token with the same name is kept'
[[ "$(grep -c 'gitlab-rails' "$LOG")" == 2 ]] || fail 'The issued token is not revoked'
grep -F -v 'set_token' "$LOG" | grep -Fq 'PersonalAccessToken.where(user: user, name: ENV.fetch("BM_TOKEN_NAME"), revoked: false).find_each(&:revoke!)' || \
    fail 'Revocation is not by the token name'
! grep -Fq "$ISSUED" "$LOG" || fail 'The token appears in a command line'

# A token the operator supplied is used as given and never revoked.
: > "$LOG"
result="$(GITLAB_ADMIN_TOKEN=glpat-operator session 'gitlab_acquire_admin_token; gitlab_revoke_ephemeral_admin_token; printf "%s\n" "$GITLAB_ADMIN_TOKEN_EPHEMERAL"')"
[[ "$result" == false && ! -s "$LOG" ]] || fail "An operator token is replaced or revoked: $result $(cat "$LOG")"

# A name passed by the caller (application delivery) is the one issued and revoked.
: > "$LOG"
GITLAB_BOOTSTRAP_TOKEN_NAME=swirl-cloud-application-delivery-abc session 'gitlab_acquire_admin_token; gitlab_revoke_ephemeral_admin_token' >/dev/null
[[ "$(grep -c 'BM_TOKEN_NAME=swirl-cloud-application-delivery-abc' "$LOG")" == 2 ]] || fail 'The caller-chosen token name is not used'

# No local GitLab or no token from it: a non-interactive run stops; nothing to revoke.
for case in "GITLAB_FOUND=false" "RUNNER_OUTPUT=error"; do
    : > "$LOG"
    # shellcheck disable=SC2163 # $case is NAME=value: export assigns it.
    if output="$(export "$case" GITLAB_ADMIN_TOKEN_NONINTERACTIVE=true; session 'gitlab_acquire_admin_token' 2>&1)"; then
        fail "A run without a token succeeds ($case)"
    fi
    [[ "$output" == *"Set GITLAB_ADMIN_TOKEN"* ]] || fail "Unexpected error ($case): $output"
done
: > "$LOG"
session 'gitlab_revoke_ephemeral_admin_token' >/dev/null
[[ ! -s "$LOG" ]] || fail 'A session without an issued token revokes one'

printf '[PASS] GitLab administrator token\n'
