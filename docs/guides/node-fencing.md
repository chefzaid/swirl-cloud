# Recover A Persistent Singleton After A Host Failure

Single-writer services such as GitLab Omnibus and SonarQube Community are not
replicated by [the HA profile](high-availability.md); they restart with their
Longhorn volume on a surviving host. A network partition does not prove the old
writer stopped, and storage replication cannot authorize that move.

The optional fencing job powers off an inventoried host through its Redfish BMC,
verifies it is off, then applies `node.kubernetes.io/out-of-service:NoExecute`
so Kubernetes removes stranded pods and detaches volumes
([non-graceful shutdown](https://kubernetes.io/docs/concepts/cluster-administration/node-shutdown/#non-graceful-node-shutdown)).
It never deletes nodes, force-deletes pods, removes the taint or restarts a
host. Recovery still means an outage.

## Requirements

- The HA topology (three or more control planes), healthy Longhorn copies on
  separate hosts and spare capacity.
- On every host that may run a singleton, a Redfish ComputerSystem with a
  stable UUID matching Kubernetes, HTTPS with a trusted CA, explicit `ForceOff`
  and a reliable `PowerState`.
- BMCs reachable from surviving control planes over a separate management
  network, with an account limited to reading that system and powering it off.

Anything else, including an OVHcloud account or SSH access, means manual
recovery. Disable automatic power-on, watchdog reboots and other power
controllers: a fenced host must stay off until an operator recovers it. The job
cannot stop someone powering it on later.

## Prepare control-plane storage safety

Kubernetes normally force-detaches volumes after a timeout. Disable that on all
control planes first, from one with private SSH to the others:

```bash
HIGH_AVAILABILITY_ENABLED=true bash scripts/configure-ha-control-planes.sh \
  --reconcile --node-network-cidr 10.40.0.0/24 \
  --control-plane-ip 10.40.0.10 --ssh-user admin
```

`--ssh-port` and `--identity-file` are optional. Run one reconciliation at a
time.

- It writes `/etc/rancher/k3s/config.yaml.d/99-swirl-cloud-ha-storage-safety.yaml`
  with `kube-controller-manager-arg+: [disable-force-detach-on-timeout=true]`,
  rejecting custom config locations and conflicting controller arguments.
- It restarts K3s one control plane at a time, only where the policy is
  unverified and the drop-in is newer than the running K3s, keeping a Ready
  majority. Missing verification logs with unchanged config need review.
- When the startup log confirms the flag, the node annotation
  `node.swirl-cloud.io/fenced-detach-policy` becomes `verified`.

The installer and Ansible use the same helper; new HA control planes get the
drop-in before K3s starts. A local join needs `HIGH_AVAILABILITY_ENABLED=true`
and a fleet reconciliation afterwards. Keep Longhorn's
`node-down-pod-deletion-policy` at `do-nothing`, and run no automation that
forces detach or pod deletion before physical fencing.

## Enable an explicit inventory

Keep BMC credentials and cluster identities in a private directory outside Git.
Create `inventory.json`, replacing every example value:

```json
{
  "version": 1,
  "controlPlanes": {
    "cp-01": "00000000-0000-0000-0000-000000000001",
    "cp-02": "00000000-0000-0000-0000-000000000002",
    "cp-03": "00000000-0000-0000-0000-000000000003"
  },
  "nodes": [
    {
      "name": "cp-01",
      "nodeUID": "00000000-0000-0000-0000-000000000001",
      "systemUUID": "10000000-0000-0000-0000-000000000001",
      "computerSystemURL": "https://bmc-01.example.internal/redfish/v1/Systems/1",
      "username": "fencing",
      "password": "REPLACE_IN_PRIVATE_FILE",
      "caFile": "bmc-ca.pem"
    }
  ]
}
```

- `controlPlanes`: the exact registered odd membership with node UIDs.
- `nodes`: only the permitted fencing targets, one entry per host.
- `kubectl get nodes -o json` shows UIDs and hardware identities.
- The CA certificate sits beside the inventory under its `caFile` name.
- Owner-only directory, credential files mode `0600`. Never put credentials in
  Helm values or command arguments.

Check the inventory against Kubernetes without contacting a BMC:

```bash
python3 k8s/files/fence-unresponsive-nodes.py --check-inventory \
  --inventory /private/fencing/inventory.json --allowed-nodes cp-01
```

This does not test BMC access or power-off permission; check those with the
vendor's tools. Create the Secret without printing it:

```bash
kubectl -n infra create secret generic node-fencing-inventory \
  --from-file=inventory.json=/private/fencing/inventory.json \
  --from-file=bmc-ca.pem=/private/fencing/bmc-ca.pem \
  --dry-run=client -o yaml | kubectl apply -f -
```

Add this non-secret map to the reviewed `PLATFORM_HA_VALUES_FILE` from the
[HA activation workflow](high-availability.md):

```yaml
nodeFencing:
  enabled: true
  inventorySecret: node-fencing-inventory
  nodeNames: [cp-01]
```

`nodeNames` must match the inventory exactly. RBAC allows node patches only
for these names; the Secret is mounted, with no API access to Secrets. The job
exists only when global HA and fencing are both enabled.

Changing control-plane membership pauses fencing until the Secret is refreshed
with the new names and UIDs. A replaced target also needs its new UID and
verified hardware UUID.

## Failure handling and return to service

The job runs every minute and acts on at most one host per run:

- A candidate must be non-Ready with a stale node Lease for at least five
  minutes.
- A majority of the pinned control planes must be Ready, with Lease renewals
  under a minute old.
- Membership, quorum and target identity are rechecked before and after
  power-off.
- The only power action is
  [Redfish `ForceOff`](https://www.dmtf.org/sites/default/files/standards/documents/DSP0268_2024.4.html).
  HTTPS redirects, untrusted certificates, unexpected reset URLs and unconfirmed
  power state stop recovery.
- Requests and jobs are time-limited; failures stop without an unsafe detach.
  Nodes already tainted out of service are skipped.

Inspect `kubectl -n infra logs job/<node-fencing-job>` and the target's taints,
then confirm the replacement pod, volume and service are healthy. A failed run
may have powered the host off without tainting it; check physical state first.

Returning the host is manual: find the fault, confirm the old workload owns no
volume, and recover the host with the taint in place. Remove it only once node,
storage attachments and applications are healthy:

```bash
kubectl taint node cp-01 node.kubernetes.io/out-of-service:NoExecute-
```

Without a supported BMC, keep fencing disabled. Confirm the host is off and
cannot restart before following the non-graceful shutdown procedure by hand; a
NotReady node or failed SSH probe is not proof.
