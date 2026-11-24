#!/usr/bin/env bash
# Guard the configuration renderer's inputs without Helm: it refuses to render
# into the checkout or into a populated directory, and refuses invalid domains,
# equal public and internal zones, a GitOps URL with credentials, and malformed
# names before it copies anything. Rendering itself (Helm) is covered by
# scripts/validate-manifests.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
RENDER="$ROOT/scripts/render-cluster-config.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
ARGS=(--domain example.com --internal-domain internal.example.com
      --gitops-repository https://gitlab.example.com/acme/swirl-cloud.git
      --gitlab-group acme --gitlab-project swirl-cloud --cloudflare-access-team acme)

refuse() {
    local message="$1" output
    shift
    if output="$(env -i PATH="$PATH" HOME="$STATE" bash "$RENDER" "$@" 2>&1)"; then
        fail "render $* was accepted"
    fi
    [[ "$output" == *"$message"* ]] || fail "render $* failed with '$output', expected '$message'"
}
with() {
    # The base arguments with one option replaced.
    local option="$1" value="$2" index
    local -a args=("${ARGS[@]}")
    for index in "${!args[@]}"; do
        [[ "${args[$index]}" != "$option" ]] || args[index + 1]="$value"
    done
    printf '%s\0' "${args[@]}"
}
refuse_with() {
    local message="$1" option="$2" value="$3"
    local -a args
    mapfile -d '' args < <(with "$option" "$value")
    refuse "$message" --output "$STATE/out-$RANDOM" "${args[@]}"
}

refuse "Choose a dedicated output directory" "${ARGS[@]}"
refuse "Render outside the repository checkout" --output "$ROOT/rendered" "${ARGS[@]}"
mkdir -p "$STATE/populated" && touch "$STATE/populated/old.yaml"
refuse "must be empty" --output "$STATE/populated" "${ARGS[@]}"
touch "$STATE/file"
refuse "Choose a dedicated output directory" --output "$STATE/file" "${ARGS[@]}"
refuse "Unknown option: --bogus" --output "$STATE/out" "${ARGS[@]}" --bogus
refuse "--domain requires a value" --output "$STATE/out" --domain

refuse_with "Invalid public domain" --domain "example.c"
refuse_with "Invalid public domain" --domain "example"
refuse_with "Invalid internal DNS domain" --internal-domain "internal_example.com"
refuse_with "must differ" --internal-domain "example.com"
refuse_with "without embedded credentials" --gitops-repository "https://user:token@gitlab.example.com/acme/swirl-cloud.git"
refuse_with "without embedded credentials" --gitops-repository "ssh://git@gitlab.example.com/acme/swirl-cloud.git"
refuse_with "without embedded credentials" --gitops-repository "https://gitlab.example.com/acme/swirl-cloud"
refuse_with "GitLab group, project name and project path" --gitlab-group "acme group"
refuse_with "Invalid Cloudflare Access team name" --cloudflare-access-team "Acme"
refuse "--apps-enabled must be true or false" --output "$STATE/out" "${ARGS[@]}" --apps-enabled yes
refuse "master is reserved" --output "$STATE/out" "${ARGS[@]}" --keycloak-realm master
[[ ! -e "$STATE/out" ]] || fail 'A refused render created its output directory'

printf '[PASS] Configuration renderer inputs\n'
