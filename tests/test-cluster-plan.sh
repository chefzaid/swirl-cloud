#!/usr/bin/env bash
# Guard the installer's topology plan without a cluster: control-plane counts stay
# odd for etcd quorum, target lists are unique and never overlap, a rerun never
# plans removing a registered node, and a cluster without workers schedules on its
# control planes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/cluster-plan.sh
source "$SCRIPT_DIR/lib/cluster-plan.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
error() { printf '%s\n' "$*" >&2; exit 1; }
info() { :; }
warn() { :; }
ask_with_default() { [[ "$2" == Y ]]; }
installer_prompt_value() { fail "prompted for $1 with AUTO_APPROVE=true"; }
INVENTORY=""
cluster_plan_inventory() { [[ -z "$INVENTORY" ]] || printf '%s\n' "$INVENTORY"; }

# plan VAR=value...: run the plan in a subshell; print the outcome or the error.
plan() {
    (
        unset K3S_WORKER_HOSTS K3S_WORKER_IPS K3S_CONTROL_PLANE_HOSTS K3S_CONTROL_PLANE_IPS CONTROL_PLANE_COUNT CLUSTER_NODE_COUNT
        AUTO_APPROVE=true CONTROL_PLANE_SCHEDULABLE=""
        local assignment
        for assignment in "$@"; do
            declare "$assignment"
        done
        configure_cluster_topology_plan
        printf '%s %s %s %s %s %s\n' "$CONTROL_PLANE_COUNT" "$CLUSTER_NODE_COUNT" "$PLANNED_WORKER_COUNT" \
            "$CONTROL_PLANES_TO_ADD" "$WORKERS_TO_ADD" "$CONTROL_PLANE_SCHEDULABLE"
    ) 2>&1
}
expect() {
    local wanted="$1" result
    shift
    result="$(plan "$@")" || true
    [[ "$result" == "$wanted" ]] || fail "plan $* gave '$result', expected '$wanted'"
}
refuse() {
    local message="$1" result
    shift
    if result="$(plan "$@")"; then
        fail "plan $* was accepted: $result"
    fi
    [[ "$result" == *"$message"* ]] || fail "plan $* failed with '$result', expected '$message'"
}

# Target lists.
[[ "$(cluster_plan_target_count "")" == 0 ]] || fail 'An empty list is not zero targets'
[[ "$(cluster_plan_target_count "a,b,10.0.0.3")" == 3 ]] || fail 'Three targets are not counted'
for list in ",a" "a," "a,,b" "a,a" "a, b"; do
    if cluster_plan_target_count "$list" >/dev/null 2>&1; then
        fail "Target list '$list' is accepted"
    fi
done
for value in 1 3 999999999; do
    cluster_plan_positive_integer "$value" || fail "$value is not a positive integer"
done
for value in 0 01 -1 1.5 x 1000000000; do
    if cluster_plan_positive_integer "$value"; then
        fail "$value is a positive integer"
    fi
done

# Columns: control planes, nodes, planned workers, control planes to add, workers to add, schedulable.
expect "1 1 0 0 0 true" # a single fresh server runs workloads
expect "1 3 2 0 2 false" K3S_WORKER_HOSTS=w1,w2
expect "3 3 0 2 0 true" K3S_CONTROL_PLANE_IPS=10.0.0.2,10.0.0.3
expect "3 5 2 2 2 false" K3S_CONTROL_PLANE_HOSTS=c2,c3 K3S_WORKER_HOSTS=w1,w2
expect "1 3 2 0 2 true" K3S_WORKER_HOSTS=w1,w2 CONTROL_PLANE_SCHEDULABLE=yes
expect "1 4 3 0 3 false" CLUSTER_NODE_COUNT=4 CONTROL_PLANE_SCHEDULABLE=controller-only

refuse "Use only one of K3S_WORKER_HOSTS and K3S_WORKER_IPS" K3S_WORKER_HOSTS=w1 K3S_WORKER_IPS=10.0.0.9
refuse "Use only one of K3S_CONTROL_PLANE_HOSTS" K3S_CONTROL_PLANE_HOSTS=c2 K3S_CONTROL_PLANE_IPS=10.0.0.2
refuse "must not overlap" K3S_CONTROL_PLANE_HOSTS=c2,c3 K3S_WORKER_HOSTS=w1,c3
refuse "Invalid worker target list" K3S_WORKER_HOSTS=w1,w1
refuse "odd positive integer" CONTROL_PLANE_COUNT=2
refuse "odd positive integer" CONTROL_PLANE_COUNT=0
refuse "at least CONTROL_PLANE_COUNT" CONTROL_PLANE_COUNT=3 CLUSTER_NODE_COUNT=2
refuse "must allow workloads on its control planes" CONTROL_PLANE_SCHEDULABLE=false
refuse "must be true or false" CONTROL_PLANE_SCHEDULABLE=maybe
refuse "exclude the bootstrap host" K3S_CONTROL_PLANE_HOSTS=c2,c3 CONTROL_PLANE_COUNT=3 CLUSTER_NODE_COUNT=3 \
    K3S_CONTROL_PLANE_HOSTS=c2,c3,c4
refuse "cannot exceed 1 planned workers" K3S_WORKER_HOSTS=w1,w2 CLUSTER_NODE_COUNT=2

# Reruns count every registered node, NotReady ones included, and never plan removals.
INVENTORY=$'cp1\tcontrol-plane\ncp2\tcontrol-plane\ncp3\tcontrol-plane\nw1\tworker'
expect "3 4 1 0 0 false"
expect "3 6 3 0 2 false" CLUSTER_NODE_COUNT=6
expect "5 6 1 2 0 false" CONTROL_PLANE_COUNT=5
refuse "already has 3 registered control planes" CONTROL_PLANE_COUNT=1
refuse "already has 1 registered workers" CLUSTER_NODE_COUNT=3
refuse "must cover 2 missing workers" K3S_WORKER_HOSTS=w2 CLUSTER_NODE_COUNT=6
# A failed enrollment can leave an even number of servers: the default stays odd.
INVENTORY=$'cp1\tcontrol-plane\ncp2\tcontrol-plane'
expect "3 3 0 1 0 true"

printf '[PASS] Cluster topology plan\n'
