# ADR 0017: Make High Availability Opt-In Through Explicit Migrations

- Status: Accepted
- Date: 2026-09-10

## Context

Adding hosts does not replicate services by itself. PostgreSQL, Kafka, Vault,
Redis and ingress each have different replication and failover mechanisms, and a
careless switch could start a second writer or lose committed data on a live
single-node installation.

## Decision

- Keep the default profile single-instance. Enable HA only with
  `HIGH_AVAILABILITY_ENABLED=true` after three Ready control planes and three
  storage-eligible hosts exist.
- Migrate each stateful service through a dedicated, journaled helper with
  maintenance gates: CloudNativePG for PostgreSQL (synchronous `ANY 1`), Kafka
  dynamic KRaft quorum with three brokers and `min.insync.replicas=2`, and Vault
  three-voter Raft.
- Record verified cutovers (`infra/postgres-ha-state`, `infra/kafka-ha-state`)
  and the availability mode (`infra/swirl-cloud-topology`); installer, Ansible and
  enrollment inherit them and refuse unfinished migrations or implicit downgrades.
- Replicate shared Redis with Sentinel and HAProxy, CoreDNS with admission-enforced
  placement, External Secrets, Keycloak and Argo CD services.
- Replace direct ingress with per-control-plane Traefik and Cloudflare Tunnel pairs.
- Leave application availability to each application repository.

## Rationale

Explicit, resumable migrations preserve identities and data and make every step
verifiable. Durable state prevents a later rerun from silently reverting the
profile.

## Consequences

HA needs roughly three times the storage for replicated databases and a planned
maintenance window. Persistent singletons (GitLab, SonarQube, Odoo) still have an
outage during recovery ([ADR 0018](0018-singleton-fencing.md)). The profiles have
not yet been activated or drilled on real hosts; the live cluster is single-node.
