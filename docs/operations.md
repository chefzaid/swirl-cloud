# Operations Runbook

Run cluster commands from a configured control-plane shell. Installation and
GitOps changes are in [deployment](deployment.md); credentials and Vault recovery
in [security](security.md).

## Services and URLs

Homepage (`https://cloud.<your-domain>`) lists every service, including
application-owned entries. Replace `<your-domain>` with the installation domain.
Administrative UIs use the access controls in [security and identity](security.md).

| Service | URL | Purpose |
|---|---|---|
| Odoo | `https://odoo.<your-domain>` | ERP and CRM |
| Homepage | `https://cloud.<your-domain>` | Service catalog and cluster status |
| GitLab | `https://gitlab.<your-domain>` | Source, CI, artifacts, and packages |
| Container Registry | `https://registry.<your-domain>/v2/` | OCI image API; browse images in GitLab |
| Argo CD | `https://argocd.<your-domain>` | GitOps delivery |
| SonarQube | `https://sonarqube.<your-domain>` | Source quality analysis |
| Grafana | `https://grafana.<your-domain>` | Metrics and security dashboards |
| Kibana | `https://kibana.<your-domain>` | Logs and audit dashboards |
| Keycloak | `https://keycloak.<your-domain>/auth/admin/master/console/` | Identity administration |
| Vault | `https://vault.<your-domain>` | Secrets and policies |
| Longhorn | `https://longhorn.<your-domain>` | Volumes, snapshots, and backups |
| Portainer | `https://portainer.<your-domain>` | Kubernetes management |
| DBGate | `https://dbgate.<your-domain>` | PostgreSQL, MongoDB, and Redis administration |
| Kafbat UI | `https://kafka.<your-domain>` | Kafka administration |
| Zammad | `https://support.<your-domain>` | IT help desk: tickets for help and access requests ([access groups](security.md#access-groups)) |
| Mailpit | `https://cloud.<your-domain>/mailpit/` | Email sent by `int` and `uat`, caught instead of delivered ([guide](guides/platform-mail.md#non-production-mail)) |
| Trivy reports | `https://grafana.<your-domain>/d/trivy-security/trivy-security-reports` | Current workload and cluster findings |
| Lynis reports | `https://kibana.<your-domain>/app/dashboards#/view/lynis-security-audits` | Host audit history |
| Compliance evidence | `https://grafana.<your-domain>/d/compliance-evidence` | Control status per framework ([guide](guides/compliance-evidence.md)) |
| Cost showback | `https://grafana.<your-domain>/d/cost-showback` | Cost per environment, namespace and workload ([guide](guides/cost-showback.md)) |
| Service levels | `https://grafana.<your-domain>/d/service-levels` | Availability and error budget per public endpoint ([guide](guides/service-levels.md)) |
| Platform report | `https://grafana.<your-domain>/d/platform-report` | Monthly availability, cost and compliance ([guide](guides/platform-reports.md)) |
| Capacity planning | `https://grafana.<your-domain>/d/capacity-planning` | Requested and used capacity, headroom and what to add ([guide](guides/capacity-planning.md)) |

## Cluster status

- `./install.sh --status` prints a read-only PASS/WARN/FAIL report
  ([checks](deployment.md#verify-the-result)) with a `Next:` command per problem.
- `./install.sh --support-bundle` writes a private archive (mode `600`) under
  `~/.local/state/swirl-cloud/support/` with that report, versions, cluster and
  storage state, unhealthy pod logs and the setup hub's latest run logs. It never
  reads Kubernetes Secrets and redacts token-, password- and key-like strings;
  still review it before sharing.

```bash
kubectl get nodes
kubectl get pods -A
kubectl get applications -n infra
kubectl get ingress -A
kubectl get pvc -A
kubectl get externalsecrets -A
```

Fix platform resources here and application resources in their own repository.

## What Healthy Looks Like

- Every node is `Ready`; Application `swirl-cloud` in `infra` is `Synced` and
  `Healthy` at the current `main` commit.
- With three or more control planes, `./scripts/configure-k3s-api-endpoint.sh --verify`
  passes and an administrator kubeconfig reaches the
  [API endpoint](guides/node-enrollment.md#kubernetes-api-endpoint) (on Tailscale,
  from an administrator device).
- Every ExternalSecret is `Ready`.
- `swirl-cloud-vault-unseal.timer` is active and Vault reports `Sealed: false`.
- `swirl-cloud-k3s-backup.service` last `Result=success`, with a fresh archive in
  `/var/backups/swirl-cloud/k3s`; with off-node storage,
  `swirl-cloud-k3s-backup-verify.service` succeeded within the past week.
- Longhorn volumes are `healthy` (detached volumes may report `unknown`).
- The default-branch pipeline passes `verify:gitops`, `verify:delivery-platform`
  and `verify:registry-write`.

## Observability

Grafana holds metrics, Kibana logs and audits, SonarQube source analysis.
Applications own instrumentation and scanner jobs; the platform owns collection,
discovery and shared dashboards on the platform cluster.

### Namespace and discovery

Collection and discovery cover managed application namespaces: `apps` and
registered local environment namespaces, labeled
`swirl-cloud.io/application-workloads: "true"`. Registration also adds read-only
discovery RoleBindings; the label alone grants nothing. App manifests must use
their registered namespace; Argo CD Applications stay in `infra`.

Discovery covers Deployments, StatefulSets, DaemonSets, CronJobs and standalone
Jobs/Pods, including those scaled to zero. It skips `infra`, `corp` (Odoo) and
unlabeled namespaces. Dashboards and log filters are per namespace, so
environments never merge; Sonar deduplicates environments of one repository.
Shared settings live in
[observability-discovery.yaml](../k8s/platform/observability-discovery.yaml).

Remote [application clusters](deployment.md#application-deployment-clusters)
need their own metrics and log collection; until then use target logs and
deployment health checks. Their CI still submits Sonar analysis.

| Signal | Automatic result | Application requirement |
|---|---|---|
| Runtime metrics | Grafana **Applications / <application>** | None for baseline metrics; add [scrape annotations](#metrics-and-logs) for endpoint metrics. |
| Container logs | Kibana **Applications / <application> / Logs** | Write logs to stdout/stderr. |
| Source analysis | Sonar project and scheduled analyses | Argo CD tracking must name a repository in the configured GitLab hosts/group; implement the [scanner contract](#scanner-contract). |
| Homepage tile | Listed on `cloud.<your-domain>` with live pod status and, for administrators, the running version | `gethomepage.dev/*` annotations on the Ingress. The version is read from the pods `gethomepage.dev/pod-selector` names, when the page loads: the `app.kubernetes.io/version` pod label, else the first container's image tag, so an image pinned by digest alone shows none until its pods carry the label. For the application's own icon, add it to [`k8s/files/homepage-icons/`](../k8s/files/homepage-icons/) and a key in the `homepage-icons` ConfigMap ([homepage.yaml](../k8s/platform/homepage.yaml)), then set `gethomepage.dev/icon: /icons/<file>`; the portal serves it from its own origin and restarts to pick it up. |

### Platform health

Prometheus alerts cover capacity, workloads, jobs, storage, scrapes, platform
services, host maintenance and ingress. Thresholds and fixes are in the
[alert runbooks](guides/alert-runbooks.md).

Alertmanager notifies once per cause: a node that is not Ready silences the
alerts carrying its name, and a critical alert silences its warning counterpart
(a full filesystem silences "space low", an expired certificate "expiring soon").

Notifications are paused since 2026-10-11: every alert goes to a receiver that
sends nothing, so alerts show only in Alertmanager, Prometheus and Grafana. Once
restored with the owner's approval ([TODO](../TODO.md)), Alertmanager posts to
the infrastructure project's GitLab **Monitor > Alerts** page and, with
[platform mail](guides/platform-mail.md), emails critical and warning alerts.

Extra scrape jobs, each covered by `PrometheusTargetDown`: `traefik`, `vault`,
`argocd` and `longhorn` (by chart label and named port); `cloudflared` (HA tunnel
connectors only); `postgres` (including
CloudNativePG), `mongodb`, `redis` (including Redis HA), `kafka` and
`elasticsearch` (exporters; Elasticsearch signs in as `metrics_monitor`);
`external-secrets`; and GitLab's `/-/metrics` (its bundled Prometheus is
disabled; see **GitLab Delivery**). Prometheus restarts when its
[configuration or rules](../k8s/files/prometheus/) change. The kubelet job drops
the K3s API server, etcd/kine, scheduler and work-queue metrics its endpoint also
serves; remove a family from its `metric_relabel_configs` before a dashboard or
rule uses it.

Vault serves `/v1/sys/metrics` without a token, reporting `vault_core_unsealed`
even while sealed, and reads telemetry settings only at startup: after an
upgrade, delete the Vault pods one at a time; the unseal timer unseals each.

Grafana's **Ingress Traffic** dashboard shows request rate, 5xx ratio and p95
latency per backend. Traefik's JSON access logs (`RequestHost` in
`kubernetes-logs-v2-*`) keep only the user agent and Cloudflare's client IP and
Ray headers; cookies, other headers and query strings are never logged.

The API server deletes Kubernetes events (evictions, out-of-memory kills, failed
mounts, scheduling and probe failures) after an hour. The `kubernetes-events`
collector in `host-agents` ships them to `kubernetes-logs-events-*` with
`log_source=kubernetes-events`, kept 30 days. Kibana's **Kubernetes Events**
search lists them, and the [incident report](guides/incident-response.md) adds
the window's Warning events to its timeline.

On the hosts:

- Fluent Bit tags logs from managed application namespaces
  `observability_scope=application`. It also ships each node's systemd journal
  (from its start) to `kubernetes-logs-host-*` with `log_source=host-journal`,
  `systemd_unit`, `syslog_identifier` and `priority`, under the same one-day
  retention; the local journal keeps longer history.
- Lynis records reach Kibana's **Lynis Security Audits** through Filebeat and
  Logstash. Each managed run writes `swirl_cloud_lynis_*` metrics to
  `/var/lib/node_exporter/textfile_collector`.
- Unattended upgrades install security updates without rebooting; nodes export
  `swirl_cloud_reboot_required` every 15 minutes.
- The backup, backup-verify and Vault unseal services record each run through an
  `ExecStopPost` drop-in for the host maintenance alerts.

Trivy Operator ([trivy.yaml](../k8s/platform/trivy.yaml), scanner settings in
[values.yaml](../k8s/values.yaml)) scans all namespaces for image/SBOM,
configuration, RBAC, exposed-secret, infrastructure and compliance reports
(secret findings hold no values). For private application images, declare
`imagePullSecrets` on the workload or ServiceAccount; the application owns them.
In Trivy metrics, `exported_namespace` is the scanned workload's namespace;
`namespace` is the operator pod's (`infra`), set by the scrape job.

```bash
kubectl get vulnerabilityreports,configauditreports,exposedsecretreports -A
```

### Application dashboards

The Grafana discovery sidecar rescans workloads every minute and Grafana loads
changes within 30 seconds. Kibana objects are imported on change and reconciled
every five minutes. A Kibana outage does not block Grafana.

#### Application identity

Discovery follows ownership to the root controller and groups components by the
first identity found on controller or pod-template metadata:

1. A valid Argo CD tracking annotation belonging to that resource.
2. `app.kubernetes.io/part-of`.
3. `app.kubernetes.io/instance`.
4. `app.kubernetes.io/name`.
5. `app`.
6. Controller kind/name, or standalone Pod kind/name.

Without Argo CD, group components with a shared `app.kubernetes.io/part-of`.
Equal display names from different sources get separate stable dashboard IDs.
`apps` IDs are preserved; other namespace names are part of the identity.

#### Metrics and logs

CPU, memory, network, readiness and restarts need no app endpoint or label. For
endpoint metrics, annotate the **pod template**:

```yaml
metadata:
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/path: /metrics
    prometheus.io/port: "8080"
```

The endpoint panels (targets up, endpoint health, scrape duration) appear only
for applications with an annotated pod or pod template. On those panels, **No
data** means no series, not a healthy endpoint. Apps own detailed
instrumentation; extra ConfigMaps labeled `grafana_dashboard: "1"` still load.
Each Grafana dashboard links to a Kibana view on `kubernetes-logs-v2-*`. Filters
organize data; they are not access controls.

#### Ownership and troubleshooting

Clone a generated dashboard before customizing it; managed objects are
overwritten. Removed workloads keep their dashboards and logs under normal
retention, and a returning application reuses its IDs.

The sidecar has read-only workload access in managed namespaces and uses the
Vault-backed `kibana-bootstrap-credentials` Secret. It cannot read app Secrets
or change workloads.

```bash
kubectl -n infra logs deployment/grafana -c application-discovery --tail=30
kubectl -n infra exec deployment/grafana -c application-discovery -- \
  node /application-discovery/discovery.mjs --once
```

The one-shot pass exits nonzero on failure. Grafana and Fluent Bit roll on
changes to their discovery or log configuration. Discovery and RBAC live in
[application-observability.yaml](../k8s/platform/application-observability.yaml).

### Source analysis

Application CI owns compilation, coverage and scanner submission. Discovery
creates Sonar projects (private for non-public repositories), binds them to
`SONAR_ALM_SETTING` and adds a protected, masked `SONAR_TOKEN` when absent.

| Trigger | Behavior |
|---|---|
| Default-branch CI | The app's quality job runs with its normal pipeline. |
| Discovery, every 15 minutes | Requests analysis when none exists or the latest is over 24 hours old. |
| Manual analysis | Run a default-branch pipeline with `SONAR_SCAN_ONLY=true`, or the app's documented manual scanner. |

Discovery requests at most one pipeline per pass, waits while any discovered app
has an active default-branch pipeline, and retries an API-triggered pipeline
only after six hours.

#### Scanner contract

Each application repository must provide:

- `sonar-project.properties` with `sonar.projectKey=<group>:<repository>` (path
  slashes become colons), all sources, required compiled inputs and coverage.
- `.sonar-auto.json` naming the actual quality job:

  ```json
  {"version":1,"job":"sonar","scanOnlyVariable":"SONAR_SCAN_ONLY"}
  ```

- CI rules that, when `SONAR_SCAN_ONLY=true`, run only compilation, tests and
  analysis: no image builds, packaging, publication, deployment or versioning.
- A protected default branch, so the job receives `SONAR_TOKEN`. Submission
  failures must be visible; quality findings may be non-blocking.

Projects with CI/CD disabled are skipped. Missing contracts, unmapped workloads
and unsupported sources fail the discovery Job; valid repositories continue.

#### Credentials and troubleshooting

[configure-sonar-discovery.sh](../scripts/configure-sonar-discovery.sh) (run by
`configure-gitlab-ci.sh`) keeps a GitLab group Maintainer token at Vault
`secret/infra/gitlab:sonar_discovery_api_token` and sets the GitLab integration
and import credential on the private service domain. Rerun it before expiry; it
renews tokens inside its renewal window. Discovery never renews them.

The [discovery CronJob](../k8s/platform/sonar-apps-discovery.yaml) can list
managed workloads and Applications in `infra`, but cannot read app Secrets or
corporate workloads.

```bash
kubectl -n infra get cronjob sonar-apps-discovery
kubectl -n infra logs -l app=sonar-apps-discovery --prefix
SONAR_JOB_NAME="sonar-apps-manual-$(date +%s)"
kubectl -n infra create job "$SONAR_JOB_NAME" --from=cronjob/sonar-apps-discovery
kubectl -n infra logs -f "job/$SONAR_JOB_NAME"
```

A manual pass still obeys the 24-hour and six-hour rules; use the scan-only
pipeline to force an analysis. A failed quality gate is not a failed submission.

## Storage and retention

Requested volume capacity is not physical usage, and lowering a request
reclaims nothing; trim the filesystem through Longhorn.

[configure-k3s-registry-mirror.sh](../scripts/configure-k3s-registry-mirror.sh)
sets `discard_unpacked_layers = true` in a containerd `config-v3.toml.d` drop-in
on every node, roughly halving image storage. Nodes then cannot re-serve pulled
layers, so keep recovery images off-host. Set `K3S_DISCARD_UNPACKED_LAYERS=false`
before running the script to keep them.

The weekly trim in [longhorn-maintenance.yaml](../k8s/platform/longhorn-maintenance.yaml)
(Sunday 05:30 UTC) handles one volume at a time in the `default` and
`swirl-cloud` groups, skipping detached volumes while
`allow-recurring-job-while-volume-detached` is disabled. To preserve snapshots,
keep `remove-snapshots-during-filesystem-trim=false` and per-volume
`unmapMarkSnapChainRemoved` at `ignored` or `disabled`. Purge obsolete snapshots
through Longhorn, then trim. **Never delete replica files directly.** If
allocation stays high, a planned stop, verified unmount, restart and new trim may
be needed ([Longhorn trim guidance](https://longhorn.io/docs/1.13.0/nodes-and-volumes/volumes/trim-filesystem/));
keep a verified recovery copy first.

| Data | Retention configuration |
|---|---|
| Container and application logs | [logging-agent.yaml](../k8s/platform/logging-agent.yaml), Elasticsearch ILM policy `swirl-cloud-logs` |
| Kubernetes API audit records and Kubernetes events | [logging-agent.yaml](../k8s/platform/logging-agent.yaml), ILM policy `swirl-cloud-audit-30d` |
| Lynis audit indices | [elk.yaml](../k8s/platform/elk.yaml), separate ILM policy |
| Shared Prometheus metrics | [monitoring.yaml](../k8s/platform/monitoring.yaml) (Prometheus arguments), time and size limits |
| Images and packages | [GitLab retention job](../k8s/platform/gitlab-registry-retention.yaml) |
| CI artifacts | Each application's pipeline settings |

Update ILM policies in place so managed indices follow. In log maintenance,
remove only closed, processed archives outside the window; keep current logs,
unprocessed `.u` files and audit records. Clean registry and artifact data only
through GitLab's supported paths.

To switch an index to another policy, set the new one; do not remove the
policy first, which leaves the index unmanaged.

### Volume snapshots

Kubernetes volume snapshots work on Longhorn volumes through the CSI snapshot
controller ([volume-snapshots.yaml](../k8s/platform/volume-snapshots.yaml)) and
the `longhorn-snapshot` class. A snapshot stays on the volume's own replicas: it
is a quick checkpoint before a risky change or the source of a copy, not a
backup, and it is lost with the volume.

```yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: before-migration
  namespace: apps-indezy-uat
spec:
  volumeSnapshotClassName: longhorn-snapshot
  source:
    persistentVolumeClaimName: data
```

Once `kubectl get volumesnapshot before-migration` shows `READYTOUSE true`, a
new claim in the same namespace copies it with
`dataSource: {apiGroup: snapshot.storage.k8s.io, kind: VolumeSnapshot, name: before-migration}`
and a size at least the original's. To copy into another namespace, create a
`VolumeSnapshotContent` there with `deletionPolicy: Retain`, the same
`driver: driver.longhorn.io` and `source.snapshotHandle` (read it from the
original's content, `snap://<volume>/<snapshot>`), and a `VolumeSnapshot` in the
target namespace that names that content; Kubernetes does not let a claim read
another namespace's snapshot directly. Deleting a `VolumeSnapshot` deletes its
Longhorn snapshot. Snapshots count against the node's disk like the data they
keep, so remove them once the change is done.

### Checkpoints

Before a risky change (an upgrade, a migration, removing a module), take a
checkpoint:

```bash
./install.sh --checkpoint create --label "CHG-12: GitLab 19.4" [--hours 72] [--namespace infra ...]
./install.sh --checkpoint list
./install.sh --checkpoint show cp-20261115-101500
./install.sh --checkpoint delete cp-20261115-101500
```

It snapshots every bound Longhorn claim (or those in the named namespaces) and
records the revision Argo CD deploys and each Helm release's revision in the
ConfigMap `checkpoint-<id>` in `infra`. Each volume's snapshot is
crash-consistent: PostgreSQL, MongoDB, Kafka and Elasticsearch recover from it
as after a power cut. Volumes are snapshotted one after another, so two volumes
may differ by a few seconds. The `checkpoint-expiry` CronJob deletes a
checkpoint when it expires (72 hours by default, at most 14 days). It is not a
backup: the snapshots are lost with their volumes.

To go back after a failed change:

1. Manifests: revert the change's commit in Git (Argo CD redeploys the recorded
   revision), or `helm rollback <release> <revision>` for a platform release,
   with the revisions from `--checkpoint show`.
2. Data, one volume at a time: turn off Argo CD's automatic sync for the
   application, scale the workload that uses the claim to zero, and in the
   Longhorn UI attach the volume in maintenance mode and **Revert** it to the
   checkpoint's snapshot. The snapshot's Longhorn name is the end of its
   `snapshotHandle` (`kubectl get volumesnapshotcontent` for the snapshot,
   `snap://<volume>/<snapshot>`). Detach the volume, scale the workload back and
   turn automatic sync on again.

Reverting discards everything written since the checkpoint on that volume. To
look before deciding, copy the snapshot into a new claim instead
([volume snapshots](#volume-snapshots)).

### GitLab storage

Repositories, Registry, packages and artifacts share the `gitlab-data` PVC
([gitlab.yaml](../k8s/platform/gitlab.yaml)). The daily retention job sets
container-tag cleanup for the configured group and subgroups and removes old
Package Registry versions; protected tags and `latest` stay. Its token comes from
Vault `secret/infra/gitlab`.

Deleting tags does not free Registry disk space; use GitLab's cleanup
procedures, after saving the [recovery images](#runtime-and-recovery-constraints).

## Backups and recovery

[configure-k3s-backups.sh](../scripts/configure-k3s-backups.sh) installs the
nightly backup and optional off-node storage.
[backup-k3s.sh](../scripts/backup-k3s.sh) writes root-only archives to
`/var/backups/swirl-cloud/k3s`: the K3s datastore, server credentials and
configuration, Vault and database backups, and GitLab's `/etc/gitlab` with its
version and image under `gitlab/`. With off-node storage it then uploads the
archive and backs up the server itself (below), and with `BACKUP_KEEP_LOCAL=false`
no archive stays on the server once its upload succeeds.

The timer runs the installed copy `/usr/local/sbin/swirl-cloud-k3s-backup`. Script
changes apply only after rerunning `./scripts/configure-k3s-backups.sh` on the
control plane, which also starts a backup (the installer and
[Ansible reconciliation](deployment.md#platform-reconciliation) do this too).
**Maintenance → Run the recovery backup now** in `./install.sh` runs the
installed copy unchanged.

A backup that fills the disk makes the kubelet evict pods, so the backup checks
space before it writes anything. It expects the previous run's size (its staging
copy plus the archive) and fails, alerting through
[`K3sRecoveryBackupMissing`](guides/alert-runbooks.md#k3srecoverybackupmissing),
if that would leave less than 15 % of the filesystem free, where the kubelet's
image garbage collection starts. Set `BACKUP_MIN_FREE_PERCENT` in
`/etc/swirl-cloud/backup.env` to change the floor.

GitLab data still needs its own native backup, restored into the same version.
With the Registry metadata database, that backup must include
`db/registry_database.sql.gz`, or a verified PostgreSQL dump from the same
write/GC freeze; `registry.tar.gz` alone is not enough
([GitLab backups](https://docs.gitlab.com/omnibus/settings/backups/)). A
successful exit status does not prove coverage; inspect the archive and verify a
replacement before rotating older ones.

```bash
sudo systemctl status swirl-cloud-k3s-backup.timer
sudo systemctl start swirl-cloud-k3s-backup.service
sudo journalctl -u swirl-cloud-k3s-backup.service --since today
```

### Coverage and recovery points

The archive runs daily at `BACKUP_TIME` (02:15 UTC unless set; installer runs
add one each). Locally the newest 7 are kept, unless `BACKUP_KEEP_LOCAL=false`; on a backup filesystem of 400 GiB or more also the newest archive of
each of the last 14 days, 8 ISO weeks and 12 months (`RETENTION_COUNT`,
`RETENTION_DAYS`, `RETENTION_WEEKS`, `RETENTION_MONTHS`). restic keeps 7 daily, 5
weekly and 12 monthly. A part that fails (GitLab's native backup, the evidence
snapshot, an application copy) leaves an archive of everything else, is listed
at the end of its `RESTORE.txt`, and fails the run (exit 3) so the backup alerts
fire; the installer's own backup then warns and continues.
Recovery times are unmeasured (restore drill, TODO §16).

| Data | How it is protected | Recovery point | Notes and gaps |
| --- | --- | --- | --- |
| K3s state (SQLite or etcd), server token, encryption keys, K3s configuration | Nightly archive | Up to 24 h | Restore steps are in the archive's `RESTORE.txt`. |
| Vault secrets | Raft snapshot in the nightly archive | Up to 24 h | The unseal key and bootstrap token travel in the same archive unless an exported [recovery kit](security.md#recovery-kit) holds them and separation was chosen. |
| PostgreSQL: Keycloak, SonarQube, Odoo and application databases | `pg_dumpall` in the nightly archive | Up to 24 h | No point-in-time recovery (TODO §10). |
| MongoDB | `mongodump` in the nightly archive | Up to 24 h | |
| Odoo filestore | `tar` in the nightly archive, taken after `pg_dumpall` | Up to 24 h, consistent with its database dump | |
| GitLab configuration and secrets (`/etc/gitlab`), version and image | `tar` of the running container in the nightly archive (`gitlab/`) | Up to 24 h | Restore before GitLab starts against restored data, with the same image. |
| GitLab database (issues, merge requests, CI settings), repositories, uploads, packages, LFS | GitLab's native backup (`gitlab-backup create`, `gitlab/gitlab_backup.tar`) in the nightly archive | Up to 24 h | Skipped, and the run failed, when three times the previous backup's size would break the free-space floor. Restore with the same image after `/etc/gitlab`; steps in `RESTORE.txt`. Repositories mirrored to GitHub survive there too. |
| GitLab CI artifacts and job logs | `gitlab-data` Longhorn volume only, when off-node volume backups are active | None until then | Artifacts expire after 7 days in every application. |
| Container registry | Same `gitlab-data` volume | None until then | Left out of the native backup (13 GB on 2026-11-13). Images can be rebuilt from their release tags; pinned upstream images are re-pulled. |
| Application SQLite databases and files (Swirl Website leads, designXprint orders and artwork) | Declared by each application on its Deployment (`swirl-cloud.io/backup-sqlite`, `swirl-cloud.io/backup-files`, comma-separated absolute paths); copied in the application's own container, databases with `VACUUM INTO` | Up to 24 h | Under `application-data/apps/<namespace>/<deployment>/`, named after the path with `/` as `_`. |
| Grafana dashboards | Provisioned from Git; user-made dashboards on the `grafana-data` volume | Git: current; volume: none until volume backups | |
| SonarQube analysis | Database in `pg_dumpall`; search index on its volume | Up to 24 h | Rebuildable: the index regenerates; rerun analyses for history. |
| Elasticsearch evidence: identity audit, platform reports, Lynis, security findings, access grants | Snapshot `nightly-<time>` of the `swirl-cloud-evidence` fs repository, streamed into the nightly archive (`elasticsearch/evidence-snapshot.tar`) | Up to 24 h | The repository keeps only the newest snapshot (about 55 MB on 2026-11-13). |
| Elasticsearch container logs and API audit events | Not backed up | None | Short retention by design. |
| Prometheus metrics | Not backed up | None | Rebuildable; 30 days in the `large` sizing profile, 15 days or 1.5 GB otherwise. |
| Kafka topics | Not backed up; volume only with volume backups | None | Applications must tolerate replaying from their own state. |
| Redis, Trivy reports | Not backed up | None | Rebuildable caches. |
| Portainer, DBGate settings | Their volumes, with volume backups | None until then | Low value; recreated by the installer. |
| Kubernetes PVC and Longhorn volume inventories | YAML in the nightly archive | Up to 24 h | Maps restored volumes back to claims. |
| The server itself: `/etc`, `/root`, `/home` without build output and caches, `/usr/local`, `/opt`, crontabs, K3s server state (a consistent SQLite copy, token, certificates, manifests), Vault's unseal key and bootstrap token, installed packages, disk and address layout | restic snapshot tagged `swirl-cloud-host`, after the archive (off-node storage only) | Up to 24 h | Container images, Docker data, logs and Longhorn's replica files stay out; the staged copies are under `/run/swirl-cloud-k3s-backup/host` in the snapshot. |
| Every Longhorn volume except `LONGHORN_BACKUP_EXCLUDE` (the runner cache, Trivy's database and ClamAV's signatures by default) | Longhorn's daily recurring backup `swirl-cloud-daily-backup` (`LONGHORN_BACKUP_CRON`, UTC; 14 kept) to the `default` BackupTarget | Up to 24 h, crash-consistent | Volumes join through Longhorn's `default` group, new ones included; an excluded volume carries only the `swirl-cloud` group, which keeps its weekly trim, and its claim the label `swirl-cloud.io/volume-backup=excluded`, which `LonghornVolumeBackupStale` skips. Databases come back as after a power cut; the archive's dumps are the consistent copy. |

Off-node storage is either S3-compatible (`BACKUP_STORAGE=s3`) or an NFS version 3
export (`BACKUP_STORAGE=nfs`, such as OVHcloud's Backup Storage). restic encrypts
the archives and host files before they leave the server, in one repository: in
the bucket, or in `BACKUP_NFS_PATH/restic` on the export, which each backup and
check mounts only while it runs, in its unit's own mount namespace (`nolock`,
soft, so an unreachable export fails the run instead of hanging it). Longhorn
backs the volumes up to the same storage through its `default` BackupTarget: the
bucket, or the export's `backupstore/`. Credentials are root-only in
`/etc/swirl-cloud/backup.env` and, for S3, the Longhorn credential Secret; an NFS
export admits the server by address. Keep the repository password off the
server: without it nothing restores. `BACKUP_REPOSITORY_PASSWORD_MIN_LENGTH`
(16 by default, 12 at least) lowers its floor as an accepted risk.

OVHcloud's Backup Storage also offers FTP, but restic through rclone's FTP backend
could not read back: on 2026-11-16 streaming the 3.1 GB archive stalled after
ten minutes, as each partial read waits for the server to acknowledge an aborted
transfer, while over NFS it read back in 78 s and a prune took 10 s.

Since 2026-11-16, `swirl-node-01` backs up to its OVHcloud Backup Storage
(500 GB, NFS export of `ftpback-rbx3-431`): the archive, the host files and every
volume but the two caches daily from 06:00 Europe/Paris (Longhorn at 05:00 UTC),
with no archive kept on the server.

`swirl-cloud-k3s-backup-verify.timer` runs
[verify-k3s-backup.sh](../scripts/verify-k3s-backup.sh) on Wednesdays at
05:30 UTC (up to 30 min later), only when `backup.env` names a restic
repository. Under the backup lock it runs `restic check` on one of four data
subsets, rotated weekly so the whole repository is read every four weeks, then
streams the newest archive and checks its SHA-256 and `tar.gz` structure
(including `RESTORE.txt`) without writing it to disk. It proves the archive reads
back; it is not a restore drill.

A failure, or a backup holding the lock for over an hour, fails the unit
(`HostMaintenanceServiceFailed`, which needs the metrics drop-in from
`./scripts/configure-node-security.sh --host-baseline`). The nightly backup waits
up to four hours for the lock, then fails. The timer runs
`/usr/local/sbin/swirl-cloud-k3s-backup-verify`, updated by rerunning
`configure-k3s-backups.sh`. A rerun in the same week repeats that week's subset;
the last command reads all data (full egress; keep clear of the backup time):

```bash
sudo systemctl start swirl-cloud-k3s-backup-verify.service
sudo journalctl -u swirl-cloud-k3s-backup-verify.service --since today
sudo env VERIFY_DATA_SUBSETS=1 /usr/local/sbin/swirl-cloud-k3s-backup-verify
```

To restore, follow the archive's `RESTORE.txt` and the
[Vault recovery guide](security.md#upgrade-and-recovery). Restore SQLite with
K3s stopped; for embedded etcd, restore the first server with its original token
before others rejoin. Verify application data, not only readiness.

### Operator recovery checklist

Only a person can keep a copy that survives the host. Do this after the first
installation and whenever Vault, K3s or backup credentials change.

1. On the control plane holding the Vault keys, export the
   [recovery kit](security.md#recovery-kit) with a passphrase of 16+ characters:
   `scripts/export-recovery-kit.sh --separate-archives` (or `./install.sh` →
   Maintenance → Export the encrypted recovery kit).
2. Store the kit and its passphrase off the host, apart (for example an
   encrypted USB key and a password manager). Delete the copy in your home
   directory.
3. From the stored copy, run `scripts/export-recovery-kit.sh --verify FILE`; it
   must match every current key file.
4. Next morning, `./install.sh --status` must show the nightly backup `PASS`; the
   archive's `RESTORE.txt` names the kit when it omits the Vault keys.

### Restore after losing the host

A rebuild needs access to the backup storage (an NFS export admits the new
server once the provider's access list names its address), the repository
password and this repository. In order:

1. Reinstall the same Ubuntu release, with the disk layout of
   `disks.json` and `mdstat.txt` from the host snapshot, and install `restic`
   (and `nfs-common` for an NFS export).
2. Point restic at the repository: for S3, `RESTIC_REPOSITORY` and the keys that
   `backup.env` held; for NFS, mount the export
   (`sudo mount -t nfs -o vers=3,nolock HOST:/PATH /mnt/backup`) and set
   `RESTIC_REPOSITORY=/mnt/backup/swirl-cloud/restic`. Export `RESTIC_PASSWORD`
   and list the snapshots with `restic snapshots`.
3. Restore the host files apart, then copy them back:
   `restic restore latest --tag swirl-cloud-host --target /root/restore`. Copy
   `/home`, `/root`, `/usr/local`, `/opt`, `/var/spool/cron`, `/var/lib/swirl-cloud`
   (Vault's unseal key and bootstrap token) and `/var/lib/rancher/k3s/server`; copy
   `/etc` except what belongs to the new disks and network (`fstab`,
   `mdadm/mdadm.conf`, `netplan`) when the hardware changed. Reinstall the
   packages of `packages-manual.txt`.
4. Put the snapshot's `run/swirl-cloud-k3s-backup/host/state.db` at
   `/var/lib/rancher/k3s/server/db/state.db` (an etcd server restores from
   `server/db/snapshots` as `RESTORE.txt` describes), then start K3s:
   `sudo systemctl start k3s`. The K3s binary, unit and configuration came back
   with `/usr/local` and `/etc`.
5. Longhorn comes up with its volumes faulted, their replica files gone. Scale the
   workloads down, then restore each volume from its latest backup under its
   previous name, so its PersistentVolume binds again: Longhorn UI → Backup →
   the volume → **Restore Latest Backup** with **Use Previous Name**, after
   deleting the faulted volume (not its claim). An excluded cache volume is
   recreated empty instead.
6. Vault starts from its restored volume, and `swirl-cloud-vault-unseal.service`
   unseals it with the restored key. Scale the workloads back up.
7. A database that does not start cleanly from its volume is restored from the
   archive's dumps: `restic restore` the newest `swirl-cloud-k3s` snapshot and
   follow its `RESTORE.txt` (PostgreSQL, MongoDB, the Odoo filestore, GitLab's
   native backup after `/etc/gitlab`).
8. Run `./install.sh --status`, fix every `FAIL`, and export a new recovery kit.

The archive alone also rebuilds a cluster on a fresh K3s, as before: get it from
restic (`restic snapshots --tag swirl-cloud-k3s`, then `restic restore`), check
it against its `.sha256` file and follow its `RESTORE.txt` with the
[Vault recovery guide](security.md#upgrade-and-recovery).

This path has not been rehearsed end to end yet (TODO §16).

## Workload rebalancing

```bash
kubectl create -f k8s/addons/descheduler-run-job.yaml
kubectl get jobs -n infra -l app=descheduler -w
```

## Node memory and eviction

[`configure-k3s-kubelet-memory.sh`](../scripts/configure-k3s-kubelet-memory.sh)
writes `/etc/rancher/k3s/config.yaml.d/30-swirl-cloud-kubelet-memory.yaml` on
every node. Pods cannot use the reserved memory.

| Setting | Value |
|---|---|
| `system-reserved` (host) | 1 GiB; override with `SYSTEM_RESERVED_MEMORY` |
| `kube-reserved` (K3s) | 25 % of the first 4 GiB, 20 % of the next 4, 10 % of the next 8, 6 % up to 128 GiB, 2 % above (as GKE; about 3.5 GiB on 32 GiB); override with `KUBE_RESERVED_MEMORY` (for example `4Gi`) |
| Soft eviction | Under 1.5 GiB available for a minute; pods get up to 60 seconds |
| Hard eviction | Under 500 MiB available, at once |

Pods above their memory request go first, lowest priority first, then the one
furthest above its request. Every platform workload names a priority class
([security-baseline.yaml](../k8s/base/security-baseline.yaml)); the scheduler may
also preempt a lower class to place a higher one:

| Class | Priority | Workloads |
|---|---|---|
| `platform-critical` | 1 000 000 | PostgreSQL, MongoDB, Redis, Kafka, Keycloak and the sign-in proxy; Traefik, Vault and External Secrets once their releases are next reconciled |
| `production` | 500 000 | Odoo, and production applications that name it |
| `platform-standard` | 0 | GitLab, Argo CD, monitoring, logs, ClamAV and the host agents; also any pod that names no class |
| `preproduction` | -20 | Applications in `uat` that name it |
| `integration` | -30 | Applications in `int` that name it |
| `platform-background` | -50 | SonarQube, Trivy and its scans, DBGate, Kafka UI, Portainer, registry retention |
| `ci-jobs` | -100 | CI job pods |

CI jobs run at the `ci-jobs` priority (-100), below
every platform and application pod, so they are evicted first and the scheduler
may preempt them to place a platform pod; they never preempt anything. They
request 2 GiB, about what a test job uses
([gitlab-runner.yaml](../k8s/platform/gitlab-runner.yaml)), so the scheduler
holds a job while the node's memory is fully requested; a job still pending after
the runner's 10-minute `poll_timeout` fails and needs a retry. A job gets 2 CPUs
and 3 GiB (`large`: 4 CPUs and 8 GiB) and may raise its own limits, never its
requests, with the `KUBERNETES_CPU_LIMIT` and `KUBERNETES_MEMORY_LIMIT` job
variables up to 4 CPUs and 6 GiB (`large`: 8 CPUs and 12 GiB); a higher value
fails the job. The instance runner and the project runners of application
delivery share these settings. A raised limit does not slow production down:
production pods request the CPU they use while a job requests 100m, so under
contention CFS shares keep production ahead of CI. GitLab requests the
5 GiB it uses. Frequent `Evicted` events in `kubectl get events -A` mean the node needs
more memory or lower requests.

When the host shuts down or reboots, the kubelet stops pods in priority order
instead of all at once, from a second drop-in
(`/var/lib/rancher/k3s/agent/etc/kubelet.conf.d/50-swirl-cloud-shutdown.conf`):
CI jobs get 10 seconds, analysis tools and `int`/`uat` 20, platform services 30,
production 30, then data stores, sign-in and system pods 60, so PostgreSQL and
Vault stop cleanly after everything that writes to them. The kubelet holds the
shutdown for up to 150 seconds through a systemd inhibitor lock, which logind
allows from a third drop-in
(`/etc/systemd/logind.conf.d/zz-swirl-cloud-kubelet-shutdown.conf`). logind
keeps the value of the drop-in whose name sorts last, and Ubuntu's
unattended-upgrades caps the wait at 30 seconds in
`/usr/lib/systemd/logind.conf.d/unattended-upgrades-logind-maxdelay.conf`, which
sorts after the kubelet's own `99-kubelet.conf`; with that cap the kubelet logs
"Failed to start node shutdown manager" and stops every pod at once.

The installer, node enrollment and Ansible write the drop-ins before K3s starts.
On a running node it applies at the next K3s restart, or now with
`scripts/configure-k3s-kubelet-memory.sh --restart` (run it when no CI job is
running; the kubelet and on a server the API restart for about 15 seconds; pods
keep running). `./install.sh --status`, or the script's `--check`, reports
whether it is active, and `shutdown-off` when the kubelet holds no shutdown lock.

```bash
kubectl get node -o custom-columns=NAME:.metadata.name,CAPACITY:.status.capacity.memory,ALLOCATABLE:.status.allocatable.memory
```

## Hardware health

On bare-metal nodes node-exporter reads software RAID (`/proc/mdstat`), memory ECC
counters (EDAC) and sensor temperatures, and the host baseline
([hardware-health.sh](../scripts/lib/hardware-health.sh)) adds disk SMART health,
NVMe wear and reallocated sectors through an hourly `swirl-cloud-smart-metrics`
timer (it installs `smartmontools`). Where the server has a BMC reachable over
IPMI, the same timer exports its threshold sensors (fans, power supplies,
voltages, temperatures) with `ipmitool`, installed only then. A disk smartctl or
a BMC ipmitool cannot read is reported as unreadable, not healthy. iSCSI disks,
which are Longhorn volumes attached to the node, are left out. Virtual
servers get none of it. The alerts and their runbooks are under
[Hardware](guides/alert-runbooks.md#hardware). Redfish-only BMCs are not read.
It applies when the host baseline runs
(`scripts/configure-node-security.sh --host-baseline`, the installer or node
enrollment).

## Maintenance

### Notices to the people affected

Before a maintenance window, or during an incident, tell the people who use the
service on the portal:

```bash
./install.sh --notice add --service GitLab --from 2026-11-20T22:00 --until 2026-11-20T23:00 \
  --en "Upgrade to GitLab 19.4: GitLab is unavailable." --fr "Mise à jour vers GitLab 19.4 : GitLab est indisponible."
./install.sh --notice list
./install.sh --notice remove 3f9c2a1b
```

`--service` is the service as the portal names it, or `*` for the whole
platform; times without a zone are UTC. From now until it ends, the notice shows
at the top of the portal for the people who can open that service and for
administrators, in their browser's language (English when no French text is
given) and with the times in their own time zone. It disappears when it ends;
adding a notice removes those that have ended. Notices live in the ConfigMap
`infra/portal-notices`, not in Git, and reach the page within about a minute.

### Image updates

Platform images are pinned by digest in their owning manifests and Helm values;
K3s and Longhorn use their release images, and two CSI sidecar pins live in the
[system workload policy](../k8s/base/system-workload-hardening.yaml).
Applications own their images and pull credentials. There is no platform image
pipeline or private registry dependency.

[Renovate](../.github/renovate.json5) proposes updates as pull requests: every
pinned image (tag and digest), the Helm charts in `k8s/Chart.yaml` (rebuilding the
vendored archives) and `config/platform.env`, the Argo CD image and K3s. New pull
requests open on Mondays after a release is three days old. Major updates and K3s
minor upgrades wait for approval on the Dependency Dashboard issue, and the Elastic
Stack, GitLab, Trivy, Traefik, External Secrets and Argo CD update as groups.
Nothing merges automatically: merging deploys `k8s/` changes. The Helm CLI pin
stays manual because its checksums change with it, and so does the CloudNativePG
operator image, which follows its chart. The
[scheduled workflow](../.github/workflows/renovate.yml) runs Renovate once the
repository has a `RENOVATE_TOKEN` secret; until then it does nothing. Each pull
request is still an update to review with the steps below.

1. Review the release's compatibility, architecture, runtime identity, writable
   paths and data format. Update the digest in the owning source. A new image or
   chart, or a licence change, needs its record in the
   [licence register](guides/releases.md#third-party-licences).
2. Compare scanner reports across all severities, including exposed secrets.
   Test startup, readiness and authentication with disposable data; for stateful
   services, restore a backup from the previous image into the candidate.
3. Run [repository validation](testing.md#validation). Reconcile separately
   installed Helm releases as well as GitOps resources. Keep a verified backup
   and the exact recovery image before rollout.
4. Verify the running digest, readiness, behavior and fresh scanner reports;
   review unfixed advisories.

Keep experiments, images, reports and archives outside Git
([validation policy](testing.md#validation-policy)).

### Runtime and recovery constraints

| Component | Check before changing its image |
| --- | --- |
| PostgreSQL and its helpers | Keep the same upstream Bookworm release. Restore globals and databases in isolation; check ownership, libc/ICU collation versions, locales and extensions. A matching major version alone is insufficient. |
| Vault | Preserve its UID, Raft data and audit mounts. Follow [Vault upgrade and recovery](security.md#upgrade-and-recovery). |
| GitLab | Keep the matching version, configuration and secrets with its [native backup](#backups-and-recovery); the archive's `gitlab/` records all three. |
| Keycloak | Preserve UID 1000 and writable augmentation paths. |
| SonarQube | Preserve UID 1000/GID 0 and group access to bundled files. A release with schema changes reports `DB_MIGRATION_NEEDED` and stays unready, which holds the sync: run `kubectl -n infra exec deploy/sonarqube -c sonarqube -- curl -fsS -X POST http://127.0.0.1:9000/api/system/migrate_db` and wait for `/api/system/db_migration_status` to report `MIGRATION_SUCCEEDED`. |
| PostgreSQL / MongoDB | Preserve UID 999 and check volume ownership. Never recursively change ownership on active database volumes. |
| MongoDB | Move one major version per release. Once it runs, raise the feature compatibility version (`db.adminCommand({setFeatureCompatibilityVersion: "<major>.0", confirm: true})`): the next major refuses to start below it, and going back means restoring a dump. 9.0 images exist only on Ubuntu 26.04 (`-resolute`). |
| Kafka | After both pods run a new Kafka minor, raise the finalized features: `kubectl -n infra exec kafka-0 -- kafka-features --bootstrap-server localhost:9092 upgrade --release-version <minor>`. It is one-way: the previous image no longer starts afterwards. |
| K3s | Upgrade one Kubernetes minor at a time, and only to a minor that Longhorn's support matrix lists for the installed Longhorn release. Rerun the K3s installer with `INSTALL_K3S_VERSION` and the server arguments in `/etc/systemd/system/k3s.service` while no CI job runs. |
| Storage components | Preserve host access and writable paths. [System hardening](../scripts/reconcile-system-hardening.sh) is independent of images. |

Keep an off-host archive of the exact recovery images and credentials. During a
registry outage, import pinned images into each node's containerd. Keep legacy
private artifacts until their consumers and rollback window are retired.

### UI themes

SonarQube and Vault default to dark mode; the bottom-right **Dark / Light**
button saves a browser preference. Odoo's **Dark Mode** user-menu switch saves an
account preference, enabled once by bootstrap for the managed administrator.

#### SonarQube and Vault

Both use vendored Dark Reader assets, with license, provenance and integrity
annotations in their manifests, served from the same origin without loosening
the Content Security Policy.

- [SonarQube](../k8s/platform/sonarqube.yaml) copies its HTML entry point in an
  init container and mounts the themed files read-only.
- [Vault](../k8s/platform/vault-ui-theme.yaml) serves only `/ui` through an NGINX
  helper; API traffic goes straight to Vault. The helper accepts UI GET/HEAD,
  strips forwarded credentials, holds no token, caches and logs nothing, and is
  restricted by network policy.

When updating assets, bump the pod revision and HTML asset query versions. After
an upgrade, check authenticated navigation, console/CSP errors, both toggles,
reload persistence, Vault API health, and that the helper rejects API paths. To
remove a theme, remove Sonar's ConfigMap, init container, volumes and mounts
together, or Vault's theme Ingress and helper resources together.

#### Odoo

Add-ons are plain files in [`k8s/files/odoo-addons/`](../k8s/files/odoo-addons/):
the managed `odoo_keycloak_sso` and `platform_crm_intake`, and OCA's vendored
`web_dark_mode` (AGPL). Each folder becomes a ConfigMap mounted read-only at
`/mnt/extra-addons/<module>` ([`_odoo_addons.tpl`](../k8s/templates/_odoo_addons.tpl)).
Any file change restarts Odoo, and bootstrap upgrades the module.

To update the dark theme, replace the folder byte-for-byte with the new upstream
revision, update its provenance in `_odoo_addons.tpl`, check compatibility, then
confirm on an authenticated `/odoo` page that dark CSS loads and the switch works
both ways. Uninstall a module in Odoo before removing its files.

Bootstrap also installs CRM and Live Chat, upgrades `platform_crm_intake` on
every start, and seeds Leads, the chat channel and its out-of-hours chatbot once
(see [Odoo website integration](guides/odoo-website.md)). Dump the database
before changing the add-on: uninstalling CRM does not restore the old schema.

## Common Incidents

Record incidents with the [incident response](guides/incident-response.md)
guide. Every alert links to its [runbook](guides/alert-runbooks.md)
(`runbook_url`) and has an `owner` label (`platform`, `security` or
`application`). These incidents span several alerts.

### Root Application is Degraded while Synced

Argo CD aggregates every chart resource's health; one failed CronJob run
degrades it. Find the unhealthy resource and read its logs:

```bash
kubectl get pods -A | grep -vE 'Running|Completed'
kubectl get jobs -n infra
kubectl -n infra logs -l app=sonar-apps-discovery --prefix --tail=20
```

A Sonar discovery `HTTP 403` means its token cannot read a discovered project:
grant access or move the project into the configured group
([credentials](#credentials-and-troubleshooting)).

### ExternalSecret is not Ready

Check whether Vault is sealed or the path is missing:

```bash
kubectl get externalsecrets -A
kubectl -n infra describe externalsecret <name>
kubectl -n infra exec vault-0 -- vault status
sudo systemctl show swirl-cloud-vault-unseal.service -p Result -p ExecMainStatus
```

Follow [Vault host unseal](security.md#host-unseal-service). Never paste
recovery material into a command line.

### Argo CD reverts a manual change

The root Application auto-syncs, prunes and self-heals, so commit changes to the
owning manifest or Helm values. Separately installed Helm releases (Longhorn,
Traefik, Vault, External Secrets, Argo CD) are reconciled by the installer or
Ansible.

### Argo CD itself is unhealthy or lost

Argo CD does not manage itself. It is the Helm release `argocd` in `infra`, pinned
by `DEFAULT_ARGOCD_CHART_VERSION` and `DEFAULT_ARGOCD_IMAGE_TAG` in
[platform.env](../config/platform.env) and installed by
`scripts/reconcile-platform-release.sh argocd` with `config/argocd-values.yaml`
(plus `argocd-ha-values.yaml` in HA). The installer (*Install/upgrade Argo CD*)
and `ansible/deploy.yml` (`install_argocd`) both run that step and reapply the
root and application Applications, restoring the release from Git. Compare the
live release with the pins first:

```bash
helm list -n infra --filter '^argocd$'
helm -n infra get values argocd | grep -A1 'image:'
```

Workloads keep running while Argo CD is down, but nothing reconciles.

### Longhorn volume is degraded or faulted

```bash
kubectl get volumes.longhorn.io -n longhorn-system \
  -o custom-columns=NAME:.metadata.name,PVC:.status.kubernetesStatus.pvcName,STATE:.status.state,ROBUSTNESS:.status.robustness
```

Inspect replicas and node disk pressure in the Longhorn UI. Never delete replica
files directly ([storage and retention](#storage-and-retention)).

### Registry push or pull fails from outside the cluster

Registry clients cannot answer browser challenges. Check the Cloudflare bot
exceptions in [Cloudflare configuration](dns.md#cloudflare) and compare with the
internal endpoint CI uses (`gitlab-registry.infra.svc.cluster.local:5050`).

### An application dashboard or log view is missing

Run a one-shot discovery pass and read its errors
([ownership and troubleshooting](#ownership-and-troubleshooting)).

### The nightly backup failed

```bash
sudo systemctl status swirl-cloud-k3s-backup.service
sudo journalctl -u swirl-cloud-k3s-backup.service --since yesterday
```

Fix the cause and start the service manually
([backups and recovery](#backups-and-recovery)).

### An operator is banned by Fail2ban or CrowdSec

Reconnect through the provider console or a private Tailscale path, then run
`sudo fail2ban-client set sshd unbanip <address>` or
`sudo cscli decisions delete --ip <address>`. See [host policy](security.md#host-policy).

## Related Guides

- [Architecture](architecture.md)
- [Deployment](deployment.md)
- [Security](security.md)
- [Data model](data-model.md)
- [Testing](testing.md)
- [High availability](guides/high-availability.md)
- [Compliance evidence](guides/compliance-evidence.md)
- [Cost showback](guides/cost-showback.md)
