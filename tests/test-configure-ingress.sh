#!/usr/bin/env bash
# Guard the native Traefik installation without a cluster: Traefik trusts
# forwarded headers only from Cloudflare's published networks (never a catch-all
# or an empty list), the origin's HSTS follows the zone's inputs and a rerun keeps
# the deployed max-age, the TLS Secret name is filled in, and an HA (tunnel)
# installation is never downgraded by a single-node run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
BIN="$STATE/bin"
mkdir -p "$BIN" "$STATE/values"
# PATH stubs. kubectl answers from files; helm records the upgrade and keeps a
# copy of each values file; curl serves the edge lists.
cat > "$BIN/kubectl" <<EOF
#!/usr/bin/env bash
case "\$*" in
    *"configmap swirl-cloud-public-ingress"*) cat "$STATE/ingress-state" 2>/dev/null || true ;;
    *"middlewares.traefik.io security-headers"*) cat "$STATE/middleware" 2>/dev/null || true ;;
    *"configmap swirl-cloud-identity"*|*"statefulset kafka"*) exit 0 ;;
    *) exit 1 ;;
esac
EOF
cat > "$BIN/helm" <<EOF
#!/usr/bin/env bash
case "\$1" in
    list) cat "$STATE/releases" ;;
    repo) exit 0 ;;
    upgrade)
        printf '%s\n' "\$*" > "$STATE/upgrade"
        n=0; previous=""
        for argument in "\$@"; do
            [[ "\$previous" != --values ]] || { n=\$((n + 1)); cp "\$argument" "$STATE/values/\$n"; }
            previous="\$argument"
        done ;;
esac
EOF
cat > "$BIN/curl" <<EOF
#!/usr/bin/env bash
case "\${*: -1}" in
    https://www.cloudflare.com/ips-v4) cat "$STATE/ips-v4" ;;
    https://www.cloudflare.com/ips-v6) cat "$STATE/ips-v6" ;;
esac
EOF
chmod +x "$BIN"/*
printf '173.245.48.0/20\n104.16.0.0/13' > "$STATE/ips-v4"
printf '2400:cb00::/32' > "$STATE/ips-v6"
printf '[]' > "$STATE/releases"
VALUES="$STATE/traefik-values.yaml"
cat "$ROOT/config/traefik-values.yaml" > "$VALUES"

ingress() {
    rm -f "$STATE/upgrade" "$STATE/values/"*
    env -i PATH="$BIN:$PATH" HOME="$STATE" PLATFORM_DOMAIN=example.com TLS_SECRET_NAME=example-com-tls \
        INGRESS_VALUES_FILE="$VALUES" "$@" bash "$ROOT/scripts/configure-ingress.sh" 2>&1
}
yaml_get() { python3 -c 'import sys, yaml, json; d = yaml.safe_load(open(sys.argv[1])); print(json.dumps(eval(sys.argv[2], {"d": d})))' "$1" "$2"; }

# A first installation: edge trust only, default HSTS, the TLS Secret filled in.
ingress >/dev/null || fail "A first installation fails: $(ingress)"
grep -q -- '--reset-values' "$STATE/upgrade" || fail 'Old values survive an upgrade'
[[ "$(yaml_get "$STATE/values/2" 'd["ports"]["websecure"]["forwardedHeaders"]["trustedIPs"]')" == '["104.16.0.0/13", "173.245.48.0/20", "2400:cb00::/32"]' ]] || \
    fail "Traefik does not trust exactly the edge networks: $(cat "$STATE/values/2")"
[[ "$(yaml_get "$STATE/values/2" 'd["ports"]["web"]["forwardedHeaders"]["trustedIPs"]')" == *"2400:cb00::/32"* ]] || fail 'The web entry point lacks the edge trust'
! grep -q '__TLS_SECRET_NAME__\|__HSTS_MAX_AGE__' "$STATE/values/1" || fail 'A placeholder is left in the Traefik values'
grep -q 'example-com-tls' "$STATE/values/1" || fail 'The TLS Secret name is not filled in'
default_age="$(sed -n 's/^DEFAULT_CLOUDFLARE_HSTS_MAX_AGE=//p' "$ROOT/config/platform.env")"
grep -Eq "stsSeconds: *$default_age\b" "$STATE/values/1" || fail "The default HSTS max-age $default_age is not used"

# A rerun without HSTS inputs keeps the deployed max-age; disabling HSTS sends none.
printf '[{"name":"traefik"}]' > "$STATE/releases"
printf '{"spec":{"headers":{"stsSeconds":600}}}' > "$STATE/middleware"
ingress >/dev/null
grep -Eq 'stsSeconds: *600\b' "$STATE/values/1" || fail 'A rerun does not keep the deployed HSTS max-age'
ingress CLOUDFLARE_ENABLE_HSTS=false >/dev/null
grep -Eq 'stsSeconds: *0\b' "$STATE/values/1" || fail 'Disabled HSTS still sends a max-age'
ingress CLOUDFLARE_HSTS_MAX_AGE=86400 >/dev/null
grep -Eq 'stsSeconds: *86400\b' "$STATE/values/1" || fail 'An explicit HSTS max-age is not used'
for input in CLOUDFLARE_ENABLE_HSTS=yes CLOUDFLARE_HSTS_MAX_AGE=1y; do
    if ingress "$input" >/dev/null; then fail "$input is accepted"; fi
done

# An edge inventory that would trust everything, or nothing, stops before Helm.
for list in "0.0.0.0/0" "not-a-network"; do
    printf '%s' "$list" > "$STATE/ips-v4"
    if ingress >/dev/null; then fail "Edge list '$list' is accepted"; fi
    [[ ! -e "$STATE/upgrade" ]] || fail "Helm runs with edge list '$list'"
done
printf '' > "$STATE/ips-v4"; printf '' > "$STATE/ips-v6"
if ingress >/dev/null; then fail 'An empty edge list is accepted'; fi
printf '173.245.48.0/20' > "$STATE/ips-v4"; printf '2400:cb00::/32' > "$STATE/ips-v6"

# HA ingress cannot be downgraded; an invalid Helm inventory stops the run.
printf '{"data":{"mode":"tunnel","tunnelID":"t1"}}' > "$STATE/ingress-state"
output="$(ingress HIGH_AVAILABILITY_ENABLED=false)" && fail 'A tunnel installation is downgraded'
[[ "$output" == *"cannot be downgraded"* && ! -e "$STATE/upgrade" ]] || fail "Unexpected result: $output"
rm -f "$STATE/ingress-state"
printf '{"not":"a list"}' > "$STATE/releases"
output="$(ingress)" && fail 'An invalid Helm inventory is accepted'
[[ "$output" == *"invalid release inventory"* ]] || fail "Unexpected error: $output"

printf '[PASS] Native ingress\n'
