# Alert Runbooks

Every alert links here through its `runbook_url` annotation and carries an `owner`
label:

| Owner | Responsible for |
| --- | --- |
| `platform` | Cluster, storage, shared services and host maintenance |
| `security` | Firewall, intrusion prevention, audits, vulnerabilities, identity attacks and compliance |
| `application` | Workloads in `apps` and `apps-<environment>`; the owning repository's team acts, the platform team helps |

Start with `./install.sh --status`: it prints a `Next:` command for each problem.
Change thresholds in
[`k8s/files/prometheus/rules.yaml`](../../k8s/files/prometheus/rules.yaml), not in
Alertmanager.

## Capacity

### NodeFilesystemSpaceLow

A host filesystem has under 20 % free. Kubelet deletes images at 85 % used and
evicts pods when ephemeral storage runs out. Find the largest consumers
(`sudo du -xh --max-depth=3 /var/lib /var/backups | sort -h | tail`), move old
recovery archives off the host (keep the pinned recovery images), run
`sudo k3s crictl rmi --prune` and shorten log retention. See
[storage and retention](../operations.md#storage-and-retention).

### NodeFilesystemSpaceCritical

Under 10 % free: evictions and failed writes are imminent. Act now as for
[NodeFilesystemSpaceLow](#nodefilesystemspacelow); postpone backups and upgrades.

### NodeFilesystemInodesLow

A host filesystem has under 10 % of its inodes free: it refuses new files even
with space left, and pods fail to start or write. Find the directories holding
the most files (`sudo find /var -xdev -type f | cut -d/ -f1-5 | sort | uniq -c | sort -n | tail`);
the usual causes are unpacked image layers (`sudo k3s crictl rmi --prune`), a
runaway cache or session directory, and leftover build workspaces.

### NodeMaintenanceHeadroomLow

Under 30 GiB free on the K3s filesystem, the headroom backups, image pulls and
upgrades need. Free space as for [NodeFilesystemSpaceLow](#nodefilesystemspacelow)
before the next maintenance or nightly backup.

### NodeFilesystemFillingWithinDay

The filesystem will fill within 24 hours. Find what grows (`sudo du`,
`kubectl top pods -A`, CI caches, container logs), stop or rotate it, then free
space.

### PersistentVolumeSpaceLow

A volume has under 20 % free. Clean up through the service, shorten its
retention, or expand the claim if the StorageClass allows it:
`kubectl -n <namespace> edit pvc <claim>`, raising `spec.resources.requests.storage`.

### PersistentVolumeSpaceCritical

Under 10 % free; the service may stop writing. Act now as for
[PersistentVolumeSpaceLow](#persistentvolumespacelow).

### PersistentVolumeFillingWithinDay

The volume will fill within 24 hours. Find the writer from the claim's namespace
and stop runaway growth before expanding.

### ClusterCapacityRequestsHigh

Pods request over 90 % of allocatable CPU or memory, so new or moved pods may stay
Pending even when real usage is low. Compare requests with usage in Grafana
**Capacity Planning** and **Cost Showback → Rightsizing** and lower the largest
over-sized requests. If usage is also high, add a server; the dashboard shows the
size that keeps requests below 70 %. See [capacity planning](capacity-planning.md).

### ClusterCannotSurviveNodeLoss

After losing the largest server, the pods would not fit. Lower requests or add a
server until the ratio on **Capacity Planning** stays below 100 %; high
availability requires this.

## Kubernetes workloads

### KubernetesDeploymentUnavailable

A Deployment has lacked available replicas for ten minutes. Run
`kubectl -n <namespace> describe deployment <name>` and
`kubectl -n <namespace> get pods -l app=<name>`; look for image pull errors,
failing probes, Pending pods and crash loops
([KubernetesPodCrashLooping](#kubernetespodcrashlooping)). In application
namespaces, roll back in Argo CD if the latest release caused it.

### KubernetesStatefulSetUnavailable

A StatefulSet (datastores, Vault) lacks ready replicas. Check the pod (`describe`,
logs), its Longhorn volume attachment and, for Vault, [VaultSealed](#vaultsealed).

### KubernetesPodCrashLooping

`kubectl -n <namespace> logs <pod> -c <container> --previous` shows why. Usual
causes: missing configuration or secrets
([ExternalSecretNotReady](#externalsecretnotready)), an unreachable dependency
([DatastoreDown](#datastoredown)) or a memory limit
([KubernetesContainerOOMKilled](#kubernetescontaineroomkilled)).

### KubernetesContainerOOMKilled

Compare usage with the limit (Grafana **Cost Showback → Rightsizing** or
`kubectl top pod`). Raise the limit in the owning manifest or fix the leak.

### KubernetesPersistentVolumeReleased

A claim was deleted but its `Retain` volume still holds data and disk space.
Confirm nobody needs the data (back it up if in doubt), then delete the
PersistentVolume and its Longhorn volume.

### KubernetesPersistentVolumeClaimPending

A claim has not bound for 15 minutes. `kubectl -n <namespace> describe pvc <claim>`
names the cause: unknown StorageClass, Longhorn unable to place replicas (disk
space, node tags; see the Longhorn UI) or a quota.

### KubernetesJobFailed

A Job failed and no later run of its CronJob succeeded;
`kubectl -n <namespace> logs job/<job>` shows why. Platform Jobs rerun on the next
Argo CD sync or schedule. Delete the failed Job once a run succeeds.

## Platform services

### PlatformTokenExpiring

A GitLab token the platform's jobs use (registry retention, Sonar discovery)
expires within 30 days, within 7 (critical), or GitLab no longer accepts it.
The daily `token-expiry` job raises it. Rerun `scripts/configure-gitlab-ci.sh`
on the control plane: it replaces a token that is stale and updates the Secret.

### KubernetesNodeNotReady

A node has not been Ready for five minutes; its single-replica volumes are
unavailable. Run `kubectl describe node <node>`, then on the host
`sudo journalctl -u k3s -u k3s-agent -n 100`. Check disk, memory and the private
transport (Tailscale or vRack). If the host is gone, follow
[node fencing](node-fencing.md) before recovering its storage.

### NodeClockNotSynchronized

The node's kernel reports its clock unsynchronized for 15 minutes. Embedded
etcd, Vault's Raft storage and Kafka misbehave on diverging clocks, and tokens
and one-time codes are refused. On the host, run `timedatectl status` and
`chronyc tracking` (or `systemctl status systemd-timesyncd`), restart the time
service, and check that the firewall lets it reach its time servers (UDP 123).

### NodeClockSkewed

The node's clock is more than 50 ms from its time source although it reports
itself synchronized: the source is far away, unreachable part of the time, or
the host's clock drifts fast. Check `chronyc sources -v` for the selected
source and its offset, and prefer the provider's or a nearby time server.

### ClusterDNSErrorsHigh

More than 5 % of CoreDNS answers are SERVFAIL for ten minutes: names inside the
cluster or outside it do not resolve, so pulls, sign-in and calls between
services fail. Read `kubectl -n kube-system logs deploy/coredns --tail=100`;
check the host's resolvers (`resolvectl status`), which CoreDNS forwards to, and
the `coredns-custom` ConfigMap for a broken private zone.

### KubernetesApiErrorsHigh

The API server answers more than one request in two seconds with a server error
for ten minutes. Controllers, deployments and `kubectl` then fail or stall. On
the control plane, read `sudo journalctl -u k3s -n 200`, check the datastore
(`sudo k3s etcd-snapshot ls` on embedded etcd, disk latency and free space on
SQLite) and the node's memory ([node memory](../operations.md#node-memory-and-eviction)).

### VaultSealed

ExternalSecrets cannot refresh and new pods needing secrets fail. Run
`sudo systemctl start swirl-cloud-vault-unseal.service` on a control plane (or
**Maintenance → Unseal Vault now** in `./install.sh`), then check
`kubectl -n infra logs vault-0 -c vault --tail=50`. Without unseal keys, use the
recovery kit ([security](../security.md)).

### VaultAuditDeviceFailing

Vault refuses requests when no audit device works. Check the audit volume's free
space and `kubectl -n infra logs vault-0 -c vault`, fix it, and confirm requests
succeed.

### LonghornVolumeDegraded

Let a running rebuild finish in the Longhorn UI; otherwise check node disk space
and status. On one node, volumes requesting more than one replica stay degraded:
match the replica count to the storage nodes.

### LonghornVolumeBackupStale

Off-node backups are active but this volume's last one is over 36 hours old.
Check the backup target and recurring job in the Longhorn UI (credentials, bucket
reachability, free space) and run a backup now.

### LonghornVolumeFaulted

No usable replica: the workload is down and data is at risk. Delete nothing.
Salvage a replica in the Longhorn UI, or restore the latest backup
([backups and recovery](../operations.md#backups-and-recovery)).

### KeycloakPasswordFailuresHigh

Over 20 failed password checks in 15 minutes in one realm. In Kibana, filter
`identity-audit-*` on `keycloak.type : LOGIN_ERROR` for usernames and sources;
block abusive sources at the edge and contact affected users. See
[identity events](../security.md#identity-events).

### ExternalSecretNotReady

An ExternalSecret could not read Vault for 15 minutes.
`kubectl -n <namespace> describe externalsecret <name>` gives the reason:
[VaultSealed](#vaultsealed), a missing path or field, or a denying policy. Seed
missing values with `scripts/configure-vault.sh`.

### ElasticsearchUnreachable

Logs and audits are not indexed (Fluent Bit buffers them on disk). Check
`kubectl -n infra get pods -l app=elasticsearch`, its logs and the data volume
([PersistentVolumeSpaceLow](#persistentvolumespacelow)).

### ElasticsearchClusterRed

A primary shard is unassigned. `GET _cluster/allocation/explain` (in the
Elasticsearch pod, as `elastic`) names the cause, usually disk watermarks. Free
space or delete expired indices, never audit indices within their retention.

### ElasticsearchClusterYellow

Replica shards are unassigned. On one node, set `number_of_replicas` to 0 on the
index and fix the template that created it.

### DatastoreDown

A PostgreSQL, MongoDB, Redis or Kafka exporter cannot reach its datastore. Check
the pod in `infra` (`kubectl -n infra get pods`, logs, volume) and its credentials
([ExternalSecretNotReady](#externalsecretnotready)).

### ArgoCDApplicationUnhealthy

Degraded, Missing or Unknown for 15 minutes. Find the unhealthy resource in Argo
CD and follow its runbook; roll back an application release through the
deployment flow.

### ArgoCDApplicationOutOfSync

Live state has differed from Git for 30 minutes: a failing sync or a manual
change. Fix the manifest from Argo CD's diff and sync error, or revert the manual
change (self-heal does on the next sync). See [Argo CD operations](../deployment.md).

### TLSCertificateExpiringSoon

A Traefik certificate expires within 14 days. Rerun
`scripts/configure-cloudflare.sh` (or the installer); it reissues the Cloudflare
Origin CA certificate when under 30 days remain. Replace other certificates in
their TLS Secret. See [public DNS and ingress](../dns.md).

### TLSCertificateExpiryCritical

Expires within three days; clients then reject the service. Renew now as for
[TLSCertificateExpiringSoon](#tlscertificateexpiringsoon).

## Hardware

These alerts come from bare-metal nodes; virtual servers export none. Hardware
faults need the hosting provider: open a ticket with the device, serial number
(`sudo smartctl -i <device>`) and the alert, and plan the node's maintenance
(drain it on a multi-node cluster) before the provider intervenes.

### HostDiskSmartFailing

The disk's own health check failed: it may stop at any time. Confirm a fresh
backup, read `sudo smartctl -a <device>`, and ask the provider to replace it. On
software RAID the array keeps running until the replacement
([HostRaidDegraded](#hostraiddegraded)).

### HostDiskSmartUnreadable

smartctl cannot read the disk's health, so a failing disk would go unnoticed. Run
`sudo smartctl -a <device>`; a controller hiding the disks needs its `-d` type,
and a disk that answers nothing is suspect itself.

### HostSmartMetricsStale

The hourly export stopped. Check `systemctl status swirl-cloud-smart-metrics.timer`
and `sudo journalctl -u swirl-cloud-smart-metrics.service -n 20`; rerun
`scripts/configure-node-security.sh --host-baseline` to reinstall it.

### HostNvmeWearHigh

The drive has used 90 % of its rated writes. Plan its replacement with the
provider before it reaches 100 %, after which the vendor no longer guarantees it.

### HostDiskReallocatedSectorsGrowing

The disk keeps remapping bad sectors, an early sign of failure. Check the trend
with `sudo smartctl -A <device>` and plan a replacement if it keeps growing.

### HostRaidDegraded

A software RAID array lost redundancy. Read `cat /proc/mdstat` and
`sudo mdadm --detail /dev/<array>`; after the provider replaces the disk, add the
new one to the array and wait for the resync before any other maintenance.

### HostMemoryUncorrectableErrors

Memory returned an error it could not correct; a process may have read corrupted
data. Read `sudo journalctl -k | grep -i -E 'edac|mce'`, ask the provider to test
or replace the module, and check recent database writes after the repair.

### HostMemoryCorrectableErrorsHigh

ECC corrects errors, but many in a day usually precede a module failure. Ask the
provider to test the memory during the next maintenance window.

### HostTemperatureNearCritical

A sensor is close to the temperature at which the hardware throttles or shuts
down. Check `sensors` (lm-sensors) and the load, and report cooling problems to
the provider.

### HostBmcSensorCritical

The server's management controller reports a sensor past its critical threshold:
a stopped fan, a failed power supply, a voltage out of range or an overheating
part. Read `sudo ipmitool sensor | grep -v '| ok'` and `sudo ipmitool sel elist`
(the event log), confirm a fresh backup and ask the provider to intervene.

### HostBmcSensorWarning

A sensor has been past its non-critical threshold for two hours. Check it with
`sudo ipmitool sensor` and report a trend (rising temperature, slowing fan) to the
provider before it turns critical.

### HostBmcUnreadable

ipmitool no longer reads the BMC, so its sensors go unwatched. Run
`sudo ipmitool mc info`; if it hangs, ask the provider to reset the BMC (it does
not affect the running server), then check
`sudo journalctl -u swirl-cloud-smart-metrics.service -n 20`.

## Host security and maintenance

### SocForwardingFailing

Fluent Bit cannot deliver to the SOC receiver ([SOC forwarding](soc-forwarding.md)).
Read `kubectl -n host-agents logs daemonset/fluent-bit | grep -i syslog`: a TLS
error points to the receiver's certificate or the recorded CA
(`scripts/configure-soc-forwarding.py --show`), a timeout to the network path or
the receiver's allowlist of node addresses. Events wait in the disk buffer and
are sent once the receiver answers.

### SocForwardingDroppedEvents

Events were dropped after their retries ran out or the 512 MB buffer filled, so
the SOC misses them. Fix the delivery first (above), then tell the SOC which time
range to fill from Elasticsearch (Kibana, the same indices).

### LynisAuditFailed

Read `sudo journalctl -u swirl-cloud-lynis.service -n 50` and rerun with
`sudo systemctl start swirl-cloud-lynis.service`.

### LynisAuditMissing

No successful monthly audit for 35 days. Check
`systemctl list-timers swirl-cloud-lynis.timer`, reinstall it with
`scripts/configure-lynis-schedule.sh` and run an audit.

### TrivyNewCriticalVulnerability

An image's critical vulnerabilities rose above its recent maximum, or a new image
has some. Check Grafana **Trivy Security Reports**, update when a fix exists and
record the decision in [vulnerability triage](../security.md#vulnerability-triage).
An accepted finding goes in `k8s/files/vulnerability-exceptions.yaml`, which keeps
it out of this alert until it expires; the alert fires again when an exception
expires on a finding still present. Application images are fixed in their own
repository.

### HostPackageScanFailing

The daily scan of a server's packages failed or has not finished for two days,
so new vulnerabilities in its packages, kernel or K3s go unseen; its last
findings stay tracked for three days. Read the scan's log on that server:
`kubectl -n host-agents logs -l app=host-package-scan --field-selector spec.nodeName=<node>`
(pick the pod on `node` with `kubectl -n host-agents get pods -o wide`). The
usual causes are the Trivy server (`kubectl -n infra get pods -l
app.kubernetes.io/name=trivy-server`) or a pod that cannot start on that server.
Deleting the pod scans again at once.

### HostFirewallDisabled

UFW is inactive or INPUT is not default-deny, so listening ports may be public.
Check `sudo ufw status verbose`, rerun `scripts/configure-node-security.sh --apply`
(it re-enables and verifies UFW) and find out why it was disabled.

### HostIntrusionPreventionDown

Run `sudo systemctl status fail2ban crowdsec crowdsec-firewall-bouncer`, restart
the stopped service and read its journal.

### HostIntrusionMetricsStale

Metrics are over 30 minutes old, so the two alerts above cannot fire. Check and
restart `swirl-cloud-intrusion-metrics.timer`.

### HostBruteForceWave

Confirm bans work (`sudo fail2ban-client status sshd`, `sudo cscli decisions list`)
and consider restricting SSH to the private interface with keys only.

### NodeRebootRequired

Updates installed over a week ago need a reboot. In a maintenance window, confirm
a fresh backup and Vault unseal access, drain the node on multi-node clusters,
reboot, then run `./install.sh --status`.

### HostMaintenanceServiceFailed

One of `swirl-cloud-k3s-backup.service`, `swirl-cloud-k3s-backup-verify.service`,
`swirl-cloud-vault-unseal.service` or `dailyaidecheck.service` (AIDE) failed. Read
`sudo journalctl -u <unit> -n 50` and rerun with `sudo systemctl start <unit>`. A
backup waits up to four hours for a verification holding the backup lock; rerun it
after that ends.

A failed verification means the weekly read-back of the off-node restic copy
failed, so off-node recovery is unproven. The journal names the cause:
`restic check` errors, a newest archive failing its SHA-256 or not a complete
`tar.gz`, an empty repository, or a backup holding the lock for over an hour.

1. Keep the local archives and confirm a fresh backup uploads:
   `sudo systemctl start swirl-cloud-k3s-backup.service`.
2. Rerun the verification; within one UTC week (Monday to Sunday) it checks the
   same data subset.
   `sudo env VERIFY_DATA_SUBSETS=1 /usr/local/sbin/swirl-cloud-k3s-backup-verify`
   checks all data.
3. If restic reports a lock while neither unit is active (`systemctl is-active`),
   clear the stale lock with
   `sudo bash -c 'set -a; source /etc/swirl-cloud/backup.env; restic unlock'` and
   rerun.

Otherwise see restic's
[troubleshooting guide](https://restic.readthedocs.io/en/stable/077_troubleshooting.html)
and [backups and recovery](../operations.md#backups-and-recovery).

### K3sRecoveryBackupMissing

No successful recovery archive for 36 hours. Read
`sudo journalctl -u swirl-cloud-k3s-backup.service -n 50`, fix the cause (space,
credentials, S3 target), then run **Maintenance → Run the recovery backup now** in
`./install.sh`.

### K3sBackupVerificationMissing

The weekly read-back of the off-node restic repository has not succeeded for
eight days, so a damaged or unreadable repository would go unnoticed. Check
`systemctl list-timers swirl-cloud-k3s-backup-verify.timer` and
`sudo journalctl -u swirl-cloud-k3s-backup-verify.service -n 50`, then run it now:
`sudo systemctl start swirl-cloud-k3s-backup-verify.service`. Reinstall the timer
with `./scripts/configure-k3s-backups.sh` if it is missing. After the off-node
target was removed on purpose, delete
`/var/lib/node_exporter/textfile_collector/swirl_cloud_unit_swirl_cloud_k3s_backup_verify_service.prom`
([backups and recovery](../operations.md#backups-and-recovery)).

### VaultUnsealTimerStopped

The unseal service has not run for 15 minutes, so Vault would stay sealed after a
restart. Restart `swirl-cloud-vault-unseal.timer`; see [VaultSealed](#vaultsealed).

## Security detection

The `security-detection` job
([`security-detection.yaml`](../../k8s/platform/security-detection.yaml)) posts
these to Alertmanager every five minutes; each stays active 30 minutes after its
evidence was last seen. See [security detection](security-detection.md).

### IdentityBruteForceThenSuccess

At least five failed logins in 15 minutes, then a sign-in. Contact the owner
through another channel; filter `identity-audit-*` on `keycloak.username` in
Kibana for the logins and sources. If it was not the owner, disable the user, end
their sessions (**Users → Sessions → Sign out**), reset the password and review
the session.

### KubernetesRBACChangedOutsideGitOps

Someone other than Argo CD or K3s changed a Role, ClusterRole or binding. Filter
`kubernetes-audit-*` on `user.username` and `objectRef.resource : *bindings`.
Back-port an intended change to Git; otherwise revert it and rotate the identity's
credentials. Installer reruns by `system:admin` also fire this alert.

### KubernetesSecretReadByNewIdentity

An identity read Secrets it had not read in the previous week. Check its reads in
`kubernetes-audit-*` (`objectRef.name`, `sourceIPs`) against recent deployments or
onboarding. If unexpected, revoke the identity (ServiceAccount token or binding)
and rotate the Secrets it read.

### EmergencyAccountUsed

Someone signed in with Keycloak's bootstrap administrator through a browser or
another client than `admin-cli`. It bypasses single sign-on and just-in-time
grants. Find who used it and why (Keycloak's master realm events show the
address); if nobody can explain it, treat it as an incident, rotate the password
(`scripts/rotate-local-admin-passwords.sh`) and review the admin events since.

### NewPlatformAdministrator

A user joined `platform-admins` in the platform realm or got `admin` in the master
realm; the alert lasts a day. If the grant was not approved, remove it, end the
user's sessions and check Keycloak admin events for who granted it.

### TimedAccessEndingSoon

A time-limited role or guest account ends within a week (labels `user`,
`access`, `ends`, and `sponsor`: the guest's sponsor or the role's approver).
Nothing needs doing if it should end. To keep it, the sponsor or approver asks
for a new grant after the old one ends, or revokes it and grants again now
(`./install.sh --admin-access`, [access with an end date](../security.md#access-with-an-end-date)).
Each grant warns once.

### HostSSHBruteForceThenSuccess

An address failed SSH logins at least five times, then signed in. Contact the
account's owner through another channel. On the host, read
`sudo journalctl -t sshd-session -t sshd --since -1h` and `last -n 20`. If it was
not the owner, remove the key or lock the account, kill its sessions
(`sudo pkill -KILL -u <user>`), block the address and review what it ran
(`sudo journalctl _UID=<uid>`). Key-only SSH (TODO §4) prevents the password case.

### HostSudoByNewUser

A user ran sudo for the first time in the recorded history; the alert lasts a day.
Confirm the account and its sudo rights (`sudo -l -U <user>`, `/etc/sudoers.d`).
If unexpected, remove the rights, lock the account and review its commands with
`sudo journalctl -t sudo --since -1d`.

### MalwareDetected

ClamAV answered `FOUND` for files an application sent, so it refused them
(labels `signature` and `sender`: the sending pod, or its address when no pod
held it in the last hour). Critical for a malware signature; a warning for a
refusal by policy (`Heuristics.*`, `PUA.*`: macros, password-protected files,
scan limits, see [what ClamAV refuses](application-onboarding.md#malware-scanning)).

1. Open Grafana's **ClamAV Malware Scanning** dashboard: **Refused files** lists
   each one with its time and sender.
2. Find who uploaded it in the application's log at that time (Indezy logs
   `Member <id> uploaded a document holding <name>: refused`).
3. For malware: treat the account as possibly compromised. Check what else it
   uploaded or sent before, contact its owner through another channel, and
   suspend it if nobody can explain the file.
4. For a policy refusal, nothing needs doing unless the same person keeps
   trying; the application told them why.
5. If a harmless file was refused by a signature, report the false positive with
   its name at <https://www.clamav.net/reports/fp>.

## Malware scanning

ClamAV ([`clamav.yaml`](../../k8s/platform/clamav.yaml)): two replicas in `infra`,
each with its signatures on its own volume. Applications refuse every file they
would scan while no replica can answer. `kubectl -n infra get pods -l app=clamav`
shows the replicas; each has the containers `clamd`, `freshclam` and `exporter`.

### ClamAVUnavailable

No replica has been ready for five minutes, so applications refuse every file.
A replica is ready only once clamd answers with a complete, loaded signature set.

- `Init:` status: the `signatures` init container has no complete set. It
  copies the set the image carries onto a new volume, so read
  `kubectl -n infra logs clamav-0 -c signatures` for a copy error (a full
  volume) or an image without databases (a `_base` tag).
- `Running` but not ready: `kubectl -n infra logs clamav-0 -c clamd`. An
  `OOMKilled` restart means the signatures and scans need more memory than the
  container limit.
- `Pending`: the volume did not attach (`kubectl -n infra describe pod
  clamav-0`, Longhorn) or the node lacks the memory requested.

### ClamAVTestScanFailing

A replica's exporter has not seen clamd detect the EICAR test file for ten
minutes, so its answers cannot be trusted. Read
`kubectl -n infra logs clamav-0 -c exporter` (the answer clamd gave) and the
`clamd` logs. If clamd answers but finds nothing, delete the pod so it starts
again from its volume's signatures; if the exporter logs nothing at all, it is
stuck and its liveness probe restarts it.

### ClamAVSignaturesOutdated

A replica scans with daily signatures published more than two days ago, so
recent malware passes. Read `kubectl -n infra logs clamav-0 -c freshclam`:

- `429` or `403` and a cool-down date: the mirror refuses the node's address,
  usually after repeated whole downloads. freshclam waits out the cool-down by
  itself; restarting the pod does not help and can extend the block. A newer
  image (its tag in [`clamav.yaml`](../../k8s/platform/clamav.yaml)) carries a
  newer set, which a restarted replica takes at start.
- DNS or connection errors: the node's outbound access.
- No space: `kubectl -n infra exec clamav-0 -c freshclam -- df -h /var/lib/clamav`.

The other replica's age on the dashboard tells whether the mirror or this pod is
at fault.

### ClamAVSignaturesStale

The same, past four days. Fix the update as for
[ClamAVSignaturesOutdated](#clamavsignaturesoutdated); if the mirror stays
unreachable, decide with the applications' owners whether to keep accepting
files.

### ClamAVScanQueueBacklog

Files have waited for a free clamd thread for fifteen minutes, so uploads slow
down and may time out. Check the **Threads and queue** and **Scans by result**
panels for a burst from one sender, and the replicas' CPU. Raise `MaxThreads`
in [`clamd.conf`](../../k8s/files/clamav/clamd.conf) (with the container's
memory) or the replicas if the load is lasting.

## Ingress and monitoring

### IngressServiceErrorRateHigh

Over 5 % of a service's requests returned 5xx for 15 minutes. Check Grafana
**Ingress Traffic**, then its pods and logs; roll back a recent release.

### RegistryApiUnavailable

The registry API stopped answering as a registry for ten minutes: an anonymous
`GET /v2/` must return `401` with `Docker-Distribution-Api-Version: registry/2.0`.
The `instance` label says which path failed. Through
`https://registry.<domain>/v2/` alone, the edge is answering instead: compare
`curl -sI https://registry.<domain>/v2/` with the Cloudflare bot exceptions in
[Cloudflare configuration](../dns.md#cloudflare) and
[Registry push or pull fails](../operations.md#registry-push-or-pull-fails-from-outside-the-cluster).
Inside the cluster as well, GitLab's registry is down: check the `gitlab` pod and
`kubectl -n infra exec deploy/gitlab -- gitlab-ctl status registry`. The probe
proves the API answers; it does not push or pull an image.

### SignInProbeFailing

The probe account could not sign in twice in a row on the tool in `target`, so
people signing in there probably fail too. Its logs name the step:
`kubectl -n infra logs deploy/signin-probe --since=30m`.

- Any step with `HTTP 403` and a challenge page: the edge is challenging the
  probe as a bot. It goes out through the public addresses with the user agent
  `swirl-cloud-signin-probe`; allow it as for the registry probe in
  [Cloudflare configuration](../dns.md#cloudflare).
- `start` or `authorization`: the tool did not send the probe to Keycloak. For
  `oauth2-proxy`, check the `oauth2-proxy` pods; for `vault`, that Vault is
  unsealed and its OIDC sign-in is configured (`scripts/configure-vault.sh`).
- `login page`: Keycloak's login page does not load or changed; check the
  `keycloak` pod and the sign-in theme.
- `sign-in`: the password was refused, the account is locked, or Keycloak asks
  for an action (terms, profile). Sync `swirl-cloud` in Argo CD: the
  `signin-probe-account` job resets the account. If it keeps failing, look at
  the account's required actions in the Keycloak console.
- `callback`: the tool could not exchange the code, usually a client secret
  that differs between Keycloak and the tool. Rerun the Keycloak reconcile
  (sync `swirl-cloud`) and compare the client's secret with the tool's.

### SignInProbeStale

The sign-in probe has reported nothing for 15 minutes, so a broken sign-in
would go unseen. Check the `signin-probe` pod in `infra` (`kubectl -n infra
describe deploy/signin-probe`) and that its `signin-probe-credentials` Secret
exists: External Secrets generates it on the first sync.

### SignInProbeUnexpectedAccess

A tool gave the sign-in probe account, which belongs to no group, a session with
access (today only Vault can tell: it issued a token). The probe revoked the
token. Check the tool's role bindings, here Vault's OIDC role `platform-admin`
and its `bound_claims`, before anyone else signs in with the same gap.

### PrometheusTargetDown

Its alerts cannot fire. Check the pod or exporter
(`kubectl get pods -A -o wide | grep <instance>`) and the network policies between
`infra` and its namespace.

### AlertNotificationsFailing

All notifications through `email` or `webhook` failed in the last hour; read
`kubectl -n infra logs deploy/alertmanager | grep -i notify`. For `email` (revoked
key, changed password, sending limit), rerun `scripts/configure-platform-mail.py`;
it checks the login and sender before storing anything
([platform mail](platform-mail.md#troubleshooting)). For `webhook`, check GitLab
is up and its alert integration token matches Vault `infra/monitoring`.

## Applications

### IndezyOfferSiteFailing

Three consecutive failures reading one job board (`site` label); each failure
doubles the retry wait, up to a day. The **Job boards** page and
`kubectl -n apps logs deploy/indezy-server | grep "Collecting <SITE> failed"` show
the error. A 403 or 429 means the site refuses the crawler; a parse error means
the reader in `indezy-server/.../scraping/reader` needs updating (see the Indezy
TODO's reader canaries). **Read now** retries after the fix.

### IndezyOfferSiteFindsNothing

A job board answered for 24 hours without any offer, usually a redesign. Compare
it with the reader's sample in `indezy-server/src/test/resources/offers/` and
update the reader. A board that stopped publishing goes in the Indezy TODO's
left-out sites.

### IndezyGeocodingTimeouts

Over 20 `geo.api.gouv.fr` lookups failed in an hour; offers lack coordinates
until the next lookup (every 6 hours). Test with
`kubectl -n apps exec deploy/indezy-server -- wget -qO- 'https://geo.api.gouv.fr/communes?nom=Paris'`.
A short outage of this public service needs no action.

### IndezyApiErrors

Over 5 % of the Indezy API's requests (at least 20 in 10 minutes, the actuator's
own left out) answered with a 5xx for 10 minutes. The **Server Errors by Route**
panel of the **Indezy Overview** dashboard names the failing routes, and Kibana's
**Indezy — warnings and errors** search their log lines, each with the
`request_id` the response's `X-Request-Id` header carries. The cause is usually
the database ([DatastoreDown](#datastoredown)) or a release to roll back.

### IndezyApiSlow

The 95th percentile of the Indezy API's requests (attachments and the actuator
left out) stayed over a second for 15 minutes. The **Slowest Routes** panel of
the **Indezy Overview** dashboard names them; the offer search under concurrent
use is the known one (its target is 300 ms, Indezy's TODO). Check the pod's CPU
throttling and the database's load before scaling the Deployment.

## Service levels

### CIJobsQueueingLong

Over six hours, one CI job in ten waited over ten minutes for a runner. Check
`gitlab_runner_concurrent` and the queue on Grafana **Service Levels** (the shared
runner runs one job at a time by default). Raise `concurrent` and per-job limits
within **Capacity Planning** (TODO §26), or add a runner on another server.

### SLOAvailabilityBudgetBurnFast

Over 7.2 % of probes failed in the last hour and still fail; the month's 0.5 %
budget lasts about two days at this rate. `curl -sSI https://<host>/` shows the
status or TLS error. A 5xx points at the service
([KubernetesDeploymentUnavailable](#kubernetesdeploymentunavailable)), a
Cloudflare 52x at Traefik or the origin, a TLS error at the certificate
([TLSCertificateExpiryCritical](#tlscertificateexpirycritical)). Roll back a
causing release, then record the incident.

### SLOAvailabilityBudgetBurnSlow

Over 3 % of probes failed in six hours and still fail; the budget lasts about
five days. Fix intermittent failures (restarts, OOM kills, a flapping dependency)
seen on **Service Levels**.

## Compliance

### ComplianceControlRegressed

A control is failing or lost evidence compared with its six-hour median.
`./install.sh --compliance` shows the changed check; follow its runbook (for
example [HostFirewallDisabled](#hostfirewalldisabled) or
[K3sRecoveryBackupMissing](#k3srecoverybackupmissing)). See
[compliance evidence](compliance-evidence.md).

## Related Guides

- [Operations](../operations.md)
- [Security](../security.md)
- [Compliance evidence](compliance-evidence.md)
