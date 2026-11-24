#!/usr/bin/env bash
# Guard the Cloudflare API helpers without Cloudflare: the token stays in the
# curl config file, a failed answer stops with Cloudflare's own messages, a zone
# setting is written only when it differs, and a ruleset rule is created,
# updated by its id, left alone when unchanged, and never guessed among
# duplicates.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/cloudflare-api.sh
source "$SCRIPT_DIR/lib/cloudflare-api.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
error() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
API_BASE="https://api.cloudflare.com/client/v4"
CURL_CONFIG="$WORK_DIR/curl.conf"
ZONE_ID=zone1
LOG="$WORK_DIR/requests"
declare -A ANSWERS=()
NO_ROUTE='{"success":false,"errors":[{"code":7003,"message":"No route"}]}'
# Answers are keyed by "METHOD path"; every request is logged with its body.
curl() {
    local method="" url="" data="" previous=""
    for argument in "$@"; do
        case "$previous" in
            --request) method="$argument" ;;
            --url) url="${argument#"$API_BASE"}" ;;
            --data-binary) data="$(jq -c . "${argument#@}")" ;;
        esac
        previous="$argument"
    done
    printf '%s %s %s | %s\n' "$method" "$url" "$data" "$*" >> "$LOG"
    printf '%s' "${ANSWERS["$method $url"]:-$NO_ROUTE}"
}
run() { : > "$LOG"; ( "$@" ) 2>&1; }
requests() { cut -d'|' -f1 "$LOG" | sed 's/ *$//'; }

# The token is never on the command line: only the config file is passed.
ANSWERS["GET /zones/zone1/settings/ssl"]='{"success":true,"result":{"value":"strict"}}'
run set_zone_setting ssl strict "SSL mode" >/dev/null
grep -Fq -- "--config $CURL_CONFIG" "$LOG" || fail 'The curl config is not used'
! grep -qi 'authorization\|bearer' "$LOG" || fail 'A credential is passed on the command line'
[[ "$(requests)" == "GET /zones/zone1/settings/ssl" ]] || fail "An unchanged setting is written: $(requests)"

ANSWERS["GET /zones/zone1/settings/min_tls_version"]='{"success":true,"result":{"value":"1.0"}}'
ANSWERS["PATCH /zones/zone1/settings/min_tls_version"]='{"success":true,"result":{}}'
run set_zone_setting min_tls_version 1.2 "Minimum TLS" >/dev/null
[[ "$(requests | tail -n 1)" == 'PATCH /zones/zone1/settings/min_tls_version {"value":"1.2"}' ]] || fail "The setting is not written: $(requests)"

ANSWERS["GET /zones/zone1/settings/http3"]='{"success":false,"errors":[{"code":9109,"message":"Invalid access token"}],"messages":[]}'
output="$(run set_zone_setting http3 on "HTTP/3")" && fail 'A failed read is accepted'
[[ "$output" == "Reading HTTP/3 failed: Invalid access token" ]] || fail "Unexpected error: $output"
grep -q PATCH "$LOG" && fail 'A setting is written after a failed read'

# Ruleset rules.
RULE="$WORK_DIR/rule.json"
printf '%s\n' '{"description":"Block admin","expression":"(http.request.uri.path eq \"/admin\")","action":"block","enabled":true}' > "$RULE"
entrypoint() { # entrypoint RULES-JSON
    ANSWERS["GET /zones/zone1/rulesets/phases/http_request_firewall_custom/entrypoint"]="{\"success\":true,\"result\":{\"id\":\"rs1\",\"rules\":$1}}"
}
reconcile() { run reconcile_ruleset_rule http_request_firewall_custom "Platform rules" "Block admin" "$RULE"; }

ANSWERS["GET /zones/zone1/rulesets/phases/http_request_firewall_custom/entrypoint"]='{"success":false,"errors":[{"code":10003,"message":"not found"}]}'
ANSWERS["POST /zones/zone1/rulesets"]='{"success":true,"result":{}}'
reconcile >/dev/null || fail 'A missing entrypoint ruleset is not created'
created="$(requests | tail -n 1)"
[[ "$created" == "POST /zones/zone1/rulesets "* ]] || fail "No ruleset is created: $(requests)"
jq -e '.name == "Platform rules" and .kind == "zone" and .phase == "http_request_firewall_custom" and
       (.rules | length) == 1 and .rules[0].description == "Block admin"' <<< "${created#POST /zones/zone1/rulesets }" >/dev/null || \
    fail "The ruleset is not created with the rule: $created"

entrypoint '[{"id":"r0","description":"Other rule","expression":"true","action":"log"}]'
ANSWERS["POST /zones/zone1/rulesets/rs1/rules"]='{"success":true,"result":{}}'
reconcile >/dev/null || fail 'A missing rule is not added'
[[ "$(requests | tail -n 1 | cut -d' ' -f1-2)" == "POST /zones/zone1/rulesets/rs1/rules" ]] || fail "The rule is not added: $(requests)"

entrypoint '[{"id":"r1","version":"3","last_updated":"x","description":"Block admin","expression":"(http.request.uri.path eq \"/admin\")","action":"block","enabled":true}]'
reconcile >/dev/null || fail 'An unchanged rule fails'
[[ "$(requests | wc -l)" == 1 ]] || fail "An unchanged rule is written: $(requests)"

entrypoint '[{"id":"r1","description":"Block admin","expression":"(http.request.uri.path eq \"/old\")","action":"block","enabled":true}]'
ANSWERS["PATCH /zones/zone1/rulesets/rs1/rules/r1"]='{"success":true,"result":{}}'
reconcile >/dev/null || fail 'A changed rule is not updated'
[[ "$(requests | tail -n 1 | cut -d' ' -f1-2)" == "PATCH /zones/zone1/rulesets/rs1/rules/r1" ]] || fail "The rule is not updated by id: $(requests)"

entrypoint '[{"id":"r1","description":"Block admin","expression":"a","action":"block"},{"id":"r2","description":"Block admin","expression":"b","action":"block"}]'
output="$(reconcile)" && fail 'Duplicate rules are accepted'
[[ "$output" == "More than one Cloudflare rule is named: Block admin" ]] || fail "Unexpected error: $output"
[[ "$(requests | wc -l)" == 1 ]] || fail 'A write follows duplicate rules'

ANSWERS["GET /zones/zone1/rulesets/phases/http_request_firewall_custom/entrypoint"]='{"success":false,"errors":[{"code":10000,"message":"Authentication error"}]}'
output="$(reconcile)" && fail 'A failed ruleset read is accepted'
[[ "$output" == "Reading Platform rules failed: Authentication error" ]] || fail "Unexpected error: $output"

printf '[PASS] Cloudflare API helpers\n'
