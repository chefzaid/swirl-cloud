#!/usr/bin/env bash
# Guard the Cloudflare origin helpers without Cloudflare: an origin certificate is
# reused only when Cloudflare's origin CA issued it for the zone and its wildcard,
# it is valid for 30 more days and matches its key; the origin lock adds the
# current proxy networks before removing old ones and the open web rules; Traefik
# must trust exactly the current networks on both entry points and never
# insecurely; and the local TLS fallback names every domain and leaves no key
# behind.
# The stubs below replace commands that the libraries run through sudo.
# shellcheck disable=SC2032
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/cloudflare-origin.sh
source "$SCRIPT_DIR/lib/cloudflare-origin.sh"
# shellcheck source=../scripts/lib/tls.sh
source "$SCRIPT_DIR/lib/tls.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
error() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }
warn() { :; }
step() { :; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
command -v openssl >/dev/null || { printf '[SKIP] openssl is required\n'; exit 0; }

# --- certificate_is_usable
ZONE_NAME=example.com
ca() { # ca NAME SUBJECT
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "$2" -keyout "$STATE/$1.key" -out "$STATE/$1.pem" \
        -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign >/dev/null 2>&1
}
leaf() { # leaf NAME CA DAYS SAN
    openssl req -newkey rsa:2048 -nodes -subj "/CN=$ZONE_NAME" -keyout "$STATE/$1.key" -out "$STATE/$1.csr" >/dev/null 2>&1
    printf 'subjectAltName=%s\n' "$4" > "$STATE/$1.ext"
    openssl x509 -req -in "$STATE/$1.csr" -CA "$STATE/$2.pem" -CAkey "$STATE/$2.key" -CAcreateserial -days "$3" \
        -extfile "$STATE/$1.ext" -out "$STATE/$1.crt" >/dev/null 2>&1
}
ca origin-ca "/O=CloudFlare, Inc./OU=CloudFlare Origin CA/CN=CloudFlare Origin SSL Certificate Authority"
ca other-ca "/CN=Some Other CA"
leaf good origin-ca 365 "DNS:*.example.com,DNS:example.com"
leaf expiring origin-ca 10 "DNS:*.example.com,DNS:example.com"
leaf apex-missing origin-ca 365 "DNS:*.example.com"
leaf wildcard-missing origin-ca 365 "DNS:example.com,DNS:www.example.com"
leaf foreign other-ca 365 "DNS:*.example.com,DNS:example.com"
certificate_is_usable "$STATE/good.crt" "$STATE/good.key" || fail 'A valid origin certificate is not reused'
for name in expiring apex-missing wildcard-missing foreign; do
    if certificate_is_usable "$STATE/$name.crt" "$STATE/$name.key"; then fail "Certificate '$name' is reused"; fi
done
if certificate_is_usable "$STATE/good.crt" "$STATE/foreign.key"; then fail 'A certificate is reused with another key'; fi
if certificate_is_usable "$STATE/good.crt" "$STATE/missing.key"; then fail 'A certificate is reused without its key'; fi
if ZONE_NAME=example.org certificate_is_usable "$STATE/good.crt" "$STATE/good.key"; then fail 'Another zone accepts the certificate'; fi

# --- lock_origin_firewall
LOG="$STATE/ufw.log"
PROXY_FILE="$STATE/cloudflare-proxy-only"
UFW_ACTIVE=true
ufw() {
    case "$*" in
        status) [[ "$UFW_ACTIVE" == true ]] && printf 'Status: active\n' || printf 'Status: inactive\n' ;;
        *) printf 'ufw %s\n' "$*" >> "$LOG" ;;
    esac
}
# The saved network list lives in the scratch directory, with or without sudo.
saved() { printf '%s\n' "${@//\/etc\/swirl-cloud\/cloudflare-proxy-only/$PROXY_FILE}"; }
test() { local a; mapfile -t a < <(saved "$@"); builtin test "${a[@]}"; }
cat() { local a; mapfile -t a < <(saved "$@"); command cat "${a[@]}"; }
tee() { local a; mapfile -t a < <(saved "$@"); command tee "${a[@]}"; }
install() { :; }
sudo() { "$@"; }
lock() {
    : > "$LOG"
    ( INGRESS_MODE=direct LOCK_ORIGIN=true; "$@"; lock_origin_firewall; printf '%s\n' "$ORIGIN_LOCK_STATUS" ) 2>&1
}
CLOUDFLARE_PROXY_CIDRS=(173.245.48.0/20 2400:cb00::/32)
cloudflare_proxy_cidr_csv="173.245.48.0/20,2400:cb00::/32"
printf '# Managed by swirl-cloud\n173.245.48.0/20\n198.41.128.0/17\nnot-a-cidr\n' > "$PROXY_FILE"
[[ "$(lock true | tail -n 1)" == "Cloudflare proxy networks only" ]] || fail 'The origin is not locked'
first_delete="$(grep -n -- '--force delete' "$LOG" | head -n 1 | cut -d: -f1)"
last_allow="$(grep -n 'ufw allow from' "$LOG" | tail -n 1 | cut -d: -f1)"
(( last_allow < first_delete )) || fail 'Rules are removed before the current networks are allowed'
grep -Fqx 'ufw allow from 2400:cb00::/32 to any port 443 proto tcp comment Cloudflare proxy' "$LOG" || fail 'IPv6 networks are not allowed'
grep -Fqx 'ufw --force delete allow from 198.41.128.0/17 to any port 443 proto tcp' "$LOG" || fail 'A network Cloudflare dropped stays allowed'
! grep -Fq 'delete allow from 173.245.48.0/20' "$LOG" || fail 'A current network is removed'
! grep -Fq 'not-a-cidr' "$LOG" || fail 'A malformed saved line reaches UFW'
grep -Fqx 'ufw --force delete allow 443/tcp' "$LOG" || fail 'The open HTTPS rule stays'
[[ "$(tail -n 2 "$PROXY_FILE")" == $'173.245.48.0/20\n2400:cb00::/32' ]] || fail "The saved networks are not the current ones: $(cat "$PROXY_FILE")"
[[ "$(lock eval INGRESS_MODE=tunnel | tail -n 1)" == "outbound tunnel; no public ingress listener" && ! -s "$LOG" ]] || \
    fail 'A tunnel installation changes the firewall'
[[ "$(lock eval LOCK_ORIGIN=false | tail -n 1)" == "left unchanged" && ! -s "$LOG" ]] || fail 'CLOUDFLARE_LOCK_ORIGIN=false is ignored'
UFW_ACTIVE=false
[[ "$(lock true | tail -n 1)" == "provider firewall required" && ! -s "$LOG" ]] || fail 'An inactive UFW is changed'
UFW_ACTIVE=true

# --- verify_ingress_proxy_trust
INGRESS_NAMESPACE=infra
WORKLOADS=""
kubectl() {
    case "$1" in
        get) [[ "$2" == secret* ]] && return 1; printf '%s' "$WORKLOADS" ;;
        create) cp "${5#--cert=}" "$STATE/issued.crt"; cp "${6#--key=}" "$STATE/issued.key"; printf '%s\n' "$*" > "$STATE/create" ;;
    esac
}
traefik() { # traefik ARG...: a Traefik Deployment with those arguments
    printf '%s\n' "$@" | jq -R . | jq -cs '{items: [{spec: {template: {spec: {containers: [{name: "traefik", args: .}]}}}}]}'
}
CIDRS="173.245.48.0/20,2400:cb00::/32"
trusts() { WORKLOADS="$1"; ( verify_ingress_proxy_trust "$CIDRS" ) >/dev/null 2>&1; }
web="--entrypoints.web.forwardedHeaders.trustedIPs=2400:cb00::/32,173.245.48.0/20"
websecure="--entrypoints.websecure.forwardedHeaders.trustedIPs=173.245.48.0/20,2400:cb00::/32"
trusts "$(traefik "$web" "$websecure")" || fail 'Traefik trusting exactly the current networks is refused'
trusts '{"items": []}' || fail 'A cluster without Traefik yet is refused'
if trusts "$(traefik "$websecure")"; then fail 'Trust on one entry point only is accepted'; fi
if trusts "$(traefik "$web" "--entrypoints.websecure.forwardedHeaders.trustedIPs=173.245.48.0/20")"; then
    fail 'A missing network is accepted'
fi
if trusts "$(traefik "$web" "$websecure" "--entrypoints.web.forwardedHeaders.insecure")"; then fail 'Insecure forwarded headers are accepted'; fi
trusts "$(traefik "$web" "$websecure" "--entrypoints.web.forwardedHeaders.insecure=false")" || fail 'insecure=false is refused'
if trusts "$(traefik "$web" "$websecure" "$web")"; then fail 'A duplicate trust argument is accepted'; fi
if trusts '[]'; then fail 'An invalid inventory is accepted'; fi

# --- ensure_tls_secret
TMPDIR_BEFORE="$(find /tmp -maxdepth 1 -name 'swirl-cloud-tls.*' | wc -l)"
ensure_tls_secret infra local-tls example.test '*.example.test' || fail 'The local TLS secret is not created'
grep -Fq 'create secret tls local-tls' "$STATE/create" || fail 'The secret is not created as a TLS secret'
san="$(openssl x509 -in "$STATE/issued.crt" -noout -ext subjectAltName)"
[[ "$san" == *"DNS:example.test"* && "$san" == *"DNS:*.example.test"* ]] || fail "The certificate does not name every domain: $san"
[[ "$(find /tmp -maxdepth 1 -name 'swirl-cloud-tls.*' | wc -l)" == "$TMPDIR_BEFORE" ]] || fail 'The private key is left in /tmp'
kubectl() { [[ "$1" == get ]] || fail 'An existing TLS secret is replaced'; }
ensure_tls_secret infra local-tls example.test || fail 'An existing TLS secret is not reused'

printf '[PASS] Cloudflare origin and local TLS\n'
