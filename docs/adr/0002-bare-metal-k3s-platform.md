# ADR 0002: Run One Bare-Metal K3s Platform Cluster

- Status: Accepted
- Date: 2026-03-07

## Context

The organization needed source control, CI, GitOps, identity, data services,
observability and an ERP for several small applications, on rented dedicated
servers rather than a managed cloud. A full Kubernetes distribution would consume
too much of a single host; separate VMs per service would duplicate operations.

## Decision

Run one K3s cluster as the shared platform:

- start on one OVHcloud dedicated server and grow by adding control planes and workers
- install K3s at a pinned version (currently `v1.36.4+k3s1`) with `--secrets-encryption`,
  `--write-kubeconfig-mode 600` and the bundled Traefik disabled
- keep SQLite for a single server and convert to embedded etcd before adding control planes
- host shared services once and let application repositories deploy onto them
- allow environments to use their own application clusters later ([ADR 0024](0024-deployment-environments.md))

## Rationale

K3s gives conformant Kubernetes with a single binary, low memory overhead, built-in
ServiceLB, containerd and an embedded datastore upgrade path. One shared platform
avoids running GitLab, Vault, Keycloak and databases per application.

## Consequences

Until more hosts are enrolled, every service, the datastore and local backups
share one failure domain. The live installation is still a single node.

Platform capacity planning must include CI builds, Longhorn rebuilds and snapshot
space. K3s upgrades are deliberate: reruns preserve the installed version.

Application availability depends on the platform; [ADR 0017](0017-opt-in-high-availability.md)
defines how redundancy is added.
