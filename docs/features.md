# Features

This guide catalogs capabilities already implemented in the platform. Planned and
unfinished work belongs in [TODO.md](../TODO.md), so this file lists only behavior
present in the source. The [architecture overview](architecture.md) explains how
the pieces fit together.

## Installation And Reconciliation

- semantic platform releases with a generated changelog, a CycloneDX SBOM of every pinned image, chart and tool, and SSH-signed release bundles
- guided installer with grouped prompts and a recommended component bundle, in English or French
- unattended installation (`--yes`) with validated environment inputs
- Ansible installation and idempotent reconciliation playbooks sharing the same contract
- installation scopes: `infra` (shared platform) or `apps` (adds Odoo in `corp`)
- automatic dependency closure between selected components
- read-only legacy migration guard before any Kubernetes change
- local administrator password alignment across infrastructure services
- rerunnable steps that preserve installed identity, exposure, topology and HA state

## Cluster Topology

- K3s at a pinned version with secrets encryption and the bundled ingress disabled
- odd control-plane quorum planning, SQLite-to-embedded-etcd conversion with backup
- floating private Kubernetes API endpoint (keepalived VIP or Tailscale Service) that follows ready control planes
- remote or local enrollment of control planes and workers (`add-node.sh`)
- OVHcloud vRack, Tailscale or existing-LAN private transport, including API-driven attachment and tagged mesh keys
- schedulable or controller-only control planes, with topology-driven Longhorn replicas
- private SSH allowlists from registered control planes
- CoreDNS private service zone and an internal Registry mirror for containerd

## Storage And Recovery

- opt-in encrypted Longhorn volumes (`longhorn-encrypted`) with the key held in Vault
- Longhorn default StorageClass, weekly filesystem trim and optional S3 volume backups
- daily root-only recovery archive with datastore snapshot, K3s credentials, Vault Raft snapshot, PostgreSQL and MongoDB dumps, and restore instructions
- optional restic encryption and upload with daily/weekly/monthly retention

## Edge, Ingress And DNS

- Cloudflare DNS, Origin CA TLS, DNSSEC, WAF, login rate limiting, cache rules, Smart Tiered Cache and Access
- origin firewall that accepts web traffic only from Cloudflare
- platform-owned Traefik with native Ingress, ForwardAuth SSO, body limits and forwarded-header normalization
- application DNS ownership through onboarding, with direct A records or Tunnel CNAMEs
- opt-in HA ingress: Traefik and `cloudflared` pairs on every control plane

## Secrets

- Vault with Raft storage, two audit devices, Kubernetes and OIDC authentication
- External Secrets projection into platform, corporate and environment namespaces
- automatic host unsealing and verified distribution of recovery material to control planes
- dark-themed Vault UI served by an isolated helper

## Identity

- Keycloak realm reconciled declaratively with password and brute-force policy
- sign-in and account pages in English and French, following the browser language
- just-in-time platform administration: approved, recorded grants of 1–24 hours that end automatically
- login and admin events stored in every Keycloak realm (90 days) and kept a year in `identity-audit-*` indices
- single sign-on to GitLab, Grafana, Argo CD, Vault, Kafka UI, Portainer and Odoo through native OIDC
- OAuth2 Proxy gate for DBGate, Kibana, Longhorn, SonarQube and the portal
- Cloudflare Access backed by Keycloak
- `platform-admins` group mapped to administrator roles in every service

## Data Services

- PostgreSQL 18, MongoDB 7, Redis 8 and Kafka KRaft in `infra`
- authenticated per-environment data access: PostgreSQL logins, Redis ACL prefixes and Kafka SCRAM users
- opt-in PostgreSQL HA (CloudNativePG), Kafka HA (three controllers and brokers) and Redis Sentinel HA with journaled migrations

## Delivery

- GitLab CE with Container Registry, Package Registry and Dependency Proxy
- Kubernetes-executor runners, including project-scoped integration and release runners
- daily registry and package retention job
- central Argo CD with Keycloak login and a self-healing root Application
- platform CI that validates, then verifies the exact reconciled revision and Registry push/read
- two-way GitHub/GitLab synchronization with token rotation
- declarative application onboarding with verified first deployment
- `int`, `uat` and `prod` deployment targets on the platform cluster or separate clusters

## Observability And Quality

- Prometheus, Alertmanager to GitLab alerts (notifications paused since 2026-10-11), Grafana dashboards for Kubernetes, delivery and security
- platform email through one SMTP relay: critical and warning alerts by email, Keycloak password resets, GitLab notifications and Odoo's outgoing mail ([guide](guides/platform-mail.md))
- every alert labelled with an owner and linked to its runbook section
- availability probes for every Ingress host, a 99.5 % objective with multi-window burn-rate alerts and a Service Levels dashboard
- an outage watch on Cloudflare that checks the public endpoints from outside the cluster and emails when one goes down or recovers ([guide](guides/service-levels.md#outage-watch))
- hourly snapshots of availability, cost and compliance kept 13 months in Elasticsearch for the monthly Platform Report
- capacity planning on requests and usage, headroom after losing the largest server, capacity to add, and days until storage fills
- Elasticsearch, Kibana, Logstash, Fluent Bit and Filebeat with retention policies
- generated per-application Grafana and Kibana dashboards from namespace labels
- Trivy Operator vulnerability, configuration, RBAC, secret and compliance reports
- SonarQube with automatic project provisioning and freshness-driven analysis requests

## Security Detection

- GitLab incident template and `./install.sh --incident-report` with an evidence timeline, NIS2/GDPR due times and report drafts
- alerts for a successful login after repeated failures, RBAC changes outside GitOps, Secret reads by new identities and new platform administrators, every five minutes

## Host Security

- UFW default deny on every node and IPv4/IPv6 forwarding guards
- Fail2ban and CrowdSec on internet-facing control planes
- SSH hardening and private-only SSH on internal nodes
- Lynis audits on a schedule for control planes and on demand for workers, shipped to Kibana
- AppArmor profile for containerd

## Kubernetes Security

- application policy pack (pinned digests, platform Registry, resources, app and ownership labels) in warn and audit mode, with a report for existing workloads
- Pod Security Admission levels per namespace
- admission policies denying hostPath and gitRepo volumes
- mutating admission hardening for K3s and Longhorn system controllers
- default-deny ingress for application and corporate namespaces
- every platform image pinned by digest; every workload declares token mounting and resource budgets


## Compliance Evidence

- inventory of every external service the platform contacts, with the data each receives; vendor telemetry and update checks switched off in GitLab, Grafana, Kibana, SonarQube, Portainer, Longhorn, Traefik and Odoo
- control catalog mapped to NIS2, ISO/IEC 27001:2022 Annex A, DORA, GDPR and CIS, evaluated continuously in Prometheus
- Compliance Evidence dashboard and a drift alert when a control starts failing or loses its evidence
- `./install.sh --compliance` report and a hashed, optionally SSH-signed evidence pack that auditors verify offline

## Cost Showback

- each server's monthly cost allocated to environments, namespaces and workloads by the larger of requests and usage
- idle capacity, shared costs and a rightsizing view of requested versus used CPU and memory in Grafana

## Administration And Corporate Tools

- Homepage portal titled `<company name> Cloud` with live Kubernetes status and app discovery
- Portainer, DBGate and Kafbat UI behind SSO; DBGate also signs in with Keycloak (`platform-admins`)
- Descheduler for manual rebalancing
- Odoo ERP with SSO and a persistent dark mode
- Odoo CRM and Live Chat for the company website: idempotent lead intake with a scoped key, and an English/French chatbot that turns out-of-hours chats into leads ([guide](guides/odoo-website.md))

## High Availability Readiness

- durable availability mode and downgrade protection
- read-only HA readiness verifier
- optional Redfish node fencing for persistent singletons
- storage-detach safety policy for HA control planes

## Current Technical Boundaries

- The live platform is one host; HA profiles and fencing have not been activated.
- Offsite backups are optional and not configured on the live installation.
- GitLab native backups are not automated.
- Alert notifications are paused (2026-10-11); once restored, alerts reach GitLab and, with platform mail configured, email. There is no chat or paging receiver and no public status page.
- `infra` has no default-deny network policy or enforced Pod Security level.
- Remote application clusters lack central telemetry.

See [TODO.md](../TODO.md) for the complete list.

## Related Guides

- [Architecture](architecture.md)
- [Deployment](deployment.md)
- [Operations](operations.md)
- [Security](security.md)
