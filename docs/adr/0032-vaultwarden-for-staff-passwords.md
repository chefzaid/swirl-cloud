# ADR 0032: Vaultwarden for Staff Passwords, Signed In Through Keycloak

- Status: Accepted
- Date: 2026-11-16

## Context

Staff keep the logins to outside services (suppliers, banks, registrars, SaaS
tools) somewhere: browsers, spreadsheets, chat. HashiCorp Vault holds the
platform's machine secrets, but it has no browser or phone apps, no autofill
and no sharing model people use day to day. The owner chose Vaultwarden on
2026-11-09. The other candidate was Bitwarden's own self-hosted server.

## Decision

- Run Vaultwarden 1.37 (AGPL-3.0) in `infra` at `passwords.<domain>`, for the
  official Bitwarden browser, desktop and mobile apps.
- Sign-in goes through Keycloak only (`SSO_ONLY`, a confidential client with
  PKCE): no local sign-up, no password login that bypasses the realm's policy,
  lockout and second factor. An account is created at a person's first sign-in,
  matched by verified email. Each person's master password still encrypts their
  vault on their device; the server never holds a readable password.
- The database is a role and database on the platform PostgreSQL; attachments,
  Sends and the server's signing key are on a Longhorn volume. Both are in the
  nightly database dump and the daily off-node volume backups (TODO §16).
- The admin page stays off (no `ADMIN_TOKEN`): every setting comes from the
  manifest, so no extra credential exists to protect. Only the platform's first
  administrator creates organizations, which hold the shared collections.
- No sign-in gate sits in front: the apps call the API directly and would fail
  behind OAuth2 Proxy or Cloudflare Access. Vaultwarden rate-limits sign-ins
  itself, and Keycloak's brute-force protection applies to the SSO step.
- HashiCorp Vault keeps every machine secret; Vaultwarden holds only what people
  type into it.

## Rationale

Bitwarden's self-hosted server needs a licence for SSO, and its stack (several
.NET services and Microsoft SQL Server or another database) is far heavier for
a company of this size. Vaultwarden implements the same client API in one
process, supports OpenID Connect sign-in since 1.35, and runs on PostgreSQL.
It is a community project not affiliated with Bitwarden Inc., so the official
apps can change in ways it follows a little later; releases are tracked by
Renovate and the licence register.

## Consequences

- Everyone with an account in the platform realm can open Vaultwarden; who sees
  which shared login is decided by organization collections inside it.
- Push notifications to phones are off (they go through Bitwarden's relay);
  the apps sync when opened.
- A person who forgets their master password loses their vault, by design.
  Emergency access (another person granted access after a waiting period) and
  each person's own encrypted export are the ways back
  ([passwords guide](../guides/passwords.md)).
- Losing Keycloak blocks sign-in to Vaultwarden. The recovery copy of the
  credentials needed to rebuild the platform is therefore kept outside it, in
  the recovery kit.
