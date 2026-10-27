#!/usr/bin/env bash
# One offline deployment gate; --live adds API dry-runs in the active context.
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE=false
while (( $# )); do
    case "$1" in
        --live) LIVE=true ;;
        -h|--help)
            printf 'Usage: scripts/validate-repository.sh [--live]\n\nValidate deployment inputs, rendering and recovery safeguards.\n--live also uses Kubernetes server-side dry-run; it does not install resources.\n'
            exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done
cd "$ROOT"
for command in git shellcheck python3 helm promtool ansible-playbook node jq sqlite3 flock openssl; do
    command -v "$command" >/dev/null || { printf 'Required validation tool is missing: %s\n' "$command" >&2; exit 1; }
done
python3 -c 'import yaml' || { printf 'Python PyYAML is required.\n' >&2; exit 1; }
WORK="$(mktemp -d /tmp/swirl-cloud-validation.XXXXXX)"
trap 'rm -r -- "$WORK"' EXIT

mapfile -d '' -t candidates < <(git ls-files -z --cached --others --exclude-standard | LC_ALL=C sort -zu)
source_files=()
shell_scripts=()
for path in "${candidates[@]}"; do
    [[ -f "$path" ]] || continue
    source_files+=("$path")
    if [[ "$path" == *.sh ]]; then
        bash -n "$path"
        shell_scripts+=("$path")
    fi
done
printf '[PASS] Shell syntax\n'
shellcheck -x "${shell_scripts[@]}"
printf '[PASS] ShellCheck\n'

python3 scripts/validate-manifests.py --output "$WORK/rendered"
for playbook in ansible/*.yml; do
    ansible-playbook -i ansible/inventory --syntax-check "$playbook" > "$WORK/ansible.log" 2>&1 || {
        cat "$WORK/ansible.log" >&2
        exit 1
    }
done
printf '[PASS] Ansible playbook syntax\n'
python3 scripts/release.py notices > "$WORK/THIRD_PARTY_NOTICES"
printf '[PASS] Third-party licence register covers the SBOM\n'

# Safety suites for data-loss, secret-handling and fencing paths; the selection
# is explained in docs/testing.md.
bash tests/test-k3s-backups.sh
bash tests/test-k3s-api-endpoint.sh
bash tests/test-k3s-kubelet-memory.sh
bash tests/test-install-record.sh
for suite in test-postgres-ha.py test-kafka-ha.py test-vault-unseal.py test-vault-ha.py test-node-fencing.py \
             test-application-clusters.py test-application-data.py test-environment-onboarding.py test-install-inputs.py \
             test-odoo-website.py test-platform-mail.py test-outage-watch.py test-asset-inventory.py \
             test-licence-register.py test-console-i18n.py test-assurance-pack.py \
             test-hardware-health.py test-security-detection.py test-soc-forwarding.py; do
    python3 "tests/$suite"
done
printf '[PASS] Deployment, recovery and release safety suites\n'

if [[ "$LIVE" == true ]]; then
    command -v kubectl >/dev/null || { printf 'kubectl is required for --live.\n' >&2; exit 1; }
    kubectl cluster-info >/dev/null
    live_namespaces="$(kubectl get namespaces -o jsonpath='{.items[*].metadata.name}')"
    live_api_versions="$(kubectl api-versions | tr '\n' ' ')"
    skipped_total=0
    for manifest in "$WORK"/rendered/*.yaml; do
        # Count, not validate, what this cluster cannot hold yet: objects in a
        # namespace the profile declares or in cnpg-system, and CloudNativePG
        # resources before HA activation installs the operator. Anything else fails.
        skipped="$(python3 - "$manifest" "$manifest.live" "$live_namespaces" "$live_api_versions" <<'PY'
import sys, yaml
source, target = sys.argv[1], sys.argv[2]
live, served = set(sys.argv[3].split()), set(sys.argv[4].split())
documents = [d for d in yaml.safe_load_all(open(source)) if d]
declared = {d["metadata"]["name"] for d in documents if d.get("kind") == "Namespace"}
pending = (declared | {"cnpg-system"}) - live
activation_groups = {"postgresql.cnpg.io"}
kept = [d for d in documents
        if d.get("metadata", {}).get("namespace") not in pending
        and not (d.get("apiVersion", "").split("/")[0] in activation_groups and d.get("apiVersion") not in served)]
yaml.safe_dump_all(kept, open(target, "w"), sort_keys=False)
print(len(documents) - len(kept))
PY
)"
        kubectl apply --dry-run=server -f "$manifest.live" >/dev/null
        skipped_total=$((skipped_total + skipped))
    done
    printf '[PASS] Kubernetes server-side dry-runs (%d objects that need namespaces or CloudNativePG APIs this cluster does not have yet were not validated)\n' "$skipped_total"
fi

git diff --check
if grep -IEq '((cfat|cfut)_[[:alnum:]]{20,}|tskey-(api|auth)-[[:alnum:]_-]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)' "${source_files[@]}"; then
    printf 'Recognizable token or private key found in repository sources.\n' >&2
    exit 1
fi
printf '[PASS] Patch whitespace and recognizable credential scan\nValidation passed.\n'
