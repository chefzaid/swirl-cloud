# ADR 0031: One Access Group Per Service, Refused Without It

- Status: Accepted
- Date: 2026-11-14

## Context

Access was one group. `platform-admins` opened every tool with full rights, and
any other account in the realm still got rights nobody granted: Viewer in
Grafana, read access to every Argo CD Application, a GitLab account, a Portainer
user. Applications sign people in to the same realm, so an account created for an
application's user got those rights too. An organization with more than one
person needs to say who may open which service, without making each of them a
platform administrator.

## Decision

- A person opens a service only as a member of its access group in Keycloak,
  named `access-<service>`; `platform-admins` opens every service. A realm
  administrator grants access by adding the person to the group.
- Access groups are Keycloak groups, because every tool already authorizes on
  the token's `groups` claim. `keycloak-sso-reconcile` creates one for each
  service that honours it, and never removes members.
- Tools behind OAuth2 Proxy are refused by default. The proxy no longer has a
  global allowed group; each Traefik ForwardAuth address names the groups it
  admits (`?allowed_groups=...`). The shared `platform-sso` middleware admits
  `platform-admins` only, and a service opened to more people gets its own
  `platform-access-<service>` middleware. `validate-manifests.py` rejects a
  ForwardAuth address without `platform-admins` or with a group the reconciler
  does not create.
- The portal admits `access-portal`, given to every member of staff. A gate in
  front of Homepage gives administrators Homepage unchanged and builds
  everyone else a page from Homepage's own service list: the services their
  groups open, and those they could ask for, greyed. Nothing else of the
  platform reaches their browser.
- Tools that sign in themselves map the group to their lowest useful role and
  refuse an account without one: Grafana (`access-grafana`, Viewer, strict role
  mapping) and Argo CD (`access-argocd`, read-only, no default role).
- Where a tool cannot refuse by group, it stops creating accounts by itself:
  GitLab blocks an account created at first sign-in until an administrator
  approves it, and Portainer no longer creates users at sign-in.
- Inside a service, the role model of TODO §34 decides what the person may do.
  Job profiles that bundle several access groups, administration per realm,
  separate realms for customers and refusal at Keycloak itself for the tools
  above are later steps (TODO §47).

## Rationale

OAuth2 Proxy applies `allowed_groups` on the request Traefik sends it, not on
the visitor's URL, on both its auth-only and proxy endpoints, so one proxy
serves every host and a visitor cannot widen their own access. Keycloak roles
with composite roles would give profiles natively, but the tools read the
`groups` claim, and merging roles into that claim could not be tested from the
development environment; a wrong merge would lock administrators out of every
tool. Groups need no change to tokens.

## Consequences

- A new account opens nothing until someone adds it to a group.
- The gate trusts the identity headers Traefik sets from OAuth2 Proxy's answer,
  as the tools behind the proxy do. A pod inside the cluster that calls the
  portal's Service directly can still claim any group; it could already read
  Homepage without signing in.
- Group changes reach a tool at the person's next sign-in, or within an hour
  when OAuth2 Proxy refreshes its session.
- Administration hosts behind Cloudflare Access still need the person's email in
  its allowlist until the edge follows these groups (TODO §47).
- Kafka UI has no role rules yet and admits any signed-in account; Cloudflare
  Access is its only limit (TODO, Bugs to fix).
