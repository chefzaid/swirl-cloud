# ADR 0033: Realms, a Staff Realm and a Customer Realm

- Status: Proposed (awaiting the owner's decision, TODO Needs Input)
- Date: 2026-11-24

## Context

A Keycloak realm is a set of people who cannot sign in to another realm's
services: its own users, passwords, sessions, signing keys and clients. Every
installation has one platform realm today, used by staff for the internal tools
(GitLab, Grafana, Argo CD, Vault, the portal, Zammad, Vaultwarden, Odoo) and by
any public application that signs its users in. A shop's customers would then be
accounts in the same realm as the staff: one password policy, one brute-force
lockout, one account console, and access groups as the only wall between a
customer and the internal tools (ADR 0031). Some installations will serve several
companies of one group (subsidiaries) that must not see each other's people.

## Proposed decision

- **Staff realm** (the platform realm today): employees, contractors, guests with
  an end date. Every internal tool trusts this realm only.
- **Customer realm**, one per installation, created when the first public
  application asks for one in its onboarding contract (`realm: customers`): self
  registration with a verified email, the application's own sign-in theme, no
  access groups to internal tools, and its own signing keys. No internal tool has
  a client in it, so a customer account cannot sign in to one even by mistake.
- **Subsidiaries:** Keycloak Organizations inside the staff realm (one
  organization per company, membership in the token), not a realm per company.
  The tools that exist once (GitLab, Grafana, Argo CD, Vault) trust one issuer;
  Organizations keep that, while access groups and the organization claim
  separate what each company's people see. A separate realm per company is kept
  for a group whose companies must be legally separated; those tools then admit
  it through a second OIDC provider where they support one (GitLab) and stay
  staff-realm-only where they do not (Grafana, Argo CD).
- The onboarding contract names the realm each application signs in to; the
  default stays the staff realm for internal applications.

## Rationale

A second realm is the only wall Keycloak guarantees: a customer realm cannot
leak sessions, roles or password resets into the staff realm, whatever the group
configuration. Per-company realms multiply that wall but break the single-issuer
tools; Organizations (supported since Keycloak 26) give a company boundary
without that cost.

## Consequences

- Public applications that sign customers in move to the customer realm; their
  existing accounts are exported and imported, or asked to register again.
- The account console, password policy, lockout and second-factor rules of the
  customer realm are set for customers, apart from the staff rules.
- The access review covers both realms; the data subject export searches both.
- The done test: an account of each realm is refused by every service of the
  other.
