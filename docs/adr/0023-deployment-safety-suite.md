# ADR 0023: Keep A Small Permanent Deployment Safety Suite

- Status: Accepted
- Date: 2026-09-14

## Context

The repository had accumulated broad UI, source-text and per-image tests that
asserted implementation details, broke on harmless edits and still could not prove
that an installation, migration or failover worked.

## Decision

Run one offline gate, [`validate-repository.sh`](../../scripts/validate-repository.sh):
syntax, ShellCheck, documentation links, manifest inventories, workload policy,
digest pins, nine installer/GitOps render profiles with reference and parity checks,
Ansible syntax and a credential scan. Keep only nine permanent suites, each guarding
a concrete deployment or recovery failure that rendering cannot detect (backups,
PostgreSQL HA, Kafka HA, Vault unseal, Vault HA, node fencing, application
clusters, application data and environment onboarding). `--live` adds server-side
dry-runs without mutation.

## Rationale

Tests that encode data-loss and secret-handling invariants earn their maintenance
cost; tests of wording or layout do not. Runtime behavior belongs to rehearsals on
real hosts.

## Consequences

New tests must name a failure that syntax, policy and rendering miss. Image
promotion and failover still require the manual rehearsals listed in
[testing](../testing.md#rehearsals-and-drills).
