# Major-Version Upgrades

Minor and patch updates follow [image updates](../operations.md#image-updates)
and the [runtime constraints](../operations.md#runtime-and-recovery-constraints).
This guide covers the upgrades that migrate data and cannot be undone by changing
an image back: PostgreSQL, Odoo, Elasticsearch, GitLab and the servers' Ubuntu
release. MongoDB, Kafka, SonarQube and K3s have their steps in the runtime
constraints table.

Every procedure below starts the same way and keeps a way back:

1. Read the upstream upgrade notes for every version crossed.
2. Rehearse on a copy restored from the last backup
   ([restore after losing the host](../operations.md#restore-after-losing-the-host)),
   on a disposable host, and time each step.
3. Before touching live data: run the nightly backup now and check it, then take
   a [checkpoint](../operations.md#checkpoints) and announce the window with a
   [notice](../operations.md#notices-to-the-people-affected).
4. Afterwards: `./install.sh --status`, sign in to each affected tool, and keep
   the checkpoint until the next nightly backup has run.

## PostgreSQL (single server)

The data directory is versioned (`postgres-v18-pvc`, `PGDATA=/var/lib/postgresql/18/docker`
in [`postgres.yaml`](../../k8s/datastores/postgres.yaml)), so a new major starts on
a new volume and the old one stays untouched until the upgrade is accepted.

1. Stop the writers: pause Argo CD's automatic sync, then scale Keycloak,
   SonarQube, Zammad, Vaultwarden, Odoo and the applications' Deployments to zero
   (GitLab keeps its own database and is not affected).
2. Dump everything: `scripts/backup-k3s.sh` writes `application-data/postgresql.sql.gz`
   with `pg_dumpall`; keep that file aside.
3. In one commit: the new image (same Debian release, its digest), a new claim
   `postgres-v<major>-pvc` and `PGDATA=/var/lib/postgresql/<major>/docker`. Keep
   the old claim declared, with `argocd.argoproj.io/sync-options: Prune=false`,
   so Argo CD does not delete it. Update the claim name in
   `k8s/files/sizing/large.yaml` and `scripts/configure-postgres-ha.py`.
4. Sync. The new server initializes an empty cluster; restore the dump into it as
   the administrator, with statement logging off for the session (the dump sets
   role passwords): `gunzip -c postgresql.sql.gz | kubectl -n infra exec -i deploy/postgres -c postgres -- sh -c 'PGOPTIONS="-c log_statement=none" psql -X -U "$POSTGRES_USER" -d postgres'`.
   Errors for objects that already exist (the administrator role, the `keycloak`
   database created at initialization) are expected; any other error stops the
   upgrade.
5. Check every database: `psql -l`, each role's `CONNECTION LIMIT`, extensions
   (`\dx` in each database) and collation versions
   (`SELECT datname, datcollversion FROM pg_database`).
6. Scale the services back, resume automatic sync, and verify.

**Way back:** revert the commit. The old claim, never written by the new server,
comes back with its data; the dump covers anything written in between. Remove the
old claim only after a week without issue.

**HA profile (CloudNativePG):** CloudNativePG 1.26 and later upgrade the major
version offline with `pg_upgrade` when the cluster's `imageName` changes to a new
major; the cluster is down during it. Take a backup first and follow
[PostgreSQL HA](postgres-ha.md); the way back is restoring that backup.

## Odoo

Odoo moves one major version a year (19.0 now). Its database and filestore are
migrated by OpenUpgrade (OCA, for the Community edition); Odoo SA's own upgrade
service covers Enterprise only.

1. Check that OpenUpgrade has the target version's scripts for every installed
   module (`installed` modules in `ir_module_module`), and that the platform's
   add-ons ([`odoo-addons`](../../k8s/files/odoo-addons)) and any third-party ones
   exist for the new version; port them first.
2. Rehearse on a copy: restore the `odoo` database and filestore into a scratch
   Odoo of the current version, run OpenUpgrade's migration into the target
   version, then open the main menus, the last closed accounting period and
   recent invoices.
3. Live: stop Odoo, back up, run the same migration on the `odoo` database, change
   the image in [`odoo.yaml`](../../k8s/corp/odoo.yaml), and start it.

**Way back:** stop Odoo, restore the database and filestore from the backup taken
in step 3, and revert the image.

## Elasticsearch and Kibana

Elasticsearch moves one major at a time, from the last minor of the previous
major, and reads indices created by the previous major only; older ones must be
reindexed first (the Upgrade Assistant in Kibana lists them). Kibana and Logstash
move with it, to the same version. The platform's indices are logs and audit
records with their own retention: check that the long-kept ones (`identity-audit-*`,
`database-audit-*`, `security-findings-*`) are readable by the target version, and
reindex those that are not. **Way back:** an index written by a newer version
cannot be read by an older one, so restore the evidence snapshot
(`swirl-cloud-evidence`) and the volume backup instead.

## GitLab

GitLab must pass through its required upgrade stops (GitLab's upgrade path tool
lists them for the installed version). At each stop: change the image, wait for
the pod, then wait until **Admin, Monitoring, Background migrations** shows none
pending (`gitlab-rake gitlab:background_migrations:status`) before the next.
Keep the native backup from before the first stop with its `gitlab.rb` and
`gitlab-secrets.json` (the archive's `gitlab/` holds both). **Way back:** restore
that backup into the version it came from.

## Ubuntu release of the servers

One server at a time, on a long-term release only. With more than one server,
drain it (`kubectl drain <node> --ignore-daemonsets --delete-emptydir-data`),
check Longhorn's volumes are healthy elsewhere, upgrade (`do-release-upgrade`) or
rebuild it from the init script and rejoin it, rerun the host baseline
(`scripts/configure-node-security.sh`), then `kubectl uncordon`. On a single
server the platform stops for the upgrade; rebuilding means restoring the platform
from its backups. **Way back:** rebuild on the previous release and rejoin, or
restore.
