# ADR 0013: Deliver Through Self-Hosted GitLab And One Central Argo CD

- Status: Accepted
- Date: 2026-08-29
- Supersedes: [ADR 0005](0005-jenkins-nexus-delivery.md)

## Context

Jenkins, Nexus and GitLab duplicated users, credentials and pipelines
([ADR 0005](0005-jenkins-nexus-delivery.md)). Source also lived on GitHub. The
platform needed one delivery system whose desired state stays in Git.

## Decision

- Run GitLab CE Omnibus (currently 19.3.1) as the single source, CI, Container
  Registry, Package Registry and Dependency Proxy, with Keycloak OIDC.
- Run a Kubernetes-executor GitLab Runner in `gitlab-runners`; version-2
  applications receive project-scoped integration and release runners.
- Keep GitHub and GitLab synchronized in both directions without force pushes,
  with a self-rotating GitLab project token.
- Run one central Argo CD in `infra`. The root `swirl-cloud` Application reconciles
  `k8s/` with automatic sync, prune and self-heal; each application owns its
  Applications.
- Platform CI validates every commit and, on `main`, waits for Argo CD to report
  the exact commit `Synced`/`Healthy`, then checks delivery services and Registry
  push/read. CI observes reconciliation; it never gates Argo's start.
- Reconcile the group, project, retention, runner and tokens with
  [`configure-gitlab-ci.sh`](../../scripts/configure-gitlab-ci.sh) using a temporary
  administrator token issued through `gitlab-rails` and revoked on exit.

## Rationale

GitLab consolidates source, CI and artifacts behind one identity. Argo CD keeps Git
as the auditable desired state and reverts manual drift. Observing reconciliation
from CI proves the deployed revision without giving CI cluster-mutation rights.

## Consequences

GitLab is a persistent singleton; its native backup is not yet automated. Deleted
tags and packages do not reclaim Registry storage without separate cleanup. The
instance runner runs one job at a time. Argo CD's own Helm release is upgraded
outside GitOps.
