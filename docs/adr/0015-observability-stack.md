# ADR 0015: Build Observability From Prometheus, ELK, Trivy And Namespace Discovery

- Status: Accepted
- Date: 2026-08-30
- Amended: 2026-08-31, 2026-09-07, 2026-09-10, 2026-09-13

## Context

Operators need metrics, logs, host audit history, vulnerability findings and
source analysis for platform services and for applications they do not own.
Applications should appear on dashboards without editing this repository.

## Decision

- Metrics: Prometheus (15 d / 1 500 MB), Alertmanager to GitLab alerts, Grafana
  with Keycloak OIDC, kube-state-metrics and node-exporter.
- Logs: Fluent Bit to Elasticsearch with a short ILM retention; Filebeat → Logstash
  for Lynis audits (365 d); Kibana behind SSO with per-user identities.
- Security: Trivy Operator with a built-in server scanning workloads, configuration,
  RBAC, exposed secrets and CIS/NSA/PSS compliance; Grafana dashboard (2026-08-31).
- Source analysis: SonarQube Community with a discovery CronJob that provisions
  projects and requests fresh analyses for Argo-tracked repositories (2026-09-07).
- Discovery: generate Grafana dashboards and Kibana saved objects for every
  workload in namespaces labelled `swirl-cloud.io/application-workloads`, grouped
  by Argo CD tracking or standard labels (2026-09-13).
- Reduce ordinary telemetry retention to fit single-host disks (2026-09-10).

## Rationale

These open-source tools run self-contained on the cluster. Label-driven discovery
keeps applications independent of this repository while still giving them
dashboards, logs and analysis.

## Consequences

Alerts reach only GitLab's alert list. Several platform components (Traefik,
Vault, Argo CD, Longhorn, databases) are not scraped. Elasticsearch is a single
node without snapshots. Remote application clusters have no telemetry path yet.
See [TODO.md](../../TODO.md).
