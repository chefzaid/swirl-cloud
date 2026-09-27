# ADR 0024: Serve Environments From Registered Targets With Isolated Shared Data

- Status: Accepted
- Date: 2026-09-24
- Amended: 2026-09-26

## Context

Applications need `int`, `uat` and `prod` environments. Running a full platform per
environment would triple GitLab, Vault, Keycloak and databases. Environments must
still be unable to read one another's data, credentials or deployment targets.

## Decision

- Register each environment as a target with
  [`configure-deployment-environments.py`](../../scripts/configure-deployment-environments.py):
  `mode: local` creates `apps-<env>` on the platform cluster with a restricted
  AppProject, a dedicated Vault store and auth mount, network policies and a
  ResourceQuota; `mode: remote` registers a separate K3s cluster bootstrapped by
  `install-application-cluster.sh` and reached over Tailscale.
- Publish only verified targets in `infra/deployment-environments`; CI reads that
  public inventory and rejects missing targets or changed bindings.
- Keep data services shared but authenticated: one PostgreSQL with per-environment
  databases and logins, Redis ACL users restricted to key prefixes, Kafka SCRAM
  users restricted to topic and group prefixes (`applicationData.enabled`).
- Give each version-2 project integration and protected-release runners whose
  admission rules bind Applications to their assigned destinations.
- Support `suffix` (`app-int.example.com`) and `nested` hostname styles.

## Rationale

Environments share operational tooling while keeping blast radius limited by
namespace, credentials, quotas and admission. Remote targets allow production
isolation without duplicating platform services.

## Consequences

Local environments share the cluster's outages and capacity. Redis key names are
visible across environments through `KEYS`/`SCAN`. Remote clusters lack central
telemetry and support one control plane per target. Legacy `apps` workloads keep
anonymous access until migrated.
