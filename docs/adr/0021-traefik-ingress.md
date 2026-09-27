# ADR 0021: Route Through A Platform-Owned Traefik Release

- Status: Accepted
- Date: 2026-09-14
- Supersedes: [ADR 0004](0004-ingress-nginx.md)

## Context

The Kubernetes project retired ingress-nginx. The platform also needed an HA
ingress mode with one ingress per control plane behind Cloudflare Tunnel, without
changing how applications own their routes.

## Decision

- Disable K3s's bundled Traefik and install Traefik (currently v3.7.13) through a
  pinned platform Helm release with [`configure-ingress.sh`](../../scripts/configure-ingress.sh).
- Keep native Kubernetes Ingress for platform and application routes; use
  same-namespace Traefik Middleware for authentication (ForwardAuth), body limits
  and redirects; disable cross-namespace CRD references.
- Run unprivileged listeners (8080/8444) with a read-only root filesystem, HTTPS
  redirect, TLS ≥ 1.2 and 900 s timeouts.
- Normalize forwarded headers in a shared middleware and publish trusted edge
  ranges in `infra/ingress-proxy-trust` for GitLab.
- Direct mode: K3s ServiceLB on the bootstrap control plane. HA mode: a DaemonSet
  with a co-located `cloudflared` per pod trusting only loopback.
- Migrate existing clusters with a candidate release and a maintenance cutover.

## Rationale

Traefik supports native Ingress, ForwardAuth and a sidecar-friendly DaemonSet
layout, and its middleware model replaces NGINX annotations without moving route
ownership to the platform.

## Consequences

Applications had to replace NGINX annotations with Middleware. Global timeouts are
not exact replacements for NGINX idle timers. Traefik metrics and access logs are
not yet collected.
