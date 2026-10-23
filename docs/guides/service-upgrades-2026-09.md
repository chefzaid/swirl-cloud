# September 2026 service upgrades

Maintenance of the shared platform and centrally managed Odoo on 2026-09-28.
Application runtime images stay with their repositories. Image digests live in the
owning manifests and Helm values, with readable tags alongside.

## Selected releases

| Component | Previous release | Selected release |
| --- | --- | --- |
| K3s | 1.36.4+k3s1 | Retained; see compatibility exception below |
| Longhorn | 1.12.1 | Already current, including its managed CSI components |
| Traefik | Chart 41.5.0 / 3.7.13 | Chart 41.6.0 / 3.7.13 |
| Vault | Chart 0.34.1 / 2.1.0 | Chart 0.34.1 / 2.1.1 |
| External Secrets | 2.9.0 | 2.11.0 |
| Argo CD | Chart 10.3.3 / 3.5.2 | Chart 10.9.2 / 3.5.3 |
| CloudNativePG, optional HA profile | Chart 0.29.0 / 1.30.0 | Chart 0.29.1 / 1.30.1 |
| GitLab CE, Runner, Runner helper | 19.3.1 | 19.4.1 |
| Keycloak | 26.7.3 | 26.7.4 |
| OAuth2 Proxy | 7.15.4 | Already current |
| Grafana | 13.2.1 | 13.2.2, distroless |
| Prometheus / Alertmanager | 3.14.0 / 0.33.1 | 3.15.0 / 0.34.1 |
| Node Exporter / kube-state-metrics | 1.12.1 / 2.20.0 | Already current; readable tags added |
| Elasticsearch, Kibana, Logstash, Filebeat | 9.4.6 | 9.5.4 |
| Fluent Bit | Digest-only pin | 5.1.2 |
| Trivy Operator / Trivy | 0.34.0 / 0.74.0 | Already current, chart 0.36.0 |
| Trivy policy bundle | Major 1 bundle | 2.2.0 |
| SonarQube Community | 26.8.0.126808 | 26.9.0.129388 |
| PostgreSQL | 18.6 Bookworm | Refreshed 18.6 Bookworm image digest |
| MongoDB | 7.0.40 Jammy | 7.0.43 Jammy |
| Redis / Redis Exporter | 8.10.0 / 1.92.0 | 8.10.2 / 1.92.1 |
| Confluent Kafka | 8.3.1 | 8.3.2 |
| Kafbat UI | 1.5.0 | Already current; readable tag added |
| Homepage | 2.2.0 | 2.4.0 |
| Portainer CE | 2.39.6 | 2.45.1 |
| DBGate | Digest-only pin | 7.3.1 |
| Descheduler | Digest-only pin | 0.36.0 |
| Odoo | 19.0-20260810 | 19.0-20260908 |
| HA tunnel connector | cloudflared 2026.8.3 | 2026.9.3 |
| Helpers and platform CI | Mixed pins | NGINX 1.30.5, Node 24.21.0, Python 3.14.7, Ruby 3.4.11, curl 8.22.0, BusyBox 1.38.0, Alpine 3.24.2 |

Unchanged: the Redis HA dependency (4.39.0), Grafana's discovery sidecar (2.11.2)
and the MongoDB, PostgreSQL and Kafka exporters. CloudNativePG and cloudflared
changed only in configuration on this single-node, non-HA installation. Longhorn
moved to 1.13.0 in [October](service-upgrades-2026-10.md).

## Compatibility and recovery decisions

- K3s 1.37.0 exists, but Longhorn 1.12.1's
  [tested Kubernetes versions](https://longhorn.io/docs/1.12.1/best-practices/)
  stop at 1.36, so K3s stays on stable 1.36.4 until storage supports 1.37. Never
  substitute a release candidate for a stable patch.
- PostgreSQL stays on Bookworm to keep libc collation version 2.36. MongoDB stays
  on the 7.0 patch line with feature compatibility version 7.0.
- GitLab 19.3.1 to 19.4.1 crosses no
  [required upgrade stop](https://docs.gitlab.com/update/upgrade_paths/). Batched
  background migrations were checked first; Runner and helper images moved with it.
- Trivy 0.74.0 expects policy bundle major 2
  ([upstream](https://github.com/aquasecurity/trivy/blob/v0.74.0/pkg/policy/policy.go));
  the major 1 pin was stale.
- Elasticsearch is backed up with a native snapshot (including feature states),
  never a filesystem copy of live data. Its manifest exposes a snapshot repository
  path on the existing volume; copy snapshots off that volume for a real backup.
- Redis is a single cache on ephemeral storage. The Recreate strategy avoids two
  independent datasets during upgrades; maintenance saves and restores keys with
  their remaining expiry. Redis is not durable; the HA profile's persistence is
  unchanged.

Backups for this maintenance were verified **local** archives, by operator choice:
`/var/backups/swirl-cloud/upgrades/20260928` (deleted after verification to free
disk) and the fresh K3s/Vault/database archive in `/var/backups/swirl-cloud/k3s`
(kept). Archives, secrets, full scan results and experiment files stay outside Git.
Pre-upgrade images were pinned in containerd during the rollouts and unpinned once
each service was verified; rollback pulls the recorded previous digest. None of
this survives loss of the host; off-host backup and an empty-host rebuild drill are
open roadmap items.

Rolling back past a schema or data-format migration needs the service's matching
data archive with its previous image. An image tag change alone is not enough for
GitLab, SonarQube, Portainer or Elasticsearch.

## Execution record

Automated sync of the `swirl-cloud` Argo CD Application was paused throughout.

1. Recovery: a K3s/Vault/database archive, a GitLab native backup with
   configuration and registry metadata, an Elasticsearch snapshot, and volume
   archives of Grafana, Portainer, DBGate, Prometheus, Odoo, SonarQube extensions
   and Kafka, each taken with the service stopped or quiesced. Checksums verified.
2. Rehearsal: each stateful service's backup was restored into its candidate image
   on an isolated network, and all data matched, including PostgreSQL ownership and
   collations, SonarQube's schema migration, Redis keys and expiry, Kafka offsets,
   and GitLab (restored on 19.3.1, then started on 19.4.1).
3. Rollout order, each ready before the next: External Secrets, Traefik, Argo CD,
   PostgreSQL, MongoDB, Vault, Keycloak, Redis, SonarQube, Portainer, Grafana,
   monitoring and portal components, DBGate, Prometheus, the Elastic stack, Fluent
   Bit and helper CronJobs, Odoo, GitLab, the project delivery runner, then the
   Kafka controller and broker.
4. All 38 candidate images were scanned first without failures. Secret findings
   were bundled snakeoil and SSH host keys. Remaining vulnerabilities are triaged in
   the TODO's vulnerability section.

The GitLab rollout filled the node disk: the native backup, its host copy, pinned
recovery images and the new 1.5 GB GitLab image crossed the kubelet's
ephemeral-storage eviction threshold. Evicted pods had to re-pull collected images,
including application images from the restarting GitLab registry. Removing the
superseded GitLab archive and the verified services' recovery images recovered all
workloads. Before future maintenance, confirm at least 30 GB free and keep recovery
archives off the node's root filesystem.
