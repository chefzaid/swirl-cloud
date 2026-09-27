# ADR 0006: Keep Credentials In Vault And Project Them With External Secrets

- Status: Accepted
- Date: 2026-03-08

## Context

Every platform service needs generated administrator, database and OIDC client
credentials. Hand-created Kubernetes Secrets could not be rotated, audited or
recovered consistently, and secrets must never enter Git.

## Decision

- Run HashiCorp Vault (currently 2.1.0) with integrated Raft storage, a read-only
  root filesystem and two audit devices (stdout and a node-local file with an
  unprivileged rotator).
- Initialize and seed Vault with [`configure-vault.sh`](../../scripts/configure-vault.sh):
  KV-v2 at `secret/`, Kubernetes auth for External Secrets, OIDC auth for
  administrators, `external-secrets-policy` and `platform-admin` policies.
- Store platform credentials under `secret/infra/<service>` and application
  credentials under `secret/apps/<app>/...`.
- Project them with External Secrets Operator (`ClusterSecretStore vault-backend`)
  into namespace-scoped Secrets; per-environment stores use separate auth mounts.
- Keep the unseal key and bootstrap token root-only on control planes and unseal
  automatically with the `swirl-cloud-vault-unseal.timer` host service.

## Rationale

Vault gives one audited source of truth with generation, rotation and fine-grained
policy. External Secrets keeps workloads on ordinary Kubernetes Secrets and lets
rotation flow from Vault. Host-side unsealing survives restarts without an
external KMS.

## Consequences

Vault availability gates the whole platform; the host timer unseals every minute.
Control-plane root access includes Vault recovery authority. The recovery key and
token currently travel inside local backup archives; separating them from data is
an open item in [TODO.md](../../TODO.md).

Changing Vault's image must preserve its UID and Raft/audit mounts; see
[Vault upgrade and recovery](../security.md#upgrade-and-recovery).
