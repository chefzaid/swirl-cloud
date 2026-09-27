# ADR 0016: Rebuild Vulnerable Platform Images In A Private Registry

> Superseded by [ADR 0022](0022-upstream-images-by-digest.md) on 2026-09-14.

- Status: Superseded
- Date: 2026-09-07

## Context

Trivy reported critical and high findings in several upstream platform images
(PostgreSQL, Redis, Vault, Grafana, Homepage, DBGate, NGINX, Trivy itself and
others). Some fixes were available in base-image packages or libraries before the
vendors published new images.

## Decision

Build patched images from Dockerfiles under `images/security/`, push them to a
private project in the GitLab Registry, select them through
`profiles/security-images-*.values` and `securityImagesEnabled`, and rewrite
system Pods to the private images through admission policies. Pull credentials
were distributed as ExternalSecrets.

## Rationale

Rebuilding closed findings quickly without waiting for upstream releases.

## Consequences

The platform depended on its own Registry to start, needed pull credentials in
every namespace, maintained patches against moving upstreams, and diverged from
vendor-supported images. Recovery after a Registry outage required importing
images manually. These costs led to [ADR 0022](0022-upstream-images-by-digest.md).
Retired profile files remain only as migration guards that stop rendering.
