# ADR 0003: Use Longhorn For Replicated Persistent Storage

- Status: Accepted
- Date: 2026-03-07

## Context

Stateful services (GitLab, PostgreSQL, SonarQube, Elasticsearch, Odoo, Prometheus,
Grafana, Portainer) need volumes that can move between hosts once the cluster has
more than one node. Bare-metal servers have local disks and no cloud block storage.

## Decision

Install Longhorn (currently 1.12.1) as the default StorageClass through the shared
Helm helper, and let topology reconciliation choose replica counts:

| Topology | Default replicas |
| --- | ---: |
| No workers | 1 |
| Non-HA with workers | registered worker count, control planes excluded |
| HA (three control planes and three storage hosts) | 3, hard anti-affinity |

Existing volumes are only increased, never shrunk. A weekly `filesystem-trim`
RecurringJob reclaims deleted blocks. Optional recurring backups go to the same
encrypted S3 bucket as host archives. Vault audit logs use a node-local volume so
Vault can start without Longhorn.

## Rationale

Longhorn runs on commodity disks, needs only iSCSI/NFS host packages, provides
snapshots and S3 backups, and exposes a UI. Keeping replica policy in one
topology helper stops installer, enrollment and Ansible from disagreeing.

## Consequences

Longhorn replicates blocks, not application consistency; each database keeps its
own replication in HA ([ADR 0017](0017-opt-in-high-availability.md)). A volume with
three replicas per PVC can consume nine physical copies of a three-instance database.

Singleton writers need fencing before a volume moves after host loss
([ADR 0018](0018-singleton-fencing.md)). Requested replica counts do not prove
healthy distinct-host copies; verify them before failure drills.

Longhorn system controllers are hardened through admission policies rather than
by editing its generated manifests.
