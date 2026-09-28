# September 2026 service upgrades

This maintenance updates the shared platform and centrally managed Odoo. Application
runtime images remain owned by their application repositories. Release discovery
was performed on 2026-09-28; immutable image digests are recorded in the owning
manifests and Helm values, with readable version tags alongside them.

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

The Redis HA dependency remains 4.39.0, Grafana's discovery sidecar remains 2.11.2,
and the MongoDB, PostgreSQL and Kafka exporters remain on their current releases.
CloudNativePG and cloudflared are configuration updates only on this single-node,
non-HA installation; enabling HA is separate work.

## Compatibility and recovery decisions

- K3s 1.37.0 is available, but Longhorn 1.12.1's
  [tested Kubernetes versions](https://longhorn.io/docs/1.12.1/best-practices/)
  stop at 1.36. Keep the current stable 1.36.4 build until the storage compatibility
  gate is satisfied. Do not substitute a release candidate for a stable patch.
- PostgreSQL stays on Bookworm to preserve libc collation version 2.36. MongoDB
  stays on the supported 7.0 patch line with feature compatibility version 7.0.
- GitLab 19.3.1 to 19.4.1 does not cross a
  [required upgrade stop](https://docs.gitlab.com/update/upgrade_paths/).
  Active batched background migrations were checked before maintenance. Runner
  and helper images move to the matching minor together.
- Trivy 0.74.0 expects policy bundle major 2 in its
  [upstream implementation](https://github.com/aquasecurity/trivy/blob/v0.74.0/pkg/policy/policy.go).
  The previous major 1 pin was stale even though the scanner itself was current.
- Elasticsearch uses a native snapshot, including feature states, rather than a
  filesystem copy of its live data directory. Its manifest now exposes a snapshot
  repository path on the existing volume. Copy snapshots out of that volume;
  keeping them there alone does not provide an independent backup.
- Redis uses a single cache with ephemeral storage. Recreate strategy avoids
  routing to two independent datasets during upgrades. Maintenance captures and
  restores existing keys with their remaining expiry; the strategy itself does
  not make Redis durable. The HA profile's persistence is unchanged.

The operator explicitly selected verified **local** backups for this maintenance.
The archive root is `/var/backups/swirl-cloud/upgrades/20260928`, alongside the
fresh K3s/Vault/database archive under `/var/backups/swirl-cloud/k3s`.
Archives, secrets, full scan results and experiment files must remain outside Git.
Pre-upgrade images were pinned in the node's containerd against garbage collection
during the rollouts and unpinned once each service was verified; rollback pulls the
recorded previous digest. None of these measures survives loss of this physical
host; off-host backup and an empty-host rebuild drill remain open roadmap items.

A rollback after an application schema or data-format migration requires restoring
that service's matching database/data archive with its previous image. Changing
only the image tag is insufficient for GitLab, SonarQube, Portainer or Elasticsearch.

## Execution record

Performed on 2026-09-28 against the single-node installation, with automated sync
of the `swirl-cloud` Argo CD Application paused during the rollouts.

1. Recovery: a fresh K3s/Vault/database archive, GitLab native backup with
   configuration and registry metadata, an Elasticsearch snapshot, and consistent
   volume archives of Grafana, Portainer, DBGate, Prometheus, Odoo, SonarQube
   extensions and Kafka were taken while each service was stopped or quiesced.
   Checksums were verified.
2. Rehearsal: each stateful service's backup was restored into its candidate
   image in an isolated network. PostgreSQL ownership and collation versions,
   MongoDB, Vault, Keycloak, SonarQube (schema migration), Elasticsearch,
   Grafana, Portainer, Prometheus, DBGate, Redis keys and expiry, Odoo SSO and
   dark-mode add-ons, Kafka topics and consumer offsets, and GitLab projects and
   users (restored on 19.3.1, then started on 19.4.1) all matched.
3. Rollout order: External Secrets, Traefik, Argo CD, PostgreSQL, MongoDB, Vault,
   Keycloak, Redis, SonarQube, Portainer, Grafana, the monitoring and portal
   components, DBGate, Prometheus, the Elastic stack, Fluent Bit and helper
   CronJobs, Odoo, GitLab, the project delivery runner, then the Kafka controller
   and broker. Each rollout was checked for readiness before the next started.
4. Candidate images were scanned before rollout (38 images, no scan failures).
   Secret findings were image-bundled snakeoil and SSH host keys. Remaining
   vulnerabilities in the selected current releases are triaged in the TODO's
   vulnerability section; an upgrade does not by itself close those items.

The GitLab rollout briefly exhausted node disk: the native backup, its host copy,
the pinned recovery images and the new 1.5 GB GitLab image crossed the kubelet's
ephemeral-storage eviction threshold. Pods were evicted and garbage-collected images
had to be pulled again, including application images from the GitLab registry
while GitLab restarted. The superseded first GitLab archive and the verified
services' recovery images were removed, after which all workloads recovered.
Before future maintenance, confirm at least 30 GB free and keep recovery archives
off the node's root filesystem.
