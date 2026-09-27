# ADR 0004: Route Public Traffic Through ingress-nginx

> Superseded by [ADR 0021](0021-traefik-ingress.md) on 2026-09-14.

- Status: Superseded
- Date: 2026-03-07

## Context

The first installation disabled K3s's bundled Traefik and needed one HTTPS entry
point for Keycloak, Jenkins, SonarQube, Nexus, GitLab, Argo CD, Grafana, Kibana
and Longhorn, plus application hosts owned by application repositories.

## Decision

Install the Kubernetes community ingress-nginx controller on the bootstrap host,
publish its public IP, and let each repository own native Ingress resources in
its namespace. Authentication and request limits used NGINX annotations.

## Rationale

ingress-nginx was the most widely documented controller, supported the
annotation-based authentication used by OAuth2 Proxy, and kept application
routes as standard Ingress objects.

## Consequences

Routing depended on NGINX-specific annotations and a controller the Kubernetes
project later retired. Platform migration required replacing annotations with
Traefik Middleware while preserving native Ingress ownership; the
[platform migration guide](../guides/platform-migration.md) documents that cutover.
