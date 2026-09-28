# swirl-cloud — reverse-engineered feature map and TODO

This file reconstructs, step by step, how the bare-metal K3s platform was built
and what it contains today. Every item was derived from the repository source
(`main` at `3926910`, 105 commits from 2026-03-07 to 2026-09-26) and checked
against a read-only inspection of the live control plane on 2026-09-27.

**Legend**

- `[x]` Implemented in source; validated by `scripts/validate-repository.sh`
  (passes: nine render profiles plus nine safety suites) and, where marked
  *(live)*, observed running on the production host.
- `[ ]` Not done, broken, partial, only documented, or still needs a real-world
  exercise. Each open item says why.

**Live snapshot (2026-09-27):** one node (`swirlit-node-01`, 8 vCPU, 31 GiB
RAM, 145 GiB disk at 66 % used), K3s `v1.36.4+k3s1`, SQLite datastore, Tailscale
private address, internet exposure, `appsEnabled=true`, `applicationData.enabled=true`,
`highAvailabilityEnabled=false`, `nodeFencing.enabled=false`. Root Argo CD
Application `swirl-cloud` is **Synced at `3926910` but Degraded** (see §13).

---

## Part I — Build timeline (how it got here)

| Phase | Dates | What was built | Key commits |
|---|---|---|---|
| 0. Genesis | 2026-03-07 → 03-15 | Single dedicated server, K3s, first manifests, MongoDB, security baseline, Descheduler, DBGate, Vault unseal fix | `efb0452` … `e540da8` |
| 1. Host hardening | 2026-05-20 → 06-10 | `todo.txt` security wish-list (Fail2ban, CrowdSec, Lynis, auto-updates, auditd, AV, EDR/IDS/WAF); internet-exposed node controls | `3952910`, `9630f0f`, `3b917e9` |
| 2. Corporate ERP | 2026-08-08 | Odoo added | `57b41c7` |
| 3. Automation | 2026-08-13 → 08-23 | Full platform automation, upgrades, private networking (vRack/Tailscale), service discovery | `0c259a9`, `d721d2b`, `9f5fc21` |
| 4. Layout & GitLab | 2026-08-28 → 08-31 | Namespace standardization (`infra`/`apps`/`corp`), delivery migrated to GitLab, SSO centralization, portable bootstrap, Trivy, runner fixes | `aab48f8` … `b22ce24` |
| 5. Public web & quorum | 2026-09-06 | Apex DNS reserved for an application-owned public site, odd-quorum multi-control-plane support | `56aa1c0`, `7e3d48d` |
| 6. Security remediation | 2026-09-07 → 09-10 | Patched/rebuilt images, Odoo → `corp`, Sonar discovery, dark themes, registry persistence, repo replication, role-aware enrollment, later replaced by official upstream images | `c3daa69` … `a76882a` |
| 7. Retention & docs | 2026-09-10 | Telemetry retention cuts, guide refactor, Mermaid diagrams | `54806ec` … `4334580` |
| 8. HA preparation & onboarding | 2026-09-10 | End-to-end opt-in HA profiles, repeatable repository onboarding | `25ad27b`, `352f61e` |
| 9. Identity parameterization | 2026-09-13 | Intranet host, apps-namespace discovery for Grafana/Kibana/Sonar, organization identity inputs | `e6a63c1` … `379131c` |
| 10. Chart 2.0 | 2026-09-14 | ingress-nginx → Traefik, private images → pinned upstream digests, migration guards | `ef35d1c` … `a20c85c` |
| 11. Recovery & edge polish | 2026-09-20 → 09-23 | Registry backup coverage docs, GitLab visitor IP, Cloudflare permissions, branding, guide consolidation | `811ba6e` … `2484453` |
| 12. Environments | 2026-09-24 → 09-26 | `int`/`uat`/`prod` local or remote targets, scoped runners, admission for Traefik middleware and Argo projects | `0e52e16` … `3926910` |

---

## Part II — Feature checklist by layer

### 1. Repository, tooling and contracts

- [x] Single GPL-3.0 repository; `.editorconfig`, `.gitattributes`, `.shellcheckrc`, `.gitignore` excluding credentials, `.pem` (except the public origin CA), kubeconfigs, caches and `__pycache__`.
- [x] Shared deployment contract in `config/platform.env` (`PLATFORM_CONTRACT_VERSION=7`), sourced by Bash and read by Ansible as properties: pinned versions (K3s `v1.36.4+k3s1`, Traefik chart 41.5.0, Longhorn 1.12.1, Vault chart 0.34.1, ESO 2.9.0, Argo CD chart 10.3.3 / image v3.5.2), timeouts, ordered manifest inventories, ExternalSecret wait list, datastore/platform/DaemonSet wait lists, Cloudflare host inventories.
- [x] Helm chart `k8s/` (`swirl-cloud` 2.0.0) with vendored dependencies `redis-ha` 4.39.0 (HA only) and `trivy-operator` 0.36.0 for offline rendering.
- [x] One renderer for installer and GitOps: `scripts/render-cluster-config.sh`, `render-identity.py`, `render-platform-ha.py`; renders into a fresh private temp dir and refuses reused output.
- [x] Placeholder substitution (`__PUBLIC_DOMAIN__`, `__INTERNAL_DNS_ZONE__`, `__KEYCLOAK_REALM__`, `__TLS_SECRET_NAME__`, `__GITLAB_GROUP_PATH__` …) so the checkout stays organization-neutral.
- [x] Offline validator `scripts/validate-repository.sh`: ShellCheck, Python/Ansible syntax, doc-link checks, manifest inventories, workload policy, image-digest pinning, nine installer/GitOps render combinations (HA, Odoo scope, PostgreSQL staging/cutover, Kafka phases, fencing, authenticated app data), Service selectors, named ports, Ingress backends, RBAC and installer/GitOps parity, patch whitespace and credential scan. *(Passes locally on 2026-09-27.)*
- [x] Live validator `validate-repository.sh --live`: server-side dry-run of every rendered profile against the active cluster, no mutation.
- [x] Nine retained safety suites in `tests/` (K3s backups, PostgreSQL HA, Kafka HA, Vault unseal, Vault HA, node fencing, application clusters, application data, environment onboarding).
- [x] Documentation follows the shared application-repository layout ([ADR 0001](docs/adr/0001-documentation-structure.md)): `features`, `architecture` (overview + ADR index), `data-model`, `development`, `testing`, `code-quality`, `deployment`, `operations`, `security`, `dns`, plus eight procedures under `docs/guides/`; Mermaid topology and delivery diagrams.
- [x] 24 ADRs in `docs/adr/` reconstructed from the Git history, including three superseded decisions (ingress-nginx, Jenkins/Nexus, private rebuilt images).
- [ ] Rename the Ansible task "Reconcile system security image overrides after registry credentials" in `ansible/deploy.yml`; private image overrides were retired in Chart 2.
- [ ] Rename the Elasticsearch ILM policy `swirl-cloud-logs-7d`: its delete phase is `min_age: 1d` (about three days with daily indices), not seven days.

### 2. Installer (`install-control-plane.sh`)

- [x] Refuses to run as root and refuses to run on a K3s worker.
- [x] Guided sections: cluster identity & scope → GitOps & repository delivery → exposure → install scope → topology → host prerequisites & K3s → components → recovery & public access → administrator credentials.
- [x] Unattended `--yes` mode with full environment-variable contract and explicit validation of every required input (Cloudflare token, Access emails, repository sync inputs, node targets).
- [x] Identity prompts: `ORGANIZATION_NAME`, slug, `PLATFORM_DOMAIN`, internal zone, GitLab group/project, Keycloak realm, TLS secret name, Sonar ALM setting, Access IdP/team names, GitOps URL; stored choices re-read from `infra/swirl-cloud-identity` on reruns.
- [x] Install scope `apps` (includes Odoo in `corp`) vs `infra`; both always create the `apps` foundation.
- [x] Server exposure `local` vs `internet`; preserves an enrolled node's recorded `local` exposure.
- [x] Component bundle selection with automatic dependency closure (platform services ⇒ data stores ⇒ Vault/ESO; Odoo ⇒ platform services; Cloudflare ⇒ ingress; HA ⇒ everything + Cloudflare).
- [x] Odd control-plane count planning, final-node-count planning, schedulable vs controller-only control planes.
- [x] HA activation gate: refuses before nodes exist, requires ≥3 control planes (and ≥3 workers when controller-only), resolves the durable HA profile through `resolve-ha-profile.py`.
- [x] K3s install with bundled Traefik disabled, `--secrets-encryption`, `--write-kubeconfig-mode 600`, pinned version; kubeconfig copied with `0600`.
- [x] Embedded-etcd preparation when more than one control plane is planned (`configure-k3s-ha.sh`).
- [x] Read-only legacy migration check before any Kubernetes change (`check-platform-migration.sh`).
- [x] Ordered platform deployment: namespaces → topology → private SSH access → HA storage-detach policy → apps/corp namespaces → foundation manifests → admission baseline → system hardening → host-security record → TLS → Longhorn → Traefik → Cloudflare → Vault → ESO → Vault manifests → Vault bootstrap → ExternalSecret readiness → data stores → platform services → password alignment → Access → GitLab CI → Sonar discovery → Odoo → Descheduler → Argo CD → root Application → environment registration → repository onboarding.
- [x] Ephemeral GitLab administrator token acquired through `gitlab-rails` and revoked on exit.
- [x] Hidden prompts for all secrets; private credentials cleaned up on exit.
- [ ] Final summary prints `Longhorn replicas: <worker count or 1>` and ignores HA mode (actual HA default is 3). Read the value from `infra/swirl-cloud-topology`.
- [x] Final summary points Argo CD to Keycloak SSO (local `admin` is disabled) instead of `argocd-initial-admin-secret`.
- [x] Final summary points GitLab to Keycloak SSO (linked to `root`) instead of `/etc/gitlab/initial_root_password`, and to Vault `secret/infra/gitlab:root_password`. Only `rotate-local-admin-passwords.sh` writes that field; a fresh install stores no local root password.
- [ ] "System prerequisites" installs `openjdk-21-jdk`, `maven`, `docker.io`, Node 24 and appends `MAVEN_OPTS` to `~/.bashrc` on a platform control plane. None is needed by the platform; Docker widens the attack surface, and app repos have moved to Java 25. Drop them or move them to an opt-in developer profile.
- [ ] Helm is installed by piping `raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3` (unpinned branch, Helm 3). Pin a released Helm version and verify its checksum.
- [ ] Node.js is installed through the NodeSource setup script without a pinned checksum.

### 3. Ansible

- [x] `ansible/install.yml`: runs the shell installer locally with `no_log`, optional Vault-encrypted `installer_environment` overrides, check-mode skip.
- [x] `ansible/deploy.yml` reconciliation on an installed control plane: rejects check mode, reads installed identity, validates the platform contract, verifies local K3s/role/kubeconfig, rejects retired image-profile inputs, runs the legacy migration check, preserves recorded exposure and HA, validates HA capacity, renders configuration, validates optional inputs.
- [x] Opt-in private transport (`manage_private_transport`, Tailscale or vRack with optional OVH API attachment) before firewall changes.
- [x] Feature switches with dependency closure: `install_longhorn`, `install_ingress`, `install_vault_stack`, `deploy_data_stores`, `deploy_platform_services`, `install_apps`, `install_odoo`, `install_descheduler`, `install_argocd`, `manage_host_security`, `configure_cloudflare`, `manage_private_transport`.
- [x] `platform_migration_approved` gate reserved for a prepared maintenance run.
- [x] Optional local administrator password alignment through stdin with `no_log`.
- [x] Repository onboarding through `add-repos.sh` with `no_log`.

### 4. Hosts and operating-system security

- [x] Host policy published to Kubernetes as `infra/host-security-config` (role/exposure matrix, Fail2ban jail, CrowdSec profiles and scenarios, inotify limits).
- [x] UFW default-deny inbound on every node; Kubernetes/Docker forwarding restricted on non-cluster interfaces for IPv4 and IPv6. *(live: active)*
- [x] Origin lock: public HTTP/HTTPS accepted only from Cloudflare ranges (`lock_origin_firewall`, `/etc/swirl-cloud/cloudflare-proxy-only`). *(live)*
- [x] Fail2ban on internet-facing control planes: 3 attempts / 1 h, 24 h ban, exponential escalation up to 30 d, UFW ban action, aggressive sshd filter. *(live: active, banning)*
- [x] CrowdSec with firewall bouncer, 7-day IP/range bans, custom slow SSH brute-force and user-enumeration scenarios, raised inotify limits. *(live: active; collections `linux`, `sshd`, `whitelist-good-actors`)*
- [x] SSH hardening: `PermitRootLogin no`, `MaxAuthTries 3`, `LoginGraceTime 30`, `MaxStartups 10:30:30`, `PerSourceMaxStartups 3`.
- [x] Private nodes accept SSH only from registered control-plane private IPs; the current SSH source is proven before rules change (`reconcile-control-plane-access.sh`).
- [x] Journald bounded (`SystemMaxUse=4G`, `MaxRetentionSec=3month`); duplicate cloud-init logrotate removed; unused `rpcbind` disabled.
- [x] Lynis on control planes via `swirl-cloud-lynis.timer` (15th of each month 03:00), root-only report archive, Filebeat → Logstash → Kibana "Lynis Security Audits". *(live)*
- [x] Worker Lynis audits via transient SSH copy (`audit-cluster-nodes.sh`, installed as `swirl-cloud-audit-nodes`).
- [x] Enforced AppArmor profile for K3s containerd (`config/host/cri-containerd.apparmor.d`, `configure-k3s-apparmor.sh`).
- [x] Longhorn host prerequisites: open-iscsi, NFS client, multipath blacklist (`config/host/multipath-longhorn.conf`).
- [x] Workers and internet-only tools removed where not applicable (no Fail2ban/CrowdSec/persistent Lynis on workers).
- [ ] Internet-facing control planes keep `PasswordAuthentication yes` on a publicly reachable port 22 (live UFW shows Fail2ban banning many sources). Move to key-only authentication, or restrict SSH to the Tailscale interface plus an out-of-band console.
- [ ] Package `lynis.timer` (daily) runs alongside the managed monthly `swirl-cloud-lynis.timer` on the live host. Disable one or document why both run.
- [ ] Automatic security updates are the Ubuntu defaults (`unattended-upgrades` enabled) but not managed or monitored by the repo; there is no reboot orchestration (for example kured) for kernel updates. *(from `todo.txt`)*
- [ ] No `auditd` host audit trail. *(from `todo.txt`)*
- [ ] No host malware scanning or EDR. *(from `todo.txt`)*
- [ ] CrowdSec parses only SSH/Linux logs; Traefik access logs are not acquired, so there is no in-cluster HTTP IDS behind Cloudflare. *(from `todo.txt`: "EDR / IDS / WAF")*
- [ ] Kubernetes API audit logging is not configured (no `audit-policy-file`/`audit-log-path` in the K3s config).

### 5. Private node networking and enrollment

- [x] Two transports: OVHcloud vRack (Netplan or ifupdown, optional tagged VLAN, NIC MAC verification, optional OVH API attachment via `configure-ovh-vrack.sh`) and Tailscale (`configure-tailscale.sh`, `configure-tailscale-fleet.sh`).
- [x] Tailscale: merges role tags `tag:<mesh>-control-plane` / `tag:<mesh>-worker` and grants into the existing tailnet policy, issues one-use tagged keys (1 h expiry), binds K3s to `tailscale0` with trusted CIDR `100.64.0.0/10`, keeps etcd control-plane-only. *(live: node InternalIP is a Tailscale address)*
- [x] Shared account/transport wizard (`scripts/lib/transport-guide.sh`) used by installer and node assistant.
- [x] `configure-k3s-control-plane-network.sh` binds K3s to the private address while preserving the public route.
- [x] `add-node.sh`: role (control plane/worker) × mode (remote/local), `--count`, `--ips`, `--hosts`, non-interactive flags, `--token-stdin` (never on the command line).
- [x] Remote enrollment: identity checks, odd-quorum planning before any datastore change, sequential server joins that must become Ready, retries accept already-joined members.
- [x] SQLite → embedded-etcd conversion with an integrity-checked pre-conversion backup under `/var/backups/swirl-cloud/k3s/pre-etcd-<timestamp>`.
- [x] Registry mirror, AppArmor, iSCSI/NFS and Longhorn prerequisites installed on each new node; conflicting roles rejected.
- [x] Topology reconciliation (`reconcile-cluster-topology.sh`): control-plane taints, Longhorn default replicas (1 / worker count / 3 in HA), StorageClass, existing-volume replica increase only, HA anti-affinity, downgrade protection (`--allow-ha-disable`), state in `infra/swirl-cloud-topology`. *(live: 1 replica, schedulable control plane)*
- [x] CoreDNS private zone: `<service>.<internal zone>` → `infra` Services, exact aliases such as Longhorn, preserved headless labels.
- [x] Containerd routes `registry.<domain>` to the fixed internal Registry ClusterIP `10.43.255.251:5050` (`configure-k3s-registry-mirror.sh`).
- [ ] Only one host exists. Every service, the datastore and all backups share one physical failure domain (see §15 and §16).
- [ ] No floating Kubernetes API endpoint; external kubectl clients must pick a reachable server manually after a control-plane loss.

### 6. Kubernetes security baseline and admission

- [x] Namespaces: `infra`, `apps`, `corp`, `gitlab-runners`, `longhorn-system`, `kube-system`, plus registered `apps-int`/`apps-uat`/`apps-prod`. *(live)*
- [x] Pod Security Admission: `apps`, `corp`, `gitlab-runners` and environment namespaces enforce `baseline` and audit/warn `restricted` (v1.36).
- [x] `ValidatingAdmissionPolicy deny-hostpath-on-isolated-namespaces` bound by the `security.<domain>/deny-hostpath` label.
- [x] Cluster-wide `deny-gitrepo-volumes` (CVE-2025-1767).
- [x] `MutatingAdmissionPolicy` hardening for K3s stateless controllers, Longhorn UI, CSI controller, CSI sidecars, driver deployer and pinned CSI sidecar images; privileged engine/instance-manager/CSI driver untouched.
- [x] `reconcile-system-hardening.sh` re-applies hardening to already-running system controllers.
- [x] Default-deny ingress NetworkPolicy in `apps` and `corp`; environment namespaces receive `default-deny` plus `application-connectivity`.
- [x] Workload hardening across platform manifests: non-root UIDs, `RuntimeDefault` seccomp, dropped capabilities, read-only root filesystems where supported, `automountServiceAccountToken: false` where no API access is needed, resource requests/limits.
- [ ] `infra` only audits/warns `restricted`; nothing is enforced there. The manifest comment says "enforce after each workload has been migrated". Enforce `baseline` at least.
- [ ] `infra` has **no default-deny NetworkPolicy**. PostgreSQL, MongoDB, single Redis, Elasticsearch, Keycloak's admin endpoints and every admin backend are reachable from any pod, including CI jobs in `gitlab-runners` and environment namespaces. Only Argo CD (chart-provided), Kafka, HA Redis and the Vault UI helper have policies. *(live: eight policies in `infra`, none default-deny)*
- [ ] Registered environment namespaces (`apps-int`/`uat`/`prod`) do not carry the `security.<domain>/deny-hostpath` label. PSA baseline already blocks hostPath, but the extra guard is inconsistent with `apps`.

### 7. Ingress, TLS and edge (Traefik + Cloudflare)

- [x] Migration from ingress-nginx to a platform-owned Traefik Helm release (v3.7.13, pinned digest), K3s bundled Traefik disabled, native Kubernetes Ingress kept for app ownership.
- [x] Direct mode: `LoadBalancer` via K3s ServiceLB on the bootstrap control plane, `externalTrafficPolicy: Local`, unprivileged listeners 8080/8444, HTTP → HTTPS permanent redirect, TLS ≥ 1.2, default certificate from the shared TLS secret, dashboard disabled, read-only root filesystem, UID 65532.
- [x] 900 s read/write/idle and response-header timeouts for long uploads and streams.
- [x] Forwarded-header normalization middleware (removes `Forwarded`, `X-Real-IP` and routing overrides, forces `X-Forwarded-Proto: https`, keeps the XFF chain) and `ingress-proxy-trust` ConfigMap shared with GitLab.
- [x] Platform middlewares: `platform-sso` (ForwardAuth to OAuth2 Proxy), `platform-body-limit` (1 MiB), `keycloak-root` redirect; strict prefix matching; cross-namespace CRD references disabled.
- [x] Ingresses: Argo CD, Grafana, Kafka UI, Portainer (native OIDC), SonarQube UI (SSO) plus unauthenticated `/api` for scanners, DBGate, Kibana, Longhorn (SSO via ExternalName), GitLab, Registry, Keycloak, Vault, Vault UI theme helper, Homepage, Odoo.
- [x] Admission rule validating environment Traefik Middleware plugins (fixed in `0c032c0`).
- [x] Candidate mode (`INGRESS_MIGRATION_CANDIDATE=true`) for rehearsing Traefik alongside the old controller.
- [x] Cloudflare configurator (`configure-cloudflare.sh`, 1.4 k lines): proxied DNS for the host inventory, retired-host cleanup (`dashboard`), optional apex, unproxied node label, Origin CA certificate (RSA 2048, 30-day renewal guard, key/cert match), Full (strict), Always HTTPS, min TLS, TLS 1.3, HTTP/3, Brotli, WebSockets, Early Hints, IPv6, DNSSEC with registrar DS verification, WAF rule (TRACE/TRACK, `/.env`, `/.git`), login rate limit, cache rules, Registry bot-fight compatibility, XFF request-header transform, Access applications backed by Keycloak for admin hosts.
- [x] Origin CA certificate valid until 2041 in `infra`, `apps` and `corp`. *(live)*
- [x] HA ingress profile: Traefik DaemonSet on control planes with a co-located `cloudflared` per pod, loopback-only trust, liveness tied to local Traefik, readiness to tunnel connection, three-connector check and HTTPS origin probes before DNS switch, `infra/swirl-cloud-public-ingress` state for apps.
- [x] Application DNS ownership: apps declare hosts; onboarding publishes A records (direct) or tunnel CNAMEs (HA) and refuses conflicts.
- [ ] HA tunnel ingress has never been activated or failover-tested (`swirl-cloud-public-ingress` does not exist live).
- [x] Traefik metrics are scraped (job `traefik`, port 9100).
- [ ] No ingress error-rate or latency dashboards.
- [ ] No HSTS or security-header middleware at the origin; header policy relies entirely on Cloudflare and each app.
- [ ] Traefik access logs are not enabled for security analysis (see §4).

### 8. Storage (Longhorn)

- [x] Longhorn 1.12.1 via `reconcile-platform-release.sh`, default StorageClass, topology-driven replica count. *(live: 20 healthy volumes)*
- [x] Weekly filesystem trim RecurringJob `swirl-cloud-weekly-trim` (Sunday 05:30, groups `default` and `swirl-cloud`, snapshots preserved).
- [x] Longhorn UI behind Keycloak SSO and Cloudflare Access through a platform-owned ExternalName Service.
- [x] HA storage policy: replica soft anti-affinity disabled, `node-down-pod-deletion-policy: do-nothing`, controller-manager `disable-force-detach-on-timeout=true` via `configure-ha-control-planes.sh`.
- [x] Optional recurring off-node Longhorn backups to the restic S3 bucket (`swirl-cloud-daily-backup`), host backup enrolls new volumes.
- [ ] **Off-node Longhorn backups are not configured live**: no `swirl-cloud-daily-backup` RecurringJob and the `default` BackupTarget has no URL. GitLab repositories/registry, SonarQube, Odoo filestore, Elasticsearch, Grafana, Portainer and Prometheus volumes have no off-host copy.
- [x] Longhorn manager metrics are scraped (job `longhorn`) through the `longhorn-manager-metrics` NetworkPolicy.
- [ ] No alerts for degraded/faulted volumes or replica rebuilds.
- [x] Cleared snapshots stuck in deletion (2026-09-28): a snapshot marked removed that is the direct parent of the volume head is only purged after a newer snapshot exists, and Longhorn's validating webhook rejects deleting snapshots whose names exceed 63 characters. Keep manual snapshot names under 63 characters; `purge-20260928` snapshots remain as ordinary checkpoints.
- [ ] One volume (`gitlab-runners/gitlab-runner-cache`) reports robustness `unknown` while detached — expected, but confirm it is still used after the move to ephemeral project-runner caches.

### 9. Secrets (Vault + External Secrets)

- [x] Vault 2.1.0 (chart 0.34.1, pinned digest), integrated Raft storage, read-only root filesystem, memory-backed `/tmp`, separate home/data/audit mounts.
- [x] Two audit devices: stdout (→ Fluent Bit) and file on a node-local `vault-audit` PVC; unprivileged rotator sidecar sharing the process namespace (rename + `SIGHUP` + compress), no Vault token.
- [x] `configure-vault.sh`: init, KV-v2 `secret/`, Kubernetes auth, `external-secrets-policy` (read `infra/*` and `apps/*`), OIDC auth to Keycloak, `platform-admin` policy, seeded generated credentials.
- [x] Host unseal service `swirl-cloud-vault-unseal.timer` (30 s after boot, then every minute), key passed as JSON over `kubectl exec -i` stdin, never initializes a cluster, joins empty replacements to an unsealed peer. *(live: active)*
- [x] Recovery material root-only `0600` in `/var/lib/swirl-cloud/`; `sync-vault-recovery.sh` distributes it to verified control planes only.
- [x] External Secrets Operator 2.9.0, `ClusterSecretStore vault-backend`, 20 install-gated ExternalSecrets in `infra` (plus runner, registry-retention and Sonar-discovery tokens) and `corp/odoo-secret`; per-environment `vault-backend-<env>` stores with separate `kubernetes-<env>` auth mounts.
- [x] Vault UI dark theme through a separate NGINX `/ui`-only helper (no token, no caching, no access logs, NetworkPolicy); API traffic goes directly to Vault.
- [x] HA overlay: three Raft voters on three control planes, per-pod audit PVCs, `OnDelete` updates, `configure-vault-ha.sh --verify`, guarded singleton migration with snapshot and archived controller.
- [x] Local administrator password rotation (`rotate-local-admin-passwords.sh`) for PostgreSQL, MongoDB, GitLab, Grafana, Keycloak, Portainer, SonarQube and Elasticsearch, persisted in Vault.
- [x] Vault telemetry is enabled (unauthenticated listener metrics) and scraped (job `vault`). Existing pods pick it up only after a restart.
- [ ] No "Vault sealed" or "audit device failing" alert.
- [ ] The unseal key and the root/bootstrap token sit on the same host as the data, and both are copied into every local backup archive (see §16).
- [ ] Vault HA has never been exercised (single node).

### 10. Data stores

- [x] PostgreSQL 18.6 (Bookworm, digest-pinned) single writer, `Recreate`, non-root UID 999, read-only root, 2 Gi Longhorn PVC `postgres-v18-pvc`, initial `keycloak` database.
- [x] MongoDB 7.0.40 single instance, 5 Gi PVC, root credentials from Vault.
- [x] Redis 8.10.0 single instance with ACL file from `application-redis-auth`. *(live: authenticated)*
- [x] Kafka (Confluent `cp-kafka`) KRaft: one controller (ID 3000) and one broker (ID 1) StatefulSets, 1 Gi each, internal DNS advertisements.
- [x] Authenticated application data profile (`applicationData`): per-environment PostgreSQL logins/databases, Redis ACL users with key prefixes, Kafka SCRAM users with topic/group prefixes, remote broker listener, NetworkPolicies, legacy compatibility credential, adoption of existing databases (`configure-application-data.py`). *(live: enabled)*
- [x] PostgreSQL HA profile: CloudNativePG 1.30 operator (3 replicas + PDB), primary + two standbys, synchronous `ANY 1`, failover quorum, primary isolation checks, strict anti-affinity; `prepare`/`fresh`/`migrate`/`cutover`/`abort` helper with journal, HBA fencing, role/ACL/large-object preservation and `infra/postgres-ha-state`.
- [x] Kafka HA profile: dynamic quorum upgrade (`kraft.version=1`), observer admission, three controllers and brokers, full reassignment, `min.insync.replicas=2`, unclean election disabled, `prepare`/`migrate`/`fresh`/`verify` with locked journal and `infra/kafka-ha-state`.
- [x] Shared Redis HA (`redis-ha` chart): three Redis/Sentinel, three HAProxy, `min-replicas-to-write 1`, `noeviction`, PDBs, NetworkPolicies limiting clients to HAProxy 6379.
- [ ] PostgreSQL PVC is only 2 Gi for Keycloak, SonarQube, Odoo, GitLab-adjacent and all application databases; add a usage alert and capacity plan.
- [ ] No continuous WAL archiving or point-in-time recovery; recovery granularity is the daily `pg_dumpall`.
- [ ] Kafka's in-cluster `PLAINTEXT` listener (9092) stays unauthenticated with `KAFKA_SUPER_USERS=User:ANONYMOUS` and `auto.create.topics.enable=true`. NetworkPolicy limits it to `infra`, `apps` and `corp`, so every legacy app and platform pod is an anonymous Kafka superuser. Migrate legacy clients to SCRAM and close 9092 to `apps`.
- [ ] Every installation shares the hard-coded Kafka cluster ID `8ipNY9RxQtWkattTais5yQ` from `k8s/datastores/kafka.yaml`; generate it per installation for new clusters.
- [ ] Redis ACLs isolate values but `KEYS`/`SCAN` still reveal other environments' key names (documented limitation).
- [x] Datastore exporters: PostgreSQL, MongoDB and Redis sidecars (including shared Redis HA), CloudNativePG's built-in endpoint, and a Kafka exporter Deployment (jobs `postgres`, `mongodb`, `redis`, `kafka`).
- [ ] No datastore health alerts.
- [ ] PostgreSQL/Kafka HA migrations have only been tested offline and against an isolated SQL canary; no three-host rehearsal.

### 11. Identity and SSO (Keycloak + OAuth2 Proxy)

- [x] Keycloak 26.7.3 on PostgreSQL, relative path `/auth`, proxy headers, health and metrics endpoints, UID 1000, realm import.
- [x] `keycloak-sso-reconcile` Job: organization realm (brute-force protection, 12-char complexity policy, no self-registration, `sslRequired: external`, 5-min access tokens, 8 h idle / 10 h max sessions), declarative user profile, roles `platform-admin`/`platform-user`, `groups` client scope, `platform-admins` group with `realm-admin`, bootstrap administrator in both realm and `master`.
- [x] Clients reconciled idempotently with group and audience mappers: `oauth2-proxy`, `gitlab`, `cloudflare-access`, `grafana`, `vault`, `kafka-ui`, `portainer` (confidential); `odoo`, `argocd` (public, PKCE for Argo CD).
- [x] OAuth2 Proxy (v7.15.4, 2 replicas) as Traefik ForwardAuth for DBGate, Kibana, Longhorn, SonarQube and Homepage.
- [x] Native OIDC: GitLab (auto sign-in, links SSO to existing `root`, removes duplicate), Grafana (PKCE, role mapping, server admin), Argo CD (`platform-admins` → `role:admin`, local admin disabled), Vault OIDC, Kafka UI OAuth2, Portainer OAuth, Odoo `auth_oauth` with a managed add-on.
- [x] Kibana signed-session proxy and `kibana-keycloak-user-sync` CronJob (every 5 min) that provisions/disables named Elastic identities.
- [x] SonarQube SSO admin bootstrap and header-based login; GitLab SSO admin bootstrap ConfigMap.
- [x] Cloudflare Access identity provider backed by the `cloudflare-access` Keycloak client, email allowlist.
- [ ] No second factor: realm has no required OTP/WebAuthn action for `platform-admins`, although one identity administers every service. Enforce MFA for the admin group.
- [ ] Odoo's client uses the OAuth2 implicit flow (Odoo 19 `auth_oauth` limitation); replace with an authorization-code add-on when available.
- [ ] Keycloak has `resetPasswordAllowed: true` but no SMTP server anywhere in the platform, so password-reset and verification emails cannot be sent (see §19).
- [ ] Keycloak runs one replica outside HA; Keycloak metrics are exposed but no dashboard/alert uses them.

### 12. Source, CI and registry (GitLab + Runner)

- [x] GitLab CE 19.3.1 Omnibus (digest-pinned, amd64), `external_url https://gitlab.<domain>`, bundled NGINX on 80, Registry on 5050, Let's Encrypt off, KAS off, Puma 2 workers, Sidekiq 10, embedded Prometheus retention 1 d.
- [x] Visitor IP preservation: Rails `trusted_proxies` and bundled NGINX `real_ip_*` built from `ingress-proxy-trust`, IPv6 expanded to avoid YAML symbols.
- [x] Persistent `gitlab-config`, `gitlab-data` (expanded for registry/artifacts), `gitlab-logs` PVCs; Registry data survives restarts.
- [x] Dependency Proxy enabled; group/project provisioning, retention policies, instance runner registration and Vault-backed tokens reconciled by `configure-gitlab-ci.sh`.
- [x] GitLab Runner 19.3.1 (Kubernetes executor) in `gitlab-runners`, `concurrent = 1`, non-privileged, pinned helper image, per-job CPU/memory/ephemeral-storage limits, `if-not-present` pulls, drains jobs during rollout, metrics Service, Grafana "GitLab Delivery" dashboard.
- [x] Project-scoped integration and protected-release runners for version-2 apps with scoped Argo CD permissions (`application_delivery.py`).
- [x] Daily `gitlab-registry-retention` CronJob (03:17 UTC): container cleanup policies and package pruning older than `RETENTION_DAYS` (default 1 095 days); protected tags and `latest` kept.
- [x] Alertmanager webhook into the infrastructure project's **Monitor → Alerts**.
- [x] Platform pipeline `.gitlab-ci.yml`: `validate:repository`; on default branch `verify:gitops` (same SHA Synced/Healthy), `verify:delivery-platform` (GitLab, Prometheus, Grafana, Trivy, Elasticsearch, Registry 401, dashboards, log ingestion) and `verify:registry-write` (OCI blob/manifest push/read). Alpine image digest-pinned, apk cache.
- [ ] GitLab native backup (`gitlab-backup create` plus `gitlab-secrets.json`/`gitlab.rb`, and `db/registry_database.sql.gz` when the metadata DB is used) is documented but **not automated**; the daily host archive does not include it.
- [ ] Deleted tags/packages do not reclaim Registry blobs; there is no scheduled registry garbage collection, and 3-year retention will keep growing `gitlab-data`.
- [ ] `concurrent = 1` on the instance runner serializes every platform job; revisit once more nodes exist.
- [ ] GitLab is a persistent singleton with no replica; recovery depends on Longhorn plus (future) fencing.

### 13. GitOps (Argo CD)

- [x] Argo CD 3.5.2 (chart 10.3.3) via Helm with rendered values; Dex disabled; local `admin` disabled; Keycloak OIDC with PKCE; default `role:readonly`; `platform-admins` → `role:admin`; `resource.respectRBAC: normal`.
- [x] Controller `GOMEMLIMIT=768MiB` under a 1 Gi limit; Redis image pinned; hardened security contexts.
- [x] HA overlay: paired API/repo/ApplicationSet servers, Redis with three Sentinels and two HAProxy, PDBs, dynamic cluster distribution for controller replacement.
- [x] Root Application `swirl-cloud` (`path: k8s`) with automated sync, prune, self-heal, `PruneLast`, `RespectIgnoreDifferences`, identity/HA parameters carried as Helm values. *(live: Synced at `3926910`)*
- [x] Repository credentials projected by generic `infra/repository-<hash>` ExternalSecrets; AppProjects per environment with nested GitLab path matching (`6bf088d`).
- [x] Admission policies binding environment Applications to their registered destination.
- [x] Sonar discovery no longer fails on application projects with GitLab CI/CD disabled: they are skipped and logged (the root Application was Degraded by an HTTP 403 on such a project).
- [ ] Argo CD Helm release is not self-managed; upgrades require the installer, Ansible or a manual Helm run.
- [ ] Notifications controller is deployed with no triggers/services configured, so sync failures and degraded health notify nobody.
- [x] Argo CD metrics are scraped (job `argocd`).
- [ ] HA application controller relies on upstream *alpha* dynamic cluster distribution with a single active controller.

### 14. Observability

- [x] Prometheus (15 s scrape, 15 d / 1 500 MB retention) scraping itself, kube-state-metrics, node-exporter DaemonSet, kubelet, cAdvisor and annotated pods.
- [x] Alert rules: filesystem low/critical/filling within a day, Deployment/StatefulSet unavailable, CrashLoopBackOff, OOMKilled, PV released, PVC pending, Job failed, target down; Alertmanager grouped by alertname/namespace, 4 h repeat.
- [x] Grafana 13.2.1 distroless with Keycloak OIDC, Prometheus and Elasticsearch data sources, Kubernetes, GitLab Delivery and Trivy dashboards.
- [x] Application discovery sidecar (`application-observability.yaml`): follows Pod → ReplicaSet/Job → root controller, identity precedence (Argo tracking → `part-of` → `instance` → `name` → `app` → kind/name), per-namespace stable dashboard IDs, generated Grafana dashboards every minute and Kibana saved objects every 5 min, read-only RBAC in labelled namespaces only.
- [x] ELK 9.4.6 (Wolfi images): Elasticsearch single node (10 Gi) with security bootstrap Job, Kibana behind SSO, Logstash for Lynis, Fluent Bit DaemonSet with Kubernetes metadata and `observability_scope=application`, Filebeat Lynis DaemonSet on control planes.
- [x] Retention: container logs ILM delete after 1 d (daily indices), Lynis indices 365 d, GitLab embedded Prometheus 1 d.
- [x] Trivy Operator 0.34.0 with built-in Trivy server (offline DB, 5 Gi), scans of ReplicaSets/StatefulSets/DaemonSets/CronJobs/standalone pods, config/RBAC/exposed-secret/infra reports, CIS 1.23, NSA 1.0, PSS baseline/restricted compliance every 6 h, detailed metrics, Grafana "Trivy Security Reports".
- [x] SonarQube 26.8 Community with dark theme, SSO, scanner `/api` path, GitLab ALM binding; `sonar-apps-discovery` CronJob (every 15 min) provisions projects, private visibility, protected masked `SONAR_TOKEN`, 24 h freshness, one pipeline per pass, 6 h retry back-off.
- [ ] Alerts go only to GitLab's alert list; there is no email, chat or paging receiver.
- [ ] Missing alerts: node NotReady, K3s/Vault/Longhorn backup failure (`swirl-cloud-k3s-backup.service` result), certificate expiry, Vault sealed, Longhorn volume degraded, Elasticsearch health, ExternalSecret not Ready, Argo CD app degraded, Trivy critical findings, CrowdSec/Fail2ban activity.
- [x] Scraped: Traefik, Vault, Argo CD, Longhorn, PostgreSQL, MongoDB, Redis and Kafka.
- [ ] Not scraped: cloudflared, Elasticsearch, External Secrets.
- [ ] Remote application clusters have no metrics/log collection or central dashboards (documented gap).
- [ ] Elasticsearch is a single node with no snapshot repository; log history is lost with its volume.
- [ ] **Vulnerability backlog live: 119 critical / 924 high findings across 90 image reports.** Largest: `postgres:18.6-bookworm` (16C/32H, also used by the SonarQube helper and application jobs), Elasticsearch (6C/34H), DBGate (5C/19H), Keycloak (4C/7H), SonarQube (4C/26H), GitLab Runner (3C/15H), Argo CD 3.5.2 (2C/26H on every component), Longhorn `csi-provisioner` (2C/10H), MongoDB (1C/49H), Node 24 helper image (1C/7H).
- [ ] Exposed-secret reports flag the GitLab image (11 critical) — review whether they are packaged test fixtures, and suppress with a documented exception or fix.
- [ ] A stale report remains for the retired private image `swirlit/swirl-cloud/security/gitlab`; expire reports whose owners no longer exist.

### 15. High availability (opt-in, not active)

- [x] Durable availability mode in `infra/swirl-cloud-topology`; installer, Ansible and enrollment inherit it and refuse implicit downgrades or unfinished migrations.
- [x] HA templates: CoreDNS three replicas with admission-enforced placement and PDB, Keycloak PDB, Redis HA policies, PostgreSQL HA cluster, Kafka HA PDBs/startup, Traefik/Tunnel DaemonSet, Vault three voters, ESO two replicas, Argo CD HA.
- [x] `verify-high-availability.py` read-only readiness checker (placement, quorum, storage copies, optional fencing coverage).
- [x] Optional Redfish node fencing CronJob (every minute, one host per run, stale Lease + NotReady ≥ 5 min, quorum rechecks, `ForceOff`, then `node.kubernetes.io/out-of-service` taint), inventory Secret, `--check-inventory` mode, RBAC limited to named nodes.
- [ ] Add at least two more control planes (or two control planes plus three workers) — the documented prerequisite for everything below.
- [ ] Execute the ordered activation: PostgreSQL migration → Kafka migration → Vault migration and recovery distribution → shared HA profile with Cloudflare Tunnel cutover → application-owned availability.
- [ ] Run the controlled one-host failure drills (original control plane, database leader, consensus leader) and verify public requests, sessions, writes, Kafka, Vault and Registry push/pull.
- [ ] Acquire Redfish-capable BMC access and enable fencing, or document the manual non-graceful shutdown runbook as the permanent path.
- [ ] Application clusters: bootstrap supports only one control plane per target.

### 16. Backup and disaster recovery

- [x] Daily `swirl-cloud-k3s-backup.timer` (02:15 UTC ± 15 min), root-only archives in `/var/backups/swirl-cloud/k3s`, 7 local copies, SHA-256 files, lock, staging dir. *(live: last run succeeded 2026-09-27)*
- [x] Archive contents: SQLite `.backup` or etcd snapshot, server token, encryption config/state, K3s config and drop-ins, registries.yaml, Vault Raft snapshot, `pg_dumpall`, `mongodump`, PVC and Longhorn volume inventories, datastore-specific `RESTORE.txt`. *(live: verified in the 2026-09-27 archive)*
- [x] Optional restic to S3-compatible storage (encrypted, `keep-daily 7 / weekly 5 / monthly 12`, prune) with root-only `/etc/swirl-cloud/backup.env`.
- [x] Recovery tests: SQLite/etcd restorability, recovery token presence (`test-k3s-backups.sh`), Vault unseal/HA, PostgreSQL SQL canary.
- [ ] **Offsite backups are not configured on the live host**: no `/etc/swirl-cloud/backup.env`, no restic repository. All seven archives live on the same disk as the cluster; a disk or host loss loses everything.
- [ ] Local archives bundle `vault-unseal-key` and `vault-bootstrap-token` with the Raft snapshot; once offsite, keep recovery keys in a separate trust domain (for example a second repository or a password manager) so one leaked archive is not full compromise.
- [ ] No `restic check` or scheduled restore drill; backup success is only the exit status.
- [ ] No automated GitLab, SonarQube, Odoo filestore, Elasticsearch or Grafana data backup (see §8 and §12).
- [ ] No documented and rehearsed full-platform rebuild from offsite backups onto a new host (RTO/RPO unknown).

### 17. Corporate workloads (Odoo in `corp`)

- [x] Odoo 19 (digest-pinned) in `corp` behind `INSTALL_SCOPE=apps` / `appsEnabled`, `dbfilter ^odoo$`, `list_db = False`, `proxy_mode`, 2 workers, memory/time limits, body-limit middleware, allow-ingress NetworkPolicy, Longhorn filestore PVC.
- [x] Bootstrap creates the database/role on PostgreSQL, main company named from the organization, managed administrator.
- [x] Keycloak SSO through the bundled `auth_oauth` module plus a managed `odoo-keycloak-sso` add-on; vendored OCA `web_dark_mode` 19.0.1.0.0 (AGPL, provenance), enabled once for the administrator.
- [x] Migrated from `infra` to `corp` with documented volume migration (`0e16c84`).
- [ ] No outgoing mail server configured, so Odoo cannot send quotes, invoices or password resets.
- [ ] Odoo filestore has no off-host backup (database is covered by `pg_dumpall`).

### 18. Administration UIs and developer portal

- [x] Homepage v2.2.0 at `intranet.<domain>` (2 replicas, PDB), `<Company> Cloud` title, behind Keycloak SSO, cluster-mode Kubernetes widgets, Ingress discovery for app entries, groups Applications / Corporate / DevOps & Delivery / Cluster Management / Data & Messaging / Observability & Logging / Security & Identity / Kubernetes System, retired `dashboard` host.
- [x] Portainer CE with Keycloak OAuth, `cluster-admin` ServiceAccount, bootstrap Job for admin/OAuth settings.
- [x] DBGate with pre-defined PostgreSQL/MongoDB/Redis connections, SSO gate, persistent workspace on its data volume.
- [x] Kafbat Kafka UI with Keycloak OAuth2 and role field mapping.
- [x] SonarQube and Vault dark mode with bottom-right toggles, vendored Dark Reader assets preserving CSP.
- [x] Descheduler addon (DefaultEvictor, RemoveDuplicates, LowNodeUtilization) in manual-run mode (`descheduler-run-job.yaml`).
- [ ] Portainer runs with `cluster-admin`; consider a narrower role or read-only default for non-admin users.
- [ ] DBGate and Kafka UI grant full write access to every `platform-admins` member; no read-only role.

### 19. Application platform services (onboarding and environments)

- [x] `add-repos.sh` → `onboard-repositories.py`: GitHub PAT import into private GitLab projects, nested groups, two-way sync workflow + encrypted Actions secrets + GitLab push/tag webhook, deployment selection, contract validation, `[skip ci]` configuration commits, registry deploy tokens in Vault, Vault field generation, Keycloak PKCE clients, bootstrap resources, DNS, API pipeline with `ONBOARDING_EXPECTED_SHA`/`ONBOARDING_RUN_ID`, trailer verification, `Synced/Healthy` and rollout checks, locked resumable journal, lost-response pipeline recovery.
- [x] GitHub ↔ GitLab synchronization workflow (`.github/workflows/sync-gitlab.yml`): fast-forward/merge without force, tag reconciliation, symbolic refs excluded, monthly self-rotating GitLab project token.
- [x] Onboarding contract version 1 (single target in `apps`) and version 2 (registered environments, per-env data, clients, runners, pinned runtime commit + pointer commit).
- [x] Deployment environments: `configure-deployment-environments.py` registers local/remote targets with namespace, AppProject, Vault store, auth mount, NetworkPolicies, ResourceQuota, TLS, gateway checks; publishes `infra/deployment-environments`. *(live: `int`/`uat`/`prod` local, suffix hostnames)*
- [x] Remote application cluster bootstrap (`install-application-cluster.sh`): pinned K3s, nftables node firewall, Tailscale route checks, refuses unrelated clusters.
- [x] `configure-application-delivery.py` for preparing CI before targets exist.
- [x] Six applications onboarded live, each with its own Argo CD Application in `infra`.
- [ ] Only one application uses contract version 2; the others still deploy single-target into the legacy `apps` namespace. *(live)*
- [ ] Issues, merge requests, releases, packages and LFS objects are not synchronized between GitHub and GitLab (documented).
- [ ] No platform SMTP relay for applications, GitLab notifications, Keycloak, Grafana, Alertmanager or Odoo.

---

## Part IV — Requested roadmap (added 2026-09-27)

Items below were requested on 2026-09-27. Sub-steps come from the current source
and the live installation; the notes flag constraints to settle before building.
Decisions marked **ADR** need a record in [`docs/adr/`](docs/adr/) first (see the
proposed list in [architecture](docs/architecture.md#proposed-adrs-for-roadmap-work)).

### 20. Upgrade every service to its latest stable release

Follow the [image update procedure](docs/operations.md#image-updates) for each
component: review release notes and upgrade paths, restore a backup into a
disposable copy for stateful services, update the pin, validate, reconcile, then
check the running digest and a fresh Trivy report. Upgrade one stateful service
per change. Current pins, as of 2026-09-27:

- [ ] Check the latest stable release of every pin below on 2026-09-27 and record target versions in one tracking issue.
- [ ] Add the human-readable tag next to every digest-only pin (`fluent-bit`, `cp-kafka`, `dbgate`, `portainer-ce`, `prometheus`, `alertmanager`, `node-exporter`, `kube-state-metrics`, `descheduler`, `kafka-ui`, `odoo`, `python`) so upgrades and reports are auditable.
- [ ] K3s `v1.36.4+k3s1` — one minor version at a time, after a fresh datastore backup; servers before agents.
- [ ] Longhorn 1.12.1 — follow the supported minor-by-minor path; check the CSI sidecar pins in `k8s/base/system-workload-hardening.yaml`.
- [ ] Traefik chart 41.5.0 / Traefik v3.7.13 and `cloudflared` 2026.8.3.
- [ ] Vault chart 0.34.1 / Vault 2.1.0 — Raft snapshot first; preserve UID and mounts; roll HA peers one at a time.
- [ ] External Secrets chart 2.9.0.
- [ ] Argo CD chart 10.3.3 / v3.5.2 (also fixes the Go stdlib critical in §22).
- [ ] CloudNativePG operator 1.30.0 (HA profile only).
- [ ] GitLab CE 19.3.1 — respect GitLab's required upgrade stops and back up first; keep GitLab Runner 19.3.1 on the matching minor.
- [ ] Keycloak 26.7.3 and OAuth2 Proxy v7.15.4.
- [ ] Grafana 13.2.1, `k8s-sidecar` 2.11.2, Prometheus, Alertmanager, node-exporter, kube-state-metrics.
- [ ] Elasticsearch, Kibana, Logstash and Filebeat 9.4.6 together (Elasticsearch first), plus Fluent Bit.
- [ ] Trivy Operator 0.34.0 (chart 0.36.0), Trivy 0.74.0, `trivy-checks`, `node-collector` 0.3.1.
- [ ] SonarQube 26.8 Community.
- [ ] PostgreSQL 18.6 — latest 18.x patch on **Bookworm** only; moving to Trixie changes glibc collations and is a separate migration with reindexing.
- [ ] MongoDB 7.0.40 — latest 7.0 patch now; a move to 8.0 needs the `featureCompatibilityVersion` step and its own decision.
- [ ] Redis 8.10.0 (single and HA chart 4.39.0), Kafka (`cp-kafka`) and Kafbat UI.
- [ ] Homepage v2.2.0, Portainer CE, DBGate, Descheduler.
- [ ] Odoo 19 — re-check the vendored `web_dark_mode` and SSO add-ons.
- [ ] Helper images: `nginx` 1.30.4, `node` 24.19.0, `python` 3.14, `ruby` 3.4, `curl`, `busybox`, and the platform CI Alpine digest.
- [ ] Bump `DEFAULT_*` versions in `config/platform.env` and the chart version; rerun installer/Ansible for the separately installed Helm releases.

### 21. Remediate Lynis findings (light-touch, non-blocking)

Live control plane on 2026-09-27: hardening index **66**, one warning, about 40
suggestions. Apply through [`configure-node-security.sh`](scripts/configure-node-security.sh)
so new nodes inherit the result, then rerun Lynis and `swirl-cloud-audit-nodes`.

- [ ] **KRNL-5830 (warning):** reboot is pending after a kernel update. Schedule a maintenance-window reboot now (single node means full outage); add reboot orchestration (for example kured) once there are several nodes.
- [ ] **SSH-7408:** `ClientAliveCountMax 2`, `LogLevel VERBOSE`, `MaxSessions 4`, `TCPKeepAlive no` (client-alive probes keep dead sessions detected).
- [ ] **BANN-7126/7130:** legal banner in `/etc/issue` and `/etc/issue.net`.
- [ ] **DEB-0280, DEB-0811, PKGS-7370, PKGS-7394:** install `libpam-tmpdir`, `apt-listchanges`, `debsums`, `apt-show-versions`.
- [ ] **AUTH-9230/9262/9328:** SHA-crypt rounds and `UMASK 027` in `/etc/login.defs`; `libpam-passwdqc` for local passwords.
- [ ] **AUTH-9282/9286:** password ageing only for human accounts, never for service accounts (optional once SSH is key-only).
- [ ] **NETW-3200:** blacklist unused `dccp`, `sctp`, `rds`, `tipc` protocol modules.
- [ ] **USB-1000:** blacklist `usb-storage` on dedicated servers.
- [ ] **PKGS-7346:** purge the 33 removed-but-not-purged packages.
- [ ] **KRNL-6000:** apply safe sysctl values (`kernel.kptr_restrict`, `kernel.dmesg_restrict`, `fs.protected_*`, `log_martians`) and leave Kubernetes-required settings (`ip_forward`, bridge netfilter) untouched.
- [ ] **FILE-7524:** tighten the file permissions Lynis lists.
- [ ] **ACCT-9622/9628:** process accounting and `auditd` with a small rule set (overlaps §4).
- [ ] **FINT-4350:** AIDE with `/var/lib/rancher`, `/var/lib/longhorn` and container paths excluded, weekly, report-only.
- [ ] **HRDN-7230:** weekly `rkhunter` or `chkrootkit` scan, report-only, shipped to Kibana (overlaps §4).
- [ ] **LOGG-2154:** forward the system journal to Elasticsearch through Fluent Bit.
- [ ] Record accepted risks as `skip-test` entries in a managed Lynis custom profile so the index reflects real gaps.

Deliberately not planned (too disruptive for the benefit): GRUB password
(BOOT-5122, breaks remote console recovery), separate `/home` and `/var`
partitions (FILE-6310, requires reinstalling), moving SSH off port 22 (low value,
breaks tooling).

### 22. Remediate Trivy findings (critical, exploitable, low-effort)

Rules: fix only critical, realistically exploitable findings; prefer a newer
upstream image that contains the fix; when no fix exists, keep the current image
and record the risk; allow only small tweaks, no private rebuilds
([ADR 0022](docs/adr/0022-upstream-images-by-digest.md)). Start with
network-facing components (Keycloak, Argo CD server, GitLab, Traefik).
Criticals with a fix available on 2026-09-27:

- [ ] Argo CD v3.5.2 — Go stdlib CVE-2025-68121 (fixed in Go 1.24.13/1.25.7): upgrade to an Argo CD patch built with a fixed Go.
- [ ] Keycloak 26.7.3 — netty-handler CVE-2026-75595, bcprov CVE-2026-8763, freemarker CVE-2026-84939: upgrade to a Keycloak patch that bundles them.
- [ ] SonarQube 26.8 — netty-handler and bcprov: newer SonarQube.
- [ ] Elasticsearch 9.4.6 (netty-handler) and Logstash 9.4.6 (bcprov): next Elastic patch, all four images together.
- [ ] Kafbat UI — netty-handler: newer image.
- [ ] GitLab Runner alpine-v19.3.1 — openssh CVE-2026-60002 (fixed in `10.3_p1-r1`): next runner patch.
- [ ] Longhorn `csi-provisioner` v5.3.0 — gRPC CVE-2026-33186 and Go stdlib: small tweak — pin a compatible newer sidecar through the existing CSI image admission policy, as already done for `node-driver-registrar` and `livenessprobe`.
- [ ] MongoDB 7.0.40 — Go stdlib in bundled tools: next 7.0 patch.
- [ ] `node` 24.19.0 helper, Homepage v2.2.0, DBGate — npm's bundled `tar` CVE-2026-59873: take newer images when published; npm is not executed at runtime, so linger otherwise.
- [ ] No fix available yet (linger and record): Debian Bookworm `perl`, `libxml2`, `libsqlite3`, `zlib` findings in `postgres:18.6-bookworm` (also used by SonarQube's helper and application backup jobs) and DBGate. The server does not invoke Perl, and the zlib CVE is in minizip, which PostgreSQL does not use.
- [ ] Triage the 11 critical exposed-secret findings in the GitLab image (likely packaged test fixtures); record documented exceptions in Trivy policy data.
- [ ] Expire stale reports for retired owners (for example `swirlit/swirl-cloud/security/gitlab`).
- [ ] Scan platform artifacts too: the vendored charts in `k8s/charts/`, the CI Alpine image and pinned GitHub Actions, as a non-blocking CI report.
- [ ] Hand application image findings to the owning repositories.

### 23. Odoo API for applications (CRM leads and Live Chat)

Applications own their UI changes in their own repositories; the platform owns
Odoo and the wiring.

- [ ] Install and configure Odoo `crm` (contact forms become CRM leads) and `im_livechat` (support chat) in the Odoo bootstrap.
- [ ] Create a dedicated Odoo integration user per consuming application with an API key limited to leads and chat; store it at Vault `secret/apps/<app>/<env>/odoo` and project it with an ExternalSecret.
- [ ] Allow in-cluster API calls from an application's namespace to Odoo on port 8069 through a `corp` NetworkPolicy (default deny today), instead of the public route.
- [ ] Keep the public Live Chat loader and chat endpoints on `odoo.<domain>` reachable without Cloudflare Access (Odoo is already outside the Access list), rate-limited at the edge.
- [ ] Document the API contract (fields, errors, idempotency) for consuming applications.

### 24. Installer: TUI, central `.env`, reruns, verification and email

Goal: one versioned `.env` drives every entry point (TUI now, web UI next,
Ansible and unattended runs); the installer engine reads only that file and never
prompts. **ADR** on the file schema and TUI technology.

- [ ] Define a versioned `.env` schema covering every current input (identity, scope, exposure, topology, transport, components, backups, Cloudflare/Access, Tailscale/OVH, repositories, administrator credentials, password rotation, HA and environments), with types, defaults, required flags and secret flags.
- [ ] Split `install-control-plane.sh` into an engine (`--env-file`, no prompts, reports every missing or invalid key at once) and a front end.
- [ ] Build the TUI front end (for example `whiptail`/`dialog`, available on Ubuntu without extra runtimes) that walks every question of the current installer and writes the `.env` (mode `0600`).
- [ ] Start with an **express** screen proposing a default configuration and listing what it installs: every platform service, no application repositories unless provided. It asks only for company name and domain, administrator credentials, required API keys (Cloudflare; Tailscale/OVH for multiple nodes; optional S3 backups) and optional GitHub repositories to import.
- [ ] Keep an **advanced** path with every current prompt grouped as today.
- [ ] After a successful run, redact secrets in the `.env` (store them in Vault and keep references such as `vault:secret/infra/installer#KEY`). Reversible obfuscation would not protect them, so redact rather than obfuscate.
- [ ] Make reruns idempotent: detect installed components (Helm releases, workloads, `swirl-cloud-identity`, topology, environments), show installed/missing/drifted, and propose only what is missing. Always offer the GitHub repository sync step.
- [ ] Add a post-install verification stage: nodes Ready, Argo CD Applications Synced/Healthy, ExternalSecrets Ready, pods Ready, HTTPS check of every Ingress host, TLS validity, Vault unsealed, backup timers active, `validate-repository.sh --live`; exit nonzero with a readable report on failure.
- [ ] Email the report when the deployment is ready, with dashboard URLs for every service and application (from Ingress hosts and Homepage). Depends on the SMTP relay in §28.
- [ ] Make `ansible/install.yml` and `--yes` consume the same `.env`.
- [ ] Next step: a web UI that edits the same schema, runs the engine and streams its logs, behind Keycloak.
- [ ] Tests: schema validation, secret redaction (no secret survives), rerun detection.

### 25. Deployment UI (app → environment → deploy)

- [ ] Small web app in `infra` behind Keycloak and Cloudflare Access, listed on Homepage, added to the host inventories.
- [ ] List onboarded applications (Argo CD Applications or GitLab projects with an onboarding contract).
- [ ] Offer an environment selector only when `infra/deployment-environments` has targets for that app.
- [ ] **Deploy** triggers the app's GitLab pipeline through the API with `PIPELINE_MODE=full` and the chosen `DEPLOYMENT_ENVIRONMENT` (latest release for `uat`/`prod`), using a scoped token from Vault; version-1 apps trigger their release pipeline.
- [ ] Show progress and, on success, the application URL from its Ingress. Nothing else: no configuration editing.
- [ ] Restrict to `platform-admins` initially; post results to Mattermost (§30).

### 26. Expand onto new EPYC servers and move workloads off the current node

Target hardware: AMD EPYC ≥ 16 cores, 128 GB RAM, ≥ 1 TB NVMe, public and private
bandwidth (for example OVHcloud Advance/Scale with vRack). The current server
stays as a lightweight control plane; no replacement migration is needed.

- [ ] Order two servers now and a third later.
- [ ] **ADR — transport:** the repository supports one transport per cluster (`K3S_NODE_TRANSPORT`), and the current server has only a public interface and uses Tailscale. Either run all nodes over Tailscale (works today), or add mixed-transport support (vRack between new servers, Tailscale to the current one), which needs per-node addresses reachable by every node for flannel, etcd and Longhorn.
- [ ] Before starting: complete offsite backups (§16), take a verified backup, and measure `fdatasync` latency with `fio` on the current SATA SSD and the new NVMe disks (etcd wants a 99th percentile under about 10 ms).
- [ ] **Phase 1 — three etcd members:** `./add-node.sh --role control-plane --mode remote --count 2` converts the current SQLite datastore to embedded etcd (with its pre-conversion backup) and joins both new servers.
- [ ] Add a **per-node** scheduling policy: the current server becomes `NoSchedule` for workloads while the new control planes stay schedulable. Today `CONTROL_PLANE_SCHEDULABLE` is cluster-wide in `reconcile-cluster-topology.sh`.
- [ ] Longhorn: disable disk scheduling on the current server, request replica eviction, wait for healthy replicas on the new nodes, and define the replica rule for two storage nodes (the current table covers only "no workers", "workers" and HA).
- [ ] Move workloads: let singletons (GitLab, SonarQube, Odoo, PostgreSQL) follow their evicted volumes in a maintenance window, then run the Descheduler.
- [ ] Ingress: direct Traefik is pinned to the bootstrap node (`svccontroller.k3s.cattle.io/enablelb`) and every DNS record points to its public IP. Either keep ingress there, move the label and update platform and application DNS, or activate Tunnel ingress (§15).
- [ ] **Use DNS names instead of IP addresses.** Today `configure-cloudflare.sh` publishes A records to the origin IP (`--origin-ip`), environments store an IP `ingressAddress`, and nodes join through `https://<private IP>:6443`. Publish one ingress hostname (for example `ingress.<domain>`) and point platform and application records at it as CNAMEs (Cloudflare flattens the apex), accept a hostname for `ingressAddress`, and join nodes through a server hostname included in the K3s TLS SANs. Moving ingress or a server then changes one record.
- [ ] Distribute Vault recovery material to the new control planes (`sync-vault-recovery.sh`) and install the unseal and backup timers there.
- [ ] Apply host security to the new servers' public interfaces (UFW, Fail2ban, CrowdSec) and confirm only the private transport carries cluster traffic.
- [ ] **Phase 2 — fourth control plane:** add the third new server, run etcd only on the three new servers, and turn the current server into an API-only control plane (`--disable-etcd`). This means removing its etcd member and rejoining it, so move Vault recovery, backup timers and ingress first.
- [ ] Teach enrollment, quorum checks and backups about API-only servers: count etcd members, not control planes, and skip snapshots on etcd-less servers.
- [ ] Then activate HA (§15) with three storage nodes, and amend ADR 0011.

### 27. Resize platform services for the new capacity

Size from observed Prometheus usage with about 2× headroom; be generous but not
excessive.

- [ ] Introduce sizing profiles (for example `small`/`large`) in chart values, selected by the installer from node allocatable CPU and memory.
- [ ] GitLab: more Puma workers and Sidekiq concurrency, higher memory limits, larger `gitlab-data`.
- [ ] GitLab Runner: raise `concurrent` from 1 (for example 4–6) and per-job limits (CPU 2 → 4, memory 3 Gi → 6–8 Gi, ephemeral storage 4 Gi → 20 Gi); resize the cache.
- [ ] PostgreSQL PVC 2 Gi → 20 Gi+ with tuned `shared_buffers` and `work_mem`; MongoDB and Kafka volumes accordingly.
- [ ] Elasticsearch heap and volume (10 Gi → 50 Gi+); container log retention from about 3 days to 7–14 days; keep Lynis at 365 days.
- [ ] Prometheus retention from 15 d / 1 500 MB to about 30 d / 20 GB; GitLab embedded Prometheus from 1 d to 7 d.
- [ ] Keycloak, SonarQube, Argo CD controller (`GOMEMLIMIT` with its limit), repo server, Trivy scan concurrency (2 → 4), Grafana.
- [ ] Raise `apps-int`/`apps-uat`/`apps-prod` ResourceQuotas.
- [ ] Review CI artifact expiry per application and keep registry retention with garbage collection (§12).

### 28. Directory, mail and SSO

**ADR — provider:** Option A, Microsoft 365 Business: mail and user accounts
(provisioned into Keycloak), Office and Teams, with a higher tier for advanced
directory and policy management. Option B, self-hosted LDAP (FreeIPA or similar)
plus Mailcow or equivalent: EU-sovereign and a potential SaaS offer.

Mail (either option):

- [ ] Create `zaid@swirlit.dev` on the chosen provider.
- [ ] Forward incoming mail to `c.zaid@outlook.com` while keeping a copy in the mailbox (for Exchange Online, forwarding with "keep a copy" and external forwarding allowed in the outbound anti-spam policy, which blocks it by default).
- [ ] Remove the inbound mail configuration from Cloudflare (Email Routing) and Resend (receiving/MX) to avoid MX conflicts; keep Resend's outgoing-mail records (SPF include, DKIM) for applications that still send through it.
- [ ] Add the provider's MX, SPF, DKIM, DMARC and autodiscover records, managed alongside `configure-cloudflare.sh` (it preserves unrelated MX/TXT records).
- [ ] Provide a platform SMTP relay credential in Vault for Keycloak, GitLab, Grafana, Alertmanager, Argo CD notifications, Odoo and the installer report. This closes the SMTP gap in §11, §17 and §19.

Directory and SSO:

- [ ] Stand up the directory for `swirlit.dev`. Option B note: FreeIPA and Mailcow expect a VM or dedicated host, not Kubernetes pods (ties to §31).
- [ ] Default access: a new directory user gets access to everything (directory group mapped to `platform-admins`); fine-grained roles later.
- [ ] Provision directory users into Keycloak (Microsoft 365 accounts for Option A, LDAP federation for Option B) in the `swirlit` and `master` realms, with periodic sync and group mappers.
- [ ] The directory `zaid` gets the same rights as today's superadmin: `master` admin and `swirlit` `platform-admins` + `realm-admin`. Resolve the username clash first: Keycloak cannot hold two `zaid` users in one realm, so rename the local break-glass account (for example `zaid-local`) and keep it active as backup.
- [ ] Verify that every SSO client (GitLab, Argo CD, Grafana, Vault, Kafka UI, Portainer, Odoo, OAuth2 Proxy) accepts directory users and that GitLab links the directory `zaid` to the existing account.
- [ ] Move application logins (in the application repositories) to the shared realm where they authenticate users.
- [ ] Device enrollment scripts in a dedicated folder or repository: Windows (Entra join/Intune for Option A; Samba AD for Option B, since FreeIPA is not an AD domain for Windows GPOs), macOS (Intune/Platform SSO or directory binding), Linux (`sssd`/`ipa-client-install`; Intune support for Linux is limited).

### 29. New `corp` services: n8n and Open WebUI

- [ ] n8n: PostgreSQL database, encryption key in Vault, Longhorn volume, Ingress with the editor behind SSO (OAuth2 Proxy, since n8n's own OIDC SSO is a paid feature) and webhook paths public but rate-limited, Homepage entry, backups.
- [ ] Open WebUI: Keycloak OIDC login, PostgreSQL database, persistent volume, OpenRouter as an OpenAI-compatible connection with its key in Vault.
- [ ] RAG: vector store (pgvector in PostgreSQL or Qdrant), embedding model, ingestion jobs (n8n workflows or CronJobs) from GitLab repositories and wikis, Odoo, read-only database users, Elasticsearch and Prometheus/Grafana (read-only tools). There is no separate wiki service today; confirm which wikis are meant.
- [ ] Data governance: exclude secrets and personal data, keep per-source read-only credentials, and select an EU datacenter region in the OpenRouter settings.
- [ ] NetworkPolicies from `corp` to the data sources in `infra`.

### 30. New `infra` services: Mattermost, Nexterm, Bitwarden

- [ ] Mattermost: PostgreSQL, file storage (volume now, S3 later), GitLab SSO (OIDC is a paid feature), channels for deployments, releases and alerts.
- [ ] Wire notifications: GitLab pipeline integration, Argo CD notifications (sync succeeded/failed, health degraded — closes §13), Alertmanager webhook (closes the receiver gap in §14), installer and Deployment UI results.
- [ ] Nexterm on a control plane (`nodeSelector`), behind SSO and Access, preloaded with every node's private address. Use a dedicated non-root SSH key from Vault, and extend the node SSH allowlists to Nexterm's source address (today only control-plane private IPs may SSH to nodes).
- [ ] Bitwarden for human logins: **ADR** between official self-hosted Bitwarden (SSO needs an Enterprise licence) and Vaultwarden (lighter, community). Disable open sign-ups, admin token in Vault, daily backup, and let browser/mobile clients reach the API without Cloudflare Access interception. HashiCorp Vault remains the store for machine secrets.

### 31. Host backup and virtualization

- [ ] Veeam for host-level backups on the live servers (Veeam Agent for Linux, outside this repository or as an optional host role) to an S3-compatible target. Evaluate Kasten K10 (Veeam's Kubernetes product) against the existing Longhorn/restic backups for application-consistent PVC backups.
- [ ] VM capability once there is spare capacity: **ADR** between Apache CloudStack (separate management plane and KVM hosts) and KubeVirt (VMs as Kubernetes workloads under Argo CD). A VM layer would also host FreeIPA/Mailcow (§28).

### 32. Future hardware

- [ ] NAS on the private network serving S3-compatible storage with MinIO (check its current community-edition terms; Garage or SeaweedFS are alternatives). Consumers: GitLab object storage (artifacts, registry, LFS), Mattermost and Open WebUI files, and any service that needs S3.
- [ ] LLM server with 512 GB–1 TB RAM and good GPUs with **120 GB+ of total VRAM** for an open-weights coding model. Keep the model weights and KV cache in VRAM; RAM holds offloaded layers and larger models. CPU-only inference (for example on OVHcloud Eco) is bound by memory bandwidth and too slow for interactive coding. Benchmark tokens per second for the chosen model and quantization on the exact GPU configuration before buying. Expose an OpenAI-compatible API privately (Tailscale) to Open WebUI and developer tools; keep it outside the platform cluster or as a tainted dedicated node.

---

## Part III — Priority index of open items (Parts II and IV)

| Priority | Item | Section |
|---|---|---|
| P0 | Configure offsite restic + Longhorn backups; separate recovery keys from data archives | §8, §16 |
| P0 | Automate GitLab native backups (with secrets and registry DB) | §12, §16 |
| P1 | Default-deny NetworkPolicy and PSA `baseline` enforcement in `infra` | §6 |
| P1 | Key-only or Tailscale-only SSH; MFA for `platform-admins` | §4, §11 |
| P1 | Alert receivers beyond GitLab and the missing backup/node/cert/Vault/Longhorn alerts | §14 |
| P1 | Reduce 119 critical / 924 high image findings, starting with PostgreSQL, Elasticsearch, DBGate, Argo CD | §14 |
| P1 | Move legacy Kafka clients off the anonymous-superuser `PLAINTEXT` listener | §10 |
| P1 | Trivy criticals with an upstream fix (Argo CD, Keycloak, SonarQube, ELK, Runner, CSI provisioner) | §22 |
| P1 | Light Lynis hardening and the pending reboot | §21 |
| P1 | Upgrade every service to its latest stable release | §20 |
| P1 | New EPYC servers, three etcd members, workloads and storage off the current node; DNS names instead of IPs | §26 |
| P2 | Platform SMTP relay | §11, §17, §19 |
| P2 | Argo CD notifications | §7, §13, §14 |
| P2 | Installer cleanup: Longhorn replica summary line, dev-tool prerequisites, pinned Helm/Node installers | §2 |
| P2 | Kubernetes API audit logging, auditd, HTTP IDS, reboot orchestration | §4 |
| P2 | Mail provider, SMTP relay, inbound mail DNS cleanup; directory users in Keycloak | §28 |
| P2 | Installer `.env` engine, TUI, idempotent reruns, verification and email report | §24 |
| P2 | Resize services, runners and retention for the new capacity | §27 |
| P2 | Mattermost notifications, Bitwarden, Nexterm | §30 |
| P2 | Odoo API for applications: CRM leads and Live Chat | §23 |
| P3 | Add nodes and run the ordered HA activation and failure drills | §15 |
| P3 | Migrate version-1 apps to contract version 2 | §19 |
| P3 | Naming cleanups (`swirl-cloud`, ILM `7d`, Ansible task title) and per-install Kafka cluster ID | §1, §10 |
| P3 | Deployment UI | §25 |
| P3 | n8n and Open WebUI with RAG | §29 |
| P3 | Veeam, CloudStack or KubeVirt, NAS S3, GPU LLM server (120 GB+ VRAM) | §31, §32 |
