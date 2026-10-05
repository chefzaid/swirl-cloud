# Swirl Cloud — platform inventory and roadmap

The direction is a reusable private cloud: recover the existing installation
reliably, expand onto several hosts, then make installation and application
delivery easier and add company services, then package it as a product: a
sovereign company cloud ([Part IV](#part-iv--product-roadmap-added-2026-10-04)).
Application repositories keep their
runtime resources and delivery contracts; this repository owns the shared
platform and centrally managed corporate services.

**Source review: 2026-09-28, `main` at `6ada82b`.** The original inventory covered
`3926910` and a read-only live inspection on 2026-09-27. This review compared the
backlog with current source, documentation and subsequent commits; it did not
inspect or change the running cluster. Completed entries remain as history.

**Legend**

- `[x]` Recorded implementation or completed work. The original audit recorded
  nine render profiles and nine safety suites passing; *(live)* means observed
  in that dated audit, not a fresh health check or a successful failover drill.
- `[ ]` Not done, broken, partial, only documented, or still needs a real-world
  exercise. Each open item says why.

**Historical live snapshot (2026-09-27):** one node (`swirlit-node-01`, 8 vCPU, 31 GiB
RAM, 145 GiB disk at 66 % used), K3s `v1.36.4+k3s1`, SQLite datastore, Tailscale
private address, internet exposure, `appsEnabled=true`, `applicationData.enabled=true`,
`highAvailabilityEnabled=false`, `nodeFencing.enabled=false`. Root Argo CD
Application `swirl-cloud` was **Synced at `3926910` but Degraded**. Source fixes
for disabled-project discovery and retention have since landed (§12–13);
current cluster health still needs verification.

Work in dependency order: **recovery and current health → access controls and
useful alerts → capacity and HA → installer/delivery UX → additional services**.
Use the [priority index](#part-v--priority-index-of-open-items) to pick work.
Repeated gaps in the inventory point to their owning roadmap section; they are
not separate implementations. Close operational items only with a dated result
(restore, migration or failure drill); source/render checks alone are insufficient.

---

## Part I — Build timeline (how it got here)

| Phase | Dates | What was built | Key commits |
|---|---|---|---|
| 0. Genesis | 2026-03-07 → 03-15 | Single dedicated server, K3s, first manifests, MongoDB, security baseline, Descheduler, DBGate, Vault unseal fix | `efb0452` … `e540da8` |
| 1. Host hardening | 2026-05-20 → 06-10 | `todo.txt` security wish-list (Fail2ban, CrowdSec, Lynis, auto-updates, auditd, AV, EDR/IDS/WAF); internet-exposed node controls | `3952910`, `9630f0f`, `3b917e9` |
| 2. Corporate ERP | 2026-08-08 | Odoo added | `57b41c7` |
| 3. Automation | 2026-08-13 → 08-23 | Full platform automation, private networking (vRack/Tailscale), service discovery | `0c259a9`, `d721d2b`, `9f5fc21` |
| 4. Layout & GitLab | 2026-08-28 → 08-31 | Namespace standardization (`infra`/`apps`/`corp`), delivery migrated to GitLab, SSO centralization, portable bootstrap, Trivy, runner fixes | `aab48f8` … `b22ce24` |
| 5. Public web & quorum | 2026-09-06 | Apex DNS reserved for an application-owned public site, odd-quorum multi-control-plane support | `56aa1c0`, `7e3d48d` |
| 6. Security remediation | 2026-09-07 → 09-10 | Patched/rebuilt images, Odoo → `corp`, Sonar discovery, dark themes, registry persistence, repo replication, role-aware enrollment, later replaced by official upstream images | `c3daa69` … `a76882a` |
| 7. Retention & docs | 2026-09-10 | Telemetry retention cuts, guide refactor, Mermaid diagrams | `54806ec` … `4334580` |
| 8. HA preparation & onboarding | 2026-09-10 | End-to-end opt-in HA profiles, repeatable repository onboarding | `25ad27b`, `352f61e` |
| 9. Identity parameterization | 2026-09-13 | Intranet host, apps-namespace discovery for Grafana/Kibana/Sonar, organization identity inputs | `e6a63c1` … `379131c` |
| 10. Chart 2.0 | 2026-09-14 | ingress-nginx → Traefik, private images → pinned upstream digests, migration guards | `ef35d1c` … `a20c85c` |
| 11. Recovery & edge polish | 2026-09-20 → 09-23 | Registry backup coverage docs, GitLab visitor IP, Cloudflare permissions, branding, guide consolidation | `811ba6e` … `2484453` |
| 12. Environments | 2026-09-24 → 09-26 | `int`/`uat`/`prod` local or remote targets, scoped runners, admission for Traefik middleware and Argo projects | `0e52e16` … `3926910` |
| 13. Platform cleanup | 2026-09-27 → 09-28 | Documentation, additional metrics/exporters, disabled-project discovery/retention fixes, portal at `cloud.<domain>` | `1eecfe6` … `6ada82b` |

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
- [x] `validate-repository.sh --live` passes against the current cluster again. The earlier Job conflict only occurred while an Argo CD sync hook was running; objects that need `cnpg-system` or CloudNativePG APIs before HA activation are now counted as not validated instead of failing. *(live, 2026-10-04: 8 objects reported)*
- [x] Ten retained safety suites in `tests/` (K3s backups, PostgreSQL HA, Kafka HA, Vault unseal, Vault HA, node fencing, application clusters, application data, environment onboarding, installation inputs).
- [x] Documentation follows the shared application-repository layout ([ADR 0001](docs/adr/0001-documentation-structure.md)): `features`, `architecture` (overview + ADR index), `data-model`, `development`, `testing`, `code-quality`, `deployment`, `operations`, `security`, `dns`, plus eight procedures under `docs/guides/`; Mermaid topology and delivery diagrams.
- [x] 25 ADRs in `docs/adr/` reconstructed from the Git history, including three superseded decisions (ingress-nginx, Jenkins/Nexus, private rebuilt images).
- [x] The Ansible hardening step is named "Reconcile hardened system controller templates"; private image overrides were retired in Chart 2.
- [ ] Reconcile the Elasticsearch ILM policy name `swirl-cloud-logs-7d` with its actual delete phase (`min_age: 1d`) and the retention decision in §26; migrate existing index/template references without dropping retained logs.

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
- [x] Final summary reports Longhorn replicas from `infra/swirl-cloud-topology`, so HA mode's three replicas show correctly.
- [x] Final summary points Argo CD to Keycloak SSO (local `admin` is disabled) instead of `argocd-initial-admin-secret`.
- [x] Final summary points GitLab to Keycloak SSO (linked to `root`) instead of `/etc/gitlab/initial_root_password`, and to Vault `secret/infra/gitlab:root_password`. Only `rotate-local-admin-passwords.sh` writes that field; a fresh install stores no local root password.
- [x] System prerequisites are runtime and validation tools only (Ansible, storage clients, `jq`, `git`, Node.js from the distro for the validator's `node --check`, ShellCheck, SQLite). Java 21, Maven and Docker install only with `INSTALL_DEVELOPER_TOOLS=true`; the `MAVEN_OPTS` profile edit is gone. Existing hosts keep their packages.
- [x] Helm is installed from the release pinned in `config/platform.env` (`DEFAULT_HELM_VERSION`, v3.22.0) after SHA-256 verification; the mutable `get-helm-3` script is no longer executed. *(validated with v3.22.0, 2026-10-04)*
- [x] The NodeSource setup script is no longer executed; the distro `nodejs` package is used.

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
- [x] The duplicate distro `lynis.timer` is masked by `configure-lynis-schedule.sh`; `swirl-cloud-lynis.timer` is the only audit schedule. *(live)*
- [x] The managed Lynis run exports `swirl_cloud_lynis_*` metrics through node-exporter's textfile collector; `LynisAuditFailed` and `LynisAuditMissing` (no success for 35 days) alert through Alertmanager. *(live: metrics scraped, rules healthy, 2026-10-04)*
- [x] Unattended security updates run with the distro defaults (security origins, no automatic reboot). Every node exports `swirl_cloud_reboot_required` through node-exporter; `NodeRebootRequired` alerts after seven days pending. *(live, 2026-10-04: pending since 2026-10-03 for kernel 7.0.0-38)*
- [ ] Define maintenance windows. Verify recovery and console access before the single-node reboot; add quorum/PDB-aware reboot coordination after multi-host drills (§20, §25).
- [x] Small host audit rule set through `auditd` in the host baseline (§20 ACCT-9628). *(live, 2026-10-04)*
- [x] Report-only integrity and malware checks (AIDE, rkhunter) run weekly (§20). *(live, 2026-10-04)*
- [ ] Assess a full EDR only if those checks leave a concrete detection gap.
- [ ] CrowdSec parses only SSH/Linux logs; Traefik access logs are not acquired, so there is no in-cluster HTTP IDS behind Cloudflare. *(from `todo.txt`: "EDR / IDS / WAF")*
- [x] Host firewall cannot stay off after a reconcile: on 2026-10-04 a hardening rerun left UFW disabled for about 3 minutes (22:10–22:13 UTC) because `ufw-init` saw leftover IPv4 chains and skipped the IPv6 rules. The script now verifies UFW after enabling it, reloads every rule file on failure and otherwise fails closed; `HostFirewallDisabled` and the `--status` firewall check detect it.
- [x] Kubernetes API audit logging is configured (live on `swirlit-node-01` since 2026-10-04 22:15 UTC). *Details:* `scripts/configure-k3s-audit.sh` with a metadata-only policy (validated on a disposable K3s v1.36.4: Secret reads and writes recorded, values absent); the installer and control-plane enrollment apply it before K3s starts. Fluent Bit ships it to `kubernetes-audit-*` with 30-day retention (pipeline tested with the pinned Fluent Bit image). Applied with a 14-second K3s restart.

### 5. Private node networking and enrollment

- [x] Three transports: OVHcloud vRack (Netplan or ifupdown, optional tagged VLAN, NIC MAC verification, optional OVH API attachment via `configure-ovh-vrack.sh`), Tailscale (`configure-tailscale.sh`, `configure-tailscale-fleet.sh`) and a local LAN (existing fixed RFC1918 addresses, no host network changes; the default-route NIC is accepted only behind NAT). Application clusters remain Tailscale-only.
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
- [ ] Provide a stable private Kubernetes API endpoint with certificate SANs and tested failover (§25); a DNS alias alone does not detect or route around a failed server.

### 6. Kubernetes security baseline and admission

- [x] Namespaces: `infra`, `apps`, `corp`, `gitlab-runners`, `longhorn-system`, `kube-system`, plus registered `apps-int`/`apps-uat`; local `prod` shares `apps`. *(live)*
- [x] Pod Security Admission: `apps`, `corp`, `gitlab-runners` and environment namespaces enforce `baseline` and audit/warn `restricted` (v1.36).
- [x] `ValidatingAdmissionPolicy deny-hostpath-on-isolated-namespaces` bound by the `security.<domain>/deny-hostpath` label.
- [x] Cluster-wide `deny-gitrepo-volumes` (CVE-2025-1767).
- [x] `MutatingAdmissionPolicy` hardening for K3s stateless controllers, Longhorn UI, CSI controller, CSI sidecars, driver deployer and pinned CSI sidecar images; privileged engine/instance-manager/CSI driver untouched.
- [x] `reconcile-system-hardening.sh` re-applies hardening to already-running system controllers.
- [x] Default-deny ingress NetworkPolicy in `apps` and `corp`; environment namespaces receive `default-deny` plus `application-connectivity`.
- [x] Workload hardening across platform manifests: non-root UIDs, `RuntimeDefault` seccomp, dropped capabilities, read-only root filesystems where supported, `automountServiceAccountToken: false` where no API access is needed, resource requests/limits.
- [x] PSA `baseline` inventory for `infra` (server dry-run of the enforce label): only the three host telemetry DaemonSets violate it — `node-exporter` (hostNetwork, hostPID, hostPort, hostPath `/`), `fluent-bit` (hostPath container logs, journal, state) and `filebeat-lynis` (hostPath `/var/log`, state). *(live, 2026-10-04)*
- [x] The three agents run in the privileged `host-agents` namespace ([ADR 0025](docs/adr/0025-host-agent-namespace-and-infra-pod-security.md)) with their ExternalSecret, store admission, scrape discovery and installer/Ansible waits; the `vault-backend` store now syncs before ExternalSecrets. Node metrics, container logs and the journal flow again, and a `baseline` dry run on `infra` reports no violating pods. During the hostPort/state-lock cutover the old DaemonSets were removed by hand; Fluent Bit discarded a few seconds of buffered chunks. *(live, 2026-10-04)*
- [ ] Enforce `baseline` on `infra`. First keep Trivy's node-collector working: exempt its service account in the K3s Pod Security admission configuration during a maintenance restart, or disable infra assessment. Its job needs host PID and hostPath and always runs in `infra`.
- [ ] Introduce default-deny ingress in `infra` with explicit service/port allowlists for platform dependencies, app data, CI, ingress, metrics and remote gateways. Current source has only selected service policies. Prove required traffic and denied cross-environment/admin access before rollout; stage egress restrictions separately with DNS/API/external dependencies accounted for.
- [x] Registered environment namespaces carry the `security.<domain>/deny-hostpath` label like `apps` and `corp` (prod shares `apps`). *(live)*

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
- [x] Grafana **Ingress Traffic** dashboard (request rate, 5xx ratio, p95 latency per service, status classes, open connections) and `IngressServiceErrorRateHigh` (> 5 % 5xx for 15 minutes with steady traffic). *(live, 2026-10-04: queries and rule healthy)*
- [ ] No HSTS or security-header middleware at the origin; header policy relies entirely on Cloudflare and each app.
- [x] Traefik writes JSON access logs (client address, host, path, status, timing, user agent, Cloudflare IP and Ray); other request headers and query strings are dropped. Fluent Bit ships them to `kubernetes-logs-v2-*`. *(live, 2026-10-04: Helm release 4, no interruption, query-string probe not logged)*

### 8. Storage (Longhorn)

- [x] Longhorn 1.12.1 via `reconcile-platform-release.sh`, default StorageClass, topology-driven replica count. *(live: 20 healthy volumes)*
- [x] Weekly filesystem trim RecurringJob `swirl-cloud-weekly-trim` (Sunday 05:30, groups `default` and `swirl-cloud`, snapshots preserved).
- [x] Longhorn UI behind Keycloak SSO and Cloudflare Access through a platform-owned ExternalName Service.
- [x] HA storage policy: replica soft anti-affinity disabled, `node-down-pod-deletion-policy: do-nothing`, controller-manager `disable-force-detach-on-timeout=true` via `configure-ha-control-planes.sh`.
- [x] Optional recurring off-node Longhorn backups to the restic S3 bucket (`swirl-cloud-daily-backup`), host backup enrolls new volumes.
- [ ] Activate and restore-test off-node Longhorn backups (§16). The 2026-09-27 audit found no daily backup job or configured `default` BackupTarget; repeat that check before declaring coverage.
- [x] `configure-k3s-backups.sh` configures the `default` BackupTarget resource (URL, credential Secret, sync request) and fails unless Longhorn reports it available; the removed `backup-target` setting no longer gates volume backups. The patch passes a server-side dry run against Longhorn 1.12; end-to-end activation waits for the off-node target in §16. *(2026-10-04)*
- [x] Longhorn manager metrics are scraped (job `longhorn`) through the `longhorn-manager-metrics` NetworkPolicy.
- [x] `LonghornVolumeDegraded` (missing replicas or rebuilding, 15 min) and `LonghornVolumeFaulted` alerts. *(live)*
- [x] Snapshot deletion cleanup was recorded complete on 2026-09-28 in `9e736db`; retain that resolution as history. Current snapshot health was not rechecked in this source review.
- [x] `gitlab-runners/gitlab-runner-cache` is still used: the instance runner mounts it at `/cache` (`gitlab-runner.yaml`) and job pods attach it, so `unknown` robustness while detached between jobs is expected. Project runners use `emptyDir` caches. *(live, 2026-10-04)*

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
- [x] `VaultSealed` and `VaultAuditDeviceFailing` alerts. *(live)*
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
- [x] Every PVC, including PostgreSQL's, alerts at 20 % and 10 % free and when predicted to fill within a day (`PersistentVolumeSpaceLow/Critical`, `PersistentVolumeFillingWithinDay`). *(live, 2026-10-04: PostgreSQL 17 % used)*
- [ ] PostgreSQL PVC is only 2 Gi for Keycloak, SonarQube, Odoo, GitLab-adjacent and all application databases; plan its capacity with §26.
- [ ] Set PostgreSQL RPO/RTO and implement continuous WAL archiving/PITR if daily `pg_dumpall` is insufficient. Prove recovery to a chosen timestamp; HA replication does not protect against accidental data changes (§16).
- [ ] Kafka's in-cluster `PLAINTEXT` listener (9092) stays unauthenticated with `KAFKA_SUPER_USERS=User:ANONYMOUS` and `auto.create.topics.enable=true`. NetworkPolicy limits it to `infra`, `apps` and `corp`, so every legacy app and platform pod is an anonymous Kafka superuser. Migrate legacy clients to SCRAM and close 9092 to `apps`.
- [ ] Generate and persist a Kafka cluster ID on new installations; reuse the recorded ID on reruns and migrations. Never replace the hard-coded ID on an existing data directory.
- [ ] Decide whether Redis key-name visibility through `KEYS`/`SCAN` is acceptable for shared environments; document the boundary or use separate instances where metadata isolation is required.
- [x] Datastore exporters: PostgreSQL, MongoDB and Redis sidecars (including shared Redis HA), CloudNativePG's built-in endpoint, and a Kafka exporter Deployment (jobs `postgres`, `mongodb`, `redis`, `kafka`).
- [x] `DatastoreDown` alerts when the PostgreSQL (including CloudNativePG), MongoDB, Redis or Kafka exporter cannot reach its datastore. *(live)*
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
- [x] Keycloak metrics are scraped (`kubernetes-pods`); `KeycloakPasswordFailuresHigh` alerts on more than 20 rejected passwords per realm in 15 minutes. *(live, 2026-10-04)*
- [ ] Keycloak runs one replica outside HA (§15).

### 12. Source, CI and registry (GitLab + Runner)

- [x] GitLab CE 19.3.1 Omnibus (digest-pinned, amd64), `external_url https://gitlab.<domain>`, bundled NGINX on 80, Registry on 5050, Let's Encrypt off, KAS off, Puma 2 workers, Sidekiq 10, embedded Prometheus retention 1 d.
- [x] Visitor IP preservation: Rails `trusted_proxies` and bundled NGINX `real_ip_*` built from `ingress-proxy-trust`, IPv6 expanded to avoid YAML symbols.
- [x] Persistent `gitlab-config`, `gitlab-data` (expanded for registry/artifacts), `gitlab-logs` PVCs; Registry data survives restarts.
- [x] Dependency Proxy enabled; group/project provisioning, retention policies, instance runner registration and Vault-backed tokens reconciled by `configure-gitlab-ci.sh`.
- [x] GitLab Runner 19.3.1 (Kubernetes executor) in `gitlab-runners`, `concurrent = 1`, non-privileged, pinned helper image, per-job CPU/memory/ephemeral-storage limits, `if-not-present` pulls, drains jobs during rollout, metrics Service, Grafana "GitLab Delivery" dashboard.
- [x] Project-scoped integration and protected-release runners for version-2 apps with scoped Argo CD permissions (`application_delivery.py`).
- [x] Daily `gitlab-registry-retention` CronJob (03:17 UTC): container cleanup policies and package pruning older than `RETENTION_DAYS` (default 1 095 days); protected tags and `latest` kept.
- [x] Package retention skips projects with `packages_enabled=false` (`6ada82b`), preserving container cleanup without a failing packages API call.
- [x] Alertmanager webhook into the infrastructure project's **Monitor → Alerts**.
- [x] Platform pipeline `.gitlab-ci.yml`: `validate:repository`; on default branch `verify:gitops` (same SHA Synced/Healthy), `verify:delivery-platform` (GitLab, Prometheus, Grafana, Trivy, Elasticsearch, Registry 401, dashboards, log ingestion) and `verify:registry-write` (OCI blob/manifest push/read). Alpine image digest-pinned, apk cache.
- [ ] Automate GitLab native backup plus matching configuration, secrets, registry files and metadata DB where enabled; upload offsite and restore with the matching GitLab version. Verify repository, LFS, package and Registry push/pull recovery, then integrate backup age/failure alerts (§16).
- [ ] Identify the installed Registry metadata mode and implement its supported garbage collection: legacy collection with writes stopped/read-only, or monitored online GC with the metadata DB. Protect deployed and rollback digests before tag pruning, test pullability after cleanup, and measure reclaimed bytes; see [GitLab GC](https://docs.gitlab.com/administration/packages/container_registry/#container-registry-garbage-collection).
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
- [x] Argo CD's Helm release and recovery ownership are documented in [operations](docs/operations.md#argo-cd-itself-is-unhealthy-or-lost); the live release (`argo-cd-10.9.2`, image `v3.5.3` digest) matches the pinned contract, and both the installer and `ansible/deploy.yml` reconcile it through `reconcile-platform-release.sh argocd`. Self-management stays a separate decision. *(live, 2026-10-04)*
- [x] Root `swirl-cloud` and all eight application Applications are `Synced/Healthy` at their current revisions; every platform CronJob's latest run succeeded (Sonar discovery, Kibana user sync, Registry retention, Longhorn trim), and default-branch pipelines pass `verify:gitops` and `verify:delivery-platform`. Remaining failed Job records are superseded; the 2026-10-03 20:33–20:35 failures coincide with a transient in-cluster DNS error (`EAI_AGAIN`). Thoughty's `apps/postgres-backup` is suspended by its own overlay; `pg_dumpall` runs in the platform backup. *(live, 2026-10-04)*
- [x] Degraded health and failed sync now alert through Prometheus (`ArgoCDApplicationUnhealthy`, `ArgoCDApplicationOutOfSync`) to Alertmanager. The notifications controller stays unconfigured until an email or chat receiver exists (§27, §29). *(live, 2026-10-04: rules healthy)*
- [x] Argo CD metrics are scraped (job `argocd`).
- [ ] Exercise Argo CD controller replacement and reconciliation recovery under the configured dynamic cluster distribution; record its upstream support status and failover result before claiming HA (§15).

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
- [x] Fluent Bit keeps Kubernetes metadata for CI job logs (256 KB filter buffer for large `gitlab-runners` pod specs) and no longer indexes Trivy scan pods' compressed report payloads. Over 15 minutes with 16 scans, no container record lacked metadata and 1 112 runner records were enriched. *(live, 2026-10-04)*
- [x] Host metrics through node-exporter's textfile collector (`/var/lib/node_exporter/textfile_collector`): Lynis run state, pending reboots and rkhunter warnings, with `LynisAuditFailed`, `LynisAuditMissing` and `NodeRebootRequired`. *(live, 2026-10-04)*
- [x] Elasticsearch is green again: the Kibana user sync now reconciles its ownership index to zero replicas (created with one before 2026-09-28), and the two legacy `.reindexed-v8-kibana-event-log-*` indices were set to zero replicas once by hand. *(live, 2026-10-04)*
- [x] Kibana's **Lynis Findings** search queried application-log fields and matched nothing; it now selects `lynis.finding_type : *` (1 368 findings). *(live, 2026-10-04)*
- [x] Platform alerts: node NotReady, Vault sealed/audit failure, Longhorn degraded/faulted, datastore down, Argo CD application unhealthy or out of sync for 30 min, and TLS certificate expiry (14 days warning, 3 days critical). *(live)*
- [x] `swirl-cloud-k3s-backup.service` (K3s, Vault and application archives) and `swirl-cloud-vault-unseal.service` record each run through an `ExecStopPost` drop-in; `HostMaintenanceServiceFailed`, `K3sRecoveryBackupMissing` (36 h) and `VaultUnsealTimerStopped` alert on them. *(live: unseal metrics and failure path verified, rules healthy, 2026-10-04)*
- [ ] After the next scheduled backup (~02:20 UTC), confirm `swirl_cloud_systemd_unit_last_success_timestamp_seconds{unit="swirl-cloud-k3s-backup.service"}` and that the archive contains `application-data/odoo-filestore.tar.gz`. No extra run was forced because local archive retention is count-based.
- [x] External Secrets is scraped (job `external-secrets`); `ExternalSecretNotReady` fires after 15 minutes. *(live, 2026-10-04: pending immediately for the two orphaned `platform-registry-auth` ExternalSecrets below)*
- [ ] Remove the orphaned `platform-registry-auth` ExternalSecrets in `kube-system` and `longhorn-system`, and the `imagePullSecrets` references to that Secret on CoreDNS, metrics-server, local-path-provisioner, the Longhorn CSI sidecars and the Longhorn UI. They were dropped from Git in the upstream-image migration (`ef35d1c`) but never pruned, and have failed since 2026-09-24 because `vault-backend` refuses those namespaces. All images are public upstream images; confirm and approve before deleting live resources.
- [x] Elasticsearch is scraped through `elasticsearch-exporter` with the monitor-only `metrics_monitor` user (Vault `infra/elasticsearch`; document reads are denied); `ElasticsearchUnreachable`, `ElasticsearchClusterRed` and `ElasticsearchClusterYellow` alert on it. *(live, 2026-10-04: green, rules healthy)*
- [x] `LonghornVolumeBackupStale` alerts on any volume without an off-node backup in 36 hours once the backup target holds backups (silent before activation). *(2026-10-04)*
- [x] CrowdSec/Fail2ban activity alerts: `swirl-cloud-intrusion-metrics.timer` (host hardening) exports service state, bans and decisions; `HostIntrusionPreventionDown`, `HostIntrusionMetricsStale` and `HostBruteForceWave` (rules and exporter output checked with `promtool` against live counts, 2026-10-04). Live on `swirlit-node-01` since 2026-10-04.
- [x] Trivy critical findings alert with a baseline: `TrivyNewCriticalVulnerability` fires only when an image's critical count exceeds its own recent maximum or a new image arrives with criticals (0 series fire on 2026-10-04; all 14 affected images would fire on one more finding). The 6 h baseline fits today's size-bound Prometheus retention (about 12 h); widen it with §26.
- [x] Scraped: Traefik, Vault, Argo CD, Longhorn, PostgreSQL, MongoDB, Redis and Kafka.
- [ ] Not scraped: cloudflared (only runs in HA Tunnel mode).
- [ ] Define remote-cluster telemetry ownership and add authenticated metrics/log shipping, cluster/environment labels, quotas and node/backup alerts before relying on remote production targets (§19).
- [ ] Define recoverable Elasticsearch log/audit retention and configure a snapshot repository if that history must survive volume loss (§16).
- [ ] Add an external availability check and notification path that still works when the only cluster or GitLab is down. Test a synthetic failure and recovery notification; SMTP/chat routing (§27, §29) alone does not monitor an unavailable cluster.

The historical Trivy snapshot was **119 critical / 924 high findings across 90
image reports**, plus 11 critical exposed-secret findings in the GitLab image
and stale retired-image reports. Refresh and triage these in §21; they are not
current totals or three additional remediation projects.

### 15. High availability (opt-in, not active)

- [x] Durable availability mode in `infra/swirl-cloud-topology`; installer, Ansible and enrollment inherit it and refuse implicit downgrades or unfinished migrations.
- [x] HA templates: CoreDNS three replicas with admission-enforced placement and PDB, Keycloak PDB, Redis HA policies, PostgreSQL HA cluster, Kafka HA PDBs/startup, Traefik/Tunnel DaemonSet, Vault three voters, ESO two replicas, Argo CD HA.
- [x] `verify-high-availability.py` read-only readiness checker (placement, quorum, storage copies, optional fencing coverage).
- [x] Optional Redfish node fencing CronJob (every minute, one host per run, stale Lease + NotReady ≥ 5 min, quorum rechecks, `ForceOff`, then `node.kubernetes.io/out-of-service` taint), inventory Secret, `--check-inventory` mode, RBAC limited to named nodes.
- [ ] Complete the topology and capacity prerequisites in §25: at least three eligible control planes and three storage hosts, with capacity after one host fails. The planned two-storage-host interim state cannot satisfy the current three-replica HA profile.
- [ ] Execute the ordered activation: PostgreSQL migration → Kafka migration → Vault migration and recovery distribution → shared HA profile with Cloudflare Tunnel cutover → application-owned availability.
- [ ] Run the controlled one-host failure drills (original control plane, database leader, consensus leader) and verify public requests, sessions, writes, Kafka, Vault and Registry push/pull.
- [ ] Acquire Redfish-capable BMC access and enable fencing, or document the manual non-graceful shutdown runbook as the permanent path.
- [ ] Application clusters: bootstrap supports only one control plane per target.

### 16. Backup and disaster recovery

- [x] Daily `swirl-cloud-k3s-backup.timer` (02:15 UTC ± 15 min), root-only archives in `/var/backups/swirl-cloud/k3s`, 7 local copies, SHA-256 files, lock, staging dir. *(live: last run succeeded 2026-09-27)*
- [x] Archive contents: SQLite `.backup` or etcd snapshot, server token, encryption config/state, K3s config and drop-ins, registries.yaml, Vault Raft snapshot, `pg_dumpall`, `mongodump`, PVC and Longhorn volume inventories, datastore-specific `RESTORE.txt`. *(live: verified in the 2026-09-27 archive)*
- [x] Optional restic to S3-compatible storage (encrypted, `keep-daily 7 / weekly 5 / monthly 12`, prune) with root-only `/etc/swirl-cloud/backup.env`.
- [x] Recovery tests: SQLite/etcd restorability, recovery token presence (`test-k3s-backups.sh`), Vault unseal/HA, PostgreSQL SQL canary.
- [ ] Configure offsite restic and Longhorn storage now using a target outside the cluster's host/site failure domain; do not wait for the future NAS (§31). Verify uploads, backup age and volume coverage after fixing the Longhorn integration (§8).
- [ ] **Operator action:** on `swirlit-node-01`, export the recovery kit (`scripts/export-recovery-kit.sh --separate-archives`), store the kit and its passphrase off the host in separate places, delete the local copy, and confirm with `--verify FILE` ([checklist](docs/operations.md#operator-recovery-checklist)). Repeat after any Vault, K3s or backup credential rotation.
- [ ] Separate Vault recovery keys and bootstrap tokens from routine data archives and keep an independently accessible recovery copy of the restic password, K3s token/encryption state and required credentials. Define automated-unseal and bootstrap-token privileges explicitly; a password manager hosted only on this cluster is not sufficient for recovery. *Progress 2026-10-04:* `scripts/export-recovery-kit.sh` exports an encrypted, self-verified kit (Vault keys, K3s token/encryption state, restic and S3 credentials) and `--verify` checks it; with `--separate-archives` nightly archives omit the Vault keys while they match the kit (tested). Remaining: export a kit for `swirlit-node-01`, store it offline, opt into separation, and define automated unseal.
- [x] Define a backup coverage/RPO/RTO table: K3s state, Vault, PostgreSQL, MongoDB, GitLab/Registry, Odoo database plus filestore, SonarQube, dashboards, logs/audits and Kafka/Redis data. Mark rebuildable caches explicitly; coordinate database/filestore recovery points and assign native backup or volume backup per service. Table in docs/operations.md (Coverage and recovery points), checked against the 2026-10-04 archive; RTO stays unmeasured until the restore drill below.
- [ ] Schedule `restic check` and isolated restore drills for both archives and Longhorn volumes, with integrity/data checks and alerts on missing or stale backups. Keep deletion protection compatible with restic pruning and Longhorn's own backup retention; do not apply generic object expiry to active backup stores.
- [ ] Write and rehearse a full rebuild on an empty host with the original host unavailable: obtain Git, pinned recovery images and keys independently; restore K3s/storage/Vault/data, then GitLab/Argo CD, SSO and application traffic. Measure achieved RPO/RTO and exercise the documented backup format before node migration.

### 17. Corporate workloads (Odoo in `corp`)

- [x] Odoo 19 (digest-pinned) in `corp` behind `INSTALL_SCOPE=apps` / `appsEnabled`, `dbfilter ^odoo$`, `list_db = False`, `proxy_mode`, 2 workers, memory/time limits, body-limit middleware, allow-ingress NetworkPolicy, Longhorn filestore PVC.
- [x] Bootstrap creates the database/role on PostgreSQL, main company named from the organization, managed administrator.
- [x] Keycloak SSO through the bundled `auth_oauth` module plus a managed `odoo-keycloak-sso` add-on; vendored OCA `web_dark_mode` 19.0.1.0.0 (AGPL, provenance), enabled once for the administrator.
- [x] Migrated from `infra` to `corp` with documented volume migration (`0e16c84`).
- [ ] No outgoing mail server configured, so Odoo cannot send quotes, invoices or password resets.
- [x] The nightly recovery archive includes the Odoo filestore (`application-data/odoo-filestore.tar.gz`, taken after `pg_dumpall` so every referenced attachment is present; 21 MB today). It leaves the host with the archive once the §16 off-node target exists. *(installed 2026-10-04; first archive with it on 2026-10-05)*

### 18. Administration UIs and developer portal

- [x] Homepage v2.2.0 at `cloud.<domain>` (2 replicas, PDB), `<Company> Cloud` title, behind Keycloak SSO, cluster-mode Kubernetes widgets, Ingress discovery for app entries, groups Applications - PROD / UAT / INT / Corporate / DevOps & Delivery / Cluster Management / Data & Messaging / Observability & Logging / Security & Identity / Kubernetes System, retired `dashboard` host.
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
- [ ] Refresh the app/contract/environment inventory, then migrate remaining version-1 apps in their owning repositories, including data, credentials, DNS and rollback. Verify isolated `int`/`uat`/`prod` delivery before retiring legacy credentials or Kafka access (§10).
- [x] GitLab is documented as the delivery and metadata authority and GitHub as a branches-and-tags mirror ([repository onboarding](docs/guides/repository-onboarding.md#synchronization-and-validation)); cross-forge metadata sync is added only for a concrete requirement. *(2026-10-04)*
- [ ] Back up issues, merge requests, releases, packages and LFS through native GitLab recovery (§12).
- [ ] Deliver the shared SMTP capability in §27 and its app onboarding contract; application repositories own their mail usage and templates.
- [x] Environment `ingressAddress` accepts hostnames, and `configure-application-dns.py` already selects A, AAAA or CNAME records. Remaining ingress alias and API endpoint work is in §25.

---

## Part III — Requested roadmap (added 2026-09-27, reviewed 2026-09-28)

The requested direction is retained below. Sub-steps now distinguish existing
source support, historical live findings, prerequisites and decisions still open.
Decisions marked **ADR** need a record in [`docs/adr/`](docs/adr/) first (see the
proposed list in [architecture](docs/architecture.md#proposed-adrs-for-roadmap-work)).

- [ ] Keep services versions updated *(perpetual; never marked done)*.

### 20. Remediate Lynis findings (light-touch, non-blocking)

Historical control-plane audit on 2026-09-27: hardening index **66**, one warning, about 40
suggestions. Apply through [`configure-node-security.sh`](scripts/configure-node-security.sh)
so new nodes inherit the result, then rerun Lynis and `swirl-cloud-audit-nodes`.

- [x] Lynis refreshed through `swirl-cloud-lynis.service`: hardening index **66**, one warning (KRNL-5830; `linux-image-7.0.0-38` installed 2026-10-03, running 7.0.0-34). Every finding below still applies; DEB-0810, FIRE-4513, LOGG-2190, NAME-4028 and BOOT-5180/5264 are informational. *(live, 2026-10-04)*
- [ ] **KRNL-5830 (warning):** if still pending, schedule a maintenance-window reboot after verifying the backup, off-host recovery material and console access (§16). Check Vault unseal, volumes, SSO and applications after boot; add coordinated reboots only after multi-host recovery is proven.
- [x] **SSH-7408:** `ClientAliveInterval 300`, `ClientAliveCountMax 2`, `LogLevel VERBOSE`, `MaxSessions 4`, `TCPKeepAlive no` in `41-swirl-cloud-sessions.conf` on every node, validated with `sshd -t` and reloaded. Lynis still prefers `MaxSessions 2` and a non-22 port; both are kept by design. *(live, 2026-10-04)*
- [x] **BANN-7126/7130:** legal banner in `/etc/issue` and `/etc/issue.net` through the new firewall-independent `configure-node-security.sh --host-baseline`, also part of every full `--apply`. *(live, Lynis BANN tests pass, 2026-10-04)*
- [x] **DEB-0280, DEB-0811, PKGS-7370, PKGS-7394:** `libpam-tmpdir`, `apt-listchanges` (text frontend), `debsums` (weekly, report-only to the journal) and `apt-show-versions` in the host baseline. *(live, Lynis tests pass, 2026-10-04)*
- [x] **AUTH-9262:** `pam_pwquality` (12 characters, dictionary and user-name checks) applies to new passwords through the host baseline; login PAM stacks are unchanged. *(live, Lynis AUTH-9262 passes, 2026-10-04)*
- [x] **AUTH-9230 / AUTH-9328 reviewed, accepted:** the host uses yescrypt (`ENCRYPT_METHOD YESCRYPT`), stronger than the SHA-512 rounds Lynis checks for. `UMASK 027` would also apply to sudo and cron sessions and make workspace files group-only, breaking image `COPY` for non-root containers; user-private groups (`USERGROUPS_ENAB yes`) already isolate users. Record both in the Lynis profile below.
- [ ] **AUTH-9282/9286:** password ageing only for human accounts, never for service accounts (optional once SSH is key-only).
- [x] **NETW-3200:** `dccp`, `rds`, `sctp` and `tipc` are blocked through `/etc/modprobe.d/swirl-cloud-unused-protocols.conf`; none were loaded and no Service or NetworkPolicy uses SCTP. Lynis NETW-3200 passes. *(live, 2026-10-04)*
- [ ] **USB-1000:** `usb-storage` stays enabled because remote consoles attach recovery media as USB. Blacklist it only on hosts whose console recovery is proven not to need it, and record that as an accepted Lynis finding (§20 profile).
- [x] **PKGS-7346:** the host baseline purges configuration left by removed kernel packages (30 on this host); the remaining `keyboxd`, `libpython3.12-minimal` and `pollinate` leftovers were purged once by hand. No `rc` packages remain. *(live, 2026-10-04)*
- [x] **KRNL-6000:** `/etc/sysctl.d/90-swirl-cloud-hardening.conf` sets `kptr_restrict=2`, `dmesg_restrict=1`, `unprivileged_bpf_disabled=1`, `bpf_jit_harden=2`, `fs.protected_*` and `send_redirects=0`; pod DNS/egress/API smoke test passed. Kept by design: `ip_forward`, `rp_filter=2`, `modules_disabled`, `sysrq` (console recovery), `suid_dumpable` (apport) and `log_martians` (each new pod sandbox logs a benign martian). *(live, 2026-10-04)*
- [x] **FILE-7524:** `/etc/crontab` and `/etc/ssh/sshd_config` are `0600`, the `/etc/cron.*` directories `0700`, with `dpkg-statoverride` keeping packaged paths restricted across upgrades. Lynis FILE-7524 passes. *(live, 2026-10-04)*
- [x] **ACCT-9622/9628:** `acct` (14 days) and `auditd` with a watch-style rule set for accounts, sudoers, SSH, cron, systemd units, K3s/Swirl Cloud config, UFW, time zone and module loads. BPF records and the Ubuntu `who` AppArmor false positive are excluded; measured ≈59 MB/day within an 8 × 32 MB rotation, no lost events. Lynis ACCT tests pass. *(live, 2026-10-04)*
- [x] **FINT-4350:** weekly report-only AIDE through the packaged `dailyaidecheck` units (no mail-server Recommends). Cluster, storage, container, log, backup, `/run` and temporary paths are excluded; in home directories only `~/.ssh` and login shell files are monitored. Package updates are filtered out, each change is reported once to the journal (`aide-report`), and run failures alert through `HostMaintenanceServiceFailed`. Initial database ≈9 min at idle I/O; a planted `/etc` file was reported. Baseline reruns no longer rewrite unchanged files, so they do not show up as changes. *(live, 2026-10-04)*
- [x] **HRDN-7230:** weekly report-only `rkhunter` (`swirl-cloud-rkhunter.timer`, installed without the mail-server Recommends), DPKG-backed property checks refreshed after APT runs, four reasoned whitelist entries, warnings to the journal and `swirl_cloud_rkhunter_warnings`. Clean baseline and a hidden-file negative test passed. Kibana shipping follows journal forwarding (LOGG-2154). *(live, 2026-10-04)*
- [x] **LOGG-2154:** Fluent Bit forwards each node's journal from its tail to `kubernetes-logs-host-*` (`log_source=host-journal`, own template, one-day ILM); recorded as accepted in the Lynis profile because Lynis only detects syslog forwarding. Elasticsearch is in-cluster, so an off-host copy depends on the snapshot repository in §14/§16. *(live, 2026-10-04)*
- [x] Accepted findings are recorded in the managed `/etc/lynis/custom.prf` (owner, review date 2027-04-04, rationale per entry), skipping single checks or values only: AUTH-9230, AUTH-9328, BOOT-5122, FILE-6310, USB-1000, SSH-7408 port/MaxSessions and the KRNL-6000 values Kubernetes or console recovery need. Raw reports stay in `/var/log/lynis-reports`. Hardening index 66 → **75**; remaining suggestions match the open items above. *(live, 2026-10-04)*

Deliberately not planned (too disruptive for the benefit): GRUB password
(BOOT-5122, breaks remote console recovery), separate `/home` and `/var`
partitions (FILE-6310, requires reinstalling), moving SSH off port 22 (low value,
breaks tooling).

### 21. Remediate Trivy findings (critical, exploitable, low-effort)

Scope: critical, realistically exploitable findings first; prefer a newer
upstream image that contains the fix. Where no upstream fix exists, assess
exposure and mitigations and record the residual risk; allow small configuration
changes, no private rebuilds
([ADR 0022](docs/adr/0022-upstream-images-by-digest.md)). Start with
network-facing components (Keycloak, Argo CD server, GitLab, Traefik).

The following are candidates from the historical scan, not verified current
advisories or proof that a fixed service image has been published. Package fixes
and availability of a compatible upstream image must be checked separately.

- [x] Critical findings refreshed for the running digests (reports updated 2026-10-03/04), deduplicated by image and checked for fixed upstream releases; dated exceptions (expiring 2026-11-04) are in [security](docs/security.md#vulnerability-triage). Keycloak 26.7.5 scans with no critical findings. *(live, 2026-10-04)*
- [x] Argo CD v3.5.3 (latest 3.5 patch) still reports Go `crypto/tls` CVE-2025-68121; recorded as a dated exception. Recheck when a patch built with Go ≥ 1.25.7 ships. *(2026-10-04)*
- [ ] Keycloak, SonarQube and Kafbat UI — reported Netty/Bouncy Castle/FreeMarker findings. Keycloak upgraded 26.7.4 → 26.7.5 *(live, 2026-10-04: 0 critical / 7 high, was 4 / 19; pre-upgrade database dump in `/var/backups/swirl-cloud/pre-change/`)*. Kafka UI v1.5.0 is the latest release (exception). SonarQube 26.9.0 (latest Community) reports Netty and Bouncy Castle findings (exception).
- [x] Elasticsearch, Kibana and Logstash 9.5.4 are the latest 9.5 patch; their Netty SNI, `piscina` and Bouncy Castle findings are recorded as dated exceptions. *(2026-10-04)*
- [x] GitLab Runner 19.4.1 no longer reports OpenSSH findings. The helper's Alpine `miniperl` critical waits for the next runner patch (dated exception). *(2026-10-04)*
- [ ] Longhorn `csi-provisioner` — reported gRPC/Go findings; validate the Longhorn/Kubernetes sidecar compatibility matrix before adding an admission image override.
- [x] MongoDB tools, Node helper, Homepage and DBGate assessed: the Node helper (`node:24.21.0-alpine`) has no critical findings; MongoDB's bundled Go tools, Homepage (no `next/og` code in its server bundle) and DBGate's Debian packages are dated exceptions with reachability notes in [security](docs/security.md#vulnerability-triage). *(2026-10-04)*
- [x] PostgreSQL/DBGate Debian-library findings checked against the Debian security tracker (ignored, postponed or no-DSA minor issues; one Perl CVE predates bookworm's Perl) and runtime use; accepted with evidence in [security](docs/security.md#vulnerability-triage) until 2026-11-04. *(2026-10-04)*
- [ ] Triage the historical GitLab exposed-secret findings; distinguish fixtures from usable credentials, rotate any real credentials, and narrowly scope exceptions with evidence and expiry.
- [x] No stale reports for retired owners remain: all Trivy Operator reports carry owner references and Kubernetes garbage-collects them with their workloads; none reference the retired `security/` images. *(live, 2026-10-03)*
- [x] Non-blocking CI job `report:platform-artifacts` scans the vendored chart archives (embedded misconfiguration checks against chart defaults; the rendered manifests are audited live by Trivy Operator) and the pinned CI Alpine image through the in-cluster Trivy server; reports are kept 30 days as job artifacts. The GitHub workflow uses no third-party actions. *(pipeline 2026-10-04 passed)*
- [ ] Hand application image findings to the owning repositories (2026-10-04 scan): TravelMapster `1.8.1` has 4 critical Debian bookworm findings (Perl, zlib minizip, the same accepted classes as PostgreSQL); `postgres:18.6-bookworm` is used by Thoughty's `postgres-backup` CronJob and Indezy's `wait-for-db` init container. No other application image has critical findings. Not yet recorded in those repositories; TravelMapster's checkout has an unpushed local commit, so it was left untouched.

### 22. Odoo API for applications (CRM leads and Live Chat)

Applications own their UI changes in their own repositories; the platform owns
Odoo and the wiring. CRM and Live Chat serve only the company website, so this
section waits until the website is linked to Odoo; scope it to the website then.
A generic implementation (CRM intake add-on, scoped keys, sandbox company, Live
Chat, rate limit) is parked on the local branch `park/odoo-crm-livechat`.

- [ ] Install and configure Odoo `crm` (contact forms become CRM leads) and `im_livechat` (support chat) in the Odoo bootstrap.
- [ ] Choose and verify the API supported by the pinned self-hosted Odoo build. Create a dedicated integration user per application/environment with model ACLs and record rules for the intended lead/chat operations; API keys inherit user permissions ([Odoo API access rights](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html#access-rights)). Store rotating keys in Vault under the existing app/env contract and keep them out of browsers.
- [ ] Permit local app→Odoo traffic on 8069 in both `corp` ingress and environment egress policies; the current environment egress allowlist does not include `corp`. Define a separate authenticated path for remote app clusters rather than assuming cross-cluster Service DNS works.
- [ ] Verify the public Live Chat loader and session/WebSocket flow (existing `/websocket` route to 8072), approved origins and edge rate limits. Preserve staff authentication while keeping required public chat endpoints usable without Access.
- [ ] Document fields, errors, retry/idempotency behavior, consent/retention and a sandbox integration. Prove duplicate retries create one lead and non-production apps cannot modify production CRM data; application repositories own forms and chat UI.

### 23. Installer: TUI, central `.env`, reruns, verification and email

Goal: one versioned `.env` drives every entry point (TUI now, web UI next,
Ansible and unattended runs); the installer engine reads only that file and never
prompts. **ADR** on the file schema and TUI technology.

- [x] Define a versioned installation-input schema covering every current input, with types, defaults, conditional requirements and secret flags. Keep `config/platform.env` as the checked-in version/service contract; parse per-install `.env` values as data, never shell code. Reference structured topology/environment files rather than flattening arbitrary YAML into shell strings.
- [x] Split `install-control-plane.sh` into an engine (`--env-file`, no prompts, reports every missing or invalid key at once) and a front end. The engine is the installer's `--env-file` mode (`--validate-only` stops after the report); it also adds the decision inputs `--yes` could not set before (`INSTALL_PREREQS`, `INSTALL_K3S`, components). Registrar nameserver delegation in `configure-cloudflare.sh` still needs a person.
- [x] Build the TUI front end (for example `whiptail`/`dialog`, available on Ubuntu without extra runtimes) that walks every question of the current installer and writes the `.env` (mode `0600`). `scripts/install-tui.py` (Python 3 + `whiptail`, ADR 0026); reruns load the file as defaults.
- [x] Start with an **express** screen proposing a default configuration and listing what it installs: every platform service, no application repositories unless provided. It asks only for company name and domain, administrator credentials, required API keys (Cloudflare; Tailscale/OVH for multiple nodes; optional S3 backups) and optional GitHub repositories to import.
- [x] Keep an **advanced** path with every current prompt grouped as today.
- [ ] Define secret resolution before adding redaction: fresh bootstrap and disaster recovery must work while Vault/Keycloak are unavailable. Support protected files/stdin and authenticated Vault references, redact logs/reports, and replace inline secrets only after durable recovery and rerun access are verified (§16); never require a secret from the Vault being bootstrapped. *Progress 2026-10-04:* `NAME_FILE=/path` references to protected one-line files (validated and resolved at import, no Vault needed) and redaction of entered secrets in hub run logs, and `--secrets-stdin` for secrets piped from a password manager or CI. Vault references for reruns remain.
- [x] Build rerun planning on the existing identity/topology/HA readers and reconciliation helpers. Show installed/missing/drifted/failed stages, reconcile selected drift, preserve explicit values and resume interrupted work. Keep repository onboarding selectable on every run without starting it unexpectedly. `./install.sh --plan` and the hub's Plan screen (`scripts/lib/install_plan.py`) compare Helm charts and K3s with the pins, node counts with the plan, workload readiness, Cloudflare/backup selection and installed identity with the file, and show the last run; reruns resume because every stage is idempotent, and components are selected under Advanced.
- [x] Add bounded post-install verification: current nodes/pods/ExternalSecrets ready, expected Argo revisions healthy, Vault unsealed, authenticated service/Ingress probes, TLS validity and successful recent backups. Treat intentional login redirects/401s correctly and check backup results, not just active timers. Include `validate-repository.sh --live` for API compatibility and emit a readable nonzero failure report. `scripts/verify-platform.py` (`./install.sh --status`, `--strict`, `--json`, `--api-dry-run`) runs at the end of every installer run; first live run 2026-10-04: 7 PASS, backup WARN (no success metric yet), FAIL for the two orphaned `platform-registry-auth` ExternalSecrets (§14).
- [ ] Email the report when the deployment is ready, with dashboard URLs for every service and application (from Ingress hosts and Homepage). Depends on the SMTP relay in §27.
- [x] One entry point: `./install.sh` opens the setup hub (installs `whiptail`/`python3` when missing), runs the engine with `--env-file`, and verifies with `--status`. A bare interactive `install-control-plane.sh` opens the hub; `--prompts` keeps plain prompts.
- [x] Setup hub: overview of what gets installed and the current state, host preflight (OS, sudo, CPU, memory, disk, ports, clock, K3s role, egress, domain), live Cloudflare/GitHub/Tailscale credential tests, strong password generation shown only from a private file, install with a private log and verification report, add servers, import repositories, service links, and installed-identity defaults on existing hosts. Maintenance menu: backup or Vault unseal now, local administrator password rotation (stdin only), Lynis host audit, API dry-run, failed units and earlier run logs. Verification prints a `Next:` command for each problem; `./install.sh --support-bundle` (also in Maintenance) collects a redacted troubleshooting archive without reading Secrets.
- [x] Make `ansible/install.yml` and `--yes` consume the same `.env`. `--env-file` runs unattended; Ansible takes `installer_env_file`.
- [ ] After the engine/TUI is proven, design the optional Keycloak-protected web front end around constrained jobs, authorization, redacted logs and a single execution lock. Keep console-based bootstrap/recovery independent of that UI.
- [ ] **Operator action:** run `./install.sh` in a real terminal on `swirlit-node-01` and check every hub screen renders and navigates (so far only driven with scripted dialog answers; see [after installation](docs/deployment.md#after-installation)).
- [ ] Rehearse fresh install, no-change rerun, interrupted resume, invalid inputs and Vault-unavailable recovery on disposable hosts (the input file and TUI have only offline tests and scripted dialog runs so far); add focused safety tests only for credential leakage or deployment/recovery failures under the existing test policy.

### 24. Deployment UI (app → environment → deploy)

- [ ] Small web app in `infra` behind Keycloak and Cloudflare Access, listed on Homepage, added to the host inventories. (The terminal flow above covers the deployment logic; the web front end remains.)
- [x] List onboarded applications (Argo CD Applications or GitLab projects with an onboarding contract). `scripts/deploy-application.py list` and the hub's Deploy screen show each environment-aware application's revision, health and URL per environment.
- [x] Derive valid choices from the app's onboarding contract intersected with `infra/deployment-environments`; the inventory records cluster targets, not a per-app release catalog.
- [x] **Deploy** uses the owning app's pipeline contract (`PIPELINE_MODE=full` and `DEPLOYMENT_ENVIRONMENT` where supported), a server-side scoped Vault token and the permitted branch/tag. Resolve and display the immutable release being promoted to `uat`/`prod`; honor protected-environment approvals and prevent duplicate/conflicting runs. Version-1 apps retain their declared pipeline until §19 migration. Implemented in the hub and CLI: commit or release shown before starting, one active deployment per environment, manual release gates only after confirmation; it uses a per-run one-day GitLab token that is revoked afterwards instead of a Vault-scoped token (revisit with the web front end).
- [x] Show pipeline progress and report success only after the selected Argo revision is healthy and the target rollout is verified; show the app URL from its owned Ingress. Keep configuration editing out of this UI. Verified end to end on 2026-10-04: `main` to `int` through pipeline 723, `swirl-demo-app-int` moved 7d715c00 → 49f206ce, Synced and Healthy, site answering 200.
- [ ] Restrict to `platform-admins` initially; post results to Mattermost (§29).

### 25. Expand onto new EPYC servers and move workloads off the current node

Target hardware: AMD EPYC ≥ 16 cores, 128 GB RAM, ≥ 1 TB NVMe, public and private
bandwidth (for example OVHcloud Advance/Scale with vRack). The current server
stays as a lightweight control plane; no replacement migration is needed.

- [ ] Confirm cost, location, private connectivity, console/BMC access and capacity after one failure; then provision two servers first and the third as a separate expansion stage.
- [ ] **ADR — transport:** the repository supports one transport per cluster (`K3S_NODE_TRANSPORT`), and the current server has only a public interface and uses Tailscale. Either run all nodes over Tailscale (works today), or add mixed-transport support (vRack between new servers, Tailscale to the current one), which needs per-node addresses reachable by every node for flannel, etcd and Longhorn.
- [ ] Before starting: complete a restore drill (§16), take a fresh verified backup, and benchmark etcd fsync latency and inter-node RTT/throughput using disposable test files. Budget Longhorn rebuild traffic, database copies and concurrent CI within the chosen transport.
- [ ] **Phase 1 — three etcd members:** `./add-node.sh --role control-plane --mode remote --count 2` converts the current SQLite datastore to embedded etcd (with its pre-conversion backup) and joins both new servers.
- [ ] Add durable **per-node** workload/storage eligibility to enrollment, topology reconciliation, Helm placement and HA validation. Keep the current server lightweight while the new control planes remain schedulable; audit Vault/ingress tolerations and node-local audit PVCs so the old host does not silently keep heavy workloads.
- [ ] Define the two-storage-node interim replica policy before eviction. Build and verify healthy copies on the new disks, then disable old-disk scheduling and evict its replicas in order; do not enable the three-storage-host HA profile yet.
- [ ] Move workloads with an explicit drain/restart plan, singleton maintenance windows, PDB handling and verified volume attachment on the destination. `NoSchedule` does not move existing pods, and replica eviction alone does not relocate a workload. Rebalance only after data checks and healthy storage copies.
- [ ] Ingress: direct Traefik is pinned to the bootstrap node (`svccontroller.k3s.cattle.io/enablelb`) and every DNS record points to its public IP. Either keep ingress there, move the label and update platform and application DNS, or activate Tunnel ingress (§15).
- [ ] **Public ingress alias:** extend the platform configurator and legacy onboarding path to use one origin hostname or the HA tunnel target, preserving DNS ownership, apex behavior, TLS coverage and origin firewall rules. Reuse existing environment hostname/CNAME support (§19); rehearse explicit A→CNAME migration because onboarding rejects conflicting records.
- [ ] **Private API endpoint:** choose a reachable private DNS name plus a health-checked load balancer/VIP or documented manual failover, add K3s TLS SANs, and update join/kubeconfig consumers. Keep API endpoint failover separate from public Cloudflare ingress; prove access when the original server is down.
- [ ] Distribute Vault recovery material to the new control planes (`sync-vault-recovery.sh`) and install the unseal and backup timers there.
- [ ] Apply host security to the new servers' public interfaces (UFW, Fail2ban, CrowdSec) and confirm only the private transport carries cluster traffic.
- [ ] **Before phase 2, ADR and rehearsal:** extend odd-quorum enrollment guards, topology, HA verification, recovery distribution and backups to distinguish etcd members, API servers and eligible workload/storage hosts. Specify a supported member replacement/rejoin sequence that preserves healthy quorum at every step; current helpers reject a four-control-plane target.
- [ ] **Phase 2 — three new etcd hosts plus the old API server:** after the new helpers are proven, add the third new server and remove the old server's etcd membership using the rehearsed sequence. Rejoin it as a dedicated control-plane server (`--disable-etcd`); move ingress, Vault placement/recovery and snapshot timers first. See [K3s server roles](https://docs.k3s.io/installation/server-roles).
- [ ] Activate and drill HA (§15) with three eligible storage/service hosts. Record singleton recovery downtime and keep the old host out of required data paths; amend ADR 0011 and the operational guides with the verified topology.

### 26. Resize platform services for the new capacity

Size from observed requests, usage, disk growth and concurrent builds. Reserve
capacity for one-host failure, storage rebuilds and rolling maintenance; suggested
sizes below are starting points to validate, not automatic allocations.

- [ ] Introduce explicit sizing profiles shared by Helm values, installer and Ansible. Suggest a profile from capacity, preserve operator overrides and reject budgets that cannot schedule after one-host failure; account for replicated database and Longhorn storage multiplication.
- [ ] GitLab: more Puma workers and Sidekiq concurrency, higher memory limits, larger `gitlab-data`.
- [ ] GitLab Runner: raise `concurrent` from 1 (for example 4–6) and per-job limits (CPU 2 → 4, memory 3 Gi → 6–8 Gi, ephemeral storage 4 Gi → 20 Gi); resize the cache.
- [ ] PostgreSQL PVC 2 Gi → 20 Gi+ with tuned `shared_buffers` and `work_mem`; MongoDB and Kafka volumes accordingly.
- [ ] Elasticsearch heap and volume (10 Gi → 50 Gi+); container log retention from about 3 days to 7–14 days; keep Lynis at 365 days.
- [ ] Prometheus retention from 15 d / 1 500 MB to about 30 d / 20 GB; GitLab embedded Prometheus from 1 d to 7 d. Measured 2026-10-04: the size cap leaves only about 12 h of history (oldest sample ~18:00 UTC the same day), which limits trend and baseline alerts; this node's 48 GiB free (30 GiB maintenance floor) cannot absorb 20 GB, so it waits for the new servers.
- [ ] Keycloak, SonarQube, Argo CD controller (`GOMEMLIMIT` with its limit), repo server, Trivy scan concurrency (2 → 4), Grafana.
- [ ] Raise `apps-int`/`apps-uat` ResourceQuotas.
- [ ] Keep maintenance headroom on the node root filesystem (alert `NodeMaintenanceHeadroomLow` below 30 GiB and a `--status` disk check added 2026-10-04; archives still on the root filesystem): alert before the kubelet image-GC (85 %) and ephemeral-storage eviction thresholds, and require about 30 GB free before maintenance. On 2026-09-28 a GitLab backup, its host copy and pinned recovery images triggered pod evictions. Store recovery archives off the root filesystem (§16).
- [ ] Review CI artifact expiry per application and keep registry retention with garbage collection (§12).

### 27. Directory, mail and SSO

**ADR — provider:** compare Microsoft 365/Entra with a self-hosted directory and
mail service on operational burden, required device management, SSO integration,
hosting/data location and current licences. Keep the selected organization,
domain, realm and administrator as installation inputs (ADR 0020). The mailbox
names below are deployment goals for the current installation, not source defaults.

Transactional SMTP is an independent early deliverable: it need not wait for
directory migration, virtualization or a new collaboration service.

Mail (either option):

- [ ] Create `zaid@swirlit.dev` on the chosen provider.
- [ ] Forward incoming mail to `c.zaid@outlook.com` while keeping a mailbox copy; verify the chosen provider's external-forwarding policy and test delivery before enabling it for the production address.
- [ ] Inventory existing mail DNS and verify the new mailbox/send/receive path before switching MX. Retire only conflicting Cloudflare Email Routing/Resend inbound records; preserve still-used outbound verification, DKIM and SPF senders, with a documented rollback.
- [ ] Manage the provider's MX, a single SPF policy per hostname, DKIM, DMARC and autodiscover alongside the platform DNS workflow. Test mailbox delivery and retained forwarding without loops before closing the migration.
- [ ] Provide a transactional SMTP contract (host, port, TLS/auth method, allowed sender and scoped credentials in Vault) for Keycloak, GitLab, Grafana, Alertmanager, Argo CD, Odoo, applications and the installer. Select an auth/relay mode the clients and provider actually support; verify password reset, invitation and alert delivery before marking §11/§17/§19 complete.

Directory and SSO:

- [ ] Establish the selected directory/tenant and document its supported deployment model and independent recovery access. For self-hosting, validate dedicated host/VM requirements before choosing §30's infrastructure.
- [ ] Set default service access and administrator access separately: ordinary directory users receive the intended application roles; membership in `platform-admins` is an explicit admin-group grant with MFA. Record any deliberate initial all-admin policy in the ADR rather than making every future user an administrator implicitly.
- [ ] Integrate the organization realm through Entra OIDC/SAML brokering or LDAP federation as appropriate; define group mapping, account linking and deprovisioning/session revocation. Decide any bulk provisioning separately from login brokering ([Keycloak identity providers](https://www.keycloak.org/docs/latest/server_admin/#_identity_broker)); keep `master` access limited to designated administrators.
- [ ] Preserve the directory `zaid` administrator's intended rights in the current installation, including `master` access, while rehearsing account linking/rename with stable identities. Keep a distinct tested local break-glass administrator and prevent `keycloak-sso-reconcile` from recreating an account/role collision on reruns.
- [ ] Verify that every SSO client (GitLab, Argo CD, Grafana, Vault, Kafka UI, Portainer, Odoo, OAuth2 Proxy) accepts directory users and that GitLab links the directory `zaid` to the existing account.
- [ ] Move application logins (in the application repositories) to the shared realm where they authenticate users.
- [ ] Define device-enrollment requirements for Windows, macOS and Linux in a dedicated folder/repository, then validate the selected provider's supported join/management options and licences. Evaluate Entra/Intune or Samba AD where Windows domain policy is required, and LDAP/SSSD/IPA clients where appropriate; prove enrollment and removal on a spare device before fleet rollout.
- [ ] Exercise an ordinary user, an administrator, a disabled directory user and an IdP outage across the SSO clients; prove break-glass access and timely removal of former users' access.

### 28. New `corp` services: n8n and Open WebUI

Introduce these after capacity, recoverability, SMTP and the identity decisions
are ready. Use the [platform capability checklist](docs/development.md#adding-a-platform-capability)
for each service: opt-in installation, pinned images, identity inputs, Vault/ESO,
resource budgets, network policy, ingress/Access, Homepage, alerts and restore.

- [ ] n8n: PostgreSQL, encryption key in Vault and recoverable persistent state. Verify the selected edition's authentication options: an OAuth2 Proxy gate alone does not provision n8n users or roles. Keep editor/admin routes authenticated; expose only required webhook paths with application authentication/signatures and rate limits.
- [ ] Open WebUI: Keycloak OIDC login, PostgreSQL database, persistent volume, OpenRouter as an OpenAI-compatible connection with its key in Vault.
- [ ] Start RAG with one approved source and a retrieval-quality check; decide vector storage (pgvector-compatible PostgreSQL image or separate store), embedding model and ingestion/update/deletion behavior. Add GitLab repositories/wikis, Odoo and read-only telemetry tools only after source ownership and access scope are defined.
- [ ] Preserve source permissions during ingestion and retrieval so private repositories, environments and CRM records are not exposed through a shared index. Exclude secrets/disallowed personal data, use per-source read-only credentials, and test denied retrieval as well as successful search.
- [ ] Verify OpenRouter account/region eligibility, model availability, retention and fallback behavior before sending company data. EU routing uses the supported regional endpoint and eligible plan, not merely a preference in generic settings; see [OpenRouter regional routing](https://openrouter.ai/blog/announcements/us-in-region-routing/). Set usage budgets and define an approved local-model fallback if required.
- [ ] NetworkPolicies from `corp` to the data sources in `infra`.

### 29. New `infra` services: Mattermost, Nexterm, Bitwarden

- [ ] Mattermost: verify a supported edition/SSO path, PostgreSQL and file storage, then create deployment/release/alert channels with backups, quotas and service health checks.
- [ ] Wire GitLab pipelines, Argo CD sync/health, Alertmanager, installer and Deployment UI notifications using scoped webhook credentials, deduplication and actionable links. Verify failure and recovery delivery; retain the external outage path in §14 because in-cluster Mattermost can fail with the platform.
- [ ] Nexterm: verify supported SSO and audited session access behind Access; use dedicated non-root SSH credentials and narrow sudo rights. Prove the actual pod/node egress source before updating private SSH allowlists, and choose placement compatible with moving workloads off the old node. Preserve independent console/private SSH recovery.
- [ ] Bitwarden for human logins: **ADR** comparing official self-hosting and Vaultwarden on supported clients, current SSO/licensing, updates and recovery. Disable open sign-ups, store required secrets in Vault, back up and restore-test data, and verify browser/mobile APIs without Access interception. Keep an independent emergency credential/recovery copy; HashiCorp Vault continues to hold machine secrets.
- [ ] Apply the same opt-in service integration and restore requirements as §28; verify namespace placement in the ADR before adding new runtime resources.

### 30. Host backup and virtualization

- [ ] Evaluate Veeam Agent/Kasten only against a named gap remaining after §16's restore drills. Verify OS/K3s/Longhorn and repository-target compatibility, licensing, consistency and measured restore benefit in a disposable proof of concept before adding another backup system.
- [ ] VM capability after a concrete directory/mail or other VM requirement: **ADR** comparing dedicated KVM/CloudStack and KubeVirt on supported hardware, spare/failover capacity, storage, networking, operations and restore. Keep platform recovery independent of VMs hosted on the same failed platform.

### 31. Future hardware

- [ ] NAS/private S3: choose a maintained implementation after checking current support/licensing and consumer API compatibility. Define separate buckets/credentials, durability, capacity, monitoring and migration for GitLab artifacts/Registry/LFS and other service files. Keep an independent offsite copy; NAS on the same site is not §16's disaster-recovery target by itself.
- [ ] GPU LLM server: retain 512 GB–1 TB RAM and 120 GB+ aggregate VRAM as a candidate budget, then size from the actual model, quantization, context, concurrency, GPU interconnect and latency/throughput measurements before purchase. Compare a rented benchmark with expected usage/cost; expose the chosen runtime privately to Open WebUI and developer tools on separate or tainted capacity.

---

## Part IV — Product roadmap (added 2026-10-04)

Swirl Cloud becomes a product: a **sovereign company cloud** that a European SME
or regulated mid-market company installs on rented or owned bare metal in hours,
then runs itself or has operated remotely while keeping its own keys. One
installation replaces what such a company otherwise assembles from a hyperscaler
and several SaaS vendors: application delivery, identity, business applications,
security operations and audit-ready compliance evidence.

**Niche.** Kubernetes distributions (OpenShift, Rancher, Talos and similar) need
an in-house platform team; hyperscalers and their sovereign offerings bill by
usage and keep the customer dependent on them; simple self-hosted PaaS tools stop
at deploying containers. Companies with 20–500 employees under NIS2, DORA (as
financial entities or their ICT suppliers) or customer security questionnaires
have none of a platform team, a predictable bill or audit evidence. Swirl Cloud
fills that gap: **own it like on-premises, run it like SaaS, leave at any time.**

**Differentiators to build and protect** (each maps to sections below):

1. Whole-company scope in one installation: developer platform, workplace,
   ERP and security operations, not a bare cluster (§34, §35, §40).
2. Compliance evidence generated continuously from the running platform for
   NIS2, ISO/IEC 27001, DORA and GDPR (§36).
3. Customer-held keys: vendor operations only through an outbound,
   customer-approved and recorded channel (§39).
4. No mandatory non-EU dependency: Cloudflare, Tailscale, GitHub and external AI
   become options with EU or self-hosted equivalents (§33).
5. Documented exit: open components, standard data formats and a
   self-operation runbook; fixed hardware-based cost instead of usage billing
   (§32, §34).

Recovery and operating gaps (Part V P0/P1) stay ahead of this roadmap: a product
cannot be sold before its own backups, MFA and alerting are proven.

### 32. Product packaging, releases and supply chain

- [ ] **ADR — licensing and editions:** choose between a GPL-3.0 core with a commercial edition (fleet console, compliance packs, LTS), support-only subscriptions, or dual licensing. Verify that every bundled component's licence permits redistribution and operation by a third party for customers (for example Elastic, Odoo, GitLab, SonarQube, Portainer and Kafbat editions) and record the result per component.
- [x] Release tooling ([releases](docs/guides/releases.md)): chart version as platform version, `scripts/release.py prepare` (version and CHANGELOG.md from commits since the last tag), annotated `v<version>` tags. *(2026-10-05; no release cut yet)*
- [ ] Release channels (`stable`, `lts`) with a defined security-backport window, and upgrade notes per release.
- [x] Release bundles with a CycloneDX 1.5 SBOM (53 pinned images by digest, 7 charts, K3s and Helm), image list, release notes, SHA256SUMS and an SSH signature, verified offline by `scripts/release.py verify` (tamper test fails as expected). *(2026-10-05)*
- [ ] Build and sign bundles in CI on tags with the key in Vault, publish them as GitLab releases, and make the installer and upgrader refuse unsigned or altered bundles; add build provenance.
- [ ] Offline installation bundle: K3s binaries, charts, every pinned image and the required OS packages in one verified archive, loaded into the internal Registry. Prove a full install on a host with no Internet egress.
- [ ] Platform upgrades as one operation (`./install.sh --upgrade <version>`): preflight (health, fresh verified backup, capacity, API deprecations through `validate-repository.sh --live`, migration guards), ordered component upgrades, node-by-node K3s upgrades, recorded rollback points and post-upgrade `--status`. Support and test only N-1 → N paths.
- [ ] Release qualification pipeline: each release candidate installs on disposable hosts, upgrades from the previous release, runs the safety suites and `verify-platform.py --strict`, and blocks the release on failure.
- [x] Support matrix in the [deployment guide](docs/deployment.md#support-matrix): Ubuntu LTS (tested 26.04), x86_64 (GitLab is amd64-only), K3s pin, topologies, and minimum/recommended/reference sizes from measured requests and usage. *(2026-10-05)*
- [ ] Extend the support matrix with sizing profiles (§26), additional tested Ubuntu releases and named providers once installations on them are verified.
- [ ] Publish versioned product documentation from `docs/` (administrator, operator and application-team guides, API reference), one version per release.
- [ ] Offer the hub, portal and installer messages in French and English.

### 33. Sovereignty mode and provider independence

- [x] [External services](docs/guides/external-services.md): every outside service, update source and data flow with provider, headquarters and the data it receives. Vendor telemetry and update checks switched off: GitLab Service Ping and version check, Grafana reporting/update checks/news/Gravatar, Kibana and SonarQube telemetry, Portainer statistics, Longhorn upgrade checker, Odoo publisher-warranty report. *(live, 2026-10-05)*
- [ ] Traefik's version check (`global.checkNewVersion: false`) takes effect at the next ingress reconcile (`scripts/configure-ingress.sh`); decide whether CrowdSec keeps sharing attack signals with its Central API (active, in exchange for the community block list).
- [ ] **ADR — edge without Cloudflare:** add an edge mode that needs no Cloudflare account: ACME certificates (cert-manager or Traefik), DNS through an EU provider API, in-cluster WAF and rate limiting (Traefik with CrowdSec or Coraza, linked to §4's HTTP IDS), Keycloak-gated admin hosts in place of Access, and the provider's network DDoS protection. Keep Cloudflare as a supported option.
- [ ] Private networking without Tailscale's coordination service: support a self-hosted control server (Headscale or NetBird) or WireGuard directly as an additional `K3S_NODE_TRANSPORT`; encrypt vRack and LAN node traffic too (flannel `wireguard-native`), which travels unencrypted today.
- [x] Repository onboarding works from GitLab alone: `./add-repos.sh --gitlab` (or `REPOSITORY_SOURCE=gitlab`) deploys existing projects in the platform group through their contracts with no GitHub account or mirroring ([projects without GitHub](docs/guides/repository-onboarding.md#projects-without-github)). *(2026-10-05: verified finding `swirlit/indezy` and rejecting a missing project, no deployment)*
- [x] Installer inputs `REPOSITORY_SOURCE` (`github`/`gitlab`) and `GITLAB_PROJECTS`, passed through the engine and the hub's repository action; GitHub inputs are required only for the GitHub source.
- [ ] `SOVEREIGNTY_MODE=strict` installation profile that refuses non-EU or non-self-hosted dependencies, pulls images only from the internal Registry (§32 offline bundle) and reports any remaining external call in `--status`.
- [ ] Provider modules (OpenTofu) for ordering and preparing servers at two or more European bare-metal providers (private network, OS image, DNS), callable from the hub with an API key; a generic path for already-installed servers stays the default.
- [ ] On-premises appliance: an unattended Ubuntu installation image (ISO and PXE) that applies the host baseline and starts the setup hub on first boot; test on physical hardware with a BMC.

### 34. Multi-tenancy, self-service and cost transparency

- [ ] **ADR — tenancy model:** projects inside one organization installation (namespaces per environment, Keycloak groups, Argo CD AppProjects, GitLab groups, Vault paths, quotas) versus virtual clusters for stronger isolation. Depends on `infra` default-deny networking (§6).
- [ ] Declarative project resource (CRD or Git-managed file) reconciled into per-environment namespaces with ResourceQuota and LimitRange, NetworkPolicies, AppProject, GitLab group, Keycloak groups (owner, developer, viewer), Vault policy, data credentials, Grafana folder and Kibana space. Everyday project work no longer needs `platform-admins`.
- [ ] Role model across every service: platform administrator, security auditor (read-only everywhere plus compliance views), project owner, developer and viewer; covers Grafana, Kibana, Argo CD, GitLab, Vault, DBGate, Kafka UI and Portainer (replaces the all-or-nothing access in §18).
- [ ] Self-service data services through claims or the portal: a PostgreSQL database (shared server or dedicated CloudNativePG cluster), Redis ACL user, Kafka topics, S3 bucket. Credentials land in the project's Vault path; backups, metrics and alerts come with the claim; deletion keeps a retention period.
- [ ] In-cluster S3-compatible object storage for applications and platform services, on the implementation chosen in §31, with per-project buckets, quotas and backups.
- [x] Cost showback from each server's recorded monthly cost (`scripts/configure-node-cost.sh`, installer input `SERVER_MONTHLY_COST`, hub Maintenance): allocation by the larger of requests and usage (65 % CPU / 35 % memory, capped at server cost), idle and shared costs, Grafana **Cost Showback** per environment, namespace and workload with a rightsizing view ([cost showback](docs/guides/cost-showback.md)). *(live, 2026-10-05: allocation series for 90 pods)*
- [ ] **Operator action:** record `swirlit-node-01`'s monthly cost (and shared costs such as backup storage) with `scripts/configure-node-cost.sh` so the dashboard shows money, not only shares.
- [x] Monthly showback per namespace and workload in Grafana **Platform Report**, from hourly snapshots kept 13 months in Elasticsearch `platform-reports-*` ([platform reports](docs/guides/platform-reports.md)). *(live, 2026-10-05: first snapshot indexed)*
- [ ] Comparison against equivalent hyperscaler list prices with stated assumptions; add backup and traffic usage once those are measured per project.

### 35. Developer portal and golden paths

- [ ] **ADR — portal:** extend Homepage, adopt Backstage, or build a small portal on the platform API. The portal is the product's front door: catalog with owner, repository, environments, deployed revision, health, SLO (§38), vulnerabilities, Sonar quality gate and documentation, fed by the onboarding contract, Argo CD, GitLab, Trivy and Sonar.
- [ ] Versioned platform API behind Keycloak for the portal, CLI, deployment UI (§24) and fleet agent (§39), so front ends never run scripts directly.
- [ ] Golden-path templates (Spring Boot, NestJS/Node, Python, static site): one action creates the GitLab project with CI, Dockerfile, manifests, onboarding contract version 2, Sonar project and Keycloak client, then deploys to `int`. Measure and publish template-to-URL time.
- [ ] Preview environments per merge request: temporary namespace and hostname, seeded test data, quota-limited, removed on merge or close.
- [ ] Progressive delivery: canary or blue-green releases with automated analysis on Prometheus metrics and automatic rollback (Argo Rollouts or equivalent), plus release-freeze windows recorded as change records (§36).
- [ ] `swirl` CLI on the platform API: Keycloak device-flow login, applications and environments, deploy and promote (packaging `scripts/deploy-application.py`), logs, dashboards and time-limited database access for authorized roles.

### 36. Compliance evidence and governance

- [x] Control catalog ([`k8s/files/compliance-controls.yaml`](k8s/files/compliance-controls.yaml)): 25 controls mapped to NIS2 Article 21(2)/23, ISO/IEC 27001:2022 Annex A, DORA, GDPR and the CIS Kubernetes and Ubuntu benchmarks; each names its automated Prometheus evidence (reusing alert thresholds) or the manual evidence required ([compliance evidence](docs/guides/compliance-evidence.md)). *(live, 2026-10-05)*
- [x] Helm-generated recording rules evaluate every control continuously; Grafana **Compliance Evidence** dashboard per framework, control and requirement; `ComplianceControlRegressed` drift alert; `./install.sh --compliance [--evidence-pack --sign-key KEY]` writes a hashed, SSH-signed archive that `--verify` checks offline (tamper test fails as expected). *(live, 2026-10-05: NIS2 12/16, ISO 27001 13/17, DORA 12/16, GDPR 10/11, CIS 6/9 automated controls passing)*
- [x] Hourly compliance status per control and framework kept 13 months in `platform-reports-*`; the **Platform Report** shows the share of hours each control passed per month.
- [ ] Generate the signed evidence pack monthly on a timer and keep it with the tamper-evident audit store below.
- [ ] Triage the standing failures the report shows (15 CIS Kubernetes and 3 Pod Security Baseline checks, `gitlab-runners`/`host-agents` without NetworkPolicies) and record accepted exceptions with owner, rationale and review date in the catalog.
- [x] [ADR 0027](docs/adr/0027-native-admission-policy-packs.md): native ValidatingAdmissionPolicy packs, audit first. Application pack (digest-pinned images, platform Registry, requests and memory limit, `app` label, owner and data-classification labels) bound in Warn+Audit mode to application namespaces; `./install.sh --policy-report` dry-runs existing workloads. *(2026-10-05)*
- [ ] Fix the violations the policy report lists in the application repositories, then move the binding to `Deny` for `prod` and later `uat`; add a platform pack for `infra` exceptions with owner and expiry.
- [ ] Sign application images in platform CI and verify signatures at admission for `prod` (and `uat` once stable).
- [x] Keycloak login and admin events stored in every realm for 90 days (admin details off), and every event logged and kept at least a year in `identity-audit-*` with parsed fields ([identity events](docs/security.md#identity-events)). *(live, 2026-10-04: 50 events indexed within minutes of rollout)*
- [ ] Collect identity, Kubernetes API, Vault, GitLab and host audit records into a tamper-evident store: off-site object storage with retention lock, retention set per framework.
- [x] Access review sheet (`./install.sh --access-review`, hub Maintenance, `--access-review` in the evidence pack): Keycloak users per realm with groups, effective roles and last login, GitLab users with admin/2FA/activity/memberships, Kubernetes `cluster-admin`/`admin`/`edit` subjects, Argo CD mappings and local break-glass administrators, with findings and a Keep/Revoke sign-off sheet ([access reviews](docs/guides/compliance-evidence.md#access-reviews)). *(2026-10-05 first run: 9 identities; findings: GitLab `root` without 2FA, `infra/portainer` and `longhorn-system/longhorn-support-bundle` ServiceAccounts with cluster-admin)*
- [ ] Resolve the first review's findings: enforce GitLab 2FA (or rely on Keycloak MFA once §11 lands) and narrow Portainer's ServiceAccount (§18); add Vault policy and Grafana role assignments to the sheet.
- [x] Draft GDPR Article 30 records from the running platform (`./install.sh --processing-records`): per application environments, owner and classification labels, endpoints, data services and processors inferred from credential names, volumes, hosts, backups and retention, plus platform activities; controller fields left to complete ([records of processing](docs/guides/compliance-evidence.md#records-of-processing)). *(2026-10-05: 8 applications; the website's Resend e-mail processor found)*
- [ ] Generate the records from the project resource (§34) once projects declare purposes and data classes, instead of inferring them.
- [x] Incident register and NIS2 workflow: GitLab **Incident** template (awareness time, significance test, deadlines, timeline, post-incident review) and `./install.sh --incident-report` with endpoint impact, a timeline of alerts and security findings, changes, restarts, logins and audit activity, NIS2/GDPR due times and drafts of the three NIS2 reports ([incident response](docs/guides/incident-response.md)); detection findings kept 13 months. *(2026-10-05)*
- [x] Vulnerability deadlines (Critical 15 days, High 30 days from first sighting in a running image): hourly first/last-seen tracking per image and vulnerability, open and past-deadline counts in the **Platform Report**, `./install.sh --vulnerability-report` listing overdue items ([platform reports](docs/guides/platform-reports.md)). *(2026-10-05; tracking starts now, so the first deadlines fall due mid-October)*
- [ ] Record triaged vulnerability exceptions as data (image, vulnerability, owner, expiry) so the deadline report and `TrivyNewCriticalVulnerability` subtract them.
- [x] Credential age report: every Vault KV secret with version, last write and age against a limit, metadata only (`./install.sh --credential-age`; [security](docs/security.md#credential-age)). *(2026-10-05: 37 secrets, oldest 35 days)*
- [ ] Dynamic or scheduled rotation for database credentials (Vault database engine), registry and GitLab tokens and API keys, and an alert when a credential passes its age limit.

### 37. Security operations

- [ ] **ADR — runtime detection:** Falco or Tetragon with curated rules (shell in a container, unexpected egress, mining, sensitive file reads, privilege escalation), alerting through Alertmanager and indexed for investigation.
- [x] Detection rules every five minutes, posted to Alertmanager with severity, `owner: security` and runbooks: brute force followed by success, new platform administrator (Keycloak state), RBAC changed outside GitOps (dry-runs excluded), Secret read by an identity unseen for a week (after a 7-day learning period) ([security detection](docs/guides/security-detection.md)). *(live, 2026-10-05: RBAC rule verified with a test RoleBinding)*
- [ ] Extend detection to host events (Fail2ban/CrowdSec bans followed by a successful SSH login, sudo by unexpected users), HTTP events once Traefik access logs are shipped (§4), and impossible travel once logins carry a geolocation.
- [ ] Encryption at rest for application volumes (Longhorn encrypted volumes with keys in Vault) and a documented disk-encryption option for hosts in rented data centers.
- [ ] Ransomware-resilient backups: off-site copies under retention lock, written with credentials that cannot delete or overwrite retained data, so a compromised cluster cannot destroy its backups (§16).
- [ ] Just-in-time administration: time-limited `platform-admins` membership requested, approved and recorded, replacing permanent membership for daily users; recorded `kubectl` and SSH sessions (with §29's Nexterm or an equivalent).
- [ ] External penetration test of a reference installation before the first customer, repeated per major release; publish a vulnerability disclosure policy and a `security.txt` contact for the platform.

### 38. Reliability, SLOs and observability depth

- [x] Availability SLO (99.5 %) for every public endpoint (portal, Git, Registry, Argo CD, SSO, Odoo and every application host, discovered from Ingresses) measured by blackbox probes over the public path; multi-window burn-rate alerts; Grafana **Service Levels** with availability and budget left per endpoint ([service levels](docs/guides/service-levels.md)). *(live, 2026-10-05: 25 endpoints probed, all up)*
- [x] Monthly availability per endpoint in Grafana **Platform Report** from the hourly snapshots, independent of Prometheus retention.
- [ ] SLIs that probes do not cover: CI job success and queue time, Registry push/pull, Argo CD sync latency and data-service availability. These define the managed-service SLA.
- [x] Every alert rule carries an `owner` label (`platform`, `security`, or `application` for application namespaces) and a `runbook_url` to its section of [alert runbooks](docs/guides/alert-runbooks.md); validation rejects an alert without both. *(2026-10-05, 44 alerts)*
- [ ] Status page hosted outside the platform, fed by the external availability check (§14), with incident updates.
- [ ] On-call routing: escalation policies and a paging channel in Alertmanager, with acknowledgement, on top of §27/§29 notifications.
- [ ] OpenTelemetry: a collector, a trace backend and automatic instrumentation for Java and Node applications; correlate traces with logs and metrics in Grafana. Decide in the ADR whether logs stay in Elasticsearch.
- [x] Capacity planning: requested and used CPU/memory per server, requests after losing the largest server, cores and memory to add to stay below 70 %, days until filesystems and volumes fill; `ClusterCapacityRequestsHigh` and `ClusterCannotSurviveNodeLoss` alerts; Grafana **Capacity Planning** ([capacity planning](docs/guides/capacity-planning.md)); per-namespace history in the platform reports. *(2026-10-05: CPU requests at 96 % of allocatable with 15 % used; about 3 cores to add or to release by rightsizing)*
- [x] Lowered CPU requests of Kibana (250m → 100m), SonarQube (250m → 150m), Trivy operator (250m → 100m) and Prometheus (200m → 100m) to measured peaks; requests fall from about 99 % to 92 % of allocatable CPU. *(2026-10-05)*
- [ ] Lower Longhorn's guaranteed instance-manager CPU (12 % = 0.96 core, used 0.2–0.26) in a maintenance window: the instance managers restart, so detach volumes first; then set it in `reconcile-platform-release.sh` for new installations. Application repositories lower their own over-sized requests (Cost Showback rightsizing).
- [ ] Application-level backup and restore per project namespace (manifests, volumes and claimed databases), including self-service restore into a sandbox and moving a project between clusters.
- [ ] Scheduled game days in non-production environments (pod and node loss, DNS failure, storage degradation) with recorded results.

### 39. Fleet management and the managed service

- [ ] **ADR — fleet architecture:** an outbound-only agent per installation reports version, health, alerts, backup age, certificate expiry, capacity and compliance status to a central fleet console hosted in the EU. No inbound access; the customer can see every field sent and can disconnect at any time.
- [ ] Fleet console: inventory of installations, staged release rollout (internal installation first, then canary customers), upgrade status, fleet-wide alert and compliance views.
- [ ] Customer-approved support access: a time-limited session that the vendor requests and a customer administrator approves in the customer's own Keycloak; every action is recorded and visible to the customer and access ends automatically. The customer keeps the Vault recovery keys.
- [ ] Support bundle upload through the agent with customer consent, linked to a support ticket.
- [ ] If the licensing ADR (§32) adopts a commercial edition: offline-verifiable licence files whose expiry disables only commercial features and never stops running workloads.
- [ ] Prepare the managed service for certification: ISO/IEC 27001 scope covering the vendor's operation of customer installations, then evaluate HDS or SecNumCloud only for a named customer segment.

### 40. Business and workplace modules

- [ ] Module catalog in the hub and portal generalizing the [platform capability checklist](docs/development.md#adding-a-platform-capability): each optional service declares dependencies, sizing, SSO, Vault secrets, network policy, backups, alerts and restore test, and installs or uninstalls with an explicit data-retention choice. §28 and §29 services become modules.
- [ ] Files and office module: Nextcloud with Collabora Online or ONLYOFFICE, Keycloak SSO, object or Longhorn storage, backups and restore test.
- [ ] Video-conferencing module: Jitsi Meet with Keycloak-authenticated room creation and a TURN service.
- [ ] Offer the mail and directory outcome of §27 as an installation option for customers.
- [ ] Odoo business profiles: preconfigured module sets (CRM and sales, invoicing and accounting, projects and timesheets, HR) applied by the Odoo bootstrap, with French localization.
- [ ] French electronic invoicing in Odoo: reception is mandatory for every French company from 2026-09-01. Connect through a registered partner platform (PDP) supported by the pinned Odoo version and test sending and receiving.

### 41. Private AI

- [ ] AI gateway (OpenAI-compatible proxy) as the only path from users and applications to language models: per-project keys from Vault, budgets, model allowlists per data classification (confidential data only to local models), usage metrics and request metadata logging without prompt contents by default. Open WebUI (§28) uses it.
- [ ] Local inference runtime on GPU nodes (§31) with GPU scheduling and sharing, exposed only through the gateway.
- [ ] Read-only operations assistant over Prometheus, Kibana, Argo CD and the runbooks that explains alerts and proposes runbook steps; it never applies a change without an operator's approval.

---

## Part V — Priority index of open items

P0 protects recovery before disruptive changes. P1 closes operating gaps and
prepares expansion. P2 improves delivery and company services once prerequisites
are met. P3 requires a demonstrated need/capacity or a separate product decision.
These rows group the checklists above; priority does not bypass dependencies.

| Priority | Deliverable | Dependency / completion evidence | Section |
|---|---|---|---|
| P0 | Current platform health | Root Application and maintenance jobs healthy after the discovery/retention fixes | §12–13 |
| P0 | Offsite archives, current Longhorn backup integration and separate recovery keys | Target outside the failure domain; fresh uploads and successful archive/volume restores | §8, §16 |
| P0 | GitLab and full-platform recovery coverage | Native backups plus configuration/secrets; isolated rebuild with measured RPO/RTO | §12, §16 |
| P1 | Useful alerts, transactional SMTP and independent outage detection | Synthetic failure/recovery delivered even if GitLab/the cluster is down | §14, §27 |
| P1 | Private/key-only SSH and administrator MFA | Tested second session/console and break-glass login; recovery prerequisites complete | §4, §11 |
| P1 | `infra` network isolation and PSA enforcement | Dependency allowlists and host-agent placement validated before enforcement | §6 |
| P1 | Fresh vulnerability triage | Verified upstream fixes and new scans | §21 |
| P1 | Pending reboot and selected host hardening | Fresh audit; backup/unseal/console recovery verified before maintenance | §4, §20 |
| P1 | Capacity baseline, PostgreSQL headroom and Registry cleanup | Usage/growth alerts, protected recovery images, verified reclamation | §10, §12, §26 |
| P1 | Version-2 app migration and removal of legacy anonymous Kafka access | Owning repos verify environment isolation and delivery before credentials/listener retirement | §10, §19 |
| P1 | Multi-host expansion, stable endpoints and HA | P0 complete; topology helpers first; three eligible storage hosts; ordered migration and host-loss drills | §5, §15, §25–26 |
| P1 | Ransomware-resilient backups and tamper-evident audit retention | Retention-locked off-site copies the cluster cannot delete | §36–37 |
| P2 | Installer prerequisite cleanup, schema/engine and TUI | Bootstrap without Vault, idempotent rerun/resume and useful verification report | §2, §23 |
| P2 | API/host/HTTP audit coverage and remote-cluster telemetry | Scoped collection/retention; remote telemetry required before remote production use | §4, §14, §19, §20 |
| P2 | Mailbox/directory migration and SSO roles | Provider ADR, verified DNS/account linking, deprovisioning and local recovery access | §27 |
| P2 | Mattermost, Nexterm and human password manager | Capacity and service ADRs; offsite restore and independent emergency access | §29 |
| P2 | Odoo CRM and Live Chat integration | Scoped identities, both sides of network policy, public-chat and retry checks | §22 |
| P2 | Deployment UI | Existing app pipeline contracts and protected promotion; exact revision/rollout verification | §24 |
| P2 | Licensing ADR and product releases | Per-component licence review; signed bundles, SBOM, offline install and a green N-1 → N upgrade qualification | §32 |
| P2 | Sovereignty mode | Edge without Cloudflare, self-hosted mesh, GitHub optional; strict profile installs with no external call reported | §33 |
| P2 | Tenancy, roles and self-service data | `infra` isolation (§6) first; cross-project access denied in tests; claims include backups and alerts | §34 |
| P2 | Compliance policy packs and periodic evidence | Policy packs in audit, then enforced; monthly evidence packs archived | §36 |
| P2 | SLOs, runbooks, status page and on-call | Every alert has a runbook; external status page; burn-rate alerts tested | §38 |
| P3 | ILM/task naming and new-install Kafka identity cleanup | Preserve existing indices and Kafka cluster identity | §1, §10 |
| P3 | Optional installer web UI, n8n and Open WebUI/RAG | Proven engine/capacity, source permissions, regional routing and usage budgets | §23, §28 |
| P3 | Additional backup products, virtualization, NAS and GPU server | Named need, compatibility/restore proof and measured sizing/cost | §30–31 |
| P3 | Developer portal, platform API, golden paths and preview environments | Platform API first; measured template-to-URL time | §35 |
| P3 | Fleet console and customer-approved support access | Licensing ADR; outbound-only agent; approved, recorded and expiring session tested | §39 |
| P3 | Runtime detection, JIT administration, encryption at rest and penetration test | Detection rules fire on simulated attacks; pentest findings closed | §37 |
| P3 | Module catalog, workplace and business modules, AI gateway | Module install/uninstall with restore test; capacity and GPU sizing | §40–41 |
