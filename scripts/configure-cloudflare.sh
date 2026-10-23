#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_CONFIG="$SCRIPT_DIR/../config/platform.env"
if [[ ! -r "$PLATFORM_CONFIG" ]]; then
    echo "[ERROR] Shared platform contract not found: $PLATFORM_CONFIG" >&2
    exit 1
fi
# shellcheck source=../config/platform.env
source "$PLATFORM_CONFIG"
NETWORK_LIBRARY="$SCRIPT_DIR/lib/network.sh"
if [[ ! -r "$NETWORK_LIBRARY" ]]; then
    echo "[ERROR] Shared network library not found: $NETWORK_LIBRARY" >&2
    exit 1
fi
# shellcheck source=lib/network.sh
source "$NETWORK_LIBRARY"

API_BASE="${CLOUDFLARE_API_BASE:-https://api.cloudflare.com/client/v4}"
PLATFORM_DOMAIN="${PLATFORM_DOMAIN:-${DEFAULT_PLATFORM_DOMAIN:-}}"
INTERNAL_DNS_ZONE="${INTERNAL_DNS_ZONE:-${DEFAULT_INTERNAL_DNS_ZONE:-}}"
ZONE_NAME="${CLOUDFLARE_ZONE:-${DEFAULT_CLOUDFLARE_ZONE:-$PLATFORM_DOMAIN}}"
ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-}"
ORIGIN_IP="${CLOUDFLARE_ORIGIN_IP:-}"
API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
NODE_DNS_LABEL="${CLOUDFLARE_NODE_DNS_LABEL:-$DEFAULT_CLOUDFLARE_NODE_DNS_LABEL}"
PUBLISH_NODE_DNS="${CLOUDFLARE_PUBLISH_NODE_DNS:-true}"
INGRESS_NAMESPACE="${INGRESS_NAMESPACE:-infra}"
INGRESS_SERVICE="${INGRESS_SERVICE:-traefik}"

MIN_TLS_VERSION="${CLOUDFLARE_MIN_TLS_VERSION:-1.2}"
ENABLE_HSTS="${CLOUDFLARE_ENABLE_HSTS:-true}"
HSTS_MAX_AGE="${CLOUDFLARE_HSTS_MAX_AGE:-$DEFAULT_CLOUDFLARE_HSTS_MAX_AGE}"
ENABLE_DNSSEC="${CLOUDFLARE_ENABLE_DNSSEC:-true}"
LOCK_ORIGIN="${CLOUDFLARE_LOCK_ORIGIN:-true}"
ENABLE_WAF="${CLOUDFLARE_ENABLE_WAF:-true}"
ENABLE_RATE_LIMIT="${CLOUDFLARE_ENABLE_RATE_LIMIT:-true}"
ENABLE_CACHE_RULES="${CLOUDFLARE_ENABLE_CACHE_RULES:-true}"
ENABLE_REGISTRY_API_COMPATIBILITY="${CLOUDFLARE_ENABLE_REGISTRY_API_COMPATIBILITY:-true}"
ENABLE_ACCESS="${CLOUDFLARE_ENABLE_ACCESS:-true}"
PUBLISH_APEX="${CLOUDFLARE_PUBLISH_APEX:-false}"
ACCESS_ALLOWED_EMAILS="${CLOUDFLARE_ACCESS_ALLOWED_EMAILS:-}"
ACCESS_SESSION_DURATION="${CLOUDFLARE_ACCESS_SESSION_DURATION:-24h}"
ACCESS_TEAM_NAME="${CLOUDFLARE_ACCESS_TEAM_NAME:-${DEFAULT_CLOUDFLARE_ACCESS_TEAM_NAME:-}}"
ACCESS_TEAM_NAME_EXPLICIT=false
[[ -z "$ACCESS_TEAM_NAME" ]] || ACCESS_TEAM_NAME_EXPLICIT=true
ACCESS_OIDC_CLIENT_SECRET="${CLOUDFLARE_ACCESS_OIDC_CLIENT_SECRET:-}"
INGRESS_MODE="${CLOUDFLARE_INGRESS_MODE:-}"
TUNNEL_ID=""
DNS_RECORD_TYPE=A
DNS_RECORD_CONTENT=""
ORIGIN_LOCK_STATUS="not requested"
REGISTRY_BOT_STATUS="not requested"
WORK_DIR=""
CURL_CONFIG=""

IFS=',' read -r -a DEFAULT_HOST_LABELS <<< "$DEFAULT_CLOUDFLARE_HOST_LABELS"

# Browser admin UIs sit behind Access. Cluster automation uses service DNS, so
# it never needs an Access bypass on these public hostnames.
IFS=',' read -r -a DEFAULT_ACCESS_HOST_LABELS <<< "$DEFAULT_CLOUDFLARE_ACCESS_HOST_LABELS"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*" >&2; }
step()  { echo -e "${BLUE}[STEP]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: scripts/configure-cloudflare.sh [options]

Configures a fresh or existing Cloudflare account for the cluster. The script
creates or finds the zone, reconciles proxied DNS records, installs a wildcard
Cloudflare Origin CA certificate in Kubernetes, and applies a secure/performance
baseline, scoped WAF/cache rules, and Keycloak-backed Access for administrative UIs.

Options:
  --zone DOMAIN       Cloudflare zone (default: config/platform.env)
  --account-id ID     Cloudflare account ID; discovered when omitted
  --origin-ip IP      Traefik public IPv4; discovered from Kubernetes when omitted
  -h, --help          Show this help

Secret input:
  The script prompts for a Cloudflare User API Token (current cfut_... type).
  CLOUDFLARE_API_TOKEN may be used for non-interactive automation.

Optional environment variables:
  CLOUDFLARE_HOST_LABELS   Space-separated labels replacing the default list
  CLOUDFLARE_TLS_SECRET_NAME
  CLOUDFLARE_MIN_TLS_VERSION       Default: 1.2
  CLOUDFLARE_ENABLE_HSTS           Default: true; configure-ingress.sh repeats HSTS at the origin
  CLOUDFLARE_HSTS_MAX_AGE          Seconds (default: config/platform.env)
  CLOUDFLARE_ENABLE_DNSSEC         Default: true
  CLOUDFLARE_LOCK_ORIGIN           Allow ports 80/443 only from Cloudflare (default: true)
  CLOUDFLARE_ENABLE_WAF            Default: true
  CLOUDFLARE_ENABLE_RATE_LIMIT     Default: true
  CLOUDFLARE_ENABLE_CACHE_RULES    Default: true
  CLOUDFLARE_ENABLE_REGISTRY_API_COMPATIBILITY
                                      Prevent browser-only bot challenges on the Registry (default: true)
  CLOUDFLARE_ENABLE_ACCESS         Default: true
  CLOUDFLARE_PUBLISH_APEX          Also manage zone-apex DNS (default: false; application-owned)
  CLOUDFLARE_PUBLISH_NODE_DNS      Publish NODE.DOMAIN unproxied for administration (default: true)
  CLOUDFLARE_NODE_DNS_LABEL        Public node label (default: config/platform.env)
  CLOUDFLARE_ACCESS_ALLOWED_EMAILS Space-separated Access email allowlist
  CLOUDFLARE_ACCESS_SESSION_DURATION  Default: 24h
  CLOUDFLARE_ACCESS_HOST_LABELS    Space-separated admin labels replacing defaults
  CLOUDFLARE_ACCESS_TEAM_NAME      Used only when creating a Zero Trust organization
  INGRESS_NAMESPACE
  INGRESS_SERVICE
  CLOUDFLARE_INGRESS_MODE         direct or tunnel; defaults to persisted cluster mode
EOF
}

cleanup() {
    API_TOKEN=""
    unset CLOUDFLARE_API_TOKEN || true
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        find "$WORK_DIR" -type f -delete 2>/dev/null || true
        find "$WORK_DIR" -depth -type d -empty -delete 2>/dev/null || true
    fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --zone)
            [[ $# -ge 2 ]] || error "--zone requires a value."
            ZONE_NAME="$2"
            shift 2
            ;;
        --account-id)
            [[ $# -ge 2 ]] || error "--account-id requires a value."
            ACCOUNT_ID="$2"
            shift 2
            ;;
        --origin-ip)
            [[ $# -ge 2 ]] || error "--origin-ip requires a value."
            ORIGIN_IP="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            ;;
    esac
done

ZONE_NAME="${ZONE_NAME,,}"
PLATFORM_DOMAIN="${PLATFORM_DOMAIN:-$ZONE_NAME}"
# shellcheck source=lib/platform-identity.sh
source "$SCRIPT_DIR/lib/platform-identity.sh"
platform_identity_load
ZONE_NAME="${ZONE_NAME:-$PLATFORM_DOMAIN}"
if [[ -z "$ACCESS_TEAM_NAME" && -n "${CLOUDFLARE_ACCESS_TEAM_NAME:-}" ]]; then
    ACCESS_TEAM_NAME="$CLOUDFLARE_ACCESS_TEAM_NAME"
    ACCESS_TEAM_NAME_EXPLICIT=true
fi
platform_identity_defaults
platform_identity_validate
ACCESS_TEAM_NAME="${ACCESS_TEAM_NAME:-swirl-cloud-${ZONE_NAME//./-}}"
[[ -n "$ZONE_NAME" ]] || error "Set PLATFORM_DOMAIN or CLOUDFLARE_ZONE."
[[ -z "$INTERNAL_DNS_ZONE" || "$ZONE_NAME" != "$INTERNAL_DNS_ZONE" ]] || \
    error "$ZONE_NAME is the cluster-only CoreDNS zone and must never be published through Cloudflare."
[[ "$ZONE_NAME" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$ ]] || \
    error "Invalid zone name: $ZONE_NAME"
[[ -z "$ACCOUNT_ID" || "$ACCOUNT_ID" =~ ^[a-f0-9]{32}$ ]] || \
    error "Cloudflare account IDs must contain 32 lowercase hexadecimal characters."
[[ "$MIN_TLS_VERSION" =~ ^1\.[23]$ ]] || error "CLOUDFLARE_MIN_TLS_VERSION must be 1.2 or 1.3."
[[ "$ENABLE_HSTS" =~ ^(true|false)$ ]] || error "CLOUDFLARE_ENABLE_HSTS must be true or false."
[[ "$ENABLE_DNSSEC" =~ ^(true|false)$ ]] || error "CLOUDFLARE_ENABLE_DNSSEC must be true or false."
[[ "$LOCK_ORIGIN" =~ ^(true|false)$ ]] || error "CLOUDFLARE_LOCK_ORIGIN must be true or false."
[[ "$ENABLE_WAF" =~ ^(true|false)$ ]] || error "CLOUDFLARE_ENABLE_WAF must be true or false."
[[ "$ENABLE_RATE_LIMIT" =~ ^(true|false)$ ]] || error "CLOUDFLARE_ENABLE_RATE_LIMIT must be true or false."
[[ "$ENABLE_CACHE_RULES" =~ ^(true|false)$ ]] || error "CLOUDFLARE_ENABLE_CACHE_RULES must be true or false."
[[ "$ENABLE_REGISTRY_API_COMPATIBILITY" =~ ^(true|false)$ ]] || \
    error "CLOUDFLARE_ENABLE_REGISTRY_API_COMPATIBILITY must be true or false."
[[ "$ENABLE_ACCESS" =~ ^(true|false)$ ]] || error "CLOUDFLARE_ENABLE_ACCESS must be true or false."
[[ "$PUBLISH_APEX" =~ ^(true|false)$ ]] || error "CLOUDFLARE_PUBLISH_APEX must be true or false."
[[ "$PUBLISH_NODE_DNS" =~ ^(true|false)$ ]] || error "CLOUDFLARE_PUBLISH_NODE_DNS must be true or false."
if [[ "$PUBLISH_NODE_DNS" == "true" ]]; then
    [[ "$NODE_DNS_LABEL" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || \
        error "Set CLOUDFLARE_NODE_DNS_LABEL to a single valid DNS label when node DNS publishing is enabled."
fi
[[ "$HSTS_MAX_AGE" =~ ^[0-9]+$ ]] || error "CLOUDFLARE_HSTS_MAX_AGE must be seconds as a whole number."
[[ "$ACCESS_SESSION_DURATION" =~ ^[1-9][0-9]*(m|h)$ ]] || \
    error "CLOUDFLARE_ACCESS_SESSION_DURATION must be a positive duration such as 30m or 24h."
[[ "$ACCESS_TEAM_NAME" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || \
    error "CLOUDFLARE_ACCESS_TEAM_NAME must contain lowercase letters, numbers, and hyphens."

for command_name in curl jq openssl kubectl base64 dig; do
    command -v "$command_name" >/dev/null 2>&1 || error "Required command not found: $command_name"
done
kubectl cluster-info >/dev/null 2>&1 || error "Cannot reach the Kubernetes cluster."

if [[ -z "$API_TOKEN" ]]; then
    cat >&2 <<'EOF'

Cloudflare credential required:
  Create it at: https://dash.cloudflare.com/profile/api-tokens
  Type: Cloudflare User API Token (current cfut_... format)
  Do not use: Account API Token (cfat_...) or Global API Key (cfk_...)

The custom user token must allow:
  User    -> Memberships              -> Read
  Zone    -> Zone                     -> Read
  Zone    -> Zone                     -> Edit
  Zone    -> DNS                      -> Edit
  Zone    -> Zone Settings            -> Edit
  Zone    -> SSL and Certificates     -> Edit
  Zone    -> WAF                      -> Edit
  Zone    -> Cache Rules              -> Edit
  Zone    -> Transform Rules          -> Edit
  Zone    -> Bot Management           -> Read
  Zone    -> Bot Management           -> Edit
  Account -> Access: Apps and Policies -> Edit
  Account -> Access: Organizations, Identity Providers, and Groups -> Edit
  Account -> Cloudflare Registrar Domains -> Read

For a blank account, scope the zone permissions to all zones in the selected
account; a token restricted to a specific existing zone cannot create the zone.
EOF
    read -rsp "Cloudflare User API Token (cfut_...): " API_TOKEN
    echo >&2
fi

case "$API_TOKEN" in
    cfut_*) ;;
    cfat_*) error "An Account API Token (cfat_...) was supplied; a User API Token (cfut_...) is required." ;;
    cfk_*)  error "A Global API Key (cfk_...) was supplied; a scoped User API Token (cfut_...) is required." ;;
    *)      error "Expected a current Cloudflare User API Token beginning with cfut_." ;;
esac

if [[ "$ENABLE_ACCESS" == "true" && -z "$ACCESS_ALLOWED_EMAILS" ]]; then
    read -rp "Email address(es) allowed into Swirl Cloud admin UIs (space-separated): " ACCESS_ALLOWED_EMAILS
fi
if [[ "$ENABLE_ACCESS" == "true" ]]; then
    read -r -a ACCESS_EMAILS <<< "$ACCESS_ALLOWED_EMAILS"
    (( ${#ACCESS_EMAILS[@]} > 0 )) || error "At least one Access email address is required."
    for access_email in "${ACCESS_EMAILS[@]}"; do
        [[ "$access_email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || \
            error "Invalid Access email address: $access_email"
    done
fi

WORK_DIR="$(mktemp -d /tmp/swirl-cloud-cloudflare.XXXXXX)"
CURL_CONFIG="$WORK_DIR/curl.conf"
printf 'silent\nshow-error\nheader = "Authorization: Bearer %s"\n' "$API_TOKEN" > "$CURL_CONFIG"
chmod 600 "$CURL_CONFIG"

# shellcheck source=lib/cloudflare-api.sh
source "$SCRIPT_DIR/lib/cloudflare-api.sh"

# shellcheck source=lib/cloudflare-edge.sh
source "$SCRIPT_DIR/lib/cloudflare-edge.sh"

# shellcheck source=lib/cloudflare-access.sh
source "$SCRIPT_DIR/lib/cloudflare-access.sh"

# shellcheck source=lib/cloudflare-origin.sh
source "$SCRIPT_DIR/lib/cloudflare-origin.sh"

step "Validating the Cloudflare User API Token"
ingress_state="$(kubectl get configmap swirl-cloud-public-ingress -n "$INGRESS_NAMESPACE" -o json --ignore-not-found)"
[[ -n "$ingress_state" ]] || ingress_state='{}'
stored_ingress_mode="$(jq -r '.data.mode // "direct"' <<< "$ingress_state")"
INGRESS_MODE="${INGRESS_MODE:-$stored_ingress_mode}"
[[ "$INGRESS_MODE" =~ ^(direct|tunnel)$ ]] || error "CLOUDFLARE_INGRESS_MODE must be direct or tunnel."
if [[ "$stored_ingress_mode" == tunnel && "$INGRESS_MODE" != tunnel ]]; then
    error "A tunnel is configured. Reverting public DNS requires an explicit maintenance migration; the direct installer cannot overwrite it."
fi
if [[ "$INGRESS_MODE" == tunnel ]]; then
    TUNNEL_ID="$(jq -r '.data.tunnelID // empty' <<< "$ingress_state")"
    [[ "$TUNNEL_ID" =~ ^[a-f0-9]{8}-([a-f0-9]{4}-){3}[a-f0-9]{12}$ ]] || error "Prepare HA ingress with configure-cloudflare-tunnel.py first."
    [[ "$(jq -r '.data.domain // empty' <<< "$ingress_state")" == "$ZONE_NAME" ]] || error "Tunnel domain differs from the requested zone."
    # Count distinct nodes, not pods: duplicate replicas on one host are not HA.
    ready_connectors="$(kubectl get pods -n "$INGRESS_NAMESPACE" -l app.kubernetes.io/name=traefik -o json | jq '
      [.items[] | select(.metadata.deletionTimestamp == null) |
       select(any(.status.containerStatuses[]?; .name == "cloudflared" and .ready == true)) |
       select(any(.status.conditions[]?; .type == "Ready" and .status == "True")) |
       .spec.nodeName] | unique | length')"
    (( ready_connectors >= 3 )) || error "Public DNS was not changed: HA ingress needs ready connectors on at least three nodes."
    DNS_RECORD_TYPE=CNAME
    DNS_RECORD_CONTENT="$TUNNEL_ID.cfargotunnel.com"
fi
token_response="$(cf_request GET /user/tokens/verify)"
require_success "$token_response" "Token verification"
[[ "$(jq -r '.result.status' <<< "$token_response")" == "active" ]] || error "The Cloudflare token is not active."

step "Finding or creating the Cloudflare zone"
zone_response="$(cf_request GET "/zones?name=$ZONE_NAME&per_page=50")"
require_success "$zone_response" "Zone lookup"
zone_count="$(jq '.result | length' <<< "$zone_response")"
((zone_count <= 1)) || error "More than one accessible zone named $ZONE_NAME was returned."

if ((zone_count == 1)); then
    ZONE_ID="$(jq -r '.result[0].id' <<< "$zone_response")"
    ACCOUNT_ID="$(jq -r '.result[0].account.id' <<< "$zone_response")"
    ZONE_STATUS="$(jq -r '.result[0].status' <<< "$zone_response")"
    mapfile -t ZONE_NAMESERVERS < <(jq -r '.result[0].name_servers[]?' <<< "$zone_response")
    info "Using existing zone $ZONE_NAME."
else
    if [[ -z "$ACCOUNT_ID" ]]; then
        membership_response="$(cf_request GET '/memberships?status=accepted&per_page=50')"
        require_success "$membership_response" "Account membership lookup (the token needs User -> Memberships -> Read)"
        membership_count="$(jq '.result | length' <<< "$membership_response")"
        ((membership_count > 0)) || error "No accessible Cloudflare accounts were returned."

        if ((membership_count == 1)); then
            ACCOUNT_ID="$(jq -r '.result[0].account.id' <<< "$membership_response")"
            info "Using Cloudflare account: $(jq -r '.result[0].account.name' <<< "$membership_response")"
        else
            echo "Accessible Cloudflare accounts:" >&2
            jq -r '.result[] | "  \(.account.id)  \(.account.name)"' <<< "$membership_response" >&2
            read -rp "Cloudflare account ID for $ZONE_NAME: " ACCOUNT_ID
            [[ "$ACCOUNT_ID" =~ ^[a-f0-9]{32}$ ]] || error "Invalid Cloudflare account ID."
            jq -e --arg id "$ACCOUNT_ID" 'any(.result[]; .account.id == $id)' <<< "$membership_response" >/dev/null || \
                error "The selected account ID was not returned by the token."
        fi
    fi

    zone_create_file="$WORK_DIR/zone-create.json"
    jq -n --arg account_id "$ACCOUNT_ID" --arg name "$ZONE_NAME" \
        '{account:{id:$account_id},name:$name,type:"full"}' > "$zone_create_file"
    zone_response="$(cf_request POST /zones "$zone_create_file")"
    require_success "$zone_response" "Zone creation"
    ZONE_ID="$(jq -r '.result.id' <<< "$zone_response")"
    ZONE_STATUS="$(jq -r '.result.status' <<< "$zone_response")"
    mapfile -t ZONE_NAMESERVERS < <(jq -r '.result.name_servers[]?' <<< "$zone_response")
    info "Created zone $ZONE_NAME."
fi

if [[ "$ZONE_STATUS" != "active" ]]; then
    warn "Cloudflare zone $ZONE_NAME is $ZONE_STATUS."
    warn "Delegate the domain at its registrar to these Cloudflare nameservers:"
    for nameserver in "${ZONE_NAMESERVERS[@]}"; do
        echo "  - $nameserver" >&2
    done
    warn "The script will prepare the pending zone now; rerun it after delegation to confirm activation."
fi

if [[ -z "$ORIGIN_IP" ]]; then
    ORIGIN_IP="$(kubectl get service "$INGRESS_SERVICE" -n "$INGRESS_NAMESPACE" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"
fi
if [[ "$INGRESS_MODE" == direct || "$PUBLISH_NODE_DNS" == true ]]; then
    valid_ipv4 "$ORIGIN_IP" || error "Could not discover a public IPv4; use --origin-ip or disable node DNS publication in tunnel mode."
    # A LAN/NAT host's ServiceLB address is private and unreachable from Cloudflare.
    ! trusted_private_ipv4 "$ORIGIN_IP" || \
        error "Ingress address $ORIGIN_IP is private. Behind a NAT router, forward TCP 80/443 to it and set --origin-ip (CLOUDFLARE_ORIGIN_IP) to the router's public IPv4."
fi
if [[ "$INGRESS_MODE" == direct ]]; then
    DNS_RECORD_CONTENT="$ORIGIN_IP"
fi
info "Using public ingress destination: $DNS_RECORD_CONTENT"

proxy_ip_response="$(cf_request GET /ips)"
require_success "$proxy_ip_response" "Reading Cloudflare proxy networks"
mapfile -t CLOUDFLARE_PROXY_CIDRS < <(jq -r '.result.ipv4_cidrs[], .result.ipv6_cidrs[]' <<< "$proxy_ip_response")
(( ${#CLOUDFLARE_PROXY_CIDRS[@]} > 0 )) || error "Cloudflare returned no proxy networks."
cloudflare_proxy_cidr_csv="$(IFS=,; echo "${CLOUDFLARE_PROXY_CIDRS[*]}")"
if [[ "$INGRESS_MODE" == tunnel ]]; then
    verify_ingress_proxy_trust '127.0.0.1/32,::1/128'
else
    verify_ingress_proxy_trust "$cloudflare_proxy_cidr_csv"
fi

if [[ -n "${CLOUDFLARE_HOST_LABELS:-}" ]]; then
    read -r -a HOST_LABELS <<< "$CLOUDFLARE_HOST_LABELS"
else
    HOST_LABELS=("${DEFAULT_HOST_LABELS[@]}")
fi
(( ${#HOST_LABELS[@]} > 0 )) || error "At least one public hostname is required."
IFS=',' read -r -a RETIRED_HOST_LABELS <<< "${CLOUDFLARE_RETIRED_HOST_LABELS:-${DEFAULT_CLOUDFLARE_RETIRED_HOST_LABELS:-}}"

configured_host_labels=" $(printf '%s ' "${HOST_LABELS[@]}")"
for retired_label in "${RETIRED_HOST_LABELS[@]}"; do
    [[ -n "$retired_label" ]] || continue
    [[ "$retired_label" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || \
        error "Invalid retired hostname label: $retired_label"
    [[ "$configured_host_labels" != *" $retired_label "* ]] || \
        error "Retired hostname $retired_label is also present in CLOUDFLARE_HOST_LABELS."
done

DNS_HOSTS=()
if [[ "$PUBLISH_APEX" == "true" ]]; then
    DNS_HOSTS+=("$ZONE_NAME")
fi
for label in "${HOST_LABELS[@]}"; do
    [[ "$label" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || error "Invalid hostname label: $label"
    DNS_HOSTS+=("$label.$ZONE_NAME")
done

reconcile_public_dns() {
step "Reconciling proxied DNS records"
for retired_label in "${RETIRED_HOST_LABELS[@]}"; do
    [[ -n "$retired_label" ]] || continue
    fqdn="$retired_label.$ZONE_NAME"
    record_response="$(cf_request GET "/zones/$ZONE_ID/dns_records?name=$fqdn&per_page=100")"
    require_success "$record_response" "DNS lookup for retired hostname $fqdn"
    mapfile -t retired_record_ids < <(
        jq -r '.result[] | select(.type == "A" or .type == "AAAA" or .type == "CNAME") | .id' \
            <<< "$record_response"
    )
    for record_id in "${retired_record_ids[@]}"; do
        update_response="$(cf_request DELETE "/zones/$ZONE_ID/dns_records/$record_id")"
        require_success "$update_response" "Deleting retired DNS record $fqdn"
        info "Deleted retired DNS record $fqdn."
    done
done

for fqdn in "${DNS_HOSTS[@]}"; do
    record_response="$(cf_request GET "/zones/$ZONE_ID/dns_records?name=$fqdn&per_page=100")"
    require_success "$record_response" "DNS lookup for $fqdn"
    address_record_count="$(jq '[.result[] | select(.type == "A" or .type == "AAAA" or .type == "CNAME")] | length' <<< "$record_response")"
    ((address_record_count <= 1)) || error "$fqdn has multiple A/AAAA/CNAME records; reconcile the conflict before rerunning."

    desired_record_file="$WORK_DIR/dns-${fqdn//./-}.json"
    jq -n --arg name "$fqdn" --arg content "$DNS_RECORD_CONTENT" --arg type "$DNS_RECORD_TYPE" \
        '{type:$type,name:$name,content:$content,ttl:1,proxied:true,comment:"Managed by swirl-cloud/scripts/configure-cloudflare.sh"}' \
        > "$desired_record_file"

    if ((address_record_count == 0)); then
        update_response="$(cf_request POST "/zones/$ZONE_ID/dns_records" "$desired_record_file")"
        require_success "$update_response" "Creating DNS record $fqdn"
        info "Created $fqdn -> $DNS_RECORD_CONTENT (proxied)."
        continue
    fi

    current_record="$(jq -c '[.result[] | select(.type == "A" or .type == "AAAA" or .type == "CNAME")][0]' <<< "$record_response")"
    if jq -e --arg content "$DNS_RECORD_CONTENT" --arg type "$DNS_RECORD_TYPE" \
        '.type == $type and .content == $content and .proxied == true and .ttl == 1' \
        <<< "$current_record" >/dev/null; then
        info "DNS record already correct: $fqdn"
        continue
    fi

    record_id="$(jq -r '.id' <<< "$current_record")"
    update_response="$(cf_request PUT "/zones/$ZONE_ID/dns_records/$record_id" "$desired_record_file")"
    require_success "$update_response" "Updating DNS record $fqdn"
    info "Updated $fqdn -> $DNS_RECORD_CONTENT (proxied)."
done
}

NODE_FQDN=""
if [[ "$PUBLISH_NODE_DNS" == "true" ]]; then
    NODE_FQDN="$NODE_DNS_LABEL.$ZONE_NAME"
    [[ " ${DNS_HOSTS[*]} " != *" $NODE_FQDN "* ]] || \
        error "The control-plane node name conflicts with a proxied application hostname: $NODE_FQDN"
    record_response="$(cf_request GET "/zones/$ZONE_ID/dns_records?name=$NODE_FQDN&per_page=100")"
    require_success "$record_response" "DNS lookup for control-plane node $NODE_FQDN"
    address_record_count="$(jq '[.result[] | select(.type == "A" or .type == "AAAA" or .type == "CNAME")] | length' <<< "$record_response")"
    ((address_record_count <= 1)) || error "$NODE_FQDN has multiple address records."
    desired_record_file="$WORK_DIR/dns-control-plane-node.json"
    jq -n --arg name "$NODE_FQDN" --arg content "$ORIGIN_IP" \
        '{type:"A",name:$name,content:$content,ttl:1,proxied:false,comment:"Managed control-plane node DNS for swirl-cloud"}' \
        > "$desired_record_file"
    if ((address_record_count == 0)); then
        update_response="$(cf_request POST "/zones/$ZONE_ID/dns_records" "$desired_record_file")"
        require_success "$update_response" "Creating control-plane node DNS record $NODE_FQDN"
        info "Created $NODE_FQDN -> $ORIGIN_IP (DNS only)."
    else
        current_record="$(jq -c '[.result[] | select(.type == "A" or .type == "AAAA" or .type == "CNAME")][0]' <<< "$record_response")"
        if ! jq -e --arg content "$ORIGIN_IP" \
            '.type == "A" and .content == $content and .proxied == false and .ttl == 1' \
            <<< "$current_record" >/dev/null; then
            record_id="$(jq -r '.id' <<< "$current_record")"
            update_response="$(cf_request PUT "/zones/$ZONE_ID/dns_records/$record_id" "$desired_record_file")"
            require_success "$update_response" "Updating control-plane node DNS record $NODE_FQDN"
            info "Updated $NODE_FQDN -> $ORIGIN_IP (DNS only)."
        else
            info "DNS record already correct: $NODE_FQDN"
        fi
    fi
fi

step "Ensuring a wildcard Cloudflare Origin CA certificate"
origin_cert="$WORK_DIR/origin.crt"
origin_key="$WORK_DIR/origin.key"
certificate_created=false

if kubectl get secret "$TLS_SECRET_NAME" -n "$INGRESS_NAMESPACE" >/dev/null 2>&1; then
    kubectl get secret "$TLS_SECRET_NAME" -n "$INGRESS_NAMESPACE" -o jsonpath='{.data.tls\.crt}' | base64 -d > "$origin_cert"
    kubectl get secret "$TLS_SECRET_NAME" -n "$INGRESS_NAMESPACE" -o jsonpath='{.data.tls\.key}' | base64 -d > "$origin_key"
fi

if certificate_is_usable "$origin_cert" "$origin_key"; then
    info "Reusing the valid Origin CA certificate from $INGRESS_NAMESPACE/$TLS_SECRET_NAME."
else
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$origin_key" 2>/dev/null
    origin_csr="$WORK_DIR/origin.csr"
    openssl req -new -key "$origin_key" -out "$origin_csr" -subj "/CN=*.$ZONE_NAME"
    certificate_request_file="$WORK_DIR/certificate-request.json"
    jq -n --rawfile csr "$origin_csr" --arg wildcard "*.$ZONE_NAME" --arg apex "$ZONE_NAME" \
        '{hostnames:[$wildcard,$apex],requested_validity:5475,request_type:"origin-rsa",csr:$csr}' \
        > "$certificate_request_file"
    certificate_response="$(cf_request POST /certificates "$certificate_request_file")"
    require_success "$certificate_response" "Origin CA certificate creation"
    jq -r '.result.certificate' <<< "$certificate_response" > "$origin_cert"
    certificate_is_usable "$origin_cert" "$origin_key" || error "Cloudflare returned an unusable Origin CA certificate."
    certificate_created=true
    info "Created a 15-year wildcard Origin CA certificate for *.$ZONE_NAME."
fi

TLS_NAMESPACES=("$INGRESS_NAMESPACE")
while IFS= read -r namespace; do
    append_unique_namespace "$namespace"
done < <(kubectl get ingress -A -o json 2>/dev/null | jq -r --arg secret "$TLS_SECRET_NAME" \
    '.items[] | select(any(.spec.tls[]?; .secretName == $secret)) | .metadata.namespace' | sort -u)
for application_namespace in apps corp; do
    if kubectl get namespace "$application_namespace" >/dev/null 2>&1; then
        append_unique_namespace "$application_namespace"
    fi
done

for namespace in "${TLS_NAMESPACES[@]}"; do
    if ! kubectl get namespace "$namespace" >/dev/null 2>&1; then
        warn "Skipping TLS secret sync because namespace does not exist: $namespace"
        continue
    fi
    kubectl create secret tls "$TLS_SECRET_NAME" \
        --namespace "$namespace" \
        --cert "$origin_cert" \
        --key "$origin_key" \
        --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    info "Installed $TLS_SECRET_NAME in namespace $namespace."
done

if [[ "$INGRESS_MODE" == tunnel ]]; then
    python3 "$SCRIPT_DIR/configure-cloudflare-tunnel.py" --verify-origins \
        --domain "$ZONE_NAME" --namespace "$INGRESS_NAMESPACE"
fi
reconcile_public_dns

step "Applying the Cloudflare security and performance baseline"
set_zone_setting ssl strict "Full (strict) TLS"
set_zone_setting always_use_https on "Always Use HTTPS"
set_zone_setting min_tls_version "$MIN_TLS_VERSION" "minimum edge TLS version"
# Not 'zrt' (0-RTT): early data is replayable, and this zone serves logins,
# APIs and admin actions.
set_zone_setting tls_1_3 on "TLS 1.3 without 0-RTT"
set_zone_setting automatic_https_rewrites on "Automatic HTTPS Rewrites"
set_zone_setting brotli on "Brotli compression"
set_zone_setting http3 on "HTTP/3"
set_zone_setting opportunistic_encryption on "opportunistic encryption"
set_zone_setting websockets on "WebSockets"
set_zone_setting early_hints on "Early Hints"
# Edges fill from the tier nearest the origin, raising the cache hit ratio.
set_zone_value argo/tiered_caching on "Tiered Cache"
set_zone_value cache/tiered_cache_smart_topology_enable on "Smart Tiered Cache topology"
set_zone_setting ipv6 on "IPv6 compatibility"
# Browser Integrity Check challenges the zone's APIs, Git webhooks, CLIs and CI
# agents. The cluster-only internal zone is CoreDNS-only, never in Cloudflare.
set_zone_setting browser_check off "Browser Integrity Check for API compatibility"
# These rewrite or block content and break dashboards and cross-host assets.
set_zone_setting hotlink_protection off "Hotlink Protection for application compatibility"
set_zone_setting email_obfuscation off "Email Address Obfuscation for application compatibility"

hsts_setting_file="$WORK_DIR/security-header-setting.json"
jq -n \
    --argjson enabled "$ENABLE_HSTS" \
    --argjson max_age "$HSTS_MAX_AGE" \
    '{value:{strict_transport_security:{enabled:$enabled,max_age:$max_age,include_subdomains:$enabled,preload:false,nosniff:true}}}' \
    > "$hsts_setting_file"
setting_response="$(cf_request PATCH "/zones/$ZONE_ID/settings/security_header" "$hsts_setting_file")"
require_success "$setting_response" "Configuring HSTS and X-Content-Type-Options"
if [[ "$ENABLE_HSTS" == "true" ]]; then
    info "Enabled HSTS for one year, including subdomains; preload remains intentionally disabled."
else
    info "HSTS is disabled by configuration."
fi

configure_registry_bot_compatibility
configure_edge_rules
configure_access

dnssec_response="$(cf_request GET "/zones/$ZONE_ID/dnssec")"
require_success "$dnssec_response" "Reading DNSSEC status"
dnssec_status="$(jq -r '.result.status' <<< "$dnssec_response")"
if [[ "$ENABLE_DNSSEC" == "true" && "$dnssec_status" == "disabled" ]]; then
    dnssec_setting_file="$WORK_DIR/dnssec-setting.json"
    printf '{"status":"active"}\n' > "$dnssec_setting_file"
    dnssec_response="$(cf_request PATCH "/zones/$ZONE_ID/dnssec" "$dnssec_setting_file")"
    require_success "$dnssec_response" "Enabling DNSSEC"
    dnssec_status="$(jq -r '.result.status' <<< "$dnssec_response")"
    info "Enabled Cloudflare DNSSEC."
elif [[ "$ENABLE_DNSSEC" == "true" ]]; then
    info "DNSSEC is already $dnssec_status."
fi

if [[ "$ENABLE_DNSSEC" == "true" && "$dnssec_status" != "active" ]]; then
    warn "DNSSEC is $dnssec_status until the registrar publishes this DS record:"
    jq -r '.result | "  Key tag: \(.key_tag)\n  Algorithm: \(.algorithm)\n  Digest type: \(.digest_algorithm)\n  Digest: \(.digest)\n  DS: \(.ds)"' <<< "$dnssec_response" >&2
fi

step "Verifying registrar delegation and public DNSSEC"
verify_registrar_and_dnssec

lock_origin_firewall

step "Verifying Cloudflare and Kubernetes state"
for fqdn in "${DNS_HOSTS[@]}"; do
    record_response="$(cf_request GET "/zones/$ZONE_ID/dns_records?type=$DNS_RECORD_TYPE&name=$fqdn&per_page=100")"
    require_success "$record_response" "Final DNS verification for $fqdn"
    jq -e --arg content "$DNS_RECORD_CONTENT" \
        '.result | length == 1 and .[0].content == $content and .[0].proxied == true' \
        <<< "$record_response" >/dev/null || error "Final DNS verification failed for $fqdn."
done
if [[ -n "$NODE_FQDN" ]]; then
    record_response="$(cf_request GET "/zones/$ZONE_ID/dns_records?type=A&name=$NODE_FQDN&per_page=100")"
    require_success "$record_response" "Final DNS verification for $NODE_FQDN"
    jq -e --arg content "$ORIGIN_IP" \
        '.result | length == 1 and .[0].content == $content and .[0].proxied == false' \
        <<< "$record_response" >/dev/null || error "Final DNS verification failed for $NODE_FQDN."
fi

ssl_response="$(cf_request GET "/zones/$ZONE_ID/settings/ssl")"
require_success "$ssl_response" "Final SSL setting verification"
[[ "$(jq -r '.result.value' <<< "$ssl_response")" == "strict" ]] || error "Cloudflare SSL mode is not strict."

https_response="$(cf_request GET "/zones/$ZONE_ID/settings/always_use_https")"
require_success "$https_response" "Final HTTPS setting verification"
[[ "$(jq -r '.result.value' <<< "$https_response")" == "on" ]] || error "Always Use HTTPS is not enabled."

min_tls_response="$(cf_request GET "/zones/$ZONE_ID/settings/min_tls_version")"
require_success "$min_tls_response" "Final minimum TLS verification"
[[ "$(jq -r '.result.value' <<< "$min_tls_response")" == "$MIN_TLS_VERSION" ]] || error "Minimum TLS is not $MIN_TLS_VERSION."

tls13_response="$(cf_request GET "/zones/$ZONE_ID/settings/tls_1_3")"
require_success "$tls13_response" "Final TLS 1.3 verification"
[[ "$(jq -r '.result.value' <<< "$tls13_response")" == "on" ]] || error "TLS 1.3 without 0-RTT is not enabled."

hsts_response="$(cf_request GET "/zones/$ZONE_ID/settings/security_header")"
require_success "$hsts_response" "Final HSTS verification"
if [[ "$ENABLE_HSTS" == "true" ]]; then
    jq -e --argjson max_age "$HSTS_MAX_AGE" \
        '.result.value.strict_transport_security | .enabled == true and .max_age == $max_age and .include_subdomains == true and .preload == false and .nosniff == true' \
        <<< "$hsts_response" >/dev/null || error "HSTS did not match the secure baseline."
fi

kubectl get secret "$TLS_SECRET_NAME" -n "$INGRESS_NAMESPACE" -o jsonpath='{.data.tls\.crt}' | base64 -d > "$WORK_DIR/verify.crt"
openssl x509 -in "$WORK_DIR/verify.crt" -noout -checkend 2592000 >/dev/null 2>&1 || \
    error "The Kubernetes Origin CA certificate failed final validation."

if [[ "$INGRESS_MODE" == tunnel ]]; then
    publication_patch="$(jq -cn --arg tunnel "$TUNNEL_ID" \
        '[{op:"test",path:"/data/tunnelID",value:$tunnel},
          {op:"add",path:"/data/publishedTunnelID",value:$tunnel}]')"
    kubectl patch configmap swirl-cloud-public-ingress -n "$INGRESS_NAMESPACE" \
        --type=json -p "$publication_patch" >/dev/null
fi

info "Cloudflare configuration is complete."
echo ""
echo "  Zone:          $ZONE_NAME ($ZONE_STATUS)"
echo "  Origin:        $DNS_RECORD_CONTENT ($INGRESS_MODE)"
echo "  DNS records:   ${#DNS_HOSTS[@]} proxied $DNS_RECORD_TYPE records"
echo "  Node DNS:      ${NODE_FQDN:-disabled}"
echo "  TLS mode:      Full (strict)"
echo "  Minimum TLS:   $MIN_TLS_VERSION"
echo "  TLS 1.3:       enabled (0-RTT disabled)"
echo "  HTTPS redirect: enabled"
echo "  HSTS:          $([[ "$ENABLE_HSTS" == "true" ]] && echo enabled || echo disabled)"
echo "  HTTP/3/Brotli: enabled"
echo "  Tiered Cache:  enabled (smart topology)"
echo "  WAF baseline:  $([[ "$ENABLE_WAF" == "true" ]] && echo enabled || echo disabled)"
echo "  Login limit:   $([[ "$ENABLE_RATE_LIMIT" == "true" ]] && echo enabled || echo disabled)"
echo "  Cache safety:  $([[ "$ENABLE_CACHE_RULES" == "true" ]] && echo enabled || echo disabled)"
echo "  Registry bots: $REGISTRY_BOT_STATUS"
echo "  Admin Access:  $([[ "$ENABLE_ACCESS" == "true" ]] && echo "Keycloak OIDC ($ACCESS_SESSION_DURATION sessions)" || echo disabled)"
echo "  DNSSEC:        $dnssec_status"
echo "  Origin access: $ORIGIN_LOCK_STATUS"
echo "  Origin cert:   $([[ "$certificate_created" == "true" ]] && echo created || echo reused)"

if [[ "$ZONE_STATUS" != "active" ]]; then
    echo ""
    warn "Zone activation is still pending; rerun this script after registrar delegation."
fi
