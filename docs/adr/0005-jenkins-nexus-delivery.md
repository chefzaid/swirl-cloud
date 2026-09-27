# ADR 0005: Build With Jenkins And Store Artifacts In Nexus

> Superseded by [ADR 0013](0013-gitlab-argocd-delivery.md) on 2026-08-29.

- Status: Superseded
- Date: 2026-03-07

## Context

The first platform hosted source in GitLab but needed CI builds, Maven/npm
mirrors and a Docker registry for application images.

## Decision

Run Jenkins with Kubernetes-native build agents for CI and Nexus Repository
Manager OSS for Maven, npm and Docker artifacts. Argo CD reconciled deployments.
Nexus mirroring was disabled by default and required manual repository setup and
Jenkins ConfigMap edits.

## Rationale

Jenkins and Nexus were familiar, flexible tools for Java and Angular builds.

## Consequences

Three separately administered systems (GitLab, Jenkins, Nexus) held overlapping
credentials, users and pipelines. Manual Nexus setup made installation
non-repeatable. Both services and their wiring were removed on 2026-08-28/29 when
GitLab CI, the GitLab Container Registry and the Dependency Proxy replaced them.
