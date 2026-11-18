#!/usr/bin/env bash
# Guard the kubelet memory reservation without a node: K3s's share scaled to the
# node's memory, an eviction-hard that would drop K3s's disk thresholds, and the
# pod cap within the node's pod range.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/configure-k3s-kubelet-memory.sh
source "$SCRIPT_DIR/configure-k3s-kubelet-memory.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

for case in 4096:1024 8192:1843 16384:2662 32045:3601 131072:9543 262144:12164; do
    reserved="$(kube_reserved_mib "${case%:*}")"
    [[ "$reserved" == "${case#*:}" ]] || fail "K3s reserves ${reserved} MiB of ${case%:*} MiB, expected ${case#*:}"
done

unset KUBELET_MAX_PODS
MEMINFO="$(mktemp)"
STATE="$(mktemp -d)"
trap 'rm -rf "$MEMINFO" "$STATE"' EXIT
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

# The pod cap: none unless asked (K3s's 110), as asked, kept by a rerun that does not
# say, and never more than the node's pod range holds.
CONFIG_ROOT="$STATE/k3s"
DROPIN_PATH="$CONFIG_ROOT/config.yaml.d/30-swirl-cloud-kubelet-memory.yaml"
K3S_UNIT_PATH="$STATE/k3s.service"
mkdir -p "$CONFIG_ROOT/config.yaml.d"
kubectl() { [[ -n "${POD_CIDRS:-}" ]] && printf '%s' "$POD_CIDRS"; }
capped() { resolve_max_pods && dropin; }
POD_CIDRS='10.42.0.0/24'
if grep -q 'max-pods' <<< "$(capped)"; then fail 'A node caps its pods without being asked'; fi
config="$(KUBELET_MAX_PODS=200 capped)"
grep -Fqx '  - "max-pods=200"' <<< "$config" || fail "KUBELET_MAX_PODS is ignored: $config"
grep -Fqx '  - "kube-reserved=memory=3601Mi"' <<< "$config" || fail 'The pod cap replaces the memory reservation'
printf '%s\n' "$config" > "$DROPIN_PATH"
grep -Fqx '  - "max-pods=200"' <<< "$(capped)" || fail 'A rerun without KUBELET_MAX_PODS drops the pod cap'
if grep -q 'max-pods' <<< "$(KUBELET_MAX_PODS='' capped)"; then fail "An empty KUBELET_MAX_PODS keeps the cap, not K3s's 110"; fi
for value in 0 -1 many 1e3 ' 200' 253; do
    if (KUBELET_MAX_PODS="$value" resolve_max_pods) 2>/dev/null; then fail "KUBELET_MAX_PODS='$value' is accepted"; fi
done
(KUBELET_MAX_PODS=252 resolve_max_pods) || fail 'A /24 pod range refuses 252 pods'
large="$(python3 "$SCRIPT_DIR/lib/sizing.py" kubelet-max-pods --profile large)"
(KUBELET_MAX_PODS="$large" resolve_max_pods) || fail "The large profile's $large pods do not fit a /24 pod range"
# The smallest registered range sets the limit; without the API, the server's K3s flags.
POD_CIDRS='10.42.0.0/24 10.42.1.0/25'
if (KUBELET_MAX_PODS=200 resolve_max_pods) 2>/dev/null; then fail "The cap ignores a node's /25 pod range"; fi
(KUBELET_MAX_PODS=124 resolve_max_pods) || fail 'A /25 pod range refuses 124 pods'
POD_CIDRS=''
cat > "$K3S_UNIT_PATH" <<'UNIT'
ExecStart=/usr/local/bin/k3s \
    server \
        '--kube-controller-manager-arg' \
        'node-cidr-mask-size=23' \
UNIT
(KUBELET_MAX_PODS=500 resolve_max_pods) || fail "The server's node-cidr-mask-size is ignored without the API"
rm "$K3S_UNIT_PATH"
if (KUBELET_MAX_PODS=253 resolve_max_pods) 2>/dev/null; then fail 'Without the API or a K3s flag the range is not /24'; fi
unset -f kubectl

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

# logind must let the kubelet delay the shutdown for the whole order, and the drop-in
# must win over Ubuntu's 30-second cap and the kubelet's own file (last name wins).
[[ "$(shutdown_seconds)" == 150 ]] || fail "The shutdown groups add up to $(shutdown_seconds) s, not 150"
grep -Fqx "InhibitDelayMaxSec=$(shutdown_seconds)" <<< "$(logind_config)" || fail 'logind does not allow the whole shutdown order'
grep -Fqx '[Login]' <<< "$(logind_config)" || fail 'The logind drop-in has no [Login] section'
last="$(printf '%s\n' unattended-upgrades-logind-maxdelay.conf 99-kubelet.conf "$(basename "$LOGIND_PATH")" | LC_ALL=C sort | tail -1)"
[[ "$last" == "$(basename "$LOGIND_PATH")" ]] || fail "logind reads $last after the platform's drop-in"

# The check reads the kubelet's delay lock from logind's inhibitor list.
busctl() { printf '%s\n' 'a(ssssuu) 2 "shutdown" "Unattended Upgrades Shutdown" "Stop ongoing upgrades" "delay" 0 1698 "sleep" "ModemManager" "Reset devices" "delay" 0 1795'; }
if shutdown_armed; then fail 'Another shutdown lock counts as the kubelet'\''s'; fi
busctl() { printf '%s\n' 'a(ssssuu) 1 "shutdown" "kubelet" "Kubelet needs time to handle node shutdown" "delay" 0 42'; }
shutdown_armed || fail "The kubelet's shutdown lock is not recognised"
unset -f busctl

printf '[PASS] Kubelet memory reservation, pod cap and shutdown order\n'
