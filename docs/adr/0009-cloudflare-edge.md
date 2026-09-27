# ADR 0009: Front The Platform With Cloudflare And Lock The Origin

- Status: Accepted
- Date: 2026-08-13
- Amended: 2026-09-06, 2026-09-10, 2026-09-23

## Context

Administrative UIs and the Registry are published on the public domain from a
single host IP. The platform needs DNS automation, trusted TLS, DDoS and WAF
protection and an identity-aware gate in front of administration tools, without
running its own edge.

## Decision

Reconcile the zone with [`configure-cloudflare.sh`](../../scripts/configure-cloudflare.sh):

- proxied DNS for the platform host inventory in `platform.env`; apex publication
  is opt-in and reserved for the website (2026-09-06)
- a Cloudflare Origin CA certificate stored as the shared TLS Secret, Full (strict)
  TLS, TLS 1.3, HTTP/3, DNSSEC with registrar verification
- WAF rules, login rate limits and cache rules; bot challenges disabled only where
  Registry clients cannot answer them
- Cloudflare Access applications backed by a Keycloak IdP for administration hosts
- a request-header transform that removes visitor-supplied `X-Forwarded-For`
- host firewall rules that accept HTTP/HTTPS only from Cloudflare ranges
- in HA, outbound Cloudflare Tunnel connectors instead of a public origin IP

Applications own their own records; onboarding publishes them without adding
names to this repository.

## Rationale

Cloudflare supplies edge TLS, WAF, rate limiting and Access at no extra
infrastructure cost, and Origin CA certificates remove renewal automation. Locking
the origin prevents bypassing the edge.

## Consequences

Cloudflare, its API token scope and the Access team are hard dependencies. The
token needs Zone, DNS, SSL, WAF, Cache, Transform, Bot Management, Access and
(for HA) Tunnel permissions. Origin certificates are trusted only by Cloudflare,
so direct origin access is not browser-trusted. Header policy (HSTS) relies on
Cloudflare and each app.
