#!/usr/bin/env bash
# Guard the API server's Keycloak sign-in without a node: the drop-in appends to
# K3s's arguments, names the platform's issuer and the "oidc:" prefixes the role
# bindings expect, and bad inputs are refused before anything is written.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/configure-k3s-oidc.sh
source "$SCRIPT_DIR/configure-k3s-oidc.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

config="$(PLATFORM_DOMAIN=example.com KEYCLOAK_REALM=example dropin)"
grep -qx 'kube-apiserver-arg+:' <<< "$config" || fail 'The drop-in replaces the audit arguments'
grep -Fqx '  - "oidc-issuer-url=https://keycloak.example.com/auth/realms/example"' <<< "$config" || fail "Wrong issuer: $config"
grep -Fqx '  - "oidc-client-id=kubernetes"' <<< "$config" || fail 'Wrong client'
grep -Fqx '  - "oidc-groups-prefix=oidc:"' <<< "$config" || fail 'Groups lack the prefix the role bindings expect'
grep -Fqx '  - "oidc-username-prefix=oidc:"' <<< "$config" || fail 'Users could take system: names'
grep -q 'name: oidc:platform-admins' "$SCRIPT_DIR/../k8s/base/security-baseline.yaml" || fail 'No role binding for oidc:platform-admins'
grep -q "ensure_client kubernetes true" "$SCRIPT_DIR/../k8s/platform/keycloak-sso.yaml" || fail 'No Keycloak client "kubernetes"'
for bad in "PLATFORM_DOMAIN=example.com KEYCLOAK_REALM=master" "PLATFORM_DOMAIN='bad domain' KEYCLOAK_REALM=example"; do
    if (eval "$bad" main >/dev/null 2>&1); then fail "Accepted: $bad"; fi
done

# The EXIT trap removes the temporary file after main returns; a local one is unset by then.
if declare -f main | grep -Eq '^\s*local .*\btemporary\b'; then fail 'main keeps the EXIT trap'\''s file in a local variable'; fi

printf '[PASS] Keycloak sign-in for kubectl\n'
