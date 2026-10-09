# ADR 0030: Watch Public Endpoints From Outside The Cluster

- Status: Accepted
- Date: 2026-10-09

## Context

Prometheus, Alertmanager and the platform mail relay
([ADR 0029](0029-platform-transactional-mail.md)) all run inside the cluster.
When the only cluster, its node or GitLab is down, nothing raises an alert,
which is exactly when one is needed. The check has to run elsewhere, need no
new account and reach the operator without the cluster.

## Decision

- A Cloudflare Worker, deployed by `scripts/configure-outage-watch.py`, checks
  the public endpoints every five minutes from Cloudflare's network. Installations
  without Cloudflare do not get the watch.
- It checks every Ingress host that reaches the origin: production application
  hosts and the platform hosts without Cloudflare Access (which answers at the
  edge without contacting the origin). Environment hosts (`int`, `uat`) are
  left out. Any answer below 500 counts as up; a 5xx answer, a timeout or a
  connection failure counts as a failed check, and two failed checks in a row
  mark the endpoint down.
- A Durable Object keeps each endpoint's state, so one email goes out when an
  endpoint goes down and one when it answers again. The Worker sends it over SMTP
  through the platform mail relay to the alert recipients; the relay password is a
  Worker secret.
- The Worker has no route and is kept off `workers.dev`: nothing new is public.
  Cloudflare accepts Durable Objects only on accounts with a `workers.dev`
  subdomain, so the configurator creates one named after the organization when
  the account has none. `--drill` adds a simulated endpoint that fails for a
  while, to prove both emails.

## Rationale

Cloudflare already fronts every public host, so the watch adds no supplier and
sees the same path visitors use. A Worker with a cron trigger needs no server,
and Durable Object storage and outbound sockets are available on the free
plan. Speaking SMTP to the configured relay keeps the watch provider-neutral like
the rest of platform mail, instead of tying it to one email service's API.

## Consequences

The watch fails with Cloudflare or the mail relay, and reports nothing about
private services. A copy of the relay password lives in Cloudflare; rerun the
configurator after changing the relay or adding public hosts. A status page fed
by the same results (TODO §38) needs a separate decision about what to publish.
