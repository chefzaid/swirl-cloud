# Alert Runbooks

Every Prometheus alert links here through its `runbook_url` annotation and
carries an `owner` label:

| Owner | Responsible for |
| --- | --- |
| `platform` | Cluster, storage, shared services and host maintenance |
| `security` | Firewall, intrusion prevention, audits, vulnerabilities, identity attacks and compliance |
| `application` | Workloads in application namespaces (`apps`, `apps-<environment>`); the owning repository's team acts, the platform team helps |

Start every investigation with `./install.sh --status`: it checks the platform
end to end and prints a `Next:` command for each problem. Alert rules live in
[`k8s/platform/monitoring.yaml`](../../k8s/platform/monitoring.yaml); change a
threshold there, not in Alertmanager.

## Capacity

### NodeFilesystemSpaceLow

A host filesystem has less than 20 % free. Kubelet starts deleting images at
85 % used and evicts pods when ephemeral storage runs out.

- Find the largest consumers: `sudo du -xh --max-depth=3 /var/lib /var/backups | sort -h | tail`.
- Move old recovery archives off the host (keep the pinned recovery images), prune
  unused images with `sudo k3s crictl rmi --prune`, and shorten log retention if logs grew.
- See [storage and retention](../operations.md#storage-and-retention).

### NodeFilesystemSpaceCritical

Less than 10 % free: evictions and failed writes are imminent. Act as for
[NodeFilesystemSpaceLow](#nodefilesystemspacelow) immediately, starting with the
largest directory, and postpone backups or upgrades until space is back.

### NodeMaintenanceHeadroomLow

Less than 30 GiB free on the K3s filesystem. Backups, image pulls and upgrades
need that headroom; on 2026-09-28 a backup with too little space evicted pods.
Free space as for [NodeFilesystemSpaceLow](#nodefilesystemspacelow) before the
next maintenance or nightly backup.

### NodeFilesystemFillingWithinDay

The filesystem is predicted to fill within 24 hours at the current rate.
Find what is growing (`sudo du` as above, `kubectl top pods -A`, CI caches,
container logs) and stop or rotate it; then free space.

### PersistentVolumeSpaceLow

A volume has less than 20 % free. Check what the service stores (database
growth, indices, uploads) and either clean up through the service, shorten its
retention, or expand the claim if the StorageClass allows it:
`kubectl -n <namespace> edit pvc <claim>` and raise `spec.resources.requests.storage`.

### PersistentVolumeSpaceCritical

Less than 10 % free; the service may stop writing. Expand the claim or free
space now as for [PersistentVolumeSpaceLow](#persistentvolumespacelow).

### PersistentVolumeFillingWithinDay

The volume is predicted to fill within 24 hours. Identify the writer from the
claim's namespace and stop runaway growth before expanding.

### ClusterCapacityRequestsHigh

Running pods request more than 90 % of the cluster's allocatable CPU or memory.
Requests reserve capacity whether used or not, so new pods, rollouts and pods
moved after a restart may stay Pending even when real usage is low.

- Compare requested with used capacity in Grafana **Capacity Planning** and
  **Cost Showback → Rightsizing**; lower the largest over-sized requests in the
  owning manifests first.
- When usage is also high, add a server: the dashboard shows how many cores and
  how much memory keep requests below 70 %. See [capacity planning](capacity-planning.md).

### ClusterCannotSurviveNodeLoss

The capacity left after losing the largest server is smaller than what running
pods request, so a host failure would leave workloads unscheduled. Lower
requests or add a server until the ratio on **Capacity Planning** stays below
100 %; this is a prerequisite for high availability.

## Kubernetes workloads

### KubernetesDeploymentUnavailable

A Deployment has had fewer available replicas than desired for ten minutes.

- `kubectl -n <namespace> describe deployment <name>` and `kubectl -n <namespace> get pods -l app=<name>`.
- Look for image pull errors, failing probes, pending pods (capacity or volumes) and crash loops; follow
  [KubernetesPodCrashLooping](#kubernetespodcrashlooping) when containers restart.
- For application namespaces, check the latest delivery in Argo CD and roll back to the previous revision if the release caused it.

### KubernetesStatefulSetUnavailable

A StatefulSet has fewer ready replicas than desired. Datastores and Vault run
as StatefulSets: check the pod (`describe`, logs), its volume attachment in
Longhorn and, for Vault, [VaultSealed](#vaultsealed).

### KubernetesPodCrashLooping

A container keeps exiting. `kubectl -n <namespace> logs <pod> -c <container> --previous`
shows why. Typical causes are missing configuration or secrets
([ExternalSecretNotReady](#externalsecretnotready)), an unreachable dependency
([DatastoreDown](#datastoredown)), or a memory limit
([KubernetesContainerOOMKilled](#kubernetescontaineroomkilled)).

### KubernetesContainerOOMKilled

A container was killed for exceeding its memory limit. Compare its usage with
the limit (Grafana **Cost Showback → Rightsizing**, or `kubectl top pod`). Raise
the limit in the owning manifest when the workload legitimately needs more, or
fix the leak; repeated OOM kills cause crash loops.

### KubernetesPersistentVolumeReleased

A volume's claim was deleted but the volume (reclaim policy `Retain`) still
holds data and disk space. Confirm nobody needs the data, back it up if in
doubt, then delete the PersistentVolume and its Longhorn volume.

### KubernetesPersistentVolumeClaimPending

A claim has not bound for 15 minutes, so its pod cannot start.
`kubectl -n <namespace> describe pvc <claim>` names the cause: unknown
StorageClass, Longhorn unable to place replicas (disk space, node tags) or a
quota. Check the Longhorn UI for scheduling errors.

### KubernetesJobFailed

A Job failed and no later run of its CronJob succeeded.
`kubectl -n <namespace> logs job/<job>` shows the error. Platform Jobs (Keycloak,
Kibana or Elasticsearch bootstrap, registry retention, discovery) rerun on the
next Argo CD sync or schedule after the cause is fixed; delete the failed Job to
clear the alert once a run succeeds.

## Platform services

### KubernetesNodeNotReady

A node has not reported Ready for five minutes; its pods are being or will be
rescheduled, and single-replica volumes on it are unavailable.

- `kubectl describe node <node>` (conditions, pressure), then on the host:
  `sudo journalctl -u k3s -u k3s-agent -n 100` and `sudo systemctl status k3s`.
- Check disk, memory and the private transport (Tailscale or vRack) to the other nodes.
- If the host is gone, follow [node fencing](node-fencing.md) before recovering its storage.

### VaultSealed

Vault is sealed: ExternalSecrets cannot refresh and new pods that need secrets
fail. Run `sudo systemctl start swirl-cloud-vault-unseal.service` on a control
plane (or **Maintenance → Unseal Vault now** in `./install.sh`), then check
`kubectl -n infra logs vault-0 -c vault --tail=50`. If the unseal keys are
missing, use the recovery kit ([security](../security.md)).

### VaultAuditDeviceFailing

Vault cannot write an audit record. Vault refuses requests when no audit device
works, so secrets stop flowing. Check the audit volume's free space and
`kubectl -n infra logs vault-0 -c vault`; free space or fix the device, then
confirm requests succeed again.

### LonghornVolumeDegraded

A volume is missing healthy replicas or rebuilding. Open the Longhorn UI: if a
rebuild is running, let it finish; otherwise check node disk space and node
status. On a single node, volumes with more than one requested replica stay
degraded by design; align the replica count with the number of storage nodes.

### LonghornVolumeBackupStale

Off-node backups are active but this volume's last backup is older than 36
hours. Check the backup target and the recurring job in the Longhorn UI
(credentials, bucket reachability, target free space) and run a backup now.

### LonghornVolumeFaulted

The volume has no usable replica; its workload is down and data may be at
risk. Do not delete anything. In the Longhorn UI, check whether a replica can be
salvaged; otherwise restore from the latest backup. See
[backups and recovery](../operations.md#backups-and-recovery).

### KeycloakPasswordFailuresHigh

More than 20 failed password checks in 15 minutes in one realm: someone may be
guessing passwords. Keycloak's brute-force protection locks accounts
temporarily. In Kibana, filter `identity-audit-*` on
`keycloak.type : LOGIN_ERROR` to see usernames and source addresses; block
abusive sources at the edge and contact affected users. See
[identity events](../security.md#identity-events).

### ExternalSecretNotReady

An ExternalSecret could not read from Vault for 15 minutes, so its Secret is
stale or missing. `kubectl -n <namespace> describe externalsecret <name>` shows
the reason: Vault sealed ([VaultSealed](#vaultsealed)), a missing path or field,
or a policy that does not allow the read. Fix the path or seed the value with
`scripts/configure-vault.sh`.

### ElasticsearchUnreachable

The exporter cannot read Elasticsearch health; logs and audits are not being
indexed (Fluent Bit buffers them on disk meanwhile).
`kubectl -n infra get pods -l app=elasticsearch` and its logs; check the data
volume's free space ([PersistentVolumeSpaceLow](#persistentvolumespacelow)).

### ElasticsearchClusterRed

A primary shard is unassigned; some indices cannot be written or searched.
`GET _cluster/allocation/explain` (from the Elasticsearch pod, as `elastic`)
names the cause, usually disk watermarks. Free space or delete expired indices;
do not delete audit indices before their retention ends.

### ElasticsearchClusterYellow

Replica shards are unassigned. On a single node this means an index asks for
replicas: set `number_of_replicas` to 0 on it and fix the template that created it.

### DatastoreDown

A shared PostgreSQL, MongoDB, Redis or Kafka exporter cannot reach its
datastore; dependent applications fail. Check the datastore pod in `infra`
(`kubectl -n infra get pods`, logs, volume) and its credentials
([ExternalSecretNotReady](#externalsecretnotready)).

### ArgoCDApplicationUnhealthy

An Argo CD application reports Degraded, Missing or Unknown health for 15
minutes. Open it in Argo CD to see which resource is unhealthy, then follow the
matching workload runbook. For an application release, roll back to the
previous revision through the deployment flow.

### ArgoCDApplicationOutOfSync

Live state has differed from Git for 30 minutes: a sync is failing or someone
changed resources by hand. Argo CD shows the diff and the last sync error;
fix the manifest or revert the manual change (self-heal reverts it on the next
sync). See [Argo CD operations](../deployment.md).

### TLSCertificateExpiringSoon

A certificate served by Traefik expires within 14 days. Rerun
`scripts/configure-cloudflare.sh` (or the installer): it issues a new Cloudflare
Origin CA certificate when fewer than 30 days remain. Replace other certificates
in their TLS Secret. See [public DNS and ingress](../dns.md).

### TLSCertificateExpiryCritical

A certificate expires within three days. Renew it now as for
[TLSCertificateExpiringSoon](#tlscertificateexpiringsoon); once expired,
browsers and API clients reject the service.

## Host security and maintenance

### LynisAuditFailed

The monthly Lynis audit exited with an error. `sudo journalctl -u swirl-cloud-lynis.service -n 50`
shows why; rerun it with `sudo systemctl start swirl-cloud-lynis.service`.

### LynisAuditMissing

No successful Lynis audit for 35 days. Check the timer:
`systemctl list-timers swirl-cloud-lynis.timer`; reinstall it with
`scripts/configure-lynis-schedule.sh` and run an audit.

### TrivyNewCriticalVulnerability

A running image gained critical vulnerabilities above its own recent maximum,
or a new image arrived with some. Open Grafana **Trivy Security Reports**,
update the image when a fixed version exists, and record the decision in the
[vulnerability triage](../security.md#vulnerability-triage). Application images
are fixed in their own repository.

### HostFirewallDisabled

UFW is inactive or INPUT is not default-deny: every listening port may be
reachable from the Internet. Check `sudo ufw status verbose`, then rerun
`scripts/configure-node-security.sh --apply` (it re-enables and verifies UFW).
Find out why it was disabled before closing the incident.

### HostIntrusionPreventionDown

Fail2ban or CrowdSec (or its firewall bouncer) is not running, so brute-force
sources are not blocked. `sudo systemctl status fail2ban crowdsec crowdsec-firewall-bouncer`,
then restart the stopped service and read its journal.

### HostIntrusionMetricsStale

Intrusion-prevention metrics have not refreshed for 30 minutes, so the two
alerts above cannot fire. `sudo systemctl status swirl-cloud-intrusion-metrics.timer`
and restart it.

### HostBruteForceWave

A host is banning attackers at an unusual rate. Protection is working; confirm
bans succeed (`sudo fail2ban-client status sshd`, `sudo cscli decisions list`)
and consider restricting SSH to the private interface with keys only.

### NodeRebootRequired

Security updates installed more than a week ago need a reboot to take effect.
Plan a maintenance window: check a fresh backup and Vault unseal access, drain
the node on multi-node clusters, reboot, then run `./install.sh --status`.

### HostMaintenanceServiceFailed

A platform maintenance unit (backup, backup verification, unseal, AIDE, rkhunter,
metrics) failed its last run. `sudo journalctl -u <unit> -n 50` shows why; rerun it with
`sudo systemctl start <unit>`. A backup that reports the backup lock still held
waited four hours for a verification; rerun it once that ends.

For `swirl-cloud-k3s-backup-verify.service`, the weekly read-back of the off-node
restic copy failed, so off-node recovery is unproven until it passes. The journal
names the cause: `restic check` errors (damaged or missing pack data, or a stale
lock), a newest archive that does not match its SHA-256 or is not a complete
`tar.gz`, an empty repository, or a backup still holding the lock after an hour.
Keep the local archives, confirm a fresh backup uploads
(`sudo systemctl start swirl-cloud-k3s-backup.service`), then rerun the verification:
within the same week (Monday to Sunday UTC) it checks the same data subset, and
`sudo env VERIFY_DATA_SUBSETS=1 /usr/local/sbin/swirl-cloud-k3s-backup-verify`
checks them all. If restic reports the repository locked while neither
`swirl-cloud-k3s-backup.service` nor `swirl-cloud-k3s-backup-verify.service` is
active (`systemctl is-active`), a killed run left a stale lock; clear it with
`sudo bash -c 'set -a; source /etc/swirl-cloud/backup.env; restic unlock'`, which
removes only stale locks, and rerun. Otherwise follow
restic's [troubleshooting guide](https://restic.readthedocs.io/en/stable/077_troubleshooting.html);
see [backups and recovery](../operations.md#backups-and-recovery).

### K3sRecoveryBackupMissing

No successful platform recovery archive for 36 hours. Without it, a lost host
cannot be rebuilt to its current state. `sudo journalctl -u swirl-cloud-k3s-backup.service -n 50`,
fix the cause (space, credentials, S3 target), then run
**Maintenance → Run the recovery backup now** in `./install.sh`.

### VaultUnsealTimerStopped

The Vault unseal service has not run for 15 minutes, so Vault would stay sealed
after a restart. `sudo systemctl status swirl-cloud-vault-unseal.timer` and
restart it; see [VaultSealed](#vaultsealed).

## Security detection

These alerts come from the `security-detection` job, which checks identity
events, the Kubernetes API audit log and Keycloak every five minutes and posts
findings to Alertmanager. Each stays active for 30 minutes after its evidence
was last seen. See [security detection](security-detection.md).

### IdentityBruteForceThenSuccess

An account had at least five failed logins in 15 minutes and then signed in.
Either the owner remembered the password or someone guessed it.

- Contact the owner through another channel. In Kibana, filter `identity-audit-*`
  on `keycloak.username` for the failures and the successful login with source addresses.
- If the owner did not sign in: disable the user in Keycloak, end their sessions
  (**Users → Sessions → Sign out**), reset the password and review what the session did.

### KubernetesRBACChangedOutsideGitOps

Someone other than Argo CD or K3s created, changed or deleted a Role,
ClusterRole or binding. Changes outside Git are reverted or drift, and new
bindings can grant access silently.

- In Kibana, filter `kubernetes-audit-*` on `user.username` and
  `objectRef.resource : *bindings` for the exact objects and source addresses.
- If the change is intended, back-port it to Git; if not, revert it and rotate
  the credentials of the identity that made it. Installer reruns by
  `system:admin` also trigger this alert.

### KubernetesSecretReadByNewIdentity

An identity read Secrets that it had not read during the previous week. New
workloads and new operators cause this legitimately; a compromised token or a
person browsing Secrets also does.

- Check `kubernetes-audit-*` for the identity's Secret reads (`objectRef.name`,
  `sourceIPs`) and whether a matching deployment or onboarding just happened.
- If unexpected, revoke the identity (delete the ServiceAccount token or remove
  the binding) and rotate the Secrets it read.

### NewPlatformAdministrator

A user joined `platform-admins` in the platform realm or received the `admin`
role in the Keycloak master realm. The alert stays for a day.

- Confirm the grant was approved. If not, remove the membership or role, end the
  user's sessions and review the Keycloak admin events for who granted it.

## Ingress and monitoring

### IngressServiceErrorRateHigh

More than 5 % of requests to a service returned 5xx for 15 minutes. Open
Grafana **Ingress Traffic** for the affected service, then check its pods and
logs; a recent release is the usual cause and the fastest fix is a rollback.

### PrometheusTargetDown

Prometheus has not scraped a target for ten minutes, so its alerts cannot fire.
Check the target pod or exporter (`kubectl get pods -A -o wide | grep <instance>`)
and the network policies between `infra` and its namespace.

### AlertNotificationsFailing

Every notification Alertmanager tried through one integration (`email` or
`webhook`) failed in the last hour, so alerts reach only the other receiver.
Read the reason in `kubectl -n infra logs deploy/alertmanager | grep -i notify`.
For `email`, a revoked key, a changed password or the provider's sending limit
are the usual causes: rerun `scripts/configure-platform-mail.py` with the relay
settings, which checks the login and sender before storing anything
([platform mail](platform-mail.md#troubleshooting)). For `webhook`, check that
GitLab is up and that its alert integration token matches Vault
`infra/monitoring`.

## Service levels

### CIJobsQueueingLong

Over six hours, one CI job in ten waited more than ten minutes before a runner
picked it up. Pipelines and deployments slow down for every team.

- Check `gitlab_runner_concurrent` and the queue on Grafana **Service Levels**;
  the shared runner runs one job at a time by default.
- Raise the runner's `concurrent` setting and per-job limits within the capacity
  shown on **Capacity Planning** (TODO §26), or add a runner on another server.

### SLOAvailabilityBudgetBurnFast

An endpoint failed more than 7.2 % of its availability probes over the last
hour and is still failing: at this rate it uses the month's 0.5 % error budget
in about two days. Users see errors now.

- Open Grafana **Service Levels** and the endpoint's probe result:
  `curl -sSI https://<host>/` shows the status code or TLS error.
- A 5xx points at the service behind the Ingress (see
  [KubernetesDeploymentUnavailable](#kubernetesdeploymentunavailable)); a
  Cloudflare 52x error at Traefik or the origin; a TLS error at the certificate
  ([TLSCertificateExpiryCritical](#tlscertificateexpirycritical)).
- Roll back a release that caused it, then record the incident.

### SLOAvailabilityBudgetBurnSlow

An endpoint failed more than 3 % of its probes over six hours and is still
failing: the month's budget lasts about five days at this rate. Look for
intermittent failures (restarts, OOM kills, a flapping dependency) on the
**Service Levels** dashboard and fix them before they become an outage.

## Compliance

### ComplianceControlRegressed

A compliance control is now failing or lost its evidence, compared with its
usual state (the median of the last six hours). Run `./install.sh --compliance` to see which
check changed and the failing series, then follow that check's alert runbook
(for example [HostFirewallDisabled](#hostfirewalldisabled) or
[K3sRecoveryBackupMissing](#k3srecoverybackupmissing)). See
[compliance evidence](compliance-evidence.md).

## Related Guides

- [Operations](../operations.md)
- [Security](../security.md)
- [Compliance evidence](compliance-evidence.md)
