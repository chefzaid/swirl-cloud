# PostgreSQL HA Profile

This opt-in profile replaces the single PostgreSQL writer with a CloudNativePG
(CNPG) primary and two synchronous standbys on three hosts (three Ready control
planes and three storage hosts required). General HA does not migrate data; the
existing Deployment and PVC stay until the migration below.

- [postgres-ha-values.yaml](../../config/postgres-ha-values.yaml) is the database
  profile; [postgres-ha-operator-values.yaml](../../config/postgres-ha-operator-values.yaml)
  is the operator release from the pinned
  [official chart](https://github.com/cloudnative-pg/charts).
- The image keeps Debian Bookworm and the PostgreSQL major version. Validate
  tools, locales, extensions and operator compatibility before swapping it.
- `postgresHa.enabled=true, active=false` stages the cluster with application
  TCP access rejected. `active=true` points the existing `infra/postgres`
  Service at the current CNPG primary and scales the original Deployment to zero.
  `postgres-v18-pvc`, ConfigMaps and the Service identity are retained.
- The CNPG Cluster has pruning/deletion protection. Removing the profile is not
  a migration or PVC cleanup.

Replication is synchronous `ANY 1` with required durability, failover quorum,
primary isolation checks, strict hostname anti-affinity and PDBs; the operator
runs three instances. Writes wait while no synchronous standby is available, so
acknowledged commits survive one host failure; clients must reconnect. Three
PVCs at three Longhorn replicas is nine database copies: check free capacity
and rebuild headroom per host.

## Prepare

Needs Python 3, PyYAML, Helm and an admin `kubectl` context. `postgres-secret`
must come from Vault/External Secrets; its credentials are reused. The state
directory must be private, durable, big enough for all dumps, outside Git and
in encrypted offsite backups.

```bash
export HIGH_AVAILABILITY_ENABLED=true
STATE_DIR=/var/backups/swirl-cloud/postgres-ha-migration
sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" "$STATE_DIR"
python3 scripts/configure-postgres-ha.py prepare --state-dir "$STATE_DIR"
```

`prepare` installs CNPG into `cnpg-system`, creates the bootstrap Secret,
applies only the PG HA template, and waits for three Ready instances on
different hosts with both standbys streaming. Keep `staged-values.yaml` with the
journal. Routine reconciliation stays paused (installer and Ansible refuse
unfinished checkpoints) until a verified cutover and the full active
`PLATFORM_HA_VALUES_FILE`.

For a brand-new installation, run `fresh` after `prepare` and before any legacy
PostgreSQL Deployment/PVC or database client exists:

```bash
python3 scripts/configure-postgres-ha.py fresh --state-dir "$STATE_DIR"
```

It keeps the administrator as superuser, creates the `keycloak` database and
refuses an existing Deployment or PVC. Continue with
[cutover](#cut-over-and-recover); application databases come afterwards.

## Existing database migration

Schedule downtime. Pause auto-sync on every Argo Application in `infra`, let
running syncs finish, and stop writers and scheduled jobs. Then run:

```bash
python3 scripts/configure-postgres-ha.py migrate \
  --state-dir "$STATE_DIR" --maintenance
```

It checks the GitOps maintenance gate, rejects new TCP connections in the
source's persistent `pg_hba.conf` and ends client sessions, keeping local access.

Roles (names, attributes, password hashes, memberships), owners, ACLs, settings,
schemas, data, large objects and sequences are preserved. Grants made by the
original initdb superuser are recorded as granted by CNPG's `postgres` role;
the original administrator stays a superuser with the same credentials.

It fails before fencing on unsupported layouts:

- CNPG-reserved role names, non-default tablespaces or non-connectable databases
- password hashes that need a SCRAM migration
- active subscriptions, replication slots, foreign tables or prepared transactions
- a PostgreSQL downgrade

Stop custom background workers and extension writers first. Template database
changes need a separate plan.

Each database is dumped (custom format, plus globals), restored into the
isolated target and compared before `active-values.yaml` is written. Dumps stay
in the state directory; nothing is uploaded and the original PVC is kept.

## Cut over and recover

Store the full reviewed `postgresHa` map from `active-values.yaml` in the
platform's persistent Helm values. Keep Argo sync paused; do not apply it early.
Then run:

```bash
python3 scripts/configure-postgres-ha.py cutover --state-dir "$STATE_DIR" \
  --maintenance --desired-values /path/to/reviewed-platform-ha-values.yaml
```

Cutover re-verifies, stops the old Deployment, switches the Service selector
and opens CNPG to applications. It records the profile in
`infra/postgres-ha-state` (`data.postgresHa`, `data.phase=active`); a partial
run records `cutover-started` and installers then stop. Reconcile GitOps,
resume writers and check application health. Backups and password rotation
follow the current primary.

Continue with [the ordered HA workflow](high-availability.md#activate-in-order).
For PostgreSQL alone, unset `HIGH_AVAILABILITY_ENABLED` afterwards and keep the
active `PLATFORM_HA_VALUES_FILE`; general HA also needs the Kafka migration.

Before cutover, a failed import leaves the original fenced and the target
isolated. To reconnect the original in the same maintenance window:

```bash
python3 scripts/configure-postgres-ha.py abort --state-dir "$STATE_DIR" --maintenance
```

Abort restores the source HBA, removes its checkpoint ConfigMap and keeps all
copies. Restore the previous platform profile before reconciling, and inspect
the failed target before retrying.

> **Warning:** after cutover, never restart the old writer as a rollback; it
> lacks the new commits. Recover the HA cluster or run a new, verified reverse
> migration. If cutover stopped partway, inspect the journal, Service selector,
> Cluster policy and old Deployment, then finish the cutover manually.

Replicas are not backups: keep encrypted offsite backups and test restores
(there is no continuous WAL archiving). Run a multi-host failover drill; the SQL
canary only validates migration semantics:

```bash
python3 tests/test-postgres-ha.py
# After explicitly pulling the profile's pinned public image:
python3 tests/test-postgres-ha.py --docker
```

Upstream: [synchronous replication](https://cloudnative-pg.io/docs/1.30/replication/),
[primary election and isolation](https://cloudnative-pg.io/docs/1.30/failover/),
[scheduling](https://cloudnative-pg.io/docs/1.30/scheduling/),
[logical import limitations](https://cloudnative-pg.io/docs/1.30/database_import/).
