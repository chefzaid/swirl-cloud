# ADR 0020: Keep The Platform Organization-Neutral

- Status: Accepted
- Date: 2026-09-13
- Amended: 2026-09-23

## Context

The repository started as one company's infrastructure, with its
domain, realm, GitLab group and branding written into manifests. That prevented
reuse and made renaming risky.

## Decision

- Make identity an installation input: organization name and slug, public domain,
  internal DNS zone, GitLab group/project, Keycloak realm, TLS secret name, Sonar
  ALM setting, Cloudflare Access names and GitOps URL.
- Render placeholders (`__PUBLIC_DOMAIN__`, `__KEYCLOAK_REALM__`, …) from those
  inputs and carry them as Helm parameters on the root Application.
- Record installed choices in `infra/swirl-cloud-identity` so reruns, Ansible and
  helpers reuse them; explicit inputs override stored ones.
- Derive branding from identity: the portal shows `<company name> Cloud`
  (2026-09-23); Keycloak and Odoo use the plain company name.

## Rationale

Any organization can install the platform without editing source, and one
installation can be rebranded by changing a display name.

## Consequences

Changing the domain, slug, realm or repository paths is a migration of accounts
and resources, not a rename. Existing installations must pin their identity as
explicit Helm parameters before syncing the generalized chart.
