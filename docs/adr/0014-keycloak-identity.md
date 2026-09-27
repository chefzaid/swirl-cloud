# ADR 0014: Centralize Identity In Keycloak

- Status: Accepted
- Date: 2026-08-30
- Amended: 2026-09-07

## Context

Each administration tool had its own local administrator, and several used ingress
Basic Auth. Argo CD bundled Dex. Operators had to manage a dozen credentials, and
disabling an administrator meant touching every tool.

## Decision

- Run Keycloak (currently 26.7.3) on the shared PostgreSQL, under `/auth`.
- Reconcile an organization realm idempotently (`keycloak-sso-reconcile` Job):
  brute-force protection, password policy, declarative user profile, `groups`
  scope, `platform-admins` group with realm administration, and a matching
  bootstrap administrator in `master`.
- Use native OIDC where the service supports it: GitLab (linked to `root`),
  Grafana, Argo CD (PKCE, local admin disabled), Vault, Kafka UI, Portainer and Odoo.
- Put the others (DBGate, Kibana, Longhorn, SonarQube UI, Homepage) behind a
  shared OAuth2 Proxy Traefik ForwardAuth middleware.
- Back Cloudflare Access with a Keycloak client for edge protection.
- Remove Dex from Argo CD (2026-09-07).

## Rationale

One identity provider gives single sign-on, one place to disable access, group
authorization and auditable logins. Native OIDC keeps per-user sessions and audit
trails inside each tool.

## Consequences

Keycloak and PostgreSQL availability gate administrative access; local recovery
credentials remain in Vault for outages. `platform-admins` membership grants broad
administration without a second factor. Odoo's bundled module requires the
implicit flow. No SMTP server exists, so password-reset emails cannot be sent.
