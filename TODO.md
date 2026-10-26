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
inspect or change the running cluster. Completed entries remain as one-line
history; the linked docs and Git history hold the details.

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
Application `swirl-cloud` was **Synced at `3926910` but Degraded**; it was
Synced/Healthy again on 2026-10-04 (§13).

Work in dependency order: **recovery and current health → access controls and
useful alerts → capacity and HA → installer/delivery UX → additional services**.
Use the [priority index](#part-v--priority-index-of-open-items) to pick work.
Repeated gaps in the inventory point to their owning roadmap section; they are
not separate implementations. Close operational items only with a dated result
(restore, migration or failure drill); source/render checks alone are insufficient.

## Bugs to fix

Defects found while working on another task: misconfiguration, drift, failing
checks, scanner findings, insecure defaults or broken automation. Each entry names
the environment and resource, the evidence (command and output, plan excerpt or
finding) and the risk. Agents add entries here instead of widening their change.

- [x] Probes no longer kill slow but working services under load: liveness probes allow 5 s (10 s for datastores) and a minute of failures, readiness 3 s (5 s), PostgreSQL uses `pg_isready -q` with a 10-minute startup probe, and `validate-manifests.py` enforces the rule and rejects `$(VAR)` without a literal value in exec probes ([manifest policy](docs/code-quality.md#manifest-and-deployment-policy)). *(2026-10-23)*
- [x] The kubelet reserves memory for the host and evicts under memory pressure: [`configure-k3s-kubelet-memory.sh`](scripts/configure-k3s-kubelet-memory.sh) sets `system-reserved`, node-scaled `kube-reserved` and memory eviction thresholds, applied by every installer path and reported by `--status` ([node memory and eviction](docs/operations.md#node-memory-and-eviction)). *(live, 2026-10-23)*
- [ ] The page cache thrashes when CI runs, and the node stalls although memory eviction never triggers (`swirlit-node-01`, 2026-10-23 19:24–19:26 UTC, after the kubelet reservation above): load 58, about 400 MB/s of reads (`vmstat`), I/O pressure `some avg60=68`, 290 MiB free and 3 GiB of page cache. Liveness probes failed even with 5-second timeouts and a one-minute window, restarting Prometheus, Grafana, Traefik (GitLab answered Cloudflare 521 meanwhile), metrics-server, the Argo CD repo server and the Longhorn CSI sidecars. Two CI jobs ran (Swirl Demo App `02-test`, Swirl Website `e2e`), about 800 MiB each. `kubepods.slice` never reached its limit (`memory.events.local` `max 0`) and the kubelet still measured about 3.8 GiB available, above the 1.5 GiB soft threshold. Pods hold 22.5 GiB of anonymous memory and the host about 5.4 GiB, which leaves too little page cache for the files the platform reads. Risk: any disk-heavy CI job can restart the ingress and the monitoring stack. Options: (a) more memory for the node; (b) one CI job at a time across both runner managers (`gitlab-runner` and the project-1 runner); (c) CI pods request the memory they use (`memory_request` 128 Mi → about 1.5 Gi in `k8s/platform/gitlab-runner.yaml`), so the scheduler holds a job while the node is full; (d) lower the JVM heaps and the limits of the largest services (§26). It recurred at 19:44 (load 69, I/O pressure `some avg10=96`, CoreDNS liveness failing) with Thoughty `02-test` at 1.8 GiB and Swirl Demo App `02-test` at 0.9 GiB. A higher soft eviction threshold alone would not help: ranked by use above request, GitLab comes first (4.7 GiB used, 2 GiB requested), ahead of either CI job, so the kubelet would evict GitLab. Any threshold change needs GitLab's request raised to what it uses first. At 20:07 the node went `NotReady` for about a minute (5-minute load 68; Traefik, CoreDNS and the release runner manager restarted), and a Swirl Demo App deploy job failed on a Kubernetes API TLS handshake timeout. It recurred at 21:47–21:58 (load 94, I/O pressure `some avg60=94`, about 300 MB/s of reads, swap full) with Thoughty `02-test` Jest workers at about 700 MiB each: Traefik, CoreDNS, Grafana, Prometheus, metrics-server, Longhorn, the Argo CD server and repo server, Portainer and two applications failed liveness within the same second, and the Kubernetes API timed out. *Progress 2026-10-24:* option (c) done: CI build containers request 2 GiB (105 jobs over 14 hours: median 180 MiB, 90th percentile 1.9 GiB, Thoughty, Indezy, Swirl Demo App and Swirl Website test jobs 2.0–2.4 GiB) and helpers 128 MiB. With the node's requests at 20.6 of 26.9 GiB, this holds back a third concurrent job but not a second; most of the overcommit is services using more than they request (GitLab first).
- [x] The Argo CD application controller no longer crash-loops on OOM kills while rebuilding its cluster cache: limit 2 GiB with `GOMEMLIMIT` 1536MiB ([argocd-values.yaml](config/argocd-values.yaml)), applied with `scripts/reconcile-platform-release.sh argocd`. *(live, 2026-10-25)*
- [x] Grafana dashboards show data again: Prometheus no longer fails liveness under I/O pressure, with the `kubernetes-nodes` job dropping 57 000 unread K3s control-plane series and 2 CPUs and 2 GiB ([prometheus.yml](k8s/files/prometheus/prometheus.yml), [monitoring.yaml](k8s/platform/monitoring.yaml)). *(2026-10-23)*
- [x] The platform PostgreSQL and SonarQube's database run the 2026-10-21 rebuild of `postgres:18.6-bookworm` (`sha256:bbf83594798e…`): only Debian security updates (OpenSSL 3.0.22, Perl `deb12u4`, PCRE2, `tzdata`), same PostgreSQL packages and glibc, so no reindex ([security](docs/security.md#vulnerability-triage)). The rescan before 2026-11-04 is the §21 recheck. *(2026-10-26)*

## Needs Input

Parts of tasks that cannot advance without the owner's decision or information:
target environment, approvals and maintenance windows, credentials and access,
cost, security exceptions, and destructive or irreversible steps. Only the part
needing input waits here; the rest of the task keeps moving. Answer under the
item, and the next agent run applies the answer and removes the entry.

Changes under `k8s/` deploy to the live cluster as soon as they reach `main`
(the root Argo CD Application syncs automatically), so agents prepare each such
change and ask the owner before pushing it.

- [ ] Which off-site S3-compatible target should hold the restic archives and Longhorn volume backups? Options: (a) OVHcloud Object Storage in another region; (b) Scaleway or Backblaze B2 (EU); (c) another provider. Please also give the monthly budget, and whether versioning or object lock is wanted (it must stay compatible with restic prune). Supply the endpoint, bucket and key through the setup hub (Configure, then Backups). *(task: §16 › Off-site backups, P0; asked by: Infra Agent)*
  - Answer:
- [ ] Export the recovery kit off the host: run `scripts/export-recovery-kit.sh --separate-archives` (or `./install.sh`, then Maintenance) on swirlit-node-01, store the kit and its passphrase in two separate off-host places, delete the local copy, check it with `--verify FILE`, and give the date here. *(task: §9 and §16 › Vault recovery keys, P0; asked by: Infra Agent)*
  - Answer:
- [ ] How should Vault unseal and the root token work from now on? Options: (a) keep the host-local unseal key for unattended reboots, revoke the standing root token and generate a short-lived one with `vault operator generate-root` when needed; (b) auto-unseal through a KMS or Transit outside this cluster (new dependency and cost); (c) manual unseal after every reboot with no key on the host; (d) keep today's model and record the accepted risk. *(task: §16 › Separate recovery keys, P0; asked by: Infra Agent)*
  - Answer:
- [ ] Should the nightly backup also run GitLab's native `gitlab-backup create`? Options: (a) database and repositories only (skipping Registry, artifacts, builds, packages and LFS) into the nightly archive after a free-space check against the 30 GiB floor; (b) wait for the off-site target above and archive storage off the root filesystem (§26); (c) a full backup including the Registry, uploaded to S3 by GitLab once a bucket exists. Meanwhile `/etc/gitlab` (secrets and host keys) and the GitLab version join the nightly archive without a decision (§12, 2026-10-14). *(task: §12 › GitLab recovery coverage, P0; asked by: Infra Agent)*
  - Answer:
- [ ] Which SSH policy should internet-facing control planes use? Options: (a) key-only on public port 22, applied only after the script proves every allowed account has keys and the current session used one; (b) key-only and port 22 on `tailscale0` only, with the provider console as break-glass; (c) keep password login and accept the exposure behind Fail2ban and CrowdSec. Please also confirm that a console login to swirlit-node-01 works, since (a) and (b) depend on it. *(task: §4 › Key-only SSH, P1; asked by: Infra Agent)*
  - Answer:
- [ ] Which maintenance window should disruptive work use (host reboots, K3s, Vault or Traefik restarts, and `k8s/` pushes that restart services)? Options: (a) a weekly slot, for example Sunday 05:00–07:00 UTC; (b) a monthly slot; (c) ad hoc, with your approval each time. *(task: §4 › Maintenance windows, P1; asked by: Infra Agent)*
  - Answer:
- [ ] How should Trivy's node-collector keep working once `infra` enforces Pod Security `baseline`? Options: (a) exempt its service account in a K3s PodSecurity admission configuration under `config/host` (a K3s restart of about 15 seconds in a window); (b) turn off infra assessment (no restart, but node CIS checks and part of the compliance evidence are lost); (c) move trivy-operator to its own privileged namespace, as ADR 0025 did for host agents (no restart, larger change). The `enforce: baseline` label then follows as a separately approved push. *(task: §6 › PSA on infra, P1; asked by: Infra Agent)*
  - Answer:
- [ ] Which alert notifications should come back, and how often? Options: (a) critical alerts only, by email, repeated every 24 hours; (b) GitLab alerts for every alert plus email for critical only; (c) the previous setup after the noisy alerts are retuned (please name the ones that were too frequent); (d) keep them paused. *(task: §14 › Reactivate notifications, P1; asked by: Infra Agent)*
  - Answer:
- [ ] What recovery point do the PostgreSQL databases (Keycloak, SonarQube, Odoo, applications) need? Options: (a) accept up to 24 hours and record it; (b) more frequent `pg_dumpall` runs, for example every 6 hours (host script only, more root-filesystem use); (c) continuous WAL archiving with point-in-time recovery to the off-site target (needs the target, a live change and a drill). *(task: §10 › PostgreSQL RPO, P1; asked by: Infra Agent)*
  - Answer:
- [ ] For the expansion: may the two EPYC servers be ordered now (provider, offer, location, monthly budget, console access)? Which node transport: (a) all Tailscale, no code change but benchmarked first; (b) vRack between new servers and Tailscale to the current one; (c) move the current server to a vRack offer? And where should public ingress go: (a) stay on the current node; (b) move to a new server with a DNS cutover; (c) Cloudflare Tunnel once three nodes run connectors? *(task: §25 › Expansion, P1; asked by: Infra Agent)*
  - Answer:
- [ ] Has the company already chosen an approved e-invoicing platform (plateforme agréée) to receive supplier invoices? French law has required this since 2026-09-01. Options: (a) Odoo's approved platform from the company's Odoo: the legal representative completes Odoo's identity check and e-signed designation, then a test invoice is received; (b) another approved platform is already designated (please name it), and Odoo only records the invoices; (c) none yet: pick (a) or another platform now. *(task: §40 › French e-invoicing, P1; asked by: Strategy review)*
  - Answer:
- [ ] Should the company apply for the funding programmes that close in early November? Options: (a) NLnet NGI Zero by 2026-11-03 12:00 CET, for one sovereignty work package (§33); the funded work must be released under an open licence, and the repository is private today; (b) the Hexatrust accelerator by 2026-11-02; (c) both; (d) neither. *(task: §44 › Funding deadlines, P2; asked by: Strategy review)*
  - Answer:

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

- [x] Single GPL-3.0 repository with editor, attribute, ShellCheck and ignore settings (credentials, keys except the public origin CA, kubeconfigs and caches excluded).
- [x] Shared deployment contract `config/platform.env` (`PLATFORM_CONTRACT_VERSION=7`) for Bash and Ansible: pinned versions, timeouts, manifest inventories, wait lists and Cloudflare host inventories.
- [x] Helm chart `k8s/` (`swirl-cloud` 2.0.0) with vendored `redis-ha` (HA only) and `trivy-operator` for offline rendering.
- [x] One renderer for installer and GitOps (`render-cluster-config.sh`, `render-identity.py`, `render-platform-ha.py`) into a fresh private directory, with placeholders that keep the checkout organization-neutral.
- [x] Offline validator `scripts/validate-repository.sh` (lint, doc links, inventories, workload policy, digest pinning, nine render combinations, installer/GitOps parity, credential scan). *(passes locally, 2026-09-27)*
- [x] Live mode `validate-repository.sh --live` (server-side dry run of every profile) passes; objects needing CloudNativePG before HA activation count as not validated. *(live, 2026-10-04)*
- [x] Fourteen safety suites in `tests/`.
- [x] Documentation in the shared layout ([ADR 0001](docs/adr/0001-documentation-structure.md)), with 25 ADRs reconstructed from the Git history (three superseded).
- [x] Ansible hardening step renamed; private image overrides retired in Chart 2.
- [ ] Reconcile the Elasticsearch ILM policy name `swirl-cloud-logs-7d` with its actual delete phase (`min_age: 1d`) and the retention decision in §26; migrate existing index/template references without dropping retained logs. *Progress 2026-10-14:* source ready, not pushed: the policy is renamed `swirl-cloud-logs` (same phases, so §26 changes it in place); Fluent Bit and Logstash (`config-version` 3) move the templates and existing `kubernetes-logs-*`/`app-logs-*` indices to it; nothing deletes the old policy ([storage and retention](docs/operations.md#storage-and-retention)); `validate-manifests.py` rejects an `index.lifecycle.name` no manifest stores. Closes after the owner-approved push and two checks in Kibana Dev Tools. Right after the sync: `GET _ilm/policy/swirl-cloud-logs` returns the policy; `_ilm/explain` on `kubernetes-logs-*,app-logs-*` shows every index `managed: true` on `swirl-cloud-logs` with no `ERROR` step, `failed_step` or error `step_info`; the `app-logs-single-node`, `kubernetes-logs-v2` and `kubernetes-logs-host` templates name it; and `_cat/indices` sampled before and after still lists every earlier index with no lower document count (except one created over a day before the second sample, which ILM deletes as designed). 25 hours or more after the sync: `_ilm/explain` shows every index managed, none older than about `1.1d`, and no `swirl-cloud-logs-7d` under `phase_execution`.

### 2. Installer (`install-control-plane.sh`)

- [x] Refuses to run as root or on a K3s worker.
- [x] Guided sections from cluster identity through GitOps, exposure, scope, topology, K3s, components, recovery and administrator credentials.
- [x] Unattended `--yes` mode with a full environment-variable contract and validation of every required input.
- [x] Identity prompts (organization, domain, internal zone, GitLab group, Keycloak realm, TLS secret, Access and GitOps inputs), re-read from `infra/swirl-cloud-identity` on reruns.
- [x] Install scope `apps` (includes Odoo in `corp`) or `infra`; both create the `apps` foundation.
- [x] Server exposure `local` or `internet`; an enrolled node's recorded `local` exposure is preserved.
- [x] Component selection with automatic dependency closure.
- [x] Odd control-plane and final-node-count planning; schedulable or controller-only control planes.
- [x] HA activation gate (≥3 control planes, ≥3 workers when controller-only) through `resolve-ha-profile.py`.
- [x] K3s installed pinned, bundled Traefik disabled, secrets encryption on, kubeconfig `0600`.
- [x] Embedded-etcd preparation when more than one control plane is planned (`configure-k3s-ha.sh`).
- [x] Read-only legacy migration check before any Kubernetes change (`check-platform-migration.sh`).
- [x] Ordered platform deployment from namespaces and hardening through TLS, storage, ingress, Vault/ESO, data stores, platform services, SSO, CI, Odoo, Argo CD and repository onboarding.
- [x] Ephemeral GitLab administrator token, revoked on exit; hidden secret prompts and credential cleanup on exit.
- [x] Final summary shows Longhorn replicas from the topology and points Argo CD and GitLab to Keycloak SSO; a GitLab local root password exists only in Vault after `rotate-local-admin-passwords.sh`.
- [x] Prerequisites limited to runtime and validation tools (developer tools only with `INSTALL_DEVELOPER_TOOLS=true`); Helm installed from the pinned SHA-256-verified release, no remote setup scripts (`get-helm-3`, NodeSource). *(validated with v3.22.0, 2026-10-04)*

### 3. Ansible

- [x] `ansible/install.yml` runs the shell installer locally with `no_log` and optional Vault-encrypted overrides.
- [x] `ansible/deploy.yml` reconciles an installed control plane from its recorded identity, exposure and HA, with contract, migration and capacity checks.
- [x] Opt-in private transport (Tailscale, or vRack with optional OVH API attachment) before firewall changes.
- [x] Feature switches with dependency closure (`install_longhorn` … `manage_private_transport`).
- [x] `platform_migration_approved` gate; password alignment and repository onboarding with `no_log`.

### 4. Hosts and operating-system security

- [x] Host policy published as `infra/host-security-config`.
- [x] UFW default-deny inbound on every node; Kubernetes/Docker forwarding restricted on non-cluster interfaces. *(live)*
- [x] Origin lock: public HTTP/HTTPS only from Cloudflare ranges. *(live)*
- [x] Fail2ban on internet-facing control planes (3 attempts / 1 h, escalating bans up to 30 d). *(live)*
- [x] CrowdSec with firewall bouncer, 7-day bans and custom SSH scenarios. *(live)*
- [x] SSH hardening (`PermitRootLogin no`, `MaxAuthTries 3`, connection limits).
- [x] Private nodes accept SSH only from registered control-plane addresses (`reconcile-control-plane-access.sh`).
- [x] Journald bounded; duplicate logrotate and unused `rpcbind` removed.
- [x] Lynis on control planes monthly (`swirl-cloud-lynis.timer`), reports to Kibana "Lynis Security Audits". *(live)*
- [x] Worker Lynis audits over transient SSH (`swirl-cloud-audit-nodes`).
- [x] Enforced AppArmor profile for K3s containerd.
- [x] Longhorn host prerequisites (open-iscsi, NFS client, multipath blacklist).
- [x] Internet-only tools kept off workers.
- [ ] Internet-facing control planes keep `PasswordAuthentication yes` on a publicly reachable port 22 (live UFW shows Fail2ban banning many sources). Move to key-only authentication, or restrict SSH to the Tailscale interface plus an out-of-band console.
- [x] Only `swirl-cloud-lynis.timer` schedules Lynis (distro timer masked); runs export metrics, and `LynisAuditFailed`/`LynisAuditMissing` alert. *(live, 2026-10-04)*
- [x] Unattended security updates (no automatic reboot); `NodeRebootRequired` alerts after seven days pending. *(live, 2026-10-04)*
- [ ] Define maintenance windows. Verify recovery and console access before the single-node reboot; add quorum/PDB-aware reboot coordination after multi-host drills (§20, §25).
- [x] `auditd` rule set and weekly report-only AIDE and rkhunter in the host baseline (§20). *(live, 2026-10-04)*
- [ ] Assess a full EDR only if those checks leave a concrete detection gap.
- [ ] CrowdSec parses only SSH/Linux logs; Traefik access logs are not acquired, so there is no in-cluster HTTP IDS behind Cloudflare. *(from `todo.txt`: "EDR / IDS / WAF")*
- [x] Host firewall cannot stay off after a reconcile: UFW is verified after enabling, rules reload on failure, otherwise fail closed; `HostFirewallDisabled` and `--status` detect it. *(2026-10-04)*
- [x] Kubernetes API audit logging (metadata-only policy, `configure-k3s-audit.sh`), shipped to `kubernetes-audit-*` with 30-day retention. *(live, 2026-10-04)*

### 5. Private node networking and enrollment

- [x] Three transports: OVHcloud vRack, Tailscale and a local LAN; application clusters remain Tailscale-only.
- [x] Tailscale role tags and grants merged into the tailnet policy, one-use tagged keys, K3s bound to `tailscale0`. *(live)*
- [x] Shared account/transport wizard (`scripts/lib/transport-guide.sh`) for installer and node assistant.
- [x] `configure-k3s-control-plane-network.sh` binds K3s to the private address, keeping the public route.
- [x] `add-node.sh`: role × mode, counts, address lists, non-interactive flags, token on stdin only.
- [x] Remote enrollment with identity checks, odd-quorum planning and sequential Ready joins.
- [x] SQLite → embedded-etcd conversion with an integrity-checked pre-conversion backup.
- [x] Registry mirror, AppArmor and storage prerequisites on each new node; conflicting roles rejected.
- [x] Topology reconciliation (`reconcile-cluster-topology.sh`): taints, Longhorn replica policy, anti-affinity and downgrade protection, state in `infra/swirl-cloud-topology`. *(live: 1 replica)*
- [x] CoreDNS private zone `<service>.<internal zone>` for `infra` Services.
- [x] Containerd routes `registry.<domain>` to the fixed internal Registry ClusterIP (`configure-k3s-registry-mirror.sh`).
- [ ] Only one host exists. Every service, the datastore and all backups share one physical failure domain (see §15 and §16).
- [x] Stable private Kubernetes API endpoint ([ADR 0028](docs/adr/0028-kubernetes-api-endpoint.md), `configure-k3s-api-endpoint.sh`); failover drill in §25. *(not live: one control plane)*

### 6. Kubernetes security baseline and admission

- [x] Namespaces `infra`, `apps`, `corp`, `gitlab-runners`, `longhorn-system`, `kube-system` and registered environments. *(live)*
- [x] Pod Security Admission `baseline` enforced (audit/warn `restricted`) on `apps`, `corp`, `gitlab-runners` and environment namespaces.
- [x] Admission policies `deny-hostpath-on-isolated-namespaces` (label-bound, including environment namespaces) and cluster-wide `deny-gitrepo-volumes` (CVE-2025-1767). *(live)*
- [x] `MutatingAdmissionPolicy` hardening for K3s and Longhorn controllers, re-applied by `reconcile-system-hardening.sh`.
- [x] Default-deny ingress NetworkPolicy in `apps`, `corp` and environment namespaces.
- [x] Workload hardening across platform manifests (non-root, seccomp, dropped capabilities, read-only roots, no token automount, resource limits).
- [x] The three host telemetry agents, the only `baseline` violators in `infra`, run in the privileged `host-agents` namespace ([ADR 0025](docs/adr/0025-host-agent-namespace-and-infra-pod-security.md)); a `baseline` dry run on `infra` reports no violating pods. *(live, 2026-10-04)*
- [ ] Enforce `baseline` on `infra`. First keep Trivy's node-collector working: exempt its service account in the K3s Pod Security admission configuration during a maintenance restart, or disable infra assessment. Its job needs host PID and hostPath and always runs in `infra`.
- [ ] Introduce default-deny ingress in `infra` with explicit service/port allowlists for platform dependencies, app data, CI, ingress, metrics and remote gateways. Current source has only selected service policies. Prove required traffic and denied cross-environment/admin access before rollout; stage egress restrictions separately with DNS/API/external dependencies accounted for.

### 7. Ingress, TLS and edge (Traefik + Cloudflare)

- [x] Platform-owned Traefik Helm release (v3.7.13, pinned digest) replaced ingress-nginx; K3s bundled Traefik disabled; native Ingress kept for apps.
- [x] Direct mode via K3s ServiceLB on the bootstrap control plane: HTTPS redirect, TLS ≥ 1.2, shared default certificate, unprivileged hardened pod.
- [x] 900 s timeouts for long uploads and streams.
- [x] Forwarded-header normalization and `ingress-proxy-trust` shared with GitLab.
- [x] Platform middlewares `platform-sso`, `platform-body-limit` (1 MiB) and `keycloak-root`; cross-namespace references disabled.
- [x] Ingresses for every platform UI, GitLab, Registry, Keycloak, Vault, Homepage and Odoo.
- [x] Admission rule validating environment Traefik Middleware plugins (`0c032c0`).
- [x] Candidate mode (`INGRESS_MIGRATION_CANDIDATE=true`) for rehearsing Traefik alongside the old controller.
- [x] Cloudflare configurator (`configure-cloudflare.sh`): proxied DNS, Origin CA certificate, Full (strict) and edge TLS settings, DNSSEC, WAF and login rate-limit rules, cache rules and Keycloak-backed Access for admin hosts.
- [x] Origin CA certificate valid until 2041 in `infra`, `apps` and `corp`. *(live)*
- [x] HA ingress profile: Traefik DaemonSet with co-located `cloudflared`, connector and origin checks before the DNS switch.
- [x] Application DNS ownership: onboarding publishes A records or tunnel CNAMEs and refuses conflicts.
- [ ] HA tunnel ingress has never been activated or failover-tested (`swirl-cloud-public-ingress` does not exist live).
- [x] Traefik metrics scraped; Grafana **Ingress Traffic** dashboard and `IngressServiceErrorRateHigh`. *(live, 2026-10-04)*
- [ ] No HSTS or security-header middleware at the origin; header policy relies entirely on Cloudflare and each app. *Progress 2026-10-14:* source ready: `infra/security-headers` on the `websecure` entry point (`config/traefik-values.yaml`) repeats the edge's HSTS (`CLOUDFLARE_HSTS_MAX_AGE`, subdomains, no preload; none when `CLOUDFLARE_ENABLE_HSTS=false`) and `nosniff`; local installations send only `nosniff`. Argo CD does not manage Traefik, so it applies when the operator reruns `./scripts/configure-ingress.sh` with the zone's HSTS inputs (remote application clusters: rerun `scripts/configure-deployment-environments.py` registration). Close with the [origin check](docs/dns.md#ingress-controller): `kubectl -n infra get middleware security-headers` exists, and Grafana's `/login` fetched through the origin returns `strict-transport-security: max-age=31536000; includeSubDomains` and `x-content-type-options: nosniff`.
- [x] Traefik JSON access logs without query strings or extra headers, shipped to `kubernetes-logs-v2-*`. *(live, 2026-10-04)*

### 8. Storage (Longhorn)

- [x] Longhorn 1.12.1, default StorageClass, topology-driven replica count. *(live: 20 healthy volumes)*
- [x] Weekly filesystem trim RecurringJob `swirl-cloud-weekly-trim`.
- [x] Longhorn UI behind Keycloak SSO and Cloudflare Access.
- [x] HA storage policy (no soft anti-affinity, no force detach) via `configure-ha-control-planes.sh`.
- [x] Optional recurring off-node Longhorn backups to the restic S3 bucket; host backup enrolls new volumes.
- [ ] Activate and restore-test off-node Longhorn backups (§16). The 2026-09-27 audit found no daily backup job or configured `default` BackupTarget; repeat that check before declaring coverage.
- [x] `configure-k3s-backups.sh` configures and checks Longhorn's `default` BackupTarget; activation waits for the §16 target. *(2026-10-04)*
- [x] Longhorn metrics scraped; `LonghornVolumeDegraded` and `LonghornVolumeFaulted` alerts. *(live)*
- [x] Snapshot deletion cleanup (`9e736db`, 2026-09-28).
- [x] `gitlab-runner-cache` is in use by the instance runner, so its `unknown` robustness between jobs is expected. *(live, 2026-10-04)*

### 9. Secrets (Vault + External Secrets)

- [x] Vault 2.1.0 (chart 0.34.1, pinned digest) on integrated Raft, hardened pod.
- [x] Two audit devices (stdout and a node-local file with a token-less rotator sidecar).
- [x] `configure-vault.sh`: init, KV-v2, Kubernetes and Keycloak OIDC auth, policies, seeded credentials.
- [x] Host unseal timer `swirl-cloud-vault-unseal.timer`, key passed over stdin, never initializes a cluster. *(live)*
- [x] Recovery material root-only in `/var/lib/swirl-cloud/`; `sync-vault-recovery.sh` distributes it to verified control planes.
- [x] External Secrets Operator 2.9.0 with `vault-backend` and per-environment stores and auth mounts.
- [x] Vault UI dark theme through a separate `/ui`-only helper.
- [x] HA overlay: three Raft voters, guarded singleton migration (`configure-vault-ha.sh --verify`).
- [x] Local administrator password rotation (`rotate-local-admin-passwords.sh`), persisted in Vault.
- [x] Vault telemetry scraped; `VaultSealed` and `VaultAuditDeviceFailing` alerts. *(live)*
- [ ] The unseal key and the root/bootstrap token sit on the same host as the data, and both are copied into every local backup archive (see §16).
- [ ] Vault HA has never been exercised (single node).

### 10. Data stores

- [x] PostgreSQL 18.6 single writer, hardened, 2 Gi Longhorn PVC `postgres-v18-pvc`.
- [x] MongoDB 7.0.40 single instance, 5 Gi PVC.
- [x] Redis 8.10.0 single instance with an ACL file. *(live)*
- [x] Kafka KRaft with one controller and one broker.
- [x] Authenticated application data profile (`configure-application-data.py`): per-environment PostgreSQL, Redis ACL and Kafka SCRAM users with prefixes and NetworkPolicies. *(live)*
- [x] PostgreSQL HA profile (CloudNativePG 1.30, synchronous standbys) with a journaled migration helper.
- [x] Kafka HA profile (dynamic quorum, three controllers and brokers) with a journaled migration helper.
- [x] Shared Redis HA (`redis-ha`: Sentinel and HAProxy).
- [x] PVC space alerts at 20 % / 10 % free and filling within a day. *(live, 2026-10-04)*
- [ ] PostgreSQL PVC is only 2 Gi for Keycloak, SonarQube, Odoo, GitLab-adjacent and all application databases; plan its capacity with §26.
- [ ] Set PostgreSQL RPO/RTO and implement continuous WAL archiving/PITR if daily `pg_dumpall` is insufficient. Prove recovery to a chosen timestamp; HA replication does not protect against accidental data changes (§16).
- [ ] Kafka's in-cluster `PLAINTEXT` listener (9092) stays unauthenticated with `KAFKA_SUPER_USERS=User:ANONYMOUS` and `auto.create.topics.enable=true`. NetworkPolicy limits it to `infra`, `apps` and `corp`, so every legacy app and platform pod is an anonymous Kafka superuser. Migrate legacy clients to SCRAM and close 9092 to `apps`.
- [ ] Generate and persist a Kafka cluster ID on new installations; reuse the recorded ID on reruns and migrations. Never replace the hard-coded ID on an existing data directory.
- [ ] Decide whether Redis key-name visibility through `KEYS`/`SCAN` is acceptable for shared environments; document the boundary or use separate instances where metadata isolation is required.
- [x] Datastore exporters for PostgreSQL, MongoDB, Redis and Kafka; `DatastoreDown` alerts. *(live)*
- [ ] PostgreSQL/Kafka HA migrations have only been tested offline and against an isolated SQL canary; no three-host rehearsal.

### 11. Identity and SSO (Keycloak + OAuth2 Proxy)

- [x] Keycloak 26.7.3 on PostgreSQL at `/auth`, health and metrics endpoints.
- [x] `keycloak-sso-reconcile` Job: organization realm policies, user profile, platform roles, `platform-admins` group and bootstrap administrator.
- [x] Clients reconciled for OAuth2 Proxy, GitLab, Cloudflare Access, Grafana, Vault, Kafka UI, Portainer, Odoo and Argo CD (PKCE).
- [x] OAuth2 Proxy as Traefik ForwardAuth for DBGate, Kibana, Longhorn, SonarQube and Homepage.
- [x] Native OIDC for GitLab, Grafana, Argo CD, Vault, Kafka UI, Portainer and Odoo.
- [x] Kibana signed-session proxy and `kibana-keycloak-user-sync` CronJob.
- [x] SonarQube and GitLab SSO administrator bootstrap.
- [x] Cloudflare Access identity provider backed by Keycloak, email allowlist.
- [x] Administrator second factor optional by decision; `./install.sh --admin-mfa status|require` ([security](docs/security.md#administrator-second-factor)). *(2026-10-05)*
- [ ] Odoo's client uses the OAuth2 implicit flow (Odoo 19 `auth_oauth` limitation); replace with an authorization-code add-on when available.
- [x] Keycloak emails through the platform mail relay (§27). *(live, 2026-10-09)*
- [x] Keycloak metrics scraped; `KeycloakPasswordFailuresHigh` alert. *(live, 2026-10-04)*
- [ ] Keycloak runs one replica outside HA (§15).

### 12. Source, CI and registry (GitLab + Runner)

- [x] GitLab CE 19.3.1 Omnibus (digest-pinned, amd64), bundled NGINX and Registry, Let's Encrypt and KAS off, 2 Puma workers, Sidekiq 10; bundled Prometheus and exporters disabled (the cluster Prometheus scrapes `/-/metrics`).
- [x] Visitor IP preservation from `ingress-proxy-trust`.
- [x] Persistent `gitlab-config`, `gitlab-data` and `gitlab-logs` PVCs.
- [x] Dependency Proxy, provisioning, retention policies and runner registration by `configure-gitlab-ci.sh`.
- [x] GitLab Runner 19.3.1 (Kubernetes executor, `concurrent = 1`, per-job limits) with metrics and a "GitLab Delivery" dashboard.
- [x] Project-scoped integration and protected-release runners for version-2 apps (`application_delivery.py`).
- [x] Daily `gitlab-registry-retention` CronJob, skipping projects with packages disabled (`6ada82b`).
- [x] Alertmanager webhook into the infrastructure project's **Monitor → Alerts**.
- [x] Platform pipeline `.gitlab-ci.yml` (`validate:repository`, `verify:gitops`, `verify:delivery-platform`, `verify:registry-write`).
- [ ] Automate GitLab native backup plus matching configuration, secrets, registry files and metadata DB where enabled; upload offsite and restore with the matching GitLab version. Verify repository, LFS, package and Registry push/pull recovery, then integrate backup age/failure alerts (§16). *Progress 2026-10-14:* configuration and secrets source ready: `scripts/backup-k3s.sh` adds `/etc/gitlab` (`gitlab/etc-gitlab.tar`, mode 600) and `gitlab/version.txt` to the nightly archive and fails the run without publishing an archive if the copy fails ([backups](docs/operations.md#backups-and-recovery)). Applies when the operator reruns `./scripts/configure-k3s-backups.sh` on the control plane; then the newest archive must list `etc-gitlab.tar` as `-rw-------` beside `version.txt`. Remaining: the native backup of repositories and database (scope under [Needs Input](#needs-input)), its off-site upload and a restore drill with the same version.
- [ ] Identify the installed Registry metadata mode and implement its supported garbage collection: legacy collection with writes stopped/read-only, or monitored online GC with the metadata DB. Protect deployed and rollback digests before tag pruning, test pullability after cleanup, and measure reclaimed bytes; see [GitLab GC](https://docs.gitlab.com/administration/packages/container_registry/#container-registry-garbage-collection).
- [ ] `concurrent = 1` on the instance runner serializes every platform job; revisit once more nodes exist.
- [ ] GitLab is a persistent singleton with no replica; recovery depends on Longhorn plus (future) fencing.

### 13. GitOps (Argo CD)

- [x] Argo CD 3.5 via Helm: Dex and local `admin` disabled, Keycloak OIDC with PKCE, `platform-admins` → `role:admin`.
- [x] Controller `GOMEMLIMIT=768MiB` under a 1 Gi limit; hardened security contexts.
- [x] HA overlay with paired servers, Redis Sentinel and PDBs.
- [x] Root Application `swirl-cloud` with automated sync, prune and self-heal. *(live)*
- [x] Repository credentials through ExternalSecrets; AppProjects per environment (`6bf088d`); admission binding environment Applications to their destination.
- [x] Sonar discovery skips application projects with CI/CD disabled.
- [x] Argo CD release and recovery ownership documented ([operations](docs/operations.md#argo-cd-itself-is-unhealthy-or-lost)), reconciled by `reconcile-platform-release.sh argocd`. *(live, 2026-10-04)*
- [x] Root and all eight application Applications `Synced/Healthy`, platform CronJobs and default-branch verification pipelines passing. *(live, 2026-10-04)*
- [x] `ArgoCDApplicationUnhealthy` and `ArgoCDApplicationOutOfSync` alerts; Argo CD metrics scraped. The notifications controller waits for a receiver (§27, §29). *(live, 2026-10-04)*
- [ ] Exercise Argo CD controller replacement and reconciliation recovery under the configured dynamic cluster distribution; record its upstream support status and failover result before claiming HA (§15).

### 14. Observability

- [x] Prometheus (15 s scrape, 15 d / 1 500 MB retention) with kube-state-metrics, node-exporter, kubelet and cAdvisor.
- [x] Base alert rules; Alertmanager grouped by alert name and namespace.
- [x] Grafana 13.2.1 with Keycloak OIDC, Prometheus and Elasticsearch data sources and platform dashboards.
- [x] Application discovery sidecar generating Grafana dashboards and Kibana saved objects per application.
- [x] ELK 9.4.6: Elasticsearch, Kibana behind SSO, Logstash for Lynis, Fluent Bit and Filebeat.
- [x] Retention: container logs deleted by ILM after 1 d, Lynis indices 365 d; GitLab's bundled Prometheus is disabled.
- [x] Trivy Operator with an offline Trivy server, compliance reports every 6 h and a Grafana dashboard.
- [x] SonarQube 26.8 Community with SSO and the `sonar-apps-discovery` CronJob.
- [x] Critical and warning alerts emailed through the platform mail relay (§27), with `AlertNotificationsFailing`. *(live, 2026-10-09)* Paused since 2026-10-11 (next item).
- [ ] **Waiting for the owner's approval and input:** reactivate Alertmanager notifications, paused on 2026-10-11 because they were too frequent. Every alert now goes to the `paused` receiver (no GitLab alert, no email); the `gitlab` and `email` receivers remain configured. Do not restore them until the owner approves and says which notifications to bring back, then restore the routes in `k8s/platform/monitoring.yaml` and `tests/test-platform-mail.py` and the paused notes in `docs/operations.md`, `docs/features.md` and `docs/guides/platform-mail.md`.
- [x] Fluent Bit keeps Kubernetes metadata for CI job logs and skips Trivy report payloads. *(live, 2026-10-04)*
- [x] Host metrics through node-exporter's textfile collector (Lynis, pending reboots, rkhunter). *(live, 2026-10-04)*
- [x] Elasticsearch green again (replica counts fixed). *(live, 2026-10-04)*
- [x] Kibana **Lynis Findings** search fixed. *(live, 2026-10-04)*
- [x] Platform alerts for nodes, Vault, Longhorn, datastores, Argo CD and TLS certificate expiry. *(live)*
- [x] Backup and unseal services record each run; `HostMaintenanceServiceFailed`, `K3sRecoveryBackupMissing` and `VaultUnsealTimerStopped` alert. *(live, 2026-10-04)*
- [x] Nightly backup verified, including the Odoo filestore and database dumps. *(2026-10-05)*
- [x] External Secrets scraped; `ExternalSecretNotReady` alert; orphaned `platform-registry-auth` ExternalSecrets removed. *(verified 2026-10-05)*
- [x] Elasticsearch exporter with a monitor-only user and cluster-health alerts. *(live, 2026-10-04)*
- [x] `LonghornVolumeBackupStale` alert once the backup target holds backups. *(2026-10-04)*
- [x] CrowdSec/Fail2ban metrics and `HostIntrusionPreventionDown`, `HostIntrusionMetricsStale` and `HostBruteForceWave` alerts. *(live, 2026-10-04)*
- [x] `TrivyNewCriticalVulnerability` alerts against each image's own baseline; its 6 h window fits today's ~12 h Prometheus history, widen it with §26. *(2026-10-04)*
- [x] Scraped: Traefik, Vault, Argo CD, Longhorn, PostgreSQL, MongoDB, Redis and Kafka.
- [x] cloudflared tunnel connectors are scraped (job `cloudflared`, named port `tunnel-metrics` in [traefik-ha-values.yaml](config/traefik-ha-values.yaml)) and covered by `PrometheusTargetDown`; untested until HA ingress is active. *(2026-10-26)*
- [ ] Define remote-cluster telemetry ownership and add authenticated metrics/log shipping, cluster/environment labels, quotas and node/backup alerts before relying on remote production targets (§19).
- [ ] Define recoverable Elasticsearch log/audit retention and configure a snapshot repository if that history must survive volume loss (§16).
- [x] Outage watch from outside the cluster: a Cloudflare Worker checks public hosts every five minutes and emails changes ([ADR 0030](docs/adr/0030-outage-watch-outside-the-cluster.md), [guide](docs/guides/service-levels.md#outage-watch)). *(live, 2026-10-09)* Deactivated since 2026-10-26 (next item).
- [ ] **Waiting for the owner:** re-enable the outage watch, deactivated on 2026-10-26 at the owner's request because of its endpoint down/up emails. `scripts/configure-outage-watch.py --remove` deleted the Worker and its state, so nothing checks the public endpoints from outside the cluster and Alertmanager is paused too (above). To re-enable, run `scripts/configure-outage-watch.py` with `CLOUDFLARE_API_TOKEN` exported ([guide](docs/guides/service-levels.md#outage-watch)). Until then, rerun `install-control-plane.sh` without Cloudflare or platform mail configuration, or remove the Worker again afterwards: with both configured it redeploys the watch.

The historical Trivy snapshot was **119 critical / 924 high findings across 90
image reports**, plus 11 critical exposed-secret findings in the GitLab image
and stale retired-image reports. Refresh and triage these in §21; they are not
current totals or three additional remediation projects.

### 15. High availability (opt-in, not active)

- [x] Durable availability mode in `infra/swirl-cloud-topology`, inherited by every entry point.
- [x] HA templates for CoreDNS, Keycloak, Redis, PostgreSQL, Kafka, Traefik/Tunnel, Vault, ESO and Argo CD.
- [x] Read-only readiness checker `verify-high-availability.py`.
- [x] Optional Redfish node fencing CronJob with quorum rechecks and RBAC limited to named nodes.
- [ ] Complete the topology and capacity prerequisites in §25: at least three eligible control planes and three storage hosts, with capacity after one host fails. The planned two-storage-host interim state cannot satisfy the current three-replica HA profile.
- [ ] Execute the ordered activation: PostgreSQL migration → Kafka migration → Vault migration and recovery distribution → shared HA profile with Cloudflare Tunnel cutover → application-owned availability.
- [ ] Run the controlled one-host failure drills (original control plane, database leader, consensus leader) and verify public requests, sessions, writes, Kafka, Vault and Registry push/pull.
- [ ] Acquire Redfish-capable BMC access and enable fencing, or document the manual non-graceful shutdown runbook as the permanent path.
- [ ] Application clusters: bootstrap supports only one control plane per target.

### 16. Backup and disaster recovery

- [x] Daily `swirl-cloud-k3s-backup.timer` with 7 local root-only archives and checksums. *(live)*
- [x] Archive contents: K3s datastore and credentials, Vault snapshot, `pg_dumpall`, `mongodump`, volume inventories and `RESTORE.txt`. *(live)*
- [x] Optional encrypted restic upload to S3-compatible storage.
- [x] Recovery tests for K3s backups, Vault unseal/HA and a PostgreSQL SQL canary.
- [ ] Configure offsite restic and Longhorn storage now using a target outside the cluster's host/site failure domain; do not wait for the future NAS (§31). Verify uploads, backup age and volume coverage after fixing the Longhorn integration (§8).
- [ ] **Operator action:** on `swirlit-node-01`, export the recovery kit (`scripts/export-recovery-kit.sh --separate-archives`), store the kit and its passphrase off the host in separate places, delete the local copy, and confirm with `--verify FILE` ([checklist](docs/operations.md#operator-recovery-checklist)). Repeat after any Vault, K3s or backup credential rotation.
- [ ] Separate Vault recovery keys and bootstrap tokens from routine data archives and keep an independently accessible recovery copy of the restic password, K3s token/encryption state and required credentials. Define automated-unseal and bootstrap-token privileges explicitly; a password manager hosted only on this cluster is not sufficient for recovery. *Progress 2026-10-04:* `scripts/export-recovery-kit.sh` exports and verifies an encrypted kit, and `--separate-archives` drops the Vault keys from nightly archives. Remaining: export a kit for `swirlit-node-01`, store it offline, opt into separation, and define automated unseal.
- [x] Backup coverage, RPO and RTO table in [operations](docs/operations.md#coverage-and-recovery-points); RTO unmeasured until the restore drill. *(2026-10-04)*
- [ ] Schedule `restic check` and isolated restore drills for both archives and Longhorn volumes, with integrity/data checks and alerts on missing or stale backups. Keep deletion protection compatible with restic pruning and Longhorn's own backup retention; do not apply generic object expiry to active backup stores. *Progress 2026-10-14:* weekly archive verification source ready (`swirl-cloud-k3s-backup-verify.timer`, [backups](docs/operations.md#backups-and-recovery)); `backup-k3s.sh` now exports `backup.env` to restic, waits for the lock, and groups `restic forget` by host and tag. Applies when the operator reruns `./scripts/configure-k3s-backups.sh` and `./scripts/configure-node-security.sh --host-baseline`. Remaining: the off-site target ([Needs Input](#needs-input)), a live timer run, a recorded isolated restore drill of an archive, an alert when the verification stops running (a rule in `k8s/platform/monitoring.yaml`), and the Longhorn volume drills.
- [ ] Write and rehearse a full rebuild on an empty host with the original host unavailable: obtain Git, pinned recovery images and keys independently; restore K3s/storage/Vault/data, then GitLab/Argo CD, SSO and application traffic. Measure achieved RPO/RTO and exercise the documented backup format before node migration.

### 17. Corporate workloads (Odoo in `corp`)

- [x] Odoo 19 in `corp` (single database, proxy mode, limits, NetworkPolicy, Longhorn filestore).
- [x] Bootstrap creates the database, company and managed administrator.
- [x] Keycloak SSO add-on and vendored OCA `web_dark_mode`.
- [x] Migrated from `infra` to `corp` (`0e16c84`).
- [x] Odoo mail through the platform relay (§27). *(live, 2026-10-09)*
- [x] Odoo filestore in the nightly recovery archive, after `pg_dumpall`. *(2026-10-05)*

### 18. Administration UIs and developer portal

- [x] Homepage v2.2.0 at `cloud.<domain>` behind Keycloak SSO with Kubernetes widgets and Ingress discovery.
- [x] Portainer CE with Keycloak OAuth.
- [x] DBGate with predefined connections behind SSO.
- [x] Kafbat Kafka UI with Keycloak OAuth2.
- [x] SonarQube and Vault dark mode toggles.
- [x] Descheduler in manual-run mode.
- [ ] Portainer runs with `cluster-admin`; consider a narrower role or read-only default for non-admin users.
- [ ] DBGate and Kafka UI grant full write access to every `platform-admins` member; no read-only role.

### 19. Application platform services (onboarding and environments)

- [x] `add-repos.sh` → `onboard-repositories.py`: GitHub import into GitLab, credentials, Keycloak clients, DNS and verified pipeline-driven deployment with a resumable journal.
- [x] GitHub ↔ GitLab sync workflow without force pushes, with a self-rotating project token.
- [x] Onboarding contract versions 1 (single target) and 2 (registered environments).
- [x] Deployment environments registered by `configure-deployment-environments.py`. *(live: `int`/`uat`/`prod` local)*
- [x] Remote application cluster bootstrap (`install-application-cluster.sh`).
- [x] `configure-application-delivery.py` for preparing CI before targets exist.
- [x] Six applications onboarded live.
- [ ] Refresh the app/contract/environment inventory, then migrate remaining version-1 apps in their owning repositories, including data, credentials, DNS and rollback. Verify isolated `int`/`uat`/`prod` delivery before retiring legacy credentials or Kafka access (§10).
- [x] GitLab is the delivery and metadata authority, GitHub a branches-and-tags mirror ([repository onboarding](docs/guides/repository-onboarding.md#synchronization-and-validation)). *(2026-10-04)*
- [ ] Back up issues, merge requests, releases, packages and LFS through native GitLab recovery (§12).
- [ ] Add the platform mail relay (§27) to the app onboarding contract, so applications can request scoped sending credentials; application repositories own their mail usage and templates.
- [x] Environment `ingressAddress` accepts hostnames (A, AAAA or CNAME); remaining alias and API endpoint work is in §25.

---

## Part III — Requested roadmap (added 2026-09-27, reviewed 2026-09-28)

The requested direction is retained below. Sub-steps now distinguish existing
source support, historical live findings, prerequisites and decisions still open.
Decisions marked **ADR** need a record in [`docs/adr/`](docs/adr/) first (see the
proposed list in [architecture](docs/architecture.md#proposed-adrs-for-roadmap-work)).

- [ ] Keep services versions updated *(perpetual; never marked done)*.
- [ ] Integrate Renovate for version bumps across the whole cluster and all apps: platform images, charts and K3s in this repository, plus the application repositories (Swirl Demo App, Indezy, Thoughty, Swirl Website).

### 20. Remediate Lynis findings (light-touch, non-blocking)

Historical control-plane audit on 2026-09-27: hardening index **66**, one warning, about 40
suggestions. Apply through [`configure-node-security.sh`](scripts/configure-node-security.sh)
so new nodes inherit the result, then rerun Lynis and `swirl-cloud-audit-nodes`.

- [x] Lynis refreshed: index 66, one warning (KRNL-5830). *(live, 2026-10-04)*
- [x] **KRNL-5830:** newest installed kernel running, no reboot pending; later kernels raise `NodeRebootRequired`. *(verified 2026-10-05)*
- [x] **SSH-7408:** session limits in `41-swirl-cloud-sessions.conf`; `MaxSessions 4` and port 22 kept by design. *(live, 2026-10-04)*
- [x] **BANN-7126/7130:** legal banners through `configure-node-security.sh --host-baseline`. *(live, 2026-10-04)*
- [x] **DEB-0280, DEB-0811, PKGS-7370, PKGS-7394:** `libpam-tmpdir`, `apt-listchanges`, `debsums` (report-only) and `apt-show-versions`. *(live, 2026-10-04)*
- [x] **AUTH-9262:** `pam_pwquality` for new passwords. *(live, 2026-10-04)*
- [x] **AUTH-9230 / AUTH-9328 accepted:** yescrypt is stronger than the checked SHA-512 rounds; `UMASK 027` would break non-root image builds. Recorded in the Lynis profile.
- [ ] **AUTH-9282/9286:** password ageing only for human accounts, never for service accounts (optional once SSH is key-only).
- [x] **NETW-3200:** `dccp`, `rds`, `sctp` and `tipc` blocked. *(live, 2026-10-04)*
- [ ] **USB-1000:** `usb-storage` stays enabled because remote consoles attach recovery media as USB. Blacklist it only on hosts whose console recovery is proven not to need it, and record that as an accepted Lynis finding (§20 profile).
- [x] **PKGS-7346:** leftover configuration of removed packages purged. *(live, 2026-10-04)*
- [x] **KRNL-6000:** sysctl hardening in `90-swirl-cloud-hardening.conf`; values Kubernetes or console recovery need are kept. *(live, 2026-10-04)*
- [x] **FILE-7524:** restrictive cron and sshd permissions, kept across upgrades. *(live, 2026-10-04)*
- [x] **ACCT-9622/9628:** `acct` and an `auditd` watch rule set (≈59 MB/day). *(live, 2026-10-04)*
- [x] **FINT-4350:** weekly report-only AIDE; run failures alert through `HostMaintenanceServiceFailed`. *(live, 2026-10-04)*
- [x] **HRDN-7230:** weekly report-only `rkhunter` with `swirl_cloud_rkhunter_warnings`. *(live, 2026-10-04)*
- [x] **LOGG-2154:** Fluent Bit forwards each node's journal to `kubernetes-logs-host-*`; an off-host copy depends on the snapshot repository (§14/§16). *(live, 2026-10-04)*
- [x] Accepted findings recorded in `/etc/lynis/custom.prf` (review 2027-04-04); hardening index 66 → **75**. *(live, 2026-10-04)*

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

- [x] Critical findings refreshed for the running digests and triaged; dated exceptions (expiring 2026-11-04) in [security](docs/security.md#vulnerability-triage). *(live, 2026-10-04)*
- [x] Fixed by upgrades: Keycloak 26.7.5 (0 critical), GitLab Runner 19.4.1 (OpenSSH), Node helper `node:24.21.0-alpine`. *(2026-10-04)*
- [x] Recorded as dated exceptions with their recheck condition: Argo CD v3.5.3 (Go `crypto/tls`), Kafka UI v1.5.0, SonarQube 26.9.0, Elastic 9.5.4, the runner helper's `miniperl`, Longhorn `csi-provisioner`, MongoDB tools, Homepage, DBGate and PostgreSQL Debian libraries ([security](docs/security.md#vulnerability-triage)). *(2026-10-04/05)*
- [x] Exposed-secret findings triaged; no real credential to rotate ([security](docs/security.md#exposed-secret-findings)). *(2026-10-05)*
- [x] No stale reports for retired owners remain. *(live, 2026-10-03)*
- [x] Non-blocking CI job `report:platform-artifacts` scans the vendored charts and the CI image. *(2026-10-04)*
- [x] Application image findings handed to the owning repositories' **Bugs to fix**: TravelMapster (Debian bookworm runtime base), Thoughty and Indezy (`postgres:18.6-bookworm` helper image, an amd64-only 2026-08-13 build). *(2026-10-26)*

### 22. Odoo for the company website (CRM leads and Live Chat)

The website owns its form, consent text and chat button; the platform owns Odoo
and the wiring. CRM and Live Chat serve only the company website
([Odoo website integration](docs/guides/odoo-website.md)).

- [x] Odoo bootstrap installs CRM, Live Chat and the `platform_crm_intake` add-on, with an English/French chatbot that turns offline visits into leads. *(live, 2026-10-07)*
- [x] Lead intake through a portal user whose key (Vault `apps/<website>/odoo`) can only call the idempotent `crm.lead/platform_intake_submit`; only labelled `apps` pods reach Odoo. *(live, 2026-10-07)*
- [x] Public Live Chat framed only by the website origin (strict CSP kept), with the Cloudflare rate limit covering chat session starts. *(live, 2026-10-07)*
- [x] Chat by email with signed plus-addresses and a sender allowlist, through the Cloudflare Email Worker `platform-chat-email` (`configure-odoo-website.py --email-worker`). *(live, 2026-10-08)*
- [x] Chatbot persona from the website's `chat-persona.json`, online indicator and closing message. *(2026-10-09)*

### 23. Installer: TUI, central `.env`, reruns, verification and email

Goal: one versioned `.env` drives every entry point (TUI now, web UI next,
Ansible and unattended runs); the installer engine reads only that file and never
prompts. **ADR** on the file schema and TUI technology.

- [x] Versioned installation-input schema; per-install `.env` values parsed as data, never shell code ([ADR 0026](docs/adr/0026-installer-input-file.md)).
- [x] Engine mode `--env-file` (no prompts, every invalid key reported at once, `--validate-only`); registrar nameserver delegation still needs a person.
- [x] TUI front end `scripts/install-tui.py` (whiptail) with **express** and **advanced** paths, writing the `.env` (`0600`).
- [ ] Define secret resolution before adding redaction: fresh bootstrap and disaster recovery must work while Vault/Keycloak are unavailable. Support protected files/stdin and authenticated Vault references, redact logs/reports, and replace inline secrets only after durable recovery and rerun access are verified (§16); never require a secret from the Vault being bootstrapped. *Progress 2026-10-04:* `NAME_FILE=/path` references, `--secrets-stdin` and redaction in hub run logs exist; Vault references for reruns remain.
- [x] Rerun planning: `./install.sh --plan` and the hub's Plan screen show drift against pins, plan and identity; reruns resume because every stage is idempotent.
- [x] Post-install verification `scripts/verify-platform.py` (`./install.sh --status`, `--strict`, `--json`, `--api-dry-run`) at the end of every run. *(first live run 2026-10-04)*
- [ ] Email the report when the deployment is ready, with dashboard URLs for every service and application (from Ingress hosts and Homepage), through the platform mail relay (§27).
- [x] One entry point `./install.sh` opening the setup hub: preflight, credential tests, install, servers, repositories, Maintenance menu and `--support-bundle`.
- [x] `ansible/install.yml` and `--yes` consume the same `.env` (`installer_env_file`).
- [ ] After the engine/TUI is proven, design the optional Keycloak-protected web front end around constrained jobs, authorization, redacted logs and a single execution lock. Keep console-based bootstrap/recovery independent of that UI.
- [ ] **Operator action:** run `./install.sh` in a real terminal on `swirlit-node-01` and check every hub screen renders and navigates (so far only driven with scripted dialog answers; see [after installation](docs/deployment.md#after-installation)).
- [ ] Rehearse fresh install, no-change rerun, interrupted resume, invalid inputs and Vault-unavailable recovery on disposable hosts (the input file and TUI have only offline tests and scripted dialog runs so far); add focused safety tests only for credential leakage or deployment/recovery failures under the existing test policy.

### 24. Deployment UI (app → environment → deploy)

- [ ] Small web app in `infra` behind Keycloak and Cloudflare Access, listed on Homepage, added to the host inventories. (The terminal flow above covers the deployment logic; the web front end remains.)
- [x] `scripts/deploy-application.py` and the hub's Deploy screen list applications with revision, health and URL per environment; valid choices come from the onboarding contract and `infra/deployment-environments`.
- [x] Deploy through the app's pipeline contract with the release shown first, one active deployment per environment and release gates, reporting success only once the Argo revision is healthy. Uses a per-run one-day GitLab token instead of a Vault-scoped token (revisit with the web front end). *(verified end to end, 2026-10-04)*
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
- [ ] **Private API endpoint:** phase 1 enrollment sets up the endpoint (a Tailscale Service, or a VIP from `--api-vip` on vRack) (ADR 0028). Write an admin kubeconfig with `configure-k3s-api-endpoint.sh --kubeconfig` and prove kubectl through it from an administrator device with the original server down, then with each new server down in turn.
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
- [ ] Elasticsearch heap and volume (10 Gi → 50 Gi+); container log retention from 1 day to 7–14 days (the delete `min_age` of `swirl-cloud-logs`, changed in place); keep Lynis at 365 days.
- [ ] Prometheus retention from 15 d / 1 500 MB to about 30 d / 20 GB. GitLab's bundled Prometheus is disabled (`k8s/platform/gitlab.yaml`); the cluster Prometheus holds GitLab's metrics, so this retention covers them. Measured 2026-10-04: the size cap leaves only about 12 h of history, which limits trend and baseline alerts; this node's 48 GiB free (30 GiB maintenance floor) cannot absorb 20 GB, so it waits for the new servers.
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
- [x] Transactional SMTP relay ([ADR 0029](docs/adr/0029-platform-transactional-mail.md), [platform mail](docs/guides/platform-mail.md)): checked and stored in Vault `infra/smtp`, used by Alertmanager, Keycloak, GitLab and Odoo; live relay is a domain-restricted Resend key sending as `cloud@swirlit.dev`. *(live, 2026-10-09)*
- [ ] Argo CD notifications (sync and deployment messages) through the platform mail relay; Alertmanager already emails unhealthy or out-of-sync Applications. Applications (§19) and the installer report (§23) are the other remaining consumers.

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

**Market context (reviewed 2026-10-23).** Cyber Resilience Act reporting has
applied since 2026-09-11, and its product requirements apply from 2027-12-11
([Commission, 2026-09-11](https://commission.europa.eu/news-and-media/news/safer-and-more-secure-digital-products-2026-09-11_en)).
The Data Act's switching rules have covered cloud contracts since 2025-09-12,
and switching charges are banned from 2027-01-12
([Bundesnetzagentur](https://bundesnetzagentur.de/EN/Areas/Digitalisation/DataAct/background/3-provider/start.html)).
DORA has applied since 2025-01-17
([EIOPA](https://www.eiopa.europa.eu/digital-operational-resilience-act-dora_en)).
France has not yet transposed NIS2: the bill was taken off the Assemblée agenda
on 2026-10-06 with no new date
([Leto](https://www.leto.legal/news/assemblee-nationale-report-projet-loi-resilience-nis2-octobre-2026)).
ANSSI's ReCyF framework has existed since 2026-03-17
([ANSSI](https://cyber.gouv.fr/reglementation/cybersecurite-systemes-dinformation/directives-nis-nis2-et-dispositif-saiv/directive-nis-2/)).
Every French company has had to receive e-invoices since 2026-09-01, and SMEs
must issue them from 2027-09-01
([economie.gouv.fr](https://www.economie.gouv.fr/tout-savoir-sur-la-facturation-electronique-pour-les-entreprises)).
Server prices rose for new orders in 2026
([Hetzner, from 2026-06-15](https://docs.hetzner.com/de/general/infrastructure-and-availability/price-adjustment/)),
so a new customer should not expect the EUR 12 reference server's price.

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
   (§32, §34, §42).

§43 holds the obligations the vendor takes on by selling and operating the
product. §44 covers go-to-market and market validation. §45 adds everyday
features for any company.

Recovery and operating gaps (Part V P0/P1) stay ahead of this roadmap: a product
cannot be sold before its own backups, MFA and alerting are proven.

### 32. Product packaging, releases and supply chain

- [ ] **ADR — licensing and editions:** choose between a GPL-3.0 core with a commercial edition (fleet console, compliance packs, LTS), support-only subscriptions, or dual licensing. Verify that every bundled component's licence permits redistribution and operation by a third party for customers (for example Elastic, Odoo, GitLab, SonarQube, Portainer and Kafbat editions) and record the result per component. For each option, record its Cyber Resilience Act consequence from §43's ADR. The Commission's guidance treats paid enterprise editions as commercial activity; paid support for freely available software is not commercial by default ([summary, 2026-07-29](https://www.opensourceforu.com/2026/07/eu-clarifies-cra-rules-for-non-commercial-open-source/), secondary source). Also record the contributor terms dual licensing needs (CLA or DCO) and when the GPL-3.0 core repository becomes public (it was private on 2026-10-23).
- [x] Release tooling ([releases](docs/guides/releases.md)): `scripts/release.py prepare` writes the version and CHANGELOG.md, annotated `v<version>` tags. *(2026-10-05; no release cut yet)*
- [ ] Release channels (`stable`, `lts`) with a defined security-backport window, and upgrade notes per release. State each release's end of support (month and year) in the release metadata, `SECURITY.md` and the support policy (§39). For products placed on the market from 2027-12-11, the CRA requires at least five years of support (or the expected use time if shorter) and keeps each security update available for ten years ([Art. 13(8)–(9)](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/eng)).
- [x] Release bundles with a CycloneDX SBOM, image list, notes, SHA256SUMS and an SSH signature, verified offline by `scripts/release.py verify`. *(2026-10-05)*
- [ ] Build and sign bundles in CI on tags with the key in Vault, publish them as GitLab releases, and make the installer and upgrader refuse unsigned or altered bundles; add build provenance.
- [ ] Offline installation bundle: K3s binaries, charts, every pinned image and the required OS packages in one verified archive, loaded into the internal Registry. Prove a full install on a host with no Internet egress.
- [ ] Platform upgrades as one operation (`./install.sh --upgrade <version>`): preflight (health, fresh verified backup, capacity, API deprecations through `validate-repository.sh --live`, migration guards), ordered component upgrades, node-by-node K3s upgrades, recorded rollback points and post-upgrade `--status`. Support and test only N-1 → N paths.
- [ ] Release qualification pipeline: each release candidate installs on disposable hosts, upgrades from the previous release, runs the safety suites and `verify-platform.py --strict`, and blocks the release on failure.
- [x] Support matrix in the [deployment guide](docs/deployment.md#support-matrix) (Ubuntu LTS, x86_64, K3s pin, topologies, measured sizes). *(2026-10-05)*
- [ ] Extend the support matrix with sizing profiles (§26), additional tested Ubuntu releases and named providers once installations on them are verified.
- [ ] Publish versioned product documentation from `docs/` (administrator, operator and application-team guides, API reference), one version per release.
- [x] Keycloak pages in English and French; Odoo already has French. *(2026-10-05)*
- [x] Setup hub in French (`./install.sh --lang fr` or `SWIRL_CLOUD_LANG=fr`). *(2026-10-05)*
- [ ] Translate the installer engine's console messages, the verification report and the portal (Homepage) for French installations.
- [ ] Third-party licence register: `config/third-party-licences.yaml` lists every image and chart in the release SBOM, with its SPDX licence, its source, whether redistribution and operation for customers are allowed, and its obligations. `validate-repository.sh` fails when an SBOM entry has no record, and bundles ship a `THIRD_PARTY_NOTICES` file. Record a decision for `mongo` (SSPL; section 13 targets offering MongoDB itself as a public service, [SSPL FAQ](https://mongodb.com/legal/licensing/server-side-public-license/faq), read 2026-10-23), `redis` 8 (RSALv2, SSPLv1 or AGPLv3, [Phoronix, 2025-05-01](https://www.phoronix.com/news/Redis-8.0-Goes-AGPLv3)), the Elastic 9.5 images (unverified whether the pinned images ship under Elastic License 2.0 or AGPLv3), `cp-kafka` versus `apache/kafka`, SonarQube, Odoo and Portainer. This feeds the licensing ADR, and the website's editions page depends on it.
- [ ] Secure-by-default product profile, kept separate from this installation's choices. New installations require MFA for `platform-admins` (`--admin-mfa require`), use key-only SSH with a key-provisioning step in the installer (§4), bind the application policy pack in `Deny` for `prod`, and keep vendor telemetry off. Each opt-out is an explicit input recorded in the evidence pack. The CRA requires a secure-by-default configuration for products placed on the market from 2027-12-11 ([Annex I](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/eng)). Depends on the `Deny` move (§36).
- [ ] Measure installation. Every installer run, including failed ones, writes a record under `/var/lib/swirl-cloud/` (phases, durations, exit class, versions), and `--status` shows it. Release qualification times an express install on a reference host, from bare Ubuntu to a passing `verify-platform.py --strict` and the first SSO login. It runs with and without the offline bundle, on at least two named EU offers, and writes the median time and success rate to the release notes and the support matrix. Until then, neither Part IV's "installs in hours" nor the website gives an install time. Depends on the release qualification pipeline. *Progress 2026-10-26:* run records done for the installer engine (`./install.sh` and unattended `--env-file` runs): each run writes `/var/lib/swirl-cloud/install-runs/<run>.jsonl` (mode, revision, versions, steps with times, result, exit code, duration; no inputs), and `--status` shows the last one ([verify the result](docs/deployment.md#verify-the-result)). Still open: records for Ansible runs, and release qualification.
- [ ] Small-company and evaluation profile (§26): one server below today's 24 GiB minimum. SonarQube and Kafka are optional, and a lighter log store is offered (chosen in an ADR). The support matrix gives the profile's measured requests and monthly cost at current list prices. Done when it installs from a README quick start on one named EU offer without help from the vendor. Server prices rose in 2026 ([Hetzner, new orders from 2026-06-15](https://docs.hetzner.com/de/general/infrastructure-and-availability/price-adjustment/)), so memory sets most of the fixed cost the product promises.
- [ ] Signed release and update feed, hosted by the vendor in the EU. It carries release bundles, security advisories (CSAF with VEX) and, where the licence register allows, mirrors of the Trivy database and the pinned images for strict and offline installations. The hub and `--status` report available updates. Component update checks stay off (§33), so this feed is the single disclosed update channel, listed in the external-services inventory. From 2027-12-11 the CRA requires update notification and secure update distribution. Depends on bundles built and signed in CI.
- [ ] Security patch releases, separate from feature releases. By default they apply unattended in the declared maintenance window, with opt-out and postponement, and administrators get a notice; a failed health check rolls back to the recorded point. From 2027-12-11, CRA Annex I requires automatic security updates by default where applicable, with an opt-out, and security updates free of charge. Depends on `--upgrade` and maintenance windows (§4).
- [ ] VEX per release, generated from the recorded vulnerability exceptions (§36) and shipped beside the SBOM. Keep per-component records (maintainer, jurisdiction, support status, end-of-life date) with the licence register, and fail release qualification on a component past end of life. This supports CRA Art. 13 due diligence on third-party components (2027-12-11) and the sovereignty report (§33).

### 33. Sovereignty mode and provider independence

- [x] [External services](docs/guides/external-services.md) inventory of every outside service and data flow; vendor telemetry and update checks switched off. *(live, 2026-10-05)*
- [ ] Traefik's version check (`global.checkNewVersion: false`) takes effect at the next ingress reconcile (`scripts/configure-ingress.sh`). CrowdSec keeps sharing attack signals with its Central API by decision (2026-10-05).
- [ ] **ADR — edge without Cloudflare:** add an edge mode that needs no Cloudflare account: ACME certificates (cert-manager or Traefik), DNS through an EU provider API, in-cluster WAF and rate limiting (Traefik with CrowdSec or Coraza, linked to §4's HTTP IDS), Keycloak-gated admin hosts in place of Access, and the provider's network DDoS protection. Keep Cloudflare as a supported option.
- [ ] Private networking without Tailscale's coordination service: support a self-hosted control server (Headscale or NetBird) or WireGuard directly as an additional `K3S_NODE_TRANSPORT`; encrypt vRack and LAN node traffic too (flannel `wireguard-native`), which travels unencrypted today.
- [x] Repository onboarding from GitLab alone (`./add-repos.sh --gitlab`, installer inputs `REPOSITORY_SOURCE` and `GITLAB_PROJECTS`), with GitHub inputs required only for the GitHub source ([projects without GitHub](docs/guides/repository-onboarding.md#projects-without-github)). *(2026-10-05)*
- [ ] `SOVEREIGNTY_MODE=strict` installation profile that refuses non-EU or non-self-hosted dependencies, pulls images only from the internal Registry (§32 offline bundle) and reports any remaining external call in `--status`.
- [ ] Provider modules (OpenTofu) for ordering and preparing servers at two or more European bare-metal providers (private network, OS image, DNS), callable from the hub with an API key; a generic path for already-installed servers stays the default.
- [ ] On-premises appliance: an unattended Ubuntu installation image (ISO and PXE) that applies the host baseline and starts the setup hub on first boot; test on physical hardware with a BMC.
- [ ] Sovereignty self-assessment: `./install.sh --sovereignty-report` scores the installed profile (default or strict) against the eight objectives of the Commission's Cloud Sovereignty Framework, with a reasoned SEAL level for each. It works from the external-services inventory, component jurisdictions (§32) and strict-mode status. Check the criteria against the Commission's own document, not a vendor summary. The Commission's sovereign cloud tender required at least SEAL-2 ([Commission, 2026-04-17](https://commission.europa.eu/news-and-media/news/commission-advances-cloud-sovereignty-through-strategic-procurement-2026-04-17_en)). The website's sovereignty page may publish a sample report.
- [ ] Extend strict mode to the paths the edge ADR leaves out. The outage watch on Cloudflare Workers ([ADR 0030](docs/adr/0030-outage-watch-outside-the-cluster.md)) and the `platform-chat-email` Email Worker (§22) get an EU-hosted alternative or are refused. Strict mode checks where the SMTP relay's provider is based (the live relay, Resend, is a US company), makes the ACME issuer selectable (Let's Encrypt is US-based), and requires an EU-hosted external status page (§38). `--status` lists any remaining non-EU path.

### 34. Multi-tenancy, self-service and cost transparency

- [ ] **ADR — tenancy model:** projects inside one organization installation (namespaces per environment, Keycloak groups, Argo CD AppProjects, GitLab groups, Vault paths, quotas) versus virtual clusters for stronger isolation. Depends on `infra` default-deny networking (§6).
- [ ] Declarative project resource (CRD or Git-managed file) reconciled into per-environment namespaces with ResourceQuota and LimitRange, NetworkPolicies, AppProject, GitLab group, Keycloak groups (owner, developer, viewer), Vault policy, data credentials, Grafana folder and Kibana space. Everyday project work no longer needs `platform-admins`.
- [ ] Role model across every service: platform administrator, security auditor (read-only everywhere plus compliance views), project owner, developer and viewer; covers Grafana, Kibana, Argo CD, GitLab, Vault, DBGate, Kafka UI and Portainer (replaces the all-or-nothing access in §18).
- [ ] Self-service data services through claims or the portal: a PostgreSQL database (shared server or dedicated CloudNativePG cluster), Redis ACL user, Kafka topics, S3 bucket. Credentials land in the project's Vault path; backups, metrics and alerts come with the claim; deletion keeps a retention period.
- [ ] In-cluster S3-compatible object storage for applications and platform services, on the implementation chosen in §31, with per-project buckets, quotas and backups.
- [x] Cost showback from each server's recorded monthly cost (`scripts/configure-node-cost.sh`, `SERVER_MONTHLY_COST`; `swirlit-node-01` at EUR 12 a month) in Grafana **Cost Showback** and monthly in **Platform Report** ([cost showback](docs/guides/cost-showback.md), [platform reports](docs/guides/platform-reports.md)). *(live, 2026-10-05)*
- [ ] Comparison against equivalent hyperscaler list prices with stated assumptions; add backup and traffic usage once those are measured per project.
- [ ] Dated server costs: `configure-node-cost.sh` records the source and date of each price, and the Platform Report flags any cost older than six months. Providers raised prices for new orders in 2026 ([Hetzner, from 2026-06-15](https://docs.hetzner.com/de/general/infrastructure-and-availability/price-adjustment/)). The hyperscaler comparison above and the website's cost calculator use the dated figures.
- [ ] Energy and carbon showback beside cost showback: per-node power from RAPL or the BMC (Kepler or Scaphandre, ADR), allocated to namespaces and workloads like the cost model, converted with the hosting country's grid intensity (source and date recorded), shown in Grafana and monthly in the Platform Report. Customers preparing sustainability reports ask their IT suppliers for these figures.

### 35. Developer portal and golden paths

- [ ] **ADR — portal:** extend Homepage, adopt Backstage, or build a small portal on the platform API. The portal is the product's front door: catalog with owner, repository, environments, deployed revision, health, SLO (§38), vulnerabilities, Sonar quality gate and documentation, fed by the onboarding contract, Argo CD, GitLab, Trivy and Sonar.
- [ ] Versioned platform API behind Keycloak for the portal, CLI, deployment UI (§24) and fleet agent (§39), so front ends never run scripts directly.
- [ ] Golden-path templates (Spring Boot, NestJS/Node, Python, static site): one action creates the GitLab project with CI, Dockerfile, manifests, onboarding contract version 2, Sonar project and Keycloak client, then deploys to `int`. Measure and publish template-to-URL time.
- [ ] Preview environments per merge request: temporary namespace and hostname, seeded test data, quota-limited, removed on merge or close.
- [ ] Progressive delivery: canary or blue-green releases with automated analysis on Prometheus metrics and automatic rollback (Argo Rollouts or equivalent), plus release-freeze windows recorded as change records (§36).
- [ ] `swirl` CLI on the platform API: Keycloak device-flow login, applications and environments, deploy and promote (packaging `scripts/deploy-application.py`), logs, dashboards and time-limited database access for authorized roles.
- [ ] Import from Docker Compose: `add-repos.sh --compose <file>` generates onboarding contract version 2 and the manifests from a Compose file. Databases and Redis in the file become claims (§34), and secrets go to Vault. Done when a sample Compose application reaches `int` with its data service claimed. Heroku stopped new features and stopped signing new Enterprise customers in February 2026 ([DeployHQ, 2026-03-23](https://www.deployhq.com/blog/heroku-sustaining-engineering-alternatives), secondary source). Depends on self-service data claims (§34).
- [ ] Custom domains for applications: the onboarding contract accepts hosts outside the platform's public zone (for example a customer-facing `shop.example.fr`), checks DNS ownership with a TXT record, issues the certificate (ACME or Cloudflare for SaaS) and adds the host to the SLO probes. Today every host must sit in the selected zone and wildcards are rejected ([application contract](docs/guides/application-onboarding.md)). Done when a test application serves a second domain with a valid certificate and a probe.
- [ ] Error tracking per project: a self-hosted Sentry-compatible service (GlitchTip or Sentry self-hosted, chosen in an ADR) behind Keycloak, a project and DSN created at onboarding into the project's Vault path, source maps uploaded by the golden-path CI, and alerts routed to the application owner. Retention and personal data in stack traces are recorded in the processing records.
- [ ] Dynamic application security testing in the golden-path CI: an OWASP ZAP baseline scan against the `int` deployment after each merge, findings in GitLab's security report and the vulnerability deadline report, blocking promotion to `prod` on high findings once tuned. Security questionnaires ask for it.
- [ ] Feature flags as a claimable service (Unleash or Flagsmith open source, ADR): per-project environments matching `int`/`uat`/`prod`, SDK keys in Vault and a flag change recorded as a change record (§36), so releases can be decoupled from deployments.

### 36. Compliance evidence and governance

- [x] Control catalog ([`k8s/files/compliance-controls.yaml`](k8s/files/compliance-controls.yaml)) mapped to NIS2, ISO/IEC 27001, DORA, GDPR and CIS, evaluated continuously: Grafana **Compliance Evidence**, `ComplianceControlRegressed`, monthly pass rates in the **Platform Report**, and signed evidence packs from `./install.sh --compliance` ([compliance evidence](docs/guides/compliance-evidence.md)). *(live, 2026-10-05)*
- [ ] Generate the signed evidence pack monthly on a timer and keep it with the tamper-evident audit store below.
- [ ] Triage the standing failures the report shows (15 CIS Kubernetes and 3 Pod Security Baseline checks, `gitlab-runners`/`host-agents` without NetworkPolicies) and record accepted exceptions with owner, rationale and review date in the catalog.
- [x] Native admission policy packs in Warn+Audit mode for application namespaces, with `./install.sh --policy-report` ([ADR 0027](docs/adr/0027-native-admission-policy-packs.md)). *(2026-10-05)*
- [ ] Fix the violations the policy report lists in the application repositories, then move the binding to `Deny` for `prod` and later `uat`; add a platform pack for `infra` exceptions with owner and expiry.
- [ ] Sign application images in platform CI and verify signatures at admission for `prod` (and `uat` once stable).
- [x] Keycloak login and admin events kept 90 days in the realm and a year in `identity-audit-*` ([identity events](docs/security.md#identity-events)). *(live, 2026-10-04)*
- [ ] Collect identity, Kubernetes API, Vault, GitLab and host audit records into a tamper-evident store: off-site object storage with retention lock, retention set per framework.
- [x] Access review sheet `./install.sh --access-review` ([access reviews](docs/guides/compliance-evidence.md#access-reviews)); first run 2026-10-05 found GitLab `root` without 2FA and two cluster-admin ServiceAccounts (`infra/portainer`, `longhorn-system/longhorn-support-bundle`).
- [ ] Resolve the first review's findings: enforce GitLab 2FA (or rely on Keycloak MFA once §11 lands) and narrow Portainer's ServiceAccount (§18); add Vault policy and Grafana role assignments to the sheet.
- [x] Draft GDPR Article 30 records from the running platform, `./install.sh --processing-records` ([records of processing](docs/guides/compliance-evidence.md#records-of-processing)). *(2026-10-05)*
- [ ] Generate the records from the project resource (§34) once projects declare purposes and data classes, instead of inferring them.
- [x] Incident register and NIS2 workflow: GitLab **Incident** template and `./install.sh --incident-report` ([incident response](docs/guides/incident-response.md)). *(2026-10-05)*
- [x] Vulnerability deadlines (Critical 15 days, High 30 days) tracked hourly, in the **Platform Report** and `./install.sh --vulnerability-report` ([platform reports](docs/guides/platform-reports.md)). *(2026-10-05)*
- [ ] Record triaged vulnerability exceptions as data (image, vulnerability, owner, expiry) so the deadline report and `TrivyNewCriticalVulnerability` subtract them.
- [x] Credential age report `./install.sh --credential-age`, metadata only ([security](docs/security.md#credential-age)). *(2026-10-05)*
- [ ] Dynamic or scheduled rotation for database credentials (Vault database engine), registry and GitLab tokens and API keys, and an alert when a credential passes its age limit.
- [ ] Add ANSSI's ReCyF as a framework in `compliance-controls.yaml` and on the Compliance Evidence dashboard, mapped from the 27 controls, and re-map when the French law and decrees are published. ReCyF is a non-binding working document, published 2026-03-17 while the French NIS2 bill is still pending ([ANSSI](https://cyber.gouv.fr/reglementation/cybersecurite-systemes-dinformation/directives-nis-nis2-et-dispositif-saiv/directive-nis-2/)). Describe controls as "mapped to" ReCyF, never as compliant.
- [ ] Security questionnaire pack: `./install.sh --questionnaire caiq-v4` maps each catalog control to CAIQ v4 question IDs, with its automated evidence or "manual", and adds the sovereignty report (§33). Use it for the vendor's own CSA STAR Level 1 entry, a free self-assessment published in a public registry ([CSA](https://cloudsecurityalliance.org/star/self-assessment/), read 2026-10-23). The website's trust centre depends on it.
- [ ] Contract annex per installation: `./install.sh --contract-annex` writes the technical and organisational measures from the control catalog, the sub-processors from the external-services inventory, the data and processing locations from the node inventory, and DORA register-of-information rows (provider identity, service type, functions supported, locations, subcontracting chain, exit plan reference). Financial entities must keep that register under DORA, which has applied since 2025-01-17 ([EIOPA](https://www.eiopa.europa.eu/digital-operational-resilience-act-dora_en)). A generic run feeds §43's contract templates.
- [ ] ISMS document kit covering the manual evidence the catalog asks for: information security policy, risk register, continuity and recovery plan, and supplier register. These are templates in `docs/`, prefilled from the catalog, the external-services inventory and the coverage table (§16), with the customer's own fields left to complete. The evidence pack lists which documents are signed and when.
- [ ] Asset inventory as evidence: `./install.sh --asset-inventory` lists servers (hardware, OS, location, owner), workloads with images and data classes, data stores, external services and enrolled staff devices (§37), with owner and criticality, exported as CSV and included in the evidence pack. NIS2 Art. 21(2)(i) names asset management among the required measures.
- [ ] Data subject requests (GDPR Art. 15–17): an intake form and a GitLab or Odoo workflow with the one-month deadline; the onboarding contract gains an optional `privacy` hook through which each application exports or erases one person's data; `./install.sh --dsr export|erase --subject` runs it across Keycloak, Odoo and every application that declares the hook, and records the outcome. Done when a test identity is exported and erased end to end.
- [ ] Security awareness and phishing exercises: a self-hosted phishing simulation (GoPhish) sending through the platform mail relay to Keycloak users, a short training module per campaign, and click and report rates per campaign in the Platform Report as evidence for NIS2 Art. 21(2)(g) (basic cyber hygiene and training). Campaigns need the employer's written authorization and are explained to staff beforehand.

### 37. Security operations

- [ ] **ADR — runtime detection:** Falco or Tetragon with curated rules (shell in a container, unexpected egress, mining, sensitive file reads, privilege escalation), alerting through Alertmanager and indexed for investigation.
- [x] Detection rules every five minutes (brute force then success, new administrator, RBAC outside GitOps, unusual Secret reads) posted to Alertmanager ([security detection](docs/guides/security-detection.md)). *(live, 2026-10-05)*
- [ ] Extend detection to host events (Fail2ban/CrowdSec bans followed by a successful SSH login, sudo by unexpected users), HTTP events once Traefik access logs are shipped (§4), and impossible travel once logins carry a geolocation.
- [x] Encrypted application volumes: StorageClass `longhorn-encrypted` (LUKS2, key in Vault) ([encrypted volumes](docs/security.md#encrypted-volumes)). *(live, 2026-10-05)*
- [ ] Document a disk-encryption option for hosts in rented data centers, and move applications holding personal data to encrypted claims (application repositories).
- [x] Two default StorageClasses assessed: claims without a class still get Longhorn, and `local-path` stays for `vault-audit`; no change. *(2026-10-05)*
- [ ] Ransomware-resilient backups: off-site copies under retention lock, written with credentials that cannot delete or overwrite retained data, so a compromised cluster cannot destroy its backups (§16).
- [x] Just-in-time administration `./install.sh --admin-access grant|revoke|list` with approver and automatic expiry ([security](docs/security.md#just-in-time-administration)). *(live, 2026-10-05)*
- [ ] Recorded `kubectl` and SSH sessions (with §29's Nexterm or an equivalent), and moving daily users from permanent to just-in-time membership.
- [ ] External penetration test of a reference installation (§44) before the first customer, repeated per major release. The platform's vulnerability disclosure policy and contact moved to §43, because CRA reporting already applies.
- [ ] Forward audit and detection events to a customer's SOC or managed security provider: identity, Kubernetes API, Vault and detection findings, sent as syslog (RFC 5424 over TLS) or OTLP, with a documented field mapping, delivery-failure alerts and a filter per destination. The tamper-evident store (§36) remains the archive.
- [ ] Staff device coverage: an endpoint agent (Wazuh or Fleet with osquery, ADR) enrolled through Keycloak on company laptops, reporting OS version, patch level, disk encryption, screen lock and antivirus status, with alerts on drift and per-device status in the Compliance Evidence dashboard. Most SME incidents start on a workstation, and the catalog has no device evidence today.
- [ ] Passkeys (WebAuthn) for Keycloak sign-in alongside the TOTP MFA of §11: phishing-resistant login for administrators first, then optional for every user, with recovery through a second registered key or a just-in-time reset approved by another administrator.

### 38. Reliability, SLOs and observability depth

- [x] Availability SLO (99.5 %) per public endpoint from blackbox probes, burn-rate alerts, Grafana **Service Levels** and monthly availability in the **Platform Report** ([service levels](docs/guides/service-levels.md)). *(live, 2026-10-05)*
- [x] Indicators beyond probes (data stores, CI queue time and failures, Argo CD sync failures) with `CIJobsQueueingLong` ([other indicators](docs/guides/service-levels.md#other-indicators)). *(2026-10-05)*
- [ ] Registry push/pull probes and per-service SLA targets for the managed service.
- [x] Every alert carries an `owner` label and a `runbook_url` into [alert runbooks](docs/guides/alert-runbooks.md); validation enforces both. *(2026-10-05)*
- [ ] Status page hosted outside the platform, fed by the outage watch (§14), with incident updates; decide first which endpoints may be shown publicly.
- [ ] On-call routing: escalation policies and a paging channel in Alertmanager, with acknowledgement, on top of §27/§29 notifications. Name a second person or partner who is paged after a set delay, write the severity matrix in `docs/guides/on-call.md`, and close with a dated paging drill acknowledged end to end.
- [ ] OpenTelemetry: a collector, a trace backend and automatic instrumentation for Java and Node applications; correlate traces with logs and metrics in Grafana. Decide in the ADR whether logs stay in Elasticsearch.
- [x] Capacity planning: Grafana **Capacity Planning**, `ClusterCapacityRequestsHigh` and `ClusterCannotSurviveNodeLoss` ([capacity planning](docs/guides/capacity-planning.md)). *(2026-10-05)*
- [x] CPU requests lowered to measured peaks (Kibana, SonarQube, Trivy operator, Prometheus) and Longhorn guaranteed instance-manager CPU set to 5 % ([October upgrades](docs/guides/service-upgrades-2026-10.md)). *(2026-10-05)*
- [ ] Application-level backup and restore per project namespace (manifests, volumes and claimed databases), including self-service restore into a sandbox and moving a project between clusters.
- [x] Game days: `./install.sh --game-day` pod-loss drill outside `prod`, recorded in the **Platform Report** ([game days](docs/guides/service-levels.md#game-days)). *(2026-10-05)*
- [ ] Add node-loss, DNS-failure and storage-degradation scenarios on disposable hosts, and run the pod-loss drill on a schedule.
- [ ] Monthly service report per installation. Declare maintenance windows as data and exclude them from availability. Each month, produce a signed report per contracted endpoint with availability, incidents and support response times (§39). Done when the report matches **Service Levels** for a full month on `swirlit-node-01`. Managed contracts and the DORA service levels (§43) rely on it.
- [ ] Hardware health on bare-metal nodes: disk SMART data and NVMe wear, software RAID, memory ECC (EDAC) and BMC sensors (IPMI or Redfish), collected through node-exporter collectors or the textfile collector, with alert rules and runbooks. Virtual servers skip it. Verify on the first bare-metal node (§25).
- [ ] Second-site recovery option: restore an installation from its off-site backups in another data centre or at another provider, measure RPO and RTO, and document the result as the geographic-redundancy option. Builds on §16's full rebuild. DORA Art. 12 requires financial entities to keep redundant ICT capacity and to restore onto segregated systems ([Art. 12](https://www.springlex.eu/en/packages/dora/dora-regulation/article-12/)).

### 39. Fleet management and the managed service

- [ ] **ADR — fleet architecture:** an outbound-only agent per installation reports version, health, alerts, backup age, certificate expiry, capacity and compliance status to a central fleet console hosted in the EU. No inbound access; the customer can see every field sent and can disconnect at any time. The product keeps working fully without the agent and console. A console the product needs in order to function could fall inside the CRA product scope as remote data processing (unverified, §43).
- [ ] Fleet console: inventory of installations, staged release rollout (internal installation first, then canary customers), upgrade status, fleet-wide alert and compliance views.
- [ ] Customer-approved support access: a time-limited session that the vendor requests and a customer administrator approves in the customer's own Keycloak; every action is recorded and visible to the customer and access ends automatically. The customer keeps the Vault recovery keys.
- [ ] Support bundle upload through the agent with customer consent, linked to a support ticket.
- [ ] If the licensing ADR (§32) adopts a commercial edition: offline-verifiable licence files whose expiry disables only commercial features and never stops running workloads.
- [ ] Prepare the managed service for certification: ISO/IEC 27001 scope covering the vendor's operation of customer installations, then evaluate HDS or SecNumCloud only for a named customer segment.
- [ ] **ADR — managed-service model:** for installations the vendor operates, decide the EU hosting providers and whose name holds the provider account and the servers; who holds the recovery kit and the Vault unseal keys (the customer holds the kit, or auto-unseal runs through a customer KMS or Transit), which sets the product default where the §16 Needs Input entry covers only `swirlit-node-01`; how the vendor's access is kept separate between customers, with no shared administrator identity or key; support hours; and the vendor's duty to tell customers about incidents. The website already describes this offer in the present tense. Record the result in `docs/guides/managed-service.md`.
- [ ] Customer installation checklist for vendor-assisted installs: preflight, acceptance test (`verify-platform.py --strict`, a restore drill and an SSO login), handover of the recovery kit and keys to the customer, and a signed acceptance record. The vendor keeps no copy of the kit unless the managed-service ADR says so. Rehearse on a disposable host.
- [ ] Support desk and published support policy. A support mailbox opens tickets with severity labels in GitLab Service Desk or a decided alternative. Service Desk is in the Free tier but "not under active development" ([GitLab docs](https://docs.gitlab.com/user/project/service_desk/), read 2026-10-23). A business-hours calendar drives first-response and resolution timers, shown in Grafana. `docs/support-policy.md` states support hours, response targets per severity, supported versions (§32) and end-of-life dates. The support bundle upload above links to these tickets, and the website's editions page depends on the policy.

### 40. Business and workplace modules

- [ ] Module catalog in the hub and portal generalizing the [platform capability checklist](docs/development.md#adding-a-platform-capability): each optional service declares dependencies, sizing, SSO, Vault secrets, network policy, backups, alerts and restore test, and installs or uninstalls with an explicit data-retention choice. §28 and §29 services become modules.
- [ ] Files and office module: Nextcloud with Collabora Online or ONLYOFFICE, Keycloak SSO, object or Longhorn storage, backups and restore test.
- [ ] Video-conferencing module: Jitsi Meet with Keycloak-authenticated room creation and a TURN service.
- [ ] Offer the mail and directory outcome of §27 as an installation option for customers.
- [ ] Odoo business profiles: preconfigured module sets (CRM and sales, invoicing and accounting, projects and timesheets, HR) applied by the Odoo bootstrap, with French localization.
- [ ] French electronic invoicing in Odoo. **Overdue for the company's own installation:** every French company has had to be able to receive e-invoices since 2026-09-01, and SMEs and micro-enterprises must issue them and send e-reporting from 2027-09-01 ([economie.gouv.fr](https://www.economie.gouv.fr/tout-savoir-sur-la-facturation-electronique-pour-les-entreprises), news items up to 2026-10-12). Odoo is an approved platform (plateforme agréée) through the `l10n_fr_pdp` module, which sends UBL and receives Factur-X, UBL and CII ([Odoo documentation](https://www.odoo.com/documentation/latest/applications/finance/fiscal_localizations/france.html), read 2026-10-23). Steps: check that `odoo/addons/l10n_fr_pdp` exists in the pinned Community image `odoo:19.0-20260908`; it is unverified whether the module works on Community, and the documentation names Enterprise only for the EDI VAT return. Then activate the module with the legal representative's identity check, or record the other approved platform already designated ([Needs Input](#needs-input)). Add Odoo SA to the external-services inventory and the processing records as the platform that receives invoice data. Test receiving, then test issuing before 2027-09-01. The "invoicing and accounting" profile above then ships the same activation steps for customers.
- [ ] **ADR — workplace suite:** package openDesk Community Edition (an existing Kubernetes-based sovereign workplace, [openDesk docs](https://docs.opendesk.eu/operations/introduction)) or La Suite numérique components behind Keycloak, or assemble the files, video and chat modules above separately. Check openDesk's deployment tooling and Community licence terms first (unverified). Also decide whether the vendor applies to the ZenDiS partner programme, which opened 2026-09-23 and takes applications until 2027-01-31. Its approved sovereign service providers need a BSI C5 attestation and SEAL-3; certified integrators need open-source and Kubernetes experience ([cloudmagazin, 2026-09-27](https://www.cloudmagazin.com/fr/2026/09/27/opendesk-souvre-aux-fournisseurs-cloud-prives/)). Partners pay EUR 18 000 a year with a two-year minimum ([WZ-IT, 2026-09-27](https://wz-it.com/en/blog/opendesk-partner-program-smes-nextcloud/), secondary source).
- [ ] Knowledge base module: a self-hosted wiki (Outline, Docmost or BookStack, ADR) with Keycloak SSO, group permissions from the project resource (§34), full-text search, backups and a restore test; the platform's runbooks can be published into it.
- [ ] IT helpdesk module: ticketing for staff and customers (Zammad, or Odoo Helpdesk if the edition allows, ADR) with Keycloak SSO, email intake through the mail relay, SLA timers and a link to the asset inventory (§36).
- [ ] Privacy-friendly web analytics module (Umami, Plausible Community Edition or Matomo, ADR) for the company website and applications: no third-party cookies, IP truncation, data kept on the platform, configured to meet the CNIL's conditions for consent-exempt audience measurement, with a per-site dashboard.
- [ ] Document signing module (DocuSeal or Documenso, ADR): templates, signing by email with an audit trail and a sealed PDF, Keycloak SSO for senders, Odoo integration for quotes and contracts. Record which eIDAS signature level it provides (simple electronic signature unless a qualified provider is connected).
- [ ] If §27 keeps Microsoft 365 or Google Workspace: back up mailboxes, calendars and drives onto the platform's backup target (for example Corso for Microsoft 365), with a restore test, since the providers' retention is not a backup.

### 41. Private AI

- [ ] AI gateway (OpenAI-compatible proxy) as the only path from users and applications to language models: per-project keys from Vault, budgets, model allowlists per data classification (confidential data only to local models), usage metrics and request metadata logging without prompt contents by default. Open WebUI (§28) uses it.
- [ ] Local inference runtime on GPU nodes (§31) with GPU scheduling and sharing, exposed only through the gateway.
- [ ] Read-only operations assistant over Prometheus, Kibana, Argo CD and the runbooks that explains alerts and proposes runbook steps; it never applies a change without an operator's approval.
- [ ] AI Act duties, done before the AI modules ship. Open WebUI and the operations assistant tell users they are interacting with AI, and generated content carries a machine-readable mark: Art. 50 has applied since 2026-08-02 to systems placed on the market from that date, under omnibus Regulation (EU) 2026/1744 ([Garrigues, 2026-07-24](https://www.garrigues.com/en_GB/garrigues-digital/ai-digital-omnibus-regulation-has-been-published-redefining-deadlines-and)); how the marking duty splits between a model provider and a gateway that only proxies the model is unverified. The gateway keeps a model registry (provider, licence, provider documentation, whether the provider signed the GPAI Code of Practice, hosting location), and the classification allowlist reads it. Each project declares its AI use cases; Annex III uses (recruitment, worker evaluation, credit scoring) stay blocked until the customer names an oversight owner, because high-risk deployer duties apply from 2027-12-02.

### 42. Exit and data portability

Differentiator 5 and the website's FAQ promise a documented exit, and no item
delivers it yet. Exit means the customer can run everything, or move it
elsewhere, without the vendor. The Data Act's switching terms are in §43.

- [ ] Full export: `./install.sh --export` writes, with a manifest and checksums, Git bundles and GitLab project exports, Registry images in OCI layout, PostgreSQL and MongoDB dumps, object storage, the Keycloak realm, Vault KV encrypted to a customer-supplied key, the Odoo database and filestore, Grafana dashboards, Argo CD Applications, the 13-month platform reports and the audit store. The manifest lists anything excluded and why (for example rebuildable caches). Done when an export of `swirlit-node-01` verifies offline. Depends on GitLab native backup (§12).
- [ ] Self-operation runbook `docs/guides/self-operation.md`: daily checks, upgrades, backups and restores, certificate and key rotation, where each credential and recovery item lives, and how to disconnect the vendor. Done when an operator from outside the vendor installs, restores and upgrades N-1 → N on a disposable host using only the docs, with times and gaps recorded. The website FAQ already calls this runbook "in progress".
- [ ] Handover from vendor-operated to self-operated: transfer every administrator credential and the recovery kit, rotate every credential the vendor knew, revoke the vendor's support access and agent (§39), and issue a signed handover record. Rehearse on a disposable installation. Depends on §39's support access.
- [ ] Yearly exit drill: restore the export onto plain K3s at another provider, and onto a fresh Swirl Cloud installation. Check data and application traffic, and record the date, duration and gaps in the evidence pack. Contracts that support financial entities' critical functions must include exit strategies with a transition period ([DORA Art. 30](https://www.springlex.eu/en/packages/dora/dora-regulation/article-30/)).
- [ ] Installation wipe: after a typed confirmation, `./install.sh --wipe` removes every volume, database, key, credential and setting, then writes an erasure record listing what it removed. From 2027-12-11 the CRA requires a way to permanently remove all data and settings ([Annex I](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/eng)); the Data Act requires erasure after the retrieval period (§43).

### 43. Vendor and operator obligations

The vendor takes on obligations by selling Swirl Cloud (Cyber Resilience Act)
and by operating customer installations (Data Act, GDPR, DORA contracts and
customers' NIS2 supply-chain duties). Get a legal review before relying on these
readings. The product features they need are in §32, §36 and §42.

- [ ] **ADR — Cyber Resilience Act role and classification:** confirm that selling support, an edition or a managed service makes the vendor a manufacturer (open-source steward status does not cover its own monetised product); that the Art. 14 reporting duty starts with the first commercial supply and covers products already on the market (Art. 69(3)); and that classification follows the product's core function, not bundled Keycloak, CrowdSec or K3s (Art. 7(1)). The default class with self-assessment is unverified until the legal review ([CRA](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/eng)). Feeds the licensing ADR (§32).
- [ ] CRA reporting readiness before the first commercial supply: an account on ENISA's Single Reporting Platform; a runbook in [incident response](docs/guides/incident-response.md) with templates for the 24 h early warning, the 72 h notification and the final report (14 days after a fix for an actively exploited vulnerability, one month for a severe incident); a named reporter and a deputy; and `./install.sh --incident-report --customer`, which writes the FR/EN notice to affected customers. Close with a dated tabletop exercise. Reporting has applied since 2026-09-11 ([Commission, 2026-09-11](https://commission.europa.eu/news-and-media/news/safer-and-more-secure-digital-products-2026-09-11_en)). Which French CSIRT coordinates CRA reports is unverified.
- [ ] Disclosure policy for the platform: a `SECURITY.md` in the repository and in the published documentation, with the contact, supported versions and end-of-support dates (§32), the disclosure timeline and the advisory channel. Link it from the website's `security.txt`. CRA Annex I Part II requires a coordinated vulnerability disclosure policy and a contact address from 2027-12-11, and the reporting duty above already needs a contact.
- [ ] CRA conformity file for releases placed on the market from 2027-12-11: technical documentation (Annex VII) with the cybersecurity risk assessment; user instructions (Annex II) taken from the versioned docs (§32); the EU declaration of conformity and CE marking on bundles and documentation; ten-year retention. Track the harmonised standards, none of which was cited in the Official Journal as of 2026-10-20 ([state of play](https://www.cyberresilienceact.eu/current-state-of-play/)), and map Annex I to the drafts meanwhile. Ship the SBOM, VEX, support period and declaration as a supplier pack for customers' questionnaires. Depends on the CRA ADR.
- [ ] Switching terms for managed-service contracts: notice of at most two months; a 30-day transition, extendable to seven months with reasons; data retrievable for at least 30 days, then erased; an exhaustive list of exportable data taken from §42's manifest; a published statement on jurisdiction and third-country access (Art. 28); and fees disclosed before signing, with no switching or egress charge from 2027-01-12 ([Bundesnetzagentur](https://bundesnetzagentur.de/EN/Areas/Digitalisation/DataAct/background/3-provider/start.html)). Get a legal opinion on whether a dedicated managed installation, or operation of customer-owned hardware, counts as a data processing service under the Data Act, and apply the terms either way. The website's legal pages depend on them.
- [ ] Processor documents for the managed service: a data processing agreement template based on the Commission's standard contractual clauses ([Decision (EU) 2021/915](https://eur-lex.europa.eu/eli/dec_impl/2021/915/oj)) or the CNIL's model; processor records (GDPR Art. 30(2)); and a sub-processor list generated from the external-services inventory, with locations and a change-notice period, which also serves customers' DORA subcontracting checks. `--contract-annex` (§36) fills the annex for each installation. The website's trust centre and legal pages depend on these documents.
- [ ] DORA contract addendum template for financial-entity customers, done before the first such customer. It covers every Art. 30(2) item (service description, subcontracting, data locations, data return on exit, service levels, incident assistance at a fixed price, cooperation with authorities, termination and notice) and adds an Art. 30(3) schedule for critical functions: measured service levels (§38), audit and inspection access, cooperation in threat-led penetration tests, a tested continuity plan, and exit with a transition period (§42) ([Art. 30](https://www.springlex.eu/en/packages/dora/dora-regulation/article-30/)). No standard clauses under Art. 30(4) have been published yet ([ACPR FAQ, 2026-03-20](https://acpr.banque-france.fr/system/files/2026-03/20260320_FAQ_webinaire_DORA_du_23-01-2026.pdf)).

### 44. Go-to-market and market validation

Validate the niche with real customers before building editions at scale. Make
no public claim until it is measured (the rule in the website's TODO).

- [ ] Reference installation with synthetic data on a separate disposable server: seeded applications in `int`, `uat` and `prod`, the deployment flow and the Compliance Evidence dashboard, rebuilt from scratch on a schedule. It produces the website's sample evidence pack and product-tour material, and it is the target of §37's penetration test. Later it can become a time-limited prospect sandbox behind Keycloak.
- [ ] Design-partner programme with companies from the target profile: 20–150 staff, a few in-house applications, an audit or questionnaire burden, no platform team. Each partner gets a written pilot plan, feedback every two weeks recorded as issues, and a go-paid-or-stop date. For each partner, record whether they need a VM import path (§30), a workplace suite (§40), e-invoicing (§40) or the small profile (§32), and reorder Part IV from the answers. The first external installation comes after the P0 items, and production data after §37's penetration test. Done when the first partner runs a pilot under a signed plan.
- [ ] Pilot scorecard: `./install.sh --pilot-report` builds a signed FR/EN scorecard over the pilot window. It draws on the install record (§32), the activation milestones (time to a passing strict verification, first application in `int` then `prod`, first evidence pack, first restore drill), deploy frequency, availability and the last restore drill. Use it with the first design partner.
- [ ] Opt-in install report. It is off by default; the hub asks once, in French or English, and shows the full payload, and `--telemetry show|on|off` controls it. Document payload schema v1 in the external-services inventory: a random rotatable ID, version, profile, node count and size class, phase durations, error codes and verification counts. A test enforces that the payload carries no hostnames, addresses, domains, or user or application names. The endpoint runs on the vendor's EU installation, and `SOVEREIGNTY_MODE=strict` refuses it. Software that makes a device send information needs consent unless an exemption applies ([EDPB Guidelines 2/2023 v2.0, 2024-10-16](https://edpb.europa.eu/system/files/2024-10/edpb_guidelines_202302_technical_scope_art_53_eprivacydirective_v2_en_0.pdf)).
- [ ] Funding deadlines; the owner decides ([Needs Input](#needs-input)). NLnet NGI Zero gives first grants of EUR 5 000–50 000 for work released under open licences, with the next deadline at 2026-11-03 12:00 CET ([NLnet](https://www.nlnet.nl/propose), read 2026-10-23). A §33 work package such as the self-hosted mesh or the edge without Cloudflare could fit (unverified). The Hexatrust accelerator takes companies with fewer than 15 employees, under EUR 500 000 revenue, more than 51 % European-owned, and an MVP or one client; applications close 2026-11-02 ([LeMondeInformatique, 2026-10-05](https://www.lemondeinformatique.fr/actualites/lire-hexatrust-lance-la-6eme-edition-de-son-programme-accelerateur-100832.html)).

### 45. Everyday company features (added 2026-10-23)

Features that make the platform useful to any company, whatever its sector or
regulation: people, data, office life, field sites and the platform's own
running cost.

- [ ] Joiner, mover and leaver automation: one HR record (Odoo Employees or the §27 directory) drives account creation in Keycloak, GitLab, Odoo, chat, wiki and mail with role-based groups, moves groups when the job changes, and on departure disables every account, revokes tokens, sessions and keys, transfers owned projects and files, and writes a record. Done when a test employee is created, moved and removed with no manual step. Builds on §27's deprovisioning.
- [ ] Staff remote access: a company VPN with Keycloak sign-in (NetBird or Headscale, shared with the §33 ADR) so employees reach internal tools from home or travel without exposing them publicly; device posture from §37 can gate access. Client setup in one page for Windows, macOS, Linux, iOS and Android.
- [ ] Company data warehouse and business dashboards: scheduled syncs from Odoo, application databases and the platform reports into an analytical store (ClickHouse or PostgreSQL with DuckDB, ADR), with a self-service BI tool (Metabase or Apache Superset) behind Keycloak, row-level access by group, and starter dashboards for sales, finance and operations. Business users answer their own questions without a developer.
- [ ] Internal tools builder: a low-code tool (NocoDB, Appsmith or ToolJet, ADR) on claimed databases (§34) and the Odoo API, so teams build forms, approval flows and admin screens themselves; apps are versioned in GitLab and deployed through the same environments.
- [ ] "Ask the company": permission-aware search and question answering across the wiki, Odoo, GitLab, files and chat through the AI gateway (§41), returning only documents the user may open, with links to sources and no prompt logging by default. Extends the §28 RAG start from one source to many.
- [ ] Meeting notes: transcription and summaries of Jitsi meetings (§40) and uploaded recordings with a local speech model (Whisper or equivalent) on the platform, opt-in per meeting with a visible indicator, results stored with the meeting's access rights and deleted on a retention schedule.
- [ ] Private translation and writing aids: document and text translation (LibreTranslate or a local model through the gateway) and a writing assistant in the wiki and Odoo, so confidential text never leaves the company.
- [ ] Document management with capture: a scanned-document inbox (Paperless-ngx, ADR) with local OCR, full-text search, tags and retention, which sends recognized supplier invoices to Odoo as draft bills. Done when a scanned invoice arrives in Odoo with amounts and supplier filled.
- [ ] Business telephony: a self-hosted phone system (Wazo Platform or Asterisk, ADR) with SIP trunk configuration, softphones for desktop and mobile, call queues and voicemail by email, and Odoo CRM integration (click-to-call, caller lookup, call log on the contact).
- [ ] Sites and connected devices: an MQTT broker and a time-series store (EMQX or Mosquitto with TimescaleDB, ADR) with dashboards and alerts in Grafana, plus a small edge node for a shop, warehouse or factory that keeps working offline and syncs to the main installation. Done when a test sensor's readings reach a dashboard through an edge node that lost its uplink for an hour.
- [ ] Cloud development environments: browser or IDE-connected workspaces (Coder or code-server, ADR) started from a golden-path template (§35) with Keycloak sign-in, project secrets and quotas, stopped automatically when idle, so a new developer is productive on day one with no local setup.
- [ ] Office-hours scheduling for non-production: `int`, `uat`, preview environments and dev workspaces scale to zero outside declared working hours and wake on first request or on demand, with the freed capacity shown in Capacity Planning. On fixed-price servers this makes room for more projects instead of buying another server.
- [ ] Platform undo: before a risky change (upgrade, migration, module removal), `./install.sh --checkpoint` takes consistent snapshots of volumes, databases and manifests; `--rollback <checkpoint>` restores them, with checkpoints expiring automatically. Done when a deliberately broken upgrade on a disposable host is rolled back with data intact.
- [ ] Phone notifications and approvals: a self-hosted push service (ntfy, ADR) and a small mobile-friendly page where administrators receive critical alerts and approve or deny just-in-time access, deployment promotions and support sessions (§39) from their phone, signed in through Keycloak.

---

## Part V — Priority index of open items

P0 protects recovery before disruptive changes. P1 closes operating gaps and
prepares expansion. P2 improves delivery and company services once prerequisites
are met. P3 requires a demonstrated need/capacity or a separate product decision.
These rows group the checklists above; priority does not bypass dependencies.

| Priority | Deliverable | Dependency / completion evidence | Section |
|---|---|---|---|
| P0 | Offsite archives, current Longhorn backup integration and separate recovery keys | Target outside the failure domain; fresh uploads and successful archive/volume restores | §8, §16 |
| P0 | GitLab and full-platform recovery coverage | Native backups plus configuration/secrets; isolated rebuild with measured RPO/RTO | §12, §16 |
| P1 | Private/key-only SSH | Tested second session/console and break-glass login; recovery prerequisites complete (administrator MFA stays optional by decision, §11) | §4 |
| P1 | `infra` network isolation and PSA enforcement | Dependency allowlists and host-agent placement validated before enforcement | §6 |
| P1 | Vulnerability triage follow-up | Application image findings handed to their repositories; fixes rechecked before the dated exceptions expire (2026-11-04) | §21 |
| P1 | Reactivate alert notifications and the outage watch | Owner's approval and choice of notifications first; Alertmanager paused 2026-10-11, outage watch removed 2026-10-26 | §14 |
| P1 | Maintenance windows and remaining host hardening | Backup/unseal/console recovery verified before maintenance; AUTH-9282/9286 and USB-1000 decided | §4, §20 |
| P1 | Capacity baseline, PostgreSQL headroom and Registry cleanup | Usage/growth alerts, protected recovery images, verified reclamation | §10, §12, §26 |
| P1 | Version-2 app migration and removal of legacy anonymous Kafka access | Owning repos verify environment isolation and delivery before credentials/listener retirement | §10, §19 |
| P1 | Multi-host expansion, stable endpoints and HA | P0 complete; topology helpers first; three eligible storage hosts; ordered migration and host-loss drills | §5, §15, §25–26 |
| P1 | Ransomware-resilient backups and tamper-evident audit retention | Retention-locked off-site copies the cluster cannot delete | §36–37 |
| P1 | Company e-invoice reception through an approved platform | Legal for every French company since 2026-09-01, so overdue; module present in the pinned Odoo image, registration by the legal representative ([Needs Input](#needs-input)), a test invoice received; issuing by 2027-09-01 | §40 |
| P2 | Installer secret resolution, rehearsals and report email | Bootstrap without Vault; fresh install, rerun, resume and recovery rehearsed on disposable hosts; hub checked in a real terminal (schema, engine, TUI, plan and verification done) | §23 |
| P2 | Host/HTTP detection coverage and remote-cluster telemetry | Scoped collection/retention; remote telemetry required before remote production use (API audit logging done) | §4, §14, §19 |
| P2 | Mailbox/directory migration and SSO roles | Provider ADR, verified DNS/account linking, deprovisioning and local recovery access | §27 |
| P2 | Mattermost, Nexterm and human password manager | Capacity and service ADRs; offsite restore and independent emergency access | §29 |
| P2 | Deployment UI | Existing app pipeline contracts and protected promotion; exact revision/rollout verification | §24 |
| P2 | Licensing ADR and release pipeline | Per-component licence review; bundles built and signed in CI, offline install and a green N-1 → N upgrade qualification (bundle, SBOM and signature tooling done) | §32 |
| P2 | Sovereignty mode | Edge without Cloudflare, self-hosted mesh; strict profile installs with no external call reported (GitHub optional, telemetry off and external-service inventory done) | §33 |
| P2 | Tenancy, roles and self-service data | `infra` isolation (§6) first; cross-project access denied in tests; claims include backups and alerts | §34 |
| P2 | Policy enforcement and periodic evidence | Application pack moved from audit to `Deny` per clean environment; signed evidence packs archived monthly | §36 |
| P2 | Status page and on-call | External status page fed by the outage watch; paging with acknowledgement (SLOs, burn-rate alerts, runbooks, alert email and the outage watch done) | §38 |
| P2 | CRA vendor readiness: role ADR, ENISA reporting runbook, disclosure policy | Art. 14 reporting applies since 2026-09-11 to products on the market, so it is needed before the first paid support, edition or managed contract; dated tabletop exercise; `SECURITY.md` published | §37, §43 |
| P2 | Exit export, self-operation runbook, handover and exit drill | The website already promises the runbook; Data Act switching rules since 2025-09-12, no switching charges from 2027-01-12; GitLab native backup (§12) first; dated drill onto plain K3s at another provider | §42 |
| P2 | Managed-service model, customer acceptance, support desk, monthly service report and contract templates | Needed before the first managed or financial-entity contract, and the website's legal pages and trust centre depend on them; Data Act fee ban from 2027-01-12 | §38–39, §43 |
| P2 | Questionnaire pack, contract annex, ReCyF mapping and ISMS kit | DORA since 2025-01-17 and French NIS2 still pending (ReCyF since 2026-03-17), so questionnaires are what buyers ask for now; the vendor's CSA STAR Level 1 entry filed | §36 |
| P2 | Licence register and measured installation | Validation fails on an unrecorded SBOM entry; release qualification records install time and success rate on two named EU offers | §32 |
| P2 | Reference installation, design partners, pilot scorecard and opt-in install report | P0 complete before the first external installation; the first partner pilots under a signed plan; the report stays off unless the operator opts in | §44 |
| P2 | CRA product requirements: secure defaults, update feed, security patch releases, support period, VEX, wipe and conformity file | Required for products placed on the market from 2027-12-11; CI-signed bundles, `--upgrade` and the CRA ADR first | §32, §42–43 |
| P2 | Sovereignty self-assessment and strict-mode coverage of the remaining non-EU paths | The Commission's sovereign cloud tender (2026-04-17) required SEAL-2; report generated per profile from the external-services inventory | §33 |
| P2 | Funding applications (NLnet, Hexatrust) | Owner decision ([Needs Input](#needs-input)); deadlines 2026-11-02 and 2026-11-03 | §44 |
| P2 | Custom application domains, passkeys, staff device coverage and asset inventory | Buyers ask for their own domains and phishing-resistant MFA; device and asset evidence fill NIS2 Art. 21(2)(i) and the questionnaire pack (§36) | §35–37 |
| P2 | Joiner/mover/leaver automation, staff VPN and office-hours scheduling | Useful to every customer from the first installation; §27 directory decision for the HR source; scale-to-zero measured in Capacity Planning | §45 |
| P3 | ILM/task naming and new-install Kafka identity cleanup | Preserve existing indices and Kafka cluster identity | §1, §10 |
| P3 | Optional installer web UI, n8n and Open WebUI/RAG | Proven engine/capacity, source permissions, regional routing and usage budgets | §23, §28 |
| P3 | Additional backup products, virtualization, NAS and GPU server | Named need, compatibility/restore proof and measured sizing/cost | §30–31 |
| P3 | Developer portal, platform API, golden paths and preview environments | Platform API first; measured template-to-URL time | §35 |
| P3 | Fleet console and customer-approved support access | Licensing ADR; outbound-only agent; approved, recorded and expiring session tested | §39 |
| P3 | Runtime detection, session recording and penetration test | Runtime rules fire on simulated attacks; pentest findings closed (audit-based detection, JIT administration and encrypted volumes done) | §37 |
| P3 | Module catalog, workplace and business modules, AI gateway | Module install/uninstall with restore test; capacity and GPU sizing | §40–41 |
| P3 | Workplace-suite ADR (openDesk or La Suite versus separate modules) | Decide before ZenDiS partner applications close on 2027-01-31; design-partner answers (§44) first | §40 |
| P3 | AI Act duties for the AI modules | Ship with the modules: Art. 50 since 2026-08-02, high-risk deployer duties from 2027-12-02 | §41 |
| P3 | Hardware health, second-site recovery and SOC forwarding | First bare-metal node (§25); §16 rebuild drill first; a customer SOC to feed | §37–38 |
| P3 | Docker Compose importer and dated server costs | Self-service claims (§34) first; price source and date recorded for each server | §34–35 |
| P3 | Error tracking, DAST, feature flags, data subject requests, awareness exercises, energy showback and new modules (wiki, helpdesk, web analytics, signing, SaaS mailbox backup) | Module catalog (§40) and self-service claims (§34) first; each with SSO, backups and a restore test | §34–37, §40 |
| P3 | Data warehouse and BI, internal tools builder, company search, meeting notes, translation, document capture, telephony, IoT and edge sites, dev workspaces, platform undo and phone approvals | Module catalog (§40), AI gateway (§41) and self-service claims (§34) first; each with SSO, backups and a restore test | §45 |
