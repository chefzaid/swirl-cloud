# Kafka High Availability

By default Kafka runs one static KRaft controller and one broker. The opt-in
profile runs three controllers and three brokers on distinct hosts:

- Every topic, including internal offset/transaction topics, gets three
  replicas.
- Writes require at least two in-sync replicas (`min.insync.replicas=2`), and
  unclean leader election is disabled.
- Producers must use `acks=all`; check each application before activation.
- A controller majority and enough broker ISR must stay available.

It is a forward migration with Kafka's native
[dynamic quorum commands](https://kafka.apache.org/43/operations/kraft/); no
cluster is reformatted. The `kafka-controller` and `kafka` StatefulSets, their
ordinal-zero PVCs, controller ID `3000`, broker ID `1` and the cluster UUID are
kept. New controllers are `3001`/`3002`; new brokers `2`/`3`.

## Prepare

1. Meet the host and capacity prerequisites in
   [high availability](high-availability.md): three Ready control planes, at
   least three schedulable hosts, and room for new broker data and its Longhorn
   replicas.
2. Upgrade the singleton to the pinned Kafka image first; the helper refuses
   to combine an image change with the migration.
3. Back up and verify Kafka metadata and records outside these PVCs. The
   journal **is not a Kafka record backup**.
4. Schedule downtime. Stop all producers, consumers and topic jobs. Pause
   auto-sync on all Argo Applications until the final values are persisted and
   verified.

`--desired-values` takes rendered installation values
([installation inputs](../deployment.md#unattended-installation)), not the
placeholder `config/kafka-ha-values.yaml`. Keep it and the journal outside Git:

```bash
umask 077
HIGH_AVAILABILITY_ENABLED=false scripts/render-cluster-config.sh \
  --output /path/to/private/rendered \
  --domain "$PLATFORM_DOMAIN" --internal-domain "$INTERNAL_DNS_ZONE" \
  --gitops-repository "$GITOPS_REPOSITORY_URL" \
  --cloudflare-access-team "$CLOUDFLARE_ACCESS_TEAM_NAME"

python3 scripts/configure-kafka-ha.py prepare \
  --state-dir /path/to/private/kafka-ha \
  --desired-values /path/to/private/rendered/k8s/values.yaml
```

`prepare` changes nothing; it writes local evidence and
`review-active-manifests.yaml`. Review them, then migrate:

```bash
python3 scripts/configure-kafka-ha.py migrate \
  --state-dir /path/to/private/kafka-ha \
  --desired-values /path/to/private/rendered/k8s/values.yaml \
  --maintenance --backup-confirmed
```

The migration:

1. Records cluster, StatefulSet and PVC identities, upgrades the quorum to
   `kraft.version=1`, then replaces `controller.quorum.voters` with
   bootstrap-server discovery.
2. Restarts controller and broker dynamically; original storage missing its
   metadata refuses to start.
3. Adds controllers and brokers up to three each. New controllers format as
   observers (`--no-initial-controllers`) and join via `add-controller` after
   zero-lag catch-up.
4. Reassigns every partition to all brokers. After full ISR it sets
   `min.insync.replicas=2` and disables unclean election on topics and the
   cluster default, removing broker election overrides (and broker min-ISR
   overrides when ELR is off; with ELR on, Kafka accepts only the cluster
   level). Pod settings give future topics and transaction state three
   replicas and min ISR 2.
5. Verifies quorum, brokers, placement and policies, records the active profile
   and writes `active-values.yaml`.

Only Kafka resources change; each phase merges `kafkaHa` into the platform
Argo Application. `infra/kafka-ha-state` blocks ordinary installation until the
migration completes. Store the **complete** active map in the desired values
before resuming sync ([activation order](high-availability.md#activate-in-order)).
Reuse the same journal for retries, one operator at a time (it is locked and
bound to the cluster); never clone it for a concurrent migration.

## Interrupted migration and recovery

A failure stops in the recorded phase. Fix capacity, placement or readiness,
let any reassignment finish, and re-run the same command. Never create a new
journal, reformat storage, shrink the voter list or restore the static manifest.
The native upgrade has no rollback, and manifests cannot restore old metadata.

- Original ordinals refuse empty storage; a lost original PVC needs explicit
  Kafka recovery even if copies exist.
- Controller storage has a directory identity; replacing a failed voter needs
  Kafka's membership recovery, not just a new volume.
- The helper never deletes retained data.

For a new installation, `fresh` replaces `prepare`/`migrate`. It takes the
same `--maintenance --backup-confirmed` flags, refuses any existing Kafka
StatefulSet, checkpoint or PVC, generates a new UUID, initializes the first
controller/broker and runs the same expansion. It cannot reset a cluster.

## Verify

The read-only verifier needs no journal:

```bash
python3 scripts/configure-kafka-ha.py verify
```

It requires an active checkpoint, all six pods Ready on distinct hosts within
each role, the expected three voters and brokers, three replicas and full ISR on
every partition, and the effective policy. It fails while a host is down, even if
the remaining quorum still serves.

Then run the [host-failure drill](high-availability.md#verify-before-relying-on-ha):
check acknowledged produce/consume after losing the original host, and restore
full health before the next failure.

`python3 tests/test-kafka-ha.py` runs offline safety checks (also in repository
validation). Before changing Kafka versions or migration logic, rehearse
prepare/migrate/verify on a disposable cluster with sample records.
