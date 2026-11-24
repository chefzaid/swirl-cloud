#!/usr/bin/env bash
# Guard the Cloudflare Access setup without Cloudflare: it creates the Keycloak
# OIDC provider (PKCE, the managed client), one reusable email policy that also
# requires that provider, and one self-hosted application per configured admin
# hostname; a rerun on a correct account changes nothing but the provider's
# secret; and it never takes over an unrelated provider, guesses among
# duplicates, protects a hostname it does not publish, or runs without the
# client secret.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/cloudflare-api.sh
source "$SCRIPT_DIR/lib/cloudflare-api.sh"
# shellcheck source=../scripts/lib/cloudflare-access.sh
source "$SCRIPT_DIR/lib/cloudflare-access.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
error() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }
step() { :; }
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
API_BASE="https://api.cloudflare.com/client/v4"
CURL_CONFIG="$WORK_DIR/curl.conf"
LOG="$WORK_DIR/requests"
NO_ROUTE='{"success":false,"errors":[{"code":7003,"message":"No route"}]}'
declare -A ANSWERS=()
curl() {
    local method="" url="" data="" previous="" argument
    for argument in "$@"; do
        case "$previous" in
            --request) method="$argument" ;;
            --url) url="${argument#"$API_BASE"}" ;;
            --data-binary) data="$(jq -c . "${argument#@}")" ;;
        esac
        previous="$argument"
    done
    printf '%s %s\t%s\n' "$method" "$url" "$data" >> "$LOG"
    printf '%s' "${ANSWERS["$method $url"]:-$NO_ROUTE}"
}
kubectl() { [[ -n "$STORED_SECRET" ]] && printf '%s' "$STORED_SECRET" | base64; }
A=/accounts/acct1/access
ZONE_NAME=example.com KEYCLOAK_REALM=staff ACCOUNT_ID=acct1 INGRESS_NAMESPACE=infra
ACCESS_SESSION_DURATION=8h CLOUDFLARE_ACCESS_IDP_NAME="Example Keycloak"
AUTH_URL="https://keycloak.example.com/auth/realms/staff/protocol/openid-connect/auth"

fresh() {
    ANSWERS=()
    ANSWERS["GET $A/organizations"]='{"success":false,"errors":[{"code":12130,"message":"not found"}]}'
    ANSWERS["POST $A/organizations"]='{"success":true,"result":{"name":"Swirl Cloud","auth_domain":"acme.cloudflareaccess.com","auto_redirect_to_identity":true}}'
    ANSWERS["GET $A/identity_providers"]='{"success":true,"result":[]}'
    ANSWERS["POST $A/identity_providers"]='{"success":true,"result":{"id":"idp1"}}'
    ANSWERS["GET $A/policies?per_page=100"]='{"success":true,"result":[]}'
    ANSWERS["POST $A/policies"]='{"success":true,"result":{"id":"pol1"}}'
    ANSWERS["GET $A/apps?per_page=100"]='{"success":true,"result":[]}'
    ANSWERS["POST $A/apps"]='{"success":true,"result":{}}'
}
run() {
    : > "$LOG"
    (
        ENABLE_ACCESS=true ACCESS_OIDC_CLIENT_SECRET="" STORED_SECRET="client-secret" ACCESS_TEAM_NAME=acme
        ACCESS_TEAM_NAME_EXPLICIT=false CLOUDFLARE_ACCESS_HOST_LABELS="" HOST_LABELS=(cloud grafana argocd gitlab)
        DEFAULT_ACCESS_HOST_LABELS=(grafana argocd) EMAILS="admin@example.com ops@example.com"
        local assignment
        for assignment in "$@"; do
            declare "$assignment"
        done
        read -r -a ACCESS_EMAILS <<< "$EMAILS"
        configure_access
    ) 2>&1
}
body() { grep -F "$1"$'\t' "$LOG" | head -n 1 | cut -f2; }
writes() { cut -f1 "$LOG" | grep -v '^GET ' || true; }

# A fresh account: organization, provider, policy and one application per label.
fresh
run >/dev/null || fail "A fresh account fails: $(run)"
jq -e '.auth_domain == "acme.cloudflareaccess.com" and .auto_redirect_to_identity == true' <<< "$(body "POST $A/organizations")" \
    >/dev/null || fail 'The organization is not created for the team'
idp="$(body "POST $A/identity_providers")"
jq -e --arg url "$AUTH_URL" '.type == "oidc" and .config.client_id == "cloudflare-access" and .config.client_secret == "client-secret"
    and .config.auth_url == $url and .config.pkce_enabled == true and (.config.scopes | index("groups"))' <<< "$idp" >/dev/null || \
    fail "The identity provider is not the managed Keycloak client: $idp"
policy="$(body "POST $A/policies")"
jq -e '.decision == "allow" and .include == [{"email":{"email":"admin@example.com"}},{"email":{"email":"ops@example.com"}}]
    and .require == [{"login_method":{"id":"idp1"}}] and .session_duration == "8h"' <<< "$policy" >/dev/null || \
    fail "The policy does not require the Keycloak login: $policy"
[[ "$(grep -c "^POST $A/apps" "$LOG")" == 2 ]] || fail "Not one application per default label: $(writes)"
app="$(grep -F "POST $A/apps"$'\t' "$LOG" | cut -f2 | jq -s 'map(select(.domain == "grafana.example.com"))[0]')"
jq -e '.name == "Swirl Cloud - Grafana" and .type == "self_hosted" and .allowed_idps == ["idp1"]
    and .policies == [{"id":"pol1","precedence":1}] and .http_only_cookie_attribute == true' <<< "$app" >/dev/null || \
    fail "The Grafana application is not protected by the policy: $app"

# A correct account: only the provider is rewritten (its secret cannot be read back).
correct_apps="$(grep -F "POST $A/apps"$'\t' "$LOG" | cut -f2 | jq -sc 'map(. + {id: ("app-" + .domain), policies: (.policies | map(. + {name: "x"}))})')"
ANSWERS["GET $A/organizations"]='{"success":true,"result":{"name":"Swirl Cloud","auth_domain":"acme.cloudflareaccess.com","auto_redirect_to_identity":true}}'
ANSWERS["GET $A/identity_providers"]="{\"success\":true,\"result\":[{\"id\":\"idp1\",\"name\":\"Renamed\",\"type\":\"oidc\",\"config\":{\"client_id\":\"cloudflare-access\",\"auth_url\":\"$AUTH_URL\"}}]}"
ANSWERS["PUT $A/identity_providers/idp1"]='{"success":true,"result":{"id":"idp1"}}'
ANSWERS["GET $A/policies?per_page=100"]="{\"success\":true,\"result\":[$(jq -c '. + {id: "pol1", reusable: true}' <<< "$policy")]}"
ANSWERS["GET $A/apps?per_page=100"]="{\"success\":true,\"result\":$correct_apps}"
run >/dev/null || fail "A correct account fails: $(run)"
[[ "$(writes)" == "PUT $A/identity_providers/idp1" ]] || fail "A correct account is changed: $(writes)"

# A changed allowlist updates the policy in place; an explicit label list is used.
ANSWERS["PUT $A/policies/pol1"]='{"success":true,"result":{}}'
run EMAILS=admin@example.com >/dev/null || fail "A changed allowlist fails"
[[ "$(writes)" == *"PUT $A/policies/pol1"* ]] || fail "The policy is not updated in place: $(writes)"
jq -e '.include == [{"email":{"email":"admin@example.com"}}]' <<< "$(body "PUT $A/policies/pol1")" >/dev/null || \
    fail "The policy does not carry the new allowlist: $(body "PUT $A/policies/pol1")"
ANSWERS["POST $A/apps"]='{"success":true,"result":{}}'
run CLOUDFLARE_ACCESS_HOST_LABELS="cloud" >/dev/null || fail 'An explicit label list fails'
jq -e '.name == "Swirl Cloud - Portal" and .domain == "cloud.example.com"' <<< "$(body "POST $A/apps")" >/dev/null || \
    fail 'The explicit label is not protected'

# Refusals.
refuse() {
    local message="$1" output
    shift
    if output="$(run "$@")"; then fail "configure_access $* was accepted"; fi
    [[ "$output" == *"$message"* ]] || fail "configure_access $* failed with '$output', expected '$message'"
}
refuse "is not present in CLOUDFLARE_HOST_LABELS" CLOUDFLARE_ACCESS_HOST_LABELS="vault"
refuse "Invalid Access hostname label" CLOUDFLARE_ACCESS_HOST_LABELS="Bad_Label"
refuse "set CLOUDFLARE_ACCESS_TEAM_NAME to its first label" ACCESS_TEAM_NAME=other ACCESS_TEAM_NAME_EXPLICIT=true
refuse "Keycloak SSO credentials are not ready" STORED_SECRET=""
[[ "$(writes)" == "" ]] || fail 'A write happens without the client secret'
saved_idps="${ANSWERS["GET $A/identity_providers"]}"
ANSWERS["GET $A/identity_providers"]='{"success":true,"result":[{"id":"x","name":"Example Keycloak","type":"saml","config":{}}]}'
refuse "An unrelated Access identity provider already uses the requested display name"
ANSWERS["GET $A/identity_providers"]="{\"success\":true,\"result\":[{\"id\":\"a\",\"name\":\"Example Keycloak\",\"type\":\"oidc\",\"config\":{}},{\"id\":\"b\",\"name\":\"Old\",\"type\":\"oidc\",\"config\":{\"client_id\":\"cloudflare-access\",\"auth_url\":\"$AUTH_URL\"}}]}"
refuse "More than one Access identity provider matches"
ANSWERS["GET $A/identity_providers"]="$saved_idps"
ANSWERS["GET $A/apps?per_page=100"]="{\"success\":true,\"result\":$(jq -c '. + .' <<< "$correct_apps")}"
refuse "More than one Access application targets"
output="$(run ENABLE_ACCESS=false)" || fail 'Disabled Access fails'
[[ ! -s "$LOG" ]] || fail 'Disabled Access calls Cloudflare'
[[ "$(access_app_name kafka)" == "Kafka UI" && "$(access_app_name custom)" == custom ]] || fail 'Application names'

printf '[PASS] Cloudflare Access\n'
