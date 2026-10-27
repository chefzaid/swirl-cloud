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
| Homepage tile | Listed on `cloud.<your-domain>` with live pod status | `gethomepage.dev/*` annotations on the Ingress. For the application's own icon, add it to [`k8s/files/homepage-icons/`](../k8s/files/homepage-icons/) and a key in the `homepage-icons` ConfigMap ([homepage.yaml](../k8s/platform/homepage.yaml)), then set `gethomepage.dev/icon: /icons/<file>`; the portal serves it from its own origin and restarts to pick it up. |

### Platform health

Prometheus alerts cover capacity, workloads, jobs, storage, scrapes, platform
services, host maintenance and ingress. Thresholds and fixes are in the
[alert runbooks](guides/alert-runbooks.md).

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

**No data** means no series, not a healthy endpoint. Apps own detailed
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
| Lynis audit indices | [elk.yaml](../k8s/platform/elk.yaml), separate ILM policy |
| Shared Prometheus metrics | [monitoring.yaml](../k8s/platform/monitoring.yaml) (Prometheus arguments), time and size limits |
| Images and packages | [GitLab retention job](../k8s/platform/gitlab-registry-retention.yaml) |
| CI artifacts | Each application's pipeline settings |

Update ILM policies in place so managed indices follow. In log maintenance,
remove only closed, processed archives outside the window; keep current logs,
unprocessed `.u` files and audit records. Clean registry and artifact data only
through GitLab's supported paths.

The log policy was renamed from `swirl-cloud-logs-7d` (same phases); Fluent Bit
and Logstash move indices and templates to `swirl-cloud-logs`. Do not remove a
policy from indices to switch it: that leaves them unmanaged. Nothing deletes
the old policy. Two days after the change, check in Kibana Dev Tools that
nothing uses it, then delete it. Fix a template that still names it first:
`kubectl -n infra rollout restart deployment/logstash` and
`kubectl -n host-agents rollout restart daemonset/fluent-bit` reinstall theirs.

```text
GET */_ilm/explain?filter_path=indices.*.policy,indices.*.phase_execution.policy
GET _ilm/policy/swirl-cloud-logs-7d?filter_path=*.in_use_by
GET _template/kubernetes-logs-v2,kubernetes-logs-host?filter_path=**.lifecycle
DELETE _ilm/policy/swirl-cloud-logs-7d
```

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
version and image under `gitlab/`.

The timer runs the installed copy `/usr/local/sbin/swirl-cloud-k3s-backup`. Script
changes apply only after rerunning `./scripts/configure-k3s-backups.sh` on the
control plane, which also starts a backup (the installer and
[Ansible reconciliation](deployment.md#platform-reconciliation) do this too).
**Maintenance → Run the recovery backup now** in `./install.sh` runs the
installed copy unchanged.

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

The archive runs at 02:15 UTC and keeps 7 local copies; restic keeps 7 daily, 5
weekly and 12 monthly. Recovery times are unmeasured (restore drill, TODO §16).

| Data | How it is protected | Recovery point | Notes and gaps |
| --- | --- | --- | --- |
| K3s state (SQLite or etcd), server token, encryption keys, K3s configuration | Nightly archive | Up to 24 h | Restore steps are in the archive's `RESTORE.txt`. |
| Vault secrets | Raft snapshot in the nightly archive | Up to 24 h | The unseal key and bootstrap token travel in the same archive unless an exported [recovery kit](security.md#recovery-kit) holds them and separation was chosen. |
| PostgreSQL: Keycloak, SonarQube, Odoo and application databases | `pg_dumpall` in the nightly archive | Up to 24 h | No point-in-time recovery (TODO §10). |
| MongoDB | `mongodump` in the nightly archive | Up to 24 h | |
| Odoo filestore | `tar` in the nightly archive, taken after `pg_dumpall` | Up to 24 h, consistent with its database dump | |
| GitLab configuration and secrets (`/etc/gitlab`), version and image | `tar` of the running container in the nightly archive (`gitlab/`) | Up to 24 h | Restore before GitLab starts against restored data, with the same image. |
| GitLab repositories, issues, merge requests, CI artifacts, LFS, packages | `gitlab-data` Longhorn volume only, when off-node volume backups are active | None until then | Native GitLab backup is missing; scope waits on an owner decision ([Needs Input](../TODO.md#needs-input), TODO §12). Repositories mirrored to GitHub survive there. |
| Container registry | Same `gitlab-data` volume | None until then | Images can be rebuilt from their pipelines; pinned upstream images are re-pulled. |
| Grafana dashboards | Provisioned from Git; user-made dashboards on the `grafana-data` volume | Git: current; volume: none until volume backups | |
| SonarQube analysis | Database in `pg_dumpall`; search index on its volume | Up to 24 h | Rebuildable: the index regenerates; rerun analyses for history. |
| Elasticsearch logs and API audit events | Not backed up | None | Short retention by design; a snapshot repository is open (TODO §14). |
| Prometheus metrics | Not backed up | None | Rebuildable; about 12 h of history today (TODO §26). |
| Kafka topics | Not backed up; volume only with volume backups | None | Applications must tolerate replaying from their own state. |
| Redis, Trivy reports | Not backed up | None | Rebuildable caches. |
| Portainer, DBGate settings | Their volumes, with volume backups | None until then | Low value; recreated by the installer. |
| Kubernetes PVC and Longhorn volume inventories | YAML in the nightly archive | Up to 24 h | Maps restored volumes back to claims. |

As of 2026-10-04, `swirlit-node-01` has no off-node storage and Longhorn's
`default` BackupTarget is unavailable: every copy shares the host's failure
domain. Configure the S3 target in the setup hub (Configure → Backups).

Off-node, restic encrypts and uploads archives to S3-compatible storage, and
Longhorn backs up enrolled volumes to the same bucket through its `default`
BackupTarget (daily at 03:15 UTC, 14 kept). Credentials are root-only in
`/etc/swirl-cloud/backup.env` and the Longhorn credential Secret. Keep the
recovery password off-host.

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
the last command reads all data (full egress; keep clear of 02:15 UTC):

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

1. Prepare a host with the same Ubuntu and K3s minor version, and clone this
   repository at the revision the platform ran.
2. Decrypt the kit:
   `openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -in KIT -out kit.tar && tar -xf kit.tar`.
3. Get the latest archive from `/var/backups/swirl-cloud/k3s` if the disk
   survived, otherwise from restic with `RESTIC_REPOSITORY` and `RESTIC_PASSWORD`
   from the kit's `backup.env` (`restic snapshots --tag swirl-cloud-k3s`, then
   `restic restore`). Check it against its `.sha256` file.
4. Restore K3s as `RESTORE.txt` describes, with the server token and encryption
   files from the archive (or the kit, if the archive predates the keys).
5. Put `vault-unseal-key` and `vault-bootstrap-token` from the kit in
   `/var/lib/swirl-cloud` (root, mode `600`), restore or start Vault, and unseal
   it: `sudo systemctl start swirl-cloud-vault-unseal.service`.
6. Restore PostgreSQL, MongoDB and the Odoo filestore from `application-data/`,
   then volumes from Longhorn backups where enabled. Restore `/etc/gitlab` from
   `gitlab/etc-gitlab.tar` before GitLab starts, on the image in
   `gitlab/version.txt`.
7. Run `./install.sh --status`, fix every `FAIL`, and export a new kit.

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

The pod furthest above its memory request goes first. CI jobs request 2 GiB,
about what a test job uses ([gitlab-runner.yaml](../k8s/platform/gitlab-runner.yaml)),
so the scheduler holds a job while the node's memory is fully requested; a job
still pending after the runner's 10-minute `poll_timeout` fails and needs a
retry. Frequent `Evicted` events in `kubectl get events -A` mean the node needs
more memory or lower requests.

The installer, node enrollment and Ansible write the drop-in before K3s starts.
On a running node it applies at the next K3s restart, or now with
`scripts/configure-k3s-kubelet-memory.sh --restart` (run it when no CI job is
running; the kubelet and on a server the API restart for about 15 seconds; pods
keep running). `./install.sh --status`, or the script's `--check`, reports
whether it is active.

```bash
kubectl get node -o custom-columns=NAME:.metadata.name,CAPACITY:.status.capacity.memory,ALLOCATABLE:.status.allocatable.memory
```

## Maintenance

Installations still on ingress-nginx or private platform images must finish the
[platform migration](guides/platform-migration.md) first.

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
([validation policy](testing.md#validation-policy)). Past upgrades:
[October 2026](guides/service-upgrades-2026-10.md) (Longhorn 1.13.0) and
[September 2026](guides/service-upgrades-2026-09.md).

### Runtime and recovery constraints

| Component | Check before changing its image |
| --- | --- |
| PostgreSQL and its helpers | Keep the same upstream Bookworm release. Restore globals and databases in isolation; check ownership, libc/ICU collation versions, locales and extensions. A matching major version alone is insufficient. |
| Vault | Preserve its UID, Raft data and audit mounts. Follow [Vault upgrade and recovery](security.md#upgrade-and-recovery). |
| GitLab | Keep the matching version, configuration and secrets with its [native backup](#backups-and-recovery); the archive's `gitlab/` records all three. |
| Keycloak | Preserve UID 1000 and writable augmentation paths. |
| SonarQube | Preserve UID 1000/GID 0 and group access to bundled files. |
| PostgreSQL / MongoDB | Preserve UID 999 and check volume ownership. Never recursively change ownership on active database volumes. |
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
