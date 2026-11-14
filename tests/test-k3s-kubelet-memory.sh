#!/usr/bin/env bash
# Guard the kubelet memory reservation without a node: K3s's share scaled to the
# node's memory, and an eviction-hard that would drop K3s's disk thresholds.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/configure-k3s-kubelet-memory.sh
source "$SCRIPT_DIR/configure-k3s-kubelet-memory.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

for case in 4096:1024 8192:1843 16384:2662 32045:3601 131072:9543 262144:12164; do
    reserved="$(kube_reserved_mib "${case%:*}")"
    [[ "$reserved" == "${case#*:}" ]] || fail "K3s reserves ${reserved} MiB of ${case%:*} MiB, expected ${case#*:}"
done

MEMINFO="$(mktemp)"
trap 'rm -f "$MEMINFO"' EXIT
printf 'MemTotal:       32814176 kB\nMemFree:          805000 kB\n' > "$MEMINFO"
config="$(dropin)"
grep -qx 'kubelet-arg+:' <<< "$config" || fail 'The drop-in replaces kubelet arguments set by other drop-ins'
grep -Fqx '  - "kube-reserved=memory=3601Mi"' <<< "$config" || fail "K3s's reservation is not sized from the node: $config"
grep -Fqx '  - "system-reserved=memory=1Gi"' <<< "$config" || fail 'The host keeps no memory'
grep -Fq 'eviction-hard=memory.available<500Mi,nodefs.available<5%,imagefs.available<5%"' <<< "$config" || \
    fail 'eviction-hard drops the disk thresholds K3s sets by default'
grep -Fq 'eviction-soft-grace-period=memory.available=' <<< "$config" || fail 'The soft threshold has no grace period'
config="$(KUBE_RESERVED_MEMORY=4Gi dropin)"
grep -Fqx '  - "kube-reserved=memory=4Gi"' <<< "$config" || fail 'KUBE_RESERVED_MEMORY is ignored'

# Shutdown by priority: every platform class falls in a group, data stores last and longest.
python3 - "$(shutdown_config)" "$SCRIPT_DIR/../k8s/base/security-baseline.yaml" <<'PYTHON' || fail 'The shutdown order does not match the priority classes'
import sys, yaml
config = yaml.safe_load(sys.argv[1])
assert config["kind"] == "KubeletConfiguration" and "shutdownGracePeriod" not in config
groups = config["shutdownGracePeriodByPodPriority"]
floors = [group["priority"] for group in groups]
assert floors == sorted(floors), "groups must rise in priority"
classes = {doc["metadata"]["name"]: doc["value"] for doc in yaml.safe_load_all(open(sys.argv[2]))
           if doc and doc["kind"] == "PriorityClass"}
classes["ci-jobs"] = -100
for name, value in classes.items():
    assert value >= floors[0], f"{name} below every group"
critical = max((g for g in groups if g["priority"] <= classes["platform-critical"]), key=lambda g: g["priority"])
assert critical["shutdownGracePeriodSeconds"] == max(g["shutdownGracePeriodSeconds"] for g in groups)
assert sum(g["shutdownGracePeriodSeconds"] for g in groups) <= 180, "the host would wait too long to shut down"
PYTHON

printf '[PASS] Kubelet memory reservation and shutdown order\n'
