# ADR 0012: Separate Platform, Application And Corporate Namespaces

- Status: Accepted
- Date: 2026-08-28
- Amended: 2026-09-07

## Context

The first installation used `infrastructure` and `application` namespaces and
kept Odoo beside platform services. Security policy, observability discovery and
ownership could not distinguish shared tools, the company ERP and workloads owned
by other repositories.

## Decision

| Namespace | Owner and purpose | Baseline |
| --- | --- | --- |
| `infra` | Shared platform services and all Argo CD Applications | PSA audit/warn `restricted` |
| `apps` | Application workloads from application repositories | PSA enforce `baseline`, default-deny ingress, hostPath denied |
| `apps-<env>` | Registered local environments (2026-09-24) | PSA enforce `baseline`, default deny with explicit connectivity, ResourceQuota, scoped secret store |
| `corp` | Centrally owned Odoo (2026-09-07) | PSA enforce `baseline`, default-deny ingress |
| `gitlab-runners` | Isolated CI jobs | PSA enforce `baseline`, hostPath denied |
| `longhorn-system`, `kube-system` | Storage and Kubernetes system | Hardened by admission policies |

Application namespaces carry `swirl-cloud.io/application-workloads: "true"` for
observability and Sonar discovery. The `apps` foundation exists even when Odoo is
disabled (`INSTALL_SCOPE=infra`).

## Rationale

Namespace boundaries map directly to ownership, admission policy, network policy
and discovery scope. Moving Odoo to `corp` keeps corporate data out of both the
platform and application discovery.

## Consequences

`infra` still enforces no Pod Security level and has no default-deny network
policy, because storage and logging agents need host access; these are open items
in [TODO.md](../../TODO.md). Moving existing workloads between namespaces requires a
documented volume migration.
