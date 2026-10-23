# High Availability

Enable and verify HA across independent physical hosts. Adding control planes
alone replicates no shared service or application. Hosts, the
[private transport](node-enrollment.md#private-node-network) and scheduling are
in [node enrollment](node-enrollment.md); Cloudflare is in
[DNS and ingress](../dns.md#cloudflare).

## Availability boundaries

| Component | Opt-in behavior and limits |
| --- | --- |
| K3s | Three or more control planes with an odd embedded-etcd membership; a majority must remain available. kubectl and joining nodes use the [floating API endpoint](node-enrollment.md#kubernetes-api-endpoint) |
| Public ingress | Independent [Traefik/Tunnel pairs](../dns.md#ha-public-ingress) on control planes; public DNS targets the tunnel |
| PostgreSQL | CloudNativePG primary and two standbys on different hosts; each commit requires one synchronous standby, and the canonical Service follows the primary |
| Kafka | Three native KRaft controllers and brokers, replicated topics and an enforced minimum in-sync replica count after migration |
| Vault | Three Raft voters on different control planes, with recovery material available on every verified control plane |
| Shared Redis | Three Redis/Sentinel servers and redundant HAProxy endpoints; replication is asynchronous and recent cache writes can be lost |
| CoreDNS | Three replicas on three hosts, at least two available during voluntary disruption; an admission policy keeps placement and count when K3s reapplies them |
| External Secrets | Two replicas per component; main and certificate controllers elect leaders, while both webhook replicas accept requests |
| Keycloak | Replicated identity service; still depends on PostgreSQL |
| Argo CD | Replicated API, repository, ApplicationSet and cache services; see [Argo CD operations](../deployment.md#argo-cd-operations) for controller recovery and Helm ownership |
| External applications | Each repository owns its availability profile, placement, shared state, secrets and background work |
| Persistent singleton tools | GitLab/Registry, SonarQube Community, Odoo and other unreplicated services restart their writer with its volume elsewhere, with an outage ([node fencing](node-fencing.md)) |

The target is surviving one host failure while the rest keep quorum and
capacity. This is not site disaster recovery; Cloudflare, the private transport
and external identity/email/storage providers remain dependencies. Replicas and
snapshots do not replace
[offsite backups and restore testing](../operations.md#backups-and-recovery).
See the [topology diagram](../architecture.md#node-topology).

## Prepare hosts and capacity

- At least three Ready, uncordoned control planes and three storage-eligible
  hosts on the same [private node network](node-enrollment.md#private-node-network).
- Three control planes can also run applications and storage. With
  `CONTROL_PLANE_SCHEDULABLE=false`, add at least three workers; ingress and
  Vault tolerate the control-plane taint.
- Replicas spread across hostnames. The remaining hosts must carry the load
  after one fails, including builds, rebuilds and migration copies. Three
  Longhorn replicas of three database PVCs is nine database copies; check real
  usage, not PVC size ([storage placement](node-enrollment.md#scheduling-and-storage)).
- Before a failure test, confirm
  [admin access](node-enrollment.md#control-plane-administration) through a
  surviving control plane and kubectl through the
  [API endpoint](node-enrollment.md#kubernetes-api-endpoint).

## Activate in order

Run from an authenticated control-plane checkout with the normal
[installer inputs](../deployment.md#unattended-installation). Keep journals,
dumps, credentials and recovery files in private durable directories outside
Git. Schedule maintenance for the data cutovers and the move to Tunnel ingress.

1. **Install normally, then enroll the extra hosts.** Leave
   `HIGH_AVAILABILITY_ENABLED` unset while doing so:

   ```bash
   ./add-node.sh --role control-plane --mode remote --count 2
   # If control planes will not run application/storage workloads:
   ./add-node.sh --role worker --mode remote --count 3
   ```

   See [remote enrollment](node-enrollment.md#remote-enrollment). Confirm all
   hosts are Ready with the intended
   [scheduling policy](node-enrollment.md#scheduling-and-storage).

2. **Migrate PostgreSQL** with [PostgreSQL HA](postgres-ha.md). Keep the
   generated full active `postgresHa` profile and the retained source volume.
   Never resume an old writer after new commits reach the HA cluster.

3. **Migrate Kafka** with [Kafka HA](kafka-ha.md) and keep its generated full
   active `kafkaHa` profile. General HA activation refuses an unfinished
   PostgreSQL or Kafka migration.

4. **Migrate Vault** with
   [the singleton migration](../security.md#migrating-an-existing-singleton)
   (deployed compatible image, `VAULT_HA_MIGRATE_EXISTING=true`). Verify three
   healthy voters, then
   [distribute recovery material](../security.md#host-unseal-service). Keep
   recovery keys and a tested Raft backup outside the cluster too.

5. **Reconcile the shared HA profile.** Put the full reviewed `postgresHa` and
   `kafkaHa` maps (never just `enabled`, never credentials) in one persistent
   values file. Supply the Cloudflare token and Access inputs
   ([Cloudflare](../dns.md#cloudflare),
   [Tunnel prerequisites](../dns.md#ha-public-ingress)) and the final node
   counts and scheduling. Then run either:

   ```bash
   export HIGH_AVAILABILITY_ENABLED=true
   export PLATFORM_HA_VALUES_FILE=/path/to/reviewed-platform-ha-values.yaml
   ./install-control-plane.sh --yes

   # Alternative for an installed platform, using the same exported inputs:
   ansible-playbook -i ansible/inventory ansible/deploy.yml \
     -e configure_cloudflare=true
   ```

   Shared resources and the platform Argo Application render from the same
   profile; keep it in the desired configuration so GitOps retains the migrated
   services. The [HA storage policy](node-enrollment.md#scheduling-and-storage)
   and CoreDNS are reconciled too. Wait for replica health and the
   [public-ingress checks](../dns.md#ha-public-ingress). Shared Redis starts
   empty (its old PVC is kept); cache and rate-limit state rebuild.

6. **Activate application availability** per each repository's guide once
   shared services are healthy. Replicas need separate hosts, shared durable
   state and secrets, readiness probes and PDBs; schema migrations finish before
   new writers start; worker concurrency, retries, leases and side effects are
   defined before scaling. Persist the profile in the repository's Argo CD
   Application. The platform does not pick overlays or migrate app data.

## Reconciliation and recovery

`infra/swirl-cloud-topology` stores the mode; enrollment, installer and Ansible
runs inherit it when `HIGH_AVAILABILITY_ENABLED` is unset. A supplied profile
must match the PostgreSQL and Kafka cutover records. Incomplete migrations and
implicit downgrades stop before workloads change. `--allow-ha-disable` is not a
database, Vault, ingress or application rollback.

Longhorn does not fence writers. HA disables Kubernetes' timeout-based force
detach, so a singleton on an unreachable host waits instead of starting a
competing writer. Prove the old host cannot write before moving its volume.
[Node fencing](node-fencing.md) automates this with enrolled Redfish BMCs; an
OVH account or provider API credentials are not enough. Activation sets this
policy on every control plane, restarting K3s one server at a time with an etcd
majority kept, inside the maintenance window.

PDBs cover voluntary disruption, not hardware loss.

## Verify before relying on HA

After activation, run the read-only checks from an authenticated control plane:

```bash
python3 scripts/verify-high-availability.py
# Also require configured fencing coverage for automatic singleton recovery:
python3 scripts/verify-high-availability.py --require-fencing
```

They check shared-service placement, quorum and storage copies (and, with
`--require-fencing`, inventory coverage and a completed fencing run). They do
not check applications, enable HA or power off hosts.

Then run a controlled one-host failure drill, one failure at a time:

1. Include the original control plane and a current database/consensus leader.
2. Check kubectl through the API endpoint from an administrator device, public
   requests, authenticated sessions, database writes, Kafka produce/consume and
   Vault access.
3. On the public path, check configured hostnames, OIDC redirects, trusted
   client IPs, Registry login and image push/pull, within the
   [Tunnel limits](../dns.md#ha-public-ingress).
4. Exercise singleton recovery only after fencing is verified with a real
   power-control test.
5. Restore healthy quorum and storage replicas before the next drill.

Finally restore a backup into an isolated environment and compare data. Each
application repository verifies its own replicas and writes.

Defaults live in [Helm values](../../k8s/values.yaml),
[HA templates](../../k8s/templates) and [component profiles](../../config).
