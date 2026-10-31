# Features

Capabilities implemented in the source today. Planned work is in
[TODO.md](../TODO.md); the [architecture overview](architecture.md) shows how the
pieces fit together.

## Installation And Reconciliation

- `./install.sh` setup hub (terminal UI) in English or French, with a recommended component bundle and automatic dependency closure
- unattended runs from a saved input file (`./install.sh --env-file FILE`) or `install-control-plane.sh --yes`, with every input problem reported before any change
- Ansible install and reconciliation playbooks on the same contract
- scopes: `infra` (shared platform) or `apps` (adds Odoo in `corp`)
- read-only legacy migration guard before any Kubernetes change
- local administrator password alignment across infrastructure services
- rerunnable steps that keep installed identity, exposure, topology and HA state
- operator commands on `./install.sh`: `--status`, `--plan`, `--support-bundle`, `--access-review`, `--credential-age`, `--vulnerability-report`, `--policy-report`, `--processing-records`, `--asset-inventory`, `--questionnaire`, `--contract-annex`, `--isms-kit`, `--admin-access`, `--admin-mfa`, `--game-day`
- semantic [releases](guides/releases.md) with a changelog, a CycloneDX SBOM, OpenVEX statements, third-party notices and SSH-signed bundles; a bundle refuses a component past its end of life
- a record of every installer and Ansible run shown by `--status`, and the installation report emailed when platform mail is configured

## Cluster Topology

- pinned K3s with secrets encryption and the bundled ingress disabled
- odd control-plane quorum planning; SQLite-to-embedded-etcd conversion with backup
- floating private API endpoint (keepalived VIP or Tailscale Service) that follows ready control planes
- remote or local enrollment of control planes and workers (`add-node.sh`)
- private transport over OVHcloud vRack, Tailscale or an existing LAN, with API-driven vRack attachment and tagged Tailscale keys
- schedulable or controller-only control planes; Longhorn replicas follow topology
- private SSH allowlists from registered control planes
- CoreDNS private service zone and an internal Registry mirror for containerd

## Storage And Recovery

- Longhorn default StorageClass, weekly trim and optional S3 volume backups
- opt-in encrypted volumes (`longhorn-encrypted`), key held in Vault
- daily root-only recovery archive: datastore snapshot, K3s credentials, Vault Raft snapshot, PostgreSQL and MongoDB dumps, GitLab configuration and secrets, restore instructions
- optional restic encryption and upload with daily/weekly/monthly retention
- the backup stops before writing when it would leave less than 15 % of the disk free

## Edge, Ingress And DNS

- Cloudflare DNS, Origin CA TLS, DNSSEC, WAF, login rate limiting, cache rules, Smart Tiered Cache and Access
- origin firewall that accepts web traffic only from Cloudflare
- platform-owned Traefik: native Ingress, ForwardAuth SSO, body limits, forwarded-header normalization, and the edge's HSTS and `nosniff` headers
- application DNS through onboarding, as A records or Tunnel CNAMEs
- opt-in HA ingress: a Traefik and `cloudflared` pair on every control plane

## Secrets

- Vault with Raft storage, two audit devices, Kubernetes and OIDC auth
- External Secrets into platform, corporate and environment namespaces
- automatic host unsealing; verified distribution of recovery material to control planes
- dark-themed Vault UI served by an isolated helper

## Identity

- declarative Keycloak realm with password and brute-force policy
- sign-in and account pages in English and French, following the browser
- just-in-time administration: approved, recorded grants of 1-24 hours that end automatically
- login and admin events kept 90 days in Keycloak and a year in `identity-audit-*` indices
- native OIDC for GitLab, Grafana, Argo CD, Vault, Kafka UI, Portainer and Odoo
- OAuth2 Proxy for DBGate, Kibana, Longhorn, SonarQube and the portal
- Cloudflare Access backed by Keycloak
- `platform-admins` mapped to administrator roles in every service

## Data Services

- PostgreSQL 18, MongoDB 7, Redis 8 and Kafka KRaft in `infra`
- per-environment access: PostgreSQL logins, Redis ACL prefixes, Kafka SCRAM users
- opt-in PostgreSQL HA (CloudNativePG), Kafka HA (three controllers and brokers) and Redis Sentinel HA, with journaled migrations

## Delivery

- GitLab CE with Container Registry, Package Registry and Dependency Proxy
- Kubernetes-executor runners, including project-scoped integration and release runners
- daily registry and package retention job
- central Argo CD with Keycloak login and a self-healing root Application
- platform CI that validates, then checks the exact reconciled revision and Registry push/read
- two-way GitHub/GitLab sync with token rotation
- declarative application onboarding with a verified first deployment
- `int`, `uat` and `prod` targets on the platform cluster or separate clusters

## Observability And Quality

- Prometheus, Alertmanager to GitLab alerts (notifications paused since 2026-10-11), and Grafana dashboards for Kubernetes, delivery and security
- [platform mail](guides/platform-mail.md) through one SMTP relay: critical and warning alerts, Keycloak password resets, GitLab notifications and Odoo mail
- every alert has an owner and a runbook section
- availability probes for every Ingress host, a 99.5 % objective, multi-window burn-rate alerts and a Service Levels dashboard
- an [outage watch](guides/service-levels.md#outage-watch) on Cloudflare that checks public endpoints from outside the cluster and emails on down and recovery (removed from the live installation on 2026-10-26)
- Registry API probes on the public and internal endpoints
- bare-metal hardware health: disk SMART, NVMe wear, software RAID, memory ECC, temperatures and BMC sensors over IPMI
- hourly availability, cost and compliance snapshots kept 13 months for the monthly Platform Report
- capacity planning: requests and usage, headroom after losing the largest server, capacity to add and days until storage fills
- Elasticsearch, Kibana, Logstash, Fluent Bit and Filebeat with retention policies
- per-application Grafana and Kibana dashboards generated from namespace labels
- Trivy Operator vulnerability, configuration, RBAC, secret and compliance reports
- SonarQube with automatic project provisioning and freshness-driven analysis

## Security Detection

- GitLab incident template and `./install.sh --incident-report`: evidence timeline, NIS2/GDPR due times and report drafts
- alerts every five minutes for a login after repeated failures, RBAC changes outside GitOps, Secret reads by new identities, new platform administrators, an SSH login after repeated failures and sudo by a new user
- optional [SOC forwarding](guides/soc-forwarding.md) of identity, API and Vault audit events and detection findings as syslog over TLS

## Host Security

- UFW default deny on every node, IPv4/IPv6 forwarding guards
- Fail2ban and CrowdSec on internet-facing control planes
- SSH hardening; private-only SSH on internal nodes
- Lynis audits shipped to Kibana: scheduled on control planes, on demand on workers
- AppArmor profile for containerd

## Kubernetes Security

- application policy pack (pinned digests, platform Registry, resources, app and ownership labels) in warn and audit mode, with a report for existing workloads
- Pod Security Admission levels per namespace
- admission policies denying hostPath and gitRepo volumes
- mutating admission hardening for K3s and Longhorn system controllers
- default-deny ingress for application and corporate namespaces
- every platform image pinned by digest; every workload declares token mounting and resource budgets

## Compliance Evidence

- inventory of every external service contacted and the data it receives; vendor telemetry and update checks off in GitLab, Grafana, Kibana, SonarQube, Portainer, Longhorn, Traefik and Odoo
- control catalog mapped to NIS2, ISO/IEC 27001:2022 Annex A, DORA, GDPR and CIS, evaluated continuously in Prometheus
- Compliance Evidence dashboard and a drift alert when a control fails or loses its evidence
- `./install.sh --compliance` report and a hashed, optionally SSH-signed evidence pack that auditors verify offline
- licence register with each component's maintainer, jurisdiction and end of life; security questionnaire, contract annex, ISMS document kit and [exit plan](guides/exit-plan.md)

## Cost Showback

- each server's monthly cost split across environments, namespaces and workloads by the larger of requests and usage
- idle capacity, shared costs and a rightsizing view in Grafana
- the same capacity priced at AWS Paris on-demand list prices, for comparison

## Administration And Corporate Tools

- Homepage portal titled `<company name> Cloud` with live Kubernetes status and app discovery
- Portainer, DBGate and Kafbat UI behind SSO; DBGate also signs in with Keycloak (`platform-admins`)
- Descheduler for manual rebalancing
- Odoo ERP with SSO and a persistent dark mode
- [Odoo CRM and Live Chat](guides/odoo-website.md) for the company website: idempotent lead intake with a scoped key, and an English/French chatbot that turns out-of-hours chats into leads

## High Availability Readiness

- durable availability mode and downgrade protection
- read-only HA readiness verifier
- optional Redfish node fencing for persistent singletons
- storage-detach safety policy for HA control planes

## Current Technical Boundaries

- The live platform is one host; HA profiles and fencing are not activated.
- Offsite backups are optional and not configured on the live installation.
- GitLab native backups are not automated.
- Alert notifications are paused (2026-10-11). Once restored, alerts reach GitLab and, with platform mail configured, email. There is no chat or paging receiver and no public status page.
- `infra` has no default-deny network policy or enforced Pod Security level.
- Remote application clusters lack central telemetry.

See [TODO.md](../TODO.md) for the complete list.

## Related Guides

- [Architecture](architecture.md)
- [Deployment](deployment.md)
- [Operations](operations.md)
- [Security](security.md)
