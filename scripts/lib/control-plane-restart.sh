#!/usr/bin/env bash
# Sequential K3s server restarts that never knowingly drop the etcd majority.
# Sourced by orchestrators running on a control plane with kubectl, jq and
# python3. FLEET_MEMBERSHIP, when set by the caller, pins the expected
# control-plane name/UID map so a membership change aborts the sequence.

# Succeeds only when a fresh Ready majority survives with $1 (or nothing) excluded.
fresh_majority() {
    local excluded="$1" nodes leases
    nodes="$(kubectl --request-timeout=10s get nodes -o json)"
    leases="$(kubectl --request-timeout=10s get leases -n kube-node-lease -o json)"
    python3 - "$excluded" "$nodes" "$leases" "${FLEET_MEMBERSHIP:-}" <<'PY'
import datetime,json,sys
excluded,nodes,leases=sys.argv[1],json.loads(sys.argv[2]),json.loads(sys.argv[3])
planes=[n for n in nodes['items'] if any(k in n['metadata'].get('labels',{}) for k in
 ('node-role.kubernetes.io/control-plane','node-role.kubernetes.io/master'))]
if len(planes)<3 or len(planes)%2!=1: raise SystemExit('An odd membership of at least three control planes is required')
if sys.argv[4] and {n['metadata']['name']:n['metadata']['uid'] for n in planes} != json.loads(sys.argv[4]):
 raise SystemExit('Control-plane membership changed during sequential reconciliation')
now=datetime.datetime.now(datetime.timezone.utc)
by_name={l['metadata']['name']:l for l in leases['items']}
count=0
for n in planes:
 m=n['metadata']; lease=by_name.get(m['name'],{})
 if m['name']==excluded or m.get('deletionTimestamp'): continue
 if not any(c['type']=='Ready' and c['status']=='True' for c in n['status'].get('conditions',[])): continue
 if not any(o.get('kind')=='Node' and o.get('uid')==m['uid'] for o in lease.get('metadata',{}).get('ownerReferences',[])): continue
 if lease.get('spec',{}).get('holderIdentity')!=m['name']: continue
 try: age=(now-datetime.datetime.fromisoformat(lease['spec']['renewTime'].replace('Z','+00:00'))).total_seconds()
 except (KeyError,ValueError,TypeError): continue
 if 0<=age<60: count+=1
if count<len(planes)//2+1: raise SystemExit('A fresh Ready etcd majority must survive the next K3s server restart')
PY
}

# Waits up to five minutes for node $1 (UID $2) to be Ready with a lease
# renewed at or after epoch second $3, i.e. a heartbeat from the new process.
wait_fresh_ready() {
    local name="$1" uid="$2" started="$3" attempt
    for (( attempt=0; attempt<60; attempt++ )); do
        if kubectl --request-timeout=10s get node "$name" -o json | jq -e --arg uid "$uid" \
            '.metadata.uid == $uid and any(.status.conditions[]?; .type == "Ready" and .status == "True")' >/dev/null && \
            kubectl --request-timeout=10s get lease "$name" -n kube-node-lease -o json | \
            jq -e --argjson after "$started" '(.spec.renewTime | sub("\\.[0-9]+Z$";"Z") | fromdateiso8601) >= $after' >/dev/null; then
            return 0
        fi
        sleep 5
    done
    return 1
}
