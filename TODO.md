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

## Bugs to fix

Defects found while working on another task: misconfiguration, drift, failing
checks, scanner findings, insecure defaults or broken automation. Each entry names
the environment and resource, the evidence (command and output, plan excerpt or
finding) and the risk. Agents add entries here instead of widening their change.

- [x] Platform probes killed slow but working services and chained the restarts (`swirlit-node-01`, 2026-10-23). Under load (average 114, swap 511/511 MiB full, 200 MB/s of page-cache reads), 15 of the 27 liveness probes under `k8s/` used Kubernetes' 1-second default timeout, among them PostgreSQL, Kafka, MongoDB, Redis and Keycloak: `kubectl get events -A` showed `Readiness probe failed: command timed out: "pg_isready -U  -d " timed out after 1s`, then `Killing ... failed liveness probe` for PostgreSQL (41 restarts in 18 days, 3 within an hour, each ending `Completed`, exit 0: a probe kill, not a crash), Traefik, Prometheus, Grafana, Argo CD's repo server and CoreDNS, and every application using PostgreSQL restarted with it. The PostgreSQL probe also passed `$(POSTGRES_USER)` and `$(POSTGRES_DB)`, which a probe expands only from literal env values, so both were empty. Fixed: liveness probes wait 5 seconds an answer (10 for a datastore) and a minute of failures before a restart; readiness probes 3 seconds (5); PostgreSQL runs `pg_isready -q`, restarts after two minutes without an answer and has a 10-minute startup probe for crash recovery; `validate-manifests.py` enforces the rule and refuses `$(VAR)` without a literal value in an exec probe ([manifest policy](docs/code-quality.md#manifest-and-deployment-policy)).
- [x] The kubelet reserves no memory for the host and evicts nothing under memory pressure (`swirlit-node-01`, 2026-10-23): `kubectl get --raw /api/v1/nodes/swirlit-node-01/proxy/configz` gives `systemReserved` and `kubeReserved` unset and `evictionHard` with disk thresholds only (`imagefs.available`, `nodefs.available` 5 %), while requests take 62 % of the node's memory, limits 186 % and swap is full. Pods far above their requests (CI jobs request 128 Mi and may use 3 Gi) are never evicted first; the node swaps and thrashes instead, and every probe slows. Risk: a memory spike (a CI job, a JVM restart storm, host-side work) degrades every service at once. Fixed: [`configure-k3s-kubelet-memory.sh`](scripts/configure-k3s-kubelet-memory.sh) writes the drop-in `30-swirl-cloud-kubelet-memory.yaml` (`kubelet-arg+`): `system-reserved` 1 GiB, `kube-reserved` scaled to the node's memory as GKE does (3601 MiB here; the K3s process uses about 3.8 GiB), eviction below 1.5 GiB available for a minute and at once below 500 MiB, with K3s's disk thresholds kept. The installer, node enrollment, the application-cluster installer and Ansible write it before K3s starts, `./install.sh --status` reports it, and `tests/test-k3s-kubelet-memory.sh` guards the sizing and the drop-in. See [node memory and eviction](docs/operations.md#node-memory-and-eviction). Live on `swirlit-node-01` since 2026-10-23 19:02 UTC (24-second K3s restart, no pod restarted or evicted): allocatable 26 920 MiB of 32 045, requests at 75 % of it.
- [ ] The page cache thrashes when CI runs, and the node stalls although memory eviction never triggers (`swirlit-node-01`, 2026-10-23 19:24–19:26 UTC, after the kubelet reservation above): load 58, about 400 MB/s of reads (`vmstat`), I/O pressure `some avg60=68`, 290 MiB free and 3 GiB of page cache. Liveness probes failed even with 5-second timeouts and a one-minute window, restarting Prometheus, Grafana, Traefik (GitLab answered Cloudflare 521 meanwhile), metrics-server, the Argo CD repo server and the Longhorn CSI sidecars. Two CI jobs ran (Swirl Demo App `02-test`, Swirl Website `e2e`), about 800 MiB each. `kubepods.slice` never reached its limit (`memory.events.local` `max 0`) and the kubelet still measured about 3.8 GiB available, above the 1.5 GiB soft threshold. Pods hold 22.5 GiB of anonymous memory and the host about 5.4 GiB, which leaves too little page cache for the files the platform reads. Risk: any disk-heavy CI job can restart the ingress and the monitoring stack. Options: (a) more memory for the node; (b) one CI job at a time across both runner managers (`gitlab-runner` and the project-1 runner); (c) CI pods request the memory they use (`memory_request` 128 Mi → about 1.5 Gi in `k8s/platform/gitlab-runner.yaml`), so the scheduler holds a job while the node is full; (d) lower the JVM heaps and the limits of the largest services (§26). It recurred at 19:44 (load 69, I/O pressure `some avg10=96`, CoreDNS liveness failing) with Thoughty `02-test` at 1.8 GiB and Swirl Demo App `02-test` at 0.9 GiB. A higher soft eviction threshold alone would not help: ranked by use above request, GitLab comes first (4.7 GiB used, 2 GiB requested), ahead of either CI job, so the kubelet would evict GitLab. Any threshold change needs GitLab's request raised to what it uses first. At 20:07 the node went `NotReady` for about a minute (5-minute load 68; Traefik, CoreDNS and the release runner manager restarted), and a Swirl Demo App deploy job failed on a Kubernetes API TLS handshake timeout.
- [ ] The Argo CD application controller is OOM-killed at its 1 GiB limit (`infra/argocd-application-controller-0`, 192 restarts in 25 days; again at 19:15 and 19:26 UTC on 2026-10-23 after a K3s restart, when it rebuilds its cluster cache): `kubectl get pod -n infra argocd-application-controller-0 -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}'` gives `OOMKilled`, while it runs at about 370 MiB between restarts. Each restart re-lists every watched resource through the API server and delays syncs. Proposed: raise `controller.resources.limits.memory` in `config/argocd-values.yaml` to 2Gi with `GOMEMLIMIT` at about 1.5GiB, and apply it with `scripts/reconcile-platform-release.sh argocd`.
- [x] Every Grafana dashboard showed no data (`infra/prometheus`, 2026-10-23): Prometheus failed its liveness probe and restarted 10 times in 5 hours (`Liveness probe failed: ... context deadline exceeded`, exit 137, no OOM kill), then spent about 50 seconds replaying its WAL each time; meanwhile its Service had no ready endpoint and Grafana got `connection refused` from `prometheus.swirlit.internal:9090`. It held 194 000 head series in a 1 GiB limit (833 MiB anonymous, Go memory limit 966 MB, 88 000 file refaults within minutes of a start) on one CPU, so `GOMAXPROCS=1`: under the node's I/O pressure (open entry above), one chunk read stalled on disk froze every handler. 57 000 of the series came from the K3s API server, etcd/kine, scheduler and work queues on the kubelet endpoint, read by no dashboard, rule or script (checked against all 24 provisioned dashboards and every rule). Fixed: the `kubernetes-nodes` job drops those families, and Prometheus gets 2 CPUs and 2 GiB (request 768 MiB, about what it uses) ([monitoring.yaml](k8s/platform/monitoring.yaml)).

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

- [x] Single GPL-3.0 repository; `.editorconfig`, `.gitattributes`, `.shellcheckrc`, `.gitignore` excluding credentials, `.pem` (except the public origin CA), kubeconfigs, caches and `__pycache__`.
- [x] Shared deployment contract in `config/platform.env` (`PLATFORM_CONTRACT_VERSION=7`), sourced by Bash and read by Ansible as properties: pinned versions (K3s `v1.36.4+k3s1`, Traefik chart 41.5.0, Longhorn 1.12.1, Vault chart 0.34.1, ESO 2.9.0, Argo CD chart 10.3.3 / image v3.5.2), timeouts, ordered manifest inventories, ExternalSecret wait list, datastore/platform/DaemonSet wait lists, Cloudflare host inventories.
- [x] Helm chart `k8s/` (`swirl-cloud` 2.0.0) with vendored dependencies `redis-ha` 4.39.0 (HA only) and `trivy-operator` 0.36.0 for offline rendering.
- [x] One renderer for installer and GitOps: `scripts/render-cluster-config.sh`, `render-identity.py`, `render-platform-ha.py`; renders into a fresh private temp dir and refuses reused output.
- [x] Placeholder substitution (`__PUBLIC_DOMAIN__`, `__INTERNAL_DNS_ZONE__`, `__KEYCLOAK_REALM__`, `__TLS_SECRET_NAME__`, `__GITLAB_GROUP_PATH__` …) so the checkout stays organization-neutral.
- [x] Offline validator `scripts/validate-repository.sh`: ShellCheck, Python/Ansible syntax, doc-link checks, manifest inventories, workload policy, image-digest pinning, nine installer/GitOps render combinations (HA, Odoo scope, PostgreSQL staging/cutover, Kafka phases, fencing, authenticated app data), Service selectors, named ports, Ingress backends, RBAC and installer/GitOps parity, patch whitespace and credential scan. *(Passes locally on 2026-09-27.)*
- [x] Live validator `validate-repository.sh --live`: server-side dry-run of every rendered profile against the active cluster, no mutation.
- [x] `validate-repository.sh --live` passes against the current cluster again. The earlier Job conflict only occurred while an Argo CD sync hook was running; objects that need `cnpg-system` or CloudNativePG APIs before HA activation are now counted as not validated instead of failing. *(live, 2026-10-04: 8 objects reported)*
- [x] Fourteen retained safety suites in `tests/` (K3s backups, K3s API endpoint, PostgreSQL HA, Kafka HA, Vault unseal, Vault HA, node fencing, application clusters, application data, environment onboarding, installation inputs, Odoo website integration, platform mail, outage watch).
- [x] Documentation follows the shared application-repository layout ([ADR 0001](docs/adr/0001-documentation-structure.md)): `features`, `architecture` (overview + ADR index), `data-model`, `development`, `testing`, `code-quality`, `deployment`, `operations`, `security`, `dns`, plus eight procedures under `docs/guides/`; Mermaid topology and delivery diagrams.
- [x] 25 ADRs in `docs/adr/` reconstructed from the Git history, including three superseded decisions (ingress-nginx, Jenkins/Nexus, private rebuilt images).
- [x] The Ansible hardening step is named "Reconcile hardened system controller templates"; private image overrides were retired in Chart 2.
- [ ] Reconcile the Elasticsearch ILM policy name `swirl-cloud-logs-7d` with its actual delete phase (`min_age: 1d`) and the retention decision in §26; migrate existing index/template references without dropping retained logs. *Progress 2026-10-14:* source ready, not pushed. The policy is now `swirl-cloud-logs` with the same phases (hot, then delete at `min_age: 1d`), so §26 changes it in place. Fluent Bit's init container stores it (and stops if that fails) before pointing the `kubernetes-logs-v2` and `kubernetes-logs-host` templates and the existing `kubernetes-logs-*` and `app-logs-*` indices at it; Logstash `config-version` 3 restarts Logstash so it installs `app-logs-single-node` with the new name. Nothing deletes `swirl-cloud-logs-7d`; why the indices switch without removing it first, the residual risk and its manual removal are in [storage and retention](docs/operations.md#storage-and-retention). `validate-manifests.py` now rejects an `index.lifecycle.name` that no rendered manifest stores. Closes after the owner-approved push and two live checks in Kibana Dev Tools. Right after the sync: `GET _ilm/policy/swirl-cloud-logs` returns the policy; `GET kubernetes-logs-*,app-logs-*/_ilm/explain?filter_path=indices.*.managed,indices.*.policy,indices.*.phase,indices.*.step,indices.*.failed_step,indices.*.step_info` shows every index `managed: true` with `swirl-cloud-logs` and no `ERROR` step, `failed_step` or error `step_info`; `GET _index_template/app-logs-single-node?filter_path=**.lifecycle` and `GET _template/kubernetes-logs-v2,kubernetes-logs-host?filter_path=**.lifecycle` name it; and `GET _cat/indices/kubernetes-logs-*,app-logs-*?h=index,creation.date.string,docs.count&s=index`, taken before and after the sync, still lists every earlier index with no lower document count, except one created more than a day before the second sample, which ILM deletes as designed. A silently stopped phase shows no error, so 25 hours or more after the sync (one day plus the 10-minute ILM poll, with margin): `GET kubernetes-logs-*,app-logs-*/_ilm/explain?filter_path=indices.*.managed,indices.*.age,indices.*.phase,indices.*.phase_execution.policy` shows every index `managed: true`, none with an `age` above about `1.1d`, and no `swirl-cloud-logs-7d` under `phase_execution`, so the indices that existed before the change were deleted on time.

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
- [x] Stable private Kubernetes API endpoint ([ADR 0028](docs/adr/0028-kubernetes-api-endpoint.md)): keepalived VIP on vRack/LAN or a Tailscale Service, held only while the server's `/readyz` succeeds; endpoint TLS SANs, node registration and `--kubeconfig` through it (`configure-k3s-api-endpoint.sh`). Failover drill in §25. *(not live: one control plane)*

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
- [ ] No HSTS or security-header middleware at the origin; header policy relies entirely on Cloudflare and each app. *Progress 2026-10-14:* source ready. `infra/security-headers` on the `websecure` entry point (`config/traefik-values.yaml`) repeats the edge's HSTS (`CLOUDFLARE_HSTS_MAX_AGE`, subdomains, no preload; none from Traefik when `CLOUDFLARE_ENABLE_HSTS=false`) and `nosniff`; a rerun without the inputs keeps the deployed value, and local installations send only `nosniff`. Argo CD does not manage Traefik: it takes effect when the operator reruns the ingress stage, `./scripts/configure-ingress.sh` on a control plane with the zone's HSTS inputs; existing remote application clusters take it when `scripts/configure-deployment-environments.py` registration is rerun. Close with the [origin check](docs/dns.md#ingress-controller): `kubectl -n infra get middleware security-headers` exists and, with `kubectl -n infra port-forward svc/traefik 8443:443` running, `curl -sSI --cacert config/cloudflare-origin-ca.pem --connect-to grafana.<domain>:443:127.0.0.1:8443 https://grafana.<domain>/login` returns `strict-transport-security: max-age=31536000; includeSubDomains` (absent before the rerun; Grafana sends none itself, unlike Keycloak) and `x-content-type-options: nosniff`.
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
- [x] Administrator second factor is optional by decision (2026-10-05): users can enrol an authenticator in the account console and are then asked for a code at sign-in; `./install.sh --admin-mfa status|require` reports and, when wanted, requires enrolment for `platform-admins` ([security](docs/security.md#administrator-second-factor)).
- [ ] Odoo's client uses the OAuth2 implicit flow (Odoo 19 `auth_oauth` limitation); replace with an authorization-code add-on when available.
- [x] Keycloak sends password-reset and verification emails through the platform mail relay (§27): the realm reconciliation sets the realm's mail server from Vault `infra/smtp` and clears it while platform mail is off. *(live, 2026-10-09: verification email delivered)*
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
- [ ] Automate GitLab native backup plus matching configuration, secrets, registry files and metadata DB where enabled; upload offsite and restore with the matching GitLab version. Verify repository, LFS, package and Registry push/pull recovery, then integrate backup age/failure alerts (§16). *Progress 2026-10-14:* configuration and secrets, source ready. `scripts/backup-k3s.sh` copies `/etc/gitlab` (`gitlab-secrets.json`, SSH host keys, `gitlab.rb`) out of the one running GitLab pod into the nightly archive as `gitlab/etc-gitlab.tar` (mode 600), with `gitlab/version.txt` (GitLab version, image and running digest) and restore steps in `RESTORE.txt`; a failed or incomplete copy fails the run without publishing an archive, so `HostMaintenanceServiceFailed` and `K3sRecoveryBackupMissing` cover it, and clusters without GitLab skip it (`tests/test-k3s-backups.sh`). It takes effect when the operator reruns `./scripts/configure-k3s-backups.sh` on the control plane, which installs `/usr/local/sbin/swirl-cloud-k3s-backup` and starts a backup (the installer and `ansible-playbook -i ansible/inventory ansible/deploy.yml` also run it; Maintenance → Run the recovery backup now does not update the script). Check with `sudo sh -c 'tar -tvzf "$(ls /var/backups/swirl-cloud/k3s/k3s-*.tar.gz | tail -n 1)" ./gitlab/'`: `etc-gitlab.tar` is `-rw-------` beside `version.txt`. Remaining: the native backup of repositories and database (scope under [Needs Input](#needs-input)), its off-site upload and a restore drill with the same version.
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
- [x] Critical and warning alerts are also emailed through the platform mail relay (§27): one message per alert name, batched every 15 minutes, repeated daily, with the resolution. Every alert still reaches GitLab, and `AlertNotificationsFailing` fires when all of one receiver's deliveries fail for an hour. Chat and paging receivers stay in §29 and §38. *(live, 2026-10-09: a test alert and two real alerts emailed when firing and when resolved; 0 failed notifications)* Paused since 2026-10-11 (next item).
- [ ] **Waiting for the owner's approval and input:** reactivate Alertmanager notifications, paused on 2026-10-11 because they were too frequent. Every alert now goes to the `paused` receiver (no GitLab alert, no email); the `gitlab` and `email` receivers remain configured. Do not restore them until the owner approves and says which notifications to bring back, then restore the routes in `k8s/platform/monitoring.yaml` and `tests/test-platform-mail.py` and the paused notes in `docs/operations.md`, `docs/features.md` and `docs/guides/platform-mail.md`.
- [x] Fluent Bit keeps Kubernetes metadata for CI job logs (256 KB filter buffer for large `gitlab-runners` pod specs) and no longer indexes Trivy scan pods' compressed report payloads. Over 15 minutes with 16 scans, no container record lacked metadata and 1 112 runner records were enriched. *(live, 2026-10-04)*
- [x] Host metrics through node-exporter's textfile collector (`/var/lib/node_exporter/textfile_collector`): Lynis run state, pending reboots and rkhunter warnings, with `LynisAuditFailed`, `LynisAuditMissing` and `NodeRebootRequired`. *(live, 2026-10-04)*
- [x] Elasticsearch is green again: the Kibana user sync now reconciles its ownership index to zero replicas (created with one before 2026-09-28), and the two legacy `.reindexed-v8-kibana-event-log-*` indices were set to zero replicas once by hand. *(live, 2026-10-04)*
- [x] Kibana's **Lynis Findings** search queried application-log fields and matched nothing; it now selects `lynis.finding_type : *` (1 368 findings). *(live, 2026-10-04)*
- [x] Platform alerts: node NotReady, Vault sealed/audit failure, Longhorn degraded/faulted, datastore down, Argo CD application unhealthy or out of sync for 30 min, and TLS certificate expiry (14 days warning, 3 days critical). *(live)*
- [x] `swirl-cloud-k3s-backup.service` (K3s, Vault and application archives) and `swirl-cloud-vault-unseal.service` record each run through an `ExecStopPost` drop-in; `HostMaintenanceServiceFailed`, `K3sRecoveryBackupMissing` (36 h) and `VaultUnsealTimerStopped` alert on them. *(live: unseal metrics and failure path verified, rules healthy, 2026-10-04)*
- [x] Nightly backup verified after the 2026-10-05 02:18 UTC run: `swirl_cloud_systemd_unit_last_success_timestamp_seconds{unit="swirl-cloud-k3s-backup.service"}` is reported (`./install.sh --status` passes) and the archive holds `application-data/odoo-filestore.tar.gz` with the PostgreSQL and MongoDB dumps, Vault snapshot and K3s state.
- [x] External Secrets is scraped (job `external-secrets`); `ExternalSecretNotReady` fires after 15 minutes. *(live, 2026-10-04: pending immediately for the two orphaned `platform-registry-auth` ExternalSecrets below)*
- [x] Orphaned `platform-registry-auth` ExternalSecrets in `kube-system` and `longhorn-system` are gone and no system pod references the Secret; it remains only where Git declares it (`apps`, `gitlab-runners`, `infra`, all synced). *(verified 2026-10-05)*
- [x] Elasticsearch is scraped through `elasticsearch-exporter` with the monitor-only `metrics_monitor` user (Vault `infra/elasticsearch`; document reads are denied); `ElasticsearchUnreachable`, `ElasticsearchClusterRed` and `ElasticsearchClusterYellow` alert on it. *(live, 2026-10-04: green, rules healthy)*
- [x] `LonghornVolumeBackupStale` alerts on any volume without an off-node backup in 36 hours once the backup target holds backups (silent before activation). *(2026-10-04)*
- [x] CrowdSec/Fail2ban activity alerts: `swirl-cloud-intrusion-metrics.timer` (host hardening) exports service state, bans and decisions; `HostIntrusionPreventionDown`, `HostIntrusionMetricsStale` and `HostBruteForceWave` (rules and exporter output checked with `promtool` against live counts, 2026-10-04). Live on `swirlit-node-01` since 2026-10-04.
- [x] Trivy critical findings alert with a baseline: `TrivyNewCriticalVulnerability` fires only when an image's critical count exceeds its own recent maximum or a new image arrives with criticals (0 series fire on 2026-10-04; all 14 affected images would fire on one more finding). The 6 h baseline fits today's size-bound Prometheus retention (about 12 h); widen it with §26.
- [x] Scraped: Traefik, Vault, Argo CD, Longhorn, PostgreSQL, MongoDB, Redis and Kafka.
- [ ] Not scraped: cloudflared (only runs in HA Tunnel mode).
- [ ] Define remote-cluster telemetry ownership and add authenticated metrics/log shipping, cluster/environment labels, quotas and node/backup alerts before relying on remote production targets (§19).
- [ ] Define recoverable Elasticsearch log/audit retention and configure a snapshot repository if that history must survive volume loss (§16).
- [x] Outage watch from outside the cluster ([ADR 0030](docs/adr/0030-outage-watch-outside-the-cluster.md), [guide](docs/guides/service-levels.md#outage-watch)): a Cloudflare Worker deployed by `scripts/configure-outage-watch.py` (and the installer) checks every origin-reaching public host every five minutes and emails each down and recovery change through the platform mail relay (§27). State lives in a Durable Object; the Worker has no route and stays off `workers.dev` (the account's `swirlit` subdomain was created because Durable Objects require one). *(live, 2026-10-09: 13 endpoints; status report, then a drill's down and recovery emails delivered 10 minutes apart)*

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
- [ ] Schedule `restic check` and isolated restore drills for both archives and Longhorn volumes, with integrity/data checks and alerts on missing or stale backups. Keep deletion protection compatible with restic pruning and Longhorn's own backup retention; do not apply generic object expiry to active backup stores. *Progress 2026-10-14:* weekly archive verification, source ready. `swirl-cloud-k3s-backup-verify.timer` (Wednesdays 05:30 UTC ± 30 min, enabled only while `/etc/swirl-cloud/backup.env` names a restic repository) runs `scripts/verify-k3s-backup.sh` under the backup lock: `restic check` on one of four data subsets rotated weekly, then the newest archive streamed out with `restic dump` and checked against its recorded SHA-256 and as a complete `tar.gz` holding `RESTORE.txt`, never written to disk. A failure, or a backup holding the lock for over an hour, fails the unit and raises `HostMaintenanceServiceFailed` through the `configure-node-security.sh` metrics drop-in (`tests/test-k3s-backups.sh`, mocked restic). `backup-k3s.sh` now exports `backup.env`, without which restic could not see the repository or credentials and the first off-node upload would have failed; it waits up to four hours for a verification holding the lock and then fails instead of silently skipping; and its `restic forget` groups by host and tag, since the default grouping by path kept every timestamped archive and would have grown the repository (and each check's egress) without bound. It takes effect when the operator reruns `./scripts/configure-k3s-backups.sh` and `./scripts/configure-node-security.sh --host-baseline` on the control plane (Ansible reconciliation runs both). Remaining: the off-site target ([Needs Input](#needs-input)), a live timer run, a recorded isolated restore drill of an archive, an alert when the verification stops running (a rule in `k8s/platform/monitoring.yaml`), and the Longhorn volume drills.
- [ ] Write and rehearse a full rebuild on an empty host with the original host unavailable: obtain Git, pinned recovery images and keys independently; restore K3s/storage/Vault/data, then GitLab/Argo CD, SSO and application traffic. Measure achieved RPO/RTO and exercise the documented backup format before node migration.

### 17. Corporate workloads (Odoo in `corp`)

- [x] Odoo 19 (digest-pinned) in `corp` behind `INSTALL_SCOPE=apps` / `appsEnabled`, `dbfilter ^odoo$`, `list_db = False`, `proxy_mode`, 2 workers, memory/time limits, body-limit middleware, allow-ingress NetworkPolicy, Longhorn filestore PVC.
- [x] Bootstrap creates the database/role on PostgreSQL, main company named from the organization, managed administrator.
- [x] Keycloak SSO through the bundled `auth_oauth` module plus a managed `odoo-keycloak-sso` add-on; vendored OCA `web_dark_mode` 19.0.1.0.0 (AGPL, provenance), enabled once for the administrator.
- [x] Migrated from `infra` to `corp` with documented volume migration (`0e16c84`).
- [x] Odoo sends quotes, invoices and password resets through its "Platform mail" outgoing server, which the bootstrap keeps in line with the platform relay (§27), after any more specific server such as a website's. *(live, 2026-10-09: connection test successful)*
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
- [ ] Add the platform mail relay (§27) to the app onboarding contract, so applications can request scoped sending credentials; application repositories own their mail usage and templates.
- [x] Environment `ingressAddress` accepts hostnames, and `configure-application-dns.py` already selects A, AAAA or CNAME records. Remaining ingress alias and API endpoint work is in §25.

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

- [x] Lynis refreshed through `swirl-cloud-lynis.service`: hardening index **66**, one warning (KRNL-5830; `linux-image-7.0.0-38` installed 2026-10-03, running 7.0.0-34). Every finding below still applies; DEB-0810, FIRE-4513, LOGG-2190, NAME-4028 and BOOT-5180/5264 are informational. *(live, 2026-10-04)*
- [x] **KRNL-5830:** the host runs the newest installed kernel (7.0.0-38, booted 2026-10-04 16:48) and no reboot is pending. Later kernel updates raise `NodeRebootRequired`; the operator reboots when convenient. *(verified 2026-10-05)*
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
- [x] Keycloak, SonarQube and Kafbat UI — reported Netty/Bouncy Castle/FreeMarker findings. Keycloak upgraded 26.7.4 → 26.7.5 *(live, 2026-10-04: 0 critical / 7 high, was 4 / 19; pre-upgrade database dump in `/var/backups/swirl-cloud/pre-change/`)*. Kafka UI v1.5.0 is the latest release (exception). SonarQube 26.9.0 (latest Community) reports Netty and Bouncy Castle findings (exception). Rechecked 2026-10-05: no newer SonarQube Community or Kafka UI release.
- [x] Elasticsearch, Kibana and Logstash 9.5.4 are the latest 9.5 patch; their Netty SNI, `piscina` and Bouncy Castle findings are recorded as dated exceptions. *(2026-10-04)*
- [x] GitLab Runner 19.4.1 no longer reports OpenSSH findings. The helper's Alpine `miniperl` critical waits for the next runner patch (dated exception). *(2026-10-04)*
- [x] Longhorn `csi-provisioner` assessed: no upstream v5.3 patch; overriding one sidecar is outside Longhorn's support matrix, so the fix follows the Longhorn release that bundles a newer provisioner (exception and path in [security](docs/security.md#vulnerability-triage)). *(2026-10-05)*
- [x] MongoDB tools, Node helper, Homepage and DBGate assessed: the Node helper (`node:24.21.0-alpine`) has no critical findings; MongoDB's bundled Go tools, Homepage (no `next/og` code in its server bundle) and DBGate's Debian packages are dated exceptions with reachability notes in [security](docs/security.md#vulnerability-triage). *(2026-10-04)*
- [x] PostgreSQL/DBGate Debian-library findings checked against the Debian security tracker (ignored, postponed or no-DSA minor issues; one Perl CVE predates bookworm's Perl) and runtime use; accepted with evidence in [security](docs/security.md#vulnerability-triage) until 2026-11-04. *(2026-10-04)*
- [x] Exposed-secret findings triaged: GitLab's `/etc/ssh` host keys are build-time keys replaced at start by the installation's own `/etc/gitlab` keys (fingerprints compared against the pristine image), the GitLab token matches are gitleaks rule examples and a masked placeholder, gem keys and JWTs are fixtures, and PostgreSQL's is Debian's snake-oil certificate; no real credential to rotate. Exceptions until 2026-11-05 in [security](docs/security.md#exposed-secret-findings). *(2026-10-05)*
- [x] No stale reports for retired owners remain: all Trivy Operator reports carry owner references and Kubernetes garbage-collects them with their workloads; none reference the retired `security/` images. *(live, 2026-10-03)*
- [x] Non-blocking CI job `report:platform-artifacts` scans the vendored chart archives (embedded misconfiguration checks against chart defaults; the rendered manifests are audited live by Trivy Operator) and the pinned CI Alpine image through the in-cluster Trivy server; reports are kept 30 days as job artifacts. The GitHub workflow uses no third-party actions. *(pipeline 2026-10-04 passed)*
- [ ] Hand application image findings to the owning repositories (2026-10-04 scan): TravelMapster `1.8.1` has 4 critical Debian bookworm findings (Perl, zlib minizip, the same accepted classes as PostgreSQL); `postgres:18.6-bookworm` is used by Thoughty's `postgres-backup` CronJob and Indezy's `wait-for-db` init container. No other application image has critical findings. Not yet recorded in those repositories; TravelMapster's checkout has an unpushed local commit, so it was left untouched.

### 22. Odoo for the company website (CRM leads and Live Chat)

The website owns its form, consent text and chat button; the platform owns Odoo
and the wiring. CRM and Live Chat serve only the company website
([Odoo website integration](docs/guides/odoo-website.md)).

- [x] Odoo bootstrap installs `crm`, `im_livechat` and the managed `platform_crm_intake` add-on, enables Leads, names the chat channel after the organization with the administrator as agent, and adds an English/French chatbot that, while no agent is online, collects the visitor's email and request and creates a lead. *(live, 2026-10-07)*
- [x] Lead intake on the JSON-2 API: `scripts/configure-odoo-website.py` creates the website's portal integration user in a dedicated group and stores its key in Vault `apps/<website>/odoo`; the key can only call the idempotent `crm.lead/platform_intake_submit` (one lead per reference, also under concurrent retries; it cannot read, write or create leads directly or list contacts). *(live, 2026-10-07; contract tested on a disposable Odoo 19)*
- [x] In-cluster path: Odoo admits only `apps` pods labelled `swirl-cloud.io/odoo-crm-intake: "true"`, on port 8069; the chat bus stays behind Traefik. *(live, 2026-10-07)*
- [x] Public Live Chat for the website: `/platform_crm_intake/chat/<id>` runs Odoo's widget on Odoo's origin, framable only by the recorded website origin (`frame-ancestors`), and reports open/closed state to it, so the website keeps a strict Content Security Policy. *(live, 2026-10-07: French and English, desktop and phone, close and reopen, no CSP violations)*
- [x] Cloudflare login rate limit also covers chat session starts (`/im_livechat/get_session`, `/im_livechat/cors/get_session`). *(live, 2026-10-07: rule updated to the script's definition and read back)*
- [x] Chat by email: new visitor messages are emailed to the team after a pause; a reply to that email is posted in the chat as the chat's agent and emailed to a visitor who has left, whose own reply returns to the chat. Signed plus-addresses and an allowlist of senders; Odoo sends through the website's Resend account; chatbot leads no longer trigger Odoo's assignment email. *(live, 2026-10-07: all three emails sent, replies posted, a stranger's reply refused)*
- [x] Chatbot persona from the website's `chat-persona.json` (name, avatar, English/French texts), applied by `configure-odoo-website.py`; the default chatbot no longer says that nobody is available. *(live, 2026-10-07)*
- [x] In the website's chat frame, the chat window shows its correspondent as online (green dot, "En ligne"/"Online" in the page's language) under their name; the frame reports an opened chat within 100 ms; the default chatbot's closing message promises a quick answer without naming the channel. *(2026-10-09: verified on a local Odoo 19 with the production image)*
- [x] Cloudflare Email Worker `platform-chat-email`, the `chat@<domain>` routing rule and zone subaddressing, deployed by `configure-odoo-website.py --email-worker`. *(live, 2026-10-08: real replies sent through Cloudflare reached the chat from both the team and the visitor)*

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
- [ ] Email the report when the deployment is ready, with dashboard URLs for every service and application (from Ingress hosts and Homepage), through the platform mail relay (§27).
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
- [x] Transactional SMTP contract ([ADR 0029](docs/adr/0029-platform-transactional-mail.md), [platform mail](docs/guides/platform-mail.md)): one operator-supplied relay (host, port, implicit TLS or STARTTLS, login, sender, alert recipients), checked by `scripts/configure-platform-mail.py` before it is stored in Vault `infra/smtp` (seeded with mail off); installer inputs `PLATFORM_MAIL_*`, the hub's Email section and an SMTP login test. Alertmanager, Keycloak, GitLab and Odoo send through it; Grafana needs none (Keycloak sign-in, Alertmanager alerts). The live relay is a sending-only Resend key restricted to the domain, sending as `cloud@swirlit.dev`. *(live, 2026-10-09: relay test, alert, GitLab and Keycloak emails delivered; Odoo connection test passed)*
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
- [x] Release tooling ([releases](docs/guides/releases.md)): chart version as platform version, `scripts/release.py prepare` (version and CHANGELOG.md from commits since the last tag), annotated `v<version>` tags. *(2026-10-05; no release cut yet)*
- [ ] Release channels (`stable`, `lts`) with a defined security-backport window, and upgrade notes per release. State each release's end of support (month and year) in the release metadata, `SECURITY.md` and the support policy (§39). For products placed on the market from 2027-12-11, the CRA requires at least five years of support (or the expected use time if shorter) and keeps each security update available for ten years ([Art. 13(8)–(9)](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/eng)).
- [x] Release bundles with a CycloneDX 1.5 SBOM (53 pinned images by digest, 7 charts, K3s and Helm), image list, release notes, SHA256SUMS and an SSH signature, verified offline by `scripts/release.py verify` (tamper test fails as expected). *(2026-10-05)*
- [ ] Build and sign bundles in CI on tags with the key in Vault, publish them as GitLab releases, and make the installer and upgrader refuse unsigned or altered bundles; add build provenance.
- [ ] Offline installation bundle: K3s binaries, charts, every pinned image and the required OS packages in one verified archive, loaded into the internal Registry. Prove a full install on a host with no Internet egress.
- [ ] Platform upgrades as one operation (`./install.sh --upgrade <version>`): preflight (health, fresh verified backup, capacity, API deprecations through `validate-repository.sh --live`, migration guards), ordered component upgrades, node-by-node K3s upgrades, recorded rollback points and post-upgrade `--status`. Support and test only N-1 → N paths.
- [ ] Release qualification pipeline: each release candidate installs on disposable hosts, upgrades from the previous release, runs the safety suites and `verify-platform.py --strict`, and blocks the release on failure.
- [x] Support matrix in the [deployment guide](docs/deployment.md#support-matrix): Ubuntu LTS (tested 26.04), x86_64 (GitLab is amd64-only), K3s pin, topologies, and minimum/recommended/reference sizes from measured requests and usage. *(2026-10-05)*
- [ ] Extend the support matrix with sizing profiles (§26), additional tested Ubuntu releases and named providers once installations on them are verified.
- [ ] Publish versioned product documentation from `docs/` (administrator, operator and application-team guides, API reference), one version per release.
- [x] Keycloak sign-in, account and administration pages in English and French, chosen from the browser language with a selector; Odoo already has French installed. *(2026-10-05)*
- [x] Setup hub in French with `./install.sh --lang fr` (or `SWIRL_CLOUD_LANG=fr`): menus, overview, every input prompt and help, checks, maintenance and common messages, translated where text reaches whiptail; untranslated lines stay in English. *(2026-10-05)*
- [ ] Translate the installer engine's console messages, the verification report and the portal (Homepage) for French installations.
- [ ] Third-party licence register: `config/third-party-licences.yaml` lists every image and chart in the release SBOM, with its SPDX licence, its source, whether redistribution and operation for customers are allowed, and its obligations. `validate-repository.sh` fails when an SBOM entry has no record, and bundles ship a `THIRD_PARTY_NOTICES` file. Record a decision for `mongo` (SSPL; section 13 targets offering MongoDB itself as a public service, [SSPL FAQ](https://mongodb.com/legal/licensing/server-side-public-license/faq), read 2026-10-23), `redis` 8 (RSALv2, SSPLv1 or AGPLv3, [Phoronix, 2025-05-01](https://www.phoronix.com/news/Redis-8.0-Goes-AGPLv3)), the Elastic 9.5 images (unverified whether the pinned images ship under Elastic License 2.0 or AGPLv3), `cp-kafka` versus `apache/kafka`, SonarQube, Odoo and Portainer. This feeds the licensing ADR, and the website's editions page depends on it.
- [ ] Secure-by-default product profile, kept separate from this installation's choices. New installations require MFA for `platform-admins` (`--admin-mfa require`), use key-only SSH with a key-provisioning step in the installer (§4), bind the application policy pack in `Deny` for `prod`, and keep vendor telemetry off. Each opt-out is an explicit input recorded in the evidence pack. The CRA requires a secure-by-default configuration for products placed on the market from 2027-12-11 ([Annex I](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/eng)). Depends on the `Deny` move (§36).
- [ ] Measure installation. Every installer run, including failed ones, writes a record under `/var/lib/swirl-cloud/` (phases, durations, exit class, versions), and `--status` shows it. Release qualification times an express install on a reference host, from bare Ubuntu to a passing `verify-platform.py --strict` and the first SSO login. It runs with and without the offline bundle, on at least two named EU offers, and writes the median time and success rate to the release notes and the support matrix. Until then, neither Part IV's "installs in hours" nor the website gives an install time. Depends on the release qualification pipeline.
- [ ] Small-company and evaluation profile (§26): one server below today's 24 GiB minimum. SonarQube and Kafka are optional, and a lighter log store is offered (chosen in an ADR). The support matrix gives the profile's measured requests and monthly cost at current list prices. Done when it installs from a README quick start on one named EU offer without help from the vendor. Server prices rose in 2026 ([Hetzner, new orders from 2026-06-15](https://docs.hetzner.com/de/general/infrastructure-and-availability/price-adjustment/)), so memory sets most of the fixed cost the product promises.
- [ ] Signed release and update feed, hosted by the vendor in the EU. It carries release bundles, security advisories (CSAF with VEX) and, where the licence register allows, mirrors of the Trivy database and the pinned images for strict and offline installations. The hub and `--status` report available updates. Component update checks stay off (§33), so this feed is the single disclosed update channel, listed in the external-services inventory. From 2027-12-11 the CRA requires update notification and secure update distribution. Depends on bundles built and signed in CI.
- [ ] Security patch releases, separate from feature releases. By default they apply unattended in the declared maintenance window, with opt-out and postponement, and administrators get a notice; a failed health check rolls back to the recorded point. From 2027-12-11, CRA Annex I requires automatic security updates by default where applicable, with an opt-out, and security updates free of charge. Depends on `--upgrade` and maintenance windows (§4).
- [ ] VEX per release, generated from the recorded vulnerability exceptions (§36) and shipped beside the SBOM. Keep per-component records (maintainer, jurisdiction, support status, end-of-life date) with the licence register, and fail release qualification on a component past end of life. This supports CRA Art. 13 due diligence on third-party components (2027-12-11) and the sovereignty report (§33).

### 33. Sovereignty mode and provider independence

- [x] [External services](docs/guides/external-services.md): every outside service, update source and data flow with provider, headquarters and the data it receives. Vendor telemetry and update checks switched off: GitLab Service Ping and version check, Grafana reporting/update checks/news/Gravatar, Kibana and SonarQube telemetry, Portainer statistics, Longhorn upgrade checker, Odoo publisher-warranty report. *(live, 2026-10-05)*
- [ ] Traefik's version check (`global.checkNewVersion: false`) takes effect at the next ingress reconcile (`scripts/configure-ingress.sh`). CrowdSec keeps sharing attack signals with its Central API by decision (2026-10-05).
- [ ] **ADR — edge without Cloudflare:** add an edge mode that needs no Cloudflare account: ACME certificates (cert-manager or Traefik), DNS through an EU provider API, in-cluster WAF and rate limiting (Traefik with CrowdSec or Coraza, linked to §4's HTTP IDS), Keycloak-gated admin hosts in place of Access, and the provider's network DDoS protection. Keep Cloudflare as a supported option.
- [ ] Private networking without Tailscale's coordination service: support a self-hosted control server (Headscale or NetBird) or WireGuard directly as an additional `K3S_NODE_TRANSPORT`; encrypt vRack and LAN node traffic too (flannel `wireguard-native`), which travels unencrypted today.
- [x] Repository onboarding works from GitLab alone: `./add-repos.sh --gitlab` (or `REPOSITORY_SOURCE=gitlab`) deploys existing projects in the platform group through their contracts with no GitHub account or mirroring ([projects without GitHub](docs/guides/repository-onboarding.md#projects-without-github)). *(2026-10-05: verified finding `swirlit/indezy` and rejecting a missing project, no deployment)*
- [x] Installer inputs `REPOSITORY_SOURCE` (`github`/`gitlab`) and `GITLAB_PROJECTS`, passed through the engine and the hub's repository action; GitHub inputs are required only for the GitHub source.
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
- [x] Cost showback from each server's recorded monthly cost (`scripts/configure-node-cost.sh`, installer input `SERVER_MONTHLY_COST`, hub Maintenance): allocation by the larger of requests and usage (65 % CPU / 35 % memory, capped at server cost), idle and shared costs, Grafana **Cost Showback** per environment, namespace and workload with a rightsizing view ([cost showback](docs/guides/cost-showback.md)). *(live, 2026-10-05: allocation series for 90 pods)*
- [x] `swirlit-node-01` costs EUR 12 a month (EUR 10 plus EUR 2 VAT), a fixed monthly price (not usage billing), recorded with `scripts/configure-node-cost.sh` and shared out by showback. *(2026-10-05)*
- [x] Monthly showback per namespace and workload in Grafana **Platform Report**, from hourly snapshots kept 13 months in Elasticsearch `platform-reports-*` ([platform reports](docs/guides/platform-reports.md)). *(live, 2026-10-05: first snapshot indexed)*
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
- [ ] Add ANSSI's ReCyF as a framework in `compliance-controls.yaml` and on the Compliance Evidence dashboard, mapped from the 27 controls, and re-map when the French law and decrees are published. ReCyF is a non-binding working document, published 2026-03-17 while the French NIS2 bill is still pending ([ANSSI](https://cyber.gouv.fr/reglementation/cybersecurite-systemes-dinformation/directives-nis-nis2-et-dispositif-saiv/directive-nis-2/)). Describe controls as "mapped to" ReCyF, never as compliant.
- [ ] Security questionnaire pack: `./install.sh --questionnaire caiq-v4` maps each catalog control to CAIQ v4 question IDs, with its automated evidence or "manual", and adds the sovereignty report (§33). Use it for the vendor's own CSA STAR Level 1 entry, a free self-assessment published in a public registry ([CSA](https://cloudsecurityalliance.org/star/self-assessment/), read 2026-10-23). The website's trust centre depends on it.
- [ ] Contract annex per installation: `./install.sh --contract-annex` writes the technical and organisational measures from the control catalog, the sub-processors from the external-services inventory, the data and processing locations from the node inventory, and DORA register-of-information rows (provider identity, service type, functions supported, locations, subcontracting chain, exit plan reference). Financial entities must keep that register under DORA, which has applied since 2025-01-17 ([EIOPA](https://www.eiopa.europa.eu/digital-operational-resilience-act-dora_en)). A generic run feeds §43's contract templates.
- [ ] ISMS document kit covering the manual evidence the catalog asks for: information security policy, risk register, continuity and recovery plan, and supplier register. These are templates in `docs/`, prefilled from the catalog, the external-services inventory and the coverage table (§16), with the customer's own fields left to complete. The evidence pack lists which documents are signed and when.
- [ ] Asset inventory as evidence: `./install.sh --asset-inventory` lists servers (hardware, OS, location, owner), workloads with images and data classes, data stores, external services and enrolled staff devices (§37), with owner and criticality, exported as CSV and included in the evidence pack. NIS2 Art. 21(2)(i) names asset management among the required measures.
- [ ] Data subject requests (GDPR Art. 15–17): an intake form and a GitLab or Odoo workflow with the one-month deadline; the onboarding contract gains an optional `privacy` hook through which each application exports or erases one person's data; `./install.sh --dsr export|erase --subject` runs it across Keycloak, Odoo and every application that declares the hook, and records the outcome. Done when a test identity is exported and erased end to end.
- [ ] Security awareness and phishing exercises: a self-hosted phishing simulation (GoPhish) sending through the platform mail relay to Keycloak users, a short training module per campaign, and click and report rates per campaign in the Platform Report as evidence for NIS2 Art. 21(2)(g) (basic cyber hygiene and training). Campaigns need the employer's written authorization and are explained to staff beforehand.

### 37. Security operations

- [ ] **ADR — runtime detection:** Falco or Tetragon with curated rules (shell in a container, unexpected egress, mining, sensitive file reads, privilege escalation), alerting through Alertmanager and indexed for investigation.
- [x] Detection rules every five minutes, posted to Alertmanager with severity, `owner: security` and runbooks: brute force followed by success, new platform administrator (Keycloak state), RBAC changed outside GitOps (dry-runs excluded), Secret read by an identity unseen for a week (after a 7-day learning period) ([security detection](docs/guides/security-detection.md)). *(live, 2026-10-05: RBAC rule verified with a test RoleBinding)*
- [ ] Extend detection to host events (Fail2ban/CrowdSec bans followed by a successful SSH login, sudo by unexpected users), HTTP events once Traefik access logs are shipped (§4), and impossible travel once logins carry a geolocation.
- [x] Encrypted application volumes: StorageClass `longhorn-encrypted` (dm-crypt LUKS2, key in Vault `infra/longhorn`, Secret kept on ExternalSecret removal), `dm_crypt` loaded by the host script ([encrypted volumes](docs/security.md#encrypted-volumes)). *(live, 2026-10-05: test claim mounted as LUKS2 aes-xts-plain64, written, read and removed)*
- [ ] Document a disk-encryption option for hosts in rented data centers, and move applications holding personal data to encrypted claims (application repositories).
- [x] Two default StorageClasses assessed: K3s re-marks `local-path` as default when it reapplies its bundled manifest, but Kubernetes resolves several defaults to the newest class (`longhorn`), so claims without a class still get Longhorn. `local-path` must stay for the node-local `vault-audit` claim, and a `.skip` file would stop K3s from updating its provisioner, so no change. *(2026-10-05)*
- [ ] Ransomware-resilient backups: off-site copies under retention lock, written with credentials that cannot delete or overwrite retained data, so a compromised cluster cannot destroy its backups (§16).
- [x] Just-in-time administration: `./install.sh --admin-access grant|revoke|list` grants `platform-admins` for 1–24 hours with a reason and a separate approver, recorded in `access-grants`; the detection job ends expired grants ([security](docs/security.md#just-in-time-administration)). *(live, 2026-10-05: grant, automatic expiry and removal verified with a test user)*
- [ ] Recorded `kubectl` and SSH sessions (with §29's Nexterm or an equivalent), and moving daily users from permanent to just-in-time membership.
- [ ] External penetration test of a reference installation (§44) before the first customer, repeated per major release. The platform's vulnerability disclosure policy and contact moved to §43, because CRA reporting already applies.
- [ ] Forward audit and detection events to a customer's SOC or managed security provider: identity, Kubernetes API, Vault and detection findings, sent as syslog (RFC 5424 over TLS) or OTLP, with a documented field mapping, delivery-failure alerts and a filter per destination. The tamper-evident store (§36) remains the archive.
- [ ] Staff device coverage: an endpoint agent (Wazuh or Fleet with osquery, ADR) enrolled through Keycloak on company laptops, reporting OS version, patch level, disk encryption, screen lock and antivirus status, with alerts on drift and per-device status in the Compliance Evidence dashboard. Most SME incidents start on a workstation, and the catalog has no device evidence today.
- [ ] Passkeys (WebAuthn) for Keycloak sign-in alongside the TOTP MFA of §11: phishing-resistant login for administrators first, then optional for every user, with recovery through a second registered key or a just-in-time reset approved by another administrator.

### 38. Reliability, SLOs and observability depth

- [x] Availability SLO (99.5 %) for every public endpoint (portal, Git, Registry, Argo CD, SSO, Odoo and every application host, discovered from Ingresses) measured by blackbox probes over the public path; multi-window burn-rate alerts; Grafana **Service Levels** with availability and budget left per endpoint ([service levels](docs/guides/service-levels.md)). *(live, 2026-10-05: 25 endpoints probed, all up)*
- [x] Monthly availability per endpoint in Grafana **Platform Report** from the hourly snapshots, independent of Prometheus retention.
- [x] Indicators beyond probes: data-store availability, CI queue time (p90) and platform-caused CI failures, Argo CD sync failures; `CIJobsQueueingLong` alert; on **Service Levels** and monthly in the **Platform Report** ([other indicators](docs/guides/service-levels.md#other-indicators)). *(2026-10-05: CI queue p90 about 4 minutes with one concurrent job)*
- [ ] Registry push/pull probes and per-service SLA targets for the managed service.
- [x] Every alert rule carries an `owner` label (`platform`, `security`, or `application` for application namespaces) and a `runbook_url` to its section of [alert runbooks](docs/guides/alert-runbooks.md); validation rejects an alert without both. *(2026-10-05, 44 alerts)*
- [ ] Status page hosted outside the platform, fed by the outage watch (§14), with incident updates; decide first which endpoints may be shown publicly.
- [ ] On-call routing: escalation policies and a paging channel in Alertmanager, with acknowledgement, on top of §27/§29 notifications. Name a second person or partner who is paged after a set delay, write the severity matrix in `docs/guides/on-call.md`, and close with a dated paging drill acknowledged end to end.
- [ ] OpenTelemetry: a collector, a trace backend and automatic instrumentation for Java and Node applications; correlate traces with logs and metrics in Grafana. Decide in the ADR whether logs stay in Elasticsearch.
- [x] Capacity planning: requested and used CPU/memory per server, requests after losing the largest server, cores and memory to add to stay below 70 %, days until filesystems and volumes fill; `ClusterCapacityRequestsHigh` and `ClusterCannotSurviveNodeLoss` alerts; Grafana **Capacity Planning** ([capacity planning](docs/guides/capacity-planning.md)); per-namespace history in the platform reports. *(2026-10-05: CPU requests at 96 % of allocatable with 15 % used; about 3 cores to add or to release by rightsizing)*
- [x] Lowered CPU requests of Kibana (250m → 100m), SonarQube (250m → 150m), Trivy operator (250m → 100m) and Prometheus (200m → 100m) to measured peaks; requests fall from about 99 % to 92 % of allocatable CPU. *(2026-10-05)*
- [x] Longhorn guaranteed instance-manager CPU lowered to 5 % (`DEFAULT_LONGHORN_GUARANTEED_INSTANCE_MANAGER_CPU`), applied to the instance manager created by the Longhorn 1.13.0 upgrade without interrupting volumes ([October upgrades](docs/guides/service-upgrades-2026-10.md)). The old instance manager (960m) is released as volumes are next reattached. *(2026-10-05)*
- [ ] Application-level backup and restore per project namespace (manifests, volumes and claimed databases), including self-service restore into a sandbox and moving a project between clusters.
- [x] Game days: `./install.sh --game-day` pod-loss drill outside `prod` with recovery times and endpoint availability, recorded in the **Platform Report** ([game days](docs/guides/service-levels.md#game-days)). *(2026-10-05, `apps-int`: recovered in 7–47 s, endpoint 94 % available during the drill: single replicas)*
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
| P0 | Current platform health | Root Application and maintenance jobs healthy after the discovery/retention fixes | §12–13 |
| P0 | Offsite archives, current Longhorn backup integration and separate recovery keys | Target outside the failure domain; fresh uploads and successful archive/volume restores | §8, §16 |
| P0 | GitLab and full-platform recovery coverage | Native backups plus configuration/secrets; isolated rebuild with measured RPO/RTO | §12, §16 |
| P1 | Private/key-only SSH and administrator MFA | Tested second session/console and break-glass login; recovery prerequisites complete | §4, §11 |
| P1 | `infra` network isolation and PSA enforcement | Dependency allowlists and host-agent placement validated before enforcement | §6 |
| P1 | Fresh vulnerability triage | Verified upstream fixes and new scans | §21 |
| P1 | Reactivate alert notifications | Owner's approval and choice of notifications first; paused 2026-10-11 | §14 |
| P1 | Pending reboot and selected host hardening | Fresh audit; backup/unseal/console recovery verified before maintenance | §4, §20 |
| P1 | Capacity baseline, PostgreSQL headroom and Registry cleanup | Usage/growth alerts, protected recovery images, verified reclamation | §10, §12, §26 |
| P1 | Version-2 app migration and removal of legacy anonymous Kafka access | Owning repos verify environment isolation and delivery before credentials/listener retirement | §10, §19 |
| P1 | Multi-host expansion, stable endpoints and HA | P0 complete; topology helpers first; three eligible storage hosts; ordered migration and host-loss drills | §5, §15, §25–26 |
| P1 | Ransomware-resilient backups and tamper-evident audit retention | Retention-locked off-site copies the cluster cannot delete | §36–37 |
| P1 | Company e-invoice reception through an approved platform | Legal for every French company since 2026-09-01, so overdue; module present in the pinned Odoo image, registration by the legal representative ([Needs Input](#needs-input)), a test invoice received; issuing by 2027-09-01 | §40 |
| P2 | Installer prerequisite cleanup, schema/engine and TUI | Bootstrap without Vault, idempotent rerun/resume and useful verification report | §2, §23 |
| P2 | API/host/HTTP audit coverage and remote-cluster telemetry | Scoped collection/retention; remote telemetry required before remote production use | §4, §14, §19, §20 |
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
| P2 | Custom application domains, passkeys, staff device coverage and asset inventory | Buyers ask for their own domains and phishing-resistant MFA; device and asset evidence fill NIS2 Art. 21(2)(i) and the questionnaire pack (§36) | §35–37 |
| P3 | Error tracking, DAST, feature flags, data subject requests, awareness exercises, energy showback and new modules (wiki, helpdesk, web analytics, signing, SaaS mailbox backup) | Module catalog (§40) and self-service claims (§34) first; each with SSO, backups and a restore test | §34–37, §40 |
| P2 | Joiner/mover/leaver automation, staff VPN and office-hours scheduling | Useful to every customer from the first installation; §27 directory decision for the HR source; scale-to-zero measured in Capacity Planning | §45 |
| P3 | Data warehouse and BI, internal tools builder, company search, meeting notes, translation, document capture, telephony, IoT and edge sites, dev workspaces, platform undo and phone approvals | Module catalog (§40), AI gateway (§41) and self-service claims (§34) first; each with SSO, backups and a restore test | §45 |
