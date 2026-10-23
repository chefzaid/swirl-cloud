#!/usr/bin/env bash
# Shared installation of native Traefik ingress and optional co-located Tunnel.
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/platform-identity.sh
source "$SCRIPT_DIR/lib/platform-identity.sh"
# shellcheck source=../config/platform.env
source "$ROOT/config/platform.env"
fail() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
[[ $# == 0 ]] || { printf 'Usage: INGRESS_VALUES_FILE=rendered/config/traefik-values.yaml %s\n' "$0"; exit 0; }
platform_identity_load
platform_identity_defaults
VALUES="${INGRESS_VALUES_FILE:-$ROOT/config/traefik-values.yaml}"
NAMESPACE="${INGRESS_NAMESPACE:-infra}"
MODE="${HIGH_AVAILABILITY_ENABLED:-}"
CANDIDATE="${INGRESS_MIGRATION_CANDIDATE:-false}"
[[ "$CANDIDATE" =~ ^(true|false)$ ]] || fail 'INGRESS_MIGRATION_CANDIDATE must be true or false.'
# The origin mirrors the zone's HSTS inputs (configure-cloudflare.sh); with
# max-age 0 Traefik sends no Strict-Transport-Security header.
ENABLE_HSTS="${CLOUDFLARE_ENABLE_HSTS:-}"
HSTS_MAX_AGE="${CLOUDFLARE_HSTS_MAX_AGE:-}"
[[ "$ENABLE_HSTS" =~ ^(true|false)?$ ]] || fail 'CLOUDFLARE_ENABLE_HSTS must be true or false.'
[[ "$HSTS_MAX_AGE" =~ ^[0-9]*$ ]] || fail 'CLOUDFLARE_HSTS_MAX_AGE must be seconds as a whole number.'
state="$(kubectl get configmap swirl-cloud-public-ingress -n "$NAMESPACE" --ignore-not-found -o json)"
[[ -n "$state" ]] || state='{}'
stored_mode="$(jq -r '.data.mode // "direct"' <<< "$state")"
[[ -n "$MODE" ]] || { MODE=false; [[ "$stored_mode" != tunnel ]] || MODE=true; }
[[ "$MODE" =~ ^(true|false)$ ]] || fail 'HIGH_AVAILABILITY_ENABLED must be true or false.'
[[ "$CANDIDATE" == true || "$stored_mode" != tunnel || "$MODE" == true ]] || fail 'Existing HA ingress cannot be downgraded by a single-node installer run.'
releases="$(helm list -n "$NAMESPACE" -o json)"
jq -e 'type == "array" and all(.[]; type == "object" and (.name | type) == "string")' <<< "$releases" >/dev/null || fail 'Helm returned an invalid release inventory.'
if [[ "$CANDIDATE" == true ]] && jq -e 'any(.[]; .name == "traefik")' <<< "$releases" >/dev/null; then
    fail 'A traefik release already exists; candidate mode cannot replace an active ingress. Remove only a verified disposable candidate before repeating its installation.'
fi
if [[ "$CANDIDATE" != true ]] && jq -e 'any(.[]; .name == "ingress-nginx")' <<< "$releases" >/dev/null; then
    fail 'Migrate the existing ingress-nginx release first; see docs/guides/platform-migration.md. Use INGRESS_MIGRATION_CANDIDATE=true for an isolated ClusterIP candidate.'
fi
# With neither input a rerun keeps the deployed max-age, as the zone keeps its
# own; only a first installation uses the defaults.
if [[ -z "$ENABLE_HSTS$HSTS_MAX_AGE" ]] && jq -e 'any(.[]; .name == "traefik")' <<< "$releases" >/dev/null; then
    HSTS_MAX_AGE="$(kubectl get middlewares.traefik.io security-headers -n "$NAMESPACE" --ignore-not-found -o json | jq -r '.spec.headers.stsSeconds // 0')"
fi
HSTS_MAX_AGE="${HSTS_MAX_AGE:-$DEFAULT_CLOUDFLARE_HSTS_MAX_AGE}"
[[ "$HSTS_MAX_AGE" =~ ^[0-9]+$ ]] || fail 'The deployed security-headers Middleware has an invalid stsSeconds.'
[[ "${ENABLE_HSTS:-true}" == true ]] || HSTS_MAX_AGE=0
temporary="$(mktemp -d /tmp/swirl-cloud-ingress.XXXXXX)"
trap 'rm -rf -- "$temporary"' EXIT
# Fill in the TLS Secret name (also for direct runs of this helper) and the
# HSTS max-age, which only this step resolves.
python3 - "$VALUES" "$temporary/base.yaml" "$TLS_SECRET_NAME" "$HSTS_MAX_AGE" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text().replace('__TLS_SECRET_NAME__', sys.argv[3])
Path(sys.argv[2]).write_text(text.replace('__HSTS_MAX_AGE__', str(int(sys.argv[4]))))
PY
args=(--values "$temporary/base.yaml")
if [[ "$MODE" == true && "$CANDIDATE" != true ]]; then
    HIGH_AVAILABILITY_ENABLED=true "$SCRIPT_DIR/reconcile-cluster-topology.sh" --print-longhorn-replicas >/dev/null
    if [[ -n "${CLOUDFLARE_API_TOKEN:-}" ]] || ! kubectl get secret cloudflare-tunnel -n "$NAMESPACE" >/dev/null 2>&1; then
        python3 "$SCRIPT_DIR/configure-cloudflare-tunnel.py" --namespace "$NAMESPACE"
    else
        [[ "$stored_mode" == tunnel ]] || fail 'Tunnel credentials exist without a managed tunnel identity; reconcile with a Cloudflare token.'
    fi
    args+=(--values "$ROOT/config/traefik-ha-values.yaml")
else
    # Trust only the edge addresses, never an entire node or pod network.
    curl --fail --silent --show-error --connect-timeout 10 --max-time 30 https://www.cloudflare.com/ips-v4 > "$temporary/edge.txt"
    printf '\n' >> "$temporary/edge.txt"
    curl --fail --silent --show-error --connect-timeout 10 --max-time 30 https://www.cloudflare.com/ips-v6 >> "$temporary/edge.txt"
    python3 - "$temporary/edge.txt" "$temporary/trust.json" <<'PY'
import ipaddress, json, sys
from pathlib import Path
networks = sorted({str(ipaddress.ip_network(line.strip())) for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()})
if not networks or any(ipaddress.ip_network(value).prefixlen == 0 for value in networks):
    raise SystemExit('Cloudflare returned an invalid trusted proxy inventory')
Path(sys.argv[2]).write_text(json.dumps({'ports': {name: {'forwardedHeaders': {'trustedIPs': networks}} for name in ('web', 'websecure')}}))
PY
    args+=(--values "$temporary/trust.json")
fi
if [[ "$CANDIDATE" == true ]]; then
    args+=(--set service.spec.type=ClusterIP --set service.spec.externalTrafficPolicy=null --set ingressClass.isDefaultClass=false)
fi
helm repo add traefik https://traefik.github.io/charts --force-update >/dev/null
helm repo update traefik --fail-on-repo-update-fail >/dev/null
helm upgrade --install traefik traefik/traefik \
    --namespace "$NAMESPACE" --version "${TRAEFIK_CHART_VERSION:-$DEFAULT_TRAEFIK_CHART_VERSION}" \
    --reset-values "${args[@]}" --wait --timeout "${INGRESS_HELM_TIMEOUT:-$DEFAULT_INGRESS_HELM_TIMEOUT}"
if [[ "$CANDIDATE" == true ]]; then
    printf '[INFO] Isolated ClusterIP candidate is ready; no tunnel connector was started. Follow docs/guides/platform-migration.md.\n'
elif [[ "$MODE" == true ]]; then
    printf '[INFO] HA ingress is ready. Run configure-cloudflare.sh to publish the tunnel CNAMEs.\n'
fi
