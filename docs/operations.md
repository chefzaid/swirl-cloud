# Operations Runbook

Run cluster commands from a configured control-plane shell. This runbook covers
the service directory, observability, storage, backups, maintenance and common
incidents. Use [deployment](deployment.md) for installation and GitOps changes
and [security](security.md) for credentials and Vault recovery.

## Services and URLs

Homepage at `https://cloud.<your-domain>` is the service directory, including
internal components and application-owned entries. Replace `<your-domain>`
below with the domain supplied during installation. Administrative UIs
use the access controls described in [security and identity](security.md).

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
| Trivy reports | `https://grafana.<your-domain>/d/trivy-security/trivy-security-reports` | Current workload and cluster findings |
| Lynis reports | `https://kibana.<your-domain>/app/dashboards#/view/lynis-security-audits` | Host audit history |
| Compliance evidence | `https://grafana.<your-domain>/d/compliance-evidence` | Control status per framework ([guide](guides/compliance-evidence.md)) |
| Cost showback | `https://grafana.<your-domain>/d/cost-showback` | Cost per environment, namespace and workload ([guide](guides/cost-showback.md)) |
| Service levels | `https://grafana.<your-domain>/d/service-levels` | Availability and error budget per public endpoint ([guide](guides/service-levels.md)) |
| Platform report | `https://grafana.<your-domain>/d/platform-report` | Monthly availability, cost and compliance ([guide](guides/platform-reports.md)) |
| Capacity planning | `https://grafana.<your-domain>/d/capacity-planning` | Requested and used capacity, headroom and what to add ([guide](guides/capacity-planning.md)) |

## Cluster status

`./install.sh --status` prints a read-only PASS/WARN/FAIL report of nodes,
workloads, ExternalSecrets, Argo CD, Vault, Longhorn, TLS certificates, platform
mail, public endpoints and the nightly backup ([checks](deployment.md#verify-the-result)),
with a `Next:` command for each problem.

`./install.sh --support-bundle` writes a private archive (mode `600`) under
`~/.local/state/swirl-cloud/support/` with that report, the rerun plan, versions,
nodes, pods, recent events, Argo CD, ExternalSecret, Longhorn and storage status,
host maintenance units, descriptions and log tails of unhealthy pods, and the
setup hub's latest run logs. It never reads Kubernetes Secrets and redacts
token-, password- and private-key-like strings; still review it before sharing.

For details:

```bash
kubectl get nodes
kubectl get pods -A
kubectl get applications -n infra
kubectl get ingress -A
kubectl get pvc -A
kubectl get externalsecrets -A
```

Investigate unhealthy workloads through their events and logs, then update the
owning repository. Platform resources belong here; application resources belong
to the application repository.

## What Healthy Looks Like

- Every node is `Ready`, and `kubectl get applications -n infra` shows `swirl-cloud` `Synced` and `Healthy` at the current `main` commit.
- With three or more control planes, `./scripts/configure-k3s-api-endpoint.sh --verify` passes on a control plane, and an administrator kubeconfig reaches the [API endpoint](guides/node-enrollment.md#kubernetes-api-endpoint) (on Tailscale, check from an administrator device).
- `kubectl get externalsecrets -A` reports every ExternalSecret `Ready`.
- `swirl-cloud-vault-unseal.timer` is active and Vault reports `Sealed: false`.
- `swirl-cloud-k3s-backup.service` last `Result=success`, with a fresh archive under `/var/backups/swirl-cloud/k3s`.
- Longhorn volumes report robustness `healthy` (detached volumes may report `unknown`).
- The default-branch platform pipeline passes `verify:gitops`, `verify:delivery-platform` and `verify:registry-write`.

## Observability

Use Grafana for metrics, Kibana for logs and audits, and SonarQube for source
analysis. The [service directory](#services-and-urls) lists their
URLs. Applications own instrumentation and scanner jobs; the platform owns
collection, discovery and shared dashboards on the platform cluster.

### Namespace and discovery

Automatic collection and discovery cover managed application namespaces on the
platform cluster. The existing `apps` namespace and registered local environment
namespaces carry `swirl-cloud.io/application-workloads: "true"`. Registration also
adds read-only discovery RoleBindings; the label alone grants no workload access.
App manifests must use their registered namespace. Argo CD Applications remain
in `infra` and select that cluster and namespace.

Discovery includes Deployments, StatefulSets, DaemonSets, CronJobs and standalone
Jobs/Pods, including controllers scaled to zero. It excludes `infra`, `corp`
(including Odoo) and unlabeled namespaces. Environment dashboards and log filters
are namespace-specific; repeated component names do not merge environments.
Sonar analysis deduplicates environments from the same repository.

Optional remote application clusters need their own metrics/log collection and
connection to central dashboards and scheduled discovery. Their application CI
still submits Sonar analysis. See
[application deployment targets](deployment.md#application-deployment-clusters).
Until remote telemetry is connected, use target logs and deployment health checks.

| Signal | Automatic result | Application requirement |
|---|---|---|
| Runtime metrics | Grafana **Applications / <application>** | Baseline metrics need no app endpoint; add [scrape annotations](#metrics-and-logs) for endpoint metrics. |
| Container logs | Kibana **Applications / <application> / Logs** | Write logs to stdout/stderr. |
| Source analysis | Sonar project and scheduled analyses | Argo CD tracking must identify a repository in the configured GitLab hosts/group; implement the [scanner contract](#scanner-contract). |

Dashboard identity can fall back to workload labels. Sonar requires the Argo CD
source mapping and deduplicates components from the same repository. Namespace
placement alone cannot instrument an app or configure its scanner.

### Platform health

Prometheus alerts cover capacity, workload health, jobs, storage and scrape
failures, plus platform state: node NotReady, Vault sealed or failing audit
writes, degraded or faulted Longhorn volumes, unavailable datastores, unhealthy or
out-of-sync Argo CD applications and TLS certificates expiring within 14 days
(critical within 3). Alert notifications are paused since 2026-10-11: Alertmanager
routes every alert to a receiver that sends nothing, so alerts are visible only in
Alertmanager, Prometheus and Grafana. When restored with the owner's approval
([TODO](../TODO.md)), it sends firing and resolved events to the infrastructure
project's **Monitor > Alerts** page in GitLab and, once [platform mail](guides/platform-mail.md)
is configured, emails critical and warning alerts to the alert recipients.
GitLab and runner metrics appear in Grafana's **GitLab Delivery** dashboard.

Prometheus also scrapes the platform releases by chart label and named port:
jobs `traefik`, `vault`, `argocd` and `longhorn`. It scrapes the datastores through
exporter sidecars and a Kafka exporter: jobs `postgres` (including CloudNativePG
instances), `mongodb`, `redis` (including the shared Redis HA servers), `kafka`
and `elasticsearch`. The Elasticsearch exporter signs in as the monitor-only
`metrics_monitor` user; `ElasticsearchUnreachable`, `ElasticsearchClusterRed`
and `ElasticsearchClusterYellow` (30 minutes) cover log and audit indexing. `KeycloakPasswordFailuresHigh` fires when a realm rejects more than
20 passwords within 15 minutes. Kubelet volume statistics cover
every persistent volume claim: `PersistentVolumeSpaceLow` (20 % free),
`PersistentVolumeSpaceCritical` (10 %) and `PersistentVolumeFillingWithinDay`
mirror the node filesystem alerts. Grafana's **Ingress Traffic** dashboard shows Traefik
request rate, 5xx ratio and p95 latency per backend service;
`IngressServiceErrorRateHigh` fires when a service with steady traffic answers
more than 5 % of requests with 5xx for 15 minutes. Traefik's JSON access logs (search `RequestHost` in
`kubernetes-logs-v2-*`) keep only the user agent and Cloudflare's client IP and
Ray headers; cookies, other headers and query strings are never logged. Job `external-secrets` scrapes
the External Secrets controller; `ExternalSecretNotReady` fires when an
ExternalSecret stays unsynchronized for 15 minutes.
`PrometheusTargetDown` covers each of these targets. Vault answers
`/v1/sys/metrics` without a token and reports `vault_core_unsealed`, even while
sealed. Vault reads its telemetry settings only at startup. After upgrading an
existing release, delete the Vault pods one at a time; the unseal timer unseals
each replacement. A `longhorn-manager-metrics` NetworkPolicy admits Prometheus
through Longhorn's internal-traffic policy. Prometheus restarts automatically
when [monitoring.yaml](../k8s/platform/monitoring.yaml) changes.

Fluent Bit adds Kubernetes metadata to container logs; records from managed
application namespaces carry
`observability_scope=application`. Fluent Bit also forwards each node's systemd
journal, from the moment it starts, to `kubernetes-logs-host-*` with
`log_source=host-journal` and fields such as `systemd_unit`,
`syslog_identifier` and `priority`. It uses the same one-day retention; the
local journal keeps the longer history. Filebeat sends host Lynis records through
Logstash to Kibana's **Lynis Security Audits** dashboard. The managed Lynis
run also writes `swirl_cloud_lynis_*` metrics to node-exporter's textfile
collector (`/var/lib/node_exporter/textfile_collector`): `LynisAuditFailed`
fires on a non-zero exit and `LynisAuditMissing` when no run has succeeded for
35 days. Monitoring starts with a control plane's first managed run.
Unattended upgrades install security updates without rebooting; every node
exports `swirl_cloud_reboot_required` every 15 minutes, and
`NodeRebootRequired` fires once a reboot has been pending for seven days.
`swirl-cloud-k3s-backup.service` and `swirl-cloud-vault-unseal.service` record
each run's result through an `ExecStopPost` drop-in:
`HostMaintenanceServiceFailed` fires after 15 minutes in a failed state,
`K3sRecoveryBackupMissing` after 36 hours without a successful backup and
`VaultUnsealTimerStopped` after 15 minutes without an unseal check. See
[storage and retention](#storage-and-retention) for retention settings.

Trivy Operator scans across namespaces and maintains image/SBOM, configuration,
RBAC, exposed-secret, infrastructure and compliance reports. Review current
reports or Grafana's **Trivy Security Reports** dashboard; secret findings contain
metadata rather than secret values.

```bash
kubectl get vulnerabilityreports,configauditreports,exposedsecretreports -A
```

For private application images, declare credentials through workload or
ServiceAccount `imagePullSecrets` in the application namespace. Trivy uses those
references; the application owns provisioning and rotation. Public platform
images need no fallback credentials. Shared configuration lives in
[monitoring.yaml](../k8s/platform/monitoring.yaml),
[observability-discovery.yaml](../k8s/platform/observability-discovery.yaml),
[trivy.yaml](../k8s/platform/trivy.yaml) and the scanner settings in
[values.yaml](../k8s/values.yaml).

### Application dashboards

The Grafana discovery sidecar refreshes workloads on startup and every minute.
Grafana loads changed files within its 30-second provisioning interval. Kibana
objects are imported when an application changes and reconciled every five
minutes. API failures are retried; a Kibana outage does not block Grafana.

#### Application identity

Discovery follows Pod, ReplicaSet and Job ownership to their root controller.
It groups components by the first available identity, using controller metadata
and pod-template labels:

1. A valid Argo CD tracking annotation belonging to that resource.
2. `app.kubernetes.io/part-of`.
3. `app.kubernetes.io/instance`.
4. `app.kubernetes.io/name`.
5. `app`.
6. Controller kind/name, or standalone Pod kind/name.

Use a shared `app.kubernetes.io/part-of` label to group components deployed without
Argo CD. Standard controllers use pod-name patterns covering rollouts and future
Jobs; custom-controller and orphaned pods use observed names. Namespace and pod
filters scope every panel. Different identity sources with the same display name
get separate stable dashboard IDs. Existing `apps` IDs are preserved; other
namespace names form part of the identity.

#### Metrics and logs

CPU, memory, network traffic, readiness and restarts use cAdvisor and
kube-state-metrics; they need neither an app metrics endpoint nor an `app` label.
For endpoint metrics, annotate the **pod template** and expose a reachable endpoint:

```yaml
metadata:
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/path: /metrics
    prometheus.io/port: "8080"
```

Generated dashboards show endpoint health and scrape duration. **No data** means
no metric series, not a healthy endpoint. Apps own business, HTTP, JVM and other
detailed instrumentation; additional ConfigMaps labeled
`grafana_dashboard: "1"` remain supported.

Each Grafana dashboard links to its Kibana counterpart. The Kibana data view uses
`kubernetes-logs-v2-*`; the saved search, both visualizations and dashboard all
carry application filters. Log volume, logs by container and searchable messages
work without an app label or structured severity. Filters organize data and do
not replace access controls.

#### Ownership and troubleshooting

Clone a generated dashboard before customizing it: Grafana provisions managed
files, and Kibana reconciliation overwrites managed objects. Stable IDs survive
rollouts. Removing a workload retains its dashboards and historical logs under
the normal retention policy; returning applications update the same IDs.

The sidecar has read-only workload access in managed local namespaces, shares Grafana's dashboard
volume and uses the Vault-backed `kibana-bootstrap-credentials` Secret. It needs
no Grafana API token and cannot read app Secrets or change workloads.

```bash
kubectl -n infra logs deployment/grafana -c application-discovery --tail=30
kubectl -n infra exec deployment/grafana -c application-discovery -- \
  node /application-discovery/discovery.mjs --once
```

The one-shot command exits nonzero on failure; the normal loop logs counts/errors
and retries. Config checksums automatically roll Grafana and Fluent Bit after
changes to their managed discovery/log configuration; ordinary workload changes
need no restart. Discovery/RBAC live in
[application-observability.yaml](../k8s/platform/application-observability.yaml);
the sidecar and provisioning provider live in `monitoring.yaml`.

### Source analysis

Application CI owns compilation, coverage and scanner submission. Discovery
provisions projects, binds them to `SONAR_ALM_SETTING` and creates a protected,
masked project `SONAR_TOKEN` when absent. Non-public GitLab repositories receive
private Sonar projects. Trivy scanning is independent.

| Trigger | Behavior |
|---|---|
| Default-branch CI | The app's quality job runs with its normal pipeline. |
| Discovery, every 15 minutes | Requests analysis when none exists or the latest is over 24 hours old. |
| Manual analysis | Run a default-branch GitLab pipeline with `SONAR_SCAN_ONLY=true`, or use the app's documented manual/local scanner. |

Discovery requests at most one pipeline per pass, defers while a discovered app
has an active default-branch pipeline, and waits six hours after a recent
API-triggered pipeline before retrying. Any successful analysis satisfies the
freshness target; it is not an exact daily schedule.

#### Scanner contract

Each application repository must provide:

- `sonar-project.properties` with `sonar.projectKey=<group>:<repository>` (replace
  every GitLab path slash with a colon), all backend/frontend/shared sources,
  required compiled inputs and coverage paths.
- `.sonar-auto.json`, using the actual quality job name:

  ```json
  {"version":1,"job":"sonar","scanOnlyVariable":"SONAR_SCAN_ONLY"}
  ```

- Default-branch CI rules that run only needed compilation, tests/coverage and
  analysis when `SONAR_SCAN_ONLY=true`. Exclude image builds, release packaging,
  publication, deployment and version changes.
- A protected default branch so the quality job receives `SONAR_TOKEN`. Scanner
  submission failures must be visible; quality findings may be non-blocking for
  delivery.

Projects with GitLab CI/CD disabled are skipped and logged. Missing contracts, unmapped workloads and unsupported sources fail the discovery
Job while valid repositories continue. A project can be provisioned without a
working scanner contract. Check its source inventory to confirm backend and
frontend coverage; each app documents its inventory and manual scan commands.

#### Credentials and troubleshooting

[configure-sonar-discovery.sh](../scripts/configure-sonar-discovery.sh), called by
`configure-gitlab-ci.sh`, manages a GitLab group Maintainer API token at Vault
`secret/infra/gitlab:sonar_discovery_api_token`. External Secrets projects it into
`infra`; Sonar administration uses its existing Vault-backed admin token. The
configurator also sets the named GitLab integration and import credential using
the private service domain. Rerun it before expiry: valid credentials are reused
and renewed within the script's renewal window. Discovery does not renew them.

The [discovery CronJob](../k8s/platform/sonar-apps-discovery.yaml) can list workloads
in managed local namespaces and Applications in `infra`, but cannot read app Secrets or corporate
workloads. It has no PVC, checkout or build cache; completed Jobs expire according
to their TTL/history limits.

```bash
kubectl -n infra get cronjob sonar-apps-discovery
kubectl -n infra logs -l app=sonar-apps-discovery --prefix
SONAR_JOB_NAME="sonar-apps-manual-$(date +%s)"
kubectl -n infra create job "$SONAR_JOB_NAME" --from=cronjob/sonar-apps-discovery
kubectl -n infra logs -f "job/$SONAR_JOB_NAME"
```

An immediate discovery pass still obeys freshness/retry rules; use the scan-only
pipeline for an explicit analysis. Inspect its quality job and the Sonar analysis
timestamp: scanner submission precedes server processing, and a failed quality
gate differs from submission failure.

## Storage and retention

Review physical host usage alongside filesystem usage inside each PVC. A
volume's requested capacity is not its physical disk usage. Reducing a capacity
request does not reclaim deleted-file blocks; reclaim unused data and trim the
filesystem through Longhorn.

Containerd keeps only the unpacked image layers:
[configure-k3s-registry-mirror.sh](../scripts/configure-k3s-registry-mirror.sh)
installs a `config-v3.toml.d` drop-in with `discard_unpacked_layers = true` on
every node, which roughly halves image storage. Nodes therefore cannot export
or re-serve pulled layers; keep recovery images in the off-host archive rather
than relying on a node's image store. Set `K3S_DISCARD_UNPACKED_LAYERS=false`
before running the script to keep compressed layers.

The recurring trim in
[longhorn-maintenance.yaml](../k8s/platform/longhorn-maintenance.yaml) processes
one volume at a time in the `default` and `swirl-cloud` groups. Detached volumes
are skipped while `allow-recurring-job-while-volume-detached` is disabled.
Keep `remove-snapshots-during-filesystem-trim=false` and per-volume
`unmapMarkSnapChainRemoved` set to `ignored` or `disabled` to preserve snapshots.
Delete and purge obsolete snapshots separately through Longhorn, then trim;
never delete replica files directly.
If allocation remains high after deletion, compare replica allocation with live
filesystem usage. A filesystem can remember previous trim requests; reclaiming
those blocks may require a planned workload stop, verified unmount, restart and
another trim. Follow [Longhorn's trim guidance](https://longhorn.io/docs/1.13.0/nodes-and-volumes/volumes/trim-filesystem/)
and preserve a verified recovery copy before that interruption.

Retention is declared with the component that owns the data:

| Data | Configuration |
|---|---|
| Container and application logs | [logging-agent.yaml](../k8s/platform/logging-agent.yaml), Elasticsearch ILM policy |
| Lynis audit indices | [elk.yaml](../k8s/platform/elk.yaml), separate ILM policy |
| Shared Prometheus metrics | [monitoring.yaml](../k8s/platform/monitoring.yaml), time and size limits |
| Images and packages | [GitLab retention job](../k8s/platform/gitlab-registry-retention.yaml) |
| CI artifacts | Each application's pipeline retention settings |

Read the configured values before changing retention. Update existing ILM
policies in place so already managed indices receive the change. GitLab's
bundled Prometheus is disabled; the shared Prometheus scrapes GitLab's
`/-/metrics` endpoint.
During log maintenance, remove only closed, processed archives outside the
chosen retention window; preserve current logs, unprocessed `.u` files, and
audit records. Use GitLab's supported cleanup paths for registry and artifact
data so references remain consistent.

### GitLab storage

Repositories, Registry data, packages and artifacts share the `gitlab-data` PVC
in [gitlab.yaml](../k8s/platform/gitlab.yaml). The daily retention job reconciles
native container-tag cleanup for the configured group and subgroups, and removes
Package Registry versions older than its declared retention period. GitLab
preserves protected tags and `latest`. The job's token comes from Vault
`secret/infra/gitlab` through External Secrets.

Deleting tags and packages does not itself reclaim physical Registry storage.
Use supported GitLab cleanup procedures; keep [recovery images](#runtime-and-recovery-constraints)
before removing old data. Increasing PVC capacity is separate from cleanup.

## Backups and recovery

[configure-k3s-backups.sh](../scripts/configure-k3s-backups.sh) installs the
backup timer and optional off-node storage. The root-only archives under
`/var/backups/swirl-cloud/k3s` include a SQLite backup or embedded-etcd snapshot,
server credentials, K3s configuration, and available Vault/shared-database
backups. [backup-k3s.sh](../scripts/backup-k3s.sh) defines the contents and
retention; inspect the archive and service result when verifying coverage.

GitLab needs its own native data backup plus matching configuration and secrets.
For a Registry using the metadata database, verify that the backup also contains
`db/registry_database.sql.gz`, or pair it with a verified PostgreSQL dump from the
same write/GC freeze as the Registry files. `registry.tar.gz` alone does not cover
that database. Check the installed [Registry backup configuration](https://docs.gitlab.com/omnibus/settings/backups/)
and archive contents; a successful backup exit status does not establish complete
coverage. Verify the replacement before rotating older recovery archives.

```bash
sudo systemctl status swirl-cloud-k3s-backup.timer
sudo systemctl start swirl-cloud-k3s-backup.service
sudo journalctl -u swirl-cloud-k3s-backup.service --since today
```

### Coverage and recovery points

The nightly archive runs at 02:15 UTC, keeps 7 local copies, and with off-node
storage restic keeps 7 daily, 5 weekly and 12 monthly copies. Recovery times
have not been measured yet; they need the restore drill in TODO §16.

| Data | How it is protected | Recovery point | Notes and gaps |
| --- | --- | --- | --- |
| K3s state (SQLite or etcd), server token, encryption keys, K3s configuration | Nightly archive | Up to 24 h | Restore steps are in the archive's `RESTORE.txt`. |
| Vault secrets | Raft snapshot in the nightly archive | Up to 24 h | The unseal key and bootstrap token travel in the same archive unless an exported [recovery kit](security.md#recovery-kit) holds them and separation was chosen. |
| PostgreSQL: Keycloak, SonarQube, Odoo and application databases | `pg_dumpall` in the nightly archive | Up to 24 h | No point-in-time recovery (TODO §10). |
| MongoDB | `mongodump` in the nightly archive | Up to 24 h | |
| Odoo filestore | `tar` in the nightly archive, taken after `pg_dumpall` | Up to 24 h, consistent with its database dump | |
| GitLab repositories, issues, merge requests, CI artifacts, LFS, packages | `gitlab-data` Longhorn volume only, when off-node volume backups are active | None until then | Native GitLab backup is missing (TODO §12). Repositories mirrored to GitHub survive there. |
| Container registry | Same `gitlab-data` volume | None until then | Images can be rebuilt from their pipelines; pinned upstream images are re-pulled. |
| Grafana dashboards | Provisioned from Git; user-made dashboards on the `grafana-data` volume | Git: current; volume: none until volume backups | |
| SonarQube analysis | Database in `pg_dumpall`; search index on its volume | Up to 24 h | Rebuildable: the index regenerates; rerun analyses for history. |
| Elasticsearch logs and API audit events | Not backed up | None | Short retention by design; a snapshot repository is open (TODO §14). |
| Prometheus metrics | Not backed up | None | Rebuildable; about 12 h of history today (TODO §26). |
| Kafka topics | Not backed up; volume only with volume backups | None | Applications must tolerate replaying from their own state. |
| Redis, Trivy reports | Not backed up | None | Rebuildable caches. |
| Portainer, DBGate settings | Their volumes, with volume backups | None until then | Low value; recreated by the installer. |
| Kubernetes PVC and Longhorn volume inventories | YAML in the nightly archive | Up to 24 h | Maps restored volumes back to claims. |

As of 2026-10-04 off-node storage is not configured on `swirlit-node-01` and
Longhorn's `default` BackupTarget is unavailable, so every copy shares the host's
failure domain. Configure the S3 target from the setup hub (Configure → Backups).

When configured, restic encrypts and uploads archives to S3-compatible storage.
Longhorn backs up enrolled volumes to the same private bucket through its
`default` BackupTarget, which the configurator points at the bucket and waits to
report available; the host backup enrolls newly created volumes. Once the target holds
backups, `LonghornVolumeBackupStale` fires for any volume without a backup in
36 hours. Credentials are stored root-only in
`/etc/swirl-cloud/backup.env` and the Longhorn credential Secret. Keep the recovery
password available off-host; local archives cannot survive loss of the host.

Follow the archive's datastore-specific `RESTORE.txt` and the
[Vault recovery guide](security.md#upgrade-and-recovery). Restore SQLite with K3s stopped; embedded-etcd
recovery restores the first server with its original token before other servers
rejoin. Verify application data as well as Kubernetes readiness after a restore.

### Operator recovery checklist

These steps need a person; automation cannot keep a copy that survives the host.
Do them after the first installation and repeat the kit export whenever the
Vault, K3s or backup credentials change.

1. Export the [recovery kit](security.md#recovery-kit) on the control plane that
   holds the Vault keys, choosing a passphrase of at least 16 characters:
   `scripts/export-recovery-kit.sh --separate-archives` (or `./install.sh` →
   Maintenance → Export the encrypted recovery kit).
2. Move the kit file and its passphrase off the host, into separate places (for
   example the kit on an encrypted USB key or cloud drive, the passphrase in a
   password manager). Delete the copy left in your home directory.
3. From the stored copy, run `scripts/export-recovery-kit.sh --verify FILE`; it
   must report that the kit matches every current key file.
4. Check `./install.sh --status` the next morning: the nightly backup must be
   `PASS`, and the archive's `RESTORE.txt` names the kit when it omits the Vault keys.

### Restore after losing the host

1. Prepare a replacement host with the same Ubuntu and K3s minor version, and
   clone this repository at the revision the platform ran.
2. Decrypt the recovery kit:
   `openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -in KIT -out kit.tar && tar -xf kit.tar`.
3. Obtain the latest archive: from `/var/backups/swirl-cloud/k3s` if the disk
   survived, otherwise from the off-node restic repository using the
   `RESTIC_REPOSITORY` and `RESTIC_PASSWORD` values in the kit's `backup.env`
   (`restic snapshots --tag swirl-cloud-k3s`, then `restic restore`). Check the
   archive against its `.sha256` file.
4. Restore K3s exactly as the archive's `RESTORE.txt` describes, with the server
   token and encryption files from the archive (or the kit when the archive is
   older than the keys).
5. Place `vault-unseal-key` and `vault-bootstrap-token` from the kit in
   `/var/lib/swirl-cloud` (owner root, mode `600`), then restore or start Vault
   and unseal it (`sudo systemctl start swirl-cloud-vault-unseal.service`).
6. Restore PostgreSQL, MongoDB and the Odoo filestore from the archive's
   `application-data/`, then volumes from Longhorn backups where enabled.
7. Run `./install.sh --status` and fix every `FAIL`, then re-export a recovery
   kit from the rebuilt host.

This path has not been rehearsed end to end yet (TODO §16).

## Workload rebalancing

The Descheduler can be triggered manually:

```bash
kubectl create -f k8s/addons/descheduler-run-job.yaml
kubectl get jobs -n infra -l app=descheduler -w
```

## Maintenance

Use this section to update container images and UI themes. For an installation
still using ingress-nginx or private platform images, complete the separate
[platform migration](guides/platform-migration.md) first. For routine health, storage
and backups, see [storage and retention](#storage-and-retention) and [backups and recovery](#backups-and-recovery).

### Image updates

Platform images are pinned by digest in their owning manifests and Helm values.
K3s and Longhorn use their pinned release images; two CSI sidecar pins live in
[system workload policy](../k8s/base/system-workload-hardening.yaml).
Application repositories own their images and pull credentials. There is no
platform image build pipeline, central image catalog or private registry dependency.

1. Review the upstream release's compatibility, architecture, runtime identity,
   writable paths and data format. Update the digest in the owning source;
   update app-owned database helpers in their repositories when needed.
2. Compare scanner reports across all severities, including exposed secrets.
   Check startup, readiness, authentication and service behavior with disposable
   data. For stateful services, restore a backup from the actual previous image
   and verify its data in the candidate.
3. Run [repository validation](testing.md#validation). Reconcile separately
   installed Helm releases through their shared helpers as well as GitOps
   resources. Keep a verified backup and the exact recovery image before rollout.
4. Verify the running digest, readiness, application behavior and fresh scanner
   reports. An upstream pin does not imply zero vulnerabilities; review unfixed
   advisories and scanner limitations in [platform health](#platform-health).

Keep experiments, downloaded images, reports and recovery archives outside Git.
The [validation policy](testing.md#validation-policy) defines which permanent
tests belong in this repository.

The [October 2026 record](guides/service-upgrades-2026-10.md) covers the Longhorn 1.13.0 upgrade.
The [September 2026 maintenance record](guides/service-upgrades-2026-09.md) lists
the selected releases, compatibility exceptions and recovery evidence.

### Runtime and recovery constraints

| Component | Check before changing its image |
| --- | --- |
| PostgreSQL and its helpers | Keep the same upstream Bookworm release. Restore globals and databases in isolation; check ownership, libc/ICU collation versions, locales and extensions. A matching major version alone is insufficient. |
| Vault | Preserve its UID, Raft data and audit mounts. Follow [Vault upgrade and recovery](security.md#upgrade-and-recovery) for snapshots, unseal material and rollout. |
| GitLab | Retain the matching GitLab version, configuration and secrets with its [native backup](#backups-and-recovery). |
| Keycloak | Preserve UID 1000 and writable augmentation paths. |
| SonarQube | Preserve UID 1000/GID 0 and group access to bundled files. |
| PostgreSQL / MongoDB | Preserve UID 999 and check existing volume ownership. Never recursively change ownership on active database volumes. |
| Storage components | Preserve host access and image-specific writable paths. [System hardening](../scripts/reconcile-system-hardening.sh) is independent of image selection. |

Keep a protected off-host archive of the exact recovery images and credentials.
During a registry outage, import pinned images into each node's containerd before
recovery. Retain legacy private artifacts and credentials until their installed
consumers and rollback window have both been retired. The old image-profile
files and retired inputs are handled by the [migration procedure](guides/platform-migration.md).

### UI themes

SonarQube and Vault default to dark mode; their bottom-right **Dark / Light**
buttons save a browser preference. Odoo's **Dark Mode** user-menu switch saves an
account preference;
bootstrap enables it once for the managed administrator. Preferences survive
application restarts.

#### SonarQube and Vault

Both use vendored Dark Reader assets, with license, provenance and integrity
annotations in the owning manifests. An external style-proxy script preserves
the application's Content Security Policy; asset requests remain on the same origin.

- [SonarQube](../k8s/platform/sonarqube.yaml) copies its HTML entry point in an
  init container and mounts the themed files read-only. Application bundles and
  scanner APIs are unchanged.
- [Vault](../k8s/platform/vault-ui-theme.yaml) serves only `/ui` through a separate
  NGINX helper. API traffic goes directly to Vault. The helper accepts UI GET/HEAD,
  strips forwarded credentials, has no Vault token, caches no responses, writes
  no access logs and is restricted by network policy.

When updating assets, bump the pod revision and HTML asset query versions.
After a theme or application upgrade, check authenticated navigation, console/CSP
errors, both toggle states and reload persistence. Also verify Vault's direct API
health and that the theme helper rejects API paths.

To remove Sonar's theme, remove its ConfigMap, init container, volumes and mounts
together. Removing Vault's theme Ingress restores its original `/ui` route;
retire the helper resources in the same change. Authentication and data do not
need to change.

#### Odoo

Odoo's add-ons live as plain files under
[`k8s/files/odoo-addons/`](../k8s/files/odoo-addons/): the managed
`odoo_keycloak_sso` and `platform_crm_intake`, and OCA's vendored `web_dark_mode`
with source, translations, tests and AGPL license. The chart turns each folder
into a ConfigMap that bootstrap and the application mount read-only at
`/mnt/extra-addons/<module>` ([`_odoo_addons.tpl`](../k8s/templates/_odoo_addons.tpl));
startup needs no download. The pod carries a checksum of every add-on file, so
any change restarts Odoo and its bootstrap upgrades the module.

To update the dark theme, replace the folder with the new upstream revision
(keep it byte-identical) and its provenance in `_odoo_addons.tpl`, check Odoo
compatibility, then validate and test an authenticated `/odoo` page. Confirm that dark CSS loads and
the switch works both ways. Users can select dark mode, light mode or their device
preference. Uninstall the module in Odoo before removing its files.

The bootstrap also installs CRM, Live Chat and the managed `platform_crm_intake`
add-on, upgrades that add-on on every start, and seeds Leads, the chat channel
and its out-of-hours chatbot once. The [Odoo website integration](guides/odoo-website.md)
guide covers the website's integration key, rotation and the chat widget. Take a
database dump before changing the add-on; uninstalling CRM does not restore the
earlier schema, so recovery means restoring that dump.

## Common Incidents

Record incidents and gather their evidence with the [incident response](guides/incident-response.md)
guide. Every alert links to its section of the [alert runbooks](guides/alert-runbooks.md)
(`runbook_url`) and carries an `owner` label (`platform`, `security` or
`application`). The incidents below cover situations that span several alerts.

### Root Application is Degraded while Synced

Argo CD aggregates the health of every chart resource. A failed CronJob run is
enough to degrade it. Find the unhealthy resource, then read its logs:

```bash
kubectl get pods -A | grep -vE 'Running|Completed'
kubectl get jobs -n infra
kubectl -n infra logs -l app=sonar-apps-discovery --prefix --tail=20
```

A Sonar discovery `HTTP 403` means its group token cannot read one discovered
project. Grant access or move the project into the configured group; see
[source analysis credentials](#credentials-and-troubleshooting).

### ExternalSecret is not Ready

Check whether Vault is sealed or the referenced path is missing:

```bash
kubectl get externalsecrets -A
kubectl -n infra describe externalsecret <name>
kubectl -n infra exec vault-0 -- vault status
sudo systemctl show swirl-cloud-vault-unseal.service -p Result -p ExecMainStatus
```

Follow [Vault host unseal](security.md#host-unseal-service) and never paste
recovery material into a command line.

### Argo CD reverts a manual change

Automatic sync, prune and self-heal are enabled on the root Application.
Commit the change to the owning manifest or Helm values; Kubernetes edits are
temporary. Separately installed Helm releases (Longhorn, Traefik, Vault, External
Secrets, Argo CD) are reconciled through the installer or Ansible instead.

### Argo CD itself is unhealthy or lost

Argo CD does not manage itself. It is the Helm release `argocd` in `infra`
(chart and image pinned as `DEFAULT_ARGOCD_CHART_VERSION` and
`DEFAULT_ARGOCD_IMAGE_TAG` in [platform.env](../config/platform.env)), installed
by `scripts/reconcile-platform-release.sh argocd` with the rendered
`config/argocd-values.yaml` (plus `argocd-ha-values.yaml` in HA). Both the
installer (*Install/upgrade Argo CD*) and `ansible/deploy.yml`
(`install_argocd`) render the values, run that release step and then reapply the
root and application Applications, so either one restores a broken or deleted
release from Git. Compare the live release with the contract first:

```bash
helm list -n infra --filter '^argocd$'
helm -n infra get values argocd | grep -A1 'image:'
```

Applications resume from Git after the release is back; workloads keep running
while Argo CD is down, but nothing reconciles.

### Longhorn volume is degraded or faulted

```bash
kubectl get volumes.longhorn.io -n longhorn-system \
  -o custom-columns=NAME:.metadata.name,PVC:.status.kubernetesStatus.pvcName,STATE:.status.state,ROBUSTNESS:.status.robustness
```

Inspect replicas and node disk pressure in the Longhorn UI. Never delete replica
files directly; follow [storage and retention](#storage-and-retention).

### Registry push or pull fails from outside the cluster

Registry clients cannot answer browser challenges. Confirm the Cloudflare bot
exceptions in [Cloudflare configuration](dns.md#cloudflare) and compare with the
internal endpoint used by CI (`gitlab-registry.infra.svc.cluster.local:5050`).

### An application dashboard or log view is missing

Run a one-shot discovery pass and read its errors; see
[ownership and troubleshooting](#ownership-and-troubleshooting).

### The nightly backup failed

```bash
sudo systemctl status swirl-cloud-k3s-backup.service
sudo journalctl -u swirl-cloud-k3s-backup.service --since yesterday
```

Correct the cause and start the service manually. See
[backups and recovery](#backups-and-recovery).

### An operator is banned by Fail2ban or CrowdSec

Reconnect through the provider console or a private Tailscale path, then remove
the ban with `sudo fail2ban-client set sshd unbanip <address>` or
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
